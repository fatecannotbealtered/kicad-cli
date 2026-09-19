"""栅格布线（写操作）。

三种模式：
    --mode full     清空既有布线，做电源扇出 + 引脚逃逸 + 两遍布线
    --mode repair   保留现状，只针对仍未连接的项定向补布（推荐反复跑）
    --mode rewidth  按目标线宽重布载流网络

rewidth 存在的理由：**加宽不是把已布的细线撑粗**。一条按 0.20 mm 规划出来的
路径，走向本来就是贴着障碍挤出来的，事后撑不到 1.2 mm。正确操作是把这条
网络整个拆掉、按目标宽度重新规划——只在焊盘处缩颈，中间走全宽。
pcb_widen.py 那种原地加宽只适合捡漏，主力手段是本模式。

repair 加 --ripup 启用撕线重布：把起点附近的异网铜临时拆掉，布通目标，
再把拆掉的重布回去。整个过程是事务性的——目标布不通、或拆掉的重布不回去，
一律整板回滚到尝试之前，绝不留下半截状态。

回滚必须连同刚布好的目标走线一起撤，只还原被拆的铜会让两者重叠。
这是实现这个功能时踩过的坑，见 rollback() 的注释。

用之前仍建议备份，用之后跑一次 DRC 确认 error 没有上升。

写门禁：先不带 --confirm 跑一次拿 token，再带 token 执行。

用法：
    <kicad-python> pcb_route.py --board b.kicad_pcb --mode repair
    <kicad-python> pcb_route.py --board b.kicad_pcb --mode repair --confirm ct_xxx

方法上的三条要点写在 router.py 的文件头，改参数前先读。最关键的一条：
起止层必须是该网络在该点已有铜的层，否则走线会静默地接不上。
"""

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kicad_lib as K  # noqa: E402
from pcbnew import VECTOR2I  # noqa: E402
from router import GRID, MM, B, F, Router, mm  # noqa: E402

NECK_DEFAULT = 0.20  # 缩颈宽度。粗线穿不出 0.5~0.65 mm 间距的引脚阵列


def _objcount(b, pcbnew, tracks):
    v = t = 0
    for x in tracks:
        if x.Type() == pcbnew.PCB_VIA_T:
            v += 1
        else:
            t += 1
    return {"via": v, "trk": t}


# board route 的三个模式产出不同字段，但契约是「精确」的：调用方不该遇到
# 未声明的键，也不该缺少已声明的键。所以键集在这里统一——不适用的模式给
# None，而不是让键消失。full 模式六个字段对不上 schema 一直没被发现，因为
# 从来没有测试真实跑过它。
ROUTE_FIELDS = (
    "mode",
    "targets",
    "routed",
    "failed",
    "unresolved",
    "tracks",
    "existing_tracks",
    "cleared_tracks",
    "escape",
    "fanout",
    "plane_served",
    "ripup",
    "vias",
    "track_len_mm",
    "unconnected_before",
    "unconnected_after",
    "improved",
    "rewidth",
    "rewidth_total",
    "rewidth_ok",
    "rewidth_reverted",
    "rewidth_drc_cause",
    "stripped_segments",
    "skipped_nets",
    "verify",
    "note",
)


def route_envelope(payload):
    """Exactly the declared keys: absent-for-this-mode is None, not missing."""
    extra = sorted(set(payload) - set(ROUTE_FIELDS))
    if extra:  # A new field must be declared before it can be emitted.
        raise AssertionError("undeclared board route fields: %s" % ", ".join(extra))
    return {name: payload.get(name) for name in ROUTE_FIELDS}


def run_drc(path):
    return K.run_drc(path)


def paint_existing(R, board, pcbnew, tracks=None):
    """tracks 为 None 时才去问板子要列表。

    调用方若在增删过程中已经维护了权威列表，就直接传进来：既省一次遍历，
    也避开 GetTracks() 在大量增删后返回未解包 SwigPyObject 的老问题（第 5 条）。
    """
    nt = 0
    for t in board.GetTracks() if tracks is None else tracks:
        nc = t.GetNetCode()
        if t.Type() == pcbnew.PCB_VIA_T:
            # 用真实过孔直径，不要拿钻径加经验值凑
            R.paint_via(
                mm(t.GetPosition().x), mm(t.GetPosition().y), mm(t.GetWidth(pcbnew.F_Cu)), nc
            )
        elif t.GetLayer() in (pcbnew.F_Cu, pcbnew.B_Cu):
            L = F if t.GetLayer() == pcbnew.F_Cu else B
            R.paint_seg(
                L,
                mm(t.GetStart().x),
                mm(t.GetStart().y),
                mm(t.GetEnd().x),
                mm(t.GetEnd().y),
                mm(t.GetWidth()),
                nc,
            )
            nt += 1
    return nt


def layers_at(R, x, y, ncode):
    """该点上本网络已有铜的层。

    这是全流程最关键的一个约束：若允许路径从任意层起步，大量走线会起止在
    没有焊盘的那一层，既不报 DRC 错也接不通，表现为「布通率怎么调都上不去」。
    """
    i, j = R.gx(x), R.gy(y)
    out = [L for L in (F, B) if 0 <= i < R.W and 0 <= j < R.H and R.occ[L][j, i] == ncode]
    return out or [F, B]


def courtyard_box(f, pcbnew):
    for side in (pcbnew.F_CrtYd, pcbnew.B_CrtYd):
        cy = f.GetCourtyard(side)
        if cy.OutlineCount():
            bb = cy.BBox()
            return (mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom()))
    bb = f.GetBoundingBox(False, False)
    return (mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom()))


def min_pitch(f):
    ps = [(mm(p.GetPosition().x), mm(p.GetPosition().y)) for p in f.Pads()]
    if len(ps) < 3:
        return 9.9
    best = 9.9
    for i in range(len(ps)):
        for j in range(i + 1, len(ps)):
            d = math.hypot(ps[i][0] - ps[j][0], ps[i][1] - ps[j][1])
            if 0.01 < d < best:
                best = d
    return best


REWIDTH_NECK_R = 2.5  # mm，重布时焊盘附近允许缩颈的半径。必须覆盖整个扇出段——
# 0.65 mm 引脚间距下，四条车道要展到 1.5 mm 间距才放得下
# 1.2 mm 的线，这段距离远超 1 mm。取 1.0 时实测四条 OUT
# 全部只能落到 0.20 mm；取 4.0 能拿到 1.20 mm 但会多出
# 14 个 DRC error（放宽的校验口径让路径钻进填不回宽度的缝）。
# 放大本身没有宽度代价：缩颈是逐格判定的（need_neck），
# nz 只是「允许更窄地校验」。
ESCAPE_CLEAR = 0.9  # mm，逃逸落点越过引脚阵列外沿的距离
ESCAPE_STAGGER = 0.5  # mm，同一条边上相邻引脚落点的错开量


