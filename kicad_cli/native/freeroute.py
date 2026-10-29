"""``board route --engine freerouting``, in this process: the board to
Freerouting as Specctra DSN, its session back onto the board.

A port of `payload/pcb_freeroute.py`, which had pcbnew write the DSN and read
the session. Both are this tool's now (`fileformat/specctra.py`): the DSN is
compared with pcbnew's, as Freerouting reads it, on the selected demo boards
in `tests/test_native_freeroute.py`; the session goes onto the board as
pcbnew's import put it on the three session fixtures -- every
track and via taken up, the session's laid down, a via as big as its
padstack's shape with the drill its name gives. The zones are filled again
by this tool's filler and the board judged by this tool's DRC (`verify.oracle`
is `engine`); more errors than before and the board goes back.

Freerouting itself is run as before: found where the user installed it, at
arm's length, single-threaded (its own log says the multi-threaded optimiser
breaks clearances), in English, its log and settings kept in a scratch
directory. Where the session moves a part, it is not moved: Freerouting
moves none here, and pcbnew's import would only have put each part on the
session's tenth-of-a-micrometre grid.
"""

from __future__ import annotations

import math
import os
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import envelope
from ..fileformat import specctra
from ..fileformat.drc.settings import Settings
from . import route, write

DEFAULT_PASSES = 10
DEFAULT_TIMEOUT_S = 1800
# Freerouting has no switch for a preferred layer; the cost of a via is the
# lever that keeps signals on the top, and 300 is the middle of the plateau
# measured on two boards (50 its default; 800 and up worse again).
VIA_COST_PLANE_FIRST = 300
LAYER_POLICIES = ("balanced", "plane-first")


def run_freerouting(
    java: str, jar: str, dsn: str, ses: str, passes: int, timeout: float, cwd: str, policy: str
) -> str:
    """Freerouting once, at arm's length: arguments in, a session out."""
    # A balanced retry must not import the first attempt's session if the
    # second process fails without producing a new one.
    try:
        Path(ses).unlink(missing_ok=True)
    except OSError as exc:
        envelope.fail(
            "E_IO", "cannot isolate Freerouting's session output", {"reason": str(exc)[:200]}
        )
    cmd = [java, "-jar", jar, "-de", dsn, "-do", ses, "-mp", str(passes), "-mt", "1", "-l", "en"]
    env = dict(os.environ)
    env["FREEROUTING__USER_DATA_PATH"] = os.path.join(cwd, "userdata")
    if policy == "plane-first":
        env["FREEROUTING__ROUTER__SCORING__VIA_COSTS"] = str(VIA_COST_PLANE_FIRST)
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=cwd, env=env)
    except subprocess.TimeoutExpired:
        envelope.fail(
            "E_TIMEOUT",
            "Freerouting 超时,板子没有改动",
            {"timeout_s": timeout, "hint": "大板子本来就慢。调小 --passes，或调大 --timeout"},
        )
    except OSError as exc:
        envelope.fail(
            "E_CONFIG", "跑不起来 Freerouting", {"cmd": cmd[:3], "reason": str(exc)[:200]}
        )
    out = (proc.stdout or b"").decode("utf-8", "replace") + (proc.stderr or b"").decode(
        "utf-8", "replace"
    )
    if proc.returncode != 0:
        envelope.fail(
            "E_IO",
            "Freerouting failed; its session was not applied",
            {"exit_code": proc.returncode, "output": out[-700:]},
        )
    if not os.path.isfile(ses):
        envelope.fail(
            "E_IO",
            "Freerouting 没有产出 .ses,板子没有改动",
            {"exit_code": proc.returncode, "output": out[-700:]},
        )
    return out


def summarize(output: str) -> tuple[int | None, int | None]:
    """Whether it routed everything, from its own log; None where it does not say."""
    unrouted = violations = None
    for line in output.splitlines():
        if "final score" in line and "unrouted" in line:
            words = line.replace("(", " ").replace(")", " ").split()
            for index, word in enumerate(words):
                if word == "unrouted" and index:
                    unrouted = _int(words[index - 1], unrouted)
                if word.startswith("violation") and index:
                    violations = _int(words[index - 1], violations)
    return unrouted, violations


def _int(text: str, fallback: int | None) -> int | None:
    try:
        return int(text)
    except (TypeError, ValueError):
        return fallback


