"""Drawing on a sheet that already has a drawing.

`sch edit` adds wires, labels, power symbols and no-connect flags to sheets
a person drew. Two things must hold for each. It joins what it is meant to
and nothing else: that is checked afterwards, by reading the design back.
And it does not land on what is already there, which is this module's job:
every symbol's body and shown text, every wire, label, flag and sheet takes
up room, and a new wire and its label go where nothing is -- a wire that
touches no pin, wire or label but the one it starts from, a label that sits
on no wire but its own.

Coordinates are integer nanometres with y down, as `fileformat/schematic.py`
has them. Text is measured by the same estimate `sch create` uses, on the
wide side.
"""

from __future__ import annotations

import re
import uuid as uuidlib
from dataclasses import dataclass, field

from ..fileformat.circuit import _on_segment
from ..fileformat.schematic import (
    LibPin,
    LibSymbol,
    Schematic,
    Symbol,
    _hidden,
    _turn,
    _unit_style,
)
from ..fileformat.sexpr import List, copy, number, quote, symbol

NM = 1_000_000
GRID = 1_270_000
FONT = 1_270_000
CHAR = 1_100_000
STUBS = (2, 3, 4, 5, 6)  # grid steps a new wire may run out from a pin

Box = tuple[int, int, int, int]
Point = tuple[int, int]
POWER_NAME = re.compile(r"^(A|D|P|S)?GND\w*$|^[+-].*$|^V(CC|DD|SS|EE)\w*$", re.IGNORECASE)


