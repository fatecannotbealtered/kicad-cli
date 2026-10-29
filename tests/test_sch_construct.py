"""Construction primitive geometry tests; no Native or engineering confirmation.

Electrical expectation enforcement has its own guard tests. These tests exercise
the actual object writer and its closed explicit-resource reader in memory.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from kicad_cli import kicad_env
from kicad_cli.fileformat.circuit import Design
from kicad_cli.sch_construct import ConstructError, Construction, _pair
from kicad_cli.sch_geometry import GeometryError
from kicad_cli.sch_objects import Catalog, CatalogError

LIBRARY = """(kicad_symbol_lib (version 20250114) (generator "test")
 (symbol "D" (property "Reference" "U" (at 0 0 0))
  (property "Value" "D" (at 0 0 0))
  (symbol "D_1_1"
   (pin passive line (at -2 0 0) (length 1) (name "L") (number "1"))
   (pin passive line (at 2 0 180) (length 1) (name "R") (number "2"))
   (pin passive line (at 0 2 270) (length 1) (name "DUP") (number "2")))
  (symbol "D_2_1"
   (pin passive line (at 0 -2 90) (length 1) (name "SECOND") (number "3")))))"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("KICAD_CONFIG_HOME", str(tmp_path / "owned-config"))
    monkeypatch.setattr(kicad_env, "find_kicad_root", lambda: None)
    root = tmp_path / "task.kicad_sch"
    root.write_bytes(
        b'(kicad_sch (version 20250114) (generator "test") '
        b'(uuid "root") (paper "A4") (lib_symbols))\r\n'
    )
    (tmp_path / "D.kicad_sym").write_text(LIBRARY, encoding="utf-8")
    (tmp_path / "sym-lib-table").write_text(
        '(sym_lib_table (lib (name "T") (type "KiCad") '
        '(uri "${KIPRJMOD}/D.kicad_sym") (options "") (descr "")))',
        encoding="utf-8",
    )
    return root


def request(root):
    catalog = Catalog(Design(root))
    return {
        "op": "circuit",
        "snapshot": {"root": catalog.root, "snapshot": catalog.snapshot},
        "affected_instances": ["/root"],
        "actions": [],
        "expected": {"components": []},
    }


def place(**extra):
    return {
        "op": "place_symbol",
        "id": "unit",
        "page": "/root",
        "symbol": "T:D",
        "unit": 1,
        "ref": "U73",
        "value": "custom",
        "at_mm": [10.1234, 20.5678],
        "angle_deg": 0,
        **extra,
    }


def prepared(engine, root):
    return Design(root, {p: s.document.dumps() for p, s in engine.documents.items()})


@pytest.mark.parametrize(
    "angle,pins",
    [
        (0, [(8_123_400, 20_567_800), (12_123_400, 20_567_800), (10_123_400, 18_567_800)]),
        (90, [(10_123_400, 22_567_800), (10_123_400, 18_567_800), (8_123_400, 20_567_800)]),
        (180, [(12_123_400, 20_567_800), (8_123_400, 20_567_800), (10_123_400, 22_567_800)]),
        (270, [(10_123_400, 18_567_800), (10_123_400, 22_567_800), (12_123_400, 20_567_800)]),
    ],
)
def test_explicit_unit_geometry_and_duplicate_pin_identity(project, angle, pins):
    before = project.read_bytes()
    engine = Construction(project, request(project))
    engine.place(place(angle_deg=angle), 0)
    design = prepared(engine, project)
    symbol = design.instances[0].schematic.symbols[0]
    assert symbol.position == (10_123_400, 20_567_800)
    assert symbol.angle == angle and symbol.unit == 1
    assert [p.position for p in design.placed_pins(design.instances[0])] == pins
    assert len({p.find("uuid").value(1) for p in symbol.node.find_all("pin")}) == 3
    assert len(symbol.node.find_all("pin")) == 3
    assert not symbol.node.find("mirror")
    assert project.read_bytes() == before


