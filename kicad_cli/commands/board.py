"""``kicad-cli board ...`` -- read-only inspection of a board.

These relay to payloads running inside KiCad's interpreter. The shell owns the
envelope: we take the payload's ``data`` and re-emit it under our own timing
and field projection, so ``--fields`` and ``--compact`` behave identically
whether a command ran in-process or as a guest.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import envelope, kicad_env


def _board_arg(args: dict[str, Any]) -> str:
    board = args.get("board")
    if not board:
        envelope.fail("E_USAGE", "--board is required", {"param": "board"})
    path = Path(board).expanduser()
    if not path.exists():
        envelope.fail("E_NOT_FOUND", "board file does not exist", {"path": str(path)})
    return str(path.resolve())


def _relay(payload: str, argv: list[str], timeout: int = 1800) -> None:
    result = kicad_env.run_payload(payload, argv, timeout=timeout)
    if result.get("ok"):
        envelope.ok(result.get("data"))
    err = result.get("error") or {}
    envelope.fail(
        err.get("code", "E_UNKNOWN"),
        err.get("message", "payload reported an error"),
        err.get("details") or {},
    )


def audit(args: dict[str, Any]) -> None:
    argv = ["--board", _board_arg(args)]
    # Copper weight and allowed temperature rise drive the IPC-2221 ampacity
    # maths, so they belong to the caller, not to a hard-coded default.
    if args.get("oz"):
        argv += ["--oz", str(args["oz"])]
    if args.get("dt"):
        argv += ["--dt", str(args["dt"])]
    _relay("audit", argv)


def parity(args: dict[str, Any]) -> None:
    _relay("parity", ["--board", _board_arg(args)])


def plane(args: dict[str, Any]) -> None:
    """Check that the copper under each track is actually there.

    A track can be spacing-legal, connected and still wrong: if the layer
    beneath it has a gap, the return current has to go around, and the loop it
    encloses is what radiates. DRC has no opinion about this.
    """
    board = _board_arg(args)
    argv = ["--board", board]
    if args.get("step"):
        argv += ["--step", str(args["step"])]
    result = kicad_env.run_payload("plane", argv, timeout=3600)
    if result.get("ok"):
        envelope.ok(result.get("data"))
    err = result.get("error") or {}
    envelope.fail(
        err.get("code", "E_UNKNOWN"),
        err.get("message", "plane payload reported an error"),
        err.get("details") or {},
    )
