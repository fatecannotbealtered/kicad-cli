"""Existing advanced edit shapes through public source and frozen entry points."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from driver_test_utils import grant

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def edit_scene(tmp_path, record_property):
    project = tmp_path / "project"
    shutil.copytree(REPO / "tests/fixtures/sch_edit", project)
    env = {k: v for k, v in os.environ.items() if not k.startswith("KICAD_CLI_")}
    env.update(grant(tmp_path), PYTHONIOENCODING="utf-8")
    binary = os.environ.get("KICAD_CLI_TEST_BINARY")
    if binary:
        env["KICAD_CLI_DRIVER_SDK_PATH"] = os.environ["KICAD_CLI_ACCEPT_DRIVER_SDK"]
        record_property(
            "fcc_artifact_sha256", hashlib.sha256(Path(binary).read_bytes()).hexdigest()
        )
    prefix = [binary] if binary else [sys.executable, "-B", "-m", "kicad_cli.main"]
    count = 0

    def call(*args):
        nonlocal count
        count += 1
        p = subprocess.run(
            [*prefix, *map(str, args), "--compact"],
            cwd=tmp_path if binary else REPO,
            env=env,
            capture_output=True,
            timeout=90,
        )
        (tmp_path / f"call-{count}.stdout").write_bytes(p.stdout)
        (tmp_path / f"call-{count}.stderr").write_bytes(p.stderr)
        return p.returncode, json.loads(p.stdout)

    changes = tmp_path / "changes.json"

    def preview(value):
        changes.write_text(json.dumps(value), encoding="utf-8")
        return call(
            "sch",
            "edit",
            "--schematic",
            project / "edit.kicad_sch",
            "--changes",
            changes,
            "--dry-run",
        )

    return project, changes, call, preview


def project_bytes(project):
    return {p.name: p.read_bytes() for p in project.glob("*.kicad_*") if p.is_file()}


def test_add_invalid_flag_is_not_silently_ignored(edit_scene):
    project, _, _, preview = edit_scene
    before = project_bytes(project)
    code, result = preview([{"op": "add", "ref": "R30", "symbol": "fixture:R", "dnp": "yes"}])
    assert code == 2 and result["error"]["code"] == "E_VALIDATION", result
    assert project_bytes(project) == before
    assert any(p["path"] == "/0/dnp" for p in result["error"]["details"]["problems"])


OPERATIONS = {
    "add": {"op": "add", "ref": "R30", "symbol": "fixture:R", "near": "R2"},
    "remove": {"op": "remove", "ref": "R4"},
    "connect": {"op": "connect", "ref": "R4", "pin": "1", "net": "/SIG"},
    "disconnect": {"op": "disconnect", "ref": "R1", "pin": "2"},
    "rename": {"op": "rename", "net": "/OUT", "to": "DRIVE"},
}


@pytest.mark.parametrize("op", list(OPERATIONS))
def test_each_existing_operation_saves_after_public_confirmation(edit_scene, op):
    project, changes, call, preview = edit_scene
    before = project_bytes(project)
    code, plan = preview({"changes": [OPERATIONS[op]], "caller_metadata": {"ticket": 123}})
    assert code == 0 and plan["data"]["preview"]["can_apply"], plan
    assert project_bytes(project) == before
    code, done = call(
        "sch",
        "edit",
        "--schematic",
        project / "edit.kicad_sch",
        "--changes",
        changes,
        "--confirm",
        plan["data"]["confirm_token"],
    )
    assert code == 0 and done["ok"], done
    receipt = done["data"]["receipt"]
    assert receipt["outcome"]["verification"]["status"] == "passed"
    assert receipt["extensions"]["task"]["changes"][0]["op"] == op
    assert project_bytes(project) != before
    assert (project / "edit.kicad_pcb").read_bytes() == before["edit.kicad_pcb"]


@pytest.mark.parametrize("op", list(OPERATIONS))
@pytest.mark.parametrize("defect", ["unknown-field", "missing-required"])
def test_public_schema_and_runtime_reject_the_same_bad_operation(edit_scene, op, defect):
    from jsonschema import Draft202012Validator

    project, _, call, preview = edit_scene
    code, ref = call("reference", "--command", "sch edit")
    assert code == 0
    schema = ref["data"]["json_schemas"]["urn:kicad-cli:sch-edit-request:1"]["$defs"][
        "changes_file"
    ]
    Draft202012Validator.check_schema(schema)
    change = dict(OPERATIONS[op])
    if defect == "unknown-field":
        field = "position"
        change[field] = [10, 20]
    else:
        field = "net" if op == "rename" else "ref"
        del change[field]
    value = {"changes": [change], "metadata": ["retained", 7, None]}
    assert list(Draft202012Validator(schema).iter_errors(value))
    before = project_bytes(project)
    code, result = preview(value)
    assert code == 2 and result["error"]["code"] == "E_VALIDATION", result
    details = result["error"]["details"]
    assert any(p["path"] == "/changes/0/" + field for p in details["problems"])
    assert details["schema_ref"].endswith("#/$defs/changes_file")
    assert "reference" in details["next_action"]
    assert details["dispatch_state"] == "not_started"
    assert project_bytes(project) == before


@pytest.mark.parametrize(
    "change",
    [
        {"op": "add", "ref": "R1", "symbol": "fixture:R"},
        {"op": "connect", "ref": "R4", "pin": "99", "net": "/SIG"},
        {"op": "connect", "ref": "R1", "pin": "1", "net": "/SIG"},
        {"op": "disconnect", "ref": "R1", "pin": "99"},
        {"op": "remove", "ref": "NO_SUCH_PART"},
        {"op": "rename", "net": "/OUT", "to": "SIG"},
        {"op": "connect", "ref": "R4", "pin": "1", "net": "/CHILD_A/LOCAL"},
        {"op": "add", "ref": "R30", "symbol": "fixture:R", "sheet": "/CHILD_A/", "near": "R1"},
    ],
    ids=[
        "duplicate-reference",
        "invalid-connect-pin",
        "same-net-already-connected",
        "invalid-disconnect-pin",
        "missing-remove-reference",
        "rename-would-merge",
        "foreign-local-net",
        "near-on-another-sheet",
    ],
)
def test_domain_refusals_preserve_every_primary_and_identify_location(edit_scene, change):
    project, _, _, preview = edit_scene
    before = project_bytes(project)
    code, result = preview({"changes": [change]})
    assert code == 2 and result["error"]["code"] == "E_VALIDATION", result
    details = result["error"]["details"]
    assert details["problems"] and all(
        p["path"].startswith("/changes") for p in details["problems"]
    )
    assert details["dispatch_state"] == "not_started"
    assert project_bytes(project) == before


def test_explicit_reference_on_second_reused_instance_stays_unique(edit_scene):
    _, _, _, preview = edit_scene
    code, result = preview(
        [{"op": "add", "ref": "TP1", "symbol": "fixture:R", "sheet": "/CHILD_B/"}]
    )
    assert code == 0 and result["data"]["preview"]["can_apply"], result
    task = result["data"]["preview"]["extensions"]["trial"]["result"]
    added = task["changes"][0]
    assert added["ref"] == "TP1" and added["also_added"] == ["TP2"]


def test_conflicting_number_and_name_aliases_are_refused_before_writing(edit_scene):
    project, _, _, preview = edit_scene
    before = project_bytes(project)
    code, result = preview(
        {
            "changes": [
                {
                    "op": "add",
                    "ref": "U30",
                    "symbol": "fixture:DUAL",
                    "pins": {"1": "/SIG", "OUT": "/OUT"},
                }
            ]
        }
    )
    assert code == 2 and result["error"]["code"] == "E_VALIDATION", result
    assert any(
        p["path"] == "/changes/0/pins/OUT" and "conflicting networks" in p["problem"]
        for p in result["error"]["details"]["problems"]
    )
    assert project_bytes(project) == before


def test_mult_unit_add_exposes_all_units_and_refuses_a_unit_selector(edit_scene):
    project, _, _, preview = edit_scene
    change = {
        "op": "add",
        "ref": "U30",
        "symbol": "fixture:DUAL",
        "pins": {"1": "/SIG", "7": "/OUT"},
    }
    before = project_bytes(project)
    code, result = preview([change])
    assert code == 0 and result["data"]["preview"]["can_apply"], result
    feedback = result["data"]["preview"]["extensions"]["engineering_feedback"]
    assert {v["unit"] for v in feedback["affected_edits"][0]["placement_mm"]} == {1, 2}
    assert feedback["geometry_findings"] and feedback["requires_visual_review"]
    code, result = preview([dict(change, unit=2)])
    assert code == 2 and any(p["path"] == "/0/unit" for p in result["error"]["details"]["problems"])
    assert project_bytes(project) == before


def test_unnamed_net_rename_previews_a_new_label_and_saves(edit_scene):
    project, changes, call, preview = edit_scene
    before = project_bytes(project)
    code, plan = preview([{"op": "rename", "net": "Net-(R4-Pad2)", "to": "BRIDGE"}])
    assert code == 0 and plan["data"]["preview"]["can_apply"], plan
    feedback = plan["data"]["preview"]["extensions"]["engineering_feedback"]
    assert feedback["affected_edits"][0]["drawn"] == {"label": "BRIDGE", "at_pin": "R4.2"}
    assert feedback["requires_visual_review"] and project_bytes(project) == before
    code, done = call(
        "sch",
        "edit",
        "--schematic",
        project / "edit.kicad_sch",
        "--changes",
        changes,
        "--confirm",
        plan["data"]["confirm_token"],
    )
    assert code == 0 and done["ok"], done
    assert project_bytes(project) != before
    assert (project / "edit.kicad_pcb").read_bytes() == before["edit.kicad_pcb"]


@pytest.mark.parametrize("case", ["clear-local-turn", "no-room-at-retained-anchor"])
def test_rc_long_rename_retains_anchors_and_exposes_conflicts(edit_scene, case):
    from kicad_cli.fileformat.schematic import Schematic
    from kicad_cli.fileformat.sexpr import render

    scene, changes, call, _preview = edit_scene
    project = scene.parent / "rc"
    shutil.copytree(REPO / "bench/advanced_edit/fixtures/rc-baseline", project)
    sch = project / "rc4.kicad_sch"
    before = project_bytes(project)
    original = Schematic.load(sch)
    expected = {
        label.node.find("uuid").value(1): label.position
        for label in original.labels
        if label.text == "OUT2"
    }
    name = "OUT2_SENSE" if case == "clear-local-turn" else "NET" + "LONG" * 200
    changes.write_text(
        json.dumps({"changes": [{"op": "rename", "net": "/OUT2", "to": name}]}), encoding="utf-8"
    )
    args = ["sch", "edit", "--schematic", sch, "--changes", changes]
    code, plan = call(*args, "--dry-run")
    assert project_bytes(project) == before
    if case == "no-room-at-retained-anchor":
        assert code == 2 and plan["error"]["code"] == "E_VALIDATION", plan
        problems = plan["error"]["details"]["problems"]
        assert problems[0]["path"] == "/changes/0/to"
        assert problems[0]["layout"][0]["status"] == "no_clear_orientation_at_retained_anchor"
        return
    assert code == 0 and plan["data"]["preview"]["can_apply"], plan
    feedback = plan["data"]["preview"]["extensions"]["engineering_feedback"]
    layouts = feedback["affected_edits"][0]["label_layout"]
    assert (
        len(layouts) == 3
        and sum(row["status"] == "turned_at_retained_anchor" for row in layouts) == 2
    )
    assert any(any(obj.get("text") == "IN3" for obj in row["conflicts_before"]) for row in layouts)
    assert all(row["overlaps"] == 0 for row in layouts) and feedback["requires_visual_review"]
    code, done = call(*args, "--confirm", plan["data"]["confirm_token"])
    assert code == 0 and done["ok"], done
    saved = Schematic.load(sch)
    assert {
        label.node.find("uuid").value(1): label.position
        for label in saved.labels
        if label.text == name
    } == expected
    assert [render(symbol.node) for symbol in saved.symbols] == [
        render(symbol.node) for symbol in original.symbols
    ]
    assert (project / "rc4.kicad_pcb").read_bytes() == before["rc4.kicad_pcb"]
    assert (project / "rc4.kicad_pro").read_bytes() == before["rc4.kicad_pro"]
