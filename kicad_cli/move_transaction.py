"""Durable, operation-owned move journal; never overwrite an unrecognised edit."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

SUFFIXES = (".kicad-cli.lock", ".kicad-cli.backup", ".kicad-cli.journal")
OWNER = "kicad-cli.driver-move.v2"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def regular(path: Path) -> None:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & 0x400
        or info.st_nlink > 1
    ):
        raise OSError("target or transaction record is not a single regular file")


def paths(path: Path) -> tuple[Path, Path, Path]:
    return tuple(path.with_name(path.name + suffix) for suffix in SUFFIXES)


def write_new(path: Path, payload: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def process_alive(pid: int) -> bool:
    """Unknown process ownership is treated as live, including access refusal."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER: no such PID
        try:
            return kernel.WaitForSingleObject(handle, 0) != 0
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def owned_journal(path: Path, operation: str) -> dict | None:
    lock, backup, journal = paths(path)
    if not journal.exists():
        if lock.exists() or backup.exists():
            raise OSError("unattributed transaction sidecars require manual inspection")
        return None
    regular(journal)
    if journal.stat().st_size > 64 * 1024:
        raise OSError("transaction journal is too large")
    record = json.loads(journal.read_bytes())
    if (
        record.get("owner") != OWNER
        or record.get("operation_id") != operation
        or record.get("board") != str(path)
    ):
        raise OSError("transaction belongs to another operation")
    regular(lock)
    holder = json.loads(lock.read_bytes())
    if holder.get("operation_id") != operation or holder.get("pid") != record.get("pid"):
        raise OSError("transaction lock identity differs from journal")
    regular(backup)
    if digest(backup) != record.get("baseline"):
        raise OSError("transaction backup no longer matches its bound baseline")
    return record


class MoveTransaction:
    def __init__(self, path: Path, operation: str, baseline: str, intended: str):
        self.path, self.operation = path, operation
        self.baseline, self.intended = baseline, intended
        self.lock, self.backup, self.journal = paths(path)
        self.armed = False

    def arm(self) -> None:
        regular(self.path)
        if self.journal.exists() or self.backup.exists():
            raise FileExistsError("an unfinished transaction already exists; inspect it first")
        write_new(
            self.lock, json.dumps({"pid": os.getpid(), "operation_id": self.operation}).encode()
        )
        created_backup = False
        try:
            payload = self.path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != self.baseline:
                raise ValueError("target changed before the move lock was acquired")
            write_new(self.backup, payload)
            created_backup = True
            record = {
                "owner": OWNER,
                "operation_id": self.operation,
                "pid": os.getpid(),
                "board": str(self.path),
                "baseline": self.baseline,
                "intended": self.intended,
            }
            write_new(self.journal, json.dumps(record).encode())
        except BaseException:
            # Only files this arm created may be removed; no target restore here.
            if created_backup:
                self.backup.unlink(missing_ok=True)
            self.lock.unlink(missing_ok=True)
            raise
        self.armed = True

    def cleanup(self) -> None:
        # Journal last: an interrupted cleanup remains visible and fails closed.
        self.backup.unlink(missing_ok=True)
        self.lock.unlink(missing_ok=True)
        self.journal.unlink(missing_ok=True)
        self.armed = False

    def rollback(self) -> None:
        current = digest(self.path)
        if current not in {self.baseline, self.intended}:
            raise OSError(
                "current bytes are not this writer's prepared bytes; preserve external edit"
            )
        regular(self.backup)
        if digest(self.backup) != self.baseline:
            raise OSError("backup no longer matches the baseline")
        if current != self.baseline:
            os.replace(self.backup, self.path)
        if digest(self.path) != self.baseline:
            raise OSError("restored bytes do not match the baseline")
        self.cleanup()
