"""Zones' fills: islands of copper nothing of their net reaches, and pads a
fill reaches by too few thermal spokes.

A zone's fill, as the file holds it, is islands of copper. Measured on
`tests/fixtures/drc/drcislands` and `drcthermal`:

- an island whose copper -- through tracks, vias and other fill of its net
  -- reaches no pad of its net is isolated copper, whatever the zone says of
  removing islands and however large it is; a track or a via of the net
  touching it alone does not connect it. Each island is reported, naming its
  zone;
- a pad a zone joins by thermal relief is reached by spokes across the
  relief's gap: as many as the times the fill crosses the line around the
  pad at the gap's middle, into the fill and out -- a spoke a neighbour has
  cut short counts while any of it is left (KiCad's complex_hierarchy demo,
  Q303). A pad with fewer than the board's minimum (`min_resolved_spokes`,
  2) is starved of them; one with none is not joined to the zone, and not
  asked about. Where all a pad's spokes reach an island of fill meeting
  nothing else, KiCad at times says so instead of the count (its interf_u
  demo, U9 pad G2) and at times not (U5 pad 16, and `drcthermal`), by a
  difference not found yet: the count is what is said here, of the same
  pad.
"""

from __future__ import annotations

import math
from collections import defaultdict

from ..board import nm
from ..connectivity import Polygon
from . import items as describe
from .shapes import pad_copper

THERMAL, SOLID, NONE = "thermal", "solid", "none"
ZONE_CONNECT = {0: NONE, 1: THERMAL, 2: SOLID, 3: "thru_hole_only"}


def check(run) -> None:
    if run.on("isolated_copper"):
        _isolated(run)
    if run.on("starved_thermal"):
        _starved(run)


def _isolated(run) -> None:
    joined = run.connectivity()
    items = joined.items
    anchored = {
        joined.cluster_of[i] for i, item in enumerate(items) if item.kind == "pad" and item.net
    }
    board = run.board
    for index, item in enumerate(items):
        if item.kind != "fill" or not item.net or joined.cluster_of[index] in anchored:
            continue
        run.report("isolated_copper", "Isolated copper fill", [describe.zone(board, item.owner)],
                   once=False)  # fmt: skip


# -- thermal spokes ---------------------------------------------------------------------------


def _zone_mode(zone) -> str:
    connect = zone.node.find("connect_pads")
    word = connect.atom(1) if connect is not None else None
    return {"yes": SOLID, "no": NONE, "thru_hole_only": "thru_hole_only"}.get(word or "", THERMAL)


def _length(node, head: str) -> int | None:
    found = node.find(head) if node is not None else None
    return nm(found.atom(1)) if found is not None and found.atom(1) is not None else None


def _pad_mode(fp, pad, zone_mode: str) -> str:
    own = pad.zone_connect
    if own is None:
        found = fp.node.find("zone_connect") if fp.node is not None else None
        own = int(found.atom(1)) if found is not None and found.atom(1) else None
    mode = ZONE_CONNECT.get(own, zone_mode) if own is not None else zone_mode
    if mode == "thru_hole_only":
        mode = THERMAL if pad.kind == "thru_hole" else SOLID
    return mode


def _ring(shapes, by: float) -> list[tuple[float, float]] | None:
    """The line a pad's copper grown by `by` runs along, closed: one convex
    shape's -- a custom pad of several has none here."""
    if len(shapes) != 1:
        return None
    shape = shapes[0]
    # An oval as long as it is wide is a circle: its two ends one point.
    core, radius = list(dict.fromkeys(shape.core)), shape.radius + by
    points: list[tuple[float, float]] = []
    if len(core) == 1:
        (cx, cy) = core[0]
        return [(cx + radius * math.cos(a), cy + radius * math.sin(a))
                for a in (2 * math.pi * k / 64 for k in range(64))]  # fmt: skip
    if len(core) == 2:
        core = [core[0], core[1]]
    elif sum(core[i - 1][0] * core[i][1] - core[i][0] * core[i - 1][1]
             for i in range(len(core))) < 0:  # fmt: skip
        core.reverse()  # counter-clockwise, as the arcs below turn
    n = len(core)
    for i in range(n):
        prev, here, nxt = core[i - 1], core[i], core[(i + 1) % n]
        if n == 2:
            ux, uy = here[0] - prev[0], here[1] - prev[1]
            length = math.hypot(ux, uy) or 1.0
            start_angle = math.atan2(-ux / length, uy / length)
            sweep = math.pi
        else:
            a1 = math.atan2(here[1] - prev[1], here[0] - prev[0]) - math.pi / 2
            a2 = math.atan2(nxt[1] - here[1], nxt[0] - here[0]) - math.pi / 2
            start_angle, sweep = a1, (a2 - a1) % (2 * math.pi)
        steps = max(1, round(sweep / (math.pi / 16)))
        for k in range(steps + 1):
            a = start_angle + sweep * k / steps
            points.append((here[0] + radius * math.cos(a), here[1] + radius * math.sin(a)))
    return points


