"""Polygons in whole nanometres, and what is done to them to fill a zone:
union, intersection, difference, and growing or shrinking by a distance.

A polygon is a list of rings, each a list of (x, y) points, the last joined
back to the first. Which points a set of rings covers is decided by winding
-- by default, a point is covered when the rings wind round it any number of
times but none (`NONZERO`); a ring and a ring wound the other way inside it
make a hole. What comes out is rings that do not cross: the boundaries of
what is covered, wound counter-clockwise round what they cover (with y up;
on a board, where y runs down, they are seen the other way round), holes
the other way. Two rings may touch at a point.

How it is done -- every step on whole numbers, nothing left to floating
point but the rounding of a crossing to the nearest nanometre:

- every edge is cut where any other crosses or touches it, and again where
  rounding a cut moved it onto another, until no two edges cross;
- edges lying on one another are merged, each keeping how much it adds to
  each operand's winding;
- one sweep from the bottom up finds the winding on either side of every
  edge, each from the edge to its left: no two edges cross between two
  rows of points, so a row's edges keep their order;
- the edges with covered on one side and not on the other are kept, turned
  to keep the covered side on their left, and joined into rings, taking at
  each point the turn furthest to the left.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from collections import defaultdict

from . import geometry

Point = tuple[int, int]
Ring = list[Point]

NONZERO, POSITIVE, EVENODD = "nonzero", "positive", "evenodd"


# -- small things ---------------------------------------------------------------------------


def area(ring: Ring) -> float:
    """Twice nothing: the signed area, positive counter-clockwise (y up)."""
    total = 0
    for i in range(len(ring)):
        x1, y1 = ring[i - 1]
        x2, y2 = ring[i]
        total += x1 * y2 - x2 * y1
    return total / 2


def total_area(rings: list[Ring]) -> float:
    return sum(area(r) for r in rings)


def _cross(o: Point, a: Point, b: Point) -> int:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _round_div(num: int, den: int) -> int:
    """num / den to the nearest whole number, halves away from zero."""
    if den < 0:
        num, den = -num, -den
    if num >= 0:
        return (2 * num + den) // (2 * den)
    return -((-2 * num + den) // (2 * den))


def clean(ring: Ring) -> Ring:
    """A ring without points repeated one after another or lying on a straight
    line between their neighbours."""
    points = [p for i, p in enumerate(ring) if p != ring[i - 1]] if len(ring) > 1 else list(ring)
    changed = True
    while changed and len(points) >= 3:
        changed = False
        out = []
        n = len(points)
        for i in range(n):
            a, b, c = points[i - 1], points[i], points[(i + 1) % n]
            if _cross(a, b, c) == 0 and (
                (b[0] - a[0]) * (c[0] - b[0]) + (b[1] - a[1]) * (c[1] - b[1]) >= 0 or a == c
            ):
                changed = True
                continue
            out.append(b)
        points = [p for i, p in enumerate(out) if p != out[i - 1]] if len(out) > 1 else out
    return points if len(points) >= 3 else []


# -- cutting edges where they meet ----------------------------------------------------------


def _segments(rings_by_operand: list[list[Ring]]) -> list[tuple[Point, Point, int]]:
    """Every edge, with the operand it belongs to."""
    out = []
    for operand, rings in enumerate(rings_by_operand):
        for ring in rings:
            n = len(ring)
            if n < 2:
                continue
            for i in range(n):
                a, b = ring[i - 1], ring[i]
                if a != b:
                    out.append((a, b, operand))
    return out


def _meet(p1: Point, p2: Point, q1: Point, q2: Point) -> list[Point]:
    """Where segment p meets segment q: no point, one (rounded to the
    nanometre), or the two ends of their overlap."""
    d1 = _cross(q1, q2, p1)
    d2 = _cross(q1, q2, p2)
    d3 = _cross(p1, p2, q1)
    d4 = _cross(p1, p2, q2)
    if d1 == 0 and d2 == 0:  # on one line
        if d3 != 0 or d4 != 0:
            return []
        # Project on the longer axis.
        axis = 0 if abs(p2[0] - p1[0]) >= abs(p2[1] - p1[1]) else 1
        lo_p, hi_p = sorted((p1, p2), key=lambda p: p[axis])
        lo_q, hi_q = sorted((q1, q2), key=lambda p: p[axis])
        lo = max(lo_p, lo_q, key=lambda p: p[axis])
        hi = min(hi_p, hi_q, key=lambda p: p[axis])
        if lo[axis] > hi[axis]:
            return []
        return [lo] if lo == hi else [lo, hi]
    if (d1 > 0 and d2 > 0) or (d1 < 0 and d2 < 0) or (d3 > 0 and d4 > 0) or (d3 < 0 and d4 < 0):
        return []
    # They cross, or one touches the other: where, rounded.
    if d1 == 0:
        return [p1]
    if d2 == 0:
        return [p2]
    if d3 == 0:
        return [q1]
    if d4 == 0:
        return [q2]
    den = d1 - d2
    x = p1[0] + _round_div((p2[0] - p1[0]) * d1, den)
    y = p1[1] + _round_div((p2[1] - p1[1]) * d1, den)
    return [(x, y)]


def _near_pairs(segments) -> list[tuple[int, int]]:
    """Pairs of edges whose boxes meet, found by a grid of cells about as
    big as the edges: each pair is looked at in the one cell holding the
    lower corner of where their boxes overlap."""
    n = len(segments)
    if n < 2:
        return []
    x1 = [min(a[0], b[0]) for a, b, _ in segments]
    x2 = [max(a[0], b[0]) for a, b, _ in segments]
    y1 = [min(a[1], b[1]) for a, b, _ in segments]
    y2 = [max(a[1], b[1]) for a, b, _ in segments]
    sizes = sorted(max(x2[i] - x1[i], y2[i] - y1[i]) for i in range(n))
    cell = max(1, sizes[n // 2] * 2)
    cells: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i in range(n):
        for cx in range(x1[i] // cell, x2[i] // cell + 1):
            for cy in range(y1[i] // cell, y2[i] // cell + 1):
                cells[(cx, cy)].append(i)
    pairs = []
    for (cx, cy), members in cells.items():
        count = len(members)
        if count < 2:
            continue
        for k in range(count):
            i = members[k]
            xi1, xi2, yi1, yi2 = x1[i], x2[i], y1[i], y2[i]
            for m in range(k + 1, count):
                j = members[m]
                lo_x = xi1 if xi1 > x1[j] else x1[j]
                if lo_x > (xi2 if xi2 < x2[j] else x2[j]):
                    continue
                lo_y = yi1 if yi1 > y1[j] else y1[j]
                if lo_y > (yi2 if yi2 < y2[j] else y2[j]):
                    continue
                if lo_x // cell == cx and lo_y // cell == cy:
                    pairs.append((i, j))
    return pairs


def _cut(segments: list[tuple[Point, Point, int]]) -> list[tuple[Point, Point, int]]:
    """The edges cut wherever another meets them, by snap rounding: each
    point where two meet, rounded to the nanometre, and each end, is a hot
    pixel -- the square of a nanometre round it -- and an edge passing
    through a hot pixel is bent through its centre. Edges so bent meet only
    at their corners (Hobby, 1999); where rounding still left a crossing,
    its point is made hot too and the edges bent again."""
    hot: set[Point] = set()
    for a, b, _ in segments:
        hot.add(a)
        hot.add(b)
    for _ in range(8):
        found = False
        for i, j in _near_pairs(segments):
            a, b, _ = segments[i]
            c, d, _ = segments[j]
            ux, uy = d[0] - c[0], d[1] - c[1]
            d1 = ux * (a[1] - c[1]) - uy * (a[0] - c[0])
            d2 = ux * (b[1] - c[1]) - uy * (b[0] - c[0])
            if (d1 > 0 and d2 > 0) or (d1 < 0 and d2 < 0):
                continue
            vx, vy = b[0] - a[0], b[1] - a[1]
            d3 = vx * (c[1] - a[1]) - vy * (c[0] - a[0])
            d4 = vx * (d[1] - a[1]) - vy * (d[0] - a[0])
            if (d3 > 0 and d4 > 0) or (d3 < 0 and d4 < 0):
                continue
            if (a == c or a == d or b == c or b == d) and (d1 or d2) and (d3 or d4):
                continue  # sharing an end and not on one line: that end is all
            for point in _meet(a, b, c, d):
                if point not in hot:
                    hot.add(point)
                    found = True
        segments = _bend(segments, hot)
        if not found:
            return segments
    raise ArithmeticError("edges still crossing after snapping them eight times")


def _bend(segments, hot: set[Point]) -> list[tuple[Point, Point, int]]:
    """Each edge bent through the centre of every hot pixel it passes
    through, in order along it."""
    if not segments:
        return segments
    sizes = sorted(max(abs(b[0] - a[0]), abs(b[1] - a[1])) for a, b, _ in segments)
    cell = max(2, sizes[len(sizes) // 2] * 2)
    grid: dict[tuple[int, int], list[Point]] = defaultdict(list)
    for p in hot:
        grid[(p[0] // cell, p[1] // cell)].append(p)
    out = []
    for a, b, op in segments:
        x1, x2 = min(a[0], b[0]), max(a[0], b[0])
        y1, y2 = min(a[1], b[1]), max(a[1], b[1])
        dx, dy = b[0] - a[0], b[1] - a[1]
        through = []
        for cx in range((x1 - 1) // cell, (x2 + 1) // cell + 1):
            for cy in range((y1 - 1) // cell, (y2 + 1) // cell + 1):
                for p in grid.get((cx, cy), ()):
                    px, py = p
                    if px < x1 - 1 or px > x2 + 1 or py < y1 - 1 or py > y2 + 1:
                        continue
                    if p == a or p == b:
                        continue
                    # Does the edge meet the square (px +- 1/2, py +- 1/2)? Its
                    # corners, doubled to stay whole, not all on one side.
                    if 2 * px + 1 < 2 * x1 or 2 * px - 1 > 2 * x2:
                        continue
                    if 2 * py + 1 < 2 * y1 or 2 * py - 1 > 2 * y2:
                        continue
                    sides = [
                        dx * (2 * py + ey - 2 * a[1]) - dy * (2 * px + ex - 2 * a[0])
                        for ex in (-1, 1) for ey in (-1, 1)
                    ]  # fmt: skip
                    if min(sides) > 0 or max(sides) < 0:
                        continue
                    through.append(p)
        if not through:
            out.append((a, b, op))
            continue
        through.sort(key=lambda p: (p[0] - a[0]) * dx + (p[1] - a[1]) * dy)
        chain = [a, *through, b]
        for k in range(len(chain) - 1):
            if chain[k] != chain[k + 1]:
                out.append((chain[k], chain[k + 1], op))
    return out


# -- winding on either side of each edge ----------------------------------------------------


class _Edge:
    """An edge after cutting, bottom end first (left first when level), with
    what it adds to each operand's winding crossing it from its right to its
    left."""

    __slots__ = ("lo", "hi", "wind", "left", "right", "dx", "dy")

    def __init__(self, lo: Point, hi: Point, operands: int) -> None:
        self.lo, self.hi = lo, hi
        self.wind = [0] * operands
        self.left: list[int] | None = None
        self.right: list[int] | None = None
        self.dx, self.dy = hi[0] - lo[0], hi[1] - lo[1]


def _edges(segments, operands: int) -> list[_Edge]:
    merged: dict[tuple[Point, Point], _Edge] = {}
    for a, b, op in segments:
        lo, hi = (a, b) if (a[1], a[0]) < (b[1], b[0]) else (b, a)
        edge = merged.get((lo, hi))
        if edge is None:
            edge = merged[(lo, hi)] = _Edge(lo, hi, operands)
        # Upward (as drawn) adds one on its left: a counter-clockwise ring
        # has its inside on the left of every edge.
        edge.wind[op] += 1 if (a, b) == (lo, hi) else -1
    return [e for e in merged.values() if any(e.wind)]


class _X:
    """A number num / den, den > 0, compared exactly."""

    __slots__ = ("num", "den")

    def __init__(self, num: int, den: int) -> None:
        self.num, self.den = num, den

    def __lt__(self, other: _X) -> bool:
        return self.num * other.den < other.num * self.den


def _x_mid(edge: _Edge, y: int) -> _X:
    """A rising edge's x on the row y + 1/2."""
    return _X(2 * edge.lo[0] * edge.dy + edge.dx * (2 * (y - edge.lo[1]) + 1), 2 * edge.dy)


