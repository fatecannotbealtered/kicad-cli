"""Real file changes, durable recovery and independent observations; no native claim."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from driver_test_utils import grant

from kicad_cli import driver_runtime, move_readback

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests/fixtures/sch_edit/edit.kicad_pcb"
_EXE_TRANSPORTS = {}


def test_authenticated_pin_survives_lf_checkout(tmp_path, monkeypatch):
    """Git's LF checkout must preserve the authenticated bootstrap manifest."""
    shutil.copytree(driver_runtime.SPEC, tmp_path, dirs_exist_ok=True)
    raw = (driver_runtime.SPEC / "PIN.json").read_bytes()
    checkout = raw.replace(b"\r\n", b"\n")
    assert json.loads(checkout) == json.loads(raw)
    (tmp_path / "PIN.json").write_bytes(checkout)
    monkeypatch.setattr(driver_runtime, "SPEC", tmp_path)
    driver_runtime._pin()


@pytest.fixture
def case(tmp_path, record_property):
    board = tmp_path / "edit.kicad_pcb"
    shutil.copyfile(FIXTURE, board)
    scope_env = grant(tmp_path)
    env = dict(os.environ, **scope_env, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    binary = os.environ.get("KICAD_CLI_TEST_BINARY")
    if binary:
        from move_exe_transport import FinalMoveExe

        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("KICAD", "PYTHON")) and k != "KIPRJMOD"
        }
        env.update(
            scope_env,
            PYTHONIOENCODING="utf-8",
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONHASHSEED="0",
            KICAD_CONFIG_HOME=str(tmp_path / "configuration"),
            KICAD_CLI_DRIVER_SDK_PATH=os.environ["KICAD_CLI_ACCEPT_DRIVER_SDK"],
        )
        Path(env["KICAD_CONFIG_HOME"]).mkdir()
        if "KICAD_CLI_ROOT" in os.environ:
            env["KICAD_CLI_ROOT"] = os.environ["KICAD_CLI_ROOT"]
        _EXE_TRANSPORTS[board] = FinalMoveExe(
            binary, tmp_path, env, tmp_path / "public-exe-calls", record_property
        )
    try:
        yield board, env
    finally:
        _EXE_TRANSPORTS.pop(board, None)


def call(case, *flags, moves="R1:101.25,100.5", **options):
    board, env = case
    if board in _EXE_TRANSPORTS:
        assert not options, "Final EXE transport forbids injected time, adapters or runtime options"
        _code, result = _EXE_TRANSPORTS[board].call(
            "board", "footprint", "move", "--board", str(board), "--moves", moves, *flags
        )
        return result
    with driver_runtime.create_cli(environ=env, home=board.parent, **options) as cli:
        result = cli.run(
            ["board", "footprint", "move", "--board", str(board), "--moves", moves, *flags]
        )
        assert result.envelope is not None, result.stdout
        cli.validator.validate("response", result.envelope)
        return result.envelope


def management(case, *args, **options):
    board, env = case
    if board in _EXE_TRANSPORTS:
        assert not options, "Final EXE transport forbids injected time, adapters or runtime options"
        _code, result = _EXE_TRANSPORTS[board].call("operation", *args)
        return result
    with driver_runtime.create_cli(environ=env, home=board.parent, **options) as cli:
        result = cli.run(["operation", *args])
        assert result.envelope is not None
        cli.validator.validate("response", result.envelope)
        return result.envelope


def plan(case, **options):
    result = call(case, "--dry-run", **options)
    assert result["ok"], result
    return result["data"]


def test_real_plan_confirm_query_and_no_target_write_during_preview(case):
    board, _env = case
    before = board.read_bytes()
    data = plan(case)
    assert board.read_bytes() == before
    assert not Path(str(board) + ".kicad-cli.journal").exists()
    preview = data["preview"]
    assert preview["target"]["locator"] == board.resolve().as_uri()
    assert preview["base_snapshot"]["revision"] == hashlib.sha256(before).hexdigest()
    assert (
        management(case, "show", "--id", preview["operation_id"])["data"]["operation_status"][
            "status"
        ]
        == "planned"
    )
    done = call(case, "--confirm", data["confirm_token"])
    assert done["ok"], done
    receipt = done["data"]["receipt"]
    assert receipt["operation_id"] == preview["operation_id"]
    assert receipt["plan_id"] == preview["plan_id"]
    assert receipt["authorization_scope_id"] == "isolated-move-test"
    assert receipt["extensions"]["observed"][0]["position_nm"] == [101250000, 100500000]
    verification = receipt["outcome"]["verification"]
    assert verification["status"] == "passed"
    profile = verification["extensions"]["verification_result"]
    assert profile["observations"][0]["source"]["kind"] == "file_consumer"
    assert profile["observations"][0]["source"]["completion"] == "completed"
    queried = management(case, "show", "--id", preview["operation_id"])["data"]["operation_status"]
    assert queried["status"] == "succeeded" and queried["receipt"] == receipt
    assert management(case, "list", "--status", "succeeded")["data"]["count"] == 1
    assert (
        call(case, "--confirm", data["confirm_token"])["error"]["details"]["conflict"]
        == "token_used"
    )


