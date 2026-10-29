"""Check fail-closed native output and frozen-preflight consumption without KiCad."""

import copy
import json
from pathlib import Path

import pytest

from kicad_cli import move_preflight as gate
from kicad_cli._driver_sdk.models import DriverError


def test_formal_configuration_allows_only_declared_working_directory():
    before = {
        "10.0/kicad_common.json": {
            "system": {"working_dir": "prepare"},
            "environment": {"vars": {"KEEP": "same"}},
        }
    }
    after = copy.deepcopy(before)
    after["10.0/kicad_common.json"]["system"]["working_dir"] = "candidate"
    assert gate._config_change(before, after, ["prepare", "candidate"])["changes"] == [
        {
            "path": "10.0/kicad_common.json",
            "pointer": "/system/working_dir",
            "before": "prepare",
            "after": "candidate",
        }
    ]


@pytest.mark.parametrize("drift", ["new-file", "removed-file", "rules", "variables", "outside-dir"])
def test_formal_configuration_drift_is_never_normalised_away(drift):
    before = {
        "10.0/kicad_common.json": {
            "system": {"working_dir": "prepare"},
            "environment": {"vars": {"KEEP": "same"}},
        },
        "10.0/pcbnew.json": {"drc": {"ignore": []}},
    }
    after = copy.deepcopy(before)
    if drift == "new-file":
        after["10.0/new.json"] = {}
    elif drift == "removed-file":
        del after["10.0/pcbnew.json"]
    elif drift == "rules":
        after["10.0/pcbnew.json"]["drc"]["ignore"] = ["shorting_items"]
    elif drift == "variables":
        after["10.0/kicad_common.json"]["environment"]["vars"]["KEEP"] = "other"
    else:
        after["10.0/kicad_common.json"]["system"]["working_dir"] = "unreviewed"
    with pytest.raises(ValueError):
        gate._config_change(before, after, ["prepare", "candidate"])


def test_preparation_defaults_are_explicit_and_formal_additions_are_rejected():
    after = {name: {} for name in gate.DEFAULT_PREFERENCES}
    assert len(gate._config_change({}, after, [], preparation=True)["added_default_files"]) == 8
    with pytest.raises(ValueError):
        gate._config_change({}, after, [])
    with pytest.raises(ValueError):
        gate._config_change({}, {"10.0/unexpected.json": {}}, [], preparation=True)


def _stage(tmp_path):
    project, config = tmp_path / "project", tmp_path / "config"
    project.mkdir()
    config.mkdir()
    board = project / "design.kicad_pcb"
    board.write_text("preserved source bytes", encoding="utf-8")
    return {"project": str(project), "board": str(board), "config_root": str(config)}


def _report(stage):
    return {
        "source": Path(stage["board"]).name,
        "date": "2026-10-16T00:00:00",
        "kicad_version": "10.0.6",
        "coordinate_units": "mm",
        "violations": [],
        "unconnected_items": [],
        "schematic_parity": [],
        "ignored_checks": [],
        "included_severities": ["error", "warning", "exclusion"],
    }


def _persist_native_record(output, record):
    output.mkdir()
    for name in ("stdout", "stderr"):
        (output / name).write_bytes(b"")
        record[name + "_sha256"] = gate._sha(b"")
        record[name + "_bytes"] = 0
    (output / "record.json").write_text(json.dumps(record), encoding="utf-8")
    return record


@pytest.mark.parametrize(
    "mode,code,started",
    [
        ("native_nonzero", "E_NATIVE_FAILURE", True),
        ("native_fatal_exit", "E_NATIVE_FAILURE", True),
        ("timeout", "E_TIMEOUT", True),
        ("launch_failed", "E_NATIVE_FAILURE", False),
        ("observation_incomplete", "E_NATIVE_FAILURE", True),
    ],
)
def test_failed_native_call_is_not_reinterpreted_as_a_clean_report(
    tmp_path, monkeypatch, mode, code, started
):
    stage = _stage(tmp_path)
    seen = []

    def native(command, **kwargs):
        seen.append(command)
        return {
            "execution": mode,
            "exit": 5,
            "root_pid": 123 if started else None,
            "output_validation_permitted": True,
        }

    monkeypatch.setattr(gate.move_native_process, "run", native)
    with pytest.raises(DriverError) as found:
        gate._native(stage, tmp_path, "explicit-official", "formal")
    assert found.value.code == code
    assert found.value.details["native_call_started"] is started
    assert found.value.details["engineering_write_started"] is False
    assert found.value.details["automatic_retry"] is False
    assert len(seen) == 1
    assert Path(stage["board"]).read_text(encoding="utf-8") == "preserved source bytes"


