"""Registrations of the schematic read group (DCLI-029): schematic create, check, export and
render; symbol list and get; net list."""
from __future__ import annotations

from typing import Any

PAGE = [
    {"name": "--limit", "type": "integer", "summary": "Items per page; default 50."},
    {"name": "--offset", "type": "integer", "summary": "Items to skip, from next_offset of the previous page."},
]
SHEET = "schematic sheet: origin at the top-left corner of the page, x to the right, y down"
FILES_SCHEMA = {"type": "object", "properties": {
    "file_type": {"type": "string"}, "output": {"type": "string"}, "files": {"type": "array", "items": {"type": "string"}}}}


def _command(**fields: Any) -> dict[str, Any]:
    base = {"modes": ["file"], "irreversible": False, "halt": False, "tolerances": {}, "default_fields": [],
            "related_knowledge": [], "untrusted_fields": [], "maturity": "draft", "evidence": []}
    return {**base, **fields}


COMMANDS: list[dict[str, Any]] = [
    _command(
        path="schematic create",
        summary="Create an empty schematic, and its project file when there is none.",
        effects=["artifact"],
        target_kind="schematic",
        flags=[
            {"name": "--output", "type": "path", "output": True,
             "summary": "The new .kicad_sch; default: the project's name with .kicad_sch. --doc is the .kicad_pro, made when missing."},
            {"name": "--title", "type": "string", "summary": "The title block's title; default the file name."},
            {"name": "--paper", "type": "string",
             "summary": "Page size: A5, A4 (default), A3, A2, A1, A0, A, B, C, D, E, USLetter, USLegal or USLedger."},
        ],
        related_knowledge=["files-open-in-kicad", "multi-file-writes-are-not-atomic", "design-flow"],
        output_schema={"type": "object", "properties": {
            "schematic": {"type": "string"}, "project": {"type": "string"},
            "created": {"type": "array", "items": {"type": "string"}}}},
        examples=["kicad-cli schematic create --doc D:/work/demo/demo.kicad_pro --title Demo --paper A4",
                  "kicad-cli schematic create --doc D:/work/demo/demo.kicad_pro --output D:/work/demo/power.kicad_sch"],
    ),
    _command(
        path="schematic check",
        summary="Run KiCad's electrical rules check (ERC) on a schematic.",
        effects=["observe"],
        target_kind="schematic",
        flags=[
            {"name": "--severity", "type": "string", "summary": "error, warning or all (default)."},
            *PAGE,
        ],
        default_fields=["type", "severity", "description", "sheet"],
        related_knowledge=["run-checks-in-the-project-folder", "checks-are-not-sign-off", "checks-time-limit"],
        untrusted_fields=["description", "sheet", "objects"],
        output_schema={"type": "object", "properties": {
            "kicad_version": {"type": "string"}, "error_count": {"type": "integer"},
            "warning_count": {"type": "integer"}, "exclusion_count": {"type": "integer"},
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "type": {"type": "string"}, "severity": {"type": "string"}, "description": {"type": "string"},
                "sheet": {"type": "string"}, "objects": {"type": "array"}}}}}},
        examples=["kicad-cli schematic check --doc D:/work/demo/demo.kicad_sch --severity error"],
    ),
    _command(
        path="schematic export",
        summary="Export a schematic's netlist with KiCad.",
        effects=["artifact"],
        target_kind="netlist",
        flags=[
            {"name": "--file-type", "type": "string", "required": True, "summary": "netlist."},
            {"name": "--output", "type": "path", "output": True, "required": True,
             "summary": "The file to write, ending in .net."},
        ],
        related_knowledge=["design-flow"],
        output_schema=FILES_SCHEMA,
        examples=["kicad-cli schematic export --doc D:/work/demo/demo.kicad_sch --file-type netlist "
                  "--output D:/work/demo/out/demo.net"],
    ),
    _command(
        path="schematic render",
        summary="Plot a schematic to PDF or SVG with KiCad.",
        effects=["artifact"],
        target_kind="plot",
        flags=[
            {"name": "--file-type", "type": "string", "required": True, "summary": "pdf or svg."},
            {"name": "--output", "type": "path", "output": True, "required": True,
             "summary": "pdf: the file to write, ending in .pdf; svg: the folder to write into."},
        ],
        output_schema=FILES_SCHEMA,
        examples=["kicad-cli schematic render --doc D:/work/demo/demo.kicad_sch --file-type pdf "
                  "--output D:/work/demo/out/demo.pdf"],
    ),
    _command(
        path="symbol list",
        summary="List the symbols placed on a schematic and its sheets.",
        effects=["observe"],
        target_kind="symbol",
        flags=[
            {"name": "--query", "type": "string", "summary": "Part of a reference, value, library symbol or footprint."},
            {"name": "--include-power", "type": "boolean", "summary": "Also list power symbols (#PWR, #FLG)."},
            *PAGE,
        ],
        default_fields=["reference", "value", "footprint", "lib_id", "sheet"],
        related_knowledge=["schematic-grid"],
        untrusted_fields=["reference", "value", "footprint", "lib_id", "sheet"],
        output_schema={"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
            "reference": {"type": "string"}, "value": {"type": "string"}, "footprint": {"type": "string"},
            "lib_id": {"type": "string"}, "unit": {"type": "integer"}, "sheet": {"type": "string"},
            "x_mm": {"type": "number"}, "y_mm": {"type": "number"}, "rotation_deg": {"type": "number"},
            "mirror": {"type": ["string", "null"]}, "uuid": {"type": "string"}, "dnp": {"type": "boolean"},
            "in_bom": {"type": "boolean"}, "on_board": {"type": "boolean"}}}}}},
        output_coordinate_system=SHEET,
        examples=["kicad-cli symbol list --doc D:/work/demo/demo.kicad_sch --query R"],
    ),
    _command(
        path="symbol get",
        summary="Read one symbol: fields, units, pins and their nets.",
        effects=["observe"],
        target_kind="symbol",
        flags=[{"name": "--id", "type": "string", "required": True,
                "summary": "A reference such as R1, a symbol UUID, or a library symbol name."}],
        related_knowledge=["net-names-and-labels"],
        untrusted_fields=["reference", "lib_id", "value", "footprint", "fields", "units", "name", "extends", "pins",
                          "library"],
        output_schema={"type": "object", "properties": {
            "reference": {"type": "string"}, "lib_id": {"type": "string"}, "value": {"type": "string"},
            "footprint": {"type": "string"}, "fields": {"type": "object"}, "unit_count": {"type": "integer"},
            "units": {"type": "array"}, "name": {"type": "string"}, "pins": {"type": "array"}}},
        output_coordinate_system=SHEET,
        examples=["kicad-cli symbol get --doc D:/work/demo/demo.kicad_sch --id U1",
                  "kicad-cli symbol get --doc D:/work/demo/Demo.kicad_sym --id DemoPart"],
    ),
    _command(
        path="net list",
        summary="List the nets and their pins or pads.",
        effects=["observe"],
        target_kind="net",
        flags=[
            {"name": "--query", "type": "string", "summary": "Part of a net name."},
            {"name": "--include-unconnected", "type": "boolean", "summary": "Also list unconnected-(...) nets."},
            *PAGE,
        ],
        default_fields=["name", "pin_count", "pins"],
        related_knowledge=["net-names-and-labels"],
        untrusted_fields=["name", "pins"],
        output_schema={"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "pin_count": {"type": "integer"},
            "pins": {"type": "array", "items": {"type": "string"}}, "no_connect": {"type": "boolean"},
            "pins_truncated": {"type": "boolean"}}}}}},
        examples=["kicad-cli net list --doc D:/work/demo/demo.kicad_sch --query VCC",
                  "kicad-cli net list --doc D:/work/demo/demo.kicad_pcb"],
    ),
]