@pytest.mark.parametrize(
    "kind", ["absent", "revoked", "expired", "wrong-root", "wrong-selector", "observe-only"]
)
def test_scope_denial_has_no_dispatch_or_target_sidecars(case, kind):
    board, env = case
    scope = Path(env["KICAD_CLI_SCOPE_FILE"])
    record = json.loads(scope.read_bytes())
    if kind == "absent":
        scope.unlink()
    elif kind == "revoked":
        record["revoked"] = True
    elif kind == "expired":
        record["expires_at"] = "2000-01-01T00:00:00Z"
    elif kind == "wrong-root":
        record["roots"] = [str(board.parent / "other")]
    elif kind == "wrong-selector":
        record["targets"] = [{"engine_id": "wrong-engine"}]
    else:
        record["effects"] = ["observe"]
    if kind != "absent":
        scope.write_text(json.dumps(record), encoding="utf-8")
    before = board.read_bytes()
    error = call(case, "--dry-run")["error"]
    assert error["code"] == "E_FORBIDDEN", error
    assert error["details"]["dispatch_state"] == "not_started"
    assert error["details"]["scope"]["missing"]
    assert board.read_bytes() == before
    assert not list(board.parent.glob("*.kicad-cli.*"))


@pytest.mark.parametrize(
    "kind",
    ["tamper", "parameters", "scope-content", "scope-revocation", "drift", "expiry", "version"],
)
def test_confirm_binding_cannot_be_reused_for_changed_intent_or_state(case, kind):
    board, env = case
    data = plan(case)
    token, moves, options = data["confirm_token"], "R1:101.25,100.5", {}
    expected = "E_CONFLICT"
    if kind == "tamper":
        token = token[:-2] + ("aa" if token[-2:] != "aa" else "bb")
    elif kind == "parameters":
        moves = "R1:102,103"
    elif kind in {"scope-content", "scope-revocation"}:
        scope = Path(env["KICAD_CLI_SCOPE_FILE"])
        value = json.loads(scope.read_bytes())
        value["notes"] += " changed while retaining the same scope_id"
        if kind == "scope-revocation":
            value["revoked"] = True
            expected = "E_FORBIDDEN"
        scope.write_text(json.dumps(value), encoding="utf-8")
    elif kind == "drift":
        board.write_bytes(board.read_bytes().replace(b"(thickness 1.6)", b"(thickness 1.7)"))
    elif kind == "expiry":
        options["now"] = lambda: datetime.now(timezone.utc) + timedelta(minutes=11)
    else:
        with driver_runtime.create_cli(environ=env, home=board.parent) as cli:
            cli.version = "2.0.1"
            result = cli.run(
                [
                    "board",
                    "footprint",
                    "move",
                    "--board",
                    str(board),
                    "--moves",
                    moves,
                    "--confirm",
                    token,
                ]
            ).envelope
            assert result["error"]["code"] == "E_CONFLICT"
            return
    before = board.read_bytes()
    result = call(case, "--confirm", token, moves=moves, **options)
    assert result["error"]["code"] == expected, result
    assert result["error"]["details"]["dispatch_state"] == "not_started"
    assert board.read_bytes() == before


def test_request_key_returns_real_record_without_a_second_write(case):
    original = plan(case)
    done = call(case, "--confirm", original["confirm_token"], "--idempotency-key", "one-intent")
    assert done["ok"], done
    before = case[0].read_bytes()
    second = plan(case)
    replay = call(case, "--confirm", second["confirm_token"], "--idempotency-key", "one-intent")
    assert replay["ok"] and replay["data"]["receipt"]["replayed"]
    assert replay["data"]["receipt"]["operation_id"] == done["data"]["receipt"]["operation_id"]
    assert case[0].read_bytes() == before