def _x_at(edge: _Edge, y: int) -> _X:
    """A rising edge's x at y itself."""
    return _X(edge.lo[0] * edge.dy + edge.dx * (y - edge.lo[1]), edge.dy)


def _windings(edges: list[_Edge], operands: int) -> None:
    """Fill in each edge's windings on its left and its right, sweeping up.

    Between two rows of points no two edges cross, so the edges across a row
    keep their order, left to right, until one ends: an edge starting is put
    in its place, and what is on its left is what is on the right of the
    edge before it. A level edge has below it what is on the right of the
    last edge left of its middle, just under its row."""
    rising = [e for e in edges if e.dy > 0]
    starts: dict[int, list[_Edge]] = defaultdict(list)
    ends: dict[int, list[_Edge]] = defaultdict(list)
    for e in rising:
        starts[e.lo[1]].append(e)
        ends[e.hi[1]].append(e)
    level_at: dict[int, list[_Edge]] = defaultdict(list)
    for e in edges:
        if e.dy == 0:
            level_at[e.lo[1]].append(e)
    zero = [0] * operands
    active: list[_Edge] = []
    for y in sorted(set(starts) | set(ends) | set(level_at)):
        for e in level_at.get(y, ()):
            # Just under the middle of a level edge: order by x at y itself,
            # which no edge reaches inside it -- cutting saw to that.
            i = bisect_left(active, _X(e.lo[0] + e.hi[0], 2), key=lambda a: _x_at(a, y))
            below = active[i - 1].right if i > 0 else zero
            e.right = list(below)  # a level edge runs left to right: below is its right
            e.left = [w + d for w, d in zip(below, e.wind, strict=True)]
        for e in ends.get(y, ()):
            i = bisect_left(active, _x_mid(e, y - 1), key=lambda a: _x_mid(a, y - 1))
            del active[i]
        for e in sorted(starts.get(y, ()), key=lambda e: _x_mid(e, y)):
            i = bisect_left(active, _x_mid(e, y), key=lambda a: _x_mid(a, y))
            left = active[i - 1].right if i > 0 else zero
            e.left = list(left)
            e.right = [w - d for w, d in zip(left, e.wind, strict=True)]
            active.insert(i, e)


