"""One bounded before/after native acceptance of an externally performed move.

stage-inputs -> prepare -> freeze -> before-consumers -> after-consumers.
The original reviewed plan is consumed unchanged at both formal stages. This
controller never runs the product writer, opens operation state, or retries.
Raw records and absolute locators belong only in a new private output directory.
"""

import argparse
import copy
import json
import shutil
import sys
from pathlib import Path

from bench.move_engineering_checks import (
    INTENT,
    check_baseline_board,
    check_native_baseline,
    check_receipts,
    compare_board,
    compare_drc,
    compare_native,
)
from bench.saved_scene_acceptance import (
    PIN,
    PREPARATION_CONFIG_ADDITIONS,
    deltas,
    dependency_identity,
    dependency_policy,
    environment_identity,
    now,
    put,
    read,
    record_call,
    tree,
)
from bench.saved_scene_acceptance import (
    method_identity as shared_method_identity,
)
from bench.saved_scene_checks import sha

REQUIRED = {
    "schema_version",
    "output",
    "repository",
    "native_home",
    "host_python",
    "binary",
    "sdk",
    "spec",
    "dependency_pin_manifest",
    "source_binding",
    "source_evidence",
    "source",
    "source_files",
    "engineer_project",
    "engineer_call_files",
    "expected_binary_sha256",
    "expected_native_version",
    "expected_sdk_commit",
    "timeout_seconds",
    "library_paths",
}
EXPECTED_FILES = {
    "edit.kicad_pcb",
    "edit.kicad_sch",
    "edit_child.kicad_sch",
    "edit.kicad_pro",
    "fp-lib-table",
    "fixture.pretty/R_0603.kicad_mod",
    "fixture.pretty/R_0805.kicad_mod",
    "fixture.pretty/SO8.kicad_mod",
}
PROJECTS = ("preparation-project", "before", "after")


def load_config(path):
    config = read(path)
    if set(config) != REQUIRED or config["schema_version"] != 1:
        raise ValueError("Explicit move consumer config has unknown/missing keys")
    if set(config["source_files"]) != EXPECTED_FILES or set(config["engineer_call_files"]) != {
        "preview",
        "confirm",
        "readback",
    }:
        raise ValueError(
            "Exactly eight engineering files and three public EXE evidence roles required"
        )
    if config["expected_sdk_commit"] != PIN or config["expected_native_version"] != "10.0.6":
        raise ValueError("Existing native and SDK versions are fixed")
    if (
        config["expected_binary_sha256"]
        != "21c46f431918e3b48263f7e49e7177f042eb1b300dba6cddfc062dc0d87a1225"
    ):
        raise ValueError("Existing tested EXE identity is fixed")
    for key in (
        "output",
        "repository",
        "native_home",
        "host_python",
        "binary",
        "sdk",
        "spec",
        "dependency_pin_manifest",
        "source",
        "engineer_project",
    ):
        if not Path(config[key]).is_absolute():
            raise ValueError("Config path must be absolute: " + key)
    if any(not Path(path).is_absolute() for path in config["engineer_call_files"].values()):
        raise ValueError("Engineer public output paths must be absolute")
    if not 0 < config["timeout_seconds"] <= 300:
        raise ValueError("Bounded timeout must be within 1..300 seconds")
    if (
        config["source_files"]["edit.kicad_pcb"]
        != "59b5c3a7f5569031c2c098c01cdee5c5be2c71aed77d33eb5992126ac7b5cb1f"
    ):
        raise ValueError("Saved baseline board identity differs")
    return config


def method_identity(config):
    result = shared_method_identity(config)
    repo = Path(config["repository"])
    for name in ("move_engineering_acceptance.py", "move_engineering_checks.py"):
        path = repo / "bench" / name
        if not path.is_file() or path.is_symlink():
            raise ValueError("Move method must be a regular bound source file")
        result[str(path.resolve())] = sha(path)
    return result


