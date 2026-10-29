"""Frozen-input representative policies reject drift and unrelated findings."""

from pathlib import Path
from types import SimpleNamespace

import erc_representatives as R
import pytest

FIXTURE = Path(__file__).parent / "fixtures/erc/erc2.kicad_sch"


def pair(rule, level, native_items, own_items):
    native = {"type": rule, "severity": level, "sheet_path": "/", "items": list(native_items)}
    own = SimpleNamespace(
        rule=rule, severity=level, sheet="/", items=[SimpleNamespace(uuid=u) for u in own_items]
    )
    return native, own


def bare():
    return pair(
        "bus_to_net_conflict", "error", (R.BARE_WIRE, R.BARE_BUS), (R.BARE_WIRE, R.BARE_LABEL)
    )


def test_bare_wire_has_a_proven_bus_and_label_representative():
    native, own = bare()
    assert R.reconcile(FIXTURE, [native], [own]) == ([], [])


@pytest.mark.parametrize(
    "defect", ["rule", "item", "same-side", "sheet", "severity", "missing-severity", "duplicate"]
)
def test_bare_junction_does_not_hide_other_failures(defect):
    native, own = bare()
    if defect == "rule":
        native["type"] = "wire_dangling"
    elif defect == "item":
        native["items"][0] = "not-the-wire"
    elif defect == "same-side":
        native["items"] = [R.BARE_BUS, R.BARE_LABEL]
    elif defect == "sheet":
        native["sheet_path"] = "/different/"
    elif defect == "severity":
        own.severity = "warning"
    elif defect == "missing-severity":
        del native["severity"]
    else:
        native["items"] = [R.BARE_WIRE, R.BARE_WIRE]
    assert R.reconcile(FIXTURE, [native], [own]) == ([native], [own])


def test_bare_junction_only_consumes_one_complete_pair():
    native, own = bare()
    assert R.reconcile(FIXTURE, [native, native], [own]) == ([native], [])
    assert R.reconcile(FIXTURE, [native], []) == ([native], [])


def test_bare_geometry_rejects_changed_input(tmp_path):
    changed = tmp_path / "changed.kicad_sch"
    changed.write_bytes(FIXTURE.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="bytes changed"):
        R.checked_bare_geometry(changed)


@pytest.fixture
def named_policy(monkeypatch):
    # This fixture exercises the matcher AFTER proof validation. Real input
    # validation is separately rechecked against the retained native case.
    pins = [v[0] for v in R.USB_POWER_PINS.values()]
    labels = list(R.VBUS_LABELS)
    pairs = {frozenset((p, label)) for p in pins for label in labels}
    monkeypatch.setattr(R, "checked_one_air_names", lambda path: pairs)
    return pins, labels


@pytest.mark.parametrize("power_index", [0, 3])
def test_proven_named_net_accepts_alternate_root_label(named_policy, power_index):
    pins, labels = named_policy
    native, own = pair(
        "multiple_net_names", "warning", (pins[power_index], labels[1]), (pins[0], labels[0])
    )
    assert R.reconcile_one_air(Path("unused"), [native], [own]) == ([], [])


@pytest.mark.parametrize(
    "defect",
    [
        "rule",
        "severity",
        "missing-severity",
        "sheet",
        "unrelated",
        "two-labels",
        "two-pins",
        "duplicate",
    ],
)
def test_named_net_policy_preserves_unproven_or_wrong_findings(named_policy, defect):
    pins, labels = named_policy
    native, own = pair("multiple_net_names", "warning", (pins[0], labels[1]), (pins[0], labels[0]))
    if defect == "rule":
        native["type"] = "pin_to_pin"
    elif defect == "severity":
        native["severity"] = "error"
    elif defect == "missing-severity":
        del native["severity"]
    elif defect == "sheet":
        native["sheet_path"] = "/child/"
    elif defect == "unrelated":
        native["items"][1] = "unrelated-same-looking-label"
    elif defect == "two-labels":
        native["items"] = labels
    elif defect == "two-pins":
        native["items"] = pins[:2]
    else:
        native["items"] = [pins[0], pins[0]]
    assert R.reconcile_one_air(Path("unused"), [native], [own]) == ([native], [own])


def test_named_net_policy_does_not_absorb_duplicates_or_missing_peer(named_policy):
    pins, labels = named_policy
    native, own = pair("multiple_net_names", "warning", (pins[0], labels[1]), (pins[0], labels[0]))
    assert R.reconcile_one_air(Path("unused"), [native, native], [own]) == ([native], [])
    assert R.reconcile_one_air(Path("unused"), [], [own]) == ([], [own])


def test_named_net_rejects_any_frozen_input_drift(tmp_path):
    altered = tmp_path / "changed.kicad_sch"
    altered.write_text('(kicad_sch (label "VBUS"))', encoding="utf-8")
    with pytest.raises(ValueError, match="frozen input bytes"):
        R.checked_one_air_names(altered)
