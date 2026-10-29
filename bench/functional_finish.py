"""Public routing and value ECO from predeclared functional intent. No CAD writer."""

from __future__ import annotations

import json
import shutil
import sys

from functional_circuit import export, verify_xml
from functional_support import Case, put, sha


def main(directory, binary=None):
    t = Case.attach(directory, binary)
    intent = json.loads((t.root / "INTENT-BEFORE-DESIGN.json").read_bytes())
    board = t.root / "project/schmitt.kicad_pcb"
    source = board.with_suffix(".kicad_sch")
    before = t.root / "before-routing"
    shutil.copytree(board.parent, before)
    native_before = t.root / "before-routing-drc.json"
    t.native("pcb", "drc", "--format", "json", "--severity-all", "-o", native_before, board)
    route_receipts = []
    for net in ["INPUT", "OUTPUT", "+3V3", "GND"]:
        observed = t.cli("board", "routing", "inspect", "--board", board, "--net", net)[
            "observation"
        ]
        row = observed["nets"][0]
        assert not row["segments"] and not row["vias"], "Fresh routing only; do not replay"
        pads = {f"{p['ref']}.{p['pin']}": p for p in row["pads"]}
        assert sorted(pads) == sorted(intent["nets"][net]), (net, pads)
        xy = {name: p["at_nm"] for name, p in pads.items()}
        if net == "INPUT":
            a, b = "TP3.1", "U1.2"
            joint = [xy["R1.1"][0], xy[a][1]]
            paths = [[xy[a], joint, xy[b]], [joint, xy["R1.1"]]]
            width = 250000
        elif net == "OUTPUT":
            a, b = "U1.4", "TP4.1"
            paths, width = [[xy[a], xy[b]]], 250000
        elif net == "+3V3":
            a, b = "TP1.1", "U1.5"
            joint = [xy[b][0], xy["C1.1"][1]]
            paths = [
                [xy[a], [xy[b][0], xy[a][1]], joint, xy[b]],
                [joint, xy["C1.1"]],
            ]
            width = 400000
        else:
            a, b = "U1.3", "TP2.1"
            joint = [xy[a][0], 23500000]
            paths = [
                [xy[a], joint, xy[b]],
                [xy["R1.2"], [xy["R1.2"][0] + 500000, 23500000], joint],
                [
                    xy["C1.2"],
                    [xy["C1.2"][0], 20000000],
                    [20000000, 20000000],
                    [20000000, xy[a][1]],
                    xy[a],
                ],
            ]
            width = 400000
        request = {
            "baseline": t.cli("project", "context", "--board", board)["observation"]["context"],
            "net": net,
            "remove_ids": [],
            "allow_locked_ids": [],
            "allowed_layers": ["F.Cu"],
            "bounds_nm": [5000000, 5000000, 35000000, 35000000],
            "endpoints": [
                {
                    "object_uuid": pads[name]["uuid"],
                    "terminal": "center",
                    "at_nm": xy[name],
                    "layer": "F.Cu",
                }
                for name in [a, b]
            ],
            "segments": [
                {"start_nm": p, "end_nm": q, "layer": "F.Cu", "width_nm": width}
                for line in paths
                for p, q in zip(line, line[1:], strict=False)
                if p != q
            ],
            "minimum_improvement_mm": 0,
        }
        spec = t.root / ("route-" + net.replace("+", "") + ".json")
        put(spec, request)
        _, saved = t.write("board", "route", "patch", "--board", board, "--spec", spec)
        route_receipts.append(saved["receipt"])
        t.cli("board", "routing", "inspect", "--board", board, "--net", net)
    t.write("project", "links", "repair", "--board", board)
    # Creation imports a minimal field set. Establish complete field parity
    # before measuring a target-only ECO rather than hydrating it during ECO.
    t.write("project", "sync", "--board", board, "--schematic", source)
    parity = t.cli("project", "parity", "--board", board, "--schematic", source)
    assert parity["status"] == "PASS"
    parts = {p["ref"]: p for p in parity["observed_footprints"]}
    for ref in ("U1", "R1", "C1"):
        assert parts[ref]["fields"]["MPN"] == intent["parts"][ref]["mpn"]
    # Baseline is retained for an exact ECO comparison, not inferred after editing.
    shutil.copytree(board.parent, t.root / "before-eco")
    board_before = board.read_bytes()
    changes = t.root / "eco-r1.json"
    put(
        changes,
        {
            "changes": [
                {
                    "op": "set",
                    "ref": "R1",
                    "value": intent["eco"]["new_value"],
                    "fields": {"MPN": intent["eco"]["new_mpn"]},
                }
            ]
        },
    )
    t.write("sch", "edit", "--schematic", source, "--changes", changes)
    assert board.read_bytes() == board_before, "SCH-only edit must not modify PCB"
    sync = ["project", "sync", "--board", board, "--schematic", source]
    preview = t.cli(*sync, "--dry-run")
    assert preview["preview"]["can_apply"]
    assert {p["id"] for p in preview["preview"]["changes"]} == {parts["R1"]["id"]}
    t.cli(*sync, "--confirm", preview["confirm_token"])
    assert t.cli("project", "parity", "--board", board, "--schematic", source)["status"] == "PASS"
    verify_xml(t, source, intent, "eco-saved")
    for fmt in ["netlist", "svg", "pdf"]:
        export(t, source, fmt, "sch-" + fmt + "-eco")
    after = t.root / "after-routing-and-eco-drc.json"
    t.native("pcb", "drc", "--format", "json", "--severity-all", "-o", after, board)
    report = json.loads(after.read_bytes())
    assert not report["unconnected_items"]
    assert not [v for v in report["violations"] if v["severity"] == "error"], report["violations"]
    for fmt in ["svg", "pdf"]:
        out = t.root / ("pcb-" + fmt)
        t.write(
            "board",
            "plot",
            "--board",
            board,
            "--file-format",
            fmt,
            "--layers",
            "F.Cu,B.Cu,F.SilkS,F.Mask,Edge.Cuts",
            "--out",
            out,
        )
    t.write(
        "fab",
        "gerber",
        "--board",
        board,
        "--out",
        t.root / "gerber",
    )
    t.write("fab", "drill", "--board", board, "--out", t.root / "drill")
    put(
        t.root / "FUNCTIONAL-RESULT.json",
        {
            "purpose": intent["purpose"],
            "route_receipts": route_receipts,
            "final_board_sha256": sha(board),
            "final_schematic_sha256": sha(source),
            "erc": json.loads((t.root / "eco-saved-erc.json").read_bytes()),
            "native_before_routing": json.loads(native_before.read_bytes()),
            "native_after": report,
            "not_claimed": intent["not_claimed"],
            "calls": t.index,
        },
    )
    print("PUBLIC FUNCTIONAL WORKFLOW COMPLETE", t.root)


if __name__ == "__main__":
    main(*sys.argv[1:])
