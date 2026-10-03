"""``kicad-cli board ...`` -- read-only inspection of a board.

They run in this process on the files themselves (`kicad_cli/native/`,
`kicad_cli/fileformat/`); `board drc` is this tool's own DRC. The shell owns
the envelope, so ``--fields`` and ``--compact`` behave identically wherever a
command ran.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import boardgen, envelope
from ..native import audit as native_audit
from ..native import from_netlist as native_from_netlist
from ..native import parity as native_parity
from ..native import plane as native_plane


def _board_arg(args: dict[str, Any]) -> str:
    board = args.get("board")
    if not board:
        envelope.fail("E_USAGE", "--board is required", {"param": "board"})
    path = Path(board).expanduser()
    if not path.exists():
        envelope.fail("E_NOT_FOUND", "board file does not exist", {"path": str(path)})
    return str(path.resolve())


def audit(args: dict[str, Any]) -> None:
    """Runs in this process on the file itself; no KiCad involved.

    Copper weight and allowed temperature rise drive the IPC-2221 ampacity
    maths, so they belong to the caller, not to a hard-coded default.
    """
    board = _board_arg(args)
    oz = float(args["oz"]) if args.get("oz") else 1.0
    dt = float(args["dt"]) if args.get("dt") else 10.0
    envelope.ok(native_audit.run(board, oz, dt))


def parity(args: dict[str, Any]) -> None:
    """Runs in this process, both sides: the board from its file, the
    schematic through this tool's own netlist. Needs no KiCad."""
    envelope.ok(native_parity.run(_board_arg(args)))


# A board with more violations than this returns the first `limit` of them and
# the true totals. Some boards have thousands, and an envelope nobody can read
# is not better than a short one -- but a *count* that shrank to fit would be a
# lie, so `counts` is always of the whole report.
DRC_LIMIT = 50


def drc(args: dict[str, Any]) -> None:
    """Check the board against its design rules and report what was found.

    Every write command in this tool already runs DRC -- it is the referee for
    `board route`, `board place` and the rest, and the thing they roll back
    against. There was no way to simply *ask*. "Is this board manufacturable?"
    is the question at the end of the chain, and answering it required
    performing a write, which is the wrong shape for a question.

    The check is this tool's own (`fileformat/drc/`), in this process: no
    KiCad runs. Footprints are held to their libraries where KiCad installed
    them, read as files; with no KiCad installed, those rules are listed in
    `not_checked`, as is what else it does not check yet. `ok_to_fabricate`
    is the verdict of the checks it makes.

    Exit is 0 whatever DRC found. The command succeeded at checking; whether
    the board passed is `counts` and `ok_to_fabricate`, not the exit code.
    A violation is a fact about the board, not a failure of this command.
    """
    from ..fileformat import drc as own  # noqa: PLC0415
    from ..fileformat.board import BoardError  # noqa: PLC0415
    from ..fileformat.sexpr import SexprError  # noqa: PLC0415

    board = _board_arg(args)
    # The accepted values live in this command's registry entry, which rejects
    # anything else before this runs. Re-listing them here would be a second
    # copy to keep in step with the first.
    severity = str(args.get("severity") or "all").lower()
    limit = int(args.get("limit") or DRC_LIMIT)
    from .. import kicad_env  # noqa: PLC0415

    try:
        # KiCad's installation, if any, for the libraries its tables name.
        report = own.check(board, kicad_root=kicad_env.find_kicad_root())
    except (BoardError, SexprError, UnicodeDecodeError) as exc:
        envelope.fail("E_VALIDATION", "the board cannot be read", {"path": board,
                      "reason": str(exc)[:200]})  # fmt: skip

    counted = [v for v in report.violations if not v.excluded]
    counts: dict[str, int] = {}
    for violation in counted:
        counts[violation.severity] = counts.get(violation.severity, 0) + 1
    unconnected = [v for v in report.unconnected if not v.excluded]

    def flatten(violation) -> dict[str, Any]:
        # The top-level message names the rule; the items name the things that
        # broke it. Keeping only the first gives every missing connection the
        # text "Missing connection between items", which identifies nothing.
        out = {
            "severity": violation.severity,
            "type": violation.rule,
            "description": violation.message,
            "items": [i.description for i in violation.items],
        }
        if violation.excluded:
            out["excluded"] = True
        return out

    shown = [v for v in report.violations if severity in ("all", v.severity)]
    trimmed = [flatten(v) for v in shown[:limit]]
    errors = counts.get("error", 0)
    envelope.ok(
        {
            "board": board,
            "engine": "kicad-cli's own DRC, in this process",
            "counts": counts,
            "excluded_count": len(report.violations) - len(counted),
            "unconnected_count": len(unconnected),
            "ok_to_fabricate": errors == 0 and not unconnected,
            "violations": trimmed,
            "violations_shown": len(trimmed),
            "violations_total": len(report.violations),
            "unconnected": [flatten(v) for v in report.unconnected[:limit]],
            "not_checked": report.not_checked,
            "partial": report.partial,
            "custom_rules": report.custom_rules,
            "note": "counts and *_total describe the whole report; violations may be "
            "truncated to --limit. Warnings do not stop fabrication, errors do; excluded "
            "violations (the project's DRC exclusions) are listed but not counted. "
            "ok_to_fabricate is the verdict of the checks made: not_checked lists the rules "
            "not checked yet, partial what a checked rule leaves out. Schematic parity is "
            "`board parity`.",
        }
    )


