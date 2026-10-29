"""Bounded change of the previously accepted J1/C1/R1 snapshot.

This is not a general circuit executor. Expected identities/nets/poses/rules are
frozen in engineering_change_intent.json before the first product invocation.
"""

from __future__ import annotations

import argparse
import json
import shutil
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from change_support import Run, arguments, sha, snapshot, write
from consume_changed_copper import consume_tracks
from consume_engineering_outputs import consume
from engineering_intent import pcb_intent, thermal_intent

INTENT_PATH = Path(__file__).with_name("engineering_change_intent.json")


def validate_baseline(path, intent):
    for name, digest in intent["baseline_files"].items():
        file = path / name
        if not file.is_file() or sha(file) != digest:
            raise ValueError(f"Frozen baseline missing or changed: {name}")


def observe_schematic(path, intent, edited):
    xml = ET.parse(path).getroot()
    nets = {
        n.attrib["name"].removeprefix("/"): sorted(
            p.attrib["ref"] + "." + p.attrib["pin"]
            for p in n.findall("node")
            if not p.attrib["ref"].startswith("#")
        )
        for n in xml.findall("./nets/net")
    }
    parts = {
        p.attrib["ref"]: {
            "value": p.findtext("value"),
            "footprint": p.findtext("footprint"),
            "uuid": p.findtext("tstamps"),
            "sheet": p.find("sheetpath").attrib,
        }
        for p in xml.findall("./components/comp")
    }
    expected = {
        p["ref"]: {
            "value": intent["edit"][p["ref"] + ".Value"]["after"]
            if edited and p["ref"] + ".Value" in intent["edit"]
            else p["value"],
            "footprint": p["footprint"],
        }
        for p in intent["parts"]
    }
    observed = {ref: {k: p[k] for k in ("value", "footprint")} for ref, p in parts.items()}
    return {
        "passed": nets == intent["nets"] and observed == expected,
        "nets": {"expected": intent["nets"], "observed": nets},
        "parts": {"expected": expected, "observed": parts},
        "input_sha256": sha(path),
    }


