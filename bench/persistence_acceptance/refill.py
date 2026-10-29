"""One changed refill after a supported public move on a saved synthetic RC copy."""

import json
import sys
from pathlib import Path

from .fill_geometry import NEW, OLD, ZONE, compare, metrics, parse, protected
from .support import ASSETS, ROOT, Scene, now, put, read, sha, tree


def native_snapshot(scene, stage, *, dirty=False):
    folder = scene.root / stage
    folder.mkdir()
    pcb, sch = (scene.project / ("rc4." + suffix) for suffix in ("kicad_pcb", "kicad_sch"))
    before = tree(scene.project)
    native, python = scene.env["KICAD_CLI_OFFICIAL"], scene.env["KICAD_CLI_PYTHON"]
    readers, intent = ASSETS["readers"], ASSETS["intent"]
    xml = folder / "netlist.xml"
    code, _ = scene.raw_call(
        stage + "-netlist",
        [native, "sch", "export", "netlist", "--format", "kicadxml", "--output", xml, sch],
        report={"path": str(xml), "kind": "xml"},
        json_output=False,
    )
    assert code == 0
    code, graph = scene.raw_call(
        stage + "-pin-intent", [sys.executable, readers / "compare_native_netlist.py", xml, intent]
    )
    assert code == 0 and graph["passed"], graph
    observation = folder / "native-board.json"
    code, physical = scene.raw_call(
        stage + "-native-board",
        [python, readers / "native_board_observe.py", pcb, intent, observation, xml],
    )
    assert code == 0 and physical["physical_intent_passed"], physical
    code, fill = scene.raw_call(
        stage + "-native-fill",
        [python, Path(__file__).with_name("native_fill_observe.py"), pcb, folder / "fill.json"],
    )
    assert code == 0
    drc_path = folder / "drc.json"
    code, _ = scene.raw_call(
        stage + "-native-drc",
        [
            native,
            "pcb",
            "drc",
            "--schematic-parity",
            "--format",
            "json",
            "--units",
            "mm",
            "--severity-all",
            "--exit-code-violations",
            "--output",
            drc_path,
            pcb,
        ],
        report={"path": str(drc_path), "kind": "drc"},
        json_output=False,
    )
    drc = read(drc_path)
    assert code in (0, 5), code
    errors = [v for v in drc["violations"] if v.get("severity") == "error"]
    if not dirty:
        assert not errors and not drc["unconnected_items"], drc
        assert all(z["filled"] and z["saved_contours"] == 1 for z in fill["zones"])
        assert all(c["touches_saved_fill"] for z in fill["zones"] for c in z["ground_contacts"])
        assert not any(
            c["touches_saved_fill"] for z in fill["zones"] for c in z["isolated_testpoint"]
        )
    assert tree(scene.project) == before
    summary = {
        "board_sha256": sha(pcb),
        "graph_passed": graph["passed"],
        "physical_passed": physical["physical_intent_passed"],
        "native_drc_exit": code,
        "errors": len(errors),
        "error_types": [v["type"] for v in errors],
        "unconnected": len(drc["unconnected_items"]),
        "warnings": len(drc["violations"]) - len(errors),
        "read_only": True,
        "preparation_dirty_fill_expected": dirty,
    }
    put(folder / "SUMMARY.json", summary)
    return read(observation), fill, summary


def apply(scene, name, args, *, expect_change):
    pcb = scene.project / "rc4.kicad_pcb"
    before = sha(pcb)
    code, value = scene.call(name + "-preview", [*args, "--dry-run"])
    assert code == 0 and value["data"]["preview"]["can_apply"], value
    preview, token = value["data"]["preview"], value["data"]["confirm_token"]
    assert sha(pcb) == before
    if "refill" in name:
        assert preview["level"] == "plan" and preview["trial_snapshot"] is None
        assert preview["extensions"]["selected"] == [ZONE]
        assert preview["extensions"]["params"]["all"] is False
    code, value = scene.call(name + "-confirm", [*args, "--confirm", token])
    assert code == 0 and value["ok"], value
    receipt = value["data"]["receipt"]
    assert (sha(pcb) != before) == expect_change, receipt["outcome"]
    assert receipt["outcome"]["persistence"] == ("saved" if expect_change else "unchanged")
    code, shown = scene.call(
        name + "-fresh-show", ["operation", "show", "--id", preview["operation_id"]]
    )
    assert code == 0 and shown["data"]["operation_status"]["status"] == "succeeded", shown
    return {
        "operation_id": preview["operation_id"],
        "before": before,
        "after": sha(pcb),
        "execution": receipt["outcome"]["execution"],
        "change": receipt["outcome"]["change"],
        "persistence": receipt["outcome"]["persistence"],
        "new_process_status": shown["data"]["operation_status"]["status"],
    }


