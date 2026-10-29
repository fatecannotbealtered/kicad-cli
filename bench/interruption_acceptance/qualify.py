"""Finite harmless qualification; each round is frozen before any fixture starts."""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from controller import now, put, run, sha

HOST = Path("D:/environment/Python/Python312/python.exe")
HERE = Path(__file__).resolve().parent
CASES = [
    ("saved_committed", True),
    ("missing_save", False),
    ("missing_receipt", False),
    ("uncommitted_receipt", False),
    ("wrong_receipt_operation", False),
    ("wrong_stdout_operation", False),
    ("no_stdout", False),
    ("nonmatching_stdout", False),
    ("timeout", False),
    ("multithread", True),
    ("prior_stdout", False),
    ("handled_exception", True),
    ("child_live", True),
    ("explicit_kernelbase", True),
    ("helper_tree", True),
    ("safety_failfast", False),
    ("safety_second_chance", False),
    ("reused_stdout_file", False),
]


def witness(directory, operation, mode):
    """Read only this case's own exact saved file and committed SQLite view."""
    row = {
        "operation_id": operation,
        "witness_at": now(),
        "read_only": True,
        "output_saved": False,
        "receipt_committed": False,
        "qualified": False,
    }
    project = directory / "own-project.json"
    if project.is_file():
        raw = project.read_bytes()
        data = json.loads(raw)
        row.update(
            output_saved=data
            == {"fixture": "owned harmless project", "operation_id": operation, "saved": True},
            output_sha256=hashlib.sha256(raw).hexdigest(),
        )
    database = directory / "receipt.sqlite3"
    if database.is_file():
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=0)
        try:
            connection.execute("PRAGMA query_only=ON")
            matches = connection.execute(
                "SELECT operation_id,status FROM receipts WHERE operation_id=?", (operation,)
            ).fetchall()
            row.update(
                receipt_committed=matches == [(operation, "succeeded")],
                exact_receipt_rows=len(matches),
                sqlite_open="mode=ro; query_only=ON",
            )
        finally:
            connection.close()
    if mode == "handled_exception":
        handler = directory / "application-handler.json"
        row["application_handler_invoked"] = handler.is_file() and json.loads(
            handler.read_text(encoding="utf-8")
        ) == {"handler_invocations": 1}
    row["qualified"] = bool(
        row["output_saved"]
        and row["receipt_committed"]
        and row.get("application_handler_invoked", True)
    )
    return row


