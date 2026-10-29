"""A board as Specctra DSN, the file Freerouting routes, and a Specctra
session (.ses), the file it hands back.

The DSN is written as pcbnew writes it -- same sections, same order, same
names, same numbers -- so Freerouting is given the board it was given
before this tool wrote the file itself. What pcbnew's export does was
measured on its output, KiCad's demo boards and boards of one question each;
no part of it is KiCad's code.

Units are micrometres, y up: a board's y is negated.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field

from . import geometry
from .board import Board, Footprint, Pad, stroke_width
from .drc.settings import Settings
from .sexpr import List

INDENT = "  "
QUOTED = set(" \t\n%(){}")


# -- words and numbers ----------------------------------------------------------


def quote(word: str) -> str:
    """A word as pcbnew writes it: a double quote turned into two single
    ones, and the word in quotes when it is empty or has a space, a bracket,
    a brace, a per cent sign, or a minus anywhere but first (measured on net
    names of every printable character)."""
    word = word.replace('"', "''")
    if word == "" or any(c in QUOTED for c in word) or "-" in word[1:]:
        return '"' + word + '"'
    return word


def g(value: float) -> str:
    """A number to six significant digits, as C's %.6g writes it."""
    text = format(value, ".6g")
    return "0" if text in ("-0", "0") else text


def exact(nm: int) -> str:
    """A length in micrometres to the nanometre: a part's place."""
    text = f"{nm / 1000.0:.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def point_list(head: str, pts: list[tuple[float, float]]) -> str:
    """`(head  x y  x y ...)` in micrometres, the line broken as pcbnew
    breaks it: twelve spaces in on a new line, once the line from the
    opening bracket on is past seventy characters."""
    out = "(" + head + "  "
    count = len(out)
    for k, (x, y) in enumerate(pts):
        word = g(x) + " " + g(y)
        if k:
            if count > 70:
                out += "\n" + " " * 12
                count = 12
            else:
                out += "  "
                count += 2
        out += word
        count += len(word)
    return out + ")"


def word_list(first: str, words: list[str], indent: int, wrap: int, step: int = 2) -> str:
    """Words after a head, the line broken once it is past `wrap`
    characters, each new line `step` spaces further in than the head."""
    out = " " * indent + first
    count = len(out)
    for word in words:
        if count > wrap:
            out += "\n" + " " * (indent + step)
            count = indent + step
        else:
            out += " "
            count += 1
        out += word
        count += len(word)
    return out


def _um(nm: float) -> float:
    return nm / 1000.0


def _dsn(p: tuple[float, float]) -> tuple[float, float]:
    """A board point in micrometres, y up."""
    return _um(p[0]), -_um(p[1])


def _round(v: float) -> int:
    """Half away from zero, as pcbnew rounds a coordinate to the nanometre."""
    return int(math.floor(v + 0.5)) if v >= 0 else -int(math.floor(-v + 0.5))


# -- arcs as pcbnew draws them in its outlines ------------------------------------


def segments(radius: float, sweep: float, error: float) -> int:
    """How many straight pieces an arc of `sweep` degrees is drawn with: as
    many as keep each within `error` of the arc, rounded (measured on circles
    of 0.1 to 20 mm: 10 to 140 for a whole turn)."""
    sweep = math.radians(abs(sweep))
    # An arc that strays less than half the error from its chord is that
    # chord; any other is two pieces at least, none longer than an eighth
    # of a turn (measured on arcs of 30 um to 0.4 mm).
    if radius * (1 - math.cos(sweep / 2)) < error / 2:
        return 0
    step = 2 * math.acos(1 - error / radius) if radius > error else math.pi
    return max(2, _round(sweep / min(step, math.pi / 4)))


def arc_points(
    center: tuple[float, float], radius: float, start: float, sweep: float, error: float
) -> list[tuple[float, float]]:
    """A corner at the middle of each piece of the arc, half way between the
    arc and the polygon whose sides touch it, so the polygon strays as far
    outside the arc as inside. How far out is set by the whole turn's count
    of pieces of this arc's size, a whole number, cut down: an arc of 44.57
    degrees in 9 pieces sits as far out as one of 45 (measured)."""
    n = segments(radius, sweep, error)
    if n == 0:
        return []
    step = sweep / n
    turn = math.floor(360.0 * n / abs(sweep) + 1e-9)
    outer = radius * (1 + 1 / math.cos(math.pi / turn)) / 2
    cx, cy = center
    out = []
    for k in range(n):
        a = math.radians(start + (k + 0.5) * step)
        out.append((cx + outer * math.cos(a), cy + outer * math.sin(a)))
    return out


def arc_of(start: tuple[int, int], mid: tuple[int, int], end: tuple[int, int]):
    """An arc the file gives as three points, as pcbnew takes it: its centre
    the circle's through them put on the nearest tenth of a micrometre --
    which undoes the rounding of the three points to the nanometre -- its
    radius out to the start. (centre, radius, start angle, sweep), degrees,
    or None for three points in a line."""
    found = geometry._circle_through(start, mid, end)
    if found is None:
        return None
    ux, uy = found[0], found[1]
    cx = math.floor(ux / 100 + 0.5) * 100
    cy = math.floor(uy / 100 + 0.5) * 100
    a0 = math.degrees(math.atan2(start[1] - cy, start[0] - cx))
    a1 = math.degrees(math.atan2(end[1] - cy, end[0] - cx))
    am = math.degrees(math.atan2(mid[1] - cy, mid[0] - cx))
    sweep = (a1 - a0) % 360
    if (am - a0) % 360 > sweep:
        sweep -= 360
    return (cx, cy), math.hypot(start[0] - cx, start[1] - cy), a0, sweep


Ring = list[tuple[int, int]]