def render(scene):
    folder = scene.root / "visual"
    folder.mkdir()
    images = {}
    for side, layers in (("front", "F.Cu,F.SilkS,Edge.Cuts"), ("back", "B.Cu,B.SilkS,Edge.Cuts")):
        svg, png = folder / (side + ".svg"), folder / (side + ".png")
        code, _ = scene.raw_call(
            side + "-svg",
            [
                scene.env["KICAD_CLI_OFFICIAL"],
                "pcb",
                "export",
                "svg",
                "--layers",
                layers,
                "--mode-single",
                "--fit-page-to-board",
                "--exclude-drawing-sheet",
                "--output",
                svg,
                scene.project / "rc4.kicad_pcb",
            ],
            json_output=False,
        )
        assert code == 0
        code, _ = scene.raw_call(
            side + "-png",
            [sys.executable, ASSETS["readers"] / "render_svg.py", svg, png, "10"],
        )
        assert code == 0
        images[side] = {"svg_sha256": sha(svg), "png_sha256": sha(png)}
    put(
        folder / "REVIEW-REQUIRED.json", {"status": "pending_human_visual_review", "images": images}
    )
    return images


def run():
    scene = Scene(ROOT, "TP-S4")
    pcb = scene.project / "rc4.kicad_pcb"
    result = {"case": "refill-changed", "binding": scene.binding, "started_at": now()}
    try:
        original = tree(scene.project)
        baseline = parse(pcb.read_text(encoding="utf-8"))
        put(
            scene.root / "FROZEN.json",
            {
                "binding": scene.binding,
                "files": original,
                "origin": scene.origin,
                "intent_sha256": sha(ASSETS["intent"]),
                "selected_zone": ZONE,
                "preparation": {
                    "command": "board footprint move",
                    "ref": "TP1",
                    "old": OLD,
                    "new": NEW,
                },
                "expected": (
                    "F.Cu GND pad clearance hole moves; B.Cu fill and all other objects unchanged"
                ),
                "changed_region_mm": "union of 1.2 mm half-width squares about old/new centres",
                "rules": "No change to any rule, severity or exclusion",
                "attempts": (
                    "One public move, one changed refill, one repeat refill. Stop on native crash."
                ),
            },
        )
        scene.freeze("original-copy")
        for command in ("reference", "context", "doctor"):
            code, value = scene.call(command, [command])
            assert code == 0 and value["ok"], value
        before, fill_before, before_summary = native_snapshot(scene, "before")
        result["baseline"] = before_summary
        move_args = ["board", "footprint", "move", "--board", str(pcb), "--moves", "TP1:55,34"]
        result["preparation"] = apply(scene, "move", move_args, expect_change=True)
        dirty = parse(pcb.read_text(encoding="utf-8"))
        assert protected(baseline, move=True) == protected(dirty, move=True)
        assert metrics(baseline) == metrics(dirty), "Public move unexpectedly changed fill"
        scene.freeze("prepared-copy")
        prepared, fill_dirty, dirty_summary = native_snapshot(scene, "prepared", dirty=True)
        result["prepared"] = dirty_summary
        assert any(
            c["touches_saved_fill"] for z in fill_dirty["zones"] for c in z["isolated_testpoint"]
        )
        assert [f for f in before["footprints"] if f["ref"] != "TP1"] == [
            f for f in prepared["footprints"] if f["ref"] != "TP1"
        ]
        tp = next(f for f in prepared["footprints"] if f["ref"] == "TP1")
        assert tp["position_nm"] == [55000000, 34000000]
        args = ["board", "zone", "refill", "--board", str(pcb), "--zones", ZONE]
        result["refill"] = apply(scene, "refill", args, expect_change=True)
        saved = parse(pcb.read_text(encoding="utf-8"))
        geometry = compare(dirty, saved)
        put(scene.root / "GEOMETRY.json", geometry)
        assert geometry["passed"], geometry
        result["geometry"] = geometry
        after, fill_after, after_summary = native_snapshot(scene, "after")
        result["after"] = after_summary
        assert prepared["footprints"] == after["footprints"]
        assert {t["uuid"]: t for t in before["tracks"]} == {t["uuid"]: t for t in after["tracks"]}
        assert [z["ground_contacts"] for z in fill_before["zones"]] == [
            z["ground_contacts"] for z in fill_after["zones"]
        ]
        result["native_fill_after"] = fill_after
        result["unchanged_copper_objects"] = len(after["tracks"])
        result["repeat"] = apply(scene, "repeat-refill", args, expect_change=False)
        assert {n: sha(scene.project / n) for n in original if n != pcb.name} == {
            n: h for n, h in original.items() if n != pcb.name
        }
        result["visual_images"] = render(scene)
        result["visual_status"] = "pending_review"
        scene.freeze("final-files")
        result.update(status="automated_passed_visual_pending", protected_non_targets=True)
    except BaseException as exc:
        result.update(status="failed", failure=type(exc).__name__ + ": " + str(exc))
    result["finished_at"] = now()
    put(scene.root / "RESULT.json", result)
    print(json.dumps({k: result.get(k) for k in ("status", "failure", "refill", "repeat")}))
    return result
