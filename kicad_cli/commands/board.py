"""``kicad-cli board ...`` -- read-only inspection of a board.

These relay to payloads running inside KiCad's interpreter. The shell owns the
envelope: we take the payload's ``data`` and re-emit it under our own timing
and field projection, so ``--fields`` and ``--compact`` behave identically
whether a command ran in-process or as a guest.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import boardgen, envelope, kicad_env


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


# A board with more violations than this returns the first `limit` of them and
# the true totals. Some boards have thousands, and an envelope nobody can read
# is not better than a short one -- but a *count* that shrank to fit would be a
# lie, so `counts` is always of the whole report.
DRC_LIMIT = 50


def drc(args: dict[str, Any]) -> None:
    """Run KiCad's design rule check and report what it found.

    Every write command in this tool already runs DRC -- it is the referee for
    `board route`, `board place` and the rest, and the thing they roll back
    against. There was no way to simply *ask*. "Is this board manufacturable?"
    is the question at the end of the chain, and answering it required
    performing a write, which is the wrong shape for a question.

    This runs host-side: KiCad's own `pcb drc` is a separate binary, so unlike
    the other board commands there is no payload and no guest interpreter.

    Exit is 0 whatever DRC found. The command succeeded at checking; whether
    the board passed is `counts` and `ok_to_fabricate`, not the exit code.
    A violation is a fact about the board, not a failure of this command.
    """
    board = _board_arg(args)
    # The accepted values live in this command's registry entry, which rejects
    # anything else before this runs. Re-listing them here would be a second
    # copy to keep in step with the first.
    severity = str(args.get("severity") or "all").lower()
    limit = int(args.get("limit") or DRC_LIMIT)

    report = kicad_env.run_drc(board)
    violations = report.get("violations") or []
    unconnected = report.get("unconnected_items") or []

    counts: dict[str, int] = {}
    for violation in violations:
        key = str(violation.get("severity", "unknown"))
        counts[key] = counts.get(key, 0) + 1

    def flatten(entry: dict[str, Any]) -> dict[str, Any]:
        # The top-level description names the rule; the items name the things
        # that broke it. Keeping only the first gives every missing connection
        # the text "Missing connection between items", which identifies nothing.
        return {
            "severity": entry.get("severity"),
            "type": entry.get("type"),
            "description": entry.get("description"),
            "items": [i.get("description") for i in (entry.get("items") or [])][:4],
        }

    shown = [v for v in violations if severity in ("all", str(v.get("severity")))]
    trimmed = [flatten(v) for v in shown[:limit]]

    errors = counts.get("error", 0)
    envelope.ok(
        {
            "board": board,
            "oracle": "kicad-cli pcb drc",
            "counts": counts,
            "unconnected_count": len(unconnected),
            "ok_to_fabricate": errors == 0 and not unconnected,
            "violations": trimmed,
            "violations_shown": len(trimmed),
            "violations_total": len(violations),
            "unconnected": [flatten(i) for i in unconnected[:limit]],
            "note": "counts and *_total describe the whole report; violations may be "
            "truncated to --limit. Warnings do not stop fabrication, errors do. "
            "Schematic parity is not checked here -- that is `board parity`, which "
            "matches by link rather than by reference designator.",
        }
    )


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


def from_netlist(args: dict[str, Any]) -> None:
    """Build a board from a netlist: place every footprint, join every net.

    The step KiCad's own "Update PCB from Schematic" performs and does not
    expose headlessly. Everything checkable without pcbnew is checked first --
    a footprint is a file, and a netlist naming one that is not installed fails
    here rather than inside KiCad's interpreter.
    """
    import json  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    netlist = args.get("netlist")
    out = args.get("out")
    if not out:
        envelope.fail(
            "E_USAGE",
            "--out is required: this command creates a board rather than changing one",
            {"param": "out"},
        )
    plan = boardgen.plan(
        str(netlist), float(args.get("pitch", 10.0)), float(args.get("margin", 10.0))
    )

    # The plan goes to the payload as a file. It is larger than an argument
    # list should carry, and a temporary file keeps the netlist parsing on this
    # side -- testable without KiCad -- while the payload only executes.
    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", prefix="kicadcli-plan-", delete=False, encoding="utf-8"
    ) as handle:
        json.dump(plan, handle)
        plan_path = handle.name
    argv = ["--plan", plan_path, "--out", str(out), "--netlist", str(netlist)]
    if args.get("confirm"):
        argv += ["--confirm", str(args["confirm"])]
    try:
        result = kicad_env.run_payload("board_build", argv)
    finally:
        Path(plan_path).unlink(missing_ok=True)

    if result.get("ok"):
        envelope.ok(result.get("data"))
    err = result.get("error") or {}
    envelope.fail(
        err.get("code", "E_UNKNOWN"),
        err.get("message", "board_build reported an error"),
        err.get("details") or {},
    )
