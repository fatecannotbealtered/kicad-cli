"""Public source runtime integration; no official Native consumer qualification."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from driver_test_utils import grant
from jsonschema import Draft202012Validator

from kicad_cli import driver_runtime, kicad_env, sch_edit_contract
from kicad_cli.fileformat.circuit import Design
from kicad_cli.sch_construct import Construction
from kicad_cli.sch_objects import Catalog

LIB = """(kicad_symbol_lib (version 20250114) (generator "owned-runtime")
 (symbol "Q" (property "Reference" "X" (at 0 0 0))
 (property "Value" "Q" (at 0 0 0))
 (symbol "Q_1_1"
 (pin passive line (at -2.54 0 0) (length 1) (name "A") (number "1"))
 (pin passive line (at 2.54 0 180) (length 1) (name "B") (number "2")))))"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("KICAD_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(kicad_env, "find_kicad_root", lambda: None)
    folder = tmp_path / "project"
    folder.mkdir()
    root = folder / "root.kicad_sch"
    root.write_text(
        '(kicad_sch (version 20250114) (uuid "root") (paper "A4") (lib_symbols))\n',
        encoding="utf-8",
    )
    (folder / "Q.kicad_sym").write_text(LIB, encoding="utf-8")
    (folder / "sym-lib-table").write_text(
        '(sym_lib_table (lib (name "Owned") (type "KiCad") '
        '(uri "${KIPRJMOD}/Q.kicad_sym") (options "") (descr "")))',
        encoding="utf-8",
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("KICAD_CLI_")}
    env.update(grant(tmp_path), PYTHONIOENCODING="utf-8")
    return root, env


def call(project, *args):
    root, env = project
    with driver_runtime.create_cli(environ=env, home=root.parent) as cli:
        result = cli.run([*map(str, args), "--compact"])
        cli.validator.validate("response", result.envelope)
        return result.envelope


def query(project, command="objects", *args):
    return call(project, "sch", command, "--schematic", project[0], *args)


def entry(project):
    data = query(project)["data"]
    return {
        "op": "circuit",
        "snapshot": {"root": data["extensions"]["root"], "snapshot": data["snapshot"]["revision"]},
        "affected_instances": ["/root"],
        "actions": [
            {
                "op": "place_symbol",
                "id": "device",
                "page": "/root",
                "symbol": "Owned:Q",
                "unit": 1,
                "ref": "X42",
                "at_mm": [20, 20],
            }
        ],
        "expected": {
            "components": [
                {
                    "members": [{"new": "device", "instance_path": "/root", "pin_ordinal": i}],
                    "explicit_names": [],
                    "nc_members": [],
                }
                for i in (0, 1)
            ]
        },
    }


def preview(project, request):
    changes = project[0].parent / "changes.json"
    changes.write_text(json.dumps({"changes": [request]}), encoding="utf-8")
    return call(
        project, "sch", "edit", "--schematic", project[0], "--changes", changes, "--dry-run"
    ), changes


def test_empty_page_query_discloses_exact_creation_target(project):
    data = query(project)["data"]
    assert data["items"] == []
    assert data["extensions"]["pages"] == [
        {
            "instance_path": "/root",
            "sheet": "/",
            "page": "1",
            "source_relative": "root.kicad_sch",
            "source_file": str(project[0]),
        }
    ]
    connected = query(project, "connections")
    assert connected["ok"], connected
    assert connected["data"]["items"] == []
    assert connected["data"]["extensions"]["pages"] == data["extensions"]["pages"]


@pytest.mark.parametrize("boundary", ["self_crossing", "self_reference", "future_reference"])
def test_public_polyline_and_contact_order_refuse_before_engineering_write(project, boundary):
    request = entry(project)
    request["actions"] = [
        {
            "op": "create",
            "id": "path",
            "page": "/root",
            "kind": "wire",
            "points": [[0, 0], [10, 10], [0, 10], [10, 0]],
        }
    ]
    if boundary != "self_crossing":
        request["actions"][0]["points"] = [[0, 0], [10, 0]]
        request["actions"][0]["contact_targets"] = [
            {"new": "path" if boundary == "self_reference" else "later", "instance_path": "/root"}
        ]
        request["actions"].append(
            {
                "op": "create",
                "id": "later",
                "page": "/root",
                "kind": "label",
                "text": "LATER",
                "at": [10, 0],
            }
        )
    before = project[0].read_bytes()
    result, _ = preview(project, request)
    assert not result["ok"] and result["error"]["code"] == "E_VALIDATION", result
    assert project[0].read_bytes() == before
    problems = result["error"]["details"]["problems"]
    assert any("/actions/0/" in row["path"] for row in problems), result


