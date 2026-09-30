"""Footprints' courtyards: whether each has one, whether it closes, and
whether two collide or a hole falls inside another's.

A courtyard is what a footprint draws on F.CrtYd or B.CrtYd, lines and arcs
joined end to end into closed outlines, rectangles, circles and polygons
closed already. Measured on `tests/fixtures/drc/drc1`:

- lines that do not close are a malformed courtyard;
- two footprints' courtyards on one side of the board overlap when they
  overlap by 10 um or more (`probe`: 9.999 um is not reported, 10 um is) --
  courtyards that only touch do not, and a front one never meets a back one;
- a plated or unplated hole of one footprint inside another's courtyard,
  on either side, is reported as that;
- a footprint with no courtyard at all is missing one, unless it says a
  courtyard is not required of it.
"""

from __future__ import annotations

import math
from collections import defaultdict

from .. import geometry
from ..board import Footprint, shape_points
from ..connectivity import Polygon
from . import items as describe
from .shapes import hole, segment_segment, triangles

CHAIN = 1_000  # nm: how near two ends must be to join
CELL = 5_000_000  # nm: the grid that finds which courtyards and holes are near which
OVERLAP = 10_000  # nm: the least overlap KiCad reports
SIDES = ("F.CrtYd", "B.CrtYd")


class Courtyard:
    def __init__(self, fp: Footprint, document=None) -> None:
        self.fp = fp
        self.outlines: dict[str, list[list[tuple[int, int]]]] = {s: [] for s in SIDES}
        self.malformed = False
        self.present = False
        open_paths: dict[str, list[list[tuple[int, int]]]] = {s: [] for s in SIDES}
        for node in _drawn_on_courtyards(fp, document):
            layer = node.find("layer")
            side = layer.value(1) if layer is not None else None
            if side not in SIDES:
                continue
            self.present = True
            points = geometry.place(shape_points(node), *fp.position, fp.angle)
            if node.head in ("fp_line", "fp_arc"):
                open_paths[side].append(points)
            elif len(points) >= 3:
                self.outlines[side].append(points)
        for side, paths in open_paths.items():
            closed, ok = _chain(paths)
            self.outlines[side] += closed
            self.malformed |= not ok
        self.polygons = {
            side: [Polygon(o) for o in outlines if len(o) >= 3]
            for side, outlines in self.outlines.items()
        }


def _drawn_on_courtyards(fp: Footprint, document):
    """A footprint's drawings that may be on a courtyard layer. A footprint
    draws dozens of silkscreen and fabrication lines; which of them are
    courtyard is read from the text first, so the rest are never parsed."""
    source = document.source if document is not None else None
    span = document.span(fp.node) if document is not None else None
    if span is not None and source.find("CrtYd", *span) < 0:
        return
    for node in fp.node.lists():
        if node.head not in ("fp_line", "fp_arc", "fp_circle", "fp_rect", "fp_poly"):
            continue
        inner = document.span(node) if document is not None else None
        if inner is not None and source.find("CrtYd", *inner) < 0:
            continue
        yield node


def _chain(paths: list[list[tuple[int, int]]]) -> tuple[list[list[tuple[int, int]]], bool]:
    """Lines and arcs joined end to end into closed outlines; False when some
    would not close."""
    left = [list(p) for p in paths if len(p) >= 2]
    closed = []
    ok = True
    while left:
        outline = left.pop(0)
        while True:
            if len(outline) > 2 and _near(outline[0], outline[-1]):
                closed.append(outline[:-1])
                break
            for i, path in enumerate(left):
                if _near(path[0], outline[-1]):
                    outline += path[1:]
                elif _near(path[-1], outline[-1]):
                    outline += path[-2::-1]
                else:
                    continue
                left.pop(i)
                break
            else:
                ok = False
                break
    return closed, ok


def _near(a, b) -> bool:
    return abs(a[0] - b[0]) <= CHAIN and abs(a[1] - b[1]) <= CHAIN


def _convex(points) -> bool:
    sign = 0
    n = len(points)
    for i in range(n):
        (ax, ay), (bx, by), (cx, cy) = points[i - 2], points[i - 1], points[i]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if cross:
            if sign and (cross > 0) != (sign > 0):
                return False
            sign = cross
    return True


def _pieces(polygon: Polygon) -> list[list[tuple[float, float]]]:
    """An outline as convex pieces: itself, or its triangles."""
    points = list(polygon.points)
    if _convex(points):
        return [points]
    return [list(t) for t in triangles(points)]


def _clip(subject, clipper):
    """The part of one convex polygon inside another (Sutherland-Hodgman)."""
    area = sum(clipper[i - 1][0] * clipper[i][1] - clipper[i][0] * clipper[i - 1][1]
               for i in range(len(clipper)))  # fmt: skip
    turn = 1 if area > 0 else -1
    out = list(subject)
    for i in range(len(clipper)):
        a, b = clipper[i - 1], clipper[i]
        inside = [turn * ((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])) >= 0
                  for p in out]  # fmt: skip
        clipped = []
        for j in range(len(out)):
            p, q = out[j - 1], out[j]
            if inside[j]:
                if not inside[j - 1]:
                    clipped.append(_cross(p, q, a, b))
                clipped.append(q)
            elif inside[j - 1]:
                clipped.append(_cross(p, q, a, b))
        out = clipped
        if not out:
            break
    return out