# -- joining the kept edges into rings ------------------------------------------------------


def _rings(directed: list[tuple[Point, Point]]) -> list[Ring]:
    out_of: dict[Point, list[Point]] = defaultdict(list)
    for a, b in directed:
        out_of[a].append(b)
    used: set[tuple[Point, Point]] = set()
    rings = []
    for start, first in directed:
        if (start, first) in used:
            continue
        ring = [start]
        used.add((start, first))
        prev, here = start, first
        while here != start:
            ring.append(here)
            choices = [p for p in out_of[here] if (here, p) not in used]
            if not choices:
                break
            nxt = choices[0] if len(choices) == 1 else _leftmost(prev, here, choices)
            used.add((here, nxt))
            prev, here = here, nxt
        else:
            ring = clean(ring)
            if ring:
                rings.append(ring)
    return rings


def _leftmost(prev: Point, here: Point, choices: list[Point]) -> Point:
    """Of the ways on from `here`, the sharpest turn to the left -- the first
    met turning clockwise from the way back: what is covered is on the left,
    and so a ring keeps to the one piece it bounds where two meet at a
    point."""
    back = (prev[0] - here[0], prev[1] - here[1])

    def turn(p: Point):
        v = (p[0] - here[0], p[1] - here[1])
        c = back[0] * v[1] - back[1] * v[0]
        d = back[0] * v[0] + back[1] * v[1]
        # Counter-clockwise from the way back: the half-plane first (0 for
        # [0, pi), 1 for [pi, 2 pi)), then the angle within it.
        half = 0 if c > 0 or (c == 0 and d > 0) else 1
        return half, _AngleKey(back, v)

    return max(choices, key=turn)