def kill_after_save(case, stage="after_save"):
    board, env = case
    data = plan(case)
    script = (
        "from kicad_cli.fileformat.sexpr import Document\nimport os\n"
        "original = Document.save\ndef saved(self, path):\n"
        + (
            " os._exit(91)\n"
            if stage == "before_save"
            else " original(self, path)\n os._exit(91)\n"
        )
        + "Document.save=saved\nfrom kicad_cli.main import main\n"
        "main(__import__('sys').argv[1:])"
    )
    argv = [
        sys.executable,
        "-B",
        "-c",
        script,
        "board",
        "footprint",
        "move",
        "--board",
        str(board),
        "--moves",
        "R1:101.25,100.5",
        "--confirm",
        data["confirm_token"],
    ]
    process = subprocess.run(argv, cwd=REPO, env=env, capture_output=True, timeout=30)
    assert process.returncode == 91 and not process.stdout
    assert move_readback.observe(board, ["R1"]).objects[0]["position_nm"] == (
        [100000000, 100000000] if stage == "before_save" else [101250000, 100500000]
    )
    return data["preview"]["operation_id"]


def future():
    return datetime.now(timezone.utc) + timedelta(seconds=40)


def test_killed_writer_is_queryable_blocks_blind_retry_and_resolves_from_real_readback(case):
    board, _env = case
    operation = kill_after_save(case)
    status = management(case, "show", "--id", operation, now=future)["data"]["operation_status"]
    assert status["status"] == "unknown" and status["receipt"] is None
    readback = status["extensions"]["recovery_readback"]
    assert readback["outcome"] == "applied" and readback["completion"] == "completed"
    refused = call(case, "--dry-run", now=future)
    assert refused["error"]["details"]["conflict"] == "unresolved_operation"
    before = board.read_bytes()
    resolved = management(
        case,
        "resolve",
        "--id",
        operation,
        "--outcome",
        "applied",
        "--evidence",
        readback["evidence_file"],
        now=future,
    )
    assert resolved["ok"], resolved
    assert resolved["data"]["operation_status"]["resolution"]["outcome"] == "applied"
    assert board.read_bytes() == before
    assert not list(board.parent.glob("*.kicad-cli.*"))
    assert (
        plan(case, now=future)["preview"]["base_snapshot"]["revision"]
        == hashlib.sha256(before).hexdigest()
    )


@pytest.mark.parametrize(
    "kind", ["fake-evidence", "wrong-outcome", "drift", "revoked", "still-running"]
)
def test_recovery_counterexamples_leave_unresolved_and_preserve_actual_bytes(case, kind):
    board, env = case
    operation = kill_after_save(case)
    status = management(case, "show", "--id", operation, now=future)["data"]["operation_status"]
    evidence = status["extensions"]["recovery_readback"]["evidence_file"]
    outcome = "applied"
    if kind == "fake-evidence":
        Path(evidence).write_text(
            '{"outcome":"applied","operation_id":"invented"}', encoding="utf-8"
        )
    elif kind == "wrong-outcome":
        outcome = "not_applied"
    elif kind == "drift":
        board.write_bytes(board.read_bytes().replace(b"(thickness 1.6)", b"(thickness 1.7)"))
    elif kind == "revoked":
        path = Path(env["KICAD_CLI_SCOPE_FILE"])
        data = json.loads(path.read_bytes())
        data["revoked"] = True
        path.write_text(json.dumps(data), encoding="utf-8")
    before = board.read_bytes()
    options = {} if kind == "still-running" else {"now": future}
    refused = management(
        case, "resolve", "--id", operation, "--outcome", outcome, "--evidence", evidence, **options
    )
    assert not refused["ok"], refused
    assert board.read_bytes() == before
    assert Path(str(board) + ".kicad-cli.backup").read_bytes() == FIXTURE.read_bytes()


def test_unknown_pilot_blocks_migrated_silkscreen_before_any_native_preview(case):
    board, env = case
    operation = kill_after_save(case)
    # A live lease blocks as lock_held; after expiry the same guard reports unresolved.
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "kicad_cli.main",
            "board",
            "silkscreen",
            "place",
            "--board",
            str(board),
            "--dry-run",
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        timeout=30,
    )
    result = json.loads(process.stdout)
    assert not result["ok"] and result["error"]["details"].get("active_operation") == operation, (
        result
    )


def test_runtime_status_identifies_all_remaining_legacy_commands():
    from kicad_cli import registry

    commands = registry.build()
    status = driver_runtime.migration_status(commands)
    assert set(status["migrated"]) == driver_runtime.MIGRATED
    assert "board route repair" in status["migrated"]
    assert "project sync" in status["migrated"]
    assert "board zone create" in status["migrated"] and "board zone refill" in status["migrated"]
    assert {"project links inspect", "project parity", "board check", "sch check"} <= set(
        status["migrated"]
    )
    assert {"fab gerber", "fab drill", "board plot"} <= set(status["migrated"])
    assert len(status["migrated"]) == 33 and len(status["legacy"]) == 0
    assert len(commands) == 33


