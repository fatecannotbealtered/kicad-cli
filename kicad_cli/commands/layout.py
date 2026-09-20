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

# `pcb_route` backs two commands with different contracts, so some of what it
# computes is genuinely not part of `board rewidth`'s. These are the keys that
# describe routing work rewidth never does, and dropping them is a decision
# recorded here rather than a side effect of shaping. Anything else the payload
# emits that a schema does not declare is drift and fails in strict mode.
_ROUTE_ONLY = frozenset(
    {
        # Which engine routed it. `board rewidth` is always the grid one --
        # Freerouting has no notion of re-routing a netclass to a width -- so
        # the field is true but is not part of what rewidth promises.
        "engine",
        "targets",
        "routed",
        "failed",
        "unresolved",
        "existing_tracks",
        "cleared_tracks",
        "escape",
        "fanout",
        # Freerouting-only; `board rewidth` is always the grid engine.
        "layer_policy",
        "layer_policy_applied",
        "layer_policy_fallback",
        "copper_by_layer_mm",
        # Both describe the router's use of copper pours. `board rewidth` skips
        # plane nets rather than reporting on them, so neither is part of what
        # it promises.
        "plane_nets",
        "plane_served",
        "ripup",
    }
)


def _to_declared_shape(data: Any) -> Any:
    """Shape a payload's output to the contract *this* command declares.

    One payload can serve two commands with different contracts: `pcb_route`
    backs both `board route` and `board rewidth`, and their schemas are not the
    same set. Normalising inside the payload therefore cannot satisfy both --
    it fixed one command's shape by breaking the other's, which is how three
    different `board route` shapes ended up behind one declaration.

    So the payload produces and the command boundary shapes: fill a key this
    command declares but this mode does not set with None, and drop only the
    keys named in ``_ROUTE_ONLY``. An earlier version of this dropped anything
    undeclared, which made the strict check agree with itself instead of with
    the payload -- removing a field from a schema then silently changed the
    output to match, and the guard that exists to catch exactly that stopped
    firing. Filling is shaping; dropping without saying so is hiding.
    """
    fields = envelope.declared_fields()
    if not fields or not isinstance(data, dict):
        return data
    envelope.reject_undeclared(data, allowed_extra=_ROUTE_ONLY)
    return {name: data.get(name) for name in fields}


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
        # The payload reports what happened to the bytes on disk in its own
        # meta, which this relay drops. "committed" is the unremarkable case;
        # anything else -- most often a write command that found nothing to do
        # and never saved -- is worth saying out loud rather than leaving the
        # caller to infer it from the numbers.
        written = (result.get("meta") or {}).get("write_state")
        notices = (
            [{"code": "write_state", "message": written}]
            if written and written != "committed"
            else None
        )
        envelope.ok(_to_declared_shape(result.get("data")), notices)
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
    """Route the board. Two engines, and the difference is push and shove.

    ``--engine grid`` is the router built into this tool. Three modes:
    ``repair`` leaves existing copper alone and only attempts what is still
    unconnected, ``full`` clears the board's tracks first and routes from
    scratch, ``rewidth`` re-routes selected netclasses at their target width.
    It does not push and shove -- nets are routed one at a time against copper
    it treats as immovable, so a connection needing an existing track to move
    aside will not be found. That is a property of the algorithm.

    ``--engine freerouting`` hands the board to Freerouting over Specctra
    DSN/SES. It does push and shove. On the 15-part board measured here both
    engines finished fully connected and fabricable, and Freerouting used 172
    mm of copper against 178, with 11 vias against 23 -- half the drilled
    holes, and half the punctures in the ground plane.

    Freerouting is GPL-3.0 and is not redistributed here, for the same reason
    KiCad is not: this tool finds what the user installed and calls it at
    arm's length. `doctor` reports whether it is there.
    """
    if str(args.get("engine") or "grid").lower() == "freerouting":
        _freeroute(args)
        return
    _relay(
        "pcb_route",
        args,
        [
            *_opt(args, "mode"),
            *_opt(args, "nets"),
            *_opt(args, "classes"),
            *_opt(args, "neck"),
            *_flag(args, "ripup"),
            *_flag(args, "use-planes"),
            *_flag(args, "no-verify"),
            *_flag(args, "no-restore"),
        ],
    )


