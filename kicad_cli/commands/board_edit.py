"""Registrations of the board edit group: netlist import, footprint move, track create,
track delete (DCLI-029)."""
from __future__ import annotations

from typing import Any

from .board_read import BOARD, COURTYARD

DOC_NOTE = "--doc is the board (.kicad_pcb)."
LENGTH = {"type": "quantity", "quantity": "length", "coordinate_system": BOARD}
NM_TOLERANCE = 0.000001  # the board file stores millimetres to 1 nm
TOUCHES = {"type": "array", "items": {"type": "string"},
           "description": "What the copper meets on the same layer, such as pad R1.2, track <uuid>, via <uuid> or zone GND."}
TRACK_END = {"type": "object", "properties": {"x_mm": {"type": "number"}, "y_mm": {"type": "number"},
                                              "connects_to": TOUCHES}}
INPUT = "A JSON file, or - for stdin, "
ONLY_ITEMS = " Every value goes in the items; give no other flag but --doc."
POINTS = {"type": "json", "coordinate_system": BOARD, "schema": {
    "type": "array", "minItems": 2, "items": {
        "type": "object", "required": ["x_mm", "y_mm"], "additionalProperties": False,
        "properties": {"x_mm": {"type": "number"}, "y_mm": {"type": "number"}}}}}

