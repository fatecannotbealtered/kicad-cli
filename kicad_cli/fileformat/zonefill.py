"""Zones filled with copper, as KiCad fills them -- the work `pcbnew`'s zone
filler did for every command that wrote a board.

A zone's fill, on each of its layers, is its outline within the board less
the copper of every other net grown by the clearance between them, the
holes, the board's edge and keep-outs, with what is too thin to keep shrunk
away, and the pads of its own net joined by thermal spokes. Measured on
boards KiCad filled (`tests/fixtures/zonefill/`, and KiCad's demos, whose
DRC finds the same in this tool's fills as in KiCad's):

- what is cut out keeps half a micrometre further off than the clearance
  asks (`MARGIN`), its arcs drawn outside the arc; the clearance is the
  larger of the zone's and the net classes', or a pad's or footprint's own
  instead of both, even when smaller; never less than the board's minimum;
- a hole with no copper keeps the board's hole clearance; a hole in a pad
  of the zone's own net is cut out as it is; the board's edge keeps its
  edge clearance; a zone of another net and of a higher priority, its own
  clearance;
- what is narrower than the zone's minimum thickness goes, and the fill's
  outward corners are rounded by half that thickness: the fill is shrunk by
  half of it and grown back;
- a pad of the zone's net joined by thermal relief is set apart by the
  thermal gap and joined by spokes as wide as the zone says, through its
  middle, square to its sides, at 45 degrees on a round pad -- each kept
  where it reaches the fill, another pad of the net, or another pad's spoke;
- a piece of fill touching nothing of the zone's net is taken away -- but
  where no piece touches anything of it, all are kept;
- text on copper is cut out by a box surely round it: its strokes are in
  KiCad's stroke font, which this tool does not have yet.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import geometry, polygons
from .board import Board, Footprint, Pad, Track, Via, Zone, nm
from .polygons import Ring

MARGIN = 500  # nm: what a fill keeps clear of beyond the clearance
MAX_ERROR = geometry.MAX_ERROR
THERMAL, SOLID, NONE = "thermal", "solid", "none"


# -- shapes, drawn outside their arcs -------------------------------------------------------


def arc_outside(cx: float, cy: float, r: float, start_deg: float, sweep_deg: float,
                max_error: float = MAX_ERROR) -> list[tuple[float, float]]:  # fmt: skip
    """A polygon's way round an arc that stays outside it: from the arc's
    start, by corners just outside it whose chords touch the arc, to its
    end. Angles as `geometry.arc` takes them."""
    if r <= 0:
        return [(cx, cy)]
    n = geometry.segments_for(r, sweep_deg, max_error)
    step = math.radians(sweep_deg) / n
    far = r / math.cos(step / 2)
    a1 = math.radians(start_deg)
    out = [(cx + r * math.cos(a1), cy + r * math.sin(a1))]
    for k in range(n):
        a = a1 + step * (k + 0.5)
        out.append((cx + far * math.cos(a), cy + far * math.sin(a)))
    a2 = a1 + step * n
    out.append((cx + r * math.cos(a2), cy + r * math.sin(a2)))
    return out


def circle_outside(cx: float, cy: float, r: float, max_error: float = MAX_ERROR) -> Ring:
    n = geometry.segments_for(r, 360, max_error)
    n = max(8, n)
    far = r / math.cos(math.pi / n)
    angles = [2 * math.pi * (k + 0.5) / n for k in range(n)]
    return _ints([(cx + far * math.cos(a), cy + far * math.sin(a)) for a in angles])


def segment_outside(a, b, r: float, max_error: float = MAX_ERROR) -> Ring:
    """A track's copper grown to `r` about its centre line: two half-circles
    and the sides between them."""
    (ax, ay), (bx, by) = a, b
    if (ax, ay) == (bx, by):
        return circle_outside(ax, ay, r, max_error)
    angle = math.degrees(math.atan2(by - ay, bx - ax))
    points = arc_outside(bx, by, r, angle - 90, 180, max_error)
    points += arc_outside(ax, ay, r, angle + 90, 180, max_error)
    return _ints(points)


def rounded_rect_outside(w: float, h: float, radius: float,
                         max_error: float = MAX_ERROR) -> list[tuple[float, float]]:  # fmt: skip
    """A rectangle of w by h about the origin, its corners rounded by
    `radius`, the arcs drawn outside."""
    radius = min(max(radius, 0), w / 2, h / 2)
    if radius <= 0:
        return geometry.rectangle(w, h)
    hw, hh = w / 2 - radius, h / 2 - radius
    out: list[tuple[float, float]] = []
    for cx, cy, start in ((hw, hh, 0), (-hw, hh, 90), (-hw, -hh, 180), (hw, -hh, 270)):
        out += arc_outside(cx, cy, radius, start, 90, max_error)
    return out


def _ints(points) -> Ring:
    ring = [(round(x), round(y)) for x, y in points]
    return [p for i, p in enumerate(ring) if p != ring[i - 1]]


def pad_outside(pad: Pad, grow: float, max_error: float = MAX_ERROR) -> list[Ring]:
    """A pad's copper grown by `grow` all round."""
    w, h = pad.size
    shape = pad.shape
    if shape == "circle":
        x, y = pad.position
        ox, oy = (
            geometry.place([pad.drill_offset], 0, 0, pad.angle)[0]
            if any(pad.drill_offset)
            else (0, 0)
        )
        return [circle_outside(x + ox, y + oy, w / 2 + grow, max_error)]
    if shape in ("rect", "roundrect", "oval") and not (
        pad.chamfer_ratio > 0 and pad.chamfer_corners
    ):
        if shape == "oval":
            radius = min(w, h) / 2
        elif shape == "roundrect":
            radius = pad.roundrect_ratio * min(w, h)
        else:
            radius = 0
        local = rounded_rect_outside(w + 2 * grow, h + 2 * grow, radius + grow, max_error)
        ox, oy = pad.drill_offset
        local = [(px + ox, py + oy) for px, py in local]
        return [geometry.place(local, *pad.position, pad.angle)]
    outline = pad.polygon(max_error)
    if outline is None:
        outline = _custom_outline(pad, max_error)
    if not outline:
        return []
    # Its own polygon has its arcs inside: grow by the error more to stay out.
    return polygons.offset(
        [outline] if isinstance(outline[0], tuple) else outline,
        grow + max_error,
        max_error,
        outside=True,
    )


