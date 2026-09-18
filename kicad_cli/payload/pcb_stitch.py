"""给铺铜孤岛补缝合过孔，让接地不再依赖走线。

铺铜被走线切碎之后会留下一堆孤岛。危险的不是碎，是**碎出来的岛上坐着焊盘
却没有过孔下到内层平面**——那些焊盘的接地就只剩一小片孤立的铜，全靠横穿
整板的长走线串起来。表现是 DRC 报一串「填充区 ↔ 填充区」未连接，以及
回流路径极长。

本脚本只做一件事：为每一座缺过孔的孤岛，在岛内找一个安全位置打一个过孔
下到平面层。做完之后那些长走线才变成冗余，可以安全删掉。

用法：
    <kicad-python> pcb_stitch.py --board B.kicad_pcb                 # 试跑
    <kicad-python> pcb_stitch.py --board B.kicad_pcb --confirm ct_x  # 落实
    可选 --net GND（默认 GND）、--min-area 0.5（小于此面积的岛视为死铜，不补）
    可选 --bridge：放不下过孔的细条岛，改用一小段走线并入旁边已接通的铜
                   （--bridge-max 2.0 限制最大跨度）
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kicad_lib as K  # noqa: E402

HOLE2HOLE = 0.25  # mm，KiCad 默认孔到孔间距
EDGE_MARGIN = 0.05  # mm，过孔盘边到岛边界再留一点
STEP = 0.4  # mm，候选点采样步长


def poly_area(pts):
    n = len(pts)
    return (
        abs(
            sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))
        )
        / 2.0
    )


def point_in_poly(pts, x, y):
    n = len(pts)
    c = False
    j = n - 1
    for i in range(n):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            c = not c
        j = i
    return c


def dist_to_poly(pts, x, y):
    """点到多边形边界的最短距离。"""
    best = 1e9
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 < 1e-12 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / L2))
        best = min(best, math.hypot(x - (x1 + t * dx), y - (y1 + t * dy)))
    return best


def seg_dist(x, y, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 < 1e-12 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / L2))
    return math.hypot(x - (x1 + t * dx), y - (y1 + t * dy))


def islands_of(b, pcbnew, mm, net, layer):
    """返回该层上属于 net 的铺铜孤岛，按面积从大到小。"""
    out = []
    for z in K.zones_of(b):
        if z.GetIsRuleArea() or not z.IsOnLayer(layer):
            continue
        if z.GetNetname() != net:
            continue
        pl = z.GetFilledPolysList(layer)
        for i in range(pl.OutlineCount()):
            o = pl.Outline(i)
            pts = [(mm(o.CPoint(k).x), mm(o.CPoint(k).y)) for k in range(o.PointCount())]
            if len(pts) >= 3:
                out.append({"pts": pts, "area": poly_area(pts)})
    out.sort(key=lambda d: -d["area"])
    return out


def main():
    args = K.parse_args(sys.argv[1:], flags=("bridge", "no-verify"))
    path = K.board_arg(args)
    net = args.get("net", "GND")
    min_area = float(args.get("min-area", 0.5))

    pcbnew = K.import_pcbnew()
    mm = pcbnew.ToMM
    b = K.load_board(pcbnew, path)
    pro, _ = K.load_project(path)
    nc = K.NetClasses(pro)
    _w, clearance, via_d, via_h = nc.params(net)

    netobj = None
    for code in range(1, b.GetNetInfo().GetNetCount()):
        cand = b.FindNet(code)
        if cand is not None and cand.GetNetname() == net:
            netobj = cand
            break
    if netobj is None:
        K.fail("E_NOT_FOUND", "板上没有网络 %s" % net, {"net": net})
    ncode = netobj.GetNetCode()

    # ---- 障碍物快照。必须在任何写入之前取完（SWIG 代理会退化） ----------
    # KiCad 判间距取**两条网络类中较大的那个**。只按 GND 自己的 0.20 mm 算，
    # 就会贴到 PWR_LOGIC(0.25) 或 SWITCH(0.30) 的线上去，DRC 原样报回来。
    # 所以每个障碍物都要记下它自己要求的间距。
    cl_of = {}

    def need_cl(code):
        if code not in cl_of:
            n2 = b.FindNet(code)
            nm = n2.GetNetname() if n2 is not None else ""
            try:
                cl_of[code] = max(clearance, nc.params(nm)[1])
            except Exception:  # noqa: BLE001
                cl_of[code] = clearance
        return cl_of[code]

    foreign_pads = []  # (onF, onB, x, y, r, cl)
    holes = []  # (x, y, r)
    for f in K.footprints_of(b):
        for p in f.Pads():
            bb = p.GetBoundingBox()
            x, y = mm(p.GetPosition().x), mm(p.GetPosition().y)
            r = math.hypot(mm(bb.GetWidth()), mm(bb.GetHeight())) / 2.0
            if p.GetDrillSizeX() > 0:
                holes.append((x, y, mm(p.GetDrillSizeX()) / 2.0))
            if p.GetNetCode() != ncode:
                foreign_pads.append(
                    (
                        p.IsOnLayer(pcbnew.F_Cu),
                        p.IsOnLayer(pcbnew.B_Cu),
                        x,
                        y,
                        r,
                        need_cl(p.GetNetCode()),
                    )
                )
    foreign_trk = {pcbnew.F_Cu: [], pcbnew.B_Cu: []}
    for t in K.tracks_of(b):
        if t.Type() == pcbnew.PCB_VIA_T:
            holes.append((mm(t.GetPosition().x), mm(t.GetPosition().y), K.via_drill(t) / 2.0))
            if t.GetNetCode() != ncode:
                for L in (pcbnew.F_Cu, pcbnew.B_Cu):
                    foreign_trk[L].append(
                        (
                            mm(t.GetPosition().x),
                            mm(t.GetPosition().y),
                            mm(t.GetPosition().x),
                            mm(t.GetPosition().y),
                            K.via_drill(t) / 2.0 + 0.2,
                            need_cl(t.GetNetCode()),
                        )
                    )
            continue
        if t.Type() != pcbnew.PCB_TRACE_T or t.GetNetCode() == ncode:
            continue
        L = t.GetLayer()
        if L in foreign_trk:
            foreign_trk[L].append(
                (
                    mm(t.GetStart().x),
                    mm(t.GetStart().y),
                    mm(t.GetEnd().x),
                    mm(t.GetEnd().y),
                    mm(t.GetWidth()) / 2.0,
                    need_cl(t.GetNetCode()),
                )
            )
    # 禁布区：过孔绝不能落进去（射频净空就是靠它表达的）
    keepouts = []
    for z in K.zones_of(b):
        if not z.GetIsRuleArea():
            continue
        bb = z.GetBoundingBox()
        keepouts.append(
            (
                z.GetZoneName() or "keepout",
                mm(bb.GetLeft()),
                mm(bb.GetTop()),
                mm(bb.GetRight()),
                mm(bb.GetBottom()),
            )
        )

    # 已有的本网络过孔，用来判断某座岛是否已经通到平面
    own_vias = [
        (mm(t.GetPosition().x), mm(t.GetPosition().y))
        for t in K.tracks_of(b)
        if t.Type() == pcbnew.PCB_VIA_T and t.GetNetCode() == ncode
    ]

    def ok_here(x, y, other_layer, vd, vh, both=False):
        """过孔落在 (x,y) 是否安全。other_layer 是对面那层。

        间距必须逐障碍物取「两条网络类中较大的那个」。只按本网络的
        0.20 mm 算，就会贴到 PWR_LOGIC(0.25) 或 SWITCH(0.30) 的线上，
        DRC 原样报回来——实测漏了这条就出 2 个 clearance error。
        """
        for onF, onB, px, py, pr, ocl in foreign_pads:
            if (other_layer == pcbnew.F_Cu and not onF) or (other_layer == pcbnew.B_Cu and not onB):
                continue
            if math.hypot(x - px, y - py) < vd / 2.0 + ocl + pr:
                return False
        for x1, y1, x2, y2, hw, ocl in foreign_trk[other_layer]:
            if seg_dist(x, y, x1, y1, x2, y2) < vd / 2.0 + ocl + hw:
                return False
        for hx, hy, hr in holes:
            d = math.hypot(x - hx, y - hy)
            if 1e-6 < d < vh / 2.0 + hr + HOLE2HOLE:
                return False
        for _nm, L0, T0, R0, B0 in keepouts:
            m = vd / 2.0 + clearance
            if L0 - m <= x <= R0 + m and T0 - m <= y <= B0 + m:
                return False
        return True

    # 过孔尺寸降档梯子：类里的尺寸 → 工程允许的最小尺寸。
    # 最小值从 .kicad_pro 读，不写死，也绝不改它。
    rules = pro.get("board", {}).get("design_settings", {}).get("rules", {})
    min_d = float(rules.get("min_via_diameter", 0.5))
    min_h = float(rules.get("min_through_hole_diameter", 0.3))
    min_ann = float(rules.get("min_via_annular_width", 0.1))
    small_h = max(min_h, round(min_d - 2 * min_ann, 3))
    ladder = [(via_d, via_h)]
    if min_d < via_d - 1e-9:
        ladder.append((min_d, min(via_h, small_h)))

    plan, skipped = [], []
    for layer, other in ((pcbnew.F_Cu, pcbnew.B_Cu), (pcbnew.B_Cu, pcbnew.F_Cu)):
        lname = b.GetLayerName(layer)
        isl = islands_of(b, pcbnew, mm, net, layer)
        for _idx, d in enumerate(isl):
            pts = d["pts"]
            if any(point_in_poly(pts, vx, vy) for vx, vy in own_vias):
                continue  # 已有过孔通到平面
            if d["area"] < min_area:
                skipped.append(
                    {
                        "layer": lname,
                        "area": round(d["area"], 2),
                        "why": "面积过小，判为死铜，建议在铺铜属性里开启孤岛移除",
                    }
                )
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            # 步长要跟岛的尺度匹配。细条岛最窄处只有半毫米，用 0.4 mm 采样
            # 几乎必然落空，于是被误判成「放不下过孔」。
            step = STEP if d["area"] > 20.0 else 0.08
            n_x = max(1, int((max(xs) - min(xs)) / step) + 1)
            n_y = max(1, int((max(ys) - min(ys)) / step) + 1)
            best = None
            # 类里的过孔放不下时降档，但绝不越过工程自己写在 .kicad_pro 里的
            # 最小值。缩小过孔是在既有约束内挪腾，放宽约束是另一回事。
            # 三档：类里的过孔整个落在岛内 → 工程最小过孔整个落在岛内 →
            # 允许过孔压在岛边界上（中心仍在岛内）。最后一档是给细条形孤岛的：
            # 它最窄处塞不下整个焊盘，但过孔铜与岛重叠一样连得上，
            # 代价是间距必须**两层都**核，不能再靠「岛边界已经让开了」这个前提。
            tries = [(vd, vh, False) for (vd, vh) in ladder] + [
                (ladder[-1][0], ladder[-1][1], True)
            ]
            for vd, vh, loose in tries:
                need_in = 0.0 if loose else vd / 2.0 + EDGE_MARGIN
                for i in range(n_x):
                    for j in range(n_y):
                        x = min(xs) + i * step
                        y = min(ys) + j * step
                        if not point_in_poly(pts, x, y):
                            continue
                        m = dist_to_poly(pts, x, y)
                        if m < need_in:
                            continue
                        if not ok_here(x, y, other, vd, vh, both=loose):
                            continue
                        if best is None or m > best[2]:
                            best = (x, y, m, vd, vh)
                if best is not None:
                    break
            if best is None:
                skipped.append(
                    {
                        "layer": lname,
                        "area": round(d["area"], 2),
                        "center": [round(sum(xs) / len(xs), 1), round(sum(ys) / len(ys), 1)],
                        "why": "岛内放不下过孔（降到 %.2f/%.2f mm 仍不行）" % ladder[-1],
                    }
                )
                continue
            plan.append(
                {
                    "layer": lname,
                    "area": round(d["area"], 2),
                    "x": round(best[0], 3),
                    "y": round(best[1], 3),
                    "margin_mm": round(best[2], 3),
                    "via_mm": best[3],
                    "drill_mm": best[4],
                }
            )

    before = K.unconnected(b)
    preview = {
        "board": path,
        "net": net,
        "via_mm": via_d,
        "drill_mm": via_h,
        "islands_to_stitch": len(plan),
        "skipped": len(skipped),
        # 试跑就要能看清打在哪、为什么有的补不了，不用先写再检查
        "plan": plan,
        "skipped_detail": skipped,
        "unconnected_before": before,
        "bridge_enabled": bool(args.get("bridge")),
        "will": "为 %d 座缺过孔的 %s 铺铜孤岛各补一个过孔%s"
        % (
            len(plan),
            net,
            "；放不下过孔的细条岛尝试用短走线并入旁边已接通的铜" if args.get("bridge") else "",
        ),
    }
    K.check_confirm(
        args.get("confirm"), preview, "pcb_stitch:%s:%s" % (net, os.path.basename(path)), path
    )

    # ---- 搭桥：放不下过孔的细条岛，改为用一小段走线并进旁边已接通的铜 ----
    # 环形或细条形的孤岛最窄处塞不进过孔，但常常离已接通的铺铜只有零点几毫米。
    # 这种缝是铺铜的最小宽度规则退让出来的，里面往往什么都没有，一段短线就能并上。
    bridges = []
    if args.get("bridge") and skipped:
        span = float(args.get("bridge-max", 2.0))
        for layer, _other in ((pcbnew.F_Cu, pcbnew.B_Cu), (pcbnew.B_Cu, pcbnew.F_Cu)):
            lname = b.GetLayerName(layer)
            isl = islands_of(b, pcbnew, mm, net, layer)
            if not isl:
                continue
            # 已接通 = 主铺铜，或岛内已有本网络过孔，或本次刚规划了过孔
            planned = [(p["x"], p["y"]) for p in plan if p["layer"] == lname]
            connected = [
                d
                for k, d in enumerate(isl)
                if k == 0
                or any(point_in_poly(d["pts"], vx, vy) for vx, vy in own_vias)
                or any(point_in_poly(d["pts"], vx, vy) for vx, vy in planned)
            ]
            for s in skipped:
                if s["layer"] != lname or "center" not in s:
                    continue
                src = next((d for d in isl if abs(d["area"] - s["area"]) < 0.05), None)
                if src is None or src in connected:
                    continue
                best = None
                for d in connected:
                    for px, py in src["pts"]:
                        for qx, qy in d["pts"]:
                            g = math.hypot(px - qx, py - qy)
                            if g <= span and (best is None or g < best[0]):
                                best = (g, px, py, qx, qy)
                if best is None:
                    continue
                _g, px, py, qx, qy = best
                # 桥要按本网络类线宽走，且沿途对异网铜留足间距
                half = _w / 2.0
                clear = True
                n = max(2, int(_g / 0.1) + 1)
                for i in range(n):
                    t = i / (n - 1.0)
                    x = px + (qx - px) * t
                    y = py + (qy - py) * t
                    for onF, onB, ax, ay, ar, ocl in foreign_pads:
                        if (layer == pcbnew.F_Cu and not onF) or (layer == pcbnew.B_Cu and not onB):
                            continue
                        if math.hypot(x - ax, y - ay) < half + ocl + ar:
                            clear = False
                    for x1, y1, x2, y2, hw, ocl in foreign_trk[layer]:
                        if seg_dist(x, y, x1, y1, x2, y2) < half + ocl + hw:
                            clear = False
                    if not clear:
                        break
                if not clear:
                    s["why"] += "；搭桥通道也被异网铜占住"
                    continue
                bridges.append(
                    {
                        "layer": lname,
                        "width_mm": _w,
                        "from": [round(px, 3), round(py, 3)],
                        "to": [round(qx, 3), round(qy, 3)],
                        "gap_mm": round(_g, 3),
                        "island_area": s["area"],
                    }
                )
        for br in bridges:
            skipped[:] = [s for s in skipped if abs(s["area"] - br["island_area"]) > 0.05]

    made = {}
    for br in bridges:
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(br["from"][0]), pcbnew.FromMM(br["from"][1])))
        t.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(br["to"][0]), pcbnew.FromMM(br["to"][1])))
        t.SetLayer(pcbnew.F_Cu if br["layer"] == b.GetLayerName(pcbnew.F_Cu) else pcbnew.B_Cu)
        t.SetWidth(pcbnew.FromMM(br["width_mm"]))
        t.SetNet(netobj)
        b.Add(t)
        K.disown(t)
    for p in plan:
        v = pcbnew.PCB_VIA(b)
        v.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(p["x"]), pcbnew.FromMM(p["y"])))
        v.SetViaType(pcbnew.VIATYPE_THROUGH)
        v.SetDrill(pcbnew.FromMM(p["drill_mm"]))
        v.SetWidth(pcbnew.FromMM(p["via_mm"]))
        v.SetNet(netobj)
        v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        b.Add(v)
        K.disown(v)
        made[(p["x"], p["y"])] = v
    K.fill_and_save(pcbnew, b, path)

    # 几何检查再细也是近似的，DRC 才是最终裁判。凡是本次新增的过孔引入了
    # error，就把那一个撤掉——宁可这座岛暂时不缝合，也不让 error 数上升。
    removed = []
    if not args.get("no-verify") and made:
        for _round in range(4):
            drc = K.run_drc(path)
            bad = set()
            for viol in drc.get("violations", []):
                if viol.get("severity") != "error":
                    continue
                for it in viol.get("items", []):
                    if (
                        "过孔" not in it.get("description", "")
                        and "via" not in it.get("description", "").lower()
                    ):
                        continue
                    pos = it.get("pos") or {}
                    for key in made:
                        if (
                            abs(key[0] - pos.get("x", 1e9)) < 0.02
                            and abs(key[1] - pos.get("y", 1e9)) < 0.02
                        ):
                            bad.add(key)
            if not bad:
                break
            for key in bad:
                b.Remove(made.pop(key))
                removed.append({"x": key[0], "y": key[1]})
            K.fill_and_save(pcbnew, b, path)

    after = K.unconnected(b)
    kept = [
        p
        for p in plan
        if not any(abs(r["x"] - p["x"]) < 1e-6 and abs(r["y"] - p["y"]) < 1e-6 for r in removed)
    ]
    K.ok(
        {
            "net": net,
            "vias_added": len(kept),
            "plan": kept,
            "bridges": bridges,
            "reverted": removed,
            "skipped": skipped,
            "unconnected_before": before,
            "unconnected_after": after,
            "improved": before - after,
            "note": "补完过孔后，原本用来串联孤岛的长走线就成了冗余，"
            "可以再确认一次连通性后删掉那些横穿整板的走线",
        }
    )


main()
