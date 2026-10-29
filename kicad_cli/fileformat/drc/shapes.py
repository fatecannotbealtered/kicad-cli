"""The shapes DRC measures, exactly where they can be.

A pad's copper, a via's, a track's and a hole are each a core -- a point, a
segment or a convex polygon -- grown by a radius: a round pad is a point
grown by half its size, an oval one a segment grown by half its width, a
rounded rectangle the rectangle inside its corners grown by their radius, a
slot a segment grown by half the drill's width. Distances between such
shapes are the distances between their cores less the two radii, which is
exact: no circle is cut into chords before it is measured, so a clearance
of 0.1 mm measured here is 0.1 mm, not 0.1 mm give or take the chords.

Whatever is not convex -- a custom pad, a courtyard, a zone -- is a list of
such shapes, or a polygon.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .. import geometry
from ..board import Pad, Via, shape_points, stroke_width

Point = tuple[float, float]


@dataclass(frozen=True, slots=True)
class Shape:
    """A convex core grown by a radius. `core` is one point, two (a
    segment), or a convex polygon's vertices."""

    core: tuple[Point, ...]
    radius: float = 0.0
    box: tuple[float, float, float, float] = field(default=(0, 0, 0, 0), compare=False)

    def __post_init__(self) -> None:
        xs = [p[0] for p in self.core]
        ys = [p[1] for p in self.core]
        r = self.radius
        object.__setattr__(self, "box", (min(xs) - r, min(ys) - r, max(xs) + r, max(ys) + r))

    def bbox(self) -> tuple[float, float, float, float]:
        return self.box


# -- distances ------------------------------------------------------------------------------


def point_segment(p: Point, a: Point, b: Point) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    if length2 == 0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def _orient(p: Point, q: Point, r: Point) -> float:
    return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])


def segments_cross(a: Point, b: Point, c: Point, d: Point) -> bool:
    d1, d2 = _orient(a, b, c), _orient(a, b, d)
    d3, d4 = _orient(c, d, a), _orient(c, d, b)
    return ((d1 > 0) != (d2 > 0) and d1 != 0 and d2 != 0) and (
        (d3 > 0) != (d4 > 0) and d3 != 0 and d4 != 0
    )


def segment_segment(a: Point, b: Point, c: Point, d: Point) -> float:
    """Between two segments (either may be a point): the nearest points'
    distance, found by where each segment's parameter clamps; 0 exactly
    where they cross."""
    ax, ay = a
    cx, cy = c
    ux, uy = b[0] - ax, b[1] - ay
    vx, vy = d[0] - cx, d[1] - cy
    wx, wy = ax - cx, ay - cy
    uu = ux * ux + uy * uy
    vv = vx * vx + vy * vy
    vw = vx * wx + vy * wy
    if uu == 0 and vv == 0:
        return math.hypot(wx, wy)
    if uu == 0:
        s, t = 0.0, min(1.0, max(0.0, vw / vv))
    else:
        uw = ux * wx + uy * wy
        if vv == 0:
            s, t = min(1.0, max(0.0, -uw / uu)), 0.0
        else:
            uv = ux * vx + uy * vy
            den = uu * vv - uv * uv
            if den > 0:
                # Not parallel: crossing is exact zero, not a rounding of it.
                cross1 = ux * (cy - ay) - uy * (cx - ax)
                cross2 = ux * (d[1] - ay) - uy * (d[0] - ax)
                if (cross1 > 0) != (cross2 > 0) and cross1 and cross2:
                    cross3 = vx * (ay - cy) - vy * (ax - cx)
                    cross4 = vx * (b[1] - cy) - vy * (b[0] - cx)
                    if (cross3 > 0) != (cross4 > 0) and cross3 and cross4:
                        return 0.0
                s = min(1.0, max(0.0, (uv * vw - uw * vv) / den))
            else:
                s = 0.0
            t = (uv * s + vw) / vv
            if t < 0:
                s, t = min(1.0, max(0.0, -uw / uu)), 0.0
            elif t > 1:
                s, t = min(1.0, max(0.0, (uv - uw) / uu)), 1.0
    return math.hypot(wx + ux * s - vx * t, wy + uy * s - vy * t)


def _edges(core: tuple[Point, ...]):
    n = len(core)
    if n == 1:
        return [(core[0], core[0])]
    if n == 2:
        return [(core[0], core[1])]
    return [(core[i - 1], core[i]) for i in range(n)]