def _custom_outline(pad: Pad, max_error: float) -> list[Ring]:
    """A custom pad's copper: its anchor and its primitives, as one."""
    from .board import shape_points, stroke_width  # noqa: PLC0415

    w, h = pad.size
    anchor = (
        geometry.circle(w / 2, max_error) if pad.anchor == "circle" else geometry.rectangle(w, h)
    )
    rings = [geometry.place(anchor, *pad.position, pad.angle)]
    primitives = pad.node.find("primitives") if pad.node is not None else None
    for primitive in primitives.lists() if primitives is not None else []:
        local = shape_points(primitive, max_error)
        if not local:
            continue
        placed = geometry.place(local, *pad.position, pad.angle)
        width = stroke_width(primitive)
        filled = primitive.find("fill")
        if primitive.head in ("gr_poly", "gr_rect", "gr_circle") and (
            filled is None or filled.atom(1) in ("yes", "solid")
        ):
            rings.append(placed)
        if width > 0:
            for i in range(len(placed) - 1):
                rings.append(segment_outside(placed[i], placed[i + 1], width / 2, max_error))
    # The anchor and the primitives may wind either way: one way round each.
    return polygons.union([r if polygons.area(r) > 0 else r[::-1] for r in rings])


# -- what a zone is made of -----------------------------------------------------------------


@dataclass
class Settings:
    """A zone's own settings, from its node."""

    clearance: int
    min_thickness: int
    thermal_gap: int
    spoke_width: int
    connect: str  # thermal, solid, none, thru_hole_only
    islands: int  # 0 remove all, 1 keep all, 2 remove those smaller than min_island_area
    min_island_area: float = 0.0
    filled: bool = True


def zone_settings(zone: Zone) -> Settings:
    node = zone.node

    def length(parent, head, default):
        found = parent.find(head) if parent is not None else None
        return nm(found.atom(1)) if found is not None and found.atom(1) is not None else default

    connect = node.find("connect_pads") if node is not None else None
    mode = connect.atom(1) if connect is not None else None
    fill = node.find("fill") if node is not None else None
    islands = fill.find("island_removal_mode") if fill is not None else None
    area = fill.find("island_area_min") if fill is not None else None
    return Settings(
        clearance=length(connect, "clearance", 500_000),
        min_thickness=length(node, "min_thickness", 250_000),
        thermal_gap=length(fill, "thermal_gap", 500_000),
        spoke_width=length(fill, "thermal_bridge_width", 500_000),
        connect={"yes": SOLID, "no": NONE, "thru_hole_only": "thru_hole_only"}.get(
            mode or "", THERMAL
        ),
        islands=int(islands.atom(1)) if islands is not None else 0,
        min_island_area=float(area.atom(1)) * 1e12 if area is not None else 0.0,
    )


