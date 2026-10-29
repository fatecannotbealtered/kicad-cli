"""Public geometry discovery and document preparation on synthetic owned inputs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from driver_test_utils import grant

from kicad_cli import driver_runtime, engineering_prepare, engineering_readback, read_inputs
from kicad_cli.native import sch_edit

ROOT_UUID = "11111111-1111-1111-1111-111111111111"
JUNCTION_UUID = "22222222-2222-2222-2222-222222222222"
OTHER_UUID = "33333333-3333-3333-3333-333333333333"
SCHEMATIC = f'''(kicad_sch (version 20250114) (generator "owned-test")
  (uuid "{ROOT_UUID}") (paper "A4") (lib_symbols)
  (junction (at 10 20) (diameter 0) (color 0 0 0 0) (uuid "{JUNCTION_UUID}"))
  (junction (at 40 50) (diameter 0) (color 0 0 0 0) (uuid "{OTHER_UUID}")))
'''


@pytest.fixture
def case(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    schematic = project / "owned.kicad_sch"
    schematic.write_text(SCHEMATIC, encoding="utf-8")
    environ = {key: value for key, value in os.environ.items() if not key.startswith("KICAD_CLI_")}
    environ.update(grant(tmp_path), PYTHONIOENCODING="utf-8")
    return schematic, environ


def run(case, *argv, scoped=True):
    schematic, environ = case
    environ = dict(environ)
    if not scoped:
        environ.pop("KICAD_CLI_SCOPE_FILE", None)
    with driver_runtime.create_cli(environ=environ, home=schematic.parent) as cli:
        result = cli.run([*map(str, argv), "--compact"])
        cli.validator.validate("response", result.envelope)
        return result.envelope


def query(case, *flags):
    return run(case, "sch", "objects", "--schematic", case[0], *flags, scoped=False)


def transform(case, selector, **updates):
    value = {
        "op": "transform",
        "objects": [selector],
        "translate_mm": [1, 0],
        "affected_instances": ["/" + ROOT_UUID],
    }
    value.update(updates)
    return value


def preview(case, changes):
    path = case[0].parent / "changes.json"
    path.write_text(json.dumps({"changes": changes}), encoding="utf-8")
    result = run(case, "sch", "edit", "--schematic", case[0], "--changes", path, "--dry-run")
    return result, path


def test_public_query_is_paginated_snapshot_bound_and_preserves_inputs(case):
    schematic, environ = case
    before = schematic.read_bytes()
    result = query(case, "--kind", "junction", "--limit", 1)
    assert result["ok"], result
    data = result["data"]
    assert data["count"] == 1 and data["has_more"] and data["next_offset"] == 1
    assert data["snapshot"]["source"] == "disk" and data["snapshot"]["dependencies"]
    assert data["extensions"]["observation"]["input_drift_checked"]
    selector = data["items"][0]["selector"]
    assert selector["root"] == schematic.as_uri()
    assert selector["snapshot"] == data["snapshot"]["revision"]
    second = query(case, "--kind", "junction", "--limit", 1, "--offset", data["next_offset"])
    assert second["ok"] and second["data"]["count"] == 1
    assert not second["data"]["has_more"] and not second["data"]["truncated"]
    assert second["data"]["snapshot"]["revision"] == data["snapshot"]["revision"]
    assert second["data"]["items"][0]["selector"] != selector
    assert schematic.read_bytes() == before
    assert not Path(environ["KICAD_CLI_STATE_DIR"]).exists()


def test_runtime_protocol_and_public_migration_are_bound(case):
    result = run(case, "reference", "--command", "sch objects")
    assert result["ok"], result
    declaration = json.loads(driver_runtime.PACKAGE.joinpath("driver_contract.json").read_bytes())
    assert declaration["reference"]["runtime_migration"]["protocol_revision"] == (
        driver_runtime.PROTOCOL_REVISION
    )


@pytest.mark.parametrize("flags", [("--limit", "501"), ("--offset", "-1"), ("--kind", "unknown")])
def test_query_invalid_filters_return_pre_dispatch_validation(case, flags):
    before = case[0].read_bytes()
    result = query(case, *flags)
    assert not result["ok"] and result["error"]["code"] == "E_VALIDATION", result
    assert result["error"]["details"]["dispatch_state"] == "not_started"
    assert case[0].read_bytes() == before


def test_query_projects_rows_and_reports_saved_input_drift(case, monkeypatch):
    projected = query(case, "--fields", "kind")
    assert projected["ok"] and all(set(row) == {"kind"} for row in projected["data"]["items"])
    validate = read_inputs.Inputs.validate
    calls = 0

    def changed(inputs):
        nonlocal calls
        calls += 1
        if calls == 2:
            case[0].write_bytes(case[0].read_bytes() + b"\n")
        return validate(inputs)

    monkeypatch.setattr(read_inputs.Inputs, "validate", changed)
    result = query(case)
    assert not result["ok"] and result["error"]["code"] == "E_CONFLICT"


def test_stale_query_selection_refuses_before_candidate_stage(case):
    observed = query(case, "--uuid", JUNCTION_UUID)
    assert observed["ok"], observed
    selector = observed["data"]["items"][0]["selector"]
    case[0].write_bytes(case[0].read_bytes() + b"\n")
    before = case[0].read_bytes()
    result, _ = preview(case, [transform(case, selector)])
    assert not result["ok"] and result["error"]["code"] == "E_CONFLICT", result
    assert result["error"]["details"]["problems"][0]["path"] == "/changes/0/objects/0"
    assert case[0].read_bytes() == before
    assert not (Path(case[1]["KICAD_CLI_STATE_DIR"]) / "engineering-trials").exists()


@pytest.mark.parametrize("number", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_transform_values_refuse_before_candidate_stage(case, number):
    observed = query(case, "--uuid", JUNCTION_UUID)
    assert observed["ok"], observed
    selected = observed["data"]["items"][0]["selector"]
    before = case[0].read_bytes()
    result, _ = preview(case, [transform(case, selected, translate_mm=[number, 0])])
    assert not result["ok"] and result["error"]["code"] == "E_VALIDATION", result
    assert any(
        p["path"] == "/changes/0/translate_mm/0" for p in result["error"]["details"]["problems"]
    )
    assert case[0].read_bytes() == before
    assert not (Path(case[1]["KICAD_CLI_STATE_DIR"]) / "engineering-trials").exists()


def test_duplicate_and_mixed_transform_batches_refuse_before_stage(case):
    observed = query(case, "--uuid", JUNCTION_UUID)
    assert observed["ok"], observed
    selection = observed["data"]["items"][0]["selector"]
    change = transform(case, selection)
    for changes, expected_path in [
        ([change, change], "/changes/1/objects/0"),
        ([change, {"op": "set", "ref": "R1", "value": "12k"}], "/changes/1/op"),
    ]:
        before = case[0].read_bytes()
        result, _ = preview(case, changes)
        assert not result["ok"] and result["error"]["code"] == "E_VALIDATION", result
        assert any(p["path"] == expected_path for p in result["error"]["details"]["problems"])
        assert case[0].read_bytes() == before
        assert not (Path(case[1]["KICAD_CLI_STATE_DIR"]) / "engineering-trials").exists()


def test_public_transform_preview_confirm_and_saved_query(case):
    observed = query(case, "--uuid", JUNCTION_UUID)
    assert observed["ok"], observed
    original_selector = observed["data"]["items"][0]["selector"]
    before = case[0].read_bytes()
    planned, changes = preview(case, [transform(case, original_selector)])
    assert planned["ok"] and planned["data"]["preview"]["can_apply"], planned
    assert case[0].read_bytes() == before
    done = run(
        case,
        "sch",
        "edit",
        "--schematic",
        case[0],
        "--changes",
        changes,
        "--confirm",
        planned["data"]["confirm_token"],
    )
    assert done["ok"], done
    receipt = done["data"]["receipt"]
    assert receipt["outcome"]["verification"]["status"] == "passed"
    assert receipt["outcome"]["persistence"] == "saved"
    assert b"(at 11 20)" in case[0].read_bytes()
    assert case[0].read_bytes() != before
    saved = query(case, "--uuid", JUNCTION_UUID)
    assert saved["ok"], saved
    assert saved["data"]["items"][0]["selector"] != original_selector
    assert not list(case[0].parent.glob("*.kicad-cli.lock"))
    assert not list(case[0].parent.glob("*.kicad-cli.journal"))


def test_transform_save_failure_preserves_original_with_observed_restoration(case, monkeypatch):
    observed = query(case, "--uuid", JUNCTION_UUID)
    assert observed["ok"], observed
    selected = observed["data"]["items"][0]["selector"]
    planned, changes = preview(case, [transform(case, selected)])
    assert planned["ok"], planned
    before = case[0].read_bytes()
    replace = os.replace

    def fail_save(source, destination):
        if Path(destination) == case[0] and str(source).endswith(".pending"):
            raise PermissionError("Synthetic owned fault before geometry save")
        return replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_save)
    done = run(
        case,
        "sch",
        "edit",
        "--schematic",
        case[0],
        "--changes",
        changes,
        "--confirm",
        planned["data"]["confirm_token"],
    )
    assert not done["ok"], done
    assert done["error"]["details"]["receipt"]["extensions"]["restore_observed"]
    assert case[0].read_bytes() == before


def test_geometry_route_precedes_legacy_kicad_discovery(tmp_path, monkeypatch):
    import sys

    called = []
    root, original = tmp_path / "candidate.kicad_sch", tmp_path / "original.kicad_sch"
    changes = [{"op": "transform"}]

    def geometry(schematic, requested, *, identity_root=None):
        called.append((schematic, requested, identity_root))
        return {"verified": {"geometry": True}}, {}

    def forbidden():
        raise AssertionError("Legacy installation discovery was reached")

    monkeypatch.setitem(
        sys.modules, "kicad_cli.sch_geometry", SimpleNamespace(run_geometry=geometry)
    )
    monkeypatch.setattr(sch_edit.kicad_env, "find_kicad_root", forbidden)
    sch_edit.run(root, changes, identity_root=original)
    assert called == [(root, changes, original)]


def test_preparation_uses_captured_original_identity_and_geometry_readback(tmp_path, monkeypatch):
    from kicad_cli.payload import write_txn

    original = tmp_path / "original.kicad_sch"
    candidate = tmp_path / "candidate.kicad_sch"
    changes = tmp_path / "changes.json"
    candidate.write_text(SCHEMATIC, encoding="utf-8")
    changes.write_text("[]", encoding="utf-8")
    requested = [{"op": "transform"}]
    observed = []
    monkeypatch.setattr(sch_edit, "load_changes", lambda _: requested)

    def prepared(schematic, loaded, *, identity_root=None):
        observed.append((schematic, loaded, identity_root))
        return {"verified": {"geometry": True}}, {candidate: SCHEMATIC}

    monkeypatch.setattr(sch_edit, "run", prepared)
    monkeypatch.setattr(write_txn, "commit", lambda: None)
    result = engineering_prepare.prepare(
        {
            "command": "sch edit",
            "params": {"changes": str(changes)},
            "primary": str(candidate),
            "original_primary": str(original),
            "mapping": {str(original): str(candidate), str(changes): str(changes)},
        }
    )
    assert result["ok"] and observed == [(candidate, requested, original)]
    readback = engineering_readback.observe([{"path": str(candidate)}])[0]
    assert len(readback["objects"]["geometry"]) == 2
    assert "22222222-2222-2222-2222-222222222222" in json.dumps(readback["objects"]["geometry"])


@pytest.mark.parametrize("drift", ["schematic", "changes", "authorization"])
def test_geometry_confirm_rejects_drift_without_new_engineering_write(case, drift):
    observed = query(case, "--uuid", JUNCTION_UUID)
    selected = observed["data"]["items"][0]["selector"]
    planned, changes = preview(case, [transform(case, selected)])
    assert planned["ok"], planned
    if drift == "schematic":
        case[0].write_bytes(case[0].read_bytes() + b"\n")
    elif drift == "changes":
        changes.write_bytes(changes.read_bytes() + b"\n")
    else:
        scope = Path(case[1]["KICAD_CLI_SCOPE_FILE"])
        record = json.loads(scope.read_bytes())
        record["scope_id"] = "changed-isolated-scope"
        scope.write_text(json.dumps(record), encoding="utf-8")
    before = case[0].read_bytes()
    done = run(
        case,
        "sch",
        "edit",
        "--schematic",
        case[0],
        "--changes",
        changes,
        "--confirm",
        planned["data"]["confirm_token"],
    )
    assert not done["ok"], done
    assert done["error"]["details"]["dispatch_state"] == "not_started", done
    assert case[0].read_bytes() == before


@pytest.mark.parametrize("kind", ["source-domain", "readonly-kind", "off-quantum"])
def test_geometry_domain_refusal_has_valid_public_error_details(case, kind):
    if kind == "source-domain":
        case[0].write_text(SCHEMATIC.replace("10 20", "10000.0001 20"), encoding="utf-8")
    elif kind == "readonly-kind":
        case[0].write_text(
            SCHEMATIC.replace(
                "(junction (at 10 20) (diameter 0) (color 0 0 0 0)",
                '(text "read only" (at 10 20 0) (effects (font (size 1 1)))',
            ),
            encoding="utf-8",
        )
    observed = query(case, "--uuid", JUNCTION_UUID)
    assert observed["ok"], observed
    selected = observed["data"]["items"][0]["selector"]
    before = case[0].read_bytes()
    move = [0.00001, 0] if kind == "off-quantum" else [1, 0]
    result, _ = preview(case, [transform(case, selected, translate_mm=move)])
    assert not result["ok"] and result["error"]["code"] == "E_VALIDATION", result
    details = result["error"]["details"]
    assert details["problem"] == "invalid_value"
    assert details["dispatch_state"] == "not_started"
    assert details["problems"][0]["path"].startswith("/changes/0")
    assert details["problems"][0]["problem"]
    assert case[0].read_bytes() == before
