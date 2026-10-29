"""Command-level saved-file checks and real fault injection on scratch boards."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from driver_test_utils import grant

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from kicad_cli import driver_runtime, move_readback  # noqa: E402

FIXTURE = REPO / "tests/fixtures/sch_edit/edit.kicad_pcb"
R1 = "2d4aa815-63ab-5983-950d-99c5287f15a2"


@pytest.fixture
def board(tmp_path):
    target = tmp_path / "edit.kicad_pcb"
    shutil.copyfile(FIXTURE, target)
    (tmp_path / "edit.kicad_pro").write_text('{"untouched":true}', encoding="utf-8")
    return target


def run(board, moves="R1:101.25,100.5", *, token=None, fault=""):
    env = dict(
        os.environ, KICAD_CLI_STRICT="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1"
    )
    state = board.parent / "state"
    state.mkdir(exist_ok=True)
    env.update(TEMP=str(state), TMP=str(state), KICAD_CLI_STATE=str(state))
    scope = board.parent / "move-scope.json"
    if not scope.exists():
        grant(board.parent)
    env.update(
        KICAD_CLI_SCOPE_FILE=str(scope), KICAD_CLI_STATE_DIR=str(board.parent / "driver-state")
    )
    argv = ["board", "footprint", "move", "--board", str(board), "--moves", moves, "--compact"]
    if token:
        argv += ["--confirm", token]
    else:
        argv += ["--dry-run"]
    prefix = [sys.executable, "-B", "-m", "kicad_cli.main"]
    if fault:
        prefix = [
            sys.executable,
            "-B",
            "-c",
            textwrap.dedent(fault)
            + "\nfrom kicad_cli.main import main\nmain(__import__('sys').argv[1:])",
        ]
    proc = subprocess.run([*prefix, *argv], cwd=REPO, env=env, capture_output=True, timeout=30)
    stderr = proc.stderr.decode("utf-8", "replace")
    doc = json.loads(proc.stdout.decode("utf-8"))
    assert "contract violation" not in stderr, stderr
    codes = driver_runtime.declarations()["reference"]["error_codes"]
    assert proc.returncode == (0 if doc["ok"] else codes[doc["error"]["code"]]["exit"]), (
        doc,
        stderr,
    )
    return doc


def confirm(board, *, moves="R1:101.25,100.5", fault=""):
    asked = run(board, moves)
    assert asked["ok"], asked
    return run(board, moves, token=asked["data"]["confirm_token"], fault=fault)


def test_saved_objects_are_measured_and_sidecar_is_untouched(board):
    original = move_readback.observe(board, ["R1"])
    sidecar = board.with_suffix(".kicad_pro").read_bytes()
    result = confirm(board)
    assert result["ok"], result
    receipt = result["data"]["receipt"]
    ext = receipt["extensions"]
    actual = move_readback.observe(board, ["R1"])
    assert ext["observed"] == actual.objects
    assert ext["observed"][0]["id"] == R1
    assert ext["observed"][0]["position_nm"] == [101250000, 100500000]
    assert ext["source_after"]["kind"] == "independent_consumer"
    assert ext["source_after"]["completed"] is True
    assert ext["source_after"]["observed_at"].endswith("Z")
    assert ext["source_after"]["input_sha256"] == hashlib.sha256(board.read_bytes()).hexdigest()
    assert receipt["outcome"]["verification"]["status"] == "passed"
    assert receipt["outcome"]["persistence"] == "saved"
    assert receipt["snapshot_before"]["revision"] == original.snapshot["revision"]
    assert receipt["snapshot_after"]["revision"] == actual.snapshot["revision"]
    assert actual.protected_sha256 == original.protected_sha256
    assert board.with_suffix(".kicad_pro").read_bytes() == sidecar
    assert "DRC" in ext["coverage"]["not_checked"]


def test_quantisation_is_visible_in_actual_not_echoed_from_plan(board):
    doc = confirm(board, moves="R1:1.005,100.5")
    assert doc["ok"], doc
    ext = doc["data"]["receipt"]["extensions"]
    assert ext["expected"][0]["position_mm"][0] == 1.005
    assert ext["observed"][0]["position_nm"][0] == 1004999
    assert ext["observed"][0]["position_mm"][0] == 1.004999
    assert ext["units"]["position_tolerance_nm"] == 1


def test_no_change_is_saved_as_unchanged(board):
    doc = confirm(board, moves="R1:100,100")
    assert doc["ok"], doc
    receipt = doc["data"]["receipt"]
    assert receipt["outcome"]["change"] == "not_applied"
    assert receipt["outcome"]["persistence"] == "unchanged"
    assert receipt["changed_items"] == []


def test_multiple_objects_are_observed_with_stable_identities(board):
    result = confirm(board, moves="R1:101.25,100.5;R2:105.5,100.75")
    assert result["ok"], result
    ext = result["data"]["receipt"]["extensions"]
    assert [item["ref"] for item in ext["observed"]] == ["R1", "R2"]
    assert len({item["id"] for item in ext["observed"]}) == 2


def test_uuid_absence_is_disclosed_as_content_identity(board):
    board.write_bytes(board.read_bytes().replace(f'(uuid "{R1}")'.encode(), b""))
    result = confirm(board)
    assert result["ok"], result
    actual = result["data"]["receipt"]["extensions"]["observed"][0]
    assert actual["identity_kind"] == "content_sha256"
    assert actual["id"].startswith("content:")


FAULTS = [
    (
        "ignored_move",
        "E_NATIVE_FAILURE",
        """
        from kicad_cli.native import move
        move.move_footprint = lambda *args: None
    """,
    ),
    (
        "automatic_adjustment",
        "E_NATIVE_FAILURE",
        """
        from kicad_cli.native import move
        original = move.move_footprint
        def adjusted(board, footprint, x, y):
            original(board, footprint, x + 10000, y)
        move.move_footprint = adjusted
    """,
    ),
    (
        "ignored_save",
        "E_NATIVE_FAILURE",
        """
        from kicad_cli.fileformat.sexpr import Document
        Document.save = lambda *args: None
    """,
    ),
    (
        "partial_save_error",
        "E_OUTCOME_UNKNOWN",
        """
        from pathlib import Path
        from kicad_cli.fileformat.sexpr import Document
        def failed_save(self, path):
            Path(path).write_bytes(b'(kicad_pcb half_written')
            raise OSError('injected disk failure')
        Document.save = failed_save
    """,
    ),
    (
        "corrupt_saved_file",
        "E_OUTCOME_UNKNOWN",
        """
        from pathlib import Path
        from kicad_cli.fileformat.sexpr import Document
        Document.save = lambda self, path: Path(path).write_bytes(b'(kicad_pcb truncated')
    """,
    ),
    (
        "read_error_once",
        "E_NATIVE_FAILURE",
        """
        from kicad_cli import move_readback
        original = move_readback.observe
        calls = 0
        def unreadable(path, references):
            global calls
            calls += 1
            if calls == 1:
                raise PermissionError('injected post-save read denial')
            return original(path, references)
        move_readback.observe = unreadable
    """,
    ),
    (
        "read_and_restore_observation_error",
        "E_OUTCOME_UNKNOWN",
        """
        from kicad_cli import move_readback
        original = move_readback.observe
        calls = 0
        def unreadable(path, references):
            global calls
            calls += 1
            if calls > 0:
                raise PermissionError('injected continuing read denial')
            return original(path, references)
        move_readback.observe = unreadable
    """,
    ),
]


@pytest.mark.parametrize("name,code,fault", FAULTS, ids=[item[0] for item in FAULTS])
def test_failed_writes_never_report_success_and_restoration_is_observed(board, name, code, fault):
    original = board.read_bytes()
    doc = confirm(board, fault=fault)
    assert doc["ok"] is False, (name, doc)
    assert doc["error"]["code"] == code, doc
    details = doc["error"]["details"]
    receipt = details["receipt"]
    assert details["dispatch_state"] == "dispatched"
    assert receipt["outcome"]["execution"] == "failed"
    if name == "read_and_restore_observation_error" and board.read_bytes() != original:
        # A real Windows refusal can prevent the attempted restoration. It is
        # not rollback: require the actual unknown state, attributable baseline
        # and a blocked followup. Preserve this scene instead of retrying it.
        assert "WinError 5" in receipt["extensions"]["restoration_error"], doc
        assert receipt["outcome"]["persistence"] == "unknown"
        assert receipt["extensions"]["restoration"] is None
        assert Path(str(board) + ".kicad-cli.backup").read_bytes() == original
        assert Path(str(board) + ".kicad-cli.journal").is_file()
        current = {p: p.read_bytes() for p in board.parent.iterdir() if p.is_file()}
        blocked = run(board)
        assert blocked["error"]["code"] in {"E_CONFLICT", "E_OUTCOME_UNKNOWN"}, blocked
        assert {p: p.read_bytes() for p in current} == current
    elif name not in {"partial_save_error", "corrupt_saved_file"}:
        assert board.read_bytes() == original
    if code == "E_OUTCOME_UNKNOWN":
        assert receipt["outcome"]["change"] == "unknown"
        assert details["write_state"] == "unknown"
        assert receipt["extensions"]["restoration_error"]
    else:
        assert receipt["outcome"]["change"] in {"rolled_back", "not_applied"}
        assert receipt["extensions"]["restoration"]["matches_baseline"] is True
        assert receipt["extensions"]["restoration"]["source"]["completed"] is True
    if name == "automatic_adjustment":
        assert receipt["extensions"]["observed"][0]["position_nm"][0] == 101260000
        assert receipt["extensions"]["expected"][0]["position_nm"][0] == 101250000


@pytest.mark.parametrize("kind", ["identity", "angle", "layer", "protected_content"])
def test_unintended_saved_properties_are_detected(board, kind):
    replacements = {
        "identity": (R1, "9d4aa815-63ab-5983-950d-99c5287f15a2"),
        "angle": ("(at 101.25 100.5)", "(at 101.25 100.5 90)"),
        "layer": ('(layer "F.Cu")', '(layer "B.Cu")'),
        "protected_content": ("(thickness 1.6)", "(thickness 1.7)"),
    }
    old, new = replacements[kind]
    fault = f"""
