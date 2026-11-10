"""The handler of each domain command, by command path.

Each module covers a group of commands and exports ``HANDLERS``; their paths never overlap.
"""
from __future__ import annotations

from ..base import Handler
from . import board_copper, board_edit, board_read, board_shape, project, schematic_edit, schematic_read

HANDLERS: dict[str, type[Handler]] = {}
for _module in (project, schematic_read, schematic_edit, board_read, board_edit, board_copper, board_shape):
    for _path, _cls in _module.HANDLERS.items():
        if _path in HANDLERS:
            raise RuntimeError(f"two handlers for {_path}")
        HANDLERS[_path] = _cls
