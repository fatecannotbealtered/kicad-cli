"""PCB 设计质量审计（只读）。

把「怎么算画得好」变成可执行的判据。每条发现给出 id、严重度、证据和修法，
不给笼统评价。判据的依据见 reference/layout-method.md。

用法：
    <kicad-python> kicad-cli board audit --board board.kicad_pcb [--oz 1.0] [--dt 10]

严重度：
    error  不修就不能投板
    warn   影响性能或可制造性，需工程判断
    info   供复核，不一定要改
"""

import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kicad_lib as K  # noqa: E402

# 名字像电源轨的网络。落在 Default 类里通常是漏指派，而不是有意为之。
POWER_HINTS = (
    "VBUS",
    "VCC",
    "VDD",
    "VSYS",
    "VRAW",
    "VIN",
    "VOUT",
    "+5V",
    "+3V3",
    "+12V",
    "+1V8",
    "BATT",
    "VBAT",
    "FUSED",
    "DC_IN",
)
# 名字带电源词但其实是逻辑信号的：eFuse / PMIC 的状态与使能脚。
# 不排除掉会把 PWR_OK、PWR_FAULT_N 这类开漏输出误判成电源轨。
LOGIC_SUFFIX = (
    "_OK",
    "_FAULT",
    "_FAULT_N",
    "_GOOD",
    "_PG",
    "_EN",
    "_STAT",
    "_STATUS",
    "_N",
    "_ALERT",
    "_INT",
    "_IRQ",
    "_RST",
    "_RESET",
)
# 自带天线的射频模块。这些封装周围必须有禁布区。
RF_HINTS = ("ESP32", "WROOM", "WROVER", "NRF5", "BLE", "RF_MODULE", "ANTENNA")
FINE_PITCH_MM = 0.70  # 引脚间距低于此值，粗线无法出脚，需要缩颈与规则放宽


