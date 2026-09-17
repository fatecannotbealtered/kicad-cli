"""``kicad-cli board route|stitch|rewidth|widen|move`` -- the commands that change copper.

These are the five operations that were built and proven against a real
four-layer audio board before they came here: autorouting, stitching copper
islands, re-routing a net at a target width, widening in place, and moving a
part with a courtyard check. They are not new code wearing a new interface --
they are the same code, with the interface a command line owes an agent.

The write gate lives in the payload rather than up here, and deliberately so.
The confirm token binds to a preview of what will happen, and only the code
holding the loaded board can produce that preview honestly. This layer relays
it; it does not invent one.

What every command here promises: a dry run that describes the change before
anything is written, a DRC pass afterwards, and a revert of whatever part of
the work introduced a new error. The last of those is the only reason it is
reasonable to let any of this run unattended.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import envelope, kicad_env
from .board import _board_arg


def _relay(payload: str, args: dict[str, Any], extra: list[str], timeout: int = 3600) -> None:
    """Run a write payload and pass its verdict through unchanged.

    The payload's confirmation request is forwarded rather than re-wrapped: its
    token is computed from the preview it built, so re-deriving one here would
    produce a token that does not match and a gate that never opens.
    """
    board = _board_arg(args)
    _guard(board, args)
    argv = ["--board", board, *extra]
    if args.get("confirm"):
        argv += ["--confirm", str(args["confirm"])]

    result = kicad_env.run_payload(payload, argv, timeout=timeout)
    if result.get("ok"):
        envelope.ok(result.get("data"))
    err = result.get("error") or {}
    envelope.fail(
        err.get("code", "E_UNKNOWN"),
        err.get("message", f"{payload} reported an error"),
        err.get("details") or {},
    )


def _flag(args: dict[str, Any], name: str) -> list[str]:
    return [f"--{name}"] if args.get(name) else []


def _opt(args: dict[str, Any], name: str, payload_name: str | None = None) -> list[str]:
    value = args.get(name)
    return [f"--{payload_name or name}", str(value)] if value not in (None, "") else []


def route(args: dict[str, Any]) -> None:
    """Grid autorouter. Three modes, and the difference matters.

    ``repair`` leaves existing copper alone and only attempts what is still
    unconnected. ``full`` clears the board's tracks first and routes from
    scratch. ``rewidth`` re-routes selected netclasses at their target width.

    It does not push and shove. Nets are routed one at a time against copper it
    treats as immovable, so a connection that needs an existing track to move
    aside will not be found -- that is a property of the algorithm, not a bug
    to be tuned around, and it is why some connections still need a person.
    """
    _relay(
        "pcb_route",
        args,
        [
            *_opt(args, "mode"),
            *_opt(args, "nets"),
            *_opt(args, "classes"),
            *_opt(args, "neck"),
            *_flag(args, "ripup"),
            *_flag(args, "no-verify"),
            *_flag(args, "no-restore"),
        ],
    )


def stitch(args: dict[str, Any]) -> None:
    """Join copper-pour islands that are electrically the same net.

    A filled zone can break into islands that look connected and are not. On
    the board this was built for, thirteen islands had pads sitting on them
    with no via anywhere -- DRC is silent about it because nothing is violated;
    the copper is simply not joined.
    """
    _relay(
        "pcb_stitch",
        args,
        [
            *_opt(args, "net"),
            *_opt(args, "min-area"),
            *_flag(args, "bridge"),
            *_flag(args, "no-verify"),
        ],
    )


def rewidth(args: dict[str, Any]) -> None:
    """Re-route a net end to end at its netclass width.

    Widening in place stops at the first obstacle; re-routing can take a
    different path to carry the width. Where the target cannot be held all the
    way, the achieved width is reported rather than quietly accepted -- a trace
    that is 1.2 mm for most of its length and 0.2 mm for one segment carries
    0.2 mm of current.
    """
    _relay("pcb_route", args, ["--mode", "rewidth", *_opt(args, "classes"), *_opt(args, "neck")])


def widen(args: dict[str, Any]) -> None:
    """Widen existing tracks in place, as far as each one will go.

    Cheaper and less disruptive than re-routing, and often not enough: a
    segment hemmed in by its neighbours will not move at all. Use this first
    and ``rewidth`` when it is not enough.
    """
    _relay("pcb_widen", args, [*_opt(args, "oz"), *_flag(args, "no-verify")])


def move(args: dict[str, Any]) -> None:
    """Move parts by reference, with a courtyard collision check.

    ``--moves "U1:120.5,60.0; C3:118,62"`` -- millimetres, board coordinates.
    """
    moves = args.get("moves")
    if not moves:
        envelope.fail(
            "E_USAGE",
            "--moves is required",
            {
                "param": "moves",
                "format": 'REF:x,y separated by ";" -- for example "U1:120.5,60.0; C3:118,62"',
            },
        )
    _relay("pcb_place", args, ["--moves", str(moves)])


def _guard(board: str, args: dict[str, Any]) -> None:
    """Refuse to write to a board another KiCad has open.

    The editor holds the whole board in memory and writes all of it on save, so
    a change made underneath it is not merged -- it is overwritten the next
    time someone presses Ctrl+S, without a word. This is not hypothetical: it
    nearly ate a weekend of routing on the board these commands were built for.
    """
    locks = sorted(p.name for p in Path(board).parent.glob("~*.lck"))
    if locks and not args.get("ignore-lock"):
        envelope.fail(
            "E_CONFLICT",
            "this project is open in KiCad; anything written now is lost on the next save",
            {
                "lock_files": locks,
                "fix": "close the project in KiCad and re-run",
                "override": "pass --ignore-lock if you are certain the editor is closed and "
                "these files are stale",
            },
        )
