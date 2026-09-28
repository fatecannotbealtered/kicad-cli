"""``board audit``, in this process: design quality, read from the file.

A port of `payload/audit.py`, which ran on pcbnew. Its output is identical to
the payload's on every board KiCad ships -- `tests/test_native_audit.py` runs
both and compares -- and that includes the payload's mistakes, kept here on
purpose and listed in DEVELOPMENT_STATUS.md, so that changing what the audit
says and changing what computes it are never the same change.

Findings carry an id, a severity, evidence and a fix; the reasoning behind
each rule is in docs/LAYOUT-METHOD_zh.md.
"""

from __future__ import annotations

import math
import os
from collections import defaultdict
from typing import Any

from ..fileformat import geometry
from ..fileformat.connectivity import connect
from . import load_board
from .project import NetClasses, ampacity, load_project

# Names that look like power rails. Left in Default, they are usually missed
# assignments rather than intent.
POWER_HINTS = (
    "VBUS", "VCC", "VDD", "VSYS", "VRAW", "VIN", "VOUT", "+5V", "+3V3", "+12V",
    "+1V8", "BATT", "VBAT", "FUSED", "DC_IN",
)  # fmt: skip
# Power words on logic: eFuse and PMIC status and enable pins.
LOGIC_SUFFIX = (
    "_OK", "_FAULT", "_FAULT_N", "_GOOD", "_PG", "_EN", "_STAT", "_STATUS", "_N",
    "_ALERT", "_INT", "_IRQ", "_RST", "_RESET",
)  # fmt: skip
# RF modules with their own antenna, which need a keep-out around them.
RF_HINTS = ("ESP32", "WROOM", "WROVER", "NRF5", "BLE", "RF_MODULE", "ANTENNA")
FINE_PITCH_MM = 0.70
ZONE_CONNECTION_FULL = 2
NM = 1_000_000


def mm(value: float) -> float:
    return value / NM