def edge_points(node: List, fp: Footprint | None, error: float) -> tuple[Ring, bool]:
    """An Edge.Cuts drawing as the corners pcbnew gives it: (points, closed)."""
    from .board import nm, points  # noqa: PLC0415

    def at(name: str) -> tuple[int, int]:
        found = node.find(name)
        return nm(found.atom(1)), nm(found.atom(2))

    head = node.head[3:]
    closed = head in ("circle", "rect", "poly")
    if head == "line":
        pts = [at("start"), at("end")]
    elif head == "rect":
        (x1, y1), (x2, y2) = at("start"), at("end")
        pts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    elif head == "arc":
        start, mid, end = at("start"), at("mid"), at("end")
        arc = arc_of(start, mid, end)
        if arc is None:
            pts = [start, end]
        else:
            center, r, a0, sweep = arc
            pts = (
                [start]
                + [(_round(x), _round(y)) for x, y in arc_points(center, r, a0, sweep, error)]
                + [end]
            )
    elif head == "circle":
        (cx, cy), (ex, ey) = at("center"), at("end")
        r = math.hypot(ex - cx, ey - cy)
        pts = [(_round(cx + r), cy)] + [
            (_round(x), _round(y)) for x, y in arc_points((cx, cy), r, 0, 360, error)
        ]
    elif head == "poly":
        pts = points(node.find("pts"))
    else:
        return [], False
    if fp is not None:
        pts = geometry.place(pts, *fp.position, fp.angle)
    return [tuple(p) for p in pts], closed


def _inside(p: tuple[int, int], ring: Ring) -> bool:
    x, y = p
    inside = False
    for i in range(len(ring)):
        (x1, y1), (x2, y2) = ring[i - 1], ring[i]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _area(ring: Ring) -> float:
    return (
        sum(ring[i - 1][0] * ring[i][1] - ring[i][0] * ring[i - 1][1] for i in range(len(ring))) / 2
    )


def _started(ring: Ring, outer: bool) -> Ring:
    """A ring turned the way pcbnew turns it -- an outline clockwise as the
    board is seen (y down), a cut-out the other way -- and started at the
    corner after its topmost one, the rightmost of those along the top of
    an outline, the leftmost of a cut-out's."""
    if (_area(ring) > 0) != outer:
        ring = ring[::-1]
    # Where two drawings meet a hair apart, the corner the ring comes to
    # first gives way to the next (CM5_MINIMA's left edge, 33 nm apart).
    from .drc.outline import _near  # noqa: PLC0415

    ring = [p for k, p in enumerate(ring) if not _near(p, ring[(k + 1) % len(ring)])]
    top = min(p[1] for p in ring)
    best = max(
        (k for k, p in enumerate(ring) if p[1] == top),
        key=lambda k: ring[k][0] if outer else -ring[k][0],
    )
    k = (best + 1) % len(ring)
    return ring[k:] + ring[:k]


ROUNDING = 2 - math.cos(
    math.pi / 36
)  # how far out a rounded pad's corners sit, as a share of the radius
CORNER_BITS = {"top_left": 1, "top_right": 2, "bottom_left": 4, "bottom_right": 8}
OUTLINE_SHAPES = ("fp_line", "fp_arc", "fp_circle", "fp_rect", "fp_poly")
# a class short of one of these is named with ",Default" after it
COMPOSITE_FIELDS = (
    "clearance",
    "track_width",
    "via_diameter",
    "via_drill",
    "microvia_diameter",
    "microvia_drill",
    "diff_pair_width",
    "diff_pair_gap",
    "wire_width",
    "bus_width",
)


def _layer(node: List) -> str | None:
    found = node.find("layer")
    return found.value(1) if found is not None else None


def corner_turn(radius: float, error: float) -> int:
    """How many pieces a whole turn of a rounded pad's corner would take:
    as many as keep within the error, never fewer than sixteen."""
    if radius <= error:
        return 16
    return max(16, _round(math.pi / math.acos(1 - error / radius)))


def rounded_rect(w: int, h: int, radius: float, outer: int, chamfer: float, corners, error: float):
    """A rounded or chamfered pad as pcbnew draws it, in the pad's frame
    with y down: from the top of the left side, round the top left corner
    and on round the rest. A rounded corner's arc sits `outer` from its
    centre (a little more than the radius, and the sides with it), cut in
    pieces of a whole turn's share with the two end pieces sharing what is
    left; a chamfered corner is cut straight, the chamfer a share of the
    shorter side."""
    grow = outer - radius
    half_w, half_h = w / 2 + grow, h / 2 + grow
    cut = chamfer * min(2 * half_w, 2 * half_h) if chamfer else 0.0
    cx, cy = w / 2 - radius, h / 2 - radius
    n = corner_turn(outer, error)
    pieces = math.ceil(n / 4)
    step = 360.0 / n
    end = (90.0 - (pieces - 2) * step) / 2 if pieces > 1 else 90.0
    out: list[tuple[int, int]] = []
    # corners in drawing order: (name, centre, start angle) with y down
    for name, (sx, sy), start in (
        ("top_left", (-1, -1), 180.0),
        ("top_right", (1, -1), 270.0),
        ("bottom_right", (1, 1), 0.0),
        ("bottom_left", (-1, 1), 90.0),
    ):
        corner = (sx * half_w, sy * half_h)
        if chamfer and name in corners:
            # along the side coming in, then along the side going on
            if name in ("top_left", "bottom_right"):
                a = (corner[0], corner[1] - sy * cut)
                b = (corner[0] - sx * cut, corner[1])
            else:
                a = (corner[0] - sx * cut, corner[1])
                b = (corner[0], corner[1] - sy * cut)
            out += [(_round(a[0]), _round(a[1])), (_round(b[0]), _round(b[1]))]
            continue
        if outer <= 0:
            out.append((_round(corner[0]), _round(corner[1])))
            continue
        centre = (sx * cx, sy * cy)
        angles = [start]
        if pieces > 1:
            angles.append(start + end)
            angles += [start + end + step * k for k in range(1, pieces - 1)]
        angles.append(start + 90.0)
        for a in angles:
            r = math.radians(a)
            out.append(
                (_round(centre[0] + outer * math.cos(r)), _round(centre[1] + outer * math.sin(r)))
            )
    # Where two corners meet with no side between, one corner is enough.
    return [p for k, p in enumerate(out) if p != out[k - 1]]


