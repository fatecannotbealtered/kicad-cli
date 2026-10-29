"""Source integration of early refusal, discovery and existing rollback protection."""

import os
from pathlib import Path

import pytest
from driver_test_utils import grant
from test_move_preflight_binding import bounded_case as bounded_case

from kicad_cli import driver_runtime, move_preflight
from kicad_cli._driver_sdk.models import DriverError
from kicad_cli.fileformat.board import Board
from kicad_cli.fileformat.sexpr import Document, List
from kicad_cli.move_driver_adapter import MoveAdapter
from kicad_cli.move_transaction import MoveTransaction

FIXTURE = Path(__file__).parent / "fixtures/sch_edit/edit.kicad_pcb"


@pytest.mark.parametrize("selection", ["single", "mixed"])
def test_locked_selection_refuses_in_real_runtime_before_preflight(
    tmp_path, monkeypatch, selection
):
    path = tmp_path / "edit.kicad_pcb"
    path.write_bytes(FIXTURE.read_bytes())
    board = Board.load(path)
    selected = next(fp for fp in board.footprints if fp.reference == "R4")
    selected.node.append(List.new("locked", "yes"))
    path.write_text(board.document.dumps(), encoding="utf-8", newline="\n")
    baseline = path.read_bytes()
    identity = selected.node.find("uuid").value(1)
    env = dict(os.environ, **grant(tmp_path))

    def forbidden(*args, **kwargs):
        raise AssertionError("A locked selection must not start the preflight")

    monkeypatch.setattr(move_preflight, "prepare", forbidden)
    with driver_runtime.create_cli(environ=env, home=tmp_path) as cli:
        result = cli.run(
            [
                "board",
                "footprint",
                "move",
                "--board",
                str(path),
                "--moves",
                "R4:99.5,110" if selection == "single" else "R5:105.5,110;R4:99.5,110",
                "--dry-run",
            ]
        )
        response = result.envelope
        cli.validator.validate("response", response)
    assert response["ok"] is False
    assert response["error"]["code"] == "E_VALIDATION"
    details = response["error"]["details"]
    assert details["locked_targets"] == [
        {"ref": "R4", "id": identity, "identity_kind": "uuid", "locked": True}
    ]
    assert details["reason"] == "locked_footprint"
    assert details["_untrusted"] == ["locked_targets"]
    assert path.read_bytes() == baseline
    assert not (tmp_path / "driver-state/move-preflights").exists()
    assert not Path(str(path) + ".kicad-cli.journal").exists()


def test_selected_reference_publishes_substructure_schema_without_a_result_schema_alias(tmp_path):
    with driver_runtime.create_cli(home=tmp_path) as cli:
        result = cli.run(["reference", "--command", "board footprint move"])
        response = result.envelope
        cli.validator.validate("response", response)
    assert response["ok"]
    data = response["data"]
    assert data["command"]["preflight_schema"] == move_preflight.SCHEMA
    assert "result_schema" not in data["command"]["driver"]
    assert data["json_schemas"][move_preflight.SCHEMA]["properties"]["status"]["enum"] == [
        "passed",
        "blocked",
    ]


def test_pre_safety_plan_has_no_authority_to_dispatch(tmp_path):
    with driver_runtime.create_cli(home=tmp_path) as cli:
        with pytest.raises(DriverError) as found:
            cli.adapter.validate_plan(
                {"command": "board footprint move", "plan": {"extensions": {}}}
            )
    assert found.value.code == "E_CONFLICT"
    assert found.value.dispatch_state == "not_started"
    assert found.value.details["reason"] == "missing_move_preflight"


@pytest.mark.parametrize("failure", ["E_VERIFY_INCOMPLETE", "E_NATIVE_FAILURE", "E_TIMEOUT"])
def test_preflight_failure_is_valid_before_write_without_a_fabricated_receipt(
    tmp_path, monkeypatch, failure
):
    path = tmp_path / "edit.kicad_pcb"
    path.write_bytes(FIXTURE.read_bytes())
    baseline = path.read_bytes()
    metadata = path.stat().st_mtime_ns
    env = dict(os.environ, **grant(tmp_path))
    calls = []

    def incomplete(*args, **kwargs):
        calls.append(1)
        raise move_preflight._error(
            failure,
            "Retained synthetic native observation, no native process started.",
            phase="prepare-version",
            reason="synthetic incomplete observation",
            native_call_started=False,
        )

    monkeypatch.setattr(move_preflight, "prepare", incomplete)
    with driver_runtime.create_cli(environ=env, home=tmp_path) as cli:
        result = cli.run(
            [
                "board",
                "footprint",
                "move",
                "--board",
                str(path),
                "--moves",
                "R4:99.5,110",
                "--dry-run",
            ]
        )
        response = result.envelope
        cli.validator.validate("response", response)
    assert not response["ok"]
    assert response["error"]["code"] == (
        "E_TIMEOUT" if failure == "E_TIMEOUT" else "E_NATIVE_FAILURE"
    )
    details = response["error"]["details"]
    assert details["dispatch_state"] == "not_started"
    assert details["engineering_write_started"] is False
    assert details["automatic_retry"] is False
    assert "Do not replay" in details["next_action"]
    assert "receipt" not in details
    assert calls == [1]
    assert path.read_bytes() == baseline and path.stat().st_mtime_ns == metadata


