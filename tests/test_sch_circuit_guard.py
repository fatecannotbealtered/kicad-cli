"""Controlled raw-graph expectations, not Native or public-EXE qualification."""

from __future__ import annotations

from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from kicad_cli.fileformat.circuit import Design
from kicad_cli.sch_circuit_guard import (
    EXPECTED_SCHEMA,
    CircuitGuardError,
    query_connections,
    verify,
)
from kicad_cli.sch_objects import Catalog


def sch(body="", *, uid="root", library=""):
    return f'(kicad_sch (version 20250114) (uuid "{uid}") {library} {body})\n'


def wire(uid, a, b):
    return (
        f"(wire (pts (xy {a[0]} {a[1]}) (xy {b[0]} {b[1]})) "
        f'(stroke (width 0) (type default)) (uuid "{uid}"))'
    )


def label(uid, text, at, kind="label"):
    return f'({kind} "{text}" (at {at[0]} {at[1]} 0) (effects (font (size 1 1))) (uuid "{uid}"))'


def junction(uid, at):
    return f'(junction (at {at[0]} {at[1]}) (diameter 0) (color 0 0 0 0) (uuid "{uid}"))'


def unit(uid, at, *, unit=1, angle=0, mirror="", lib="T:X"):
    return (
        f'(symbol (lib_id "{lib}") (at {at[0]} {at[1]} {angle}) '
        f'{mirror} (unit {unit}) (uuid "{uid}") '
        '(property "Reference" "U73" (at 0 -1 0)) '
        '(property "Value" "X" (at 0 1 0)))'
    )


LIB = (
    '(lib_symbols (symbol "T:X" (symbol "X_1_1" '
    '(pin passive line (at 0 0 0) (length 1) (name "P") (number "7")))))'
)


def models(tmp_path, before, after):
    path = tmp_path / "root.kicad_sch"
    path.write_text(before, encoding="utf-8")
    a = Design(path)
    b = Design(path, {path.resolve(): after})
    return a, b, Catalog(a), Catalog(b)


def anchor(cat, uid, *, instance="/root", pin=None, sheet_pin=None):
    obj = next(o for o in cat.objects if o.record.get("uuid") == uid)
    value = {"selector": obj.record["selector"], "instance_path": instance}
    if pin is not None:
        value["pin_ordinal"] = pin
    if sheet_pin is not None:
        value["sheet_pin_ordinal"] = sheet_pin
    return value


def fresh(name, *, instance="/root", pin=None):
    value = {"new": name, "instance_path": instance}
    if pin is not None:
        value["pin_ordinal"] = pin
    return value


def component(*members, names=(), nc=()):
    return {"members": list(members), "explicit_names": list(names), "nc_members": list(nc)}


def alias(kind, *uids, index=0, source="root.kicad_sch"):
    return {"kind": kind, "source_relative": source, "uuids": list(uids), "action_index": index}


def apply(models, actions, expected, aliases, *, touched=(), instances=("/root",), targets=None):
    a, b, ac, bc = models
    request = {
        "actions": actions,
        "expected": {"components": expected},
        "affected_instances": list(instances),
    }
    Draft202012Validator(EXPECTED_SCHEMA).validate(request["expected"])
    return verify(a, b, ac, bc, request, aliases, list(touched), targets)


def kinds(report):
    return {row["kind"] for row in report["differences"]}


def test_inserting_source_object_does_not_reidentify_untouched_wire(tmp_path):
    old = wire("old", (0, 0), (10, 0))
    m = models(tmp_path, sch(old), sch(wire("new", (20, 0), (30, 0)) + old))
    result = apply(m, [{"op": "create"}], [component(fresh("new"))], {"new": alias("wire", "new")})
    assert result["ok"], result
    assert result["connections"]["removed_count"] == 0
    assert result["connections"]["added_count"] == 1


def test_one_explicit_polyline_alias_expands_multiple_source_wire_segments(tmp_path):
    m = models(
        tmp_path, sch(), sch(wire("first", (0, 0), (5, 0)) + wire("second", (5, 0), (5, 10)))
    )
    result = apply(
        m,
        [{"op": "create"}],
        [component(fresh("polyline"))],
        {"polyline": alias("wire", "first", "second")},
    )
    assert result["ok"], result
    assert len(result["connections"]["after"][0]["occurrences"]) == 2


def test_polyline_alias_does_not_authorize_nonadjacent_self_crossing(tmp_path):
    m = models(
        tmp_path,
        sch(),
        sch(
            wire("first", (0, 0), (10, 10))
            + wire("second", (10, 10), (0, 10))
            + wire("third", (0, 10), (10, 0))
        ),
    )
    result = apply(
        m,
        [{"op": "create"}],
        [component(fresh("polyline"))],
        {"polyline": alias("wire", "first", "second", "third")},
    )
    assert not result["ok"]
    assert "undeclared_geometric_contact_change" in kinds(result)
    rejected = [row for row in result["contacts"]["changes"] if not row["declared"]]
    assert len(rejected) == 1


