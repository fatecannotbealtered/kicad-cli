"""Tool-local state (COR-021): the confirm-token secret and the operation record store.

Records live in one SQLite file under the tool state directory. When the store cannot be read
or written, callers refuse to dispatch (LAY-007): a missing ledger is never treated as empty.

A dispatched operation holds a lease that the running CLI renews; a submitted long operation holds
one that each successful status observation renews. When nobody renews it, the record reads as
unknown instead of running forever (DRV-035).
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import stat
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .scope import file_path_of

TABLES = """
CREATE TABLE IF NOT EXISTS operations (
    operation_id TEXT PRIMARY KEY,
    plan_id TEXT,
    capability_id TEXT NOT NULL,
    command TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'single',
    batch_id TEXT,
    item_index INTEGER,
    item_key TEXT,
    target_key TEXT,
    request_digest TEXT,
    scope_id TEXT,
    idempotency_key TEXT,
    status TEXT NOT NULL,
    effects TEXT NOT NULL,
    plan TEXT,
    receipt TEXT,
    extra TEXT,
    resolution TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    plan_expires_at REAL,
    lease_until REAL
);
CREATE TABLE IF NOT EXISTS consumed_tokens (
    fingerprint TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL,
    consumed_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS scope_usage (
    scope_id TEXT PRIMARY KEY,
    used INTEGER NOT NULL
);
"""
INDEXES = """
CREATE INDEX IF NOT EXISTS operations_target ON operations(target_key, status);
CREATE INDEX IF NOT EXISTS operations_key ON operations(idempotency_key);
CREATE INDEX IF NOT EXISTS operations_batch ON operations(batch_id, item_index);
"""
COLUMNS = {"kind": "TEXT NOT NULL DEFAULT 'single'", "batch_id": "TEXT", "item_index": "INTEGER", "item_key": "TEXT",
           "lease_until": "REAL"}
# Statuses that keep a target busy until they finish or are resolved (DRV-041). "dispatched" is a
# synchronous apply in flight: running while its lease is live, unknown once the lease has expired.
ACTIVE = ("dispatched", "pending", "running", "cancel_requested", "unknown")
LIVE = ("pending", "running", "cancel_requested")
FINAL = ("succeeded", "partial", "failed", "cancelled", "unknown")
JSON_FIELDS = ("effects", "plan", "receipt", "extra", "resolution")
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class StoreError(Exception):
    """The record store is unavailable; nothing that needs it may be dispatched."""


class TokenUsed(Exception):
    """The confirm token was already consumed, or its plan is no longer waiting for confirmation."""


class Blocked(Exception):
    """Another operation on the same target is still running or has an unresolved outcome."""

    def __init__(self, record: dict, live: bool):
        super().__init__(record["operation_id"])
        self.record = record
        self.live = live


class KeyUsed(Exception):
    """The idempotency key belongs to an earlier operation; the token was consumed without dispatching."""

    def __init__(self, record: dict):
        super().__init__(record["operation_id"])
        self.record = record


class KeyMismatch(Exception):
    """The idempotency key was used with different arguments."""

    def __init__(self, record: dict):
        super().__init__(record["operation_id"])
        self.record = record


class BudgetSpent(Exception):
    """The task scope's limits.max_operations would be exceeded."""

    def __init__(self, used: int, limit: int):
        super().__init__(f"{used} of {limit}")
        self.used = used
        self.limit = limit


class LeaseHeld(Exception):
    """The operation is still running under a live lease."""

    def __init__(self, record: dict):
        super().__init__(record["operation_id"])
        self.record = record


def target_key(target: dict | None) -> str | None:
    """Identity used to serialize writes: mode, engine, instance and the normalized locator."""
    if not target:
        return None
    locator = str(target.get("locator", ""))
    try:
        path = file_path_of(locator)
    except ValueError:
        path = None
    if path:
        locator = "file:" + os.path.normcase(os.path.realpath(path))
    return "|".join(str(target.get(key)) for key in ("mode", "engine_id", "instance_id")) + "|" + locator


def effective_status(record: dict, now: float) -> str:
    """The stored status, with an expired lease read as unknown: nobody is observing the operation any more."""
    status = record["status"]
    lease = record.get("lease_until")
    if status == "dispatched":
        return "running" if lease is not None and lease > now else "unknown"
    if status in LIVE and lease is not None and lease <= now:
        return "unknown"
    return status


def provably_not_started(record: dict) -> bool:
    """True when the record shows that nothing reached the native side."""
    extra = record.get("extra") or {}
    resolution = record.get("resolution") or {}
    if record["status"] == "planned":
        return True
    if record["status"] == "failed" and record.get("receipt") is None and extra.get("dispatch_state") == "not_started":
        return True
    if resolution.get("outcome") == "not_applied" and record.get("receipt") is None:
        return True
    receipt = record.get("receipt") or {}
    return (receipt.get("outcome") or {}).get("execution") == "not_started"


def _not_linked(path: Path) -> os.stat_result | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & REPARSE_POINT:
        raise StoreError(f"state path is a link or reparse point: {path}")
    return info


def _decode(row) -> dict:
    value = dict(row)
    try:
        for key in JSON_FIELDS:
            if value.get(key) is not None:
                value[key] = json.loads(value[key])
    except (ValueError, RecursionError) as exc:
        raise StoreError(f"the operation store holds a damaged record: {value.get('operation_id')}") from exc
    return value


class StateStore:
    def __init__(self, directory: Path | str, *, retain_days: int = 30, clock=time.time):
        self.directory = Path(os.path.abspath(Path(directory).expanduser()))
        self.retain_days = retain_days
        self.clock = clock
        self._connection: sqlite3.Connection | None = None

    # ------------------------------------------------------------ files

    def _directory(self) -> Path:
        info = _not_linked(self.directory)
        if info is None:
            try:
                self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
            except OSError as exc:
                raise StoreError(f"cannot create the state directory: {exc}") from exc
            info = _not_linked(self.directory)
        if info is None or not stat.S_ISDIR(info.st_mode):
            raise StoreError("the state path is not a directory")
        return self.directory

    def protection(self) -> str:
        try:
            directory = self._directory()
        except StoreError:
            return "unknown"
        if os.name == "nt":
            home = os.path.normcase(str(Path.home()))
            inside = os.path.normcase(str(directory)).startswith(home.rstrip("\\/") + os.sep)
            return "windows_profile_acl" if inside else "unknown"
        mode = directory.stat().st_mode
        return "posix_owner_only" if mode & 0o077 == 0 else "unknown"

    def secret(self) -> bytes:
        """The machine-local HMAC secret. Created atomically; a damaged secret is replaced, which only
        invalidates outstanding tokens (they then fail as token_mismatch)."""
        directory = self._directory()
        path = directory / "confirm.secret"
        for _attempt in range(3):
            info = _not_linked(path)
            if info is not None:
                if not stat.S_ISREG(info.st_mode):
                    raise StoreError("the confirm secret is not a regular file")
                try:
                    text = path.read_text(encoding="ascii")
                    if len(text) == 64:
                        return bytes.fromhex(text)
                except (OSError, ValueError, UnicodeError):
                    pass
            self._write_secret(path, replace=info is not None)
        raise StoreError("the confirm secret cannot be created")

    def _write_secret(self, path: Path, *, replace: bool) -> None:
        temporary = path.with_name(f".confirm.secret.{uuid.uuid4().hex}.tmp")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(secrets.token_hex(32).encode("ascii"))
                stream.flush()
                os.fsync(stream.fileno())
            if replace:
                os.replace(temporary, path)
            else:
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    pass
        except OSError as exc:
            raise StoreError(f"cannot create the confirm secret: {exc}") from exc
        finally:
            try:
                temporary.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------ database

    def connection(self) -> sqlite3.Connection:
        if self._connection is not None:
            return self._connection
        path = self._directory() / "operations.sqlite3"
        info = _not_linked(path)
        if info is not None and not stat.S_ISREG(info.st_mode):
            raise StoreError("the operation store is not a regular file")
        try:
            connection = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.executescript(TABLES)
            present = {row["name"] for row in connection.execute("PRAGMA table_info(operations)")}
            for name, declaration in COLUMNS.items():
                if name not in present:
                    connection.execute(f"ALTER TABLE operations ADD COLUMN {name} {declaration}")
            connection.executescript(INDEXES)
            if not os.name == "nt":
                os.chmod(path, 0o600)
        except sqlite3.Error as exc:
            raise StoreError(f"the operation store cannot be opened: {exc}") from exc
        self._connection = connection
        self.prune()
        return connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    @contextmanager
    def _transaction(self):
        connection = self.connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except sqlite3.Error as exc:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StoreError(f"the operation store failed: {exc}") from exc
        except BaseException:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    def _query(self, sql: str, params=()) -> list[dict]:
        try:
            rows = self.connection().execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise StoreError(f"the operation store failed: {exc}") from exc
        return [_decode(row) for row in rows]

    def available(self) -> tuple[bool, str | None]:
        try:
            self.connection().execute("SELECT 1").fetchone()
            self.secret()
            return True, None
        except (StoreError, sqlite3.Error) as exc:
            return False, str(exc)

    # ------------------------------------------------------------ plans

    def add_plan(self, record: dict, items: list[dict] = ()) -> None:
        """Record a plan (and, for a batch, one planned record per item) before any token is issued."""
        now = self.clock()
        with self._transaction() as connection:
            for value in [record, *items]:
                connection.execute(
                    "INSERT INTO operations (operation_id, plan_id, capability_id, command, kind, batch_id, item_index, item_key,"
                    " target_key, request_digest, scope_id, status, effects, plan, created_at, updated_at, plan_expires_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?, ?, ?, ?)",
                    (value["operation_id"], value["plan_id"], value["capability_id"], value["command"], value.get("kind", "single"),
                     value.get("batch_id"), value.get("item_index"), value.get("item_key"), value.get("target_key"),
                     value.get("request_digest"), value.get("scope_id"), json.dumps(value["effects"]),
                     json.dumps(value["plan"], ensure_ascii=False), now, now, value["plan_expires_at"]))

    def get(self, operation_id: str) -> dict | None:
        rows = self._query("SELECT * FROM operations WHERE operation_id = ?", (operation_id,))
        return rows[0] if rows else None

    def items_of(self, batch_id: str) -> list[dict]:
        return self._query("SELECT * FROM operations WHERE batch_id = ? ORDER BY item_index", (batch_id,))

    def list(self, status: str | None = None, limit: int = 20, offset: int = 0) -> tuple[list[dict], int]:
        now = self.clock()
        live = ",".join(f"'{name}'" for name in LIVE)
        if status == "unknown":
            where = (f"WHERE status = 'unknown' OR (status = 'dispatched' AND (lease_until IS NULL OR lease_until <= ?))"
                     f" OR (status IN ({live}) AND lease_until IS NOT NULL AND lease_until <= ?)")
            params = (now, now)
        elif status in {"running", "pending", "cancel_requested"}:
            where = "WHERE status = ? AND (lease_until IS NULL OR lease_until > ?)"
            params = (status, now)
            if status == "running":
                where += " OR (status = 'dispatched' AND lease_until > ?)"
                params = (status, now, now)
        elif status:
            where, params = "WHERE status = ?", (status,)
        else:
            where, params = "", ()
        total = self._query(f"SELECT COUNT(*) AS n FROM operations {where}", params)[0]["n"]
        rows = self._query(f"SELECT * FROM operations {where} ORDER BY created_at DESC, operation_id LIMIT ? OFFSET ?",
                           (*params, limit, offset))
        return rows, total

    def _blocking(self, connection, keys, exclude) -> dict | None:
        keys = [key for key in keys if key]
        if not keys:
            return None
        marks = ",".join("?" for _ in ACTIVE)
        holders = ",".join("?" for _ in keys)
        excluded = ",".join("?" for _ in exclude) or "''"
        row = connection.execute(
            f"SELECT * FROM operations WHERE target_key IN ({holders}) AND status IN ({marks}) AND resolution IS NULL"
            f" AND operation_id NOT IN ({excluded}) ORDER BY created_at LIMIT 1", (*keys, *ACTIVE, *exclude)).fetchone()
        return _decode(row) if row else None

    def blocking(self, keys, exclude=()) -> tuple[dict, bool] | None:
        """The first unresolved or running operation on any of these targets, and whether it is still live."""
        try:
            record = self._blocking(self.connection(), list(keys), list(exclude))
        except sqlite3.Error as exc:
            raise StoreError(f"the operation store failed: {exc}") from exc
        if record is None:
            return None
        return record, effective_status(record, self.clock()) in {"running", *LIVE}

    def started_under(self, scope_id: str | None) -> int:
        """Confirmed operations counted against the scope's limits.max_operations (kept past record retention)."""
        if scope_id is None:
            return 0
        rows = self._query("SELECT used FROM scope_usage WHERE scope_id = ?", (scope_id,))
        return rows[0]["used"] if rows else 0

    @staticmethod
    def _count(connection, scope_id, units: int) -> None:
        if scope_id is None or not units:
            return
        connection.execute("INSERT INTO scope_usage (scope_id, used) VALUES (?, MAX(?, 0))"
                           " ON CONFLICT(scope_id) DO UPDATE SET used = MAX(used + ?, 0)", (scope_id, units, units))

    # ------------------------------------------------------------ dispatch

    def dispatch(self, operation_id: str, *, fingerprint: str, token_expires: float, key: str | None, digest: str,
                 target_keys, exclude=(), budget: int | None = None, units: int = 1, lease: float | None = None) -> None:
        """In one transaction: the plan still waits, no other operation holds its targets, the idempotency key is
        free, the scope's operation budget allows it; then consume the token, count the units against the scope
        and mark the operation dispatched.

        A key already used with the same arguments consumes the token without dispatching and raises KeyUsed.
        """
        now = self.clock()
        replay = None
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM operations WHERE operation_id = ?", (operation_id,)).fetchone()
            if row is None or row["status"] != "planned":
                raise TokenUsed(operation_id)
            blocking = self._blocking(connection, list(target_keys), [operation_id, *exclude])
            if blocking is not None:
                raise Blocked(blocking, effective_status(blocking, now) in {"running", *LIVE})
            if key is not None:
                for candidate in (_decode(item) for item in connection.execute(
                        "SELECT * FROM operations WHERE idempotency_key = ? AND operation_id != ? AND status != 'planned'"
                        " ORDER BY created_at DESC", (key, operation_id)).fetchall()):
                    if provably_not_started(candidate):
                        continue
                    if candidate["request_digest"] != digest:
                        raise KeyMismatch(candidate)
                    replay = candidate
                    break
            if replay is not None:
                # Commit the consumed token and the superseded plan, then report the earlier operation.
                self._consume(connection, operation_id, fingerprint, token_expires, now)
                connection.execute("UPDATE operations SET status = 'failed', updated_at = ?, finished_at = ?, extra = ?"
                                   " WHERE operation_id = ?",
                                   (now, now, json.dumps({"stage": "superseded_by_replay", "dispatch_state": "not_started",
                                                          "replay_of": replay["operation_id"]}), operation_id))
            else:
                used_row = connection.execute("SELECT used FROM scope_usage WHERE scope_id = ?", (row["scope_id"],)).fetchone()
                used = used_row["used"] if used_row else 0
                if budget is not None and used + units > budget:
                    raise BudgetSpent(used, budget)
                self._consume(connection, operation_id, fingerprint, token_expires, now)
                self._count(connection, row["scope_id"], units)
                connection.execute("UPDATE operations SET status = 'dispatched', started_at = ?, updated_at = ?, idempotency_key = ?,"
                                   " lease_until = ? WHERE operation_id = ?",
                                   (now, now, key, None if lease is None else now + lease, operation_id))
        if replay is not None:
            raise KeyUsed(replay)

    @staticmethod
    def _consume(connection, operation_id, fingerprint, token_expires, now) -> None:
        try:
            connection.execute("INSERT INTO consumed_tokens VALUES (?, ?, ?, ?)", (fingerprint, operation_id, now, token_expires))
        except sqlite3.IntegrityError as exc:
            raise TokenUsed(operation_id) from exc

    def start_item(self, operation_id: str, target: str | None, lease: float) -> None:
        """Dispatch one batch item under the batch's already consumed token (its unit was counted with the batch)."""
        now = self.clock()
        with self._transaction() as connection:
            row = connection.execute("SELECT status FROM operations WHERE operation_id = ?", (operation_id,)).fetchone()
            if row is None or row["status"] != "planned":
                raise TokenUsed(operation_id)
            blocking = self._blocking(connection, [target], [operation_id])
            if blocking is not None:
                raise Blocked(blocking, effective_status(blocking, now) in {"running", *LIVE})
            connection.execute("UPDATE operations SET status = 'dispatched', started_at = ?, updated_at = ?, lease_until = ?"
                               " WHERE operation_id = ?", (now, now, now + lease, operation_id))

    def release(self, operation_id: str, note: dict) -> None:
        """A batch item that never reached the native side (skipped, refused, drifted): record it as failed and
        give its unit back to the scope."""
        now = self.clock()
        with self._transaction() as connection:
            row = connection.execute("SELECT scope_id, status FROM operations WHERE operation_id = ?", (operation_id,)).fetchone()
            if row is None or row["status"] not in {"planned", "dispatched"}:
                return
            connection.execute("UPDATE operations SET status = 'failed', started_at = NULL, lease_until = NULL, updated_at = ?,"
                               " finished_at = ?, extra = ? WHERE operation_id = ?",
                               (now, now, json.dumps(dict(note, dispatch_state="not_started"), ensure_ascii=False), operation_id))
            self._count(connection, row["scope_id"], -1)

    def undispatch(self, operation_id: str, fingerprint: str | None, note: dict) -> None:
        """The adapter reported that nothing was sent: return the plan to planned, release the token and the unit."""
        now = self.clock()
        with self._transaction() as connection:
            row = connection.execute("SELECT scope_id, status FROM operations WHERE operation_id = ?", (operation_id,)).fetchone()
            if row is None or row["status"] != "dispatched":
                return
            connection.execute("UPDATE operations SET status = 'planned', started_at = NULL, lease_until = NULL, idempotency_key = NULL,"
                               " updated_at = ?, extra = ? WHERE operation_id = ?",
                               (now, json.dumps(note, ensure_ascii=False), operation_id))
            if fingerprint:
                connection.execute("DELETE FROM consumed_tokens WHERE fingerprint = ?", (fingerprint,))
            self._count(connection, row["scope_id"], -1)

    def heartbeat(self, operation_id: str, lease: float) -> None:
        now = self.clock()
        with self._transaction() as connection:
            connection.execute("UPDATE operations SET lease_until = ? WHERE operation_id = ? AND status = 'dispatched'",
                               (now + lease, operation_id))

    def finish(self, operation_id: str, status: str, receipt: dict | None = None, extra: dict | None = None) -> bool:
        """Record the outcome. A result that arrives after the record was resolved (or finished elsewhere) is kept
        in extra.late_result and does not overwrite the recorded status; returns False in that case."""
        now = self.clock()
        final = status in FINAL
        with self._transaction() as connection:
            row = connection.execute("SELECT status, resolution, extra FROM operations WHERE operation_id = ?", (operation_id,)).fetchone()
            if row is None:
                return False
            settled = row["status"] in {"succeeded", "partial", "failed", "cancelled"} and status != row["status"]
            if row["resolution"] is not None or settled:
                previous = json.loads(row["extra"]) if row["extra"] else {}
                previous["late_result"] = {"status": status, "receipt": receipt, "observed_at": now}
                connection.execute("UPDATE operations SET extra = ?, updated_at = ? WHERE operation_id = ?",
                                   (json.dumps(previous, ensure_ascii=False), now, operation_id))
                return False
            connection.execute(
                "UPDATE operations SET status = ?, receipt = ?, extra = COALESCE(?, extra), updated_at = ?, lease_until = NULL,"
                " finished_at = CASE WHEN ? THEN ? ELSE finished_at END WHERE operation_id = ?",
                (status, json.dumps(receipt, ensure_ascii=False) if receipt is not None else None,
                 json.dumps(extra, ensure_ascii=False) if extra is not None else None, now, final, now, operation_id))
            return True

    def update_status(self, operation_id: str, status: str, extra: dict, lease: float) -> bool:
        """Progress of a submitted operation (pending, running, cancel_requested), observed now: renews its lease.
        Never moves a finished or resolved record back to an active state."""
        now = self.clock()
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE operations SET status = ?, extra = ?, updated_at = ?, lease_until = ? WHERE operation_id = ?"
                " AND resolution IS NULL AND status IN ('dispatched', 'pending', 'running', 'cancel_requested', 'unknown')",
                (status, json.dumps(extra, ensure_ascii=False), now, now + lease, operation_id))
            return cursor.rowcount == 1

    def demote(self, operation_id: str, reason: str) -> None:
        """The reported result failed the contract after dispatch: keep it aside and record unknown."""
        now = self.clock()
        with self._transaction() as connection:
            row = connection.execute("SELECT receipt, extra FROM operations WHERE operation_id = ?", (operation_id,)).fetchone()
            if row is None:
                return
            extra = json.loads(row["extra"]) if row["extra"] else {}
            extra.update(stage=reason, rejected_receipt=json.loads(row["receipt"]) if row["receipt"] else None)
            connection.execute("UPDATE operations SET status = 'unknown', receipt = NULL, extra = ?, updated_at = ?, lease_until = NULL,"
                               " finished_at = ? WHERE operation_id = ?", (json.dumps(extra, ensure_ascii=False), now, now, operation_id))

    def settle_batches(self, derive) -> None:
        """A batch whose CLI stopped keeps 'dispatched' with an expired lease: record the status its items add up to."""
        now = self.clock()
        stuck = self._query("SELECT operation_id FROM operations WHERE kind = 'batch' AND status = 'dispatched'"
                            " AND (lease_until IS NULL OR lease_until <= ?)", (now,))
        for row in stuck:
            statuses = [effective_status(item, now) for item in self.items_of(row["operation_id"])]
            with self._transaction() as connection:
                connection.execute("UPDATE operations SET status = ?, lease_until = NULL, updated_at = ?, finished_at = ?"
                                   " WHERE operation_id = ? AND status = 'dispatched'", (derive(statuses), now, now, row["operation_id"]))

    def resolve(self, operation_id: str, resolution: dict) -> dict:
        now = self.clock()
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM operations WHERE operation_id = ?", (operation_id,)).fetchone()
            if row is None:
                raise TokenUsed(operation_id)
            record = _decode(row)
            if effective_status(record, now) in {"running", *LIVE}:
                raise LeaseHeld(record)
            if record["resolution"] is not None:
                raise TokenUsed(operation_id)
            status = "unknown" if record["status"] in {"dispatched", *LIVE} else record["status"]
            connection.execute("UPDATE operations SET resolution = ?, status = ?, lease_until = NULL, updated_at = ? WHERE operation_id = ?",
                               (json.dumps(resolution, ensure_ascii=False), status, now, operation_id))
        return self.get(operation_id)

    def prune(self) -> None:
        """Drop finished records past retention, plans a day past expiry and expired consumed tokens.
        A batch and its items go together; an item is never dropped on its own."""
        now = self.clock()
        horizon = now - self.retain_days * 86400
        marks = ",".join("?" for _ in ACTIVE)
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(f"DELETE FROM operations WHERE kind = 'single' AND (status NOT IN ({marks}) OR resolution IS NOT NULL)"
                               " AND status != 'planned' AND updated_at < ?", (*ACTIVE, horizon))
            connection.execute("DELETE FROM operations WHERE kind IN ('single', 'batch') AND status = 'planned' AND plan_expires_at < ?",
                               (now - 86400,))
            connection.execute(f"DELETE FROM operations WHERE kind = 'batch' AND status NOT IN ('planned', 'dispatched')"
                               " AND updated_at < ? AND NOT EXISTS (SELECT 1 FROM operations AS item WHERE item.batch_id ="
                               f" operations.operation_id AND item.status IN ({marks}) AND item.resolution IS NULL)",
                               (horizon, *ACTIVE))
            connection.execute("DELETE FROM operations WHERE kind = 'item' AND batch_id NOT IN"
                               " (SELECT operation_id FROM operations WHERE kind = 'batch')")
            connection.execute("DELETE FROM consumed_tokens WHERE expires_at < ?", (now - 86400,))
            connection.execute("COMMIT")
        except sqlite3.Error:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