def pad_bbox(f, pcbnew):
    """封装的焊盘包络。判断引脚归属哪条边要用它，不能用庭院。"""
    L = T = Rr = Bm = None
    for p in f.Pads():
        bb = p.GetBoundingBox()
        L = mm(bb.GetLeft()) if L is None else min(L, mm(bb.GetLeft()))
        T = mm(bb.GetTop()) if T is None else min(T, mm(bb.GetTop()))
        Rr = mm(bb.GetRight()) if Rr is None else max(Rr, mm(bb.GetRight()))
        Bm = mm(bb.GetBottom()) if Bm is None else max(Bm, mm(bb.GetBottom()))
    return L, T, Rr, Bm


def pad_side(box, px, py):
    """引脚靠近封装的哪条边。返回 (法向, 边名, 到该边的距离)。

    早先用「庭院中心指向焊盘」当出脚方向，实测偏离引脚真实法向 30~53 度
    （庭院不对称时中心还会被推偏），于是外围引脚的出脚方向是斜的，
    互相穿插打架。按边判定得到的是真实法向，同一条边上的引脚彼此平行。
    """
    L, T, Rr, Bm = box
    d = {"L": px - L, "R": Rr - px, "T": py - T, "B": Bm - py}
    side = min(d, key=d.get)
    nrm = {"L": (-1.0, 0.0), "R": (1.0, 0.0), "T": (0.0, -1.0), "B": (0.0, 1.0)}[side]
    return nrm, side, d[side]


def escape_pins(R, b, pcbnew, nc, neck, plane_nets, log):
    """引脚逃逸：把细间距封装的每个引脚沿法向平行引出引脚阵列。

    两条硬规则，破坏任何一条都会让后处理的引脚被围死：

      1  **只沿法向外出，不允许贴边绕行。** 旧实现会尝试 ±60~±90 度，
         那相对法向就是沿封装边走，先做的引脚等于在边上砌了一堵墙。
         实测某 TSSOP 的一根信号线在一个引脚周围 3.6x3.6 mm 内铺了 259 格铜，
         就是贴边绕出来的。这里角度上限收到 ±35 度。

      2  **不打过孔。** 32 脚的 IC 周围塞不下 32 个孔。逃逸只需把线带出引脚
         阵列，换不换层交给后面的主干 A* 决定。

    同一条边上的引脚天然按引脚间距分道，互不相交，所以没有顺序依赖，
    不需要匹配算法。落点按序号轻微错开，避免落点连成一条线挡住后续主干布线。
    """
    npads = {}
    for f in K.footprints_of(b):
        for p in f.Pads():
            npads[p.GetNetCode()] = npads.get(p.GetNetCode(), 0) + 1

    targets = []
    for f in K.footprints_of(b):
        if len(list(f.Pads())) >= 6:
            pitch = min_pitch(f)
            if pitch <= 1.30:
                targets.append((pitch, f))
    targets.sort(key=lambda t: t[0])

    access = {}
    ok = fail = skip = 0
    per_fp = {}
    for _pitch, f in targets:
        box = pad_bbox(f, pcbnew)
        L0, T0, R0, B0 = courtyard_box(f, pcbnew)
        # 按边分组，组内按沿边坐标排序，逐个分配平行车道
        groups = {}
        for p in f.Pads():
            px, py = mm(p.GetPosition().x), mm(p.GetPosition().y)
            nrm, side, _ = pad_side(box, px, py)
            groups.setdefault(side, []).append((p, px, py, nrm))
        fok = ffail = 0
        for side, items in groups.items():
            items.sort(key=lambda t: t[2] if side in ("L", "R") else t[1])
            for idx, (p, px, py, (nx, ny)) in enumerate(items):
                net = p.GetNet()
                nn = net.GetNetname()
                if (
                    nn in plane_nets
                    or nn.startswith("unconnected-")
                    or not nn
                    or npads.get(p.GetNetCode(), 0) < 2
                ):
                    skip += 1
                    continue
                w, cl, vd, vh = nc.params(nn)
                ww = min(w, neck)
                # 出到引脚阵列外沿之外；落点按序号小幅错开，避免连成一线
                base = abs((R0 if nx > 0 else L0) - px) if nx else abs((B0 if ny > 0 else T0) - py)
                got = None
                for extra in (
                    ESCAPE_CLEAR,
                    ESCAPE_CLEAR + ESCAPE_STAGGER * (idx % 3),
                    ESCAPE_CLEAR + 1.6,
                    ESCAPE_CLEAR + 2.6,
                ):
                    for adeg in (0, 12, -12, 24, -24, 35, -35):
                        ca = math.cos(math.radians(adeg))
                        sa = math.sin(math.radians(adeg))
                        ex, ey = nx * ca - ny * sa, nx * sa + ny * ca
                        d = base + extra
                        x = float(px + ex * d)
                        y = float(py + ey * d)
                        path = R.route(
                            p.GetNetCode(),
                            [(px, py, F)],
                            [(x, y, F)],
                            ww,
                            cl,
                            vd,
                            vh,
                            allow_via=False,
                            margin=5.0,
                            neck=ww,
                            neck_r=99.0,
                        )
                        if path is not None:
                            R.commit(path, net, ww, vd, vh, snap_a=(px, py))
                            access[(f.GetReference(), p.GetNumber())] = (x, y)
                            got = (x, y)
                            break
                    if got:
                        break
                if got:
                    ok += 1
                    fok += 1
                else:
                    fail += 1
                    ffail += 1
        per_fp[f.GetReference()] = "%d/%d" % (fok, fok + ffail)
    log["escape"] = {"per_footprint": per_fp, "ok": ok, "failed": fail, "skipped": skip}
    return access


