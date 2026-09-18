"""A failed write must put the board back, and say which of those happened.

Fault injection against the real module; no KiCad and no pcbnew. The board is a
stand-in file, because what is under test is the transaction, not the geometry.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from kicad_cli.payload import write_txn  # noqa: E402

ORIGINAL = "(kicad_pcb original)"


@pytest.fixture(autouse=True)
def _isolated_transaction_state(monkeypatch):
    """Reset the module's per-process state around every test in this file.

    `write_txn` keeps `_active` and `_state` at module scope, and anything that
    reaches `kicad_lib.fail` in this interpreter moves them -- `test_drc_runner`
    does, for one. Resetting only on the way in leaves a test that arms a
    transaction and does not finish it able to reach the next file. Autouse and
    symmetric, so neither direction can carry.
    """
    monkeypatch.setattr(write_txn, "_active", {})
    monkeypatch.setattr(write_txn, "_state", "not_started")
    monkeypatch.setattr(write_txn, "_installed", False)
    monkeypatch.setattr(write_txn, "_failure", None)
    yield
    write_txn._active.clear()


@pytest.fixture
def board(tmp_path):
    path = tmp_path / "demo.kicad_pcb"
    path.write_text(ORIGINAL, encoding="utf-8")
    return path


def leftovers(path: Path) -> list[str]:
    return sorted(p.name for p in path.parent.iterdir() if "kicad-cli" in p.name)


def test_a_commit_keeps_the_write_and_leaves_nothing_behind(board):
    write_txn.begin(board)
    board.write_text("(kicad_pcb widened)", encoding="utf-8")
    write_txn.commit()
    assert board.read_text(encoding="utf-8") == "(kicad_pcb widened)"
    assert write_txn.state() == "committed"
    assert leftovers(board) == []


def test_a_rollback_restores_the_bytes_that_were_there(board):
    write_txn.begin(board)
    board.write_text("(kicad_pcb half written", encoding="utf-8")
    write_txn.rollback("E_IO")
    assert board.read_text(encoding="utf-8") == ORIGINAL
    assert write_txn.state() == "rolled_back"
    assert leftovers(board) == []


def test_rollback_without_a_write_is_not_reported_as_one(board):
    """`not_started` and `rolled_back` are different facts about the disk."""
    write_txn.rollback("E_USAGE")
    assert write_txn.state() == "not_started"


def test_rollback_is_safe_to_call_twice(board):
    write_txn.begin(board)
    board.write_text("(kicad_pcb changed)", encoding="utf-8")
    write_txn.rollback("first")
    write_txn.rollback("second")
    assert board.read_text(encoding="utf-8") == ORIGINAL


def test_arming_twice_does_not_overwrite_the_original_backup(board):
    """The backup must be the state before the *first* write, not the last one."""
    write_txn.begin(board)
    board.write_text("(kicad_pcb pass one)", encoding="utf-8")
    write_txn.begin(board)  # pcb_widen saves once per trial round
    board.write_text("(kicad_pcb pass two)", encoding="utf-8")
    write_txn.rollback("E_IO")
    assert board.read_text(encoding="utf-8") == ORIGINAL


def test_a_second_writer_is_refused_while_one_holds_the_board(board):
    write_txn.begin(board)
    lock, _backup, _journal = write_txn._paths(board.resolve())
    assert lock.exists()
    with pytest.raises(write_txn.TxnError) as exc:
        write_txn._acquire(lock, board)
    assert exc.value.code == "E_CONFLICT"
    assert "lock_file" in exc.value.details


def test_an_unfinished_previous_write_refuses_rather_than_writing_over_it(board):
    """kill -9 leaves the journal. The next run must not write on top of it."""
    write_txn.begin(board)
    board.write_text("(kicad_pcb half written", encoding="utf-8")
    lock, backup, _journal = write_txn._paths(board.resolve())
    lock.unlink()  # The process died; the OS released nothing else.
    write_txn._active.clear()

    with pytest.raises(write_txn.TxnError) as exc:
        write_txn.begin(board)
    assert exc.value.code == "E_CONFLICT"
    assert exc.value.details["backup"] == str(backup)
    assert "recover" in exc.value.details
    # The half-written board is left exactly as found: deciding between it and
    # the backup is the owner's call, and guessing would be the worse error.
    assert board.read_text(encoding="utf-8") == "(kicad_pcb half written"


def test_a_stale_lock_does_not_wedge_the_tool_forever(board):
    """An abandoned lock must not make the board permanently unwritable."""
    lock, backup, journal = write_txn._paths(board.resolve())
    lock.write_text('{"pid": 1}', encoding="utf-8")
    os.utime(lock, (0.0, 0.0))  # Far older than STALE_LOCK_SECONDS.

    write_txn.begin(board)
    assert write_txn.state() == "armed"
    assert journal.exists() and backup.exists()
    board.write_text("(kicad_pcb widened)", encoding="utf-8")
    write_txn.commit()
    assert board.read_text(encoding="utf-8") == "(kicad_pcb widened)"
    assert leftovers(board) == []


def test_a_fresh_lock_is_not_treated_as_stale(board):
    """The takeover is bounded by age; a live writer keeps the board."""
    lock, _backup, _journal = write_txn._paths(board.resolve())
    lock.write_text('{"pid": 1}', encoding="utf-8")
    with pytest.raises(write_txn.TxnError) as exc:
        write_txn.begin(board)
    assert exc.value.code == "E_CONFLICT"
    assert write_txn.state() == "not_started"


def test_an_unreadable_target_fails_before_it_locks_anything(board):
    missing = board.parent / "absent.kicad_pcb"
    with pytest.raises(write_txn.TxnError) as exc:
        write_txn.begin(missing)
    assert exc.value.code == "E_IO"
    assert leftovers(board) == []


def test_an_unhandled_exception_rolls_back_in_a_real_process(tmp_path):
    """The handlers only matter if they are installed in a process that dies."""
    target = tmp_path / "demo.kicad_pcb"
    target.write_text(ORIGINAL, encoding="utf-8")
    script = tmp_path / "crash.py"
    script.write_text(
        textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(REPO / "kicad_cli" / "payload")!r})
            import write_txn
            write_txn.begin({str(target)!r})
            open({str(target)!r}, "w").write("(kicad_pcb destroyed")
            raise RuntimeError("upstream blew up mid-write")
        """),
        encoding="utf-8",
    )
    proc = subprocess.run([sys.executable, str(script)], capture_output=True, timeout=60)
    assert proc.returncode != 0
    assert target.read_text(encoding="utf-8") == ORIGINAL
    assert sorted(p.name for p in tmp_path.iterdir() if "kicad-cli" in p.name) == []


