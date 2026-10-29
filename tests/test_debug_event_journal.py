"""Synthetic debugger events: no child, KiCad process, or debugger is started."""

import ctypes as C
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))
import win_debugging as subject


def observer(tmp_path, monkeypatch, codes, *, interrupt=False):
    events = iter(codes)
    continued = []

    def receive(pointer, _timeout):
        event = C.cast(pointer, C.POINTER(subject.Event)).contents
        try:
            code, first = next(events)
        except StopIteration:
            if interrupt:
                raise OSError("synthetic observation interrupted") from None
            event.code, event.pid, event.tid = 5, 123, 456
            event.data.exit_code = 0
            return True
        event.code, event.pid, event.tid = 1, 123, 456
        event.data.exception.record.code = code
        event.data.exception.first_chance = first
        return True

    value = object.__new__(subject.Debugger)
    value.process = SimpleNamespace(pid=123, returncode=0, wait=lambda **kwargs: 0)
    value.directory = tmp_path
    value.kernel = SimpleNamespace(
        WaitForDebugEventEx=receive,
        ContinueDebugEvent=lambda pid, tid, how: continued.append((pid, tid, how)) or True,
    )
    value.modules, value.exceptions = [], []
    value.initial_breakpoint_seen = False
    value.address = lambda address: {"address": address}
    monkeypatch.setattr(subject, "file_version", lambda path: "fixture")
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    return value, continued


def test_event_flood_has_linear_persistence_and_preserves_handling(tmp_path, monkeypatch):
    value, continued = observer(tmp_path, monkeypatch, [(0x80000003, 1)] + [(0xE06D7363, 1)] * 200)
    saved_rows = []
    original = subject.write

    def measured(path, rows):
        if Path(path).name == "exceptions.json":
            saved_rows.append(len(rows))
        original(path, rows)

    monkeypatch.setattr(subject, "write", measured)
    result = value.monitor(None, 10)
    assert sum(saved_rows) <= 201, "Repeated full-list persistence is quadratic"
    journal = [
        json.loads(line) for line in (tmp_path / "exceptions.jsonl").read_text().splitlines()
    ]
    assert len(journal) == 201
    assert [row["event_index"] for row in journal] == list(range(201))
    assert result["exceptions"][0]["bootstrap_breakpoint"] is True
    assert all(row["first_chance"] for row in result["exceptions"])
    assert continued[0][2] == 0x00010002
    assert all(row[2] == 0x80010001 for row in continued[1:-1])
    assert continued[-1][2] == 0x00010002


def test_interruption_keeps_completed_event_prefix(tmp_path, monkeypatch):
    value, _ = observer(tmp_path, monkeypatch, [(0xE06D7363, 1)] * 2, interrupt=True)
    with pytest.raises(OSError, match="synthetic observation"):
        value.monitor(None, 10)
    journal = [
        json.loads(line) for line in (tmp_path / "exceptions.jsonl").read_text().splitlines()
    ]
    assert [row["event_index"] for row in journal] == [0, 1]
    assert not (tmp_path / "result.json").exists()


def test_fatal_event_persists_before_diagnostic_capture(tmp_path, monkeypatch):
    value, _ = observer(tmp_path, monkeypatch, [(0xC0000409, 1)])

    def capture(_event):
        first = json.loads((tmp_path / "exceptions.jsonl").read_text().splitlines()[0])
        assert first["event"]["code"] == "0xc0000409"
        raise OSError("synthetic capture unavailable")

    value.context_and_dump = capture
    result = value.monitor(None, 10)
    assert result["exceptions"][0]["capture_refused_or_failed"] == "synthetic capture unavailable"
    journal = [
        json.loads(line) for line in (tmp_path / "exceptions.jsonl").read_text().splitlines()
    ]
    assert [row["kind"] for row in journal] == ["exception", "capture"]
    assert [row["event_index"] for row in journal] == [0, 0]


def test_existing_journal_is_never_reused(tmp_path, monkeypatch):
    path = tmp_path / "exceptions.jsonl"
    path.write_bytes(b"original\n")
    value, _ = observer(tmp_path, monkeypatch, [])
    with pytest.raises(FileExistsError):
        value.monitor(None, 10)
    assert path.read_bytes() == b"original\n"