class _AngleKey:
    __slots__ = ("back", "v")

    def __init__(self, back, v) -> None:
        self.back, self.v = back, v

    def __lt__(self, other: _AngleKey) -> bool:
        # Within one half-plane: a before b when b is counter-clockwise of a.
        a, b = self.v, other.v
        return a[0] * b[1] - a[1] * b[0] > 0


# -- the operations -------------------------------------------------------------------------


def _filled(winding: int, rule: str) -> bool:
    if rule == POSITIVE:
        return winding > 0
    if rule == EVENODD:
        return winding % 2 == 1
    return winding != 0


def boolean(operands: list[list[Ring]], keep, rule: str = NONZERO) -> list[Ring]:
    """What `keep(covered...)` -- one flag per operand -- holds for, as rings."""
    count = len(operands)
    segments = _cut(_segments(operands))
    edges = _edges(segments, count)
    _windings(edges, count)
    directed = []
    for e in edges:
        inside_left = keep(*(_filled(w, rule) for w in e.left))
        inside_right = keep(*(_filled(w, rule) for w in e.right))
        if inside_left == inside_right:
            continue
        # Keep what is covered on the left: rising edges run up, level
        # edges left to right; turn the other ones round.
        directed.append((e.lo, e.hi) if inside_left else (e.hi, e.lo))
    return _rings(directed)