def _cross(p, q, a, b):
    """Where the line through p and q crosses the line through a and b."""
    x1, y1, x2, y2 = p[0], p[1], q[0], q[1]
    x3, y3, x4, y4 = a[0], a[1], b[0], b[1]
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if den == 0:
        return q
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


def _width(points) -> float:
    """A convex polygon's least width: across it, the narrowest way."""
    if len(points) < 3:
        return 0.0
    least = math.inf
    for i in range(len(points)):
        (ax, ay), (bx, by) = points[i - 1], points[i]
        length = math.hypot(bx - ax, by - ay)
        if length == 0:
            continue
        far = max(abs((bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax)) / length for p in points)
        least = min(least, far)
    return 0.0 if least is math.inf else least


def overlap(a: Polygon, b: Polygon) -> bool:
    """Whether two outlines overlap by KiCad's least overlap or more."""
    ax1, ay1, ax2, ay2 = a.bbox
    bx1, by1, bx2, by2 = b.bbox
    if ax2 - bx1 < OVERLAP or bx2 - ax1 < OVERLAP or ay2 - by1 < OVERLAP or by2 - ay1 < OVERLAP:
        return False
    return any(_width(_clip(p, q)) >= OVERLAP - 1 for p in _pieces(a) for q in _pieces(b))


def _edges(polygon: Polygon):
    points = polygon.points
    return [(points[i - 1], points[i]) for i in range(len(points))]


def check(run) -> None:
    rules = ("courtyards_overlap", "malformed_courtyard", "missing_courtyard",
             "pth_inside_courtyard", "npth_inside_courtyard")  # fmt: skip
    if not any(run.on(rule) for rule in rules):
        return
    board = run.board
    yards = [Courtyard(fp, board.document) for fp in board.footprints]
    for yard in yards:
        attr = yard.fp.attributes
        if not yard.present and "allow_missing_courtyard" not in attr:
            run.report(
                "missing_courtyard", "Footprint has no courtyard defined",
                [describe.footprint(yard.fp)],
            )  # fmt: skip
        if yard.malformed:
            run.report(
                "malformed_courtyard",
                "Footprint has malformed courtyard (not a closed shape)",
                [describe.footprint(yard.fp)],
            )
    boxes = [_box(yard) for yard in yards]
    if run.on("courtyards_overlap"):
        for i, j in _pairs(boxes):
            a, b = yards[i], yards[j]
            if any(
                overlap(p, q) for side in SIDES for p in a.polygons[side] for q in b.polygons[side]
            ):
                run.report(
                    "courtyards_overlap", "Courtyards overlap",
                    [describe.footprint(a.fp), describe.footprint(b.fp)],
                )  # fmt: skip
    if run.on("pth_inside_courtyard") or run.on("npth_inside_courtyard"):
        _holes_inside(run, yards, boxes)


def _cells(box):
    for cx in range(int(box[0] // CELL), int(box[2] // CELL) + 1):
        for cy in range(int(box[1] // CELL), int(box[3] // CELL) + 1):
            yield cx, cy


def _pairs(boxes) -> list[tuple[int, int]]:
    """The pairs of courtyards whose boxes meet, found by grid cell."""
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for index, box in enumerate(boxes):
        if box is not None:
            for cell in _cells(box):
                grid[cell].append(index)
    pairs = set()
    for members in grid.values():
        for n, i in enumerate(members):
            for j in members[n + 1 :]:
                if _meet(boxes[i], boxes[j]):
                    pairs.add((min(i, j), max(i, j)))
    return sorted(pairs)


def _box(yard: Courtyard):
    polygons = [p for side in SIDES for p in yard.polygons[side]]
    if not polygons:
        return None
    return (
        min(p.bbox[0] for p in polygons),
        min(p.bbox[1] for p in polygons),
        max(p.bbox[2] for p in polygons),
        max(p.bbox[3] for p in polygons),
    )


def _meet(a, b) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def _holes_inside(run, yards: list[Courtyard], boxes) -> None:
    board = run.board
    holes: dict[tuple[int, int], list] = defaultdict(list)
    for fp in board.footprints:
        for pad in fp.pads:
            shape = hole(pad) if pad.kind in ("thru_hole", "np_thru_hole") else None
            if shape is not None:
                for cell in _cells(shape.bbox()):
                    holes[cell].append((fp, pad, shape))
    for yard, box in zip(yards, boxes, strict=True):
        if box is None:
            continue
        polygons = [p for side in SIDES for p in yard.polygons[side]]
        seen = set()
        for cell in _cells(box):
            for fp, pad, shape in holes.get(cell, ()):
                if fp is yard.fp or id(pad) in seen:
                    continue
                seen.add(id(pad))
                x1, y1, x2, y2 = shape.bbox()
                if x2 < box[0] or x1 > box[2] or y2 < box[1] or y1 > box[3]:
                    continue
                if not any(_hole_in(polygon, shape) for polygon in polygons):
                    continue
                kind = "pth" if pad.kind == "thru_hole" else "npth"
                run.report(
                    f"{kind}_inside_courtyard", f"{kind.upper()} inside courtyard",
                    [describe.pad(board, fp, pad), describe.footprint(yard.fp)],
                )  # fmt: skip


def _hole_in(polygon: Polygon, shape) -> bool:
    if any(polygon.contains(*p) for p in shape.core):
        return True
    ends = (shape.core[0], shape.core[-1])
    return any(segment_segment(*ends, a, b) < shape.radius for a, b in _edges(polygon))
