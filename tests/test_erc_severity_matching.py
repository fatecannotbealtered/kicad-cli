"""Preserve per-finding severity, including mixed pin-conflict matrix outcomes."""

import json
import subprocess
from types import SimpleNamespace

import pytest


@pytest.fixture
def matcher(tmp_path, monkeypatch):
    for key in ("KICAD_CLI_PYTHON", "KICAD_CLI_OFFICIAL", "KICAD_CLI_ROOT", "KICAD_CLI_DEMOS"):
        monkeypatch.setenv(key, str(tmp_path / ("absent-" + key)))
    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append(True)
        raise RuntimeError("Pure ERC matcher check cannot start a subprocess")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    import test_fileformat_erc as checks

    yield checks
    assert attempts == []


def finding(identity, severity, alternatives=()):
    return SimpleNamespace(
        rule="pin_to_pin",
        severity=severity,
        items=[SimpleNamespace(uuid=identity)],
        alternatives=set(alternatives) | {identity},
    )


def observed(identity, severity):
    return {"type": "pin_to_pin", "severity": severity, "items": [identity]}


def test_mixed_levels_match_each_finding_independent_of_order(matcher):
    native = [observed("a", "warning"), observed("b", "error")]
    own = [finding("b", "error"), finding("a", "warning")]
    assert matcher.compare(native, own) == ([], [])


def test_severity_swap_refuses_even_with_identical_type_and_level_totals(matcher):
    native = [observed("a", "warning"), observed("b", "error")]
    own = [finding("a", "error"), finding("b", "warning")]
    missing, extra = matcher.compare(native, own)
    assert len(missing) == len(extra) == 2


def test_alternative_representative_cannot_hide_a_changed_severity(matcher):
    missing, extra = matcher.compare([observed("a", "error")], [finding("b", "warning", ("a",))])
    assert len(missing) == len(extra) == 1


def test_missing_severity_is_rejected(matcher):
    with pytest.raises(KeyError):
        matcher.compare([{"type": "pin_to_pin", "items": ["a"]}], [finding("a", "error")])


def test_recorded_mixed_fixture_is_accepted_without_changing_any_rule(matcher, monkeypatch):
    recorded = json.loads(matcher.DESIGNS["erc"][1].read_bytes())["violations"]
    assert {v["severity"] for v in recorded if v["type"] == "pin_to_pin"} == {"warning", "error"}
    monkeypatch.setattr(matcher, "kicad_erc", lambda path: recorded)
    matcher.test_kicad_still_says_what_was_recorded("erc")