def try_ripup(R, b, pcbnew, nc, neck, net, nn, ax, ay, bx, by, ww, cl, vd, vh, log):
    """拆掉起点附近的异网铜，布通目标，再把拆掉的重布回去。

    只在起点被封死时有意义。整体是原子的：目标布不通，或拆掉的重布不回去，
    一律还原，绝不为了一条连接净损失另外几条。
    """
    ncode = net.GetNetCode()
    for px, py in ((ax, ay), (bx, by)):
        victims = near_items(b, pcbnew, px, py, RIPUP_R, ncode)
        if not victims:
            continue
        snaps = snapshot(b, pcbnew, victims)
        for t in victims:
            b.Remove(t)
        R2 = rebuild(b, pcbnew)
        p2 = R2.route(
            ncode,
            [(ax, ay, L) for L in layers_at(R2, ax, ay, ncode)],
            [(bx, by, L) for L in layers_at(R2, bx, by, ncode)],
            ww,
            cl,
            vd,
            vh,
            allow_via=True,
            margin=max(25.0, 0.9 * math.hypot(bx - ax, by - ay)),
            neck=ww,
            neck_r=99.0,
        )
        if p2 is None:
            undo(b, [], pcbnew, snaps)
            R2 = rebuild(b, pcbnew)
            continue
        added = list(R2.commit(p2, net, ww, vd, vh, snap_a=(ax, ay), snap_b=(bx, by)) or [])
        # 把拆掉的重新布回去
        back_ok = True
        for s in snaps:
            if s["kind"] == "via":
                continue  # 过孔随走线一起由 A* 重新决定
            vnet = b.FindNet(s["net"])
            if vnet is None:
                continue
            w2, cl2, vd2, vh2 = nc.params(vnet.GetNetname())
            p3 = R2.route(
                s["net"],
                [(s["x1"], s["y1"], L) for L in layers_at(R2, s["x1"], s["y1"], s["net"])],
                [(s["x2"], s["y2"], L) for L in layers_at(R2, s["x2"], s["y2"], s["net"])],
                min(w2, neck),
                cl2,
                vd2,
                vh2,
                allow_via=True,
                margin=18.0,
                neck=min(w2, neck),
                neck_r=99.0,
            )
            if p3 is None:
                back_ok = False
                break
            added += list(
                R2.commit(
                    p3,
                    vnet,
                    min(w2, neck),
                    vd2,
                    vh2,
                    snap_a=(s["x1"], s["y1"]),
                    snap_b=(s["x2"], s["y2"]),
                )
                or []
            )
        if back_ok:
            log.setdefault("ripup", {"attempts": 0, "success": 0, "removed": 0})
            log["ripup"]["attempts"] += 1
            log["ripup"]["success"] += 1
            log["ripup"]["removed"] += len(snaps)
            return True, R2
        # 重布不回去，撤销本次尝试
        undo(b, added, pcbnew, snaps)
        R2 = rebuild(b, pcbnew)
        log.setdefault("ripup", {"attempts": 0, "success": 0, "removed": 0})
        log["ripup"]["attempts"] += 1
        return None, R2
    return None, R


def mst_edges(pts):
    n = len(pts)
    if n < 2:
        return []
    used, rest, edges = [0], list(range(1, n)), []
    while rest:
        best = None
        for a in used:
            for c in rest:
                d = math.hypot(pts[a][0] - pts[c][0], pts[a][1] - pts[c][1])
                if best is None or d < best[0]:
                    best = (d, a, c)
        edges.append((best[1], best[2]))
        used.append(best[2])
        rest.remove(best[2])
    return edges


RIPUP_R = 5.0  # mm，撕线半径。太大会拆掉过多、重布代价高


def snapshot(b, pcbnew, items):
    """记录被拆对象的几何，便于重布或还原。"""
    out = []
    for t in items:
        if t.Type() == pcbnew.PCB_VIA_T:
            out.append(
                {
                    "kind": "via",
                    "net": t.GetNetCode(),
                    "x": mm(t.GetPosition().x),
                    "y": mm(t.GetPosition().y),
                    "drill": K.via_drill(t),
                    # 直径必须一并记下。只设钻径会让还原出来的过孔用默认直径，
                    # DRC 报一片 via_diameter。GetWidth 在 KiCad 10 需要层参数。
                    "d": mm(t.GetWidth(pcbnew.F_Cu)),
                }
            )
        else:
            out.append(
                {
                    "kind": "track",
                    "net": t.GetNetCode(),
                    "layer": t.GetLayer(),
                    "w": mm(t.GetWidth()),
                    "x1": mm(t.GetStart().x),
                    "y1": mm(t.GetStart().y),
                    "x2": mm(t.GetEnd().x),
                    "y2": mm(t.GetEnd().y),
                }
            )
    return out


def restore(b, pcbnew, snaps):
    from pcbnew import VECTOR2I

    out = []
    for s in snaps:
        if s["kind"] == "via":
            v = pcbnew.PCB_VIA(b)
            v.SetPosition(VECTOR2I(MM(s["x"]), MM(s["y"])))
            v.SetViaType(pcbnew.VIATYPE_THROUGH)
            v.SetDrill(MM(s["drill"]))
            v.SetWidth(MM(s.get("d") or (s["drill"] + 0.3)))
            v.SetNetCode(s["net"])
            v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
            b.Add(v)
            K.disown(v)
            out.append(v)
        else:
            if abs(s["x1"] - s["x2"]) < 1e-6 and abs(s["y1"] - s["y2"]) < 1e-6:
                continue  # 零长度走线塞回去会让铺铜填充器崩
            t = pcbnew.PCB_TRACK(b)
            t.SetStart(VECTOR2I(MM(s["x1"]), MM(s["y1"])))
            t.SetEnd(VECTOR2I(MM(s["x2"]), MM(s["y2"])))
            t.SetLayer(s["layer"])
            t.SetWidth(MM(s["w"]))
            t.SetNetCode(s["net"])
            b.Add(t)
            K.disown(t)
            out.append(t)
    return out


def undo(b, added, pcbnew, snaps):
    """撤销一次撕线尝试：删掉本次新增的对象，再把拆掉的原样放回。

    两个坑都在这里：
      1  必须连同「已经布好的目标走线」一起撤掉。早先只还原被拆的铜，
         目标走线留在板上与之重叠，20 次尝试里 16 次失败就留 16 对重叠，
         DRC 随即出现大批 tracks_crossing / shorting_items。
      2  不能用「删光全部走线再整体还原」的方式回滚。上千次增删会让
         board.GetTracks() 自身退化成不可迭代的 SwigPyObject。
         只撤本次新增的十几个对象，churn 小得多。
    """
    for t in added:
        b.Remove(t)
    return restore(b, pcbnew, snaps)


