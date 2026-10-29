"""Read/copy/check/render a bounded saved RC edit stage; never execute design writes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def put(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "verify", "freeze"))
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument(
        "--out", type=Path, required=True, help="New check directory / existing review root"
    )
    parser.add_argument("--stage", choices=[f"S{i}" for i in range(6)])
    parser.add_argument("--schematic", type=Path)
    parser.add_argument("--board", type=Path)
    parser.add_argument("--config-root", type=Path)
    parser.add_argument("--baseline", type=Path, help="Frozen S0 review root, required after S0")
    parser.add_argument(
        "--previous", type=Path, help="Frozen preceding review root, required after S0"
    )
    args = parser.parse_args()
    package, work = args.package_root.resolve(), args.out.resolve()
    base = package / "rc/execution"
    if not work.is_relative_to(base) or work == base:
        raise ValueError(
            "Check outputs must remain inside the authorized package rc/execution root"
        )
    sys.path.insert(0, str(base))
    import review_binding as audit

    packet, review = work / "packet", work / "review"
    if args.action != "check":
        audit.verify(work, base, packet, review)
        if args.action == "freeze":
            audit.freeze(work, base, packet, review, work / "snapshot")
        elif (work / "snapshot").exists():
            audit.verify_snapshot(work / "snapshot")
        print(json.dumps({"ok": True, "action": args.action, "review_root": str(work)}))
        return 0
    if not all((args.stage, args.schematic, args.board, args.config_root)):
        raise ValueError("check requires --stage, --schematic, --board, --config-root")
    sources = {
        "rc4.kicad_sch": args.schematic.resolve(),
        "rc4.kicad_pcb": args.board.resolve(),
        "rc4.kicad_pro": args.board.resolve().with_suffix(".kicad_pro"),
    }
    config = args.config_root.resolve()
    if not all((config / "10.0" / name).is_file() for name in ("sym-lib-table", "fp-lib-table")):
        raise ValueError(
            "Explicit configuration must contain 10.0/sym-lib-table and fp-lib-table. "
            "Copy the package rc/agent-packet/config seed to an owned configuration "
            "directory; do not change user/global preferences."
        )
    if args.schematic.name != "rc4.kicad_sch" or args.board.name != "rc4.kicad_pcb":
        raise ValueError("This bounded RC profile requires the frozen rc4 file stem")
    source_dirs = {p.parent for p in sources.values()}
    if any(work.is_relative_to(folder) for folder in source_dirs | {config}):
        raise ValueError(
            "Review output must be separate from live project/configuration directories"
        )
    for directory in source_dirs:
        for name in ("sym-lib-table", "fp-lib-table", "rc4.kicad_dru"):
            if (directory / name).exists():
                raise ValueError(
                    "This supplied RC profile has no project-local rule/library files: " + name
                )
    requirements = package / "advanced-edit/requirements"
    frozen = read(requirements / "FROZEN.json")
    before_copy = {name: sha(path) for name, path in sources.items()}
    if args.stage == "S0" and before_copy != frozen["baseline_files"]:
        raise ValueError("S0 must be the exact frozen original RC ECO input")
    priors = {}
    for role, prior, expected in (
        ("baseline", args.baseline, "S0"),
        ("previous", args.previous, f"S{int(args.stage[1:]) - 1}"),
    ):
        if args.stage != "S0":
            if prior is None:
                raise ValueError("Every changed stage requires frozen --baseline and --previous")
            verified = audit.verified_snapshot_inputs(prior.resolve() / "snapshot")
            if work.is_relative_to(prior.resolve()):
                raise ValueError(
                    "New check output must not modify predecessor evidence directories"
                )
            if verified["stage"] != "advanced-" + expected:
                raise ValueError("Wrong predecessor check cycle supplied")
            if frozen["pcb_strategy"].get("same_board"):
                for name in ("rc4.kicad_sch", "rc4.kicad_pcb"):
                    if verified["source_paths"][name] != str(sources[name]):
                        raise ValueError(
                            "Same-board lifecycle cannot switch the original schematic/PCB path"
                        )
            priors[role] = verified
    settings = read(package / "rc/agent-packet/DEPENDENCIES.json")
    if Path(sys.executable).resolve() != Path(settings["readonly_consumer_python"]).resolve():
        raise ValueError("Use the explicitly configured read-only consumer Python")
    work.mkdir(parents=True, exist_ok=False)
    for folder in ("project", "plans", "inputs", "evidence", "packet", "state"):
        (work / folder).mkdir()
    revision = read(package / "rc/agent-packet/REVISION.json")
    put(
        work / "plans/candidate-selection.json",
        {"candidate_path": revision["candidate"], "sha256": revision["sha256"]},
    )
    put(
        work / "plans/baseline-layout-v2.json",
        {"stage": args.stage, "frozen_requirement_sha256": sha(requirements / "FROZEN.json")},
    )
    put(
        work / "plans/zone-ids.json",
        {"policy": "Original zone definitions are compared independently"},
    )
    settings["bound_sources"] = {
        name: {"kind": "file", "path": str(path)} for name, path in sources.items()
    }
    settings["bound_sources"].update(
        {
            "live_configuration": {"kind": "directory", "path": str(config)},
            "advanced_method_and_requirements": {
                "kind": "directory",
                "path": str(package / "advanced-edit"),
            },
        }
    )
    settings["verified_predecessors"] = priors
    settings["bound_sources"].update(
        {
            "prior_" + role: {
                "kind": "directory",
                "path": verified["path"],
                "expected_files": verified["files"],
            }
            for role, verified in priors.items()
        }
    )
    settings["bound_sources"].update(
        {
            "live_project_directory_" + str(i): {"kind": "directory", "path": str(path)}
            for i, path in enumerate(sorted(source_dirs))
        }
    )
    put(packet / "DEPENDENCIES.json", settings)
    stage = "advanced-" + args.stage
    # Bind live file sets, configuration, methods and dependencies BEFORE the
    # first copy. No later initialization is allowed to adopt a changed source.
    origin = audit.capture(work, base, packet, stage, copies=False)
    for name, bound in origin["external_trees"].items():
        if name.startswith("source:live_project_directory_") and any(
            key in bound["files"] for key in ("sym-lib-table", "fp-lib-table", "rc4.kicad_dru")
        ):
            raise ValueError("Project-local rule/library input changed or is unsupported")
    copied_inputs = {
        "project": {name: origin["external_files"]["source:" + name]["sha256"] for name in sources},
        "configuration": origin["external_trees"]["source:live_configuration"]["files"],
        "requirements": {
            name.removeprefix("requirements/"): value
            for name, value in origin["external_trees"]["source:advanced_method_and_requirements"][
                "files"
            ].items()
            if name.startswith("requirements/")
        },
        "predecessors": {
            role + "/" + name: verified["files"][name]
            for role, verified in priors.items()
            for name in (
                "MANIFEST.json",
                "review/START.json",
                "review/READY.json",
                "review/VISUAL_REVIEW.json",
                "review/native-observation-xml-identity.json",
            )
        },
    }
    put(
        packet / "PRE_COPY.json",
        {"origin": origin, "origin_digest": audit.digest(origin), "copied_inputs": copied_inputs},
    )
    if copied_inputs["project"] != before_copy:
        raise ValueError("Original project changed before the copy binding was established")
    for name, path in sources.items():
        shutil.copyfile(path, work / "project" / name)
    shutil.copytree(config, work / "configuration")
    shutil.copytree(requirements, packet / "requirements")
    for name, expected in copied_inputs["predecessors"].items():
        role, relative = name.split("/", 1)
        source = Path(priors[role]["path"]) / relative
        destination = packet / "predecessors" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        if sha(source) != expected or sha(destination) != expected:
            raise ValueError("Verified predecessor changed while copying evidence")
    audit.verify_origin(work, base, packet, stage)
    if {
        "project": audit.tree(work / "project"),
        "configuration": audit.tree(work / "configuration"),
        "requirements": audit.tree(packet / "requirements"),
        "predecessors": audit.tree(packet / "predecessors") if priors else {},
    } != copied_inputs:
        raise ValueError("Review copies differ from the bound original input set")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("KICAD", "PYTHON"))}
    env.update(
        settings["environment"],
        KICAD_CONFIG_HOME=str(work / "configuration"),
        KICAD_CLI_STATE_DIR=str(work / "state"),
        PYTHONIOENCODING="utf-8",
        PYTHONDONTWRITEBYTECODE="1",
    )
    native = env["KICAD_CLI_PYTHON"]
    official = env["KICAD_CLI_OFFICIAL"]
    pcb, sch = work / "project/rc4.kicad_pcb", work / "project/rc4.kicad_sch"

    def call(name, argv):
        audit.verify_origin(work, base, packet, stage)
        dest = work / "evidence" / name
        dest.mkdir()
        started = time.monotonic()
        record = {
            "argv": list(map(str, argv)),
            "program_sha256": sha(argv[0]),
            "cwd": str(work),
            "copied_input_hashes": {
                p.name: sha(p) for p in (sch, pcb, pcb.with_suffix(".kicad_pro"))
            },
            "status": "started",
        }
        put(dest / "start.json", record)
        try:
            process = subprocess.run(
                list(map(str, argv)), cwd=work, env=env, capture_output=True, timeout=150
            )
            out, err, code = process.stdout, process.stderr, process.returncode
            status = "exited" if code == 0 else "failed"
        except subprocess.TimeoutExpired as exc:
            out, err, code, status = exc.stdout or b"", exc.stderr or b"", None, "timed_out"
        (dest / "stdout").write_bytes(out)
        (dest / "stderr").write_bytes(err)
        put(
            dest / "finish.json",
            {
                **record,
                "status": status,
                "exit_code": code,
                "elapsed_seconds": time.monotonic() - started,
                "stdout_sha256": sha(dest / "stdout"),
                "stderr_sha256": sha(dest / "stderr"),
            },
        )
        audit.verify_origin(work, base, packet, stage)
        actual_project = audit.tree(work / "project")
        allowed_preference = {"rc4.kicad_prl"} if name.startswith("initialize-") else set()
        if any(actual_project.get(n) != h for n, h in copied_inputs["project"].items()) or (
            name.startswith("initialize-")
            and set(actual_project) - set(copied_inputs["project"]) - allowed_preference
        ):
            raise ValueError("Native initialization/check changed the copied project input set")
        if "rc4.kicad_prl" in actual_project:
            if not isinstance(read(work / "project/rc4.kicad_prl"), dict):
                raise ValueError("Native project-local preferences are not valid JSON")
        if code != 0:
            raise ValueError(
                f"{name} did not finish successfully ({code}); "
                "raw evidence retained, no automatic retry"
            )
        return dest / "finish.json", out

    # Native preference initialization is explicit and not a passing check.
    original_cfg = audit.tree(work / "configuration")
    original_cfg_bytes = {
        name: (work / "configuration" / name).read_bytes() for name in original_cfg
    }
    call(
        "initialize-xml",
        [
            official,
            "sch",
            "export",
            "netlist",
            "--format",
            "kicadxml",
            "--output",
            work / "inputs/init.xml",
            sch,
        ],
    )
    call(
        "initialize-erc",
        [
            official,
            "sch",
            "erc",
            "--format",
            "json",
            "--output",
            work / "inputs/init-erc.json",
            sch,
        ],
    )
    call(
        "initialize-drc",
        [
            official,
            "pcb",
            "drc",
            "--format",
            "json",
            "--output",
            work / "inputs/init-drc.json",
            pcb,
        ],
    )
    if {n: sha(work / "project" / n) for n in sources} != before_copy:
        raise ValueError("Native initialization modified an existing design file")
    current_cfg = audit.tree(work / "configuration")
    allowed_new = {
        "10.0/" + name
        for name in (
            "kicad_common.json",
            "eeschema.json",
            "cvpcb.json",
            "pcbnew.json",
            "fpedit.json",
            "3d_viewer.json",
            "kicad.json",
            "symbol_editor.json",
        )
    }
    if set(current_cfg) - set(original_cfg) - allowed_new:
        raise ValueError(
            "Native initialization created unexpected preference files: "
            + ", ".join(sorted(set(current_cfg) - set(original_cfg) - allowed_new))
        )
    for name, expected in original_cfg.items():
        if current_cfg.get(name) == expected:
            continue
        if name != "10.0/kicad_common.json" or name not in current_cfg:
            raise ValueError("Native initialization changed a supplied preference file: " + name)
        old, new = json.loads(original_cfg_bytes[name]), read(work / "configuration" / name)
        old.get("system", {}).pop("working_dir", None)
        new.get("system", {}).pop("working_dir", None)
        if old != new:
            raise ValueError(
                "Native initialization changed preferences beyond working_dir relocation"
            )
    put(
        work / "inputs/configuration-relocation.json",
        {
            "original": original_cfg,
            "initialized": audit.tree(work / "configuration"),
            "project_before": copied_inputs["project"],
            "project_initialized": audit.tree(work / "project"),
            "project_preference_policy": "Only a new rc4.kicad_prl JSON preference in the review "
            "copy is allowed during initialization; design files remain exact and live sources "
            "stay bound.",
            "purpose": (
                "Fresh check copy; no earlier verdict inherited. Live originals separately bound."
            ),
        },
    )
    start = audit.begin(work, base, packet, review, "advanced-" + args.stage)
    steps = []

    def checked(name, argv):
        audit.stable(work, base, packet, review)
        evidence, stdout = call(name, argv)
        audit.stable(work, base, packet, review)
        steps.append(
            {
                "check": name,
                "passed": True,
                "review_id": start["review_id"],
                "evidence_path": str(evidence),
                "evidence_sha256": sha(evidence),
            }
        )
        return stdout

    intent = packet / "requirements" / f"intent-{args.stage}.json"
    consumers = base / "consumers"
    checked(
        "native-xml",
        [
            official,
            "sch",
            "export",
            "netlist",
            "--format",
            "kicadxml",
            "--output",
            review / "rc4.xml",
            sch,
        ],
    )
    result = checked(
        "independent-xml",
        [sys.executable, consumers / "compare_native_netlist.py", review / "rc4.xml", intent],
    )
    put(review / "intent.json", json.loads(result))
    observation = review / "native-observation-xml-identity.json"
    checked(
        "reopened",
        [
            native,
            consumers / "native_board_observe.py",
            pcb,
            intent,
            observation,
            review / "rc4.xml",
        ],
    )
    baseline = (
        observation
        if args.stage == "S0"
        else packet / "predecessors/baseline/review/native-observation-xml-identity.json"
    )
    checked(
        "geometry",
        [
            sys.executable,
            package / "advanced-edit/geometry.py",
            baseline,
            observation,
            packet / "requirements/FROZEN.json",
            args.stage,
            review / "geometry.json",
            *(
                [packet / "predecessors/previous/review/native-observation-xml-identity.json"]
                if args.stage != "S0"
                else []
            ),
        ],
    )
    if args.stage in {"S3", "S4"}:
        previous = read(
            packet / "predecessors/previous/review/native-observation-xml-identity.json"
        )
        old = next(p for p in previous["footprints"] if p["ref"] == "TP1")
        new = next(p for p in read(observation)["footprints"] if p["ref"] == "TP1")
        if any(
            old[k] != new[k]
            for k in ("uuid", "source_path", "footprint", "position_nm", "rotation_deg", "layer")
        ) or [(p["uuid"], p["number"]) for p in old["pads"]] != [
            (p["uuid"], p["number"]) for p in new["pads"]
        ]:
            raise ValueError("Testpoint identity/pose changed across its declared lifetime")
    checked(
        "erc",
        [
            official,
            "sch",
            "erc",
            "--format",
            "json",
            "--units",
            "mm",
            "--severity-all",
            "--exit-code-violations",
            "--output",
            review / "erc.json",
            sch,
        ],
    )
    checked(
        "drc",
        [
            official,
            "pcb",
            "drc",
            "--format",
            "json",
            "--units",
            "mm",
            "--severity-all",
            "--all-track-errors",
            "--schematic-parity",
            "--exit-code-violations",
            "--output",
            review / "drc.json",
            pcb,
        ],
    )
    erc, drc = read(review / "erc.json"), read(review / "drc.json")
    if (
        any(s["violations"] for s in erc["sheets"])
        or drc["violations"]
        or drc["unconnected_items"]
        or drc["schematic_parity"]
    ):
        raise ValueError(
            "Required native report contains findings; no READY or passing visual template"
        )
    checked(
        "schematic-svg",
        [official, "sch", "export", "svg", "--output", review / "schematic-svg", sch],
    )
    checked(
        "schematic-raster",
        [
            sys.executable,
            consumers / "render_svg.py",
            review / "schematic-svg/rc4.svg",
            review / "schematic.png",
            "3",
        ],
    )
    for side, layers in (("front", "F.Cu,F.SilkS,Edge.Cuts"), ("back", "B.Cu,B.SilkS,Edge.Cuts")):
        checked(
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
                "--output",
                review / (side + ".svg"),
                pcb,
            ],
        )
        checked(
            side + "-raster",
            [
                sys.executable,
                consumers / "render_svg.py",
                review / (side + ".svg"),
                review / (side + ".png"),
                "10",
            ],
        )
    ready = audit.complete(work, base, packet, review, steps)
    put(
        review / "VISUAL_TEMPLATE.json",
        {
            "passed": False,
            "review_id": ready["review_id"],
            "ready_sha256": sha(review / "READY.json"),
            "images": ready["images"],
            "reviewer": "",
            "observations": {name: "" for name in audit.IMAGES},
        },
    )
    print(
        json.dumps(
            {
                "ok": True,
                "status": "awaiting_actual_full_image_review",
                "review": str(review),
                "next": (
                    "Inspect all three images, complete VISUAL_REVIEW.json, then freeze. "
                    "Verify again before the next design operation."
                ),
            }
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "ADVANCED_REVIEW_REFUSED",
                        "message": str(exc),
                        "next_action": (
                            "Preserve evidence. Observe actual files; a changed input "
                            "requires a new complete check/render/visual cycle, "
                            "never a refreshed old pass."
                        ),
                    },
                }
            )
        )
        raise SystemExit(2) from exc
