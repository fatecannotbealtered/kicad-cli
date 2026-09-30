"""Copper too near copper of another net, a hole, or the board's edge.

Measured on `tests/fixtures/drc/drc2`, a board asking KiCad's DRC about
clearance one case at a time:

- Two items are held to the larger of their net classes' clearances, and
  never less than the board's minimum. The two nets of a differential pair
  (names alike but for a last P and N, or + and -) are held to the smaller
  of that and their class's pair gap. A pad's own clearance, or its
  footprint's, stands instead of the net classes' -- even when it is
  smaller. A zone's fill is held to its zone's clearance too, whichever is
  larger. The message names what set the clearance: "netclass 'X'" -- with
  no name at all when the project has no net class patterns -- "board
  minimum", "pad", "footprint R1", "zone".
- Distances are exact: edge to edge, an arc as an arc. Less than the
  clearance by more than 0.5 um is a violation, and not by less.
- Copper of two nets touching is a short, two tracks' centre lines crossing
  is a crossing -- except touching a zone's fill, which is a clearance of
  nothing, and two pads touching, which is a short only when both have a
  net. Copper of no net is held apart from other copper of no net only
  where one of the two is a pad.
  Which net copper is checked on is `copper.py`'s.
- A hole keeps clear of other nets' copper by the board's hole clearance:
  an unplated hole always; a plated one on a layer it has no copper on, or
  where a pad of another net reaches into its own copper -- a track or a
  via doing that is a short and no more. An unplated pad's shape keeps
  clear of other holes by as much.
- Copper keeps clear of the board's edge by the board's edge clearance,
  measured from the middle of the line the edge is drawn with, whatever its
  width; a footprint's cut-out is an edge too. Copper on the edge is
  reported even where the board asks no clearance of it. An item is
  reported once, with the edge nearest it.
"""

from __future__ import annotations

import math
from collections import defaultdict

from .. import geometry
from ..board import shape_points
from . import items as describe
from . import mm
from .copper import ARC_ERROR, Thing
from .settings import DEFAULT_CLASS, NM
from .shapes import Shape, core_distance, distance, segments_cross

EPSILON = 500  # nm: KiCad does not report a clearance short by this much or less
CELL = 2_000_000  # nm
TRACKS = ("segment", "arc")


def check(run) -> None:
    rules = ("clearance", "shorting_items", "tracks_crossing", "hole_clearance",
             "copper_edge_clearance")  # fmt: skip
    if not any(run.on(rule) for rule in rules):
        return
    copper = run.copper()
    things = copper.things
    grid = _grid(things)
    widest = _widest(run, things)
    if run.on("clearance") or run.on("shorting_items") or run.on("tracks_crossing"):
        _pairs(run, things, grid, widest)
        _fills(run, things, grid, widest)
    if run.on("hole_clearance"):
        _holes(run, things, grid)
    if run.on("copper_edge_clearance"):
        _edges(run, things)


# -- which clearance ------------------------------------------------------------------------


def _widest(run, things) -> int:
    """The largest clearance anything on the board can be held to."""
    settings = run.settings
    values = [settings.nm("min_clearance"), round(DEFAULT_CLASS["clearance"] * NM)]
    for raw in settings.netclasses.classes.values():
        value = raw.get("clearance")
        if isinstance(value, (int, float)):
            values.append(round(value * NM))
    values += [t.override[0] for t in things if t.override]
    values += [t.zone_clearance for t in things if t.zone_clearance]
    return max(values)