def test_batch_pin_reference_and_two_segment_wire_are_exact(project):
    before = project.read_bytes()
    engine = Construction(project, request(project))
    engine.place(place(at_mm=[10, 20]), 0)
    engine.scalar(
        {
            "op": "create",
            "id": "lead",
            "kind": "wire",
            "page": "/root",
            "points": [
                {"pin": {"new": "unit", "instance_path": "/root", "pin_ordinal": 1}},
                [15, 20],
                [15, 25],
            ],
        },
        1,
    )
    assert len(engine.aliases["lead"]["uuids"]) == 2
    wires = prepared(engine, project).instances[0].schematic.wires
    assert [(w.start, w.end) for w in wires] == [
        ((12_000_000, 20_000_000), (15_000_000, 20_000_000)),
        ((15_000_000, 20_000_000), (15_000_000, 25_000_000)),
    ]
    assert project.read_bytes() == before


def test_multiple_units_use_same_reference_but_not_all_units_implicitly(project):
    engine = Construction(project, request(project))
    engine.place(place(), 0)
    assert len(prepared(engine, project).instances[0].schematic.symbols) == 1
    engine.place(place(id="other", unit=2, at_mm=[40, 20]), 1)
    symbols = prepared(engine, project).instances[0].schematic.symbols
    assert [(p.properties["Reference"], p.unit) for p in symbols] == [("U73", 1), ("U73", 2)]
    with pytest.raises(ConstructError, match="already placed"):
        engine.place(place(id="duplicate"), 2)


def test_units_cannot_disagree_on_value(project):
    engine = Construction(project, request(project))
    engine.place(place(), 0)
    with pytest.raises(ConstructError, match="same library and value"):
        engine.place(place(id="other", unit=2, value="not custom"), 1)


def test_fields_have_explicit_positions_and_do_not_invoke_layout(project):
    engine = Construction(project, request(project))
    engine.place(place(fields={"Reference": {"at_mm": [11.0001, 12.0002], "angle_deg": 90}}), 0)
    fields = prepared(engine, project).instances[0].schematic.symbols[0].node.find_all("property")
    reference = next(p for p in fields if p.value(1) == "Reference")
    assert reference.find("at").values() == ["11.0001", "12.0002", "90"]
    assert reference.find("effects").find("font").find("size").values() == ["1.27", "1.27"]
    assert reference.find("hide") is None


@pytest.mark.parametrize(
    "kind", ["label", "global_label", "hierarchical_label", "junction", "no_connect"]
)
def test_scalar_creation_gets_unique_publicly_queryable_identity(project, kind):
    engine = Construction(project, request(project))
    action = {"op": "create", "id": "mark", "page": "/root", "kind": kind, "at": [14.0001, 24.0002]}
    if "label" in kind:
        action.update(text="CONTROL", angle_deg=90)
    engine.scalar(action, 0)
    catalog = Catalog(prepared(engine, project))
    row = catalog.query(kind=kind)["items"][0]
    assert row["uuid"] == engine.aliases["mark"]["uuids"][0]
    assert row["geometry"]["coordinate_nm"] == [14_000_100, 24_000_200]


@pytest.mark.parametrize(
    "points",
    [
        [[10, 10], [10, 10]],
        [[10, 10], [20, 10], [10, 10]],
        [[10, 10], [20, 10], [15, 10]],
    ],
)
def test_zero_duplicate_and_partial_overlap_segments_refused(project, points):
    before = project.read_bytes()
    engine = Construction(project, request(project))
    with pytest.raises(ConstructError):
        engine.scalar(
            {"op": "create", "id": "w", "kind": "wire", "page": "/root", "points": points}, 0
        )
    assert project.read_bytes() == before