def _meets(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _text_width(text: str) -> int:
    return len(text) * CHAR + GRID // 2


def _along(anchor: Point, direction: Point, length: int, half: int = FONT) -> Box:
    """The box of something `length` long laid from `anchor` along `direction`."""
    (x, y), (dx, dy) = anchor, direction
    if dx:
        x2 = x + dx * length
        return (min(x, x2), y - half, max(x, x2), y + half)
    y2 = y + dy * length
    return (x - half, min(y, y2), x + half, max(y, y2))


def _mm(value: int) -> str:
    return number(value / NM, 4)


def _xy(point: Point) -> list[str]:
    return [_mm(point[0]), _mm(point[1])]


def _nm(atom) -> int:
    try:
        return round(float(atom) * NM)
    except (TypeError, ValueError):
        return 0


# Label angles by the way they point, sheet frame: y down.
ANGLE = {(1, 0): 0, (0, -1): 90, (-1, 0): 180, (0, 1): 270}


@dataclass
class Placed:
    """A pin as a sheet has it: where it connects, and which way is out."""

    symbol: Symbol
    pin: LibPin
    at: Point
    out: Point


# Paper sizes KiCad knows, in millimetres, landscape.
PAPERS = {
    "A5": (210, 148), "A4": (297, 210), "A3": (420, 297), "A2": (594, 420),
    "A1": (841, 594), "A0": (1189, 841), "A": (279.4, 215.9), "B": (431.8, 279.4),
    "C": (558.8, 431.8), "D": (863.6, 558.8), "E": (1117.6, 863.6),
    "USLetter": (279.4, 215.9), "USLegal": (355.6, 215.9), "USLedger": (431.8, 279.4),
}  # fmt: skip
MARGIN = 15 * NM  # the page's frame, and a little room inside it
TITLE_BLOCK = (110 * NM, 40 * NM)  # its size in the bottom right-hand corner
CELL = 10 * NM  # the side of a cell of the index of what is where


def _cells(box: Box):
    for cx in range(box[0] // CELL, box[2] // CELL + 1):
        for cy in range(box[1] // CELL, box[3] // CELL + 1):
            yield (cx, cy)


@dataclass
class Canvas:
    """One sheet file: what attaches where, and what takes up room -- found
    by where it is, so a search over the sheet stays quick."""

    sch: Schematic
    points: dict[Point, list[str]] = field(default_factory=dict)
    wires: list[tuple[Point, Point]] = field(default_factory=list)
    boxes: list[Box] = field(default_factory=list)
    _box_cells: dict = field(default_factory=dict)
    _point_cells: dict = field(default_factory=dict)
    _wire_cells: dict = field(default_factory=dict)
    area: Box = (0, 0, 0, 0)  # where a new thing may go: inside the frame

    @classmethod
    def of(cls, sch: Schematic) -> Canvas:
        canvas = cls(sch)
        canvas._read()
        return canvas

    # -- what is there ---------------------------------------------------------------------

    def _at(self, point: Point, what: str) -> None:
        if point not in self.points:
            self._point_cells.setdefault((point[0] // CELL, point[1] // CELL), []).append(point)
        self.points.setdefault(point, []).append(what)

    def _room(self, box: Box) -> None:
        self.boxes.append(box)
        for cell in _cells(box):
            self._box_cells.setdefault(cell, []).append(box)

    def _wire(self, a: Point, b: Point) -> None:
        self.wires.append((a, b))
        for cell in _cells(_span(a, b)):
            self._wire_cells.setdefault(cell, []).append((a, b))

    def _read(self) -> None:
        sch = self.sch
        paper = sch.root.find("paper")
        name = (paper.value(1) or "A4") if paper is not None else "A4"
        if name == "User" and paper is not None:
            width, height = _nm(paper.atom(2)) / NM, _nm(paper.atom(3)) / NM
        else:
            width, height = PAPERS.get(name, PAPERS["A4"])
            if paper is not None and "portrait" in paper.values():
                width, height = height, width
        w, h = round(width * NM), round(height * NM)
        self.area = (MARGIN, MARGIN, w - MARGIN, h - MARGIN)
        self._room((w - MARGIN - TITLE_BLOCK[0], h - MARGIN - TITLE_BLOCK[1], w, h))
        for wire in sch.wires:
            self._wire(wire.start, wire.end)
            self._at(wire.start, wire.kind)
            self._at(wire.end, wire.kind)
            self._room(_pad(_span(wire.start, wire.end), GRID // 4))
        for point in sch.junctions:
            self._at(point, "junction")
            self._room(_pad((point[0], point[1], point[0], point[1]), GRID // 2))
        for point in sch.no_connects:
            self._at(point, "no_connect")
            self._room(_pad((point[0], point[1], point[0], point[1]), GRID // 2))
        for label in sch.labels:
            self._at(label.position, "label")
            self._room(_label_box(label.kind, label.text, label.position, label.angle))
        for sheet in sch.sheets:
            (x, y), (sw, sh) = sheet.position, sheet.size
            self._room((x, y - 2 * FONT, x + sw, y + sh + 2 * FONT))
            for pin in sheet.pins:
                self._at(pin.position, "sheet_pin")
        for entry in sch.bus_entries:
            self._at(entry.position, "bus_entry")
            self._at(entry.end, "bus_entry")
            self._room(_pad(_span(entry.position, entry.end), GRID // 4))
        for text in sch.texts:
            width_ = max((len(line) for line in text.text.split("\n")), default=0) * CHAR
            lines = text.text.count("\n") + 1
            x, y = text.position
            self._room((x, y - FONT, x + width_, y + FONT * 2 * lines))
        for sym in sch.symbols:
            lib = sch.library.get(sym.library_name)
            if lib is None:
                continue
            for placed in self.pins(sym, lib):
                self._at(placed.at, "pin")
            body = _body(sym, lib)
            if body is not None:
                self._room(body)
            for box in _field_boxes(sym):
                self._room(box)

    def pins(self, sym: Symbol, lib: LibSymbol) -> list[Placed]:
        out = []
        for pin in lib.pins_of(sym.unit, sym.body_style):
            at = sym.transform(pin.position)
            dx, dy = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[(pin.angle + 180) % 360]
            tip = sym.transform((pin.position[0] + dx * GRID, pin.position[1] + dy * GRID))
            out.append(Placed(sym, pin, at, ((tip[0] - at[0]) // GRID, (tip[1] - at[1]) // GRID)))
        return out

    def _wires_near(self, box: Box) -> set[tuple[Point, Point]]:
        found = set()
        for cell in _cells(box):
            found.update(self._wire_cells.get(cell, ()))
        return found

    def _points_near(self, box: Box) -> set[Point]:
        found = set()
        for cell in _cells(box):
            found.update(self._point_cells.get(cell, ()))
        return found

    def touching(self, point: Point, besides: str | None = None) -> list[str]:
        """What attaches at a point -- and wires passing through it, which a
        label there would join."""
        found = list(self.points.get(point, []))
        if besides in found:
            found.remove(besides)
        for a, b in self._wires_near((point[0], point[1], point[0], point[1])):
            if point not in (a, b) and _on_segment(point, a, b):
                found.append("wire_through")
        return found

    def clear(self, start: Point, end: Point) -> bool:
        """A new wire from `start` to `end` touches nothing but at `start`."""
        span = _span(start, end)
        for point in self._points_near(span):
            if point != start and self.points.get(point) and _on_segment(point, start, end):
                return False
        return all(not _crosses((start, end), wire) for wire in self._wires_near(span))

    def _boxes_near(self, box: Box) -> list[Box]:
        seen, found = set(), []
        for cell in _cells(box):
            for other in self._box_cells.get(cell, ()):
                if id(other) not in seen:
                    seen.add(id(other))
                    found.append(other)
        return found

    def free(self, box: Box) -> bool:
        return not any(_meets(box, other) for other in self._boxes_near(box))

    def overlaps(self, box: Box) -> int:
        return sum(1 for other in self._boxes_near(box) if _meets(box, other))

    def inside(self, box: Box) -> bool:
        a = self.area
        return a[0] <= box[0] and a[1] <= box[1] and box[2] <= a[2] and box[3] <= a[3]

    def forget(self, point: Point, what: str) -> None:
        """Something taken away: a no-connect flag, say, and its room."""
        if what in self.points.get(point, []):
            self.points[point].remove(what)
        if what == "no_connect":
            room = _pad((point[0], point[1], point[0], point[1]), GRID // 2)
            for cell in _cells(room):
                cell_boxes = self._box_cells.get(cell, [])
                if room in cell_boxes:
                    cell_boxes.remove(room)
            if room in self.boxes:
                self.boxes.remove(room)

    def take(self, point: Point, what: str, box: Box | None = None) -> None:
        self._at(point, what)
        if box is not None:
            self._room(box)

    def take_box(self, box: Box) -> None:
        self._room(box)

    def take_wire(self, a: Point, b: Point) -> None:
        self._wire(a, b)
        self._at(a, "wire")
        self._at(b, "wire")
        self._room(_pad(_span(a, b), GRID // 4))


def _span(a: Point, b: Point) -> Box:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))


def _pad(box: Box, by: int) -> Box:
    return (box[0] - by, box[1] - by, box[2] + by, box[3] + by)


def _crosses(new: tuple[Point, Point], old: tuple[Point, Point]) -> bool:
    """Whether two axis-aligned or skew segments share any point but the new
    one's start."""
    (a, b), (c, d) = new, old
    if _on_segment(c, a, b) and c != a or _on_segment(d, a, b) and d != a:
        return True
    if _on_segment(b, c, d) or (a != c and a != d and _on_segment(a, c, d)):
        return True

    def orient(p: Point, q: Point, r: Point) -> int:
        v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return (v > 0) - (v < 0)

    return orient(a, b, c) * orient(a, b, d) < 0 and orient(c, d, a) * orient(c, d, b) < 0


def _label_box(kind: str, text: str, at: Point, angle: float) -> Box:
    direction = {0: (1, 0), 90: (0, -1), 180: (-1, 0), 270: (0, 1)}.get(int(angle) % 360, (1, 0))
    length = _text_width(text) + (2 * GRID if kind != "label" else 0)
    return _along(at, direction, length)


def _body(sym: Symbol, lib: LibSymbol) -> Box | None:
    """The unit's drawing on the sheet, its pins' lines included."""
    xs: list[int] = []
    ys: list[int] = []
    node = lib.node
    if node is None:
        return None
    own = node.value(1) or ""
    for sub in node.find_all("symbol"):
        unit, style = _unit_style(sub.value(1) or "", own)
        if unit not in (0, sym.unit) or style not in (0, sym.body_style):
            continue
        for item in sub.lists():
            points = []
            if item.head == "rectangle":
                points = [item.find("start"), item.find("end")]
            elif item.head in ("polyline", "bezier"):
                points = [p for p in item.find("pts").lists()] if item.find("pts") else []
            elif item.head == "arc":
                points = [item.find(k) for k in ("start", "mid", "end")]
            elif item.head == "circle":
                center, radius = item.find("center"), _nm(item.find("radius").atom(1))
                cx, cy = _nm(center.atom(1)), _nm(center.atom(2))
                xs += [cx - radius, cx + radius]
                ys += [cy - radius, cy + radius]
            for p in points:
                if p is not None:
                    xs.append(_nm(p.atom(1)))
                    ys.append(_nm(p.atom(2)))
    for pin in lib.pins_of(sym.unit, sym.body_style):
        dx, dy = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[pin.angle % 360]
        xs += [pin.position[0], pin.position[0] + dx * pin.length]
        ys += [pin.position[1], pin.position[1] + dy * pin.length]
    if not xs:
        return None
    corners = [sym.transform((x, y)) for x in (min(xs), max(xs)) for y in (min(ys), max(ys))]
    return _span(
        (min(c[0] for c in corners), min(c[1] for c in corners)),
        (max(c[0] for c in corners), max(c[1] for c in corners)),
    )


def _field_boxes(sym: Symbol) -> list[Box]:
    out = []
    for prop in sym.node.find_all("property"):
        value = prop.value(2) or ""
        at = prop.find("at")
        if not value or at is None or _hidden(prop):
            continue
        x, y = _nm(at.atom(1)), _nm(at.atom(2))
        width = _text_width(value)
        effects = prop.find("effects")
        justify = effects.find("justify") if effects is not None else None
        words = justify.values() if justify is not None else []
        if int(float(at.atom(3) or 0)) % 180 == 90:
            out.append((x - FONT, y - width, x + FONT, y + width))
        elif "left" in words:
            out.append((x, y - FONT, x + width, y + FONT))
        elif "right" in words:
            out.append((x - width, y - FONT, x, y + FONT))
        else:
            out.append((x - width // 2, y - FONT, x + width // 2, y + FONT))
    return out


# -- what is drawn -------------------------------------------------------------------------


class Ids:
    """Uuids that follow from what they are for: the same edit gives the
    same file."""

    def __init__(self, seed: str) -> None:
        self.namespace = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "kicad-cli/sch-edit/" + seed)

    def __call__(self, *parts: object) -> str:
        return str(uuidlib.uuid5(self.namespace, "/".join(str(p) for p in parts)))


def _effects(justify: str | None = None) -> List:
    size = number(FONT / NM)
    effects = List.new("effects", List.new("font", List.new("size", size, size)))
    if justify:
        effects.append(List.new("justify", *[symbol(j) for j in justify.split()]))
    return effects


def wire(a: Point, b: Point, uid: str) -> List:
    return List.new(
        "wire",
        List.new("pts", List.new("xy", *_xy(a)), List.new("xy", *_xy(b))),
        List.new("stroke", List.new("width", "0"), List.new("type", symbol("default"))),
        List.new("uuid", quote(uid)),
    )


def no_connect(at: Point, uid: str) -> List:
    return List.new("no_connect", List.new("at", *_xy(at)), List.new("uuid", quote(uid)))


def label(kind: str, text: str, at: Point, out: Point, uid: str, shape: str | None = None) -> List:
    """A label pointing `out` from where it sits: its text runs that way."""
    angle = ANGLE[out]
    justify = "left bottom" if angle in (0, 90) else "right bottom"
    if kind != "label":
        justify = "left" if angle in (0, 90) else "right"
    node = List.new("label" if kind == "label" else kind, quote(text))
    if kind != "label":
        node.append(List.new("shape", symbol(shape or "bidirectional")))
    node.append(List.new("at", *_xy(at), str(angle)))
    node.append(List.new("fields_autoplaced", symbol("yes")))
    node.append(_effects(justify))
    node.append(List.new("uuid", quote(uid)))
    return node


def label_box(kind: str, text: str, at: Point, out: Point) -> Box:
    return _label_box(kind, text, at, ANGLE[out])


def power_box(text: str, at: Point, out: Point) -> Box:
    body = _along(at, out, 3 * GRID, GRID + GRID // 2)
    far = (at[0] + out[0] * 4 * GRID, at[1] + out[1] * 4 * GRID)
    width = _text_width(text)
    words = (far[0] - width // 2, far[1] - FONT, far[0] + width // 2, far[1] + FONT)
    return _span((min(body[0], words[0]), min(body[1], words[1])),
                 (max(body[2], words[2]), max(body[3], words[3])))  # fmt: skip


def power_angle(lib: LibSymbol, out: Point) -> int:
    """The angle that points a power symbol's body along `out`, away from the
    pin it is on: a ground hangs below its pin at 0 degrees, a supply rises
    above it."""
    placed = Symbol(lib.name, None, (0, 0), 0, None, 1, 1, "", {}, True, True, False, False, [])
    box = _body(placed, lib) or (0, 0, 0, 0)
    centre = ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
    for angle in (0, 90, 180, 270):
        x, y = _turn(centre[0], centre[1], angle)
        if x * out[0] + y * out[1] > 0:
            return angle
    return 0


def power_symbol(template: List, lib: LibSymbol, text: str, at: Point, out: Point, ids: Ids,
                 key: tuple, references: dict[str, str], project: str) -> List:  # fmt: skip
    """A copy of a power symbol already on the design, of the same net, at
    `at` with its body pointing `out`. `references` gives its reference in
    each placement of the sheet."""
    node = copy(template)
    angle = power_angle(lib, out)
    node.replace(node.find("at"), List.new("at", *_xy(at), str(angle)))
    mirror = node.find("mirror")
    if mirror is not None:
        node.remove(mirror)
    node.replace(node.find("uuid"), List.new("uuid", quote(ids(*key, "power"))))
    for prop in node.find_all("property"):
        name = prop.value(1)
        if name == "Value":
            prop.set(2, quote(text))
        if name == "Reference":
            prop.set(2, quote(next(iter(references.values()))))
        place = prop.find("at")
        if place is not None:
            offset = 2 * GRID if name == "Value" else 0
            spot = (at[0] + out[0] * (offset + 2 * GRID), at[1] + out[1] * (offset + 2 * GRID))
            prop.replace(place, List.new("at", *_xy(spot), "0"))
    for pin in node.find_all("pin"):
        pin.replace(pin.find("uuid"), List.new("uuid", quote(ids(*key, "pin", pin.value(1)))))
    instances = node.find("instances")
    if instances is not None:
        node.remove(instances)
    paths = [
        List.new("path", quote(path), List.new("reference", quote(ref)), List.new("unit", "1"))
        for path, ref in references.items()
    ]
    node.append(List.new("instances", List.new("project", quote(project), *paths)))
    return node
