"""Actual saved-file zone effects, scope binding, findings and durable recovery.

These are isolated fixture tests, not official KiCad/native acceptance.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from driver_test_utils import grant
from test_native_pour import BOARD

from kicad_cli import driver_runtime, zone_plan, zone_readback
from kicad_cli.fileformat.board import Board
from kicad_cli.fileformat.sexpr import Document
from kicad_cli.zone_driver_adapter import ZoneAdapter

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def case(tmp_path):
    path = tmp_path / "zones.kicad_pcb"
    path.write_text(BOARD, encoding="utf-8")
    env = dict(os.environ, **grant(tmp_path), PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    return path, env


def run(case, argv, **options):
    path, env = case
    with driver_runtime.create_cli(environ=env, home=path.parent, **options) as cli:
        output = cli.run(argv)
        assert output.envelope is not None, output.stdout
        cli.validator.validate("response", output.envelope)
        return output.envelope


def command(case, task, *flags, **options):
    extra = ["--net", "GND", "--layer", "F.Cu"] if task == "create" else []
    return run(case, ["board", "zone", task, "--board", str(case[0]), *extra, *flags], **options)


def apply(case, task, *flags):
    planned = command(case, task, *flags, "--dry-run")
    assert planned["ok"], planned
    result = command(case, task, *flags, "--confirm", planned["data"]["confirm_token"])
    assert result["ok"], result
    return result["data"]["receipt"]


def test_zone_preparation_is_memory_only_and_saved_consumer_detects_missing_and_changed_fields(
    case,
):
    path = case[0]
    before = path.read_bytes()
    board, selected, _ = zone_plan.create(
        Board.load(path), {"net": "GND", "layer": "F.Cu"}, hashlib.sha256(before).hexdigest()
    )
    assert path.read_bytes() == before
    raw = board.document.dumps().encode()
    actual, protected, _ = zone_readback.inspect_bytes(raw, selected)
    assert (
        actual[0]["net"] == "GND" and not actual[0]["filled"] and actual[0]["filled_polygons"] == 0
    )
    prior = zone_readback.observe(path, selected, allow_missing=True)
    assert prior.objects == [] and prior.protected_sha256 == protected
    changed, _, _ = zone_readback.inspect_bytes(
        raw.replace(b"(min_thickness 0.25)", b"(min_thickness 0.35)"), selected
    )
    assert changed[0]["definition_sha256"] != actual[0]["definition_sha256"]
    with pytest.raises(zone_readback.ReadbackError, match="absent"):
        zone_readback.observe(path, selected)
    with pytest.raises(zone_readback.ReadbackError, match="fill state"):
        zone_readback.inspect_bytes(raw.replace(b"(thermal_gap", b"no (thermal_gap", 1), selected)


def test_empty_all_refill_is_a_verified_unchanged_board_task(case):
    before = case[0].read_bytes()
    receipt = apply(case, "refill", "--all")
    assert receipt["outcome"]["change"] == "not_applied"
    assert receipt["outcome"]["persistence"] == "unchanged"
    assert receipt["outcome"]["verification"]["status"] == "passed"
    assert receipt["extensions"]["observed"] == [] and case[0].read_bytes() == before


@pytest.mark.parametrize("kind", ["absent", "stroked"])
def test_legacy_fill_marker_is_refused_without_changing_selected_definition(case, kind):
    identity = apply(case, "create")["extensions"]["observed"][0]["id"]
    raw = case[0].read_bytes()
    marker = b"(filled_areas_thickness no)"
    assert marker in raw
    case[0].write_bytes(
        raw.replace(marker, b"" if kind == "absent" else b"(filled_areas_thickness yes)")
    )
    before = case[0].read_bytes()
    refused = command(case, "refill", "--zones", identity, "--dry-run")
    assert refused["error"]["code"] == "E_VALIDATION", refused
    assert refused["error"]["details"]["zones"] == [identity]
    assert case[0].read_bytes() == before and not list(case[0].parent.glob("*.kicad-cli.*"))


@pytest.mark.parametrize("kind", ["scope-change", "revoked", "plan-tamper", "cross-task"])
def test_zone_authorization_and_confirmation_counterexamples_leave_board_unchanged(case, kind):
    plan = command(case, "create", "--dry-run")
    assert plan["ok"], plan
    before = case[0].read_bytes()
    token = plan["data"]["confirm_token"]
    if kind in {"scope-change", "revoked"}:
        path = Path(case[1]["KICAD_CLI_SCOPE_FILE"])
        scope = json.loads(path.read_bytes())
        scope["notes" if kind == "scope-change" else "revoked"] = (
            "changed" if kind == "scope-change" else True
        )
        path.write_text(json.dumps(scope), encoding="utf-8")
    elif kind == "plan-tamper":
        with driver_runtime.create_cli(environ=case[1], home=case[0].parent) as cli:
            operation = plan["data"]["preview"]["operation_id"]
            stored = cli._record(operation)["plan"]
            stored["extensions"]["params"]["margin"] = 1.0
            cli._store_open().connection().execute(
                "UPDATE operations SET plan = ? WHERE operation_id = ?",
                (json.dumps(stored), operation),
            )
    result = (
        command(case, "refill", "--all", "--confirm", token)
        if kind == "cross-task"
        else command(case, "create", "--confirm", token)
    )
    assert result["error"]["code"] in {"E_CONFLICT", "E_FORBIDDEN"}, result
    assert case[0].read_bytes() == before and not list(case[0].parent.glob("*.kicad-cli.*"))


def test_refill_planning_refuses_changed_thermal_settings_before_dispatch(case, monkeypatch):
    identity = apply(case, "create")["extensions"]["observed"][0]["id"]
    from kicad_cli.fileformat import zonefill

    original = zonefill.write

    def wrong(fill):
        original(fill)
        fill.zone.node.find("fill").find("thermal_gap").set(1, "0.75")

    monkeypatch.setattr(zonefill, "write", wrong)
    before = case[0].read_bytes()
    refused = command(case, "refill", "--zones", identity, "--dry-run")
    assert refused["error"]["code"] == "E_VALIDATION", refused
    assert case[0].read_bytes() == before


def test_create_refill_plan_actual_readback_and_durable_query(case):
    before = case[0].read_bytes()
    plan = command(case, "create", "--dry-run")
    assert plan["ok"] and case[0].read_bytes() == before
    assert not list(case[0].parent.glob("*.kicad-cli.*"))
    saved = command(case, "create", "--confirm", plan["data"]["confirm_token"])
    assert saved["ok"], saved
    receipt = saved["data"]["receipt"]
    actual = receipt["extensions"]["observed"][0]
    assert actual["net"] == "GND" and actual["filled"] is False
    assert receipt["snapshot_after"]["revision"] == hashlib.sha256(case[0].read_bytes()).hexdigest()
    verification = receipt["outcome"]["verification"]
    assert verification["status"] == "passed" and verification["coverage"] == "complete"
    assert (
        receipt["extensions"]["finding_comparison"]["final"]["provenance"]["source"][
            "official_native"
        ]
        is False
    )
    zones = Board.load(case[0]).zones
    assert len(zones) == 1 and zones[0].filled == {}
    filled = apply(case, "refill", "--zones", actual["id"])
    observed = filled["extensions"]["observed"][0]
    assert observed["filled"] and observed["filled_polygons"] > 0
    assert observed["definition_sha256"] == actual["definition_sha256"]
    status = run(case, ["operation", "show", "--id", filled["operation_id"]])["data"][
        "operation_status"
    ]
    assert status["status"] == "succeeded" and status["receipt"] == filled


@pytest.mark.parametrize("dependency", [".kicad_pro", ".kicad_dru"])
@pytest.mark.parametrize("change", ["appear", "change", "disappear"])
def test_project_or_rule_dependency_drift_is_refused_before_dispatch(case, dependency, change):
    path = case[0].with_suffix(dependency)
    content = b"{}" if dependency == ".kicad_pro" else b"(version 1)"
    if change != "appear":
        path.write_bytes(content)
    plan = command(case, "create", "--dry-run")
    assert plan["ok"], plan
    before = case[0].read_bytes()
    if change == "disappear":
        path.unlink()
    else:
        path.write_bytes(content + b"\n")
    refused = command(case, "create", "--confirm", plan["data"]["confirm_token"])
    assert (
        refused["error"]["code"] == "E_CONFLICT"
        and refused["error"]["details"]["dispatch_state"] == "not_started"
    )
    assert case[0].read_bytes() == before
    status = run(case, ["operation", "show", "--id", plan["data"]["preview"]["operation_id"]])[
        "data"
    ]["operation_status"]
    assert status["status"] == "planned" and not list(case[0].parent.glob("*.kicad-cli.*"))


@pytest.mark.parametrize(
    "flags", [[], ["--all=false"], ["--all", "--zones", "x"], ["--zones", ","], ["--zones", "x,x"]]
)
def test_invalid_zone_selection_leaves_file_unchanged(case, flags):
    before = case[0].read_bytes()
    result = command(case, "refill", *flags, "--dry-run")
    assert not result["ok"] and result["error"]["code"] in {"E_USAGE", "E_VALIDATION"}, result
    assert case[0].read_bytes() == before


def test_selected_refill_preserves_another_unfilled_zone(case):
    first = apply(case, "create")["extensions"]["observed"][0]
    planned = command(case, "create", "--layer", "B.Cu", "--dry-run")
    # Repeated domain flags are invalid; call the explicit alternate command instead.
    assert planned["error"]["code"] == "E_USAGE"
    argv = ["board", "zone", "create", "--board", str(case[0]), "--net", "GND", "--layer", "B.Cu"]
    plan = run(case, [*argv, "--dry-run"])
    assert plan["ok"], plan
    created = run(case, [*argv, "--confirm", plan["data"]["confirm_token"]])
    assert created["ok"], created
    second = created["data"]["receipt"]["extensions"]["observed"][0]
    before_second = zone_readback.observe(case[0], [second["id"]]).objects
    apply(case, "refill", "--zones", first["id"])
    assert zone_readback.observe(case[0], [second["id"]]).objects == before_second


@pytest.mark.parametrize("fault", ["ignore_save", "new_finding", "lost_check", "external_edit"])
def test_failure_reports_real_restoration_or_preserves_unrecognized_bytes(case, monkeypatch, fault):
    plan = command(case, "create", "--dry-run")
    assert plan["ok"], plan
    before = case[0].read_bytes()
    if fault == "ignore_save":
        monkeypatch.setattr(Document, "save", lambda *_args: None)
    elif fault == "new_finding":
        from kicad_cli import drc_baseline

        compare = drc_baseline.compare_records
        monkeypatch.setattr(
            drc_baseline,
            "compare_records",
            lambda *args: dict(compare(*args), status="failed", new_findings=[{"id": "injected"}]),
        )
    elif fault == "lost_check":

        def unavailable(*_args):
            raise OSError("injected saved-check loss")

        monkeypatch.setattr(ZoneAdapter, "_capture", unavailable)
    else:
        save = Document.save

        def foreign(document, path):
            save(document, path)
            path.write_bytes(path.read_bytes().replace(b"(thickness 1.6)", b"(thickness 1.7)"))

        monkeypatch.setattr(Document, "save", foreign)
    result = command(case, "create", "--confirm", plan["data"]["confirm_token"])
    assert not result["ok"], result
    receipt = result["error"]["details"]["receipt"]
    if fault == "external_edit":
        assert result["error"]["code"] == "E_OUTCOME_UNKNOWN"
        assert b"(thickness 1.7)" in case[0].read_bytes()
        assert Path(str(case[0]) + ".kicad-cli.backup").read_bytes() == before
        assert receipt["outcome"]["change"] == "unknown"
    else:
        assert receipt["extensions"]["restoration"]["matches_baseline"] is True
        assert case[0].read_bytes() == before and not list(case[0].parent.glob("*.kicad-cli.*"))


@pytest.mark.parametrize("stage", ["before_save", "after_save"])
def test_killed_zone_writer_blocks_move_and_resolves_from_authenticated_current_observation(
    case, stage
):
    planned = command(case, "create", "--dry-run")
    assert planned["ok"], planned
    data = planned["data"]
    script = (
        "import os\nfrom kicad_cli.fileformat.sexpr import Document\n"
        "original=Document.save\ndef saved(self,path=None):\n"
        + (" os._exit(91)\n" if stage == "before_save" else " original(self,path)\n os._exit(91)\n")
        + "Document.save=saved\nfrom kicad_cli.main import main\nmain(__import__('sys').argv[1:])"
    )
    argv = [
        sys.executable,
        "-B",
        "-c",
        script,
        "board",
        "zone",
        "create",
        "--board",
        str(case[0]),
        "--net",
        "GND",
        "--layer",
        "F.Cu",
        "--confirm",
        data["confirm_token"],
    ]
    process = subprocess.run(argv, cwd=REPO, env=case[1], capture_output=True, timeout=30)
    assert process.returncode == 91 and not process.stdout, process.stderr

    def future():
        return datetime.now(timezone.utc) + timedelta(seconds=40)

    op = data["preview"]["operation_id"]
    status = run(case, ["operation", "show", "--id", op], now=future)["data"]["operation_status"]
    readback = status["extensions"]["recovery_readback"]
    outcome = "not_applied" if stage == "before_save" else "applied"
    assert status["status"] == "unknown" and readback["outcome"] == outcome, readback
    reference = Board.load(case[0]).footprints[0].reference
    move = run(
        case,
        [
            "board",
            "footprint",
            "move",
            "--board",
            str(case[0]),
            "--moves",
            reference + ":20,20",
            "--dry-run",
        ],
        now=future,
    )
    assert move["error"]["code"] == "E_CONFLICT", move
    assert move["error"]["details"]["active_operation"] == op
    before = case[0].read_bytes()
    resolved = run(
        case,
        [
            "operation",
            "resolve",
            "--id",
            op,
            "--outcome",
            outcome,
            "--evidence",
            readback["evidence_file"],
        ],
        now=future,
    )
    assert resolved["ok"], resolved
    assert case[0].read_bytes() == before and not list(case[0].parent.glob("*.kicad-cli.*"))
