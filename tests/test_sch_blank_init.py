"""Public empty-root creation through the existing scoped transaction pipeline."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from driver_test_utils import grant
from jsonschema import Draft202012Validator

from kicad_cli import schematic
from kicad_cli.fileformat import netlist

REPO = Path(__file__).resolve().parents[1]


def cli(env, *argv):
    process = subprocess.run(
        [sys.executable, "-B", "-m", "kicad_cli.main", *map(str, argv), "--compact"],
        cwd=REPO,
        env=env,
        capture_output=True,
        timeout=60,
    )
    return process.returncode, json.loads(process.stdout)


@pytest.fixture
def blank_case(tmp_path):
    work = tmp_path / "new-project"
    work.mkdir()
    spec = tmp_path / "controller.json"
    spec.write_text('{"mode":"blank","title":"Controller root"}', encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("KICAD")}
    env.update(grant(tmp_path), PYTHONIOENCODING="utf-8")
    for key, child in (
        ("HOME", "home"),
        ("USERPROFILE", "home"),
        ("APPDATA", "appdata"),
        ("LOCALAPPDATA", "localappdata"),
        ("KICAD_CONFIG_HOME", "config"),
    ):
        directory = tmp_path / child
        directory.mkdir(exist_ok=True)
        env[key] = str(directory)
    # No installed symbol libraries/backend may be required for an empty root.
    env["KICAD_CLI_ROOT"] = str(tmp_path / "no-cad-installation")
    args = ["sch", "create", "--spec", spec, "--out", work]
    return tmp_path, work, spec, env, args


def test_blank_schema_is_explicit_and_normal_circuits_stay_nonempty():
    contract = json.loads((REPO / "kicad_cli/driver_contract.json").read_bytes())
    schema = contract["reference"]["json_schemas"][schematic.REQUEST_SCHEMA]["$defs"][
        "circuit_spec"
    ]
    validator = Draft202012Validator(schema)
    assert not list(validator.iter_errors({"mode": "blank", "title": "Empty"}))
    for spec in (
        {},
        {"parts": [], "nets": []},
        {"mode": "blank", "parts": []},
        {"mode": "blank", "nets": []},
        {"mode": "blank", "hierarchy": {}},
    ):
        assert list(validator.iter_errors(spec)), spec
        assert schematic.spec_problems(spec), spec


def test_public_blank_save_reopen_and_continue_without_libraries(blank_case):
    root, work, _, env, args = blank_case
    code, preview = cli(env, *args, "--dry-run")
    assert code == 0 and preview["ok"], preview
    files = preview["data"]["preview"]["extensions"]["files"]
    assert {Path(row["path"]).suffix for row in files} == {
        ".kicad_sch",
        ".kicad_pro",
        ".net",
    }
    assert list(work.iterdir()) == []
    code, saved = cli(env, *args, "--confirm", preview["data"]["confirm_token"])
    assert code == 0 and saved["ok"], saved
    sch = work / "controller.kicad_sch"
    assert json.loads(sch.with_suffix(".kicad_pro").read_bytes()) == {}
    built = netlist.build(sch)
    assert not built.components and not built.nets
    code, objects = cli(env, "sch", "objects", "--schematic", sch)
    assert code == 0 and objects["ok"] and objects["data"]["items"] == [], objects
    code, connections = cli(env, "sch", "connections", "--schematic", sch)
    assert code == 0 and connections["ok"] and not connections["data"]["items"], connections
    data = objects["data"]
    instance = data["extensions"]["pages"][0]["instance_path"]
    request = {
        "op": "circuit",
        "snapshot": {"root": data["extensions"]["root"], "snapshot": data["snapshot"]["revision"]},
        "affected_instances": [instance],
        "actions": [
            {
                "op": "create",
                "id": "wire",
                "page": instance,
                "kind": "wire",
                "points": [[50, 50], [60, 50]],
            }
        ],
        "expected": {
            "components": [
                {
                    "members": [{"new": "wire", "instance_path": instance}],
                    "explicit_names": [],
                    "nc_members": [],
                }
            ]
        },
    }
    changes = root / "continue.json"
    changes.write_text(json.dumps({"changes": [request]}), encoding="utf-8")
    edit = ["sch", "edit", "--schematic", sch, "--changes", changes]
    code, next_preview = cli(env, *edit, "--dry-run")
    assert code == 0 and next_preview["ok"], next_preview
    code, edited = cli(env, *edit, "--confirm", next_preview["data"]["confirm_token"])
    assert code == 0 and edited["ok"], edited
    code, reopened = cli(env, "sch", "objects", "--schematic", sch)
    assert code == 0 and reopened["ok"], reopened
    assert [row["kind"] for row in reopened["data"]["items"]] == ["wire"]


@pytest.mark.parametrize("suffix", [".kicad_sch", ".kicad_pro", ".net"])
def test_blank_refuses_existing_outputs_without_replacement(blank_case, suffix):
    _, work, _, env, args = blank_case
    existing = work / ("controller" + suffix)
    existing.write_bytes(b"existing bytes must survive")
    code, value = cli(env, *args, "--dry-run")
    assert code == 6 and value["error"]["code"] == "E_CONFLICT", value
    assert existing.read_bytes() == b"existing bytes must survive"


def test_blank_requires_scoped_output_and_keeps_missing_target_absent(blank_case):
    root, work, _, env, args = blank_case
    scope = Path(env["KICAD_CLI_SCOPE_FILE"])
    record = json.loads(scope.read_bytes())
    record["roots"] = [str(root / "somewhere-else")]
    scope.write_text(json.dumps(record), encoding="utf-8")
    code, value = cli(env, *args, "--dry-run")
    assert code == 4 and value["error"]["code"] == "E_FORBIDDEN", value
    assert list(work.iterdir()) == []


def test_plain_empty_spec_is_still_refused(blank_case):
    _, work, spec, env, args = blank_case
    spec.write_text('{"parts":[],"nets":[]}', encoding="utf-8")
    code, value = cli(env, *args, "--dry-run")
    assert code == 2 and value["error"]["code"] == "E_VALIDATION", value
    assert list(work.iterdir()) == []


@pytest.mark.parametrize("drift", ["spec", "new_project"])
def test_blank_confirm_binds_intent_and_absent_project(blank_case, drift):
    _, work, spec, env, args = blank_case
    code, preview = cli(env, *args, "--dry-run")
    assert code == 0 and preview["ok"], preview
    foreign = work / "controller.kicad_pro"
    if drift == "spec":
        spec.write_text('{"mode":"blank","title":"Changed intent"}', encoding="utf-8")
    else:
        foreign.write_bytes(b"foreign project must survive")
    code, rejected = cli(env, *args, "--confirm", preview["data"]["confirm_token"])
    assert code == 6 and rejected["error"]["code"] == "E_CONFLICT", rejected
    assert not (work / "controller.kicad_sch").exists()
    assert not (work / "controller.net").exists()
    if drift == "new_project":
        assert foreign.read_bytes() == b"foreign project must survive"
