"""Actual public source CLI processes for hierarchy save/reopen and drift.

This is controlled source-runtime coverage, not final EXE or native ERC proof.
Electrical hierarchy task families and independent native acceptance are separate.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from driver_test_utils import grant

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def owned_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "modules").mkdir()
    root = project / "root.kicad_sch"
    root.write_bytes(
        b'(kicad_sch (version 20250114) (generator "owned-test") '
        b'(uuid "root") (paper "A4") (lib_symbols))\n'
    )
    env = dict(os.environ, **grant(tmp_path), PYTHONIOENCODING="utf-8")
    env["KICAD_CONFIG_HOME"] = str(tmp_path / "isolated-config")
    return root, env


def cli(case, *args):
    binary = case[1].get("KICAD_CLI_TEST_BINARY")
    prefix = [binary] if binary else [sys.executable, "-B", "-m", "kicad_cli.main"]
    process = subprocess.run(
        [*prefix, *map(str, args), "--compact"],
        cwd=REPO,
        env=case[1],
        capture_output=True,
        timeout=90,
    )
    value = json.loads(process.stdout)
    return value


def objects(case):
    value = cli(case, "sch", "objects", "--schematic", case[0])
    assert value["ok"], value
    return value["data"]


def preview(case, *, parent=None, file="modules/first.kicad_sch", name="ModuleOne"):
    data = objects(case)
    parent = parent or data["extensions"]["pages"][0]["instance_path"]
    request = {
        "op": "circuit",
        "snapshot": {"root": data["extensions"]["root"], "snapshot": data["snapshot"]["revision"]},
        "affected_instances": [parent, {"new": "module", "parent": parent}],
        "actions": [
            {
                "op": "create_sheet",
                "id": "module",
                "page": parent,
                "file": file,
                "name": name,
                "at_mm": [40, 30],
                "size_mm": [25, 20],
                "pins": [],
            }
        ],
        "expected": {"components": []},
    }
    changes = case[0].parent / "changes.json"
    changes.write_text(json.dumps({"changes": [request]}), encoding="utf-8")
    args = ["sch", "edit", "--schematic", case[0], "--changes", changes]
    value = cli(case, *args, "--dry-run")
    assert value["ok"], value
    return args, value


def confirm(case, args, value, key="new-hierarchy"):
    return cli(case, *args, "--confirm", value["data"]["confirm_token"], "--idempotency-key", key)


def preview_connected_sheet(case):
    data = objects(case)
    parent = data["extensions"]["pages"][0]["instance_path"]
    child = {"new": "module", "parent": parent}
    request = {
        "op": "circuit",
        "snapshot": {"root": data["extensions"]["root"], "snapshot": data["snapshot"]["revision"]},
        "affected_instances": [parent, child],
        "actions": [
            {
                "op": "create_sheet",
                "id": "module",
                "page": parent,
                "file": "modules/connected.kicad_sch",
                "name": "Connected",
                "at_mm": [40, 30],
                "size_mm": [25.4, 20.32],
                "pins": [{"name": "IN", "shape": "input", "edge": "left", "offset_mm": 10.16}],
            },
            {
                "op": "create",
                "kind": "hierarchical_label",
                "id": "port",
                "page": child,
                "text": "IN",
                "shape": "input",
                "at": [10.16, 20.32],
                "angle_deg": 0,
            },
            {
                "op": "create",
                "kind": "wire",
                "id": "child_wire",
                "page": child,
                "points": [
                    {"object": {"new": "port"}, "instance_path": child, "endpoint": 0},
                    [20.32, 20.32],
                ],
            },
            {
                "op": "create",
                "kind": "wire",
                "id": "parent_wire",
                "page": parent,
                "points": [
                    {
                        "sheet_pin": {
                            "new": "module",
                            "instance_path": parent,
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
                        {"new": "module", "instance_path": parent, "sheet_pin_ordinal": 0},
                        {"new": "port", "instance_path": child},
                        {"new": "child_wire", "instance_path": child},
                        {"new": "parent_wire", "instance_path": parent},
                    ],
                    "explicit_names": [
                        {"kind": kind, "name": "/Connected/IN"}
                        for kind in ["sheet_pin", "hierarchical_label"]
                    ],
                    "nc_members": [],
                }
            ]
        },
    }
    changes = case[0].parent / "connected-changes.json"
    changes.write_text(json.dumps({"changes": [request]}), encoding="utf-8")
    args = ["sch", "edit", "--schematic", case[0], "--changes", changes]
    value = cli(case, *args, "--dry-run")
    assert value["ok"], value
    return args, value


@pytest.mark.skipif(os.name != "nt", reason="new-file persistence requires Windows NTFS")
def test_public_populated_sheet_save_new_process_query_and_continue(owned_project):
    root, _ = owned_project
    args, value = preview_connected_sheet(owned_project)
    saved = confirm(owned_project, args, value, key="connected-sheet-save")
    assert saved["ok"], saved
    assert saved["data"]["receipt"]["outcome"]["persistence"] == "saved"
    before = root.read_bytes()
    # This launches the public CLI again; no in-process Catalog can conceal an
    # output contract error on populated per-instance sheet_pins.
    reopened = objects(owned_project)
    sheet = next(row for row in reopened["items"] if row["kind"] == "sheet")
    pin = sheet["instances"][0]["sheet_pins"][0]
    assert pin["coordinate_nm"] == [40_000_000, 40_160_000]
    assert pin["coordinate_mm"] == [40.0, 40.16]
    assert pin["name"] == "IN" and pin["direction"] == "input"
    assert pin["net"] == "/Connected/IN" and pin["connection_id"].startswith("schconn:")
    assert sheet["instances"][0]["connection_status"] == "complete"
    assert reopened["extensions"]["hierarchy"]["interface_status"] == "complete"
    original_objects = {row["uuid"]: (row["kind"], row["geometry"]) for row in reopened["items"]}
    child = next(
        page["instance_path"]
        for page in reopened["extensions"]["pages"]
        if page["sheet"] == "/Connected/"
    )
    next_args, next_preview = preview(
        owned_project, parent=child, file="modules/leaf.kicad_sch", name="SavedLeaf"
    )
    next_saved = confirm(
        owned_project, next_args, next_preview, key="continue-after-connected-query"
    )
    assert next_saved["ok"], next_saved
    final = objects(owned_project)
    assert {page["sheet"] for page in final["extensions"]["pages"]} == {
        "/",
        "/Connected/",
        "/Connected/SavedLeaf/",
    }
    assert root.read_bytes() == before
    final_objects = {row["uuid"]: (row["kind"], row["geometry"]) for row in final["items"]}
    assert all(final_objects[uid] == geometry for uid, geometry in original_objects.items())


@pytest.mark.skipif(os.name != "nt", reason="new-file persistence requires Windows NTFS")
def test_public_save_new_process_reopen_then_nested_extension_and_receipt_replay(owned_project):
    root, _ = owned_project
    args, value = preview(owned_project)
    first = confirm(owned_project, args, value)
    assert first["ok"], first
    assert first["data"]["receipt"]["outcome"]["persistence"] == "saved"
    saved = {path: path.read_bytes() for path in (root, root.parent / "modules/first.kicad_sch")}
    replay = confirm(owned_project, args, value)
    assert replay["ok"] and replay["data"]["receipt"]["replayed"] is True
    refused = confirm(owned_project, args, value, key="different-key")
    assert not refused["ok"] and refused["error"]["code"] == "E_CONFLICT"
    assert all(path.read_bytes() == raw for path, raw in saved.items())
    pages = objects(owned_project)["extensions"]["pages"]
    parent = next(page["instance_path"] for page in pages if page["sheet"] == "/ModuleOne/")
    args, value = preview(owned_project, parent=parent, file="modules/leaf.kicad_sch", name="Leaf")
    second = confirm(owned_project, args, value, key="nested-extension")
    assert second["ok"], second
    pages = objects(owned_project)["extensions"]["pages"]
    assert {page["sheet"] for page in pages} == {"/", "/ModuleOne/", "/ModuleOne/Leaf/"}
    assert root.read_bytes() == saved[root]


@pytest.mark.parametrize(
    "drift", ["new_target", "source", "rules", "directory", "candidate", "scope"]
)
def test_public_hierarchy_drift_refuses_before_formal_source_write(owned_project, drift):
    root, env = owned_project
    args, value = preview(owned_project)
    child = root.parent / "modules/first.kicad_sch"
    foreign = None
    if drift == "new_target":
        foreign = b"external new source must not be overwritten"
        child.write_bytes(foreign)
    elif drift == "source":
        root.write_bytes(root.read_bytes() + b"\n")
    elif drift == "rules":
        root.with_suffix(".kicad_pro").write_text("{}", encoding="utf-8")
    elif drift == "directory":
        child.parent.rename(root.parent / "modules-preserved")
        child.parent.mkdir()
    elif drift == "candidate":
        rows = value["data"]["preview"]["extensions"]["files"]
        candidate = Path(next(row["candidate"] for row in rows if row["path"] == str(child)))
        candidate.write_bytes(candidate.read_bytes() + b"\n")
    else:
        scope = Path(env["KICAD_CLI_SCOPE_FILE"])
        data = json.loads(scope.read_bytes())
        data["notes"] += " authorization drift"
        scope.write_text(json.dumps(data), encoding="utf-8")
    before = root.read_bytes()
    result = confirm(owned_project, args, value)
    assert not result["ok"] and result["error"]["code"] == "E_CONFLICT", result
    assert result["error"]["details"]["dispatch_state"] == "not_started", result
    assert root.read_bytes() == before
    if foreign:
        assert child.read_bytes() == foreign
    else:
        assert not child.exists()
