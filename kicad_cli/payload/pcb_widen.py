"""载流加宽（写操作，DRC 把闸）。

布线先按缩颈宽度布通（粗线穿不出细间距引脚阵列），再由本脚本把能加宽的加回去。

**判据是 DRC，不是栅格。** 上一版先用栅格近似判断「这段放不放得下」，放得下才加宽，
DRC 只做事后兜底。问题是那个近似过度保守三层：全板统一按**最大**间距避障（实际
规则 0.20 mm 的地方也按 0.30 mm 让）、焊盘按**圆形**膨胀（U701 是 HTSSOP-32，
两个 5.2x11 mm 散热焊盘，当成圆直接虚构出重叠）、再加 0.10 mm 栅格量化。三项叠加，
足以把一条本来能做 1.2 mm 的线判成只能 0.2 mm。

实测：在一块四层功放板上把 55 段欠宽走线一次性加到目标、重填铺铜再跑 DRC，**20 段
（97.4 mm，占欠宽总长 62%）完全干净**——也就是说那 62% 从来就不需要什么推挤算法，
是判据自己把它们挡掉了。

新策略：整组按档位试宽 → 重填铺铜 → 跑 DRC → 把闯祸的退回、留到下一轮更窄的档。
每轮一次 DRC，最多八轮，代价可控而判据精确。DRC 不可用时明确失败，不把栅格近似冒充验证成功。

写门禁：先不带 --confirm 跑一次拿 token。

用法：
    <kicad-python> pcb_widen.py --board b.kicad_pcb [--confirm ct_xxx]
"""

import math
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kicad_lib as K
import numpy as np
from router import CL_MAX, GRID, MM, QUANT, B, F, Router, mm

# 档位要密。0.20 mm 的线加到 0.48 mm 就是从 0.74 A 提到 1.5 A，
# 哪怕够不到目标也是实打实的收益；档位太疏会把这些收益整段丢掉。
STEPS = (1.0, 0.85, 0.70, 0.60, 0.50, 0.42, 0.35, 0.28)


def run_drc(path):
    return K.run_drc(path)


def err_count(drc):
    return sum(1 for v in (drc or {}).get("violations", []) if v["severity"] == "error")


def revert_offenders(pcbnew, path, drc, orig, mm, MM):
    """把新引入 error 的走线退回原宽度。返回退回的段数。"""
    b = K.load_board(pcbnew, path)
    tracks = [t for t in K.tracks_of(b) if t.Type() == pcbnew.PCB_TRACE_T]
    reverted = 0
    for v in (drc or {}).get("violations", []):
        if v["severity"] != "error" or v["type"] not in (
            "clearance",
            "shorting_items",
            "solder_mask_bridge",
        ):
            continue
        for it in v.get("items", []):
            if (
                "走线" not in it.get("description", "")
                and "track" not in it.get("description", "").lower()
            ):
                continue
            pos = it.get("pos") or {}
            x, y = pos.get("x", 0), pos.get("y", 0)
            best, bd = None, 1e9
            for t in tracks:
                ax, ay = mm(t.GetStart().x), mm(t.GetStart().y)
                bx, by = mm(t.GetEnd().x), mm(t.GetEnd().y)
                vx, vy = bx - ax, by - ay
                L2 = vx * vx + vy * vy
                if L2 > 1e-9:
                    u = max(0.0, min(1.0, ((x - ax) * vx + (y - ay) * vy) / L2))
                    d = math.hypot(ax + u * vx - x, ay + u * vy - y)
                else:
                    d = math.hypot(ax - x, ay - y)
                if d < bd:
                    bd, best = d, t
            if best is not None and bd < 0.6:
                key = (
                    round(mm(best.GetStart().x), 3),
                    round(mm(best.GetStart().y), 3),
                    round(mm(best.GetEnd().x), 3),
                    round(mm(best.GetEnd().y), 3),
                    best.GetLayer(),
                )
                w0 = orig.get(key)
                if w0 is not None and mm(best.GetWidth()) > w0 + 1e-6:
                    best.SetWidth(MM(w0))
                    reverted += 1
    if reverted:
        K.fill_and_save(pcbnew, b, path)
    return reverted