def test_existing_collinear_overlap_is_not_silently_created(project):
    engine = Construction(project, request(project))
    engine.scalar(
        {
            "op": "create",
            "id": "w",
            "kind": "wire",
            "page": "/root",
            "points": [[10, 10], [20, 10]],
        },
        0,
    )
    with pytest.raises(ConstructError, match="overlap"):
        engine.scalar(
            {
                "op": "create",
                "id": "overlap",
                "kind": "wire",
                "page": "/root",
                "points": [[15, 10], [25, 10]],
            },
            1,
        )


def test_forward_and_wrong_unit_pin_references_refuse_without_disk_write(project):
    before = project.read_bytes()
    engine = Construction(project, request(project))
    bad = {
        "op": "create",
        "id": "w",
        "kind": "wire",
        "page": "/root",
        "points": [{"pin": {"new": "unit", "instance_path": "/root", "pin_ordinal": 0}}, [30, 20]],
    }
    with pytest.raises(ConstructError, match="earlier"):
        engine.scalar(bad, 0)
    engine.place(place(), 1)
    bad["points"][0]["pin"]["pin_ordinal"] = 3
    with pytest.raises(ConstructError, match="another unit"):
        engine.scalar(bad, 2)
    assert project.read_bytes() == before


def test_stale_snapshot_does_not_prepare_geometry(project):
    original = request(project)
    project.write_bytes(project.read_bytes() + b"\n")
    with pytest.raises(CatalogError, match="stale"):
        Construction(project, original)


def test_mm_quantum_does_not_quantize_long_source_decimal():
    assert _pair(["1.2345", 10], "/at") == (1_234_500, 10_000_000)
    with pytest.raises(GeometryError, match="grid"):
        _pair(["1.23450000000000000000001", 10], "/at")


def test_update_first_segment_keeps_uuid_and_properties_delete_keeps_attached_wire(project):
    # The source is saved only by the test setup, not by a product writer.
    engine = Construction(project, request(project))
    engine.place(place(), 0)
    engine.scalar(
        {
            "op": "create",
            "id": "w",
            "kind": "wire",
            "page": "/root",
            "points": [[12.1234, 20.5678], [20, 20.5678]],
        },
        1,
    )
    project.write_bytes(engine.documents[project.resolve()].document.dumps().encode())
    original = Catalog(Design(project))
    wire = original.query(kind="wire")["items"][0]
    placed = original.query(kind="symbol")["items"][0]
    next_engine = Construction(project, request(project))
    next_engine.scalar(
        {
            "op": "update",
            "id": "changed",
            "target": wire["selector"],
            "points": [[12.1234, 20.5678], [20, 20.5678], [20, 25]],
        },
        0,
    )
    assert next_engine.aliases["changed"]["uuids"][0] == wire["uuid"]
    next_engine.delete({"op": "delete", "target": placed["selector"]}, 1)
    new = prepared(next_engine, project).instances[0].schematic
    assert len(new.wires) == 2 and not new.symbols
    assert not next_engine.report[-1]["attached_objects_implicitly_deleted"]


def test_circuit_snapshot_can_never_be_reused_for_another_root(project):
    other = project.with_name("other.kicad_sch")
    other.write_bytes(project.read_bytes())
    with pytest.raises(CatalogError):
        Construction(other, deepcopy(request(project)))


