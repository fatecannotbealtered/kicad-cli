"""Registrations of the board read and output group (DCLI-029).

board create, board check, board export, board render, footprint list, footprint create and
track list. Checks and exports run KiCad's own program on the saved board.
"""
from __future__ import annotations

from typing import Any

BOARD = "board: origin at the top-left corner of the page, x to the right, y down, in mm"
PAGE = [
    {"name": "--limit", "type": "integer", "summary": "Items per page; default 50."},
    {"name": "--offset", "type": "integer", "summary": "Items to skip, from next_offset of the previous page."},
]

COURTYARD = {"type": ["object", "null"], "description": "The box around the courtyard on the board.",
             "properties": {k: {"type": "number"} for k in ("min_x_mm", "min_y_mm", "max_x_mm", "max_y_mm")}}
REFERENCE_TEXT = {"type": ["object", "null"],
                  "description": "The reference text's anchor (its centre unless the text is justified otherwise), "
                                 "angle and layer.",
                  "properties": {"x_mm": {"type": "number"}, "y_mm": {"type": "number"}, "rotation_deg": {"type": "number"},
                                 "layer": {"type": "string"}, "hidden": {"type": "boolean"}}}
PAIR_MM = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2, "description": "Two numbers."}
FOOTPRINT_DEFINITION = {
    "type": "object", "required": ["name", "body_mm", "courtyard_mm", "pads"], "additionalProperties": False,
    "properties": {
        "name": {"type": "string", "description": "The footprint name, such as SOT-23-5_Demo."},
        "body_mm": {**PAIR_MM, "description": "The body rectangle [width, height], centred on the origin."},
        "courtyard_mm": {"type": "number", "minimum": 0, "description": "Free space from body and pads to the courtyard line."},
        "pads": {"type": "array", "minItems": 1, "items": {
            "type": "object", "required": ["number", "at_mm", "size_mm"], "additionalProperties": False,
            "properties": {"number": {"type": "string"}, "at_mm": {**PAIR_MM, "description": "Pad centre [x, y], y down."},
                           "size_mm": {**PAIR_MM, "description": "Pad size [width, height]."}}}},
    },
}


def _reg(**fields: Any) -> dict[str, Any]:
    base = {"modes": ["file"], "untrusted_fields": [], "irreversible": False, "halt": False, "tolerances": {},
            "default_fields": [], "related_knowledge": [], "maturity": "draft", "evidence": []}
    return {**base, **fields}