@dataclass
class Fill:
    """A zone's fill on each of its layers."""

    zone: Zone
    layers: dict[str, list[Ring]] = field(default_factory=dict)


class Filler:
    """Fills a board's zones, by the rules of its project (`drc.Settings`):
    the net classes' clearances and the board setup's minimums."""

    def __init__(self, board: Board, settings=None) -> None:
        from .drc.settings import Settings as Project  # noqa: PLC0415

        self.board = board
        self.settings = settings if settings is not None else Project.of(None)
        rules = self.settings
        self.min_clearance = rules.nm("min_clearance")
        self.hole_clearance = rules.nm("min_hole_clearance")
        self.edge_clearance = rules.nm("min_copper_edge_clearance")
        self.max_error = rules.nm("max_error") or MAX_ERROR
        self.pads: list[tuple[Footprint, Pad]] = [
            (fp, pad) for fp in board.footprints for pad in fp.pads
        ]
        self.tracks: list[Track] = board.tracks
        self.vias: list[Via] = board.vias
        self._area: list[Ring] | None = None
        self._edges: list[list[tuple[int, int]]] | None = None
        self._fills: dict[int, Fill] = {}

    # -- what keeps the zone's copper off --------------------------------------------------

    def clearance(self, zone: Zone, settings: Settings, net: str, own: int | None = None) -> int:
        """How far the zone keeps from copper of a net: the larger of its own
        clearance and the net classes' -- or a pad's or footprint's own
        clearance instead of both, even when smaller (pic_programmer's JP1)
        -- and never less than the board's minimum."""
        classes = self.settings.netclasses
        if own is not None:
            value = own
        else:
            value = max(settings.clearance, classes.of(zone.net).nm("clearance"),
                        classes.of(net).nm("clearance"))  # fmt: skip
        return max(value, self.min_clearance)

    def board_area(self) -> list[Ring]:
        """What Edge.Cuts closes round: outlines less their cut-outs."""
        if self._area is None:
            from .drc import outline  # noqa: PLC0415

            rings, edges = [], []
            for chain, closed in outline._chains(outline.drawings(self.board)):
                points = [p for _, piece in chain for p in piece]
                edges.append(points)
                if closed:
                    rings.append([p for i, p in enumerate(points) if p != points[i - 1]])
            self._area = polygons.union(rings, polygons.EVENODD) if rings else []
            self._edges = edges
        return self._area

    def fill(self, zone: Zone) -> Fill:
        """A zone's fill; zones of higher priority it keeps clear of are
        filled first."""
        found = self._fills.get(id(zone))
        if found is None:
            settings = zone_settings(zone)
            found = Fill(zone)
            for layer in zone.layers:
                found.layers[layer] = self.fill_layer(zone, settings, layer)
            self._fills[id(zone)] = found
        return found

    def fill_layer(self, zone: Zone, settings: Settings, layer: str) -> list[Ring]:
        outline = polygons.union([ring for ring in zone.outlines if len(ring) >= 3])
        area = self.board_area()
        if area:
            outline = polygons.intersection(outline, area)
        if not outline:
            return []
        box = _box(outline)
        reach = settings.clearance + settings.thermal_gap + settings.min_thickness + 2_000_000
        cut: list[Ring] = []
        reliefs: list[Ring] = []
        spokes: list[tuple[Ring, int]] = []  # each with the pad it leaves
        own_pads: list[list[Ring]] = []
        own_holes: list[Ring] = []
        anchors: list[Ring] = []
        for fp, pad in self.pads:
            if not _near(pad.bbox(), box, reach):
                continue
            copper = layer in pad.layers
            if pad.kind == "np_thru_hole" or (not copper and pad.drill):
                cut += self._hole(pad, self.hole_clearance + MARGIN)
                continue
            if not copper:
                continue
            if pad.net and pad.net == zone.net:
                anchors += pad_outside(pad, 0, self.max_error)
                mode = _pad_mode(fp, pad, settings.connect)
                if mode == SOLID:
                    continue
                if mode == NONE:
                    grow = self.clearance(zone, settings, pad.net, _own(fp, pad)) + MARGIN
                    cut += pad_outside(pad, grow, self.max_error)
                    continue
                reliefs += pad_outside(pad, settings.thermal_gap, self.max_error)
                copper = pad_outside(pad, 0, self.max_error)
                for spoke in self._spokes(pad, settings):
                    spokes.append((spoke, len(own_pads)))
                own_pads.append(copper)
                if pad.drill:
                    own_holes += self._hole(pad, 0)
                continue
            grow = self.clearance(zone, settings, pad.net, _own(fp, pad)) + MARGIN
            cut += pad_outside(pad, grow, self.max_error)
        for track in self.tracks:
            if track.layer != layer:
                continue
            if track.net and track.net == zone.net:
                anchors.append(segment_outside(track.start, track.end, track.width / 2,
                                               self.max_error))  # fmt: skip
                continue
            grow = track.width / 2 + self.clearance(zone, settings, track.net) + MARGIN
            ends = geometry.bbox([track.start, track.end])
            if track.kind == "arc" and track.mid is not None:
                points = geometry.arc_through(track.start, track.mid, track.end, self.max_error)
                if not _near(geometry.bbox(points), box, grow):
                    continue
                for i in range(len(points) - 1):
                    cut.append(segment_outside(points[i], points[i + 1], grow + self.max_error,
                                               self.max_error))  # fmt: skip
            elif _near(ends, box, grow):
                cut.append(segment_outside(track.start, track.end, grow, self.max_error))
        for via in self.vias:
            if layer not in via.layers:
                continue
            if via.net and via.net == zone.net:
                anchors.append(circle_outside(*via.position, via.diameter / 2, self.max_error))
                continue
            grow = self.clearance(zone, settings, via.net) + MARGIN
            x, y = via.position
            if _near((x, y, x, y), box, via.diameter / 2 + grow):
                cut.append(circle_outside(x, y, via.diameter / 2 + grow, self.max_error))
        cut += self._drawings(zone, settings, layer, box)
        cut += self._texts(zone, settings, layer, box)
        cut += self._edge_cut(box)
        cut += self._zones(zone, settings, layer)
        fill = polygons.difference(outline, cut + reliefs) if cut or reliefs else outline
        half = settings.min_thickness / 2
        opened = fill
        if half > 0:
            shrunk = polygons.offset(fill, -half, self.max_error)
            opened = polygons.offset(shrunk, half, self.max_error)
        joined = self._reaching(spokes, opened, outline, cut, own_pads) if spokes else []
        # In one pass: shrunk and grown back -- kept to where it was, which a
        # chord's error can stray past at a corner -- with the spokes, less
        # the holes of its own pads.
        fill = polygons.boolean(
            [opened, fill, joined, own_holes],
            lambda thick, was, spoke, hole: ((thick and was) or spoke) and not hole,
        )
        if settings.islands != 1:
            fill = _keep_islands(fill, anchors, settings)
        return fill

    def _drawings(self, zone: Zone, settings: Settings, layer: str, box) -> list[Ring]:
        """Copper drawn on the layer -- the board's and footprints' -- grown
        by the clearance."""
        from .board import shape_points, stroke_width  # noqa: PLC0415

        out: list[Ring] = []
        heads = ("gr_line", "gr_arc", "gr_circle", "gr_rect", "gr_poly")
        nodes = [(node, None) for node in self.board.root.lists() if node.head in heads]
        for fp in self.board.footprints:
            if fp.node is None:
                continue
            for node in fp.node.lists():
                if node.head in ("fp_line", "fp_arc", "fp_circle", "fp_rect", "fp_poly"):
                    nodes.append((node, fp))
        for node, fp in nodes:
            on = node.find("layer")
            if on is None or on.value(1) != layer:
                continue
            net = self.board.net_of(node) if node.find("net") is not None else ""
            if net and net == zone.net:
                continue
            points = shape_points(node, self.max_error)
            if not points:
                continue
            if fp is not None:
                points = geometry.place(points, *fp.position, fp.angle)
            grow = stroke_width(node) / 2 + self.clearance(zone, settings, net) + MARGIN
            if not _near(geometry.bbox(points), box, grow):
                continue
            closed = node.head.split("_", 1)[1] in ("circle", "rect", "poly")
            fill = node.find("fill")
            if closed and fill is not None and fill.atom(1) in ("yes", "solid"):
                out += polygons.offset(
                    [points], grow + self.max_error, self.max_error, outside=True
                )
            chain = points + [points[0]] if closed else points
            for i in range(len(chain) - 1):
                out.append(segment_outside(chain[i], chain[i + 1], grow, self.max_error))
        return out

    def _reaching(self, spokes: list[tuple[Ring, int]], fill: list[Ring], outline: list[Ring],
                  cut: list[Ring], own_pads: list[list[Ring]]) -> list[Ring]:  # fmt: skip
        """The spokes, less what keeps other nets' copper off, that reach the
        fill, another pad of the zone's net, or another pad's spoke: one
        cut short of all of them by a neighbour's clearance is left out."""
        cut_boxes = [(geometry.bbox(r), r) for r in cut]
        outline_boxes = [(geometry.bbox(r), r) for r in outline]
        covered = _Cover(fill)
        pads = [(_box(rings), _Cover(rings)) for rings in own_pads]
        pieces = []
        for spoke, origin in spokes:
            box = geometry.bbox(spoke)
            piece = polygons.intersection(
                [spoke], [r for b, r in outline_boxes if _near(b, box, 0)]
            )
            near_cut = [r for b, r in cut_boxes if _near(b, box, 0)]
            if near_cut and piece:
                piece = polygons.difference(piece, near_cut)
            if piece:
                pieces.append((piece, origin, _box(piece), [p for r in piece for p in _probes(r)]))
        others = [(box, origin, _Cover(piece)) for piece, origin, box, _ in pieces]
        kept = []
        for piece, origin, box, probes in pieces:
            reaches = any(covered.holds(*p) for p in probes)
            if not reaches:
                reaches = any(
                    k != origin and _near(b, box, 0) and any(c.holds(*p) for p in probes)
                    for k, (b, c) in enumerate(pads)
                )
            if not reaches:
                reaches = any(
                    o != origin and _near(b, box, 0) and any(c.holds(*p) for p in probes)
                    for b, o, c in others
                )
            if reaches:
                kept += piece
        return kept

    def _texts(self, zone: Zone, settings: Settings, layer: str, box) -> list[Ring]:
        """Text on the layer, cut out by the box round it grown by the
        clearance. KiCad cuts out its strokes, drawn in its stroke font,
        which this tool does not have yet: inside the box, what KiCad
        would fill between the strokes is left empty."""
        out: list[Ring] = []
        nodes = [(node, None) for node in self.board.root.lists() if node.head == "gr_text"]
        for fp in self.board.footprints:
            if fp.node is None:
                continue
            for node in fp.node.lists():
                if node.head in ("fp_text", "property"):
                    nodes.append((node, fp))
        for node, fp in nodes:
            on = node.find("layer")
            if on is None or on.value(1) != layer:
                continue
            if node.find("hide") is not None:
                continue
            effects = node.find("effects")
            if effects is not None and effects.find("hide") is not None:
                continue
            text = node.value(2) if node.head == "property" else node.value(1)
            if node.head == "fp_text":
                text = node.value(2)
            ring = _text_box(node, text or "", fp)
            if ring is None:
                continue
            grow = self.clearance(zone, settings, "") + MARGIN
            if _near(geometry.bbox(ring), box, grow):
                out += polygons.offset([ring], grow + self.max_error, self.max_error, outside=True)
        return out

    def _edge_cut(self, box) -> list[Ring]:
        """The board's edges grown by its edge clearance."""
        self.board_area()
        grow = self.edge_clearance + MARGIN
        out = []
        for points in self._edges or ():
            for i in range(len(points) - 1):
                a, b = points[i], points[i + 1]
                if _near(geometry.bbox([a, b]), box, grow):
                    out.append(segment_outside(a, b, grow, self.max_error))
        return out

    def _zones(self, zone: Zone, settings: Settings, layer: str) -> list[Ring]:
        """Keep-outs on the layer, and the fills of other nets' zones of a
        higher priority, grown by the clearance."""
        out: list[Ring] = []
        for other in self.board.zones:
            if other is zone or layer not in other.layers:
                continue
            if other.rule_area:
                keepout = other.node.find("keepout") if other.node is not None else None
                pour = keepout.find("copperpour") if keepout is not None else None
                if pour is not None and pour.atom(1) == "not_allowed":
                    out += [ring for ring in other.outlines if len(ring) >= 3]
                continue
            if other.priority <= zone.priority or (other.net and other.net == zone.net):
                continue
            theirs = self.fill(other).layers.get(layer, [])
            if theirs:
                grow = self.clearance(zone, settings, other.net) + MARGIN
                out += polygons.offset(theirs, grow, self.max_error, outside=True)
        return out

    def _hole(self, pad: Pad, grow: float) -> list[Ring]:
        dx, dy = pad.drill or (0, 0)
        x, y = pad.position
        if dx == dy:
            return [circle_outside(x, y, dx / 2 + grow, self.max_error)]
        # An oval hole, along its longer side.
        half = abs(dx - dy) / 2
        if dx > dy:
            a, b = (-half, 0), (half, 0)
        else:
            a, b = (0, -half), (0, half)
        a, b = geometry.place([a, b], x, y, pad.angle)
        return [segment_outside(a, b, min(dx, dy) / 2 + grow, self.max_error)]

    def _spokes(self, pad: Pad, settings: Settings) -> list[Ring]:
        """Spokes from the pad's middle out across the gap: square to its
        sides, or at 45 degrees on a round pad."""
        w, h = pad.size
        reach = max(w, h) / 2 + settings.thermal_gap + settings.min_thickness
        half = settings.spoke_width / 2
        tilt = 45 if pad.shape == "circle" else 0
        rings = []
        for k in range(4):
            angle = tilt + 90 * k
            local = [(0, -half), (reach, -half), (reach, half), (0, half)]
            rings.append(geometry.place(local, *pad.position, pad.angle + angle))
        return rings


