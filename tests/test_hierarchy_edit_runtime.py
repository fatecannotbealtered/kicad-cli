"""Public child-only commit keeps the unmodified root usable, without a stale lock."""

import pytest
from test_sch_edit_contract import edit_scene as edit_scene


@pytest.fixture(autouse=True)
def deterministic_public_child_hashing(monkeypatch):
    # Existing alias disconnect chose a survivor through unordered-set iteration.
    # Seed 0 makes its false expectation reproducible in the public subprocess.
    monkeypatch.setenv("PYTHONHASHSEED", "0")


def test_child_only_edit_cleans_primary_lock_and_allows_next_preview(edit_scene):
    project, changes, call, preview = edit_scene
    root = project / "edit.kicad_sch"
    original = root.read_bytes()
    code, planned = preview([{"op": "rename", "net": "/CHILD_A/LOCAL", "to": "SENSE"}])
    assert code == 0 and planned["data"]["preview"]["can_apply"]
    files = planned["data"]["preview"]["extensions"]["files"]
    assert len(files) == 1 and files[0]["path"].endswith("edit_child.kicad_sch")
    code, saved = call(
        "sch",
        "edit",
        "--schematic",
        root,
        "--changes",
        changes,
        "--confirm",
        planned["data"]["confirm_token"],
    )
    assert code == 0 and saved["ok"]
    assert saved["data"]["receipt"]["outcome"]["persistence"] == "saved"
    assert root.read_bytes() == original
    assert not list(project.glob("*.kicad-cli.lock")), "Child-only edit left its primary lock"
    assert not list(project.glob("*.kicad-cli.journal"))
    code, next_plan = preview([{"op": "set", "ref": "U1", "value": "LM358A"}])
    assert code == 0 and next_plan["data"]["preview"]["can_apply"]
    assert root.read_bytes() == original


def test_real_name_alias_disconnect_keeps_expectations_only_on_surviving_pins(edit_scene):
    project, changes, call, preview = edit_scene
    root = project / "edit.kicad_sch"
    code, planned = preview([{"op": "connect", "ref": "U1", "pin": "IN+", "net": "/OUT"}])
    assert code == 0 and planned["data"]["preview"]["can_apply"]
    action = planned["data"]["preview"]["extensions"]["trial"]["result"]["changes"][0]
    assert sorted(action["joined"], key=lambda p: p["pin"]) == [
        {"pin": "U1.3", "net": "/OUT"},
        {"pin": "U1.5", "net": "/OUT"},
    ]
    code, saved = call(
        "sch",
        "edit",
        "--schematic",
        root,
        "--changes",
        changes,
        "--confirm",
        planned["data"]["confirm_token"],
    )
    assert code == 0 and saved["ok"]
    connected = root.read_bytes()
    code, planned = preview([{"op": "disconnect", "ref": "U1", "pin": "IN+"}])
    assert code == 0 and planned["data"]["preview"]["can_apply"], (
        "All selected named pins must detach; survivors alone keep the old net name"
    )
    matched = planned["data"]["preview"]["extensions"]["engineering_feedback"]["affected_edits"][0][
        "matched_pins"
    ]
    assert [(p["ref"], p["pin"], p["unit"]) for p in matched] == [("U1", "3", 1), ("U1", "5", 2)]
    assert all(p["instance_path"].startswith("/") and p["symbol_uuid"] for p in matched)
    assert root.read_bytes() == connected
    code, saved = call(
        "sch",
        "edit",
        "--schematic",
        root,
        "--changes",
        changes,
        "--confirm",
        planned["data"]["confirm_token"],
    )
    assert code == 0 and saved["ok"]
    assert saved["data"]["receipt"]["outcome"]["verification"]["status"] == "passed"
