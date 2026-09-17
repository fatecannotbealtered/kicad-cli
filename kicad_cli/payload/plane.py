"""参考平面连续性：逐段核验走线下方到底有没有铜。

这是 DRC 从不报的一类缺陷。走线本身合规、间距合规、连通性合规，但它下面那层
参考平面开了个口子——反焊盘连成一片、或者有人从平面中间穿了一条线——回流电流
就得绕路。绕出来的那个环路面积，就是辐射和串扰的来源。板子做出来测不过 EMC，
回头查是查不到 DRC 上的。

**一个必须说清的陷阱：KiCad 的层 ID 不是物理叠层顺序。** 四层板上
F.Cu=0、B.Cu=2、In1.Cu=4、In2.Cu=6。按 ID 排序会得到 F/B/In1/In2，
于是「相邻层」全错，整个分析的结论也就全错。物理顺序必须自己拼：
F.Cu → In1..In30 → B.Cu。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kicad_lib as K  # noqa: E402

# 采样步长。再密收益递减，再疏会漏掉窄缝——0.5 mm 约等于一个 0402 焊盘的宽度。
STEP_MM = 0.5
# 一层上铺铜占板面多少才算「平面层」而不是「带铺铜的信号层」。
PLANE_COVERAGE = 0.40
# 归并缺口用的网格边长。同一个洞打到的多条线应当被报成一件事。
CLUSTER_MM = 5.0


def physical_order(board, pcbnew):
    """铜层的物理叠层顺序，不是 ID 顺序。"""
    order = []
    if board.IsLayerEnabled(pcbnew.F_Cu):
        order.append(pcbnew.F_Cu)
    for i in range(1, 31):
        lid = getattr(pcbnew, "In%d_Cu" % i, None)
        if lid is not None and board.IsLayerEnabled(lid):
            order.append(lid)
    if board.IsLayerEnabled(pcbnew.B_Cu):
        order.append(pcbnew.B_Cu)
    return order


def zone_polys(board, pcbnew, layer):
    """该层上每块铺铜的填充多边形与网名。规则区（禁布区）不算铜。"""
    out = []
    for z in K.zones_of(board):
        if z.GetIsRuleArea() or not z.IsOnLayer(layer):
            continue
        polys = z.GetFilledPolysList(layer)
        if polys.OutlineCount():
            out.append((z.GetNetname(), polys))
    return out


def covered_by(polys, point):
    """点落在哪块铺铜里。Contains 原生处理孔洞——反焊盘、穿过平面的通道都算断。"""
    for net, poly in polys:
        if poly.Contains(point):
            return net
    return None


def stitch_caps(board, pcbnew, mm, plane_nets):
    """能让回流在分割处换层的电容。

    分割两侧的平面（比如 +3V3 与 VSYS）各自通过去耦电容交流接地，所以跨越点
    附近若有这样的电容，回流就能就近转移，不必绕到分割端点。**这个距离才是
    「跨分割」是不是问题的判据**——只报「跨了」而不报「最近的换层点有多远」，
    等于把一个事实说成了一个问题。
    """
    out = []
    for fp in board.GetFootprints():
        # 只有电容能在关心的频段上导通。第一版漏了这个过滤，于是任何一个脚接
        # 平面、另一个脚接地的器件都被算进来——板上最近的「换层点」因此报成了
        # 一个**按钮开关**，而开关没按下时是断路。差一点就拿这个数下结论。
        if not str(fp.GetReference()).upper().startswith("C"):
            continue
        nets = {p.GetNetname() for p in fp.Pads()}
        touching = nets & plane_nets
        if not touching:
            continue
        # 直接桥接两个平面网络的最有用；退而求其次是任一平面到地的去耦电容。
        if len(touching) >= 2:
            kind = "bridges_planes"
        elif nets & {"GND", "GNDA", "AGND", "DGND"}:
            kind = "plane_to_ground"
        else:
            continue
        pos = fp.GetPosition()
        out.append(
            {
                "ref": fp.GetReference(),
                "value": fp.GetValue(),
                "kind": kind,
                "x": mm(pos.x),
                "y": mm(pos.y),
            }
        )
    return out


def nearest_stitch(caps, x, y):
    best = None
    for c in caps:
        d = ((c["x"] - x) ** 2 + (c["y"] - y) ** 2) ** 0.5
        if best is None or d < best[0]:
            best = (d, c)
    if best is None:
        return None
    d, c = best
    return {"ref": c["ref"], "value": c["value"], "kind": c["kind"], "distance_mm": round(d, 1)}


def main():
    args = K.parse_args(sys.argv[1:])
    path = K.board_arg(args)
    step = float(args.get("step", STEP_MM))

    pcbnew = K.import_pcbnew()
    mm = pcbnew.ToMM
    board = K.load_board(pcbnew, path)
    order = physical_order(board, pcbnew)
    if len(order) < 2:
        K.fail(
            "E_VALIDATION",
            "单层板没有参考平面可查",
            {"copper_layers": len(order)},
        )

    box = board.GetBoardEdgesBoundingBox()
    board_area = mm(box.GetWidth()) * mm(box.GetHeight())

    polys_by_layer = {lid: zone_polys(board, pcbnew, lid) for lid in order}
    coverage = {}
    for lid in order:
        area = sum(poly.Area() for _net, poly in polys_by_layer[lid])
        coverage[lid] = (mm(1) * mm(1) * area / board_area) if board_area else 0.0
    planes = [lid for lid in order if coverage[lid] >= PLANE_COVERAGE]

    # 参考层 = 物理上紧邻的下一层；最底层则取它上面那层。
    #
    # 第一版按覆盖率把层分成「平面层」和「信号层」，只给信号层找参考。在这块板上
    # 四层覆盖率都在 79%~93%，于是全被判成平面层、一条也不分析，整个命令返回空。
    # 模型本身就错了：顶底层同时走信号和铺地是常规做法，「是平面还是信号」不是
    # 二分的。回流走的是紧邻层，跟那层叫什么无关。覆盖率保留下来作为上下文。
    reference_of = {}
    for i, lid in enumerate(order):
        pick = order[i + 1] if i + 1 < len(order) else (order[i - 1] if i else None)
        if pick is not None:
            reference_of[lid] = pick

    # 铺铜自己占着的网络。它们的走线「跨越分割」不构成回流问题——一条 GND 线
    # 从 +3V3 区上方走到 VSYS 区上方，谈不上回流路径断裂。第一版把它们和信号线
    # 并列报出去，六条里有两条是这种，等于把噪声掺进了结论。
    plane_nets = {net for polys in polys_by_layer.values() for net, _p in polys}
    caps = stitch_caps(board, pcbnew, mm, plane_nets)

    gaps, splits = [], []
    per_net = {}
    total_samples = ok_samples = 0

    for t in K.tracks_of(board):
        if t.Type() != pcbnew.PCB_TRACE_T:
            continue
        layer = t.GetLayer()
        ref = reference_of.get(layer)
        if ref is None:
            continue
        net = t.GetNetname()
        a, b = t.GetStart(), t.GetEnd()
        length = mm(t.GetLength())
        if length <= 0:
            continue
        n = max(2, int(length / step) + 1)
        seen_nets = set()
        bad = 0
        for k in range(n):
            f = k / (n - 1)
            p = pcbnew.VECTOR2I(int(a.x + (b.x - a.x) * f), int(a.y + (b.y - a.y) * f))
            under = covered_by(polys_by_layer[ref], p)
            total_samples += 1
            if under is None:
                bad += 1
            else:
                ok_samples += 1
                seen_nets.add(under)

        stat = per_net.setdefault(net, {"net": net, "samples": 0, "unbacked": 0, "length_mm": 0.0})
        stat["samples"] += n
        stat["unbacked"] += bad
        stat["length_mm"] += length

        if bad:
            gaps.append(
                {
                    "net": net,
                    "layer": board.GetLayerName(layer),
                    "reference": board.GetLayerName(ref),
                    "at_mm": [round(mm(a.x), 3), round(mm(a.y), 3)],
                    "length_mm": round(length, 3),
                    "unbacked_fraction": round(bad / n, 3),
                }
            )
        if len(seen_nets) > 1 and net not in plane_nets:
            at = [round(mm(a.x), 3), round(mm(a.y), 3)]
            splits.append(
                {
                    "net": net,
                    "layer": board.GetLayerName(layer),
                    "reference": board.GetLayerName(ref),
                    "at_mm": at,
                    "crosses": sorted(seen_nets),
                    # 有多远才能换层。这个数决定了它是不是问题。
                    "nearest_stitch": nearest_stitch(caps, at[0], at[1]),
                }
            )

    worst = sorted(
        (v for v in per_net.values() if v["unbacked"]),
        key=lambda v: -v["unbacked"] / max(1, v["samples"]),
    )
    for v in worst:
        v["unbacked_fraction"] = round(v["unbacked"] / max(1, v["samples"]), 3)
        v["length_mm"] = round(v["length_mm"], 2)

    gaps.sort(key=lambda g: -g["unbacked_fraction"] * g["length_mm"])

    # 一个平面上的洞会同时打到经过它的每一条线。四百多条逐条报出去，等于让人
    # 去查四百多次同一件事。按位置归并，答案就变成「平面上这几个地方有洞」。
    clusters = {}
    for g in gaps:
        key = (
            g["layer"],
            g["reference"],
            int(g["at_mm"][0] // CLUSTER_MM),
            int(g["at_mm"][1] // CLUSTER_MM),
        )
        c = clusters.setdefault(
            key,
            {
                "layer": g["layer"],
                "reference": g["reference"],
                "near_mm": [
                    round(key[2] * CLUSTER_MM + CLUSTER_MM / 2, 1),
                    round(key[3] * CLUSTER_MM + CLUSTER_MM / 2, 1),
                ],
                "segments": 0,
                "nets": set(),
            },
        )
        c["segments"] += 1
        c["nets"].add(g["net"])
    hot = sorted(clusters.values(), key=lambda c: -c["segments"])
    for c in hot:
        c["nets"] = sorted(c["nets"])[:12]

    findings = []
    if gaps:
        findings.append(
            {
                "id": "P1-no-reference",
                "severity": "warn",
                "title": "走线下方的参考平面有缺口",
                "detail": "这些线段下方那一层没有铜。回流电流必须绕行，绕出的环路面积"
                "就是辐射源。DRC 不检查这件事，间距和连通性都合规。",
                "evidence": {
                    "segments": len(gaps),
                    "hotspots": hot[:8],
                    "worst_nets": worst[:10],
                },
                "confidence": "measured",
                "fix": "要么把走线挪到有平面支撑的区域，要么补平面上的缺口；高速或大电流网络优先",
            }
        )
    if splits:
        findings.append(
            {
                "id": "P2-crosses-split",
                "severity": "warn",
                "title": "走线跨越了参考平面上的分割",
                "detail": "线段下方的参考铜从一个网络变成了另一个。回流要换层只能借道"
                "跨接电容，绕出去的距离就是额外环路。铺铜自身的网络已排除——一条 GND "
                "走线经过分割上方不构成回流问题。`nearest_stitch` 给出每处最近的换层点"
                "有多远：几毫米以内影响有限，十几毫米以上对快沿信号才真正要紧。",
                "evidence": {
                    "segments": len(splits),
                    "stitch_candidates": len(caps),
                    "bridging_caps": sum(1 for c in caps if c["kind"] == "bridges_planes"),
                    "sample": splits[:10],
                },
                "confidence": "measured",
                "fix": "按信号的边沿速率排序处理：时钟、快沿总线优先改走参考完整的层，"
                "或在跨越点就近补一个跨接电容；静态信号（strap、使能、静音）可以接受",
            }
        )

    K.ok(
        {
            "board": path,
            "stackup": [board.GetLayerName(x) for x in order],
            "plane_layers": [board.GetLayerName(x) for x in planes],
            "coverage": {board.GetLayerName(x): round(coverage[x], 3) for x in order},
            "reference_map": {
                board.GetLayerName(k): board.GetLayerName(v) for k, v in reference_of.items()
            },
            "samples": total_samples,
            "backed_fraction": round(ok_samples / total_samples, 4) if total_samples else None,
            "segments_without_reference": len(gaps),
            "segments_crossing_split": len(splits),
            "worst_nets": worst[:20],
            "stitch_candidates": len(caps),
            "hotspots": hot[:20],
            "gaps": gaps[:40],
            "splits": splits[:40],
            "findings": findings,
            "status": "PASS" if not (gaps or splits) else "FAIL",
            "not_checked": [
                "只看物理上最近的那个平面层。三层以上叠层里，真正的回流层可能是另一层",
                "不计算环路面积，也不看边沿速率。跨分割要不要紧取决于信号有多快，"
                "工具只给出「跨了」和「最近换层点多远」两个事实，快慢要由人判断",
                "跨接电容只按位置找，没有核算它在关心的频段上阻抗够不够低",
                "过孔的换层不在此列——那需要看伴地孔，属于 board via 的范围",
                "铺铜必须已经填充过。未填充的区在这里等同于没有铜，"
                "这是保守的方向，但会把「忘了填充」报成「平面有缺口」",
            ],
        }
    )


main()
