"""One bounded, externally controlled final-EXE lost-reply attempt.

No product hook, source monkeypatch, automatic retry, old fault harness, or old
operation store. The final artifact and ordinary fixed R4 preview are declared
by the v3 controller. An undrained stdout pipe provides the narrow opportunity;
failure to hit the durable-success/incomplete-reply window is a retained failure,
not a skip or an invitation to rerun. Only this exact owned Job can be stopped.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest
from move_safety_transport import windows_argv
from test_move_safety_exe import (
    digest,
    files,
    fresh_preview,
    native_calls,
    operation,
    pose_rows,
    protected_bytes,
    store_rows,
)
from test_move_safety_exe import safety_scene as safety_scene

REPO = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Owned Windows final-EXE reply loss")


def _put(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _metadata(output, root, writer, env):
    """One separately owned, recorded read of exact root/child command lines."""
    sys.path.insert(0, str(REPO / "bench"))
    from bench.process_evidence import run_recorded

    script = (
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
        f'Get-CimInstance Win32_Process -Filter "ProcessId = {root} OR ProcessId = {writer}" | '
        "Select-Object ProcessId,ParentProcessId,CreationDate,ExecutablePath,CommandLine | "
        "ConvertTo-Json -Compress"
    )
    record, _ = run_recorded(
        output,
        "exact-command-metadata",
        [
            str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        cwd=output,
        env=env,
        timeout=20,
        role="owned-exact-pid-read-only-metadata",
    )
    assert record["execution"] == "completed" and record["exit"] == 0, record
    assert record["job_finalization"]["natural_tree_complete"] is True
    rows = json.loads((output / "exact-command-metadata.stdout").read_bytes())
    assert isinstance(rows, list) and {r["ProcessId"] for r in rows} == {root, writer}
    return {r["ProcessId"]: r for r in rows}


def test_durable_receipt_lost_reply_is_queryable_without_second_write(
    safety_scene, record_property
):
    """F1: real public EXE save/receipt, external pipe loss, exact public query."""
    from bench.persistence_acceptance.win32_control import OwnedProcess

    assert os.environ.get("KICAD_CLI_MOVE_REPLYLOSS_CONTROL") == "reviewed-single-attempt", (
        "The frozen matrix must explicitly authorize this one owned-Job stop"
    )
    s = safety_scene
    original, raw = files(s.root), s.board.read_bytes()
    data = fresh_preview(s)
    plan, preflight = data["preview"], data["preview"]["extensions"]["preflight"]
    identifier = plan["operation_id"]
    output = s.root.parent / "owned-reply-loss"
    output.mkdir()
    binary = Path(os.environ["KICAD_CLI_TEST_BINARY"]).resolve(strict=True)
    expected_sha = os.environ["KICAD_CLI_MOVE_SAFETY_BINARY_SHA256"]
    assert digest(binary) == expected_sha
    command = [
        str(binary),
        *s.args(),
        "--confirm",
        data["confirm_token"],
        "--idempotency-key",
        "owned-reply-loss",
        "--compact",
    ]
    _put(
        output / "START.json",
        {
            "kind": "final-exe-controlled-lost-reply-single-attempt",
            "command": command,
            "cwd": str(s.root.parent),
            "operation_id": identifier,
            "artifact_sha256": expected_sha,
            "source_files": original,
            "candidate_sha256": preflight["candidate_sha256"],
            "state": str(s.state),
            "scope": str(s.scope),
            "stop_authority": (
                "one caller-owned Job, after exact image/command/birth/membership checks"
            ),
            "retry_policy": "never; absent stage or complete delivered reply is not a passing hit",
        },
    )
    native_before = native_calls(s)
    control = OwnedProcess(
        command,
        s.root.parent,
        s.env,
        output / "confirm.stdout",
        output / "confirm.stderr",
        pipe=True,
        allow_control=True,
    )
    hit, frozen, failure, durable, metadata, writer_identity, stop = (
        False,
        False,
        None,
        None,
        None,
        None,
        None,
    )
    partial = bytearray()
    try:
        deadline = time.monotonic() + 120
        while control.running() and time.monotonic() < deadline:
            # Only the explicitly registered store/operation created above is read.
            row = operation(store_rows(s.state), identifier)
            if control.pipe_available() and row["status"] == "succeeded" and row["receipt"]:
                writer = json.loads(row["extra"])["writer_pid"]
                writer_identity = control.identify(writer)
                assert writer_identity["owned_job_member"] is True
                assert Path(writer_identity["image"]).resolve() == binary
                assert digest(writer_identity["image"]) == expected_sha
                control.freeze(writer, binary, expected_birth=writer_identity["creation_filetime"])
                frozen = control.debug_event is not None
                assert frozen and control.debug_event.code == 3, "Missing initial attach event"
                metadata = _metadata(output, control.pid, writer, s.env)
                for pid in (control.pid, writer):
                    actual = windows_argv(metadata[pid]["CommandLine"])
                    assert Path(actual[0]).resolve() == binary and actual[1:] == command[1:]
                    assert Path(metadata[pid]["ExecutablePath"]).resolve() == binary
                assert writer != control.pid and metadata[writer]["ParentProcessId"] == control.pid
                assert (
                    control.identify(control.pid)["creation_filetime"] == control.creation_filetime
                )
                assert (
                    control.identify(writer)["creation_filetime"]
                    == writer_identity["creation_filetime"]
                )
                owned_tree = control.tree_snapshot()
                assert owned_tree["all_members_attributed"] is True
                assert {p["pid"] for p in owned_tree["members"]} == {control.pid, writer}
                assert all(
                    Path(p["image"]).resolve() == binary and digest(p["image"]) == expected_sha
                    for p in owned_tree["members"]
                ), "Unexpected consumer in pure confirmation; stop, retain and do not retry"
                durable = operation(store_rows(s.state), identifier)
                receipt = json.loads(durable["receipt"])
                assert durable["status"] == "succeeded" and durable["resolution"] is None
                assert (
                    receipt["operation_id"] == identifier and receipt["plan_id"] == plan["plan_id"]
                )
                assert receipt["outcome"]["execution"] == "completed"
                assert receipt["outcome"]["persistence"] == "saved"
                assert digest(s.board) == preflight["candidate_sha256"]
                assert not list(s.root.glob("*.kicad-cli.*"))
                assert control.pipe_available() > 0
                _put(
                    output / "FROZEN-WITNESS.json",
                    {
                        "operation_id": identifier,
                        "owned_root_pid": control.pid,
                        "owned_root_birth": control.creation_filetime,
                        "writer": writer_identity,
                        "complete_owned_job": owned_tree,
                        "exact_process_metadata": metadata,
                        "pipe_available": control.pipe_available(),
                        "candidate_sha256": preflight["candidate_sha256"],
                        "saved_sha256": digest(s.board),
                        "durable_operation": durable,
                        "engineering_files": files(s.root),
                        "events": control.events,
                    },
                )
                # Receipt is durable and all checks are frozen before stopping the
                # sole authorized owned Job. No application exception is consumed
                # to make this stage look successful.
                stop = control.stop()
                hit = True
                break
            time.sleep(0.002)
        if not hit:
            failure = (
                "declared-stage-not-hit" if not control.running() else "bounded-observation-timeout"
            )
            if control.running():
                stop = control.stop()
            elif control.exit_code() >= 0xC0000000:
                failure = "spontaneous-exception-exit-stop-cycle"
        while control.pipe_available():
            partial.extend(os.read(control.pipe_fd, control.pipe_available()))
        (output / "confirm.stdout").write_bytes(partial)
        try:
            json.loads(partial)
        except (ValueError, UnicodeError):
            complete_reply = False
        else:
            complete_reply = True
            hit, failure = False, "complete-reply-already-delivered"
        _put(
            output / "CONTROL-FINISH.json",
            {
                "hit": hit,
                "failure": failure,
                "frozen": frozen,
                "stop": stop,
                "partial_stdout_bytes": len(partial),
                "complete_reply_delivered": complete_reply,
                "events": control.events,
                "automatic_retry": False,
            },
        )
    except BaseException as exc:
        # Preserve a classification/protection/metadata failure even if it occurs
        # before the planned reply-loss window. Cleanup is limited to this Job;
        # it never supplies a passing stage witness or authorizes another try.
        if control.running() or control.debug_event is not None:
            stop = control.stop()
        if control.pipe_fd is not None:
            while control.pipe_available():
                partial.extend(os.read(control.pipe_fd, control.pipe_available()))
        if not (output / "CONTROL-FINISH.json").exists():
            _put(
                output / "CONTROL-FINISH.json",
                {
                    "hit": False,
                    "failure": type(exc).__name__ + ": " + str(exc),
                    "frozen": frozen,
                    "stop": stop,
                    "events": control.events,
                    "automatic_retry": False,
                },
            )
        raise
    finally:
        finalization = control.close()
        _put(output / "JOB-FINALIZATION.json", finalization)
        if not (output / "confirm.stdout").exists():
            (output / "confirm.stdout").write_bytes(partial)
    assert hit and not failure, "Lost-reply phase not hit; preserve, do not retry"
    assert finalization["controlled_termination"] is True
    assert finalization["after_cleanup"]["members"] == []
    assert partial and durable is not None and metadata is not None
    saved = files(s.root)
    assert digest(s.board) == preflight["candidate_sha256"]
    assert pose_rows(s.board.read_bytes())["R4"]["position_nm"] == [99500000, 110000000]
    assert protected_bytes(raw, ["R4"]) == protected_bytes(s.board.read_bytes(), ["R4"])
    assert {k: v for k, v in saved.items() if k != s.board.name} == {
        k: v for k, v in original.items() if k != s.board.name
    }
    rows = store_rows(s.state)
    assert len(rows["operations"]) == 1 and len(rows["consumed_tokens"]) == 1
    assert len(rows["scope_usage"]) == 1 and rows["scope_usage"][0]["used"] == 1
    code, queried = s.call("operation", "show", "--id", identifier)
    assert code == 0 and queried["ok"]
    status = queried["data"]["operation_status"]
    assert status["status"] == "succeeded" and status["receipt"] == json.loads(durable["receipt"])
    assert files(s.root) == saved and store_rows(s.state) == rows
    code, refused = s.call(
        *s.args(), "--confirm", data["confirm_token"], "--idempotency-key", "owned-reply-loss"
    )
    assert code == 6 and not refused["ok"]
    assert refused["error"]["details"]["conflict"] == "token_used"
    assert files(s.root) == saved and store_rows(s.state) == rows
    assert native_calls(s) == native_before
    _put(
        output / "RESULT.json",
        {
            "hit": True,
            "operation_id": identifier,
            "exact_query": queried,
            "spent_confirmation": refused,
            "engineering_files_after": saved,
            "confirmation_pre_attach_actual_native_processes": "unknown-not-measured",
            "held_stage_non_candidate_consumers": 0,
            "strict_transport_recovery_native_processes": native_calls(s) - native_before,
            "static_confirmation_call_tree_native_expectation": {
                "expected": 0,
                "basis": "reviewed source call tree; not a measured confirmation process count",
            },
            "second_engineering_write": False,
            "limit": (
                "Durable succeeded receipt and lost public reply; not an unknown pre-receipt window"
            ),
        },
    )
    record_property("move_replyloss_controlled_exe_confirmations", 1)
    record_property("move_replyloss_declared_stage_hit", "true")