@pytest.mark.parametrize(
    "drift",
    [
        "missing",
        "invalid-json",
        "duplicate-key",
        "missing-group",
        "nonfinite",
        "wrong-source",
        "version",
    ],
)
def test_exit_zero_with_missing_or_bad_report_is_incomplete(tmp_path, monkeypatch, drift):
    stage = _stage(tmp_path)

    def native(command, **kwargs):
        report = _report(stage)
        if drift == "missing-group":
            del report["unconnected_items"]
        elif drift == "wrong-source":
            report["source"] = "different.kicad_pcb"
        elif drift == "version":
            report["kicad_version"] = "different"
        raw = json.dumps(report)
        if drift == "invalid-json":
            raw = "{"
        elif drift == "duplicate-key":
            raw = '{"source":"x","source":"y"}'
        elif drift == "nonfinite":
            raw = raw[:-1] + ',"extra":NaN}'
        if drift != "missing":
            Path(command[command.index("-o") + 1]).write_text(raw, encoding="utf-8")
        return _persist_native_record(
            kwargs["output"],
            {
                "execution": "completed_for_output_validation",
                "exit": 0,
                "output_validation_permitted": True,
            },
        )

    monkeypatch.setattr(gate.move_native_process, "run", native)
    with pytest.raises(DriverError) as found:
        gate._native(stage, tmp_path, "explicit-official", "formal")
    assert found.value.code == "E_NATIVE_FAILURE"
    assert found.value.details["native_call_started"] is True


def test_zero_exit_can_report_violations_without_being_a_clean_design(tmp_path, monkeypatch):
    stage = _stage(tmp_path)

    def native(command, **kwargs):
        report = _report(stage)
        report["violations"] = [
            {
                "type": "shorting_items",
                "description": "observed short",
                "severity": "error",
                "items": [],
            }
        ]
        Path(command[command.index("-o") + 1]).write_text(json.dumps(report), encoding="utf-8")
        assert Path(kwargs["cwd"]) == Path(stage["project"])
        assert kwargs["env"]["KICAD_CONFIG_HOME"] == stage["config_root"]
        assert not {"--save-board", "--refill-zones", "--schematic-parity"} & set(command)
        return _persist_native_record(
            kwargs["output"],
            {
                "execution": "completed_for_output_validation",
                "exit": 0,
                "output_validation_permitted": True,
            },
        )

    monkeypatch.setattr(gate.move_native_process, "run", native)
    result = gate._native(stage, tmp_path, "explicit-official", "formal")
    assert result["violation_result"] is True
    assert result["value"]["violations"][0]["type"] == "shorting_items"


@pytest.mark.parametrize(
    "drift", ["candidate", "report", "new-file", "replacement", "blocked", "missing-preflight"]
)
def test_frozen_artifacts_cannot_be_replaced_at_confirmation(tmp_path, monkeypatch, drift):
    root = tmp_path / "move-preflights"
    workspace = root / "new-owned-operation"
    workspace.mkdir(parents=True)
    candidate, report = workspace / "candidate.kicad_pcb", workspace / "report.json"
    candidate.write_bytes(b"exact checked bytes")
    report.write_bytes(b"bound report")
    placement = {"status": "passed", "scope": "synthetic placement witness for artifact-only test"}
    (workspace / "PLACEMENT-GUARD.json").write_text(json.dumps(placement), encoding="utf-8")
    data = {
        "schema_version": 1,
        "version": gate.VERSION,
        "status": "passed",
        "workspace": str(workspace),
        "input_binding": {},
        "staging": {
            "stages": {
                "candidate": {"board": str(candidate)},
                "baseline": {"board": str(candidate)},
            }
        },
        "candidate_sha256": gate._sha(candidate.read_bytes()),
        "placement_guard": placement,
        "artifacts": gate._files(workspace),
    }
    plan = {"extensions": {"preflight": data, "moves": {"R1": [0, 0]}}}
    monkeypatch.setattr(gate.move_preflight_inputs, "validate", lambda binding: None)
    monkeypatch.setattr(gate.move_guard, "check", lambda *args: placement)
    assert gate.validate(plan, state_dir=tmp_path) is data
    if drift == "candidate":
        candidate.write_bytes(b"another move")
    elif drift == "report":
        report.write_bytes(b"other report")
    elif drift == "new-file":
        (workspace / "new-input").write_bytes(b"new")
    elif drift == "replacement":
        replacement = workspace / "replacement"
        replacement.write_bytes(report.read_bytes())
        replacement.replace(report)
    elif drift == "blocked":
        data["status"] = "blocked"
    else:
        del plan["extensions"]["preflight"]
    with pytest.raises(DriverError) as found:
        gate.validate(plan, state_dir=tmp_path)
    assert found.value.code == "E_CONFLICT"
    assert found.value.dispatch_state == "not_started"
