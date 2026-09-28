"""Changing a board from this process: the transaction and the save.

The same promises the payloads made, kept by the same code: `write_txn` is
standard library only and moves out of `payload/` when the payloads go.

- A board is backed up and locked before its first byte changes, and a
  journal survives a kill, so the next invocation refuses to write over an
  unfinished write rather than quietly carrying on.
- Every failure after `start()` -- a refusal, a check that failed, a crash
  turned into E_UNKNOWN -- restores the board first and then reports
  `write_state`: `not_started`, `rolled_back`, or `unknown` with the reason.
- Success commits, and says so when nothing was written.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import envelope
from ..fileformat.board import Board
from ..payload import write_txn


def _roll_back(_code: str, details: dict[str, Any]) -> None:
    write_txn.rollback("failure")
    details["write_state"] = write_txn.state()
    if write_txn.failure():
        # Present only when the board could not be put back, and then it is
        # the first thing worth reading: retry, or go and look.
        details["rollback_error"] = write_txn.failure()


def start() -> None:
    """From here on this command writes, and every failure says what became of the board."""
    envelope.on_fail(_roll_back)


def begin(path: str | Path) -> None:
    """Back up and lock the file before changing it; idempotent."""
    try:
        write_txn.begin(path)
    except write_txn.TxnError as exc:
        envelope.fail(exc.code, str(exc), exc.details)


def save(board: Board) -> None:
    """Write the board back: only what changed is re-rendered."""
    begin(board.path)
    board.document.save(board.path)


def done(data: Any) -> None:
    """Commit and report. A write command that wrote nothing says so."""
    write_txn.commit()
    state = write_txn.state()
    notices = [{"code": "write_state", "message": state}] if state != "committed" else None
    envelope.ok(data, notices)
