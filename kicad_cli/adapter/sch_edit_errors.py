"""How the schematic edit engine reports a change it cannot make.

The engine was written to stop at the first refusal; ``fail`` raises ``EditError``, which the
command handlers turn into an adapter error (nothing is written).
"""
from __future__ import annotations

from typing import Any


class EditError(Exception):
    """A change the engine refuses: an error code, a message and details."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


def fail(code: str, message: str, details: dict[str, Any] | None = None) -> None:
    raise EditError(code, message, details)


def pointer(parts: list[Any]) -> str:
    return "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def details(found: list[dict[str, Any]]) -> dict[str, Any]:
    return {"problems": found[:40], "problem_count": len(found)}


def kicad_root() -> str | None:
    """KiCad's installation folder, for its symbol libraries; None when not found."""
    from . import base  # noqa: PLC0415

    found = base._found()
    return str(found.root) if found else None
