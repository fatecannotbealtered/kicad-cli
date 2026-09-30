"""What copper fails to reach: connections still open, ends left dangling.

A net's copper falls into clusters (`connectivity.py`, which agrees with
pcbnew's own on every demo board). Measured on `tests/fixtures/drc/drc1`:

- Every cluster of a net beyond the first is a connection still to make,
  reported between the two nearest items of the clusters it would join, as
  a minimum spanning tree joins them. A cluster of zone fill alone is not
  one: it is an island, which is another check.
- A track's end is held by copper on its layer that comes within half the
  track's width of it -- a pad's, a via's, a zone fill's, a copper
  graphic's, another track's, in its middle or at its end -- of any net: a
  track ending on another net's pad is a short, not a loose end. A track
  reaching both ends of a short one holds only the end nearer its own end,
  so a sliver of track lying along another is loose at its far end
  (`tests/fixtures/drc/drc1`, and four such slivers on KiCad's NFC antenna
  demo), while a short jog between two tracks is held at both and a stub
  inside one pad is held by it. A track is reported once, whichever end
  dangles.
- A via is dangling unless copper of its net meets it on two layers at
  least: a track on one and a pad on another, a pad through every layer.
  Another via touching it does not count, and a via standing inside a pad's
  hole meets none of its copper.
"""

from __future__ import annotations

import math
from collections import defaultdict

from .. import connectivity
from ..connectivity import Item as Copper
from . import items as describe
from .shapes import depth, hole

CELL = connectivity.CELL
COUNTED = {"pad", "segment", "arc", "via", "shape"}
FILL_ANCHORS = 64  # vertices of a fill polygon an open connection may be drawn to


def check(run) -> None:
    rules = ("unconnected_items", "track_dangling", "via_dangling")
    if not any(run.on(rule) for rule in rules):
        return
    joined = run.connectivity()
    grid = _grid(joined.items)
    if run.on("unconnected_items"):
        _unconnected(run, joined)
    if run.on("track_dangling"):
        _tracks(run, joined.items, grid)
    if run.on("via_dangling"):
        _vias(run, joined.items, grid)