def change_checks(actual, baseline, intent, *, moved, edited, copper=False):
    checks = pcb_intent(actual, intent, edited=edited)["checks"]

    def check(name, expected, observed):
        checks.append(
            {"id": name, "expected": expected, "observed": observed, "passed": expected == observed}
        )

    before = {p["ref"]: p for p in baseline["footprints"]}
    poses = intent["final_positions_mm" if moved else "baseline_positions_mm"]
    for fp in actual["footprints"]:
        ref = fp["ref"]
        check(ref + ".uuid", before[ref]["uuid"], fp["uuid"])
        check(ref + ".position_nm", [round(v * 1e6) for v in poses[ref]], fp["position_nm"])
        check(ref + ".rotation_deg", intent["pose"]["rotation_deg"], fp["rotation_deg"])
        check(ref + ".layer", intent["pose"]["layer"], fp["layer"])
        delta = [round(v * 1e6) - before[ref]["position_nm"][i] for i, v in enumerate(poses[ref])]

        def pad_rows(pads, offset):
            return Counter(
                (
                    p["number"],
                    p["net"],
                    tuple(p["position_nm"][i] + offset[i] for i in range(2)),
                    tuple(p["drill_nm"]),
                )
                for p in pads
            )

        check(
            ref + ".all-physical-pad-poses-drills",
            sorted(pad_rows(before[ref]["pads"], delta).items()),
            sorted(pad_rows(fp["pads"], [0, 0]).items()),
        )
        check(ref + ".dnp", before[ref]["dnp"], fp["dnp"])
    check("locked-ref-set", intent["pose"]["locked_refs"], actual["locked_refs"])
    check(
        "symbol-uuid-paths",
        [(p["ref"], p["schematic_path"]) for p in baseline["identity_text"]],
        [(p["ref"], p["schematic_path"]) for p in actual["identity_text"]],
    )

    def protected(observation):
        return [
            t
            for t in observation["track_geometry"]
            if t["net"] in intent["copper"]["protected_nets"]
        ]

    check("protected-copper", protected(baseline), protected(actual))
    check("edge-bounds", baseline["edge_bbox_nm"], actual["edge_bbox_nm"])
    before_edges, after_edges = baseline.get("edge_geometry", {}), actual.get("edge_geometry", {})
    check(
        "edge-geometry-supported",
        True,
        before_edges.get("status") == after_edges.get("status") == "supported",
    )
    check("edge-geometry", before_edges.get("segments"), after_edges.get("segments"))

    def definition(observation):
        return [
            {k: v for k, v in z.items() if k not in ("filled", "filled_polygons")}
            for z in observation["zone_geometry"]
        ]

    check("zone-definitions", definition(baseline), definition(actual))
    if copper:
        for t in actual["track_geometry"]:
            if t["net"] not in intent["copper"]["nets"]:
                continue
            if t["kind"] == "PCB_TRACK":
                check(
                    t["uuid"] + ".width_nm",
                    round(intent["copper"]["width_mm"] * 1e6),
                    t["width_nm"],
                )
                check(t["uuid"] + ".allowed-layer", True, t["layer"] in intent["copper"]["layers"])
            elif t["kind"] == "PCB_VIA":
                check(
                    t["uuid"] + ".via-dimensions",
                    [600000, 300000, ["B.Cu", "F.Cu"]],
                    [t["width_nm"], t["drill_nm"], sorted(t["width_by_layer_nm"])],
                )
            else:
                check(
                    t["uuid"] + ".supported-copper-kind",
                    "straight segment or through via",
                    t["kind"],
                )
        for pad in actual["pad_components"]:
            observed = sorted(set(pad["connected_pads"]) | {pad["ref"] + "." + pad["number"]})
            check(pad["uuid"] + ".connected-physical-pins", intent["nets"][pad["net"]], observed)
        cls = next(c for c in actual["effective_netclasses"] if c["net"] == "VIN")
        check(
            "effective-VIN-rules",
            {
                "net": "VIN",
                "name": "VIN_Change",
                "width_nm": 250000,
                "clearance_nm": 200000,
                "via_diameter_nm": 600000,
                "via_drill_nm": 300000,
            },
            cls,
        )
        checks += thermal_intent(actual, intent)["checks"]
    return {
        "status": "passed" if all(c["passed"] for c in checks) else "failed",
        "checks": checks,
        "source": "fresh native reader versus pre-execution intent and frozen baseline",
        "position_width_units": "nm",
        "numeric_tolerance_nm": 1,
        "angle_units": "degrees",
    }


