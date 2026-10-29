"""Checks only a board's custom rules ask for: items a rule disallows,
track segments too short or long, too many vias, tracks turning too sharply.

KiCad has no board setting for these; a rule's constraint is the only thing
that asks (`rules.py`). Measured on `tests/fixtures/drc/drcrules`:

- `disallow` names the kinds of item it keeps out -- track, via (through,
  micro, blind or buried), pad, zone, hole, footprint -- and reports each
  item its condition holds for as "Items not allowed (rule 'NAME')";
- `track_segment_length` holds each straight segment of track;
- `via_count` counts a net's vias, and names the last of them;
- `track_angle` is the angle between two segments meeting end to end, 180
  degrees where they run straight on; its values are plain numbers.
"""

from __future__ import annotations

import math
from collections import defaultdict

from . import items as describe
from . import mm, subjects
from .rules import _holds, _on

VIA_KINDS = {"": "through_via", "micro": "micro_via", "blind": "blind_via", "buried": "buried_via"}


def _rules(run, kind: str):
    rules = run.settings.dru
    if rules is None or not rules.rules:
        return None
    return rules if any(kind in rule.constraints for rule in rules.rules) else None


def _deciding(rules, kind: str, subject, layer=None, bound: str | None = None, word=None):
    """The last rule of a kind whose layers and condition hold for an item --
    for `disallow`, one naming the item's kind."""
    for rule in reversed(rules.rules):
        constraint = rule.constraints.get(kind)
        if constraint is None or rule.unread:
            continue
        if bound is not None and getattr(constraint, bound) is None:
            continue
        if word is not None and not set(word) & set(constraint.words):
            continue
        if not _on(rule.layer, layer, subject):
            continue
        if rule.condition is None or _holds(rule.condition, subject, None, layer):
            return rule, constraint
    return None


def check(run) -> None:
    _disallowed(run)
    _segments(run)
    _vias(run)
    _angles(run)


def _words(thing) -> tuple[str, ...]:
    if thing.kind in ("segment", "arc"):
        return ("track",)
    if thing.kind == "via":
        return ("via", VIA_KINDS.get(thing.owner.kind, "through_via"), "hole")
    if thing.kind == "pad":
        words = ("pad",)
        return words + ("hole",) if thing.owner.kind in ("thru_hole", "np_thru_hole") else words
    if thing.kind == "fill":
        return ("zone",)
    return ()


def _disallowed(run) -> None:
    if not run.on("items_not_allowed"):
        return
    rules = _rules(run, "disallow")
    if rules is None:
        return
    for thing in run.copper().things:
        words = _words(thing)
        if not words:
            continue
        found = _deciding(rules, "disallow", subjects.of_thing(run, thing), word=words)
        if found is not None:
            rule = found[0]
            run.report("items_not_allowed", f"Items not allowed (rule '{rule.name}')",
                       [thing.item], rule.severity)  # fmt: skip
    for fp in run.board.footprints:
        found = _deciding(rules, "disallow", subjects.of_footprint(run, fp), word=("footprint",))
        if found is not None:
            rule = found[0]
            run.report("items_not_allowed", f"Items not allowed (rule '{rule.name}')",
                       [describe.footprint(fp)], rule.severity)  # fmt: skip


def _segments(run) -> None:
    if not run.on("track_segment_length"):
        return
    rules = _rules(run, "track_segment_length")
    if rules is None:
        return
    board = run.board
    for track in board.tracks:
        if track.kind == "arc":
            continue
        subject = subjects.of_track(run, track)
        length = round(math.dist(track.start, track.end))
        for bound in ("min", "max"):
            found = _deciding(rules, "track_segment_length", subject, track.layer, bound)
            if found is None:
                continue
            rule, constraint = found
            limit = getattr(constraint, bound)
            if (length < limit) if bound == "min" else (length > limit):
                run.report(
                    "track_segment_length",
                    f"Track segment length (rule '{rule.name}' {bound} length {mm(limit)}; "
                    f"actual {mm(length)})",
                    [describe.track(board, track)], rule.severity,
                )  # fmt: skip
                break


def _vias(run) -> None:
    if not run.on("too_many_vias"):
        return
    rules = _rules(run, "via_count")
    if rules is None:
        return
    board = run.board
    by_net = defaultdict(list)
    for via in board.vias:
        if via.net:
            by_net[via.net].append(via)
    for vias in by_net.values():
        found = _deciding(rules, "via_count", subjects.of_via(run, vias[0]), bound="max")
        if found is None:
            continue
        rule, constraint = found
        if len(vias) > constraint.max:
            run.report(
                "too_many_vias",
                f"Too many vias on a connection (rule '{rule.name}' max count "
                f"{constraint.max:g}; actual {len(vias)})",
                [describe.via(board, vias[-1])], rule.severity,
            )  # fmt: skip


def _angles(run) -> None:
    if not run.on("track_angle"):
        return
    rules = _rules(run, "track_angle")
    if rules is None:
        return
    board = run.board
    ends = defaultdict(list)
    straight = [t for t in board.tracks if t.kind != "arc"]
    for index, track in enumerate(straight):
        for end in (track.start, track.end):
            ends[(track.layer, track.net, end)].append(index)
    seen = set()
    for (layer, _net, point), members in ends.items():
        if len(members) != 2:
            continue
        a, b = (straight[i] for i in members)
        key = (min(members), max(members))
        if key in seen:
            continue
        seen.add(key)
        angle = _between(a, b, point)
        if angle is None:
            continue
        subject = subjects.of_track(run, a)
        for bound in ("min", "max"):
            found = _deciding(rules, "track_angle", subject, layer, bound)
            if found is None:
                continue
            rule, constraint = found
            limit = getattr(constraint, bound)
            if (angle < limit) if bound == "min" else (angle > limit):
                run.report(
                    "track_angle",
                    f"Track angle (rule '{rule.name}' {bound} angle {limit:.3f}°; "
                    f"actual {angle:.3f}°)",
                    [describe.track(board, a), describe.track(board, b)], rule.severity,
                )  # fmt: skip
                break


def _between(a, b, point) -> float | None:
    """The angle two segments make where they meet: 180 running straight on."""
    far_a = a.end if a.start == point else a.start
    far_b = b.end if b.start == point else b.start
    ux, uy = far_a[0] - point[0], far_a[1] - point[1]
    vx, vy = far_b[0] - point[0], far_b[1] - point[1]
    if (ux, uy) == (0, 0) or (vx, vy) == (0, 0):
        return None
    cosine = (ux * vx + uy * vy) / (math.hypot(ux, uy) * math.hypot(vx, vy))
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))