def config_values(root):
    values = {}
    for name in tree(root / "configuration"):
        if name not in PREPARATION_CONFIG_ADDITIONS:
            raise ValueError("Undeclared configuration file: " + name)
        value = read(root / "configuration" / name)
        if not isinstance(value, dict):
            raise ValueError("Preference must be a regular JSON object")
        values[name] = value
    return values


def config_identity(root):
    result = {}
    allowed = {str((root / name).resolve()) for name in PROJECTS}
    for name, digest in tree(root / "configuration").items():
        if name == "10.0/kicad_common.json":
            value = read(root / "configuration" / name)
            working = value.get("system", {}).get("working_dir")
            if not isinstance(working, str) or str(Path(working).resolve()) not in allowed:
                raise ValueError(
                    "working_dir is outside the predeclared three consumer directories"
                )
            value["system"]["working_dir"] = "<predeclared-consumer-working-directory>"
            result[name] = {"canonical_json": value}
        else:
            result[name] = {"sha256": digest}
    return result


def validate_config_transition(root, before):
    after = config_values(root)
    if set(before) - set(after):
        raise ValueError("Initialization removed an existing preference")
    for name, old in before.items():
        new = copy.deepcopy(after[name])
        if new == old:
            continue
        if name != "10.0/kicad_common.json":
            raise ValueError("Initialization changed a non-metadata preference")
        old_working = old.get("system", {}).get("working_dir")
        if not isinstance(old_working, str):
            raise ValueError("Existing working_dir metadata is missing")
        config_identity(root)
        new["system"]["working_dir"] = old_working
        if new != old:
            raise ValueError("Initialization changed common preferences outside working_dir")
    return after


def identity(config):
    return {
        "config": config,
        "methods": method_identity(config),
        "dependencies": dependency_identity(config),
        "environment": environment_identity(config),
        "original_source_inventory": tree(config["source"]),
    }


def calls(config, phase):
    root = Path(config["output"])
    preparation = phase == "prepare"
    project = root / ("preparation-project" if preparation else phase)
    output = root / ("preparation-output" if preparation else phase + "-output")
    native = Path(config["native_home"]) / "bin"
    board = project / "edit.kicad_pcb"
    rows = [
        (
            "sdk",
            [
                native / "python.exe",
                "-B",
                "-S",
                Path(config["repository"]) / "bench/saved_scene_native_read.py",
                board,
            ],
            ["owned-call/stdout"],
        ),
        (
            "drc",
            [
                native / "kicad-cli.exe",
                "pcb",
                "drc",
                "--schematic-parity",
                "--severity-all",
                "--format",
                "json",
                "--output",
                output / "drc.json",
                board,
            ],
            [str(output / "drc.json")],
        ),
    ]
    for side, layers in (("front", "F.Cu,F.SilkS,Edge.Cuts"), ("back", "B.Cu,B.SilkS,Edge.Cuts")):
        rows.append(
            (
                side + "-svg",
                [
                    native / "kicad-cli.exe",
                    "pcb",
                    "export",
                    "svg",
                    "--layers",
                    layers,
                    "--mode-single",
                    "--fit-page-to-board",
                    "--exclude-drawing-sheet",
                    "--output",
                    output / (side + ".svg"),
                    board,
                ],
                [str(output / (side + ".svg"))],
            )
        )
    return [
        {
            "id": phase + "-" + name,
            "kind": name,
            "phase": phase,
            "argv": list(map(str, argv)),
            "cwd": str(project),
            "timeout_seconds": config["timeout_seconds"],
            "allowed_exit_codes": [0],
            "expected_outputs": expected,
        }
        for name, argv, expected in rows
    ]


def images(config, phase):
    root = Path(config["output"])
    return [
        {
            "id": phase + "-" + side,
            "phase": phase,
            "page_identity": "PCB-" + side,
            "source": str(root / (phase + "-output") / (side + ".svg")),
            "scale": "8",
        }
        for side in ("front", "back")
    ]


