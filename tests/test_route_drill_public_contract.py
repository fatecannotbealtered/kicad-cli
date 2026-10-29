"""Finite planner witness/public discovery contract; no routing or subprocess execution."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from test_move_unrouted_reselection import saved_board

from bench.persistence_acceptance.surface_review import review_command
from kicad_cli import driver_runtime
from kicad_cli._driver_sdk.models import DriverError
from kicad_cli.native.smd_drill_guard import SmdDrillGuard, not_requested
from kicad_cli.power_driver_adapter import (
    DRILL_FIELDS,
    DRILL_SCOPE,
    DRILL_SOURCE,
    PowerAdapter,
    route_drill_constraint,
)
from scripts.public_surface import inventory, markdown

ROOT = Path(__file__).resolve().parents[1]
URI = "kicad-cli://schemas/smd-drill-clearance/v1"
PARAM = "smd-drill-clearance-mm"
COMMAND = "board route rewidth"


def witness():
    return {
        "status": "enforced",
        "enabled": True,
        "executed": True,
        "clearance_mm": 0.2,
        "scope": DRILL_SCOPE,
        "same_net_exempt": False,
        "source": DRILL_SOURCE,
        "checked_pad_count": 42,
        "shape_counts": {"rect": 8, "roundrect": 34},
        "planner_calls": 1,
        "limits": ["Finite analytic fixture witness; independent native readback is separate."],
    }


def policy_schema():
    return inventory()["json_schemas"][URI]


@pytest.mark.parametrize("enabled", [False, True])
def test_public_policy_schema_exactly_matches_prepared_witness_fields(enabled):
    params, trial = (
        ({PARAM: 0.2}, {"result": {"smd_drill_clearance": witness()}}) if enabled else ({}, {})
    )
    actual = route_drill_constraint(COMMAND, params, trial)
    schema = policy_schema()
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(actual)
    assert set(actual) == set(schema["required"]) == set(schema["properties"]) == DRILL_FIELDS
    assert schema["additionalProperties"] is False
    assert actual["enabled"] is enabled and actual["executed"] is enabled
    if enabled:
        assert actual == trial["result"]["smd_drill_clearance"]
        actual["shape_counts"]["rect"] = 0
        assert trial["result"]["smd_drill_clearance"]["shape_counts"]["rect"] == 8


@pytest.mark.parametrize("enabled", [False, True])
def test_actual_router_policy_report_matches_public_schema(tmp_path, enabled):
    board = saved_board(tmp_path, "material")
    guard = SmdDrillGuard.read([(fp, pad) for fp in board.footprints for pad in fp.pads], 0.2)
    actual = guard.report() if enabled else not_requested()
    Draft202012Validator(policy_schema()).validate(actual)
    assert set(actual) == set(policy_schema()["required"]) == DRILL_FIELDS
    if enabled:
        assert actual["checked_pad_count"] == 7
        assert actual["shape_counts"] == {"rect": 0, "roundrect": 7}
        assert (
            route_drill_constraint(
                COMMAND, {PARAM: 0.2}, {"result": {"smd_drill_clearance": actual}}
            )
            == actual
        )


@pytest.mark.parametrize("field", sorted(DRILL_FIELDS))
def test_missing_prepared_policy_field_refuses_before_public_plan(field):
    value = witness()
    del value[field]
    assert list(Draft202012Validator(policy_schema()).iter_errors(value))
    with pytest.raises(ValueError, match="witness"):
        route_drill_constraint(COMMAND, {PARAM: 0.2}, {"result": {"smd_drill_clearance": value}})


@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "not-executed",
        "wrong-margin",
        "same-net-exempt",
        "false-count",
        "extra-field",
        "unknown-shape",
    ],
)
def test_request_flags_cannot_substitute_for_real_matching_policy_execution(defect):
    trial = {"result": {"smd_drill_clearance": witness()}}
    value = trial["result"]["smd_drill_clearance"]
    if defect == "missing":
        del trial["result"]["smd_drill_clearance"]
    elif defect == "not-executed":
        value["executed"] = False
    elif defect == "wrong-margin":
        value["clearance_mm"] = 0.1
    elif defect == "same-net-exempt":
        value["same_net_exempt"] = True
    elif defect == "false-count":
        value["checked_pad_count"] = 41
    elif defect == "extra-field":
        value["unknown"] = True
    else:
        value["shape_counts"]["custom"] = 1
    with pytest.raises(ValueError):
        route_drill_constraint(COMMAND, {PARAM: 0.2}, trial)


@pytest.mark.parametrize("value", [-1, 101, float("nan"), float("inf"), True, "0.2"])
def test_invalid_margin_never_becomes_an_enforced_policy(value):
    with pytest.raises(ValueError, match="finite number"):
        route_drill_constraint(
            COMMAND, {PARAM: value}, {"result": {"smd_drill_clearance": witness()}}
        )


def test_invalid_margin_refuses_before_path_lookup_or_candidate_process(monkeypatch):
    from types import SimpleNamespace

    from kicad_cli import power_driver_adapter

    monkeypatch.setattr(power_driver_adapter, "digest", lambda *_: pytest.fail("Input read"))
    monkeypatch.setattr(
        power_driver_adapter.subprocess, "run", lambda *_a, **_k: pytest.fail("Process started")
    )
    adapter = PowerAdapter(None)
    call = SimpleNamespace(command=COMMAND, params={PARAM: -1})
    with pytest.raises(DriverError) as error:
        adapter.plan(call)
    assert error.value.code == "E_VALIDATION"
    assert error.value.details["parameter"] == "--smd-drill-clearance-mm"


def test_only_explicit_rewidth_advertises_new_finite_constraint():
    commands = {row["path"]: row for row in inventory()["commands"]}
    command = commands[COMMAND]
    param = next(row for row in command["params"] if row["name"] == PARAM)
    assert "default" not in param and param["required"] is False
    assert param["type"] == "number" and param["unit"] == "mm"
    assert command["routing_constraint_schema"] == URI
    assert "width is unchanged" in command["description"]
    for path in ("board route repair", "board route rebuild"):
        assert PARAM not in {row["name"] for row in commands[path]["params"]}
        assert "routing_constraint_schema" not in commands[path]
        with pytest.raises(ValueError, match="only by grid route rewidth"):
            route_drill_constraint(path, {PARAM: 0.2}, {})


def test_selected_public_reference_exposes_exact_routing_policy_schema():
    reference = inventory()
    command = next(row for row in reference["commands"] if row["path"] == COMMAND)
    with driver_runtime.create_cli() as cli:
        output = cli.run(["reference", "--command", COMMAND, "--compact"])
        result = json.loads(output.stdout)
    assert result["ok"] is True and output.exit_code == 0
    data = result["data"]
    review_command(data, command, reference["schemas"], reference["json_schemas"])
    assert data["json_schemas"][URI] == policy_schema()
    damaged = copy.deepcopy(data)
    del damaged["json_schemas"][URI]
    with pytest.raises(ValueError, match="Schema URI"):
        review_command(damaged, command, reference["schemas"], reference["json_schemas"])
    assert URI in markdown(reference)