def test_query_components_are_directly_reusable_and_pinless_components_remain(tmp_path):
    old = wire("old", (0, 0), (10, 0)) + label("name", "LOCAL_X", (10, 0))
    m = models(tmp_path, sch(old), sch(old + wire("new", (20, 0), (30, 0))))
    page = query_connections(m[2], limit=1)
    assert page["total"] == 1 and page["has_more"] is False
    c = page["items"][0]["component"]
    Draft202012Validator(EXPECTED_SCHEMA).validate({"components": [c]})
    assert c["explicit_names"] == [{"kind": "label", "name": "/LOCAL_X"}]
    result = apply(
        m, [{"op": "create"}], [c, component(fresh("new"))], {"new": alias("wire", "new")}
    )
    assert result["ok"], result


def test_declared_contact_does_not_waive_undeclared_component_merge(tmp_path):
    old = wire("a", (0, 0), (10, 0)) + wire("b", (20, 0), (30, 0))
    m = models(tmp_path, sch(old), sch(old + wire("bridge", (10, 0), (20, 0))))
    contacts = [anchor(m[2], "a"), anchor(m[2], "b")]
    actions = [{"op": "create", "contact_targets": contacts}]
    aliases = {"bridge": alias("wire", "bridge")}
    bad = apply(m, actions, [component(fresh("bridge"))], aliases)
    assert not bad["ok"]
    assert "undeclared_new_or_changed_component" in kinds(bad)
    good = apply(m, actions, [component(*contacts, fresh("bridge"))], aliases)
    assert good["ok"], good
    assert len(good["connections"]["merges"]) == 1


def test_same_net_unmentioned_crossing_is_not_exempted(tmp_path):
    geometry = [
        ((0, 0), (10, 0)),
        ((10, 0), (10, 10)),
        ((10, 10), (0, 10)),
        ((0, 10), (0, 0)),
        ((0, 5), (10, 5)),
    ]
    old = "".join(wire(f"w{i}", a, b) for i, (a, b) in enumerate(geometry))
    old += junction("j0", (0, 5)) + junction("j1", (10, 5))
    m = models(tmp_path, sch(old), sch(old + wire("diagonal", (0, 0), (10, 10))))
    members = [anchor(m[2], f"w{i}") for i in range(5)]
    aliases = {"diagonal": alias("wire", "diagonal")}
    expected = [component(*members, fresh("diagonal"))]
    bad = apply(m, [{"op": "create", "contact_targets": members[:4]}], expected, aliases)
    assert not bad["ok"]
    assert "undeclared_geometric_contact_change" in kinds(bad)
    good = apply(m, [{"op": "create", "contact_targets": members}], expected, aliases)
    assert good["ok"], good


def test_new_unjunctioned_crossing_requires_explicit_contact_acknowledgment(tmp_path):
    old = wire("horizontal", (0, 5), (10, 5))
    m = models(tmp_path, sch(old), sch(old + wire("vertical", (5, 0), (5, 10))))
    expected = [component(fresh("vertical"))]
    aliases = {"vertical": alias("wire", "vertical")}
    bad = apply(m, [{"op": "create"}], expected, aliases)
    assert "undeclared_geometric_contact_change" in kinds(bad)
    good = apply(
        m, [{"op": "create", "contact_targets": [anchor(m[2], "horizontal")]}], expected, aliases
    )
    assert good["ok"], good
    assert good["connections"]["removed_count"] == 0


def test_nc_to_connected_transition_reports_raw_pin_and_preserves_library(tmp_path):
    old = unit("unit", (0, 0)) + '(no_connect (at 0 0) (uuid "nc"))'
    new = unit("unit", (0, 0)) + wire("wire", (0, 0), (10, 0)) + label("label", "SENSE", (10, 0))
    m = models(tmp_path, sch(old, library=LIB), sch(new, library=LIB))
    pin = anchor(m[2], "unit", pin=0)
    actions = [
        {"op": "delete", "target": anchor(m[2], "nc")["selector"]},
        {"op": "create", "contact_targets": [pin]},
        {"op": "create", "contact_targets": [fresh("wire")]},
    ]
    aliases = {"wire": alias("wire", "wire", index=1), "label": alias("label", "label", index=2)}
    target = next(o for o in m[2].objects if o.record.get("uuid") == "nc")
    result = apply(
        m,
        actions,
        [
            component(
                pin, fresh("wire"), fresh("label"), names=[{"kind": "label", "name": "/SENSE"}]
            )
        ],
        aliases,
        touched=[target],
    )
    assert result["ok"], result
    assert result["connections"]["nc_changes"] == [{"pin": pin, "before": True, "after": False}]


