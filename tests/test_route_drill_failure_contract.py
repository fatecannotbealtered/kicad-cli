"""Finite source/mock failure transport; no actual child, native DRC or final EXE."""

from __future__ import annotations

import hashlib
import hmac
import json
import subprocess
import sys
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from test_driver_power_runtime import make_case, management, run
from test_router_smd_drill_constraint import read_guard, source

from kicad_cli import driver_runtime, power_prepare
from kicad_cli._driver_sdk import tokens
from kicad_cli.move_transaction import paths
from kicad_cli.native.smd_drill_guard import DrillConstraintError


def refused_material(defect):
    text = source(shape="oval") if defect == "shape" else source(layer='"In1.Cu" "F.Mask"')
    with pytest.raises(DrillConstraintError) as failure:
        read_guard(text)
    return text, failure.value


@pytest.mark.parametrize("defect", ["shape", "layer"])
def test_finite_geometry_failure_carries_actual_pad_identity_and_untrusted_text(defect):
    _text, error = refused_material(defect)
    record = error.record()
    assert isinstance(error, ValueError)
    assert record["code"] == "E_VALIDATION"
    details = record["details"]
    assert details["constraint"] == "smd-drill-clearance-mm"
    assert details["objects"] == [
        {
            "kind": "pad",
            "reference": "R1",
            "number": "1",
            "uuid": "b0000000-0000-4000-8000-000000000001",
        }
    ]
    assert details["_untrusted"] == ["reason", "objects"]
    assert "Unsupported SMD" in details["reason"]


def test_hole_refusal_identifies_planned_drill_and_conflicting_same_net_pad():
    guard = read_guard()
    with pytest.raises(DrillConstraintError) as failure:
        guard.require_clearance(5_000_000, 5_000_000, 300_000)
    objects = failure.value.record()["details"]["objects"]
    assert objects[0] == {
        "kind": "planned_via",
        "uuid": None,
        "position_nm": [5_000_000, 5_000_000],
        "drill_nm": 300_000,
    }
    assert objects[1]["identity"] == "R1.1:b0000000-0000-4000-8000-000000000001"
    assert objects[1]["net"] == "VIN" and objects[1]["shape"] == "roundrect"


def private_fixture(tmp_path, monkeypatch, text, *, margin=0.2):
    """Exercise the authenticated private entrypoint with inert DRC/route substitutes."""
    state = tmp_path / "owned-state"
    stage = state / "power-trials/op-finite-constraint"
    candidate = stage / "candidate/input.kicad_pcb"
    candidate.parent.mkdir(parents=True)
    candidate.write_text(text, encoding="utf-8")
    secret = b"inert-unit-fixture-secret"
    record = {"id": "inert-scope"}
    binding = {"source": "inert-unit-fixture"}
    calls = []
    scope = SimpleNamespace(record=record, require=lambda **kw: calls.append(kw))
    cli = SimpleNamespace(
        state_dir=state,
        scopes=SimpleNamespace(resolve=lambda: scope),
        _binding=lambda: binding,
        _store_open=lambda: SimpleNamespace(secret=lambda: secret),
    )
    request = {
        "owner": "kicad-cli.power-trial.v1",
        "board": str(candidate.resolve()),
        "engineering_target": "file:///inert-unit-fixture",
        "command": "board route rewidth",
        "params": {"smd-drill-clearance-mm": margin},
        "selected": ["VIN"],
        "binding": binding,
        "scope_digest": tokens.digest(record),
        "captured": {str(candidate.resolve()): hashlib.sha256(candidate.read_bytes()).hexdigest()},
    }
    request["hmac"] = hmac.new(secret, tokens.canonical(request), hashlib.sha256).hexdigest()
    request_path = stage / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["power_prepare", "--request", str(request_path)])
    monkeypatch.setattr(driver_runtime, "create_cli", lambda: nullcontext(cli))
    monkeypatch.setattr(power_prepare.drc_baseline, "capture", lambda _p: (None, {"inert": True}))
    monkeypatch.setattr(power_prepare.drc_baseline, "record", lambda value: value)

    def prepare(path, params, _selected):
        guard = read_guard(path.read_text(encoding="utf-8"), gap=params["smd-drill-clearance-mm"])
        return {"new_constraint_requested": guard is not None}

    monkeypatch.setattr(power_prepare.power_geometry, "prepare_route", prepare)
    return candidate, calls


