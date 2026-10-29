"""SDK code must not enter either the checker archive or specification data."""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

import build
from kicad_cli import driver_runtime, sdk_loader


def test_checker_archive_excludes_both_sdk_locations(tmp_path):
    package = tmp_path / "package"
    for name in (
        "own.py",
        "_driver_sdk/runtime.py",
        "driver_spec/packages/tool_driver_sdk/verification.py",
    ):
        p = package / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# source\n", encoding="utf-8")
    archive = tmp_path / "sources.zip"
    files = build.archive_checker_sources(package, archive, "2.0.0")
    assert set(files) == {"own.py"}
    with zipfile.ZipFile(archive) as packed:
        assert set(packed.namelist()) == {"own.py", "source-manifest.json"}
        assert set(json.loads(packed.read("source-manifest.json"))["files"]) == {"own.py"}


def test_specification_stage_has_exact_data_and_no_sdk_source(tmp_path):
    original = driver_runtime.SPEC
    staged = build.specification_data(original, tmp_path / "spec")
    expected = {
        p.relative_to(original).as_posix(): p.read_bytes()
        for p in original.rglob("*")
        if p.is_file() and p.suffix != ".py" and "__pycache__" not in p.parts
    }
    observed = {
        p.relative_to(staged).as_posix(): p.read_bytes() for p in staged.rglob("*") if p.is_file()
    }
    assert expected == observed and "PIN.json" in observed
    assert not list(staged.rglob("*.py"))


def test_frozen_manifest_authenticates_external_duplicate(tmp_path, monkeypatch):
    reference = driver_runtime.declarations()["reference"]
    package = tmp_path / "package"
    staged = build.specification_data(driver_runtime.SPEC, package / "driver_spec")
    external = Path(driver_runtime.PACKAGE / "_driver_sdk").resolve()
    loaded = []
    monkeypatch.setattr(driver_runtime, "PACKAGE", package)
    monkeypatch.setattr(driver_runtime, "SPEC", staged)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("KICAD_CLI_DRIVER_SDK_PATH", str(external))
    monkeypatch.setattr(sdk_loader, "load", lambda path, root: loaded.append((path, root)))
    validation_root = driver_runtime._pin()
    assert loaded == [(external, package)]
    assert validation_root == external.parent / "driver_spec"
    from kicad_cli._driver_sdk.validation import ContractValidator

    validator = ContractValidator(validation_root)
    validator.validate_reference(reference)


def test_old_stage_cannot_reintroduce_sdk_source(tmp_path):
    old = tmp_path / "stage"
    old.mkdir()
    stale = old / "verification.py"
    stale.write_text("# old SDK source\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="must be empty"):
        build.specification_data(driver_runtime.SPEC, old)
    assert stale.read_text(encoding="utf-8") == "# old SDK source\n"


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_optional_windows_wmi_extension_is_not_bundled(tmp_path, monkeypatch, platform):
    calls = []
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(build.subprocess, "call", lambda argv: calls.append(argv) or 0)
    assert (
        build.main(["--work-dir", str(tmp_path / "work"), "--output", str(tmp_path / "dist")]) == 0
    )
    excluded = {
        calls[0][i + 1] for i, value in enumerate(calls[0][:-1]) if value == "--exclude-module"
    }
    assert ("_wmi" in excluded) is (platform == "win32")
    assert {"kicad_cli._driver_sdk", "kipy"} <= excluded
    hooks = [calls[0][i + 1] for i, value in enumerate(calls[0][:-1]) if value == "--runtime-hook"]
    assert hooks == (
        [str(build.ROOT / "kicad_cli/frozen_platform.py")] if platform == "win32" else []
    )
