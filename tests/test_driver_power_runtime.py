"""Actual saved copper/project effects and operation recovery on owned fixtures.

Custom protocol/geometry tests are not official native KiCad acceptance. The
separate delivery runner uses the real SDK/DRC and final EXE on ATmega inputs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from driver_test_utils import grant

from kicad_cli import drc_baseline, driver_runtime, power_readback, power_rules
from kicad_cli.move_transaction import MoveTransaction, paths
from kicad_cli.power_driver_adapter import COMMANDS, PowerAdapter


def uid(name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "kicad-power-test/" + name))


def make_case(directory, *, routed=True):
    directory.mkdir(exist_ok=True)
    board = directory / "power.kicad_pcb"
    footprints = []
    for ref, x in [("J1", 10), ("J2", 20)]:
        pads = []
        for pin, y, net in [("1", 0, "VIN"), ("2", 6, "OTHER")]:
            pads.append(
                f'(pad "{pin}" smd rect (at 0 {y}) (size 1 1) '
                f'(layers "F.Cu" "F.Paste" "F.Mask") '
                f'(net {1 if net == "VIN" else 2} "{net}") (uuid "{uid(ref + pin)}"))'
            )
        footprints.append(
            f'(footprint "Test:Power" (layer "F.Cu") (uuid "{uid(ref)}") (at {x} 10) '
            f'(fp_text reference "{ref}" (at 0 -2) (layer "F.SilkS") '
            "(effects (font (size 1 1) (thickness 0.15)))) "
            '(fp_text value "Power" (at 0 -3) (layer "F.Fab") '
            f"(effects (font (size 1 1) (thickness 0.15)))) {' '.join(pads)})"
        )
    edges = []
    for a, b in [((5, 5), (25, 5)), ((25, 5), (25, 20)), ((25, 20), (5, 20)), ((5, 20), (5, 5))]:
        edges.append(
            f"(gr_line (start {a[0]} {a[1]}) (end {b[0]} {b[1]}) "
            f'(stroke (width 0.05) (type default)) (layer "Edge.Cuts") '
            f'(uuid "{uid(str(a) + str(b))}"))'
        )
    copper = [
        '(segment (start 10 16) (end 20 16) (width 0.2) (layer "F.Cu") '
        f'(net 2) (uuid "{uid("signal")}"))'
    ]
    if routed:
        copper.append(
            '(segment (start 10 10) (end 20 10) (width 0.2) (layer "F.Cu") '
            f'(net 1) (uuid "{uid("power")}"))'
        )
    board.write_text(
        '(kicad_pcb (version 20240108) (generator "power-test") (general (thickness 1.6)) '
        '(layers (0 "F.Cu" signal) (31 "B.Cu" signal) (35 "F.Paste" user) '
        '(37 "F.SilkS" user) (39 "F.Mask" user) (44 "Edge.Cuts" user) (49 "F.Fab" user)) '
        '(setup (pad_to_mask_clearance 0)) (net 0 "") (net 1 "VIN") (net 2 "OTHER") '
        + " ".join([*footprints, *edges, *copper])
        + ")\n",
        encoding="utf-8",
    )
    board.with_suffix(".kicad_pro").write_text(
        json.dumps(
            {
                "net_settings": {
                    "classes": [
                        {
                            "name": "Default",
                            "track_width": 0.2,
                            "clearance": 0.2,
                            "via_diameter": 0.6,
                            "via_drill": 0.3,
                        }
                    ],
                    "netclass_patterns": [],
                    "netclass_assignments": None,
                }
            }
        ),
        encoding="utf-8",
    )
    return board, dict(os.environ, **grant(directory), PYTHONIOENCODING="utf-8")


@pytest.fixture
def case(tmp_path):
    return make_case(tmp_path)


def run(case, task, *flags, **options):
    board, env = case
    with driver_runtime.create_cli(environ=env, home=board.parent, **options) as cli:
        result = cli.run([*task.split(), "--board", str(board), *flags])
        assert result.envelope is not None, result.stdout
        cli.validator.validate("response", result.envelope)
        return result.envelope


def management(case, *args, **options):
    with driver_runtime.create_cli(environ=case[1], **options) as cli:
        output = cli.run(["operation", *args])
        cli.validator.validate("response", output.envelope)
        return output.envelope


def planned(case, task, *flags, **options):
    result = run(case, task, *flags, "--dry-run", **options)
    assert result["ok"], result
    return result["data"]


def confirmed(case, task, *flags):
    data = planned(case, task, *flags)
    assert data["preview"]["can_apply"], data["preview"]["constraints"]
    result = run(case, task, *flags, "--confirm", data["confirm_token"])
    assert result["ok"], result
    return result["data"]["receipt"]


CLASS = (
    "--name",
    "Power",
    "--nets",
    "VIN",
    "--width",
    "0.3",
    "--clearance",
    "0.2",
    "--via-diameter",
    "0.6",
    "--via-drill",
    "0.3",
)


def test_netclass_changes_project_and_keeps_actual_copper(case):
    before = power_readback.observe(case[0], ["VIN"])
    plan = planned(case, "project netclass set", *CLASS)
    assert power_readback.observe(case[0], ["VIN"]) == before
    assert plan["preview"]["level"] == "trial_observed"
    assert plan["preview"]["trial_snapshot"]["source"] == "trial"
    assert len(plan["preview"]["outputs"]) == 2
    result = run(case, "project netclass set", *CLASS, "--confirm", plan["confirm_token"])
    assert result["ok"], result
    receipt = result["data"]["receipt"]
    after = power_readback.observe(case[0], ["VIN"])
    assert after["board_sha256"] == before["board_sha256"]
    assert after["project_sha256"] != before["project_sha256"]
    assert receipt["extensions"]["quality"]["all_selected_target_met"] is False
    assert receipt["extensions"]["quality"]["ampacity_assessed"] is False
    assert receipt["outcome"]["verification"]["status"] == "passed"
    assert after["project"]["net_settings"]["netclass_assignments"]["VIN"] == ["Power"]


@pytest.mark.parametrize("task", ["board route rewidth", "board track widen"])
def test_actual_selected_copper_widths_and_nonselected_preservation(case, task):
    confirmed(case, "project netclass set", *CLASS)
    before = power_readback.observe(case[0], ["VIN"])
    receipt = confirmed(case, task, "--nets", "VIN", "--neck", "0.3")
    after = power_readback.observe(case[0], ["VIN"])
    assert after["protected_sha256"] == before["protected_sha256"]
    assert after["pads"] == before["pads"]
    actual = [o for o in after["objects"] if o["net"] == "VIN" and o["kind"] == "segment"]
    assert actual and all(
        o["width_nm"] == 300000 and o["layer"] in {"F.Cu", "B.Cu"} for o in actual
    )
    assert receipt["extensions"]["quality"]["all_selected_target_met"]
    assert receipt["extensions"]["finding_comparison"]["status"] == "passed"
    fresh = json.loads(json.dumps(drc_baseline.record(drc_baseline.capture(case[0])[1])))
    assert (
        drc_baseline.compare_records(receipt["extensions"]["finding_comparison"]["final"], fresh)[
            "status"
        ]
        == "passed"
    )


def test_selected_repair_retains_signal_and_routes_missing_connection(tmp_path):
    case = make_case(tmp_path, routed=False)
    confirmed(case, "project netclass set", *CLASS)
    before = power_readback.observe(case[0], ["VIN"])
    receipt = confirmed(case, "board route repair", "--nets", "VIN", "--neck", "0.3")
    after = power_readback.observe(case[0], ["VIN"])
    assert before["protected_sha256"] == after["protected_sha256"]
    assert any(o["net"] == "VIN" for o in after["objects"])
    assert not receipt["extensions"]["finding_comparison"]["final"]["unconnected"]


def test_trial_geometry_refusal_exposes_findings_without_config_advice(case, monkeypatch):
    failure = {
        "ok": False,
        "error": {
            "code": "E_INTEGRITY",
            "message": "Custom DRC found new error findings",
            "details": {
                "write_state": "rolled_back",
                "finding_comparison": {
                    "status": "failed",
                    "new_findings": [{"id": "owned-short", "report": {"rule": "shorting_items"}}],
                },
            },
        },
    }
    stream = json.dumps(failure).encode()
    monkeypatch.setattr(
        "kicad_cli.power_driver_adapter.subprocess.run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, stream, b""),
    )
    before = (case[0].read_bytes(), case[0].with_suffix(".kicad_pro").read_bytes())
    result = run(case, "board route repair", "--nets", "VIN", "--dry-run")
    error = result["error"]
    assert error["code"] == "E_VALIDATION" and not error["retryable"]
    details = error["details"]
    assert details["dispatch_state"] == "not_started"
    assert details["phase"] == "candidate_validation"
    assert details["primary_write_state"] == "not_applied"
    assert details["operation_registered"] is False
    assert details["candidate_error"] == failure["error"]
    assert "doctor" not in details["next_action"]
    assert "board check" in details["next_action"]
    assert Path(details["stage"], "stdout.txt").read_bytes() == stream
    assert before == (case[0].read_bytes(), case[0].with_suffix(".kicad_pro").read_bytes())
    assert not paths(case[0])[2].exists()
    assert management(case, "list")["data"]["items"] == []


def test_rebuild_discloses_replacing_both_networks_and_readbacks(case):
    data = planned(case, "board route rebuild")
    assert data["preview"]["extensions"]["selected"] == ["OTHER", "VIN"]
    result = run(case, "board route rebuild", "--confirm", data["confirm_token"])
    assert result["ok"], result
    receipt = result["data"]["receipt"]
    assert not receipt["extensions"]["finding_comparison"]["final"]["unconnected"]
    assert (
        power_readback.observe(case[0], ["VIN", "OTHER"])["pads"]
        == data["preview"]["extensions"]["expected"]["pads"]
    )


@pytest.mark.parametrize("file", ["board", "project", "rules", "candidate"])
def test_complete_input_and_prepared_candidate_drift_refuses_before_write(case, file):
    data = planned(case, "project netclass set", *CLASS)
    target = {
        "board": case[0],
        "project": case[0].with_suffix(".kicad_pro"),
        "rules": case[0].with_suffix(".kicad_dru"),
        "candidate": Path(data["preview"]["extensions"]["candidate"]),
    }[file]
    if file == "rules":
        target.write_text(
            '(version 1)\n(rule "track" (constraint track_width (min 0.2)))\n', encoding="utf-8"
        )
    else:
        target.write_bytes(target.read_bytes() + b"\n")
    before = (case[0].read_bytes(), case[0].with_suffix(".kicad_pro").read_bytes())
    result = run(case, "project netclass set", *CLASS, "--confirm", data["confirm_token"])
    assert result["error"]["code"] == "E_CONFLICT"
    assert result["error"]["details"]["dispatch_state"] == "not_started"
    assert before == (case[0].read_bytes(), case[0].with_suffix(".kicad_pro").read_bytes())
    assert not paths(case[0].with_suffix(".kicad_pro"))[2].exists()


def test_expired_plan_and_spent_token_leave_saved_files_unchanged(case):
    now = datetime.now(timezone.utc)
    data = planned(case, "project netclass set", *CLASS, now=lambda: now)
    before = case[0].with_suffix(".kicad_pro").read_bytes()
    result = run(
        case,
        "project netclass set",
        *CLASS,
        "--confirm",
        data["confirm_token"],
        now=lambda: now + timedelta(hours=1),
    )
    assert result["error"]["code"] == "E_CONFLICT"
    assert case[0].with_suffix(".kicad_pro").read_bytes() == before
    fresh = planned(case, "project netclass set", *CLASS)
    first = run(case, "project netclass set", *CLASS, "--confirm", fresh["confirm_token"])
    assert first["ok"]
    applied = case[0].with_suffix(".kicad_pro").read_bytes()
    again = run(case, "project netclass set", *CLASS, "--confirm", fresh["confirm_token"])
    assert again["error"]["code"] == "E_CONFLICT"
    assert case[0].with_suffix(".kicad_pro").read_bytes() == applied


def test_authenticated_keyed_replay_does_not_run_writer_again(case, monkeypatch):
    data = planned(case, "project netclass set", *CLASS)
    first = run(
        case,
        "project netclass set",
        *CLASS,
        "--confirm",
        data["confirm_token"],
        "--request-id",
        "power-key",
    )
    assert first["ok"]
    before = case[0].with_suffix(".kicad_pro").stat().st_mtime_ns
    monkeypatch.setattr(
        PowerAdapter, "apply", lambda *args: pytest.fail("replay dispatched a writer")
    )
    again = run(
        case,
        "project netclass set",
        *CLASS,
        "--confirm",
        data["confirm_token"],
        "--request-id",
        "power-key",
    )
    assert (
        again["ok"]
        and again["data"]["receipt"]["operation_id"] == first["data"]["receipt"]["operation_id"]
    )
    assert case[0].with_suffix(".kicad_pro").stat().st_mtime_ns == before


@pytest.mark.parametrize("after_save", [False, True])
def test_interrupted_write_is_unknown_until_actual_board_and_project_readback(
    case, monkeypatch, after_save
):
    data = planned(case, "project netclass set", *CLASS)
    baseline = case[0].with_suffix(".kicad_pro").read_bytes()
    original = PowerAdapter._observe if after_save else PowerAdapter._candidate
    method = "_observe" if after_save else "_candidate"

    def interrupt(self, plan):
        # BaseException simulates lost process/control; no adapter rollback runs.
        if after_save or paths(case[0].with_suffix(".kicad_pro"))[2].exists():
            raise KeyboardInterrupt
        return original(self, plan)

    with monkeypatch.context() as patch:
        patch.setattr(PowerAdapter, method, interrupt)
        result = run(case, "project netclass set", *CLASS, "--confirm", data["confirm_token"])
    assert result["error"]["code"] == "E_OUTCOME_UNKNOWN"
    assert (case[0].with_suffix(".kicad_pro").read_bytes() != baseline) is after_save
    operation = data["preview"]["operation_id"]
    shown = management(case, "show", "--id", operation)
    recovery = shown["data"]["operation_status"]["extensions"]["recovery_readback"]
    assert recovery["outcome"] == ("applied" if after_save else "not_applied")
    # Embedded process is still alive: resolution must refuse regardless of lease.
    refused = management(
        case,
        "resolve",
        "--id",
        operation,
        "--outcome",
        recovery["outcome"],
        "--evidence",
        recovery["evidence_file"],
    )
    assert refused["error"]["code"] == "E_CONFLICT"
    assert planned_or_blocked(case)["error"]["details"]["conflict"] in {
        "lock_held",
        "unresolved_operation",
    }


def planned_or_blocked(case):
    return run(case, "board track widen", "--nets", "VIN", "--dry-run")


def test_restore_permission_refusal_stays_unknown_and_keeps_real_backups(case, monkeypatch):
    data = planned(case, "project netclass set", *CLASS)
    board, _ = case
    baseline = board.with_suffix(".kicad_pro").read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(
            PowerAdapter,
            "profile",
            lambda *args: (_ for _ in ()).throw(ValueError("failed saved verification")),
        )
        patch.setattr(
            MoveTransaction,
            "rollback",
            lambda *args: (_ for _ in ()).throw(
                PermissionError("independent simulated WinError5 refusal")
            ),
        )
        failed = run(case, "project netclass set", *CLASS, "--confirm", data["confirm_token"])
    assert failed["error"]["code"] == "E_OUTCOME_UNKNOWN"
    receipt = failed["error"]["details"]["receipt"]
    assert receipt["outcome"]["change"] == "unknown"
    assert receipt["extensions"]["baseline_restored"] is False
    assert board.with_suffix(".kicad_pro").read_bytes() != baseline
    assert paths(board.with_suffix(".kicad_pro"))[1].read_bytes() == baseline
    assert all(p.exists() for p in paths(board.with_suffix(".kicad_pro")))


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows file sharing denial")
def test_real_windows_restore_denial_preserves_saved_candidate_and_owned_backup(case, monkeypatch):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    target = case[0].with_suffix(".kicad_pro")
    baseline = target.read_bytes()
    data = planned(case, "project netclass set", *CLASS)
    held = []

    def fail_with_owned_read_handle(self, *args):
        handle = kernel.CreateFileW(str(target), 0x80000000, 1, None, 3, 0x80, None)
        assert handle != ctypes.c_void_p(-1).value
        held.append(handle)
        raise ValueError("Intentional saved-verification failure with owned read-only handle")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(PowerAdapter, "profile", fail_with_owned_read_handle)
            failed = run(case, "project netclass set", *CLASS, "--confirm", data["confirm_token"])
        assert failed["error"]["code"] == "E_OUTCOME_UNKNOWN"
        ext = failed["error"]["details"]["receipt"]["extensions"]
        assert "WinError 5" in ext["restoration_error"]
        assert not ext["baseline_restored"]
        assert target.read_bytes() != baseline
        assert paths(target)[1].read_bytes() == baseline
        assert all(path.exists() for path in paths(target))
    finally:
        for handle in held:
            kernel.CloseHandle(handle)


@pytest.mark.parametrize(
    "flags",
    [
        ("--nets", "VIN,,OTHER"),
        ("--nets", "VIN,VIN"),
        ("--no-verify",),
        ("--ignore-lock",),
        ("--no-restore",),
    ],
)
def test_invalid_selection_and_removed_bypasses_never_write_primary(case, flags):
    before = case[0].read_bytes()
    result = run(case, "board route rewidth", *flags, "--dry-run")
    assert result["error"]["code"] in {"E_USAGE", "E_VALIDATION"}
    assert case[0].read_bytes() == before


@pytest.mark.parametrize(
    "flags", [("--width", "0.1"), ("--via-diameter", "0.2"), ("--via-drill", "0.1")]
)
def test_class_dimensions_cannot_hide_board_minimums(case, flags):
    values = list(CLASS)
    values[values.index(flags[0]) + 1] = flags[1]
    before = case[0].with_suffix(".kicad_pro").read_bytes()
    result = run(case, "project netclass set", *values, "--dry-run")
    assert result["error"]["code"] == "E_VALIDATION"
    assert case[0].with_suffix(".kicad_pro").read_bytes() == before


def test_private_worker_refuses_forged_owned_looking_request(case):
    board, env = case
    root = Path(env["KICAD_CLI_STATE_DIR"]) / "power-trials" / "op-forged"
    candidate = root / "candidate"
    candidate.mkdir(parents=True)
    copied = candidate / board.name
    copied.write_bytes(board.read_bytes())
    copied.with_suffix(".kicad_pro").write_bytes(board.with_suffix(".kicad_pro").read_bytes())
    with driver_runtime.create_cli(environ=env) as cli:
        scope = cli.scopes.resolve()
        from kicad_cli._driver_sdk import tokens
        from kicad_cli.move_driver_adapter import target_of

        cli._store_open().secret()
        request = {
            "owner": "kicad-cli.power-trial.v1",
            "board": str(copied),
            "command": "board track widen",
            "params": {},
            "selected": ["VIN"],
            "engineering_target": target_of(board),
            "binding": cli._binding(),
            "scope_digest": tokens.digest(scope.record),
            "captured": {},
            "hmac": "forged",
        }
    request_path = root / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    before = copied.read_bytes()
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "kicad_cli.main", "--internal-power-trial", str(request_path)],
        env=env,
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        timeout=30,
    )
    assert proc.returncode != 0
    assert copied.read_bytes() == before and board.read_bytes() == before
    assert b"authentication/build/grant mismatch" in proc.stderr


def test_effective_class_precedence_and_json_stored_finding_provenance(case):
    p = json.loads(case[0].with_suffix(".kicad_pro").read_bytes())
    p["net_settings"]["classes"].append({"name": "Wildcard", "priority": -10, "track_width": 0.5})
    p["net_settings"]["netclass_patterns"] = [{"pattern": "*", "netclass": "Wildcard"}]
    case[0].with_suffix(".kicad_pro").write_text(json.dumps(p), encoding="utf-8")
    blocked = run(case, "project netclass set", *CLASS, "--dry-run")
    assert blocked["error"]["code"] == "E_VALIDATION"
    confirmed(case, "project netclass set", *CLASS, "--priority", "-11")
    p = power_readback.observe(case[0], ["VIN"])["project"]
    assert power_rules.effective(p, ["VIN"])["VIN"]["values_nm"]["track_width"] == 300000
    record = drc_baseline.record(drc_baseline.capture(case[0])[1])
    assert (
        drc_baseline.compare_records(
            json.loads(json.dumps(record)), drc_baseline.record(drc_baseline.capture(case[0])[1])
        )["status"]
        == "passed"
    )


@pytest.mark.parametrize("task", sorted(COMMANDS))
def test_missing_scope_has_no_primary_or_trial_writes(case, task):
    before = case[0].read_bytes()
    env = dict(case[1], KICAD_CLI_SCOPE_FILE=str(case[0].parent / "absent-scope.json"))
    flags = (
        CLASS
        if task == "project netclass set"
        else ("--nets", "VIN")
        if task != "board route rebuild"
        else ()
    )
    failed = run((case[0], env), task, *flags, "--dry-run")
    assert failed["error"]["code"] == "E_FORBIDDEN"
    assert case[0].read_bytes() == before
    assert not (Path(case[1]["KICAD_CLI_STATE_DIR"]) / "power-trials").exists()
