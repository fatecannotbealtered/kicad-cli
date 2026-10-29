"""Pure host fixtures for the optional public, pre-confirm sync candidate.

These tests do not run a native renderer and do not simulate a real process kill.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import test_driver_sync_runtime as sync_tests
from test_board_update import edit
from test_driver_sync_runtime import command, run

from kicad_cli import (
    candidate_output,
    document_transaction,
    driver_runtime,
    sync_candidate,
    sync_inputs,
    sync_plan,
)

case = sync_tests.case

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="Handle-bound candidate publication requires Windows."
)


def candidate_command(case, *flags, output=None, **options):
    output = output or case[0].parent / "review.kicad_pcb"
    return run(
        case,
        [
            "project",
            "sync",
            "--board",
            str(case[0]),
            "--schematic",
            str(case[0].with_suffix(".kicad_sch")),
            "--candidate-out",
            str(output),
            *flags,
        ],
        **options,
    )


def planned(case):
    edit(case[0], [{"op": "set", "ref": "R1", "value": "22k"}])
    result = candidate_command(case, "--dry-run")
    assert result["ok"], result
    return result["data"]


def engineering_bytes(case):
    return {
        str(path): path.read_bytes()
        for path in case[0].parent.rglob("*")
        if path.is_file()
        and path.suffix in {".kicad_pcb", ".kicad_sch", ".kicad_mod", ".kicad_pro", ".kicad_dru"}
        and path.name != "review.kicad_pcb"
        and "driver-state" not in path.parts
    }


def test_legacy_preview_keeps_compatibility_and_no_implicit_candidate(case):
    before = engineering_bytes(case)
    result = command(case, "--dry-run")
    assert result["ok"], result
    assert "candidate_review" not in result["data"]["preview"]["extensions"]
    assert engineering_bytes(case) == before


def test_public_leaf_reference_includes_complete_candidate_schema(case):
    result = run(case, ["reference", "--command", "project sync"])
    assert result["ok"], result
    data = result["data"]
    assert data["command"]["candidate_review_schema"] == sync_candidate.SCHEMA
    schema = data["json_schemas"][sync_candidate.SCHEMA]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert "candidate" in schema["properties"]


def test_exact_materialized_public_bytes_source_schema_and_saved_receipt(case):
    edit(case[0], [{"op": "set", "ref": "R1", "value": "22k"}])
    before = engineering_bytes(case)
    result = candidate_command(case, "--dry-run")
    assert result["ok"], result
    data = result["data"]
    plan = data["preview"]
    review = plan["extensions"]["candidate_review"]
    raw = Path(review["candidate"]["path"]).read_bytes()
    assert engineering_bytes(case) == before
    assert (
        raw
        == sync_plan.prepare(sync_inputs.capture(case[0], case[0].with_suffix(".kicad_sch")))[
            "intended"
        ]
    )
    assert hashlib.sha256(raw).hexdigest() == review["candidate"]["sha256"]
    assert len(raw) == review["candidate"]["bytes"]
    assert review["candidate"]["sha256"] == plan["extensions"]["intended_sha256"]
    assert (review["operation_id"], review["plan_id"]) == (plan["operation_id"], plan["plan_id"])
    assert review["validity"]["expires_at_ref"] == "preview.expires_at"
    assert data["expires_at"] == plan["expires_at"]
    assert review["render_binding"]["status"] == "not_generated"
    companions = review["companions"]
    assert any(row["path"].endswith(".kicad_dru") and not row["present"] for row in companions)
    assert any(row["path"].endswith("edit_child.kicad_sch") for row in companions)
    assert {"candidate.path", "companions", "change_scope"} <= set(review["_untrusted"])
    output_paths = {row["path"] for row in plan["outputs"]}
    assert review["candidate"]["path"] in output_paths
    assert str(Path(review["manifest"]["path"]).parent) in output_paths
    with driver_runtime.create_cli(environ=case[1], home=case[0].parent) as cli:
        cli.validator.validate_schema(sync_candidate.SCHEMA, review, cli.adapter.sync.json_schemas)
        schema = cli.adapter.sync.json_schemas[sync_candidate.SCHEMA]
        assert set(review) == set(schema["properties"]) == set(schema["required"])
        assert schema["additionalProperties"] is False
        for field in (
            "candidate",
            "source",
            "board",
            "manifest",
            "confirmation_binding",
            "validity",
            "usage",
            "render_binding",
            "change_scope",
        ):
            child = schema["properties"][field]
            assert set(review[field]) == set(child["properties"]) == set(child["required"])
            assert child["additionalProperties"] is False
        invalid = copy.deepcopy(review)
        invalid["candidate"]["unchecked_path"] = "invented"
        with pytest.raises(ValueError):
            cli.validator.validate_schema(
                sync_candidate.SCHEMA, invalid, cli.adapter.sync.json_schemas
            )
    query = run(case, ["operation", "show", "--id", plan["operation_id"]])
    assert query["data"]["operation_status"]["plan"] == plan
    done = candidate_command(case, "--confirm", data["confirm_token"])
    assert done["ok"], done
    assert case[0].read_bytes() == raw
    assert done["data"]["receipt"]["extensions"]["candidate_review"] == review
    repeated = candidate_command(case, "--confirm", data["confirm_token"])
    assert repeated["error"]["code"] == "E_CONFLICT"
    assert repeated["error"]["details"]["conflict"] == "token_used"
    query = run(case, ["operation", "show", "--id", plan["operation_id"]])
    assert query["data"]["operation_status"]["receipt"] == done["data"]["receipt"]


@pytest.mark.parametrize("kind", ["missing", "bytes", "same-bytes", "manifest"])
def test_candidate_missing_changed_or_replaced_refuses_without_formal_writes(case, kind):
    data = planned(case)
    review = data["preview"]["extensions"]["candidate_review"]
    path = Path(review["candidate"]["path"])
    before = engineering_bytes(case)
    if kind == "missing":
        path.unlink()
    elif kind == "bytes":
        path.write_bytes(path.read_bytes() + b"\n")
    elif kind == "same-bytes":
        replacement = path.with_suffix(".replacement")
        replacement.write_bytes(path.read_bytes())
        os.replace(replacement, path)
    else:
        manifest = Path(review["manifest"]["path"])
        manifest.write_bytes(manifest.read_bytes() + b"\n")
    refused = candidate_command(case, "--confirm", data["confirm_token"])
    assert refused["error"]["code"] == "E_CONFLICT", refused
    details = refused["error"]["details"]
    assert details["dispatch_state"] == "not_started"
    assert (
        details["candidate_conflict"]
        == {
            "missing": "missing",
            "bytes": "identity_changed",
            "same-bytes": "identity_changed",
            "manifest": "manifest_changed",
        }[kind]
    )
    if kind == "same-bytes":
        assert details["bytes_unchanged"] is True
    assert engineering_bytes(case) == before
    assert not list(case[0].parent.glob("*.kicad-cli.*"))
    query = run(case, ["operation", "show", "--id", data["preview"]["operation_id"]])
    assert query["data"]["operation_status"]["status"] == "planned"


@pytest.mark.parametrize("kind", ["input-replaced", "input-missing", "rules", "config", "scope"])
def test_bound_inputs_rules_configuration_and_authority_still_guard_candidate_confirm(case, kind):
    data = planned(case)
    before_pcb = case[0].read_bytes()
    if kind.startswith("input-"):
        path = case[0].with_suffix(".kicad_sch")
        if kind == "input-missing":
            path.unlink()
        else:
            replacement = path.with_suffix(".replacement")
            replacement.write_bytes(path.read_bytes())
            os.replace(replacement, path)
    elif kind == "rules":
        case[0].with_suffix(".kicad_dru").write_text("(version 1)", encoding="utf-8")
    elif kind == "config":
        config = case[0].parent / "configuration/10.0"
        config.mkdir(exist_ok=True)
        (config / "kicad_common.json").write_text("{}", encoding="utf-8")
    else:
        path = Path(case[1]["KICAD_CLI_SCOPE_FILE"])
        scope = json.loads(path.read_bytes())
        scope["granted_by"] = "changed grant"
        path.write_text(json.dumps(scope), encoding="utf-8")
    refused = candidate_command(case, "--confirm", data["confirm_token"])
    assert refused["error"]["code"] == "E_CONFLICT", refused
    if kind == "input-replaced":
        assert refused["error"]["details"]["changed_file_details"][0]["bytes_unchanged"]
        assert (
            "identity" in refused["error"]["details"]["changed_file_details"][0]["changed_fields"]
        )
    assert case[0].read_bytes() == before_pcb
    assert not list(case[0].parent.glob("*.kicad-cli.*"))


@pytest.mark.parametrize(
    "kind", ["existing", "input", "relative", "parent-missing", "outside", "private-outside"]
)
def test_materialization_paths_are_owned_absent_and_scope_checked(case, tmp_path, kind):
    before = engineering_bytes(case)
    output = case[0].parent / "review.kicad_pcb"
    if kind == "existing":
        output.write_bytes(b"foreign existing output")
    elif kind == "input":
        output = case[0]
    elif kind == "relative":
        output = Path("relative-review.kicad_pcb")
    elif kind == "parent-missing":
        output = case[0].parent / "missing/review.kicad_pcb"
    elif kind == "outside":
        output = tmp_path.parent / (tmp_path.name + "-outside.kicad_pcb")
    else:
        # Authorize the formal PCB/candidate, but place private materialization outside it.
        case[1]["KICAD_CLI_STATE_DIR"] = str(tmp_path.parent / (tmp_path.name + "-state"))
    refused = candidate_command(case, "--dry-run", output=output)
    assert not refused["ok"], refused
    assert refused["error"]["code"] in {"E_CONFLICT", "E_FORBIDDEN", "E_IO"}, refused
    assert engineering_bytes(case) == before
    if kind == "existing":
        assert output.read_bytes() == b"foreign existing output"
    elif kind != "input":
        assert not output.exists()
    assert not list(case[0].parent.glob("*.kicad-cli.*"))


def test_candidate_rechecked_under_writer_lock_before_formal_persistence(case, monkeypatch):
    data = planned(case)
    before = case[0].read_bytes()
    candidate = Path(data["preview"]["extensions"]["candidate_review"]["candidate"]["path"])
    original = document_transaction.DocumentTransaction.arm

    def drift_after_arm(transaction):
        original(transaction)
        candidate.write_bytes(candidate.read_bytes() + b"\n")

    monkeypatch.setattr(document_transaction.DocumentTransaction, "arm", drift_after_arm)
    refused = candidate_command(case, "--confirm", data["confirm_token"])
    assert not refused["ok"], refused
    assert refused["error"]["details"]["receipt"]["extensions"]["failure_code"] == "E_CONFLICT"
    assert case[0].read_bytes() == before


def test_expired_parent_token_keeps_readable_evidence_but_cannot_confirm(case):
    now = datetime(2026, 10, 19, 4, 0, tzinfo=timezone.utc)
    result = candidate_command(case, "--dry-run", now=lambda: now)
    assert result["ok"], result
    data = result["data"]
    candidate = Path(data["preview"]["extensions"]["candidate_review"]["candidate"]["path"])
    before = case[0].read_bytes()
    refused = candidate_command(
        case, "--confirm", data["confirm_token"], now=lambda: now + timedelta(minutes=11)
    )
    assert refused["error"]["code"] == "E_CONFLICT", refused
    assert refused["error"]["details"]["conflict"] == "token_expired"
    assert candidate.is_file() and case[0].read_bytes() == before


def test_legacy_fresh_preview_key_replay_keeps_original_sdk_semantics(case):
    original = command(case, "--dry-run")
    assert original["ok"], original
    done = command(
        case, "--confirm", original["data"]["confirm_token"], "--idempotency-key", "one-sync-intent"
    )
    assert done["ok"], done
    before = case[0].read_bytes()
    fresh = command(case, "--dry-run")
    assert fresh["ok"], fresh
    replay = command(
        case, "--confirm", fresh["data"]["confirm_token"], "--idempotency-key", "one-sync-intent"
    )
    assert replay["ok"] and replay["data"]["receipt"]["replayed"], replay
    assert replay["data"]["receipt"]["operation_id"] == done["data"]["receipt"]["operation_id"]
    assert case[0].read_bytes() == before


def test_no_clobber_publication_race_preserves_foreign_output(case, monkeypatch):
    output = case[0].parent / "review.kicad_pcb"
    original = candidate_output.File.publish

    def publish(opened, name):
        if opened.parent.path / name == output:
            output.write_bytes(b"external winner")
        return original(opened, name)

    monkeypatch.setattr(candidate_output.File, "publish", publish)
    before = engineering_bytes(case)
    refused = candidate_command(case, "--dry-run")
    assert refused["error"]["code"] == "E_CONFLICT", refused
    assert refused["error"]["details"]["candidate_conflict"] == "already_exists"
    assert output.read_bytes() == b"external winner"
    assert engineering_bytes(case) == before


def test_failed_manifest_publication_removes_only_own_complete_candidate(case, monkeypatch):
    original = document_transaction.persist

    def fail_manifest(path, value, key):
        if Path(path).name == "manifest.json":
            raise OSError("fixture refusal before candidate manifest publication")
        return original(path, value, key)

    monkeypatch.setattr(document_transaction, "persist", fail_manifest)
    before = engineering_bytes(case)
    refused = candidate_command(case, "--dry-run")
    assert refused["error"]["code"] == "E_IO", refused
    assert engineering_bytes(case) == before
    assert not (case[0].parent / "review.kicad_pcb").exists()
    assert list((case[0].parent / "driver-state/sync-candidates").glob("*/preparation.json"))


def test_materialization_temporary_is_scoped_and_published_in_destination_parent(case, monkeypatch):
    output = case[0].parent / "review.kicad_pcb"
    original = candidate_output.File.publish
    observed = []

    def publish(opened, name):
        if opened.parent.path / name == output:
            observed.append(opened.parent.path / opened.name)
        return original(opened, name)

    monkeypatch.setattr(candidate_output.File, "publish", publish)
    data = planned(case)
    assert len(observed) == 1
    temporary = observed[0]
    plan = data["preview"]
    assert {"path": str(temporary), "effect": "persist", "exists": False} in plan["outputs"]
    assert not temporary.exists()
    review = plan["extensions"]["candidate_review"]
    prep_path = Path(review["manifest"]["path"]).with_name("preparation.json")
    with driver_runtime.create_cli(environ=case[1], home=case[0].parent) as cli:
        preparation = document_transaction.authenticated(prep_path, cli._store_open().secret())
    assert preparation["temporary"]["path"] == str(temporary)
    assert preparation["temporary"]["sha256"] == review["candidate"]["sha256"]
    assert preparation["temporary"]["publication"] == "same_parent_no_clobber"


@pytest.mark.parametrize("race", [False, True])
def test_existing_temporary_is_never_overwritten_or_deleted(case, monkeypatch, race):
    output = case[0].parent / "review.kicad_pcb"
    temporary = output.with_name(".preoccupied-sync-candidate.pending")
    monkeypatch.setattr(sync_candidate, "temporary_path", lambda *_: temporary)
    if race:
        original = candidate_output.Directory.create

        def occupy_before_exclusive_write(parent, name):
            assert parent.path / name == temporary
            temporary.write_bytes(b"foreign temporary")
            return original(parent, name)

        monkeypatch.setattr(candidate_output.Directory, "create", occupy_before_exclusive_write)
    else:
        temporary.write_bytes(b"foreign temporary")
    before = engineering_bytes(case)
    refused = candidate_command(case, "--dry-run")
    assert refused["error"]["code"] == "E_CONFLICT", refused
    assert refused["error"]["details"]["candidate_conflict"] == "temporary_exists"
    assert temporary.read_bytes() == b"foreign temporary"
    assert not output.exists()
    assert engineering_bytes(case) == before


def test_failed_preparation_blocks_temporary_replacement_and_preserves_foreign_file(
    case, monkeypatch
):
    output = case[0].parent / "review.kicad_pcb"
    temporary = output.with_name(".replaced-sync-candidate.pending")
    monkeypatch.setattr(sync_candidate, "temporary_path", lambda *_: temporary)
    original = document_transaction.persist
    replacement = temporary.with_name(".foreign-replacement")

    def replace_after_identity_capture(path, value, key):
        if Path(path).name == "preparation.json" and "identity" in value["temporary"]:
            replacement.write_bytes(b"foreign file")
            with pytest.raises(OSError):
                os.replace(replacement, temporary)
            raise OSError("fixture failure after blocked foreign temporary replacement")
        return original(path, value, key)

    monkeypatch.setattr(document_transaction, "persist", replace_after_identity_capture)
    before = engineering_bytes(case)
    refused = candidate_command(case, "--dry-run")
    assert refused["error"]["code"] == "E_IO", refused
    assert replacement.read_bytes() == b"foreign file"
    assert not temporary.exists() and not output.exists()
    assert engineering_bytes(case) == before
