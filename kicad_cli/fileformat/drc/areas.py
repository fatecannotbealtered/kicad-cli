"""Rule areas: copper, pads and footprints kept out of where the board says.

A rule area names what it keeps out of it on its layers -- tracks, vias,
pads, zone fill, footprints. Measured on `tests/fixtures/drc/drc3`:

- an item is not allowed when its copper overlaps the area on one of the
  area's layers; copper that stops short of the area's edge is allowed;
- a footprint is not allowed when its courtyard overlaps the area --
  its pads may lie outside -- or, with no courtyard, its pads;
- the message carries "(keepout area)" unless the area has a name.
"""

from __future__ import annotations

from ..connectivity import Polygon
from . import items as describe
from .clearance import fill_distance
from .courtyards import SIDES, Courtyard
from .shapes import Shape, pad_copper

KINDS = {"segment": "tracks", "arc": "tracks", "via": "vias", "pad": "pads", "fill": "copperpour"}


def _rules(zone) -> set[str]:
    keepout = zone.node.find("keepout")
    if keepout is None:
        return set()
    return {rule.head for rule in keepout.lists() if rule.value(1) == "not_allowed"}


def _touches(pieces: list[Shape], polygon: Polygon) -> bool:
    return fill_distance(pieces, polygon, 0) <= 0


def check(run) -> None:
    if not run.on("items_not_allowed"):
        return
    board = run.board
    areas = [(zone, _rules(zone)) for zone in board.zones if zone.rule_area]
    areas = [(zone, rules) for zone, rules in areas if rules]
    if not areas:
        return
    copper = run.copper()
    for zone, rules in areas:
        name = zone.node.find("name")
        message = "Items not allowed" if name is not None and name.value(1) else (
            "Items not allowed (keepout area)")  # fmt: skip
        outlines = [Polygon(o) for o in zone.outlines if len(o) >= 3]
        layers = set(zone.layers)
        for thing in copper.things:
            rule = KINDS.get(thing.kind)
            if rule not in rules or thing.owner is zone:
                continue
            for layer in layers & thing.layers:
                pieces = thing.pieces.get(layer, [])
                hit = any(_touches(pieces, o) for o in outlines if pieces) or any(
                    _enters(fill, o) for fill in thing.fills.get(layer, []) for o in outlines
                )
                if hit:
                    run.report("items_not_allowed", message, [thing.item])
                    break
        if "footprints" in rules:
            for fp in board.footprints:
                if _footprint_in(board, fp, layers, outlines):
                    run.report("items_not_allowed", message, [describe.footprint(fp)])


def _enters(fill: Polygon, area: Polygon) -> bool:
    """Whether a zone's fill reaches into an area: the filler cuts fill off
    at an area's edge, so fill along the edge is not in it."""
    from .shapes import point_segment, segments_cross  # noqa: PLC0415

    fx1, fy1, fx2, fy2 = fill.bbox
    ax1, ay1, ax2, ay2 = area.bbox
    if fx2 <= ax1 or ax2 <= fx1 or fy2 <= ay1 or ay2 <= fy1:
        return False

    def deep(polygon: Polygon, point) -> bool:
        if not polygon.contains(*point):
            return False
        x, y = point
        edges = polygon.near_edges(x - 600, y - 600, x + 600, y + 600)
        return all(point_segment(point, a, b) > 500 for a, b in edges)

    if any(deep(area, p) for p in fill.points) or any(deep(fill, p) for p in area.points):
        return True
    points = area.points
    for i in range(len(points)):
        a, b = points[i - 1], points[i]
        box = (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
        if any(segments_cross(a, b, c, d) for c, d in fill.near_edges(*box)):
            return True
    return False


def _fills_touch(fill: Polygon, area: Polygon) -> bool:
    from .clearance import fills_distance  # noqa: PLC0415

    return fills_distance(fill, area, 0) <= 0


def _footprint_in(board, fp, layers: set[str], outlines: list[Polygon]) -> bool:
    """Whether a footprint lies in the area: its courtyard, or, with none,
    its pads -- on the side of the board the area covers."""
    if ("F.Cu" if fp.layer == "F.Cu" else "B.Cu") not in layers:
        return False
    yard = Courtyard(fp, board.document)
    polygons = [p for side in SIDES for p in yard.polygons[side]]
    if polygons:
        return any(_fills_touch(p, o) for p in polygons for o in outlines)
    pieces = [piece for pad in fp.pads for piece in pad_copper(pad)]
    return bool(pieces) and any(_touches(pieces, o) for o in outlines)
