"""Public RC discovery/edit hints; source and final-EXE invocations bind separately."""

import hashlib
import json
import shutil

from test_schematic_input_discovery import (  # noqa: F401 - public fixture
    REPO,
    artifact_binding,
    call,
)


def test_edit_file_schema_has_value_example_and_explicit_qualification(tmp_path):
    code, result = call(tmp_path, "reference", "--command", "sch edit")
    assert code == 0 and result["ok"]
    data = result["data"]
    uri = data["command"]["driver"]["request_schema"]
    schema = data["json_schemas"][uri]
    body = schema["$defs"]["changes_file"]
    from jsonschema import Draft202012Validator

    Draft202012Validator.check_schema(body)
    example = body["examples"][0]
    Draft202012Validator(body).validate(example)
    assert example["changes"] == [{"op": "set", "ref": "C2", "value": "220nF"}]
    assert set(body["$defs"]) == {"set", "add", "remove", "connect", "disconnect", "rename"}
    assert all(op["additionalProperties"] is False for op in body["$defs"].values())
    assert "set" in schema["operation_syntax"] and "rename" in schema["operation_syntax"]


def test_current_tree_has_no_stale_legacy_writer_selection_advice(tmp_path):
    code, result = call(tmp_path, "reference")
    assert code == 0 and result["ok"]
    commands = {c["path"]: c for c in result["data"]["commands"]}
    assert len(commands) == 33
    assert "other writers remain explicitly legacy" not in json.dumps(commands)
    rewidth = {p["name"]: p for p in commands["board route rewidth"]["params"]}
    assert "Legacy profile" not in rewidth["classes"]["description"]
    assert "mm" in rewidth["neck"]["description"]
    assert "all board nets" not in rewidth["nets"]["description"]
    assert "omitting both is E_USAGE" in rewidth["nets"]["description"]
    assert "omitted selection is refused" in rewidth["classes"]["description"]
    silk = {p["name"]: p for p in commands["board silkscreen place"]["params"]}
    assert "mm" in silk["clearance"]["description"] and "block" in silk["clearance"]["description"]


def edit_case(tmp_path):
    project = tmp_path / "owned"
    shutil.copytree(REPO / "tests/fixtures/sch_edit", project)
    return project / "edit.kicad_sch"


def test_value_edit_preview_warns_about_retained_text_positions_and_accepts_utf8_bom(tmp_path):
    schematic = edit_case(tmp_path)
    before = hashlib.sha256(schematic.read_bytes()).hexdigest()
    changes = tmp_path / "changes.json"
    changes.write_text(
        json.dumps({"changes": [{"op": "set", "ref": "R1", "value": "12k"}]}), encoding="utf-8-sig"
    )
    code, result = call(
        tmp_path,
        "sch",
        "edit",
        "--schematic",
        str(schematic),
        "--changes",
        str(changes),
        "--dry-run",
    )
    assert code == 0 and result["ok"], result
    feedback = result["data"]["preview"]["extensions"]["engineering_feedback"]
    assert feedback["geometry_check"] == "partial_conservative_schematic_bounds"
    fields = feedback["affected_edits"][0]["field_layout"]
    assert fields and all(f["before"]["at_mm"] == f["after"]["at_mm"] for f in fields)
    assert "longer value" in feedback["requires_visual_review"][0]
    assert feedback["unsupported"]
    assert hashlib.sha256(schematic.read_bytes()).hexdigest() == before


def test_refused_edit_names_changes_schema_and_keeps_source(tmp_path):
    schematic = edit_case(tmp_path)
    before = schematic.read_bytes()
    changes = tmp_path / "changes.json"
    changes.write_text(
        json.dumps({"changes": [{"op": "set", "ref": "R1", "position": [3, 4]}]}), encoding="utf-8"
    )
    code, result = call(
        tmp_path,
        "sch",
        "edit",
        "--schematic",
        str(schematic),
        "--changes",
        str(changes),
        "--dry-run",
    )
    assert code == 2 and result["error"]["code"] == "E_VALIDATION"
    detail = result["error"]["details"]
    assert detail["parameter"] == "--changes"
    assert detail["schema_ref"] == "urn:kicad-cli:sch-edit-request:1#/$defs/changes_file"
    assert "sch edit" in detail["next_action"]
    assert detail["dispatch_state"] == "not_started" and schematic.read_bytes() == before
