"""原理图与 PCB 一致性校验（只读）。

KiCad 的「从原理图更新 PCB」是交互式的，结果不可机读。本脚本从原理图导出
网表，再逐项比对板上的实际情况，把差异变成结构化输出。

比对四层：
    器件   原理图有而板上没有 / 板上有而原理图没有
    值     同一位号的 Value 不一致
    封装   同一位号的封装不一致
    连接   每个网络的焊盘集合是否一致（这一项最要紧，也最容易悄悄跑偏）

用法：
    <kicad-python> kicad-cli board parity --board board.kicad_pcb [--sch sheet.kicad_sch]
                                 [--netlist exported.xml]

不给 --netlist 时会调 KiCad 自己的 kicad-cli 从 --sch（缺省为同名 .kicad_sch）
现导一份，保证比对的是原理图当下的状态，而不是某份可能过期的旧网表。
"""

import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kicad_lib as K  # noqa: E402


def export_netlist(sch_path):
    """从原理图现导一份网表。

    走 K.official_cli() 的绝对路径，不按名字查 PATH——我们这套工具**也叫
    kicad-cli**。在 Windows 上这里一直是对的，但靠的是巧合：载荷跑在 KiCad
    自带的 python 下，CreateProcess 会先搜调用方所在目录，官方二进制正好在
    同一个 bin 里，于是 PATH 上排在前面的同名程序根本轮不到。POSIX 的
    execvp 没有这一步，只认 PATH，所以那边谁在前面就是谁。
    """
    out = os.path.join(tempfile.gettempdir(), "kicad_layout_parity_netlist.xml")
    exe = K.official_cli()
    try:
        r = subprocess.run(
            [exe, "sch", "export", "netlist", "--format", "kicadxml", "-o", out, sch_path],
            capture_output=True,
            timeout=900,
        )
    except FileNotFoundError:
        K.fail(
            "E_CONFIG",
            "找不到 KiCad 自己的 kicad-cli，无法从原理图导出网表",
            {"tried": exe, "hint": "用 --netlist 直接给网表，或跑 kicad-cli doctor 看解析结果"},
        )
    except subprocess.TimeoutExpired:
        K.fail("E_TIMEOUT", "kicad-cli 导出网表超时", {"sch": sch_path})
    if r.returncode != 0 or not os.path.exists(out):
        K.fail(
            "E_IO",
            "网表导出失败",
            {"returncode": r.returncode, "stderr": r.stderr.decode("utf-8", "ignore")[-400:]},
        )
    return out


