"""Real saved-format lock parsing; no product or native subprocesses."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kicad_cli import move_readback
from kicad_cli._driver_sdk.models import DriverError
from kicad_cli.fileformat.board import Board
from kicad_cli.fileformat.sexpr import Document
from kicad_cli.move_lock import require_unlocked


def footprint(reference, lock="", *, identity_kind="uuid", identity=None):
    identity = identity or f"00000000-0000-4000-8000-{int(reference[1:]):012d}"
    identity_text = f'({identity_kind} "{identity}")' if identity_kind else ""
    bare, child = ("locked", "") if lock == "bare" else ("", lock)
    return f"""(footprint "fixture:R_0805" {bare}
      (layer "F.Cu") (at 100 100)
      (property "Reference" "{reference}")
      (property "Value" "10k") {identity_text} {child})"""


def board(*footprints):
    source = '(kicad_pcb (version 20260206) (layers (0 "F.Cu" signal))'
    return Board(Document.parse(source + "".join(footprints) + ")"))


def refusal(parsed, requested, observed=None, *, expected_code="E_VALIDATION"):
    before = parsed.document.dumps()
    with pytest.raises(DriverError) as caught:
        require_unlocked(parsed, requested, observed)
    error = caught.value
    assert error.code == expected_code
    assert error.explicit and error.dispatch_state == "not_started"
    assert parsed.document.dumps() == before
    return error


@pytest.mark.parametrize("lock", ["(locked yes)", "(locked)", "bare"])
def test_existing_model_lock_syntax_refuses_without_mutating(lock):
    parsed = board(footprint("R1", lock))
    assert parsed.footprints[0].locked is True
    error = refusal(parsed, {"R1": (98.5, 100)})
    assert error.details["problem"] == "invalid_value"
    assert error.details["parameter"] == "--moves"
    assert error.details["_untrusted"] == ["locked_targets"]
    assert error.details["reason"] == "locked_footprint"
    assert error.details["locked_targets"] == [
        {
            "ref": "R1",
            "id": "00000000-0000-4000-8000-000000000001",
            "identity_kind": "uuid",
            "locked": True,
        }
    ]


@pytest.mark.parametrize("lock", ["", "(locked no)"])
def test_unlocked_target_remains_unchanged(lock):
    parsed = board(footprint("R1", lock))
    assert parsed.footprints[0].locked is False
    before = parsed.document.dumps()
    assert require_unlocked(parsed, {"R1": (98.5, 100)}) is None
    assert parsed.document.dumps() == before


def test_legacy_bare_marker_after_header_children_still_refuses():
    parsed = board(footprint("R1").replace('"10k")', '"10k") locked'))
    assert "locked" in parsed.footprints[0].node.items[4:]
    assert refusal(parsed, {"R1": (98.5, 100)}).details["locked_targets"][0]["ref"] == "R1"


@pytest.mark.parametrize(
    "locks", ["(locked no)(locked yes)", "(locked yes)(locked no)", "(locked no)(locked no)"]
)
def test_duplicate_saved_lock_fields_are_ambiguous_and_never_move(locks):
    parsed = board(footprint("R1", locks))
    before = parsed.document.dumps()
    with pytest.raises(DriverError) as caught:
        require_unlocked(parsed, {"R1": (98.5, 100)})
    assert caught.value.code == "E_VALIDATION"
    assert caught.value.details["reason"] == "ambiguous_lock_encoding"
    assert caught.value.details["targets"][0]["ref"] == "R1"
    assert parsed.document.dumps() == before


def test_mixed_selection_reports_all_locked_targets_before_any_move():
    parsed = board(
        footprint("R3", "bare"), footprint("R2", "(locked no)"), footprint("R1", "(locked yes)")
    )
    error = refusal(parsed, {"R3": (90, 90), "R2": (91, 91), "R1": (92, 92)})
    assert [item["ref"] for item in error.details["locked_targets"]] == ["R1", "R3"]
    assert [fp.position for fp in parsed.footprints] == [(100_000_000, 100_000_000)] * 3
    assert [fp.locked for fp in parsed.footprints] == [True, False, True]


def test_lock_on_unselected_or_differently_cased_reference_does_not_restrict_selection():
    parsed = board(footprint("R1", "(locked yes)"), footprint("R2", "(locked no)"))
    before = parsed.document.dumps()
    assert require_unlocked(parsed, ["R2", "r1"]) is None
    assert parsed.document.dumps() == before


@pytest.mark.parametrize("kind", ["uuid", "tstamp"])
def test_actual_saved_identity_agrees_with_independent_observation(tmp_path, kind):
    parsed = board(footprint("R1", "(locked yes)", identity_kind=kind))
    path = tmp_path / "saved.kicad_pcb"
    path.write_text(parsed.document.dumps(), encoding="utf-8")
    raw = path.read_bytes()
    observed = move_readback.observe(path, ["R1"])
    error = refusal(Board.load(path), ["R1"], observed)
    target = error.details["locked_targets"][0]
    assert target["id"] == observed.objects[0]["id"]
    assert target["identity_kind"] == kind
    assert path.read_bytes() == raw


def test_content_identity_comes_from_independent_observation(tmp_path):
    parsed = board(footprint("R1", "bare", identity_kind=None))
    path = tmp_path / "saved.kicad_pcb"
    path.write_text(parsed.document.dumps(), encoding="utf-8")
    observed = move_readback.observe(path, ["R1"])
    error = refusal(parsed, ["R1"], observed)
    assert error.details["locked_targets"][0]["id"] == observed.objects[0]["id"]
    assert error.details["locked_targets"][0]["identity_kind"] == "content_sha256"


@pytest.mark.parametrize("drift", ["uuid", "kind", "missing", "duplicate"])
def test_observation_identity_mismatch_is_rejected(drift):
    parsed = board(footprint("R1", "(locked yes)"))
    item = {
        "ref": "R1",
        "id": "00000000-0000-4000-8000-000000000001",
        "identity_kind": "uuid",
    }
    items = [item]
    if drift == "uuid":
        item["id"] = "other-uuid"
    elif drift == "kind":
        item["identity_kind"] = "tstamp"
    elif drift == "missing":
        items = []
    else:
        items = [item, dict(item)]
    error = refusal(parsed, ["R1"], SimpleNamespace(objects=items), expected_code="E_CONFLICT")
    assert error.details["conflict"] == "state_drift"
    assert error.details["reason"] == "locked_footprint_identity_mismatch"


def test_without_observation_missing_identity_is_not_invented():
    parsed = board(footprint("R1", "bare", identity_kind=None))
    error = refusal(parsed, ["R1"])
    target = error.details["locked_targets"][0]
    assert target["id"] is None and target["identity_kind"] is None
