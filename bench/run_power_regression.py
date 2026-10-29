"""Replay the original power recipe and independently check its frozen ATmega intent.

All inputs are explicit. Historical scripts and delivery directories are never
searched. Library contents must match the original manifest; only owned tables
are relocated to the explicitly selected existing installation.
"""

import argparse
import json
import shutil
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from change_support import Run, arguments, sha, write
from consume_power_outputs import consume


def graph(path, expected):
    actual = {
        n.attrib["name"].removeprefix("/"): sorted(
            p.attrib["ref"] + "." + p.attrib["pin"]
            for p in n.findall("node")
            if p.attrib["ref"] in expected["refs"]
        )
        for n in ET.parse(path).findall("./nets/net")
    }
    return {n: actual.get(n) for n in expected["named_nets"]}


def board_intent(actual, expected, profile):
    assert Counter(p["ref"] for p in actual["footprints"]) == Counter(expected["refs"])
    logical = {pin: net for net, pins in expected["named_nets"].items() for pin in pins}
    physical = []
    for fp in actual["footprints"]:
        ref = fp["ref"]
        want = profile["parts"][ref]
        assert (fp["value"], fp["footprint"], fp["dnp"], fp["layer"], fp["rotation_deg"]) == (
            want["value"],
            want["footprint"],
            want["dnp"],
            want["layer"],
            want["rotation_deg"],
        )
        assert fp["position_nm"] == [round(v * 1e6) for v in want["position_mm"]]
        assert Counter(p["number"] for p in fp["pads"]) == Counter(
            p["number"] for p in want["pads"]
        )
        for pad in fp["pads"]:
            key = ref + "." + pad["number"]
            if key in logical:
                assert pad["net"].removeprefix("/") == logical[key], (key, pad)
                physical.append({"pin": key, "expected": logical[key], "observed": pad["net"]})
            else:
                assert (
                    key in expected["unused_u1_pins"]
                    and pad["net"].removeprefix("/") not in expected["named_nets"]
                )
    return {
        "status": "passed",
        "named_nets": len(expected["named_nets"]),
        "logical_pins": len(logical),
        "physical_pads": sum(len(p["pads"]) for p in actual["footprints"]),
        "physical_named_mapping": physical,
        "duplicate_policy": (
            "Physical occurrences counted against original assembly profile; "
            "each occurrence compared to frozen logical pin"
        ),
    }