def custom_outline(pad: Pad, error: float) -> list[Ring]:
    """A custom pad as pcbnew gives it to Freerouting: the convex hull of its
    anchor and its primitives, in the pad's own frame (a notched pad comes
    out a rectangle, pic_programmer's JP1). A circle's corners are on the
    circle, at the middles of a whole number of pieces that is a multiple of
    four (One-Air-Max's JP2: 24 for half a millimetre)."""
    from .board import shape_points  # noqa: PLC0415

    w, h = pad.size
    if pad.anchor == "circle":
        pts = _circle_corners((0, 0), w / 2, error)
    else:
        pts = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    primitives = pad.node.find("primitives") if pad.node is not None else None
    for node in primitives.lists() if primitives is not None else []:
        half = stroke_width(node) / 2
        if node.head == "gr_circle":
            from .board import nm  # noqa: PLC0415

            c, e = node.find("center"), node.find("end")
            cx, cy = nm(c.atom(1)), nm(c.atom(2))
            r = math.hypot(nm(e.atom(1)) - cx, nm(e.atom(2)) - cy) + half
            pts += _circle_corners((cx, cy), r, error)
            continue
        local = shape_points(node, error)
        if half:
            closed = node.head in ("gr_poly", "gr_rect")
            pts += _stroked(local, half, closed, error)
        else:
            pts += local
    hull = _hull([(_round(x), _round(y)) for x, y in pts])
    return [hull] if hull else []


def _stroked(pts: list[tuple[float, float]], half: float, closed: bool, error: float):
    """The corners a pen of radius `half` drawn along pts goes round, for a
    hull to be taken of: at each corner, on each side, an arc from one
    side's normal to the next on the pen's radius, in as many equal steps
    as keep within the error (One-Air-Max's U5: two steps round a right
    angle for a 0.1 mm pen); at an open end, half a turn."""
    out: list[tuple[float, float]] = []
    n = len(pts)
    step = 2 * math.acos(1 - error / half) if half > error else math.pi / 2

    def normal(a, b):
        return math.atan2(b[1] - a[1], b[0] - a[0]) + math.pi / 2

    def arc(centre, start, turn):
        count = max(1, _round(abs(turn) / step))
        for k in range(count + 1):
            a = start + turn * k / count
            out.append((centre[0] + half * math.cos(a), centre[1] + half * math.sin(a)))

    for i, p in enumerate(pts):
        before = pts[i - 1] if (closed or i > 0) else None
        after = pts[(i + 1) % n] if (closed or i < n - 1) else None
        before = before if before != p else None
        after = after if after != p else None
        if before is None and after is None:
            out.append(p)
        elif before is None or after is None:
            a = normal(p, after) if before is None else normal(before, p)
            arc(p, a, math.pi)  # round the open end
        else:
            n1, n2 = normal(before, p), normal(p, after)
            for side in (0.0, math.pi):
                turn = (n2 - n1 + math.pi) % (2 * math.pi) - math.pi
                arc(p, n1 + side, turn)
    return out


def _circle_corners(center, radius: float, error: float) -> list[tuple[float, float]]:
    if radius <= 0:
        return [center]
    n = math.pi / math.acos(1 - error / radius) if radius > error else 4
    n = max(4, 4 * math.ceil(math.ceil(n) / 4))
    return [
        (
            center[0] + radius * math.cos(math.pi * (2 * k + 1) / n),
            center[1] + radius * math.sin(math.pi * (2 * k + 1) / n),
        )
        for k in range(n)
    ]


def _hull(points: list[tuple[int, int]]) -> Ring:
    """The convex hull, clockwise as the board is seen (y down)."""
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: Ring = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: Ring = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    ring = lower[:-1] + upper[:-1]
    return ring if _area(ring) > 0 else ring[::-1]


def inset(ring: Ring, d: float) -> list[tuple[float, float]]:
    """A ring drawn clockwise as the board is seen (y down), each side moved
    `d` inwards and the corners where the moved sides meet."""
    n = len(ring)
    lines = []
    for i in range(n):
        (x1, y1), (x2, y2) = ring[i], ring[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy) or 1.0
        # inwards of a clockwise ring with y down is to the right of travel
        nx, ny = -dy / length, dx / length
        lines.append(((x1 + nx * d, y1 + ny * d), (dx, dy)))
    out = []
    for i in range(n):
        (p, u), (q, v) = lines[i - 1], lines[i]
        det = u[0] * v[1] - u[1] * v[0]
        if abs(det) < 1e-9:
            out.append(q)
            continue
        t = ((q[0] - p[0]) * v[1] - (q[1] - p[1]) * v[0]) / det
        out.append((p[0] + u[0] * t, p[1] + u[1] * t))
    return out


# -- what goes into the file -------------------------------------------------------


@dataclass
class Padstack:
    name: str
    shapes: list[str]  # each `(shape ...)`, one per copper layer

    def body(self) -> str:
        return "".join("      " + s + "\n" for s in self.shapes) + "      (attach off)\n"

    def text(self) -> str:
        return "    (padstack " + quote(self.name) + "\n" + self.body() + "    )\n"


@dataclass
class Image:
    name: str
    lines: list[str] = field(default_factory=list)  # outlines, pins, keepouts

    def body(self) -> str:
        return "".join("      " + line + "\n" for line in self.lines)


