"""Bounded read/copy/native-check/render/visual binding for the supplied hierarchy.

No product write implementation is imported or invoked. Native initialization
is limited to owned check copies; public CLI operations remain the executor's job.
"""

import argparse
import hashlib
import json
import os
import secrets
import shutil
import sys
from pathlib import Path

from hierarchy_frozen import read_verified_baseline

IMAGES = ("edit.png", "edit-CHILD_A.png", "edit-CHILD_B.png", "front.png", "back.png")
PRIMARY = {"edit.kicad_sch", "edit_child.kicad_sch", "edit.kicad_pcb", "edit.kicad_pro"}


def images_for(case):
    return IMAGES + (("front-silk.png",) if case == "usable" else ())


def requirements_for(method, case):
    if case not in {"legacy", "usable"}:
        raise ValueError("Unknown bounded hierarchy case")
    return method / ("usable/requirements" if case == "usable" else "requirements")


REQUIRED_RESULTS = {
    "review/" + name
    for name in (
        *IMAGES,
        "native.xml",
        "native-erc.json",
        "native-drc.json",
        "observation.json",
        "comparison.json",
        "svg/edit.svg",
        "svg/edit-CHILD_A.svg",
        "svg/edit-CHILD_B.svg",
        "front.svg",
        "back.svg",
    )
}


def read(path):
    return json.loads(Path(path).read_bytes())