def write(fill: Fill) -> None:
    """A zone's fill put in its node, as KiCad writes one: a
    `filled_polygon` per piece, its holes joined to it, after the outline;
    and `(fill yes ...)`."""
    from .sexpr import List, number, quote, symbol  # noqa: PLC0415

    node = fill.zone.node
    for old in node.find_all("filled_polygon"):
        node.remove(old)
    flag = node.find("fill")
    if flag is not None and flag.atom(1) != "yes":
        if flag.atom(1) is None or flag.atom(1).startswith("("):
            flag.insert(1, symbol("yes"))
        else:
            flag.set(1, symbol("yes"))
    for layer in fill.zone.layers:
        for ring in polygons.fracture(fill.layers.get(layer, [])):
            # KiCad's rings, seen with y down, run the other way round.
            points = [List.new("xy", number(x / 1e6), number(y / 1e6)) for x, y in ring[::-1]]
            node.append(
                List.new(
                    "filled_polygon", List.new("layer", quote(layer)), List.new("pts", *points)
                )
            )


def _text_box(node, text: str, fp) -> Ring | None:
    """A box surely round a text's strokes, from its size alone -- the
    strokes themselves are in KiCad's stroke font, which this tool does not
    have yet."""
    at = node.find("at")
    effects = node.find("effects")
    font = effects.find("font") if effects is not None else None
    size = font.find("size") if font is not None else None
    if at is None or size is None or not text:
        return None
    h, w = nm(size.atom(1)), nm(size.atom(2))
    thickness = font.find("thickness")
    t = nm(thickness.atom(1)) if thickness is not None else round(h / 8)
    lines = text.split("\\n")
    # Generous, so that the strokes are surely inside: a letter is at most
    # about as wide as it is high, and some reach below the line.
    width = max(len(line) for line in lines) * w * 1.05 + t
    height = (len(lines) - 1) * h * 1.62 + h * 1.5 + t
    x, y = nm(at.atom(1)), nm(at.atom(2))
    angle = float(at.atom(3)) if at.atom(3) is not None else 0.0
    justify = effects.find("justify") if effects is not None else None
    words = set(justify.values()) if justify is not None else set()
    x1 = 0 if "left" in words else (-width if "right" in words else -width / 2)
    y1 = 0 if "top" in words else (-height if "bottom" in words else -height / 2)
    local = [(x1, y1), (x1 + width, y1), (x1 + width, y1 + height), (x1, y1 + height)]
    if fp is not None:
        # A footprint's text is placed in the footprint's frame.
        placed = geometry.place(local, x, y, angle)
        return placed
    return geometry.place(local, x, y, angle)