def union(rings: list[Ring], rule: str = NONZERO) -> list[Ring]:
    return boolean([rings], lambda a: a, rule)


def unite(a: list[Ring], b: list[Ring], rule: str = NONZERO) -> list[Ring]:
    return boolean([a, b], lambda x, y: x or y, rule)


def intersection(a: list[Ring], b: list[Ring], rule: str = NONZERO) -> list[Ring]:
    return boolean([a, b], lambda x, y: x and y, rule)


def difference(a: list[Ring], b: list[Ring], rule: str = NONZERO) -> list[Ring]:
    return boolean([a, b], lambda x, y: x and not y, rule)


def xor(a: list[Ring], b: list[Ring], rule: str = NONZERO) -> list[Ring]:
    return boolean([a, b], lambda x, y: x != y, rule)


# -- holes joined to their outline ----------------------------------------------------------


def contains(ring: Ring, x: float, y: float) -> bool:
    """Whether a point is inside one ring, by its winding."""
    w = 0
    n = len(ring)
    for i in range(n):
        (ax, ay), (bx, by) = ring[i - 1], ring[i]
        if ay <= y < by and (bx - ax) * (y - ay) - (by - ay) * (x - ax) > 0:
            w += 1
        elif by <= y < ay and (bx - ax) * (y - ay) - (by - ay) * (x - ax) < 0:
            w -= 1
    return w != 0


def fracture(rings: list[Ring]) -> list[Ring]:
    """Each outline with its holes made one ring, a hole joined to it by a
    cut of no width -- the way a board file holds a zone's fill. Rings as
    the operations above give them: outlines counter-clockwise, holes the
    other way."""
    outers = [r for r in rings if area(r) > 0]
    holes = [r for r in rings if area(r) < 0]
    owned: dict[int, list[Ring]] = defaultdict(list)
    for hole in holes:
        # The smallest outline round a point just inside the hole's edge.
        probe = _inside_point(hole)
        best = None
        for k, outer in enumerate(outers):
            if contains(outer, *probe) and (best is None or area(outer) < area(outers[best])):
                best = k
        if best is not None:
            owned[best].append(hole)
    out = []
    for k, outer in enumerate(outers):
        ring = list(outer)
        for hole in sorted(owned.get(k, ()), key=lambda h: -max(p[0] for p in h)):
            ring = _bridge(ring, hole)
        out.append(ring)
    return out


def _inside_point(hole: Ring) -> tuple[float, float]:
    """A point a quarter of a nanometre inside a hole, off its first edge."""
    (ax, ay), (bx, by) = hole[0], hole[1]
    length = math.hypot(bx - ax, by - ay)
    # A hole runs clockwise: its inside is on the right of its edges.
    nx, ny = (by - ay) / length, -(bx - ax) / length
    return (ax + bx) / 2 + nx * 0.25, (ay + by) / 2 + ny * 0.25