def _grid(items: list[Copper]) -> dict[tuple[str, int, int], list[int]]:
    grid: dict[tuple[str, int, int], list[int]] = defaultdict(list)
    for index, item in enumerate(items):
        x1, y1, x2, y2 = item.bbox
        for layer in item.layers:
            for cx in range(x1 // CELL, x2 // CELL + 1):
                for cy in range(y1 // CELL, y2 // CELL + 1):
                    grid[(layer, cx, cy)].append(index)
    return grid


def _near(grid, layer: str, x: float, y: float):
    return grid.get((layer, int(x) // CELL, int(y) // CELL), ())


def describe_copper(board, item: Copper):
    owner = item.owner
    if item.kind == "pad":
        fp, pad = owner
        return describe.pad(board, fp, pad)
    if item.kind in ("segment", "arc"):
        return describe.track(board, owner)
    if item.kind == "via":
        return describe.via(board, owner)
    if item.kind == "fill":
        return describe.zone(board, owner)
    position = item.path[0] if item.path else (item.bbox[0], item.bbox[1])
    return describe.shape(board, owner, position)


# -- connections still open -----------------------------------------------------------------


def _anchors(item: Copper) -> list[tuple[int, int]]:
    if item.kind == "pad":
        return [item.owner[1].position]
    if item.kind == "via":
        return [item.owner.position]
    if item.kind in ("segment", "arc"):
        return [item.path[0], item.path[-1]]
    if item.kind == "fill":
        points = item.polygon.points
        step = max(1, len(points) // FILL_ANCHORS)
        return points[::step]
    return list(item.path or [])


def _unconnected(run, joined) -> None:
    board = run.board
    clusters: dict[str, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
    counted: dict[str, set[int]] = defaultdict(set)
    for index, item in enumerate(joined.items):
        if not item.net:
            continue
        cluster = joined.cluster_of[index]
        clusters[item.net][cluster].append(index)
        if item.kind in COUNTED:
            counted[item.net].add(cluster)
    for net in sorted(clusters):
        open_ = sorted(counted[net])
        if len(open_) < 2:
            continue
        points = []  # (x, y, cluster, item index, rank)
        for cluster in open_:
            for index in clusters[net][cluster]:
                item = joined.items[index]
                # Of anchors equally near, KiCad names a track's before a pad's.
                rank = 1 if item.kind == "pad" else 0
                for x, y in _anchors(item):
                    points.append((x, y, cluster, index, rank))
        for a, b in _spanning(points, open_):
            run.report(
                "unconnected_items",
                "Missing connection between items",
                [describe_copper(board, joined.items[a]), describe_copper(board, joined.items[b])],
            )


def _spanning(points, clusters) -> list[tuple[int, int]]:
    """Prim's tree over the clusters, each step the nearest pair of anchors
    between a cluster joined and one not: [(item, item)]."""
    by_cluster: dict[int, list] = defaultdict(list)
    for p in points:
        by_cluster[p[2]].append(p)
    joined = {clusters[0]}
    # For every cluster not yet joined: its nearest anchor pair to the tree.
    best: dict[int, tuple[float, int, int, int]] = {}

    def offer(source: int) -> None:
        for cluster in clusters:
            if cluster in joined:
                continue
            found = best.get(cluster, (math.inf, 2, -1, -1))
            for ax, ay, _c, ai, ar in by_cluster[source]:
                for bx, by, _d, bi, br in by_cluster[cluster]:
                    d = (ax - bx) ** 2 + (ay - by) ** 2
                    if (d, ar + br) < found[:2]:
                        found = (d, ar + br, ai, bi)
            best[cluster] = found

    offer(clusters[0])
    edges = []
    while len(joined) < len(clusters):
        cluster = min(best, key=lambda c: best[c][:2])
        _d, _rank, a, b = best.pop(cluster)
        edges.append((a, b))
        joined.add(cluster)
        offer(cluster)
    return edges


# -- dangling ends --------------------------------------------------------------------------


def _reaches(item: Copper, x: float, y: float, tolerance: float) -> bool:
    """Whether an item's copper comes within `tolerance` of a point."""
    x1, y1, x2, y2 = item.bbox
    if not (x1 - tolerance <= x <= x2 + tolerance and y1 - tolerance <= y <= y2 + tolerance):
        return False
    if item.polygon is not None:
        if item.polygon.contains(x, y):
            return True
        edges = item.polygon.near_edges(x - tolerance, y - tolerance, x + tolerance, y + tolerance)
        if any(connectivity._distance_to_segment(x, y, *a, *b) <= tolerance for a, b in edges):
            return True
    if item.path is not None:
        return any(
            connectivity._distance_to_segment(x, y, *p, *q) <= item.radius + tolerance
            for p, q in connectivity._path_segments(item.path)
        )
    return False


def _around(grid, layer: str, x: float, y: float, reach: float) -> set[int]:
    found: set[int] = set()
    for cx in range(int(x - reach) // CELL, int(x + reach) // CELL + 1):
        for cy in range(int(y - reach) // CELL, int(y + reach) // CELL + 1):
            found.update(grid.get((layer, cx, cy), ()))
    return found


def _ends_held(items: list[Copper], index: int, grid) -> bool:
    """Whether copper of any net holds both of a track's ends: comes within
    half the track's width of each. A pad, a via, a zone's fill or a copper
    graphic holds every end it reaches. Another track that reaches both ends
    holds only one of them -- the one nearer its own end -- so a sliver of
    track lying along another is loose at one end, while a short jog between
    two tracks is held at both."""
    item = items[index]
    layer, half = item.layers[0], item.radius
    ends = (item.path[0], item.path[-1])
    held = [False, False]
    near = _around(grid, layer, *ends[0], half) | _around(grid, layer, *ends[1], half)
    for other in near:
        if other == index:
            continue
        target = items[other]
        reach = [_reaches(target, *ends[0], half), _reaches(target, *ends[1], half)]
        if not (reach[0] or reach[1]):
            continue
        if reach[0] and reach[1] and target.kind in ("segment", "arc"):
            own = (target.path[0], target.path[-1])
            nearer = [min(math.dist(e, point) for e in own) for point in ends]
            held[0 if nearer[0] <= nearer[1] else 1] = True
        else:
            held[0] |= reach[0]
            held[1] |= reach[1]
        if held[0] and held[1]:
            return True
    return False


def _tracks(run, items: list[Copper], grid) -> None:
    board = run.board
    for index, item in enumerate(items):
        if item.kind not in ("segment", "arc"):
            continue
        if not _ends_held(items, index, grid):
            run.report(
                "track_dangling", "Track has unconnected end", [describe.track(board, item.owner)]
            )


def _in_hole(via: Copper, pad_item: Copper) -> bool:
    """Whether a via stands wholly inside a pad's hole, touching no copper."""
    _fp, pad = pad_item.owner
    shape = hole(pad) if pad.kind == "thru_hole" else None
    if shape is None:
        return False
    x, y = via.path[0]
    return depth((x, y), shape.core) + shape.radius > via.radius


def _vias(run, items: list[Copper], grid) -> None:
    board = run.board
    for index, item in enumerate(items):
        if item.kind != "via":
            continue
        met: set[str] = set()
        x1, y1, x2, y2 = item.bbox
        seen = set()
        for layer in item.layers:
            if layer in met:
                continue
            for cx in range(x1 // CELL, x2 // CELL + 1):
                for cy in range(y1 // CELL, y2 // CELL + 1):
                    for other in grid.get((layer, cx, cy), ()):
                        if other == index or (other, layer) in seen:
                            continue
                        seen.add((other, layer))
                        target = items[other]
                        if target.kind == "via" or target.net != item.net:
                            continue  # another via is not a connection of its own
                        if not connectivity._boxes_meet(item.bbox, target.bbox):
                            continue
                        if target.kind == "pad" and _in_hole(item, target):
                            continue
                        if connectivity._overlap(item, target):
                            met |= set(item.layers) & set(target.layers)
            if len(met) >= 2:
                break
        if len(met) < 2:
            run.report(
                "via_dangling",
                "Via is not connected or connected on only one layer",
                [describe.via(board, item.owner)],
            )