def _crossings(ring, polygons) -> list[tuple[float, int]]:
    """Where the ring crosses the fill's edges: (how far along the ring, which
    polygon), in order along it."""
    from .shapes import segments_cross  # noqa: PLC0415

    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    box = (min(xs), min(ys), max(xs), max(ys))
    found = []
    for which, polygon in polygons:
        px1, py1, px2, py2 = polygon.bbox
        if px2 < box[0] or px1 > box[2] or py2 < box[1] or py1 > box[3]:
            continue
        edges = polygon.near_edges(*box)
        for i in range(len(ring)):
            a, b = ring[i - 1], ring[i]
            for c, d in edges:
                if segments_cross(a, b, c, d):
                    found.append((i + _along(a, b, c, d), which))
    return sorted(found)


def _along(a, b, c, d) -> float:
    """How far along a-b it crosses c-d, 0 to 1."""
    rx, ry = b[0] - a[0], b[1] - a[1]
    sx, sy = d[0] - c[0], d[1] - c[1]
    den = rx * sy - ry * sx
    if den == 0:
        return 0.5
    return ((c[0] - a[0]) * sy - (c[1] - a[1]) * sx) / den


def _starved(run) -> None:
    board = run.board
    least = round(run.settings.rules.get("min_resolved_spokes", 2))
    joined = run.connectivity()
    items = joined.items
    fills_by = defaultdict(list)  # (zone id, layer) -> [item index]
    for index, item in enumerate(items):
        if item.kind == "fill":
            fills_by[(id(item.owner), item.layers[0])].append(index)
    pads_by_net = defaultdict(list)
    for fp in board.footprints:
        for pad in fp.pads:
            if pad.net and pad.kind != "np_thru_hole":
                pads_by_net[pad.net].append((fp, pad))
    for zone in board.zones:
        if zone.rule_area or not zone.net:
            continue
        mode = _zone_mode(zone)
        fill = zone.node.find("fill")
        gap = _length(fill, "thermal_gap") or 500_000
        outlines = [Polygon(o) for o in zone.outlines if len(o) >= 3]
        if not outlines:
            continue
        zx1 = min(o.bbox[0] for o in outlines)
        zy1 = min(o.bbox[1] for o in outlines)
        zx2 = max(o.bbox[2] for o in outlines)
        zy2 = max(o.bbox[3] for o in outlines)
        zone_item = describe.zone(board, zone)
        for layer in zone.layers:
            islands = fills_by.get((id(zone), layer))
            if not islands:
                continue
            for fp, pad in pads_by_net.get(zone.net, ()):
                px, py = pad.position
                if not (zx1 <= px <= zx2 and zy1 <= py <= zy2):
                    continue
                if layer not in pad.layers or _pad_mode(fp, pad, mode) != THERMAL:
                    continue
                if not any(o.contains(px, py) for o in outlines):
                    continue
                own_gap = _length(pad.node, "thermal_gap") or gap
                ring = _ring(pad_copper(pad), own_gap / 2)
                if ring is None:
                    continue
                polygons = [(index, items[index].polygon) for index in islands]
                crossings = _crossings(ring, polygons)
                if not crossings:
                    continue  # no spoke, or the fill all round: not a relief
                spokes = []
                for n in range(0, len(crossings) - 1, 2):
                    # A crossing into the fill and the next out of it: the
                    # island it belongs to.
                    spokes.append(crossings[n][1])
                _judge(run, board, zone_item, layer, fp, pad, len(spokes), least)


def _judge(run, board, zone_item, layer, fp, pad, spokes: int, least: int) -> None:
    if not 0 < spokes < least:
        return
    run.report(
        "starved_thermal",
        f"Thermal relief connection to zone incomplete (layer {board.layer_name(layer)}; "
        f"zone min spoke count {least}; actual {spokes})",
        [describe.pad(board, fp, pad), zone_item],  # the pad first, as KiCad lists them
    )
