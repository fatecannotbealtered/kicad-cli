"""Actual saved-input byte changes refuse before dispatch on source or final EXE."""

import pytest
from test_saved_project_exe import primary
from test_saved_project_exe import pytestmark as pytestmark
from test_saved_project_exe import saved_scene as saved_scene


def refused(s, args, token, baseline):
    code, result = s.call(*args, "--confirm", token, "--idempotency-key", "drift-must-not-write")
    assert code == 6 and not result["ok"] and result["error"]["code"] == "E_CONFLICT", result
    assert result["error"]["details"]["dispatch_state"] == "not_started"
    assert primary(s.root) == baseline


@pytest.mark.parametrize("target", ["board", "schematic", "project"])
def test_sync_saved_input_byte_drift_refuses(saved_scene, target):
    s = saved_scene
    code, plan = s.call(*s.sync, "--dry-run")
    assert code == 0 and plan["ok"]
    path = {"board": s.board, "schematic": s.sch, "project": s.board.with_suffix(".kicad_pro")}[
        target
    ]
    path.write_bytes(path.read_bytes() + b"\n")
    refused(s, s.sync, plan["data"]["confirm_token"], primary(s.root))


def test_edit_changes_byte_drift_refuses(saved_scene):
    s = saved_scene
    args = s.edit([{"op": "set", "ref": "R1", "value": "12k"}])
    code, plan = s.call(*args, "--dry-run")
    assert code == 0 and plan["ok"]
    s.changes.write_bytes(s.changes.read_bytes().replace(b"12k", b"13k"))
    changes = s.changes.read_bytes()
    refused(s, args, plan["data"]["confirm_token"], primary(s.root))
    assert s.changes.read_bytes() == changes


def test_edit_schematic_byte_drift_refuses(saved_scene):
    s = saved_scene
    args = s.edit([{"op": "set", "ref": "R1", "value": "12k"}])
    code, plan = s.call(*args, "--dry-run")
    assert code == 0 and plan["ok"]
    s.sch.write_bytes(s.sch.read_bytes() + b"\n")
    refused(s, args, plan["data"]["confirm_token"], primary(s.root))