COMMANDS: list[dict[str, Any]] = [
    {
        "path": "netlist import",
        "summary": "Update the board from its schematic; never deletes footprints.",
        "effects": ["change"],
        "modes": ["file"],
        "target_kind": "board",
        "flags": [
            {"name": "--schematic", "type": "path",
             "summary": "The root schematic; default: the .kicad_sch beside the board with the same name."},
        ],
        "untrusted_fields": ["board", "schematic", "added", "changed", "renamed_nets", "copper_left_on_old_nets",
                             "not_removed", "problems"],
        "irreversible": False,
        "halt": False,
        "tolerances": {},
        "default_fields": [],
        "related_knowledge": ["files-open-in-kicad", "library-changes-do-not-refresh-instances"],
        "output_schema": {"type": "object", "properties": {
            "board": {"type": "string"}, "schematic": {"type": "string"}, "counts": {"type": "object"},
            "added": {"type": "array"}, "changed": {"type": "object"}, "renamed_nets": {"type": "array"},
            "copper_left_on_old_nets": {"type": "array"}, "not_removed": {"type": "array"},
            "problems": {"type": "array"}, "note": {"type": "string"}}},
        "examples": ["kicad-cli netlist import --doc D:/work/demo/demo.kicad_pcb"],
        "maturity": "draft",
        "evidence": [],
    },
    {
        "path": "footprint move",
        "summary": "Move or turn footprints on a board after a KiCad DRC check.",
        "effects": ["change"],
        "modes": ["file"],
        "target_kind": "footprint",
        "flags": [
            {"name": "--id", "type": "string", "required": True,
             "summary": "Reference of an unlocked footprint, such as R1; tracks do not follow."},
            {"name": "--x", **LENGTH, "summary": "The new X position of the footprint's origin."},
            {"name": "--y", **LENGTH, "summary": "The new Y position of the footprint's origin."},
            {"name": "--rotation", "type": "quantity", "quantity": "angle",
             "summary": "The new angle about the origin, counterclockwise as KiCad shows it with y down: a pad "
                        "at (dx, dy) from the origin goes to (dx*cos a + dy*sin a, -dx*sin a + dy*cos a), so at "
                        "90deg a pad 1 mm right of the origin ends 1 mm above it. Such as 90deg."},
            {"name": "--input", "type": "input",
             "summary": INPUT + 'with several moves and one DRC check: {"items": [{"id": "R1", "x": "12.5mm", '
                        '"y": "30mm", "rotation": "90deg"}, ...]}.' + ONLY_ITEMS},
        ],
        "untrusted_fields": ["moved"],
        "irreversible": False,
        "halt": False,
        "tolerances": {"x_mm": NM_TOLERANCE, "y_mm": NM_TOLERANCE, "rotation_deg": 0.000001},
        "default_fields": [],
        "related_knowledge": ["files-open-in-kicad", "design-rules-files", "run-checks-in-the-project-folder",
                              "checks-time-limit", "placement-method"],
        "output_schema": {"type": "object", "properties": {
            "moved": {"type": "array", "items": {"type": "object", "properties": {
                "id": {"type": "string"}, "x_mm": {"type": "number"}, "y_mm": {"type": "number"},
                "rotation_deg": {"type": "number"}, "layer": {"type": "string"},
                "from_x_mm": {"type": "number"}, "from_y_mm": {"type": "number"}, "from_rotation_deg": {"type": "number"},
                "courtyard": COURTYARD,
                "pads": {"type": "array", "items": {"type": "object", "properties": {
                    "number": {"type": "string"}, "net": {"type": "string"}, "x_mm": {"type": "number"},
                    "y_mm": {"type": "number"}, "connects_to": TOUCHES}}},
                "tracks_left_behind": {"type": "array", "items": {"type": "string"},
                                       "description": "Tracks and vias that met the footprint's pads before the move "
                                                      "and no longer do."}}}},
            "note": {"type": "string"}}},
        "output_coordinate_system": BOARD,
        "examples": ["kicad-cli footprint move --doc D:/work/demo/demo.kicad_pcb --id R1 --x 30mm --y 20mm",
                     "kicad-cli footprint move --doc D:/work/demo/demo.kicad_pcb --id J1 --rotation 90deg",
                     "kicad-cli footprint move --doc D:/work/demo/demo.kicad_pcb --input D:/work/demo/moves.json"],
        "maturity": "draft",
        "evidence": [],
    },
    {
        "path": "track create",
        "summary": "Add a straight track or a path of straight segments after a KiCad DRC check.",
        "effects": ["change"],
        "modes": ["file"],
        "target_kind": "track",
        "flags": [
            {"name": "--net", "type": "string", "required": True, "summary": "The net name, as the board has it."},
            {"name": "--layer", "type": "string", "required": True, "summary": "The copper layer, such as F.Cu; no vias or arcs."},
            {"name": "--width", "type": "quantity", "quantity": "length",
             "summary": "The track width; default: the net class's track width."},
            {"name": "--x1", **LENGTH, "summary": "X of the start of one segment, with --y1, --x2 and --y2."},
            {"name": "--y1", **LENGTH, "summary": "Y of the start."},
            {"name": "--x2", **LENGTH, "summary": "X of the end."},
            {"name": "--y2", **LENGTH, "summary": "Y of the end."},
            {"name": "--points", **POINTS,
             "summary": 'Instead of --x1..--y2, a path through these corners: [{"x_mm": 10, "y_mm": 20}, ...].'},
            {"name": "--input", "type": "input",
             "summary": INPUT + 'with several tracks and one DRC check: {"items": [{"net": "GND", "layer": "F.Cu", '
                        '"width": "0.5mm", "points": [...]}, ...]}.' + ONLY_ITEMS},
        ],
        "untrusted_fields": ["net", "layer", "start", "end"],
        "irreversible": False,
        "halt": False,
        "tolerances": {"width_mm": NM_TOLERANCE, "x1_mm": NM_TOLERANCE, "y1_mm": NM_TOLERANCE,
                       "x2_mm": NM_TOLERANCE, "y2_mm": NM_TOLERANCE, "points": NM_TOLERANCE},
        "default_fields": [],
        "related_knowledge": ["files-open-in-kicad", "design-rules-files", "run-checks-in-the-project-folder",
                              "checks-time-limit", "routing-method"],
        "output_schema": {"type": "object", "properties": {
            "id": {"type": "string"}, "net": {"type": "string"}, "layer": {"type": "string"},
            "width_mm": {"type": "number"}, "segments": {"type": "array", "items": {"type": "string"}},
            "start": TRACK_END, "end": TRACK_END,
            "unconnected_ends": {"type": "array", "items": {"enum": ["start", "end"]}}}},
        "output_coordinate_system": BOARD,
        "examples": ["kicad-cli track create --doc D:/work/demo/demo.kicad_pcb --net GND --layer F.Cu "
                     "--x1 10mm --y1 20mm --x2 15mm --y2 20mm",
                     "kicad-cli track create --doc D:/work/demo/demo.kicad_pcb --net +5V --layer F.Cu --width 0.5mm "
                     "--points '[{\"x_mm\": 10, \"y_mm\": 20}, {\"x_mm\": 15, \"y_mm\": 20}, {\"x_mm\": 17, \"y_mm\": 22}]'",
                     "kicad-cli track create --doc D:/work/demo/demo.kicad_pcb --input D:/work/demo/tracks.json"],
        "maturity": "draft",
        "evidence": [],
    },
    {
        "path": "track delete",
        "summary": "Delete straight track segments and vias by UUID.",
        "effects": ["change"],
        "modes": ["file"],
        "target_kind": "track",
        "flags": [
            {"name": "--id", "type": "string", "required": True, "summary": "The UUID of an unlocked straight segment or via, from track list."},
            {"name": "--ids", "type": "list", "summary": "Several segment or via UUIDs, comma-separated."},
        ],
        "untrusted_fields": ["deleted"],
        "irreversible": True,
        "halt": False,
        "tolerances": {},
        "default_fields": [],
        "related_knowledge": ["files-open-in-kicad"],
        "output_schema": {"type": "object", "properties": {
            "deleted": {"type": "array", "items": {"type": "object", "properties": {
                "id": {"type": "string"}, "type": {"enum": ["segment", "via"]}, "net": {"type": "string"},
                "layer": {"type": "string"}, "width_mm": {"type": "number"}, "start": TRACK_END, "end": TRACK_END,
                "x_mm": {"type": "number"}, "y_mm": {"type": "number"},
                "connects_to": {"type": "object", "additionalProperties": TOUCHES}}}},
            "note": {"type": "string"}}},
        "output_coordinate_system": BOARD,
        "examples": ["kicad-cli track delete --doc D:/work/demo/demo.kicad_pcb "
                     "--ids 3f2a6c1e-0d7b-4f7e-9a51-2b8c4d6e8f10"],
        "maturity": "draft",
        "evidence": [],
    },
]