def test_native_project_lock_is_busy_without_fabricating_an_operation(case):
    board, _env = case
    before = board.read_bytes()
    lock = board.parent / "~edit.kicad_pro.lck"
    lock.write_text('{"hostname":"owned test host","username":"test"}', encoding="utf-8")
    refused = call(case, "--dry-run")
    assert refused["error"]["code"] == "E_BUSY", refused
    assert refused["error"]["retryable"] is True
    assert refused["error"]["details"]["dispatch_state"] == "not_started"
    assert refused["error"]["details"]["lock_files"] == [lock.name]
    assert board.read_bytes() == before and lock.exists()


def test_killed_before_save_resolves_not_applied_without_replaying(case):
    operation = kill_after_save(case, "before_save")
    status = management(case, "show", "--id", operation, now=future)["data"]["operation_status"]
    observed = status["extensions"]["recovery_readback"]
    assert observed["outcome"] == "not_applied"
    before = case[0].read_bytes()
    result = management(
        case,
        "resolve",
        "--id",
        operation,
        "--outcome",
        "not_applied",
        "--evidence",
        observed["evidence_file"],
        now=future,
    )
    assert result["ok"], result
    assert case[0].read_bytes() == before == FIXTURE.read_bytes()


def test_partial_external_edit_is_observed_and_kept_by_explicit_resolution(case):
    operation = kill_after_save(case)
    board = case[0]
    board.write_bytes(board.read_bytes().replace(b"(thickness 1.6)", b"(thickness 1.7)"))
    status = management(case, "show", "--id", operation, now=future)["data"]["operation_status"]
    observed = status["extensions"]["recovery_readback"]
    assert observed["outcome"] == "partial" and observed["verification"]["status"] == "failed"
    before = board.read_bytes()
    result = management(
        case,
        "resolve",
        "--id",
        operation,
        "--outcome",
        "partial",
        "--evidence",
        observed["evidence_file"],
        now=future,
    )
    assert result["ok"], result
    assert board.read_bytes() == before


def test_expired_lease_cannot_resolve_a_writer_that_is_actually_alive(case):
    operation = kill_after_save(case)
    status = management(case, "show", "--id", operation, now=future)["data"]["operation_status"]
    evidence = status["extensions"]["recovery_readback"]["evidence_file"]
    with driver_runtime.create_cli(environ=case[1], home=case[0].parent) as cli:
        row = cli._record(operation)
        extra = dict(row["extra"], writer_pid=os.getpid())  # deliberate fault injection
        cli._store_open().connection().execute(
            "UPDATE operations SET extra = ? WHERE operation_id = ?", (json.dumps(extra), operation)
        )
    refused = management(
        case,
        "resolve",
        "--id",
        operation,
        "--outcome",
        "applied",
        "--evidence",
        evidence,
        now=future,
    )
    assert refused["error"]["details"]["conflict"] == "lock_held"


def test_persisted_plan_tamper_is_rejected_even_when_target_and_baseline_match(case):
    data = plan(case)
    with driver_runtime.create_cli(environ=case[1], home=case[0].parent) as cli:
        row = cli._record(data["preview"]["operation_id"])
        row["plan"]["extensions"]["moves"]["R1"] = [102, 103]
        cli._store_open().connection().execute(
            "UPDATE operations SET plan = ? WHERE operation_id = ?",
            (json.dumps(row["plan"]), row["operation_id"]),
        )
    result = call(case, "--confirm", data["confirm_token"])
    assert result["error"]["details"]["conflict"] == "token_mismatch"
    assert case[0].read_bytes() == FIXTURE.read_bytes()


def test_lost_output_preserves_the_actual_success_receipt(case, monkeypatch, capsys):
    data = plan(case)
    driver_runtime._dependencies()
    from kicad_cli._driver_sdk.runtime import Output

    def broken_output(*args):
        raise BrokenPipeError("injected lost stdout")

    monkeypatch.setattr(Output, "write", broken_output)
    result = driver_runtime.execute(
        [
            "board",
            "footprint",
            "move",
            "--board",
            str(case[0]),
            "--moves",
            "R1:101.25,100.5",
            "--confirm",
            data["confirm_token"],
        ],
        environ=case[1],
        home=case[0].parent,
    )
    assert result == 6
    assert capsys.readouterr().out == ""
    status = management(case, "show", "--id", data["preview"]["operation_id"])["data"][
        "operation_status"
    ]
    assert (
        status["status"] == "succeeded"
        and status["receipt"]["outcome"]["verification"]["status"] == "passed"
    )
