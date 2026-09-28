"""A `.kicad_pcb`, read into the objects the rest of the tool reasons about.

Read-only and built on `sexpr`: every object keeps the list it came from
(``node``), so a write edits that list and the document writes back only
what changed. Nothing here imports KiCad.

Units are integer nanometres, as inside KiCad, so a coordinate read and
written back is the same coordinate; angles are degrees. Every rule about
what a number in the file means was checked against pcbnew, item by item,
on KiCad's demo boards (`tests/test_fileformat_board.py`):

- a footprint's children are stored in its own frame, already mirrored when
  it sits on the back, and placed by turning them by its angle;
- the angle stored on a pad is the pad's final angle on the board -- the
  footprint's own angle already added in;
- nets are named on each item in KiCad 10 (``(net "GND")``) and numbered in
  KiCad 9 (``(net 3)``, against a table at the top). Both come out as names;
- ``*.Cu`` on a pad means this board's copper layers, not every copper layer
  KiCad could have;
- the outline is measured with half the width of the line it is drawn with,
  and includes whatever a footprint draws on Edge.Cuts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import geometry
from .sexpr import Document, List, text

NM = 1_000_000

TECHNICAL_WILDCARDS = {
    "*.Mask": ("F.Mask", "B.Mask"),
    "*.Paste": ("F.Paste", "B.Paste"),
    "*.SilkS": ("F.SilkS", "B.SilkS"),
    "*.Adhes": ("F.Adhes", "B.Adhes"),
    "*.CrtYd": ("F.CrtYd", "B.CrtYd"),
    "*.Fab": ("F.Fab", "B.Fab"),
    "F&B.Cu": ("F.Cu", "B.Cu"),
}


def nm(atom: str) -> int:
    return round(float(atom) * NM)


@dataclass(slots=True)
class Layer:
    number: int
    name: str  # the standard name the file's items refer to: "F.Cu"
    kind: str  # signal, power, mixed, jumper, user
    user_name: str | None  # what someone renamed it to, if anything


@dataclass(slots=True)
class Pad:
    number: str
    kind: str  # smd, thru_hole, np_thru_hole, connect
    shape: str  # circle, rect, oval, trapezoid, roundrect, custom
    position: tuple[int, int]
    angle: float  # final, on the board
    size: tuple[int, int]
    layers: list[str]
    net: str
    drill: tuple[int, int] | None = None
    drill_offset: tuple[int, int] = (0, 0)
    roundrect_ratio: float = 0.0
    chamfer_ratio: float = 0.0
    chamfer_corners: frozenset[str] = frozenset()
    rect_delta: tuple[int, int] = (0, 0)
    anchor: str | None = None  # custom pads: the shape under the primitives
    node: List | None = field(default=None, repr=False)

    def polygon(self, max_error: float = geometry.MAX_ERROR) -> list[tuple[int, int]] | None:
        """The pad's copper as a polygon, or None for a custom pad."""
        w, h = self.size
        shape = self.shape
        if shape == "circle":
            local = geometry.circle(w / 2, max_error)
        elif shape == "oval":
            local = geometry.oval(w, h, max_error)
        elif shape == "trapezoid":
            local = geometry.trapezoid(w, h, *self.rect_delta)
        elif shape in ("rect", "roundrect", "chamfered_rect"):
            radius = self.roundrect_ratio * min(w, h) if shape != "rect" else 0
            if self.chamfer_ratio > 0 and self.chamfer_corners:
                chamfer = self.chamfer_ratio * min(w, h)
                local = geometry.chamfered_rectangle(
                    w, h, chamfer, set(self.chamfer_corners), radius, max_error
                )
            else:
                local = geometry.rounded_rectangle(w, h, radius, max_error)
        else:
            return None
        ox, oy = self.drill_offset
        local = [(x + ox, y + oy) for x, y in local]
        return geometry.place(local, *self.position, self.angle)

    def bbox(self, max_error: float = geometry.MAX_ERROR) -> tuple[int, int, int, int]:
        """The box around the pad's copper, custom pads included: the anchor
        shape and every primitive, each primitive's line width counted."""
        polygon = self.polygon(max_error)
        if polygon is not None:
            return geometry.bbox(polygon)
        w, h = self.size
        anchor = (
            geometry.circle(w / 2, max_error) if self.anchor == "circle"
            else geometry.rectangle(w, h)
        )  # fmt: skip
        boxes = [geometry.bbox(geometry.place(anchor, *self.position, self.angle))]
        primitives = self.node.find("primitives") if self.node is not None else None
        for primitive in primitives.lists() if primitives is not None else []:
            local = shape_points(primitive, max_error)
            if not local:
                continue
            half = stroke_width(primitive) // 2
            x1, y1, x2, y2 = geometry.bbox(geometry.place(local, *self.position, self.angle))
            boxes.append((x1 - half, y1 - half, x2 + half, y2 + half))
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )


@dataclass(slots=True)
class Footprint:
    reference: str
    value: str
    library_id: str
    layer: str
    position: tuple[int, int]
    angle: float
    pads: list[Pad]
    locked: bool = False
    attributes: frozenset[str] = frozenset()
    node: List | None = field(default=None, repr=False)


@dataclass(slots=True)
class Track:
    kind: str  # segment or arc
    start: tuple[int, int]
    end: tuple[int, int]
    width: int
    layer: str
    net: str
    mid: tuple[int, int] | None = None
    node: List | None = field(default=None, repr=False)


@dataclass(slots=True)
class Via:
    position: tuple[int, int]
    diameter: int
    drill: int
    layers: list[str]  # every copper layer it passes through, top down
    net: str
    kind: str = "through"  # through, blind, buried, micro
    node: List | None = field(default=None, repr=False)


@dataclass(slots=True)
class Zone:
    net: str
    layers: list[str]
    name: str
    priority: int
    rule_area: bool
    outlines: list[list[tuple[int, int]]]
    filled: dict[str, list[list[tuple[int, int]]]]
    node: List | None = field(default=None, repr=False)


class Board:
    """The objects of one `.kicad_pcb`, read on demand from its document."""

    def __init__(self, document: Document, path: Path | None = None) -> None:
        self.document = document
        self.path = path
        self.root = document.root
        if self.root.head != "kicad_pcb":
            raise ValueError(f"not a board: the file starts with ({self.root.head} ...)")
        self.version = int(self.root.find("version").atom(1))
        self.layers = self._layers()
        self._net_names = self._net_table()
        self._footprints: list[Footprint] | None = None
        self._tracks: list[Track] | None = None
        self._vias: list[Via] | None = None
        self._zones: list[Zone] | None = None

    @classmethod
    def load(cls, path: str | Path) -> Board:
        path = Path(path)
        return cls(Document.load(path), path)

    # -- layers and nets ----------------------------------------------------------

    def _layers(self) -> list[Layer]:
        table = self.root.find("layers")
        out = []
        for entry in table.lists() if table is not None else []:
            values = entry.values()
            number = int(entry.head)
            out.append(Layer(number, values[0], values[1], values[2] if len(values) > 2 else None))
        return out

    @property
    def copper_layers(self) -> list[str]:
        """Copper, top to bottom: F.Cu, In1.Cu ... InN.Cu, B.Cu."""
        names = [layer.name for layer in self.layers if layer.name.endswith(".Cu")]
        inner = sorted((n for n in names if n.startswith("In")), key=lambda n: int(n[2:-3]))
        return (["F.Cu"] if "F.Cu" in names else []) + inner + (["B.Cu"] if "B.Cu" in names else [])

    def _net_table(self) -> dict[str, str]:
        """KiCad 9's numbered nets. KiCad 10 names them on every item instead."""
        table = {}
        for entry in self.root.find_all("net"):
            number, name = entry.atom(1), entry.value(2)
            if number is not None and name is not None:
                table[number] = name
        return table

    def net_of(self, node: List) -> str:
        """The net an item belongs to, by name, whichever format wrote it."""
        net = node.find("net")
        if net is None:
            return ""
        first = net.atom(1)
        if first is None:
            return ""
        if first.startswith('"'):
            return text(first)  # KiCad 10: (net "GND")
        if len(net.items) > 2:
            return net.value(2)  # KiCad 9 on a pad: (net 3 "GND")
        return self._net_names.get(first, "")  # KiCad 9 on a track: (net 3)

    def expand_layers(self, names: list[str]) -> list[str]:
        out: list[str] = []
        for name in names:
            if name == "*.Cu":
                out += self.copper_layers
            elif name in TECHNICAL_WILDCARDS:
                out += TECHNICAL_WILDCARDS[name]
            else:
                out.append(name)
        return out

    # -- items --------------------------------------------------------------------

    @property
    def footprints(self) -> list[Footprint]:
        if self._footprints is None:
            self._footprints = [self._footprint(n) for n in self.root.find_all("footprint")]
        return self._footprints

    @property
    def tracks(self) -> list[Track]:
        if self._tracks is None:
            self._tracks = [
                self._track(n) for n in self.root.lists() if n.head in ("segment", "arc")
            ]
        return self._tracks

    @property
    def vias(self) -> list[Via]:
        if self._vias is None:
            self._vias = [self._via(n) for n in self.root.find_all("via")]
        return self._vias

    @property
    def zones(self) -> list[Zone]:
        if self._zones is None:
            self._zones = [self._zone(n) for n in self.root.find_all("zone")]
        return self._zones

    def _footprint(self, node: List) -> Footprint:
        at = node.find("at")
        position = (nm(at.atom(1)), nm(at.atom(2)))
        angle = float(at.atom(3)) if at.atom(3) is not None else 0.0
        properties = {p.value(1): p.value(2) for p in node.find_all("property")}
        attr = node.find("attr")
        # KiCad 8 and later write (locked yes); earlier ones a bare atom.
        locked_node = node.find("locked")
        locked = (locked_node is not None and locked_node.value(1) != "no") or "locked" in [
            i for i in node.items[2:4] if isinstance(i, str)
        ]
        return Footprint(
            reference=properties.get("Reference", ""),
            value=properties.get("Value", ""),
            library_id=node.value(1) or "",
            layer=node.find("layer").value(1),
            position=position,
            angle=angle,
            pads=[self._pad(p, position, angle) for p in node.find_all("pad")],
            locked=locked,
            attributes=frozenset(attr.values()) if attr is not None else frozenset(),
            node=node,
        )

    def _pad(self, node: List, origin: tuple[int, int], footprint_angle: float) -> Pad:
        at = node.find("at")
        lx, ly = nm(at.atom(1)), nm(at.atom(2))
        rx, ry = geometry.rotate(lx, ly, footprint_angle)
        size = node.find("size")
        drill = node.find("drill")
        drill_size = drill_offset = None
        if drill is not None:
            atoms = [a for a in drill.items[1:] if isinstance(a, str)]
            numbers = [a for a in atoms if a != "oval"]
            if numbers:
                d1 = nm(numbers[0])
                drill_size = (d1, nm(numbers[1]) if len(numbers) > 1 else d1)
            offset = drill.find("offset")
            if offset is not None:
                drill_offset = (nm(offset.atom(1)), nm(offset.atom(2)))
        delta = node.find("rect_delta")
        ratio = node.find("roundrect_rratio")
        chamfer = node.find("chamfer_ratio")
        corners = node.find("chamfer")
        options = node.find("options")
        anchor = options.find("anchor") if options is not None else None
        layers = node.find("layers")
        return Pad(
            number=node.value(1) or "",
            kind=node.atom(2),
            shape=node.atom(3),
            position=(round(origin[0] + rx), round(origin[1] + ry)),
            # Already the pad's angle on the board; KiCad leaves it out at 0.
            angle=float(at.atom(3)) if at.atom(3) is not None else 0.0,
            size=(nm(size.atom(1)), nm(size.atom(2))),
            layers=self.expand_layers(layers.values() if layers is not None else []),
            net=self.net_of(node),
            drill=drill_size,
            drill_offset=drill_offset or (0, 0),
            roundrect_ratio=float(ratio.atom(1)) if ratio is not None else 0.0,
            chamfer_ratio=float(chamfer.atom(1)) if chamfer is not None else 0.0,
            chamfer_corners=frozenset(corners.values()) if corners is not None else frozenset(),
            rect_delta=(nm(delta.atom(1)), nm(delta.atom(2))) if delta is not None else (0, 0),
            anchor=anchor.atom(1) if anchor is not None else None,
            node=node,
        )

    def _track(self, node: List) -> Track:
        def point(name: str) -> tuple[int, int] | None:
            found = node.find(name)
            return (nm(found.atom(1)), nm(found.atom(2))) if found is not None else None

        return Track(
            kind=node.head,
            start=point("start"),
            end=point("end"),
            mid=point("mid"),
            width=nm(node.find("width").atom(1)),
            layer=node.find("layer").value(1),
            net=self.net_of(node),
            node=node,
        )

    def _via(self, node: List) -> Via:
        at = node.find("at")
        ends = node.find("layers").values()
        stack = self.copper_layers
        if len(ends) == 2 and ends[0] in stack and ends[1] in stack:
            i, j = sorted((stack.index(ends[0]), stack.index(ends[1])))
            layers = stack[i : j + 1]
        else:
            layers = self.expand_layers(ends)
        kind = node.atom(1) if node.atom(1) in ("blind", "buried", "micro") else "through"
        return Via(
            position=(nm(at.atom(1)), nm(at.atom(2))),
            diameter=nm(node.find("size").atom(1)),
            drill=nm(node.find("drill").atom(1)),
            layers=layers,
            net=self.net_of(node),
            kind=kind,
            node=node,
        )

    def _zone(self, node: List) -> Zone:
        single = node.find("layer")
        several = node.find("layers")
        names = [single.value(1)] if single is not None else (several.values() if several else [])
        filled: dict[str, list[list[tuple[int, int]]]] = {}
        for poly in node.find_all("filled_polygon"):
            layer = poly.find("layer").value(1)
            filled.setdefault(layer, []).append(points(poly.find("pts")))
        name = node.find("name")
        priority = node.find("priority")
        return Zone(
            net=self.net_of(node),
            layers=self.expand_layers(names),
            name=name.value(1) if name is not None else "",
            priority=int(priority.atom(1)) if priority is not None else 0,
            rule_area=node.find("keepout") is not None or node.find("rule_area") is not None,
            outlines=[points(p.find("pts")) for p in node.find_all("polygon")],
            filled=filled,
            node=node,
        )

    # -- the outline ---------------------------------------------------------------

    def edge_shapes(self) -> list[List]:
        """The graphic shapes drawn on Edge.Cuts: the board's outline."""
        out = []
        for node in self.root.lists():
            if node.head in ("gr_line", "gr_arc", "gr_circle", "gr_rect", "gr_poly"):
                layer = node.find("layer")
                if layer is not None and layer.value(1) == "Edge.Cuts":
                    out.append(node)
        return out

    def edge_bbox(self, max_error: float = geometry.MAX_ERROR) -> tuple[int, int, int, int] | None:
        """The box around the outline, the drawn line's width included --
        which is how KiCad measures it too. A footprint can draw on Edge.Cuts
        as well -- an antenna's keep-out, a cut-out for a connector -- and
        that is part of the outline like anything else."""
        boxes = []
        for node in self.edge_shapes():
            half = stroke_width(node) // 2
            x1, y1, x2, y2 = geometry.bbox(shape_points(node, max_error))
            boxes.append((x1 - half, y1 - half, x2 + half, y2 + half))
        for fp in self.footprints:
            for node in fp.node.lists():
                if node.head not in ("fp_line", "fp_arc", "fp_circle", "fp_rect", "fp_poly"):
                    continue
                layer = node.find("layer")
                if layer is None or layer.value(1) != "Edge.Cuts":
                    continue
                half = stroke_width(node) // 2
                placed = geometry.place(shape_points(node, max_error), *fp.position, fp.angle)
                x1, y1, x2, y2 = geometry.bbox(placed)
                boxes.append((x1 - half, y1 - half, x2 + half, y2 + half))
        if not boxes:
            return None
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )


def points(pts: List | None, max_error: float = geometry.MAX_ERROR) -> list[tuple[int, int]]:
    """The vertices of a (pts ...), arcs within it followed as chords."""
    out: list[tuple[int, int]] = []
    if pts is None:
        return out
    for item in pts.lists():
        if item.head == "xy":
            out.append((nm(item.atom(1)), nm(item.atom(2))))
        elif item.head == "arc":
            s, m, e = (item.find(k) for k in ("start", "mid", "end"))
            chords = geometry.arc_through(
                (nm(s.atom(1)), nm(s.atom(2))),
                (nm(m.atom(1)), nm(m.atom(2))),
                (nm(e.atom(1)), nm(e.atom(2))),
                max_error,
            )
            out += [(round(x), round(y)) for x, y in chords]
    return out


def stroke_width(node: List) -> int:
    """A graphic's line width: (stroke (width w)) since KiCad 7, (width w) before."""
    stroke = node.find("stroke")
    width = stroke.find("width") if stroke is not None else node.find("width")
    return nm(width.atom(1)) if width is not None else 0


def shape_points(node: List, max_error: float = geometry.MAX_ERROR) -> list[tuple[int, int]]:
    """Points on a graphic shape's centre line."""

    def point(name: str) -> tuple[int, int]:
        found = node.find(name)
        return nm(found.atom(1)), nm(found.atom(2))

    head = node.head
    if head in ("gr_line", "fp_line"):
        return [point("start"), point("end")]
    if head in ("gr_rect", "fp_rect"):
        (x1, y1), (x2, y2) = point("start"), point("end")
        return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    if head in ("gr_arc", "fp_arc"):
        chords = geometry.arc_through(point("start"), point("mid"), point("end"), max_error)
        return [(round(x), round(y)) for x, y in chords]
    if head in ("gr_circle", "fp_circle"):
        (cx, cy), (ex, ey) = point("center"), point("end")
        r = ((ex - cx) ** 2 + (ey - cy) ** 2) ** 0.5
        return [(round(cx + x), round(cy + y)) for x, y in geometry.circle(r, max_error)]
    if head in ("gr_poly", "fp_poly"):
        return points(node.find("pts"), max_error)
    return []