class Dsn:
    def __init__(self, board: Board, settings: Settings) -> None:
        self.board = board
        self.settings = settings
        self.error = settings.nm("max_error")
        copper = board.copper_layers
        self.copper = copper
        self.names = {layer: board.layer_name(layer) for layer in copper}
        self.padstacks: dict[str, Padstack] = {}  # by body
        self.images: list[Image] = []
        self.image_of: dict[int, Image] = {}
        self.components: list[tuple[Image, list[Footprint]]] = []
        self.refs: dict[int, str] = {}
        self.pin_names: dict[int, str] = {}  # each pad that is a pin, by id

    # -- names ------------------------------------------------------------------

    def layer(self, layer: str) -> str:
        return quote(self.names[layer])

    def kind(self, layer: str) -> str:
        """A copper layer's type: power where the board says power, else
        signal."""
        for entry in self.board.layers:
            if entry.name == layer:
                return "power" if entry.kind == "power" else "signal"
        return "signal"

    def span(self, pad: Pad, back: bool = False) -> str:
        """Which copper a pad is on, as its padstack's name says: A for
        every layer -- unless its part is on the back, whose pads pcbnew
        names layer by layer -- else T and B for the outer ones and the
        inner ones by number."""
        raw = pad.node.find("layers").values() if pad.node is not None else []
        if "*.Cu" in raw and not back:
            return "A"
        out = ""
        for layer in self.pad_layers(pad, back):
            if layer == "F.Cu":
                out += "T"
            elif layer == "B.Cu":
                out += "B"
            else:
                out += layer[2:-3]
        return out

    # -- padstacks --------------------------------------------------------------

    def pad_padstack(self, pad: Pad, fp: Footprint, back: bool) -> Padstack:
        """The padstack of a pad as the footprint is on the front: a pad of
        a part on the back is turned over with it."""
        w, h = pad.size
        on = self.pad_layers(pad, back)
        ox, oy = pad.drill_offset
        if back:
            oy = -oy
        offset = f"[{_um(ox):.6f},{-_um(oy):.6f}]" if (ox or oy) else ""
        span = self.span(pad, back)
        shape = pad.shape
        dx, dy = _um(ox), -_um(oy)
        if (
            shape == "roundrect"
            and pad.roundrect_ratio <= 0
            and not (pad.chamfer_ratio > 0 and pad.chamfer_corners)
        ):
            shape = "rect"
        if shape == "circle":
            name = f"Round[{span}]{offset}Pad_{_um(w):.6f}_um"
            extra = f" {g(dx)} {g(dy)}" if (ox or oy) else ""
            shapes = [f"(shape (circle {self.layer(layer)} {g(_um(w))}{extra}))" for layer in on]
        elif shape == "oval":
            name = f"Oval[{span}]{offset}Pad_{_um(w):.6f}x{_um(h):.6f}_um"
            if w >= h:
                half = _um((w - h) / 2)
                ends, width = [(-half + dx, dy), (half + dx, dy)], h
            else:
                half = _um((h - w) / 2)
                ends, width = [(dx, -half + dy), (dx, half + dy)], w
            shapes = [
                f"(shape {point_list(f'path {self.layer(layer)} {g(_um(width))}', ends)})"
                for layer in on
            ]
        elif shape == "rect":
            name = f"Rect[{span}]{offset}Pad_{_um(w):.6f}x{_um(h):.6f}_um"
            x1, y1, x2, y2 = -_um(w) / 2 + dx, -_um(h) / 2 + dy, _um(w) / 2 + dx, _um(h) / 2 + dy
            shapes = [
                f"(shape (rect {self.layer(layer)} {g(x1)} {g(y1)} {g(x2)} {g(y2)}))"
                for layer in on
            ]
        elif shape == "roundrect":
            radius = pad.roundrect_ratio * min(w, h)
            outer = _round(radius * ROUNDING)
            chamfer = pad.chamfer_ratio if pad.chamfer_corners else 0.0
            corners = set(pad.chamfer_corners)
            if back:  # turned over, its top corners are its bottom ones
                corners = {
                    c.replace("top", "x").replace("bottom", "top").replace("x", "bottom")
                    for c in corners
                }
            bits = sum(CORNER_BITS[c] for c in corners) if chamfer else 0
            name = (
                f"RoundRect[{span}]{offset}Pad_{_um(w):.6f}x{_um(h):.6f}_{_um(outer):.6f}_um_"
                f"{chamfer:.6f}_{bits}"
            )
            pts = rounded_rect(w, h, radius, outer, chamfer, corners, self.error)
            ring = [(_um(x) + dx, -_um(y) + dy) for x, y in pts]
            shapes = [
                f"(shape {point_list(f'polygon {self.layer(layer)} 0', ring + ring[:1])})"
                for layer in on
            ]
        elif shape == "trapezoid":
            ddx, ddy = pad.rect_delta
            if back:
                ddy = -ddy
            sign = lambda v: ("m" if v < 0 else "p") + f"{abs(_um(v)):.6f}"  # noqa: E731
            name = f"Trapz[{span}]{offset}Pad_{_um(w):.6f}x{_um(h):.6f}_{sign(ddx)}x{sign(ddy)}_um"
            corners = [
                (-w / 2 - ddy / 2, h / 2 + ddx / 2),
                (-w / 2 + ddy / 2, -h / 2 - ddx / 2),
                (w / 2 - ddy / 2, -h / 2 + ddx / 2),
                (w / 2 + ddy / 2, h / 2 - ddx / 2),
            ]
            ring = [(_um(x) + dx, -_um(y) + dy) for x, y in corners]
            shapes = [
                f"(shape {point_list(f'polygon {self.layer(layer)} 0', ring)})" for layer in on
            ]
        else:
            rings = custom_outline(pad, self.error)
            ring = rings[0] if rings else []
            if back:
                ring = [(x, -y) for x, y in ring][::-1]
            xs = [p[0] for p in ring] or [0]
            ys = [p[1] for p in ring] or [0]
            closed = [(_um(x), -_um(y)) for x, y in ring]
            closed = closed + closed[:1]
            digest = hashlib.md5(repr(ring).encode()).hexdigest().upper()
            name = (
                f"Cust[{span}]{offset}Pad_{_um(w):.6f}x{_um(h):.6f}_{_um(max(xs) - min(xs)):.6f}x"
                f"{_um(max(ys) - min(ys)):.6f}_{len(closed)}_um_{digest}"
            )
            shapes = [
                f"(shape {point_list(f'polygon {self.layer(layer)} 0', closed)})" for layer in on
            ]
        found = Padstack(name, shapes)
        key = found.body() + "\0" + name
        return self.padstacks.setdefault(key, found)

    def pad_layers(self, pad: Pad, back: bool) -> list[str]:
        """The copper a pad is on, as on the front: turned over with its part."""
        on = [layer for layer in self.copper if layer in pad.layers]
        if back:
            on = [self.copper[len(self.copper) - 1 - self.copper.index(layer)] for layer in on]
            on.sort(key=self.copper.index)
        return on

    # -- images -----------------------------------------------------------------

    def local(self, fp: Footprint, p: tuple[float, float], back: bool) -> tuple[float, float]:
        """A board point in the part's own frame, as Specctra has it: in
        micrometres, y up, a part on the back seen from the front."""
        x, y = geometry.rotate(p[0] - fp.position[0], p[1] - fp.position[1], -fp.angle)
        return _um(x), _um(y) if back else -_um(y)

    def image(self, fp: Footprint) -> Image:
        back = fp.layer == "B.Cu"
        lines: list[str] = []
        lines += self.outlines(fp, back)
        numbers: dict[str, int] = {}
        keepouts: list[str] = []
        for pad in fp.pads:
            number = "" if pad.kind == "np_thru_hole" else pad.number
            if not any(layer in pad.layers for layer in self.copper):
                continue
            if pad.shape == "circle" and pad.drill and min(pad.drill) >= pad.size[0]:
                # A round hole with no copper round it, plated or not: a
                # keep-out of the hole and the board's hole clearance on
                # every copper layer.
                x, y = self.local(fp, pad.position, back)
                at = f" {g(x)} {g(y)}" if (x or y) else ""
                size = _um(pad.drill[0] + 2 * self.settings.nm("min_hole_clearance"))
                for layer in self.copper:
                    keepouts.append(f'(keepout "" (circle {self.layer(layer)} {g(size)}{at}))')
                continue
            stack = self.pad_padstack(pad, fp, back)
            x, y = self.local(fp, pad.position, back)
            # A second pin of a number is "1@1"; a pin with no number is
            # "@1" from the first. Pads that are no pin are not counted.
            seen = numbers.get(number, 0)
            numbers[number] = seen + 1
            pin = f"@{seen + 1}" if not number else (number if not seen else f"{number}@{seen}")
            self.pin_names[id(pad)] = pin
            turn = (pad.angle - fp.angle) % 360
            if back:
                turn = (-turn) % 360
            rotate = f" (rotate {g(turn)})" if round(turn, 9) % 360 else ""
            lines.append(f"(pin {quote(stack.name)}{rotate} {quote(pin)} {g(x)} {g(y)})")
        lines += keepouts
        # A part whose image is the same as one already made shares it,
        # whatever its library name: every logo of nothing but filled
        # shapes ends up in one empty image (CM5_MINIMA's).
        for im in self.images:
            if im.lines == lines:
                return im
        base = fp.library_id
        candidate = Image(base, lines)
        same = [im for im in self.images if im.name.split("::")[0] == base]
        if same:
            candidate.name = f"{base}::{len(same)}"
        self.images.append(candidate)
        return candidate

    def outlines(self, fp: Footprint, back: bool) -> list[str]:
        """The part's courtyard as a polygon, then its other drawings as
        lines with their width -- all but filled polygons and text, which
        Freerouting only shows."""
        out: list[str] = []
        side = "B.CrtYd" if back else "F.CrtYd"
        yard = []
        nodes = list(fp.node.lists()) if fp.node is not None else []
        for node in nodes:
            if node.head in OUTLINE_SHAPES and _layer(node) == side:
                pts, closed = edge_points(node, fp, self.error)
                if pts:
                    yard.append((None, pts, closed))
        if yard:
            from .drc import outline  # noqa: PLC0415

            for chain, closed in outline._chains(yard):
                if not closed:
                    continue
                ring = [p for _, piece in chain for p in piece]
                ring = [p for i, p in enumerate(ring) if p != ring[i - 1]]
                if len(ring) < 3:
                    continue
                ring = inset(_started(ring, True), self.error)
                pts = [self.local(fp, p, back) for p in ring]
                out.append(f"(outline {point_list('polygon signal 0', pts)})")
        for node in nodes:
            if (
                node.head not in OUTLINE_SHAPES
                or node.head == "fp_poly"
                or _layer(node) in ("F.CrtYd", "B.CrtYd")
            ):
                continue
            width = g(_um(stroke_width(node)))
            pts = self.drawing_points(node, fp)
            if pts:
                local = [self.local(fp, p, back) for p in pts]
                out.append(f"(outline {point_list(f'path signal {width}', local)})")
        return out

    def drawing_points(self, node: List, fp: Footprint) -> list[tuple[float, float]]:
        """A drawing's line through the board, as pcbnew lists it for an
        image: a line's ends, a rectangle's corners round to the first, a
        circle's corners on the circle from its rightmost point."""
        from .board import nm  # noqa: PLC0415

        def at(name: str) -> tuple[int, int]:
            found = node.find(name)
            return nm(found.atom(1)), nm(found.atom(2))

        head = node.head[3:]
        if head == "line":
            pts = [at("start"), at("end")]
        elif head == "rect":
            (x1, y1), (x2, y2) = at("start"), at("end")
            pts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
        elif head == "circle":
            (cx, cy), (ex, ey) = at("center"), at("end")
            r = math.hypot(ex - cx, ey - cy)
            n = max(1, math.ceil(math.pi / math.acos(1 - self.error / r))) if r > self.error else 8
            # round the circle from its rightmost point on the board
            (bx, by) = geometry.place([(cx, cy)], *fp.position, fp.angle)[0]
            pts = [
                (bx + r * math.cos(2 * math.pi * k / n), by + r * math.sin(2 * math.pi * k / n))
                for k in range(n)
            ]
            return pts + pts[:1]
        elif head == "arc":
            start, mid, end = at("start"), at("mid"), at("end")
            arc = arc_of(start, mid, end)
            if arc is None:
                pts = [start, end]
            else:
                center, r, a0, sweep = arc
                n = max(1, segments(r, sweep, self.error))
                pts = [
                    (
                        center[0] + r * math.cos(math.radians(a0 + sweep * k / n)),
                        center[1] + r * math.sin(math.radians(a0 + sweep * k / n)),
                    )
                    for k in range(n + 1)
                ]
        else:
            return []
        return geometry.place(pts, *fp.position, fp.angle)

    # -- the file ---------------------------------------------------------------

    def text(self, path: str, version: str) -> str:
        board = self.board
        for fp in board.footprints:
            im = self.image(fp)
            for entry in self.components:
                if entry[0] is im:
                    entry[1].append(fp)
                    break
            else:
                self.components.append((im, [fp]))
        out = [f"(pcb {quote(path)}\n"]
        out.append(
            "  (parser\n"
            '    (string_quote ")\n'
            "    (space_in_quoted_tokens on)\n"
            '    (host_cad "KiCad\'s Pcbnew")\n'
            f'    (host_version "{version}")\n'
            "  )\n"
            "  (resolution um 10)\n"
            "  (unit um)\n"
        )
        out.append(self.structure())
        out.append(self.placement())
        out.append(self.library())
        out.append(self.network())
        out.append(self.wiring())
        out.append(")\n")
        return "".join(out)

    def structure(self) -> str:
        out = ["  (structure\n"]
        for k, layer in enumerate(self.copper):
            out.append(
                f"    (layer {self.layer(layer)}\n"
                f"      (type {self.kind(layer)})\n"
                "      (property\n"
                f"        (index {k})\n"
                "      )\n"
                "    )\n"
            )
        outer, holes = self.outline()
        if outer:
            out.append("    (boundary\n")
            for ring in outer:
                pts = [_dsn(p) for p in ring + ring[:1]]
                out.append("      " + point_list("path pcb 0", pts) + "\n")
            out.append("    )\n")
        for ring in holes:
            pts = [_dsn(p) for p in ring + ring[:1]]
            out.append('    (keepout "" ' + point_list("polygon signal 0", pts) + ")\n")
        for zone in self.board.zones:
            if zone.rule_area or not zone.net:
                continue
            for layer in self.copper:
                if layer not in zone.layers:
                    continue
                for ring in zone.outlines:
                    pts = [_dsn(p) for p in ring] + [_dsn(ring[0])]
                    head = f"polygon {self.layer(layer)} 0"
                    out.append(f"    (plane {quote(zone.net)} {point_list(head, pts)})\n")
        for zone in self.board.zones:
            if not zone.rule_area:
                continue
            for layer in self.copper:
                if layer not in zone.layers:
                    continue
                for ring in zone.outlines:
                    pts = [_dsn(p) for p in ring] + [_dsn(ring[0])]
                    head = f"polygon {self.layer(layer)} 0"
                    out.append(f'    (keepout "" {point_list(head, pts)})\n')
        out.append(word_list("(via", [quote(v) for v in self.via_names()], 4, 90, 3) + ")\n")
        default = self.settings.netclasses.of("")
        clearance = _um(default.nm("clearance"))
        out.append(
            "    (rule\n"
            f"      (width {g(_um(default.nm('track_width')))})\n"
            f"      (clearance {g(clearance)})\n"
            f"      (clearance {g(clearance / 4)} (type smd_smd))\n"
            "    )\n"
            "  )\n"
        )
        return "".join(out)

    def outline(self) -> tuple[list[Ring], list[Ring]]:
        """The board's outlines and its cut-outs, each a ring of board points
        the way pcbnew lists them: outlines clockwise as the board is seen,
        cut-outs the other way, each starting at the corner after its
        topmost one."""
        from .drc import outline  # noqa: PLC0415

        found = []
        for node in self.board.edge_shapes():
            pts, closed = edge_points(node, None, self.error)
            if pts:
                found.append((None, pts, closed))
        for fp in self.board.footprints:
            for node in fp.node.lists() if fp.node is not None else []:
                if node.head in ("fp_line", "fp_arc", "fp_circle", "fp_rect", "fp_poly"):
                    layer = node.find("layer")
                    if layer is not None and layer.value(1) == "Edge.Cuts":
                        pts, closed = edge_points(node, fp, self.error)
                        if pts:
                            found.append((None, pts, closed))
        rings = []
        for chain, closed in outline._chains(found):
            if not closed:
                continue
            pts = [p for _, piece in chain for p in piece]
            pts = [p for i, p in enumerate(pts) if p != pts[i - 1]]
            if len(pts) >= 3:
                rings.append(pts)
        outer, holes = [], []
        for k, ring in enumerate(rings):
            depth = sum(1 for j, other in enumerate(rings) if j != k and _inside(ring[0], other))
            (holes if depth % 2 else outer).append(ring)
        return [_started(r, True) for r in outer], [_started(r, False) for r in holes]

    def via_name(self, diameter: int, drill: int) -> str:
        top = len(self.copper) - 1
        return f"Via[0-{top}]_{g(_um(diameter))}:{g(_um(drill))}_um"

    def class_name(self, net: str) -> str:
        """The class a net is in, by pcbnew's name for it: the classes that
        claim the net, highest priority first, and ",Default" after them
        when none of them sets one of the sizes Default would then give --
        the line widths of the schematic among them, but not the gap to a
        pair's vias or a colour (measured, a class short of one field each)."""
        nc = self.settings.netclasses.of(net)
        matched = [c for c in nc.name.split(",") if c and c != "Default"]
        if not matched:
            return "Default"
        classes = self.settings.netclasses.classes
        undefined = any(
            not any(isinstance(classes.get(c, {}).get(key), (int, float)) for c in matched)
            for key in COMPOSITE_FIELDS
        )
        return ",".join(matched + (["Default"] if undefined else []))

    def classes(self) -> list[tuple[str, list[str]]]:
        """Default and every class with nets in it, each with its nets in
        name order. pcbnew lists the classes after Default in the order a
        hash table hands them back; here they follow the project's own order
        of its classes, which Freerouting does not mind."""
        groups: dict[str, list[str]] = {}
        for net in self.nets():
            groups.setdefault(self.class_name(net), []).append(net)
        declared = list(self.settings.netclasses.classes)
        rest = sorted(
            (c for c in groups if c != "Default"),
            key=lambda c: (
                declared.index(c.split(",")[0]) if c.split(",")[0] in declared else len(declared),
                c,
            ),
        )
        return [("Default", sorted(groups.get("Default", [])))] + [
            (c, sorted(groups[c])) for c in rest
        ]

    def via_names(self) -> list[str]:
        """The vias Freerouting may use: each class's, Default's first, then
        any other size already on the board."""
        names: list[str] = []
        for _name, members in self.classes():
            nc = self.settings.netclasses.of(members[0] if members else "")
            via = self.via_name(nc.nm("via_diameter"), nc.nm("via_drill"))
            if via not in names:
                names.append(via)
        for via in self.board.vias:
            name = self.via_name(via.diameter, via.drill)
            if name not in names:
                names.append(name)
        return names

    def placement(self) -> str:
        out = ["  (placement\n"]
        used: dict[str, int] = {}
        for im, fps in self.components:
            out.append(f"    (component {quote(im.name)}\n")
            for fp in fps:
                ref = fp.reference or "REF**"
                if ref in used:
                    used[ref] += 1
                    ref = f"{ref}_{used[ref]}"
                else:
                    used[ref] = 0
                self.refs[id(fp)] = ref
                x, y = fp.position
                if fp.layer == "B.Cu":
                    side, turn = "back", (fp.angle + 180) % 360
                else:
                    side, turn = "front", fp.angle
                part = f" (PN {quote(fp.value)})" if fp.value else ""
                out.append(
                    f"      (place {quote(ref)} {exact(x)} {exact(-y)} {side} {g(turn)}{part})\n"
                )
            out.append("    )\n")
        out.append("  )\n")
        return "".join(out)

    def library(self) -> str:
        out = ["  (library\n"]
        for im in self.images:
            out.append(f"    (image {quote(im.name)}\n" + im.body() + "    )\n")
        for stack in sorted(self.padstacks.values(), key=lambda s: (s.body(), s.name)):
            out.append(stack.text())
        for name in self.via_names():
            diameter = float(name.split("_")[1].split(":")[0])
            shapes = [
                f"(shape (circle {self.layer(layer)} {g(diameter)}))" for layer in self.copper
            ]
            out.append(Padstack(name, shapes).text())
        out.append("  )\n")
        return "".join(out)

    def nets(self) -> list[str]:
        """The board's nets in the order they first appear in the file."""
        order: dict[str, None] = {}
        for name in self.board._net_table().values():
            order.setdefault(name, None)
        for node in self.board.root.lists():
            if node.head == "footprint":
                for pad in node.find_all("pad"):
                    order.setdefault(self.board.net_of(pad), None)
            elif node.head in ("segment", "arc", "via", "zone"):
                order.setdefault(self.board.net_of(node), None)
        order.pop("", None)
        return list(order)

    def network(self) -> str:
        out = ["  (network\n"]
        pins: dict[str, list[str]] = {}
        for fp in self.board.footprints:
            for pad in fp.pads:
                pin = self.pin_names.get(id(pad))
                if pin is None or not pad.net:
                    continue
                pins.setdefault(pad.net, []).append(f"{quote(self.refs[id(fp)])}-{quote(pin)}")
        nets = self.nets()
        for net in nets:
            out.append(f"    (net {quote(net)}\n")
            out.append(word_list("(pins", pins.get(net, []), 6, 80) + ")\n")
            out.append("    )\n")
        for name, members in self.classes():
            nc = self.settings.netclasses.of(members[0] if members else "")
            label = "kicad_default" if name == "Default" else name
            out.append(
                word_list("(class", [quote(label)] + [quote(n) for n in members], 4, 72) + "\n"
            )
            via = self.via_name(nc.nm("via_diameter"), nc.nm("via_drill"))
            out.append(
                "      (circuit\n"
                f"        (use_via {quote(via)})\n"
                "      )\n"
                "      (rule\n"
                f"        (width {g(_um(nc.nm('track_width')))})\n"
                f"        (clearance {g(_um(nc.nm('clearance')))})\n"
                "      )\n"
                "    )\n"
            )
        out.append("  )\n")
        return "".join(out)

    def wiring(self) -> str:
        out = ["  (wiring\n"]
        chain: list | None = None
        wires: list[tuple] = []
        # A track runs on from the one before it in the file when it starts
        # where that one ends, on its layer, its net, at its width. An arc
        # goes as the straight line between its ends (CM5_MINIMA's and
        # One-Air-Max's tuned pairs).
        for track in self.board.tracks:
            if (
                chain is not None
                and chain[0] == (track.layer, track.width, track.net)
                and chain[1][-1] == track.start
            ):
                chain[1].append(track.end)
                continue
            chain = [(track.layer, track.width, track.net), [track.start, track.end]]
            wires.append(chain)
        for (layer, width, net), pts in wires:
            head = f"path {self.layer(layer)} {g(_um(width))}"
            out.append(
                f"    (wire {point_list(head, [_dsn(p) for p in pts])}"
                f"(net {quote(net)})(type route))\n"
            )
        for via in self.board.vias:
            x, y = _dsn(via.position)
            name = self.via_name(via.diameter, via.drill)
            out.append(
                f"    (via {quote(name)}  {g(x)} {g(y)} (net {quote(via.net)})(type route))\n"
            )
        out.append("  )\n")
        return "".join(out)


