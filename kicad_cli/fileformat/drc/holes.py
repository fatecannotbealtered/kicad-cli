"""Holes too close to each other, or on top of each other.

Measured on `tests/fixtures/drc/drc1`: every drilled hole counts -- a via's,
a plated pad's, an unplated pad's, pads of one footprint as much as of two --
and the distance is between the holes' edges, a slot's by its whole length.
Two holes whose centres are one point are co-located, whatever their sizes
(a via in a pad's hole is), and are reported as that, not as too close.
Vias whose layers do not meet do not drill into each other.
"""

from __future__ import annotations

from collections import defaultdict

from . import items as describe
from . import mm
from .shapes import Shape, distance, hole, via_hole

CELL = 2_000_000


def _holes(board):
    out = []
    for fp in board.footprints:
        for pad in fp.pads:
            if pad.kind not in ("thru_hole", "np_thru_hole"):
                continue
            shape = hole(pad)
            if shape is not None and pad.drill is not None and min(pad.drill) > 0:
                out.append((shape, pad.position, None, describe.pad(board, fp, pad)))
    for via in board.vias:
        out.append((via_hole(via), via.position, set(via.layers), describe.via(board, via)))
    return out


def check(run) -> None:
    if not (run.on("hole_to_hole") or run.on("holes_co_located")):
        return
    least = run.settings.nm("min_hole_to_hole")
    holes = _holes(run.board)
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for index, (shape, *_rest) in enumerate(holes):
        x1, y1, x2, y2 = _grown(shape, least)
        for cx in range(int(x1 // CELL), int(x2 // CELL) + 1):
            for cy in range(int(y1 // CELL), int(y2 // CELL) + 1):
                grid[(cx, cy)].append(index)
    done = set()
    for cell in grid.values():
        for i in cell:
            for j in cell:
                if j <= i or (i, j) in done:
                    continue
                done.add((i, j))
                _pair(run, holes[i], holes[j], least)


def _grown(shape: Shape, by: int):
    x1, y1, x2, y2 = shape.bbox()
    return x1 - by, y1 - by, x2 + by, y2 + by


def _pair(run, a, b, least: int) -> None:
    shape_a, centre_a, layers_a, item_a = a
    shape_b, centre_b, layers_b, item_b = b
    if layers_a is not None and layers_b is not None and not layers_a & layers_b:
        return
    if centre_a == centre_b:
        run.report("holes_co_located", "Drilled holes co-located", [item_a, item_b])
        return
    gap = distance(shape_a, shape_b)
    if gap < least:
        run.report(
            "hole_to_hole",
            f"Drilled hole too close to other hole (board setup constraints min {mm(least)}; "
            f"actual {mm(round(gap))})",
            [item_a, item_b],
        )
