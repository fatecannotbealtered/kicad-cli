"""Finite source recovery evidence, with inert native services explicitly labelled.

The worker exits at one declared source seam; it is neither a final-EXE fault
nor a native KiCad failure. Every case owns a new scope/store/project. No old
operation is opened and no native call is retried. Execution requires the root
controller's reviewed, frozen plan; importing this file performs no experiment.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from driver_test_utils import grant
from test_move_preflight_binding import bounded_case as bounded_case
from test_move_safety_exe import files, pose_rows, protected_bytes

from kicad_cli import driver_runtime, move_preflight
from kicad_cli.fileformat.sexpr import Document
from kicad_cli.move_transaction import MoveTransaction

REPO = Path(__file__).resolve().parents[1]
MOVES = "R4:99.5,110"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _put(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _call(scene, arguments, **options):
    with driver_runtime.create_cli(environ=scene.env, home=scene.root, **options) as cli:
        result = cli.run(arguments)
        cli.validator.validate("response", result.envelope)
        return result.envelope


def _args(scene):
    return ["board", "footprint", "move", "--board", str(scene.board), "--moves", MOVES]


def _plan(scene):
    result = _call(scene, [*_args(scene), "--dry-run"])
    assert result["ok"], result
    assert result["data"]["preview"]["extensions"]["preflight"]["status"] == "passed"
    assert scene.control["seen"] == [
        "prepare-version",
        "prepare-drc",
        "baseline-drc",
        "candidate-drc",
    ]
    return result["data"]


@pytest.fixture
def source_scene(bounded_case, tmp_path, monkeypatch):
    """Real full prepare, real file transaction; only native service is inert."""
    # Binding reads the actual process environment as well as the explicit
    # runner environment. The child inherits exactly the same reviewed inputs;
    # adding scope/state only in its environment would correctly be a drift.
    for key, value in {
        **grant(tmp_path, scope_id="owned-stability-source"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }.items():
        monkeypatch.setenv(key, value)
    env = dict(os.environ)
    for name in ("edit.kicad_sch", "edit_child.kicad_sch"):
        shutil.copyfile(
            REPO / "tests/fixtures/sch_edit" / name, bounded_case["board"].parent / name
        )
    scene = SimpleNamespace(
        root=tmp_path, board=bounded_case["board"], env=env, control=bounded_case
    )
    _put(
        tmp_path / "OWNED-CASE.json",
        {
            "kind": "source-controlled-native-service",
            "real_native_processes": 0,
            "root": str(tmp_path),
            "board": str(scene.board),
            "state": env["KICAD_CLI_STATE_DIR"],
            "scope": env["KICAD_CLI_SCOPE_FILE"],
            "moves": MOVES,
            "clock_policy": "source-only controlled now plus 40 seconds",
        },
    )
    return scene


@pytest.mark.parametrize("mode", ["prepare-native-failure", "candidate-report-missing"])
def test_controlled_preflight_failure_has_no_formal_write(source_scene, monkeypatch, mode):
    """A2: exercise actual prepare/config/report path, not an early prepare stub."""
    scene = source_scene
    before = files(scene.board.parent)
    underlying = move_preflight.move_native_process.run
    saves = []

    def forbidden_save(*args, **kwargs):
        saves.append(1)
        raise AssertionError("A failed preview cannot save a formal document")

    def service(command, **kwargs):
        record = underlying(command, **kwargs)
        phase = kwargs["output"].name
        if mode == "prepare-native-failure" and phase == "prepare-drc":
            record.update(execution="native_nonzero", exit=5)
            (kwargs["output"] / "record.json").write_text(json.dumps(record), encoding="utf-8")
        if mode == "candidate-report-missing" and phase == "candidate-drc":
            Path(command[command.index("-o") + 1]).unlink()
        return record

    monkeypatch.setattr(move_preflight.move_native_process, "run", service)
    monkeypatch.setattr(Document, "save", forbidden_save)
    response = _call(scene, [*_args(scene), "--dry-run"])
    assert not response["ok"] and response["error"]["code"] == "E_NATIVE_FAILURE"
    details = response["error"]["details"]
    assert details["dispatch_state"] == "not_started"
    assert details["engineering_write_started"] is False and details["automatic_retry"] is False
    assert "receipt" not in details and "confirm_token" not in response.get("data", {})
    expected = ["prepare-version", "prepare-drc"]
    if mode == "candidate-report-missing":
        expected += ["baseline-drc", "candidate-drc"]
    assert scene.control["seen"] == expected
    assert saves == [] and files(scene.board.parent) == before
    assert not list(scene.board.parent.glob("*.kicad-cli.*"))
    _put(
        scene.root / "FAILURE-WITNESS.json",
        {
            "mode": mode,
            "service_calls": expected,
            "real_native_processes": 0,
            "document_save_calls": 0,
            "formal_files_before": before,
            "formal_files_after": files(scene.board.parent),
            "response": response,
        },
    )


def test_arm_then_candidate_drift_is_dispatched_but_never_saved(source_scene, monkeypatch):
    """C1: prove the under-lock second validation, after real journal/backup arm."""
    scene = source_scene
    data = _plan(scene)
    plan = data["preview"]
    candidate = Path(plan["extensions"]["preflight"]["staging"]["stages"]["candidate"]["board"])
    before = files(scene.board.parent)
    original_arm = MoveTransaction.arm
    saves, stages = [], []

    def arm_then_drift(transaction):
        original_arm(transaction)
        assert transaction.operation == plan["operation_id"]
        assert transaction.armed and transaction.backup.is_file() and transaction.journal.is_file()
        _put(
            scene.root / "ARM-WITNESS.json",
            {
                "operation_id": transaction.operation,
                "baseline": transaction.baseline,
                "intended": transaction.intended,
                "journal_sha256": _sha(transaction.journal),
                "backup_sha256": _sha(transaction.backup),
                "candidate_before_sha256": _sha(candidate),
                "phase": "transaction-armed-before-under-lock-validation",
                "pid": os.getpid(),
            },
        )
        candidate.write_bytes(candidate.read_bytes() + b"\n; controlled candidate drift\n")
        stages.append("armed-and-candidate-mutated")

    def forbidden_save(*args, **kwargs):
        saves.append(1)
        raise AssertionError("Under-lock input drift must refuse before Document.save")

    monkeypatch.setattr(MoveTransaction, "arm", arm_then_drift)
    monkeypatch.setattr(Document, "save", forbidden_save)
    response = _call(scene, [*_args(scene), "--confirm", data["confirm_token"]])
    assert not response["ok"]
    details = response["error"]["details"]
    assert details["dispatch_state"] == "dispatched", response
    receipt = details["receipt"]
    assert receipt["operation_id"] == plan["operation_id"]
    assert receipt["outcome"]["execution"] == "failed"
    assert receipt["outcome"]["change"] == "not_applied"
    assert receipt["outcome"]["persistence"] == "unchanged"
    assert receipt["outcome"]["verification"]["status"] == "incomplete"
    assert stages == ["armed-and-candidate-mutated"] and saves == []
    assert files(scene.board.parent) == before
    assert scene.control["seen"] == [
        "prepare-version",
        "prepare-drc",
        "baseline-drc",
        "candidate-drc",
    ]
    queried = _call(scene, ["operation", "show", "--id", plan["operation_id"]])
    assert queried["data"]["operation_status"]["status"] == "failed"
    assert queried["data"]["operation_status"]["receipt"] == receipt
    _put(
        scene.root / "DRIFT-RESULT.json",
        {
            "kind": "source-under-lock-drift",
            "response": response,
            "real_native_processes": 0,
            "document_save_calls": 0,
            "formal_files_before": before,
            "formal_files_after": files(scene.board.parent),
        },
    )


def _birth():
    if os.name != "nt":
        return None
    from ctypes import wintypes as W

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = W.HANDLE
    kernel.GetProcessTimes.argtypes = [W.HANDLE, *([ctypes.c_void_p] * 4)]
    values = [ctypes.c_ulonglong() for _ in range(4)]
    if not kernel.GetProcessTimes(kernel.GetCurrentProcess(), *[ctypes.byref(v) for v in values]):
        raise ctypes.WinError(ctypes.get_last_error())
    return values[0].value


def _fault_worker(request_path):
    """Source-only subprocess entry; exact one operation, no native dispatch."""
    from kicad_cli._driver_sdk.store import StateStore

    request = json.loads(Path(request_path).read_bytes())
    root, board = Path(request["root"]), Path(request["board"])
    operation_id, stage = request["operation_id"], request["stage"]
    preflight = request["preflight"]
    move_preflight.move_preflight_inputs.kicad_env.find_kicad_root = lambda: os.environ[
        "KICAD_CLI_ROOT"
    ]
    move_preflight.move_preflight_inputs.kicad_env.find_official_cli = lambda: os.environ[
        "KICAD_CLI_OFFICIAL"
    ]

    def forbidden_native(*args, **kwargs):
        _put(root / "UNEXPECTED-NATIVE-CALL.json", {"phase": stage})
        raise AssertionError("Confirmation/recovery must not regenerate the preflight")

    move_preflight.move_native_process.run = forbidden_native
    original_save, original_finish = Document.save, StateStore.finish
    save_count = 0

    def witness(receipt=None):
        sidecars = {}
        for suffix in (".kicad-cli.lock", ".kicad-cli.backup", ".kicad-cli.journal"):
            path = Path(str(board) + suffix)
            if path.exists():
                sidecars[suffix] = {"sha256": _sha(path), "bytes": path.stat().st_size}
        if stage == "before-save":
            journal = json.loads(Path(str(board) + ".kicad-cli.journal").read_bytes())
            assert journal["operation_id"] == operation_id
            assert journal["pid"] == os.getpid()
            assert journal["intended"] == preflight["candidate_sha256"]
        else:
            assert not sidecars and receipt["outcome"]["persistence"] == "saved"
            assert _sha(board) == preflight["candidate_sha256"]
        _put(
            root / "EXIT-SEAM-WITNESS.json",
            {
                "kind": "source-exit-injection",
                "stage": stage,
                "operation_id": operation_id,
                "pid": os.getpid(),
                "creation_filetime": _birth(),
                "source_python": sys.executable,
                "board_sha256": _sha(board),
                "candidate_sha256": preflight["candidate_sha256"],
                "baseline_sha256": preflight["baseline_sha256"],
                "sidecars": sidecars,
                "document_save_calls": save_count,
                "receipt_before_store_finish": receipt,
                "real_native_processes": 0,
                "exit_code": 91 if stage == "before-save" else 92,
            },
        )

    def save(document, path):
        nonlocal save_count
        if Path(path).resolve() == board.resolve():
            if stage == "before-save":
                witness()
                os._exit(91)
            save_count += 1
        return original_save(document, path)

    def finish(store, identifier, status, receipt=None, extra=None):
        if identifier == operation_id and stage == "before-receipt-store":
            assert status == "succeeded" and receipt is not None
            assert save_count == 1 and receipt["operation_id"] == operation_id
            witness(receipt)
            os._exit(92)
        return original_finish(store, identifier, status, receipt, extra)

    Document.save, StateStore.finish = save, finish
    scene = SimpleNamespace(root=root, board=board, env=dict(os.environ))
    result = _call(scene, [*_args(scene), "--confirm", request["token"]])
    _put(root / "UNREACHED-SEAM-RESPONSE.json", result)
    raise AssertionError("Declared source fault seam was not reached; do not retry")


@pytest.mark.parametrize("stage", ["before-save", "before-receipt-store"])
def test_source_exit_has_exact_unknown_readback_block_and_resolution(source_scene, stage):
    """D1/E1: source worker exits once; same store resolves observed bytes only."""
    scene = source_scene
    data = _plan(scene)
    plan, preflight = data["preview"], data["preview"]["extensions"]["preflight"]
    before, raw = files(scene.board.parent), scene.board.read_bytes()
    request = scene.root / "FAULT-REQUEST.json"
    _put(
        request,
        {
            "root": str(scene.root),
            "board": str(scene.board),
            "operation_id": plan["operation_id"],
            "stage": stage,
            "token": data["confirm_token"],
            "preflight": preflight,
        },
    )
    script = (
        "import sys;sys.path[:0]=[sys.argv[1],sys.argv[2]];"
        "from kicad_cli import driver_runtime;driver_runtime._pin();"
        "from test_move_stability_recovery import _fault_worker;_fault_worker(sys.argv[3])"
    )
    # Same mature owned-Job recorder as other source acceptance; no subprocess.run
    # timeout/kill guessed from a PID, and no debugger/native classification claim.
    sys.path.insert(0, str(REPO / "bench"))
    from bench.process_evidence import run_recorded

    record, _ = run_recorded(
        scene.root,
        "source-fault",
        [sys.executable, "-B", "-c", script, str(REPO / "tests"), str(REPO), str(request)],
        cwd=REPO,
        env=scene.env,
        timeout=60,
        role="source-controlled-exit-no-native",
        inputs=[request, Path(__file__)],
    )
    assert record["state"] == "finished"
    assert record["exit"] == (91 if stage == "before-save" else 92), record
    assert record["execution"] == "nonzero_exit"
    assert not (scene.root / "source-fault.stdout").read_bytes()
    if os.name == "nt":
        assert record["job_finalization"]["natural_tree_complete"] is True
    witness = json.loads((scene.root / "EXIT-SEAM-WITNESS.json").read_bytes())
    assert witness["pid"] == record["pid"]
    if os.name == "nt":
        assert witness["creation_filetime"] == record["root_identity"]["creation_filetime"]
    assert witness["document_save_calls"] == (0 if stage == "before-save" else 1)
    expected = "not_applied" if stage == "before-save" else "applied"
    expected_sha = (
        preflight["baseline_sha256"] if stage == "before-save" else preflight["candidate_sha256"]
    )
    assert _sha(scene.board) == expected_sha
    if stage == "before-save":
        assert {
            name: row
            for name, row in files(scene.board.parent).items()
            if ".kicad-cli." not in name
        } == before
    else:
        assert pose_rows(scene.board.read_bytes())["R4"]["position_nm"] == [99500000, 110000000]
        assert protected_bytes(raw, ["R4"]) == protected_bytes(scene.board.read_bytes(), ["R4"])
        assert {k: v for k, v in files(scene.board.parent).items() if k != scene.board.name} == {
            k: v for k, v in before.items() if k != scene.board.name
        }

    # Explicit source-clock observation only; the subprocess/Job is already gone.
    def future():
        return datetime.now(timezone.utc) + timedelta(seconds=40)

    status = _call(scene, ["operation", "show", "--id", plan["operation_id"]], now=future)
    observed = status["data"]["operation_status"]
    assert observed["status"] == "unknown" and observed["receipt"] is None
    readback = observed["extensions"]["recovery_readback"]
    assert readback["outcome"] == expected and readback["completion"] == "completed"
    calls = list(scene.control["seen"])
    refused = _call(scene, [*_args(scene), "--dry-run"], now=future)
    assert not refused["ok"] and refused["error"]["details"]["conflict"] == "unresolved_operation"
    assert (
        scene.control["seen"] == calls and not (scene.root / "UNEXPECTED-NATIVE-CALL.json").exists()
    )
    settled_files = files(scene.board.parent)
    resolved = _call(
        scene,
        [
            "operation",
            "resolve",
            "--id",
            plan["operation_id"],
            "--outcome",
            expected,
            "--evidence",
            readback["evidence_file"],
        ],
        now=future,
    )
    assert resolved["ok"], resolved
    resolution_status = resolved["data"]["operation_status"]
    assert resolution_status["resolution"]["outcome"] == expected
    assert resolution_status["receipt"] is None
    assert _sha(scene.board) == expected_sha
    assert not list(scene.board.parent.glob("*.kicad-cli.*"))
    assert {k: v for k, v in settled_files.items() if ".kicad-cli." not in k} == files(
        scene.board.parent
    )
    resolved_files = files(scene.board.parent)
    post_resolve = _call(scene, ["operation", "show", "--id", plan["operation_id"]], now=future)
    assert post_resolve["ok"], post_resolve
    post_status = post_resolve["data"]["operation_status"]
    assert post_status["operation_id"] == plan["operation_id"]
    assert post_status["status"] == "unknown" and post_status["receipt"] is None
    assert post_status["resolution"] == resolution_status["resolution"]
    assert post_status["resolution"]["evidence"] == readback["evidence_file"]
    assert files(scene.board.parent) == resolved_files and scene.control["seen"] == calls
    stale = _call(scene, [*_args(scene), "--confirm", data["confirm_token"]], now=future)
    assert not stale["ok"] and scene.control["seen"] == calls
    assert _sha(scene.board) == expected_sha
    _put(
        scene.root / "RECOVERY-RESULT.json",
        {
            "kind": "source-fault-and-controlled-clock-recovery",
            "stage": stage,
            "operation_id": plan["operation_id"],
            "query": status,
            "blocked_preview": refused,
            "resolution": resolved,
            "post_resolve_query": post_resolve,
            "old_confirmation_refusal": stale,
            "real_native_processes": 0,
            "service_calls": calls,
            "formal_files_before": before,
            "formal_files_after": files(scene.board.parent),
            "no_preflight_regeneration": True,
        },
    )
