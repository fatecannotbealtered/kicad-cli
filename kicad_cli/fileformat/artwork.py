"""A board's artwork: what it puts on each layer, as a plotter draws it.

Three kinds of thing are drawn:

- a `Flash`: a pad's shape stamped at a point -- circle, rectangle, oval,
  rounded rectangle, trapezoid, or for the rest a polygon;
- a `Stroke`: a line, an arc or a closed outline drawn with a round pen;
- a `Fill`: a region filled solid -- a zone's fill, a filled drawing.

`artwork(board, layer)` lists them for one layer in the order the board
holds them: each footprint's drawings and pads, then vias, tracks, zones and
the board's own drawings. A pad on a mask or paste layer is its copper
grown or shrunk by the margins the pad, its footprint or the board set.
Text is not drawn yet: it needs the stroke font.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import geometry
from .board import Board, Footprint, Pad, nm, shape_points, stroke_width
from .sexpr import List

Point = tuple[int, int]
SMALL_DRILL = 350_000  # nm: the mark pcbnew's plots put at each hole


@dataclass
class Flash:
    shape: str  # circle, rect, oval, roundrect, trapezoid, polygon
    at: Point
    size: tuple[int, int]
    angle: float = 0.0  # degrees, as the board turns things
    radius: float = 0.0  # a rounded rectangle's corners
    delta: tuple[int, int] = (0, 0)  # a trapezoid's
    rings: list[list[Point]] | None = None  # a polygon's, placed on the board
    attrs: dict = field(default_factory=dict)


@dataclass
class Stroke:
    points: list[Point]  # a polyline; an arc's start, middle and end
    width: int
    arc: bool = False
    closed: bool = False
    circle: bool = False  # points: the centre and a point on the circle
    attrs: dict = field(default_factory=dict)


@dataclass
class Fill:
    rings: list[list[Point]]
    attrs: dict = field(default_factory=dict)


Shape = Flash | Stroke | Fill
GRAPHICS = ("fp_line", "fp_arc", "fp_circle", "fp_rect", "fp_poly",
            "gr_line", "gr_arc", "gr_circle", "gr_rect", "gr_poly")  # fmt: skip


def _layer_of(node: List) -> str | None:
    found = node.find("layer")
    return found.value(1) if found is not None else None


def _filled(node: List) -> bool:
    fill = node.find("fill")
    if fill is None:
        return False
    return fill.atom(1) in ("yes", "solid")


def _point(node: List, name: str) -> Point:
    found = node.find(name)
    return nm(found.atom(1)), nm(found.atom(2))


def graphic(node: List, fp: Footprint | None, attrs: dict | None = None) -> list[Shape]:
    """A drawing as the plotter draws it: its outline with its pen, and its
    inside filled where it is filled."""
    attrs = attrs or {}
    width = stroke_width(node)
    head = node.head[3:]

    def place(pts):
        return geometry.place(pts, *fp.position, fp.angle) if fp is not None else list(pts)

    if head == "line":
        return [Stroke(place([_point(node, "start"), _point(node, "end")]), width, attrs=attrs)]
    if head == "arc":
        pts = place([_point(node, "start"), _point(node, "mid"), _point(node, "end")])
        return [Stroke(pts, width, arc=True, attrs=attrs)]
    out: list[Shape] = []
    outline = place(shape_points(node))
    if not outline:
        return []
    if _filled(node):
        out.append(Fill([outline], attrs))
    if width > 0 or not _filled(node):
        if head == "circle":
            ring = place([_point(node, "center"), _point(node, "end")])
            out.append(Stroke(ring, width, circle=True, attrs=attrs))
        else:
            out.append(Stroke(outline, width, closed=True, attrs=attrs))
    return out


def _margins(board: Board, fp: Footprint, pad: Pad, which: str) -> tuple[float, float]:
    """A pad's margin and ratio on a mask or paste layer: its own, else its
    footprint's, else the board's -- and none for a pad with no copper,
    which is an aperture drawn as it is to be."""
    if not any(name.endswith(".Cu") for name in board.expand_layers(pad.layers)):
        return 0.0, 0.0

    def local(node, head):
        found = node.find(head) if node is not None else None
        return float(found.atom(1)) if found is not None and found.atom(1) is not None else None

    if which == "mask":
        heads = ("solder_mask_margin", None, "pad_to_mask_clearance", None)
    else:
        heads = ("solder_paste_margin", "solder_paste_margin_ratio",
                 "pad_to_paste_clearance", "pad_to_paste_clearance_ratio")  # fmt: skip
    setup = board.root.find("setup")
    margin = local(pad.node, heads[0])
    if margin is None:
        margin = local(fp.node, heads[0])
    if margin is None:
        margin = local(setup, heads[2]) or 0.0
    ratio = 0.0
    if heads[1] is not None:
        ratio = local(pad.node, heads[1])
        if ratio is None:
            ratio = local(fp.node, heads[1])
        if ratio is None:
            ratio = local(setup, heads[3]) or 0.0
    return margin * 1_000_000, ratio


def pad_flash(pad: Pad, grow: tuple[float, float] = (0.0, 0.0), attrs: dict | None = None) -> Flash:
    """A pad's copper as a flash, grown by (x, y) in its own frame."""
    attrs = attrs or {}
    w, h = pad.size
    gx, gy = grow
    ox, oy = pad.drill_offset
    cx, cy = geometry.place([(ox, oy)], *pad.position, pad.angle)[0] if (ox or oy) else pad.position
    size = (max(0, round(w + 2 * gx)), max(0, round(h + 2 * gy)))
    shape = pad.shape
    if shape == "circle":
        return Flash("circle", (cx, cy), (size[0], size[0]), pad.angle, attrs=attrs)
    if shape == "oval":
        return Flash("oval", (cx, cy), size, pad.angle, attrs=attrs)
    if shape == "rect" and gx == 0 and gy == 0:
        return Flash("rect", (cx, cy), size, pad.angle, attrs=attrs)
    if shape in ("rect", "roundrect") and not (pad.chamfer_ratio > 0 and pad.chamfer_corners):
        radius = pad.roundrect_ratio * min(w, h) if shape == "roundrect" else 0.0
        if gx == gy:
            radius = max(0.0, radius + gx)  # grown or shrunk alike all round
        elif shape == "roundrect":
            # Shrunk unevenly, by the paste ratio: the corners keep the pad's
            # proportions (measured on StickHub's QFN pads).
            radius = pad.roundrect_ratio * min(size)
        if radius <= 0:
            return Flash("rect", (cx, cy), size, pad.angle, attrs=attrs)
        return Flash("roundrect", (cx, cy), size, pad.angle, radius=radius, attrs=attrs)
    if shape == "trapezoid" and gx == 0 and gy == 0:
        return Flash("trapezoid", (cx, cy), size, pad.angle, delta=pad.rect_delta, attrs=attrs)
    # The rest as polygons: chamfered rectangles, custom pads, grown trapezoids.
    from . import polygons, zonefill  # noqa: PLC0415

    outline = pad.polygon() or None
    if outline is None:
        rings = zonefill._custom_outline(pad, geometry.MAX_ERROR)
        rings = rings if rings and isinstance(rings[0], list) else ([rings] if rings else [])
    else:
        rings = [outline]
    if gx or gy:
        rings = polygons.offset(rings, (gx + gy) / 2, geometry.MAX_ERROR)
    return Flash("polygon", (cx, cy), size, pad.angle, rings=rings, attrs=attrs)


