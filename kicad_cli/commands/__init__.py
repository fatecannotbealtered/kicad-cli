"""The domain commands of kicad-cli, as registrations (DCLI-029).

Each command is one dict; the runtime checks every field when the tool starts. The
registrations live in one module per group, beside the group's handlers in
``adapter/handlers``. Every command also gets a behavior matrix in tests/matrix/; its
``normal`` row is the write sample (DVER-002, DVER-030). Run ``python scripts/spec.py
generate`` after a change.
"""
from __future__ import annotations

from typing import Any

from . import board_copper, board_edit, board_read, board_shape, maturity, project, schematic_edit, schematic_read

COMMANDS: list[dict[str, Any]] = maturity.apply([
    *project.COMMANDS,
    *schematic_read.COMMANDS,
    *schematic_edit.COMMANDS,
    *board_read.COMMANDS,
    *board_edit.COMMANDS,
    *board_copper.COMMANDS,
    *board_shape.COMMANDS,
])