def near_items(b, pcbnew, x, y, r, ncode, limit=16):
    """起点附近属于其它网络的铜，按距离由近及远取前 limit 个。

    密集区候选轻易超过十几个。早先的实现是「超过上限就整体放弃」，
    结果恰恰在最需要撕线的地方不撕。改成取最近的若干个：拆得少、
    重布代价低，失败了也能整体还原。
    """
    out = []
    for t in K.tracks_of(b):
        if t.GetNetCode() == ncode:
            continue
        if t.Type() == pcbnew.PCB_VIA_T:
            dv = math.hypot(mm(t.GetPosition().x) - x, mm(t.GetPosition().y) - y)
            if dv < r:
                out.append((dv, t))
        else:
            ax, ay = mm(t.GetStart().x), mm(t.GetStart().y)
            bx, by = mm(t.GetEnd().x), mm(t.GetEnd().y)
            vx, vy = bx - ax, by - ay
            L2 = vx * vx + vy * vy
            if L2 > 1e-9:
                u = max(0.0, min(1.0, ((x - ax) * vx + (y - ay) * vy) / L2))
                d = math.hypot(ax + u * vx - x, ay + u * vy - y)
            else:
                d = math.hypot(ax - x, ay - y)
            if d < r:
                out.append((d, t))
    out.sort(key=lambda z: z[0])
    return [t for _, t in out[:limit]]


def rebuild(b, pcbnew, tracks=None):
    R = Router(b, lambda s: None)
    paint_existing(R, b, pcbnew, tracks)
    return R


def mode_repair(R, b, pcbnew, nc, neck, log, ripup=False):
    d = run_drc(R.b.GetFileName() or "")
    un = d.get("unconnected_items", [])
    log["targets"] = len(un)
    ok = fail = 0
    per_net = {}
    for u in un:
        items = u.get("items", [])
        if len(items) < 2:
            continue
        nn = None
        for it in items:
            m = re.search(r"\[([^\]]+)\]", it.get("description", ""))
            if m:
                nn = m.group(1)
                break
        if not nn:
            continue
        net = b.FindNet(nn)
        if net is None:
            continue
        w, cl, vd, vh = nc.params(nn)
        ww = min(w, neck)
        pa, pb = items[0].get("pos") or {}, items[1].get("pos") or {}
        ax, ay = pa.get("x", 0), pa.get("y", 0)
        bx, by = pb.get("x", 0), pb.get("y", 0)
        ncode = net.GetNetCode()
        dist = math.hypot(bx - ax, by - ay)
        path = R.route(
            ncode,
            [(ax, ay, L) for L in layers_at(R, ax, ay, ncode)],
            [(bx, by, L) for L in layers_at(R, bx, by, ncode)],
            ww,
            cl,
            vd,
            vh,
            allow_via=True,
            margin=max(25.0, 0.9 * dist),
            neck=ww,
            neck_r=99.0,
        )
        st = per_net.setdefault(nn, [0, 0])
        if path is None and ripup:
            path, R = try_ripup(
                R, b, pcbnew, nc, neck, net, nn, ax, ay, bx, by, ww, cl, vd, vh, log
            )
        if path is None:
            fail += 1
            st[1] += 1
        else:
            if path is not True:
                R.commit(path, net, ww, vd, vh, snap_a=(ax, ay), snap_b=(bx, by))
            ok += 1
            st[0] += 1
    log["routed"] = ok
    log["failed"] = fail
    log["unresolved"] = {k: v for k, v in sorted(per_net.items()) if v[1]}


def fanout_planes(R, b, pcbnew, nc, plane_nets, log):
    """给够不到内层平面的电源焊盘打扇出过孔。

    这一步不能省。顶层的贴片电源焊盘不会自己连到 In2 的平面上，
    缺了它，VSYS / +3V3 的几十个焊盘全部悬空——表现为「布线成功率很高，
    未连接数反而暴涨」。
    """
    res = {}
    served = set()
    for netname in sorted(plane_nets):
        if netname in ("GND",):
            served.add(netname)
            continue  # GND 顶层有铺铜，焊盘就近入铜，不需要逐个打孔
        net = b.FindNet(netname)
        if net is None:
            continue
        ncode = net.GetNetCode()
        w, cl, vd, vh = nc.params(netname)
        zones = [z for z in K.zones_of(b) if not z.GetIsRuleArea() and z.GetNetname() == netname]
        ok = fail = 0
        for f in K.footprints_of(b):
            for p in f.Pads():
                if p.GetNetCode() != ncode or not p.IsOnLayer(pcbnew.F_Cu):
                    continue
                if p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
                    continue  # 通孔焊盘本身已穿过内层
                px, py = mm(p.GetPosition().x), mm(p.GetPosition().y)
                spot = None
                for step in range(0, 40):
                    rad = step * 0.15
                    for ang in range(0, 360, 15) if step else (0,):
                        x = float(px + rad * math.cos(math.radians(ang)))
                        y = float(py + rad * math.sin(math.radians(ang)))
                        inside = any(
                            z.GetFilledPolysList(z.GetLayerSet().Seq()[0]).Collide(
                                VECTOR2I(MM(x), MM(y)), MM(0.35)
                            )
                            for z in zones
                        )
                        if inside and via_spot_ok(R, x, y, vd, ncode):
                            spot = (x, y)
                            break
                    if spot:
                        break
                if not spot:
                    fail += 1
                    continue
                if abs(spot[0] - px) > 1e-6 or abs(spot[1] - py) > 1e-6:
                    path = R.route(
                        ncode,
                        [(px, py, F)],
                        [(spot[0], spot[1], F)],
                        min(w, 0.5),
                        cl,
                        vd,
                        vh,
                        allow_via=False,
                        margin=6.0,
                        neck=min(w, 0.5),
                        neck_r=99.0,
                    )
                    if path is None:
                        fail += 1
                        continue
                    R.commit(path, net, min(w, 0.5), vd, vh, snap_a=(px, py))
                place_via(R, b, pcbnew, spot[0], spot[1], net, vd, vh)
                ok += 1
        res[netname] = "%d/%d" % (ok, ok + fail)
        if fail == 0:
            served.add(netname)
    log["fanout"] = res
    return served


def via_spot_ok(R, x, y, d, ncode):
    r = int(math.ceil(((d / 2.0) + 0.35) / GRID))
    i, j = R.gx(x), R.gy(y)
    if not (r <= i < R.W - r and r <= j < R.H - r):
        return False
    for L in (F, B):
        sub = R.occ[L][j - r : j + r + 1, i - r : i + r + 1]
        if ((sub != 0) & (sub != ncode)).any():
            return False
    return True


