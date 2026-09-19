"""Backup, rollback and cross-process exclusion for board writes.

Importable as a package or a sibling script; standard library only.

Writes went straight to the board: ``board.Save(path)``, in place, with nothing
kept. If DRC then said the result was worse, the payload undid its own changes
object by object and saved again -- which works when the payload is running and
correct, and covers nothing else. A failure between the save and that repair,
or in the repair, left a modified board and an envelope that could only say
``write_state: unknown``.

The shape here is the one the payload already has, so nothing needed
restructuring: ``begin`` arms a transaction at the first save, ``K.ok`` commits
it, ``K.fail`` rolls it back, and an unhandled exception or Ctrl+C rolls it back
too. What survives a kill -9 is the journal: the next invocation finds it,
refuses to start a second write over a board whose first write never finished,
and says where the backup is. An interrupted write should be loud, not quietly
retried on top of itself.

Two locks are involved and they are not the same thing. KiCad's ``~*.lck`` says
the editor has the project open, and the layout commands already check it. This
one says another kicad-cli is mid-write, which nothing checked.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import sys
import time
from pathlib import Path

SUFFIX_LOCK = ".kicad-cli.lock"
SUFFIX_BACKUP = ".kicad-cli.backup"
SUFFIX_JOURNAL = ".kicad-cli.journal"
STALE_LOCK_SECONDS = 3600

_active: dict[str, dict] = {}
_installed = False
_state = "not_started"
_failure: str | None = None


class TxnError(Exception):
    """A transaction that cannot be started or completed safely."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def state() -> str:
    """What happened to the bytes on disk: reportable, not guessed."""
    return _state


def _paths(path: Path) -> tuple[Path, Path, Path]:
    return (
        path.with_name(path.name + SUFFIX_LOCK),
        path.with_name(path.name + SUFFIX_BACKUP),
        path.with_name(path.name + SUFFIX_JOURNAL),
    )


def _acquire(lock: Path, target: Path) -> None:
    try:
        handle = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        try:
            age = time.time() - lock.stat().st_mtime
            holder = lock.read_text(encoding="utf-8")[:200]
        except OSError:  # It vanished between the two calls; treat as contended.
            age, holder = 0.0, ""
        if age > STALE_LOCK_SECONDS:
            # An abandoned lock must not wedge the tool forever, but taking it
            # over is a decision, not a default: the journal check below is what
            # decides whether the board is safe to touch.
            try:
                lock.unlink()
                _acquire(lock, target)
                return
            except OSError:
                pass
        raise TxnError(
            "E_CONFLICT",
            "another kicad-cli write holds this board",
            {
                "board": str(target),
                "lock_file": str(lock),
                "held_by": holder,
                "age_s": int(age),
                "hint": "wait for it to finish, or remove the lock file if you are "
                "certain no other write is running",
            },
        ) from exc
    except OSError as exc:
        raise TxnError("E_IO", "cannot create the write lock", {"lock_file": str(lock)}) from exc
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump({"pid": os.getpid(), "started_at": time.time()}, stream)


