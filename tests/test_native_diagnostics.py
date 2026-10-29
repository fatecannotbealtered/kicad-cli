"""Actual supported OS capture self-test; not a KiCad crash claim."""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))
from process_evidence import ENVIRONMENT_KEYS, run_recorded
from read_minidump import exception


@pytest.mark.skipif(os.name != "nt", reason="Owned Windows handled-exception journal")
def test_handled_exception_flood_keeps_all_events_and_natural_completion(tmp_path):
    code = (
        "import ctypes\n"
        "count=0\n"
        "for _ in range(1000):\n"
        " try: ctypes.windll.kernel32.RaiseException(0xE1234567,0,0,None)\n"
        " except OSError: count+=1\n"
        "assert count==1000\n"
        "print(count)\n"
    )
    record, _ = run_recorded(
        tmp_path,
        "handled-flood",
        [sys.executable, "-u", "-c", code],
        cwd=tmp_path,
        env={k: v for k, v in os.environ.items() if k in ENVIRONMENT_KEYS},
        timeout=30,
        debug=True,
        role="synthetic-handled-events-selftest",
    )
    assert record["execution"] == "completed" and record["exit"] == 0
    assert record["job_finalization"]["natural_tree_complete"]
    assert not record["job_finalization"]["controlled_termination"]
    data = json.loads((tmp_path / "handled-flood-debug/result.json").read_bytes())
    events = [e for e in data["exceptions"] if e["code"] == "0xe1234567"]
    assert len(events) == 1000 and all(e["first_chance"] for e in events)
    assert not any("scene" in e for e in events)
    rows = [
        json.loads(line)
        for line in (tmp_path / "handled-flood-debug/exceptions.jsonl").read_text().splitlines()
    ]
    assert len(rows) == len(data["exceptions"])
    assert (tmp_path / "handled-flood.stdout").read_text().strip() == "1000"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows x64 debug/minidump API only")
def test_owned_fastfail_has_fault_context_stack_and_readable_dump(tmp_path):
    env = {k: v for k, v in os.environ.items() if k in ENVIRONMENT_KEYS}
    code = "import ctypes; ctypes.windll.kernel32.RaiseFailFastException(None,None,0)"
    record, _ = run_recorded(
        tmp_path,
        "owned-fastfail",
        [sys.executable, "-u", "-c", code],
        cwd=tmp_path,
        env=env,
        timeout=20,
        debug=True,
        role="synthetic-diagnostic-selftest",
    )
    assert record["execution"] == "crashed" and record["exit"] == 0xC0000602
    data = json.loads((tmp_path / "owned-fastfail-debug/result.json").read_bytes())
    fault = next(e for e in data["exceptions"] if not e["first_chance"])
    assert fault["scene"]["dump"]["succeeded"] and fault["scene"]["stack"]
    parsed = exception(fault["scene"]["dump"]["path"])
    assert parsed["code"] == fault["code"]
    assert parsed["thread_id"] == fault["thread_id"]
    assert parsed["rip"] == fault["scene"]["registers"]["rip"]
    assert parsed["address"] == fault["fault"]["address"]
    assert any(m.get("file_version") for m in data["modules"])


@pytest.mark.skipif(os.name != "nt", reason="Native Windows debug loop timeout")
def test_debugger_timeout_finishes_the_owned_process_record(tmp_path):
    env = {k: v for k, v in os.environ.items() if k in ENVIRONMENT_KEYS}
    record, _ = run_recorded(
        tmp_path,
        "timeout-debug",
        [sys.executable, "-u", "-c", "import time; print('alive',flush=True); time.sleep(20)"],
        cwd=tmp_path,
        env=env,
        timeout=1.5,
        debug=True,
        role="synthetic-diagnostic-selftest",
    )
    assert record["execution"] == "timeout" and record["status"] == "failed"
    assert record["state"] == "finished" and record["exit"] is not None
    assert (tmp_path / "timeout-debug.stdout").read_text().strip() == "alive"
