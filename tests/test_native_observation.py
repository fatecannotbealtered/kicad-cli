"""Harmless owned-process checks for the acceptance observer, never KiCad."""

import copy
import json
import os
import shutil
import sys

import pytest

from bench.native_observation import classify_trace, inspect_trace


def trace_example(*, first_chance=True, exception_code="0xe0424242"):
    rows = [
        {"index": 0, "code": 3, "pid": 42, "thread_id": 7},
        {
            "index": 1,
            "code": 1,
            "pid": 42,
            "thread_id": 7,
            "exception_code": exception_code,
            "first_chance": first_chance,
            "bootstrap_breakpoint": False,
        },
        {"index": 2, "code": 5, "pid": 42, "thread_id": 7, "exit_code": 0},
    ]
    for row in rows:
        row.update(
            cleanup=False,
            continued=True,
            continuation="0x80010001" if row["code"] == 1 else "0x10002",
        )
    empty = {"members": [], "all_members_attributed": True}
    return {
        "observation_schema": 2,
        "complete_tree": True,
        "root_pid": 42,
        "root_identity": {
            "pid": 42,
            "creation_filetime": 1234567,
            "image": "owned-python.exe",
            "assigned_before_resume": True,
            "owned_job_member": True,
        },
        "exit": 0,
        "timed_out": False,
        "events": rows,
        "processes": [
            {
                "pid": 42,
                "creation_filetime": 1234567,
                "image": "owned-python.exe",
                "image_sha256": "a" * 64,
                "owned": True,
                "metadata": {"ProcessId": 42, "CommandLine": "owned-python.exe harmless.py"},
                "exit": 0,
            }
        ],
        "exceptions": [
            {
                "pid": 42,
                "thread_id": 7,
                "code": exception_code,
                "first_chance": first_chance,
                "event_index": 1,
                "continuation": "0x80010001",
            }
        ],
        "job_active_before_close": 0,
        "job_finalization": {
            "natural_tree_complete": True,
            "controlled_termination": False,
            "control_events": [],
            "before_close": empty,
            "after_cleanup": empty,
        },
    }


def test_first_chance_without_stack_permits_separate_functional_validation():
    result = classify_trace(trace_example())
    assert result["status"] == "completed_with_forwarded_first_chance"
    assert result["may_validate_functional_output"]
    assert result["functional_output_verified"] is False
    assert result["stack_limitations"] == [1]


@pytest.mark.parametrize("field", ["pid", "thread_id", "index", "code"])
@pytest.mark.parametrize("value", [None, 0, -1, True, "7", []])
def test_event_identity_must_be_present_typed_and_bound(field, value):
    trace = trace_example()
    trace["events"][1][field] = value
    # Missing identity on both journals must not pass an equality-of-None check.
    if field in {"pid", "thread_id"}:
        trace["exceptions"][0][field] = value
    result = classify_trace(trace)
    assert result["status"] == "observation_incomplete"
    assert result["stop_required"]
    assert not result["may_validate_functional_output"]


@pytest.mark.parametrize("field", ["pid", "thread_id", "index", "code"])
def test_deleting_event_identity_from_both_journals_refuses(field):
    trace = trace_example()
    del trace["events"][1][field]
    trace["exceptions"][0].pop(field, None)
    result = classify_trace(trace)
    assert result["status"] == "observation_incomplete"
    assert result["stop_required"]


@pytest.mark.parametrize("code", ["0xe06d7363", "0xc0000005", "0xe0424242"])
def test_no_first_chance_exception_code_whitelist(code):
    result = classify_trace(trace_example(exception_code=code))
    assert result["may_validate_functional_output"]
    assert result["functional_output_verified"] is False


def test_exit_zero_does_not_override_second_chance():
    result = classify_trace(trace_example(first_chance=False))
    assert result["status"] == "unhandled_exception"
    assert result["stop_required"]


@pytest.mark.parametrize("code", ["0xc0000409", "0xc0000602"])
def test_fail_fast_status_stops_even_with_fabricated_zero_exit(code):
    result = classify_trace(trace_example(exception_code=code))
    assert result["status"] == "fail_fast_status"
    assert result["stop_required"]


@pytest.mark.parametrize(
    "mutation",
    ["create", "exit", "exception", "forwarding", "birth", "ownership", "job", "journal"],
)
def test_missing_critical_evidence_or_swallowed_exception_refuses(mutation):
    trace = trace_example()
    if mutation == "create":
        trace["events"].pop(0)
    elif mutation == "exit":
        trace["events"].pop()
    elif mutation == "exception":
        trace["exceptions"] = []
    elif mutation == "forwarding":
        trace["events"][1]["continuation"] = "0x10002"
    elif mutation == "birth":
        trace["processes"][0]["creation_filetime"] += 1
    elif mutation == "ownership":
        trace["processes"][0]["owned"] = False
    elif mutation == "job":
        del trace["job_finalization"]
    else:
        trace["events"][1]["continued"] = False
    result = classify_trace(trace)
    assert result["status"] == "observation_incomplete"
    assert result["stop_required"]


def test_timeout_is_distinct_from_crash_and_missing_observation():
    trace = trace_example()
    trace.update(timed_out=True, complete_tree=False)
    result = classify_trace(trace)
    assert result["status"] == "timeout"
    assert result["stop_required"]


