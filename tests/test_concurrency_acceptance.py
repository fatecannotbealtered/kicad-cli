"""The controller cannot credit a merely completed or partially saved operation."""

import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from bench.persistence_acceptance.concurrency import MATRIX, cleaning_status, saved_outcome


def test_pure_verdict_import_does_not_require_windows_dlls():
    root = str(Path(__file__).resolve().parents[1])
    code = (
        "import ctypes,sys; "
        "ctypes.__dict__.pop('WinDLL',None); "
        "sys.path.insert(0,sys.argv[1]); "
        "from bench.persistence_acceptance.concurrency import MATRIX; "
        "assert MATRIX['attempts']==1"
    )
    result = subprocess.run([sys.executable, "-I", "-B", "-c", code, root], capture_output=True)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")


@pytest.fixture
def saved():
    return {
        "status": "succeeded",
        "receipt": {
            "outcome": {
                "execution": "completed",
                "change": "applied",
                "persistence": "saved",
                "verification": {"status": "passed"},
            }
        },
    }


def test_actual_saved_receipt_required(saved):
    assert saved_outcome(saved)["persistence"] == "saved"


@pytest.mark.parametrize(
    "field,value",
    [
        ("execution", "unknown"),
        ("change", "partial"),
        ("change", "not_applied"),
        ("persistence", "unknown"),
        ("persistence", "unchanged"),
    ],
)
def test_incomplete_receipt_cannot_pass(saved, field, value):
    changed = deepcopy(saved)
    changed["receipt"]["outcome"][field] = value
    with pytest.raises(AssertionError):
        saved_outcome(changed)


def test_public_unknown_is_not_a_saved_terminal_status(saved):
    saved["status"] = "unknown"
    with pytest.raises(AssertionError):
        saved_outcome(saved)


def test_finite_matrix_does_not_repeat_old_faults():
    ids = [row["id"] for row in MATRIX["cases"]]
    assert len(ids) == len(set(ids)) == 10
    assert MATRIX["attempts"] == 1
    assert "old F4/F5" in MATRIX["safety"]


@pytest.fixture
def cleaning():
    return {
        "status": "unknown",
        "operation_id": "op-A",
        "target": {"locator": "owned-A"},
        "observed_at": "2026-10-14T00:00:00Z",
        "extensions": {
            "writer_process": {"alive_or_unknown": True},
            "recovery_readback": {
                "completion": "completed",
                "outcome": "applied",
                "operation_id": "op-A",
                "target": {"locator": "owned-A"},
                "snapshot": {"revision": "final-A"},
                "verification": {"status": "passed"},
                "cleanup": {"status": "attributable"},
            },
        },
    }


def test_cleaning_unknown_requires_lease_and_saved_readback(cleaning):
    assert cleaning_status(cleaning, 0, "op-A", {"locator": "owned-A"}, "final-A") == "unknown"
    with pytest.raises(AssertionError):
        cleaning_status(cleaning, 9999999999, "op-A", {"locator": "owned-A"}, "final-A")


@pytest.mark.parametrize(
    "field,value",
    [
        ("completion", "incomplete"),
        ("outcome", "partial"),
        ("operation_id", "op-other"),
        ("target", {"locator": "wrong"}),
        ("snapshot", {"revision": "wrong"}),
        ("cleanup", {"status": "unknown"}),
        ("verification", {"status": "failed"}),
    ],
)
def test_cleaning_does_not_credit_wrong_or_incomplete_readback(cleaning, field, value):
    cleaning["extensions"]["recovery_readback"][field] = value
    with pytest.raises(AssertionError):
        cleaning_status(cleaning, 0, "op-A", {"locator": "owned-A"}, "final-A")