def apply_session(layout: route.Layout, session: specctra.Session, layers: dict[str, str]) -> None:
    """The session onto the board, as pcbnew's import put it: every track
    and via taken up, each wire laid down a segment between each two of its
    points, each via through the board."""
    # Validate the complete result before taking up any of the owner's copper.
    # Layout.code creates missing nets; an imported result must not do that.
    for net, layer, width, pts in session.wires:
        if layout.find(net) is None or layer not in layers:
            raise specctra.SessionError("session refers to an unexported net or layer")
        if width <= 0 or len(pts) < 2:
            raise specctra.SessionError("session wire has no usable width or path")
    for net, _stack, _at, diameter, drill in session.vias:
        if layout.find(net) is None or not 0 < drill <= diameter:
            raise specctra.SessionError("session via has an unknown net or invalid padstack")
    for piece in list(layout.pieces):
        layout.remove(piece)
    for net, layer, width, pts in session.wires:
        code = layout.code(net)
        name = layers.get(layer, layer)
        for a, b in zip(pts, pts[1:], strict=False):
            # a wire of no length too: pcbnew's import kept those
            layout.add(route.Piece("segment", a, b, width, name, code))
    for net, _stack, at, diameter, drill in session.vias:
        layout.add(route.Piece("via", at, at, diameter, route.FRONT, layout.code(net), drill))


def restore_min_width(layout: route.Layout, floor: int) -> int:
    """Segments narrower than the board's least track width brought up to
    it: Freerouting necks down to 0.15 mm to leave a fine-pitch row, below
    what the board itself allows. Returns how many."""
    fixed = 0
    for piece in layout.pieces:
        if piece.kind == "segment" and piece.width < floor:
            piece.width = floor
            fixed += 1
    return fixed


def copper_by_layer(layout: route.Layout) -> dict[str, float]:
    """Track length on each layer, mm, longest first; vias not counted."""
    out: dict[str, float] = {}
    for piece in layout.pieces:
        if piece.kind != "segment":
            continue
        name = layout.board.layer_name(piece.layer)
        length = math.hypot(piece.end[0] - piece.start[0], piece.end[1] - piece.start[1]) / route.NM
        out[name] = out.get(name, 0.0) + length
    return {name: round(value, 1) for name, value in sorted(out.items(), key=lambda kv: -kv[1])}


def begin_unchanged_write(layout: route.Layout) -> None:
    """Acquire the write lock, then check the board exported to the router.

    The transaction backs up the current disk bytes, not the exported board.
    A refusal therefore preserves an edit made during the external run.
    """
    write.begin(layout.path)
    try:
        unchanged = Path(layout.path).read_bytes() == layout.board.document.source.encode("utf-8")
    except OSError as exc:
        envelope.fail("E_IO", "cannot recheck the exported board", {"reason": str(exc)[:200]})
    if not unchanged:
        envelope.fail(
            "E_CONFLICT",
            "the board changed while Freerouting was running; its session was not applied",
            {"board": layout.path, "hint": "preview the changed board and route again"},
        )


