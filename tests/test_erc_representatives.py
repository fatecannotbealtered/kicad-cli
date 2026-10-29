"""A representative mismatch cannot hide changed rules, objects or fixture bytes."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from erc_representatives import BUS, BUS_LABEL, NET_LABEL, WIRE, reconcile

FIXTURE = Path(__file__).parent / "fixtures/erc/erc2.kicad_sch"


def finding():
    native = {
        "type": "bus_to_net_conflict",
        "severity": "error",
        "sheet_path": "/",
        "items": [WIRE, BUS],
    }
    ours = SimpleNamespace(
        rule="bus_to_net_conflict",
        severity="error",
        sheet="/",
        items=[SimpleNamespace(uuid=NET_LABEL), SimpleNamespace(uuid=BUS_LABEL)],
    )
    return native, ours


def test_same_frozen_junction_has_two_valid_representatives():
    native, ours = finding()
    assert reconcile(FIXTURE, [native], [ours]) == ([], [])


@pytest.mark.parametrize(
    "defect",
    [
        "wrong-rule",
        "wrong-item",
        "same-side",
        "wrong-sheet",
        "severity",
        "missing-severity",
        "duplicate-item",
    ],
)
def test_invalid_representative_does_not_match(defect):
    native, ours = finding()
    if defect == "wrong-rule":
        native["type"] = "unconnected_wire_endpoint"
    elif defect == "wrong-item":
        native["items"][0] = "unrelated-uuid"
    elif defect == "same-side":
        native["items"] = [WIRE, NET_LABEL]
    elif defect == "wrong-sheet":
        native["sheet_path"] = "/other/"
    elif defect == "severity":
        native["severity"] = "ignore"
    elif defect == "missing-severity":
        del native["severity"]
    elif defect == "duplicate-item":
        native["items"] = [WIRE, WIRE]
    assert reconcile(FIXTURE, [native], [ours]) == ([native], [ours])


def test_missing_or_duplicate_finding_cannot_be_absorbed():
    native, ours = finding()
    assert reconcile(FIXTURE, [], [ours]) == ([], [ours])
    assert reconcile(FIXTURE, [native], []) == ([native], [])
    assert reconcile(FIXTURE, [native, native], [ours]) == ([native], [])
    assert reconcile(FIXTURE, [native], [ours, ours]) == ([], [ours])


def test_fixture_drift_refuses_equivalence(tmp_path):
    native, ours = finding()
    copy = tmp_path / "drift.kicad_sch"
    copy.write_bytes(FIXTURE.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="bytes changed"):
        reconcile(copy, [native], [ours])