def run(options):
    intent = json.loads(INTENT_PATH.read_bytes())
    validate_baseline(options.baseline, intent)
    trial = Run(options)
    root = trial.root
    shutil.copyfile(INTENT_PATH, root / "frozen-intent.json")
    project = root / "project"
    project.mkdir()
    for name in intent["baseline_files"]:
        shutil.copyfile(options.baseline / name, project / name)
    board, schematic = (
        project / ("decoupling" + suffix) for suffix in (".kicad_pcb", ".kicad_sch")
    )
    if trial.standalone:
        for command in ("reference", "context", "doctor"):
            trial.call("discovery-" + command, [command, "--compact"])
        trial.call("public-before-board", ["board", "check", "--board", board, "--compact"])
        trial.call("public-before-sch", ["sch", "check", "--schematic", schematic, "--compact"])
    write(
        root / "baseline-source.json",
        {"source": str(options.baseline.resolve()), "files": snapshot(project)},
    )
    baseline = trial.native("baseline", "native_change_reader.py", board)

    def require(name, report):
        write(root / (name + ".json"), report)
        assert report.get("status") == "passed" or report.get("passed") is True, name

    def xml(name, path=schematic):
        output = root / (name + ".xml")
        trial.official_call(
            name, ["sch", "export", "netlist", "--format", "kicadxml", "--output", output, path]
        )
        return output

    require("baseline-intent", change_checks(baseline, baseline, intent, moved=False, edited=False))
    initial_sch = observe_schematic(xml("baseline-netlist"), intent, False)
    require("baseline-schematic-intent", initial_sch)
    edit = root / "edit.json"
    write(edit, {"changes": [{"op": "set", "ref": "C1", "value": "470nF"}]})
    trial.mutate("edit", ["sch", "edit", "--schematic", str(schematic), "--changes", str(edit)])
    edited_sch = observe_schematic(xml("edited-netlist"), intent, True)
    require("edited-schematic-intent", edited_sch)
    assert {r: p["uuid"] for r, p in initial_sch["parts"]["observed"].items()} == {
        r: p["uuid"] for r, p in edited_sch["parts"]["observed"].items()
    }
    trial.mutate("sync", ["project", "sync", "--board", str(board), "--schematic", str(schematic)])
    synced = trial.native("synced", "native_change_reader.py", board)
    require("synced-intent", change_checks(synced, baseline, intent, moved=False, edited=True))
    assert {p["ref"]: p["schematic_path"].rsplit("/", 1)[-1] for p in synced["identity_text"]} == {
        r: p["uuid"] for r, p in edited_sch["parts"]["observed"].items()
    }, "Saved PCB symbol UUID paths must match independently exported schematic identities"
    assert synced["track_geometry"] == baseline["track_geometry"]
    trial.mutate(
        "move", ["board", "footprint", "move", "--board", str(board), "--moves", "R1:15.460,14.281"]
    )
    moved = trial.native("moved", "native_change_reader.py", board)
    require("moved-intent", change_checks(moved, baseline, intent, moved=True, edited=True))
    assert moved["track_geometry"] == baseline["track_geometry"], (
        "move must not silently move copper"
    )
    trial.official_call(
        "moved-drc", ["pcb", "drc", "--format", "json", "--output", root / "moved-drc.json", board]
    )
    trial.mutate(
        "netclass",
        [
            "project",
            "netclass",
            "set",
            "--board",
            str(board),
            "--name",
            "VIN_Change",
            "--nets",
            "VIN",
            "--width",
            "0.25",
            "--clearance",
            "0.2",
            "--via-diameter",
            "0.6",
            "--via-drill",
            "0.3",
        ],
    )
    trial.mutate(
        "repair",
        ["board", "route", "repair", "--board", str(board), "--nets", "VIN", "--neck", "0.2"],
    )
    repaired = trial.native("repaired", "native_change_reader.py", board)
    write(
        root / "repair-widths.json",
        {
            "note": "Repair is not a whole-net width conversion",
            "segments": repaired["track_geometry"],
        },
    )
    # Repair can leave the original route ending at the former pad. Replace only
    # the explicitly selected VIN routing to remove that stub and meet the class.
    # This is the existing rewidth capability, not whole-board rebuild.
    trial.mutate(
        "rewidth",
        ["board", "route", "rewidth", "--board", str(board), "--nets", "VIN", "--neck", "0.25"],
    )
    trial.mutate(
        "widen",
        ["board", "track", "widen", "--board", str(board), "--nets", "VIN", "--neck", "0.2"],
    )
    trial.mutate(
        "native-refill", ["board", "zone", "stitch", "--board", str(board), "--net", "GND"]
    )
    silk = trial.mutate(
        "silk", ["board", "silkscreen", "place", "--board", str(board), "--clearance", "0.15"]
    )
    assert all(c["clear"] and c["inside_edge"] for c in silk["extensions"]["task"]["checks"])
    final = trial.native("final", "native_change_reader.py", board)
    require(
        "final-intent", change_checks(final, baseline, intent, moved=True, edited=True, copper=True)
    )
    trial.mutate(
        "refill-repeat", ["board", "zone", "stitch", "--board", str(board), "--net", "GND"]
    )
    reopened = trial.native("reopened-sdk", "native_change_reader.py", board)
    require(
        "reopened-intent",
        change_checks(reopened, baseline, intent, moved=True, edited=True, copper=True),
    )
    if trial.standalone:
        trial.call("public-reopened-board", ["board", "check", "--board", board, "--compact"])
        trial.call("public-reopened-sch", ["sch", "check", "--schematic", schematic, "--compact"])
    for key in ("zone_geometry", "track_geometry", "identity_text", "footprints"):
        assert final[key] == reopened[key], ("repeat changed saved geometry", key)
    rules_before = json.loads((options.baseline / "decoupling.kicad_pro").read_bytes())
    rules_after = json.loads(board.with_suffix(".kicad_pro").read_bytes())
    assert {k: v for k, v in rules_before.items() if k != "net_settings"} == {
        k: v for k, v in rules_after.items() if k != "net_settings"
    }
    for name, args in (
        ("erc", ["sch", "erc", str(schematic)]),
        ("drc", ["pcb", "drc", str(board)]),
    ):
        trial.official_call(name, args + ["--format", "json", "--output", root / (name + ".json")])
    erc, drc = (json.loads((root / (n + ".json")).read_bytes()) for n in ("erc", "drc"))
    assert not [v for s in erc["sheets"] for v in s["violations"]]
    assert not drc["violations"] and not drc["unconnected_items"]
    freeze = {name: sha(project / name) for name in intent["baseline_files"]}
    shutil.copytree(project, root / "saved-snapshot")
    write(
        root / "output-input-snapshot.json",
        {
            "project": freeze,
            "configuration": snapshot(root / "configuration"),
            "native": trial.binding,
        },
    )
    for name, args in (
        ("gerber", ["fab", "gerber"]),
        ("drill", ["fab", "drill"]),
        ("silk-svg", ["board", "plot", "--file-format", "svg", "--layers", "F.SilkS,Edge.Cuts"]),
    ):
        trial.mutate(name, args + ["--board", str(board), "--out", str(root / name)])
    trial.official_call("bom", ["sch", "export", "bom", "--output", root / "bom.csv", schematic])
    trial.official_call(
        "cpl",
        [
            "pcb",
            "export",
            "pos",
            "--format",
            "csv",
            "--units",
            "mm",
            "--output",
            root / "cpl.csv",
            board,
        ],
    )
    assert freeze == {name: sha(project / name) for name in freeze}
    assert (
        snapshot(root / "configuration")
        == json.loads((root / "output-input-snapshot.json").read_bytes())["configuration"]
    ), "Export configuration changed"
    manufacturing = consume(root)
    write(root / "changed-copper-gerber.json", consume_tracks(root, reopened, ["VIN"]))
    clear_root = root / "clear-polarity-negative"
    shutil.copytree(root / "gerber", clear_root / "gerber")
    for file in (clear_root / "gerber").glob("*-[FB]_Cu.gbr"):
        text = file.read_text(encoding="utf-8")
        assert "%LPD*%" in text
        file.write_text(text.replace("%LPD*%", "%LPC*%", 1), encoding="utf-8")
    try:
        consume_tracks(clear_root, reopened, ["VIN"])
    except ValueError as exc:
        write(
            clear_root / "result.json",
            {
                "status": "passed_rejection",
                "change": "LPD to LPC only; coordinate and width records unchanged",
                "observed": str(exc),
            },
        )
    else:
        raise AssertionError("Clear polarity must not certify copper")
    # Same exported coordinates and apertures; change only attribute encoding/value.
    import re

    for form in ("direct", "comment"):
        for polarity in ("Positive", "Negative"):
            variant = root / f"file-polarity-{form}-{polarity.lower()}"
            shutil.copytree(root / "gerber", variant / "gerber")
            changed = []
            for file in (variant / "gerber").glob("*-[FB]_Cu.gbr"):
                text = file.read_text(encoding="utf-8")
                attribute = "TF.FilePolarity," + polarity
                encoded = f"%{attribute}*%" if form == "direct" else f"G04 #@! {attribute}*"
                text, count = re.subn(
                    r"%TF.FilePolarity,[^*]*\*%|G04\s+#@!\s+TF.FilePolarity,[^*]*\*", encoded, text
                )
                assert count == 1
                file.write_text(text, encoding="utf-8")
                changed.append({"file": file.name, "sha256": sha(file)})
            try:
                accepted = consume_tracks(variant, reopened, ["VIN"])
            except ValueError as exc:
                assert polarity == "Negative"
                outcome = {"status": "passed_rejection", "observed": str(exc)}
            else:
                assert polarity == "Positive" and accepted["status"] == "passed"
                outcome = accepted
            write(
                variant / "result.json", dict(outcome, form=form, polarity=polarity, files=changed)
            )
    outline_root = root / "outline-same-bbox-negative"
    shutil.copytree(project, outline_root)
    bad_outline = outline_root / board.name
    trial.call(
        "outline-fault",
        [
            "-I",
            "-B",
            Path(__file__).with_name("native_intent_faults.py"),
            bad_outline,
            "outline-same-bbox",
        ],
        program=[str(trial.sdk)],
        json_output=False,
    )
    outline = trial.native("outline-read", "native_change_reader.py", bad_outline)
    assert outline["edge_bbox_nm"] == reopened["edge_bbox_nm"]
    outline_result = change_checks(outline, baseline, intent, moved=True, edited=True)
    assert {c["id"] for c in outline_result["checks"] if not c["passed"]} == {"edge-geometry"}
    write(outline_root / "comparison.json", outline_result)
    for kind in ("pcb-wrong-net", "pcb-wrong-pad", "pcb-wrong-footprint"):
        directory = root / kind
        directory.mkdir()
        damaged = directory / board.name
        shutil.copyfile(board, damaged)
        trial.call(
            kind + "-fault",
            ["-I", "-B", Path(__file__).with_name("native_intent_faults.py"), damaged, kind],
            program=[str(trial.sdk)],
            json_output=False,
        )
        check = pcb_intent(
            trial.native(kind + "-read", "native_sdk_reader.py", damaged), intent, edited=True
        )
        write(directory / "comparison.json", check)
        assert check["status"] == "failed"
        assert sha(schematic) == freeze[schematic.name]
    # Negative fixture setup is independent native mutation of a disposable copy.
    negative = root / "negative-one-layer"
    shutil.copytree(project, negative)
    bad = negative / board.name
    trial.call(
        "one-layer-fault",
        [
            "-I",
            "-B",
            Path(__file__).with_name("native_intent_faults.py"),
            bad,
            "stitch-single-layer-contact",
        ],
        program=[str(trial.sdk)],
        json_output=False,
    )
    observed = trial.native("one-layer-read", "native_engineering_reader.py", bad)
    refused = thermal_intent(observed, intent)
    assert refused["status"] == "failed"
    ground_vias = [v for v in observed["native_connectivity"]["vias"] if v["net"] == "GND"]
    assert ground_vias
    for via in ground_vias:
        assert {"F.Cu", "B.Cu"} <= {
            layer for zone in via["component_zones"] for layer in zone["layers"]
        }
        contacts = {
            r["layer"]: r["direct_copper_intersection"] for r in via["direct_zone_contacts"]
        }
        assert contacts == {"F.Cu": True, "B.Cu": False}
    trial.official_call(
        "one-layer-drc", ["pcb", "drc", "--format", "json", "--output", negative / "drc.json", bad]
    )
    write(
        root / "one-layer-negative.json",
        {
            "independent": refused,
            "native_drc": json.loads((negative / "drc.json").read_bytes()),
            "interpretation": (
                "Independent criterion and native finding are separate; "
                "intentional fixture damage is not a product defect"
            ),
        },
    )
    trial.finish_binding()
    write(
        root / "summary.json",
        {
            "status": "passed",
            "intent_sha256": sha(root / "frozen-intent.json"),
            "baseline": intent["baseline_files"],
            "final": freeze,
            "erc": erc,
            "drc": drc,
            "manufacturing": manufacturing,
            "source": trial.binding,
            "actual_copper": reopened["track_geometry"],
            "unrelated_preservation": "See final-intent.json per-object checks",
            "not_covered": intent["not_covered"],
        },
    )
    validate_baseline(options.baseline, intent)
    return {"status": "passed", "evidence": str(root)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    arguments(parser)
    parser.add_argument(
        "--baseline",
        type=Path,
        required=True,
        help="Explicit accepted three-part project, read only",
    )
    options = parser.parse_args()
    try:
        print(json.dumps(run(options)))
    except (ValueError, RuntimeError, AssertionError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