def _own(fp: Footprint, pad: Pad) -> int | None:
    """A pad's own clearance, or its footprint's."""
    for node in (pad.node, fp.node):
        found = None
        if node is not None:
            found = next((c for c in node.lists() if c.head == "clearance"), None)
        if found is not None and found.atom(1) is not None:
            return nm(found.atom(1))
    return None


def _keep_islands(fill: list[Ring], anchors: list[Ring], settings: Settings) -> list[Ring]:
    """The pieces of fill touching copper of the zone's net -- and, where the
    zone keeps islands above a size, those big enough; all of them where
    none does. A piece touches a piece of copper where a corner of either is
    inside the other."""
    from .connectivity import Polygon  # noqa: PLC0415

    outers = [r for r in fill if polygons.area(r) > 0]
    holes = [r for r in fill if polygons.area(r) < 0]
    owner: dict[int, list[Ring]] = {i: [] for i in range(len(outers))}
    indexed = [Polygon(r) for r in outers]
    for hole in holes:
        probe = polygons._inside_point(hole)
        best = None
        for i, outer in enumerate(outers):
            box = indexed[i].bbox
            if not (box[0] <= probe[0] <= box[2] and box[1] <= probe[1] <= box[3]):
                continue
            if indexed[i].contains(*probe) and (
                best is None or polygons.area(outer) < polygons.area(outers[best])
            ):
                best = i
        if best is not None:
            owner[best].append(hole)
    shapes = [(geometry.bbox(a), Polygon(a)) for a in anchors]
    kept = []
    joined_any = False
    for i, outer in enumerate(outers):
        piece = _Cover([outer, *owner[i]])
        box = indexed[i].bbox
        joined = False
        for a_box, shape in shapes:
            if not _near(a_box, box, 0):
                continue
            if any(piece.holds(*p) for p in shape.points) or any(
                a_box[0] <= x <= a_box[2] and a_box[1] <= y <= a_box[3] and shape.contains(x, y)
                for ring in (outer, *owner[i])
                for x, y in ring
            ):
                joined = True
                break
        joined_any = joined_any or joined
        size = polygons.area(outer) + sum(polygons.area(h) for h in owner[i])
        if joined or (settings.islands == 2 and size >= settings.min_island_area):
            kept += [outer, *owner[i]]
    # Where nothing of the net touches any piece, KiCad keeps them all.
    return kept if joined_any else fill


