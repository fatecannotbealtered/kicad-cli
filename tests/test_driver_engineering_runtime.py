"""Public parsing/confirmation/save/recovery on disposable documents.

Fault shims enter main() with normal public argv; they never call an adapter
directly. Actual native and frozen cases are a separate delivery gate.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from driver_test_utils import grant

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests/fixtures/sch_edit"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cli(env, *argv, shim=None):
    args = [sys.executable, "-B", "-m", "kicad_cli.main"]
    if shim:
        args = [sys.executable, "-B", "-c", shim]
    process = subprocess.run(
        args + list(argv) + ["--compact"], cwd=REPO, env=env, capture_output=True, timeout=60
    )
    return process, json.loads(process.stdout) if process.stdout else None


@pytest.fixture
def schematic_case(tmp_path):
    shutil.copytree(
        FIXTURE, tmp_path, dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.py", "__pycache__")
    )
    changes = tmp_path / "changes.json"
    changes.write_text(
        json.dumps({"changes": [{"op": "set", "ref": "R1", "value": "12k"}]}), encoding="utf-8"
    )
    env = dict(os.environ, **grant(tmp_path), PYTHONIOENCODING="utf-8")
    return (
        tmp_path,
        env,
        ["sch", "edit", "--schematic", str(tmp_path / "edit.kicad_sch"), "--changes", str(changes)],
    )


def test_discovery_is_complete_runtime():
    process, value = cli(os.environ, "reference")
    assert process.returncode == 0 and value["ok"]
    commands = value["data"]["commands"]
    from kicad_cli.driver_runtime import MIGRATED

    paths = [command["path"] for command in commands]
    assert len(paths) == len(set(paths))
    assert set(paths) == MIGRATED
    assert {c["path"] for c in commands} >= {
        "sch create",
        "sch edit",
        "board zone stitch",
        "session inspect",
    }
    assert all("driver" in c for c in commands)
    assert value["data"]["release_readiness"]["level"] == "unpublishable"
    from kicad_cli import __version__

    delivery = value["data"]["extensions"]["internal_delivery"]
    assert delivery["series"] == delivery["product_version"] == __version__
    assert delivery["tag"] == f"v{__version__}"


def test_discovery_selector_and_changelog_are_actual():
    _, value = cli(os.environ, "reference", "--command", "sch edit")
    assert value["data"]["command"]["path"] == "sch edit"
    _, log = cli(os.environ, "changelog")
    assert log["data"]["count"] > 0
    process, invalid = cli(os.environ, "changelog", "--since", "invalid")
    assert process.returncode == 2 and invalid["error"]["code"] == "E_VALIDATION"


def test_context_and_doctor_create_no_engineering_operations(tmp_path):
    env = dict(os.environ, KICAD_CLI_STATE_DIR=str(tmp_path / "not-created"))
    _, value = cli(env, "context")
    assert value["ok"] and value["data"]["environment"]["components"]
    import sqlite3

    with sqlite3.connect(tmp_path / "not-created/operations.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0
    _, doctor = cli(env, "doctor")
    assert any(c["check"] == "native_backend" for c in doctor["data"]["checks"])
    with sqlite3.connect(tmp_path / "not-created/operations.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0


def test_edit_receipt_and_exact_key_replay(schematic_case):
    root, env, args = schematic_case
    board = root / "edit.kicad_pcb"
    board_before = sha(board)
    before = sha(root / "edit.kicad_sch")
    _, preview = cli(env, *args, "--dry-run")
    assert preview["ok"], preview
    token = preview["data"]["confirm_token"]
    process, value = cli(env, *args, "--confirm", token, "--idempotency-key", "edit-test")
    assert process.returncode == 0 and value["ok"], value
    receipt = value["data"]["receipt"]
    assert receipt["outcome"]["verification"]["status"] == "passed"
    assert sha(root / "edit.kicad_sch") != before and sha(board) == board_before
    saved = sha(root / "edit.kicad_sch")
    process, replay = cli(env, *args, "--confirm", token, "--idempotency-key", "edit-test")
    assert process.returncode == 0 and replay["data"]["receipt"].pop("replayed") is True
    assert replay["data"]["receipt"] == receipt
    assert sha(root / "edit.kicad_sch") == saved
    _, wrong_key = cli(env, *args, "--confirm", token, "--idempotency-key", "another")
    assert not wrong_key["ok"] and sha(root / "edit.kicad_sch") == saved


@pytest.mark.parametrize("drift", ["document", "changes", "scope", "candidate"])
def test_stale_edit_is_refused_without_saving(schematic_case, drift):
    root, env, args = schematic_case
    _, preview = cli(env, *args, "--dry-run")
    assert preview["ok"], preview
    if drift == "scope":
        path = Path(env["KICAD_CLI_SCOPE_FILE"])
        data = json.loads(path.read_bytes())
        data["notes"] += " changed grant"
        path.write_text(json.dumps(data), encoding="utf-8")
    elif drift == "candidate":
        path = Path(preview["data"]["preview"]["extensions"]["files"][0]["candidate"])
        path.write_bytes(path.read_bytes() + b"\n")
    else:
        path = root / ("edit.kicad_sch" if drift == "document" else "changes.json")
        path.write_bytes(path.read_bytes() + b"\n")
    saved = sha(root / "edit.kicad_sch")
    process, value = cli(env, *args, "--confirm", preview["data"]["confirm_token"])
    assert process.returncode == 6 and not value["ok"], value
    assert value["error"]["details"]["dispatch_state"] == "not_started"
    assert sha(root / "edit.kicad_sch") == saved


def test_existing_creation_target_is_protected(schematic_case):
    root, env, _ = schematic_case
    spec = root / "edit.json"
    spec.write_text('{"parts": [], "nets": []}', encoding="utf-8")
    before = sha(root / "edit.kicad_sch")
    process, value = cli(env, "sch", "create", "--spec", str(spec), "--out", str(root), "--dry-run")
    assert process.returncode == 6 and value["error"]["code"] == "E_CONFLICT", value
    assert sha(root / "edit.kicad_sch") == before


@pytest.mark.parametrize("when", ["before", "after"])
def test_public_save_failure_restores_observed_bytes(schematic_case, when):
    root, env, args = schematic_case
    _, preview = cli(env, *args, "--dry-run")
    token = preview["data"]["confirm_token"]
    before = sha(root / "edit.kicad_sch")
    shim = (
        "import os,sys; from kicad_cli.main import main; original=os.replace; "
        "target=sys.argv[sys.argv.index('--schematic')+1];\n"
        "def replace(a,b):\n"
        " if str(b)==target and str(a).endswith('.pending'):\n"
        + (
            "  raise PermissionError('owned fault before save')\n"
            if when == "before"
            else "  original(a,b); raise OSError('owned fault after save')\n"
        )
        + " return original(a,b)\n"
        "os.replace=replace; main(sys.argv[1:])\n"
    )
    process, value = cli(env, *args, "--confirm", token, shim=shim)
    assert process.returncode != 0 and not value["ok"], value
    receipt = value["error"]["details"]["receipt"]
    assert receipt["extensions"]["restore_observed"] is True
    assert sha(root / "edit.kicad_sch") == before
    assert not list(root.glob("*.kicad-cli.lock"))


def test_public_restore_refusal_is_unknown_and_preserves_backups(schematic_case):
    root, env, args = schematic_case
    _, preview = cli(env, *args, "--dry-run")
    token = preview["data"]["confirm_token"]
    before = sha(root / "edit.kicad_sch")
    shim = (
        "import os,sys; from kicad_cli.main import main; original=os.replace; "
        "target=sys.argv[sys.argv.index('--schematic')+1];\n"
        "def replace(a,b):\n"
        " if str(b)==target:\n"
        "  if str(a).endswith('.pending'): original(a,b); raise OSError('owned post-save fault')\n"
        "  raise PermissionError('owned restore refusal')\n"
        " return original(a,b)\n"
        "os.replace=replace; main(sys.argv[1:])\n"
    )
    process, value = cli(env, *args, "--confirm", token, shim=shim)
    assert process.returncode == 6 and not value["ok"], value
    assert sha(root / "edit.kicad_sch") != before
    assert list(root.glob("*.backup")) and list(root.glob("*.kicad-cli.journal"))
    operation = preview["data"]["preview"]["operation_id"]
    _, shown = cli(env, "operation", "show", "--id", operation)
    observed = shown["data"]["operation_status"]["extensions"]["recovery_readback"]
    assert observed["outcome"] == "applied"
    # This rejects stale/tampered recovery without interpreting rejection as restoration.
    evidence = Path(observed["evidence_file"])
    evidence.write_bytes(evidence.read_bytes() + b" ")
    _, refused = cli(
        env,
        "operation",
        "resolve",
        "--id",
        operation,
        "--outcome",
        "not_applied",
        "--evidence",
        str(evidence),
    )
    assert not refused["ok"] and sha(root / "edit.kicad_sch") != before


def test_ampacity_reports_actual_width_and_assumptions(tmp_path):
    from test_driver_power_runtime import make_case

    board, env = make_case(tmp_path)
    process, value = cli(
        env, "board", "analyze", "ampacity", "--board", str(board), "--oz", "2", "--dt", "20"
    )
    assert process.returncode == 0 and value["ok"], value
    assert value["data"]["model"]["copper_oz"] == 2
    assert value["data"]["model"]["delta_t_k"] == 20
    assert {t["width_nm"] for t in value["data"]["tracks"]} == {200000}
    assert all(t["estimated_current_a"] > 0 for t in value["data"]["tracks"])


def test_legacy_unsafe_flags_are_not_accepted(schematic_case):
    _root, env, args = schematic_case
    process, value = cli(env, *args, "--dry-run", "--ignore-lock")
    assert process.returncode == 2 and value["error"]["code"] == "E_USAGE"


def test_partial_document_set_lost_reply_and_authenticated_resolution(schematic_case):
    root, env, args = schematic_case
    (root / "changes.json").write_text(
        json.dumps(
            {
                "changes": [
                    {"op": "set", "ref": "R1", "value": "12k"},
                    {"op": "set", "ref": "R10", "value": "33k"},
                ]
            }
        ),
        encoding="utf-8",
    )
    _, preview = cli(env, *args, "--dry-run")
    assert preview["ok"], preview
    plan = preview["data"]["preview"]
    assert len(plan["extensions"]["files"]) == 2
    shim = (
        "import os,sys; from kicad_cli.main import main; original=os.replace;\n"
        "def replace(a,b):\n"
        " original(a,b)\n"
        " if str(a).endswith('.pending') and str(b).endswith('.kicad_sch'): os._exit(93)\n"
        "os.replace=replace; main(sys.argv[1:])\n"
    )
    process, value = cli(env, *args, "--confirm", preview["data"]["confirm_token"], shim=shim)
    assert process.returncode == 93 and value is None
    rows = plan["extensions"]["files"]
    assert sha(rows[0]["path"]) == rows[0]["intended"]
    assert sha(rows[1]["path"]) == rows[1]["baseline"]
    assert list(root.glob("*.backup")) and list(root.glob("*.kicad-cli.journal"))
    time.sleep(31)  # Real SDK lease, not a fabricated operation timestamp.
    operation = plan["operation_id"]
    _, shown = cli(env, "operation", "show", "--id", operation)
    observed = shown["data"]["operation_status"]["extensions"]["recovery_readback"]
    assert observed["outcome"] == "partial"
    hashes = [sha(r["path"]) for r in rows]
    process, resolved = cli(
        env,
        "operation",
        "resolve",
        "--id",
        operation,
        "--outcome",
        "partial",
        "--evidence",
        observed["evidence_file"],
    )
    assert process.returncode == 0 and resolved["ok"], resolved
    assert [sha(r["path"]) for r in rows] == hashes
    assert len(list((root / "driver-state/recovery" / operation).glob("baseline-*"))) == 2
    assert not list(root.glob("*.kicad-cli.lock"))
    _, continuing = cli(env, *args, "--dry-run")
    assert continuing["ok"], continuing
