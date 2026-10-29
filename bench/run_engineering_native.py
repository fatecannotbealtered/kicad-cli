"""Owned connector/decoupling task A+B through source or final EXE.

No original project, user editor, security setting or library is modified.
Native readers are repository-owned standalone observations; they import no product.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from engineering_intent import pcb_intent, thermal_intent

REPO = Path(__file__).resolve().parents[1]
SPEC = {
    "title": "Connector decoupling engineering acceptance",
    "parts": [
        {
            "ref": "J1",
            "symbol": "Connector_Generic:Conn_01x02",
            "value": "VIN",
            "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
        },
        {
            "ref": "C1",
            "symbol": "Device:C",
            "value": "100nF",
            "footprint": "Capacitor_SMD:C_0805_2012Metric",
        },
        {
            "ref": "R1",
            "symbol": "Device:R",
            "value": "10k",
            "footprint": "Resistor_SMD:R_0805_2012Metric",
        },
    ],
    "nets": [
        {"name": "VIN", "connect": ["J1.1", "C1.1", "R1.1"]},
        {"name": "GND", "connect": ["J1.2", "C1.2", "R1.2"]},
    ],
}
INTENT = {"VIN": {"J1.1", "C1.1", "R1.1"}, "GND": {"J1.2", "C1.2", "R1.2"}}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def graph(path):
    xml = ET.parse(path).getroot()
    return {
        n.attrib["name"].removeprefix("/"): {
            p.attrib["ref"] + "." + p.attrib["pin"]
            for p in n.findall("node")
            if not p.attrib["ref"].startswith("#")
        }
        for n in xml.findall("./nets/net")
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--kicad-root", type=Path, default=os.environ.get("KICAD_CLI_ROOT"))
    parser.add_argument("--native-python", type=Path, default=os.environ.get("KICAD_CLI_PYTHON"))
    parser.add_argument("--official-cli", type=Path, default=os.environ.get("KICAD_CLI_OFFICIAL"))
    parser.add_argument(
        "--spec", type=Path, help="Optional input copy of the declared three-part SPEC"
    )
    parser.add_argument(
        "--config-seed", type=Path, help="Existing version preferences to copy; never edited"
    )
    options = parser.parse_args()
    native_root = options.kicad_root
    if native_root is None and (options.native_python is None or options.official_cli is None):
        parser.error(
            "Set --kicad-root or both --native-python/--official-cli; "
            "no historical installation fallback"
        )
    SDK = options.native_python or native_root / "bin/python.exe"
    OFFICIAL = options.official_cli or native_root / "bin/kicad-cli.exe"
    native_root = native_root or SDK.parent.parent
    for label, dependency in (("native Python", SDK), ("official CLI", OFFICIAL)):
        if not dependency.is_file():
            parser.error(f"Missing {label}: {dependency}")
    if options.binary and not options.binary.is_file():
        parser.error(f"Missing candidate EXE: {options.binary}")
    if options.spec and json.loads(options.spec.read_bytes()) != SPEC:
        parser.error(
            "Input must match the frozen three-part SPEC; "
            "arbitrary circuit acceptance is outside this workflow"
        )
    if options.config_seed and not options.config_seed.is_dir():
        parser.error(f"Missing explicit configuration seed: {options.config_seed}")
    root = options.output.resolve()
    root.mkdir(exist_ok=False, parents=True)
    project = root / "project"
    project.mkdir()
    methods = root / "readers"
    methods.mkdir()
    for name in ("native_sdk_reader.py", "native_zone_reader.py"):
        shutil.copyfile(Path(__file__).with_name(name), methods / name)
    shutil.copyfile(
        Path(__file__).with_name("native_engineering_reader.py"),
        methods / "native_engineering_reader.py",
    )
    shutil.copyfile(
        Path(__file__).with_name("native_intent_faults.py"), methods / "native_intent_faults.py"
    )
    spec = project / "decoupling.json"
    write(spec, SPEC)
    configuration = root / "configuration"
    configuration.mkdir()
    if options.config_seed:
        seed = options.config_seed.resolve()
        shutil.copytree(seed, configuration / seed.name)
    write(
        root / "source-environment.json",
        {
            "repository": str(REPO),
            "source_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
            ).strip(),
            "native_root": str(native_root.resolve()),
            "native_python": str(SDK.resolve()),
            "official_cli": str(OFFICIAL.resolve()),
            "configuration": str(configuration),
            "configuration_seed": str(options.config_seed.resolve())
            if options.config_seed
            else None,
            "input_source": str(options.spec.resolve())
            if options.spec
            else "predeclared SPEC in this runner",
            "method_sha256": sha(__file__),
            "readers": {p.name: sha(p) for p in methods.glob("*.py")},
            "no_historical_delivery_dependency": True,
        },
    )
    write(
        root / "frozen-intent.json",
        {
            "nets": {k: sorted(v) for k, v in INTENT.items()},
            "parts": SPEC["parts"],
            "physical_pad_mapping": {
                ref: [
                    {"number": "1", "logical_pin": "1", "net": "VIN", "occurrences": 1},
                    {"number": "2", "logical_pin": "2", "net": "GND", "occurrences": 1},
                ]
                for ref in ("C1", "J1", "R1")
            },
            "edit": {
                "C1.Value": {
                    "before": "100nF",
                    "after": "220nF",
                    "basis": "Explicit software test configuration; no performance claim",
                }
            },
            "placement": {
                "keep": ["J1"],
                "clearance_mm": 1,
                "rotation_deg": 0,
                "layer": "F.Cu",
                "pose_policy": "autoplace translations; retain identities and orientation",
                "units": "native nanometres; 1 nm numeric tolerance",
            },
            "ground": {
                "net": "GND",
                "layers": ["F.Cu", "B.Cu"],
                "boundary": "frozen board Edge.Cuts inset 0.5 mm",
                "connect": "thermal",
                "clearance_mm": 0.2,
                "minimum_width_mm": 0.25,
                "thermal_gap_mm": 0.5,
                "thermal_spoke_mm": 0.5,
                "via_diameter_mm": 0.6,
                "via_drill_mm": 0.3,
                "keepout": "No keepout exists in this input; keepout protection tested separately",
            },
            "silk": {
                "clearance_mm": 0.15,
                "basis": (
                    "Explicit test configuration; native rectangular "
                    "font/mask bounds plus official DRC and render"
                ),
                "font_and_rotation": "preserved from installed libraries",
            },
        },
    )
    scope = root / "scope.json"
    write(
        scope,
        {
            "scope_id": root.name,
            "tool": "kicad-cli",
            "granted_by": "User authorizes owned engineering acceptance",
            "granted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "expires_at": None,
            "effects": ["observe", "change", "persist", "artifact"],
            "dangerous": False,
            "roots": [str(root)],
            "outputs": [str(root)],
            "notes": "Owned tests only; originals, user instances and licenses unchanged",
        },
    )
    env = dict(
        os.environ,
        KICAD_CLI_ROOT=str(native_root.resolve()),
        KICAD_CLI_PYTHON=str(SDK),
        KICAD_CLI_OFFICIAL=str(OFFICIAL),
        KICAD_CLI_SCOPE_FILE=str(scope),
        KICAD_CLI_STATE_DIR=str(root / "state"),
        KICAD_CLI_DRIVER_SDK_PATH=str(REPO / "kicad_cli/_driver_sdk"),
        KICAD_CLI_IPC_CLIENT_PATH=str(REPO / ".vendor"),
        KICAD_CONFIG_HOME=str(configuration),
        PYTHONIOENCODING="utf-8",
        PYTHONDONTWRITEBYTECODE="1",
    )
    env.pop("KICAD_CLI_TRACE", None)
    probe = subprocess.run(
        [str(SDK), "-I", "-B", str(methods / "native_sdk_reader.py")],
        env=env,
        cwd=root,
        capture_output=True,
        timeout=60,
    )
    if probe.returncode != 0:
        parser.error(
            "Native SDK reader dependency check failed: "
            + probe.stderr.decode("utf-8", "replace")[-900:]
        )
    identity = json.loads(probe.stdout)
    major = identity["application_version"].split(".")[0]
    for suffix, folder in (("FOOTPRINT_DIR", "footprints"), ("SYMBOL_DIR", "symbols")):
        library = native_root / "share/kicad" / folder
        if library.is_dir():
            env[f"KICAD{major}_{suffix}"] = str(library.resolve())
    write(root / "native-dependencies.json", identity)
    cli = (
        [str(options.binary.resolve())]
        if options.binary
        else [sys.executable, "-B", "-m", "kicad_cli.main"]
    )
    events = []

    def call(name, args, *, program=None, json_output=True):
        process = subprocess.run(
            (program or cli) + args,
            cwd=REPO,
            env=env,
            capture_output=True,
            timeout=1900,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        (root / (name + ".stdout")).write_bytes(process.stdout)
        (root / (name + ".stderr")).write_bytes(process.stderr)
        value = json.loads(process.stdout) if json_output else None
        events.append(
            {
                "name": name,
                "argv": args,
                "exit": process.returncode,
                "stdout_sha256": sha(root / (name + ".stdout")),
                "stderr_sha256": sha(root / (name + ".stderr")),
            }
        )
        write(root / "events.json", events)
        assert process.returncode == 0 and (not json_output or value.get("ok", True)), (
            name,
            value,
            process.stderr.decode("utf-8", "replace")[-800:],
        )
        return value

    def mutate(name, args):
        preview = call(name + "-preview", args + ["--dry-run", "--compact"])
        assert preview["data"]["preview"]["can_apply"], name
        return call(
            name + "-receipt",
            args
            + [
                "--confirm",
                preview["data"]["confirm_token"],
                "--idempotency-key",
                name,
                "--compact",
            ],
        )["data"]["receipt"]

    def official(name, args):
        return call(name, args, program=[str(OFFICIAL)], json_output=False)

    def native(name, reader="native_sdk_reader.py", path=None):
        return call(
            name, ["-I", "-B", str(methods / reader), str(path or board)], program=[str(SDK)]
        )

    for name in ("reference", "context", "doctor", "changelog"):
        call(name, [name, "--compact"])
    mutate("create", ["sch", "create", "--spec", str(spec), "--out", str(project)])
    schematic = project / "decoupling.kicad_sch"
    netlist = schematic.with_suffix(".net")
    board = schematic.with_suffix(".kicad_pcb")
    official(
        "initial-netlist",
        [
            "sch",
            "export",
            "netlist",
            "--format",
            "kicadxml",
            "--output",
            str(root / "initial.xml"),
            str(schematic),
        ],
    )
    assert graph(root / "initial.xml") == INTENT
    mutate(
        "from-netlist", ["board", "from-netlist", "--netlist", str(netlist), "--out", str(board)]
    )
    before_place = native("before-place")
    frozen_intent = json.loads((root / "frozen-intent.json").read_bytes())
    pcb_created = pcb_intent(before_place, frozen_intent)
    write(root / "pcb-created-intent.json", pcb_created)
    assert pcb_created["status"] == "passed", pcb_created
    place_receipt = mutate(
        "place",
        [
            "board",
            "footprint",
            "place",
            "--board",
            str(board),
            "--keep",
            "J1",
            "--clearance",
            "1",
            "--iterations",
            "100",
        ],
    )
    placed = native("placed")
    old, new = ({fp["ref"]: fp for fp in r["footprints"]} for r in (before_place, placed))
    assert old["J1"] == new["J1"]
    assert place_receipt["extensions"]["task"]["moved"] > 0
    for ref in old:
        assert (
            old[ref]["uuid"],
            old[ref]["footprint"],
            old[ref]["rotation_deg"],
            old[ref]["layer"],
        ) == (new[ref]["uuid"], new[ref]["footprint"], new[ref]["rotation_deg"], new[ref]["layer"])
        assert sorted((p["number"], p["net"]) for p in old[ref]["pads"]) == sorted(
            (p["number"], p["net"]) for p in new[ref]["pads"]
        )
    mutate("initial-identities", ["project", "links", "repair", "--board", str(board)])
    # Isolated deliberate identity damage. The requested repair may add only R1.path.
    text = board.read_text(encoding="utf-8")
    blocks = list(re.finditer(r"(?ms)^\t\(footprint .*?^\t\)", text))
    target = next(b for b in blocks if '(property "Reference" "R1"' in b[0])
    damaged, count = re.subn(r'(?m)^\t\t\(path "[^"]+"\)\r?\n', "", target[0], count=1)
    assert count == 1
    board.write_bytes((text[: target.start()] + damaged + text[target.end() :]).encode())
    shutil.copyfile(board, root / "identity-error-input.kicad_pcb")
    repaired = mutate(
        "identity-repair", ["project", "links", "repair", "--board", str(board), "--refs", "R1"]
    )
    assert repaired["extensions"]["task"]["written"] == ["R1"]
    change = project / "edit.json"
    write(change, {"changes": [{"op": "set", "ref": "C1", "value": "220nF"}]})
    mutate("edit", ["sch", "edit", "--schematic", str(schematic), "--changes", str(change)])
    pre_sync = native("pre-sync")
    mutate("sync", ["project", "sync", "--board", str(board), "--schematic", str(schematic)])
    post_sync = native("post-sync")
    pcb_synced = pcb_intent(post_sync, frozen_intent, edited=True)
    write(root / "pcb-synced-intent.json", pcb_synced)
    assert pcb_synced["status"] == "passed", pcb_synced
    assert [
        (p["ref"], p["uuid"], p["position_nm"], p["rotation_deg"], p["layer"])
        for p in pre_sync["footprints"]
    ] == [
        (p["ref"], p["uuid"], p["position_nm"], p["rotation_deg"], p["layer"])
        for p in post_sync["footprints"]
    ]
    assert next(p for p in post_sync["footprints"] if p["ref"] == "C1")["value"] == "220nF"
    official(
        "edited-netlist",
        [
            "sch",
            "export",
            "netlist",
            "--format",
            "kicadxml",
            "--output",
            str(root / "edited.xml"),
            str(schematic),
        ],
    )
    assert graph(root / "edited.xml") == INTENT
    mutate(
        "vin-route",
        ["board", "route", "repair", "--board", str(board), "--nets", "VIN", "--neck", "0.2"],
    )
    for layer in ("F.Cu", "B.Cu"):
        mutate(
            "ground-" + layer,
            [
                "board",
                "zone",
                "create",
                "--board",
                str(board),
                "--net",
                "GND",
                "--layer",
                layer,
                "--clearance",
                "0.2",
                "--connect",
                "thermal",
            ],
        )
    stitch = mutate("stitch", ["board", "zone", "stitch", "--board", str(board), "--net", "GND"])
    assert stitch["extensions"]["task"]["connections"]["new_vias"]
    silk = mutate(
        "silk", ["board", "silkscreen", "place", "--board", str(board), "--clearance", "0.15"]
    )
    assert all(v["clear"] and v["inside_edge"] for v in silk["extensions"]["task"]["checks"])
    final = native("final-sdk", "native_engineering_reader.py")
    repeated = mutate(
        "refill-repeat", ["board", "zone", "stitch", "--board", str(board), "--net", "GND"]
    )
    assert repeated["extensions"]["task"]["connections"]["new_vias"] == []
    reopened = native("reopened-sdk", "native_engineering_reader.py")
    final_pcb_intent = pcb_intent(reopened, frozen_intent, edited=True)
    final_thermal_intent = thermal_intent(reopened, frozen_intent)
    write(root / "pcb-final-intent.json", final_pcb_intent)
    write(root / "thermal-final-intent.json", final_thermal_intent)
    assert final_pcb_intent["status"] == final_thermal_intent["status"] == "passed"
    pcb_only_negatives = []
    for kind in (
        "pcb-wrong-net",
        "pcb-wrong-pad",
        "pcb-wrong-footprint",
        "thermal-solid",
        "thermal-layer",
        "thermal-net",
        "thermal-gap",
        "thermal-spoke",
    ):
        directory = root / "pcb-only-negatives" / kind
        directory.mkdir(parents=True)
        damaged = directory / board.name
        shutil.copyfile(board, damaged)
        original_sch = sha(schematic)
        call(
            kind + "-fault",
            ["-I", "-B", str(methods / "native_intent_faults.py"), str(damaged), kind],
            program=[str(SDK)],
            json_output=False,
        )
        read = native(kind + "-read", "native_engineering_reader.py", damaged)
        comparison = (
            pcb_intent(read, frozen_intent, edited=True)
            if kind.startswith("pcb-")
            else thermal_intent(read, frozen_intent)
        )
        write(directory / "comparison.json", comparison)
        assert comparison["status"] == "failed", (kind, comparison)
        assert sha(schematic) == original_sch
        pcb_only_negatives.append(
            {
                "kind": kind,
                "expected": "reject",
                "observed": "rejected",
                "schematic_unchanged": True,
                "saved_board_sha256": sha(damaged),
            }
        )
    write(root / "pcb-zone-negative-results.json", pcb_only_negatives)
    contact_case = root / "pcb-only-negatives/stitch-single-layer-contact"
    contact_case.mkdir()
    for source in project.iterdir():
        if source.is_file() and source.suffix in {
            ".kicad_pcb",
            ".kicad_pro",
            ".kicad_sch",
            ".kicad_dru",
        }:
            shutil.copyfile(source, contact_case / source.name)
    for name in ("fp-lib-table", "sym-lib-table"):
        if (project / name).is_file():
            shutil.copyfile(project / name, contact_case / name)
    damaged = contact_case / board.name
    call(
        "single-layer-contact-fault",
        [
            "-I",
            "-B",
            str(methods / "native_intent_faults.py"),
            str(damaged),
            "stitch-single-layer-contact",
        ],
        program=[str(SDK)],
        json_output=False,
    )
    contact_read = native("single-layer-contact-read", "native_engineering_reader.py", damaged)
    comparison = thermal_intent(contact_read, frozen_intent)
    write(contact_case / "comparison.json", comparison)
    assert comparison["status"] == "failed", comparison
    gnd = [v for v in contact_read["native_connectivity"]["vias"] if v["net"] == "GND"]
    assert all(
        {"F.Cu", "B.Cu"} <= {layer for z in v["component_zones"] for layer in z["layers"]}
        for v in gnd
    ), "Negative must retain the broad component containing both layers"
    assert all(
        {r["layer"] for r in v["direct_zone_contacts"] if r["direct_copper_intersection"]}
        == {"F.Cu"}
        for v in gnd
    ), "Only B contact is removed"
    official(
        "single-layer-contact-drc",
        [
            "pcb",
            "drc",
            "--format",
            "json",
            "--severity-all",
            "--output",
            str(contact_case / "drc.json"),
            str(damaged),
        ],
    )
    contact_drc = json.loads((contact_case / "drc.json").read_bytes())
    write(
        root / "single-layer-contact-negative.json",
        {
            "expected": "Independent per-layer criterion rejects missing B.Cu direct contact",
            "independent_observed": comparison,
            "native_DRC": {
                "violations": contact_drc["violations"],
                "unconnected_items": contact_drc["unconnected_items"],
                "ignored_checks": contact_drc.get("ignored_checks", []),
                "report_sha256": sha(contact_case / "drc.json"),
            },
            "broad_component_still_both_layers": True,
            "schematic_unchanged": sha(schematic) == original_sch,
            "interpretation": (
                "Intentional owned geometry modification; native findings are recorded separately. "
                "No current-product defect is assumed."
            ),
        },
    )
    assert (
        final["zone_geometry"] == reopened["zone_geometry"]
        and final["track_geometry"] == reopened["track_geometry"]
        and final["identity_text"] == reopened["identity_text"]
    )
    call(
        "reference-plane",
        [
            "board",
            "analyze",
            "reference-plane",
            "--board",
            str(board),
            "--step",
            "0.25",
            "--compact",
        ],
    )
    ampacity = call(
        "ampacity",
        [
            "board",
            "analyze",
            "ampacity",
            "--board",
            str(board),
            "--oz",
            "1",
            "--dt",
            "10",
            "--compact",
        ],
    )
    assert ampacity["data"]["model"]["name"] == "IPC-2221 empirical estimate"
    official(
        "erc",
        ["sch", "erc", "--format", "json", "--output", str(root / "erc.json"), str(schematic)],
    )
    official(
        "drc", ["pcb", "drc", "--format", "json", "--output", str(root / "drc.json"), str(board)]
    )
    erc, drc = (json.loads((root / n).read_bytes()) for n in ("erc.json", "drc.json"))
    assert not [v for s in erc["sheets"] for v in s["violations"]]
    assert not drc["violations"] and not drc["unconnected_items"]
    freeze = {
        p.name: sha(p)
        for p in project.iterdir()
        if p.suffix in {".kicad_sch", ".kicad_pro", ".kicad_pcb", ".kicad_dru"}
    }
    for label, args in (
        ("gerber", ["fab", "gerber"]),
        ("drill", ["fab", "drill"]),
        ("silk-svg", ["board", "plot", "--file-format", "svg", "--layers", "F.SilkS,Edge.Cuts"]),
    ):
        mutate(label, args + ["--board", str(board), "--out", str(root / label)])
    official("bom", ["sch", "export", "bom", "--output", str(root / "bom.csv"), str(schematic)])
    official(
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
            str(root / "cpl.csv"),
            str(board),
        ],
    )
    assert freeze == {p.name: sha(p) for p in project.iterdir() if p.name in freeze}
    negative_results = []
    for name, ref, pin, wrong in (
        ("disconnected", "C1", "2", None),
        ("wrong-net", "J1", "2", "VIN"),
        ("same-net-R1", "R1", "2", "VIN"),
    ):
        case = root / name
        shutil.copytree(project, case)
        bad_schematic = case / schematic.name
        changes = [{"op": "disconnect", "ref": ref, "pin": pin}]
        if wrong:
            changes.append({"op": "connect", "ref": ref, "pin": pin, "net": wrong})
        change = case / "negative.json"
        write(change, {"changes": changes})
        mutate(name, ["sch", "edit", "--schematic", str(bad_schematic), "--changes", str(change)])
        xml_path = case / "observed.xml"
        official(
            name + "-xml",
            [
                "sch",
                "export",
                "netlist",
                "--format",
                "kicadxml",
                "--output",
                str(xml_path),
                str(bad_schematic),
            ],
        )
        actual = graph(xml_path)
        assert actual != INTENT
        expected_other = {k: v - {ref + "." + pin} for k, v in INTENT.items()}
        observed_other = {k: v - {ref + "." + pin} for k, v in actual.items() if k in INTENT}
        assert observed_other == expected_other, "Unrelated electrical pins changed"
        official(
            name + "-erc",
            [
                "sch",
                "erc",
                "--format",
                "json",
                "--output",
                str(case / "erc.json"),
                str(bad_schematic),
            ],
        )
        if wrong:
            repair = [
                {"op": "disconnect", "ref": ref, "pin": pin},
                {"op": "connect", "ref": ref, "pin": pin, "net": "GND"},
            ]
        else:
            repair = [{"op": "connect", "ref": ref, "pin": pin, "net": "GND"}]
        write(case / "repair.json", {"changes": repair})
        mutate(
            name + "-repair",
            [
                "sch",
                "edit",
                "--schematic",
                str(bad_schematic),
                "--changes",
                str(case / "repair.json"),
            ],
        )
        official(
            name + "-repaired-xml",
            [
                "sch",
                "export",
                "netlist",
                "--format",
                "kicadxml",
                "--output",
                str(case / "repaired.xml"),
                str(bad_schematic),
            ],
        )
        assert graph(case / "repaired.xml") == INTENT
        assert sha(case / board.name) == freeze[board.name]
        negative_results.append(
            {
                "case": name,
                "expected": {k: sorted(v) for k, v in INTENT.items()},
                "observed_negative": {k: sorted(v) for k, v in actual.items()},
                "repaired": True,
                "unrelated_pins_preserved": True,
                "board_unchanged": True,
            }
        )
    write(root / "negative-results.json", negative_results)
    bom = list(csv.DictReader((root / "bom.csv").open(encoding="utf-8-sig")))
    cpl = list(csv.DictReader((root / "cpl.csv").open(encoding="utf-8-sig")))
    write(
        root / "summary.json",
        {
            "status": "passed",
            "input_spec_sha256": sha(spec),
            "frozen_input_intent_sha256": sha(root / "frozen-intent.json"),
            "binary": {"path": str(options.binary.resolve()), "sha256": sha(options.binary)}
            if options.binary
            else None,
            "snapshot": freeze,
            "native_version": final["application_version"],
            "stitches": stitch["extensions"]["task"]["connections"],
            "silk": silk["extensions"]["task"],
            "erc": {"violations": 0, "ignored": erc.get("ignored_checks", [])},
            "drc": {"violations": 0, "unconnected": 0, "ignored": drc.get("ignored_checks", [])},
            "manufacturing": {
                "bom_rows": bom,
                "cpl_rows": cpl,
                "snapshot_unchanged": True,
                "geometry_acceptance": (
                    "Separate selected changed-fill, via drill and silk "
                    "output consumer required; full artwork not certified"
                ),
            },
            "coverage": {
                "intent_nets": 2,
                "logical_pins": 6,
                "footprints": 3,
                "native_reopen": "two fresh native processes",
                "not_checked": [
                    "all KiCad capabilities",
                    "full manufacturing geometry",
                    "supplier approval",
                    "load capacity",
                    "cross-platform",
                ],
            },
        },
    )
    print(json.dumps({"status": "passed", "output": str(root)}))


if __name__ == "__main__":
    main()