def clearance(run, a: Thing, b: Thing) -> tuple[int, str]:
    """What two items are held to, and what the message names as setting it."""
    settings = run.settings
    overrides = [t.override for t in (a, b) if t.override is not None]
    if overrides:
        # A pad's or footprint's own clearance stands instead of everything
        # else but the board's minimum -- its zone's too.
        value, name = max(overrides, key=lambda o: o[0])
    else:
        ca = settings.netclasses.of(a.net)
        cb = settings.netclasses.of(b.net)
        best = ca if ca.nm("clearance") >= cb.nm("clearance") else cb
        value = best.nm("clearance")
        name = f"netclass '{best.sources['clearance']}'" if settings.netclasses.patterns else ""
        if coupled(a.net, b.net):
            # The two nets of a differential pair keep apart by its gap, where
            # that is the smaller -- by a rule KiCad does not name.
            gap = max(ca.nm("diff_pair_gap"), cb.nm("diff_pair_gap"))
            value, name = min(value, gap), ""
        for zone in (a, b):
            if zone.zone_clearance is not None and zone.zone_clearance > value:
                value, name = zone.zone_clearance, "zone"
    least = settings.nm("min_clearance")
    if value < least:
        value, name = least, "board minimum"
    return value, name


def coupled(a: str, b: str) -> bool:
    """Whether two nets are the two halves of a differential pair: names
    alike but for a last P and N, or + and -."""
    return (
        len(a) > 1 and len(a) == len(b) and a[:-1] == b[:-1]
        and {a[-1], b[-1]} in ({"P", "N"}, {"+", "-"})
    )  # fmt: skip


# -- the grid -------------------------------------------------------------------------------


