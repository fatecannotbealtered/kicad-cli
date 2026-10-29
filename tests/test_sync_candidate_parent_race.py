"""Preserved b68d158 public-API repro and fixed Windows race regressions.

The baseline fixture is the exact Git source, not a reconstruction of the unsafe
ordering. Windows junction execution and POSIX symlink execution are distinct.
Legacy source SHA256: 2a7baa8beb9d6cb1376ce2043869a29d2817b87d1c9739d54e46d634cce3089e.
"""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path

import pytest
import test_driver_sync_runtime as sync_tests
from test_candidate_output_backend import junction
from test_sync_candidate_review import candidate_command, engineering_bytes

from kicad_cli import candidate_output, document_transaction, sync_driver_adapter

case = sync_tests.case
WINDOWS = pytest.mark.skipif(os.name != "nt", reason="Actual Windows junction/API regression.")


def legacy_module():
    path = Path(__file__).parent / "fixtures/sync_candidate_b68d158.py.txt"
    loader = importlib.machinery.SourceFileLoader(
        "kicad_cli._legacy_sync_candidate_b68d158", str(path)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def legacy_parent_redirect(case, monkeypatch, *, windows):
    legacy = legacy_module()
    monkeypatch.setattr(sync_driver_adapter, "sync_candidate", legacy)
    parent = case[0].parent / "candidate-output"
    moved = parent.with_name("candidate-output-retained")
    outside = case[0].parent.parent / (case[0].parent.name + "-outside-grant")
    parent.mkdir()
    outside.mkdir()
    output = parent / "review.kicad_pcb"
    original = legacy.write_new
    witnessed = []

    def redirect_immediately_before_actual_write(path, raw):
        if Path(path).parent != parent:
            return original(path, raw)
        parent.rename(moved)
        if windows:
            junction(parent, outside)
        else:
            parent.symlink_to(outside, target_is_directory=True)
        try:
            original(path, raw)  # Real exclusive-create API; follows redirected parent.
            escaped = outside / Path(path).name
            assert escaped.read_bytes() == raw
            witnessed.append({"path": str(escaped), "sha256": hashlib.sha256(raw).hexdigest()})
        finally:
            parent.rmdir() if windows else parent.unlink()
            moved.rename(parent)

    monkeypatch.setattr(legacy, "write_new", redirect_immediately_before_actual_write)
    before = case[0].read_bytes()
    result = candidate_command(case, "--dry-run", output=output)
    assert not result["ok"], result
    assert result["error"]["code"] == "E_IO"
    assert len(witnessed) == 1
    assert list(outside.iterdir()) == [Path(witnessed[0]["path"])]
    assert not output.exists()
    assert case[0].read_bytes() == before
    (case[0].parent / "legacy-parent-race-witness.json").write_text(
        json.dumps(
            {
                "platform": "actual_windows_junction" if windows else "actual_posix_symlink",
                "baseline": "b68d1580726278258f3962eceb77c0612c50acba",
                "legacy_source_sha256": hashlib.sha256(
                    (Path(__file__).parent / "fixtures/sync_candidate_b68d158.py.txt").read_bytes()
                ).hexdigest(),
                "writes": witnessed,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


@WINDOWS
def test_preserved_b68_public_api_windows_junction_writes_outside_before_recheck(case, monkeypatch):
    legacy_parent_redirect(case, monkeypatch, windows=True)


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink repro; not Windows evidence.")
def test_preserved_b68_public_api_posix_symlink_writes_outside_before_recheck(case, monkeypatch):
    legacy_parent_redirect(case, monkeypatch, windows=False)


@WINDOWS
def test_fixed_public_api_blocks_parent_redirect_at_write_boundary(case, monkeypatch):
    parent = case[0].parent / "candidate-output"
    parent.mkdir()
    outside = case[0].parent.parent / (case[0].parent.name + "-outside-grant")
    outside.mkdir()
    original = candidate_output.File.write
    attempts = []

    def attempt_redirect(opened, raw):
        with pytest.raises(OSError):
            parent.rename(parent.with_name("relocated"))
        attempts.append(True)
        return original(opened, raw)

    monkeypatch.setattr(candidate_output.File, "write", attempt_redirect)
    before = case[0].read_bytes()
    result = candidate_command(case, "--dry-run", output=parent / "review.kicad_pcb")
    assert result["ok"], result
    assert attempts == [True]
    assert list(outside.iterdir()) == []
    assert case[0].read_bytes() == before


@WINDOWS
def test_parent_replaced_by_junction_before_anchor_acquisition_never_writes_outside(
    case, monkeypatch
):
    parent = case[0].parent / "candidate-output"
    moved = parent.with_name("retained-parent")
    outside = case[0].parent.parent / (case[0].parent.name + "-outside-grant")
    parent.mkdir()
    outside.mkdir()
    original = candidate_output.Directory.__init__
    acquisitions = []

    def replace_before_acquisition(directory, path):
        if Path(path) == parent:
            acquisitions.append(True)
            if len(acquisitions) == 2:
                parent.rename(moved)
                junction(parent, outside)
        return original(directory, path)

    monkeypatch.setattr(candidate_output.Directory, "__init__", replace_before_acquisition)
    before = case[0].read_bytes()
    try:
        result = candidate_command(case, "--dry-run", output=parent / "review.kicad_pcb")
        assert not result["ok"], result
        assert result["error"]["code"] in {"E_CONFLICT", "E_IO"}, result
        assert acquisitions == [True, True]
        assert list(outside.iterdir()) == []
        assert case[0].read_bytes() == before
    finally:
        if moved.exists():
            parent.rmdir()
            moved.rename(parent)


@WINDOWS
def test_source_drift_after_handle_readback_removes_owned_temporary(case, monkeypatch):
    original = document_transaction.persist

    def drift_after_capture(path, value, key):
        result = original(path, value, key)
        if Path(path).name == "preparation.json" and "identity" in value["temporary"]:
            source = case[0].with_suffix(".kicad_sch")
            source.write_bytes(source.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(document_transaction, "persist", drift_after_capture)
    before = case[0].read_bytes()
    result = candidate_command(case, "--dry-run")
    assert result["error"]["code"] == "E_CONFLICT", result
    assert not (case[0].parent / "review.kicad_pcb").exists()
    assert not list(case[0].parent.glob(".review.kicad_pcb.*.pending"))
    assert case[0].read_bytes() == before


@WINDOWS
def test_partial_candidate_write_failure_cleans_owned_file_and_preserves_inputs(case, monkeypatch):
    original = candidate_output.File.write

    def partial_then_fail(opened, raw):
        original(opened, raw[:5])
        raise OSError("fixture partial candidate write")

    monkeypatch.setattr(candidate_output.File, "write", partial_then_fail)
    before = engineering_bytes(case)
    result = candidate_command(case, "--dry-run")
    assert result["error"]["code"] == "E_IO", result
    assert not (case[0].parent / "review.kicad_pcb").exists()
    assert not list(case[0].parent.glob(".review.kicad_pcb.*.pending"))
    assert engineering_bytes(case) == before


@pytest.mark.skipif(os.name == "nt", reason="Actual unsupported-platform public API contract.")
def test_non_windows_candidate_api_refuses_explicitly_without_materialization(case):
    before = engineering_bytes(case)
    result = candidate_command(case, "--dry-run")
    assert result["error"]["code"] == "E_CONFLICT", result
    assert result["error"]["details"]["candidate_conflict"] == "unsupported_platform"
    assert engineering_bytes(case) == before
    assert not list(case[0].parent.glob(".review.kicad_pcb.*.pending"))