def negative_erc(trial, negative, sch, expected, bad, unchanged):
    """Commit the independent verdict before invoking a fallible native process."""
    assert unchanged
    write(
        trial.root / "negative-intent.json",
        {
            "status": "passed_rejection",
            "expected": expected["named_nets"],
            "observed": bad,
            "changed_pins": ["R1.2"],
            "board_unchanged": unchanged,
            "native_erc_evidence": "negative-native-erc.json",
            "downstream": "stopped on independent intent mismatch",
        },
    )
    native = {"status": "started", "independent_intent": "already persisted"}
    write(trial.root / "negative-native-erc.json", native)
    try:
        trial.official_call(
            "negative-erc",
            [
                "sch",
                "erc",
                "--format",
                "json",
                "--output",
                negative / "erc.json",
                negative / sch.name,
            ],
        )
        native.update(
            status="completed_valid_report", report=json.loads((negative / "erc.json").read_bytes())
        )
    except BaseException as exc:
        native.update(status="failed_or_incomplete", error=type(exc).__name__ + ": " + str(exc))
        write(
            trial.root / "result.json",
            {
                "status": "incomplete_native_failure",
                "independent_negative_intent": "passed_rejection",
                "native_negative_erc": native,
                "source_binding": trial.binding,
                "whole_workflow_passed": False,
            },
        )
        raise
    finally:
        record = trial.root / "negative-erc.call.json"
        if record.is_file():
            native["call"] = json.loads(record.read_bytes())
        write(trial.root / "negative-native-erc.json", native)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    arguments(parser)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument(
        "--reference-data",
        type=Path,
        required=True,
        help="Original expected-design, assembly-profile and libraries-manifest directory",
    )
    options = parser.parse_args()
    expected_path = options.reference_data / "expected-design.json"
    expected = json.loads(expected_path.read_bytes())
    profile = json.loads((options.reference_data / "assembly-profile.json").read_bytes())
    assert (
        sha(expected_path)
        == profile["expected_design_sha256"]
        == "4690e29fd271328110aefbd51e45c45c3d6b1a29a17d705b6aaf121e5ad07f5b"
    )
    assert (
        sha(options.baseline / "atmega328p.kicad_pcb")
        == "813c33fec3fe09a915c1b83dfb122e08554b99ba49f28679b31ed7d8fd18aaeb"
    )
    libraries = json.loads((options.reference_data / "libraries-manifest.json").read_bytes())
    for name, metadata in libraries.items():
        assert sha(options.kicad_root / name.removeprefix("libraries/")) == metadata["sha256"], name
    trial = Run(options)
    root = trial.root
    project = root / "project"
    project.mkdir()
    for suffix in (".kicad_pcb", ".kicad_pro", ".kicad_sch"):
        shutil.copyfile(
            options.baseline / ("atmega328p" + suffix), project / ("atmega328p" + suffix)
        )
    # Explicit fixture configuration relocation: same frozen library bytes, no hidden old paths.
    for kind, ext, table in (("footprints", ".pretty", "fp"), ("symbols", ".kicad_sym", "sym")):
        names = sorted(
            {Path(n).parts[4].removesuffix(ext) for n in libraries if "/" + kind + "/" in n}
        )
        text = "(" + table + "_lib_table\n (version 7)\n"
        for name in names:
            path = (options.kicad_root / "share/kicad" / kind / (name + ext)).as_posix()
            text += (
                f' (lib (name "{name}") (type "KiCad") (uri "{path}") (options "") (descr ""))\n'
            )
        (project / (table + "-lib-table")).write_text(text + ")\n", encoding="utf-8")
    for name in ("expected-design.json", "assembly-profile.json", "libraries-manifest.json"):
        shutil.copyfile(options.reference_data / name, root / name)
    write(
        root / "frozen-criteria.json",
        {
            "VIN": {"segments": 10, "width_nm": 300000},
            "+5V": {"segments": 41, "width_nm": 250000},
            "intent_sha256": sha(expected_path),
            "tolerance_nm": 1,
            "basis": "Unchanged original geometric test; no load approval",
            "input": {p.name: sha(p) for p in project.iterdir()},
            "library_relocation": (
                "owned tables now point to explicit installation; "
                "every selected library hash checked"
            ),
        },
    )
    board = project / "atmega328p.kicad_pcb"
    sch = board.with_suffix(".kicad_sch")
    before = trial.native("before-native", "native_change_reader.py", board)
    write(root / "before-intent.json", board_intent(before, expected, profile))
    recipe = json.loads(
        Path(__file__).with_name("specs").joinpath("power-atmega-recipe.json").read_bytes()
    )
    for index, step in enumerate(recipe):
        receipt = trial.mutate(
            "power-" + str(index), [*step["command"].split(), "--board", str(board), *step["args"]]
        )
        if step.get("require_target_width"):
            assert receipt["extensions"]["quality"]["all_selected_target_met"]
    after = trial.native("after-native", "native_change_reader.py", board)
    write(root / "after-intent.json", board_intent(after, expected, profile))
    assert before["footprints"] == after["footprints"] and before["origin_nm"] == after["origin_nm"]

    def unrelated(r):
        return [t for t in r["track_geometry"] if t["net"] not in ("VIN", "+5V")]

    assert unrelated(before) == unrelated(after)
    for net, width, count in (("VIN", 300000, 10), ("+5V", 250000, 41)):
        tracks = [
            t for t in after["track_geometry"] if t["net"] == net and t["kind"] == "PCB_TRACK"
        ]
        assert len(tracks) == count and all(t["width_nm"] == width for t in tracks)
        cls = next(c for c in after["effective_netclasses"] if c["net"] == net)
        assert cls["width_nm"] == width and cls["clearance_nm"] == 200000
    for name, args in (
        ("netlist", ["sch", "export", "netlist", "--format", "kicadxml"]),
        ("erc", ["sch", "erc", "--format", "json"]),
        ("drc", ["pcb", "drc", "--format", "json"]),
    ):
        destination = root / (name + (".xml" if name == "netlist" else ".json"))
        trial.official_call(name, [*args, "--output", destination, board if name == "drc" else sch])
    assert graph(root / "netlist.xml", expected) == expected["named_nets"]
    erc, drc = (json.loads((root / (n + ".json")).read_bytes()) for n in ("erc", "drc"))
    assert not [v for s in erc["sheets"] for v in s["violations"]]
    assert not drc["violations"] and not drc["unconnected_items"]
    freeze = {p.name: sha(p) for p in project.iterdir() if p.suffix != ".kicad_prl"}
    for name, args in (
        ("gerber", ["fab", "gerber", "--layers", ",".join(profile["gerber_layers"])]),
        ("drill", ["fab", "drill", "--aux-origin", "--map", "pdf"]),
    ):
        trial.mutate(name, args + ["--board", str(board), "--out", str(root / name)])
    for suffix, flags in (("", ["--exclude-dnp"]), ("-all", [])):
        trial.official_call(
            "bom" + suffix,
            [
                "sch",
                "export",
                "bom",
                "--fields",
                "Reference,Value,Footprint,QUANTITY,DNP",
                "--labels",
                "Refs,Value,Footprint,Qty,DNP",
                *flags,
                "--output",
                root / ("bom" + suffix + ".csv"),
                sch,
            ],
        )
        trial.official_call(
            "cpl" + suffix,
            [
                "pcb",
                "export",
                "pos",
                "--format",
                "csv",
                "--units",
                "mm",
                "--side",
                "both",
                "--use-drill-file-origin",
                *flags,
                "--output",
                root / ("cpl" + suffix + ".csv"),
                board,
            ],
        )
    outputs = consume(root, after, profile)
    write(root / "manufacturing-readback.json", outputs)
    assert freeze == {n: sha(project / n) for n in freeze}
    negative = root / "same-net-R1"
    shutil.copytree(project, negative)
    change = negative / "changes.json"
    write(
        change,
        {
            "changes": [
                {"op": "disconnect", "ref": "R1", "pin": "2"},
                {"op": "connect", "ref": "R1", "pin": "2", "net": "+5V"},
            ]
        },
    )
    trial.mutate(
        "same-net-R1",
        ["sch", "edit", "--schematic", str(negative / sch.name), "--changes", str(change)],
    )
    trial.official_call(
        "negative-xml",
        [
            "sch",
            "export",
            "netlist",
            "--format",
            "kicadxml",
            "--output",
            negative / "actual.xml",
            negative / sch.name,
        ],
    )
    bad = graph(negative / "actual.xml", expected)
    wanted = {k: list(v) for k, v in expected["named_nets"].items()}
    wanted["RESET"].remove("R1.2")
    wanted["+5V"] = sorted(wanted["+5V"] + ["R1.2"])
    assert bad == wanted and bad != expected["named_nets"]
    negative_erc(
        trial, negative, sch, expected, bad, sha(negative / board.name) == freeze[board.name]
    )
    trial.finish_binding()
    write(
        root / "result.json",
        {
            "status": "passed_bounded_power_regression",
            "snapshot": freeze,
            "source": trial.binding,
            "native_DRC": drc,
            "native_ERC": erc,
            "independent_intent": "passed 15 refs / 11 named nets / every physical occurrence",
            "manufacturing": outputs,
            "original_criteria": {"VIN": "10 segments at 0.30 mm", "+5V": "41 segments at 0.25 mm"},
            "ampacity": "not_assessed",
            "full_manufacturing": "not_certified",
        },
    )
    print(json.dumps({"status": "passed_bounded_power_regression", "evidence": str(root)}))


if __name__ == "__main__":
    main()
