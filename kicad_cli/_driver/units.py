"""Physical units in parameters (DCLI-080, DCLI-082) and durations (DCLI-026).

The contract names the quantities, their standard units and the units each one accepts. This
module holds only the conversion factors to the standard unit. A value without a unit, or
with a unit its quantity does not accept, is refused with ``E_USAGE``.
"""
from __future__ import annotations

import math
import re
from decimal import Decimal

from .errors import usage

_PI = Decimal(repr(math.pi))

# unit -> (factor, offset): standard = value * factor + offset
FACTORS: dict[str, dict[str, tuple[Decimal, Decimal]]] = {}


def _table(quantity: str, units: dict[str, str | Decimal]) -> None:
    FACTORS[quantity] = {u: (Decimal(f) if isinstance(f, str) else f, Decimal(0)) for u, f in units.items()}


_table("length", {"nm": "0.000001", "um": "0.001", "mm": "1", "cm": "10", "m": "1000", "mil": "0.0254", "in": "25.4"})
_table("angle", {"deg": "1", "rad": Decimal(180) / _PI})
_table("time", {"ms": "0.001", "s": "1", "min": "60", "h": "3600"})
FACTORS["temperature"] = {
    "degC": (Decimal(1), Decimal(0)),
    "K": (Decimal(1), Decimal("-273.15")),
    "degF": (Decimal(5) / Decimal(9), Decimal(-32) * Decimal(5) / Decimal(9)),
}
_table("mass", {"mg": "0.000001", "g": "0.001", "kg": "1"})
_table("force", {"N": "1", "kN": "1000"})
_table("pressure", {"Pa": "1", "kPa": "1000", "MPa": "1000000", "bar": "100000"})
_table("speed", {"mm/s": "1", "mm/min": Decimal(1) / Decimal(60), "m/s": "1000"})
_table("angular_speed", {"deg/s": "1", "rad/s": Decimal(180) / _PI, "rpm": "6"})
_table("frequency", {"Hz": "1", "kHz": "1000", "MHz": "1000000", "GHz": "1000000000"})
_table("voltage", {"uV": "0.000001", "mV": "0.001", "V": "1", "kV": "1000"})
_table("current", {"nA": "0.000000001", "uA": "0.000001", "mA": "0.001", "A": "1"})
_table("resistance", {"milliohm": "0.001", "ohm": "1", "kohm": "1000", "megohm": "1000000"})
_table("capacitance", {"pF": "1e-12", "nF": "1e-9", "uF": "1e-6", "mF": "0.001", "F": "1"})
_table("inductance", {"nH": "1e-9", "uH": "1e-6", "mH": "0.001", "H": "1"})
_table("power", {"mW": "0.001", "W": "1", "kW": "1000"})
_table("acceleration", {"mm/s2": "1", "m/s2": "1000"})
_table("torque", {"N*mm": "0.001", "N*m": "1"})
_table("energy", {"mJ": "0.001", "J": "1", "kJ": "1000"})
_table("area", {"mm2": "1", "cm2": "100", "m2": "1000000"})
_table("volume", {"mm3": "1", "cm3": "1000", "l": "1000000", "m3": "1000000000"})
_table("ratio", {"%": "1"})

_NUMBER = re.compile(r"\s*([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)\s*(\S*)\s*")


def convert(text: str, quantity: str, accepts: list[str], parameter: str) -> float:
    """Parse ``<number><unit>`` and return the value in the quantity's standard unit."""
    match = _NUMBER.fullmatch(text)
    listed = ", ".join(accepts)
    if not match:
        raise usage(f"{parameter} needs a number with a unit, such as 2.54{accepts[0]}; accepted units: {listed}.", parameter)
    number, unit = match.groups()
    if not unit:
        raise usage(f"{parameter} has no unit; give one of the accepted units: {listed}.", parameter)
    if unit not in accepts:
        raise usage(f"{parameter} does not accept the unit {unit!r}; accepted units: {listed}.", parameter)
    factor, offset = FACTORS[quantity][unit]
    value = Decimal(number) * factor + offset
    return float(round(value, 12).normalize())


def duration_seconds(text: str, parameter: str, suffixes: list[str]) -> float:
    """Parse a duration written as ``<n>ms``, ``<n>s``, ``<n>min`` or ``<n>h``."""
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(ms|s|min|h)", text.strip())
    if not match or match.group(2) not in suffixes:
        raise usage(
            f"{parameter} is a duration written as <n>ms, <n>s, <n>min or <n>h, such as 30s; got {text!r}.", parameter
        )
    return float(Decimal(match.group(1)) * FACTORS["time"][match.group(2)][0])
