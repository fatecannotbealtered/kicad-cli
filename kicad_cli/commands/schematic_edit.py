"""Registrations of the schematic edit group: symbol create/update/move/delete, pin update,
net update (DCLI-029)."""
from __future__ import annotations

from typing import Any

from ..adapter.sch_edit_library import PIN_TYPES

SHEET = "schematic sheet: origin at the top-left corner of the page, x to the right, y down; 1.27 mm grid"
SCH_TOLERANCES = {"x_mm": 0.0001, "y_mm": 0.0001, "rotation_deg": 0}
RESULT = {"type": "object", "properties": {"id": {}, "files": {"type": "array", "items": {"type": "string"}}}}
SELECT_ONE = {"name": "--id", "type": "string", "required": True, "summary": "The symbol's reference, such as R1."}
IDS = {"name": "--ids", "type": "list", "summary": "Several references, such as R1,R2: the same values for each."}
AS_STORED = {"type": "object", "description": "Each target, by label, as the written file now holds it; a symbol with "
                                            "its fields, position, rotation and the net of each pin."}
EFFECTS = {"type": "array", "items": {"type": "object"},
           "description": "What the change did besides its targets, such as wires and labels removed or moved with a symbol."}
PAIR_MM = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2, "description": "Two numbers."}
SYMBOL_DEFINITION = {
    "type": "object", "required": ["name", "reference", "body_mm", "pins"], "additionalProperties": False,
    "properties": {
        "name": {"type": "string"}, "reference": {"type": "string", "description": "The designator prefix, such as U."},
        "value": {"type": "string"}, "footprint": {"type": "string", "description": "LIBRARY:NAME."},
        "datasheet": {"type": "string"}, "description": {"type": "string"},
        "body_mm": {**PAIR_MM, "description": "The body rectangle [width, height], centred on the origin."},
        "pins": {"type": "array", "minItems": 1, "items": {
            "type": "object", "required": ["number", "type", "at_mm"], "additionalProperties": False,
            "properties": {
                "number": {"type": "string"}, "name": {"type": "string"}, "type": {"type": "string", "enum": list(PIN_TYPES)},
                "at_mm": {**PAIR_MM, "description": "The connection point [x, y], y up, on the 1.27 mm grid."},
                "angle_deg": {"type": "number", "enum": [0, 90, 180, 270], "description": "The direction towards the body."},
                "length_mm": {"type": "number", "exclusiveMinimum": 0}}}},
    },
}
INPUT = {"name": "--input", "type": "input",
         "summary": 'A JSON file or - with {"items": [{"id": "R1", ...}, ...]}: values per symbol.'}


def _command(path: str, summary: str, target_kind: str, flags: list[dict[str, Any]], **more: Any) -> dict[str, Any]:
    reg = {
        "path": path,
        "summary": summary,
        "effects": ["change"],
        "modes": ["file"],
        "target_kind": target_kind,
        "flags": flags,
        "untrusted_fields": [],
        "irreversible": False,
        "halt": False,
        "tolerances": {},
        "default_fields": [],
        "related_knowledge": ["files-open-in-kicad"],
        "output_schema": RESULT,
        "examples": [],
        "maturity": "draft",
        "evidence": [],
    }
    reg.update(more)
    # The result also holds each target as written and the engine's account of its effects (DCLI-068).
    reg["output_schema"] = {**reg["output_schema"], "properties": {
        **reg["output_schema"]["properties"], "as_stored": AS_STORED, "effects": EFFECTS}}
    reg["untrusted_fields"] = sorted({*reg["untrusted_fields"], "as_stored", "effects"})
    return reg


