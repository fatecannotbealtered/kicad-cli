"""Copper too near copper of another net, a hole, or the board's edge.

Measured on `tests/fixtures/drc/drc2`, a board asking KiCad's DRC about
clearance one case at a time:

- Two items are held to the larger of their net classes' clearances, and
  never less than the board's minimum. The two nets of a differential pair
  (names alike but for a last P and N, or + and -) are held to the smaller
  of that and their class's pair gap. A pad's own clearance, or its
  footprint's, stands instead of the net classes' -- even when it is
  smaller. A zone's fill is held to its zone's clearance too, whichever is
  larger. The message names what set the clearance: "board minimum",
  "pad", "footprint R1", "zone", "netclass 'X'" -- of two classes asking
  the same, the one later by name, Default the earliest -- or, where only
  the classes' own clearances can set one, no name at all
  (`tests/fixtures/drc/drcwords`).
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
from . import mm, subjects
from . import rules as rules_module
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
    rules = settings.dru
    if rules is not None:
        values += [
            c.min for rule in rules.rules for kind, c in rule.constraints.items()
            if kind == "clearance" and c.min is not None
        ]  # fmt: skip
    return max(values)


def clearance(run, a: Thing, b: Thing, layer: str | None = None) -> tuple[int, str, str | None]:
    """What two items are held to, what the message names as setting it,
    and the severity a custom rule setting it gives."""
    settings = run.settings
    overrides = [t.override for t in (a, b) if t.override is not None]
    rules = settings.dru
    found = None
    if not overrides and rules is not None and rules.rules:
        from . import subjects  # noqa: PLC0415

        found = rules.find("clearance", subjects.of_thing(run, a), subjects.of_thing(run, b),
                           layer)  # fmt: skip
    if overrides:
        # A pad's or footprint's own clearance stands instead of everything
        # else but the board's minimum -- its zone's too -- and a custom rule.
        value, name = max(overrides, key=lambda o: o[0])
    elif found is not None:
        # A custom rule, loosening as well as tightening: the board's minimum
        # and the net classes give way to it.
        rule, constraint = found
        return (constraint.min or 0), f"rule '{rule.name}'", rule.severity
    else:
        ca = settings.netclasses.of(a.net)
        cb = settings.netclasses.of(b.net)
        if ca.nm("clearance") != cb.nm("clearance"):
            best = ca if ca.nm("clearance") > cb.nm("clearance") else cb
        else:
            # Of two classes asking the same, the one KiCad lists last: Default
            # first, then the rest by name, capitals before small letters.
            best = max((ca, cb), key=lambda c: _listed(c.sources["clearance"]))
        value = best.nm("clearance")
        name = f"netclass '{best.sources['clearance']}'" if _names_classes(run) else ""
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
    return value, name, None


def _listed(name: str) -> tuple[bool, str]:
    return name != "Default", name


def _names_classes(run) -> bool:
    """Whether a clearance message names the class setting it. KiCad names it
    only where more than the classes' own clearances can set one: a custom
    rule with a clearance, or a class in use -- Default always is -- whose
    pairs keep closer than its clearance (`tests/fixtures/drc/drcwords`).
    Elsewhere it says "( clearance 0.2000 mm; ...)"."""
    found = getattr(run, "_names_classes", None)
    if found is None:
        settings = run.settings
        rules = settings.dru
        found = rules is not None and any("clearance" in r.constraints for r in rules.rules)
        if not found:
            board = run.board
            nets = (
                {""} | {t.net for t in board.tracks} | {v.net for v in board.vias}
                | {p.net for fp in board.footprints for p in fp.pads}
                | {z.net for z in board.zones if not z.rule_area}
            )  # fmt: skip
            classes = {settings.netclasses.of(net) for net in nets}
            found = any(c.nm("clearance") > c.nm("diff_pair_gap") for c in classes)
        run._names_classes = found
    return found


def coupled(a: str, b: str) -> bool:
    """Whether two nets are the two halves of a differential pair
    (`pairs.py`)."""
    from .pairs import complement  # noqa: PLC0415

    return complement(a) == b


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
    value, name, severity = clearance(run, a, b, layer)
    if gap >= value - EPSILON:
        return
    seen.add(key)
    fill = a.kind == "fill" or b.kind == "fill"
    both_pads = a.kind == "pad" and b.kind == "pad"
    a, b = _listed_order(a, b)
    bridge = ""
    if gap <= 0 and not fill:
        if a.kind in TRACKS and b.kind in TRACKS and _crossing(a, b, layer):
            run.report("tracks_crossing", "Tracks crossing", [a.item, b.item])
            return
        across = a.bridges or b.bridges
        if across and a.fp is not None and a.fp is b.fp and {a.kind, b.kind} == {"pad", "shape"}:
            # A footprint's drawing across its pads, touching one: a short
            # where the drawing's uuid is the smaller, else a clearance of
            # nothing (`tests/fixtures/drc/drc2`, KiCad's microwave demo).
            drawing, pad = (a, b) if a.kind == "shape" else (b, a)
            bridge = "short" if drawing.item.uuid < pad.item.uuid else "clearance"
        short = (a.net and b.net) if both_pads else (a.net or b.net)
        if bridge == "short" or (short and bridge != "clearance"):
            # The nets as the file has them, "S{slash}1", unlike the items;
            # no net at all is nothing, but a drawing's
            # (`tests/fixtures/drc/drcwords`).
            nets = " and ".join(
                t.net or (describe.NO_NET if t.kind == "shape" else "") for t in (a, b)
            )
            run.report("shorting_items", f"Items shorting two nets (nets {nets})", [a.item, b.item])
            return
    label = f"{name} " if name else " "
    run.report(
        "clearance",
        f"Clearance violation ({label}clearance {mm(value)}; actual {mm(round(gap))})",
        [a.item, b.item],
        severity,
    )


def _listed_order(a: Thing, b: Thing) -> tuple[Thing, Thing]:
    """Two items in the order KiCad lists them: a zone's fill second, two
    pads as found, and any other two by their uuids (KiCad's demos,
    `tests/fixtures/drc/drcwords`)."""
    if a.kind == "fill" or b.kind == "fill":
        return (b, a) if a.kind == "fill" else (a, b)
    if a.kind == "pad" and b.kind == "pad":
        return a, b
    return (a, b) if a.item.uuid <= b.item.uuid else (b, a)


def _pairs(run, things: list[Thing], grid, widest: int) -> None:
    seen: set = set()
    rules = run.settings.dru
    ruled = rules is not None and any("clearance" in r.constraints for r in rules.rules)
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
                reach = widest
                if ruled:
                    # A rule's clearance can be far wider than most: what this
                    # pair is held to bounds how far to measure.
                    reach = clearance(run, a, b, layer)[0]
                    if _apart(box, b.box(layer), reach):
                        continue
                gap = thing_distance(a, b, layer, reach)
                if gap < reach:
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
                    value = clearance(run, zone, b, layer)[0]
                    gap = _to_fill(b, layer, polygon, value)
                    if gap < value:
                        _report(run, b, zone, gap, layer, seen)
    for i, a in enumerate(fills):
        for b in fills[i + 1 :]:
            if _unchecked(a, b):
                continue
            value = clearance(run, a, b)[0]
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
    board_least = run.settings.nm("min_hole_clearance")
    rules = run.settings.dru
    reach = max(board_least, rules_module.largest(rules, "hole_clearance"))
    copper = set(run.board.copper_layers)
    seen: set = set()

    def limit(a: Thing, b: Thing, layer: str | None):
        if rules is None or not rules.rules:
            return board_least, "board setup constraints hole clearance", None
        value, who, severity = rules_module.least(
            rules, "hole_clearance", subjects.of_thing(run, a), subjects.of_thing(run, b), layer,
            board_least, "board setup constraints hole clearance",
        )  # fmt: skip
        who = who if who.startswith("board") else f"{who} clearance"
        return value, who, severity

    def check(a: Thing, b: Thing, gap: float, layer: str | None) -> None:
        key = (id(a), id(b)) if id(a) < id(b) else (id(b), id(a))
        if key in seen:
            return
        value, who, severity = limit(a, b, layer)
        if gap >= value - EPSILON:
            return
        seen.add(key)
        run.report(
            "hole_clearance",
            f"Hole clearance violation ({who} {mm(value)}; actual {mm(round(max(gap, 0)))})",
            [b.item, a.item],
            severity,
        )

    drilled = [t for t in things if t.hole is not None]
    for owner in drilled:
        hole = owner.hole
        box = hole.bbox()
        for layer in _hole_layers(copper, owner):
            own = owner.pieces.get(layer)
            for other in _near(grid, layer, box, reach):
                b = things[other]
                if b is owner or (owner.net and owner.net == b.net) or _one_pad(owner, b):
                    continue
                if own is not None and (b.kind != "pad" or thing_distance(owner, b, layer, 0) > 0):
                    continue  # its own copper stands between: a clearance, or a short
                gap = min(
                    [distance(p, hole) for p in b.pieces.get(layer, ())]
                    + [fill_distance([hole], area, reach) for area in b.areas.get(layer, ())]
                )
                if gap < reach - EPSILON:
                    check(owner, b, gap, layer)
        if owner.npth is not None:
            for other in drilled:
                if other.kind != "via" or _apart(box, other.hole.bbox(), reach):
                    continue
                gap = min(distance(p, other.hole) for p in owner.npth)
                if gap < reach - EPSILON:
                    check(owner, other, gap, None)
    # Holes against zones' fills: a zone is measured against the holes a grid
    # of them finds near its fill, in the holes' own order.
    holes_at: dict[tuple[int, int], list[int]] = defaultdict(list)
    for index, owner in enumerate(drilled):
        for cell in _cells(owner.hole.bbox(), reach):
            holes_at[cell].append(index)
    for zone in (t for t in things if t.fills):
        near = {
            index for polygons in zone.fills.values() for polygon in polygons
            for cell in _cells(polygon.bbox, reach) for index in holes_at.get(cell, ())
        }  # fmt: skip
        for index in sorted(near):
            owner = drilled[index]
            if owner.net and owner.net == zone.net:
                continue
            box = owner.hole.bbox()
            for layer in _hole_layers(copper, owner):
                if owner.pieces.get(layer) is not None:
                    continue
                for polygon in zone.fills.get(layer, ()):
                    if _apart(box, polygon.bbox, reach):
                        continue
                    gap = fill_distance([owner.hole], polygon, reach)
                    if gap < reach - EPSILON:
                        check(owner, zone, gap, layer)


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
    board_least = run.settings.nm("min_copper_edge_clearance")
    rules = run.settings.dru
    least = max(board_least, rules_module.largest(rules, "edge_clearance"))
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
        if index is None:
            continue
        value, who, severity = board_least, "board setup constraints edge", None
        if rules is not None and rules.rules:
            value, who, severity = rules_module.least(
                rules, "edge_clearance", subjects.of_thing(run, thing), None, None,
                board_least, "board setup constraints edge",
            )  # fmt: skip
        if not (gap <= 0 or gap < value - EPSILON):
            continue
        actual = mm(round(max(gap, 0)))
        detail = f" ({who} clearance {mm(value)}; actual {actual})" if value > 0 else ""
        run.report(
            "copper_edge_clearance", f"Board edge clearance violation{detail}",
            [edges[index][0], thing.item], severity,
        )  # fmt: skip