def sha(path):
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise ValueError("Expected an unlinked input file: " + str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def put(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def tree(path, python_only=False):
    path = Path(path)
    if not path.is_dir() or path.is_symlink():
        raise ValueError("Expected an unlinked input directory: " + str(path))
    result = {}
    for item in sorted(path.rglob("*")):
        if "__pycache__" in item.parts:
            continue
        if item.is_symlink() or getattr(item, "is_junction", lambda: False)():
            raise ValueError("Linked input unsupported")
        if item.is_file() and (not python_only or item.suffix == ".py"):
            result[item.relative_to(path).as_posix()] = sha(item)
    return result


def capture(package, project, configuration, stage, case="legacy"):
    method = package / "hierarchy-edit"
    packet = package / "rc/agent-packet"
    settings = read(packet / "DEPENDENCIES.json")
    revision = read(packet / "REVISION.json")
    if sha(revision["candidate"]) != revision["sha256"]:
        raise ValueError("Selected candidate drifted")
    native = {}
    for name, item in {**settings["libraries"], **settings["native_files"]}.items():
        if sha(item["path"]) != item["sha256"]:
            raise ValueError("Configured dependency drifted: " + name)
        native[name] = item
    env = settings["environment"]
    sdk = Path(env["KICAD_CLI_DRIVER_SDK_PATH"])
    requirements_path = requirements_for(method, case)
    frozen, _, verified_baseline = read_verified_baseline(
        requirements_path / "FROZEN.json", requirements_path / "baseline-observation.json"
    )
    requirements = tree(requirements_path)
    if (
        requirements.get("FROZEN.json") != verified_baseline["frozen_sha256"]
        or requirements.get("baseline-observation.json")
        != verified_baseline["baseline_observation_sha256"]
    ):
        raise ValueError("Frozen baseline changed during input capture")
    files = tree(project)
    expected = frozen["input_preparation"]["fixture_files"]
    if set(files) - {"edit.kicad_prl"} != set(expected):
        raise ValueError("Project input file set differs from the bounded fixture")
    if "edit.kicad_prl" in files and not isinstance(read(project / "edit.kicad_prl"), dict):
        raise ValueError("Project-local preference is not valid JSON")
    if case == "usable" and stage not in frozen["stages"]:
        raise ValueError("Stage is not declared for the frozen usable hierarchy")
    if any(
        files[n] != h
        for n, h in expected.items()
        if stage == frozen.get("baseline_stage", "H0") or n not in PRIMARY
    ):
        raise ValueError("Frozen baseline/library input differs")
    return {
        "stage": stage,
        "case": case,
        "project_path": str(project),
        "project": files,
        "configuration_path": str(configuration),
        "configuration": tree(configuration),
        "requirements": requirements,
        "verified_baseline": verified_baseline,
        "methods": tree(method, True),
        "shared_readers": tree(package / "rc/execution/consumers", True),
        "candidate": revision,
        "dependency_configuration": sha(packet / "DEPENDENCIES.json"),
        "declared_dependencies": native,
        "consumer_python": sha(settings["readonly_consumer_python"]),
        "driver_sdk": tree(sdk),
        "driver_spec": tree(sdk.parent / "driver_spec"),
        "scope": sha(env["KICAD_CLI_SCOPE_FILE"]),
        "environment": env,
    }


def origin_stable(package, work):
    start = read(work / "SOURCE_BINDING.json")
    current = capture(
        package,
        Path(start["project_path"]),
        Path(start["configuration_path"]),
        start["stage"],
        start.get("case", "legacy"),
    )
    if current != start:
        raise ValueError("Original inputs/dependencies changed; start a new complete check cycle")
    return start


def stable(package, work):
    origin_stable(package, work)
    start = read(work / "START.json")
    for group in ("project", "configuration", "requirements"):
        if tree(work / group) != start["copied_inputs"][group]:
            raise ValueError("Checked copy changed: " + group)
    if sha(work / "SOURCE_BINDING.json") != start["source_binding_sha256"]:
        raise ValueError("Original binding record changed")
    return start


def verify(package, work):
    start = stable(package, work)
    images = images_for(start.get("case", "legacy"))
    required = REQUIRED_RESULTS | (
        {"review/front-silk.png", "review/front-silk.svg"}
        if start.get("case") == "usable"
        else set()
    )
    ready = read(work / "READY.json")
    if (
        ready["start_sha256"] != sha(work / "START.json")
        or ready["review_id"] != start["review_id"]
        or ready["stage"] != start["stage"]
        or ready["bounded_edit_verified"] is not True
    ):
        raise ValueError("Review does not match this complete bounded edit check")
    if (
        not required <= set(ready["artifacts"])
        or set(ready["images"]) != set(images)
        or ready["images"] != {name: ready["artifacts"].get("review/" + name) for name in images}
    ):
        raise ValueError("Required current-cycle artifacts/images are missing")
    for name, expected in ready["artifacts"].items():
        if not (work / name).resolve().is_relative_to(work) or sha(work / name) != expected:
            raise ValueError("Checked artifact replaced: " + name)
    visual = read(work / "VISUAL_REVIEW.json")
    if (
        visual.get("passed") is not True
        or visual.get("review_id") != ready["review_id"]
        or visual.get("ready_sha256") != sha(work / "READY.json")
        or visual.get("images") != ready["images"]
    ):
        raise ValueError("Explicit passing review of all exact current full images is required")
    if (
        not isinstance(visual.get("reviewer"), str)
        or not visual["reviewer"].strip()
        or set(visual.get("observations", {})) != set(images)
        or not all(isinstance(v, str) and v.strip() for v in visual["observations"].values())
    ):
        raise ValueError("Record an actual reviewer and observations for every full image")
    return ready


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "verify", "freeze"))
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--config-root", type=Path)
    parser.add_argument(
        "--case",
        choices=("legacy", "usable"),
        help="check defaults to legacy; freeze/verify use the bound case",
    )
    parser.add_argument(
        "--stage",
        choices=("H0", "H1", "H2", "H3", "H4", "N1", "M1", "M2", "U0", "U1", "U2", "U3", "U4"),
    )
    args = parser.parse_args()
    package, work = args.package_root.resolve(), args.out.resolve()
    execution = package / "rc/execution"
    if not work.is_relative_to(execution) or work == execution:
        raise ValueError("Check output must be inside this package's owned execution root")
    if args.action != "check":
        if args.case and args.case != read(work / "SOURCE_BINDING.json").get("case", "legacy"):
            raise ValueError("Requested case differs from the bound review")
        ready = verify(package, work)
        if args.action == "freeze":
            target = work / "snapshot"
            target.mkdir(exist_ok=False)
            selected = {}
            start = read(work / "START.json")
            for group, files in start["copied_inputs"].items():
                selected.update({group + "/" + n: h for n, h in files.items()})
            selected.update(ready["artifacts"])
            selected.update(
                {
                    n: sha(work / n)
                    for n in (
                        "SOURCE_BINDING.json",
                        "START.json",
                        "READY.json",
                        "VISUAL_REVIEW.json",
                    )
                }
            )
            for name, expected in selected.items():
                output = target / name
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(work / name, output)
                if sha(output) != expected or sha(work / name) != expected:
                    raise ValueError("Input changed while freezing; snapshot incomplete")
            verify(package, work)
            put(
                target / "MANIFEST.json",
                {
                    "review_id": ready["review_id"],
                    "stage": ready["stage"],
                    "files": selected,
                    "scope": "Bounded edit and explicit visual verdict; "
                    "native checks retain known findings",
                },
            )
        elif (work / "snapshot").exists():
            manifest = read(work / "snapshot/MANIFEST.json")
            files = tree(work / "snapshot")
            files.pop("MANIFEST.json")
            if (
                files != manifest["files"]
                or manifest["review_id"] != ready["review_id"]
                or manifest["stage"] != ready["stage"]
            ):
                raise ValueError("Frozen snapshot changed")
        print(
            json.dumps(
                {"ok": True, "action": args.action, "scope": "bounded hierarchy editing only"}
            )
        )
        return
    if not args.project or not args.config_root or not args.stage:
        raise ValueError("check requires project, config-root and stage")
    project, configuration = args.project.resolve(), args.config_root.resolve()
    if (
        not project.is_relative_to(execution)
        or work.is_relative_to(project)
        or work.is_relative_to(configuration)
    ):
        raise ValueError("Use an owned project and separate check/configuration directories")
    method = package / "hierarchy-edit"
    case = args.case or "legacy"
    images = images_for(case)
    requirements_path = requirements_for(method, case)
    settings = read(package / "rc/agent-packet/DEPENDENCIES.json")
    if Path(sys.executable).resolve() != Path(settings["readonly_consumer_python"]).resolve():
        raise ValueError("Use the explicitly configured read-only consumer Python")
    before = capture(package, project, configuration, args.stage, case)
    work.mkdir(parents=True, exist_ok=False)
    put(work / "SOURCE_BINDING.json", before)
    shutil.copytree(project, work / "project")
    shutil.copytree(configuration, work / "configuration")
    shutil.copytree(requirements_path, work / "requirements")
    for group in ("project", "configuration", "requirements"):
        if tree(work / group) != before[group]:
            raise ValueError("Copy differs from bound original: " + group)
    origin_stable(package, work)
    sys.path.insert(0, str(method / "support"))
    from process_evidence import run_recorded

    evidence = work / "evidence"
    evidence.mkdir()
    review = work / "review"
    review.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("KICAD", "PYTHON"))}
    env.update(
        settings["environment"],
        KICAD_CONFIG_HOME=str(work / "configuration"),
        KICAD_CLI_STATE_DIR=str(work / "state"),
        PYTHONIOENCODING="utf-8",
        PYTHONDONTWRITEBYTECODE="1",
    )
    official, native = env["KICAD_CLI_OFFICIAL"], env["KICAD_CLI_PYTHON"]
    sch, pcb = work / "project/edit.kicad_sch", work / "project/edit.kicad_pcb"
    initialized = False

    def call(name, command, report=None, exits=(0,), stdout_json=False):
        origin_stable(package, work)
        if initialized:
            stable(package, work)
        record, value = run_recorded(
            evidence,
            name,
            command,
            cwd=work,
            env=env,
            timeout=150,
            report=report,
            stdout_json=stdout_json,
            role="read-only-hierarchy-acceptance",
        )
        origin_stable(package, work)
        if initialized:
            stable(package, work)
        expected_execution = "completed" if record["exit"] == 0 else "nonzero_exit"
        if (
            record["exit"] not in exits
            or record["execution"] != expected_execution
            or (report and record.get("report", {}).get("status") != "valid")
        ):
            raise ValueError("Native/check process failed or report invalid: " + name)
        return value

    def native_reports(prefix):
        xml = review / (prefix + ".xml")
        call(
            prefix + "-xml",
            [official, "sch", "export", "netlist", "--format", "kicadxml", "--output", xml, sch],
            {"path": str(xml), "kind": "xml"},
        )
        for kind, path in (("erc", sch), ("drc", pcb)):
            output = review / (prefix + "-" + kind + ".json")
            command = [official, "sch" if kind == "erc" else "pcb", kind]
            if kind == "drc":
                command += ["--schematic-parity"]
            command += [
                "--format",
                "json",
                "--units",
                "mm",
                "--severity-all",
                "--exit-code-violations",
                "--output",
                output,
                path,
            ]
            call(prefix + "-" + kind, command, {"path": str(output), "kind": kind}, exits=(0, 5))
        return xml

    original_config = {
        n: (work / "configuration" / n).read_bytes() for n in before["configuration"]
    }
    native_reports("initialization")
    after_project = tree(work / "project")
    if any(after_project.get(n) != h for n, h in before["project"].items()) or set(
        after_project
    ) - set(before["project"]) - {"edit.kicad_prl"}:
        raise ValueError("Native initialization changed copied design inputs")
    if "edit.kicad_prl" in after_project and not isinstance(
        read(work / "project/edit.kicad_prl"), dict
    ):
        raise ValueError("Invalid initialized project-local preference")
    current_cfg = tree(work / "configuration")
    allowed = {
        "10.0/" + n + ".json"
        for n in (
            "kicad_common",
            "eeschema",
            "pcbnew",
            "cvpcb",
            "fpedit",
            "3d_viewer",
            "kicad",
            "symbol_editor",
        )
    }
    if set(current_cfg) - set(before["configuration"]) - allowed:
        raise ValueError("Unexpected native initialization configuration")
    for name, digest in before["configuration"].items():
        if current_cfg.get(name) == digest:
            continue
        if name != "10.0/kicad_common.json" or name not in current_cfg:
            raise ValueError("Supplied configuration changed in native initialization")
        old, new = json.loads(original_config[name]), read(work / "configuration" / name)
        old.get("system", {}).pop("working_dir", None)
        new.get("system", {}).pop("working_dir", None)
        if old != new:
            raise ValueError("Native preference change exceeds working-directory relocation")
    put(
        work / "START.json",
        {
            "review_id": "hierarchy-review-" + secrets.token_hex(16),
            "stage": args.stage,
            "case": case,
            "source_binding_sha256": sha(work / "SOURCE_BINDING.json"),
            "copied_inputs": {
                g: tree(work / g) for g in ("project", "configuration", "requirements")
            },
            "copy_only_initialization_changes": {
                "configuration_before": before["configuration"],
                "configuration_after": current_cfg,
                "project_before": before["project"],
                "project_after": after_project,
            },
        },
    )
    initialized = True
    xml = native_reports("native")
    observed = review / "observation.json"
    readers = package / "rc/execution/consumers"
    call(
        "native-observation",
        [
            native,
            method / "observe.py",
            "--schematic",
            sch,
            "--board",
            pcb,
            "--xml",
            xml,
            "--readers",
            readers,
            "--output",
            observed,
        ],
        stdout_json=True,
    )
    comparison = review / "comparison.json"
    call(
        "frozen-intent",
        [
            sys.executable,
            method / "compare.py",
            "--readers",
            readers,
            "--baseline",
            work / "requirements/baseline-observation.json",
            "--current",
            observed,
            "--frozen",
            work / "requirements/FROZEN.json",
            "--stage",
            args.stage,
            "--erc",
            review / "native-erc.json",
            "--drc",
            review / "native-drc.json",
            "--output",
            comparison,
        ],
        stdout_json=True,
    )
    result = read(comparison)
    if result["bounded_edit_verified"] is not True:
        raise ValueError("Independent intent/preservation comparison failed")
    call("schematic-svg", [official, "sch", "export", "svg", "--output", review / "svg", sch])
    for name in IMAGES[:3]:
        call(
            "render-" + name,
            [
                sys.executable,
                readers / "render_svg.py",
                review / "svg" / name.replace(".png", ".svg"),
                review / name,
                "3",
            ],
        )
    board_views = [("front", "F.Cu,F.SilkS,Edge.Cuts"), ("back", "B.Cu,B.SilkS,Edge.Cuts")]
    if case == "usable":
        board_views.append(("front-silk", "F.Mask,F.SilkS,Edge.Cuts"))
    for side, layers in board_views:
        call(
            side + "-svg",
            [
                official,
                "pcb",
                "export",
                "svg",
                "--layers",
                layers,
                "--mode-single",
                "--fit-page-to-board",
                "--exclude-drawing-sheet",
                *(["--black-and-white"] if case == "usable" else []),
                "--output",
                review / (side + ".svg"),
                pcb,
            ],
        )
        call(
            side + "-raster",
            [
                sys.executable,
                readers / "render_svg.py",
                review / (side + ".svg"),
                review / (side + ".png"),
                "8",
            ],
        )
    start = stable(package, work)
    artifacts = {"review/" + n: h for n, h in tree(review).items()}
    artifacts.update({"evidence/" + n: h for n, h in tree(evidence).items()})
    image_hashes = {n: sha(review / n) for n in images}
    for name in images:
        if not (review / name).read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Missing rendered full image")
    ready = {
        "review_id": start["review_id"],
        "stage": args.stage,
        "start_sha256": sha(work / "START.json"),
        "bounded_edit_verified": True,
        "delivery_eligible": result["delivery_eligible"],
        "artifacts": artifacts,
        "images": image_hashes,
        "native_erc": result["native_erc"],
        "native_drc": result["native_drc"],
        "scope": result["scope"],
    }
    put(work / "READY.json", ready)
    put(
        work / "VISUAL_TEMPLATE.json",
        {
            "passed": False,
            "review_id": ready["review_id"],
            "ready_sha256": sha(work / "READY.json"),
            "images": image_hashes,
            "reviewer": "",
            "observations": {n: "" for n in images},
        },
    )
    print(
        json.dumps(
            {
                "status": "awaiting_actual_full_image_review",
                "bounded_edit_verified": True,
                "delivery_eligible": result["delivery_eligible"],
                "native_erc": result["native_erc"],
                "native_drc": result["native_drc"],
                "images": list(images),
                "next": "Inspect every required full image, write an explicit VISUAL_REVIEW.json, "
                "then freeze and verify. Native baseline findings remain failures.",
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError) as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "HIERARCHY_REVIEW_REFUSED",
                        "message": str(error),
                        "next_action": "Preserve evidence; correct only the declared input "
                        "or start a fresh complete review. Do not refresh an old pass.",
                    },
                }
            )
        )
        raise SystemExit(2) from error
