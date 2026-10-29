"""Bounded host identity checks; no native process or public write is invoked."""

from __future__ import annotations

import hashlib
import os
from types import SimpleNamespace

import pytest

from kicad_cli import engineering_inputs, read_inputs, sync_inputs


def stat_copy(info, **changes):
    fields = {
        key: getattr(info, key)
        for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    }
    return SimpleNamespace(**dict(fields, **changes))


@pytest.mark.skipif(os.name != "nt", reason="Observed Windows stat/fstat ctime API difference")
def test_cross_api_ctime_difference_preserves_path_identity_and_stable_handle(
    tmp_path, monkeypatch
):
    path = tmp_path / "stable.bin"
    path.write_bytes(b"stable captured bytes")
    path_info = path.stat()
    real_fstat = os.fstat

    def handle_change_time(fd):
        return stat_copy(real_fstat(fd), st_ctime_ns=path_info.st_ctime_ns + 123456789)

    monkeypatch.setattr(sync_inputs.os, "fstat", handle_change_time)
    row, raw = sync_inputs._read(path, capture_identity=True)
    assert raw == b"stable captured bytes"
    assert row["identity"]["ctime_ns"] == str(path_info.st_ctime_ns)
    assert row["identity"]["inode"] == str(path_info.st_ino)
    assert sync_inputs._read(path, capture_identity=True)[0] == row


@pytest.mark.parametrize("field", ["st_ctime_ns", "st_mtime_ns", "st_size", "st_ino"])
def test_handle_identity_must_remain_fully_stable_during_read(tmp_path, monkeypatch, field):
    path = tmp_path / "input.bin"
    path.write_bytes(b"stable bytes")
    real_fstat = os.fstat
    calls = 0

    def changed_after_read(fd):
        nonlocal calls
        calls += 1
        info = real_fstat(fd)
        return stat_copy(info, **{field: getattr(info, field) + (1 if calls == 2 else 0)})

    monkeypatch.setattr(sync_inputs.os, "fstat", changed_after_read)
    with pytest.raises(sync_inputs.Drift):
        sync_inputs._read(path, capture_identity=True)


@pytest.mark.parametrize("field", ["st_dev", "st_ino", "st_size", "st_mtime_ns"])
def test_cross_api_shared_identity_still_must_match(tmp_path, monkeypatch, field):
    path = tmp_path / "input.bin"
    path.write_bytes(b"stable bytes")
    real_fstat = os.fstat

    def wrong_handle_identity(fd):
        info = real_fstat(fd)
        return stat_copy(info, **{field: getattr(info, field) + 1})

    monkeypatch.setattr(sync_inputs.os, "fstat", wrong_handle_identity)
    with pytest.raises(sync_inputs.Drift):
        sync_inputs._read(path, capture_identity=True)


def test_path_identity_must_remain_stable_even_when_handle_does(tmp_path, monkeypatch):
    path = tmp_path / "input.bin"
    path.write_bytes(b"stable bytes")
    real_stat = type(path).stat
    calls = 0

    def replaced_path(self, *args, **kwargs):
        nonlocal calls
        info = real_stat(self, *args, **kwargs)
        if self == path:
            calls += 1
            if calls >= 2:
                return stat_copy(info, st_ino=info.st_ino + 1)
        return info

    monkeypatch.setattr(type(path), "stat", replaced_path)
    with pytest.raises(sync_inputs.Drift):
        sync_inputs._read(path, capture_identity=True)


def test_same_bytes_replacement_remains_visible_only_to_opted_in_sync_identity(tmp_path):
    path = tmp_path / "input.bin"
    path.write_bytes(b"retained input bytes")
    legacy, _ = sync_inputs._read(path)
    bound, _ = sync_inputs._read(path, capture_identity=True)
    replacement = path.with_name("replacement.bin")
    replacement.write_bytes(path.read_bytes())
    os.replace(replacement, path)
    assert sync_inputs._read(path)[0] == legacy
    actual = sync_inputs._read(path, capture_identity=True)[0]
    assert actual["sha256"] == bound["sha256"]
    assert actual["identity"] != bound["identity"]


def test_missing_path_retains_legacy_shape_and_opted_in_absence_identity(tmp_path):
    path = tmp_path / "absent.bin"
    legacy, raw = sync_inputs._read(path)
    assert raw is None
    assert set(legacy) == {"path", "resolved", "present", "sha256"}
    bound, raw = sync_inputs._read(path, capture_identity=True)
    assert raw is None and bound == dict(legacy, identity=None)


def test_legacy_observation_and_allowed_engineering_replacement_keep_manifest_shape(
    tmp_path, monkeypatch
):
    board = tmp_path / "legacy.kicad_pcb"
    board.write_bytes(b"(kicad_pcb)")

    def old_single_argument_reader(path):
        return sync_inputs._read(path)

    monkeypatch.setattr(read_inputs, "_read", old_single_argument_reader)
    monkeypatch.setattr(engineering_inputs, "_read", old_single_argument_reader)
    observed = read_inputs.capture(board=board)
    assert all(set(row) == {"path", "resolved", "present", "sha256"} for row in observed.manifest)
    inputs = engineering_inputs.Inputs()
    inputs.add(board, required=True)
    replacement = board.with_name("replacement.kicad_pcb")
    changed = b"(kicad_pcb (version 20240108))"
    replacement.write_bytes(changed)
    os.replace(replacement, board)
    inputs.validate(replacements={str(board): hashlib.sha256(changed).hexdigest()})