def run(path: str, oz: float = 1.0, dT: float = 10.0, carry: float = 0.8) -> dict[str, Any]:  # noqa: N803
    board = load_board(path)
    project, _ = load_project(path)
    nc = NetClasses(project)

    def name(layer: str) -> str:
        """The board's own name for a layer -- what pcbnew's GetLayerName says."""
        for entry in board.layers:
            if entry.name == layer:
                return entry.user_name or entry.name
        return layer

    number = {entry.name: entry.number for entry in board.layers}
    findings: list[dict[str, Any]] = []

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

    edge = board.edge_bbox() or (0, 0, 0, 0)
    width, height = edge[2] - edge[0], edge[3] - edge[1]
    board_area = mm(width) * mm(height)
    cu_layers = [name(layer) for layer in board.copper_layers]

    # ---- pads and nets -----------------------------------------------------------
    pads_by_net: dict[str, list] = defaultdict(list)
    for fp in board.footprints:
        for pad in fp.pads:
            if pad.net:
                pads_by_net[pad.net].append((fp, pad))

    zones = [z for z in board.zones if not z.rule_area]

    def first_layer(zone) -> str:
        # pcbnew reports a zone by the first of its layers in layer-number order.
        return min(zone.layers, key=lambda layer: number.get(layer, 1 << 30))

    # ---- A1 pours filled, planes whole -----------------------------------------------
    zone_by_layer: dict[str, list] = defaultdict(list)
    for z in zones:
        layer = first_layer(z)
        polygons = z.filled.get(layer, [])
        n = len(polygons)
        a = sum(geometry.area(poly) for poly in polygons) / 1e12
        lay = name(layer)
        zone_by_layer[lay].append((z, n, a))
        if n == 0:
            add(
                "A1-unfilled",
                "error",
                "铺铜未真正填充",
                f"{lay} 层 {z.net} 网络的铺铜填充多边形数为 0。IsFilled() 会返回真，"
                "但平面上没有铜，所有靠它连通的焊盘实际是断开的。",
                "在 KiCad 里按 B 重新填充后保存；写命令写完会重填铺铜，"
                "所以跑一次 board stitch 或 board widen 也能带上",
                {"layer": lay, "net": z.net},
            )
        elif n > 3:
            add(
                "A1-fragmented",
                "warn",
                "平面被切碎",
                f"{lay} 层 {z.net} 网络的铺铜被分成 {n:d} 块。"
                f"参考平面碎裂会让信号失去连续回流路径。",
                "检查是否有走线横穿该层；参考层应保持整片，信号改走其它层",
                {"layer": lay, "net": z.net, "polygons": n},
            )

    # ---- A2 layer use -----------------------------------------------------------------
    for lay, zs in zone_by_layer.items():
        for z, _n, a in zs:
            net = z.net
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
        z.net in ("GND", "GNDA", "AGND")
        for zs in zone_by_layer.values()
        for z, _, _ in zs
        if name(first_layer(z)).startswith("In")
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

    # ---- A3 net class assignment ----------------------------------------------------
    unassigned_power = []
    for net, pads in pads_by_net.items():
        if net.startswith("unconnected-") or len(pads) < 2:
            continue
        if nc.name_for(net) != "Default":
            continue
        up = net.upper()
        if any(up.endswith(x) for x in LOGIC_SUFFIX):
            continue
        if any(h in up for h in POWER_HINTS):
            unassigned_power.append({"net": net, "pads": len(pads)})
    if unassigned_power:
        add(
            "A3-power-in-default",
            "error",
            "电源网络落在 Default 类",
            f"{len(unassigned_power):d} 个名字像电源的网络没有被任何 netclass_patterns 命中，"
            f"正在按 Default 线宽布线。",
            "在 .kicad_pro 的 netclass_patterns 里给这些网络补上规则，"
            "再用 board audit 复核线宽是否达标",
            {"nets": unassigned_power[:20]},
        )

    mixed = []
    for n in [net for net in pads_by_net if net.startswith("Net-(")]:
        if len(pads_by_net[n]) < 2:
            continue
        if nc.name_for(n) == "Default":
            refs = {fp.reference for fp, _ in pads_by_net[n]}
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

    # ---- A4 width against current -------------------------------------------------------
    plane_nets = {z.net for z in zones}
    per_net = defaultdict(lambda: {"len": 0.0, "ok": 0.0, "min_w": 99.0, "vias": 0})
    per_class = defaultdict(lambda: {"len": 0.0, "ok": 0.0, "min_w": 99.0})
    for via in board.vias:
        if via.net:
            per_net[via.net]["vias"] += 1
    for track in board.tracks:
        net = track.net
        if not net or track.kind != "segment":
            continue
        n = per_net[net]
        cls = nc.name_for(net)
        target = nc.classes.get(cls, nc.classes["Default"]).get("track_width", 0.2)
        w = mm(track.width)
        ln = math.hypot(
            mm(track.end[0]) - mm(track.start[0]), mm(track.end[1]) - mm(track.start[1])
        )
        n["len"] += ln
        n["min_w"] = min(n["min_w"], w)
        if w >= target - 1e-6:
            n["ok"] += ln
        if net not in plane_nets:
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
                ampacity(r["target_mm"], oz, dT),
                r["compliant_pct"],
                r["min_mm"],
                ampacity(r["min_mm"], oz, dT),
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
                "target_amp": round(ampacity(target, oz, dT), 2),
                "min_amp": round(ampacity(c["min_w"], oz, dT), 2),
                "note": "已剔除平面服务的网络",
            }
        )

    # ---- A5 RF keep-outs ----------------------------------------------------------------
    rule_areas = [z for z in board.zones if z.rule_area]
    for fp in board.footprints:
        ident = (fp.library_id + " " + fp.value).upper()
        if not any(h in ident for h in RF_HINTS):
            continue
        fb = fp.bbox()
        covered = False
        for z in rule_areas:
            points = [p for poly in z.outlines for p in poly]
            if not points or fb is None:
                continue
            zb = geometry.bbox(points)
            if zb[0] < fb[2] and zb[2] > fb[0] and zb[1] < fb[3] and zb[3] > fb[1]:
                covered = True
        if not covered:
            add(
                "A5-no-rf-keepout",
                "error",
                "射频模块没有天线禁布区",
                f"{fp.reference} 是自带天线的模块，板上没有覆盖它的禁布区。"
                "地平面与电源平面会一直铺到天线正下方，射频性能无从保证。",
                "按模块手册的基板净空要求加四层禁布区（禁走线/禁过孔/禁铺铜），"
                "并让天线端贴齐板边或悬出板外",
                {"ref": fp.reference, "fp": fp.library_id},
            )

    # ---- A6 fine pitch ---------------------------------------------------------------------
    dru = os.path.splitext(path)[0] + ".kicad_dru"
    dru_txt = open(dru, encoding="utf-8").read() if os.path.exists(dru) else ""
    fine = []
    for fp in board.footprints:
        ps = [(mm(p.position[0]), mm(p.position[1])) for p in fp.pads]
        if len(ps) < 8:
            continue
        best = 99.0
        for i in range(len(ps)):
            for j in range(i + 1, len(ps)):
                d = math.hypot(ps[i][0] - ps[j][0], ps[i][1] - ps[j][1])
                if 0.01 < d < best:
                    best = d
        if best <= FINE_PITCH_MM:
            fine.append(
                {
                    "ref": fp.reference,
                    "pitch_mm": round(best, 3),
                    "has_rule": fp.reference in dru_txt,
                }
            )
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

    # ---- A7 thermal pads ----------------------------------------------------------------------
    thermal = []
    for fp in board.footprints:
        for pad in fp.pads:
            if pad.net not in ("GND", "AGND", "PGND"):
                continue
            if mm(pad.size[0]) >= 2.0 and mm(pad.size[1]) >= 2.0:
                if pad.zone_connect != ZONE_CONNECTION_FULL:
                    thermal.append(
                        {
                            "ref": fp.reference,
                            "pad": pad.number,
                            "size_mm": [round(mm(pad.size[0]), 2), round(mm(pad.size[1]), 2)],
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

    # ---- A9 connectivity ----------------------------------------------------------------------
    un = connect(board).unconnected
    if un:
        add(
            "A9-unconnected",
            "error",
            "还有未连接项",
            f"{un:d} 个连接没有布通。",
            "跑 pcb_route.py 布线，再用 --repair 定向补剩余项",
            {"count": un},
        )

    sev: dict[str, int] = defaultdict(int)
    for finding in findings:
        sev[finding["severity"]] += 1

    track_mm = sum(mm(t.length()) for t in board.tracks)
    by_layer: dict[str, float] = {}
    for track in board.tracks:
        if track.kind != "segment":
            continue
        lay = name(track.layer)
        by_layer[lay] = by_layer.get(lay, 0.0) + mm(track.length())
    copper_by_layer = {
        lay: round(value, 1) for lay, value in sorted(by_layer.items(), key=lambda kv: -kv[1])
    }

    return {
        "board": path,
        "board_mm": [round(mm(width), 1), round(mm(height), 1)],
        "copper_layers": cu_layers,
        "copper_oz": oz,
        "copper_mm": round(track_mm, 1),
        "copper_by_layer_mm": copper_by_layer,
        "track_count": len(board.tracks),
        "via_count": len(board.vias),
        "delta_t_c": dT,
        "summary": {"error": sev["error"], "warn": sev["warn"], "info": sev["info"]},
        "width_compliance": width_rows,
        "width_by_net": net_rows,
        "findings": findings,
    }
