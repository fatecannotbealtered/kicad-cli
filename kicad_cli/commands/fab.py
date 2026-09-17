"""``kicad-cli fab ...`` -- manufacturing outputs, produced in process.

These do not shell out. ``PLOT_CONTROLLER`` has always been part of pcbnew's
Python binding; nobody had wrapped it in a contract an agent could read.

Writes go through the same gate as everything else. Plotting only creates new
files and never edits the board, but it can overwrite a fab package someone is
about to send out, which is worth one confirmation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import envelope, kicad_env
from .board import _board_arg


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


def _plot(fmt: str, args: dict[str, Any]) -> None:
    """Shared implementation. The output format is carried by the command name.

    ``--format`` is a global flag in the spec (json/text/raw); reusing it for
    gerber/pdf/svg would collide. Splitting into one command per format also
    follows the rule that a command should do one clear thing with the fewest
    arguments, rather than growing a mode switch.
    """
    board = _board_arg(args)
    layers = args.get("layers")
    outdir = args.get("out") or str(Path(board).parent / "fab")

    preview = {
        "board": board,
        "format": fmt,
        "layers": layers or "(project default set)",
        "output_dir": outdir,
        "will": f"plot the selected layers to {fmt} using the project's existing plot settings",
    }
    envelope.check_confirm(args.get("confirm"), f"fab plot:{fmt}:{Path(board).name}", preview)

    argv = ["--board", board, "--fmt", fmt, "--out", outdir]
    if layers:
        argv += ["--layers", str(layers)]
    _relay("plot", argv)


def gerber(args: dict[str, Any]) -> None:
    _plot("gerber", args)


def pdf(args: dict[str, Any]) -> None:
    _plot("pdf", args)


def svg(args: dict[str, Any]) -> None:
    _plot("svg", args)


def dxf(args: dict[str, Any]) -> None:
    _plot("dxf", args)


def drill(args: dict[str, Any]) -> None:
    """Excellon drill files, the drill map and the report.

    Kept separate from the plot commands because it is a different generator
    with different settings, not another output format of the same one. The
    settings are also a different kind of thing: plotting reuses what the
    project already stores, while the drill dialog's choices are not saved in
    any readable form, so these are ours and the output says so.
    """
    board = _board_arg(args)
    outdir = args.get("out") or str(Path(board).parent / "fab")
    units = "inch" if args.get("inch") else "mm"
    origin = "aux" if args.get("aux-origin") else "absolute"
    merge = bool(args.get("merge"))
    map_fmt = str(args.get("map") or "pdf").lower()

    preview = {
        "board": board,
        "output_dir": outdir,
        "units": units,
        "origin": origin,
        "pth_npth": "merged" if merge else "separate",
        "map_format": map_fmt,
        "will": "write Excellon drill files, a drill map and a drill report; "
        "the board itself is not modified",
    }
    envelope.check_confirm(args.get("confirm"), f"fab drill:{Path(board).name}", preview)

    argv = ["--board", board, "--out", outdir, "--map", map_fmt]
    if merge:
        argv.append("--merge")
    if units == "inch":
        argv.append("--inch")
    if origin == "aux":
        argv.append("--aux_origin")
    _relay("drill", argv)
