"""``sch create``, in this process: a schematic a person can read, from a circuit.

The input is the JSON description `sch create` has always taken: parts with
their symbols, and nets naming the pins they join. The output is a
`.kicad_sch` and its netlist -- both deliverables, the drawing for the
engineer who signs it off and the netlist for the board.

How it is drawn, and why:

- Every pin gets a short stub. A signal net is a label at the stub's end, a
  power net a power symbol -- ground pointing down, a supply up -- and an
  unused pin a no-connect flag. Neighbouring pins on one supply are joined
  by a wire and share one symbol. No wire runs between parts, so no wire
  crosses another or a symbol, and a net is read by its name wherever it
  appears: how engineers draw anything bigger than a few parts.
- A supply that no pin drives -- one that comes in on a connector -- gets a
  PWR_FLAG, drawn apart with its own power symbol: that is what tells ERC
  the net is powered.
- Parts are grouped. Each IC is a group's centre; the small parts sharing a
  signal with it gather beside the pins they meet, and capacitors between a
  supply it uses and ground sit in a row beneath it -- bulk capacitors by
  the regulator that drives the supply, small ones by the part it feeds.
  Groups are then packed onto the sheet.
- Nothing overlaps: every part is placed by a box that holds everything it
  draws, and its reference and value go where its pins leave room.
- The same description gives the same file, byte for byte: every uuid
  follows from the description.

The schematic is then read back through this tool's own netlist
(`kicad_cli/fileformat/netlist.py`) and every net compared with the
description, pin for pin. A schematic that does not say what was asked for
is not delivered.

Text is measured by an estimate of the stroke font's widths, on the wide
side: labels may sit further apart than they need to, not closer.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid as uuidlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import envelope, kicad_env
from ..fileformat import netlist as netlist_file
from ..fileformat.schematic import LibPin, _turn
from ..fileformat.sexpr import List, number, quote, render, symbol
from ..fileformat.symbols import Libraries, Resolved, SymbolError, installed

NM = 1_000_000
GRID = 1_270_000  # KiCad's schematic grid: 50 mil
STUB = 2 * GRID
GAP = 2 * GRID  # between the boxes of neighbouring parts
GROUP_GAP = 6 * GRID  # between groups
FONT = 1_270_000
# The stroke font's advance for an average character is below this; a label
# measured by it is never narrower than it draws.
CHAR = 1_100_000
POWER_NAME = re.compile(
    r"^(A|D|P|S)?GND\w*$|^[+-].*$|^V(CC|DD|SS|EE|BUS|BAT|IN|SYS)\w*$|^\d+V\d*$",
    re.IGNORECASE,
)
PAPERS = [("A4", 297, 210), ("A3", 420, 297), ("A2", 594, 420), ("A1", 841, 594)]
PIN_REF = re.compile(r"\A(?P<ref>[A-Za-z_][A-Za-z0-9_]*)\.(?P<pin>.+)\Z")
FACES = {(1, 0): "right", (-1, 0): "left", (0, -1): "top", (0, 1): "bottom"}

Box = tuple[int, int, int, int]
Point = tuple[int, int]


def mm(value: float) -> float:
    return value / NM


def snap(value: float) -> int:
    return int(round(value / GRID)) * GRID


def _natural(text: str) -> tuple:
    return tuple(int(t) if t.isdigit() else t for t in re.split(r"(\d+)", text))


def _union(*boxes: Box | None) -> Box:
    found = [b for b in boxes if b is not None]
    return (
        min(b[0] for b in found),
        min(b[1] for b in found),
        max(b[2] for b in found),
        max(b[3] for b in found),
    )


def _meets(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _text_width(text: str) -> int:
    return len(text) * CHAR + GRID // 2


# -- what the description asks for -------------------------------------------------------


@dataclass
class Part:
    ref: str
    lib: Resolved
    value: str
    footprint: str


@dataclass
class Spot:
    """Where one pin of one placed unit meets the outside, and what is there."""

    pin: LibPin
    at: Point  # the connection point, relative to the unit's origin
    out: Point  # unit vector away from the body, sheet frame
    net: str | None = None


# What a unit draws besides itself, relative to its origin.
@dataclass
class Wire:
    a: Point
    b: Point


@dataclass
class Label:
    text: str
    at: Point
    angle: int


@dataclass
class Power:
    net: str
    at: Point
    out: Point  # which way its body points


@dataclass
class NoConnect:
    at: Point


@dataclass
class Unit:
    part: Part
    unit: int
    spots: list[Spot]
    body: Box
    drawn: list = field(default_factory=list)
    box: Box = (0, 0, 0, 0)  # everything it draws
    origin: Point = (0, 0)
    ref_at: Point = (0, 0)
    value_at: Point = (0, 0)
    fields_justify: str | None = None


def _resolve(spec: dict[str, Any], libs: Libraries) -> tuple[list[Part], list[dict]]:
    parts, problems = [], []
    for item in spec["parts"]:
        try:
            lib = libs.resolve(str(item["symbol"]))
        except SymbolError as exc:
            problems.append({"ref": item["ref"], "problem": str(exc), "symbol": item["symbol"]})
            continue
        value = item.get("value")
        if value in (None, ""):
            value = lib.symbol.properties.get("Value", "")
        footprint = item.get("footprint") or lib.symbol.properties.get("Footprint", "")
        parts.append(Part(str(item["ref"]), lib, str(value), str(footprint)))
    return parts, problems


def _pins_for(part: Part, wanted: str) -> list[LibPin]:
    """A pin by number; else every pin of that name -- a part's two VCC pins
    both join, as the description language always had them."""
    pins = part.lib.symbol.pins
    found = [p for p in pins if p.number == wanted]
    if found:
        return found[:1]
    for match in (lambda p: p.name == wanted, lambda p: p.name.lower() == wanted.lower()):
        named: dict[str, LibPin] = {}
        for p in pins:
            if p.name and match(p):
                named.setdefault(p.number, p)
        if named:
            return list(named.values())
    return []


def _pin_nets(spec: dict[str, Any], parts: dict[str, Part]) -> tuple[dict, list, list]:
    """Which net each pin is on, by (reference, pin number)."""
    on: dict[tuple[str, str], str] = {}
    joins: list[dict] = []
    problems: list[dict] = []
    for index, item in enumerate(spec["nets"]):
        name = str(item["name"])
        joined = 0
        for entry in item.get("connect") or []:
            where = {"index": index, "net": name}
            match = PIN_REF.match(str(entry))
            if match is None:
                problems.append({**where, "problem": "connection must be 'REF.PIN'", "got": entry})
                continue
            part = parts.get(match["ref"])
            if part is None:
                problems.append({**where, "problem": "no such part", "ref": match["ref"]})
                continue
            pins = _pins_for(part, match["pin"])
            if not pins:
                problems.append(
                    {
                        **where,
                        "problem": "the symbol has no such pin",
                        "pin": f"{part.ref}.{match['pin']}",
                        # The list is the useful half: a pin name is usually a
                        # near miss, and guessing from a bare refusal is work
                        # the tool can do instead.
                        "available": sorted({p.name or p.number for p in part.lib.symbol.pins})[
                            :40
                        ],
                    }
                )
                continue
            for pin in pins:
                key = (part.ref, pin.number)
                if key in on and on[key] != name:
                    problems.append(
                        {
                            **where,
                            "problem": "pin is already on another net",
                            "pin": f"{part.ref}.{pin.number}",
                            "other": on[key],
                        }
                    )
                    continue
                on[key] = name
                joined += 1
        joins.append({"net": name, "connections": joined})
    return on, joins, problems


def _kinds(parts: dict[str, Part], on: dict) -> dict[str, set[str]]:
    """Per net, the electrical types of the pins on it."""
    kinds: dict[str, set[str]] = {}
    for (ref, number_), net in on.items():
        for pin in parts[ref].lib.symbol.pins:
            if pin.number == number_:
                kinds.setdefault(net, set()).add(pin.electrical)
    return kinds


def _power_nets(spec: dict[str, Any], parts: dict[str, Part], on: dict) -> set[str]:
    """Nets drawn with power symbols: named like a supply or ground, or
    joining a pin that takes or gives power."""
    kinds = _kinds(parts, on)
    out = set()
    for item in spec["nets"]:
        name = str(item["name"])
        if POWER_NAME.match(name) or kinds.get(name, set()) & {"power_in", "power_out"}:
            out.add(name)
    return out


def _is_ground(net: str) -> bool:
    return "GND" in net.upper() or net.upper() in ("VSS", "0")


# -- drawing each unit ----------------------------------------------------------------------


def _direction(angle: int) -> Point:
    """A library angle as a unit vector in the library's frame (y up)."""
    return {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[angle % 360]


def _sheet(point: Point) -> Point:
    """A library point in the sheet's frame, the symbol unturned (y down)."""
    return (point[0], -point[1])


def _graphics_box(lib: Resolved, unit: int) -> Box | None:
    """The unit's drawing in the sheet's frame, pins' lines included."""
    xs: list[int] = []
    ys: list[int] = []

    def add(x: float, y: float) -> None:
        sx, sy = _sheet((round(x), round(y)))
        xs.append(sx)
        ys.append(sy)

    def nm(atom: str) -> int:
        return round(float(atom) * NM)

    for sub in lib.node.find_all("symbol"):
        tail = (sub.value(1) or "").rsplit("_", 2)
        try:
            sub_unit, style = int(tail[-2]), int(tail[-1])
        except (ValueError, IndexError):
            continue
        if sub_unit not in (0, unit) or style not in (0, 1):
            continue
        for item in sub.lists():
            head = item.head
            if head == "rectangle":
                for key in ("start", "end"):
                    point = item.find(key)
                    add(nm(point.atom(1)), nm(point.atom(2)))
            elif head in ("polyline", "bezier"):
                for xy in item.find("pts").lists():
                    add(nm(xy.atom(1)), nm(xy.atom(2)))
            elif head == "circle":
                center, radius = item.find("center"), nm(item.find("radius").atom(1))
                cx, cy = nm(center.atom(1)), nm(center.atom(2))
                add(cx - radius, cy - radius)
                add(cx + radius, cy + radius)
            elif head == "arc":
                for key in ("start", "mid", "end"):
                    point = item.find(key)
                    add(nm(point.atom(1)), nm(point.atom(2)))
    for pin in lib.symbol.pins_of(unit, 1):
        add(*pin.position)
        dx, dy = _direction(pin.angle)
        add(pin.position[0] + dx * pin.length, pin.position[1] + dy * pin.length)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _units(part: Part, on: dict[tuple[str, str], str]) -> list[Unit]:
    """Every unit of the part that has a pin, each with its pins' outward faces."""
    out = []
    for unit in range(1, part.lib.symbol.unit_count + 1):
        pins = part.lib.symbol.pins_of(unit, 1)
        if not pins:
            continue
        spots = []
        for pin in pins:
            dx, dy = _direction((pin.angle + 180) % 360)
            spots.append(Spot(pin, _sheet(pin.position), (dx, -dy), on.get((part.ref, pin.number))))
        body = _graphics_box(part.lib, unit) or (-GRID, -GRID, GRID, GRID)
        out.append(Unit(part=part, unit=unit, spots=spots, body=body))
    return out


def _runs(spots: list[Spot], power: set[str]) -> list[list[Spot]]:
    """Pins on one face, next to one another, on one supply: drawn as one."""
    faces: dict[Point, list[Spot]] = {}
    for spot in spots:
        faces.setdefault(spot.out, []).append(spot)
    runs = []
    for out, members in faces.items():
        along = 0 if out[0] == 0 else 1  # the coordinate that varies along the face
        members.sort(key=lambda s: s.at[along])
        current: list[Spot] = []
        for spot in members:
            joinable = spot.net in power and spot.net is not None
            if (
                current
                and joinable
                and current[-1].net == spot.net
                and abs(spot.at[along] - current[-1].at[along]) <= 2 * GRID
            ):
                current.append(spot)
                continue
            if current:
                runs.append(current)
            current = [spot]
        if current:
            runs.append(current)
    return runs


def _plan(unit: Unit, power: set[str]) -> None:
    """What the unit draws, the box of all of it, and where its text goes."""
    drawn: list = []
    boxes = [unit.body]
    for run in _runs(unit.spots, power):
        first = run[0]
        (dx, dy) = first.out
        if first.net is None:
            for spot in run:
                drawn.append(NoConnect(spot.at))
                x, y = spot.at
                boxes.append((x - GRID, y - GRID, x + GRID, y + GRID))
            continue
        ends = []
        for spot in run:
            x, y = spot.at
            end = (x + dx * STUB, y + dy * STUB)
            drawn.append(Wire(spot.at, end))
            ends.append(end)
        if len(ends) > 1:
            drawn.append(Wire(ends[0], ends[-1]))
        anchor = ends[0]
        if first.net in power:
            drawn.append(Power(first.net, anchor, (dx, dy)))
            boxes.append(_power_box(first.net, anchor, (dx, dy)))
        else:
            angle = {(1, 0): 0, (0, -1): 90, (-1, 0): 180, (0, 1): 270}[(dx, dy)]
            drawn.append(Label(first.net, anchor, angle))
            boxes.append(_along(anchor, (dx, dy), _text_width(first.net)))
        for spot, end in zip(run, ends, strict=True):
            (ax, ay), (bx, by) = spot.at, end
            boxes.append((min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)))
    unit.drawn = drawn
    taken = _union(*boxes)
    _place_fields(unit, taken, boxes)
    ref_box, value_box = _field_boxes(unit)
    unit.box = _union(taken, ref_box, value_box)