class _Cover:
    """Whether points are covered by rings -- outlines less holes -- by
    their winding, each ring indexed."""

    def __init__(self, rings: list[Ring]) -> None:
        from .connectivity import Polygon  # noqa: PLC0415

        self.rings = [(Polygon(r), 1 if polygons.area(r) > 0 else -1) for r in rings if len(r) >= 3]

    def holds(self, x: float, y: float) -> bool:
        winding = 0
        for polygon, sign in self.rings:
            box = polygon.bbox
            if box[0] <= x <= box[2] and box[1] <= y <= box[3] and polygon.contains(x, y):
                winding += sign
        return winding > 0


def _probes(ring: Ring) -> list[tuple[float, float]]:
    """Points just inside a ring: off the middle of each edge, inwards."""
    out = []
    sign = 1 if polygons.area(ring) > 0 else -1
    for i in range(len(ring)):
        (ax, ay), (bx, by) = ring[i - 1], ring[i]
        length = math.hypot(bx - ax, by - ay)
        if length == 0:
            continue
        # The inside of a counter-clockwise ring is on the left of its edges.
        nx, ny = -(by - ay) / length * sign, (bx - ax) / length * sign
        out.append(((ax + bx) / 2 + nx * 2, (ay + by) / 2 + ny * 2))
    return out


def _pad_mode(fp: Footprint, pad: Pad, zone_mode: str) -> str:
    own = pad.zone_connect
    if own is None and fp.node is not None:
        found = fp.node.find("zone_connect")
        own = int(found.atom(1)) if found is not None and found.atom(1) else None
    mode = (
        {0: NONE, 1: THERMAL, 2: SOLID, 3: "thru_hole_only"}.get(own, zone_mode)
        if own is not None
        else zone_mode
    )
    if mode == "thru_hole_only":
        mode = THERMAL if pad.kind == "thru_hole" else SOLID
    return mode


def _box(rings: list[Ring]) -> tuple[int, int, int, int]:
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    return min(xs), min(ys), max(xs), max(ys)


def _near(a, b, reach) -> bool:
    return not (
        a[2] + reach < b[0] or b[2] + reach < a[0] or a[3] + reach < b[1] or b[3] + reach < a[1]
    )


def _between(board: Board, layer: str, span: list[str]) -> bool:
    return False