def test_public_library_query_geometry_and_schema_are_exact(project):
    before = project[0].read_bytes()
    discovered = query(project, "symbols")
    assert discovered["ok"], discovered
    libraries = discovered["data"]
    assert libraries["items"][0]["library_id"] == "Owned"
    identities = query(project, "symbols", "--library", "Owned")["data"]
    assert identities["items"][0]["symbol_id"] == "Owned:Q"
    geometry = query(project, "symbols", "--symbol", "Owned:Q")
    assert geometry["ok"], geometry
    item = geometry["data"]["items"][0]
    assert item["detail_level"] == "pin_geometry"
    assert [p["pin_ordinal"] for p in item["pins"]] == [0, 1]
    assert item["pins"][0]["position_nm"] == [-2_540_000, 0]
    assert item["pins"][0]["coordinate_frame"] == "library_y_up"
    contract = json.loads(driver_runtime.PACKAGE.joinpath("driver_contract.json").read_bytes())[
        "reference"
    ]
    item_schema = contract["schemas"]["sch_symbol_page"]["item_schema"]
    for page in [libraries, identities, geometry["data"]]:
        for row in page["items"]:
            Draft202012Validator(item_schema).validate(row)
            assert set(row) == set(item_schema["required"]) | (
                {"library_type"} if row["kind"] == "library" else set()
            )
        extension_schema = contract["json_schemas"]["urn:kicad-cli:sch-symbol-page:1"]["allOf"][1][
            "properties"
        ]["extensions"]
        Draft202012Validator(extension_schema).validate(page["extensions"])
    assert project[0].read_bytes() == before


@pytest.mark.parametrize("bad", [[], {}, None, "1", 0, True])
def test_unit_malformed_shape_is_rejected_before_trial(project, bad):
    request = entry(project)
    request["actions"][0]["unit"] = bad
    before = project[0].read_bytes()
    result, _ = preview(project, request)
    assert not result["ok"] and result["error"]["code"] == "E_VALIDATION", result
    assert any(p["path"].endswith("/unit") for p in result["error"]["details"]["problems"])
    assert project[0].read_bytes() == before
    assert not (Path(project[1]["KICAD_CLI_STATE_DIR"]) / "engineering-trials").exists()


@pytest.mark.parametrize(
    "parameter,value",
    [
        ("limit", "0"),
        ("limit", "201"),
        ("offset", "-1"),
        ("symbol", "invalid"),
        ("symbol", "../../bad:Q"),
    ],
)
def test_resource_query_boundaries_return_structured_refusal(project, parameter, value):
    before = project[0].read_bytes()
    result = query(project, "symbols", "--" + parameter, value)
    assert not result["ok"] and result["error"]["code"] == "E_VALIDATION", result
    assert result["error"]["details"]["dispatch_state"] == "not_started"
    assert project[0].read_bytes() == before


def test_circuit_snapshot_stale_refuses_before_trial(project):
    request = entry(project)
    project[0].write_bytes(project[0].read_bytes() + b"\n")
    before = project[0].read_bytes()
    result, _ = preview(project, request)
    assert not result["ok"] and result["error"]["code"] == "E_CONFLICT", result
    assert project[0].read_bytes() == before
    assert not (Path(project[1]["KICAD_CLI_STATE_DIR"]) / "engineering-trials").exists()


@pytest.mark.parametrize("key", ["at_mm", "points"])
def test_nonfinite_circuit_numbers_and_mixed_batch_refuse(project, key):
    request = entry(project)
    request["actions"][0][key] = [float("nan"), 0]
    problems = sch_edit_contract.problems({"changes": [request]})
    assert any("finite" in p["problem"] for p in problems)
    assert sch_edit_contract.problems(
        {"changes": [request, {"op": "set", "ref": "X42", "value": "bad"}]}
    )