def run(
    path: str, args: dict[str, Any], jar: str, java: str, shape: Callable[[Any], Any] = lambda d: d
) -> None:
    write.start()
    passes = max(1, int(args.get("passes") or DEFAULT_PASSES))
    policy = str(args.get("layer-policy") or "balanced").lower()
    if policy not in LAYER_POLICIES:
        envelope.fail(
            "E_USAGE", "--layer-policy 只能是 " + " / ".join(LAYER_POLICIES), {"got": policy}
        )
    timeout = float(args.get("timeout") or DEFAULT_TIMEOUT_S)

    layout = route.Layout(path)
    before_un = route.unconnected(layout.board)
    before_tracks = len([p for p in layout.pieces if p.kind == "segment"])
    preview = {
        "board": path,
        "engine": "freerouting",
        "jar": jar,
        "passes": passes,
        "layer_policy": policy,
        "unconnected_before": before_un,
        "tracks_before": before_tracks,
        "will": "导出 DSN 交给 Freerouting 重新布线，再把结果导回来；板上现有走线会被它的结果取代",
    }
    envelope.check_confirm(
        args.get("confirm"), "freeroute:" + os.path.basename(path), preview, path
    )

    # The baseline, before anything is written.
    errors_baseline, _ = route.errors(path)
    settings = Settings.of(layout.board.path.with_suffix(".kicad_pro"))
    dsn = specctra.Dsn(layout.board, settings)
    with tempfile.TemporaryDirectory(prefix="kicadcli-fr-") as scratch:
        dsn_path = os.path.join(scratch, "board.dsn")
        ses_path = os.path.join(scratch, "board.ses")
        with open(dsn_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(dsn.text(dsn_path, "10.0"))
        output = run_freerouting(java, jar, dsn_path, ses_path, passes, timeout, scratch, policy)
        unrouted, violations = summarize(output)
        applied, fallback = policy, None
        if policy == "plane-first" and unrouted:
            # plane-first trades routability for a cleaner bottom layer, and
            # a board short of a connection cannot be made: try the plain
            # way once more on the same DSN.
            fallback = {
                "from": policy,
                "to": "balanced",
                "reason": f"plane-first 留下 {unrouted} 条没布通",
            }
            output = run_freerouting(
                java, jar, dsn_path, ses_path, passes, timeout, scratch, "balanced"
            )
            unrouted, violations = summarize(output)
            applied = "balanced"
        try:
            with open(ses_path, encoding="utf-8") as handle:
                session = specctra.read_session(handle.read())
        except (OSError, UnicodeDecodeError, specctra.SessionError) as exc:
            envelope.fail(
                "E_IO",
                "Freerouting 的 .ses 导不回来,板子没有改动",
                {
                    "reason": str(exc)[:200],
                    "hint": "多半是 DSN 和板子对不上；重跑一次 board route 再试",
                },
            )

    layers = {name: layer for layer, name in dsn.names.items()}
    try:
        apply_session(layout, session, layers)
    except specctra.SessionError as exc:
        envelope.fail(
            "E_IO",
            "Freerouting's session does not match the exported board",
            {"reason": str(exc), "write_state": "not_started"},
        )
    floor = settings.nm("min_track_width")
    widened = restore_min_width(layout, floor)
    begin_unchanged_write(layout)
    board = layout.fill_and_save()

    after_un = route.unconnected(board)
    errors_final, _ = route.errors(path)
    if errors_final > errors_baseline:
        envelope.fail(
            "E_INTEGRITY",
            "Freerouting 布线后 DRC error 数高于动手前,已整盘回滚",
            {
                "errors_baseline": errors_baseline,
                "errors_final": errors_final,
                "next_action": "板子已恢复原状。改用 --engine grid，或先处理已有的 error 再重试",
            },
        )
    tracks = [p for p in layout.pieces if p.kind == "segment"]
    vias = [p for p in layout.pieces if p.kind == "via"]
    has_pour = any(not z.rule_area for z in board.zones)
    suggest = applied == "balanced" and has_pour and not fallback
    floor_mm = round(floor / route.NM, 3)
    write.done(
        shape(
            {
                "board": path,
                "engine": "freerouting",
                "passes": passes,
                "layer_policy": policy,
                "layer_policy_applied": applied,
                "layer_policy_fallback": fallback,
                "copper_by_layer_mm": copper_by_layer(layout),
                "unconnected_before": before_un,
                "unconnected_after": after_un,
                "improved": before_un - after_un,
                "tracks": len(tracks),
                "vias": len(vias),
                "freerouting_unrouted": unrouted,
                "freerouting_violations": violations,
                "necked_tracks_widened": widened,
                "min_track_width_mm": floor_mm,
                "verify": {
                    "ran": True,
                    "oracle": "engine",
                    "errors_baseline": errors_baseline,
                    "errors_final": errors_final,
                    "note": "error 数没超过动手前的基线，否则这次调用已经回滚。"
                    "freerouting_violations 是它自己的判断，和本工具的 DRC 不是一套模型——"
                    "以 errors_final 为准",
                },
                "note": _note(applied, suggest, fallback, widened, floor_mm),
            }
        )
    )


def _note(applied: str, suggest: bool, fallback: dict | None, widened: int, floor_mm: float) -> str:
    note = "Freerouting 布的线。它会推挤，所以过孔通常比自研栅格少一半左右。"
    if applied == "plane-first":
        note += (
            "层策略 plane-first：过孔成本调高，线尽量留在顶层，底层让给地平面。"
            "代价是绕路、铜更长。跑 board plane 看 backed_fraction 值不值。"
        )
    if suggest:
        note += (
            "这块板有敷铜，而现在是 balanced：底层每一段信号线都在平面上割一刀。"
            "--layer-policy plane-first 大致能把底层铜砍掉一半、过孔少两三个、"
            "board plane 的 backed_fraction 涨 0.1 上下（两块同规格板实测分别是 "
            "21.5%->5.1% 和 24.1%->12.2%），代价是铜长多约 31%。"
            "布不通会自动退回来，试一次不亏。"
        )
    if fallback:
        note += (
            f"要的是 {fallback['from']}，但它{fallback['reason']}，"
            f"已自动退回 {fallback['to']} —— 一块少了连接的板子不能打，"
            "层分布再好也没用。这块板多半没有敷铜可保护：先跑 board pour 再试。"
        )
    if widened:
        note += (
            f"有 {widened} 段扇出缩颈线被补回板子的最小线宽 {floor_mm} mm"
            "——它缩到的宽度低于这块板自己的规则。"
        )
    return note + "接下来 board drc 复核，需要载流就 board netclass + board rewidth。"