def _bridge(ring: Ring, hole: Ring) -> Ring:
    """A hole joined to the ring round it from its rightmost point, along
    the row to the right to the first edge met -- to that edge's right end,
    or to the corner of the ring in the way that is nearest that row."""
    m = max(range(len(hole)), key=lambda i: (hole[i][0], -hole[i][1]))
    mx, my = hole[m]
    best = None  # (x as num/den, edge index)
    n = len(ring)
    for i in range(n):
        (ax, ay), (bx, by) = ring[i], ring[(i + 1) % n]
        if not (min(ay, by) <= my <= max(ay, by)) or ay == by:
            continue
        num = ax * (by - ay) + (bx - ax) * (my - ay)
        den = by - ay
        if den < 0:
            num, den = -num, -den
        if num < mx * den:
            continue
        if best is None or num * best[1] < best[0] * den:
            best = (num, den, i)
    if best is None:
        raise ValueError("a hole with no ring round it")
    num, den, i = best
    a, b = ring[i], ring[(i + 1) % n]
    if num == a[0] * den and my == a[1]:
        p = i
    elif num == b[0] * den and my == b[1]:
        p = (i + 1) % n
    else:
        p = i if a[0] > b[0] else (i + 1) % n
        # Corners of the ring inside the triangle M, I, P block the view of
        # P: take the one nearest the row instead.
        ix = num / den
        px, py = ring[p]
        blocking = []
        for k in range(n):
            if k == p:
                continue
            qx, qy = ring[k]
            if _in_triangle((mx, my), (ix, my), (px, py), (qx, qy)):
                angle = abs(math.atan2(qy - my, qx - mx))
                blocking.append((angle, math.hypot(qx - mx, qy - my), k))
        if blocking:
            p = min(blocking)[2]
    joined = hole[m:] + hole[: m + 1]
    return ring[: p + 1] + joined + [ring[p]] + ring[p + 1 :]


def _in_triangle(a, b, c, q) -> bool:
    def side(p1, p2, p3):
        return (p2[0] - p1[0]) * (p3[1] - p1[1]) - (p2[1] - p1[1]) * (p3[0] - p1[0])

    d1, d2, d3 = side(a, b, q), side(b, c, q), side(c, a, q)
    negative = d1 < 0 or d2 < 0 or d3 < 0
    positive = d1 > 0 or d2 > 0 or d3 > 0
    return not (negative and positive)


# -- growing and shrinking ------------------------------------------------------------------


def offset(rings: list[Ring], delta: float, max_error: float = geometry.MAX_ERROR,
           outside: bool = False) -> list[Ring]:  # fmt: skip
    """The rings grown by `delta` (shrunk where it is negative), corners
    rounded, each arc in chords none of which strays more than max_error
    from it: inside the arc -- the corner cut -- or, with `outside`, outside
    it. Grown with arcs inside, or shrunk with arcs outside, a shape never
    takes in more than it should; the other way round, never less."""
    if delta == 0:
        return union(rings)
    raw = []
    for ring in rings:
        ring = clean(ring)
        if not ring:
            continue
        raw.append(_offset_ring(ring, delta, max_error, outside))
    return union(raw, POSITIVE)


def _offset_ring(ring: Ring, delta: float, max_error: float, outside: bool = False) -> Ring:
    """One ring's edges moved out by delta -- to their right, which is out of
    a counter-clockwise ring -- and joined round its corners: an arc where
    the ring turns away from the side moved to, the corner itself where it
    turns towards it, the loops that makes undone by the union after."""
    n = len(ring)
    out: list[Point] = []
    normals = []
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        normals.append((dy / length, -dx / length))  # right of the edge, y up
    for i in range(n):
        here = ring[i]
        before = normals[i - 1]
        after = normals[i]
        turn = before[0] * after[1] - before[1] * after[0]
        start = (here[0] + before[0] * delta, here[1] + before[1] * delta)
        end = (here[0] + after[0] * delta, here[1] + after[1] * delta)
        # Turning so that the moved edges part: join them by an arc.
        parting = turn > 0 if delta > 0 else turn < 0
        out.append(_snap(start))
        if parting:
            a1 = math.atan2(before[1], before[0])
            a2 = math.atan2(after[1], after[0])
            sweep = a2 - a1
            while sweep > math.pi:
                sweep -= 2 * math.pi
            while sweep < -math.pi:
                sweep += 2 * math.pi
            steps = geometry.segments_for(abs(delta), math.degrees(abs(sweep)), max_error)
            if outside:
                # Corners just outside the arc, their chords touching it.
                far = delta / math.cos(sweep / steps / 2)
                for k in range(steps):
                    t = a1 + sweep * (k + 0.5) / steps
                    out.append(_snap((here[0] + math.cos(t) * far, here[1] + math.sin(t) * far)))
            else:
                for k in range(1, steps):
                    t = a1 + sweep * k / steps
                    out.append(
                        _snap((here[0] + math.cos(t) * delta, here[1] + math.sin(t) * delta))
                    )
        else:
            out.append(here)
        out.append(_snap(end))
    return [p for i, p in enumerate(out) if p != out[i - 1]]


def _snap(p: tuple[float, float]) -> Point:
    return round(p[0]), round(p[1])
