"""``board widen``, in this process: tracks widened in place toward their
net class's width, as far as the DRC lets each go.

A port of `payload/pcb_widen.py`, which ran on pcbnew. The router lays a
track at the neck width, which leaves a power net carrying a third of its
current; this puts the width back wherever it fits.

The judge is the DRC, not a grid. An earlier version asked a grid whether a
track would fit before widening it and was three times over cautious --
every net kept at the board's largest clearance, pads grown as circles, a
0.1 mm rounding on top -- enough to hold a track that fits at 1.2 mm to
0.2 mm. Measured on a four-layer amplifier board: of 55 tracks under width,
20 (62% of their length) widened straight to their target came out clean.

So: the tracks of each width class, widest first, are tried a step at a
time -- refill the zones, run the DRC, put back what brought an error and
try it again a step narrower. The DRC and the zone filler are this tool's
own (`fileformat/drc/`, `fileformat/zonefill.py`). `--no-verify` alone
asks the grid instead, and says that nothing checked it.
"""

from __future__ import annotations

import math
import os
import re
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from .. import envelope, kicad_env
from ..fileformat import connectivity, drc, zonefill
from ..fileformat.board import Board
from ..fileformat.drc.settings import Settings
from ..fileformat.sexpr import number
from . import load_board, write
from .project import NetClasses, ampacity, load_project
from .route import Layout
from .router import CL_MAX, GRID, MM, B, F, mm

# Fine steps: 0.20 mm widened to 0.48 mm is 0.74 A raised to 1.5 A, worth
# having even where the target is out of reach.
STEPS = (1.0, 0.85, 0.70, 0.60, 0.50, 0.42, 0.35, 0.28)
QUANT = 0.10  # mm: the grid check's allowance for rounding to its cells
_TRACK_RE = re.compile(r"\[([^\]]+)\].*?([0-9]+\.[0-9]+)\s*mm")


def layer_id(name: str) -> int:
    """pcbnew's number for a copper layer: F.Cu 0, B.Cu 2, In1.Cu 4 ..."""
    if name == "F.Cu":
        return 0
    if name == "B.Cu":
        return 2
    if name.startswith("In") and name[2:-3].isdigit():
        return 2 + 2 * int(name[2:-3])
    return 10_000


def key_of(start, end, layer: str) -> tuple:
    return (
        round(mm(start[0]), 3),
        round(mm(start[1]), 3),
        round(mm(end[0]), 3),
        round(mm(end[1]), 3),
        layer_id(layer),
    )


def segments(board: Board):
    """The board's straight tracks, in the file's order."""
    return [t for t in board.tracks if t.kind == "segment"]


def errors(path: str):
    report = drc.check(path, kicad_root=kicad_env.find_kicad_root())
    count = sum(1 for v in report.violations if v.severity == "error" and not v.excluded)
    return count, report


def _refill_and_save(board: Board) -> None:
    filler = zonefill.Filler(board, Settings.of(board.path.with_suffix(".kicad_pro")))
    for zone in board.zones:
        if not zone.rule_area:
            zonefill.write(filler.fill(zone))
    write.save(board)


def set_widths(path: str, widths: dict[tuple, float]) -> None:
    """Set a group of tracks to the widths given, fill the zones again, save."""
    board = load_board(path)
    for track in segments(board):
        k = key_of(track.start, track.end, track.layer)
        if k in widths:
            node = track.node.find("width")
            node.set(1, number(MM(widths[k]) / 1e6))
    board = Board(board.document, board.path)
    _refill_and_save(board)


def offending_keys(report, candidates: set, net_of: dict) -> set:
    """Which widened tracks the DRC names.

    The candidates are every track widened so far, not only this round's: a
    track settled in the first round can clash with one widened in the fifth,
    and looking at this round's alone, the error stays on the board.

    A position is not enough to name a track by: a report names a track by
    its net and its length, which is far more exact. A violation with no
    position -- KiCad's placeholder [1, 1] -- names the tracks round the pads
    it names.
    """
    bad = set()
    for v in report.violations:
        if v.severity != "error" or v.excluded:
            continue
        for item in v.items:
            desc = item.description or ""
            m = _TRACK_RE.search(desc)
            if m and ("走线" in desc or "track" in desc.lower()):
                net, length = m.group(1), float(m.group(2))
                for k in candidates:
                    if net_of.get(k) != net:
                        continue
                    if abs(math.hypot(k[2] - k[0], k[3] - k[1]) - length) < 0.02:
                        bad.add(k)
            x, y = mm(item.position[0]), mm(item.position[1])
            if abs(x - 1.0) < 1e-6 and abs(y - 1.0) < 1e-6:
                continue
            for k in candidates:
                x1, y1, x2, y2, _layer = k
                vx, vy = x2 - x1, y2 - y1
                l2 = vx * vx + vy * vy
                if l2 > 1e-9:
                    u = max(0.0, min(1.0, ((x - x1) * vx + (y - y1) * vy) / l2))
                    d = math.hypot(x1 + u * vx - x, y1 + u * vy - y)
                else:
                    d = math.hypot(x1 - x, y1 - y)
                if d < 0.8:
                    bad.add(k)
    return bad