_TRACK_RE = re.compile(r"\[([^\]]+)\].*?([0-9]+\.[0-9]+)\s*mm")


def offending_keys(drc, candidates, mm, net_of):
    """哪些**已加宽**的段被 DRC 点名了。

    两点教训写在这里：

    一是候选集必须是「所有加宽过的段」，不能只是本轮试的那些。一条第 1 轮就定下来
    的线，完全可能跟第 5 轮才加宽的线打架；那时前者已不在本轮候选里，只看本轮就归
    不到它头上，错误于是留在板上。

    二是光按坐标匹配不够。DRC 的报文里点名了「走线 [网名] (层), 长度: L mm」——
    网名加长度就是一个精确得多的键。而 starved_thermal 这类违规坐标是 [1,1]，
    根本没有位置信息，只能靠它涉及的焊盘去圈附近的段。
    """
    bad = set()
    for v in (drc or {}).get("violations", []):
        if v.get("severity") != "error":
            continue
        for it in v.get("items", []):
            desc = it.get("description") or ""
            m = _TRACK_RE.search(desc)
            if m and ("走线" in desc or "track" in desc.lower()):
                net, length = m.group(1), float(m.group(2))
                for k in candidates:
                    if net_of.get(k) != net:
                        continue
                    if abs(math.hypot(k[2] - k[0], k[3] - k[1]) - length) < 0.02:
                        bad.add(k)
            pos = it.get("pos") or {}
            x, y = pos.get("x"), pos.get("y")
            # [1,1] 是 KiCad 对「这条违规没有具体位置」的占位，别拿它当坐标用
            if x is None or (abs(x - 1.0) < 1e-6 and abs(y - 1.0) < 1e-6):
                continue
            for k in candidates:
                x1, y1, x2, y2, _lay = k
                vx, vy = x2 - x1, y2 - y1
                L2 = vx * vx + vy * vy
                if L2 > 1e-9:
                    u = max(0.0, min(1.0, ((x - x1) * vx + (y - y1) * vy) / L2))
                    d = math.hypot(x1 + u * vx - x, y1 + u * vy - y)
                else:
                    d = math.hypot(x1 - x, y1 - y)
                if d < 0.8:
                    bad.add(k)
    return bad


def set_widths(pcbnew, path, widths, MM, mm):
    """把一组段设成指定宽度，重填铺铜后存盘。"""
    bb = K.load_board(pcbnew, path)
    for t in K.tracks_of(bb):
        if t.Type() != pcbnew.PCB_TRACE_T:
            continue
        k = (
            round(mm(t.GetStart().x), 3),
            round(mm(t.GetStart().y), 3),
            round(mm(t.GetEnd().x), 3),
            round(mm(t.GetEnd().y), 3),
            t.GetLayer(),
        )
        if k in widths:
            t.SetWidth(MM(widths[k]))
    K.fill_and_save(pcbnew, bb, path)