def test_duplicate_pin_numbers_are_queryable_as_separate_occurrences(tmp_path):
    duplicate_lib = LIB.replace(
        '(number "7")))',
        '(number "7")) (pin passive line (at 0 0 0) (length 1) (name "DUP") (number "7")))',
    )
    text = sch(unit("unit", (0, 0)) + '(no_connect (at 0 0) (uuid "nc"))', library=duplicate_lib)
    m = models(tmp_path, text, text)
    row = query_connections(m[2])["items"][0]
    pins = [p for p in row["occurrences"] if p["kind"] == "pin"]
    assert len(pins) == 2
    assert {p["lib_pin_ordinal"] for p in pins} == {0, 1}
    assert len(row["component"]["nc_members"]) == 2


def test_global_merge_discloses_remote_instance_and_rejects_incomplete_ack(tmp_path):
    sheet = (
        '(sheet (at 50 50) (size 10 10) (uuid "child-sheet") '
        '(property "Sheetname" "amp") (property "Sheetfile" "child.kicad_sch"))'
    )
    (tmp_path / "child.kicad_sch").write_text(
        sch(
            wire("remote-wire", (100, 0), (110, 0))
            + label("remote-name", "VREF", (110, 0), "global_label"),
            uid="child",
        ),
        encoding="utf-8",
    )
    old = wire("wire", (0, 0), (10, 0)) + sheet
    m = models(tmp_path, sch(old), sch(old + label("name", "VREF", (10, 0), "global_label")))
    remote = "/root/child-sheet"
    members = [
        anchor(m[2], "wire"),
        fresh("name"),
        anchor(m[2], "remote-wire", instance=remote),
        anchor(m[2], "remote-name", instance=remote),
    ]
    actions = [{"op": "create", "contact_targets": [anchor(m[2], "wire")]}]
    expected = [component(*members, names=[{"kind": "global_label", "name": "VREF"}])]
    aliases = {"name": alias("global_label", "name")}
    bad = apply(m, actions, expected, aliases)
    assert "affected_instances_incomplete_or_inaccurate" in kinds(bad)
    good = apply(m, actions, expected, aliases, instances=("/root", remote))
    assert good["ok"], good


def test_unrelated_bus_is_not_a_whole_design_ban_but_contact_is_rejected(tmp_path):
    bus = '(bus (pts (xy 100 0) (xy 110 0)) (stroke (width 0) (type default)) (uuid "bus"))'
    bus += label("bus-name", "D[0..2]", (100, 0))
    old = bus + wire("old", (0, 0), (10, 0))
    m = models(tmp_path, sch(old), sch(old + wire("new", (20, 0), (30, 0))))
    result = apply(m, [{"op": "create"}], [component(fresh("new"))], {"new": alias("wire", "new")})
    assert result["ok"], result
    m = models(tmp_path, sch(old), sch(old + wire("new", (105, -2), (105, 2))))
    result = apply(m, [{"op": "create"}], [component(fresh("new"))], {"new": alias("wire", "new")})
    assert "unsupported_fixed_bus_contact_changed" in kinds(result)


@pytest.mark.parametrize("problem", ["missing_new", "bad_pin", "wrong_instance", "stale"])
def test_invalid_anchor_is_a_structured_prewrite_error(tmp_path, problem):
    old = unit("unit", (0, 0))
    m = models(
        tmp_path, sch(old, library=LIB), sch(old + wire("new", (20, 0), (30, 0)), library=LIB)
    )
    ref = fresh("new")
    if problem == "missing_new":
        ref = fresh("absent")
    elif problem == "bad_pin":
        ref = anchor(m[2], "unit", pin=30)
    elif problem == "wrong_instance":
        ref = fresh("new", instance="/absent")
    else:
        ref = deepcopy(anchor(m[2], "unit", pin=0))
        ref["selector"]["snapshot"] = "0" * 64
    protected = (tmp_path / "root.kicad_sch").read_bytes()
    with pytest.raises(ValueError):
        apply(m, [{"op": "create"}], [component(ref)], {"new": alias("wire", "new")})
    assert (tmp_path / "root.kicad_sch").read_bytes() == protected


def test_query_pagination_and_bad_public_expectation_shape(tmp_path):
    text = sch(wire("a", (0, 0), (1, 0)) + wire("b", (10, 0), (11, 0)))
    m = models(tmp_path, text, text)
    one = query_connections(m[2], limit=1)
    assert one["has_more"] and one["next_offset"] == 1
    two = query_connections(m[2], offset=1, limit=1)
    assert not two["has_more"]
    assert one["items"][0]["connection_id"] != two["items"][0]["connection_id"]
    with pytest.raises(CircuitGuardError):
        query_connections(m[2], limit=True)
    assert list(Draft202012Validator(EXPECTED_SCHEMA).iter_errors({"components": "anything"}))


