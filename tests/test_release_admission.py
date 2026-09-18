"""Release admission fails closed without publishing or contacting KiCad."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("release_check", REPO / "scripts/check_release.py")
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)
VERSION = json.loads((REPO / "package.json").read_text(encoding="utf-8"))["version"]


@pytest.fixture
def candidate():
    return {
        "ok": True,
        "schema_version": "1.0",
        "data": {
            "tool": "kicad-cli",
            "version": VERSION,
            "release_readiness": {
                "level": "stable",
                "fcc_required": True,
                "mock_upstream_required": True,
                "live_smoke_required_for_stable": True,
                "fcc_status": "verified",
                "mock_upstream_status": "verified",
                "live_smoke_status": "verified",
                "required_evidence": sorted(GATE.REQUIRED_EVIDENCE),
                "evidence_held": ["synthetic test fixture, NOT actual release evidence"],
                "reason": "Synthetic admission tests only.",
            },
        },
    }


def test_stable_complete_declaration_is_admitted(candidate):
    assert GATE.validate(candidate, VERSION, f"v{VERSION}") == []


@pytest.mark.parametrize("level", ["beta", "unpublishable", "unknown", None, True])
def test_nonstable_candidates_cannot_reach_normal_publication(candidate, level):
    candidate["data"]["release_readiness"]["level"] = level
    assert GATE.validate(candidate, VERSION, f"v{VERSION}")


@pytest.mark.parametrize("name", ["fcc_status", "mock_upstream_status", "live_smoke_status"])
@pytest.mark.parametrize("status", ["missing", "unknown", "not_applicable", True, None])
def test_each_evidence_status_must_be_verified(candidate, name, status):
    candidate["data"]["release_readiness"][name] = status
    assert any(name in problem for problem in GATE.validate(candidate, VERSION, f"v{VERSION}"))


@pytest.mark.parametrize(
    "name", ["fcc_required", "mock_upstream_required", "live_smoke_required_for_stable"]
)
@pytest.mark.parametrize("value", [False, "true", 1, None])
def test_requirements_cannot_be_disabled_or_faked_with_truthiness(candidate, name, value):
    candidate["data"]["release_readiness"][name] = value
    assert any(name in problem for problem in GATE.validate(candidate, VERSION, f"v{VERSION}"))


@pytest.mark.parametrize("tag", ["", "main", "v9.9.9", "refs/tags/v1.0.0", "v1.0.0-beta.1"])
def test_exact_tag_identity_is_required(candidate, tag):
    assert GATE.validate(candidate, VERSION, tag)


@pytest.mark.parametrize("version", [None, 1, "01.0.0", "1.0", "1.0.0-beta.1", "1.0.0+meta"])
def test_stable_only_version_policy(candidate, version):
    candidate["data"]["version"] = version
    assert GATE.validate(candidate, version, f"v{version}")


@pytest.mark.parametrize("doc", [None, [], True, {"ok": "true"}, {"ok": True, "data": []}])
def test_bad_reference_shape_is_refused(doc):
    assert GATE.validate(doc, VERSION, f"v{VERSION}")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("required_evidence", []),
        ("required_evidence", [True]),
        ("required_evidence", "not an array"),
        ("evidence_held", []),
        ("evidence_held", [""]),
        ("evidence_held", [1]),
        ("reason", ""),
        ("reason", None),
    ],
)
def test_metadata_and_evidence_descriptions_are_required(candidate, name, value):
    candidate["data"]["release_readiness"][name] = value
    assert GATE.validate(candidate, VERSION, f"v{VERSION}")


def test_wrong_tool_version_schema_and_missing_readiness_are_refused(candidate):
    for key, value in (("tool", "another-tool"), ("version", "0.0.0"), ("release_readiness", None)):
        doc = copy.deepcopy(candidate)
        doc["data"][key] = value
        assert GATE.validate(doc, VERSION, f"v{VERSION}")
    candidate["schema_version"] = "unknown"
    assert GATE.validate(candidate, VERSION, f"v{VERSION}")


@pytest.mark.parametrize(
    "stdout", [b"{}\nnoise", b"{}\n{}", b'{"ok":true,"ok":false}', b'{"n":NaN}', b"\xff"]
)
def test_reference_protocol_noise_is_not_accepted(monkeypatch, stdout):
    monkeypatch.setattr(
        GATE.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout, b"")
    )
    with pytest.raises(GATE.GateError):
        GATE.read_reference()


@pytest.mark.parametrize("failure", ["nonzero", "timeout", "launch"])
def test_failed_reference_process_is_not_admitted(monkeypatch, failure):
    def fail(*args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args[0], 20)
        if failure == "launch":
            raise OSError("simulated")
        return subprocess.CompletedProcess(args[0], 1, b'{"ok":true}', b"do not replay")

    monkeypatch.setattr(GATE.subprocess, "run", fail)
    with pytest.raises(GATE.GateError):
        GATE.read_reference()


def test_gate_does_not_manufacture_live_fcc_evidence(monkeypatch, candidate):
    calls = []
    monkeypatch.setenv("KICAD_CLI_TRACE", "must-not-write")

    def fake(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps(candidate).encode(), b"")

    monkeypatch.setattr(GATE.subprocess, "run", fake)
    assert GATE.read_reference() == candidate
    argv, kwargs = calls[0]
    assert argv[:3] == [sys.executable, "-m", "kicad_cli.main"]
    assert "KICAD_CLI_TRACE" not in kwargs["env"]
    assert kwargs["cwd"] == REPO
    assert kwargs["timeout"] == 20


def test_frozen_gate_calls_exact_artifact_path(tmp_path, monkeypatch, candidate):
    binary = tmp_path / "artifact with spaces"
    binary.write_bytes(b"not executed: subprocess replaced below")
    seen = []

    def fake(argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(candidate).encode(), b"")

    monkeypatch.setattr(GATE.subprocess, "run", fake)
    assert GATE.read_reference(binary) == candidate
    assert seen == [[str(binary.resolve()), "reference", "--compact"]]
    with pytest.raises(GATE.GateError):
        GATE.read_reference(tmp_path / "missing")


def test_checker_entrypoint_reports_refusal_without_child_diagnostics(monkeypatch, capsys):
    def fail(_binary):
        raise GATE.GateError("candidate reference exited unsuccessfully")

    monkeypatch.setattr(GATE, "read_reference", fail)
    assert GATE.main(["--tag", f"v{VERSION}"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False and result["gate"] == "stable-release"


def test_current_candidate_is_actually_blocked_and_no_trace_written(tmp_path):
    trace = tmp_path / "not-live-evidence.txt"
    proc = subprocess.run(
        [sys.executable, "scripts/check_release.py", "--tag", f"v{VERSION}"],
        cwd=REPO,
        env=dict(os.environ, KICAD_CLI_TRACE=str(trace)),
        capture_output=True,
        timeout=30,
    )
    assert proc.returncode == 1, proc.stderr
    result = json.loads(proc.stdout)
    assert result["ok"] is False
    assert any("unpublishable" in problem for problem in result["problems"])
    assert not trace.exists()


def test_publish_dependency_chain_and_least_privilege_are_explicit():
    workflow = (REPO / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "permissions:\n  contents: read\n" in workflow
    assert "  build:\n    needs: preflight\n" in workflow
    assert "name: Publish release assets\n    needs: build\n" in workflow
    assert "name: Publish to npm\n    needs: release\n" in workflow
    preflight = workflow.split("  preflight:")[1].split("  build:")[0]
    assert "node scripts/check-version.js" in preflight
    assert 'python scripts/check_release.py --tag "$RELEASE_TAG"' in preflight
    build = workflow.split("  build:")[1].split("  release:")[0]
    assert build.index("Admit the actual frozen candidate") < build.index("Package release archive")
    assert "--binary dist/kicad-cli.exe" in build and "--binary dist/kicad-cli\n" in build
    assert "contents: write" not in build and "id-token: write" not in build


def test_python_version_has_one_runtime_source():
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    project = text.split("[project]\n")[1].split("\n[")[0]
    assert 'dynamic = ["version"]' in project
    assert not any(line.startswith("version =") for line in project.splitlines())
    assert 'version = { attr = "kicad_cli.__version__" }' in text


def test_built_python_distribution_uses_runtime_version(tmp_path):
    if importlib.util.find_spec("setuptools") is None or importlib.util.find_spec("wheel") is None:
        pytest.skip("setuptools/wheel build backend unavailable in this interpreter")
    for filename in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copyfile(REPO / filename, tmp_path / filename)
    (tmp_path / "kicad_cli").mkdir()
    shutil.copyfile(REPO / "kicad_cli/__init__.py", tmp_path / "kicad_cli/__init__.py")
    (tmp_path / "metadata").mkdir()
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from setuptools.build_meta import prepare_metadata_for_build_wheel; "
            "prepare_metadata_for_build_wheel('metadata')",
        ],
        cwd=tmp_path,
        capture_output=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    files = list((tmp_path / "metadata").glob("*.dist-info/METADATA"))
    assert len(files) == 1
    assert f"Version: {VERSION}\n" in files[0].read_text(encoding="utf-8")


def test_checker_entrypoint_admits_only_the_validated_fixture(candidate, monkeypatch, capsys):
    monkeypatch.setattr(GATE, "read_reference", lambda _binary: candidate)
    assert GATE.main(["--tag", f"v{VERSION}"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
        "gate": "stable-release",
        "problems": [],
    }


def test_reference_output_budget_is_enforced(monkeypatch):
    monkeypatch.setattr(
        GATE.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, b" " * (4 * 1024 * 1024 + 1), b""),
    )
    with pytest.raises(GATE.GateError, match="limit"):
        GATE.read_reference()


def test_installed_python_distribution_version_is_not_stale():
    from importlib.metadata import PackageNotFoundError, version

    try:
        installed = version("kicad-cli")
    except PackageNotFoundError:
        pytest.skip("editable distribution not installed locally; PR CI installs it")
    assert installed == VERSION


def test_release_gate_changes_are_machine_discoverable():
    proc = subprocess.run(
        [sys.executable, "-m", "kicad_cli.main", "changelog", "--compact"],
        cwd=REPO,
        capture_output=True,
        timeout=15,
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
    )
    assert proc.returncode == 0
    entries = json.loads(proc.stdout)["data"]["entries"]
    unreleased = next(entry for entry in entries if entry["version"] == "Unreleased")
    assert any(
        "Block the stable publishing workflow" in item for item in unreleased["changes"]["security"]
    )