def test_system_debug_error_cannot_be_ignored():
    trace = trace_example()
    trace["events"][1]["code"] = 9
    assert classify_trace(trace)["stop_required"]


SYNTHETIC_SOURCE = r"""
import ctypes as C
import pathlib
import struct
import sys
import time

mode, marker = sys.argv[1], pathlib.Path(sys.argv[2])
kernel = C.WinDLL("kernel32", use_last_error=True)
kernel.SetErrorMode(3)  # This child only: no critical-error/GPF dialog.
kernel.RaiseException.argtypes = [C.c_uint32, C.c_uint32, C.c_uint32, C.c_void_p]
if mode == "handled":
    try:
        kernel.RaiseException(0xE0424242, 0, 0, None)
    except OSError:
        marker.write_text("handled-by-application", encoding="utf-8")
        print("HANDLED", flush=True)
    else:
        raise AssertionError("Debugger swallowed the application exception")
elif mode == "unhandled":
    # The new native thread has no ctypes SEH frame. Its application exception
    # must receive first/second-chance notifications and terminate this child.
    kernel.VirtualAlloc.argtypes = [C.c_void_p, C.c_size_t, C.c_uint32, C.c_uint32]
    kernel.VirtualAlloc.restype = C.c_void_p
    code = (b"\x48\x83\xec\x28\xb9\x42\x42\x42\xe0\x31\xd2"
            b"\x45\x31\xc0\x45\x31\xc9\x48\xb8"
            + struct.pack("<Q", C.cast(kernel.RaiseException, C.c_void_p).value)
            + b"\xff\xd0\x31\xc0\x48\x83\xc4\x28\xc3")
    address = kernel.VirtualAlloc(None, len(code), 0x3000, 0x40)
    if not address:
        raise C.WinError(C.get_last_error())
    C.memmove(address, code, len(code))
    kernel.CreateThread.argtypes = [C.c_void_p, C.c_size_t, C.c_void_p, C.c_void_p,
                                    C.c_uint32, C.c_void_p]
    kernel.CreateThread.restype = C.c_void_p
    kernel.WaitForSingleObject.argtypes = [C.c_void_p, C.c_uint32]
    thread = kernel.CreateThread(None, 0, address, None, 0, None)
    if not thread:
        raise C.WinError(C.get_last_error())
    kernel.WaitForSingleObject(thread, 10000)
    marker.write_text("unexpected-survival", encoding="utf-8")
elif mode == "failfast":
    kernel.RaiseFailFastException.argtypes = [C.c_void_p, C.c_void_p, C.c_uint32]
    kernel.RaiseFailFastException(None, None, 0)
    marker.write_text("unexpected-survival", encoding="utf-8")
elif mode == "timeout":
    marker.write_text("started-before-timeout", encoding="utf-8")
    time.sleep(120)
else:
    raise AssertionError(mode)
"""


@pytest.mark.skipif(os.name != "nt", reason="Owned Windows debugging APIs")
@pytest.mark.parametrize(
    "mode,expected",
    [
        ("handled", "completed_with_forwarded_first_chance"),
        ("unhandled", "unhandled_exception"),
        ("failfast", "fail_fast_status"),
        ("timeout", "timeout"),
    ],
)
def test_real_owned_synthetic_process_dispositions(tmp_path, mode, expected):
    from bench.persistence_acceptance.process_tree import observe

    source, marker, output = tmp_path / "harmless.py", tmp_path / "marker", tmp_path / "observation"
    source.write_text(SYNTHETIC_SOURCE, encoding="utf-8")
    try:
        observe(
            [sys.executable, "-B", str(source), mode, str(marker)],
            str(tmp_path),
            dict(os.environ),
            output,
            timeout=8 if mode == "timeout" else 30,
        )
    except RuntimeError:
        if mode == "handled":
            raise
    trace = json.loads((output / "TRACE.json").read_bytes())
    result = inspect_trace(output)
    (tmp_path / "CLASSIFICATION.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    assert result["status"] == expected, result
    assert trace["job_active_before_close"] == 0
    assert trace["job_finalization"]["after_cleanup"]["members"] == []
    if mode == "handled":
        assert marker.read_text() == "handled-by-application"
        assert (output / "stdout").read_text().strip() == "HANDLED"
        assert result["may_validate_functional_output"]
        # One real complete observation, then read-only evidence variants. No
        # additional process is started to test missing-event/hash rejection.
        missing = copy.deepcopy(trace)
        missing["events"] = [event for event in missing["events"] if event["code"] != 5]
        missing_result = classify_trace(missing)
        assert missing_result["status"] == "observation_incomplete"
        tampered = tmp_path / "tampered-evidence"
        shutil.copytree(output, tampered)
        with (tampered / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write("{}\n")
        tampered_result = inspect_trace(tampered)
        assert tampered_result["status"] == "observation_incomplete"
        (tmp_path / "EVIDENCE-VARIANTS.json").write_text(
            json.dumps(
                {"missing_exit_events": missing_result, "tampered_journal": tampered_result},
                indent=2,
            ),
            encoding="utf-8",
        )
    elif mode == "timeout":
        assert marker.read_text() == "started-before-timeout"
        assert trace["job_finalization"]["controlled_termination"]
        assert trace["controlled_cleanup_tree_complete"]
        assert trace["exit"] is not None
    else:
        assert not marker.exists()
        assert result["observation_complete"]
        assert result["stop_required"]
