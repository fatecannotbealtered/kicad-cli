"""Registrations of the board copper group: via create, zone list, zone create and zone delete
(DCLI-029). Tracks are in board_edit.
"""
from __future__ import annotations

from typing import Any

from .board_edit import INPUT, LENGTH, NM_TOLERANCE, ONLY_ITEMS, POINTS, TOUCHES
from .board_read import BOARD, PAGE, _reg

POINT_LIST = {"type": "array", "items": {"type": "object", "properties": {"x_mm": {"type": "number"}, "y_mm": {"type": "number"}}}}
HOLES = {"type": "array", "items": POINT_LIST, "description": "Holes cut out of the zone, each a list of corners."}


COMMANDS: list[dict[str, Any]] = [
    _reg(
        path="via create",
        summary="Add a through via after a KiCad DRC check.",
        effects=["change"],
        target_kind="via",
        flags=[
            {"name": "--net", "type": "string", "required": True, "summary": "The net name, as the board has it."},
            {"name": "--x", **LENGTH, "summary": "X of the via's centre."},
            {"name": "--y", **LENGTH, "summary": "Y of the via's centre."},
            {"name": "--diameter", "type": "quantity", "quantity": "length",
             "summary": "The copper diameter; default: the net class's via diameter."},
            {"name": "--drill", "type": "quantity", "quantity": "length",
             "summary": "The hole diameter; default: the net class's via drill."},
            {"name": "--input", "type": "input",
             "summary": INPUT + 'with several vias and one DRC check: {"items": [{"net": "GND", "x": "12mm", '
                        '"y": "30mm"}, ...]}.' + ONLY_ITEMS},
        ],
        untrusted_fields=["net", "connects_to"],
        tolerances={"x_mm": NM_TOLERANCE, "y_mm": NM_TOLERANCE, "diameter_mm": NM_TOLERANCE, "drill_mm": NM_TOLERANCE},
        related_knowledge=["files-open-in-kicad", "design-rules-files", "run-checks-in-the-project-folder",
                           "checks-time-limit", "routing-method"],
        output_schema={"type": "object", "properties": {
            "id": {"type": "string"}, "net": {"type": "string"}, "x_mm": {"type": "number"}, "y_mm": {"type": "number"},
            "diameter_mm": {"type": "number"}, "drill_mm": {"type": "number"},
            "layers": {"type": "array", "items": {"type": "string"}},
            "connects_to": {"type": "object", "description": "By copper layer, what the via's copper meets there.",
                            "additionalProperties": TOUCHES}}},
        output_coordinate_system=BOARD,
        examples=["kicad-cli via create --doc D:/work/demo/demo.kicad_pcb --net GND --x 21.6mm --y 13.2mm",
                  "kicad-cli via create --doc D:/work/demo/demo.kicad_pcb --input D:/work/demo/vias.json"],
    ),
    _reg(
        path="zone list",
        summary="List the copper zones on a board, with their outlines.",
        effects=["observe"],
        target_kind="zone",
        flags=[
            {"name": "--net", "type": "string", "summary": "Only this net, such as GND."},
            {"name": "--layer", "type": "string", "summary": "Only zones on this copper layer, such as B.Cu."},
            *PAGE,
        ],
        untrusted_fields=["net", "name"],
        default_fields=["id", "net", "layers", "priority", "points"],
        related_knowledge=["zone-fills"],
        output_schema={"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "net": {"type": "string"}, "layers": {"type": "array", "items": {"type": "string"}},
            "name": {"type": "string"}, "priority": {"type": "integer"}, "keepout": {"type": "boolean"},
            "points": POINT_LIST, "holes": HOLES, "filled_in_file": {"type": "boolean"}, "locked": {"type": "boolean"}}}}}},
        output_coordinate_system=BOARD,
        examples=["kicad-cli zone list --doc D:/work/demo/demo.kicad_pcb --net GND"],
    ),
    _reg(
        path="zone create",
        summary="Add a copper zone outline on one layer after a KiCad DRC check.",
        effects=["change"],
        target_kind="zone",
        flags=[
            {"name": "--net", "type": "string", "required": True, "summary": "The net the zone fills with, such as GND."},
            {"name": "--layer", "type": "string", "required": True, "summary": "The copper layer, such as B.Cu."},
            {"name": "--points", **POINTS, "schema": {**POINTS["schema"], "minItems": 3}, "required": True,
             "summary": 'The outline corners in order, at least 3: [{"x_mm": 10, "y_mm": 10}, ...]. KiCad clips '
                        "the fill to the board outline and fills with its default settings: 0.5 mm from other copper, "
                        "at least 0.25 mm wide, and thermal reliefs with a 0.5 mm gap and 0.5 mm spokes."},
            {"name": "--priority", "type": "integer", "summary": "Fill priority; a higher one fills first. Default 0."},
        ],
        untrusted_fields=["net"],
        tolerances={"points": NM_TOLERANCE},
        related_knowledge=["zone-fills", "files-open-in-kicad", "design-rules-files", "starved-thermal-in-10-0-6",
                           "checks-time-limit", "routing-method"],
        output_schema={"type": "object", "properties": {
            "id": {"type": "string"}, "net": {"type": "string"}, "layer": {"type": "string"},
            "priority": {"type": "integer"}, "points": POINT_LIST}},
        output_coordinate_system=BOARD,
        examples=["kicad-cli zone create --doc D:/work/demo/demo.kicad_pcb --net GND --layer B.Cu --points "
                  "'[{\"x_mm\": 10, \"y_mm\": 10}, {\"x_mm\": 45, \"y_mm\": 10}, {\"x_mm\": 45, \"y_mm\": 37}, "
                  "{\"x_mm\": 10, \"y_mm\": 37}]'"],
    ),
    _reg(
        path="zone delete",
        summary="Delete copper zones by UUID.",
        effects=["change"],
        target_kind="zone",
        flags=[
            {"name": "--id", "type": "string", "required": True, "summary": "The UUID of an unlocked copper zone, from zone list."},
            {"name": "--ids", "type": "list", "summary": "Several zone UUIDs, comma-separated."},
        ],
        untrusted_fields=["deleted"],
        irreversible=True,
        related_knowledge=["files-open-in-kicad"],
        output_schema={"type": "object", "properties": {
            "deleted": {"type": "array", "items": {"type": "object", "properties": {
                "id": {"type": "string"}, "net": {"type": "string"}, "layers": {"type": "array"},
                "points": POINT_LIST, "holes": HOLES}}}}},
        output_coordinate_system=BOARD,
        examples=["kicad-cli zone delete --doc D:/work/demo/demo.kicad_pcb --id 77fa30a7-0951-4e87-89fe-2cd8a8e297fb"],
    ),
]
