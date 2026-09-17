"""``kicad-cli board live`` -- talk to the KiCad that is open on the screen.

Every other command in this tool works on a file: it loads a ``.kicad_pcb``,
changes it, saves it. That is the right default -- it needs nobody watching and
nothing running. But it also means the person who owns the board finds out what
happened afterwards, by opening the file.

KiCad's IPC API is the other mode. The editor is already open, holding the
board; this connects to it and works on *that* board. Changes appear on screen
as they are made, and because they go through the editor's own commit stack,
Ctrl+Z undoes them exactly like something drawn by hand.

That is the real difference, and it is not about the visuals: a change you can
watch and undo is a change you can supervise. The file-based commands ask for
trust up front; this one lets it be withdrawn at any point.

The API ships disabled, so the first run will fail until it is turned on. That
is deliberate on KiCad's part -- it lets any local program edit your board --
and the error here says exactly what to click.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .. import envelope

# The official client is vendored rather than installed into KiCad's own
# interpreter: it talks over a socket and needs nothing from pcbnew, so there
# is no reason to modify the user's KiCad installation to get it.
#
# Only from a checkout, though. In the frozen binary there is no repository
# layout and no .vendor directory: build.py puts that directory on
# PyInstaller's import path so kipy is analysed and frozen in with its
# dependencies. Carrying it as data instead looks like it works -- the files
# are all there -- and then fails at `import kipy` with "No module named
# 'platform'", because data is never analysed.
_FROZEN = getattr(sys, "frozen", False)
_VENDOR = None if _FROZEN else Path(__file__).resolve().parents[2] / ".vendor"


def _client():
    if _VENDOR is not None and str(_VENDOR) not in sys.path:
        sys.path.insert(0, str(_VENDOR))
    try:
        from kipy import KiCad
    except ImportError as exc:
        # The reason used to be swallowed, which made a packaging problem
        # indistinguishable from a missing directory: the frozen binary carried
        # .vendor correctly and still failed here, and the message said only
        # "not available". Whatever could not be imported is the useful part.
        envelope.fail(
            "E_CONFIG",
            "the KiCad IPC client is not available",
            {
                "frozen": _FROZEN,
                "expected": "bundled in the binary" if _FROZEN else str(_VENDOR),
                "expected_exists": None if _FROZEN else _VENDOR.is_dir(),
                "import_error": str(exc),
                "fix": "this build is missing its IPC client; report it"
                if _FROZEN
                else "pip install --target .vendor kicad-python",
            },
        )
    try:
        return KiCad()
    except Exception as exc:  # noqa: BLE001 - the client raises several types
        envelope.fail(
            "E_CONFIG",
            "could not reach the running KiCad",
            {
                "reason": str(exc)[:200],
                "fix": "in KiCad: Preferences -> KiCad API -> tick 'Enable KiCad API', "
                "then restart KiCad",
                "why_off_by_default": "the API lets any local program edit the open board, "
                "so KiCad ships it disabled; turning it on is the owner's decision",
                "note": "this command needs the board open in KiCad. Every other command "
                "in this tool works on the file and needs nothing running.",
            },
        )


def status(args: dict[str, Any]) -> None:
    """Report what the running KiCad has open, without touching it."""
    kicad = _client()
    sys.path.insert(0, str(_VENDOR))
    from kipy.proto.common.types import DocumentType

    version = kicad.get_version()
    docs = []
    for kind, label in (
        (DocumentType.DOCTYPE_PCB, "pcb"),
        (DocumentType.DOCTYPE_SCHEMATIC, "schematic"),
        (DocumentType.DOCTYPE_PROJECT, "project"),
    ):
        try:
            for d in kicad.get_open_documents(kind):
                docs.append({"type": label, "path": getattr(d, "board_filename", "") or str(d)})
        except Exception:  # noqa: BLE001, S110 - a closed document type is not an error
            pass

    board = None
    try:
        b = kicad.get_board()
        board = {
            "name": b.name,
            "footprints": len(b.get_footprints()),
            "tracks": len(b.get_tracks()),
            "vias": len(b.get_vias()),
            "zones": len(b.get_zones()),
            "nets": len(b.get_nets()),
            "copper_layers": b.get_copper_layer_count(),
            "active_layer": b.get_layer_name(b.get_active_layer()),
            "selected": len(b.get_selection()),
        }
    except Exception as exc:  # noqa: BLE001
        board = {"error": str(exc)[:200]}

    envelope.ok(
        {
            "connected": True,
            "kicad_version": str(version),
            "open_documents": docs,
            "board": board,
            "capabilities": {
                "edits_are_undoable": "changes go through the editor's commit stack, so "
                "Ctrl+Z reverts them and the undo entry carries the message we set",
                "visible": "items appear in the editor as they are created; there is no "
                "cursor to watch, because nothing is being clicked",
            },
            "not_checked": [
                "whether the interactive router can be driven this way: run_action can "
                "trigger pcbnew.InteractiveRouter.* by name, but those actions are built "
                "around a mouse position and may not be usable unattended",
                "nothing was written; this command only reads",
            ],
        }
    )
