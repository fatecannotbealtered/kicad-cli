"""Adversarial staged-input binding checks; every native call is an inert stub."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from kicad_cli import move_preflight as gate
from kicad_cli._driver_sdk.models import DriverError
from kicad_cli.fileformat.board import Board


@pytest.fixture
def bounded_case(tmp_path, monkeypatch):
    fixture = Path(__file__).parent / "fixtures/sch_edit"
    project = tmp_path / "original"
    project.mkdir()
    for name in ("edit.kicad_pcb", "edit.kicad_pro", "fp-lib-table"):
        shutil.copyfile(fixture / name, project / name)
    shutil.copytree(fixture / "fixture.pretty", project / "fixture.pretty")
    board = project / "edit.kicad_pcb"
    board.with_suffix(".kicad_dru").write_bytes(b"(version 1)\n")
    config = tmp_path / "user-configuration"
    (config / "10.0/empty").mkdir(parents=True)
    (config / "10.0/kicad_common.json").write_text(
        '{"system":{"working_dir":"original"},"environment":{"vars":{}}}', encoding="utf-8"
    )
    (config / "10.0/pcbnew.json").write_text('{"keep":true}', encoding="utf-8")
    installed = tmp_path / "installation"
    (installed / "bin").mkdir(parents=True)
    official = installed / "bin/kicad-cli.exe"
    official.write_bytes(b"inert-not-an-executable")
    monkeypatch.setenv("KICAD_CONFIG_HOME", str(config))
    monkeypatch.setenv("KICAD_CLI_ROOT", str(installed))
    monkeypatch.setenv("KICAD_CLI_OFFICIAL", str(official))
    inputs = gate.move_preflight_inputs
    monkeypatch.setattr(inputs.kicad_env, "find_kicad_root", lambda: str(installed))
    monkeypatch.setattr(inputs.kicad_env, "find_official_cli", lambda: str(official))
    seen = []
    case = {"root": tmp_path, "board": board, "config": config, "seen": seen, "hook": None}

    def native(command, *, cwd, env, output, timeout):
        label = output.name
        seen.append(label)
        workspace = output.parent.parent
        case["workspace"] = workspace
        config_root = Path(env["KICAD_CONFIG_HOME"])
        for name in gate.DEFAULT_PREFERENCES:
            path = config_root / name
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}", encoding="utf-8")
        common = config_root / "10.0/kicad_common.json"
        value = json.loads(common.read_bytes())
        value.setdefault("system", {})["working_dir"] = str(cwd)
        common.write_text(json.dumps(value), encoding="utf-8")
        output.mkdir()
        stdout = b"10.0.6\n" if "--version" in command else b"DRC completed\n"
        record = {
            "execution": "completed_for_output_validation",
            "exit": 0,
            "root_pid": 123,
            "output_validation_permitted": True,
        }
        for name, raw in (("stdout", stdout), ("stderr", b"")):
            (output / name).write_bytes(raw)
            record[name + "_sha256"] = gate._sha(raw)
            record[name + "_bytes"] = len(raw)
        (output / "record.json").write_text(json.dumps(record), encoding="utf-8")
        if "--version" not in command:
            report = {
                "source": Path(command[-1]).name,
                "date": "2026-10-16T00:00:00",
                "kicad_version": "10.0.6",
                "coordinate_units": "mm",
                "violations": [],
                "unconnected_items": [],
                "schematic_parity": [],
                "ignored_checks": [],
                "included_severities": ["error", "warning", "exclusion"],
            }
            Path(command[command.index("-o") + 1]).write_text(json.dumps(report), encoding="utf-8")
            if label == "prepare-drc":
                Path(command[-1]).with_suffix(".kicad_prl").write_text("{}", encoding="utf-8")
        if case["hook"]:
            case["hook"](label, workspace, output, record)
        return record

    monkeypatch.setattr(gate.move_native_process, "run", native)
    return case


def _prepare(case):
    path = case["board"]
    return gate.prepare(
        path,
        Board.load(path),
        gate.move_readback.observe(path, ["R1"]),
        {"R1": (100.0, 100.5)},
        state_dir=case["root"] / "state",
        operation_id="new-isolated-operation",
    )


def _mutate(workspace, target):
    stage, kind = target.split(":")
    root = workspace / stage
    if kind == "candidate":
        path = root / "project/edit.kicad_pcb"
        path.write_bytes(path.read_bytes().replace(b"100.5", b"101.5", 1))
    elif kind == "library":
        path = next((root / "libraries").rglob("*.kicad_mod"))
        path.write_bytes(path.read_bytes() + b"\n; changed library\n")
    elif kind == "rules":
        (root / "project/edit.kicad_dru").write_bytes(b"(version 1)\n; changed rule\n")
    elif kind == "configuration":
        (root / "configuration/10.0/pcbnew.json").write_text('{"keep":false}', encoding="utf-8")
    elif kind == "table":
        (root / "project/fp-lib-table").write_bytes(b"(fp_lib_table)\n")
    else:
        raise AssertionError(target)


def test_clean_projection_freezes_exact_inputs_before_formal_calls(bounded_case):
    original = {
        path: path.read_bytes()
        for directory in (bounded_case["board"].parent, bounded_case["config"])
        for path in directory.rglob("*")
        if path.is_file()
    }
    result = _prepare(bounded_case)
    assert result["status"] == "passed"
    assert bounded_case["seen"] == [
        "prepare-version",
        "prepare-drc",
        "baseline-drc",
        "candidate-drc",
    ]
    assert result["staging"]["formal_configuration_frozen"] is True
    assert result["staging"]["configuration_prepared"] is True
    frozen = json.loads((bounded_case["workspace"] / "FORMAL-FROZEN.json").read_bytes())
    assert frozen["formal_configuration_frozen"] is True
    assert frozen["initial_staged_files"] == result["staging"]["files"]
    assert all(path.read_bytes() == raw for path, raw in original.items())
    for label in ("baseline", "candidate"):
        stage = result["staging"]["stages"][label]
        assert gate._sha(Path(stage["board"]).read_bytes()) == result[label + "_sha256"]
        assert Path(stage["board"]).with_suffix(".kicad_prl").read_bytes() == b"{}"
    plan = {"extensions": {"preflight": result, "moves": {"R1": [100.0, 100.5]}}}
    assert gate.validate(plan, state_dir=bounded_case["root"] / "state") is result


@pytest.mark.parametrize("kind", ["missing", "changed"])
def test_frozen_preflight_requires_original_moves(bounded_case, kind):
    path = bounded_case["board"]
    original = path.read_bytes()
    result = _prepare(bounded_case)
    plan = {"extensions": {"preflight": result, "moves": {"R1": [100.0, 100.5]}}}
    assert gate.validate(plan, state_dir=bounded_case["root"] / "state") is result
    if kind == "missing":
        del plan["extensions"]["moves"]
    else:
        plan["extensions"]["moves"] = {"R1": [100.0, 101.5]}
    with pytest.raises(DriverError) as found:
        gate.validate(plan, state_dir=bounded_case["root"] / "state")
    assert found.value.code == "E_CONFLICT"
    assert found.value.dispatch_state == "not_started"
    assert found.value.details["preflight_status"] == "drifted"
    assert found.value.details["engineering_write_started"] is False
    assert path.read_bytes() == original
    assert bounded_case["seen"] == [
        "prepare-version",
        "prepare-drc",
        "baseline-drc",
        "candidate-drc",
    ]


TARGETS = [
    "candidate:candidate",
    "preparation:library",
    "baseline:library",
    "candidate:library",
    "preparation:rules",
    "baseline:rules",
    "candidate:rules",
    "preparation:configuration",
    "baseline:configuration",
    "candidate:configuration",
    "preparation:table",
    "baseline:table",
    "candidate:table",
]


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("phase", ["prepare-drc", "baseline-drc"])
def test_stage_drift_during_native_call_stops_before_next_call(bounded_case, target, phase):
    def hook(label, workspace, output, record):
        if label == phase:
            _mutate(workspace, target)

    bounded_case["hook"] = hook
    with pytest.raises(DriverError) as found:
        _prepare(bounded_case)
    assert found.value.code == "E_NATIVE_FAILURE"
    assert found.value.details["engineering_write_started"] is False
    expected = ["prepare-version", "prepare-drc"]
    if phase == "baseline-drc":
        expected.append("baseline-drc")
    assert bounded_case["seen"] == expected
    assert not (bounded_case["workspace"] / "RESULT.json").exists()


@pytest.mark.parametrize("target", TARGETS)
def test_drift_after_preparation_is_not_a_new_formal_baseline(bounded_case, monkeypatch, target):
    original = gate._prepare_config

    def after_preparation(staging, workspace, official, environ, guard):
        result = original(staging, workspace, official, environ, guard)
        _mutate(workspace, target)
        return result

    monkeypatch.setattr(gate, "_prepare_config", after_preparation)
    with pytest.raises(DriverError):
        _prepare(bounded_case)
    assert bounded_case["seen"] == ["prepare-version", "prepare-drc"]


@pytest.mark.parametrize("kind", ["source", "relocation", "extra-file"])
def test_initial_staged_manifest_is_consumed_before_any_call(bounded_case, monkeypatch, kind):
    original = gate.move_preflight_inputs.stage

    def stage(binding, raw):
        staged = original(binding, raw)
        workspace = Path(staged["workspace"])
        if kind == "source":
            _mutate(workspace, "candidate:candidate")
        elif kind == "relocation":
            staged["relocations"][0]["sha256"] = "0" * 64
            (workspace / "STAGING.json").write_text(json.dumps(staged), encoding="utf-8")
        else:
            (workspace / "candidate/project/unreviewed.kicad_dru").write_bytes(b"new rule")
        return staged

    monkeypatch.setattr(gate.move_preflight_inputs, "stage", stage)
    with pytest.raises(DriverError):
        _prepare(bounded_case)
    assert bounded_case["seen"] == []


@pytest.mark.parametrize("kind", ["record", "stdout", "stderr"])
def test_successful_native_return_must_match_persisted_evidence(bounded_case, kind):
    def hook(label, workspace, output, record):
        if label == "prepare-version":
            (output / ("record.json" if kind == "record" else kind)).write_bytes(b"replaced")

    bounded_case["hook"] = hook
    with pytest.raises(DriverError) as found:
        _prepare(bounded_case)
    assert found.value.code == "E_NATIVE_FAILURE"
    assert bounded_case["seen"] == ["prepare-version"]


@pytest.mark.parametrize("kind", ["bytes", "same-byte-replacement", "returned-value"])
def test_report_cannot_change_between_native_validation_and_first_freeze(
    bounded_case, monkeypatch, kind
):
    original = gate._native

    def native(stage, workspace, official, label, **kwargs):
        result = original(stage, workspace, official, label, **kwargs)
        if label == "baseline-drc":
            path = Path(result["report"])
            if kind == "bytes":
                path.write_bytes(path.read_bytes() + b"\n")
            elif kind == "same-byte-replacement":
                replacement = path.with_suffix(".replacement")
                replacement.write_bytes(path.read_bytes())
                replacement.replace(path)
            else:
                result["value"]["date"] = "different returned report"
        return result

    monkeypatch.setattr(gate, "_native", native)
    with pytest.raises(DriverError):
        _prepare(bounded_case)
    assert bounded_case["seen"] == ["prepare-version", "prepare-drc", "baseline-drc"]


def test_previous_report_is_protected_while_candidate_consumer_runs(bounded_case):
    def hook(label, workspace, output, record):
        if label == "candidate-drc":
            path = workspace / "baseline-drc.json"
            path.write_bytes(path.read_bytes() + b"\n")

    bounded_case["hook"] = hook
    with pytest.raises(DriverError):
        _prepare(bounded_case)
    assert bounded_case["seen"][-1] == "candidate-drc"
    assert not (bounded_case["workspace"] / "RESULT.json").exists()


def test_temporarily_substituting_and_restoring_candidate_is_not_accepted(bounded_case):
    def hook(label, workspace, output, record):
        if label == "baseline-drc":
            path = workspace / "candidate/project/edit.kicad_pcb"
            original, metadata = path.read_bytes(), path.stat()
            path.write_bytes((workspace / "baseline/project/edit.kicad_pcb").read_bytes())
            path.write_bytes(original)
            os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1_000_000_000))

    bounded_case["hook"] = hook
    with pytest.raises(DriverError):
        _prepare(bounded_case)
    assert bounded_case["seen"] == ["prepare-version", "prepare-drc", "baseline-drc"]


@pytest.mark.parametrize("kind", ["invalid-report", "timeout"])
def test_preparation_incomplete_never_starts_formal_consumer(bounded_case, kind):
    def hook(label, workspace, output, record):
        if label == "prepare-drc":
            if kind == "invalid-report":
                (workspace / "prepare-drc.json").write_bytes(b"{}")
            else:
                record.update(execution="timeout", exit=5)

    bounded_case["hook"] = hook
    with pytest.raises(DriverError) as found:
        _prepare(bounded_case)
    assert found.value.code == ("E_TIMEOUT" if kind == "timeout" else "E_NATIVE_FAILURE")
    assert bounded_case["seen"] == ["prepare-version", "prepare-drc"]


def test_completed_preflight_payload_matches_published_schema(bounded_case):
    """Real projected payload plus inert native records, never an engineering pass."""
    from kicad_cli import driver_runtime

    result = _prepare(bounded_case)
    with driver_runtime.create_cli(home=bounded_case["root"] / "schema-only-home") as cli:
        checked = cli.validator.validate_schema(gate.SCHEMA, result, cli.reference["json_schemas"])
    assert checked == result
    assert checked["source"]["official_native"] is True
    assert checked["staging"]["formal_configuration_frozen"] is True
    assert bounded_case["seen"] == [
        "prepare-version",
        "prepare-drc",
        "baseline-drc",
        "candidate-drc",
    ]