def output_names():
    return ["back.png", "back.svg", "drc.json", "front.png", "front.svg"]


def copy_files(source, target, names):
    target.mkdir(parents=True, exist_ok=False)
    for name in names:
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(source) / name, path)


def stage_inputs(config):
    root = Path(config["output"])
    if root.exists():
        raise ValueError("A new consumer cycle output directory is required")
    source = Path(config["source"])
    if any(sha(source / name) != digest for name, digest in config["source_files"].items()):
        raise ValueError("Original eight-file source binding differs")
    check_baseline_board(source / "edit.kicad_pcb")
    root.mkdir(parents=True)
    (root / "configuration").mkdir()
    for name in PROJECTS:
        copy_files(source, root / name, config["source_files"])
    (root / "preparation-output").mkdir()
    version = {
        "id": "prepare-version",
        "kind": "version",
        "argv": [str(Path(config["native_home"]) / "bin/kicad-cli.exe"), "--version"],
        "cwd": str(root / "preparation-project"),
        "timeout_seconds": config["timeout_seconds"],
        "allowed_exit_codes": [0],
        "expected_outputs": ["owned-call/stdout"],
    }
    value = {
        "schema_version": 1,
        "at": now(),
        "binding": identity(config),
        "engineering": {name: tree(root / name) for name in PROJECTS},
        "intent": INTENT,
        "preparation_calls": [version, *calls(config, "prepare")],
        "formal_calls": {phase: calls(config, phase) for phase in ("before", "after")},
        "images": {phase: images(config, phase) for phase in ("before", "after")},
        "metadata_policy": {
            "preparation_new_config_paths": sorted(PREPARATION_CONFIG_ADDITIONS),
            "only_mutable_formal_json_pointer": "10.0/kicad_common.json#/system/working_dir",
            "allowed_working_directories": [str((root / name).resolve()) for name in PROJECTS],
            "project_metadata": (
                "optional edit.kicad_prl: absent is explicitly frozen absent; if created as a "
                "regular non-link file during preparation, copy to consumer before/after and "
                "freeze its SHA; actual eight/nine-file consumer sets strict thereafter"
            ),
            "engineering_and_rules_and_libraries_and_ignores": "strict; no relaxation",
        },
        "dependency_policy": dependency_policy(config),
        "native_calls_started": 0,
    }
    put(root / "INPUT-FROZEN.json", value)
    return {"input_sha256": sha(root / "INPUT-FROZEN.json"), "native_calls_started": 0}


def stage_prepared_metadata(root):
    """Predeclared absent/present metadata alternatives, decided before freeze."""
    metadata = root / "preparation-project/edit.kicad_prl"
    if metadata.is_symlink() or (metadata.exists() and not metadata.is_file()):
        raise ValueError("Optional prepared project metadata must be a regular non-link file")
    present = metadata.is_file()
    if present:
        for name in ("before", "after"):
            target = root / name / metadata.name
            if target.exists():
                raise ValueError("Consumer metadata target already exists")
            shutil.copyfile(metadata, target)
    return {
        "path": "edit.kicad_prl",
        "present": present,
        "sha256": sha(metadata) if present else None,
        "origin": "preparation-project" if present else None,
        "consumer_file_count": 9 if present else 8,
        "formal_policy": "exact actual presence and bytes frozen; no formal additions",
    }


def metadata_identity(root):
    result = {}
    for name in PROJECTS:
        path = root / name / "edit.kicad_prl"
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError("Project metadata must remain absent or a regular non-link file")
        result[name] = {"present": path.is_file(), "sha256": sha(path) if path.is_file() else None}
    return result


