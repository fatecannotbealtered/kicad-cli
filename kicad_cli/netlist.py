"""The schematic's parts, as the commands that hold a board up to it read them.

`sch sync-preview` and `sch relink` need what "Update PCB from Schematic"
reads: every part, with the uuids that link it to its footprint. They asked
KiCad's binary for its netlist; this reads this tool's own
(`kicad_cli/fileformat/netlist.py`), whose parts are KiCad's on all 35 of its
demo projects (`tests/test_netlist_read.py`). Nothing here runs KiCad.

Two things a count needs to know, and KiCad's export did not say in the
netlist: whether the annotation has errors -- the export warned of them on
stderr, and the update refuses to run -- and whether a sheet's file is
missing, which the export passes over in silence, leaving that sheet's
parts out.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import envelope, sexpr
from .fileformat import annotation
from .fileformat import netlist as built_netlist
from .fileformat.circuit import Design
from .fileformat.schematic import SchematicError
from .fileformat.sexpr import SexprError


@dataclass
class Read:
    components: list[dict[str, Any]]
    annotation: list[annotation.Problem]
    missing_sheets: list[str]


def read(schematic: Path) -> Read:
    """The design's parts, its annotation errors and its missing sheets.

    E_NOT_FOUND without the schematic, E_VALIDATION when it does not read.
    """
    if not schematic.exists():
        envelope.fail("E_NOT_FOUND", "schematic file does not exist", {"path": str(schematic)})
    try:
        design = Design(schematic)
        found = built_netlist.build(design)
        problems = annotation.problems(design)
    except (SchematicError, SexprError, UnicodeDecodeError, OSError) as exc:
        envelope.fail(
            "E_VALIDATION",
            "the schematic could not be read",
            {"schematic": str(schematic), "reason": str(exc)[:300]},
        )
        raise AssertionError("unreachable") from exc
    return Read([record(c) for c in found.components], problems, list(design.missing))


def record(component: built_netlist.Component) -> dict[str, Any]:
    """A part as `components()` reads it from a netlist file."""
    sheet = component.sheet_tstamps
    return {
        "ref": component.ref,
        "value": component.value,
        "fpid": component.footprint,
        "sheet": sheet,
        "uuids": list(component.tstamps),
        "paths": [join_path(sheet, u) for u in component.tstamps],
        "fields": {name: value for name, value in component.fields},
        "properties": {name: value or "" for name, value in component.properties},
        "unit_names": [name for name, _ in component.units],
    }


def components(node: sexpr.Node) -> list[dict[str, Any]]:
    """One record per component, with every uuid the updater will try.

    ``(tstamps "a" "b" "c")`` carries one uuid per unit of a multi-unit part,
    and KiCad's updater loops over all of them, so the match is against a set
    rather than a value. Reading only the first element -- which is what a
    naive ``value(child(...))`` does -- silently turns a multi-unit part into a
    single-unit one.
    """
    out = []
    for comp in sexpr.walk(node, "comp"):
        ref = sexpr.value(sexpr.child(comp, "ref"))
        if not ref:
            continue
        ts = sexpr.child(comp, "tstamps") or []
        uuids = [u for u in ts[1:] if isinstance(u, str)]
        sheetpath = sexpr.child(comp, "sheetpath")
        sheet = sexpr.value(sexpr.child(sheetpath, "tstamps"), default="/") if sheetpath else "/"

        fields: dict[str, str] = {}
        fnode = sexpr.child(comp, "fields")
        if fnode:
            for f in sexpr.children(fnode, "field"):
                name = sexpr.value(sexpr.child(f, "name"))
                if name:
                    fields[str(name)] = f[2] if len(f) > 2 and isinstance(f[2], str) else ""

        props: dict[str, str] = {}
        for p in sexpr.children(comp, "property"):
            name = sexpr.value(sexpr.child(p, "name"))
            if name:
                props[str(name)] = str(sexpr.value(sexpr.child(p, "value"), default=""))

        units = sexpr.child(comp, "units")
        out.append(
            {
                "ref": str(ref),
                "value": str(sexpr.value(sexpr.child(comp, "value"), default="")),
                "fpid": str(sexpr.value(sexpr.child(comp, "footprint"), default="")),
                "sheet": str(sheet),
                "uuids": uuids,
                "paths": [join_path(sheet, u) for u in uuids],
                "fields": fields,
                "properties": props,
                "unit_names": [
                    str(sexpr.value(sexpr.child(u, "name"), default=""))
                    for u in sexpr.children(units, "unit")
                ]
                if units
                else [],
            }
        )
    return out


def join_path(sheet: str, uuid: str) -> str:
    """The footprint ``path`` KiCad builds: the sheet path plus the symbol uuid."""
    return "/" + "/".join(x for x in f"{sheet}/{uuid}".split("/") if x)


def normalise(path: str) -> str:
    return "/" + "/".join(x for x in str(path).split("/") if x) if path else ""