from pathlib import Path
from kicad_cli.fileformat.sexpr import Document
original = Document.save
def tampered(self, path):
    original(self, path)
    p = Path(path)
    p.write_bytes(p.read_bytes().replace({old.encode()!r}, {new.encode()!r}, 1))
Document.save = tampered
"""
    before = board.read_bytes()
    doc = confirm(board, fault=fault)
    assert doc["error"]["code"] == "E_OUTCOME_UNKNOWN", doc
    assert doc["error"]["details"]["receipt"]["extensions"]["failures"]
    assert board.read_bytes() != before


def test_failed_restoration_retains_evidence_and_reports_unknown(board):
    fault = """
        from kicad_cli.native import move
        from kicad_cli.move_transaction import MoveTransaction
        original = move.move_footprint
        def adjusted(board, footprint, x, y):
            original(board, footprint, x + 10000, y)
        move.move_footprint = adjusted
        def failed_restore(self):
            raise OSError('injected restore failure')
        MoveTransaction.rollback = failed_restore
    """
    before = board.read_bytes()
    doc = confirm(board, fault=fault)
    assert doc["error"]["code"] == "E_OUTCOME_UNKNOWN", doc
    assert doc["error"]["details"]["receipt"]["outcome"]["persistence"] == "unknown"
    assert board.read_bytes() != before
    assert Path(str(board) + ".kicad-cli.backup").read_bytes() == before
    assert Path(str(board) + ".kicad-cli.journal").is_file()


def test_os_replace_permission_denial_stops_and_preserves_same_target(board):
    """Synthetic WinError5-shaped refusal; not a retry of the preserved real denial."""
    fault = """
        from kicad_cli import move_readback, move_transaction
        original = move_readback.observe
        calls = 0
        def unreadable(path, references):
            global calls
            calls += 1
            if calls == 1:
                raise PermissionError('injected first read denial')
            return original(path, references)
        move_readback.observe = unreadable
        def refused(source, destination):
            raise PermissionError(13, '[WinError 5] injected access denied', str(source))
        move_transaction.os.replace = refused
    """
    original = board.read_bytes()
    failed = confirm(board, fault=fault)
    assert failed["error"]["code"] == "E_OUTCOME_UNKNOWN", failed
    receipt = failed["error"]["details"]["receipt"]
    assert receipt["outcome"]["change"] == "unknown"
    assert "WinError 5" in receipt["extensions"]["restoration_error"]
    backup = Path(str(board) + ".kicad-cli.backup")
    assert backup.read_bytes() == original and board.read_bytes() != original
    protected = {p: p.read_bytes() for p in board.parent.iterdir() if p.is_file()}
    blocked = run(board)
    assert blocked["error"]["code"] in {"E_CONFLICT", "E_OUTCOME_UNKNOWN"}, blocked
    assert {p: p.read_bytes() for p in protected} == protected


def test_drift_after_observation_is_not_committed_as_verified(board):
    fault = """
        from pathlib import Path
        from kicad_cli import move_readback
        original = move_readback.observe
        calls = 0
        def observed_then_changed(path, references):
            global calls
            result = original(path, references)
            calls += 1
            if calls == 1:
                p = Path(path)
                p.write_bytes(p.read_bytes().replace(b'(at 101.25 100.5)', b'(at 102.25 100.5)'))
            return result
        move_readback.observe = observed_then_changed
    """
    before = board.read_bytes()
    doc = confirm(board, fault=fault)
    assert doc["error"]["code"] == "E_OUTCOME_UNKNOWN", doc
    assert "changed after" in doc["error"]["details"]["receipt"]["extensions"]["failure_reason"]
    assert board.read_bytes() != before


def test_change_before_lock_is_preserved_and_not_claimed_as_our_move(board):
    fault = """
        from pathlib import Path
        from kicad_cli.move_transaction import MoveTransaction
        original = MoveTransaction.arm
        def concurrent(self):
            p = self.path
            p.write_bytes(p.read_bytes().replace(b'(thickness 1.6)', b'(thickness 1.7)'))
            original(self)
        MoveTransaction.arm = concurrent
    """
    doc = confirm(board, fault=fault)
    assert doc["error"]["code"] == "E_CONFLICT", doc
    assert b"(thickness 1.7)" in board.read_bytes()
    assert doc["error"]["details"]["dispatch_state"] == "not_started"


@pytest.mark.parametrize(
    "moves", ["R1:nan,0", "R1:inf,0", "R1:-inf,0", "R1:1e308,0", "R1:1,2;R1:3,4", ":1,2", "; ;"]
)
def test_bad_move_is_rejected_before_write(board, moves):
    before = board.read_bytes()
    doc = run(board, moves)
    assert doc["error"]["code"] == "E_VALIDATION", doc
    assert board.read_bytes() == before


def test_ambiguous_reference_is_not_silently_selected(board):
    board.write_bytes(board.read_bytes().replace(b'"R2"', b'"R1"'))
    before = board.read_bytes()
    doc = run(board)
    assert doc["error"]["code"] == "E_VALIDATION", doc
    assert doc["error"]["details"]["refs"] == ["R1"]
    assert board.read_bytes() == before


def test_duplicate_uuid_is_rejected_before_write(board):
    # Discover the actual other ID without assuming this fixture's UUID.
    other = move_readback.observe(board, ["R2"]).objects[0]["id"]
    board.write_bytes(board.read_bytes().replace(other.encode(), R1.encode()))
    before = board.read_bytes()
    doc = run(board)
    assert doc["error"]["code"] == "E_VALIDATION", doc
    assert board.read_bytes() == before


def test_reader_ignores_nested_and_quoted_fake_coordinates(board):
    text = board.read_bytes().replace(
        b'(property "Reference" "R1"',
        b'(property "Value" "text (at 0 0)" (at 0 0) (layer "F.Fab"))\n(property "Reference" "R1"',
        1,
    )
    board.write_bytes(text)
    record = move_readback.observe(board, ["R1"]).objects[0]
    assert record["position_nm"] == [100000000, 100000000]