def _along(anchor: Point, direction: Point, length: int, half: int = FONT) -> Box:
    """The box of something ``length`` long laid from ``anchor`` along ``direction``."""
    (x, y), (dx, dy) = anchor, direction
    if dx:
        x2 = x + dx * length
        return (min(x, x2), y - half, max(x, x2), y + half)
    y2 = y + dy * length
    return (x - half, min(y, y2), x + half, max(y, y2))


def _power_box(net: str, at: Point, out: Point) -> Box:
    """A power symbol pointing ``out`` from ``at``, and its name beyond it."""
    body = _along(at, out, 3 * GRID, GRID + GRID // 2)
    text = _power_text(net, at, out)
    return _union(body, text[2])


def _power_text(net: str, at: Point, out: Point) -> tuple[Point, str | None, Box]:
    """Where a power symbol's name goes -- beyond its body, always horizontal
    -- the justification, and the box the text takes."""
    width = _text_width(net)
    x, y = at
    if out == (0, -1):  # a supply pointing up: its name above it
        pos = (x, y - 4 * GRID)
        return pos, None, (x - width // 2, pos[1] - FONT, x + width // 2, pos[1] + FONT // 2)
    if out == (0, 1):  # ground pointing down: its name below it
        pos = (x, y + 4 * GRID)
        return pos, None, (x - width // 2, pos[1] - FONT // 2, x + width // 2, pos[1] + FONT)
    if out == (1, 0):
        pos = (x + 4 * GRID, y)
        return pos, "left", (pos[0], y - FONT, pos[0] + width, y + FONT)
    pos = (x - 4 * GRID, y)
    return pos, "right", (pos[0] - width, y - FONT, pos[0], y + FONT)


def _field_boxes(unit: Unit) -> tuple[Box, Box]:
    ref, value = unit.part.ref, unit.part.value
    boxes = []
    for text, (x, y) in ((ref, unit.ref_at), (value, unit.value_at)):
        width = _text_width(text)
        if unit.fields_justify == "left":
            boxes.append((x, y - FONT, x + width, y + FONT))
        elif unit.fields_justify == "right":
            boxes.append((x - width, y - FONT, x, y + FONT))
        else:
            boxes.append((x - width // 2, y - FONT, x + width // 2, y + FONT))
    return boxes[0], boxes[1]


def _place_fields(unit: Unit, taken: Box, boxes: list[Box]) -> None:
    """Reference and value where they meet nothing the unit draws: beside the
    body where no pin leaves it, else above or below everything."""
    x1, y1, x2, y2 = unit.body
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    faces = {FACES[s.out] for s in unit.spots}
    options = []
    if "right" not in faces:
        options.append(("left", (x2 + GRID, cy - GRID), (x2 + GRID, cy + GRID)))
    if "left" not in faces:
        options.append(("right", (x1 - GRID, cy - GRID), (x1 - GRID, cy + GRID)))
    if "top" not in faces:
        options.append((None, (cx, y1 - 3 * GRID), (cx, y1 - GRID)))
    if "bottom" not in faces:
        options.append((None, (cx, y2 + GRID), (cx, y2 + 3 * GRID)))
    # Above everything and below everything always fit; last resorts.
    options.append((None, (cx, taken[1] - 3 * GRID), (cx, taken[1] - GRID)))
    options.append((None, (cx, taken[3] + GRID), (cx, taken[3] + 3 * GRID)))
    others = boxes[1:]
    for justify, ref_at, value_at in options:
        unit.fields_justify, unit.ref_at, unit.value_at = justify, ref_at, value_at
        ref_box, value_box = _field_boxes(unit)
        if not any(_meets(f, b) for f in (ref_box, value_box) for b in others):
            return


# -- laying out ---------------------------------------------------------------------------------


@dataclass
class Group:
    units: list[Unit] = field(default_factory=list)
    extra: list = field(default_factory=list)  # (kind, point) drawn apart from any part
    box: Box = (0, 0, 0, 0)

    def place(self, dx: int, dy: int) -> None:
        for unit in self.units:
            unit.origin = (unit.origin[0] + dx, unit.origin[1] + dy)
        self.extra = [(kind, net, (x + dx, y + dy)) for kind, net, (x, y) in self.extra]
        x1, y1, x2, y2 = self.box
        self.box = (x1 + dx, y1 + dy, x2 + dx, y2 + dy)


def _placed_box(unit: Unit) -> Box:
    x1, y1, x2, y2 = unit.box
    return (x1 + unit.origin[0], y1 + unit.origin[1], x2 + unit.origin[0], y2 + unit.origin[1])


def _at(unit: Unit, x: int, y: int) -> None:
    """Put the unit so its box's top-left corner is at (x, y) or just after, on the grid."""
    ox, oy = snap(x - unit.box[0]), snap(y - unit.box[1])
    if ox + unit.box[0] < x:
        ox += GRID
    if oy + unit.box[1] < y:
        oy += GRID
    unit.origin = (ox, oy)


def _capacitance(value: str) -> float | None:
    """A capacitor's value in farads, when it reads as one: "100nF", "10u"."""
    match = re.match(r"^\s*([\d.]+)\s*([pnuµm]?)", value)
    if not match:
        return None
    try:
        number_ = float(match.group(1))
    except ValueError:
        return None
    scale = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3, "": 1.0}[match.group(2)]
    return number_ * scale


def _groups(units: list[Unit], parts: list[Part], on: dict, power: set[str]) -> list[Group]:
    """Parts gathered around the ICs."""
    by_part = {p.ref: p for p in parts}
    by_ref: dict[str, list[Unit]] = {}
    for unit in units:
        by_ref.setdefault(unit.part.ref, []).append(unit)
    nets_of: dict[str, set[str]] = {}
    for (ref, _), net in on.items():
        nets_of.setdefault(ref, set()).add(net)
    kinds_on: dict[tuple[str, str], str] = {}
    for (ref, number_), _ in on.items():
        for pin in by_part[ref].lib.symbol.pins:
            if pin.number == number_:
                kinds_on[(ref, number_)] = pin.electrical
    pins_of = {p.ref: len(p.lib.symbol.pins) for p in parts}
    order = sorted(by_ref, key=lambda r: (-pins_of[r], _natural(r)))

    def is_centre(ref: str) -> bool:
        return pins_of[ref] >= 5 or (re.match(r"^(U|IC)\d", ref) is not None and pins_of[ref] >= 3)

    centres = [r for r in order if is_centre(r)]
    assigned: dict[str, str] = {}
    decoupling: set[str] = set()
    for ref in order:
        if ref in centres:
            continue
        signals = nets_of.get(ref, set()) - power
        best, score = None, 0
        for centre in centres:
            shared = len(signals & nets_of.get(centre, set()))
            if shared > score:
                best, score = centre, shared
        if best is None and pins_of[ref] <= 3:
            rails = nets_of.get(ref, set()) & power
            supply = {n for n in rails if not _is_ground(n)}
            if supply and rails - supply:
                farads = _capacitance(by_part[ref].value) or 0.0
                pins_on_supply = {
                    c: [n for (r, n), net in on.items() if r == c and net in supply]
                    for c in centres
                }
                drivers = [
                    c
                    for c in centres
                    if any(kinds_on.get((c, n)) == "power_out" for n in pins_on_supply[c])
                ]
                users = [c for c in centres if pins_on_supply[c]]
                if farads >= 1e-6 and drivers:
                    best = drivers[0]
                elif users:
                    best = max(users, key=lambda c: (len(pins_on_supply[c]), -order.index(c)))
                if best is not None:
                    decoupling.add(ref)
        if best is not None:
            assigned[ref] = best
    groups = []
    for centre in centres:
        members = [r for r in order if assigned.get(r) == centre]
        groups.append(_lay_group(centre, members, decoupling, by_ref, on, power))
    loose = [r for r in order if r not in centres and r not in assigned]
    # What is left joins by the signals it shares.
    while loose:
        seed = loose.pop(0)
        members = [seed]
        signals = nets_of.get(seed, set()) - power
        grown = True
        while grown:
            grown = False
            for ref in list(loose):
                if (nets_of.get(ref, set()) - power) & signals:
                    members.append(ref)
                    signals |= nets_of.get(ref, set()) - power
                    loose.remove(ref)
                    grown = True
        groups.append(_lay_row([u for r in members for u in by_ref[r]]))
    return groups


def _lay_row(units: list[Unit], x: int = 0, y: int = 0) -> Group:
    for unit in units:
        _at(unit, x, y)
        x = _placed_box(unit)[2] + GAP
    return Group(units, [], _union(*[_placed_box(u) for u in units]))


def _lay_group(centre: str, satellites: list[str], decoupling: set[str], by_ref: dict,
               on: dict, power: set[str]) -> Group:  # fmt: skip
    """The centre's units in a row; beside each side, the parts that meet
    that side's pins, stacked; beneath the centre, its decoupling."""
    units = by_ref[centre]
    x = 0
    for unit in units:
        _at(unit, x, 0)
        x = _placed_box(unit)[2] + GAP
    core = _union(*[_placed_box(u) for u in units])
    side: dict[str, str] = {}
    for unit in units:
        for spot in unit.spots:
            if spot.net is None or spot.net in power:
                continue
            face = FACES[spot.out]
            for ref in satellites:
                if (
                    ref not in side
                    and ref not in decoupling
                    and any(
                        on.get((ref, s.pin.number)) == spot.net
                        for u in by_ref[ref]
                        for s in u.spots
                    )
                ):
                    side[ref] = face
    placed = list(units)

    def column(refs: list[str], left_edge: int | None, right_edge: int | None) -> None:
        stack = [u for r in refs for u in by_ref[r]]
        if not stack:
            return
        width = max(u.box[2] - u.box[0] for u in stack)
        y = core[1]
        for unit in stack:
            x = left_edge if left_edge is not None else right_edge - width
            _at(unit, x, y)
            y = _placed_box(unit)[3] + GAP
            placed.append(unit)

    column([r for r in satellites if side.get(r) == "right"], core[2] + GAP, None)
    column([r for r in satellites if side.get(r) == "left"], None, core[0] - GAP)
    under = [r for r in satellites if r in decoupling] + [
        r for r in satellites if r not in decoupling and side.get(r) not in ("left", "right")
    ]
    beneath = _union(core, *[_placed_box(u) for u in placed if u in units])
    x, y = beneath[0], beneath[3] + GAP
    row_bottom = y
    for ref in under:
        for unit in by_ref[ref]:
            _at(unit, x, y)
            box = _placed_box(unit)
            # Clear of the side columns: never under what already stands there.
            while any(_meets(box, _placed_box(o)) for o in placed):
                _at(unit, box[0] + GRID, y)
                box = _placed_box(unit)
            x = box[2] + GAP
            row_bottom = max(row_bottom, box[3])
            placed.append(unit)
    return Group(placed, [], _union(*[_placed_box(u) for u in placed]))


def _flags_group(nets: list[str]) -> Group | None:
    """A PWR_FLAG for each supply nothing drives, each with its own power
    symbol on the same point, in a row."""
    if not nets:
        return None
    extra = []
    boxes = []
    x = 0
    for net in nets:
        at = (x, 4 * GRID)
        extra.append(("flag", net, at))
        out = (0, 1) if _is_ground(net) else (0, -1)
        back = (0, -out[1])
        boxes.append(_power_box(net, at, out))
        flag_at = (at[0], at[1] + back[1] * 2 * GRID)
        boxes.append(_power_box("PWR_FLAG", flag_at, back))
        box = _union(*boxes[-2:])
        x = snap(max(box[2], x + 4 * GRID) + GAP + _text_width(net) // 2)
    return Group([], extra, _union(*boxes))


def _pack(groups: list[Group], width: int) -> tuple[int, int]:
    """Groups onto the sheet in rows, largest first; the extent they take."""
    x = y = 0
    row = 0
    right = 0
    for group in sorted(groups, key=lambda g: -(g.box[2] - g.box[0]) * (g.box[3] - g.box[1])):
        w, h = group.box[2] - group.box[0], group.box[3] - group.box[1]
        if x and x + w > width:
            x, y = 0, y + row + GROUP_GAP
            row = 0
        group.place(snap(x - group.box[0]), snap(y - group.box[1]))
        x += w + GROUP_GAP
        row = max(row, h)
        right = max(right, x)
    return right, y + row


# -- writing ---------------------------------------------------------------------------------


class _Ids:
    def __init__(self, seed: str) -> None:
        self.namespace = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "kicad-cli/schematic/" + seed)

    def __call__(self, *parts: object) -> str:
        return str(uuidlib.uuid5(self.namespace, "/".join(str(p) for p in parts)))


def _effects(justify: str | None = None, hide: bool = False) -> list[List]:
    size = number(mm(FONT))
    effects = List.new("effects", List.new("font", List.new("size", size, size)))
    if justify:
        effects.append(List.new("justify", *[symbol(j) for j in justify.split()]))
    return ([List.new("hide", symbol("yes"))] if hide else []) + [effects]


def _xy(point: Point) -> list[str]:
    return [number(mm(point[0]), 4), number(mm(point[1]), 4)]


def _property(name: str, value: str, at: Point, justify=None, hide=False, turned: int = 0) -> List:
    """A field of a placed symbol, drawn horizontal whatever the symbol's angle.

    A field's angle is taken with the symbol's, and a text that would come
    out upside down is turned the right way up with its justification
    swapped -- so on a symbol turned 90 or 270 degrees the field is at 90,
    and at 90 or 180 degrees "left" means right. Measured by rendering every
    combination with KiCad.
    """
    angle = 90 if turned in (90, 270) else 0
    if justify and (turned + angle) % 360 == 180:
        justify = {"left": "right", "right": "left"}.get(justify, justify)
    return List.new(
        "property", quote(name), quote(value), List.new("at", *_xy(at), str(angle)),
        *_effects(justify, hide),
    )  # fmt: skip


def _symbol_node(lib: Resolved, ref: str, value: str, footprint: str, at: Point, angle: int,
                 unit: int, ids: _Ids, root: str, project: str, ref_at: Point, value_at: Point,
                 justify: str | None = None, hide_ref: bool = False) -> List:  # fmt: skip
    node = List.new(
        "symbol",
        List.new("lib_id", quote(lib.lib_id)),
        List.new("at", *_xy(at), str(angle)),
        List.new("unit", str(unit)),
        List.new("exclude_from_sim", symbol("no")),
        List.new("in_bom", symbol("yes")),
        List.new("on_board", symbol("yes")),
        List.new("dnp", symbol("no")),
        List.new("uuid", quote(ids("symbol", ref, unit))),
        _property("Reference", ref, ref_at, justify, hide_ref, angle),
        _property("Value", value, value_at, justify, turned=angle),
        _property("Footprint", footprint, at, hide=True),
        _property("Datasheet", "", at, hide=True),
        _property("Description", "", at, hide=True),
    )
    for pin in lib.symbol.pins_of(unit, 1):
        node.append(
            List.new(
                "pin", quote(pin.number), List.new("uuid", quote(ids("pin", ref, unit, pin.number)))
            )
        )
    path = List.new(
        "path", quote("/" + root), List.new("reference", quote(ref)), List.new("unit", str(unit))
    )
    node.append(List.new("instances", List.new("project", quote(project), path)))
    return node


def _power_angle(lib: Resolved, out: Point) -> int:
    """The angle that points a power symbol's body along ``out``."""
    box = _graphics_box(lib, 1) or (0, 0, 0, 0)
    body = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    for angle in (0, 90, 180, 270):
        bx, by = _turn(body[0], body[1], angle)
        if (bx * out[0] + by * out[1]) > 0:
            return angle
    return 0


def _power_symbol_for(net: str, libs: Libraries) -> Resolved:
    """KiCad's own symbol for the net if it has one -- power:+5V, power:GND --
    else a ground or supply arrow carrying the net's name as its value."""
    for name in (net, net.upper()):
        try:
            return libs.resolve(f"power:{name}")
        except SymbolError:
            pass
    return libs.resolve("power:GND" if _is_ground(net) else "power:VCC")


class _Writer:
    def __init__(self, libs: Libraries, ids: _Ids, project: str) -> None:
        self.libs, self.ids, self.project = libs, ids, project
        self.root = ids("root")
        self.embedded: dict[str, List] = {}
        self.items: list[List] = []
        self.power_count = 0
        self.flag_count = 0

    def embed(self, lib: Resolved) -> None:
        self.embedded.setdefault(lib.lib_id, lib.node)

    def wire(self, a: Point, b: Point, key) -> None:
        self.items.append(
            List.new(
                "wire",
                List.new("pts", List.new("xy", *_xy(a)), List.new("xy", *_xy(b))),
                List.new("stroke", List.new("width", "0"), List.new("type", "default")),
                List.new("uuid", quote(self.ids("wire", *key))),
            )
        )

    def power(self, net: str, at: Point, out: Point) -> None:
        lib = _power_symbol_for(net, self.libs)
        self.embed(lib)
        self.power_count += 1
        value_at, justify, _ = _power_text(net, at, out)
        self.items.append(
            _symbol_node(
                lib,
                f"#PWR{self.power_count:03d}",
                net,
                "",
                at,
                _power_angle(lib, out),
                1,
                self.ids,
                self.root,
                self.project,
                at,
                value_at,
                justify,
                hide_ref=True,
            )  # fmt: skip
        )

    def flag(self, net: str, at: Point, out: Point) -> None:
        lib = self.libs.resolve("power:PWR_FLAG")
        self.embed(lib)
        self.flag_count += 1
        value_at, justify, _ = _power_text("PWR_FLAG", at, out)
        angle = _power_angle(lib, out)
        self.items.append(
            _symbol_node(
                lib,
                f"#FLG{self.flag_count:02d}",
                "PWR_FLAG",
                "",
                at,
                angle,
                1,
                self.ids,
                self.root,
                self.project,
                at,
                value_at,
                justify,
                hide_ref=True,
            )  # fmt: skip
        )

    def unit(self, unit: Unit) -> None:
        part = unit.part
        self.embed(part.lib)
        ox, oy = unit.origin

        def at(p: Point) -> Point:
            return (ox + p[0], oy + p[1])

        self.items.append(
            _symbol_node(
                part.lib,
                part.ref,
                part.value,
                part.footprint,
                (ox, oy),
                0,
                unit.unit,
                self.ids,
                self.root,
                self.project,
                at(unit.ref_at),
                at(unit.value_at),
                unit.fields_justify,
            )  # fmt: skip
        )
        for index, item in enumerate(unit.drawn):
            key = (part.ref, unit.unit, index)
            if isinstance(item, Wire):
                self.wire(at(item.a), at(item.b), key)
            elif isinstance(item, NoConnect):
                self.items.append(
                    List.new(
                        "no_connect",
                        List.new("at", *_xy(at(item.at))),
                        List.new("uuid", quote(self.ids("nc", *key))),
                    )  # fmt: skip
                )
            elif isinstance(item, Power):
                self.power(item.net, at(item.at), item.out)
            elif isinstance(item, Label):
                justify = "left bottom" if item.angle in (0, 90) else "right bottom"
                self.items.append(
                    List.new(
                        "label",
                        quote(item.text),
                        List.new("at", *_xy(at(item.at)), str(item.angle)),
                        List.new("fields_autoplaced", symbol("yes")),
                        *_effects(justify),
                        List.new("uuid", quote(self.ids("label", *key))),
                    )  # fmt: skip
                )

    def document(self, title: str, paper: str) -> str:
        sch = List.new(
            "kicad_sch",
            List.new("version", "20260101"),
            List.new("generator", quote("kicad-cli")),
            List.new("generator_version", quote("1.0")),
            List.new("uuid", quote(self.root)),
            List.new("paper", quote(paper)),
            List.new("title_block", List.new("title", quote(title))),
            List.new("lib_symbols", *self.embedded.values()),
            *self.items,
            List.new("sheet_instances", List.new("path", quote("/"), List.new("page", quote("1")))),
            List.new("embedded_fonts", symbol("no")),
        )
        return render(sch)


def _undriven(parts: list[Part], on: dict, power: set[str]) -> list[str]:
    """Supplies no pin gives power to. Every one is drawn with power symbols,
    whose own pins take power, so each needs a flag -- not only those where a
    part's pin takes power too."""
    kinds = _kinds({p.ref: p for p in parts}, on)
    return sorted((n for n in power if "power_out" not in kinds.get(n, set())), key=_natural)


# -- the command -------------------------------------------------------------------------------


def _libraries() -> Libraries:
    root = kicad_env.find_kicad_root()
    if root is None:
        envelope.fail(
            "E_CONFIG",
            "could not find the KiCad installation, so its symbol libraries cannot be read",
            {
                "hint": f"set {kicad_env.ENV_ROOT} to the KiCad installation directory; "
                "run: kicad-cli doctor"
            },
        )
    return installed(root)


def plan(spec: dict[str, Any]) -> tuple[list[Part], dict, list[dict], set[str]]:
    """Everything resolved against the libraries; problems refused together."""
    libs = _libraries()
    parts, problems = _resolve(spec, libs)
    by_ref = {p.ref: p for p in parts}
    on, joins, pin_problems = _pin_nets(spec, by_ref) if not problems else ({}, [], [])
    problems += pin_problems
    if problems:
        envelope.fail(
            "E_VALIDATION",
            "the circuit specification does not resolve against the KiCad libraries",
            {"problems": problems[:40], "problem_count": len(problems)},
        )
    return parts, on, joins, _power_nets(spec, by_ref, on)


def create(spec_path: str, out_dir: str) -> dict[str, Any]:
    from ..schematic import load_spec  # noqa: PLC0415

    spec = load_spec(spec_path)
    parts, on, joins, power = plan(spec)
    libs = _libraries()
    units = [u for p in parts for u in _units(p, on)]
    for unit in units:
        _plan(unit, power)
    groups = _groups(units, parts, on, power)
    flags = _flags_group(_undriven(parts, on, power))
    if flags is not None:
        groups.append(flags)
    paper = PAPERS[-1][0]
    for name, w, h in PAPERS:
        right, bottom = _pack(groups, (w - 40) * NM)
        if right <= (w - 30) * NM and bottom <= (h - 60) * NM:
            paper = name
            break
    for group in groups:
        group.place(snap(20 * NM), snap(20 * NM))

    stem = Path(spec_path).stem
    digest = hashlib.sha256(json.dumps(spec, sort_keys=True).encode("utf-8")).hexdigest()
    writer = _Writer(libs, _Ids(digest), stem)
    for unit in sorted(units, key=lambda u: (_natural(u.part.ref), u.unit)):
        writer.unit(unit)
    for group in groups:
        for kind, net, at in group.extra:
            if kind == "flag":
                # The supply's symbol one way, the flag the other, a wire between.
                out = (0, 1) if _is_ground(net) else (0, -1)
                back = (0, -out[1])
                writer.power(net, at, out)
                flag_at = (at[0], at[1] + back[1] * 2 * GRID)
                writer.wire(at, flag_at, ("flag", net))
                writer.flag(net, flag_at, back)
    text = writer.document(str(spec.get("title") or stem), paper)

    destination = Path(out_dir)
    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        envelope.fail("E_IO", "cannot create the output directory", {"out": str(destination)})
        raise AssertionError from exc
    schematic_path = destination / f"{stem}.kicad_sch"
    schematic_path.write_bytes(text.encode("utf-8"))

    # Read it back: the schematic must say what was asked for, net by net.
    built = netlist_file.build(schematic_path)
    mismatches = _check(on, built)
    if mismatches:
        envelope.fail(
            "E_INTEGRITY",
            "the drawn schematic does not join the pins the specification joins",
            {"mismatches": mismatches[:20], "schematic": str(schematic_path)},
        )
    netlist_path = destination / f"{stem}.net"
    netlist_path.write_bytes(netlist_file.dumps(built, tool="kicad-cli").encode("utf-8"))
    unconnected = sorted((p.ref for p in parts if not any(k[0] == p.ref for k in on)), key=_natural)
    return {
        "title": str(spec.get("title") or stem),
        "parts": [
            {"ref": p.ref, "symbol": p.lib.lib_id, "value": p.value}
            for p in sorted(parts, key=lambda p: _natural(p.ref))
        ],
        "nets": joins,
        "unconnected_parts": unconnected,
        "written": {"schematic": str(schematic_path), "netlist": str(netlist_path)},
        "drawing": {"status": "drawn", "style": "labelled", "reason": None, "paper": paper},
    }


def _check(on: dict, built) -> list[dict]:
    """Where the netlist read back differs from the specification."""
    wanted: dict[str, set[tuple[str, str]]] = {}
    for key, net in on.items():
        wanted.setdefault(net, set()).add(key)
    got: dict[str, set[tuple[str, str]]] = {}
    for net in built.nets:
        got[net.name.removeprefix("/").replace("{slash}", "/")] = {
            (n.ref, n.pin) for n in net.nodes
        }
    problems = []
    for net, pins in sorted(wanted.items()):
        have = got.get(net, set())
        if pins != have:
            problems.append(
                {
                    "net": net,
                    "missing": sorted(f"{r}.{p}" for r, p in pins - have)[:10],
                    "extra": sorted(f"{r}.{p}" for r, p in have - pins)[:10],
                }
            )
    return problems
