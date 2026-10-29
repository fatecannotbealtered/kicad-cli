"""Finite current-artifact operation-show supplement; no CAD fixture execution.

Normal cases prove exact final-EXE retrieval of SDK-valid controlled records
shaped as ordinary SCH/sync planned/succeeded operations. Fixture-original
plans and receipts are the independent truth. They are not product dry-run or
confirm results, and they do not certify a SCH/PCB write or recovery workflow.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from trial_operation_fixture import make_scene, seed_record

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows owned final-EXE observation")


@pytest.mark.parametrize(
    ("command", "status"),
    [
        pytest.param("sch edit", "planned", id="sch-edit-planned"),
        pytest.param("sch edit", "succeeded", id="sch-edit-succeeded"),
        pytest.param("project sync", "planned", id="project-sync-planned"),
        pytest.param("project sync", "succeeded", id="project-sync-succeeded"),
    ],
)
def test_operation_show_preserves_owned_record(tmp_path, record_property, command, status):
    scene = make_scene(tmp_path, record_property)
    original = seed_record(scene.sdk, scene, command, status, tag="selected")
    # A different valid record catches a reader that ignores the requested ID.
    decoy = seed_record(scene.sdk, scene, command, status, tag="decoy")
    assert decoy["operation_id"] != original["operation_id"]
    observed_after = datetime.now(timezone.utc)
    code, result = scene.exe.call(
        ["operation", "show", "--id", original["operation_id"]], expected_exit=0
    )
    assert code == 0 and result["ok"] is True
    operation = result["data"]["operation_status"]
    assert operation["operation_id"] == original["operation_id"]
    assert operation["plan_id"] == original["plan"]["plan_id"]
    assert operation["capability_id"] == original["plan"]["capability_id"]
    assert operation["status"] == status
    assert operation["plan"] == original["plan"]
    assert operation["target"] == original["plan"]["target"]
    assert operation["receipt"] == original["receipt"]
    assert operation["resolution"] is None
    assert operation["blocked"] is None and operation["stage"] is None
    assert operation["stop_observed"] is False
    assert operation["commands"] == {
        "lookup": {
            "argv": ["operation", "show", "--id", original["operation_id"]],
            "read_only": True,
        },
        "result": None,
        "cancel": None,
    }
    observed = datetime.fromisoformat(operation["observed_at"].replace("Z", "+00:00"))
    assert observed_after <= observed <= datetime.now(timezone.utc)
    assert operation["record"]["availability"] == "available"
    assert operation["record"]["survives_cli_exit"] is True
    assert operation["record"]["survives_reconnect"] is True
    assert operation["record"]["survives_instance_restart"] is True
    assert operation["record"]["retain_until"] is not None
    if status == "planned":
        assert operation["request_id"] is None
        assert operation["started_at"] is None and operation["finished_at"] is None
    else:
        assert operation["request_id"] == original["receipt"]["request_id"]
        assert operation["started_at"] is not None and operation["finished_at"] is not None
        assert operation["receipt"]["extensions"]["actual_cad_execution"] is False
    assert len(scene.exe.calls) == 1


@pytest.mark.parametrize(
    ("variant", "arguments", "exit_code", "error_code", "problem", "next_action"),
    [
        pytest.param(
            "missing-id",
            [],
            2,
            "E_USAGE",
            "missing_parameter",
            "Add --id a non-empty string.",
            id="missing-id",
        ),
        pytest.param(
            "empty-id",
            ["--id="],
            2,
            "E_VALIDATION",
            "invalid_value",
            "Pass --id as a non-empty string.",
            id="empty-id",
        ),
        pytest.param(
            "malformed-id",
            ["--id", "not an operation / 中文 ?!"],
            3,
            "E_NOT_FOUND",
            "unknown_operation",
            "Run operation list to see the records; "
            "an expired record does not mean the operation never ran.",
            id="malformed-id",
        ),
        pytest.param(
            "nonexistent-id",
            ["--id", "op-00000000000000000000000000000000"],
            3,
            "E_NOT_FOUND",
            "unknown_operation",
            "Run operation list to see the records; "
            "an expired record does not mean the operation never ran.",
            id="nonexistent-id",
        ),
        # Duplicate detection takes priority even when the first value is empty.
        pytest.param(
            "duplicate-id",
            ["--id=", "--id=op-never-created"],
            2,
            "E_USAGE",
            "conflicting_flags",
            "Pass --id once.",
            id="duplicate-id",
        ),
    ],
)
def test_operation_show_id_boundaries(
    tmp_path, record_property, variant, arguments, exit_code, error_code, problem, next_action
):
    scene = make_scene(tmp_path, record_property)
    seed_record(scene.sdk, scene, "sch edit", "planned", tag="boundary-initialized")
    code, result = scene.exe.call(["operation", "show", *arguments], expected_exit=exit_code)
    assert result["ok"] is False and code == exit_code
    error = result["error"]
    assert error["code"] == error_code and error["retryable"] is False
    details = error["details"]
    assert details["dispatch_state"] == "not_started"
    assert details["problem"] == problem and details["parameter"] == "--id"
    assert details["next_action"] == next_action
    if variant in {"missing-id", "empty-id"}:
        assert details["expected"] == "a non-empty string"
    else:
        assert "expected" not in details
    assert set(details) == {"dispatch_state", "problem", "parameter", "next_action"} | (
        {"expected"} if variant in {"missing-id", "empty-id"} else set()
    )
    assert len(scene.exe.calls) == 1