def inside_convex(p: Point, core: tuple[Point, ...]) -> bool:
    """Whether a point is inside (or on) a convex polygon, either winding."""
    if len(core) < 3:
        return False
    sign = 0
    for a, b in _edges(core):
        o = _orient(a, b, p)
        if o == 0:
            continue
        s = 1 if o > 0 else -1
        if sign == 0:
            sign = s
        elif s != sign:
            return False
    return sign != 0  # a polygon with no area has no inside


def core_distance(a: tuple[Point, ...], b: tuple[Point, ...]) -> float:
    """Between two convex cores: 0 where they meet."""
    if len(a) <= 2 and len(b) <= 2:
        return segment_segment(a[0], a[-1], b[0], b[-1])
    if len(a) >= 3 and any(inside_convex(p, a) for p in b[:1]):
        return 0.0
    if len(b) >= 3 and any(inside_convex(p, b) for p in a[:1]):
        return 0.0
    return min(segment_segment(p, q, r, s) for p, q in _edges(a) for r, s in _edges(b))


def distance(a: Shape, b: Shape) -> float:
    """Between two shapes' edges; 0 where they overlap or touch."""
    return max(0.0, core_distance(a.core, b.core) - a.radius - b.radius)


def depth(p: Point, core: tuple[Point, ...]) -> float:
    """How far a point is inside a convex core's boundary; negative outside.
    A point or a segment has no inside: its depth is minus the distance."""
    if len(core) >= 3 and inside_convex(p, core):
        return min(_line_distance(p, a, b) for a, b in _edges(core))
    return -min(point_segment(p, a, b) for a, b in _edges(core))


def _line_distance(p: Point, a: Point, b: Point) -> float:
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    if length == 0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    return abs(_orient(a, b, p)) / length


def ring(outer: Shape, hole: Shape) -> float:
    """How much of `outer` is left around `hole` at its narrowest: 0 when the
    hole reaches out of it. Both convex, the hole a point or a segment grown
    by its radius; the narrowest place is at one of the hole's ends."""
    ends = hole.core if len(hole.core) <= 2 else hole.core
    least = min(depth(p, outer.core) for p in ends)
    return max(0.0, least + outer.radius - hole.radius)


def covers(outer: Shape, inner: Shape) -> bool:
    """Whether `inner` lies wholly within `outer`."""
    return all(depth(p, outer.core) + outer.radius >= inner.radius for p in inner.core)


# -- the shapes of things -------------------------------------------------------------------


def hole(pad: Pad) -> Shape | None:
    """A pad's hole, where the pad is: KiCad offsets the copper, not the hole."""
    if pad.drill is None:
        return None
    w, h = pad.drill
    x, y = pad.position
    if w == h:
        return Shape(((x, y),), w / 2)
    half = (max(w, h) - min(w, h)) / 2
    dx, dy = (half, 0.0) if w > h else (0.0, half)
    ax, ay = geometry.rotate(dx, dy, pad.angle)
    return Shape(((x - ax, y - ay), (x + ax, y + ay)), min(w, h) / 2)


def via_hole(via: Via) -> Shape:
    return Shape((via.position,), via.drill / 2)


def via_copper(via: Via) -> Shape:
    return Shape((via.position,), via.diameter / 2)


def _placed(pad: Pad, points) -> tuple[Point, ...]:
    ox, oy = pad.drill_offset
    x, y = pad.position
    out = []
    for px, py in points:
        rx, ry = geometry.rotate(px + ox, py + oy, pad.angle)
        out.append((x + rx, y + ry))
    return tuple(out)


