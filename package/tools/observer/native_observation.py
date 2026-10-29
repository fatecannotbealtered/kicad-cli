"""Classify owned Windows observations, independently of functional correctness.

First-chance exceptions are passed to the application with
DBG_EXCEPTION_NOT_HANDLED. Complete subsequent events and natural termination
can establish that no unhandled notification was observed; neither that fact
nor exit zero establishes correct output. No exception-code allowlist is used.
This is a bounded acceptance observer, not a subprocess security sandbox.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

MICROSOFT_REFERENCES = (
    "https://learn.microsoft.com/en-us/windows/win32/debug/debugger-exception-handling",
    "https://learn.microsoft.com/en-us/windows/win32/api/debugapi/nf-debugapi-continuedebugevent",
    "https://learn.microsoft.com/en-us/windows/win32/api/errhandlingapi/nf-errhandlingapi-raisefailfastexception",
    "https://learn.microsoft.com/en-us/windows/win32/api/minwinbase/ns-minwinbase-debug_event",
)
DBG_CONTINUE = 0x00010002
DBG_EXCEPTION_NOT_HANDLED = 0x80010001
FAIL_FAST_STATUSES = {0xC0000409, 0xC0000602}


def process_instance_id(pid, birth):
    if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF or type(birth) is not int or birth <= 0:
        raise ValueError("A process instance needs an exact positive PID and kernel birth")
    return f"{pid}-{birth}"


def metadata_birth_matches(metadata, birth):
    """Match CIM's recorded millisecond precision, without claiming 100 ns CIM precision."""
    if type(birth) is not int or birth <= 0:
        return False
    value = metadata.get("CreationDate")
    if not isinstance(value, str):
        return False
    match = re.fullmatch(r"/Date\((-?[0-9]+)(?:[+-][0-9]{4})?\)/", value)
    try:
        if match:
            milliseconds = int(match[1])
        else:
            observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if observed.tzinfo is None:
                return False
            delta = observed.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
            milliseconds = (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000
        return milliseconds == (birth - 116444736000000000) // 10000
    except (TypeError, ValueError, OverflowError):
        return False


def root_process(trace):
    """Select the exact preexecution root instance; legacy traces need a unique root PID."""
    rows = trace.get("processes", [])
    if trace.get("observation_schema") == 3:
        wanted = trace.get("root_process_instance_id")
        roots = [row for row in rows if row.get("process_instance_id") == wanted]
        identity = trace.get("root_identity", {})
        if not wanted or identity.get("process_instance_id") != wanted:
            raise ValueError("Root process instance is not bound")
    elif trace.get("observation_schema") == 2:
        roots = [row for row in rows if row.get("pid") == trace.get("root_pid")]
    else:
        raise ValueError("Unsupported process observation schema")
    if len(roots) != 1:
        raise ValueError("Exactly one root process instance is required")
    root = roots[0]
    if trace.get("observation_schema") == 3 and process_instance_id(
        root.get("pid"), root.get("creation_filetime")
    ) != trace.get("root_process_instance_id"):
        raise ValueError("Root instance key is not the canonical PID/birth identity")
    if root.get("pid") != trace.get("root_pid") or root.get("creation_filetime") != trace.get(
        "root_identity", {}
    ).get("creation_filetime"):
        raise ValueError("Root PID/birth differs from its preexecution identity")
    return root


def _number(value):
    return int(value, 0) if isinstance(value, str) else int(value)


def _unsigned(value):
    return _number(value) & 0xFFFFFFFF


def _event_identity_issues(trace):
    events, processes = trace.get("events"), trace.get("processes")
    if (
        not isinstance(events, list)
        or not events
        or not isinstance(processes, list)
        or not processes
        or any(not isinstance(row, dict) for row in events + processes)
    ):
        return ["event_or_process_identity_records_missing"]
    issues = []
    process_ids = []
    modern = trace.get("observation_schema") == 3
    instance_rows = {}
    for row in processes:
        pid = row.get("pid")
        if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
            issues.append("process_pid_missing_or_invalid")
        else:
            process_ids.append(pid)
        if modern:
            try:
                identity = process_instance_id(pid, row.get("creation_filetime"))
                if row.get("process_instance_id") != identity or identity in instance_rows:
                    issues.append("process_instance_identity_missing_or_duplicated")
                instance_rows[identity] = row
            except ValueError:
                issues.append("process_instance_birth_missing_or_invalid")
    for position, event in enumerate(events):
        for field in ("pid", "thread_id"):
            value = event.get(field)
            if type(value) is not int or not 0 < value <= 0xFFFFFFFF:
                issues.append("event_" + field + "_missing_or_invalid")
        if type(event.get("index")) is not int or event["index"] != position:
            issues.append("event_index_missing_or_invalid")
        if type(event.get("code")) is not int or not 1 <= event["code"] <= 9:
            issues.append("event_code_missing_or_invalid")
        if event.get("pid") not in process_ids:
            issues.append("event_pid_not_bound_to_process_identity")
        if modern:
            process = instance_rows.get(event.get("process_instance_id"), {})
            if not process or any(
                event.get(key) != process.get(key) for key in ("pid", "creation_filetime")
            ):
                issues.append("event_process_instance_binding_disagrees")
        if event.get("code") == 1 and type(event.get("first_chance")) is not bool:
            issues.append("exception_first_chance_identity_missing")
    return sorted(set(issues))


def classify_trace(trace, *, allowed_exit_codes=(0,)):
    """Require schema-3 instances or complete unique-lifecycle schema-2 evidence.

    ``may_validate_functional_output`` permits the caller to begin independent
    output/protection validation. It never certifies an engineering result.
    A first-chance call stack is optional; missing critical event/Job evidence
    is not. Old traces without the new journal remain historical observations.
    """
    identity_issues = _event_identity_issues(trace)
    if identity_issues:
        return {
            "status": "timeout" if trace.get("timed_out") else "observation_incomplete",
            "observation_complete": False,
            "may_validate_functional_output": False,
            "stop_required": True,
            "functional_output_verified": False,
            "issues": identity_issues,
            "first_chance_notifications": [],
            "second_chance_notifications": [],
            "fail_fast_status_notifications": [],
            "exception_disposition": (
                "Critical event identity incomplete; not classified as handled"
            ),
            "stack_limitations": [],
            "interpretation": "Missing or invalid event identity stops this observation.",
        }
    issues, first, second, fail_fast = [], [], [], []
    events = trace.get("events", [])
    processes = trace.get("processes", [])
    modern = trace.get("observation_schema") == 3
    if type(trace.get("observation_schema")) is not int or trace.get("observation_schema") not in {
        2,
        3,
    }:
        issues.append("schema_2_or_3_event_evidence_required")
    if not trace.get("complete_tree"):
        issues.append("debug_event_tree_incomplete")
    if not events or [row.get("index") for row in events] != list(range(len(events))):
        issues.append("event_sequence_missing_or_noncontiguous")
    key_name = "process_instance_id" if modern else "pid"
    root_key = trace.get("root_process_instance_id") if modern else trace.get("root_pid")
    rows = {row.get(key_name): row for row in processes}
    if not rows or len(rows) != len(processes) or root_key not in rows:
        issues.append("process_identities_missing_or_duplicated")
    creates, exits, exception_indices, bootstraps = {}, {}, set(), set()
    active_pids = {}
    for event in events:
        pid, code = event.get(key_name), event.get("code")
        actual_pid = event.get("pid")
        if code not in range(1, 10) or code == 9:
            issues.append("unknown_or_system_debug_error_event")
        if not event.get("continued") or event.get("cleanup"):
            issues.append("uncontinued_event_or_controlled_cleanup")
        if code == 3:
            if pid in creates or actual_pid in active_pids:
                issues.append("duplicate_process_creation")
            creates[pid] = event.get("index")
            active_pids[actual_pid] = pid
            if modern and (
                event.get("create_event_index") != event["index"]
                or event.get("identity_status") != "bound"
                or rows[pid].get("create_event_index") != event["index"]
            ):
                issues.append("process_create_instance_binding_incomplete")
        elif pid not in creates or pid in exits or active_pids.get(actual_pid) != pid:
            issues.append("event_outside_observed_process_lifetime")
        if code == 5:
            exits[pid] = event
            if active_pids.get(actual_pid) == pid:
                del active_pids[actual_pid]
        expected = DBG_CONTINUE
        if code == 1:
            try:
                exception_code = _number(event["exception_code"])
                bootstrap = event.get("bootstrap_breakpoint", False)
                if bootstrap:
                    if (
                        exception_code != 0x80000003
                        or not event.get("first_chance")
                        or pid in bootstraps
                    ):
                        issues.append("invalid_loader_breakpoint_exemption")
                    bootstraps.add(pid)
                else:
                    expected = DBG_EXCEPTION_NOT_HANDLED
                    exception_indices.add(event["index"])
                    (first if event.get("first_chance") else second).append(event["index"])
                    if exception_code in FAIL_FAST_STATUSES:
                        fail_fast.append(event["index"])
            except (KeyError, TypeError, ValueError):
                issues.append("exception_identity_incomplete")
        try:
            if _number(event["continuation"]) != expected:
                issues.append("application_exception_was_not_forwarded")
        except (KeyError, TypeError, ValueError):
            issues.append("continuation_status_missing")
    if set(creates) != set(rows) or set(exits) != set(rows):
        issues.append("create_exit_identity_sets_differ")
    for pid, process in rows.items():
        metadata = process.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
            issues.append("process_metadata_identity_incomplete")
        if (
            not process.get("owned")
            or type(process.get("creation_filetime")) is not int
            or process.get("creation_filetime", 0) <= 0
            or not process.get("image")
            or not process.get("image_sha256")
            or metadata.get("ProcessId") != process.get("pid")
            or not metadata.get("CommandLine")
        ):
            issues.append("process_birth_image_command_or_ownership_missing")
        if (modern or "CreationDate" in metadata) and not metadata_birth_matches(
            metadata, process.get("creation_filetime")
        ):
            issues.append("metadata_process_birth_disagrees")
        if modern:
            if (
                process.get("identity_status") != "bound"
                or process.get("metadata_creation_filetime_matches") is not True
            ):
                issues.append("process_metadata_identity_incomplete")
            parent_id = process.get("parent_process_instance_id")
            if pid == root_key:
                if parent_id is not None or process.get("parent_binding") != "external-observer":
                    issues.append("root_parent_binding_invalid")
            else:
                parent = rows.get(parent_id, {})
                if (
                    not parent
                    or process.get("parent_binding") != "observed-parent"
                    or metadata.get("ParentProcessId") != parent.get("pid")
                    or parent.get("creation_filetime", 0) > process.get("creation_filetime", 0)
                    or creates.get(parent_id, float("inf")) >= creates.get(pid, -1)
                    or exits.get(parent_id, {}).get("index", -1) <= creates.get(pid, -1)
                ):
                    issues.append("parent_process_instance_not_alive_at_child_create")
        try:
            if _unsigned(process["exit"]) != _unsigned(exits[pid]["exit_code"]):
                issues.append("process_exit_event_disagrees")
        except (KeyError, TypeError, ValueError):
            issues.append("process_exit_missing")
    root = rows.get(root_key, {})
    identity = trace.get("root_identity", {})
    if (
        not identity.get("assigned_before_resume")
        or not identity.get("owned_job_member")
        or identity.get("pid") != trace.get("root_pid")
        or identity.get("creation_filetime") != root.get("creation_filetime")
        or str(identity.get("image", "")).casefold() != str(root.get("image", "")).casefold()
        or (modern and identity.get("process_instance_id") != root_key)
    ):
        issues.append("root_preexecution_owned_identity_missing")
    try:
        if _unsigned(trace["exit"]) != _unsigned(root["exit"]):
            issues.append("root_return_code_disagrees")
    except (KeyError, TypeError, ValueError):
        issues.append("root_return_code_missing")
    finalization = trace.get("job_finalization", {})
    if (
        trace.get("job_active_before_close") != 0
        or not finalization.get("natural_tree_complete")
        or finalization.get("controlled_termination")
        or finalization.get("control_events")
        or any(
            not finalization.get(name, {}).get("all_members_attributed")
            or finalization.get(name, {}).get("members") != []
            for name in ("before_close", "after_cleanup")
        )
    ):
        issues.append("natural_empty_owned_job_termination_unproven")
    recorded_exceptions = trace.get("exceptions", [])
    if modern:
        inventory = trace.get("module_inventory")
        if (
            not isinstance(inventory, dict)
            or set(inventory) != set(rows)
            or any(not isinstance(value, list) for value in inventory.values())
        ):
            issues.append("instance_module_inventory_missing_or_mixed")
    if {row.get("event_index") for row in recorded_exceptions} != exception_indices or len(
        recorded_exceptions
    ) != len(exception_indices):
        issues.append("exception_journal_identity_set_differs")
    for row in recorded_exceptions:
        try:
            event = events[row["event_index"]]
            identity_keys = ("pid", "thread_id", "first_chance", "continuation")
            if modern:
                identity_keys += ("process_instance_id", "creation_filetime")
            if any(row.get(key) != event.get(key) for key in identity_keys) or _number(
                row["code"]
            ) != _number(event["exception_code"]):
                issues.append("exception_event_identity_disagrees")
        except (KeyError, IndexError, TypeError, ValueError):
            issues.append("exception_event_binding_incomplete")
    issues = sorted(set(issues))
    complete = not issues
    try:
        abnormal = [p["pid"] for p in processes if _unsigned(p["exit"]) >= 0xC0000000]
        wrong_exit = _unsigned(trace["exit"]) not in set(map(_unsigned, allowed_exit_codes))
        child_nonzero = any(p["exit"] != 0 for p in processes if p.get(key_name) != root_key)
    except (KeyError, TypeError, ValueError):
        abnormal, wrong_exit, child_nonzero = [], True, True
    status = (
        "timeout"
        if trace.get("timed_out")
        else "observation_incomplete"
        if not complete
        else "fail_fast_status"
        if fail_fast
        else "unhandled_exception"
        if second
        else "abnormal_exit"
        if abnormal
        else "unexpected_exit"
        if wrong_exit or child_nonzero
        else "completed_with_forwarded_first_chance"
        if first
        else "completed"
    )
    allowed = status in {"completed", "completed_with_forwarded_first_chance"}
    return {
        "status": status,
        "observation_complete": complete,
        "may_validate_functional_output": allowed,
        "stop_required": not allowed,
        "functional_output_verified": False,
        "issues": issues,
        "first_chance_notifications": first,
        "second_chance_notifications": second,
        "fail_fast_status_notifications": fail_fast,
        "exception_disposition": (
            "Application exceptions forwarded with DBG_EXCEPTION_NOT_HANDLED; "
            "only initial loader breakpoint consumed"
        ),
        "stack_limitations": [
            row.get("event_index")
            for row in recorded_exceptions
            if not (row.get("local_only_scene") or {}).get("stack")
        ],
        "interpretation": (
            "Complete subsequent events establish no observed unhandled exception, "
            "not the cause or harmlessness of each first-chance notification. "
            "Validate outputs and protected inputs separately."
        ),
    }


def inspect_trace(directory, *, allowed_exit_codes=(0,)):
    """Verify the complete persisted journal and output hashes before classifying."""
    directory = Path(directory)
    trace = json.loads((directory / "TRACE.json").read_bytes())
    issues = []
    for filename, key in (
        ("stdout", "stdout_sha256"),
        ("stderr", "stderr_sha256"),
        ("events.jsonl", "event_journal_sha256"),
    ):
        path = directory / filename
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != trace.get(key):
            issues.append("persisted_hash_mismatch:" + filename)
    try:
        records = [
            json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()
        ]
        events = trace["events"]
        if len(records) != 2 * len(events):
            issues.append("event_journal_record_count_differs")
        for index, event in enumerate(events):
            received, continued = records[2 * index : 2 * index + 2]
            if (
                received["kind"] != "received"
                or continued != {"kind": "continued", "event": event}
                or received["event"]["continued"]
                or any(
                    received["event"][key] != event[key]
                    for key in received["event"]
                    if key != "continued"
                )
            ):
                issues.append("event_journal_pair_disagrees")
    except (OSError, KeyError, IndexError, TypeError, ValueError):
        issues.append("event_journal_missing_or_malformed")
    if trace.get("observation_schema") == 3:
        for process in trace.get("processes", []):
            try:
                identity = process_instance_id(process["pid"], process["creation_filetime"])
                record = process["creation_record"]
                if record["path"] != "process-" + identity + ".json":
                    raise ValueError("Creation record name is not instance bound")
                path = directory / record["path"]
                raw = path.read_bytes()
                expected = {
                    key: value
                    for key, value in process.items()
                    if key not in {"creation_record", "exit", "exited_at"}
                }
                expected["exit"] = None
                if (
                    path.is_symlink()
                    or hashlib.sha256(raw).hexdigest() != record["sha256"]
                    or json.loads(raw) != expected
                ):
                    raise ValueError("Immutable instance creation snapshot differs")
            except (OSError, KeyError, TypeError, ValueError):
                issues.append("instance_creation_record_missing_or_changed")
    result = classify_trace(trace, allowed_exit_codes=allowed_exit_codes)
    if issues:
        result.update(
            status="timeout" if trace.get("timed_out") else "observation_incomplete",
            observation_complete=False,
            may_validate_functional_output=False,
            stop_required=True,
            issues=sorted(set(result["issues"] + issues)),
        )
    return result