def prepare(config, digest):
    root = Path(config["output"])
    if sha(root / "INPUT-FROZEN.json") != digest:
        raise ValueError("Preparation input digest differs")
    approved = read(root / "INPUT-FROZEN.json")
    if (
        identity(config) != approved["binding"]
        or approved["engineering"] != {name: tree(root / name) for name in PROJECTS}
        or tree(root / "configuration")
    ):
        raise ValueError("Pre-native preparation inputs/configuration drifted")
    put(root / "PREPARE-START.json", {"at": now(), "input_sha256": digest})
    directory = root / "preparation-calls"
    directory.mkdir()
    original = tree(root / "preparation-project")
    for row in approved["preparation_calls"]:
        if identity(config) != approved["binding"]:
            raise ValueError("Preparation pre-call method/dependency/source/environment drift")
        before, values = tree(root / "configuration"), config_values(root)
        try:
            record_call(config, row, directory / row["id"])
        finally:
            put(
                directory / (row["id"] + ".DELTAS.json"),
                {
                    "at": now(),
                    "configuration": deltas(before, tree(root / "configuration")),
                    "project": deltas(original, tree(root / "preparation-project")),
                    "configuration_values_before": values,
                    "configuration_values_after": config_values(root),
                },
            )
        validate_config_transition(root, values)
        actual = tree(root / "preparation-project")
        if any(actual.get(name) != value for name, value in original.items()) or set(actual) - set(
            original
        ) - {"edit.kicad_prl"}:
            raise ValueError("Preparation changed engineering or undeclared project metadata")
        if (
            row["kind"] == "version"
            and (directory / row["id"] / "stdout").read_text(encoding="utf-8").strip() != "10.0.6"
        ):
            raise ValueError("Official KiCad version differs")
    metadata = stage_prepared_metadata(root)
    result = {
        "schema_version": 1,
        "at": now(),
        "input_sha256": digest,
        "binding": identity(config),
        "configuration": tree(root / "configuration"),
        "configuration_canonical": config_identity(root),
        "engineering": {name: tree(root / name) for name in PROJECTS},
        "preparation_outputs": tree(root / "preparation-output"),
        "preparation_call_evidence": tree(directory),
        "explicit_consumer_metadata_addition": metadata,
        "formal_consumers_executed": 0,
    }
    put(root / "PREPARATION.json", result)
    return {"preparation_sha256": sha(root / "PREPARATION.json"), "formal_consumers_executed": 0}


def freeze(config, digest):
    root = Path(config["output"])
    if sha(root / "PREPARATION.json") != digest:
        raise ValueError("Preparation summary digest differs")
    prepared, initial = read(root / "PREPARATION.json"), read(root / "INPUT-FROZEN.json")
    if (
        sha(root / "INPUT-FROZEN.json") != prepared["input_sha256"]
        or identity(config) != initial["binding"]
        or prepared["binding"] != initial["binding"]
    ):
        raise ValueError("Prepared method/dependency/source/environment chain differs")
    if (
        prepared["configuration"] != tree(root / "configuration")
        or prepared["engineering"] != {name: tree(root / name) for name in PROJECTS}
        or prepared["preparation_outputs"] != tree(root / "preparation-output")
        or prepared["preparation_call_evidence"] != tree(root / "preparation-calls")
    ):
        raise ValueError("Prepared engineering/configuration/evidence drift before freeze")
    if tree(config["engineer_project"]) != config["source_files"]:
        raise ValueError("Independent engineer project must be exactly the original eight files")
    if any(Path(path).exists() for path in config["engineer_call_files"].values()):
        raise ValueError("Public engineer outputs must be new after the original formal freeze")
    plan = {
        "schema_version": 1,
        "at": now(),
        "binding": identity(config),
        "input_sha256": prepared["input_sha256"],
        "preparation_sha256": digest,
        "engineering": prepared["engineering"],
        "project_metadata": metadata_identity(root),
        "configuration": config_identity(root),
        "intent": INTENT,
        "metadata_policy": initial["metadata_policy"],
        "native_consumers": {phase: calls(config, phase) for phase in ("before", "after")},
        "images": {phase: images(config, phase) for phase in ("before", "after")},
        "complete_output_names": output_names(),
        "after_truth": (
            "original baseline plus unique predeclared R1 top-level at x/y change; "
            "not a post-write refreeze"
        ),
        "manual_views_completed": 0,
    }
    put(root / "FROZEN.json", plan)
    return {"plan_sha256": sha(root / "FROZEN.json"), "formal_consumers_executed": 0}