COMMANDS: list[dict[str, Any]] = [
    _reg(
        path="board create",
        summary="Create a board from the schematic, with a fresh netlist from KiCad.",
        effects=["artifact"],
        target_kind="board",
        flags=[
            {"name": "--output", "type": "path", "output": True,
             "summary": "The new .kicad_pcb; default: the schematic's name with .kicad_pcb."},
            {"name": "--width", "type": "quantity", "quantity": "length",
             "summary": "Outline width; with --height. Default: the parts' extent plus 2 mm on each side."},
            {"name": "--height", "type": "quantity", "quantity": "length", "summary": "Outline height; with --width."},
            {"name": "--pitch", "type": "quantity", "quantity": "length",
             "summary": "Grid spacing between part origins; default 10mm."},
        ],
        untrusted_fields=["unmatched_pins", "skipped"],
        related_knowledge=["design-flow", "library-changes-do-not-refresh-instances"],
        output_schema={"type": "object", "properties": {
            "board": {"type": "string"}, "footprints": {"type": "integer"}, "nets": {"type": "integer"},
            "pads_connected": {"type": "integer"}, "unmatched_pins": {"type": "array"},
            "width_mm": {"type": "number"}, "height_mm": {"type": "number"}, "placement": {"type": "string"},
            "skipped": {"type": "array"}}},
        examples=["kicad-cli board create --doc D:/work/demo/demo.kicad_sch",
                  "kicad-cli board create --doc D:/work/demo/demo.kicad_pro --width 50mm --height 40mm"],
    ),
    _reg(
        path="board check",
        summary="Run KiCad's DRC on a board, with schematic parity.",
        effects=["observe"],
        target_kind="board",
        flags=[
            {"name": "--severity", "type": "string", "summary": "error, warning or all (default): which findings to list."},*PAGE],
        untrusted_fields=["description", "items"],
        default_fields=["group", "type", "severity", "description", "items"],
        related_knowledge=["run-checks-in-the-project-folder", "zone-fills", "checks-are-not-sign-off", "starved-thermal-in-10-0-6", "design-rules-files", "checks-time-limit"],
        output_schema={"type": "object", "properties": {
            "passed": {"type": "boolean"}, "errors": {"type": "integer"}, "warnings": {"type": "integer"},
            "summary": {"type": "object"}, "schematic_parity_checked": {"type": "boolean"},
            "zones_refilled": {"type": "boolean"}, "kicad_version": {"type": "string"},
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "group": {"type": "string"}, "type": {"type": "string"}, "severity": {"type": "string"},
                "excluded": {"type": "boolean"}, "description": {"type": "string"}, "items": {"type": "array"}}}}}},
        output_coordinate_system=BOARD,
        examples=["kicad-cli board check --doc D:/work/demo/demo.kicad_pcb"],
    ),
    _reg(
        path="board export",
        summary="Export Gerber or drill files with KiCad.",
        effects=["artifact"],
        target_kind="folder",
        flags=[
            {"name": "--file-type", "type": "string", "required": True, "summary": "gerber or drill."},
            {"name": "--output", "type": "path", "output": True, "required": True,
             "summary": "The folder for the files; made when missing."},
        ],
        related_knowledge=["zone-fills", "checks-are-not-sign-off", "plot-origin"],
        output_schema={"type": "object", "properties": {
            "file_type": {"type": "string"}, "folder": {"type": "string"}, "files": {"type": "array"}}},
        examples=["kicad-cli board export --doc D:/work/demo/demo.kicad_pcb --file-type gerber --output D:/work/demo/fab"],
    ),
    _reg(
        path="board render",
        summary="Plot board layers to PDF or SVG with KiCad.",
        effects=["artifact"],
        target_kind="file",
        flags=[
            {"name": "--file-type", "type": "string", "required": True, "summary": "pdf or svg."},
            {"name": "--output", "type": "path", "output": True, "required": True,
             "summary": "The file to write, ending in .pdf or .svg."},
            {"name": "--layers", "type": "string",
             "summary": "Comma-separated layer names; default F.Cu,B.Cu,F.SilkS,B.SilkS,Edge.Cuts."},
        ],
        related_knowledge=["plot-origin", "zone-fills"],
        output_schema={"type": "object", "properties": {
            "file_type": {"type": "string"}, "file": {"type": "string"}, "layers": {"type": "array"}}},
        examples=["kicad-cli board render --doc D:/work/demo/demo.kicad_pcb --file-type pdf --output D:/work/demo/board.pdf"],
    ),
    _reg(
        related_knowledge=["design-flow"],
        path="footprint list",
        summary="List the footprints on a board: position, rotation, side, lock.",
        effects=["observe"],
        target_kind="footprint",
        flags=[
            {"name": "--query", "type": "string", "summary": "Part of a reference or value, such as U or 10k."},
            {"name": "--side", "type": "string", "summary": "front or back."},
            *PAGE,
        ],
        untrusted_fields=["id", "value", "lib_id", "pads"],
        default_fields=["id", "value", "x_mm", "y_mm", "rotation_deg", "side"],
        output_schema={"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "uuid": {"type": "string"}, "value": {"type": "string"},
            "lib_id": {"type": "string"},
            "x_mm": {"type": "number", "description": "The footprint's origin, which footprint move sets: often pad 1, "
                                                      "not the middle of the part."},
            "y_mm": {"type": "number"},
            "rotation_deg": {"type": "number"}, "side": {"type": "string"}, "locked": {"type": "boolean"},
            "courtyard": COURTYARD,
            "pads": {"type": "array", "items": {"type": "object", "properties": {
                "number": {"type": "string"}, "net": {"type": "string"}, "x_mm": {"type": "number"},
                "y_mm": {"type": "number"}, "type": {"enum": ["smd", "thru_hole", "np_thru_hole", "connect"]},
                "width_mm": {"type": "number", "description": "Width of the box the pad's copper takes, as placed."},
                "height_mm": {"type": "number"}}}},
            "reference_text": REFERENCE_TEXT}}}}},
        output_coordinate_system=BOARD,
        examples=["kicad-cli footprint list --doc D:/work/demo/demo.kicad_pcb --query U",
                  "kicad-cli footprint list --doc D:/work/demo/demo.kicad_pcb --fields id,courtyard,pads,reference_text"],
    ),
    _reg(
        path="footprint create",
        summary="Define an SMD footprint in a project footprint library.",
        effects=["artifact", "change"],
        target_kind="footprint",
        flags=[
            {"name": "--definition", "type": "json", "required": True, "schema": FOOTPRINT_DEFINITION,
             "summary": "JSON, inline or a file: name, body_mm [w,h], courtyard_mm, pads [{number, at_mm, size_mm}]."},
        ],
        untrusted_fields=["id"],
        related_knowledge=["library-changes-do-not-refresh-instances", "multi-file-writes-are-not-atomic"],
        output_schema={"type": "object", "properties": {
            "id": {"type": "string"}, "file": {"type": "string"},
            "pads": {"type": "array", "items": FOOTPRINT_DEFINITION["properties"]["pads"]["items"]},
            "registered": {"type": "boolean"}}},
        examples=["kicad-cli footprint create --doc D:/work/demo/Demo.pretty --definition D:/work/demo/sot23-5.json",
                  "kicad-cli footprint create --doc D:/work/demo/Demo.pretty --definition '{\"name\": \"PAD2\", "
                  "\"body_mm\": [2, 1.2], \"courtyard_mm\": 0.25, \"pads\": [{\"number\": \"1\", \"at_mm\": [-0.75, 0], "
                  "\"size_mm\": [0.8, 1]}, {\"number\": \"2\", \"at_mm\": [0.75, 0], \"size_mm\": [0.8, 1]}]}'"],
    ),
    _reg(
        related_knowledge=["design-flow"],
        path="track list",
        summary="List the tracks and vias on a board.",
        effects=["observe"],
        target_kind="track",
        flags=[
            {"name": "--net", "type": "string", "summary": "Only this net, such as GND."},
            {"name": "--layer", "type": "string", "summary": "Only this copper layer, such as F.Cu."},
            *PAGE,
        ],
        untrusted_fields=["net", "layer"],
        default_fields=["id", "type", "net", "layer", "width_mm", "length_mm"],
        output_schema={"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "type": {"type": "string"}, "net": {"type": "string"}, "layer": {"type": "string"},
            "width_mm": {"type": "number"}, "length_mm": {"type": "number"},
            "start_x_mm": {"type": "number"}, "start_y_mm": {"type": "number"},
            "end_x_mm": {"type": "number"}, "end_y_mm": {"type": "number"},
            "x_mm": {"type": "number"}, "y_mm": {"type": "number"}, "diameter_mm": {"type": "number"},
            "drill_mm": {"type": "number"}, "via_type": {"type": "string"}, "locked": {"type": "boolean"}}}}}},
        output_coordinate_system=BOARD,
        examples=["kicad-cli track list --doc D:/work/demo/demo.kicad_pcb --net GND"],
    ),
]