def test_complete_memory_batch_uses_real_electrical_guard(project):
    def new(name, ordinal=None):
        return {
            "new": name,
            "instance_path": "/root",
            **({"pin_ordinal": ordinal} if ordinal is not None else {}),
        }

    actions = [
        place(at_mm=[10, 20]),
        {
            "op": "create",
            "id": "w",
            "kind": "wire",
            "page": "/root",
            "points": [{"pin": new("unit", 1)}, [15, 20]],
        },
        {
            "op": "create",
            "id": "name",
            "kind": "label",
            "page": "/root",
            "text": "CONTROL",
            "at": {"object": {"new": "w"}, "endpoint": 1, "instance_path": "/root"},
        },
        {
            "op": "create",
            "id": "nc",
            "kind": "no_connect",
            "page": "/root",
            "at": {"pin": new("unit", 2)},
        },
    ]
    declared = [
        {"members": [new("unit", 0)], "explicit_names": [], "nc_members": []},
        {
            "members": [new("unit", 1), new("w"), new("name")],
            "explicit_names": [{"kind": "label", "name": "/CONTROL"}],
            "nc_members": [],
        },
        {
            "members": [new("unit", 2), new("nc")],
            "explicit_names": [],
            "nc_members": [new("unit", 2)],
        },
    ]
    intended = {**request(project), "actions": actions, "expected": {"components": declared}}
    before = project.read_bytes()
    report, sources = Construction(project, intended).build()
    assert all(
        report["verified"][key]
        for key in [
            "circuit_expected",
            "expected_connections",
            "protected_content",
            "geometry",
            "batch_complete",
        ]
    )
    assert len(report["circuit"]["object_changes"]) == 4
    assert report["circuit"]["guard"]["ok"]
    assert sources[project.resolve()]
    assert project.read_bytes() == before


@pytest.mark.parametrize(
    "points",
    [
        [[0, 0], [10, 10], [0, 10], [10, 0]],
        [[0, 0], [10, 0], [10, 10], [0, 0]],
        [[0, 0], [10, 0], [10, 10], [5, 0]],
    ],
)
def test_nonadjacent_polyline_contact_refuses_before_disk_write(project, points):
    before = project.read_bytes()
    intended = request(project)
    intended["actions"] = [
        {"op": "create", "id": "path", "page": "/root", "kind": "wire", "points": points}
    ]
    with pytest.raises(ConstructError, match="Non-adjacent") as error:
        Construction(project, intended).build()
    assert error.value.details["segment_indices"] == [0, 2]
    assert project.read_bytes() == before


@pytest.mark.parametrize("name", ["self", "future"])
def test_contact_target_requires_an_earlier_action(project, name):
    before = project.read_bytes()
    intended = request(project)
    intended["actions"] = [
        {
            "op": "create",
            "id": "self",
            "page": "/root",
            "kind": "wire",
            "points": [[0, 0], [10, 0]],
            "contact_targets": [{"new": name, "instance_path": "/root"}],
        },
        {
            "op": "create",
            "id": "future",
            "page": "/root",
            "kind": "label",
            "text": "N",
            "at": [10, 0],
        },
    ]
    with pytest.raises(ConstructError, match="earlier") as error:
        Construction(project, intended).build()
    assert error.value.details["path"] == "/changes/0/actions/0/contact_targets/0/new"
    assert project.read_bytes() == before


def test_earlier_contact_target_and_final_forward_expectation_are_valid(project):
    def new(name):
        return {"new": name, "instance_path": "/root"}

    intended = request(project)
    intended["actions"] = [
        {"op": "create", "id": "w", "page": "/root", "kind": "wire", "points": [[0, 0], [10, 0]]},
        {
            "op": "create",
            "id": "name",
            "page": "/root",
            "kind": "label",
            "text": "VALID",
            "at": [10, 0],
            "contact_targets": [new("w")],
        },
    ]
    intended["expected"] = {
        "components": [
            {
                "members": [new("w"), new("name")],
                "explicit_names": [{"kind": "label", "name": "/VALID"}],
                "nc_members": [],
            }
        ]
    }
    before = project.read_bytes()
    report, _ = Construction(project, intended).build()
    assert report["circuit"]["guard"]["ok"]
    assert project.read_bytes() == before