def verify_plan(config, plan, digest):
    root = Path(config["output"])
    if Path(plan).resolve() != (root / "FROZEN.json").resolve() or sha(plan) != digest:
        raise ValueError("Explicit original formal plan digest differs")
    approved = read(plan)
    if (
        approved["binding"] != identity(config)
        or approved["configuration"] != config_identity(root)
        or approved["intent"] != INTENT
        or approved["native_consumers"]
        != {phase: calls(config, phase) for phase in ("before", "after")}
        or approved["images"] != {phase: images(config, phase) for phase in ("before", "after")}
        or approved["complete_output_names"] != output_names()
    ):
        raise ValueError("Formal method/configuration/environment/dependency/command drift")
    if (
        sha(root / "INPUT-FROZEN.json") != approved["input_sha256"]
        or sha(root / "PREPARATION.json") != approved["preparation_sha256"]
    ):
        raise ValueError("Original preparation/freeze chain differs")
    prepared = read(root / "PREPARATION.json")
    if prepared["preparation_outputs"] != tree(root / "preparation-output") or prepared[
        "preparation_call_evidence"
    ] != tree(root / "preparation-calls"):
        raise ValueError("Retained preparation output/evidence drift")
    if (
        tree(root / "preparation-project") != approved["engineering"]["preparation-project"]
        or tree(root / "before") != approved["engineering"]["before"]
        or metadata_identity(root) != approved["project_metadata"]
    ):
        raise ValueError("Protected preparation/baseline consumer project drift")
    return approved


def after_engineering(config, approved):
    root, engineer = Path(config["output"]), Path(config["engineer_project"])
    actual = tree(engineer)
    original = config["source_files"]
    if set(actual) != set(original) or any(
        actual[name] != value for name, value in original.items() if name != "edit.kicad_pcb"
    ):
        raise ValueError("Independent engineer changed a protected non-board input or file set")
    structure = compare_board(root / "before/edit.kicad_pcb", engineer / "edit.kicad_pcb")
    receipts = check_receipts(
        config["engineer_call_files"],
        original["edit.kicad_pcb"],
        actual["edit.kicad_pcb"],
        engineer / "edit.kicad_pcb",
    )
    expected = dict(
        approved["engineering"]["after"], **{"edit.kicad_pcb": actual["edit.kicad_pcb"]}
    )
    return actual, expected, structure, receipts


