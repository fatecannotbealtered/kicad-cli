"""A design's references, and what KiCad calls an annotation error.

KiCad will not update a board from a schematic whose annotation has errors,
and its netlist export warns of them. Measured with KiCad's own export on its
demo projects and on changes made to them for the purpose, an annotation
error is any of:

- a reference not yet numbered: "R?";
- one reference and unit placed twice -- on one sheet, on two, or on two
  instances of one sheet; power symbols ("#PWR01") count too;
- units of one part with different values;
- a unit the part does not have: unit 5 of a part with three.

None of KiCad's 35 demo projects has one. Each problem is named after the
ERC rule that reports it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .circuit import Design


@dataclass
class Problem:
    rule: str  # unannotated, duplicate_reference, unit_value_mismatch, extra_units
    reference: str
    # Where: (sheet instance name, symbol uuid) of each symbol involved.
    symbols: list[tuple[str, str]] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "reference": self.reference,
            "sheets": sorted({sheet for sheet, _ in self.symbols}),
            "detail": self.detail,
        }


def problems(design: Design) -> list[Problem]:
    """Every annotation error in the design, in reference order."""
    placed: dict[str, list] = defaultdict(list)  # reference -> [(instance, symbol, unit)]
    for instance in design.instances:
        for symbol in instance.schematic.symbols:
            inst = symbol.instance(instance.path)
            reference = inst.reference if inst else symbol.properties.get("Reference", "")
            unit = inst.unit if inst else symbol.unit
            placed[reference].append((instance, symbol, unit))

    out: list[Problem] = []
    for reference in sorted(placed):
        entries = placed[reference]

        def where(items) -> list[tuple[str, str]]:
            return [(instance.name, symbol.uuid) for instance, symbol, _ in items]

        if not reference or reference.endswith("?"):
            out.append(Problem("unannotated", reference, where(entries), "not numbered"))
            continue
        by_unit: dict[int, list] = defaultdict(list)
        for entry in entries:
            by_unit[entry[2]].append(entry)
        for unit in sorted(by_unit):
            if len(by_unit[unit]) > 1:
                out.append(
                    Problem(
                        "duplicate_reference",
                        reference,
                        where(by_unit[unit]),
                        f"unit {unit} is placed {len(by_unit[unit])} times",
                    )
                )
        values = {symbol.properties.get("Value", "") for _, symbol, _ in entries}
        if len(values) > 1:
            out.append(
                Problem(
                    "unit_value_mismatch",
                    reference,
                    where(entries),
                    "units have different values: " + ", ".join(sorted(values)),
                )
            )
        for instance, symbol, unit in entries:
            library = instance.schematic.library.get(symbol.library_name)
            if library is not None and unit > library.unit_count:
                out.append(
                    Problem(
                        "extra_units",
                        reference,
                        [(instance.name, symbol.uuid)],
                        f"unit {unit} of a part with {library.unit_count}",
                    )
                )
    return out
