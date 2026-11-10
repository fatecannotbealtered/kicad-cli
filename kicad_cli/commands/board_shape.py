"""Registrations of the board shape group: board get, board update and footprint update
(DCLI-029)."""
from __future__ import annotations

from typing import Any

from .board_edit import INPUT, LENGTH, NM_TOLERANCE, ONLY_ITEMS
from .board_read import BOARD, REFERENCE_TEXT, _reg

BOX = {"type": "object", "properties": {"x_mm": {"type": "number"}, "y_mm": {"type": "number"},
                                        "width_mm": {"type": "number"}, "height_mm": {"type": "number"}}}
PLACEMENT = {
    "parts_extent": {**BOX, "description": "The box around every footprint's courtyard, or its pads and drawings "
                                           "where it has none."},
    "margins_mm": {"type": "object", "description": "From the parts' extent to each side of the outline; negative "
                                                     "where parts stick out.",
                   "properties": {"left": {"type": "number"}, "top": {"type": "number"}, "right": {"type": "number"},
                                  "bottom": {"type": "number"}}},
    "footprints_outside": {"type": "array", "items": {"type": "string"},
                           "description": "Footprints whose courtyard, or pads and drawings where they have none, "
                                          "are not inside the outline; for an outline that is not a rectangle, its box."},
}


COMMANDS: list[dict[str, Any]] = [
    _reg(
        path="board get",
        summary="Read a board: its outline, copper layers and where its footprints sit.",
        effects=["observe"],
        target_kind="board",
        flags=[],
        related_knowledge=["placement-method"],
        output_schema={"type": "object", "properties": {
            "outline": {"type": "object", "description": "shape is rectangle (with x_mm, y_mm, width_mm, height_mm "
                                                         "as drawn), none or other (with its extent).",
                        "properties": {"shape": {"enum": ["rectangle", "none", "other"]}, **BOX["properties"],
                                       "extent": BOX}},
            "copper_layers": {"type": "array", "items": {"type": "string"}}, "thickness_mm": {"type": ["number", "null"]},
            **PLACEMENT}},
        output_coordinate_system=BOARD,
        examples=["kicad-cli board get --doc D:/work/demo/demo.kicad_pcb"],
    ),
    _reg(
        path="board update",
        summary="Set the board outline to a rectangle after a KiCad DRC check.",
        effects=["change"],
        target_kind="board",
        flags=[
            {"name": "--x", **LENGTH, "summary": "X of the outline's left side; default: as it is."},
            {"name": "--y", **LENGTH, "summary": "Y of the outline's top side; default: as it is."},
            {"name": "--width", "type": "quantity", "quantity": "length", "summary": "The outline's width; default: as it is."},
            {"name": "--height", "type": "quantity", "quantity": "length", "summary": "The outline's height; default: as it is."},
        ],
        tolerances={"x_mm": NM_TOLERANCE, "y_mm": NM_TOLERANCE, "width_mm": NM_TOLERANCE, "height_mm": NM_TOLERANCE},
        related_knowledge=["files-open-in-kicad", "design-rules-files", "checks-time-limit", "placement-method"],
        output_schema={"type": "object", "properties": {"outline": BOX, **PLACEMENT}},
        output_coordinate_system=BOARD,
        examples=["kicad-cli board update --doc D:/work/demo/demo.kicad_pcb --x 10mm --y 10mm --width 35mm --height 27mm"],
    ),
    _reg(
        path="footprint update",
        summary="Move or turn footprints' reference texts on the silkscreen.",
        effects=["change"],
        target_kind="footprint",
        flags=[
            {"name": "--id", "type": "string", "required": True, "summary": "Reference of an unlocked footprint, such as R1."},
            {"name": "--reference-x", **LENGTH, "summary": "Where the reference text's anchor goes: X on the board."},
            {"name": "--reference-y", **LENGTH, "summary": "Y of the reference text's anchor on the board."},
            {"name": "--reference-rotation", "type": "quantity", "quantity": "angle",
             "summary": "The text's angle on the board, counterclockwise, such as 0deg or 90deg."},
            {"name": "--input", "type": "input",
             "summary": INPUT + 'with several footprints: {"items": [{"id": "R1", "reference_x": "12mm", '
                        '"reference_y": "28mm"}, ...]}.' + ONLY_ITEMS},
        ],
        untrusted_fields=["id", "nearest_courtyard"],
        tolerances={"reference_x_mm": NM_TOLERANCE, "reference_y_mm": NM_TOLERANCE, "reference_rotation_deg": 0.000001},
        related_knowledge=["files-open-in-kicad", "placement-method"],
        output_schema={"type": "object", "properties": {
            "id": {"type": "string"}, "reference_text": REFERENCE_TEXT,
            "nearest_courtyard": {"type": ["string", "null"], "description": "The footprint whose courtyard is nearest "
                                                                             "to the text's anchor."},
            "own_courtyard_distance_mm": {"type": ["number", "null"],
                                          "description": "From the text's anchor to its own footprint's courtyard."}}},
        output_coordinate_system=BOARD,
        examples=["kicad-cli footprint update --doc D:/work/demo/demo.kicad_pcb --id R1 --reference-x 14mm "
                  "--reference-y 24.5mm --reference-rotation 0deg",
                  "kicad-cli footprint update --doc D:/work/demo/demo.kicad_pcb --input D:/work/demo/texts.json"],
    ),
]