def run(path: str, args: dict, shape: Callable[[Any], Any] = lambda d: d) -> None:
    write.start()
    oz = float(args.get("oz") if args.get("oz") not in (None, "") else 1.0)
    board = load_board(path)
    pro, _ = load_project(path)
    nc = NetClasses(pro)

    tracks = segments(board)
    vias = board.vias
    under: dict[str, int] = defaultdict(int)
    for t in tracks:
        cls = nc.name_for(t.net)
        target = nc.classes.get(cls, nc.classes["Default"]).get("track_width", 0.2)
        if mm(t.width) < target - 1e-6:
            under[cls] += 1
    preview = {
        "board": path,
        "tracks": len(tracks),
        "under_width_by_class": {k: int(v) for k, v in sorted(under.items())},
        "will": "在不与异网铜冲突的前提下逐段加宽",
    }
    envelope.check_confirm(args.get("confirm"), "pcb_widen:%s" % os.path.basename(path),  # noqa: UP031
                           preview, path)  # fmt: skip

    orig: dict[tuple, float] = {}
    for t in tracks:
        orig[key_of(t.start, t.end, t.layer)] = mm(t.width)

    stat: dict[str, dict] = defaultdict(lambda: {"len": 0.0, "ok": 0.0, "up": 0})
    targets: dict[tuple, tuple[str, float]] = {}
    net_by_key: dict[tuple, str] = {}
    for t in tracks:
        cls = nc.name_for(t.net)
        target = nc.classes.get(cls, nc.classes["Default"]).get("track_width", 0.2)
        k = key_of(t.start, t.end, t.layer)
        targets[k] = (cls, target)
        net_by_key[k] = t.net
        stat[cls]["len"] += math.hypot(mm(t.end[0]) - mm(t.start[0]), mm(t.end[1]) - mm(t.start[1]))

    have_drc = not args.get("no-verify")
    # The baseline: whatever the widths, the board ends with no more errors
    # than this.
    baseline_err = errors(path)[0] if have_drc else 0
    settled = dict(orig)
    rounds = []
    bisects = 0

    if have_drc:
        # Widest first: let every under-width track race for the same room
        # and the signals take it, the power nets last -- Default gained 42
        # points where BTL_OUT, the one that mattered, gained 4.
        groups = sorted({targets[k][1] for k in orig}, reverse=True)
        for gw in groups:
            pending = {k for k in orig if targets[k][1] == gw and settled[k] < gw - 1e-6}
            for frac in STEPS:
                if not pending:
                    break
                trial = {
                    k: round(targets[k][1] * frac, 2)
                    for k in pending
                    if round(targets[k][1] * frac, 2) > settled[k] + 1e-6
                }
                if not trial:
                    break
                set_widths(path, trial)
                count, report = errors(path)
                # Down to the baseline: first what the report names, and
                # where it names nothing of this round, half the round at a
                # time -- throwing the whole round away to clear five or six
                # local errors took hundreds of widenings back with them.
                kept = dict(trial)
                while count > baseline_err and kept:
                    widened = {k for k, w in settled.items() if w > orig[k] + 1e-6} | set(kept)
                    bad = offending_keys(report, widened, net_by_key) & set(kept)
                    if not bad:
                        bad = set(sorted(kept)[: max(1, len(kept) // 2)])
                        bisects += 1
                    set_widths(path, {k: settled[k] for k in bad})
                    for k in bad:
                        kept.pop(k, None)
                    count, report = errors(path)
                for k in kept:
                    settled[k] = trial[k]
                    stat[targets[k][0]]["up"] += 1
                rounds.append({
                    "target_mm": gw,
                    "at_fraction": frac,
                    "tried": len(trial),
                    "kept": len(kept),
                    "reverted": len(trial) - len(kept),
                    "errors_after": count,
                })  # fmt: skip
                pending = {k for k in trial if k not in kept}
    else:
        _grid_widen(path, board, vias, tracks, nc, stat, settled)

    for k, (cls, target) in targets.items():
        if settled.get(k, 0.0) >= target - 1e-6:
            stat[cls]["ok"] += math.hypot(k[2] - k[0], k[3] - k[1])

    final = load_board(path)
    verify: dict[str, Any] = {"ran": have_drc, "rounds": rounds}
    if have_drc:
        final_err = errors(path)[0]
        verify.update(
            oracle="engine",
            errors_baseline=baseline_err,
            errors_final=final_err,
            bisect_steps=bisects,
            note=(
                "error 未超过动手前的基线"
                if final_err <= baseline_err
                else "error 高于基线，这是缺陷，不该发生"
            ),
        )
    else:
        verify["note"] = "用户明确跳过 DRC，仅使用栅格近似；结果未经 DRC 验证"
    rows = []
    for cls, s in sorted(stat.items()):
        target = nc.classes.get(cls, nc.classes["Default"]).get("track_width", 0.2)
        rows.append({
            "class": cls,
            "target_mm": target,
            "len_mm": round(s["len"], 1),
            "widened_segments": s["up"],
            "compliant_pct": round(100.0 * s["ok"] / max(s["len"], 1e-9), 1),
            "target_amp": round(ampacity(target, oz), 2),
        })  # fmt: skip
    write.done(shape({
        "board": path,
        "classes": rows,
        "verify": verify,
        "unconnected": connectivity.connect(final).unconnected,
        "note": "判据是 DRC 不是栅格近似。仍未达标的段是真的放不下——"
        "挡路的多半是过孔和别的走线，那才是推挤算法该解决的部分",
    }))  # fmt: skip


def _grid_widen(path, board, vias, tracks, nc, stat, settled) -> None:
    """--no-verify only: widen what the grid says fits, nothing checked."""
    layout = Layout(path)
    R = layout.router()  # noqa: N806
    for v in vias:
        # The payload drew a via as its drill and 0.6 mm more.
        R.paint_via(mm(v.position[0]), mm(v.position[1]), mm(v.drill) + 0.6, layout.code(v.net))
    for t in tracks:
        if t.layer in ("F.Cu", "B.Cu"):
            R.paint_seg(F if t.layer == "F.Cu" else B, mm(t.start[0]), mm(t.start[1]),
                        mm(t.end[0]), mm(t.end[1]), mm(t.width), layout.code(t.net))  # fmt: skip

    def seg_clear(layer, x1, y1, x2, y2, w, code) -> bool:
        r = int(math.ceil((w / 2.0 + CL_MAX + QUANT) / GRID))
        n = max(2, int(math.hypot(x2 - x1, y2 - y1) / GRID) + 1)
        for k in range(n + 1):
            tt = k / n
            i, j = R.gx(x1 + (x2 - x1) * tt), R.gy(y1 + (y2 - y1) * tt)
            if not (r <= i < R.W - r and r <= j < R.H - r):
                return False
            if R.foreign(layer, code, (j - r, j + r + 1), (i - r, i + r + 1)):
                return False
        return True

    fresh = load_board(path)
    for t in segments(fresh):
        cls = nc.name_for(t.net)
        target = nc.classes.get(cls, nc.classes["Default"]).get("track_width", 0.2)
        cur = mm(t.width)
        if cur >= target - 1e-6:
            continue
        layer = F if t.layer == "F.Cu" else B  # an inner layer's track checked on B.Cu
        x1, y1 = mm(t.start[0]), mm(t.start[1])
        x2, y2 = mm(t.end[0]), mm(t.end[1])
        code = layout.code(t.net)
        for frac in STEPS:
            w2 = round(target * frac, 2)
            if w2 <= cur:
                break
            if seg_clear(layer, x1, y1, x2, y2, w2, code):
                t.node.find("width").set(1, number(MM(w2) / 1e6))
                R.paint_seg(layer, x1, y1, x2, y2, w2, code)
                stat[cls]["up"] += 1
                settled[(round(x1, 3), round(y1, 3), round(x2, 3), round(y2, 3),
                         layer_id(t.layer))] = w2  # fmt: skip
                break
    fresh = Board(fresh.document, fresh.path)
    _refill_and_save(fresh)
