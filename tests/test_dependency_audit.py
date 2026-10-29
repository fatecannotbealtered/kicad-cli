"""Audit resolved dependencies strictly without treating editable source as a registry package."""

import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import audit_dependencies as audit

ROOT = Path(__file__).parents[1]


def test_installed_build_runtime_matches_the_complete_lock():
    runtime = audit.check_runtime(ROOT)
    assert set(runtime) == {
        "attrs",
        "jsonschema",
        "jsonschema-specifications",
        "referencing",
        "rpds-py",
        "typing-extensions",
    }


def test_build_runtime_lock_drift_refuses(tmp_path):
    for name in ("pyproject.toml", "requirements-runtime.lock"):
        shutil.copyfile(ROOT / name, tmp_path / name)
    path = tmp_path / "requirements-runtime.lock"
    value = path.read_text(encoding="utf-8").replace("attrs==26.1.0", "attrs==0.0.0")
    path.write_text(value, encoding="utf-8")
    with pytest.raises(ValueError, match="differs"):
        audit.check_runtime(tmp_path)


def test_vendored_build_runtime_cannot_override_the_lock(tmp_path):
    for name in ("pyproject.toml", "requirements-runtime.lock"):
        shutil.copyfile(ROOT / name, tmp_path / name)
    metadata = tmp_path / ".vendor/attrs-0.0.0.dist-info"
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text("Name: attrs\nVersion: 0.0.0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="violates|differs"):
        audit.check_runtime(tmp_path)


def test_audit_uses_all_resolved_pins_and_preserves_vulnerability_failure(monkeypatch):
    runtime = audit.check_runtime(ROOT)
    frozen = "\n".join(f"{name}=={version}" for name, version in runtime.items()) + "\n"
    calls = []

    def freeze(argv, **kwargs):
        assert argv[-3:] == ["freeze", "--all", "--exclude-editable"]
        return frozen

    def run(argv):
        calls.append(argv)
        actual = Path(argv[-1]).read_text(encoding="utf-8")
        assert actual == frozen
        assert "--strict" in argv and "--disable-pip" in argv and "--no-deps" in argv
        assert "--skip-editable" not in argv and "--ignore-vuln" not in argv
        return 1  # A reported vulnerability must still fail the gate.

    monkeypatch.setattr(audit.subprocess, "check_output", freeze)
    monkeypatch.setattr(audit.subprocess, "call", run)
    assert audit.audit(runtime) == 1
    assert len(calls) == 1


@pytest.mark.parametrize(
    "frozen",
    [
        "",
        "attrs==26.1.0\n",
        "attrs @ https://invalid.example/a.whl\n",
        "jsonschema==4.26.0; python_version<'3.10'\n",
    ],
)
def test_incomplete_or_unresolved_audit_input_fails_closed(monkeypatch, frozen):
    monkeypatch.setattr(audit.subprocess, "check_output", lambda *a, **k: frozen)
    with pytest.raises(ValueError):
        audit.audit({"jsonschema": "4.26.0"})


def test_freeze_collection_failure_fails_closed(monkeypatch):
    def failure(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(audit.subprocess, "check_output", failure)
    with pytest.raises(subprocess.CalledProcessError):
        audit.audit({})