def run_consumers(config, phase, plan, digest):
    root = Path(config["output"])
    approved = verify_plan(config, plan, digest)
    start = root / (phase.upper() + "-START.json")
    if start.exists():
        raise ValueError("An already started stage is retained; no retry/resume is supported")
    receipts, structure, before_result_digest = None, None, None
    if phase == "before":
        if (
            tree(config["engineer_project"]) != config["source_files"]
            or tree(root / "after") != approved["engineering"]["after"]
        ):
            raise ValueError("Before-write engineer/after-template input drift")
        if any(Path(path).exists() for path in config["engineer_call_files"].values()):
            raise ValueError("Before stage requires not-yet-executed engineer public outputs")
        expected_engineering = approved["engineering"]["before"]
    else:
        before_result = read(root / "BEFORE-RESULT.json")
        before_result_digest = sha(root / "BEFORE-RESULT.json")
        if (
            before_result["plan_sha256"] != digest
            or before_result["complete_output_tree"] != tree(root / "before-output")
            or before_result["call_evidence"] != tree(root / "before-calls")
        ):
            raise ValueError("Before formal output/evidence drift")
        actual, expected_engineering, structure, receipts = after_engineering(config, approved)
        if tree(root / "after") != approved["engineering"]["after"]:
            raise ValueError("After consumer template drift before accepting writer output")
        # Copy only after the independently parsed output satisfies the intent
        # already present in FROZEN.json. This hash is observed output evidence.
        shutil.copyfile(
            Path(config["engineer_project"]) / "edit.kicad_pcb", root / "after/edit.kicad_pcb"
        )
        put(
            root / "AFTER-STAGED.json",
            {
                "at": now(),
                "plan_sha256": digest,
                "observed_engineer_files": actual,
                "structure": structure,
                "public_output_chain": receipts,
                "truth": "pre-write original plan intent; no new freezing",
            },
        )
    put(start, {"at": now(), "original_plan_sha256": digest})
    output, evidence = root / (phase + "-output"), root / (phase + "-calls")
    output.mkdir()
    evidence.mkdir()

    def verify():
        verify_plan(config, plan, digest)
        if tree(root / phase) != expected_engineering:
            raise ValueError("Formal consumer engineering input drift")
        if phase == "after":
            if (
                sha(root / "BEFORE-RESULT.json") != before_result_digest
                or before_result["complete_output_tree"] != tree(root / "before-output")
                or before_result["call_evidence"] != tree(root / "before-calls")
            ):
                raise ValueError("Before formal output/evidence drift during after consumers")
            current, expected, _, current_receipts = after_engineering(config, approved)
            if expected != expected_engineering or current_receipts != receipts:
                raise ValueError(
                    "Engineer saved files/public output chain changed during consumers"
                )
        elif tree(config["engineer_project"]) != config["source_files"]:
            raise ValueError("Engineer project changed during before consumers")

    for row in approved["native_consumers"][phase]:
        verify()
        before, values = tree(root / "configuration"), config_values(root)
        try:
            record_call(config, row, evidence / row["id"])
        finally:
            put(
                evidence / (row["id"] + ".CONFIG-DELTAS.json"),
                {
                    "at": now(),
                    "deltas": deltas(before, tree(root / "configuration")),
                    "configuration_values_before": values,
                    "configuration_values_after": config_values(root),
                },
            )
        for name in row["expected_outputs"]:
            path = evidence / row["id"] / "stdout" if name == "owned-call/stdout" else Path(name)
            if not path.is_file():
                raise ValueError("Declared native consumer output is absent")
        # A completed process is not a complete observation. Reject malformed
        # or incomplete critical readback before starting any later consumer.
        if row["kind"] == "sdk":
            observed = read(evidence / row["id"] / "stdout")
            if phase == "before":
                check_native_baseline(observed, root / "before/edit.kicad_pcb")
            else:
                compare_native(
                    read(root / "before-calls/before-sdk/stdout"),
                    observed,
                    root / "before/edit.kicad_pcb",
                    root / "after/edit.kicad_pcb",
                )
        elif row["kind"] == "drc":
            observed = read(output / "drc.json")
            compare_drc(observed, observed) if phase == "before" else compare_drc(
                read(root / "before-output/drc.json"), observed
            )
        verify()
    rendered = []
    for image in approved["images"][phase]:
        verify()
        source = Path(image["source"])
        target = source.with_suffix(".png")
        row = {
            "id": image["id"] + "-render",
            "kind": "render",
            "argv": [
                config["host_python"],
                "-B",
                "-S",
                str(Path(config["repository"]) / "bench/saved_scene_render.py"),
                str(source),
                str(target),
                image["scale"],
            ],
            "cwd": str(root / phase),
            "timeout_seconds": config["timeout_seconds"],
            "allowed_exit_codes": [0],
        }
        record_call(config, row, evidence / row["id"])
        result = read(evidence / row["id"] / "stdout")
        if (
            result["source_sha256"] != sha(source)
            or result["target_sha256"] != sha(target)
            or result["coverage"] != "full SVG viewport; no cropping"
        ):
            raise ValueError("Complete image source/output/coverage binding differs")
        rendered.append(
            {
                **image,
                "target": str(target),
                "source_sha256": sha(source),
                "target_sha256": sha(target),
                "pixel_size": result["pixel_size"],
                "coverage": result["coverage"],
            }
        )
        verify()
    if sorted(tree(output)) != approved["complete_output_names"]:
        raise ValueError("Complete formal output set differs")
    native = read(evidence / (phase + "-sdk") / "stdout")
    if (
        native["input_sha256"] != sha(root / phase / "edit.kicad_pcb")
        or not native["input_unchanged"]
    ):
        raise ValueError("Native input byte identity differs")
    drc = read(output / "drc.json")
    if phase == "before":
        check_baseline_board(root / "before/edit.kicad_pcb")
        compare_drc(drc, drc)
        comparison = {
            "baseline": "exact saved identity, native readback and DRC preconditions retained"
        }
    else:
        comparison = {
            "complete_board": structure,
            "native": compare_native(
                read(root / "before-calls/before-sdk/stdout"),
                native,
                root / "before/edit.kicad_pcb",
                root / "after/edit.kicad_pcb",
            ),
            "drc": compare_drc(read(root / "before-output/drc.json"), drc),
            "public_output_chain": receipts,
        }
    value = {
        "schema_version": 1,
        "at": now(),
        "phase": phase,
        "status": "consumer_checks_completed_manual_pending",
        "plan_sha256": digest,
        "comparisons": comparison,
        "images": rendered,
        "manual_views_completed": 0,
        "complete_output_tree": tree(output),
        "call_evidence": tree(evidence),
        "engineering": tree(root / phase),
        "configuration": tree(root / "configuration"),
        "native_consumers": 4,
        "host_renderers": 2,
        "preparation_counted_as_formal": 0,
        "engineering_writer_calls_by_this_controller": 0,
    }
    put(root / (phase.upper() + "-RESULT.json"), value)
    return {
        "completed": True,
        "phase": phase,
        "native_consumers": 4,
        "host_renderers": 2,
        "manual_images_pending": 2,
        "result_sha256": sha(root / (phase.upper() + "-RESULT.json")),
    }


