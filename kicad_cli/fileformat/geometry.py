"""Plane geometry in KiCad's frame, in integer nanometres.

KiCad's y axis points down and an angle turns counter-clockwise as seen on
screen. So a point (x, y) turned by a is

    (x cos a + y sin a,  -x sin a + y cos a)

which is the rotation measured against pcbnew on every pad of the demo
boards, front and back: a footprint's children are stored in its own frame,
already mirrored if it sits on the back, and are placed by turning them by
the footprint's angle and adding its position.

Curves become polygons whose vertices lie on the curve, the chords inside it,
none further from the curve than ``max_error`` -- KiCad's own default is
5 um. Two such approximations of one shape can differ by up to max_error
times its perimeter in area, which is how comparisons against KiCad measure
agreement.
"""

from __future__ import annotations

import math

Point = tuple[int, int]

MAX_ERROR = 5_000  # nm; KiCad's default arc approximation error


def rotate(x: float, y: float, degrees: float) -> tuple[float, float]:
    """Turn (x, y) about the origin, exactly at multiples of 90 degrees."""
    quarter = degrees % 360
    if quarter == 0:
        return x, y
    if quarter == 90:
        return y, -x
    if quarter == 180:
        return -x, -y
    if quarter == 270:
        return -y, x
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    return x * c + y * s, -x * s + y * c


def place(points, dx: int, dy: int, degrees: float) -> list[Point]:
    """Turn a shape about its origin, move it, and round to the nanometre."""
    out = []
    for x, y in points:
        rx, ry = rotate(x, y, degrees)
        out.append((round(rx + dx), round(ry + dy)))
    return out


def area(points: list[Point]) -> float:
    """Unsigned area of a simple polygon (shoelace)."""
    total = 0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2


def bbox(points) -> tuple[int, int, int, int]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def segments_for(radius: float, sweep_degrees: float, max_error: float = MAX_ERROR) -> int:
    """Chords needed so none strays more than max_error inside the arc."""
    if radius <= max_error:
        return max(1, math.ceil(abs(sweep_degrees) / 90))
    per_chord = 2 * math.degrees(math.acos(1 - max_error / radius))
    return max(1, math.ceil(abs(sweep_degrees) / per_chord))


def arc(cx: float, cy: float, radius: float, start_deg: float, sweep_deg: float,
        max_error: float = MAX_ERROR) -> list[tuple[float, float]]:  # fmt: skip
    """Points along an arc, both ends included. Angles in the maths sense
    (counter-clockwise with y up); callers in KiCad's frame pass y already
    negated where it matters."""
    n = segments_for(radius, sweep_deg, max_error)
    out = []
    for i in range(n + 1):
        a = math.radians(start_deg + sweep_deg * i / n)
        out.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
    return out


def circle(radius: float, max_error: float = MAX_ERROR) -> list[tuple[float, float]]:
    return arc(0, 0, radius, 0, 360, max_error)[:-1]


def arc_through(start: Point, mid: Point, end: Point, max_error: float = MAX_ERROR):
    """The arc KiCad stores as start / mid / end, as points from start to end."""
    (x1, y1), (x2, y2), (x3, y3) = start, mid, end
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if d == 0:  # collinear: a straight track that says it is an arc
        return [start, end]
    ux = ((x1 * x1 + y1 * y1) * (y2 - y3) + (x2 * x2 + y2 * y2) * (y3 - y1)
          + (x3 * x3 + y3 * y3) * (y1 - y2)) / d  # fmt: skip
    uy = ((x1 * x1 + y1 * y1) * (x3 - x2) + (x2 * x2 + y2 * y2) * (x1 - x3)
          + (x3 * x3 + y3 * y3) * (x2 - x1)) / d  # fmt: skip
    r = math.hypot(x1 - ux, y1 - uy)
    a1 = math.degrees(math.atan2(y1 - uy, x1 - ux))
    a2 = math.degrees(math.atan2(y2 - uy, x2 - ux))
    a3 = math.degrees(math.atan2(y3 - uy, x3 - ux))
    sweep = (a3 - a1) % 360
    # The sweep that passes through the middle point, not the other way round.
    if (a2 - a1) % 360 > sweep:
        sweep -= 360
    return arc(ux, uy, r, a1, sweep, max_error)


# -- pad shapes, centred on the origin, before any rotation ---------------------


def rectangle(w: float, h: float) -> list[tuple[float, float]]:
    return [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]


def trapezoid(w: float, h: float, dx: float, dy: float) -> list[tuple[float, float]]:
    """KiCad's `rect_delta`: dx lengthens one vertical side and shortens the
    other, dy does the same to the horizontal sides."""
    hw, hh, ddx, ddy = w / 2, h / 2, dx / 2, dy / 2
    return [
        (-hw - ddy, hh + ddx),
        (hw + ddy, hh - ddx),
        (hw - ddy, -hh + ddx),
        (-hw + ddy, -hh - ddx),
    ]


def rounded_rectangle(w: float, h: float, radius: float, max_error: float = MAX_ERROR):
    if radius <= 0:
        return rectangle(w, h)
    radius = min(radius, w / 2, h / 2)
    hw, hh = w / 2 - radius, h / 2 - radius
    out = []
    for cx, cy, start in ((hw, hh, 0), (-hw, hh, 90), (-hw, -hh, 180), (hw, -hh, 270)):
        out += arc(cx, cy, radius, start, 90, max_error)
    return out


def oval(w: float, h: float, max_error: float = MAX_ERROR):
    return rounded_rectangle(w, h, min(w, h) / 2, max_error)


def chamfered_rectangle(w: float, h: float, chamfer: float, corners: set[str],
                        radius: float = 0, max_error: float = MAX_ERROR):  # fmt: skip
    """A rectangle, optionally rounded, with named corners cut at 45 degrees.

    Corner names are KiCad's, in its y-down frame: top is -y.
    """
    hw, hh = w / 2, h / 2
    radius = min(max(radius, 0), hw, hh)
    chamfer = min(max(chamfer, 0), hw, hh)
    # Walk the corners in a fixed order, each either cut, rounded or square.
    specs = (
        ("bottom_right", hw, hh, 0),
        ("bottom_left", -hw, hh, 90),
        ("top_left", -hw, -hh, 180),
        ("top_right", hw, -hh, 270),
    )
    out: list[tuple[float, float]] = []
    for name, x, y, start in specs:
        sx = 1 if x > 0 else -1
        sy = 1 if y > 0 else -1
        if name in corners and chamfer > 0:
            # The first point on the edge reached first in this walk.
            if start in (0, 180):
                out += [(x, y - sy * chamfer), (x - sx * chamfer, y)]
            else:
                out += [(x - sx * chamfer, y), (x, y - sy * chamfer)]
        elif radius > 0:
            out += arc(x - sx * radius, y - sy * radius, radius, start, 90, max_error)
        else:
            out.append((x, y))
    return out
