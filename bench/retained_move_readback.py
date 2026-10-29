"""Finish read-only assertions on retained source moves; never replay confirmation.

Run after the serial source suite ends. Default is plan-only. Source inputs,
receipts, and the installed baseline are hash-bound before making fresh copies.
Only pcbnew_oracle.py may start a new native process, once per copied board.
Raw records are private; export a separate allowlisted summary.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import sys
import traceback
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def put(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def document(path):
    text = path.read_text(encoding="utf-8").lstrip()
    value, end = json.JSONDecoder().raw_decode(text)
    trailing = text[end:].strip()
    if trailing and not (
        path.parent.name.startswith("sdk-")
        and all(
            line.startswith("swig/python detected a memory leak") for line in trailing.splitlines()
        )
    ):
        raise ValueError("Unexpected content after original JSON")
    if not isinstance(value, dict):
        raise ValueError("Original response must be an object")
    record = json.loads(path.with_name("process.call.json").read_bytes())
    job = record["job_finalization"]
    if (
        record["stdout_sha256"] != sha(path)
        or record["stderr_sha256"] != sha(path.with_name("process.stderr"))
        or not job["natural_tree_complete"]
        or job["controlled_termination"]
        or job["after_cleanup"]["members"]
        or record["program"] != record["program_after"]
    ):
        raise ValueError("Original response/tree binding differs")
    return value, record


def files(folder):
    return {p.relative_to(folder).as_posix(): sha(p) for p in folder.rglob("*") if p.is_file()}


def copied_project(board, destination):
    destination.mkdir(parents=True, exist_ok=False)
    for path in board.parent.glob(board.stem + ".kicad_*"):
        if path.suffix in {".kicad_pcb", ".kicad_pro", ".kicad_prl", ".kicad_dru"}:
            shutil.copyfile(path, destination / path.name)
    return destination / board.name


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
    if out.is_relative_to(retained) or out.is_relative_to(source):
        raise ValueError("Choose a new output outside retained evidence and source")
    terminal = retained / "FINISH.json"
    finish = json.loads(terminal.read_bytes()) if terminal.exists() else None
    start = json.loads((retained / "START.json").read_bytes())
    if finish is None and args.execute:
        raise ValueError("Wait for the serial source suite terminal before native readback")
    if finish is not None and (
        finish["exit"] not in (0, 1)
        or not finish["job_finalization"]["natural_tree_complete"]
        or (retained / "NATIVE-STOP.json").exists()
    ):
        raise ValueError("Original source cycle must have ended naturally without a native stop")
    out.mkdir(parents=True, exist_ok=False)
    for key in tuple(os.environ):
        if key.startswith(("KICAD", "PYTHON")) or key == "KIPRJMOD":
            del os.environ[key]
    native_python = args.native_root.resolve() / "bin/python.exe"
    os.environ.update(
        KICAD_CLI_ROOT=str(args.native_root.resolve()),
        KICAD_CLI_PYTHON=str(native_python),
        PYTHONIOENCODING="utf-8",
        PYTHONDONTWRITEBYTECODE="1",
    )
    sys.path[:0] = [str(source / "tests"), str(source)]
    import test_native_move as checks

    failures = [
        json.loads(line)
        for line in (retained / "PRIVATE-FAILURES.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    failed_ids = {
        f["nodeid"]
        for f in failures
        if f["nodeid"].startswith(
            "tests/test_native_move.py::test_the_native_move_does_exactly_what_the_payload_did["
        )
    }
    cases, originals = [], {}
    for folder in sorted((retained / "tmp").glob("test_the_native_move_does_exac*")):
        if not folder.name[-1].isdigit():
            continue
        candidates = list((folder / "native").glob("*.kicad_pcb"))
        if len(candidates) != 1:
            continue
        board = candidates[0]
        case_id = (
            "tests/test_native_move.py::test_the_native_move_does_exactly_what_the_payload_did["
            + board.name
            + "]"
        )
        if case_id not in failed_ids:
            continue
        old_paths = sorted(folder.glob("native-evidence/sdk-pcb_place-*/process.stdout"))
        new_paths = sorted(folder.glob("native-evidence/public-read-move-*/process.stdout"))
        if len(old_paths) != 2 or len(new_paths) != 2:
            raise ValueError("Exactly one original preview and confirmation on each side required")
        old = [document(p)[0] for p in old_paths]
        new = [document(p)[0] for p in new_paths]
        receipt = new[1]["data"]["receipt"]
        before, after = (
            receipt["snapshot_before"]["revision"],
            receipt["snapshot_after"]["revision"],
        )
        if (
            sha(board) != after
            or Path(receipt["snapshot_after"]["extensions"]["path"]).resolve() != board.resolve()
        ):
            raise ValueError("Saved public target drifted or belongs to a different case")
        matches = [p for p in checks.DEMO_BOARDS if p.name == board.name and sha(p) == before]
        if len(matches) != 1:
            raise ValueError("Exact original board bytes are unavailable or ambiguous")
        baseline = matches[0]
        expected_moves, _ = checks.moves_for(baseline)
        for paths in (old_paths, new_paths):
            command = document(paths[0])[1]["command"]
            if command[command.index("--moves") + 1] != expected_moves:
                raise ValueError("Retained request differs from frozen baseline-derived moves")
        original_native = document(old_paths[0])[1]["program"][0]
        if Path(original_native["path"]).resolve() != native_python or original_native[
            "sha256"
        ] != sha(native_python):
            raise ValueError("Native interpreter differs from retained reference")
        row = {
            "id": case_id,
            "fixture": folder.name,
            "original_board": str(baseline),
            "before_sha256": before,
            "after_sha256": after,
            "payload_sha256": sha(folder / "payload" / board.name),
            "operation_id": receipt["operation_id"],
            "moves": expected_moves,
            "response_files": {str(p): sha(p) for p in old_paths + new_paths},
            "call_records": {
                str(p.with_name("process.call.json")): sha(p.with_name("process.call.json"))
                for p in old_paths + new_paths
            },
        }
        originals[case_id] = (folder, baseline, old, new)
        cases.append(row)
    if {row["id"] for row in cases} != failed_ids or not cases:
        raise ValueError("Retained case set is incomplete")
    methods = [
        "tests/test_native_move.py",
        "tests/pcbnew_oracle.py",
        "tests/native_process.py",
        "bench/retained_move_readback.py",
    ]
    plan = {
        "kind": "Read-only completion of preserved source moves; no confirmation replay",
        "original_source_commit": start["source_commit"],
        "original_finish_sha256": sha(terminal) if finish is not None else None,
        "provisional_until_original_terminal": finish is None,
        "method_files_sha256": {name: sha(source / name) for name in methods},
        "native_python_sha256": sha(native_python),
        "cases": cases,
        "expected_new_native_reads": 3 * len(cases),
        "confirmation_replays": 0,
        "configuration_seed_sha256": files(args.configuration_seed),
    }
    put(out / "PLAN.json", plan)
    if not args.execute:
        print(json.dumps({"status": "plan_only", "cases": len(cases)}))
        return 0
    configuration = out / "configuration"
    shutil.copytree(args.configuration_seed, configuration)
    common = configuration / "10.0/kicad_common.json"
    if common.is_file():
        value = json.loads(common.read_bytes())
        value.setdefault("system", {})["working_dir"] = str(out)
        common.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.environ["KICAD_CONFIG_HOME"] = str(configuration)
    results = []
    try:
        for number, row in enumerate(cases):
            folder, baseline, old, new = originals[row["id"]]
            stage = out / f"case-{number:02d}"
            paths = {
                "baseline": copied_project(baseline, stage / "baseline"),
                "payload": copied_project(folder / "payload" / baseline.name, stage / "payload"),
                "native": copied_project(folder / "native" / baseline.name, stage / "native"),
            }
            frozen = {side: files(path.parent) for side, path in paths.items()}
            os.environ.update(
                KICAD_CLI_NATIVE_TEST_ROOT=str(stage),
                KICAD_CLI_NATIVE_TEST_EVIDENCE=str(stage / "native-evidence"),
            )
            reader = checks.pcbnew_reads
            read_counts = {}

            def read_once(path, paths=paths, read_counts=read_counts, reader=reader):
                if path not in paths.values() or path in read_counts:
                    raise ValueError("Unexpected or repeated native read")
                read_counts[path] = 1
                return reader(path)

            def copied(_original, destination, paths=paths):
                assert _original == paths["baseline"]
                return paths[destination.name]

            def captured(which, board, moves, paths=paths, row=row, old=old, new=new):
                assert board == paths[which] and moves == row["moves"]
                return copy.deepcopy(old if which == "payload" else new)

            def forbidden(*_args, **_kwargs):
                raise RuntimeError("A retained readback cannot call a mutating entry")

            saved = {
                name: getattr(checks, name)
                for name in (
                    "copy_project",
                    "payload_move",
                    "native_move",
                    "pcbnew_reads",
                    "native",
                    "sdk_payload",
                )
            }
            try:
                checks.copy_project = copied
                checks.payload_move = lambda board, moves, capture=captured: capture(
                    "payload", board, moves
                )
                checks.native_move = lambda board, moves, capture=captured: capture(
                    "native", board, moves
                )
                checks.pcbnew_reads = read_once
                checks.native = checks.sdk_payload = forbidden
                checks.test_the_native_move_does_exactly_what_the_payload_did(
                    paths["baseline"], stage
                )
            finally:
                for name, value in saved.items():
                    setattr(checks, name, value)
            assert len(read_counts) == 3
            assert frozen == {side: files(path.parent) for side, path in paths.items()}
            assert sha(baseline) == row["before_sha256"]
            assert sha(folder / "native" / baseline.name) == row["after_sha256"]
            assert sha(folder / "payload" / baseline.name) == row["payload_sha256"]
            assert all(sha(p) == digest for p, digest in row["response_files"].items())
            result = {
                "id": row["id"],
                "status": "passed",
                "native_reads": 3,
                "copies_unchanged": True,
                "original_saved_files_unchanged": True,
            }
            results.append(result)
            put(stage / "RESULT.json", result)
        assert plan["method_files_sha256"] == {name: sha(source / name) for name in methods}
        put(
            out / "RESULT.json",
            {
                "status": "passed",
                "cases": results,
                "plan_sha256": sha(out / "PLAN.json"),
                "confirmation_replays": 0,
            },
        )
    except BaseException:
        put(
            out / "PRIVATE-FAILURE.json",
            {"completed": results, "traceback": traceback.format_exc()},
        )
        raise
    print(
        json.dumps(
            {
                "status": "passed",
                "cases": len(results),
                "native_reads": 3 * len(results),
                "confirmation_replays": 0,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