@pytest.mark.parametrize("defect", ["shape", "layer"])
def test_private_entrypoint_returns_finite_failure_json_without_traceback(
    tmp_path, monkeypatch, capsys, defect
):
    text, _error = refused_material(defect)
    candidate, calls = private_fixture(tmp_path, monkeypatch, text)
    before = candidate.read_bytes()
    assert power_prepare.main() == 2
    output = capsys.readouterr()
    failure = json.loads(output.out)
    assert failure["ok"] is False
    assert failure["error"]["details"]["constraint"] == "smd-drill-clearance-mm"
    assert failure["error"]["details"]["objects"][0]["reference"] == "R1"
    assert output.err == "" and "Traceback" not in output.out
    assert candidate.read_bytes() == before and len(calls) == 1


def test_none_does_not_enable_the_new_constraint(tmp_path, monkeypatch, capsys):
    text, _error = refused_material("shape")
    candidate, _calls = private_fixture(tmp_path, monkeypatch, text, margin=None)
    before = candidate.read_bytes()
    assert power_prepare.main() == 0
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["ok"] is True
    assert result["result"] == {"new_constraint_requested": False}
    assert candidate.read_bytes() == before and output.err == ""


def test_unrelated_legacy_preparation_error_is_not_converted(tmp_path, monkeypatch):
    private_fixture(tmp_path, monkeypatch, source(), margin=None)

    def unrelated(*_args):
        raise ValueError("unrelated historical failure")

    monkeypatch.setattr(power_prepare.power_geometry, "prepare_route", unrelated)
    with pytest.raises(ValueError, match="unrelated historical failure"):
        power_prepare.main()


@pytest.mark.parametrize("defect", ["shape", "layer"])
def test_public_preview_exposes_finite_failure_without_registering_an_operation(
    tmp_path, monkeypatch, defect
):
    _text, refusal = refused_material(defect)
    failure = {"ok": False, "error": refusal.record()}
    stream = json.dumps(failure).encode("utf-8")
    child_calls = []

    def child(argv, **_kwargs):
        child_calls.append(argv)
        return subprocess.CompletedProcess(argv, 2, stream, b"")

    monkeypatch.setattr("kicad_cli.power_driver_adapter.subprocess.run", child)
    case = make_case(tmp_path)
    before = (case[0].read_bytes(), case[0].with_suffix(".kicad_pro").read_bytes())
    result = run(
        case, "board route rewidth", "--nets", "VIN", "--smd-drill-clearance-mm", "0.2", "--dry-run"
    )
    assert result["ok"] is False
    error = result["error"]
    assert error["code"] == "E_VALIDATION" and error["retryable"] is False
    details = error["details"]
    assert details["parameter"] == "--smd-drill-clearance-mm"
    assert details["constraint"] == "smd-drill-clearance-mm"
    assert details["reason"] == str(refusal) and details["objects"] == refusal.objects
    assert details["_untrusted"] == ["reason", "objects", "stage", "candidate_error"]
    assert details["primary_write_state"] == "not_applied"
    assert details["dispatch_state"] == "not_started"
    assert details["operation_registered"] is False
    assert len(child_calls) == 1 and "Traceback" not in json.dumps(result)
    assert before == (case[0].read_bytes(), case[0].with_suffix(".kicad_pro").read_bytes())
    assert not paths(case[0])[2].exists()
    assert management(case, "list")["data"]["items"] == []
