"""Defining a footprint in a project footprint library: front-side rectangular SMD pads.

Ported from the footprint half of the previous implementation's ``library_build.py``. The
definition is the value of ``--definition``, a json flag (inline JSON or a file that holds it):

    {"name": "SOT-23-5_Demo", "body_mm": [3.0, 1.6], "courtyard_mm": 0.25,
     "pads": [{"number": "1", "at_mm": [-1.1, -0.95], "size_mm": [1.06, 0.65]}, ...]}

``body_mm`` is the nominal centred body rectangle (drawn on F.Fab); ``courtyard_mm`` is the
free space from the body and pads to the inner edge of the courtyard line. Every length
must be a whole number of nanometres, which is how KiCad stores them; the courtyard is
rounded outward.
"""
from __future__ import annotations

import re
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from .base import invalid
from .fileformat.sexpr import Document, copy, quote, render

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,99}$")
PAD_NUMBER = re.compile(r"^[A-Za-z0-9_.+-]{1,16}$")


def _pair(value: Any, where: str, positive: bool) -> None:
    if not (isinstance(value, list) and len(value) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
        raise invalid(f"{where} is a list of two numbers in mm.")
    if positive and any(v <= 0 for v in value):
        raise invalid(f"{where} must be greater than zero.")


def _whole_nm(value: float, where: str) -> None:
    nm = Decimal(str(value)) * 1_000_000
    if nm != nm.to_integral_value():
        raise invalid(f"{where} needs whole nanometres (0.000001 mm); {value} is finer than KiCad stores.")


def validate(data: Any) -> None:
    if not isinstance(data, dict):
        raise invalid("The footprint definition is a JSON object.")
    extra = set(data) - {"name", "body_mm", "courtyard_mm", "pads"}
    if extra:
        raise invalid(f"The footprint definition has unknown keys: {sorted(extra)}.")
    name = data.get("name")
    if not isinstance(name, str) or not NAME.fullmatch(name) or ".." in name:
        raise invalid("name is 1 to 100 letters, digits, _ . + or -, starting with a letter or digit.")
    _pair(data.get("body_mm"), "body_mm", True)
    courtyard = data.get("courtyard_mm")
    if not isinstance(courtyard, (int, float)) or isinstance(courtyard, bool) or courtyard < 0:
        raise invalid("courtyard_mm is a number of millimetres, 0 or more.")
    pads = data.get("pads")
    if not isinstance(pads, list) or not pads:
        raise invalid("pads is a non-empty list.")
    numbers = []
    for index, pad in enumerate(pads):
        where = f"pads[{index}]"
        if not isinstance(pad, dict) or set(pad) - {"number", "at_mm", "size_mm"}:
            raise invalid(f"{where} has only number, at_mm and size_mm.")
        if not isinstance(pad.get("number"), str) or not PAD_NUMBER.fullmatch(pad["number"]):
            raise invalid(f"{where}.number is a pad number such as \"1\" or \"A1\".")
        numbers.append(pad["number"])
        _pair(pad.get("at_mm"), f"{where}.at_mm", False)
        _pair(pad.get("size_mm"), f"{where}.size_mm", True)
        for axis, v in enumerate(pad["at_mm"] + pad["size_mm"]):
            _whole_nm(v, f"{where} value {axis + 1}")
    if len(set(numbers)) != len(numbers):
        raise invalid("Every pad needs its own number.")
    for v in data["body_mm"]:
        _whole_nm(v, "body_mm")
    _whole_nm(courtyard, "courtyard_mm")


def _effects(size: float = 1.0) -> str:
    return f"(effects (font (size {size} {size})))"


def footprint_text(spec: dict[str, Any]) -> str:
    """The ``.kicad_mod`` text of a definition."""
    bx, by = spec["body_mm"]
    pads = "".join(
        f"(pad {quote(p['number'])} smd rect (at {p['at_mm'][0]} {p['at_mm'][1]})"
        f"(size {p['size_mm'][0]} {p['size_mm'][1]})"
        '(layers "F.Cu" "F.Paste" "F.Mask"))'
        for p in spec["pads"]
    )

    def d(value: float) -> Decimal:
        return Decimal(str(value))

    x0 = min(-d(bx) / 2, *(d(p["at_mm"][0]) - d(p["size_mm"][0]) / 2 for p in spec["pads"]))
    y0 = min(-d(by) / 2, *(d(p["at_mm"][1]) - d(p["size_mm"][1]) / 2 for p in spec["pads"]))
    x1 = max(d(bx) / 2, *(d(p["at_mm"][0]) + d(p["size_mm"][0]) / 2 for p in spec["pads"]))
    y1 = max(d(by) / 2, *(d(p["at_mm"][1]) + d(p["size_mm"][1]) / 2 for p in spec["pads"]))
    # The margin is free space to the inner edge of a 0.05 mm courtyard line.
    excess = d(spec["courtyard_mm"]) + Decimal("0.025")

    def edge(value: Decimal, rounding: str) -> str:
        nm = (value * 1_000_000).to_integral_value(rounding=rounding)
        return format(nm / 1_000_000, ".6f")

    left, top = edge(x0 - excess, ROUND_FLOOR), edge(y0 - excess, ROUND_FLOOR)
    right, bottom = edge(x1 + excess, ROUND_CEILING), edge(y1 + excess, ROUND_CEILING)
    cy = max(abs(float(top)), abs(float(bottom)))
    node = copy(Document.parse(
        f'(footprint {quote(spec["name"])} (version 20241229) (generator "kicad-cli")'
        '(layer "F.Cu") (attr smd)'
        f'(property "Reference" "REF**" (at 0 {-cy - 1.2} 0) (layer "F.SilkS") {_effects()})'
        f'(property "Value" {quote(spec["name"])} (at 0 {cy + 1.2} 0) (layer "F.Fab") {_effects()})'
        f"(fp_rect (start {-bx / 2} {-by / 2}) (end {bx / 2} {by / 2})"
        '(stroke (width 0.1) (type default)) (fill none) (layer "F.Fab"))'
        f"(fp_rect (start {left} {top}) (end {right} {bottom})"
        '(stroke (width 0.05) (type default)) (fill none) (layer "F.CrtYd"))'
        f"{pads})"
    ).root)
    return render(node) + "\n"


def pads_of(text: str) -> list[dict[str, Any]]:
    """The pads of a footprint file as written: number, centre and size in millimetres."""
    pads = []
    for pad in Document.parse(text).root.find_all("pad"):
        at, size = pad.find("at"), pad.find("size")
        pads.append({"number": pad.value(1), "at_mm": [float(at.atom(1)), float(at.atom(2))],
                     "size_mm": [float(size.atom(1)), float(size.atom(2))]})
    return pads
