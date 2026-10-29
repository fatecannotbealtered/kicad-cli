"""Saved SCH/PCB contracts through the public candidate, with owned process evidence.

The input is the committed synthetic sch_edit fixture, never a retained failed
native project. File consumers below are custom checks; official native and
complete-image acceptance are recorded separately.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from driver_test_utils import grant

from kicad_cli import sync_readback
from kicad_cli.fileformat import netlist
from kicad_cli.fileformat.board import Board
from kicad_cli.fileformat.sexpr import Document, copy

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests/fixtures/sch_edit"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows owned process-tree acceptance")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def primary(root):
    return {
        p.relative_to(root).as_posix(): digest(p) for p in sorted(root.rglob("*")) if p.is_file()
    }


def pins(schematic):
    return {
        (node.ref, node.pin): network.name
        for network in netlist.build(schematic).nets
        for node in network.nodes
    }


def refused_input(code, result, defect):
    expected = (
        (2, "E_USAGE")
        if defect == "omitted"
        else ((3, "E_NOT_FOUND") if defect == "missing-file" else (2, "E_VALIDATION"))
    )
    assert not result["ok"] and (code, result["error"]["code"]) == expected, result
    assert result["error"]["details"]["dispatch_state"] == "not_started"


@pytest.fixture
def saved_scene(tmp_path, record_property):
    project = tmp_path / "保存工程 中文 空格"
    shutil.copytree(FIXTURE, project, ignore=shutil.ignore_patterns("*.py", "__pycache__"))
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("KICAD", "PYTHON")) and k != "KIPRJMOD"
    }
    env.update(grant(tmp_path), PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    env["PYTHONHASHSEED"] = "0"
    env["KICAD_CONFIG_HOME"] = str(tmp_path / "configuration")
    Path(env["KICAD_CONFIG_HOME"]).mkdir()
    for name in ("KICAD_CLI_ROOT", "KICAD_CLI_DRIVER_SDK_PATH"):
        if name in os.environ:
            env[name] = os.environ[name]
    binary = os.environ.get("KICAD_CLI_TEST_BINARY")
    if binary:
        env["KICAD_CLI_DRIVER_SDK_PATH"] = os.environ["KICAD_CLI_ACCEPT_DRIVER_SDK"]
        record_property("fcc_artifact_sha256", digest(binary))
    prefix = [binary] if binary else [sys.executable, "-B", "-m", "kicad_cli.main"]
    calls = tmp_path / "calls"
    calls.mkdir()
    count = 0

    def call(*args):
        nonlocal count
        from bench.persistence_acceptance.process_tree import observe

        count += 1
        output = calls / f"{count:03d}"
        result = observe(
            [*prefix, *map(str, args), "--compact"],
            tmp_path if binary else REPO,
            env,
            output,
            timeout=120,
        )
        assert result["complete_tree"] and result["job_active_before_close"] == 0
        assert all(row["owned"] and row["exit"] is not None for row in result["processes"])
        return result["exit"], json.loads((output / "stdout").read_bytes())

    schematic = project / "edit.kicad_sch"
    board = project / "edit.kicad_pcb"
    changes = tmp_path / "changes.json"

    def edit(request):
        changes.write_text(json.dumps({"changes": request}), encoding="utf-8")
        return ["sch", "edit", "--schematic", str(schematic), "--changes", str(changes)]

    def apply(args, key):
        before = primary(project)
        code, plan = call(*args, "--dry-run")
        assert code == 0 and plan["ok"], plan
        assert primary(project) == before
        code, done = call(
            *args, "--confirm", plan["data"]["confirm_token"], "--idempotency-key", key
        )
        assert code == 0 and done["ok"], done
        assert done["data"]["receipt"]["outcome"]["verification"]["status"] == "passed"
        return plan, done

    return SimpleNamespace(
        root=project,
        env=env,
        sch=schematic,
        board=board,
        changes=changes,
        call=call,
        edit=edit,
        apply=apply,
        sync=["project", "sync", "--board", str(board), "--schematic", str(schematic)],
    )


def test_edit_idempotency_receipt_retrieval(saved_scene):
    s = saved_scene
    initial = primary(s.root)
    args = s.edit([{"op": "set", "ref": "R1", "value": "12k"}])
    plan, done = s.apply(args, "saved-edit")
    receipt = done["data"]["receipt"]
    saved = primary(s.root)
    assert saved["edit.kicad_sch"] != initial["edit.kicad_sch"]
    assert {k: v for k, v in saved.items() if k != "edit.kicad_sch"} == {
        k: v for k, v in initial.items() if k != "edit.kicad_sch"
    }
    database = Path(s.env["KICAD_CLI_STATE_DIR"]) / "operations.sqlite3"
    with sqlite3.connect(database) as db:
        operations = db.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
    code, replay = s.call(
        *args, "--confirm", plan["data"]["confirm_token"], "--idempotency-key", "saved-edit"
    )
    assert code == 0 and replay["ok"]
    retrieved = replay["data"]["receipt"]
    assert retrieved.pop("replayed") is True and retrieved == receipt
    assert primary(s.root) == saved
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == operations
    code, refused = s.call(
        *args, "--confirm", plan["data"]["confirm_token"], "--idempotency-key", "different-key"
    )
    assert code == 6 and not refused["ok"]
    assert refused["error"]["details"]["dispatch_state"] == "not_started"
    assert primary(s.root) == saved
    code, fresh = s.call(*s.edit([{"op": "set", "ref": "R1", "value": "13k"}]), "--dry-run")
    assert code == 0 and fresh["data"]["preview"]["can_apply"]
    assert primary(s.root) == saved


def test_values_fields_marks_and_net_rename_sync_saved(saved_scene):
    s = saved_scene
    initial = primary(s.root)
    before_bytes = s.board.read_bytes()
    before = sync_readback.observe(s.board).objects
    args = s.edit(
        [
            {"op": "set", "ref": "R1", "value": "22k"},
            {"op": "set", "ref": "R3", "dnp": True, "fields": {"MPN": "RC0805"}},
            {"op": "rename", "net": "/SIG", "to": "DATA"},
        ]
    )
    edit_plan, _ = s.apply(args, "edit-before-sync")
    assert edit_plan["data"]["preview"]["extensions"]["engineering_feedback"]
    schematic_saved = primary(s.root)
    assert schematic_saved["edit.kicad_pcb"] == initial["edit.kicad_pcb"]
    plan, done = s.apply(s.sync, "sync-saved")
    receipt = done["data"]["receipt"]
    assert receipt["outcome"]["persistence"] == "saved"
    assert receipt["extensions"]["task_check"]["status"] == "passed"
    observed = sync_readback.observe(s.board).objects
    assert (
        observed
        == receipt["extensions"]["observed"]
        == plan["data"]["preview"]["extensions"]["expected"]
    )
    actual = {item["ref"]: item for item in observed}
    assert (
        sync_readback.inspect_bytes(before_bytes, renamed={"/SIG": "/DATA"})[1]
        == (sync_readback.inspect_bytes(s.board.read_bytes())[1])
    )
    assert actual["R1"]["value"] == "22k"
    assert "dnp" in actual["R3"]["attributes"] and actual["R3"]["fields"]["MPN"] == "RC0805"
    for old in before:
        current = actual[old["ref"]]
        for key in ("id", "path", "position_nm", "rotation_deg", "fpid"):
            assert current[key] == old[key]
        assert [(p["number"], p["position_nm"]) for p in current["pads"]] == [
            (p["number"], p["position_nm"]) for p in old["pads"]
        ]
    assert receipt["extensions"]["rows"]["renamed_nets"] == [{"from": "/SIG", "to": "/DATA"}]
    final = primary(s.root)
    assert {k: v for k, v in final.items() if k != "edit.kicad_pcb"} == {
        k: v for k, v in schematic_saved.items() if k != "edit.kicad_pcb"
    }


def test_noop_sync_is_scoped_and_preserves_all_inputs(saved_scene):
    s = saved_scene
    before = primary(s.root)
    plan, done = s.apply(s.sync, "no-op-sync")
    assert plan["data"]["preview"]["changes"] == []
    assert done["data"]["receipt"]["outcome"]["change"] == "not_applied"
    assert done["data"]["receipt"]["outcome"]["persistence"] == "unchanged"
    assert primary(s.root) == before


@pytest.mark.parametrize("defect", ["omitted", "missing-file", "wrong-kind"])
def test_sync_board_refusals(saved_scene, defect):
    s = saved_scene
    args = list(s.sync)
    if defect == "omitted":
        del args[2:4]
    else:
        args[3] = str(s.root / "absent.kicad_pcb") if defect == "missing-file" else str(s.sch)
    before = primary(s.root)
    code, result = s.call(*args, "--dry-run")
    refused_input(code, result, defect)
    assert primary(s.root) == before


@pytest.mark.parametrize("defect", ["omitted", "missing-file", "wrong-kind", "missing-child"])
def test_sync_schematic_refusals(saved_scene, defect):
    s = saved_scene
    args = list(s.sync)
    if defect == "omitted":
        del args[4:6]
    elif defect == "missing-child":
        s.sch.write_bytes(
            s.sch.read_bytes().replace(b"edit_child.kicad_sch", b"absent_child.kicad_sch")
        )
    else:
        args[5] = str(s.root / "absent.kicad_sch") if defect == "missing-file" else str(s.board)
    before = primary(s.root)
    code, result = s.call(*args, "--dry-run")
    refused_input(code, result, defect)
    assert primary(s.root) == before


@pytest.mark.parametrize("defect", ["omitted", "missing-file", "wrong-kind", "wrong-target"])
def test_edit_schematic_refusals(saved_scene, defect):
    s = saved_scene
    args = s.edit(
        [{"op": "set", "ref": "ABSENT" if defect == "wrong-target" else "R1", "value": "12k"}]
    )
    if defect == "omitted":
        del args[2:4]
    elif defect != "wrong-target":
        args[3] = str(s.root / "absent.kicad_sch") if defect == "missing-file" else str(s.board)
    before = primary(s.root)
    code, result = s.call(*args, "--dry-run")
    refused_input(code, result, defect)
    assert primary(s.root) == before


@pytest.mark.parametrize(
    "defect", ["omitted", "missing-file", "malformed-json", "unsupported-change"]
)
def test_edit_changes_refusals(saved_scene, defect):
    s = saved_scene
    args = s.edit([{"op": "set", "ref": "R1", "value": "12k"}])
    if defect == "omitted":
        del args[4:6]
    elif defect == "missing-file":
        args[5] = str(s.root / "absent.json")
    else:
        s.changes.write_text(
            "{" if defect == "malformed-json" else '{"changes":[{"op":"route","ref":"R1"}]}',
            encoding="utf-8",
        )
    before = primary(s.root)
    code, result = s.call(*args, "--dry-run")
    refused_input(code, result, defect)
    assert primary(s.root) == before


@pytest.mark.parametrize("parameter", ["board", "schematic"])
def test_sync_confirmation_input_identity_mismatch(saved_scene, parameter):
    s = saved_scene
    code, planned = s.call(*s.sync, "--dry-run")
    assert code == 0 and planned["ok"]
    args = list(s.sync)
    index = 3 if parameter == "board" else 5
    other = s.root / ("other.kicad_pcb" if parameter == "board" else "other.kicad_sch")
    shutil.copyfile(args[index], other)
    args[index] = str(other)
    before = primary(s.root)
    code, refused = s.call(*args, "--confirm", planned["data"]["confirm_token"])
    assert code == 6 and not refused["ok"], refused
    assert refused["error"]["details"]["dispatch_state"] == "not_started"
    assert primary(s.root) == before


def test_sync_locked_footprint_retains_saved_schematic_stage(saved_scene):
    s = saved_scene
    document = Document.parse(s.board.read_text(encoding="utf-8"), lazy=False)
    footprint = next(item for item in Board(document).footprints if item.reference == "R1")
    footprint.node.append(copy(Document.parse("(locked yes)").root))
    document.save(s.board)
    prepared = Board(Document.parse(s.board.read_text(encoding="utf-8"), lazy=False))
    assert len(prepared.footprints) == len(Board(document).footprints)
    assert next(item for item in prepared.footprints if item.reference == "R1").locked
    initial = primary(s.root)
    s.apply(s.edit([{"op": "set", "ref": "R1", "value": "12k"}]), "locked-sch-stage")
    saved = primary(s.root)
    assert saved["edit.kicad_sch"] != initial["edit.kicad_sch"]
    assert saved["edit.kicad_pcb"] == initial["edit.kicad_pcb"]
    code, refused = s.call(*s.sync, "--dry-run")
    assert code == 2 and refused["error"]["code"] == "E_VALIDATION", refused
    assert refused["error"]["details"]["reference"] == "R1"
    assert "locked" in refused["error"]["details"]["reason"].lower()
    assert primary(s.root) == saved
    assert next(
        item
        for item in Board(Document.parse(s.board.read_text(encoding="utf-8"))).footprints
        if item.reference == "R1"
    ).locked


def test_named_pin_alias_disconnect_preserves_survivor_intent(saved_scene):
    s = saved_scene
    original = pins(s.sch)
    selected = {("U1", "3"), ("U1", "5")}
    s.apply(s.edit([{"op": "connect", "ref": "U1", "pin": "IN+", "net": "/OUT"}]), "alias-connect")
    connected = pins(s.sch)
    assert all(connected[key] == "/OUT" for key in selected)
    plan, _ = s.apply(s.edit([{"op": "disconnect", "ref": "U1", "pin": "IN+"}]), "alias-disconnect")
    matched = plan["data"]["preview"]["extensions"]["engineering_feedback"]["affected_edits"][0][
        "matched_pins"
    ]
    assert {(p["ref"], p["pin"], p["unit"]) for p in matched} == {("U1", "3", 1), ("U1", "5", 2)}
    assert all(p["symbol_uuid"] and p["instance_path"].startswith("/") for p in matched)
    actual = pins(s.sch)
    assert all(actual.get(key) != "/OUT" for key in selected)
    assert {k: v for k, v in actual.items() if k not in selected} == {
        k: v for k, v in original.items() if k not in selected
    }
    assert {k for k, v in actual.items() if v == "/OUT"} == {
        k for k, v in original.items() if v == "/OUT"
    }
    assert not list(s.root.glob("*.kicad-cli.lock"))
    code, fresh = s.call(*s.edit([{"op": "set", "ref": "R2", "value": "15k"}]), "--dry-run")
    assert code == 0 and fresh["ok"]
