"""Public hierarchy schema has real required fields, not an open extension pass."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from kicad_cli.fileformat.circuit import Design
from kicad_cli.sch_construct import Construction
from kicad_cli.sch_edit_contract import problems
from kicad_cli.sch_objects import Catalog


def populated_sheet_query(tmp_path):
    """Prepare actual pin/label/wire content with prospective public batch refs."""
    root = tmp_path / "source.kicad_sch"
    root.write_bytes(
        b'(kicad_sch (version 20250114) (generator "test") '
        b'(uuid "root") (paper "A4") (lib_symbols))\n'
    )
    initial = Catalog(Design(root))
    page = {"new": "child", "parent": "/root"}
    q = {
        "op": "circuit",
        "snapshot": {"root": initial.root, "snapshot": initial.snapshot},
        "affected_instances": ["/root", page],
        "actions": [
            {
                "op": "create_sheet",
                "id": "child",
                "page": "/root",
                "file": "module.kicad_sch",
                "name": "Module",
                "at_mm": [40, 30],
                "size_mm": [25.4, 20.32],
                "pins": [{"name": "IN", "shape": "input", "edge": "left", "offset_mm": 10.16}],
            },
            {
                "op": "create",
                "id": "port",
                "kind": "hierarchical_label",
                "page": page,
                "text": "IN",
                "shape": "input",
                "at": [10.16, 20.32],
                "angle_deg": 0,
            },
            {
                "op": "create",
                "id": "child_wire",
                "kind": "wire",
                "page": page,
                "points": [
                    {"object": {"new": "port"}, "instance_path": page, "endpoint": 0},
                    [20.32, 20.32],
                ],
            },
            {
                "op": "create",
                "id": "parent_wire",
                "kind": "wire",
                "page": "/root",
                "points": [
                    {
                        "sheet_pin": {
                            "new": "child",
                            "instance_path": "/root",
                            "sheet_pin_ordinal": 0,
                        }
                    },
                    [20.32, 40.16],
                ],
            },
        ],
        "expected": {
            "components": [
                {
                    "members": [
                        {"new": "child", "instance_path": "/root", "sheet_pin_ordinal": 0},
                        {"new": "port", "instance_path": page},
                        {"new": "child_wire", "instance_path": page},
                        {"new": "parent_wire", "instance_path": "/root"},
                    ],
                    "explicit_names": [
                        {"kind": kind, "name": "/Module/IN"}
                        for kind in ["sheet_pin", "hierarchical_label"]
                    ],
                    "nc_members": [],
                }
            ]
        },
    }
    assert not problems([q])
    report, sources = Construction(root, q).build()
    assert report["verified"]["expected_connections"], report["circuit"]["guard"]
    return Catalog(Design(root, sources)).query()


@pytest.fixture
def populated_object_schema_case(tmp_path):
    declaration = json.loads(
        (Path(__file__).parents[1] / "kicad_cli/driver_contract.json").read_bytes()
    )
    named = declaration["reference"]["json_schemas"]["urn:kicad-cli:sch-object:1"]
    embedded = declaration["reference"]["schemas"]["sch_object_page"]["item_schema"]
    assert named == embedded
    query = populated_sheet_query(tmp_path)
    sheet = next(row for row in query["items"] if row["kind"] == "sheet")
    assert len(sheet["instances"]) == 1
    assert len(sheet["instances"][0]["sheet_pins"]) == 1
    validator = Draft202012Validator(named)
    # A rejection test is meaningful only after the exact unmodified producer
    # object passes; an unrelated unknown-field error must not satisfy it.
    validator.validate(sheet)
    return sheet, validator


def test_populated_sheet_occurrence_matches_closed_public_object_schema(
    populated_object_schema_case,
):
    sheet, validator = populated_object_schema_case
    validator.validate(sheet)
    pin = sheet["instances"][0]["sheet_pins"][0]
    assert set(pin) == {
        "sheet_pin_ordinal",
        "uuid",
        "name",
        "direction",
        "coordinate_nm",
        "coordinate_mm",
        "net",
        "connection_id",
    }
    assert pin["sheet_pin_ordinal"] == 0 and pin["direction"] == "input"
    assert pin["coordinate_nm"] == [40_000_000, 40_160_000]
    assert pin["coordinate_mm"] == [40.0, 40.16]
    assert pin["net"] == "/Module/IN" and pin["connection_id"].startswith("schconn:")


@pytest.mark.parametrize(
    "field",
    [
        "sheet_pin_ordinal",
        "uuid",
        "name",
        "direction",
        "coordinate_nm",
        "coordinate_mm",
        "net",
        "connection_id",
    ],
)
def test_populated_sheet_pin_requires_every_actual_field(populated_object_schema_case, field):
    sheet, validator = populated_object_schema_case
    missing = deepcopy(sheet)
    del missing["instances"][0]["sheet_pins"][0][field]
    with pytest.raises(ValidationError):
        validator.validate(missing)


@pytest.mark.parametrize(
    "field,value",
    [
        ("sheet_pin_ordinal", "0"),
        ("uuid", 12),
        ("name", False),
        ("direction", 42),
        ("coordinate_nm", [0.5, 0]),
        ("coordinate_mm", ["x", 0]),
        ("net", 17),
        ("connection_id", None),
    ],
)
def test_populated_sheet_pin_rejects_wrong_field_types(populated_object_schema_case, field, value):
    sheet, validator = populated_object_schema_case
    wrong = deepcopy(sheet)
    wrong["instances"][0]["sheet_pins"][0][field] = value
    with pytest.raises(ValidationError):
        validator.validate(wrong)


@pytest.mark.parametrize("level", ["occurrence", "pin", "pins_array"])
def test_populated_sheet_pin_does_not_open_other_output_fields(populated_object_schema_case, level):
    sheet, validator = populated_object_schema_case
    extra = deepcopy(sheet)
    row = extra["instances"][0]
    if level == "occurrence":
        row["invented_instance_property"] = True
    elif level == "pin":
        row["sheet_pins"][0]["invented_pin_property"] = True
    else:
        row["sheet_pins"] = {"not": "an array"}
    with pytest.raises(ValidationError):
        validator.validate(extra)


def test_actual_nested_query_matches_required_public_hierarchy_schema(tmp_path):
    root = tmp_path / "source.kicad_sch"
    root.write_text(
        '(kicad_sch(version 20250114)(generator test)(uuid "root")(paper "A4")(lib_symbols))'
    )
    initial = Catalog(Design(root))
    page = {"new": "child", "parent": "/root"}
    q = {
        "op": "circuit",
        "snapshot": {"root": initial.root, "snapshot": initial.snapshot},
        "affected_instances": ["/root", page],
        "expected": {"components": []},
        "actions": [
            {
                "op": "create_sheet",
                "id": "child",
                "page": "/root",
                "file": "module.kicad_sch",
                "name": "Module",
                "at_mm": [50, 40],
                "size_mm": [25.4, 25.4],
                "pins": [],
            }
        ],
    }
    assert not problems([q])
    _, sources = Construction(root, q).build()
    hierarchy = Catalog(Design(root, sources)).hierarchy
    declaration = json.loads(
        (Path(__file__).parents[1] / "kicad_cli/driver_contract.json").read_bytes()
    )
    schema = declaration["reference"]["json_schemas"]["urn:kicad-cli:sch-hierarchy:1"]
    validator = Draft202012Validator(schema)
    validator.validate(hierarchy)
    missing = deepcopy(hierarchy)
    del missing["instances"][0]["source_relative"]
    with pytest.raises(ValidationError):
        validator.validate(missing)
    extra = deepcopy(hierarchy)
    extra["instances"][0]["silently_invented"] = True
    with pytest.raises(ValidationError):
        validator.validate(extra)


def test_bad_recursive_instance_and_pin_creation_have_field_diagnostics():
    base = {
        "op": "circuit",
        "snapshot": {"root": "file:///r", "snapshot": "f" * 64},
        "affected_instances": [{"new": "child"}],
        "expected": {"components": []},
        "actions": [
            {
                "op": "add_sheet_pin",
                "id": "pin",
                "target": {},
                "pin": {"name": "IN", "shape": "not-a-type", "edge": "middle", "offset_mm": 2},
            }
        ],
    }
    found = problems([base])
    assert any("affected_instances" in p["path"] for p in found)
    assert any("shape" in p["path"] for p in found)
