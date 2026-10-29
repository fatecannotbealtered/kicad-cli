"""Discovery is executable metadata, not a second hand-maintained manual."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from kicad_cli import __version__, kicad_env  # noqa: E402


def run(*argv):
    proc = subprocess.run(
        [sys.executable, "-m", "kicad_cli.main", *argv, "--compact"],
        cwd=REPO,
        capture_output=True,
        timeout=20,
        env=dict(os.environ, KICAD_CLI_STRICT="1", PYTHONIOENCODING="utf-8"),
    )
    return json.loads(proc.stdout), proc


def test_canonical_reference_keys_and_error_mapping_are_exposed():
    doc, proc = run("reference")
    assert proc.returncode == 0 and doc["ok"]
    contract = json.loads((REPO / "contract" / "contract.json").read_text(encoding="utf-8"))
    required = contract["self_description"]["reference"]["required_top_keys"]
    assert set(required) <= doc["data"].keys()
    assert doc["data"]["risk_tier"] == "T1"
    expected = dict(contract["error_codes"]["core"])
    extension = REPO / "contract" / "contract-ext.json"
    if extension.is_file():
        declared = json.loads(extension.read_text(encoding="utf-8"))["error_codes"]
        assert not set(declared) & set(expected), "extensions must not redefine core codes"
        expected.update(declared)
    assert all(
        doc["data"]["error_codes"][k][field] == value[field]
        for k, value in expected.items()
        for field in ("exit", "retryable")
    )
    assert {p["name"] for p in doc["data"]["global_options"]} >= {
        "format",
        "compact",
        "dry-run",
        "confirm",
    }


def test_context_exposes_the_tool_version_not_just_kicad_version():
    doc, proc = run("context")
    assert proc.returncode == 0
    assert doc["data"]["version"] == __version__
    assert doc["data"]["credentials"]["configured"] is False


def test_scoped_reference_preserves_the_required_envelope_and_reduces_payload():
    full, full_proc = run("reference")
    doc, proc = run("reference", "--command", "board route rewidth")
    assert proc.returncode == 0
    assert doc["data"]["command"]["path"] == "board route rewidth"
    assert list(doc["data"]["schemas"]) == ["power_receipt"]
    assert doc["data"]["view"] == "command"
    assert len(proc.stdout) < len(full_proc.stdout) / 2
    assert "handler" not in doc["data"]["command"]


def test_unknown_reference_target_has_a_structured_failure():
    doc, proc = run("reference", "--command", "board nonexistent")
    assert proc.returncode == 2
    assert doc["error"]["code"] == "E_VALIDATION"
    assert doc["error"]["retryable"] is False


def test_parameter_semantics_and_constraints_are_discoverable():
    doc, _ = run("reference", "--command", "board route rewidth")
    params = {p["name"]: p for p in doc["data"]["command"]["params"]}
    assert "mode" not in params
    assert "explicitly selected" in doc["data"]["command"]["description"]
    assert "when" not in params["nets"]
    assert params["neck"]["unit"] == "mm"
    assert params["nets"]["multiple"] and params["classes"]["multiple"]
    assert "no-verify" not in params
    all_doc, _ = run("reference")
    assert "sync_receipt" in all_doc["data"]["schemas"]
    assert "sch_sync_preview" not in all_doc["data"]["schemas"]


def test_current_candidate_does_not_claim_historical_live_evidence():
    """A live claim must point at a record for *this* candidate.

    This used to assert `live_smoke_status == "missing"`, which made recording
    real evidence fail the suite -- a test that fires on progress rather than
    on regression. What must not happen is the claim being made on the strength
    of the 1.0.0 record, which describes a different tree.
    """
    doc, _ = run("reference")
    readiness = doc["data"]["release_readiness"]
    assert readiness["level"] == "unpublishable"
    assert readiness["fcc_status"] in {"missing", "unknown"}

    if readiness.get("live_smoke_status", "unknown") == "verified":
        held = " ".join(readiness["evidence_held"]) + " " + readiness["reason"]
        # `live-smoke-1.0.0.md` on its own is the historical baseline. A claim
        # has to name a record carrying a source commit, which is what
        # distinguishes this candidate from the one that shipped.
        records = set(re.findall(r"live-smoke-[0-9.]+\+[0-9a-f]{7,}\.md", held))
        assert records, "a verified live claim names no record for this candidate"
        for name in records:
            assert (REPO / "docs" / "evidence" / name).is_file(), f"{name} does not exist"
    else:
        assert readiness.get("live_smoke_status", "unknown") in ("missing", "unknown")

    doctor, _ = run("doctor")
    check = next(c for c in doctor["data"]["checks"] if c["check"] == "release_readiness")
    assert check["status"] == "fail" and check["fix"]


@pytest.fixture
def clear_probe_cache():
    kicad_env.kicad_version.cache_clear()
    yield
    kicad_env.kicad_version.cache_clear()


def test_discovery_and_version_share_one_probe(monkeypatch, clear_probe_cache):
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"test-kicad-version\n", b"")

    monkeypatch.delenv(kicad_env.ENV_PYTHON, raising=False)
    monkeypatch.delenv(kicad_env.ENV_ROOT, raising=False)
    monkeypatch.setattr(kicad_env.subprocess, "run", fake_run)
    python = kicad_env.find_python()
    assert python == sys.executable
    assert kicad_env.kicad_version(python) == "test-kicad-version"
    assert len(calls) == 1


def test_failed_probe_is_not_treated_as_a_valid_interpreter(monkeypatch, clear_probe_cache):
    monkeypatch.setattr(
        kicad_env.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, b"", b"unavailable"),
    )
    assert not kicad_env._can_import_pcbnew("missing-python")
    assert kicad_env.kicad_version("missing-python") is None


def test_frozen_cli_does_not_probe_itself_as_a_python_interpreter(monkeypatch, clear_probe_cache):
    monkeypatch.delenv(kicad_env.ENV_PYTHON, raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(kicad_env, "_candidate_roots", lambda: [])
    monkeypatch.setattr(kicad_env, "_can_import_pcbnew", lambda _p: pytest.fail("self-probe"))
    assert kicad_env.find_python() is None