def test_public_source_confirm_saved_requery_and_idempotent_receipt(project):
    before = project[0].read_bytes()
    plan, changes = preview(project, entry(project))
    assert plan["ok"], plan
    assert project[0].read_bytes() == before
    token = plan["data"]["confirm_token"]
    saved = call(
        project,
        "sch",
        "edit",
        "--schematic",
        project[0],
        "--changes",
        changes,
        "--confirm",
        token,
        "--idempotency-key",
        "owned-runtime-save",
    )
    assert saved["ok"], saved
    actual = project[0].read_bytes()
    fresh = query(project, "objects", "--ref", "X42")
    assert fresh["ok"] and fresh["data"]["count"] == 1, fresh
    pins = fresh["data"]["items"][0]["instances"][0]["pins"]
    assert [p["coordinate_nm"] for p in pins] == [
        [17_460_000, 20_000_000],
        [22_540_000, 20_000_000],
    ]
    groups = query(project, "connections")
    assert groups["ok"] and groups["data"]["count"] == 2, groups
    ids = [row["connection_id"] for row in groups["data"]["items"]]
    assert ids == sorted(ids)
    schema = json.loads(driver_runtime.PACKAGE.joinpath("driver_contract.json").read_bytes())[
        "reference"
    ]["schemas"]["sch_connection_page"]["item_schema"]
    for row in groups["data"]["items"]:
        Draft202012Validator(schema).validate(row)
        assert set(row) == set(schema["required"])
    replay = call(
        project,
        "sch",
        "edit",
        "--schematic",
        project[0],
        "--changes",
        changes,
        "--confirm",
        token,
        "--idempotency-key",
        "owned-runtime-save",
    )
    assert replay["ok"], replay
    assert project[0].read_bytes() == actual


def test_legal_existing_child_outside_root_folder_uses_public_page(project):
    root = project[0]
    child = root.parent.parent / "shared.kicad_sch"
    child.write_text(
        '(kicad_sch (version 20250114) (uuid "child") (lib_symbols))', encoding="utf-8"
    )
    root.write_text(
        '(kicad_sch (version 20250114) (uuid "root") (lib_symbols) '
        '(sheet (at 0 0) (size 10 10) (uuid "branch") (property "Sheetname" "Shared") '
        '(property "Sheetfile" "../shared.kicad_sch")))',
        encoding="utf-8",
    )
    catalog = Catalog(Design(root))
    request = {
        "op": "circuit",
        "snapshot": {"root": catalog.root, "snapshot": catalog.snapshot},
        "affected_instances": ["/root/branch"],
        "actions": [
            {
                "op": "create",
                "id": "lead",
                "page": "/root/branch",
                "kind": "wire",
                "points": [[40, 40], [45, 40]],
            }
        ],
        "expected": {
            "components": [
                {
                    "members": [{"new": "lead", "instance_path": "/root/branch"}],
                    "explicit_names": [],
                    "nc_members": [],
                }
            ]
        },
    }
    report, sources = Construction(root, request).build()
    assert report["verified"]["expected_connections"]
    assert set(sources) == {child.resolve()}
    assert report["circuit"]["new_objects"]["lead"]["source_relative"] == "../shared.kicad_sch"


@pytest.mark.parametrize("name", ["Q.kicad_sym", "sym-lib-table", "changes.json"])
def test_same_byte_dependency_replacement_rejects_original_confirmation(project, name):
    planned, changes = preview(project, entry(project))
    assert planned["ok"], planned
    before = project[0].read_bytes()
    target = project[0].parent / name
    previous = target.read_bytes()
    info = target.stat()
    replacement = target.with_name(target.name + ".owned-replacement")
    replacement.write_bytes(previous)
    os.utime(replacement, ns=(info.st_atime_ns, info.st_mtime_ns))
    os.replace(replacement, target)
    refused = call(
        project,
        "sch",
        "edit",
        "--schematic",
        project[0],
        "--changes",
        changes,
        "--confirm",
        planned["data"]["confirm_token"],
    )
    assert not refused["ok"] and refused["error"]["code"] == "E_CONFLICT", refused
    assert refused["error"]["details"]["dispatch_state"] == "not_started"
    assert project[0].read_bytes() == before
    assert target.read_bytes() == previous