def _freeroute(args: dict[str, Any]) -> None:
    """Resolve the engine before spending a confirmation on it.

    Finding the jar is a question about this machine, not about the board, so
    it is answered here rather than inside the payload: a missing installation
    should fail the same way whether or not KiCad is even present, and should
    say what to install rather than reporting some downstream symptom.
    """
    from ..payload import freerouting  # noqa: PLC0415

    state = freerouting.status()
    if not state["usable"]:
        envelope.fail(
            "E_CONFIG",
            "Freerouting 引擎不可用",
            {
                **{k: v for k, v in state.items() if k != "usable"},
                "install": "https://github.com/freerouting/freerouting/releases",
                "hint": f"下载 jar 后设 {freerouting.ENV_JAR}=<path>；"
                f"另需 JDK 21+（或设 {freerouting.ENV_JAVA}=<path to java>）。"
                "本工具不分发 Freerouting：它是 GPL-3.0，和 KiCad 一样由用户本机安装",
                "fallback": "或者改用 --engine grid，那个不需要任何外部程序",
            },
        )
    _relay(
        "pcb_freeroute",
        args,
        [
            "--jar",
            state["jar"],
            "--java",
            state["java"],
            *_opt(args, "passes"),
            *_opt(args, "layer-policy"),
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


def place(args: dict[str, Any]) -> None:
    """Rearrange parts so connected ones sit together, measured in wirelength.

    `board move` is how a person says where something goes. This is the other
    half: the netlist already states which parts belong together, and until now
    nothing in this tool read that as a placement instruction -- `board
    from-netlist` laid out a grid ordered by reference designator, which puts a
    decoupling capacitor wherever the alphabet happens to put it.

    The payload reports half-perimeter wirelength before and after and declines
    to write when it did not improve, so "the placement got better" is a number
    in the envelope rather than a claim about the algorithm.
    """
    extra: list[str] = []
    for name in ("iterations", "clearance"):
        if args.get(name) is not None:
            extra += [f"--{name}", str(args[name])]
    keep = args.get("keep")
    if keep:
        refs = keep if isinstance(keep, list) else [keep]
        extra += ["--keep", ",".join(str(r) for r in refs)]
    _relay("pcb_autoplace", args, extra)


def netclass(args: dict[str, Any]) -> None:
    """Create a netclass and put nets in it.

    The chain has been missing this. `board from-netlist` makes a board whose
    only netclass is Default at 0.20 mm -- roughly 0.74 A on 1 oz copper at a
    10 C rise, which is fine for a signal and not for a supply. `board rewidth`
    and `board widen` both work *from* a netclass, and nothing could make one,
    so `board audit` reported an error against a board this tool had just
    produced and no command in it could clear that error.
    """
    extra = ["--name", str(args["name"]), "--nets", ",".join(_list(args.get("nets")))]
    for option in ("width", "clearance", "via-diameter", "via-drill"):
        if args.get(option) is not None:
            extra += [f"--{option}", str(args[option])]
    _relay("netclass", args, extra)


def pour(args: dict[str, Any]) -> None:
    """Fill a layer with copper for one net -- usually the ground pour.

    `board stitch` joins the islands of a pour and `board plane` audits what
    sits under each track; both assume a pour exists and neither could make
    one. A board out of this chain had zero zones, so `board plane` returned
    FAIL with `backed_fraction: 0.0` -- every track with no copper beneath it,
    on a board DRC was perfectly happy with.
    """
    extra = ["--net", str(args["net"]), "--layer", str(args["layer"])]
    for option in ("margin", "clearance", "min-width", "connect"):
        if args.get(option) is not None:
            extra += [f"--{option}", str(args[option])]
    _relay("pour", args, extra)


def silkscreen(args: dict[str, Any]) -> None:
    """Move reference designators off pads and off each other.

    The last class of problem this chain leaves on a finished board: every
    board it makes comes out with silkscreen warnings, and the reference
    designator is one side of all of them. Not purely cosmetic -- a refdes you
    cannot read is a part you cannot hand-place, rework or check against a BOM,
    and silk over a pad is clipped by the solder mask opening, so it prints as
    half a character.
    """
    _relay("silkscreen", args, [*_opt(args, "clearance")])


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    return [str(v) for v in (value if isinstance(value, list) else [value])]


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
