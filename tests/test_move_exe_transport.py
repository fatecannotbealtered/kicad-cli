"""Pure host checks for the one-candidate exit-propagation acceptance rule.

Synthetic records and WinAPI string parsing launch no observed process. These
checks grant permission to validate output; they do not certify a move result.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from move_exe_transport import (
    ARTIFACT_SHA256,
    MOVE_REFUSAL_EXITS,
    FinalMoveExe,
    candidate_exit_decision,
    inspect_candidate_trace,
    windows_argv,
)

from bench.native_observation import classify_trace

pytestmark = pytest.mark.skipif(os.name != "nt", reason="WinAPI recorded-command parsing")
ALLOWED_EXITS = (0, *sorted(MOVE_REFUSAL_EXITS))


def sample(cwd, exit_code=2):
    image = str(cwd / "candidate 中文 空格" / "kicad-cli.exe")
    argv = [
        image,
        "board",
        "footprint",
        "move",
        "--moves",
        "R1:101.25,100.5",
        "--dry-run",
        "--compact",
    ]

    def process(pid, parent, birth, path, sha256, command, code):
        return {
            "pid": pid,
            "creation_filetime": birth,
            "image": path,
            "image_sha256": sha256,
            "owned": True,
            "metadata": {
                "ProcessId": pid,
                "ParentProcessId": parent,
                "CommandLine": subprocess.list2cmdline(command),
            },
            "exit": code,
        }

    root = process(42, 41, 1000, image, ARTIFACT_SHA256, argv, exit_code)
    child = process(43, 42, 2000, image, ARTIFACT_SHA256, argv, exit_code)
    support = process(44, 43, 3000, "conhost.exe", "b" * 64, ["conhost.exe", "0x4"], 0)
    events = []
    for code, pid in ((3, 42), (3, 43), (3, 44), (5, 44), (5, 43), (5, 42)):
        row = {
            "index": len(events),
            "code": code,
            "pid": pid,
            "thread_id": pid + 100,
            "cleanup": False,
            "continued": True,
            "continuation": "0x10002",
        }
        if code == 5:
            row["exit_code"] = 0 if pid == 44 else exit_code
        events.append(row)
    trace = {
        "observation_schema": 2,
        "complete_tree": True,
        "root_pid": 42,
        "root_identity": {
            "pid": 42,
            "creation_filetime": 1000,
            "image": image,
            "assigned_before_resume": True,
            "owned_job_member": True,
        },
        "exit": exit_code,
        "timed_out": False,
        "events": events,
        "processes": [root, child, support],
        "exceptions": [],
        "job_active_before_close": 0,
        "job_finalization": {
            "natural_tree_complete": True,
            "controlled_termination": False,
            "control_events": [],
            "before_close": {"members": [], "all_members_attributed": True},
            "after_cleanup": {"members": [], "all_members_attributed": True},
        },
    }
    return trace, argv


def generic(trace):
    return classify_trace(trace, allowed_exit_codes=ALLOWED_EXITS)


def decide(trace, argv, cwd):
    observed = generic(trace)
    return observed, *candidate_exit_decision(
        observed, trace, argv, cwd=cwd, allowed_exit_codes=ALLOWED_EXITS
    )


@pytest.mark.parametrize("exit_code", sorted(MOVE_REFUSAL_EXITS))
def test_exact_candidate_propagation_only_permits_functional_validation(tmp_path, exit_code):
    trace, argv = sample(tmp_path, exit_code)
    original = copy.deepcopy(trace)
    observed, decision, policy = decide(trace, argv, tmp_path)
    assert observed["status"] == "unexpected_exit" and observed["stop_required"]
    assert policy["generic_status"] == "unexpected_exit" and policy["admitted"]
    assert policy["sdk_exit"] == exit_code and policy["issues"] == []
    assert policy["root_pid"] == 42 and policy["child_pid"] == 43
    assert decision["status"] == "candidate_expected_sdk_exit"
    assert decision["may_validate_functional_output"] and not decision["stop_required"]
    assert decision["functional_output_verified"] is policy["functional_output_verified"] is False
    assert trace == original


def test_empty_move_value_is_preserved_for_existing_invalid_input_case(tmp_path):
    trace, argv = sample(tmp_path)
    argv[5] = ""
    for row in trace["processes"][:2]:
        row["metadata"]["CommandLine"] = subprocess.list2cmdline(argv)
    _observed, decision, policy = decide(trace, argv, tmp_path)
    assert policy["admitted"] and decision["may_validate_functional_output"]


def test_resolved_same_image_allows_relative_and_case_insensitive_argv_zero(tmp_path):
    trace, argv = sample(tmp_path)
    child = trace["processes"][1]
    child["image"] = "candidate 中文 空格/kicad-cli.exe"
    child["metadata"]["CommandLine"] = subprocess.list2cmdline(
        [str(tmp_path / "CANDIDATE 中文 空格" / "KICAD-CLI.EXE"), *argv[1:]]
    )
    _observed, decision, policy = decide(trace, argv, tmp_path)
    assert policy["admitted"] and decision["may_validate_functional_output"]


@pytest.mark.parametrize(
    "defect",
    [
        "root-sha",
        "child-sha",
        "root-image",
        "child-image",
        "parent",
        "parent-type",
        "older-birth",
        "missing-birth",
        "birth-type",
        "root-command",
        "child-command",
        "root-argv-zero",
        "child-argv-zero",
        "missing-command",
        "invalid-command",
        "unrelated-command",
        "different-argument-order",
        "extra-argument",
        "empty-argv",
        "empty-argv-zero",
        "invalid-expected-type",
        "expected-nul",
    ],
)
def test_candidate_identity_birth_and_complete_request_drift_refuses(tmp_path, defect):
    trace, argv = sample(tmp_path)
    root, child = trace["processes"][:2]
    observed = generic(trace)
    assert observed["status"] == "unexpected_exit"
    if defect.endswith("-sha"):
        (root if defect == "root-sha" else child)["image_sha256"] = "c" * 64
    elif defect.endswith("-image"):
        (root if defect == "root-image" else child)["image"] = str(tmp_path / "foreign.exe")
    elif defect == "parent":
        child["metadata"]["ParentProcessId"] = 41
    elif defect == "parent-type":
        child["metadata"]["ParentProcessId"] = "42"
    elif defect == "older-birth":
        child["creation_filetime"] = 999
    elif defect == "missing-birth":
        child.pop("creation_filetime")
    elif defect == "birth-type":
        child["creation_filetime"] = "2000"
    elif defect in {"root-command", "child-command"}:
        row = root if defect == "root-command" else child
        row["metadata"]["CommandLine"] = subprocess.list2cmdline([*argv[:-1], "--quiet"])
    elif defect in {"root-argv-zero", "child-argv-zero"}:
        row = root if defect == "root-argv-zero" else child
        row["metadata"]["CommandLine"] = subprocess.list2cmdline(["foreign.exe", *argv[1:]])
    elif defect == "missing-command":
        child["metadata"].pop("CommandLine")
    elif defect == "invalid-command":
        child["metadata"]["CommandLine"] = "bad\0command"
    elif defect == "unrelated-command":
        argv[1:4] = ["operation", "show", "--id"]
        for row in (root, child):
            row["metadata"]["CommandLine"] = subprocess.list2cmdline(argv)
    elif defect == "different-argument-order":
        child["metadata"]["CommandLine"] = subprocess.list2cmdline(
            [*argv[:4], "--dry-run", *argv[4:6], "--compact"]
        )
    elif defect == "extra-argument":
        child["metadata"]["CommandLine"] = subprocess.list2cmdline([*argv, "--quiet"])
    elif defect == "empty-argv":
        argv = []
    elif defect == "empty-argv-zero":
        argv[0] = ""
    elif defect == "invalid-expected-type":
        argv[5] = 2
    elif defect == "expected-nul":
        argv[5] += "\0"
    original = copy.deepcopy(trace)
    decision, policy = candidate_exit_decision(
        observed, trace, argv, cwd=tmp_path, allowed_exit_codes=ALLOWED_EXITS
    )
    assert decision is observed and decision["stop_required"]
    assert policy["applied"] and not policy["admitted"] and policy["issues"]
    assert not policy["functional_output_verified"] and trace == original


@pytest.mark.parametrize("code", [0, 1, 5, 8, 0xC0000409, None, True, "2"])
def test_root_exit_outside_frozen_refusals_never_admits(tmp_path, code):
    trace, argv = sample(tmp_path)
    observed = generic(trace)
    trace["exit"] = trace["processes"][0]["exit"] = code
    decision, policy = candidate_exit_decision(
        observed, trace, argv, cwd=tmp_path, allowed_exit_codes=ALLOWED_EXITS
    )
    assert decision is observed and not policy["admitted"]
    assert "root_not_frozen_move_refusal_exit" in policy["issues"]


def test_successful_root_cannot_hide_nonzero_candidate_child(tmp_path):
    trace, argv = sample(tmp_path)
    trace["exit"] = trace["processes"][0]["exit"] = 0
    trace["events"][-1]["exit_code"] = 0
    observed, decision, policy = decide(trace, argv, tmp_path)
    assert observed["status"] == "unexpected_exit"
    assert decision is observed and decision["stop_required"] and not policy["admitted"]


@pytest.mark.parametrize(
    "defect", ["none", "two", "exit-mismatch", "unknown-exit", "missing-exit", "bool-zero"]
)
def test_only_one_nonzero_child_and_all_other_exact_zero_exits(tmp_path, defect):
    trace, argv = sample(tmp_path)
    observed = generic(trace)
    child, support = trace["processes"][1:]
    if defect == "none":
        child["exit"] = 0
    elif defect == "two":
        support["exit"] = 2
    elif defect == "exit-mismatch":
        child["exit"] = 3
    elif defect == "unknown-exit":
        child["exit"] = 1
    elif defect == "missing-exit":
        support.pop("exit")
    else:
        support["exit"] = False
    decision, policy = candidate_exit_decision(
        observed, trace, argv, cwd=tmp_path, allowed_exit_codes=ALLOWED_EXITS
    )
    assert decision is observed and policy["issues"] and not policy["admitted"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("observation_complete", False),
        ("issues", ["persisted_hash_mismatch:stdout"]),
        ("second_chance_notifications", [3]),
        ("fail_fast_status_notifications", [3]),
        ("functional_output_verified", True),
        ("may_validate_functional_output", True),
        ("stop_required", False),
    ],
)
def test_generic_critical_evidence_cannot_be_relaxed(tmp_path, field, value):
    trace, argv = sample(tmp_path)
    observed = generic(trace)
    observed[field] = value
    decision, policy = candidate_exit_decision(
        observed, trace, argv, cwd=tmp_path, allowed_exit_codes=ALLOWED_EXITS
    )
    assert decision is observed and not policy["admitted"]
    assert "generic_critical_evidence_not_eligible" in policy["issues"]


@pytest.mark.parametrize(
    "status",
    [
        "completed",
        "completed_with_forwarded_first_chance",
        "timeout",
        "observation_incomplete",
        "fail_fast_status",
        "unhandled_exception",
        "abnormal_exit",
    ],
)
def test_every_other_generic_status_is_preserved_exactly(tmp_path, status):
    trace, argv = sample(tmp_path)
    observed = generic(trace)
    observed["status"] = status
    original = copy.deepcopy(observed)
    decision, policy = candidate_exit_decision(
        observed, trace, argv, cwd=tmp_path, allowed_exit_codes=ALLOWED_EXITS
    )
    assert decision is observed and observed == original
    assert not policy["applied"] and not policy["admitted"]
    assert policy["decision"] == "generic_decision_preserved"


@pytest.mark.parametrize("value", [None, "", "\0", "recorded\0argument"])
def test_invalid_recorded_command_is_never_executed_or_accepted(value):
    with pytest.raises(ValueError):
        windows_argv(value)


@pytest.mark.parametrize(
    "arguments",
    [
        ["candidate 中文 空格.exe", "", "R1:101.25,100.5"],
        ["candidate.exe", 'literal"quoted', "trailing\\", "white space"],
        ["candidate.exe", "$(never-execute)", "`never-execute`", "&", "|"],
    ],
)
def test_windows_parser_only_round_trips_recorded_literal_arguments(arguments):
    assert windows_argv(subprocess.list2cmdline(arguments)) == arguments


def persist(directory, trace):
    directory.mkdir()
    (directory / "stdout").write_bytes(
        b'{"ok":false,"schema_version":"1.0","error":{"code":"E_USAGE","retryable":false}}'
    )
    (directory / "stderr").write_bytes(b"")
    records = []
    for event in trace["events"]:
        received = dict(event, continued=False)
        records.extend(
            [{"kind": "received", "event": received}, {"kind": "continued", "event": event}]
        )
    (directory / "events.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in records), encoding="utf-8"
    )
    for name, key in (
        ("stdout", "stdout_sha256"),
        ("stderr", "stderr_sha256"),
        ("events.jsonl", "event_journal_sha256"),
    ):
        trace[key] = hashlib.sha256((directory / name).read_bytes()).hexdigest()
    (directory / "TRACE.json").write_text(json.dumps(trace), encoding="utf-8")


@pytest.mark.parametrize("tamper", [None, "stdout", "events.jsonl"])
def test_persisted_generic_inspection_precedes_candidate_policy_and_never_rewrites(
    tmp_path, tamper
):
    trace, argv = sample(tmp_path)
    directory = tmp_path / "recorded"
    persist(directory, trace)
    if tamper:
        with (directory / tamper).open("ab") as output:
            output.write(b" ")
    original = {p.name: p.read_bytes() for p in directory.iterdir()}
    decision, policy = inspect_candidate_trace(
        directory, argv, cwd=tmp_path, allowed_exit_codes=ALLOWED_EXITS
    )
    assert decision["functional_output_verified"] is False
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == original
    if tamper:
        assert decision["status"] == "observation_incomplete"
        assert decision["stop_required"] and not policy["applied"]
    else:
        assert decision["status"] == "candidate_expected_sdk_exit"
        assert decision["may_validate_functional_output"] and policy["admitted"]


def test_trace_change_during_generic_inspection_stops_candidate_policy(tmp_path, monkeypatch):
    import bench.native_observation as observer

    trace, argv = sample(tmp_path)
    directory = tmp_path / "recorded"
    persist(directory, trace)
    inspect = observer.inspect_trace

    def changed_after_inspection(*args, **kwargs):
        result = inspect(*args, **kwargs)
        with (directory / "TRACE.json").open("ab") as output:
            output.write(b" ")
        return result

    monkeypatch.setattr(observer, "inspect_trace", changed_after_inspection)
    decision, policy = inspect_candidate_trace(
        directory, argv, cwd=tmp_path, allowed_exit_codes=ALLOWED_EXITS
    )
    assert decision["status"] == "observation_incomplete" and decision["stop_required"]
    assert decision["inspection_status_before_trace_drift"] == "unexpected_exit"
    assert "trace_changed_during_generic_inspection" in decision["issues"]
    assert not policy["applied"] and not policy["admitted"]


def test_invocation_allowed_exits_remain_a_hard_constraint(tmp_path):
    trace, argv = sample(tmp_path, 2)
    directory = tmp_path / "recorded"
    persist(directory, trace)
    decision, policy = inspect_candidate_trace(
        directory, argv, cwd=tmp_path, allowed_exit_codes=(0,)
    )
    assert decision["status"] == "unexpected_exit" and decision["stop_required"]
    assert not policy["admitted"]
    assert "root_exit_not_allowed_by_invocation" in policy["issues"]


@pytest.mark.parametrize("identity", ["same-sha", "same-image", "same-sha-and-image"])
def test_zero_exit_additional_candidate_identity_also_refuses(tmp_path, identity):
    trace, argv = sample(tmp_path)
    support = trace["processes"][2]
    if identity in {"same-sha", "same-sha-and-image"}:
        support["image_sha256"] = ARTIFACT_SHA256
    if identity in {"same-image", "same-sha-and-image"}:
        support["image"] = argv[0]
    observed, decision, policy = decide(trace, argv, tmp_path)
    assert observed["status"] == "unexpected_exit"
    assert decision is observed and decision["stop_required"]
    assert not policy["admitted"]
    assert "exactly_one_candidate_child_identity_required" in policy["issues"]


@pytest.mark.parametrize(
    "drift",
    [
        "stdout",
        "trace-bytes",
        "trace-and-stdout",
        "returned-exit",
        "returned-sequence-type",
        "returned-scalar-type",
        "clean",
    ],
)
def test_real_call_binds_observe_and_schema_input_before_validation(tmp_path, monkeypatch, drift):
    import move_exe_transport as transport_module

    trace, argv = sample(tmp_path)
    validated = []

    def validate(value):
        validated.append(value)
        assert drift == "clean", "Schema was reached with an unbound functional input"

    def fake_observe(command, cwd, env, output, *, timeout):
        assert command == argv and cwd == tmp_path and env == {} and timeout == 120
        returned = copy.deepcopy(trace)
        persist(output, returned)
        if drift == "returned-exit":
            returned["exit"] = 0
        elif drift == "returned-sequence-type":
            returned["processes"] = tuple(returned["processes"])
        elif drift == "returned-scalar-type":
            returned["timed_out"] = 0
        return returned

    fake_module = ModuleType("bench.persistence_acceptance.process_tree")
    fake_module.observe = fake_observe
    monkeypatch.setitem(sys.modules, fake_module.__name__, fake_module)
    monkeypatch.setattr(transport_module, "digest", lambda _path: ARTIFACT_SHA256)
    inspect = transport_module.inspect_candidate_trace

    def inspected_then_drift(*args, **kwargs):
        result = inspect(*args, **kwargs)
        directory = args[0]
        if drift == "stdout":
            (directory / "stdout").write_bytes(b"unbound non-JSON output")
        elif drift == "trace-bytes":
            with (directory / "TRACE.json").open("ab") as output:
                output.write(b" ")
        elif drift == "trace-and-stdout":
            (directory / "stdout").write_bytes(b'{"ok":true}')
            changed = json.loads((directory / "TRACE.json").read_bytes())
            changed["stdout_sha256"] = hashlib.sha256(
                (directory / "stdout").read_bytes()
            ).hexdigest()
            (directory / "TRACE.json").write_text(json.dumps(changed), encoding="utf-8")
        return result

    monkeypatch.setattr(transport_module, "inspect_candidate_trace", inspected_then_drift)
    transport = FinalMoveExe.__new__(FinalMoveExe)
    transport.binary = Path(argv[0])
    transport.cwd, transport.env = tmp_path, {}
    transport.output = tmp_path / "calls"
    transport.output.mkdir()
    transport.index = 0
    transport.allowed_exit_codes = ALLOWED_EXITS
    transport.validator = SimpleNamespace(validate=validate)
    transport.errors = {"E_USAGE": {"exit": 2, "retryable": False}}
    if drift == "clean":
        code, value = transport.call(*argv[1:-1])
        assert code == 2 and value["error"]["code"] == "E_USAGE" and len(validated) == 1
    else:
        with pytest.raises(pytest.exit.Exception, match="preserved fatal/incomplete observation"):
            transport.call(*argv[1:-1])
        assert validated == []
    classification = json.loads((transport.output / "001/CLASSIFICATION.json").read_bytes())
    assert classification["functional_output_verified"] is False
    assert classification["candidate_policy"]["functional_output_verified"] is False
    if drift != "clean":
        assert classification["status"] == "observation_incomplete"
        assert classification["stop_required"]
        assert "functional_input_binding_failed" in classification["issues"]
