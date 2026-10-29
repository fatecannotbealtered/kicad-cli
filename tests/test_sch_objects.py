"""Source-only object identity, hierarchy expansion and connection observations."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from kicad_cli import read_inputs, sch_objects
from kicad_cli.fileformat.circuit import Design
from kicad_cli.sch_objects import Catalog, CatalogError


def schematic(body="", *, root="root", library=""):
    return f'(kicad_sch (version 20260101) (uuid "{root}") {library} {body})\n'


def pin(number, *, unit=1):
    return (
        f'(symbol "D_{unit}_1" (pin passive line (at 0 0 0) (length 1) '
        f'(name "P{number}") (number "{number}")))'
    )


def library(*, duplicates=False):
    common = pin("8", unit=0)
    own = pin("1")
    return f'(lib_symbols (symbol "T:D" {common} {own} {own if duplicates else pin("2", unit=2)}))'


def symbol(uuid="symbol", *, ref="U1", unit=1, angle=0, instances="", lib="T:D"):
    return (
        f'(symbol (lib_id "{lib}") (at 10 10 {angle}) (unit {unit}) (uuid "{uuid}") '
        f'(property "Reference" "{ref}" (at 11 11 0) (effects (font (size 1 1)))) '
        f'(property "Value" "D" (at 12 12 90) (effects (font (size 1 1)) (hide yes))) '
        f"{instances})"
    )


def sheet(uuid, name, file="child.kicad_sch"):
    return (
        f'(sheet (at 30 30) (size 10 10) (uuid "{uuid}") '
        f'(property "Sheetname" "{name}") (property "Sheetfile" "{file}"))'
    )


def write(tmp_path, text, *, name="root.kicad_sch"):
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8"))
    return path


def hierarchy(tmp_path, *, bad_child=False):
    root = write(tmp_path, schematic(sheet("a", "left") + sheet("b", "right")))
    instances = (
        '(instances (project "root" '
        '(path "/root/a" (reference "U11") (unit 1)) '
        '(path "/root/b" (reference "U21") (unit 2))))'
    )
    write(
        tmp_path,
        schematic(
            symbol(instances=instances, lib="absent" if bad_child else "T:D"),
            root="child",
            library=library(),
        ),
        name="child.kicad_sch",
    )
    return root


def test_shared_source_object_expands_reference_and_unit_per_instance(tmp_path):
    catalog = Catalog(Design(hierarchy(tmp_path)))
    result = catalog.query({"kind": "symbol", "ref": "U21"})
    assert result["total"] == 1
    row = result["items"][0]
    assert row["source_relative"] == "child.kicad_sch"
    assert row["source_unit"] == 1
    assert [(i["instance_path"], i["ref"], i["unit"]) for i in row["instances"]] == [
        ("/root/a", "U11", 1),
        ("/root/b", "U21", 2),
    ]
    assert row["matching_instances"] == ["/root/b"]
    assert {p["number"] for p in row["instances"][0]["pins"]} == {"8", "1"}
    assert {p["number"] for p in row["instances"][1]["pins"]} == {"8", "2"}
    assert catalog.resolve(row["selector"]).node.head == "symbol"
    assert len(catalog.resolve(row["selector"]).instances) == 2


@pytest.mark.parametrize("coordinate,editable", [(10000, True), (10000.0001, False)])
def test_query_discloses_native_domain_edit_boundary(tmp_path, coordinate, editable):
    root = write(tmp_path, schematic(f"(junction (at {coordinate} 20) (uuid dot))"))
    row = Catalog(Design(root)).query(kind="junction")["items"][0]
    assert row["editable"] is editable
    if not editable:
        assert "absolute10000mm edit domain" in str(row["uneditable_reasons"])


def test_one_polyline_object_retains_all_points_and_junction_connections(tmp_path):
    root = write(
        tmp_path,
        schematic(
            '(wire (pts (xy 0 0) (xy 1 0) (xy 2 0)) (uuid "wire")) '
            '(junction (at 1 0) (diameter 0) (color 0 0 0 0) (uuid "dot")) '
            '(label "NET" (at 0 0 0) (uuid "local")) '
            '(global_label "G" (at 2 0 0) (uuid "global")) '
            '(hierarchical_label "H" (at 0 0 0) (uuid "hier")) '
            '(no_connect (at 2 0) (uuid "nc"))'
        ),
    )
    catalog = Catalog(Design(root))
    wire = catalog.query({"kind": "wire"})["items"][0]
    assert wire["geometry"]["points_nm"] == [[0, 0], [1_000_000, 0], [2_000_000, 0]]
    assert catalog.query({"kind": "wire"})["total"] == 1
    assert wire["nets"] == ["G"]
    for kind in ("junction", "no_connect", "label", "global_label", "hierarchical_label"):
        row = catalog.query({"kind": kind})["items"][0]
        assert row["connection_ids"] == wire["connection_ids"]
        assert row["nets"] == ["G"]
    assert catalog.query({"net": "G"})["total"] == 6


def test_pin_occurrences_preserve_identical_library_pins_and_units(tmp_path):
    root = write(
        tmp_path,
        schematic(symbol("unit-a") + symbol("unit-b", unit=2), library=library(duplicates=True)),
    )
    rows = Catalog(Design(root)).query({"kind": "symbol"})["items"]
    assert len(rows) == 2
    first = rows[0]["instances"][0]["pins"]
    assert [p["number"] for p in first] == ["8", "1", "1"]
    assert len({p["pin_id"] for p in first}) == 3
    assert [p["lib_pin_ordinal"] for p in first] == [0, 1, 2]
    supply = [p for row in rows for p in row["instances"][0]["pins"] if p["number"] == "8"]
    assert len(supply) == 2
    assert len({p["pin_id"] for p in supply}) == 2
    assert supply[0]["connection_id"] == supply[1]["connection_id"]


def test_duplicate_uuids_do_not_ambiguous_source_selectors(tmp_path):
    root = write(
        tmp_path,
        schematic('(junction (at 0 0) (uuid "same")) (junction (at 1 0) (uuid "same"))'),
    )
    catalog = Catalog(Design(root))
    rows = catalog.query({"uuid": "same"})["items"]
    assert len(rows) == 2
    assert len({r["object_id"] for r in rows}) == 2
    assert [catalog.resolve(row["selector"]).node.start for row in rows] == [
        row["source_span"][0] for row in rows
    ]
    with pytest.raises(CatalogError, match="exactly root"):
        catalog.resolve({"uuid": "same"})


def test_selectors_bind_root_project_and_complete_hierarchy_revision(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    root = hierarchy(original)
    project = root.with_suffix(".kicad_pro")
    project.write_text('{"schematic": {}}', encoding="utf-8")
    catalog = Catalog(Design(root))
    selected = catalog.query({"kind": "symbol"})["items"][0]["selector"]
    scratch = tmp_path / "scratch"
    shutil.copytree(original, scratch)
    copied = Catalog(Design(scratch / root.name), identity_root=root)
    assert copied.root == catalog.root
    assert copied.snapshot == catalog.snapshot
    assert copied.resolve(selected).object_id == selected["object_id"]
    with pytest.raises(CatalogError) as rejected:
        Catalog(Design(scratch / root.name)).resolve(selected)
    assert rejected.value.code == "E_CONFLICT"
    project.write_text('{"schematic": {}, "text_variables": {}}', encoding="utf-8")
    with pytest.raises(CatalogError) as stale:
        Catalog(Design(root)).resolve(selected)
    assert stale.value.code == "E_CONFLICT"
    project.write_text('{"schematic": {}}', encoding="utf-8")
    child = original / "child.kicad_sch"
    child.write_bytes(child.read_bytes() + b"\n")
    assert Catalog(Design(root)).snapshot != catalog.snapshot


def test_captured_snapshot_never_falls_back_to_disk(tmp_path, monkeypatch):
    root = hierarchy(tmp_path)
    root.with_suffix(".kicad_pro").write_text("{}", encoding="utf-8")
    inputs = read_inputs.capture(schematic=root)
    catalog = Catalog(inputs.design)
    root.with_suffix(".kicad_pro").write_text('{"changed": true}', encoding="utf-8")

    def forbidden_read(*args, **kwargs):
        raise AssertionError("a captured catalog must not reread input bytes")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    frozen = Catalog(inputs.design)
    assert frozen.snapshot == catalog.snapshot
    assert json.dumps(frozen.query())


def test_fields_are_owned_and_source_spans_are_characters(tmp_path):
    text = "\ufeff" + schematic(symbol(), library=library()).replace("\n", "\r\n")
    text = text.replace('(uuid "root")', '(uuid "root") (title_block (title "中文"))')
    root = write(tmp_path, text)
    row = Catalog(Design(root)).query({"kind": "symbol"})["items"][0]
    start, end = row["source_span"]
    assert text[start:end].startswith('(symbol (lib_id "T:D")')
    assert start < len(text[:start].encode("utf-8"))
    assert row["span_unit"] == "unicode_codepoint"
    assert row["geometry"]["coordinate_nm"] == [10_000_000, 10_000_000]
    assert row["geometry"]["coordinate_mm"] == [10.0, 10.0]
    assert [field["name"] for field in row["fields"]] == ["Reference", "Value"]
    assert all(field["ownership"] == "moves_with_object" for field in row["fields"])
    assert row["fields"][1]["geometry"]["angle_deg"] == 90
    assert root.read_bytes() == text.encode("utf-8")


def test_unsupported_object_does_not_ban_unrelated_source_object(tmp_path):
    root = write(
        tmp_path,
        schematic(
            '(label "odd" (at 0 0 0) (future_geometry (at 9 9))) '
            "(bus (pts (xy 0 0) (xy 2 0))) "
            "(junction (at 4 4))"
        ),
    )
    catalog = Catalog(Design(root))
    assert catalog.query({"kind": "label"})["items"][0]["editable"] is False
    assert catalog.query({"kind": "bus"})["items"][0]["editable"] is False
    assert catalog.query({"kind": "junction"})["items"][0]["editable"] is True


def test_connection_failure_is_scoped_and_missing_library_is_explicit(tmp_path):
    root = hierarchy(tmp_path, bad_child=True)
    data = root.read_text(encoding="utf-8").replace(
        '(uuid "root")', '(uuid "root") (label "SAFE" (at 0 0 0))'
    )
    root.write_text(data, encoding="utf-8")
    catalog = Catalog(Design(root))
    assert catalog.connection_status == "partial"
    safe = catalog.query({"page": "/", "net": "/SAFE"})
    assert safe["total"] == 1
    assert safe["items"][0]["instances"][0]["connection_status"] == "complete"
    bad = catalog.query({"kind": "symbol"})["items"][0]
    assert bad["editable"] is False
    assert all(not row["editable"] for row in bad["instances"])
    assert all("missing embedded" in row["connection_problem"] for row in bad["instances"])
    with pytest.raises(CatalogError, match="complete connections"):
        catalog.query({"net": "/SAFE"})


def test_off_quantum_coordinate_and_nonorthogonal_symbol_are_local_failures(tmp_path):
    root = write(
        tmp_path,
        schematic(
            symbol(angle=45) + "(junction (at 0.00001 0)) (junction (at 20 20))",
            library=library(),
        ),
    )
    catalog = Catalog(Design(root))
    rows = catalog.query()["items"]
    assert rows[0]["editable"] is False
    assert "angle is not a multiple of 90 degrees" in rows[0]["uneditable_reasons"]
    assert rows[1]["geometry"]["coordinate_nm"] == [10, 0]
    assert "coordinates are outside the 100 nm schematic quantum" in rows[1]["uneditable_reasons"]
    assert rows[2]["geometry"]["coordinate_mm"] == [20.0, 20.0]
    assert rows[2]["uneditable_reasons"] == ["connections unavailable for instance /root"]


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 501},
        {"limit": 0},
        {"limit": True},
        {"offset": -1},
        {"offset": "0"},
        {"kind": "does_not_exist"},
        {"uuid": ""},
        {"instance": "/a", "instance_path": "/b"},
    ],
)
def test_invalid_query_values_fail_clearly(tmp_path, params):
    catalog = Catalog(Design(write(tmp_path, schematic("(junction (at 0 0))"))))
    with pytest.raises(CatalogError) as error:
        catalog.query(params)
    assert error.value.code == "E_VALIDATION"
    assert error.value.details["parameter"]


def test_pagination_source_order_and_declared_caps(tmp_path, monkeypatch):
    body = " ".join(f'(junction (at {i} 0) (uuid "j{i}"))' for i in range(3))
    root = write(tmp_path, schematic(body))
    catalog = Catalog(Design(root))
    first = catalog.query({"limit": 2})
    assert (first["count"], first["total"], first["has_more"], first["next_offset"]) == (
        2,
        3,
        True,
        2,
    )
    assert [row["uuid"] for row in first["items"]] == ["j0", "j1"]
    last = catalog.query({"offset": 2, "limit": 2})
    assert (last["count"], last["has_more"], last["next_offset"]) == (1, False, None)
    assert first["bounds"]["max_objects"] == 50_000
    assert first["bounds"]["max_instance_objects"] == 100_000
    monkeypatch.setattr(sch_objects, "MAX_OBJECTS", 2)
    with pytest.raises(CatalogError, match="bounds exceeded"):
        Catalog(Design(root))