def place_via(R, b, pcbnew, x, y, net, vd, vh):
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(VECTOR2I(MM(x), MM(y)))
    v.SetViaType(pcbnew.VIATYPE_THROUGH)
    v.SetDrill(MM(vh))
    v.SetWidth(MM(vd))
    v.SetNet(net)
    v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
    b.Add(v)
    K.disown(v)
    R.paint_via(x, y, vd, net.GetNetCode())


def pad_layers(p, pcbnew):
    """焊盘真实存在的层。拆掉网络自身铜之后 layers_at 会退化成 [F, B] 兜底，
    那会让走线起止在没有焊盘的一层，静默接不上。"""
    out = []
    if p.IsOnLayer(pcbnew.F_Cu):
        out.append(F)
    if p.IsOnLayer(pcbnew.B_Cu):
        out.append(B)
    return out or [F]


def rewidth_targets(b, pcbnew, nc, classes):
    """挑出要按目标线宽重布的网络，只返回网络名，不返回对象。"""
    plane_nets = {z.GetNetname() for z in K.zones_of(b) if not z.GetIsRuleArea()}
    pads = {}
    for f in K.footprints_of(b):
        for p in f.Pads():
            if p.GetNetCode():
                pads.setdefault(p.GetNetCode(), []).append(p)
    out = []
    for code, ps in pads.items():
        net = b.FindNet(code)
        if net is None or len(ps) < 2:
            continue
        nn = net.GetNetname()
        if nn in plane_nets:
            continue  # 平面网络的电流走平面，走线只是过孔桩，不重布
        if nc.name_for(nn) not in classes:
            continue
        span = max(
            math.hypot(
                mm(a.GetPosition().x) - mm(c.GetPosition().x),
                mm(a.GetPosition().y) - mm(c.GetPosition().y),
            )
            for a in ps
            for c in ps
        )
        out.append((span, nn))
    # 长的先重布：短的更容易见缝插针，留到后面
    out.sort(reverse=True)
    return [nn for _span, nn in out]


def rewidth_one(path, pcbnew, nc, neck, nn):
    """在一份**新加载**的板子上重布一条网络，返回 (结果文本, 原始几何快照)。

    每条网络单独 LoadBoard 是刻意的。同一进程里持续增删之后，SWIG 代理会
    退化——不只是缓存下来的旧代理，连 board.GetTracks() 本身都会抛
    AttributeError: 'SwigPyObject' object has no attribute 'x'。试过缓存
    权威列表、试过每次重新取，都挡不住，因为根因是进程内累积。
    重新加载一次就把代理表清干净了，代价只有一次读盘。
    跨轮次要传的状态一律用几何快照（纯 dict），不要传对象。
    """
    b = pcbnew.LoadBoard(path)
    net = None
    for n in range(1, b.GetNetInfo().GetNetCount()):
        cand = b.FindNet(n)
        if cand is not None and cand.GetNetname() == nn:
            net = cand
            break
    if net is None:
        return "跳过（板上找不到该网络）", None
    ncode = net.GetNetCode()
    ps = [p for f in K.footprints_of(b) for p in f.Pads() if p.GetNetCode() == ncode]
    if len(ps) < 2:
        return "跳过（焊盘少于两个）", None
    w, cl, vd, vh = nc.params(nn)

    victims = [t for t in K.tracks_of(b) if t.GetNetCode() == ncode]
    snaps = snapshot(b, pcbnew, victims)
    for t in victims:
        b.Remove(t)
    R = rebuild(b, pcbnew)
    pts = [(mm(p.GetPosition().x), mm(p.GetPosition().y), pad_layers(p, pcbnew)) for p in ps]

    # 目标宽度布不通时逐档降低，而不是直接放弃。0.2 mm 的 2 A 线换成
    # 0.8 mm 是从 0.9 A 提到 2.2 A，远胜于「保持原状」。实落宽度如实上报。
    # 最后一档一定是缩颈宽度。没有它，「布不通」只说明装不下 0.30，
    # 而这条网络现状就是 0.20 —— 一条本来好好的线被判成失败，整组拆除之后
    # 就再也回不来了。兜底档保证结果不会比现状差。
    ladder = [x for x in (w, round(w * 0.75, 2), round(w * 0.5, 2)) if x > neck + 1e-9] + [neck]
    for w_try in ladder:
        plans, ok = [], True
        for a, c in mst_edges([(x, y) for x, y, _ in pts]):
            p = R.route(
                ncode,
                [(pts[a][0], pts[a][1], L) for L in pts[a][2]],
                [(pts[c][0], pts[c][1], L) for L in pts[c][2]],
                w_try,
                cl,
                vd,
                vh,
                allow_via=True,
                margin=max(18.0, 0.7 * math.hypot(pts[a][0] - pts[c][0], pts[a][1] - pts[c][1])),
                # 缩颈半径宁小勿大。改成按格判定宽度之后，本以为放大
                # 缩颈区不再有代价，实测相反：同一块板 1.0 保住 6 条，
                # 2.5 只剩 1 条，其余全被 DRC 自验回退。放宽的校验口径
                # 会让路径钻进事后填不回宽度的缝里。
                neck=min(w_try, neck),
                neck_r=REWIDTH_NECK_R,
            )
            if p is None:
                ok = False
                break
            plans.append((p, (pts[a][0], pts[a][1]), (pts[c][0], pts[c][1])))
        if not ok:
            continue  # 本档不通，降档再算；板上什么都还没落
        added = []
        for p, sa, sb in plans:
            added += list(R.commit(p, net, w_try, vd, vh, snap_a=sa, snap_b=sb) or [])
        tot = full = 0.0
        for t in added:
            if t.Type() != pcbnew.PCB_TRACE_T:
                continue
            ln = math.hypot(
                mm(t.GetEnd().x) - mm(t.GetStart().x), mm(t.GetEnd().y) - mm(t.GetStart().y)
            )
            tot += ln
            if mm(t.GetWidth()) >= w_try - 1e-6:
                full += ln
        K.fill_and_save(pcbnew, b, path)
        return (
            "重布 目标%.2f 实落%.2f 达标 %.0f%%（%.1f/%.1f mm）"
            % (w, w_try, 100.0 * full / max(tot, 1e-9), full, tot)
        ), snaps
    # 一档都不通：板子没存盘，磁盘上还是原样，什么都不用还原
    return "保持原状（降到 %.2f mm 仍布不通）" % ladder[-1], None