@pytest.mark.parametrize("name", ["D[0..999999999999]", "A[999999999999..0]", "USB{DP DM}"])
def test_new_scalar_bus_rejection_does_not_expand_members(project, monkeypatch, name):
    from kicad_cli.fileformat import circuit

    engine = Construction(project, request(project))

    def must_not_expand(*args):
        raise AssertionError("Construction must reject bus syntax before member expansion")

    monkeypatch.setattr(circuit, "bus_members", must_not_expand)
    with pytest.raises(ConstructError, match="scalar name"):
        engine._scalar(name, "/changes/0/actions/0/text")


@pytest.mark.parametrize("name", ["~{RESET}", "+V_{ADJ}", "V^{SENSE}", "{slash}SIG"])
def test_scalar_markup_keeps_literal_name_and_never_expands(project, monkeypatch, name):
    from kicad_cli.fileformat import circuit

    engine = Construction(project, request(project))
    monkeypatch.setattr(
        circuit, "bus_members", lambda *args: pytest.fail("Unexpected bus expansion")
    )
    assert engine._scalar(name, "/text") == name


def test_known_bus_alias_is_not_mistaken_for_new_scalar_label(project):
    project.with_suffix(".kicad_pro").write_text(
        '{"schematic":{"bus_aliases":{"ALIAS":["D0","D1"]}}}', encoding="utf-8"
    )
    engine = Construction(project, request(project))
    with pytest.raises(ConstructError, match="scalar name"):
        engine._scalar("ALIAS", "/text")


def reused_units(project):
    from kicad_cli.sch_symbols import Resources

    resolved, _ = Resources(project).resolve("T:D")
    from kicad_cli.fileformat.sexpr import render

    library = render(resolved.node)
    project.write_text(
        '(kicad_sch (version 20250114) (uuid "root") (lib_symbols) '
        '(sheet (at 50 50) (size 10 10) (uuid "a") '
        '(property "Sheetname" "A") (property "Sheetfile" "child.kicad_sch")) '
        '(sheet (at 80 50) (size 10 10) (uuid "b") '
        '(property "Sheetname" "B") (property "Sheetfile" "child.kicad_sch")))',
        encoding="utf-8",
    )
    (project.parent / "child.kicad_sch").write_text(
        '(kicad_sch (version 20250114) (uuid "child") (lib_symbols ' + library + ") "
        '(symbol (lib_id "T:D") (at 10 20 90) (mirror y) (unit 1) (uuid "shared-symbol") '
        '(property "Reference" "U31" (at 10 18 0)) (property "Value" "custom" (at 10 22 0)) '
        '(instances (project "task" (path "/root/a" (reference "U31") (unit 1)) '
        '(path "/root/b" (reference "U41") (unit 2))))))',
        encoding="utf-8",
    )
    intended = request(project)
    intended["affected_instances"] = ["/root", "/root/a", "/root/b"]
    return Construction(project, intended)


def test_existing_pin_reference_uses_instance_unit_and_actual_mirror(project):
    engine = reused_units(project)
    obj = next(o for o in engine.catalog.objects if o.record.get("uuid") == "shared-symbol")
    child = engine.documents[(project.parent / "child.kicad_sch").resolve()]
    point = {
        "pin": {"selector": obj.record["selector"], "instance_path": "/root/b", "pin_ordinal": 3}
    }
    assert engine._point(point, child, "/points/0") == (8_000_000, 20_000_000)
    point["pin"]["instance_path"] = "/root/a"
    with pytest.raises(ConstructError, match="another unit"):
        engine._point(point, child, "/points/0")


def test_duplicate_ref_unit_checks_effective_shared_instance_not_source_unit(project):
    engine = reused_units(project)
    with pytest.raises(ConstructError, match="already placed"):
        engine.place(place(unit=2, ref="U41"), 0)


def test_complementary_unit_is_not_refused_due_to_other_instance_source_unit(project):
    engine = reused_units(project)
    engine.place(place(unit=1, ref="U41"), 0)
    root_symbol = prepared(engine, project).instances[0].schematic.symbols[0]
    assert root_symbol.properties["Reference"] == "U41" and root_symbol.unit == 1
