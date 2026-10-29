"""Complete two private repair comparisons without replaying saved SDK writes.

Default is plan-only. Execution waits for the original full-source terminal.
One new native DRC reference query and one private-domain repair cycle per case
use fresh copies. The historical routing vector is explicitly fixed test data;
its original producer exit was not retained and is never backfilled.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import io
import json
import os
import shutil
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

from bench.retained_move_readback import copied_project, document, files, put, sha


class Capture(io.StringIO):
    def __init__(self):
        super().__init__()
        self.records = []

    def readouterr(self):
        value = self.getvalue()
        self.records.append(value)
        self.seek(0)
        self.truncate(0)
        return SimpleNamespace(out=value, err="")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--native-root", type=Path, required=True)
    parser.add_argument("--configuration-seed", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    source, retained, out = args.source.resolve(), args.run.resolve(), args.out.resolve()
    if out.is_relative_to(source) or out.is_relative_to(retained):
        raise ValueError("Choose new output outside source and retained evidence")
    terminal = retained / "FINISH.json"
    finish = json.loads(terminal.read_bytes()) if terminal.exists() else None
    if args.execute and finish is None:
        raise ValueError("Wait for the original serial source cycle")
    if finish is not None and (
        finish["exit"] not in (0, 1)
        or not finish["job_finalization"]["natural_tree_complete"]
        or (retained / "NATIVE-STOP.json").exists()
    ):
        raise ValueError("Original cycle did not finish naturally without a native stop")
    out.mkdir(parents=True, exist_ok=False)
    for key in tuple(os.environ):
        if key.startswith(("KICAD", "PYTHON")) or key == "KIPRJMOD":
            del os.environ[key]
    native_root = args.native_root.resolve()
    os.environ.update(
        KICAD_CLI_ROOT=str(native_root),
        KICAD_CLI_PYTHON=str(native_root / "bin/python.exe"),
        PYTHONIOENCODING="utf-8",
        PYTHONDONTWRITEBYTECODE="1",
    )
    sys.path[:0] = [str(source / "tests"), str(source)]
    import pytest
    import test_native_route as checks

    failures = [
        json.loads(line)
        for line in (retained / "PRIVATE-FAILURES.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    required = {
        value["nodeid"]
        for value in failures
        if value["nodeid"].startswith(
            "tests/test_native_route.py::test_repair_routes_as_the_payload_routed["
        )
    }
    cases, captured = [], {}
    for folder in sorted((retained / "tmp").glob("test_repair_routes_as_the_payl*")):
        if not folder.name[-1].isdigit():
            continue
        boards = list((folder / "base").glob("*.kicad_pcb"))
        if len(boards) != 1:
            continue
        board = boards[0]
        case_id = (
            "tests/test_native_route.py::test_repair_routes_as_the_payload_routed["
            + board.stem
            + "]"
        )
        if case_id not in required:
            continue
        paths = sorted(folder.glob("native-evidence/sdk-pcb_route-*/process.stdout"))
        if len(paths) != 2:
            raise ValueError("Exactly one original SDK preview/confirmation required")
        old = [document(path)[0] for path in paths]
        if not old[1]["ok"] or sha(board) != sha(folder / "native" / board.name):
            raise ValueError("Original saved SDK side or unapplied port side differs")
        row = {
            "id": case_id,
            "fixture": folder.name,
            "board": board.name,
            "base_sha256": sha(board),
            "saved_sdk_sha256": sha(folder / "payload" / board.name),
            "fixed_vector_sha256": sha(folder / "kicad-report.json"),
            "original_sdk_responses": {str(path): sha(path) for path in paths},
            "original_drc_producer_exit": "not_retained; not claimed successful",
        }
        cases.append(row)
        captured[case_id] = (folder, old)
    if {row["id"] for row in cases} != required or not cases:
        raise ValueError("Missing exact retained repair cases")
    methods = [
        "tests/test_native_route.py",
        "tests/native_process.py",
        "bench/retained_route_compare.py",
        "bench/retained_move_readback.py",
    ]
    plan = {
        "kind": "Private source algorithm comparison with a fixed routing vector",
        "cases": cases,
        "provisional_until_original_terminal": finish is None,
        "method_files_sha256": {name: sha(source / name) for name in methods},
        "original_finish_sha256": sha(terminal) if finish is not None else None,
        "expected_new_native_drc_queries": len(cases),
        "expected_new_private_domain_cycles": len(cases),
        "original_confirmation_replays": 0,
        "public_exe_acceptance": False,
    }
    put(out / "PLAN.json", plan)
    if not args.execute:
        print(json.dumps({"status": "plan_only", "cases": len(cases)}))
        return 0
    configuration = out / "configuration"
    shutil.copytree(args.configuration_seed, configuration)
    common = configuration / "10.0/kicad_common.json"
    if common.exists():
        value = json.loads(common.read_bytes())
        value.setdefault("system", {})["working_dir"] = str(out)
        common.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.environ["KICAD_CONFIG_HOME"] = str(configuration)
    results = []
    try:
        for number, row in enumerate(cases):
            folder, old = captured[row["id"]]
            stage = out / f"case-{number:02d}"
            stage.mkdir()
            os.environ.update(
                KICAD_CLI_NATIVE_TEST_ROOT=str(stage),
                KICAD_CLI_NATIVE_TEST_EVIDENCE=str(stage / "native-evidence"),
            )
            original_hashes = files(folder)
            transcript, stderr = Capture(), io.StringIO()
            original_report, original_compare = checks.kicads_report, checks.same_report
            original_payload = folder / "payload" / row["board"]

            def damaged(_board, destination, folder=folder, row=row):
                result = copied_project(folder / "base" / row["board"], destination)
                assert sha(result) == row["base_sha256"]
                return result

            def reference(
                board,
                destination,
                folder=folder,
                stage=stage,
                original_report=original_report,
                row=row,
            ):
                fresh = stage / "current-native-reference"
                fresh.mkdir()
                before = sha(board)
                current = original_report(board, fresh)
                assert sha(board) == before == row["base_sha256"]
                fixed = destination / "fixed-retained-routing-vector.json"
                shutil.copyfile(folder / "kicad-report.json", fixed)
                assert sha(fixed) == row["fixed_vector_sha256"]
                put(
                    stage / "REFERENCE.json",
                    {
                        "new_native_report_sha256": sha(current),
                        "fixed_router_input_sha256": sha(fixed),
                        "same_vector_assumed": False,
                    },
                )
                return fixed

            def payload(board, argv, row=row, old=old, original_payload=original_payload):
                assert argv == ["--mode", "repair"] and sha(board) == row["base_sha256"]
                shutil.copyfile(original_payload, board)
                assert sha(board) == row["saved_sdk_sha256"]
                return copy.deepcopy(old)

            def compared(
                done,
                new_done,
                theirs,
                ours,
                original_compare=original_compare,
                original_payload=original_payload,
            ):
                assert sha(theirs) == sha(original_payload)
                return original_compare(done, new_done, original_payload, ours)

            try:
                with pytest.MonkeyPatch.context() as patch:
                    patch.setattr(checks, "damaged", damaged)
                    patch.setattr(checks, "kicads_report", reference)
                    patch.setattr(checks, "payload_route", payload)
                    patch.setattr(checks, "same_report", compared)
                    with contextlib.redirect_stdout(transcript), contextlib.redirect_stderr(stderr):
                        checks.test_repair_routes_as_the_payload_routed(
                            folder / "base" / row["board"], stage, patch, transcript
                        )
            finally:
                put(
                    stage / "PRIVATE-TRANSCRIPT.json",
                    {
                        "records": transcript.records,
                        "remaining_stdout": transcript.getvalue(),
                        "stderr": stderr.getvalue(),
                    },
                )
                put(stage / "AFTER-FILES.json", files(stage))
            assert original_hashes == files(folder)
            result = {
                "id": row["id"],
                "status": "passed",
                "new_native_drc_queries": 1,
                "new_private_domain_cycles": 1,
                "original_confirmation_replays": 0,
                "original_files_unchanged": True,
            }
            results.append(result)
            put(stage / "RESULT.json", result)
        assert plan["method_files_sha256"] == {name: sha(source / name) for name in methods}
        put(
            out / "RESULT.json",
            {"status": "passed", "cases": results, "plan_sha256": sha(out / "PLAN.json")},
        )
    except BaseException:
        put(
            out / "PRIVATE-FAILURE.json",
            {"completed": results, "traceback": traceback.format_exc()},
        )
        raise
    print(
        json.dumps({"status": "passed", "cases": len(results), "original_confirmation_replays": 0})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