def main():
    args = K.parse_args(sys.argv[1:], flags=("no-verify",))
    path = K.board_arg(args)
    oz = float(args.get("oz", 1.0))

    pcbnew = K.import_pcbnew()
    b = K.load_board(pcbnew, path)
    pro, _ = K.load_project(path)
    nc = K.NetClasses(pro)

    tracks = [t for t in K.tracks_of(b) if t.Type() == pcbnew.PCB_TRACE_T]
    vias = [t for t in K.tracks_of(b) if t.Type() == pcbnew.PCB_VIA_T]

    under = defaultdict(float)
    for t in tracks:
        cls = nc.name_for(t.GetNetname())
        target = nc.classes.get(cls, nc.classes["Default"]).get("track_width", 0.2)
        if mm(t.GetWidth()) < target - 1e-6:
            under[cls] += 1
    preview = {
        "board": path,
        "tracks": len(tracks),
        "under_width_by_class": {k: int(v) for k, v in sorted(under.items())},
        "will": "在不与异网铜冲突的前提下逐段加宽",
    }
    K.check_confirm(args.get("confirm"), preview, "pcb_widen:%s" % os.path.basename(path))

    R = Router(b, lambda s: None)
    for t in vias:
        R.paint_via(
            mm(t.GetPosition().x), mm(t.GetPosition().y), K.via_drill(t) + 0.6, t.GetNetCode()
        )
    for t in tracks:
        if t.GetLayer() in (pcbnew.F_Cu, pcbnew.B_Cu):
            L = F if t.GetLayer() == pcbnew.F_Cu else B
            R.paint_seg(
                L,
                mm(t.GetStart().x),
                mm(t.GetStart().y),
                mm(t.GetEnd().x),
                mm(t.GetEnd().y),
                mm(t.GetWidth()),
                t.GetNetCode(),
            )

    def seg_clear(L, x1, y1, x2, y2, w, ncode):
        r = int(math.ceil((w / 2.0 + CL_MAX + QUANT) / GRID))
        n = max(2, int(math.hypot(x2 - x1, y2 - y1) / GRID) + 1)
        for k in range(n + 1):
            tt = k / n
            i, j = R.gx(x1 + (x2 - x1) * tt), R.gy(y1 + (y2 - y1) * tt)
            if not (r <= i < R.W - r and r <= j < R.H - r):
                return False
            sub = R.occ[L][j - r : j + r + 1, i - r : i + r + 1]
            if np.any((sub != 0) & (sub != ncode)) or np.any(sub == -1):
                return False
        return True

    orig = {}
    for t in tracks:
        orig[
            (
                round(mm(t.GetStart().x), 3),
                round(mm(t.GetStart().y), 3),
                round(mm(t.GetEnd().x), 3),
                round(mm(t.GetEnd().y), 3),
                t.GetLayer(),
            )
        ] = mm(t.GetWidth())

    stat = defaultdict(lambda: {"len": 0.0, "ok": 0.0, "up": 0})
    targets = {}
    net_by_key = {}
    for t in tracks:
        cls = nc.name_for(t.GetNetname())
        target = nc.classes.get(cls, nc.classes["Default"]).get("track_width", 0.2)
        key = (
            round(mm(t.GetStart().x), 3),
            round(mm(t.GetStart().y), 3),
            round(mm(t.GetEnd().x), 3),
            round(mm(t.GetEnd().y), 3),
            t.GetLayer(),
        )
        targets[key] = (cls, target)
        net_by_key[key] = t.GetNetname()
        s0 = stat[cls]
        s0["len"] += math.hypot(
            mm(t.GetEnd().x) - mm(t.GetStart().x), mm(t.GetEnd().y) - mm(t.GetStart().y)
        )

    baseline_drc = None if args.get("no-verify") else run_drc(path)
    have_drc = baseline_drc is not None
    # 基线 error 数。整个脚本的硬承诺是「跑完不比这个数大」——达标率再好看，
    # 只要板子的 error 涨了就是失败。
    baseline_err = err_count(baseline_drc) if have_drc else 0
    settled = dict(orig)
    rounds = []
    bisects = 0

    if have_drc:
        # **按载流分批，粗的先占道。**
        #
        # 一起加宽所有欠宽段看着高效，实际是让 0.25 mm 的信号线和 1.2 mm 的功率线
        # 抢同一条走廊，而且先到先得——结果 Default 类涨了 42 个点，真正要紧的
        # BTL_OUT 只涨了 4 个点。载流大的那几条才是欠宽会出事的，必须先给它们占。
        groups = sorted({targets[k][1] for k in orig}, reverse=True)
        for gw in groups:
            pending = {k for k, w in orig.items() if targets[k][1] == gw and settled[k] < gw - 1e-6}
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
                set_widths(pcbnew, path, trial, MM, mm)
                drc = run_drc(path)

                # 收敛到「不超过基线」。先按报文归因回退闯祸的；归因不出来时二分——
                # 整轮丢弃能保证安全但太钝：为了消掉五六条局部违规，会把几百段加宽
                # 全撤掉，一轮一轮撤到底，最后回到原点。二分保住大部分成果。
                kept = dict(trial)
                while err_count(drc) > baseline_err and kept:
                    widened = {k for k, w in settled.items() if w > orig[k] + 1e-6} | set(kept)
                    bad = offending_keys(drc, widened, mm, net_by_key) & set(kept)
                    if not bad:
                        bad = set(sorted(kept)[: max(1, len(kept) // 2)])
                        bisects += 1
                    set_widths(pcbnew, path, {k: settled[k] for k in bad}, MM, mm)
                    for k in bad:
                        kept.pop(k, None)
                    drc = run_drc(path)

                for k in kept:
                    settled[k] = trial[k]
                    stat[targets[k][0]]["up"] += 1
                rounds.append(
                    {
                        "target_mm": gw,
                        "at_fraction": frac,
                        "tried": len(trial),
                        "kept": len(kept),
                        "reverted": len(trial) - len(kept),
                        "errors_after": err_count(drc),
                    }
                )
                pending = {k for k in trial if k not in kept}
    else:
        # 只有用户明确指定 --no-verify 才走栅格近似；DRC 故障不再降级。
        bb = K.load_board(pcbnew, path)
        for t in K.tracks_of(bb):
            if t.Type() != pcbnew.PCB_TRACE_T:
                continue
            net = t.GetNet()
            cls = nc.name_for(net.GetNetname())
            target = nc.classes.get(cls, nc.classes["Default"]).get("track_width", 0.2)
            cur = mm(t.GetWidth())
            if cur >= target - 1e-6:
                continue
            L = F if t.GetLayer() == pcbnew.F_Cu else B
            x1, y1 = mm(t.GetStart().x), mm(t.GetStart().y)
            x2, y2 = mm(t.GetEnd().x), mm(t.GetEnd().y)
            for frac in STEPS:
                w2 = round(target * frac, 2)
                if w2 <= cur:
                    break
                if seg_clear(L, x1, y1, x2, y2, w2, net.GetNetCode()):
                    t.SetWidth(MM(w2))
                    R.paint_seg(L, x1, y1, x2, y2, w2, net.GetNetCode())
                    stat[cls]["up"] += 1
                    settled[
                        (round(x1, 3), round(y1, 3), round(x2, 3), round(y2, 3), t.GetLayer())
                    ] = w2
                    break
        K.fill_and_save(pcbnew, bb, path)

    # 达标长度按最终宽度重算
    for k, (cls, target) in targets.items():
        if settled.get(k, 0.0) >= target - 1e-6:
            stat[cls]["ok"] += math.hypot(k[2] - k[0], k[3] - k[1])

    # 这里不再存盘：每一轮都已经 fill_and_save 过，磁盘上就是最终结果。
    # 旧版在这里又存了一次内存里的 b，那份 b 停留在第一次加载时的状态，
    # 会把逐轮的成果整个盖掉。

    final = K.load_board(pcbnew, path)
    verify = {"ran": have_drc, "rounds": rounds}
    if have_drc:
        drc = run_drc(path)
        final_err = err_count(drc)
        verify.update(
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
        rows.append(
            {
                "class": cls,
                "target_mm": target,
                "len_mm": round(s["len"], 1),
                "widened_segments": s["up"],
                "compliant_pct": round(100.0 * s["ok"] / max(s["len"], 1e-9), 1),
                "target_amp": round(K.ampacity(target, oz), 2),
            }
        )
    K.ok(
        {
            "board": path,
            "classes": rows,
            "verify": verify,
            "unconnected": K.unconnected(final),
            "note": "判据是 DRC 不是栅格近似。仍未达标的段是真的放不下——"
            "挡路的多半是过孔和别的走线，那才是推挤算法该解决的部分",
        }
    )


if __name__ == "__main__":
    main()