def _cells(box, reach=0):
    x1, y1, x2, y2 = box
    for cx in range(int(x1 - reach) // CELL, int(x2 + reach) // CELL + 1):
        for cy in range(int(y1 - reach) // CELL, int(y2 + reach) // CELL + 1):
            yield cx, cy


def _grid(things) -> dict[tuple[str, int, int], list[int]]:
    """Every item's copper by layer and grid cell -- all but zone fills,
    which cover too many cells to be worth listing in each."""
    grid: dict[tuple[str, int, int], list[int]] = defaultdict(list)
    for index, thing in enumerate(things):
        if thing.fills:
            continue
        for layer in thing.layers:
            for cx, cy in _cells(thing.box(layer)):
                grid[(layer, cx, cy)].append(index)
    return grid


def _near(grid, layer, box, reach) -> set[int]:
    found: set[int] = set()
    for cx, cy in _cells(box, reach):
        found.update(grid.get((layer, cx, cy), ()))
    return found


def _apart(a, b, reach) -> bool:
    return a[2] + reach < b[0] or b[2] + reach < a[0] or a[3] + reach < b[1] or b[3] + reach < a[1]


# -- distances ------------------------------------------------------------------------------


def pieces_distance(a: list[Shape], b: list[Shape], reach: float = math.inf) -> float:
    """Between two sets of convex pieces; only pieces within `reach` of
    each other are measured, the rest standing at infinity."""
    best = math.inf
    for p in a:
        box = p.box
        for q in b:
            if _apart(box, q.box, reach):
                continue
            d = distance(p, q)
            if d < best:
                best = d
                if best <= 0:
                    return 0.0
    return best


def thing_distance(a: Thing, b: Thing, layer: str, reach: float = math.inf) -> float:
    """Between two items' copper on a layer: pieces, fills and the insides
    of filled drawings, each against each."""
    best = math.inf
    pa, pb = a.pieces.get(layer, ()), b.pieces.get(layer, ())
    areas_a = a.fills.get(layer, []) + a.areas.get(layer, [])
    areas_b = b.fills.get(layer, []) + b.areas.get(layer, [])
    if pa and pb:
        best = pieces_distance(pa, pb, reach)
    for area in areas_a:
        if best <= 0:
            return 0.0
        if pb:
            best = min(best, fill_distance(pb, area, reach))
        for other in areas_b:
            best = min(best, fills_distance(area, other, reach))
    for area in areas_b:
        if best <= 0:
            return 0.0
        if pa:
            best = min(best, fill_distance(pa, area, reach))
    return best


def fill_distance(pieces: list[Shape], polygon, reach: float) -> float:
    """From convex pieces to a zone's fill polygon; 0 where they meet."""
    best = math.inf
    for piece in pieces:
        x1, y1, x2, y2 = piece.bbox()
        if _apart((x1, y1, x2, y2), polygon.bbox, reach):
            continue
        if any(polygon.contains(*p) for p in piece.core):
            return 0.0
        edges = polygon.near_edges(x1 - reach, y1 - reach, x2 + reach, y2 + reach)
        for a, b in edges:
            d = core_distance((a, b), piece.core) - piece.radius
            if d < best:
                best = d
                if best <= 0:
                    return 0.0
        if len(piece.core) >= 3 and _inside_convex(polygon.points[0], piece):
            return 0.0
    return max(best, 0.0)


def _inside_convex(point, piece: Shape) -> bool:
    from .shapes import depth  # noqa: PLC0415

    return depth(point, piece.core) + piece.radius >= 0


def fills_distance(p, q, reach: float) -> float:
    if _apart(p.bbox, q.bbox, reach):
        return math.inf
    if p.contains(*q.points[0]) or q.contains(*p.points[0]):
        return 0.0
    small, big = (p, q) if len(p.points) <= len(q.points) else (q, p)
    best = math.inf
    points = small.points
    for i in range(len(points)):
        a, b = points[i - 1], points[i]
        box = (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
        if _apart(box, big.bbox, reach):
            continue
        for c, d in big.near_edges(box[0] - reach, box[1] - reach, box[2] + reach, box[3] + reach):
            if segments_cross(a, b, c, d):
                return 0.0
            gap = core_distance((a, b), (c, d))
            if gap < best:
                best = gap
    return best


def _crossing(a: Thing, b: Thing, layer: str) -> bool:
    return any(
        segments_cross(p.core[0], p.core[-1], q.core[0], q.core[-1])
        for p in a.pieces[layer]
        for q in b.pieces[layer]
        if len(p.core) == 2 and len(q.core) == 2
    )


# -- copper against copper ------------------------------------------------------------------


def _to_fill(thing: Thing, layer: str, polygon, reach: float) -> float:
    best = math.inf
    pieces = thing.pieces.get(layer)
    if pieces:
        best = fill_distance(pieces, polygon, reach)
    for area in thing.areas.get(layer, ()):
        best = min(best, fills_distance(area, polygon, reach))
    return best


def _net_tie(a: Thing, b: Thing) -> bool:
    """Whether two items are copper a net-tie footprint joins on purpose."""
    if a.fp is None or a.fp is not b.fp or not a.ties:
        return False
    pads = [t.owner.number for t in (a, b) if t.kind == "pad"]
    if len(pads) < 2:
        return any(pads[0] in group for group in a.ties) if pads else True
    return any(pads[0] in group and pads[1] in group for group in a.ties)


def _one_pad(a: Thing, b: Thing) -> bool:
    return (
        a.kind == "pad" and b.kind == "pad" and a.fp is not None and a.fp is b.fp
        and a.owner.number == b.owner.number
    )  # fmt: skip


def _unchecked(a: Thing, b: Thing) -> bool:
    if a.net and a.net == b.net:
        return True
    if not a.net and not b.net and a.kind != "pad" and b.kind != "pad":
        return True  # copper of no net is held apart only from pads
    if a.fp is not None and a.fp is b.fp:
        if a.kind == "pad" and b.kind == "pad" and a.owner.number == b.owner.number:
            return True  # one pad, drawn in pieces
        if a.kind == "shape" and b.kind == "shape":
            return True  # a footprint's drawings are its own business
    if a.tied and b.net in a.tied or b.tied and a.net in b.tied:
        return True
    return _net_tie(a, b)


def _report(run, a: Thing, b: Thing, gap: float, layer: str, seen: set) -> None:
    key = (id(a), id(b)) if id(a) < id(b) else (id(b), id(a))
    if key in seen:
        return
    value, name = clearance(run, a, b)
    if gap >= value - EPSILON:
        return
    seen.add(key)
    fill = a.kind == "fill" or b.kind == "fill"
    if gap <= 0 and not fill:
        if a.kind in TRACKS and b.kind in TRACKS and _crossing(a, b, layer):
            run.report("tracks_crossing", "Tracks crossing", [a.item, b.item])
            return
        both_pads = a.kind == "pad" and b.kind == "pad"
        bridge = a.bridges or b.bridges
        short = (a.net and b.net) if both_pads else (a.net or b.net)
        if bridge == "short" or (short and bridge != "clearance"):
            nets = " and ".join(t.net or "<no net>" for t in (a, b))
            run.report("shorting_items", f"Items shorting two nets (nets {nets})", [a.item, b.item])
            return
    label = f"{name} " if name else " "
    run.report(
        "clearance",
        f"Clearance violation ({label}clearance {mm(value)}; actual {mm(round(gap))})",
        [a.item, b.item],
    )


def _pairs(run, things: list[Thing], grid, widest: int) -> None:
    seen: set = set()
    for index, a in enumerate(things):
        if a.fills:
            continue
        for layer in a.layers:
            box = a.box(layer)
            for other in _near(grid, layer, box, widest):
                if other <= index:
                    continue
                b = things[other]
                if _unchecked(a, b) or _apart(box, b.box(layer), widest):
                    continue
                gap = thing_distance(a, b, layer, widest)
                if gap < widest:
                    _report(run, a, b, gap, layer, seen)


def _fills(run, things: list[Thing], grid, widest: int) -> None:
    seen: set = set()
    fills = [t for t in things if t.fills]
    for zone in fills:
        for layer, polygons in zone.fills.items():
            for polygon in polygons:
                for other in _near(grid, layer, polygon.bbox, widest):
                    b = things[other]
                    if b is zone or _unchecked(zone, b):
                        continue
                    if layer not in b.layers or _apart(polygon.bbox, b.box(layer), widest):
                        continue
                    value, _ = clearance(run, zone, b)
                    gap = _to_fill(b, layer, polygon, value)
                    if gap < value:
                        _report(run, b, zone, gap, layer, seen)
    for i, a in enumerate(fills):
        for b in fills[i + 1 :]:
            if _unchecked(a, b):
                continue
            value, _ = clearance(run, a, b)
            for layer in set(a.fills) & set(b.fills):
                gap = min(
                    (fills_distance(p, q, value) for p in a.fills[layer] for q in b.fills[layer]),
                    default=math.inf,
                )
                if gap < value:
                    _report(run, a, b, gap, layer, seen)


# -- copper against holes -------------------------------------------------------------------


def _hole_layers(copper: set[str], thing: Thing) -> list[str]:
    """The layers a hole is checked on: its via's span, or its pad's copper
    layers -- an unplated pad on F&B.Cu is not checked on the inner layers
    it passes through, and KiCad fills zones over it there."""
    return [layer for layer in thing.owner.layers if layer in copper]


def _holes(run, things: list[Thing], grid) -> None:
    least = run.settings.nm("min_hole_clearance")
    copper = set(run.board.copper_layers)
    seen: set = set()

    def report(a: Thing, b: Thing, gap: float) -> None:
        key = (id(a), id(b)) if id(a) < id(b) else (id(b), id(a))
        if key in seen:
            return
        seen.add(key)
        run.report(
            "hole_clearance",
            f"Hole clearance violation (board setup constraints hole clearance {mm(least)}; "
            f"actual {mm(round(max(gap, 0)))})",
            [b.item, a.item],
        )

    drilled = [t for t in things if t.hole is not None]
    for owner in drilled:
        hole = owner.hole
        box = hole.bbox()
        for layer in _hole_layers(copper, owner):
            own = owner.pieces.get(layer)
            for other in _near(grid, layer, box, least):
                b = things[other]
                if b is owner or (owner.net and owner.net == b.net) or _one_pad(owner, b):
                    continue
                if own is not None and (b.kind != "pad" or thing_distance(owner, b, layer, 0) > 0):
                    continue  # its own copper stands between: a clearance, or a short
                gap = min(
                    [distance(p, hole) for p in b.pieces.get(layer, ())]
                    + [fill_distance([hole], area, least) for area in b.areas.get(layer, ())]
                )
                if gap < least - EPSILON:
                    report(owner, b, gap)
        if owner.npth is not None:
            for other in drilled:
                if other.kind != "via" or _apart(box, other.hole.bbox(), least):
                    continue
                gap = min(distance(p, other.hole) for p in owner.npth)
                if gap < least - EPSILON:
                    report(owner, other, gap)
    for zone in (t for t in things if t.fills):
        for owner in drilled:
            if owner.net and owner.net == zone.net:
                continue
            for layer in _hole_layers(copper, owner):
                if owner.pieces.get(layer) is not None:
                    continue
                for polygon in zone.fills.get(layer, ()):
                    gap = fill_distance([owner.hole], polygon, least)
                    if gap < least - EPSILON:
                        report(owner, zone, gap)


# -- copper against the board's edge --------------------------------------------------------


def edge_pieces(board) -> list[tuple[describe.Item, list[Shape]]]:
    """The board's edge, drawn: each Edge.Cuts drawing as the segments of
    its middle line."""
    out = []

    def add(node, fp=None) -> None:
        points = shape_points(node, ARC_ERROR)
        if fp is not None:
            points = geometry.place(points, *fp.position, fp.angle)
        if not points:
            return
        head = node.head.split("_", 1)[1]
        if head in ("circle", "rect", "poly") and len(points) > 2:
            points = list(points) + [points[0]]
        pieces = [Shape((points[i], points[i + 1])) for i in range(len(points) - 1)]
        item = describe.shape(board, node, points[0], fp)
        out.append((item, pieces or [Shape((points[0],))]))

    for node in board.edge_shapes():
        add(node)
    source = board.document.source
    for fp in board.footprints:
        span = board.document.span(fp.node)
        if span is not None and source.find('"Edge.Cuts"', *span) < 0:
            continue
        for node in fp.node.lists():
            if node.head not in ("fp_line", "fp_arc", "fp_circle", "fp_rect", "fp_poly"):
                continue
            layer = node.find("layer")
            if layer is not None and layer.value(1) == "Edge.Cuts":
                add(node, fp)
    return out


def _edges(run, things: list[Thing]) -> None:
    least = run.settings.nm("min_copper_edge_clearance")
    edges = edge_pieces(run.board)
    if not edges:
        return
    grid: dict[tuple[int, int], list[tuple[int, Shape]]] = defaultdict(list)
    for index, (_item, pieces) in enumerate(edges):
        for piece in pieces:
            for cell in _cells(piece.bbox()):
                grid[cell].append((index, piece))
    for thing in things:
        best = (math.inf, None)
        for layer in thing.layers:
            box = thing.box(layer)
            near = {}
            for cell in _cells(box, least):
                for index, piece in grid.get(cell, ()):
                    near.setdefault(id(piece), (index, piece))
            for index, piece in near.values():
                if _apart(box, piece.bbox(), least):
                    continue
                gap = min(
                    [distance(p, piece) for p in thing.pieces.get(layer, ())]
                    + [fill_distance([piece], polygon, least)
                       for polygon in thing.fills.get(layer, []) + thing.areas.get(layer, [])]
                )  # fmt: skip
                if gap < best[0]:
                    best = (gap, index)
        gap, index = best
        if index is None or not (gap <= 0 or gap < least - EPSILON):
            continue
        actual = mm(round(max(gap, 0)))
        detail = (
            f" (board setup constraints edge clearance {mm(least)}; actual {actual})"
            if least > 0 else ""
        )  # fmt: skip
        run.report(
            "copper_edge_clearance", f"Board edge clearance violation{detail}",
            [edges[index][0], thing.item],
        )  # fmt: skip