COMMANDS: list[dict[str, Any]] = [
    _command(
        "symbol create",
        "Place a library symbol on a sheet, or define one in a project symbol library.",
        "symbol",
        [
            {"name": "--library-id", "type": "string", "summary": "The library symbol to place, LIBRARY:NAME, such as Device:R."},
            {"name": "--reference", "type": "string",
             "summary": "The new reference, such as R5, or a prefix and ? for the next free number; default: the symbol's own prefix and ?."},
            {"name": "--value", "type": "string", "summary": "The Value field; default: the library symbol's."},
            {"name": "--footprint", "type": "string", "summary": "The Footprint field, LIBRARY:NAME; default: the library symbol's."},
            {"name": "--x", "type": "quantity", "quantity": "length", "summary": "X of the symbol's anchor, with --y.",
             "coordinate_system": SHEET},
            {"name": "--y", "type": "quantity", "quantity": "length", "summary": "Y of the symbol's anchor, with --x.",
             "coordinate_system": SHEET},
            {"name": "--near", "type": "string", "summary": "Without --x/--y: place it beside this reference."},
            {"name": "--sheet", "type": "string", "summary": "The sheet path to place it on, such as /power/; default: the root sheet."},
            {"name": "--definition", "type": "json", "schema": SYMBOL_DEFINITION,
             "summary": "With a .kicad_sym --doc: the symbol as JSON, inline or a file (name, reference, body_mm, pins)."},
        ],
        effects=["artifact", "change"],
        untrusted_fields=["sheet", "also_added"],
        tolerances={"x_mm": 0.0001, "y_mm": 0.0001},
        related_knowledge=["schematic-grid", "files-open-in-kicad", "library-changes-do-not-refresh-instances",
                           "multi-file-writes-are-not-atomic"],
        output_schema={"type": "object", "properties": {
            "id": {"type": "string"}, "library_id": {"type": "string"}, "sheet": {"type": "string"},
            "x_mm": {"type": "number"}, "y_mm": {"type": "number"}, "also_added": {"type": "array"},
            "library": {"type": "string"}, "registered": {"type": "boolean"},
            "files": {"type": "array", "items": {"type": "string"}}}},
        examples=[
            "kicad-cli symbol create --doc D:/work/demo/demo.kicad_sch --library-id Device:R --value 10k --x 50.8mm --y 30.48mm",
            "kicad-cli symbol create --doc D:/work/demo/Demo.kicad_sym --definition D:/work/demo/buffer.json",
        ],
    ),
    _command(
        "symbol update",
        "Change symbols' fields and flags.",
        "symbol",
        [
            SELECT_ONE, IDS, INPUT,
            {"name": "--value", "type": "string", "summary": "The Value field."},
            {"name": "--footprint", "type": "string", "summary": "The Footprint field, LIBRARY:NAME."},
            {"name": "--datasheet", "type": "string", "summary": "The Datasheet field."},
            {"name": "--description", "type": "string", "summary": "The Description field."},
            {"name": "--field-name", "type": "string", "summary": "Another field to set, with --field-value, such as MPN."},
            {"name": "--field-value", "type": "string", "summary": "The value of --field-name; an empty value empties it."},
            {"name": "--dnp", "type": "boolean", "summary": "Do not populate."},
            {"name": "--in-bom", "type": "boolean", "summary": "Listed in the bill of materials."},
            {"name": "--on-board", "type": "boolean", "summary": "Placed on the board."},
            {"name": "--exclude-from-sim", "type": "boolean", "summary": "Left out of simulation."},
        ],
        related_knowledge=["files-open-in-kicad", "library-changes-do-not-refresh-instances"],
        examples=[
            "kicad-cli symbol update --doc D:/work/demo/demo.kicad_sch --id R1 --value 4k7",
            "kicad-cli symbol update --doc D:/work/demo/demo.kicad_sch --ids R1,R2 --footprint Resistor_SMD:R_0603_1608Metric",
        ],
    ),
    _command(
        "symbol move",
        "Move or rotate a symbol with its own wires and labels.",
        "symbol",
        [
            SELECT_ONE, INPUT,
            {"name": "--x", "type": "quantity", "quantity": "length", "summary": "New X of the symbol's anchor, with --y.",
             "coordinate_system": SHEET},
            {"name": "--y", "type": "quantity", "quantity": "length", "summary": "New Y of the symbol's anchor, with --x.",
             "coordinate_system": SHEET},
            {"name": "--rotation", "type": "quantity", "quantity": "angle",
             "summary": "New rotation: 0, 90, 180 or 270 degrees, counter-clockwise on screen."},
        ],
        tolerances=dict(SCH_TOLERANCES),
        related_knowledge=["schematic-grid", "files-open-in-kicad"],
        output_schema={"type": "object", "properties": {
            "id": {}, "moved_with": {"type": "object"}, "files": {"type": "array", "items": {"type": "string"}}}},
        examples=["kicad-cli symbol move --doc D:/work/demo/demo.kicad_sch --id C3 --x 63.5mm --y 40.64mm --rotation 90deg"],
    ),
    _command(
        "symbol delete",
        "Delete symbols with the wires and labels only they use.",
        "symbol",
        [SELECT_ONE, IDS],
        irreversible=True,
        related_knowledge=["files-open-in-kicad"],
        examples=["kicad-cli symbol delete --doc D:/work/demo/demo.kicad_sch --id R7"],
    ),
    _command(
        "pin update",
        "Connect a pin to a net, or mark it not connected.",
        "pin",
        [
            {"name": "--id", "type": "string", "required": True, "summary": "The pin, REFERENCE.NUMBER, such as U1.3."},
            {"name": "--ids", "type": "list", "summary": "Several pins, such as U1.6,U1.7: the same change for each."},
            {"name": "--input", "type": "input",
             "summary": 'A JSON file or - with {"items": [{"id": "U1.3", "net": "SDA"}, ...]}: a change per pin.'},
            {"name": "--net", "type": "string", "summary": "The net to join, such as GND or SDA; a new name makes a new net."},
            {"name": "--no-connect", "type": "boolean", "summary": "Disconnect the pin and mark it as not connected."},
        ],
        untrusted_fields=["net"],
        related_knowledge=["net-names-and-labels", "schematic-grid", "files-open-in-kicad"],
        output_schema={"type": "object", "properties": {
            "id": {}, "net": {"type": "string"}, "files": {"type": "array", "items": {"type": "string"}}}},
        examples=[
            "kicad-cli pin update --doc D:/work/demo/demo.kicad_sch --id U1.3 --net SDA",
            "kicad-cli pin update --doc D:/work/demo/demo.kicad_sch --ids U1.6,U1.7 --no-connect",
        ],
    ),
    _command(
        "net update",
        "Rename a net and every label that names it.",
        "net",
        [
            {"name": "--id", "type": "string", "required": True, "summary": "The net's name now, as net list shows it."},
            {"name": "--name", "type": "string", "summary": "The new name."},
        ],
        untrusted_fields=["id"],
        related_knowledge=["net-names-and-labels", "files-open-in-kicad"],
        output_schema={"type": "object", "properties": {
            "id": {"type": "string"}, "name": {"type": "string"}, "files": {"type": "array", "items": {"type": "string"}}}},
        examples=["kicad-cli net update --doc D:/work/demo/demo.kicad_sch --id /SIG --name SENSE"],
    ),
]
