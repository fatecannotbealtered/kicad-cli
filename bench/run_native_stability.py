"""Input-bound finite native controls; a successful replay never closes history."""

import argparse
import json
import shutil
from pathlib import Path

from native_repro import freeze, verify, verify_configuration
from process_evidence import files, run_recorded, sha, utc, write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--capsule", type=Path)
    group.add_argument("--failed-run", type=Path)
    parser.add_argument("--official-cli", type=Path)
    parser.add_argument("--historical-cwd", type=Path)
    parser.add_argument("--event")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    root = options.output.resolve()
    if options.capsule and root.is_relative_to(options.capsule.resolve()):
        parser.error("Output must be separate from the frozen input capsule")
    if options.failed_run and root.is_relative_to(options.failed_run.resolve()):
        parser.error("Output must be separate from the historical failure")
    root.mkdir(parents=True, exist_ok=False)
    capsule = options.capsule.resolve() if options.capsule else root / "capsule"
    if options.failed_run:
        if not options.official_cli or not options.historical_cwd:
            parser.error("Freezing old evidence requires explicit official CLI and historical cwd")
        freeze(
            options.failed_run,
            capsule,
            options.official_cli,
            options.historical_cwd,
            event_name=options.event,
        )
    manifest = json.loads((capsule / "manifest.json").read_bytes())
    if options.prepare_only:
        print(json.dumps({"capsule": str(capsule), "gaps": manifest["gaps"]}))
        return
    verify(manifest, capsule)
    definition = [
        {
            "id": "baseline",
            "factor": "none relative to recovered baseline",
            "hypothesis": "Observe the retained command with declared file dependencies present",
            "debug": False,
            "stdio": "pipes",
        },
        {
            "id": "debug",
            "factor": "DEBUG_ONLY_THIS_PROCESS only",
            "hypothesis": "Capture parameters/context/stack/dump if the native fault recurs",
            "debug": True,
            "stdio": "pipes",
        },
        {
            "id": "stdio-file",
            "factor": "stdout/stderr files instead of pipes only",
            "hypothesis": "Observe whether the native outcome differs with output handle type",
            "debug": False,
            "stdio": "files",
        },
    ]
    write(
        root / "plan.json",
        {
            "frozen_at": utc(),
            "experiments": definition,
            "repetitions": 1,
            "timeout_seconds": 120,
            "expected": "Record actual outcome; no successful control proves a cause or a fix",
            "common_differences_from_history": manifest["gaps"]
            + [
                "Ephemeral job ownership is common here; absent in the historical harness.",
                "Isolation paths/PID/timing differ; this is not an exact historical replay.",
            ],
            "limits": "ERC and XML remain separate observations; no combined root cause inference",
        },
    )
    rows = []
    for experiment in definition:
        case = root / experiment["id"]
        case.mkdir()
        if not manifest["configuration_present"] or not manifest["declared_file_inputs_complete"]:
            row = dict(
                experiment,
                status="not_run",
                reason="Missing historical configuration or declared input dependency",
            )
            rows.append(row)
            write(case / "result.json", row)
            continue
        shutil.copytree(capsule / "project", case / "project")
        shutil.copytree(capsule / "configuration", case / "configuration")
        env = dict(manifest["environment"], KICAD_CONFIG_HOME=str(case / "configuration"))
        argv = list(manifest["failure_event"]["argv"])
        for i, arg in enumerate(argv):
            if arg.endswith(".kicad_sch"):
                argv[i] = str(case / "project" / manifest["schematic_relative"])
        report = case / ("result.xml" if "kicadxml" in argv else "result.json")
        argv[argv.index("--output") + 1] = str(report)
        verify(manifest, capsule)
        configuration_evidence = verify_configuration(manifest, case / "configuration")
        write(case / "configuration-verification.json", configuration_evidence)
        inputs = [p for p in (case / "project").rglob("*") if p.is_file()]
        inputs += [p for p in (case / "configuration").rglob("*") if p.is_file()]
        inputs += [Path(r["source"]) for r in manifest["dependencies"]]
        binding = {
            "capsule_sha256": sha(capsule / "manifest.json"),
            "configuration_verification": configuration_evidence,
            "runner_sha256": sha(__file__),
            "recorder_sha256": sha(Path(__file__).with_name("process_evidence.py")),
            "owned_process_sha256": sha(Path(__file__).with_name("owned_process.py")),
            "debugger_sha256": sha(Path(__file__).with_name("win_debugging.py")),
        }
        record, _ = run_recorded(
            case,
            "native",
            [manifest["official_cli"]["path"], *argv],
            cwd=manifest["historical_cwd"],
            env=env,
            timeout=120,
            report={"path": str(report), "kind": "xml" if "kicadxml" in argv else "erc"},
            inputs=inputs,
            binding=binding,
            role="official-native-cli",
            debug=experiment["debug"],
            stdio=experiment["stdio"],
        )
        row = dict(
            experiment,
            status=record["status"],
            process=record,
            observation="not_reproduced_in_this_control"
            if record["status"] == "passed"
            else "failed_native_control",
            method_after=files([__file__, Path(__file__).with_name("process_evidence.py")]),
        )
        rows.append(row)
        write(case / "experiment.json", row)
        write(root / "progress.json", rows)
    result = {
        "status": "controls_recorded",
        "cases": rows,
        "capsule": str(capsule),
        "historical_failure": manifest["failure_event"],
        "fidelity_gaps": manifest["gaps"],
        "root_cause_confirmed": False,
        "historical_fault_resolved": False,
        "scope": (
            "Failures/timeouts/missing reports retained. Local dumps; no global settings changed."
        ),
    }
    write(root / "result.json", result)
    print(
        json.dumps(
            {
                "exits": [r.get("process", {}).get("exit") for r in rows],
                "historical_fault_resolved": False,
                "output": str(root),
            }
        )
    )


if __name__ == "__main__":
    main()