def write(board: Board, settings: Settings, path: str, version: str = "10.0") -> str:
    """The board as pcbnew exports it for Freerouting."""
    return Dsn(board, settings).text(path, version)


# -- the session Freerouting hands back --------------------------------------------

UNITS = {
    "um": 1_000.0,
    "mm": 1_000_000.0,
    "cm": 10_000_000.0,
    "mil": 25_400.0,
    "inch": 25_400_000.0,
}


@dataclass
class Session:
    """What a Specctra session says, in the board's terms: nanometres, y
    down, layers by the names the DSN gave them."""

    wires: list[tuple[str, str, int, list[tuple[int, int]]]] = field(default_factory=list)
    # (net, padstack, position, diameter, drill)
    vias: list[tuple[str, str, tuple[int, int], int, int]] = field(default_factory=list)
    parts: list[tuple[str, tuple[int, int], str, float]] = field(default_factory=list)


class SessionError(ValueError):
    """A session file that cannot be read as one."""


def _tokens(text: str):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c in "()":
            yield c
            i += 1
        elif c == '"':
            j = text.find('"', i + 1)
            if j < 0:
                raise SessionError("a quoted word is never closed")
            yield ("word", text[i + 1 : j])
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n()"':
                j += 1
            yield ("word", text[i:j])
            i = j


