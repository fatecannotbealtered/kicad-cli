"""Finite via-hole witness/transport tests; no native consumer or routing execution."""

from __future__ import annotations

import hashlib
import json
import subprocess

import pytest
from jsonschema import Draft202012Validator
from test_driver_power_runtime import make_case, management, run

from kicad_cli.native.via_hole_guard import ViaHoleConstraintError, ViaHoleGuard
from kicad_cli.power_driver_adapter import VIA_HOLE_FIELDS, route_via_hole_constraint
from scripts.public_surface import inventory, markdown

URI = "kicad-cli://schemas/via-hole-spacing/v1"


def case(tmp_path):
    board = tmp_path / "board.kicad_pcb"
    board.write_bytes(b"inert file, never parsed")
    project = board.with_suffix(".kicad_pro")
    project.write_text(
        json.dumps({"board": {"design_settings": {"rules": {"min_hole_to_hole": 0.25}}}})
    )
    rows = [
        {"path": str(project), "sha256": hashlib.sha256(project.read_bytes()).hexdigest()},
        {"path": str(board.with_suffix(".kicad_dru")), "sha256": None},
    ]
    value = ViaHoleGuard.read(board).report()
    return {"board": str(board)}, {"result": {"via_hole_spacing": value}}, rows


def test_actual_guard_witness_matches_every_public_field(tmp_path):
    params, trial, rows = case(tmp_path)
    value = route_via_hole_constraint("board route rewidth", params, trial, rows)
    schema = inventory()["json_schemas"][URI]
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(value)
    assert set(value) == set(schema["required"]) == set(schema["properties"]) == VIA_HOLE_FIELDS
    assert set(value["binding"]) == set(schema["properties"]["binding"]["required"])
    assert schema["additionalProperties"] is False
    assert value["binding"]["minimum_nm"] == 250_000
    value["binding"]["minimum_nm"] = 0
    assert trial["result"]["via_hole_spacing"]["binding"]["minimum_nm"] == 250_000


@pytest.mark.parametrize("field", sorted(VIA_HOLE_FIELDS))
def test_each_missing_execution_witness_field_refuses(tmp_path, field):
    params, trial, rows = case(tmp_path)
    del trial["result"]["via_hole_spacing"][field]
    with pytest.raises(ValueError, match="witness"):
        route_via_hole_constraint("board route repair", params, trial, rows)


@pytest.mark.parametrize(
    "field", ["rule", "minimum_nm", "defaulted_to_kicad10", "project_sha256", "custom_rules_sha256"]
)
def test_each_missing_rule_binding_field_refuses(tmp_path, field):
    params, trial, rows = case(tmp_path)
    del trial["result"]["via_hole_spacing"]["binding"][field]
    with pytest.raises(ValueError, match="captured rules"):
        route_via_hole_constraint("board route rewidth", params, trial, rows)


@pytest.mark.parametrize(
    "change",
    [
        "project",
        "custom",
        "same_net",
        "source",
        "limits",
        "counter",
        "minimum",
        "wrong-minimum",
        "defaulted",
        "extra",
    ],
)
def test_forged_binding_or_semantics_refuses(tmp_path, change):
    params, trial, rows = case(tmp_path)
    value = trial["result"]["via_hole_spacing"]
    if change in {"project", "custom"}:
        value["binding"][change + ("_sha256" if change == "project" else "_rules_sha256")] = (
            "0" * 64
        )
    elif change == "same_net":
        value["same_net_exempt"] = True
    elif change == "source":
        value["source"] = "official native DRC"
    elif change == "limits":
        value["limits"] = []
    elif change == "counter":
        value["checked_pair_count"] = True
    elif change == "minimum":
        value["binding"]["minimum_nm"] = -1
    elif change == "wrong-minimum":
        value["binding"]["minimum_nm"] = 0
    elif change == "defaulted":
        value["binding"]["defaulted_to_kicad10"] = True
    else:
        value["extra"] = 1
    with pytest.raises(ValueError):
        route_via_hole_constraint("board route rebuild", params, trial, rows)


def test_external_engine_and_nonrouting_do_not_invent_a_grid_witness():
    assert (
        route_via_hole_constraint("board route rewidth", {"engine": "freerouting"}, {}, []) is None
    )
    assert route_via_hole_constraint("board track widen", {}, {}, []) is None
    surface = inventory()
    for command in surface["commands"]:
        if command["path"] in {"board route repair", "board route rewidth", "board route rebuild"}:
            assert command["via_hole_spacing_schema"] == URI
    assert URI in markdown(surface)


def test_public_project_hole_refusal_is_structured_and_never_registered(tmp_path, monkeypatch):
    refusal = ViaHoleConstraintError("Unsupported custom hole_to_hole fixture", objects=[])
    stream = json.dumps({"ok": False, "error": refusal.record()}).encode()
    calls = []

    def child(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 2, stream, b"")

    monkeypatch.setattr("kicad_cli.power_driver_adapter.subprocess.run", child)
    fixture = make_case(tmp_path)
    before = (fixture[0].read_bytes(), fixture[0].with_suffix(".kicad_pro").read_bytes())
    result = run(fixture, "board route rewidth", "--nets", "VIN", "--dry-run")
    assert not result["ok"] and result["error"]["code"] == "E_VALIDATION"
    details = result["error"]["details"]
    assert details["parameter"] == "--board" and details["constraint"] == "project-min-hole-to-hole"
    assert details["dispatch_state"] == "not_started" and details["operation_registered"] is False
    assert len(calls) == 1 and management(fixture, "list")["data"]["items"] == []
    assert before == (fixture[0].read_bytes(), fixture[0].with_suffix(".kicad_pro").read_bytes())