def predicate(raw, operation):
    try:
        value = json.loads(raw)
        return (
            type(value) is dict
            and value.get("ok") is True
            and value.get("schema_version") == "1.0"
            and value.get("data", {}).get("operation_id") == operation
        )
    except (ValueError, UnicodeError, AttributeError):
        return False


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Supply one NEW round output directory")
    output = Path(sys.argv[1]).resolve()
    output.mkdir(exist_ok=False)
    sources = {
        name: sha(HERE / name)
        for name in ("controller.py", "fixture.py", "qualify.py", "METHOD.md")
    }
    freeze = {
        "at": now(),
        "method_revision": "hardware-writefile-v4-object-and-safety",
        "baseline": "b67eb6e65037d1c0af2bf142777e3dac1534ca6b",
        "source_sha256": sources,
        "host": str(HOST),
        "host_sha256": sha(HOST),
        "expected": [
            {"mode": mode, "qualified_interruption": expected} for mode, expected in CASES
        ],
        "stage": "saved exact own output + exact committed receipt + stdout prior bytes zero",
        "scope": "harmless method qualification only; no real product acceptance",
        "maximum_fixture_seconds": 12,
        "timeout_case_seconds": 2,
        "pyc_policy": "-B prevents writes; -S plus new empty pycache_prefix redirects reads",
    }
    put(output / "FROZEN.json", freeze)
    protector = output / "protected"
    protector.mkdir()
    marker = protector / "protected-object.txt"
    marker.write_text("Preserve this new external object exactly.\n", encoding="utf-8")
    marker_before = sha(marker)
    protector_cache = protector / "empty-pycache"
    protector_cache.mkdir()
    sentinel = subprocess.Popen(
        [
            str(HOST),
            "-B",
            "-S",
            "-X",
            "pycache_prefix=" + str(protector_cache),
            str(HERE / "fixture.py"),
            "sentinel",
            str(protector),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=0x08000000,
    )
    summaries = []
    try:
        deadline = time.monotonic() + 5
        while not (protector / "ready").exists():
            if time.monotonic() > deadline or sentinel.poll() is not None:
                raise RuntimeError("Protected sentinel failed to become ready")
            time.sleep(0.01)
        for index, (mode, expected) in enumerate(CASES):
            case = output / (f"{index + 1:02d}-" + mode)
            case.mkdir()
            owned, cache = case / "own-work", case / "empty-pycache"
            owned.mkdir()
            cache.mkdir()
            operation = "fixture-round-" + output.name + "-" + str(index + 1)
            env = {
                key: val
                for key, val in os.environ.items()
                if not key.upper().startswith(("PYTHON", "KICAD_CLI"))
            }
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            argv = [
                str(HOST),
                "-B",
                "-S",
                "-X",
                "pycache_prefix=" + str(cache),
                str(HERE / "fixture.py"),
                mode,
                str(owned),
                operation,
            ]
            result = run(
                argv,
                owned,
                env,
                case / "observation",
                predicate=lambda raw, op=operation: predicate(raw, op),
                stage_witness=lambda boundary, d=owned, op=operation, m=mode: witness(d, op, m),
                timeout=2 if mode == "timeout" else 12,
                protected_handles=[int(sentinel._handle)],
                allow_owned_termination=True,
            )
            checks = {
                "expected_boundary_outcome": result["qualified_interruption"] is expected,
                "no_controller_failure": "controller_failure" not in result,
                "complete_exit_events": result.get("complete_exit_debug_events") is True,
                "all_process_handles_signaled": bool(result["process_handle_finalization"])
                and all(
                    row["duplicate_handle_signaled"]
                    for row in result["process_handle_finalization"]
                ),
                "empty_job": result.get("job_final_active_count") == 0
                and result.get("job_final_member_ids") == [],
                "protected_process_survived": sentinel.poll() is None,
                "protected_file_preserved": sha(marker) == marker_before,
                "no_pycache_files": not list(cache.rglob("*.pyc")),
            }
            if expected:
                checks["zero_caller_bytes"] = result["caller_stdout_bytes"] == 0
                checks["actual_terminated_exit"] = result["exit_proof_consistent"]
            if mode == "timeout":
                checks["timeout_not_a_boundary"] = result["timed_out"] and not result["matched"]
            else:
                checks["not_timeout"] = result["timed_out"] is False
            if mode in (
                "missing_save",
                "missing_receipt",
                "uncommitted_receipt",
                "wrong_receipt_operation",
            ):
                checks["stage_refusal_observed"] = bool(result["candidate_boundaries"]) and all(
                    boundary["stage"]["qualified"] is False
                    for boundary in result["candidate_boundaries"]
                )
                checks["not_terminated_for_stage_mismatch"] = not result["termination_requested"]
            if mode == "prior_stdout":
                checks["prior_bytes_refusal_observed"] = bool(result["candidate_boundaries"]) and (
                    result["candidate_boundaries"][0]["stdout_prior_bytes"] > 0
                )
            if mode == "multithread":
                checks["file_write_hits_observed"] = result["non_stdout_writefile_hits"] >= 40
                checks["worker_stdout_observed"] = bool(result["candidate_boundaries"]) and (
                    result["candidate_boundaries"][0]["tid"] != result["events"][0]["tid"]
                )
                checks["all_created_threads_armed"] = (
                    sum(e["code"] == 2 for e in result["events"]) >= 4
                )
            if mode == "child_live":
                checks["live_child_owned_at_stop"] = (
                    len(result.get("termination_snapshot", {}).get("members", [])) >= 2
                )
            if mode == "helper_tree":
                cmd = [
                    row
                    for row in result["processes"]
                    if Path(row["identity"]["image"]).name.lower() == "cmd.exe"
                ]
                checks["cmd_naturally_exited_before_boundary"] = (
                    len(cmd) == 1 and cmd[0]["exit_code"] == 0
                )
                checks["system_conhost_observed"] = any(
                    Path(row["identity"]["image"]).name.lower() == "conhost.exe"
                    for row in result["processes"]
                )
            if mode.startswith("safety_"):
                checks["safety_stop_not_a_boundary"] = (
                    bool(result.get("safety_stop")) and not result["matched"]
                )
                checks["cleanup_terminated"] = (
                    result["termination_requested"] and result["caller_stdout_bytes"] == 0
                )
            if mode == "reused_stdout_file":
                checks["numeric_handle_reuse_exercised"] = json.loads(
                    (owned / "reused-handle.json").read_text(encoding="utf-8")
                ) == {"numeric_stdout_handle_reused": True}
                checks["different_kernel_object_rejected"] = any(
                    not row["same_kernel_object"]
                    for row in result.get("stdout_object_comparisons", [])
                )
                checks["file_json_not_stdout_boundary"] = (
                    not result["candidate_boundaries"]
                    and not result["termination_requested"]
                    and result["caller_stdout_bytes"] == 0
                )
            if mode == "handled_exception":
                checks["application_exception_passed_back"] = any(
                    row["code"] == "0xe0424444" and row["continuation"] == "0x80010001"
                    for row in result["application_exceptions"]
                )
            summaries.append(
                {
                    "mode": mode,
                    "expected_interruption": expected,
                    "qualified_interruption": result["qualified_interruption"],
                    "checks": checks,
                    "passed": all(checks.values()),
                    "result": str(case / "observation" / "result.json"),
                    "result_sha256": sha(case / "observation" / "result.json"),
                    "caller_stdout_bytes": result["caller_stdout_bytes"],
                    "controller_failure": result.get("controller_failure"),
                }
            )
            print(
                json.dumps(
                    {
                        "mode": mode,
                        "passed": summaries[-1]["passed"],
                        "qualified_interruption": result["qualified_interruption"],
                    }
                ),
                flush=True,
            )
    finally:
        (protector / "release").write_text("finish naturally", encoding="ascii")
        sentinel.wait(timeout=10)
        final_file_preserved = sha(marker) == marker_before
        sources_unchanged = all(sha(HERE / name) == digest for name, digest in sources.items())
        overall_passed = bool(
            len(summaries) == len(CASES)
            and all(row["passed"] for row in summaries)
            and sentinel.returncode == 0
            and final_file_preserved
            and sources_unchanged
        )
        put(
            output / "SUMMARY.json",
            {
                "at": now(),
                "all_passed": overall_passed,
                "cases": summaries,
                "protected_sentinel_natural_exit": sentinel.returncode,
                "protected_file_sha256_unchanged": final_file_preserved,
                "sources_unchanged": sources_unchanged,
                "real_product_qualified": False,
            },
        )
    return 0 if overall_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