@pytest.mark.parametrize("observed", ["baseline", "intended", "external"])
def test_existing_move_transaction_only_restores_its_own_recognised_bytes(tmp_path, observed):
    import hashlib

    path = tmp_path / "owned-synthetic-board"
    baseline, intended, external = b"baseline", b"intended", b"external edit"
    path.write_bytes(baseline)
    transaction = MoveTransaction(
        path,
        "new-source-rollback-test",
        hashlib.sha256(baseline).hexdigest(),
        hashlib.sha256(intended).hexdigest(),
    )
    transaction.arm()
    path.write_bytes({"baseline": baseline, "intended": intended, "external": external}[observed])
    if observed == "external":
        with pytest.raises(OSError, match="preserve external edit"):
            transaction.rollback()
        assert path.read_bytes() == external
        assert transaction.backup.is_file() and transaction.journal.is_file()
    else:
        transaction.rollback()
        assert path.read_bytes() == baseline
        assert not transaction.backup.exists() and not transaction.journal.exists()


@pytest.mark.parametrize("failure", ["E_CONFLICT", "E_VALIDATION"])
@pytest.mark.parametrize("restoration", ["proved", "unproved"])
def test_post_arm_driver_error_retains_semantics_and_valid_failure_receipt(
    bounded_case, tmp_path, monkeypatch, failure, restoration
):
    """Real arm/SDK receipt; controlled native service and writer failure, no native process."""
    for key, value in grant(tmp_path).items():
        monkeypatch.setenv(key, value)
    board = bounded_case["board"]
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in board.parent.rglob("*")
        if path.is_file()
    }
    arguments = ["board", "footprint", "move", "--board", str(board), "--moves", "R4:99.5,110"]
    armed, saved = [], []
    original_arm, original_validate = MoveTransaction.arm, MoveAdapter.validate_plan
    with driver_runtime.create_cli(environ=dict(os.environ), home=tmp_path) as cli:
        preview = cli.run([*arguments, "--dry-run"]).envelope
        assert preview["ok"], preview
        data = preview["data"]
        candidate = Path(
            data["preview"]["extensions"]["preflight"]["staging"]["stages"]["candidate"]["board"]
        )

        def arm(transaction):
            original_arm(transaction)
            assert transaction.armed and transaction.backup.is_file()
            assert transaction.journal.is_file()
            armed.append(transaction)
            if failure == "E_CONFLICT":
                candidate.write_bytes(candidate.read_bytes() + b"\n; controlled candidate drift\n")

        def validate(adapter, record):
            original_validate(adapter, record)
            if armed and failure == "E_VALIDATION":
                raise move_preflight._error(
                    failure,
                    "Controlled post-arm validation failure; no native process.",
                    reason="source-test-controlled-validation",
                    problem="invalid_value",
                    parameter="--board",
                    write_state="saved",
                )

        def forbidden_save(*_args, **_kwargs):
            saved.append(True)
            raise AssertionError("A failed under-lock validation cannot save the formal board")

        def unproved_rollback(_transaction):
            raise OSError()

        monkeypatch.setattr(MoveTransaction, "arm", arm)
        monkeypatch.setattr(MoveAdapter, "validate_plan", validate)
        monkeypatch.setattr(Document, "save", forbidden_save)
        if restoration == "unproved":
            monkeypatch.setattr(MoveTransaction, "rollback", unproved_rollback)
        result = cli.run([*arguments, "--confirm", data["confirm_token"]])
        response = result.envelope
        cli.validator.validate("response", response)
        assert not response["ok"]
        assert response["error"]["code"] == (
            "E_OUTCOME_UNKNOWN" if restoration == "unproved" else failure
        )
        details = response["error"]["details"]
        assert details["dispatch_state"] == "dispatched"
        assert "contract_errors" not in details
        receipt = details["receipt"]
        cli.validator.validate("receipt", receipt)
        assert receipt["operation_id"] == data["preview"]["operation_id"]
        assert receipt["outcome"]["execution"] == "failed"
        assert receipt["outcome"]["verification"]["status"] == "incomplete"
        assert response["error"]["code"] in cli.validator.receipt_rule(receipt)["codes"]
        if restoration == "proved":
            semantic_key = "conflict" if failure == "E_CONFLICT" else "problem"
            semantic_value = "state_drift" if failure == "E_CONFLICT" else "invalid_value"
            assert details[semantic_key] == semantic_value
            assert details["engineering_write_started"] is False
            assert details["automatic_retry"] is False
            assert details["write_state"] == "rolled_back"
            assert receipt["outcome"]["change"] == "not_applied"
            assert receipt["outcome"]["persistence"] == "unchanged"
            assert receipt["extensions"]["restoration_error"] is None
            assert receipt["snapshot_after"] is not None
            assert not armed[0].backup.exists() and not armed[0].journal.exists()
        else:
            assert details["write_state"] == "unknown"
            assert receipt["outcome"]["change"] == receipt["outcome"]["persistence"] == "unknown"
            assert "conflict" not in details and "problem" not in details
            assert "engineering_write_started" not in details and "automatic_retry" not in details
            assert receipt["extensions"]["restoration_error"] == ""
            assert receipt["snapshot_after"] is None
            assert receipt["outcome"]["verification"]["snapshot_id"] is None
            assert receipt["outcome"]["verification"]["checks"] == []
            assert armed[0].backup.is_file() and armed[0].journal.is_file()
    assert armed and saved == []
    assert all(
        path.read_bytes() == raw and path.stat().st_mtime_ns == stamp
        for path, (raw, stamp) in before.items()
    )
    assert bounded_case["seen"] == [
        "prepare-version",
        "prepare-drc",
        "baseline-drc",
        "candidate-drc",
    ]
