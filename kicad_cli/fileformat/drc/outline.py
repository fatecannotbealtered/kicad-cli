"""The board's outline: whether Edge.Cuts closes into shapes a board can be
cut to.

Measured on boards drawn one outline each (`tests/fixtures/drc/outline/`):

- no drawing on Edge.Cuts at all is a malformed outline;
- lines and arcs join end to end where their ends are within 10 um of each
  other (10.5 um is a gap); a chain that does not close is malformed, as is
  a stray line within the board;
- two edges crossing -- an outline crossing itself, or two outlines each
  other -- is malformed too;
- a board of several separate outlines, or one with a cut-out inside, is
  not.

Which of an outline's drawings KiCad names for a fault follows its own
order; the fault is what is compared.
"""

from __future__ import annotations

from .. import geometry
from ..board import shape_points
from . import items as describe
from .copper import ARC_ERROR
from .shapes import segments_cross

CHAIN = 10_000  # nm: ends this near are joined
CELL = 5_000_000
TITLE = "Board has malformed outline"


def drawings(board) -> list[tuple[describe.Item, list[tuple[int, int]], bool]]:
    """(item, points, closed) for every Edge.Cuts drawing, footprints' too."""
    out = []
    for node in board.edge_shapes():
        out.append(_drawing(board, node, None))
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
                out.append(_drawing(board, node, fp))
    return [d for d in out if d[1]]


def _drawing(board, node, fp):
    points = shape_points(node, ARC_ERROR)
    if fp is not None:
        points = geometry.place(points, *fp.position, fp.angle)
    closed = node.head.split("_", 1)[1] in ("circle", "rect", "poly")
    item = describe.shape(board, node, points[0] if points else (0, 0), fp)
    return item, [tuple(p) for p in points], closed


def _near(a, b) -> bool:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 <= CHAIN * CHAIN


def _chains(found):
    """Open drawings joined end to end: (pieces, closed), each piece an item
    and its points in the chain's direction."""
    loops = [([(item, points + [points[0]])], True) for item, points, closed in found if closed]
    left = [(item, points) for item, points, closed in found if not closed]
    while left:
        chain = [left.pop(0)]
        while True:
            head, tail = chain[0][1][0], chain[-1][1][-1]
            if (len(chain) > 1 or len(chain[0][1]) > 2) and _near(head, tail):
                loops.append((chain, True))
                break
            for index, (item, points) in enumerate(left):
                if _near(points[0], tail):
                    chain = chain + [(item, points)]
                elif _near(points[-1], tail):
                    chain = chain + [(item, points[::-1])]
                elif _near(points[-1], head):
                    chain = [(item, points)] + chain
                elif _near(points[0], head):
                    chain = [(item, points[::-1])] + chain
                else:
                    continue
                left.pop(index)
                break
            else:
                loops.append((chain, False))
                break
    return loops


def check(run) -> None:
    if not run.on("invalid_outline"):
        return
    board = run.board
    found = drawings(board)
    if not found:
        run.report(
            "invalid_outline", f"{TITLE} (no edges found on Edge.Cuts layer)",
            [describe.Item("board", "", "PCB", (0, 0))],
        )  # fmt: skip
        return
    edges = []
    for chain, closed in _chains(found):
        if not closed:
            run.report(
                "invalid_outline", f"{TITLE} (not a closed shape)", [chain[0][0], chain[-1][0]]
            )
        for item, points in chain:
            edges += [(item, points[i], points[i + 1]) for i in range(len(points) - 1)]
    crossed = _crossing(edges)
    if crossed is not None:
        run.report("invalid_outline", f"{TITLE} (self-intersecting)", crossed)


def _crossing(edges):
    """The items of two edges that cross, if any do."""
    grid: dict[tuple[int, int], list[int]] = {}
    for index, (_item, a, b) in enumerate(edges):
        for cx in range(int(min(a[0], b[0])) // CELL, int(max(a[0], b[0])) // CELL + 1):
            for cy in range(int(min(a[1], b[1])) // CELL, int(max(a[1], b[1])) // CELL + 1):
                grid.setdefault((cx, cy), []).append(index)
    for members in grid.values():
        for x, first in enumerate(members):
            for second in members[x + 1 :]:
                item1, a, b = edges[first]
                item2, c, d = edges[second]
                if segments_cross(a, b, c, d):
                    return [item1, item2]
    return None