def main():
    if not sys.flags.no_site or not sys.flags.dont_write_bytecode:
        raise SystemExit("move_engineering_acceptance requires Python -B -S")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage",
        choices=("stage-inputs", "prepare", "freeze", "before-consumers", "after-consumers"),
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--input-sha256")
    parser.add_argument("--preparation-sha256")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--plan-sha256")
    args = parser.parse_args()
    config = load_config(args.config)
    root = Path(config["output"])
    try:
        if args.stage == "stage-inputs":
            result = stage_inputs(config)
        elif args.stage == "prepare":
            if not args.input_sha256:
                parser.error("prepare requires --input-sha256")
            result = prepare(config, args.input_sha256)
        elif args.stage == "freeze":
            if not args.preparation_sha256:
                parser.error("freeze requires --preparation-sha256")
            result = freeze(config, args.preparation_sha256)
        else:
            if not args.plan or not args.plan_sha256:
                parser.error("formal stages require the original --plan and --plan-sha256")
            result = run_consumers(
                config, args.stage.removesuffix("-consumers"), args.plan, args.plan_sha256
            )
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        stop = root / (args.stage.upper() + "-STOP.json")
        if root.is_dir() and not stop.exists():
            put(
                stop,
                {
                    "at": now(),
                    "stage": args.stage,
                    "failure": type(exc).__name__ + ": " + str(exc),
                    "action": "preserve cycle; do not retry or replay ambiguous writer operations",
                    "configuration_at_stop": tree(root / "configuration"),
                    "engineering_at_stop": {name: tree(root / name) for name in PROJECTS},
                },
            )
        raise


if __name__ == "__main__":
    main()
