"""Bounded process ownership tests; no KiCad, board, state or real exceptions."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from kicad_cli import move_native_process as process


def invoke(tmp_path, script, *, timeout=10):
    return process.run(
        [str(Path(sys.executable).resolve()), "-I", "-B", "-c", script],
        cwd=tmp_path,
        env=dict(os.environ, PRIVATE_TEST_SECRET="not-in-the-record"),
        output=tmp_path / "call",
        timeout=timeout,
    )


def persisted(tmp_path):
    return json.loads((tmp_path / "call/record.json").read_bytes())


def test_harmless_root_and_descendant_finish_naturally(tmp_path):
    script = (
        "import subprocess,sys; "
        "print('parent',flush=True); "
        "subprocess.Popen([sys.executable,'-I','-B','-c',"
        "\"import time;time.sleep(.4);print('child',flush=True)\"]); "
        "print('root exiting',flush=True)"
    )
    observed = invoke(tmp_path, script)
    assert observed["execution"] == "completed_for_output_validation", observed
    assert observed["exit"] == 0 and observed["output_validation_permitted"]
    assert observed == persisted(tmp_path)
    assert observed["job_finalization"]["natural_tree_complete"]
    assert not observed["job_finalization"]["controlled_termination"]
    assert observed["job_finalization"]["after_cleanup"]["members"] == []
    assert b"child" in (tmp_path / "call/stdout").read_bytes()
    assert observed["duration_ms"] >= 350
    identity = observed["root_identity"]
    assert identity["pid"] == observed["root_pid"]
    assert identity["image_sha256"] == process._digest(sys.executable)
    if os.name == "nt":
        assert identity["assigned_before_resume"] and identity["owned_job_member"]
        assert identity["creation_filetime"] > 0
        assert observed["job_finalization"]["kind"] == "windows_job"
    else:
        assert identity["birth_identity"]["start_ticks"] > 0
        assert identity["session"] == identity["process_group"] == identity["pid"]
    raw = (tmp_path / "call/record.json").read_text()
    assert "PRIVATE_TEST_SECRET" in raw and "not-in-the-record" not in raw
    assert json.loads((tmp_path / "call/command.json").read_bytes())["command"][1:4] == [
        "-I",
        "-B",
        "-c",
    ]


def test_ordinary_nonzero_is_not_a_crash_or_valid_drc(tmp_path):
    observed = invoke(tmp_path, "import sys;print('report-ready');sys.exit(5)")
    assert observed["execution"] == "native_nonzero"
    assert observed["exit"] == 5 and observed["output_validation_permitted"]
    assert observed["job_finalization"]["natural_tree_complete"]
    assert persisted(tmp_path) == observed
    assert (tmp_path / "call/stdout").read_bytes().strip() == b"report-ready"


def test_timeout_stops_only_this_owned_tree_and_keeps_records(tmp_path):
    marker = tmp_path / "late-marker"
    child = f"import time,pathlib;time.sleep(30);pathlib.Path({str(marker)!r}).write_text('late')"
    script = (
        f"import subprocess,sys,time;subprocess.Popen([sys.executable,'-I','-B','-c',{child!r}]);"
        "print('started',flush=True);time.sleep(30)"
    )
    started = time.monotonic()
    observed = invoke(tmp_path, script, timeout=1)
    assert time.monotonic() - started < 15
    assert observed["execution"] == "timeout" and observed["timed_out"]
    assert not observed["output_validation_permitted"]
    assert observed["job_finalization"]["controlled_termination"]
    assert not observed["job_finalization"]["natural_tree_complete"]
    assert observed["job_finalization"]["after_cleanup"]["members"] == []
    assert observed["job_finalization"]["control_events"]
    assert persisted(tmp_path) == observed
    assert not marker.exists()
    assert b"started" in (tmp_path / "call/stdout").read_bytes()


@pytest.mark.parametrize(
    "exit_code,platform,expected",
    [
        (0, "nt", "completed_for_output_validation"),
        (5, "nt", "native_nonzero"),
        (0xC0000409, "nt", "native_fatal_exit"),
        (-1073741819, "nt", "native_fatal_exit"),
        (-9, "posix", "native_fatal_exit"),
        (5, "posix", "native_nonzero"),
    ],
)
def test_exit_classification_without_executing_a_fatal_process(exit_code, platform, expected):
    assert process._execution(exit_code, platform) == expected


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True, "1"])
def test_invalid_timeout_never_launches(tmp_path, monkeypatch, timeout):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid input must not launch")

    monkeypatch.setattr(process.subprocess, "Popen", unexpected)
    with pytest.raises(ValueError):
        invoke(tmp_path, "pass", timeout=timeout)
    assert not (tmp_path / "call").exists()


@pytest.mark.parametrize("command", [[], ["python"], [sys.executable, "a\0b"], [1]])
def test_invalid_command_never_launches(tmp_path, monkeypatch, command):
    monkeypatch.setattr(process.subprocess, "Popen", lambda *a, **kw: pytest.fail("launch"))
    with pytest.raises(ValueError):
        process.run(command, cwd=tmp_path, env={}, output=tmp_path / "call", timeout=1)
    assert not (tmp_path / "call").exists()


def test_existing_call_directory_is_not_reused(tmp_path, monkeypatch):
    folder = tmp_path / "call"
    folder.mkdir()
    (folder / "record.json").write_text("previous-stop", encoding="utf-8")
    monkeypatch.setattr(process.subprocess, "Popen", lambda *a, **kw: pytest.fail("launch"))
    with pytest.raises(FileExistsError):
        invoke(tmp_path, "pass")
    assert (folder / "record.json").read_text() == "previous-stop"


class FakeProcess:
    pid = 12345
    returncode = None

    def __init__(self, events, code=0):
        self.events, self.code = events, code

    def poll(self):
        return self.code

    def wait(self, timeout):
        self.events.append("wait")
        self.returncode = self.code
        return self.code

    def kill(self):
        self.events.append("kill_exact_created_root")


class FakeOwner:
    def __init__(self, events, tmp_path):
        self.events, self.tmp_path = events, tmp_path
        self.root_identity = None

    def assign(self, child):
        self.events.append("assign")
        self.root_identity = {
            "pid": child.pid,
            "image": str(Path(sys.executable).resolve()),
            "image_sha256": process._digest(sys.executable),
            "creation_filetime": 10,
            "assigned_before_resume": True,
            "owned_job_member": True,
        }
        return self.root_identity

    def resume(self, child):
        self.events.append("resume")
        record = persisted(self.tmp_path)
        assert record["execution"] == "started"
        assert record["root_identity"] == self.root_identity

    def active(self):
        return 0

    def close(self):
        self.events.append("close_owned")
        return {
            "kind": "windows_job",
            "natural_tree_complete": True,
            "controlled_termination": False,
            "control_events": [],
            "before_close": {"members": [], "all_members_attributed": True},
            "after_cleanup": {"members": [], "all_members_attributed": True},
        }

    def terminate(self):
        self.events.append("terminate_owned")


@pytest.mark.skipif(os.name != "nt", reason="Windows suspended Job launch contract")
def test_birth_is_durable_before_resume_and_only_new_job_closes(tmp_path, monkeypatch):
    events = []
    owner = FakeOwner(events, tmp_path)
    monkeypatch.setattr(process, "_WindowsJob", lambda: owner)

    def start(command, **kwargs):
        events.append("create_suspended")
        assert kwargs["creationflags"] == 0x08000004
        assert persisted(tmp_path)["execution"] == "not_started"
        assert kwargs["cwd"] == tmp_path
        return FakeProcess(events)

    monkeypatch.setattr(process.subprocess, "Popen", start)
    observed = invoke(tmp_path, "pass")
    assert events == ["create_suspended", "assign", "resume", "wait", "close_owned"]
    assert observed["output_validation_permitted"]


@pytest.mark.skipif(os.name != "nt", reason="Windows suspended Job launch contract")
def test_launch_failure_is_retained_without_claimed_output(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(process, "_WindowsJob", lambda: FakeOwner(events, tmp_path))

    def fail(*args, **kwargs):
        raise OSError("synthetic launch refusal")

    monkeypatch.setattr(process.subprocess, "Popen", fail)
    observed = invoke(tmp_path, "pass")
    assert observed["execution"] == "launch_failed"
    assert observed["exit"] is None and not observed["output_validation_permitted"]
    assert observed == persisted(tmp_path)
    assert events == ["close_owned"]


def test_record_failure_before_launch_cannot_be_a_pass(tmp_path, monkeypatch):
    original = process._put

    def fail_record(path, value, **kwargs):
        if Path(path).name == "record.json":
            raise process._EvidenceWriteError("synthetic storage refusal")
        return original(path, value, **kwargs)

    monkeypatch.setattr(process, "_put", fail_record)
    monkeypatch.setattr(process.subprocess, "Popen", lambda *a, **kw: pytest.fail("launch"))
    with pytest.raises(process._EvidenceWriteError):
        invoke(tmp_path, "pass")
    assert (tmp_path / "call/command.json").is_file()
    assert not (tmp_path / "call/record.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows suspended Job launch contract")
def test_birth_record_failure_stops_created_root_and_propagates(tmp_path, monkeypatch):
    events = []
    child = FakeProcess(events)
    child.poll = lambda: None
    monkeypatch.setattr(process, "_WindowsJob", lambda: FakeOwner(events, tmp_path))
    monkeypatch.setattr(process.subprocess, "Popen", lambda *a, **kw: child)
    original = process._put
    failed = False

    def fail_once(path, value, **kwargs):
        nonlocal failed
        if value.get("execution") == "started" and not failed:
            failed = True
            raise process._EvidenceWriteError("synthetic birth persistence refusal")
        return original(path, value, **kwargs)

    monkeypatch.setattr(process, "_put", fail_once)
    with pytest.raises(process._EvidenceWriteError):
        invoke(tmp_path, "pass")
    assert events == ["kill_exact_created_root", "wait", "close_owned"]
    assert persisted(tmp_path)["execution"] == "observation_incomplete"
    assert not persisted(tmp_path)["output_validation_permitted"]


@pytest.mark.parametrize(
    "missing",
    [
        "kind",
        "natural_tree_complete",
        "controlled_termination",
        "control_events",
        "before_close",
        "after_cleanup",
    ],
)
def test_missing_critical_finalization_never_permits_output(tmp_path, missing):
    owner = FakeOwner([], tmp_path)
    complete = owner.close()
    assert process._natural_completion(complete)
    del complete[missing]
    assert not process._natural_completion(complete)