def plane(args: dict[str, Any]) -> None:
    """Check that the copper under each track is actually there.

    A track can be spacing-legal, connected and still wrong: if the layer
    beneath it has a gap, the return current has to go around, and the loop it
    encloses is what radiates. DRC has no opinion about this. Runs in this
    process on the file itself; no KiCad involved.
    """
    board = _board_arg(args)
    step = float(args["step"]) if args.get("step") else native_plane.STEP_MM
    envelope.ok(native_plane.run(board, step))


def from_netlist(args: dict[str, Any]) -> None:
    """Build a board from a netlist: place every footprint, join every net.

    The step KiCad's own "Update PCB from Schematic" performs and does not
    expose headlessly. The plan is checked first -- a footprint is a file, and
    a netlist naming one that is not installed fails before anything is built
    -- and the board is built in this process, with no KiCad involved.
    """
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

    native_from_netlist.run(plan, str(out), str(netlist), args.get("confirm"))


def update(args: dict[str, Any]) -> None:
    """Carry the schematic onto its board, as KiCad's Update PCB from
    Schematic does with the options its dialog opens with -- in this process.

    The board is updated in memory and read back before anything is written:
    every part's footprint with its reference, value and footprint, every pad
    on its pin's net, the copper as it was but for nets renamed. The preview
    carries the board's hash and every sheet's, so a token cannot be spent on
    a design that has moved on since the dry run.
    """
    from ..native import board_update  # noqa: PLC0415
    from ..native import write as native_write  # noqa: PLC0415
    from . import layout  # noqa: PLC0415

    board = Path(_board_arg(args))
    schematic = (
        Path(str(args["schematic"])).expanduser()
        if args.get("schematic")
        else board.with_suffix(".kicad_sch")
    )
    layout._guard(str(board), args)
    report, text = board_update.run(board, schematic)
    result = {
        "board": report["board"],
        "schematic": report["schematic"],
        "counts": report["counts"],
        "changes": report["changes"],
        "verified": report["verified"],
    }
    # What cannot be made -- a footprint no library has -- is passed over, as
    # KiCad's dialog passes over it, and said to be.
    result["skipped"] = report["problems"]
    status = "PARTIAL" if report["problems"] else "PASS"
    if text is None:
        envelope.ok(
            {**result, "status": "NOOP" if status == "PASS" else status,
             "not_checked": board_update.not_checked()}
        )  # fmt: skip
    if not board_update.passed(report["verified"]):
        envelope.fail(
            "E_INTEGRITY",
            "the updated board does not read back as the schematic says; nothing was written",
            {"verified": report["verified"]},
        )
    preview = {
        **report,
        "will": "rewrite the board: footprints, their fields and pads' nets as the schematic "
        "says, the copper of renamed nets renamed; nothing is deleted",
    }
    envelope.check_confirm(args.get("confirm"), f"board update:{board.name}", preview, str(board))
    native_write.start()
    native_write.begin(board)
    board.write_bytes(text.encode("utf-8"))
    native_write.done({**result, "status": status, "not_checked": board_update.not_checked()})