def test_the_payload_envelope_reports_the_write_state_on_failure(tmp_path):
    """`write_state: unknown` was the only answer available before this."""
    target = tmp_path / "demo.kicad_pcb"
    target.write_text(ORIGINAL, encoding="utf-8")
    script = tmp_path / "failing.py"
    script.write_text(
        textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(REPO / "kicad_cli" / "payload")!r})
            import kicad_lib as K
            K.begin_write({str(target)!r})
            open({str(target)!r}, "w").write("(kicad_pcb changed)")
            K.fail("E_IO", "upstream failed after the save")
        """),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        timeout=60,
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
    )
    doc = json.loads(proc.stdout.decode("utf-8").splitlines()[0])
    assert doc["error"]["details"]["write_state"] == "rolled_back"
    assert target.read_text(encoding="utf-8") == ORIGINAL


def test_the_payload_envelope_reports_a_commit_on_success(tmp_path):
    target = tmp_path / "demo.kicad_pcb"
    target.write_text(ORIGINAL, encoding="utf-8")
    script = tmp_path / "succeeding.py"
    script.write_text(
        textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(REPO / "kicad_cli" / "payload")!r})
            import kicad_lib as K
            K.begin_write({str(target)!r})
            open({str(target)!r}, "w").write("(kicad_pcb widened)")
            K.ok({{"done": True}})
        """),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        timeout=60,
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
    )
    doc = json.loads(proc.stdout.decode("utf-8").splitlines()[0])
    assert doc["ok"] is True
    assert doc["meta"]["write_state"] == "committed"
    assert target.read_text(encoding="utf-8") == "(kicad_pcb widened)"


def test_a_transient_refusal_to_restore_is_retried(board, monkeypatch):
    """Windows refuses os.replace while anything still holds the destination.

    Those clear in milliseconds, and the rollback is the safety mechanism -- it
    should not lose to the most ordinary condition on the platform this tool is
    mostly used on.
    """
    write_txn.begin(board)
    board.write_text("(kicad_pcb half written", encoding="utf-8")
    real = os.replace
    calls = []

    def flaky(src, dst):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(32, "being used by another process")
        return real(src, dst)

    monkeypatch.setattr(write_txn.os, "replace", flaky)
    monkeypatch.setattr(write_txn, "RESTORE_BACKOFF_S", 0.001)

    write_txn.rollback("E_IO")
    assert len(calls) == 3
    assert board.read_text(encoding="utf-8") == ORIGINAL
    assert write_txn.state() == "rolled_back"
    assert write_txn.failure() is None


def test_a_persistent_refusal_says_why_and_keeps_the_journal(board, monkeypatch):
    """`unknown` with no cause attached is the hardest state to act on."""
    write_txn.begin(board)
    board.write_text("(kicad_pcb half written", encoding="utf-8")
    _lock, _backup, journal = write_txn._paths(board.resolve())

    def always_refuse(_src, _dst):
        raise PermissionError(32, "being used by another process")

    monkeypatch.setattr(write_txn.os, "replace", always_refuse)
    monkeypatch.setattr(write_txn, "RESTORE_BACKOFF_S", 0.001)

    write_txn.rollback("E_IO")
    assert write_txn.state() == "unknown"
    assert "PermissionError" in write_txn.failure()
    # The journal survives, so the next invocation refuses to write over a
    # board nobody could put back.
    assert journal.exists()