def _on(board: Board, names: list[str], layer: str) -> bool:
    return layer in board.expand_layers(names)


def pad_function(pad: Pad) -> str:
    """What a pad is, as a Gerber file's aperture function names it."""
    props = pad.node.find("property") if pad.node is not None else None
    prop = props.value(1) if props is not None else ""
    if pad.kind == "np_thru_hole":
        return "WasherPad"
    if prop == "pad_prop_heatsink":
        return "HeatsinkPad"
    if prop == "pad_prop_castellated":
        return "CastellatedPad"
    if prop == "pad_prop_testpoint":
        return "TestPad"
    if prop == "pad_prop_fiducial_glob":
        return "FiducialPad,Global"
    if prop == "pad_prop_fiducial_loc":
        return "FiducialPad,Local"
    if pad.kind == "connect":
        return "ConnectorPad"
    if pad.kind == "smd":
        return "BGAPad,CuDef" if prop == "pad_prop_bga" else "SMDPad,CuDef"
    return "ComponentPad"


def _pin_function(pad: Pad) -> str:
    found = pad.node.find("pinfunction") if pad.node is not None else None
    return (found.value(1) or "") if found is not None else ""


def artwork(board: Board, layer: str) -> list[Shape]:
    """Everything the board draws on a layer, in the board's order."""
    out: list[Shape] = []
    copper = layer.endswith(".Cu")
    kind = layer.split(".")[-1] if "." in layer else layer
    edge = layer == "Edge.Cuts"
    holes: list[tuple[Point, int]] = []  # where a hole is, and how wide
    for fp in board.footprints:
        owner = {"ref": fp.reference}
        for node in fp.node.lists() if fp.node is not None else []:
            if node.head in GRAPHICS and _layer_of(node) == layer:
                attrs = dict(owner)
                if copper:
                    attrs["function"] = "Conductor" if board.net_of(node) else "NonConductor"
                elif edge:
                    attrs["function"] = "Profile"
                out += graphic(node, fp, attrs)
        for pad in fp.pads:
            if not _on(board, pad.layers, layer):
                continue
            if (copper or kind == "Mask") and pad.drill and min(pad.drill) > 0:
                holes.append((pad.position, min(pad.drill)))
            if copper:
                if (
                    pad.kind == "np_thru_hole" and pad.shape == "circle" and pad.drill
                    and pad.drill[0] == pad.drill[1] and pad.drill[0] >= pad.size[0]
                ):  # fmt: skip
                    continue  # a round hole with no copper round it; a slot is drawn
                attrs = {**owner, "pin": pad.number, "pinfunction": _pin_function(pad),
                         "net": pad.net, "function": pad_function(pad)}  # fmt: skip
                out.append(pad_flash(pad, attrs=attrs))
            elif kind == "Mask":
                margin, _ratio = _margins(board, fp, pad, "mask")
                out.append(pad_flash(pad, (margin, margin), dict(owner)))
            elif kind == "Paste":
                if pad.kind in ("thru_hole", "np_thru_hole"):
                    continue
                margin, ratio = _margins(board, fp, pad, "paste")
                gx = margin + ratio * pad.size[0]
                gy = margin + ratio * pad.size[1]
                flash = pad_flash(pad, (gx, gy), dict(owner))
                if flash.size[0] > 0 and flash.size[1] > 0:
                    out.append(flash)
            else:
                out.append(pad_flash(pad, attrs=dict(owner)))
    if copper:
        for via in board.vias:
            if layer in via.layers or layer in board.expand_layers(via.layers):
                attrs = {"function": "ViaPad", "net": via.net}
                out.append(Flash("circle", via.position, (via.diameter, via.diameter), attrs=attrs))
                holes.append((via.position, via.drill))
        for track in board.tracks:
            if track.layer != layer:
                continue
            attrs = {"function": "Conductor", "net": track.net}
            if track.kind == "arc":
                out.append(Stroke([track.start, track.mid, track.end], track.width, arc=True,
                                  attrs=attrs))  # fmt: skip
            else:
                out.append(Stroke([track.start, track.end], track.width, attrs=attrs))
        for zone in board.zones:
            if zone.rule_area:
                continue
            for ring in zone.filled.get(layer, []):
                out.append(Fill([ring], {"function": "Conductor", "net": zone.net}))
    for node in board.root.lists():
        if node.head in GRAPHICS and _layer_of(node) == layer:
            attrs = {}
            if copper:
                net = board.net_of(node)
                attrs = {"function": "NonConductor"}
                if net:
                    attrs = {"function": "Conductor", "net": net}
            elif edge:
                attrs = {"function": "Profile"}
            out += graphic(node, None, attrs)
    # Last, a dot at each hole, as pcbnew marks them: 0.35 mm, or the hole
    # if it is smaller.
    for at, drill in holes:
        dot = min(SMALL_DRILL, drill)
        out.append(Flash("circle", at, (dot, dot), attrs={"mark": True}))
    return out