@pytest.mark.parametrize("intrusion", ["metadata", "drawing", "library"])
def test_non_target_metadata_drawing_and_library_are_protected(tmp_path, intrusion):
    old = sch(unit("unit", (0, 0)), library=LIB).replace(
        '(uuid "root")', '(uuid "root") (paper "A4")'
    )
    candidate = old[:-2] + wire("new", (20, 0), (30, 0)) + ")\n"
    if intrusion == "metadata":
        candidate = candidate.replace('(paper "A4")', '(paper "A3")')
    elif intrusion == "drawing":
        candidate = candidate[:-2] + '(text "UNDECLARED" (at 80 80 0) (uuid "text")) )\n'
    else:
        candidate = candidate.replace('(name "P")', '(name "RENAMED")')
    m = models(tmp_path, old, candidate)
    result = apply(m, [{"op": "create"}], [component(fresh("new"))], {"new": alias("wire", "new")})
    assert not result["ok"]
    assert result["verified"]["protected_content_preserved"] is False


def test_explicit_endpoint_reference_does_not_grant_overlap_farther_along_wire(tmp_path):
    old = wire("old", (0, 0), (10, 0))
    aliases = {"new": alias("wire", "new")}
    m = models(tmp_path, sch(old), sch(old + wire("new", (0, 0), (8, 0))))
    endpoint = {"object": anchor(m[2], "old")["selector"], "instance_path": "/root", "endpoint": 0}
    actions = [{"op": "create", "points": [endpoint, [8, 0]]}]
    result = apply(m, actions, [component(anchor(m[2], "old"), fresh("new"))], aliases)
    assert "undeclared_geometric_contact_change" in kinds(result)
    m = models(tmp_path, sch(old), sch(old + wire("new", (0, 0), (0, 5))))
    result = apply(
        m,
        [{"op": "create", "points": [endpoint, [0, 5]]}],
        [component(anchor(m[2], "old"), fresh("new"))],
        aliases,
    )
    assert result["ok"], result


def test_unreadable_unrelated_page_allows_local_pinless_edit_but_not_new_global(tmp_path):
    sheet = (
        '(sheet (at 50 50) (size 10 10) (uuid "bad") '
        '(property "Sheetname" "bad") (property "Sheetfile" "bad.kicad_sch"))'
    )
    (tmp_path / "bad.kicad_sch").write_text(
        sch(unit("bad-unit", (0, 0)), uid="bad"), encoding="utf-8"
    )
    old = wire("old", (0, 0), (10, 0)) + sheet
    m = models(tmp_path, sch(old), sch(old + wire("new", (20, 0), (30, 0))))
    assert query_connections(m[2])["connection_status"] == "partial"
    result = apply(m, [{"op": "create"}], [component(fresh("new"))], {"new": alias("wire", "new")})
    assert result["ok"], result
    m = models(tmp_path, sch(old), sch(old + label("new", "VPOWER", (30, 0), "global_label")))
    result = apply(
        m,
        [{"op": "create"}],
        [component(fresh("new"), names=[{"kind": "global_label", "name": "VPOWER"}])],
        {"new": alias("global_label", "new")},
    )
    assert "unproved_affected_component" in kinds(result)


def test_hidden_power_and_repeated_unit_pin_numbers_keep_complete_occurrences(tmp_path):
    lib = (
        '(lib_symbols (symbol "T:X" '
        '(symbol "X_0_1" (pin power_in line (at 0 0 0) (length 1) '
        '(hide yes) (name "VDD") (number "8"))) '
        '(symbol "X_1_1" (pin passive line (at -2 0 0) (length 1) '
        '(name "A") (number "7"))) '
        '(symbol "X_2_1" (pin passive line (at -2 0 0) (length 1) '
        '(name "B") (number "7")))))'
    )
    text = sch(
        unit("a", (10, 10), angle=90, mirror="(mirror x)") + unit("b", (30, 30), unit=2, angle=180),
        library=lib,
    )
    m = models(tmp_path, text, text)
    rows = query_connections(m[2])["items"]
    power = next(row for row in rows if row["component"]["explicit_names"])
    assert power["component"]["explicit_names"] == [{"kind": "power", "name": "VDD"}]
    assert len(power["occurrences"]) == 2
    pins = [p for row in rows for p in row["occurrences"] if p["kind"] == "pin"]
    assert len(pins) == 4
    assert len([p for p in pins if p["pin_number"] == "7"]) == 2
    assert next(p for p in pins if p["pin_number"] == "7" and p["unit"] == 1)["coordinate_nm"] == [
        10_000_000,
        8_000_000,
    ]