def main():
    args = K.parse_args(sys.argv[1:])
    path = K.board_arg(args)

    nl = args.get("netlist")
    if not nl:
        sch = args.get("sch") or os.path.splitext(path)[0] + ".kicad_sch"
        if not os.path.exists(sch):
            K.fail("E_NOT_FOUND", "找不到原理图，且没有给 --netlist", {"sch": sch})
        nl = export_netlist(sch)
        source = f"现导（{os.path.basename(sch)}）"
    else:
        if not os.path.exists(nl):
            K.fail("E_NOT_FOUND", "指定的网表文件不存在", {"netlist": nl})
        source = f"外部提供（{os.path.basename(nl)}）"

    root = ET.parse(nl).getroot()

    # ---- 原理图侧 ----------------------------------------------------------
    sch_comp = {}
    for c in root.findall("components/comp"):
        ref = c.get("ref")
        fp = (c.findtext("footprint") or "").strip()
        sch_comp[ref] = {"value": (c.findtext("value") or "").strip(), "footprint": fp}
    sch_net = defaultdict(set)
    for n in root.findall("nets/net"):
        name = n.get("name")
        for node in n.findall("node"):
            sch_net[name].add("{}.{}".format(node.get("ref"), node.get("pin")))

    # ---- PCB 侧 -------------------------------------------------------------
    pcbnew = K.import_pcbnew()
    b = K.load_board(pcbnew, path)
    pcb_comp = {}
    pcb_net = defaultdict(set)
    for f in K.footprints_of(b):
        ref = f.GetReference()
        pcb_comp[ref] = {"value": f.GetValue().strip(), "footprint": f.GetFPIDAsString().strip()}
        for p in f.Pads():
            nn = p.GetNetname()
            if nn:
                pcb_net[nn].add(f"{ref}.{p.GetNumber()}")

    diffs = []

    def add(kind, sev, detail, evidence):
        diffs.append({"kind": kind, "severity": sev, "detail": detail, "evidence": evidence})

    only_sch = sorted(set(sch_comp) - set(pcb_comp))
    only_pcb = sorted(set(pcb_comp) - set(sch_comp))
    if only_sch:
        add(
            "component_missing_on_pcb",
            "error",
            f"原理图有而板上没有的器件 {len(only_sch):d} 个。板子缺件，网表一致性无从谈起。",
            {"refs": only_sch},
        )
    if only_pcb:
        add(
            "component_not_in_schematic",
            "error",
            f"板上有而原理图没有的器件 {len(only_pcb):d} 个。多出来的器件不会被 ERC 覆盖。",
            {"refs": only_pcb},
        )

    val_diff, fp_diff = [], []
    for ref in sorted(set(sch_comp) & set(pcb_comp)):
        s, p = sch_comp[ref], pcb_comp[ref]
        if s["value"] != p["value"]:
            val_diff.append({"ref": ref, "sch": s["value"], "pcb": p["value"]})
        # 原理图侧可能没填封装；只在两边都有时比对
        if s["footprint"] and s["footprint"] != p["footprint"]:
            fp_diff.append({"ref": ref, "sch": s["footprint"], "pcb": p["footprint"]})
    if val_diff:
        add(
            "value_mismatch",
            "error",
            f"{len(val_diff):d} 个器件的值在两边不一致。BOM 与实物会对不上。",
            {"items": val_diff[:30]},
        )
    if fp_diff:
        add(
            "footprint_mismatch",
            "error",
            f"{len(fp_diff):d} 个器件的封装在两边不一致。焊盘几何与网表都会跟着错。",
            {"items": fp_diff[:30]},
        )

    # ---- 连接比对：以焊盘集合为准，网络名可能被重命名 ----------------------
    def real(pins):
        return {x for x in pins if not x.startswith("#")}

    sch_groups = {frozenset(real(v)) for v in sch_net.values() if len(real(v)) > 1}
    pcb_groups = {frozenset(real(v)) for v in pcb_net.values() if len(real(v)) > 1}
    missing_groups = sch_groups - pcb_groups
    extra_groups = pcb_groups - sch_groups

    if missing_groups:
        sample = []
        for g in list(missing_groups)[:10]:
            name = next((k for k, v in sch_net.items() if frozenset(real(v)) == g), "?")
            # 找板上把这些脚拆到了哪些网络里
            landed = sorted({n for n, v in pcb_net.items() for pin in g if pin in v})
            sample.append({"sch_net": name, "pins": sorted(g)[:12], "pcb_nets": landed[:6]})
        add(
            "net_membership_mismatch",
            "error",
            f"{len(missing_groups):d} 个原理图网络在板上找不到相同的焊盘集合。"
            f"这意味着板上的连接关系与原理图不同，"
            f"通常是改了原理图却没有重新同步 PCB。",
            {"samples": sample},
        )
    if extra_groups and not missing_groups:
        add(
            "extra_net_on_pcb",
            "warn",
            f"板上有 {len(extra_groups):d} 个焊盘集合在原理图里找不到对应网络。",
            {"count": len(extra_groups)},
        )

    un = K.unconnected(b)
    if un:
        add(
            "unrouted",
            "warn",
            f"网表一致不等于布线完成：还有 {un:d} 个连接没有布通。",
            {"count": un},
        )

    status = "PASS" if not any(d["severity"] == "error" for d in diffs) else "FAIL"
    K.ok(
        {
            "status": status,
            "board": path,
            "netlist_source": source,
            "schematic_components": len(sch_comp),
            "pcb_components": len(pcb_comp),
            "schematic_nets": len(sch_groups),
            "pcb_nets": len(pcb_groups),
            "unconnected": un,
            "diffs": diffs,
            "limits": "只比对器件、值、封装和连接关系。不校验布线质量、"
            "载流、EMC、阻抗，也不替代 ERC 与 DRC。",
        }
    )


if __name__ == "__main__":
    main()
