"""Differential pairs: the two nets of a pair, and how near their coupled
tracks run.

A net is half of a pair when its last P, N, + or - -- with nothing but
digits and underscores after it -- swapped for its opposite names another
net of the board: `X_P` and `X_N`, `XP` and `XN`, `X+` and `X-`, `X_P1` and
`X_N1`, `XP_1` and `XN_1`. Lower case does not pair, nor `+5V` and `-5V`
(`tests/fixtures/drc/drcpairs`).

Two tracks of a pair are coupled where they run parallel on one layer, side
by side for some length, however far apart; tracks meeting at an angle, end
to end, or on two layers are not. With no custom rule, a coupled pair is
held apart by the board's minimum clearance -- not the net class's pair gap
-- and the message names the pair's class: "netclass 'X' (diff pair)
minimum gap"; with the board's minimum at 0 nothing is asked.

A custom rule on pairs (`diff_pair_gap`, `diff_pair_uncoupled`) is not read
yet: on simple boards its bounds and the uncoupled length are as KiCad
measures them, but on its demo boards KiCad couples only the tracks that run
as the pair's route, and counts vias in a route's length -- which is not
done here yet. Where such a rule decides, the check says it is not made.
"""

from __future__ import annotations

import math
from collections import defaultdict

from . import items as describe
from . import mm

SWAP = {"P": "N", "N": "P", "+": "-", "-": "+"}
PARALLEL = 1e-6  # the sine of the angle two tracks may make and still be parallel


def _suffix(net: str) -> int | None:
    """Where a net's pair letter is: the last P, N, + or - with nothing but
    digits and underscores after it."""
    for index in range(len(net) - 1, -1, -1):
        char = net[index]
        if char.isdigit() or char == "_":
            continue
        return index if char in SWAP else None
    return None


def complement(net: str) -> str | None:
    """The name of the other half of the pair a net would belong to."""
    index = _suffix(net)
    return None if index is None else net[:index] + SWAP[net[index]] + net[index + 1 :]


def partner(net: str, nets) -> str | None:
    """The other half of the pair a net belongs to, if the board has it."""
    other = complement(net)
    return other if other is not None and other in nets else None


def positive(net: str) -> bool:
    """Whether a net is its pair's P (or +) half."""
    index = _suffix(net)
    return index is not None and net[index] in ("P", "+")


def check(run) -> None:
    """With no custom rule on pairs -- one that has them leaves this check
    to it (`rules.py`), and it is not read yet."""
    if not run.on("diff_pair_gap_out_of_range"):
        return
    least = run.settings.nm("min_clearance")
    if least <= 0:
        return
    board = run.board
    by_net = defaultdict(list)
    for track in board.tracks:
        if track.kind != "arc" and track.net:
            by_net[track.net].append(track)
    nets = set(by_net)
    for net in sorted(nets):
        other = partner(net, nets)
        if other is None or not positive(net):
            continue
        name = run.settings.netclasses.of(net).name.split(",")[0]
        for a in by_net[net]:
            for b in by_net[other]:
                if a.layer != b.layer:
                    continue
                found = _coupled_gap(a, b)
                if found is not None and found[0] < least:
                    run.report(
                        "diff_pair_gap_out_of_range",
                        f"Differential pair gap out of range (netclass '{name}' (diff pair) "
                        f"minimum gap {mm(least)}; actual {mm(round(found[0]))})",
                        [describe.track(board, a), describe.track(board, b)],
                    )


def _coupled_gap(a, b) -> tuple[float, float] | None:
    """The gap between two straight tracks running parallel side by side,
    and how far they run so, or None where they do not."""
    ax, ay = a.end[0] - a.start[0], a.end[1] - a.start[1]
    length = math.hypot(ax, ay)
    bx, by = b.end[0] - b.start[0], b.end[1] - b.start[1]
    other = math.hypot(bx, by)
    if length == 0 or other == 0:
        return None
    ux, uy = ax / length, ay / length
    if abs(ux * by - uy * bx) / other > PARALLEL:
        return None
    # How far along a the ends of b fall: they must overlap a, not meet it.
    s1 = (b.start[0] - a.start[0]) * ux + (b.start[1] - a.start[1]) * uy
    s2 = (b.end[0] - a.start[0]) * ux + (b.end[1] - a.start[1]) * uy
    overlap = min(max(s1, s2), length) - max(min(s1, s2), 0.0)
    if overlap <= 0:
        return None
    apart = abs((b.start[0] - a.start[0]) * uy - (b.start[1] - a.start[1]) * ux)
    return apart - (a.width + b.width) / 2, overlap