def _tree(text: str) -> list:
    stack: list[list] = [[]]
    for t in _tokens(text):
        if t == "(":
            stack.append([])
        elif t == ")":
            if len(stack) < 2:
                raise SessionError("a closing bracket with nothing open")
            done = stack.pop()
            stack[-1].append(done)
        else:
            stack[-1].append(t[1])
    if len(stack) != 1 or not stack[0] or not isinstance(stack[0][0], list):
        raise SessionError("brackets do not balance")
    return stack[0][0]


def _all(node: list, head: str) -> list[list]:
    return [x for x in node if isinstance(x, list) and x and x[0] == head]


def _first(node: list | None, head: str) -> list | None:
    found = _all(node, head) if node is not None else []
    return found[0] if found else None


def _read_session(text: str) -> Session:
    """A Specctra session as Freerouting writes one: the wires and vias of
    each net, and where each part is."""
    root = _tree(text)
    if not root or root[0] != "session":
        raise SessionError("not a Specctra session: it does not start with (session")
    routes = _first(root, "routes")
    placement = _first(root, "placement")
    if routes is None or _first(routes, "network_out") is None:
        raise SessionError("session has no routing result")

    def scale(node: list | None) -> float:
        res = _first(node, "resolution")
        if res is None or len(res) < 3:
            return 100.0  # um 10, Freerouting's own
        divisor = float(res[2])
        if res[1].lower() not in UNITS or not math.isfinite(divisor) or divisor <= 0:
            raise SessionError("session resolution has an unsupported unit or invalid scale")
        return UNITS[res[1].lower()] / divisor

    def to_nm(value: str, unit: float) -> int:
        scaled = float(value) * unit
        if not math.isfinite(scaled):
            raise SessionError("session coordinate is not finite")
        return _round(scaled)

    out = Session()
    unit = scale(routes)
    sizes: dict[str, int] = {}
    for stack in _all(_first(routes, "library_out") or [], "padstack"):
        for shape in _all(stack, "shape"):
            circle = _first(shape, "circle")
            if circle is not None and len(circle) >= 3:
                sizes[stack[1]] = to_nm(circle[2], unit)
                break
    for net in _all(_first(routes, "network_out") or [], "net"):
        name = net[1]
        for wire in _all(net, "wire"):
            path = _first(wire, "path")
            if path is None or len(path) < 7:
                raise SessionError("session wire is truncated")
            if any(isinstance(v, list) for v in path[3:]) or (len(path) - 3) % 2:
                raise SessionError("session wire coordinates are incomplete")
            values = [to_nm(v, unit) for v in path[3:] if not isinstance(v, list)]
            pts = [(values[k], -values[k + 1]) for k in range(0, len(values) - 1, 2)]
            out.wires.append((name, path[1], to_nm(path[2], unit), pts))
        for via in _all(net, "via"):
            words = [v for v in via[1:] if not isinstance(v, list)]
            if len(words) < 3:
                raise SessionError("session via is truncated")
            stack = words[0]
            found = re.match(r"Via\[\d+-\d+\]_([\d.]+):([\d.]+)_um$", stack)
            drill = _round(float(found.group(2)) * 1000) if found else 0
            diameter = sizes.get(stack) or (_round(float(found.group(1)) * 1000) if found else 0)
            at = (to_nm(words[1], unit), -to_nm(words[2], unit))
            out.vias.append((name, stack, at, diameter, drill))
    unit = scale(placement)
    for component in _all(placement or [], "component"):
        for place in _all(component, "place"):
            words = [v for v in place[1:] if not isinstance(v, list)]
            if len(words) >= 5:
                at = (to_nm(words[1], unit), -to_nm(words[2], unit))
                out.parts.append((words[0], at, words[3], float(words[4])))
    return out


def read_session(text: str) -> Session:
    """Reject malformed numeric/structural results before they can replace copper."""
    try:
        return _read_session(text)
    except SessionError:
        raise
    except (ValueError, TypeError, IndexError, OverflowError, ZeroDivisionError) as exc:
        raise SessionError("session contains malformed numeric or structural data") from exc


__all__ = ["List", "Session", "SessionError", "read_session", "write"]
