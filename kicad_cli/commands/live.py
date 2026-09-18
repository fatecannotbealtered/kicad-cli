"""``kicad-cli board live`` -- ask the KiCad that is open on the screen what it has.

Every other command in this tool works on a file: it loads a ``.kicad_pcb``,
changes it, saves it. It needs nobody watching and nothing running, but it also
means the person who owns the board finds out what happened afterwards, by
opening the file.

KiCad's IPC API is the other way in. The editor is already open, holding the
board; this connects to it and reports what is there. That is all it does. The
API can also create and modify items, and this command deliberately does not:
an editing path through the IPC API would need the same confirmation gate,
verification and rollback the file-based writes are still missing, and none of
that exists here. Do not read the API's potential as this command's capability.

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


NOTE = (
    "this command needs the board open in KiCad. Every other command in this "
    "tool works on the file and needs nothing running."
)
FIX = (
    "open the board in KiCad; if it is already open, enable "
    "Preferences -> KiCad API -> 'Enable KiCad API' and restart KiCad"
)


def connect() -> tuple[Any, str, str]:
    """Try to reach the running KiCad. Returns (client, kind, reason).

    Never exits, because `doctor` has to ask this question without answering
    it for the whole process. `_client` turns a failure into the envelope;
    this returns it. One code path, so the two cannot drift -- `doctor` used
    to answer from the preference file instead and reported `pass` while this
    command could not connect at all, which is the one thing a preflight check
    must never do.
    """
    if _VENDOR is not None and str(_VENDOR) not in sys.path:
        sys.path.insert(0, str(_VENDOR))
    try:
        from kipy import KiCad
    except ImportError as exc:
        return None, "client_missing", str(exc)
    try:
        kicad = KiCad()
        kicad.get_version()
    except Exception as exc:  # noqa: BLE001 - the client raises several types
        return None, "unreachable", str(exc)[:200]
    return kicad, "ok", ""


def _client():
    """The command's entry point: connect or leave through the envelope."""
    kicad, kind, reason = connect()
    if kind == "client_missing":
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
                "import_error": reason,
                "fix": "this build is missing its IPC client; report it"
                if _FROZEN
                else "pip install --target .vendor kicad-python",
                # A fresh checkout has no .vendor -- it is gitignored and fetched
                # at build time -- so this is the first failure a new clone meets,
                # and the one most likely to be read as "the tool is broken"
                # rather than "one command needs one more thing".
                "note": NOTE,
            },
        )
    if kind != "ok":
        envelope.fail(
            "E_CONFIG",
            "could not reach the running KiCad",
            {
                "reason": reason,
                # Two causes, and the likelier one goes first: nothing is
                # listening either because KiCad is closed or because the API
                # is off. "Connection refused" cannot tell them apart, so the
                # fix names both rather than guessing.
                "fix": FIX,
                "why_off_by_default": "the API lets any local program edit the open board, "
                "so KiCad ships it disabled; turning it on is the owner's decision",
                "note": NOTE,
            },
        )
    return kicad


def status(args: dict[str, Any]) -> None:
    """Report what the running KiCad has open, without touching it."""
    kicad = _client()
    if _VENDOR is not None:
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
            # What this command does, not what the IPC API could be made to do.
            # These two were the other way round: they described drawing into
            # the open editor and the undo entries it would leave, which this
            # command has never done. An agent reads this field to decide what
            # it may ask for next, so an aspirational answer here is worse than
            # no answer -- the READMEs were corrected and this was not.
            "capabilities": {
                "reads": ["connection", "open_documents", "board_summary"],
                "writes": [],
                "note": "this command does not create, modify or delete board items and "
                "adds no entry to KiCad's undo stack. No command in this tool edits "
                "through the IPC API; the file-based writes are the only write path, "
                "and they do not go through the open editor.",
            },
            "not_checked": [
                "whether the interactive router can be driven this way: run_action can "
                "trigger pcbnew.InteractiveRouter.* by name, but those actions are built "
                "around a mouse position and may not be usable unattended",
            ],
        }
    )
