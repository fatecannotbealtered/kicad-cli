"""``board parity``, in this process: does the board still match its schematic?

A port of `payload/parity.py`, which ran on pcbnew; its output is identical
on every board KiCad ships (`tests/test_native_parity.py`).

KiCad's "Update PCB from Schematic" is interactive and its result is not
machine-readable. This compares four things and returns the differences:

- parts on one side and not the other;
- a reference whose value differs;
- a reference whose footprint differs;
- each net's set of pads -- the one that matters most, and the one that
  drifts without anyone noticing.

The schematic side is the netlist KiCad's own binary exports, fresh, so the
comparison is against the schematic as it is now. That call is the last
thing here that needs KiCad; it goes when this tool exports the netlist
itself.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET  # noqa: N817
from collections import defaultdict
from pathlib import Path
from typing import Any

from .. import envelope, netlist
from ..fileformat.connectivity import connect
from . import load_board


def schematic_side(root: ET.Element) -> tuple[dict[str, dict[str, str]], dict[str, set[str]]]:
    """Components and each net's pins, from an exported XML netlist."""
    components: dict[str, dict[str, str]] = {}
    for comp in root.findall("components/comp"):
        components[comp.get("ref")] = {
            "value": (comp.findtext("value") or "").strip(),
            "footprint": (comp.findtext("footprint") or "").strip(),
        }
    nets: dict[str, set[str]] = defaultdict(set)
    for net in root.findall("nets/net"):
        for pin in net.findall("node"):
            nets[net.get("name")].add(f"{pin.get('ref')}.{pin.get('pin')}")
    return components, nets


def run(path: str) -> dict[str, Any]:
    sch = os.path.splitext(path)[0] + ".kicad_sch"
    if not os.path.exists(sch):
        envelope.fail(
            "E_NOT_FOUND",
            "找不到原理图，且没有给 --netlist",
            {"sch": sch, "write_state": "not_started"},
        )
    # XML, as the payload read: it keeps the parts marked "exclude from
    # board", which the S-expression netlist leaves out -- see
    # DEVELOPMENT_STATUS.md for what that costs.
    text, _stderr = netlist.export_text(Path(sch), "kicadxml")
    source = f"现导（{os.path.basename(sch)}）"
    try:
        root = ET.fromstring(text)  # noqa: S314 - KiCad's own export of the user's file
    except ET.ParseError as exc:
        envelope.fail(
            "E_IO",
            "KiCad's netlist export is not the XML it was asked for",
            {"sch": sch, "reason": str(exc)[:300], "write_state": "not_started"},
        )
        raise AssertionError("unreachable") from exc
    sch_comp, sch_net = schematic_side(root)

    board = load_board(path)
    pcb_comp: dict[str, dict[str, str]] = {}
    pcb_net: dict[str, set[str]] = defaultdict(set)
    for fp in board.footprints:
        pcb_comp[fp.reference] = {"value": fp.value.strip(), "footprint": fp.library_id.strip()}
        for pad in fp.pads:
            if pad.net:
                pcb_net[pad.net].add(f"{fp.reference}.{pad.number}")

    diffs: list[dict[str, Any]] = []

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
        # The schematic may leave a footprint out; compare only where both say.
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

    # Connections are compared as sets of pads: a net may have been renamed.
    def real(pins):
        return {x for x in pins if not x.startswith("#")}

    sch_groups = {frozenset(real(v)) for v in sch_net.values() if len(real(v)) > 1}
    pcb_groups = {frozenset(real(v)) for v in pcb_net.values() if len(real(v)) > 1}
    missing_groups = sch_groups - pcb_groups
    extra_groups = pcb_groups - sch_groups

    if missing_groups:
        sample = []
        # Sorted: the payload took the first ten of a set, which Python orders
        # differently in every process.
        for g in sorted(missing_groups, key=sorted)[:10]:
            name = next((k for k, v in sch_net.items() if frozenset(real(v)) == g), "?")
            # Where the board put these pins instead.
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

    un = connect(board).unconnected
    if un:
        add(
            "unrouted",
            "warn",
            f"网表一致不等于布线完成：还有 {un:d} 个连接没有布通。",
            {"count": un},
        )

    status = "PASS" if not any(d["severity"] == "error" for d in diffs) else "FAIL"
    return {
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