def begin(path) -> None:
    """Arm a transaction for this board. Idempotent within one process."""
    global _state
    target = Path(path).resolve()
    key = str(target)
    if key in _active:
        return
    lock, backup, journal = _paths(target)
    if journal.exists():
        # Not ours: this process has nothing in _active for it. A previous run
        # died between arming and finishing, so the board may be half written.
        raise TxnError(
            "E_CONFLICT",
            "a previous write to this board did not finish; refusing to write over it",
            {
                "board": str(target),
                "journal": str(journal),
                "backup": str(backup) if backup.exists() else None,
                "recover": "compare the board against the backup, keep the one you want, "
                "then delete the journal and backup files",
                # Deciding this automatically would mean guessing whether the
                # interrupted write was wanted. That is the owner's call.
                "why_not_automatic": "restoring or keeping a half-finished write is a "
                "design decision, not a cleanup step",
            },
        )
    _acquire(lock, target)
    # Creating a file and changing one are both writes, and the difference is
    # what "undo" means. There is nothing to back up when the target does not
    # exist yet, and restoring it means removing what we made rather than
    # putting something back. `board from-netlist` is the first command that
    # creates a board, and it met this as "cannot take a backup before writing"
    # -- a true statement about a file that was never there.
    creating = not target.exists()
    try:
        if not creating:
            shutil.copy2(target, backup)
        journal.write_text(
            json.dumps(
                {
                    "board": str(target),
                    "backup": None if creating else str(backup),
                    "creating": creating,
                    "pid": os.getpid(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        lock.unlink(missing_ok=True)
        raise TxnError(
            "E_IO",
            "cannot prepare the write" if creating else "cannot take a backup before writing",
            {"board": str(target)},
        ) from exc
    _active[key] = {
        "lock": lock,
        "backup": backup,
        "journal": journal,
        "board": target,
        "creating": creating,
    }
    _state = "armed"
    _install_handlers()


def commit() -> None:
    """The write stands. Drop the backup and release the board."""
    global _state
    if not _active:
        return
    for entry in list(_active.values()):
        entry["journal"].unlink(missing_ok=True)
        entry["backup"].unlink(missing_ok=True)
        entry["lock"].unlink(missing_ok=True)
    _active.clear()
    _state = "committed"


RESTORE_ATTEMPTS = 5
RESTORE_BACKOFF_S = 0.1


def _restore(backup: Path, board: Path) -> OSError | None:
    """Replace the board with its backup, retrying a transient refusal.

    On Windows ``os.replace`` raises PermissionError while anything still holds
    the destination open -- a virus scanner, the search indexer, a child process
    whose handles have not been reaped yet. Those clear in milliseconds. Giving
    up on the first one means the rollback, which is the safety mechanism,
    loses to the most ordinary condition on the platform this tool is mostly
    used on, and the caller is told ``write_state: unknown`` about a board that
    could have been put back by waiting.
    """
    last: OSError | None = None
    for attempt in range(RESTORE_ATTEMPTS):
        try:
            os.replace(backup, board)
        except OSError as exc:
            last = exc
            if attempt + 1 < RESTORE_ATTEMPTS:
                time.sleep(RESTORE_BACKOFF_S * (attempt + 1))
            continue
        return None
    return last


def failure() -> str | None:
    """Why the last rollback could not finish, if it could not."""
    return _failure


def rollback(reason: str = "") -> list[str]:
    """Put the board back as it was. Safe to call twice."""
    global _state, _failure
    restored = []
    for key, entry in list(_active.items()):
        if entry.get("creating"):
            # Undoing a creation is removing what was created. A target that is
            # not there means the write never got far enough to make it, which
            # is `not_started` rather than a rollback.
            try:
                if entry["board"].exists():
                    entry["board"].unlink()
                    restored.append(key)
            except OSError as error:
                _failure = f"{type(error).__name__}: {error}"
                _state = "unknown"
                continue
        elif entry["backup"].exists():
            error = _restore(entry["backup"], entry["board"])
            if error is not None:
                # The journal stays, so the next invocation refuses rather than
                # writing over a board we could not put back. The reason travels
                # with it: "unknown" with no cause attached is the hardest state
                # to act on, and the hardest to diagnose after the fact.
                _failure = f"{type(error).__name__}: {error}"
                _state = "unknown"
                continue
            restored.append(key)
        entry["journal"].unlink(missing_ok=True)
        entry["lock"].unlink(missing_ok=True)
        _active.pop(key, None)
    if _state != "unknown":
        _state = "rolled_back" if restored else "not_started"
    return restored


def _on_signal(signum, _frame):
    rollback(f"signal {signum}")
    # Re-raise as the default action so the exit status still says "killed",
    # rather than reporting a tidy failure for something nobody asked for.
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


def _on_exception(kind, value, traceback):
    rollback("unhandled exception")
    sys.__excepthook__(kind, value, traceback)


def _install_handlers() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    sys.excepthook = _on_exception
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        handler = getattr(signal, name, None)
        if handler is None:
            continue
        try:
            signal.signal(handler, _on_signal)
        except (OSError, ValueError, RuntimeError):
            # Not the main thread, or the platform will not deliver it. The
            # journal still covers the case where nothing gets to run.
            continue