def revert_net(path, pcbnew, nn, snaps):
    """把一条网络还原成重布前的几何。同样一次加载一次存盘。"""
    b = pcbnew.LoadBoard(path)
    code = None
    for n in range(1, b.GetNetInfo().GetNetCount()):
        cand = b.FindNet(n)
        if cand is not None and cand.GetNetname() == nn:
            code = cand.GetNetCode()
            break
    if code is None:
        return
    for t in [t for t in K.tracks_of(b) if t.GetNetCode() == code]:
        b.Remove(t)
    restore(b, pcbnew, snaps)
    K.fill_and_save(pcbnew, b, path)


def mode_full(R, b, pcbnew, nc, neck, log):
    plane_nets = set(log.get("plane_nets", []))
    # 只有扇出完整的网络才真正由平面服务。像 +3V3 这种平面只覆盖局部区域的，
    # 够不到平面的焊盘必须照常走线——否则它们会被当成「平面已连」整个跳过，
    # 表现为布线成功率很高而未连接数居高不下。
    served = fanout_planes(R, b, pcbnew, nc, plane_nets, log)
    log["plane_served"] = sorted(served)
    access = escape_pins(R, b, pcbnew, nc, neck, served, log)
    plane_nets = served
    pads_by_net = {}
    for f in K.footprints_of(b):
        for p in f.Pads():
            code = p.GetNetCode()
            if code:
                pads_by_net.setdefault(code, []).append(p)
    todo = []
    for code, ps in pads_by_net.items():
        net = b.FindNet(code)
        if net is None or len(ps) < 2:
            continue
        nn = net.GetNetname()
        if nn.startswith("unconnected-"):
            continue
        # 有平面服务的网络不逐对布线
        if nn in log.get("plane_nets", []):
            continue
        pts = []
        for p in ps:
            key = (p.GetParentFootprint().GetReference(), p.GetNumber())
            pts.append(access.get(key, (mm(p.GetPosition().x), mm(p.GetPosition().y))))
        todo.append((net, pts))
    # 短连接优先：本地的去耦、自举、电荷泵先占位，再布长距离干线
    todo.sort(key=lambda t: max(math.hypot(a[0] - c[0], a[1] - c[1]) for a in t[1] for c in t[1]))
    ok = fail = 0
    per_net = {}
    for net, pts in todo:
        nn = net.GetNetname()
        w, cl, vd, vh = nc.params(nn)
        ww = min(w, neck)
        ncode = net.GetNetCode()
        st = per_net.setdefault(nn, [0, 0])
        for a, c in mst_edges(pts):
            path = R.route(
                ncode,
                [(pts[a][0], pts[a][1], L) for L in layers_at(R, pts[a][0], pts[a][1], ncode)],
                [(pts[c][0], pts[c][1], L) for L in layers_at(R, pts[c][0], pts[c][1], ncode)],
                ww,
                cl,
                vd,
                vh,
                allow_via=True,
                margin=max(14.0, 0.5 * math.hypot(pts[a][0] - pts[c][0], pts[a][1] - pts[c][1])),
                neck=ww,
                neck_r=99.0,
            )
            if path is None:
                fail += 1
                st[1] += 1
            else:
                R.commit(path, net, ww, vd, vh, snap_a=pts[a], snap_b=pts[c])
                ok += 1
                st[0] += 1
    log["routed"] = ok
    log["failed"] = fail
    log["unresolved"] = {k: v for k, v in sorted(per_net.items()) if v[1]}


def _child(path, extra):
    """把一次「只碰一块板一次」的工作交给独立子进程。

    KiCad 的 SWIG 绑定在同一进程里**反复 LoadBoard 会退化**：第二次拿到的
    board 是裸 SwigPyObject，连 GetNetInfo 都没有。逐网络重布天然需要多次
    加载，所以只能一条网络一个进程。跨进程传的是几何快照（JSON），不是对象。
    """
    cmd = [
        sys.executable,
        "-B",
        "-u",
        os.path.abspath(__file__),
        "--board",
        path,
        "--internal",
    ] + extra
    r = subprocess.run(cmd, capture_output=True, timeout=1800)
    out = (r.stdout or b"").decode("utf-8", "replace").splitlines()
    for line in out:  # SWIG 的 C 层噪声可能抢在前面
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                pass
    if _child.soft:
        _child.last_err = {
            "cmd": extra,
            "rc": r.returncode,
            "stderr": (r.stderr or b"").decode("utf-8", "replace")[-300:],
        }
        return None
    K.fail(
        "E_IO",
        "子进程没有返回信封",
        {
            "cmd": extra,
            "rc": r.returncode,
            "stdout": chr(10).join(out[-6:])[-500:],
            "stderr": (r.stderr or b"")
            .decode("utf-8", "replace")
            .replace("swig/python detected a memory leak", "")[-700:],
        },
    )


_child.soft = False
_child.last_err = None


def _emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + chr(10))
    sys.stdout.flush()
    os._exit(0)