def pad_copper(pad: Pad, max_error: float = geometry.MAX_ERROR) -> list[Shape]:
    """A pad's copper: one convex shape, or several for a custom pad (its
    anchor and each primitive)."""
    w, h = pad.size
    shape = pad.shape
    if shape == "circle":
        return [Shape(_placed(pad, [(0, 0)]), w / 2)]
    if shape == "oval":
        half = abs(w - h) / 2
        ends = [(-half, 0), (half, 0)] if w > h else [(0, -half), (0, half)]
        return [Shape(_placed(pad, ends), min(w, h) / 2)]
    if shape == "trapezoid":
        return [Shape(_placed(pad, geometry.trapezoid(w, h, *pad.rect_delta)))]
    if shape in ("rect", "roundrect", "chamfered_rect"):
        radius = pad.roundrect_ratio * min(w, h) if shape != "rect" else 0.0
        if pad.chamfer_ratio > 0 and pad.chamfer_corners:
            chamfer = pad.chamfer_ratio * min(w, h)
            points = geometry.chamfered_rectangle(
                w, h, chamfer, set(pad.chamfer_corners), radius, max_error
            )
            return [Shape(_placed(pad, points))]
        radius = min(radius, min(w, h) / 2)
        return [Shape(_placed(pad, _box(w - 2 * radius, h - 2 * radius)), radius)]
    if shape == "custom":
        return _custom(pad, max_error)
    return [Shape(_placed(pad, geometry.rectangle(w, h)))]


def _box(w: float, h: float) -> list[tuple[float, float]]:
    """A rectangle about the origin; a segment or a point when it has no
    width or no height -- a rounded rectangle whose corners meet."""
    if w <= 0 and h <= 0:
        return [(0.0, 0.0)]
    if h <= 0:
        return [(-w / 2, 0.0), (w / 2, 0.0)]
    if w <= 0:
        return [(0.0, -h / 2), (0.0, h / 2)]
    return geometry.rectangle(w, h)


def _custom(pad: Pad, max_error: float) -> list[Shape]:
    w, h = pad.size
    anchor = (
        Shape(_placed(pad, [(0, 0)]), w / 2) if pad.anchor == "circle"
        else Shape(_placed(pad, geometry.rectangle(w, h)))
    )  # fmt: skip
    out = [anchor]
    primitives = pad.node.find("primitives") if pad.node is not None else None
    for primitive in primitives.lists() if primitives is not None else []:
        out += primitive_shapes(pad, primitive, max_error)
    return out


def primitive_shapes(pad: Pad, node, max_error: float = geometry.MAX_ERROR) -> list[Shape]:
    """A custom pad's primitive, as convex pieces: a filled polygon is cut
    into triangles, an outline into its strokes."""
    points = shape_points(node, max_error)
    if not points:
        return []
    half = stroke_width(node) / 2
    placed = _placed_raw(pad, points)
    fill = node.find("fill")
    filled = fill is not None and fill.value(1) in ("yes", "solid")
    head = node.head
    if head == "gr_circle" and not filled:
        closed = list(placed) + [placed[0]]
        return [Shape((closed[i], closed[i + 1]), half) for i in range(len(closed) - 1)]
    if head in ("gr_poly", "gr_rect", "gr_circle") or filled:
        out = [Shape(t, half) for t in triangles(placed)] if len(placed) >= 3 else []
        closed = list(placed) + [placed[0]]
        if half > 0 or not out:
            out += [Shape((closed[i], closed[i + 1]), half) for i in range(len(closed) - 1)]
        return out
    return [Shape((placed[i], placed[i + 1]), half) for i in range(len(placed) - 1)] or [
        Shape((placed[0],), half)
    ]


def _placed_raw(pad: Pad, points) -> tuple[Point, ...]:
    x, y = pad.position
    out = []
    for px, py in points:
        rx, ry = geometry.rotate(px, py, pad.angle)
        out.append((x + rx, y + ry))
    return tuple(out)


def triangles(points) -> list[tuple[Point, Point, Point]]:
    """A simple polygon cut into triangles, ears first."""
    pts = list(points)
    if len(pts) > 3 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 3:
        return []
    area = sum(pts[i - 1][0] * pts[i][1] - pts[i][0] * pts[i - 1][1] for i in range(len(pts)))
    if area < 0:
        pts.reverse()
    out = []
    guard = 0
    while len(pts) > 3 and guard < 10_000:
        guard += 1
        n = len(pts)
        for i in range(n):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
            if _orient(a, b, c) <= 0:
                continue
            if any(p not in (a, b, c) and _in_triangle(p, a, b, c) for p in pts):
                continue
            out.append((a, b, c))
            del pts[i]
            break
        else:
            break  # not simple: what is left is kept as it is
    if len(pts) >= 3:
        out.append(tuple(pts[:3]) if len(pts) == 3 else tuple(pts))
    return out


def _in_triangle(p, a, b, c) -> bool:
    return _orient(a, b, p) >= 0 and _orient(b, c, p) >= 0 and _orient(c, a, p) >= 0