def main():
    args = K.parse_args(sys.argv[1:])
    path = K.board_arg(args)
    oz = float(args.get("oz", 1.0))
    dT = float(args.get("dt", 10.0))

    pcbnew = K.import_pcbnew()
    b = K.load_board(pcbnew, path)
    pro, _ = K.load_project(path)
    nc = K.NetClasses(pro)
    mm = pcbnew.ToMM

    findings = []

    def add(fid, sev, title, detail, fix, evidence=None):
        findings.append(
            {
                "id": fid,
                "severity": sev,
                "title": title,
                "detail": detail,
                "fix": fix,
                "evidence": evidence or {},
            }
        )

    bb = b.GetBoardEdgesBoundingBox()
    board_area = mm(bb.GetWidth()) * mm(bb.GetHeight())
    cu_layers = [b.GetLayerName(lay) for lay in b.GetEnabledLayers().CuStack()]

    # ---- 焊盘与网络统计 ----------------------------------------------------
    pads_by_net = defaultdict(list)
    for f in K.footprints_of(b):
        for p in f.Pads():
            n = p.GetNetname()
            if n:
                pads_by_net[n].append((f, p))

    # ---- A1 铺铜填充与平面完整性 -------------------------------------------
    zone_by_layer = defaultdict(list)
    for z in K.zones_of(b):
        if z.GetIsRuleArea():
            continue
        n, a, lay = K.zone_filled_area(pcbnew, z, b)
        zone_by_layer[lay].append((z, n, a))
        if n == 0:
            add(
                "A1-unfilled",
                "error",
                "铺铜未真正填充",
                f"{lay} 层 {z.GetNetname()} 网络的铺铜填充多边形数为 0。IsFilled() 会返回真，"
                "但平面上没有铜，所有靠它连通的焊盘实际是断开的。",
                "在 KiCad 里按 B 重新填充后保存；写命令写完会重填铺铜，"
                "所以跑一次 board stitch 或 board widen 也能带上",
                {"layer": lay, "net": z.GetNetname()},
            )
        elif n > 3:
            add(
                "A1-fragmented",
                "warn",
                "平面被切碎",
                f"{lay} 层 {z.GetNetname()} 网络的铺铜被分成 {n:d} 块。"
                f"参考平面碎裂会让信号失去连续回流路径。",
                "检查是否有走线横穿该层；参考层应保持整片，信号改走其它层",
                {"layer": lay, "net": z.GetNetname(), "polygons": n},
            )

    # ---- A2 层利用率 --------------------------------------------------------
    for lay, zs in zone_by_layer.items():
        for z, _n, a in zs:
            net = z.GetNetname()
            npads = len(pads_by_net.get(net, []))
            if a > 0.5 * board_area and npads and npads < 30 and lay != "In1.Cu":
                add(
                    "A2-wasted-layer",
                    "warn",
                    "整层喂少量焊盘",
                    f"{lay} 层几乎整层铺给 {net}，但该网络只有 {npads:d} 个焊盘"
                    f"（铜面积 {a:.0f} mm2，占板面 {100 * a / board_area:.0f}%）"
                    f"。低针数网络不值得占用一个布线层。",
                    "若该网络电流不大，改为走线；把这一层让给信号或地",
                    {"layer": lay, "net": net, "pads": npads},
                )

    has_gnd_plane = any(
        z.GetNetname() in ("GND", "GNDA", "AGND")
        for zs in zone_by_layer.values()
        for z, _, _ in zs
        if b.GetLayerName(z.GetLayerSet().Seq()[0]).startswith("In")
    )
    if len(cu_layers) >= 4 and not has_gnd_plane:
        add(
            "A2-no-gnd-plane",
            "error",
            "内层没有完整地平面",
            "四层及以上的板子必须有一整层地做参考平面，否则信号没有确定的回流路径。",
            "把一个内层整层铺 GND，信号不要走这一层",
            {"layers": cu_layers},
        )

    # ---- A3 网络类指派 ------------------------------------------------------
    unassigned_power = []
    for net, pads in pads_by_net.items():
        if net.startswith("unconnected-") or len(pads) < 2:
            continue
        cls = nc.name_for(net)
        if cls != "Default":
            continue
        up = net.upper()
        if any(up.endswith(x) for x in LOGIC_SUFFIX):
            continue  # 状态/使能信号，不是电源轨
        if any(h in up for h in POWER_HINTS):
            unassigned_power.append({"net": net, "pads": len(pads)})
    if unassigned_power:
        add(
            "A3-power-in-default",
            "error",
            "电源网络落在 Default 类",
            f"{len(unassigned_power):d} 个名字像电源的网络没有被任何 netclass_patterns 命中，"
            f"正在按 Default 线宽布线。",
            # 原来写的是「跑 pcb_netclass.py」——那个脚本在这个仓库里不存在，
            # 是从旧 skill 带过来的残留。让调用方去跑一个找不到的东西，比不给
            # 建议更糟。
            "在 .kicad_pro 的 netclass_patterns 里给这些网络补上规则，"
            "再用 board audit 复核线宽是否达标",
            {"nets": unassigned_power[:20]},
        )

    # 功率器件的输出段容易漏：芯片到滤波元件那一段和滤波后归了不同的类
    auto_nets = [n for n in pads_by_net if n.startswith("Net-(")]
    mixed = []
    for n in auto_nets:
        if len(pads_by_net[n]) < 2:
            continue
        if nc.name_for(n) == "Default":
            refs = {f.GetReference() for f, _ in pads_by_net[n]}
            # 与电感/大电容相连的自动命名网络，多半是功率或开关节点
            if any(r[0] in ("L", "FB") for r in refs):
                mixed.append({"net": n, "refs": sorted(refs)})
    if mixed:
        add(
            "A3-power-node-in-default",
            "warn",
            "疑似功率或开关节点落在 Default",
            f"{len(mixed):d} 个与电感相连的自动命名网络归在 Default 类。"
            f"芯片到电感那一段往往载着与输出相同的电流，"
            f"且是 dV/dt 最高的节点。",
            "确认电流后归入功率或开关类；开关节点还要求走线短、回路面积小",
            {"nets": mixed[:15]},
        )

    # ---- A4 线宽达标与载流 --------------------------------------------------
    # 按网络算，不按类算，且必须分清「走线载流」和「平面载流」。
    # 一条由铺铜平面服务的电源轨，走线只是焊盘到过孔的短桩，宽度不代表载流
    # 能力；把它计进类的达标率，会把一条健康的轨报成不达标（实测 VSYS
    # 19.9%，实际只有 5.3 mm 走线配 19 个过孔，是正常的平面扇出）。
    plane_nets = {z.GetNetname() for z in K.zones_of(b) if not z.GetIsRuleArea()}
    per_net = defaultdict(lambda: {"len": 0.0, "ok": 0.0, "min_w": 99.0, "vias": 0})
    per_class = defaultdict(lambda: {"len": 0.0, "ok": 0.0, "min_w": 99.0})
    for t in K.tracks_of(b):
        net = t.GetNetname()
        if not net:
            continue
        n = per_net[net]
        if t.Type() == pcbnew.PCB_VIA_T:
            n["vias"] += 1
            continue
        if t.Type() != pcbnew.PCB_TRACE_T:
            continue
        cls = nc.name_for(net)
        target = nc.classes.get(cls, nc.classes["Default"]).get("track_width", 0.2)
        w = mm(t.GetWidth())
        ln = math.hypot(
            mm(t.GetEnd().x) - mm(t.GetStart().x), mm(t.GetEnd().y) - mm(t.GetStart().y)
        )
        n["len"] += ln
        n["min_w"] = min(n["min_w"], w)
        if w >= target - 1e-6:
            n["ok"] += ln
        if net not in plane_nets:  # 类汇总只统计真正靠走线载流的网络
            c = per_class[cls]
            c["len"] += ln
            c["min_w"] = min(c["min_w"], w)
            if w >= target - 1e-6:
                c["ok"] += ln

    net_rows, worst = [], []
    for net, n in sorted(per_net.items()):
        if n["len"] <= 0:
            continue
        cls = nc.name_for(net)
        if cls == "Default":
            continue
        target = nc.classes.get(cls, nc.classes["Default"]).get("track_width", 0.2)
        pct = 100.0 * n["ok"] / max(n["len"], 1e-9)
        row = {
            "net": net,
            "class": cls,
            "target_mm": target,
            "len_mm": round(n["len"], 1),
            "compliant_pct": round(pct, 1),
            "min_mm": round(n["min_w"], 3),
            "vias": n["vias"],
            "served_by": "plane" if net in plane_nets else "track",
        }
        net_rows.append(row)
        if net in plane_nets:
            # 平面网络只有一种真实缺陷：焊盘没有过孔下到平面，电流被迫走细走线
            if n["vias"] == 0 and n["len"] > 2.0:
                add(
                    "A4-plane-no-via",
                    "error",
                    "平面网络缺过孔桩",
                    "{} 有铺铜平面但一个过孔都没有，{:.1f} mm 电流全走在 {:.2f} mm 走线上。".format(
                        net, n["len"], n["min_w"]
                    ),
                    "在该网络每个焊盘旁补过孔下到平面",
                    row,
                )
        elif pct < 95.0:
            worst.append(row)

    # 严重度按「这条线宽是不是电流定的」来分，不按差多少。
    # 0.30 mm 的 AUDIO、0.25 mm 的 I2S 是风格约定：线电平信号走 0.20 mm
    # 电气上毫无问题，报成 error 会把真正会烧的那几条埋掉。没人会为了信号
    # 完整性把线拉到 0.8 mm 以上，所以用目标宽度本身当判据——
    # 0.8 mm 在 1 oz / ΔT10K 下约 2.2 A，只可能是电流定的。
    carry = float(args.get("carry-mm") or 0.8)
    worst.sort(key=lambda r: (r["compliant_pct"], -r["len_mm"]))
    for r in worst:
        deficit = r["len_mm"] * (1 - r["compliant_pct"] / 100.0)
        add(
            "A4-underwidth",
            "error" if r["target_mm"] >= carry else "warn",
            "载流线宽未达标",
            "{}（{} 类）目标 {:.2f} mm（{:.2f} A），达标 {:.0f}%，最细 {:.2f} mm"
            "（{:.2f} A），欠宽约 {:.1f} mm。".format(
                r["net"],
                r["class"],
                r["target_mm"],
                K.ampacity(r["target_mm"], oz, dT),
                r["compliant_pct"],
                r["min_mm"],
                K.ampacity(r["min_mm"], oz, dT),
                deficit,
            ),
            "pcb_route.py --mode rewidth 按目标宽度整条重布；仍布不通的改走内层或并联过孔分流",
            r,
        )

    width_rows = []
    for cls, c in sorted(per_class.items()):
        target = nc.classes.get(cls, nc.classes["Default"]).get("track_width", 0.2)
        width_rows.append(
            {
                "class": cls,
                "target_mm": target,
                "len_mm": round(c["len"], 1),
                "compliant_pct": round(100.0 * c["ok"] / max(c["len"], 1e-9), 1),
                "min_mm": round(c["min_w"], 3),
                "target_amp": round(K.ampacity(target, oz, dT), 2),
                "min_amp": round(K.ampacity(c["min_w"], oz, dT), 2),
                "note": "已剔除平面服务的网络",
            }
        )

    # ---- A5 射频净空 --------------------------------------------------------
    rule_areas = [z for z in K.zones_of(b) if z.GetIsRuleArea()]
    for f in K.footprints_of(b):
        ident = (f.GetFPIDAsString() + " " + f.GetValue()).upper()
        if not any(h in ident for h in RF_HINTS):
            continue
        fb = f.GetBoundingBox(False, False)
        covered = False
        for z in rule_areas:
            zb = z.Outline().BBox()
            if (
                zb.GetLeft() < fb.GetRight()
                and zb.GetRight() > fb.GetLeft()
                and zb.GetTop() < fb.GetBottom()
                and zb.GetBottom() > fb.GetTop()
            ):
                covered = True
        if not covered:
            add(
                "A5-no-rf-keepout",
                "error",
                "射频模块没有天线禁布区",
                f"{f.GetReference()} 是自带天线的模块，板上没有覆盖它的禁布区。"
                "地平面与电源平面会一直铺到天线正下方，射频性能无从保证。",
                "按模块手册的基板净空要求加四层禁布区（禁走线/禁过孔/禁铺铜），"
                "并让天线端贴齐板边或悬出板外",
                {"ref": f.GetReference(), "fp": f.GetFPIDAsString()},
            )

    # ---- A6 细间距封装 ------------------------------------------------------
    dru = os.path.splitext(path)[0] + ".kicad_dru"
    dru_txt = open(dru, encoding="utf-8").read() if os.path.exists(dru) else ""
    fine = []
    for f in K.footprints_of(b):
        ps = [(mm(p.GetPosition().x), mm(p.GetPosition().y)) for p in f.Pads()]
        if len(ps) < 8:
            continue
        best = 99.0
        for i in range(len(ps)):
            for j in range(i + 1, len(ps)):
                d = math.hypot(ps[i][0] - ps[j][0], ps[i][1] - ps[j][1])
                if 0.01 < d < best:
                    best = d
        if best <= FINE_PITCH_MM:
            ref = f.GetReference()
            fine.append({"ref": ref, "pitch_mm": round(best, 3), "has_rule": ref in dru_txt})
    missing = [x for x in fine if not x["has_rule"]]
    if missing:
        add(
            "A6-fine-pitch-no-rule",
            "warn",
            "细间距封装缺少间距放宽规则",
            "{} 的引脚间距 ≤ {:.2f} mm，封装自身的焊盘间隙通常小于网络类要求的间距，"
            "DRC 会一直报间距违规，而这是封装地盘图决定的、改线消不掉。".format(
                ", ".join(x["ref"] for x in missing), FINE_PITCH_MM
            ),
            "在 <board>.kicad_dru 里对这些封装的庭院内部放宽间距。"
            "condition 必须写成单行，跨行会让整个规则文件静默失效",
            {"footprints": fine},
        )

    # ---- A7 散热焊盘连接方式 ------------------------------------------------
    thermal = []
    for f in K.footprints_of(b):
        for p in f.Pads():
            if p.GetNetname() not in ("GND", "AGND", "PGND"):
                continue
            s = p.GetSize()
            if mm(s.x) >= 2.0 and mm(s.y) >= 2.0:
                conn = p.GetLocalZoneConnection()
                if conn != pcbnew.ZONE_CONNECTION_FULL:
                    thermal.append(
                        {
                            "ref": f.GetReference(),
                            "pad": p.GetNumber(),
                            "size_mm": [round(mm(s.x), 2), round(mm(s.y), 2)],
                        }
                    )
    if thermal:
        add(
            "A7-thermal-relief-on-pad",
            "warn",
            "大面积散热焊盘走花焊盘连接",
            f"{len(thermal):d} 个面积 ≥ 2x2 mm 的接地焊盘仍用热隔离辐条连接铺铜。"
            f"散热焊盘要的是导热，"
            f"辐条会把热阻做上去，还容易触发热阻断错误。",
            "把这些焊盘的铺铜连接方式改为实连（ZONE_CONNECTION_FULL）",
            {"pads": thermal[:20]},
        )

    # ---- A9 连通性 ----------------------------------------------------------
    un = K.unconnected(b)
    if un:
        add(
            "A9-unconnected",
            "error",
            "还有未连接项",
            f"{un:d} 个连接没有布通。",
            "跑 pcb_route.py 布线，再用 --repair 定向补剩余项",
            {"count": un},
        )

    sev = defaultdict(int)
    for f in findings:
        sev[f["severity"]] += 1

    # 铜的总长度。布局好不好最终就落在这个数上：同一张网表、同一个布线器，
    # 线短的那块板走线阻抗低、串扰小、板子还能做得更小。board place 拿它
    # 做前后对比，所以它必须是本工具能读出来的事实，而不是外部脚本算的。
    track_mm = 0.0
    tracks = vias = 0
    for t in K.tracks_of(b):
        if t.GetClass() == "PCB_VIA":
            vias += 1
        else:
            tracks += 1
            track_mm += mm(t.GetLength())

    K.ok(
        {
            "board": path,
            "board_mm": [round(mm(bb.GetWidth()), 1), round(mm(bb.GetHeight()), 1)],
            "copper_layers": cu_layers,
            "copper_oz": oz,
            "copper_mm": round(track_mm, 1),
            "copper_by_layer_mm": K.copper_by_layer(b, pcbnew),
            "track_count": tracks,
            "via_count": vias,
            "delta_t_c": dT,
            "summary": {"error": sev["error"], "warn": sev["warn"], "info": sev["info"]},
            "width_compliance": width_rows,
            "width_by_net": net_rows,
            "findings": findings,
        }
    )


if __name__ == "__main__":
    main()