def main():
    args = K.parse_args(
        sys.argv[1:], flags=("dry-run", "ripup", "internal", "no-verify", "no-restore")
    )
    path = K.board_arg(args)
    mode = args.get("mode", "repair")
    neck = float(args.get("neck", NECK_DEFAULT))
    if mode not in ("full", "repair", "rewidth"):
        K.fail("E_USAGE", "--mode 只能是 full / repair / rewidth", {"got": mode})
    classes = tuple((args.get("classes") or "PWR_MAIN,BTL_OUT,SWITCH").split(","))

    pcbnew = K.import_pcbnew()
    if args.get("internal"):
        pro, _ = K.load_project(path)
        nc = K.NetClasses(pro)
        sub = args.get("sub")
        if sub == "plan":
            b = K.load_board(pcbnew, path)
            _emit({"names": rewidth_targets(b, pcbnew, nc, classes)})
        if sub == "one":
            txt, snaps = rewidth_one(path, pcbnew, nc, neck, args["net"])
            _emit({"txt": txt, "snaps": snaps})
        if sub == "strip":
            # 整组一起拆。不这样做的话「顺序即优先级」是假的：
            # 逐条拆逐条布时，排第一的那条面对的仍是其余各条的**旧**走线。
            b = K.load_board(pcbnew, path)
            want = set(x.strip() for x in (args.get("nets") or "").split(",") if x.strip())
            snaps = {}
            victims = [t for t in K.tracks_of(b) if t.GetNetname() in want]
            for t in victims:
                snaps.setdefault(t.GetNetname(), []).extend(snapshot(b, pcbnew, [t]))
            for t in victims:
                b.Remove(t)
            K.fill_and_save(pcbnew, b, path)
            _emit({"snaps": snaps, "stripped": len(victims)})
        if sub == "revert":
            with open(args["snap"], encoding="utf-8") as f:
                revert_net(path, pcbnew, args["net"], json.load(f))
            _emit({"ok": True})
        if sub == "stats":
            b = K.load_board(pcbnew, path)
            tr = [t for t in K.tracks_of(b) if t.Type() == pcbnew.PCB_TRACE_T]
            _emit(
                {
                    "unconnected": K.unconnected(b),
                    "tracks": len(tr),
                    "vias": len(K.tracks_of(b)) - len(tr),
                    "len_mm": round(
                        sum(
                            math.hypot(
                                mm(t.GetEnd().x) - mm(t.GetStart().x),
                                mm(t.GetEnd().y) - mm(t.GetStart().y),
                            )
                            for t in tr
                        ),
                        1,
                    ),
                }
            )
        K.fail("E_USAGE", "--internal 需要 --sub plan|one|revert|stats")

    b = K.load_board(pcbnew, path)
    pro, _ = K.load_project(path)
    nc = K.NetClasses(pro)

    before_un = K.unconnected(b)
    before_tr = len([t for t in K.tracks_of(b) if t.Type() == pcbnew.PCB_TRACE_T])

    preview = {
        "board": path,
        "mode": mode,
        "neck_mm": neck,
        "unconnected_before": before_un,
        "tracks_before": before_tr,
        "will": {
            "full": "清空全部既有布线后重布",
            "repair": "保留现状，只补未连接项",
            "rewidth": (
                "按指定顺序重布 %s" % args["nets"]
                if args.get("nets")
                else "拆掉 %s 类网络并按目标线宽重布" % ",".join(classes)
            ),
        }[mode],
    }
    K.check_confirm(
        args.get("confirm"), preview, "pcb_route:%s:%s" % (mode, os.path.basename(path)), path
    )

    log = {"mode": mode}
    # repair 和 full 的判据基线：必须在任何改动落盘之前取。
    # 这两个模式过去完全不跑 DRC，只数连通性——而 full 的定义就是清空全板走线。
    # 一个察觉不到自己把板子改坏的模式，回滚机制对它毫无意义：没有东西会触发回滚。
    errors_baseline = None
    width_before = None
    if mode in ("repair", "full"):
        errors_baseline = K.err_count(run_drc(path))
        # Widths are the thing DRC cannot see. full routes at neck width by
        # design and expects `board widen` after, which the note has always
        # said -- in prose, to a caller that acts on fields.
        width_before = K.width_summary(b, pcbnew, nc)

    if mode == "rewidth":
        # 先把当前状态落盘，之后每条网络都从磁盘重新加载一份干净的板子。
        K.fill_and_save(pcbnew, b, path)
        del b  # 父进程从此不再碰板子，全部交给子进程
        if args.get("nets"):
            # 显式指定网络与顺序。这是解「先布的占了走廊、后布的没位置」的正解：
            # 把最难布或最要紧的排在最前面，让它先挑车道，其余的绕着它走。
            names = [x.strip() for x in args["nets"].split(",") if x.strip()]
        else:
            names = _child(path, ["--sub", "plan", "--classes", ",".join(classes), "--mode", mode])[
                "names"
            ]
        res, keep = {}, {}
        pre, need_restore = {}, []
        grp_bak = None
        if args.get("nets"):
            # 整组拆重布跨十几个子进程，任何一个崩掉都会把板子留在半截状态。
            # 先存一份整盘备份，出事直接回滚——per-net 还原救不了崩溃。
            d0 = run_drc(path)
            unconn_before_names = set()
            for u in d0.get("unconnected_items", []):
                for it in u.get("items", []):
                    m0 = re.search(r"\[([^\]]+)\]", it.get("description", ""))
                    if m0:
                        unconn_before_names.add(m0.group(1))
            grp_bak = os.path.join(tempfile.gettempdir(), "kicad_layout_group_backup.kicad_pcb")
            shutil.copyfile(path, grp_bak)
            _child.soft = True
            st0 = _child(path, ["--sub", "strip", "--nets", args["nets"], "--mode", mode])
            if st0 is None:
                shutil.copyfile(grp_bak, path)
                K.fail("E_IO", "整组拆除失败，板子已整盘回滚到操作之前", _child.last_err)
            pre = st0.get("snaps") or {}
            log["stripped_segments"] = st0.get("stripped")
        for nn in names:
            r = _child(path, ["--sub", "one", "--net", nn, "--neck", str(neck), "--mode", mode])
            if r is None:
                if grp_bak:
                    shutil.copyfile(grp_bak, path)
                    K.fail("E_IO", "重布 %s 时子进程崩溃，板子已整盘回滚" % nn, _child.last_err)
                K.fail("E_IO", "重布 %s 时子进程崩溃" % nn, _child.last_err)
            if r["txt"].startswith("跳过"):
                log.setdefault("skipped_nets", []).append(nn)
            res[nn] = r["txt"]
            if r.get("snaps"):
                keep[nn] = r["snaps"]
            if nn in pre:
                if args.get("no-restore"):
                    # 搬过器件之后用这个。拆之前的几何连的是**旧坐标上的焊盘**，
                    # 还原回去等于往板上塞一堆通向空气的线：未连接数不降，
                    # 还会跟新布的线打架。这种情况下宁可留空交给补布。
                    if not r["txt"].startswith("重布"):
                        res[nn] = "拆后布不通，留空待补布（器件已挪位，不还原旧走线）"
                    keep[nn] = []  # DRC 否掉时只删新线，不还原
                    continue
                if not r["txt"].startswith("重布"):
                    # 还原要等**全组布完**再做。就地还原会把旧铜塞回一块
                    # 「这条网络不存在」的板上，而后续网络正是在那个前提下
                    # 布的——旧铜一回来就压在它们身上，DRC 报一片组内短路。
                    need_restore.append(nn)
                keep[nn] = pre[nn]  # DRC 自验回退一律回到拆前的几何
        for nn in need_restore:
            # 只还原「拆之前本来是连通的」网络。本来就断着的（比如 BSNL）
            # 还原回去只是把半截垃圾线塞回板上，不如让它空着等下一步处理。
            if nn in unconn_before_names:
                res[nn] = "拆后布不通，原本也未连通，未还原（留空待处理）"
                keep.pop(nn, None)
                continue
            sf = os.path.join(tempfile.gettempdir(), "kicad_layout_snap.json")
            with open(sf, "w", encoding="utf-8") as f:
                json.dump(pre[nn], f)
            if _child(path, ["--sub", "revert", "--net", nn, "--snap", sf, "--mode", mode]) is None:
                shutil.copyfile(grp_bak, path)
                K.fail("E_IO", "还原 %s 时子进程崩溃，板子已整盘回滚" % nn, _child.last_err)
            res[nn] = "已还原（整组重布后仍布不通，恢复原走线）"
        log["rewidth"] = res
        log["rewidth_total"] = len(res)
        # 栅格间距模型是近似的，DRC 才是最终裁判。凡是重布后引入 error 的
        # 网络，按网络粒度还原——宁可这条线宽不达标，也不让 error 数上升。
        # 必须迭代：还原 A 之后，A 的旧路径可能与 B 的新粗线打架，一轮不闭合。
        if not args.get("no-verify") and keep:
            alive, reverted = dict(keep), []
            for _round in range(5):
                drc = run_drc(path)
                bad = set()
                for v in drc.get("violations", []):
                    if v["severity"] != "error":
                        continue
                    for it in v.get("items", []):
                        m2 = re.search(r"\[([^\]]+)\]", it.get("description", ""))
                        if m2 and m2.group(1) in alive:
                            bad.add(m2.group(1))
                            log.setdefault("rewidth_drc_cause", []).append(
                                {
                                    "why": v["description"],
                                    "items": [x.get("description", "") for x in v.get("items", [])],
                                }
                            )
                if not bad:
                    break
                for nn2 in bad:
                    sf = os.path.join(tempfile.gettempdir(), "kicad_layout_snap.json")
                    with open(sf, "w", encoding="utf-8") as f:
                        json.dump(alive.pop(nn2), f)
                    _child(path, ["--sub", "revert", "--net", nn2, "--snap", sf, "--mode", mode])
                    res[nn2] = "已还原（重布引入 DRC error）"
                    reverted.append(nn2)
            if reverted:
                log["rewidth_reverted"] = sorted(reverted)
        log["rewidth_ok"] = sum(1 for v in res.values() if v.startswith("重布"))
        st = _child(path, ["--sub", "stats", "--mode", mode])
        K.ok(
            route_envelope(
                {
                    **log,
                    "unconnected_before": before_un,
                    "unconnected_after": st["unconnected"],
                    "tracks": st["tracks"],
                    "improved": before_un - st["unconnected"],
                    "track_len_mm": st["len_mm"],
                    "vias": st["vias"],
                    # rewidth 的判据是逐网络的 DRC 回退循环（见上），不是整盘
                    # 基线比较，所以这里如实说明它验的是什么。
                    "verify": {
                        "ran": not args.get("no-verify"),
                        "oracle": "kicad-cli pcb drc",
                        "scope": "per-net revert loop",
                        "reverted": log.get("rewidth_reverted") or [],
                        "note": "引入 DRC error 的网络已逐条还原；未做整盘 error 基线比较",
                    },
                    "note": "线宽按目标值重布；仍未达标的段是真的放不下",
                }
            )
        )
    elif mode == "repair":
        b.SetFileName(path)
        R = Router(b, lambda s: None)
        log["existing_tracks"] = paint_existing(R, b, pcbnew)
        mode_repair(R, b, pcbnew, nc, neck, log, ripup=bool(args.get("ripup")))
    else:
        # full 的定义就是「清空既有布线后重布」。这一步在一次重构里被删掉过，
        # 后果是新线叠在旧线上：走线段数翻倍，DRC 报 548 个 error。
        # 症状看起来像布线器失控，实际是少了一行清空。
        old_tracks = list(K.tracks_of(b))
        for t in old_tracks:
            b.Remove(t)
        b.BuildConnectivity()
        log["cleared_tracks"] = len(old_tracks)
        R = Router(b, lambda s: None)
        mode_full(R, b, pcbnew, nc, neck, log)

    K.fill_and_save(pcbnew, b, path)
    after_un = K.unconnected(b)
    # DRC 才是裁判，栅格和连通性都不是。error 数超过动手前的基线就整盘回滚：
    # K.fail 会让 write_txn 把板子恢复成写之前的字节。
    errors_final = K.err_count(run_drc(path))
    if errors_final > errors_baseline:
        K.fail(
            "E_INTEGRITY",
            "布线后 DRC error 数高于动手前，已整盘回滚",
            {
                "mode": mode,
                "errors_baseline": errors_baseline,
                "errors_final": errors_final,
                "next_action": "板子已恢复原状。改用 --mode repair 逐步修，或先处理已有的 "
                "error 再重试",
            },
        )
    width_after = K.width_summary(b, pcbnew, nc)
    log["verify"] = {
        "ran": True,
        "oracle": "kicad-cli pcb drc",
        "errors_baseline": errors_baseline,
        "errors_final": errors_final,
        "width_before": width_before,
        "width_after": width_after,
        "width_regressed": bool(
            width_before
            and width_after
            and width_before.get("compliant_pct") is not None
            and width_after.get("compliant_pct") is not None
            and width_after["compliant_pct"] < width_before["compliant_pct"]
        ),
        "note": "error 数未超过动手前的基线；这不等于板子没问题，只等于 DRC 没变差。"
        "线宽是 DRC 看不见的那部分：width_regressed 为真时走线已按缩颈宽度重布，"
        "载流能力随之下降，必须再跑 board widen 或 board rewidth 才算完成",
    }
    all_tr = list(K.tracks_of(b))
    tr = [t for t in all_tr if t.Type() == pcbnew.PCB_TRACE_T]
    total = sum(
        math.hypot(mm(t.GetEnd().x) - mm(t.GetStart().x), mm(t.GetEnd().y) - mm(t.GetStart().y))
        for t in tr
    )
    note = K.progress_note(mode, before_un, after_un)
    K.ok(
        route_envelope(
            {
                **log,
                "unconnected_before": before_un,
                "unconnected_after": after_un,
                "improved": before_un - after_un,
                "tracks": len(tr),
                "track_len_mm": round(total, 1),
                "vias": len(all_tr) - len(tr),
                "note": note,
            }
        )
    )


if __name__ == "__main__":
    main()
