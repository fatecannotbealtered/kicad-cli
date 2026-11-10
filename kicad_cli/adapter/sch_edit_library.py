"""Defining a symbol in a project symbol library: one unit, a rectangular body, explicit pins.

The definition is the value of ``symbol create --definition``, a json flag (inline JSON or a
file that holds it):

    {"name": "SN74LVC1G14", "reference": "U", "value": "SN74LVC1G14",
     "footprint": "LocalLogic:DBV0005A", "datasheet": "https://...", "description": "...",
     "body_mm": [10.16, 10.16],
     "pins": [{"number": "1", "name": "NC", "type": "no_connect",
               "at_mm": [-7.62, -2.54], "angle_deg": 0, "length_mm": 2.54}, ...]}

``at_mm`` is a pin's connection point in the library's own coordinates (y up), on the
1.27 mm schematic grid so that wires meet it; ``angle_deg`` is the direction the pin points
from that point towards the body. The symbol is written as KiCad's symbol editor writes one;
a new library file is created when the library does not exist yet.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from .fileformat.sexpr import Document, List, copy, quote

NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.+-]{0,63}$")
REFERENCE = re.compile(r"^[A-Za-z#][A-Za-z_]{0,15}$")
PIN_TYPES = (
    "input", "output", "bidirectional", "tri_state", "passive", "free", "unspecified",
    "power_in", "power_out", "open_collector", "open_emitter", "no_connect",
)
GRID_NM = 1_270_000
NEW_LIBRARY = '(kicad_symbol_lib (version 20250114) (generator "kicad-cli"))'


class DefinitionError(ValueError):
    """A definition that cannot be written; the message names the field."""


def _nm(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise DefinitionError(f"{where} is a number of millimetres")
    try:
        nm = Decimal(str(value)) * 1_000_000
    except InvalidOperation:
        raise DefinitionError(f"{where} is a number of millimetres") from None
    if not nm.is_finite() or nm != nm.to_integral_value():
        raise DefinitionError(f"{where} has more than 6 decimals")
    return int(nm)


def _mm(nm: int) -> str:
    text = f"{nm / 1_000_000:.6f}".rstrip("0").rstrip(".")
    return text if text not in ("-0", "") else "0"


def check(spec: Any) -> dict[str, Any]:
    """The definition, checked: every problem is a ``DefinitionError``."""
    if not isinstance(spec, dict):
        raise DefinitionError("the definition is a JSON object")
    known = {"name", "reference", "value", "footprint", "datasheet", "description", "body_mm", "pins"}
    extra = sorted(set(spec) - known)
    if extra:
        raise DefinitionError(f"the definition does not take {', '.join(extra)}")
    if not isinstance(spec.get("name"), str) or not NAME.match(spec["name"]):
        raise DefinitionError("name is a symbol name: a letter, then letters, digits, _ . + or -")
    if not isinstance(spec.get("reference"), str) or not REFERENCE.match(spec["reference"]):
        raise DefinitionError("reference is the designator prefix, such as U or R")
    for key in ("value", "footprint", "datasheet", "description"):
        if key in spec and not isinstance(spec[key], str):
            raise DefinitionError(f"{key} is text")
    body = spec.get("body_mm")
    if not isinstance(body, list) or len(body) != 2:
        raise DefinitionError("body_mm is [width, height] in millimetres")
    if any(_nm(v, "body_mm") <= 0 for v in body):
        raise DefinitionError("body_mm values are positive")
    pins = spec.get("pins")
    if not isinstance(pins, list) or not pins:
        raise DefinitionError("pins is a non-empty list")
    numbers = []
    for n, pin in enumerate(pins):
        where = f"pins[{n}]"
        if not isinstance(pin, dict):
            raise DefinitionError(f"{where} is an object")
        extra = sorted(set(pin) - {"number", "name", "type", "at_mm", "angle_deg", "length_mm"})
        if extra:
            raise DefinitionError(f"{where} does not take {', '.join(extra)}")
        if not isinstance(pin.get("number"), str) or not pin["number"].strip() or " " in pin["number"]:
            raise DefinitionError(f"{where}.number is text without spaces, such as 1 or A3")
        if not isinstance(pin.get("name", ""), str):
            raise DefinitionError(f"{where}.name is text")
        if pin.get("type") not in PIN_TYPES:
            raise DefinitionError(f"{where}.type is one of {', '.join(PIN_TYPES)}")
        at = pin.get("at_mm")
        if not isinstance(at, list) or len(at) != 2:
            raise DefinitionError(f"{where}.at_mm is [x, y] in millimetres")
        if any(_nm(v, f"{where}.at_mm") % GRID_NM for v in at):
            raise DefinitionError(f"{where}.at_mm is on the 1.27 mm grid, so that wires meet the pin")
        if pin.get("angle_deg", 0) not in (0, 90, 180, 270):
            raise DefinitionError(f"{where}.angle_deg is 0, 90, 180 or 270")
        if _nm(pin.get("length_mm", 2.54), f"{where}.length_mm") <= 0:
            raise DefinitionError(f"{where}.length_mm is positive")
        numbers.append(pin["number"])
    if len(set(numbers)) != len(numbers):
        raise DefinitionError("pin numbers are unique")
    return spec


def _effects(size: str = "1.27") -> str:
    return f"(effects (font (size {size} {size})))"


def symbol_node(spec: dict[str, Any]) -> List:
    """The library symbol, as a node of a symbol library."""
    name = spec["name"]
    w, h = (_nm(v, "body_mm") for v in spec["body_mm"])
    half_w, half_h = w // 2, h // 2
    fields = [
        ("Reference", spec["reference"], half_h + 1_270_000, False),
        ("Value", spec.get("value") or name, -half_h - 1_270_000, False),
        ("Footprint", spec.get("footprint", ""), -half_h - 3_810_000, True),
        ("Datasheet", spec.get("datasheet", ""), -half_h - 6_350_000, True),
        ("Description", spec.get("description", ""), -half_h - 8_890_000, True),
    ]
    props = "".join(
        f"(property {quote(k)} {quote(v)} (at 0 {_mm(y)} 0){' (hide yes)' if hide else ''} {_effects()})"
        for k, v, y, hide in fields
    )
    pins = "".join(
        f"(pin {p['type']} line (at {_mm(_nm(p['at_mm'][0], 'x'))} {_mm(_nm(p['at_mm'][1], 'y'))} "
        f"{p.get('angle_deg', 0)}) (length {_mm(_nm(p.get('length_mm', 2.54), 'length'))}) "
        f"(name {quote(p.get('name') or '~')} {_effects('1.016')}) "
        f"(number {quote(p['number'])} {_effects('1.016')}))"
        for p in spec["pins"]
    )
    text = (
        f"(symbol {quote(name)} (pin_names (offset 0.508)) (exclude_from_sim no) (in_bom yes) (on_board yes) "
        f"{props}"
        f"(symbol {quote(name + '_0_1')} (rectangle (start {_mm(-half_w)} {_mm(half_h)}) "
        f"(end {_mm(half_w)} {_mm(-half_h)}) (stroke (width 0.254) (type default)) (fill (type background))))"
        f"(symbol {quote(name + '_1_1')} {pins}))"
    )
    return copy(Document.parse(text).root)


def names(text: str) -> list[str]:
    """The symbols a library holds."""
    return [(node.value(1) or "") for node in Document.parse(text).root.find_all("symbol")]


def with_symbol(raw: str | None, spec: dict[str, Any]) -> str:
    """The library text with the new symbol appended; the library must not have it yet."""
    doc = Document.parse(raw) if raw else Document.parse(NEW_LIBRARY)
    if doc.root.head != "kicad_symbol_lib":
        raise DefinitionError("the file is not a KiCad symbol library")
    if any((node.value(1) or "") == spec["name"] for node in doc.root.find_all("symbol")):
        raise DefinitionError(f"the library already has a symbol named {spec['name']}")
    doc.root.append(symbol_node(spec))
    return doc.dumps()


def read_symbol(text: str, name: str) -> dict[str, Any] | None:
    """A library symbol's fields and pin numbers, or None when the library lacks it."""
    for node in Document.parse(text).root.find_all("symbol"):
        if (node.value(1) or "") != name:
            continue
        props = {p.value(1): p.value(2) for p in node.find_all("property")}
        pins = sorted(
            (pin.find("number").value(1) or "") for unit in node.find_all("symbol") for pin in unit.find_all("pin")
            if pin.find("number") is not None
        )
        return {"properties": props, "pins": pins}
    return None
