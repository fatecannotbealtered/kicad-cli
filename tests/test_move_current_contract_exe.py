"""Explicit migration of the old move contract to the bounded native preflight.

Historical test_candidate_move_acceptance.py and its 21c results stay immutable.
Unchanged safety intents already covered by test_move_safety_exe are mapped to
those exact cases rather than executed twice. This module supplies the missing
parser, saved-pose, identity, authorization, native-lock and keyed-request intents.
Every case uses the explicitly frozen final EXE; there is no source fallback.

The R4 saved task remains (100,110) -> (99.5,110) mm. Parameter variants use the
same unconnected R4: one additional nanometre tests truncation, a no-op tests
unchanged content, and the multi-selection translates R4 and R5 by -0.5 mm.
Those variants prove argument/save behavior only, not new engineering tasks.
R1 outside/overlap previews never confirm: their existing copper becomes detached
or conflicts, so the limited official gate must block. This does not claim an
independent board-edge or complete courtyard/collision algorithm.
"""

from __future__ import annotations

import json
import os
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from move_safety_transport import SafetyMoveExe
from test_candidate_move_acceptance import INVALID_MOVES
from test_move_safety_exe import (
    R1_UUID,
    digest,
    files,
    fresh_preview,
    native_calls,
    no_dispatch,
    operation,
    pose_rows,
    protected_bytes,
    refused_confirm,
    saved_positive,
    store_rows,
)
from test_move_safety_exe import (
    safety_scene as safety_scene,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows owned current-EXE acceptance")
PREVIOUS_BINARY_SHA256 = "d1d0eef7b83899549d261c0df8a67da4c9d3bed73cc464f563cd4596b48f070b"

TRANSLATIONS = [
    pytest.param("R4:99.5,110", {"R4": [99500000, 110000000]}, id="single"),
    pytest.param(
        "R4:99.5,110;R5:104.5,110",
        {"R4": [99500000, 110000000], "R5": [104500000, 110000000]},
        id="multiple",
    ),
    pytest.param("R4:99.5000019,110", {"R4": [99500001, 110000000]}, id="quantisation"),
    pytest.param("R4:100,110", {"R4": [100000000, 110000000]}, id="no-op"),
    pytest.param(
        " ; R4 : 99.5 , 110 ; ;", {"R4": [99500000, 110000000]}, id="spaces-empty-segments"
    ),
]


def initial_preview(s, *, moves="R4:99.5,110", expected="passed"):
    data = fresh_preview(s, moves=moves, expected=expected)
    plan = data["preview"]
    assert plan["target"]["locator"] == s.board.resolve().as_uri()
    assert plan["base_snapshot"]["revision"] == digest(s.board)
    rows = store_rows(s.state)
    assert len(rows["operations"]) == 1
    record = operation(rows, plan["operation_id"])
    assert record["status"] == "planned" and record["started_at"] is None
    assert json.loads(record["plan"]) == plan
    no_dispatch(rows)
    return data


def assert_refused(s, args, *, code, error):
    before, count = files(s.root), native_calls(s)
    actual, result = s.call(*args, "--dry-run")
    assert actual == code and not result["ok"] and result["error"]["code"] == error, result
    assert result["error"]["details"]["dispatch_state"] == "not_started"
    assert files(s.root) == before and native_calls(s) == count
    no_dispatch(store_rows(s.state))
    return result


@pytest.mark.parametrize("moves,expected_nm", TRANSLATIONS)
def test_preview_confirm_saved_translation(safety_scene, moves, expected_nm):
    s = safety_scene
    before_files, raw = files(s.root), s.board.read_bytes()
    original = pose_rows(raw)
    assert not any(original[ref]["locked"] for ref in expected_nm)
    assert not (s.root / (s.board.name + ".kicad-cli.journal")).exists()
    data = initial_preview(s, moves=moves)
    plan = data["preview"]
    assert [c["id"] for c in plan["changes"]] == [original[r]["id"] for r in sorted(expected_nm)]
    assert all(c["action"] == "translate" for c in plan["changes"])
    requested_mm = {}
    for part in moves.split(";"):
        if part.strip():
            ref, xy = part.split(":")
            requested_mm[ref.strip()] = [Decimal(v.strip()) for v in xy.split(",")]
    for change, ref in zip(plan["changes"], sorted(expected_nm), strict=True):
        prior = change["before"]
        assert prior["ref"] == ref and prior["id"] == original[ref]["id"]
        assert prior["position_nm"] == original[ref]["position_nm"]
        assert [Decimal(str(v)) * 1_000_000 for v in prior["position_mm"]] == [
            Decimal(v) for v in original[ref]["position_nm"]
        ]
        assert Decimal(str(prior["rotation_deg"])) == original[ref]["rotation_deg"]
        assert prior["layer"] == original[ref]["layer"]
        assert [Decimal(str(v)) for v in change["after"]["position_mm"]] == requested_mm[ref]
    feedback = plan["extensions"]["engineering_feedback"]
    assert feedback["geometry_check"] == "official_native_drc_limited"
    assert feedback["board_contains_copper"] is True
    assert feedback["requires_visual_review"] and feedback["unsupported"]
    assert feedback["copper_follows_footprint"] is False
    assert any("copper" in value.lower() for value in plan["unknowns"])
    if moves == "R4:99.5,110":
        code, shown = s.call("operation", "show", "--id", plan["operation_id"])
        assert code == 0 and shown["ok"]
        assert shown["data"]["operation_status"]["status"] == "planned"
        assert files(s.root) == before_files
    calls = native_calls(s)
    candidate_sha = plan["extensions"]["preflight"]["candidate_sha256"]
    code, result = s.call(
        *s.args(moves), "--confirm", data["confirm_token"], "--idempotency-key", "current-move"
    )
    assert code == 0 and result["ok"], result
    assert native_calls(s) == calls, (
        "Confirmation must consume this preflight without native replay"
    )
    receipt = result["data"]["receipt"]
    assert receipt["operation_id"] == plan["operation_id"] and receipt["plan_id"] == plan["plan_id"]
    assert receipt["authorization_scope_id"] == "owned-move-safety-exe"
    assert receipt["outcome"]["verification"]["status"] == "passed"
    assert receipt["snapshot_before"]["revision"] == digest_bytes(raw)
    assert receipt["snapshot_after"]["revision"] == digest(s.board) == candidate_sha
    assert receipt["extensions"]["units"] == {"position": "nm", "position_tolerance_nm": 1}
    assert receipt["extensions"]["source_after"]["completed"] is True
    assert receipt["extensions"]["source_after"]["input_sha256"] == digest(s.board)
    assert receipt["extensions"]["readback"]["snapshot"]["revision"] == digest(s.board)
    assert receipt["extensions"]["failures"] == []
    for unchecked in ("DRC", "connectivity", "manufacturing"):
        assert unchecked in receipt["extensions"]["coverage"]["not_checked"]
    saved = pose_rows(s.board.read_bytes())
    for ref, original_pose in original.items():
        wanted = dict(original_pose, position_nm=expected_nm.get(ref, original_pose["position_nm"]))
        assert saved[ref] == wanted
    assert {
        row["ref"]: row["position_nm"] for row in receipt["extensions"]["observed"]
    } == expected_nm
    assert protected_bytes(raw, expected_nm) == protected_bytes(s.board.read_bytes(), expected_nm)
    assert {k: v for k, v in files(s.root).items() if k != s.board.name} == {
        k: v for k, v in before_files.items() if k != s.board.name
    }
    expected_change = "not_applied" if moves == "R4:100,110" else "applied"
    assert receipt["outcome"]["change"] == expected_change
    assert receipt["outcome"]["persistence"] == (
        "unchanged" if expected_change == "not_applied" else "saved"
    )
    rows = store_rows(s.state)
    record = operation(rows, plan["operation_id"])
    assert record["status"] == "succeeded" and json.loads(record["receipt"]) == receipt
    assert record["started_at"] is not None and record["finished_at"] is not None
    assert len(rows["consumed_tokens"]) == 1
    assert rows["scope_usage"] == [{"scope_id": "owned-move-safety-exe", "used": 1}]
    current = files(s.root)
    code, shown = s.call("operation", "show", "--id", plan["operation_id"])
    assert code == 0 and shown["ok"]
    assert shown["data"]["operation_status"]["status"] == "succeeded"
    assert shown["data"]["operation_status"]["receipt"] == receipt and files(s.root) == current
    if moves == "R4:99.5,110":
        profile = receipt["outcome"]["verification"]["extensions"]["verification_result"]
        assert profile["observations"][0]["source"]["kind"] == "file_consumer"
        assert profile["observations"][0]["source"]["completion"] == "completed"
        code, listing = s.call("operation", "list", "--status", "succeeded")
        assert code == 0 and listing["ok"] and listing["data"]["count"] == 1
        code, repeated = s.call(*s.args(moves), "--confirm", data["confirm_token"])
        assert code == 6 and not repeated["ok"]
        assert repeated["error"]["details"]["conflict"] == "token_used"
        assert repeated["error"]["details"]["dispatch_state"] == "not_started"
        assert files(s.root) == current and store_rows(s.state) == rows


def digest_bytes(raw):
    import hashlib

    return hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("parameter", ["board", "moves"])
def test_missing_required_parameter(safety_scene, parameter):
    s = safety_scene
    args = s.args()
    index = args.index("--" + parameter)
    del args[index : index + 2]
    assert_refused(s, args, code=2, error="E_USAGE")


@pytest.mark.parametrize("parameter", ["board", "moves"])
def test_repeated_scalar_parameter_refuses(safety_scene, parameter):
    s = safety_scene
    value = str(s.board) if parameter == "board" else "R4:99.5,110"
    assert_refused(s, [*s.args(), "--" + parameter, value], code=2, error="E_USAGE")


@pytest.mark.parametrize("defect", ["missing-file", "wrong-kind", "malformed-board"])
def test_board_input_refusals(safety_scene, defect):
    s = safety_scene
    target = s.root / "absent.kicad_pcb"
    if defect == "wrong-kind":
        target = s.root / "edit.kicad_sch"
    elif defect == "malformed-board":
        target = s.root / "malformed.kicad_pcb"
        target.write_bytes(b"(kicad_pcb (version 20260206) (footprint")
    assert_refused(
        s,
        s.args(target=target),
        code=3 if defect == "missing-file" else 2,
        error="E_NOT_FOUND" if defect == "missing-file" else "E_VALIDATION",
    )


@pytest.mark.parametrize("moves", INVALID_MOVES)
def test_invalid_move_input_refuses_without_write(safety_scene, moves):
    assert_refused(safety_scene, safety_scene.args(moves), code=2, error="E_VALIDATION")


@pytest.mark.parametrize(
    "defect", ["absent", "case-sensitive", "ambiguous-reference", "duplicate-uuid"]
)
def test_exact_reference_and_identity_boundaries(safety_scene, defect):
    s = safety_scene
    moves = "R1:101.25,100.5"
    if defect == "absent":
        moves = "ABSENT:101.25,100.5"
    elif defect == "case-sensitive":
        moves = "r1:101.25,100.5"
    elif defect == "ambiguous-reference":
        s.board.write_bytes(s.board.read_bytes().replace(b'"R2"', b'"R1"'))
    else:
        other = pose_rows(s.board.read_bytes())["R2"]["id"]
        s.board.write_bytes(s.board.read_bytes().replace(other.encode(), R1_UUID.encode()))
    invalid = defect in {"ambiguous-reference", "duplicate-uuid"}
    assert_refused(
        s,
        s.args(moves),
        code=2 if invalid else 3,
        error="E_VALIDATION" if invalid else "E_NOT_FOUND",
    )


@pytest.mark.parametrize(
    "flag,value",
    [("--rotate", "90"), ("--layer", "B.Cu"), ("--unlock", None)],
    ids=["rotation", "layer-change", "unlock"],
)
def test_unsupported_move_options_refuse(safety_scene, flag, value):
    extra = [flag] + ([value] if value else [])
    assert_refused(safety_scene, [*safety_scene.args(), *extra], code=2, error="E_USAGE")


@pytest.mark.parametrize("condition", ["outside-board", "overlapping-footprint"])
def test_limited_preflight_blocks_declared_r1_risk_without_claiming_full_geometry(
    safety_scene, condition
):
    s = safety_scene
    moves = "R1:-1000,-1000" if condition == "outside-board" else "R1:105,100"
    data = initial_preview(s, moves=moves, expected="blocked")
    preflight = data["preview"]["extensions"]["preflight"]
    comparison = preflight["comparison"]
    assert comparison["new_findings"] or comparison["connection_changes"]["blocking_new"]
    assert comparison["connection_changes"]["complete_connectivity_graph"] is False
    limits = " ".join(comparison["not_checked"]).lower()
    assert "board-edge" in limits and "courtyard" in limits and "complex" in limits
    assert data["preview"]["extensions"]["engineering_feedback"]["requires_visual_review"]
    no_dispatch(store_rows(s.state))
    # No confirmation: these retained parser/placement counterexamples must
    # never become a second engineering save or a comprehensive geometry claim.


@pytest.mark.parametrize("drift", ["footprint-uuid", "scope-root"])
def test_confirmation_identity_and_scope_root_drift_refuse(safety_scene, drift):
    s = safety_scene
    data = initial_preview(s)
    if drift == "footprint-uuid":
        old = pose_rows(s.board.read_bytes())["R4"]["id"]
        new = "9c5beb16-fbc7-5af2-918d-da26a4b5251c"
        s.board.write_bytes(s.board.read_bytes().replace(old.encode(), new.encode()))
    else:
        scope = json.loads(s.scope.read_bytes())
        scope["roots"] = [str(s.root / "unauthorized")]
        s.scope.write_text(json.dumps(scope), encoding="utf-8")
    result = refused_confirm(s, data)
    assert result["error"]["details"]["conflict"] == (
        "state_drift" if drift == "footprint-uuid" else "scope_changed"
    )
    record = operation(store_rows(s.state), data["preview"]["operation_id"])
    assert record["status"] == "planned" and record["receipt"] is None


def test_absent_authorization_does_not_plan_or_write(safety_scene):
    s = safety_scene
    s.scope.unlink()
    assert_refused(s, s.args(), code=4, error="E_FORBIDDEN")
    assert store_rows(s.state)["operations"] == []


def test_native_project_lock_is_busy_and_preserved(safety_scene):
    s = safety_scene
    lock = s.root / "~edit.kicad_pro.lck"
    lock.write_text('{"hostname":"owned fixture host","username":"test"}', encoding="utf-8")
    result = assert_refused(s, s.args(), code=7, error="E_BUSY")
    assert result["error"]["retryable"] is True
    assert result["error"]["details"]["lock_files"] == [lock.name]
    assert lock.is_file()


def test_reused_key_changed_request_refuses_before_new_write(safety_scene):
    s = safety_scene
    _original, receipt = saved_positive(s)
    # The original saved position provides a different canonical request;
    # this plan never confirms, so it cannot reverse the completed R4 task.
    args = s.args("R4:100,110")
    data = fresh_preview(s, moves="R4:100,110")
    before, state, count = files(s.root), store_rows(s.state), native_calls(s)
    code, refused = s.call(
        *args, "--confirm", data["confirm_token"], "--idempotency-key", "one-safe-move"
    )
    assert code == 6 and not refused["ok"] and refused["error"]["code"] == "E_CONFLICT"
    assert refused["error"]["details"]["conflict"] == "idempotency_mismatch"
    assert refused["error"]["details"]["dispatch_state"] == "not_started"
    assert files(s.root) == before and store_rows(s.state) == state and native_calls(s) == count
    assert operation(state, receipt["operation_id"])["status"] == "succeeded"


def test_previous_d1_preflight_token_cannot_authorize_current_artifact(
    safety_scene, monkeypatch, record_property
):
    """One new d1 preview in this case's own store; no old-tool confirmation."""
    s = safety_scene
    previous = Path(os.environ["KICAD_CLI_MOVE_PREVIOUS_BINARY"])
    assert previous.is_absolute() and previous.is_file()
    assert digest(previous) == PREVIOUS_BINARY_SHA256
    assert s.transport.artifact_sha256 != PREVIOUS_BINARY_SHA256
    before, config_before = files(s.root), files(s.config)
    assert store_rows(s.state) == {"operations": [], "consumed_tokens": [], "scope_usage": []}
    with monkeypatch.context() as temporary:
        temporary.setenv("KICAD_CLI_TEST_BINARY", str(previous))
        temporary.setenv("KICAD_CLI_MOVE_SAFETY_BINARY_SHA256", PREVIOUS_BINARY_SHA256)
        old = SafetyMoveExe(
            previous,
            s.root.parent,
            s.env,
            s.root.parent / "previous-d1-public-exe-calls",
            lambda name, value: record_property("previous_d1_" + name, value),
        )
        code, preview = old.call(*s.args(), "--dry-run")
    assert code == 0 and preview["ok"], preview
    data = preview["data"]
    plan = data["preview"]
    assert data["confirm_token"] and plan["can_apply"] is True
    preflight = plan["extensions"]["preflight"]
    assert preflight["status"] == "passed"
    assert preflight["source"]["official_native"] is True
    assert preflight["source"]["version"] == "10.0.6"
    assert preflight["baseline_sha256"] == digest(s.board)
    assert files(s.root) == before and files(s.config) == config_before
    rows = store_rows(s.state)
    no_dispatch(rows)
    assert len(rows["operations"]) == 1
    assert json.loads(operation(rows, plan["operation_id"])["plan"]) == plan
    assert len(old.calls) == 1
    native_count = sum(len(call["official_native_processes"]) for call in old.calls)
    assert native_count == 4, "Exactly one d1 preflight: version, preparation, baseline, candidate"
    record_property("previous_d1_public_exe_calls", 1)
    record_property("previous_d1_official_native_calls", native_count)
    record_property(
        "previous_d1_direct_pe_processes",
        sum(len(call["direct_pe_processes"]) for call in old.calls),
    )
    refused = refused_confirm(s, data)
    assert refused["error"]["details"]["conflict"] == "token_mismatch"
    assert files(s.root) == before and store_rows(s.state) == rows
    assert native_calls(s) == 0


@pytest.mark.parametrize("drift", ["board-path", "coordinates", "board-bytes", "scope-content"])
def test_strict_confirmation_drift_preserves_exact_conflict(safety_scene, drift):
    s = safety_scene
    data = initial_preview(s)
    args = s.args()
    expected_conflict = {
        "board-path": "token_mismatch",
        "coordinates": "token_mismatch",
        "board-bytes": "state_drift",
        "scope-content": "scope_changed",
    }[drift]
    if drift == "board-path":
        other = s.root / "other.kicad_pcb"
        shutil.copyfile(s.board, other)
        args = s.args(target=other)
    elif drift == "coordinates":
        args = s.args("R4:99.75,110")
    elif drift == "board-bytes":
        s.board.write_bytes(s.board.read_bytes() + b"\n")
    else:
        scope = json.loads(s.scope.read_bytes())
        scope["notes"] += " changed while retaining original scope_id"
        s.scope.write_text(json.dumps(scope), encoding="utf-8")
    before, state, count = files(s.root), store_rows(s.state), native_calls(s)
    result = refused_confirm(s, data, args=args)
    assert result["error"]["details"]["conflict"] == expected_conflict
    assert files(s.root) == before and store_rows(s.state) == state and native_calls(s) == count
    record = operation(store_rows(s.state), data["preview"]["operation_id"])
    assert record["status"] == "planned" and record["receipt"] is None


def test_without_key_saves_fixed_r4_and_retains_public_operation_contract(safety_scene):
    s = safety_scene
    original_files, raw = files(s.root), s.board.read_bytes()
    original = pose_rows(raw)
    assert original["R4"]["position_nm"] == [100000000, 110000000]
    assert not (s.root / (s.board.name + ".kicad-cli.journal")).exists()
    data = initial_preview(s)
    plan = data["preview"]
    code, planned = s.call("operation", "show", "--id", plan["operation_id"])
    assert code == 0 and planned["ok"]
    assert planned["data"]["operation_status"]["status"] == "planned"
    assert files(s.root) == original_files and s.board.read_bytes() == raw
    calls = native_calls(s)
    code, result = s.call(*s.args(), "--confirm", data["confirm_token"])
    assert code == 0 and result["ok"], result
    assert native_calls(s) == calls
    receipt = result["data"]["receipt"]
    assert receipt["operation_id"] == plan["operation_id"]
    assert receipt["plan_id"] == plan["plan_id"]
    assert receipt["authorization_scope_id"] == "owned-move-safety-exe"
    assert receipt["outcome"]["change"] == "applied"
    assert receipt["outcome"]["persistence"] == "saved"
    assert receipt["outcome"]["verification"]["status"] == "passed"
    assert receipt["extensions"]["observed"][0]["position_nm"] == [99500000, 110000000]
    profile = receipt["outcome"]["verification"]["extensions"]["verification_result"]
    assert profile["observations"][0]["source"]["kind"] == "file_consumer"
    assert profile["observations"][0]["source"]["completion"] == "completed"
    expected = dict(original)
    expected["R4"] = dict(original["R4"], position_nm=[99500000, 110000000])
    assert pose_rows(s.board.read_bytes()) == expected
    assert protected_bytes(raw, ["R4"]) == protected_bytes(s.board.read_bytes(), ["R4"])
    assert digest(s.board) == plan["extensions"]["preflight"]["candidate_sha256"]
    assert {name: value for name, value in files(s.root).items() if name != s.board.name} == {
        name: value for name, value in original_files.items() if name != s.board.name
    }
    state = store_rows(s.state)
    record = operation(state, plan["operation_id"])
    assert record["status"] == "succeeded" and json.loads(record["receipt"]) == receipt
    current = files(s.root)
    code, shown = s.call("operation", "show", "--id", plan["operation_id"])
    assert code == 0 and shown["ok"]
    assert shown["data"]["operation_status"]["status"] == "succeeded"
    assert shown["data"]["operation_status"]["receipt"] == receipt
    code, listed = s.call("operation", "list", "--status", "succeeded")
    assert code == 0 and listed["ok"] and listed["data"]["count"] == 1
    code, refused = s.call(*s.args(), "--confirm", data["confirm_token"])
    assert code == 6 and not refused["ok"] and refused["error"]["code"] == "E_CONFLICT"
    assert refused["error"]["details"]["conflict"] == "token_used"
    assert refused["error"]["details"]["dispatch_state"] == "not_started"
    assert files(s.root) == current and store_rows(s.state) == state and native_calls(s) == calls


def test_strict_fresh_token_same_key_returns_exact_receipt_and_supersedes_plan(safety_scene):
    s = safety_scene
    original, receipt = saved_positive(s)
    before = files(s.root)
    data = fresh_preview(s)
    next_plan = data["preview"]
    assert next_plan["operation_id"] != original["preview"]["operation_id"]
    assert files(s.root) == before
    state, calls = store_rows(s.state), native_calls(s)
    assert len(state["operations"]) == 2
    code, replay = s.call(
        *s.args(), "--confirm", data["confirm_token"], "--idempotency-key", "one-safe-move"
    )
    assert code == 0 and replay["ok"], replay
    assert replay["data"]["receipt"] == dict(receipt, replayed=True, request_id="one-safe-move")
    assert files(s.root) == before and native_calls(s) == calls
    after = store_rows(s.state)
    assert operation(after, receipt["operation_id"]) == operation(state, receipt["operation_id"])
    superseded = operation(after, next_plan["operation_id"])
    assert superseded["status"] == "failed" and superseded["started_at"] is None
    assert json.loads(superseded["extra"]) == {
        "stage": "superseded_by_replay",
        "dispatch_state": "not_started",
        "replay_of": receipt["operation_id"],
    }
    assert len(after["operations"]) == len(state["operations"]) == 2
    assert len(after["consumed_tokens"]) == len(state["consumed_tokens"]) + 1
    assert (
        after["scope_usage"]
        == state["scope_usage"]
        == [{"scope_id": "owned-move-safety-exe", "used": 1}]
    )
