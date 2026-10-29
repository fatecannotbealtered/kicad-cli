"""Single-use, expiring confirmation records, shared by the shell and payloads.

Importable as a package or a sibling script; standard library only.

The token used to be ``sha256(operation + preview)``. It bound the plan to the
previewed state, which is the part that was right, but it was a pure function of
public inputs and nothing else: the same token came back on every dry run, stayed
valid indefinitely, and could be replayed without limit. A gate like that costs
one extra round trip. It does not cost a decision, which is the only thing a
write gate is for.

So the token is now a random name for a record this process wrote, and confirming
consumes it. What that buys, precisely:

- it cannot be derived, so holding one is evidence a dry run actually happened
  and its preview was returned to whoever is confirming;
- it expires, so an approval cannot be banked and spent against a board that has
  moved on since;
- it is redeemed once, so a retry loop cannot apply the same write twice.

What it is still not: an authentication boundary. Anyone who can run this tool
can run the dry run themselves, and anyone who can do that can edit the board
directly. Under T1 that is the correct scope -- this gate exists to stop an
unconsidered write, not an adversary with local access.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

TOKEN_RE = re.compile(r"\Act_[0-9a-f]{32}\Z")
TTL_SECONDS = 900
STATE_ENV = "KICAD_CLI_STATE"
RECORD_VERSION = 1


class ConfirmError(Exception):
    """A token that cannot be redeemed. Never a finding about the board."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def state_dir() -> Path:
    override = os.environ.get(STATE_ENV)
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    return Path(base) / "kicad-cli" / "confirm"


def _prepare() -> Path:
    directory = state_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(directory, 0o700)
    except OSError as exc:
        raise ConfirmError(
            "E_CONFIG",
            "cannot create the confirmation state directory",
            {
                "path": str(directory),
                "hint": f"set {STATE_ENV} to a writable directory",
                # Falling back to a derived token here would silently restore
                # the replayable gate this module exists to remove.
                "note": "the write gate needs to record issued tokens; it does not "
                "degrade to a derived token",
            },
        ) from exc
    return directory


def _sweep(directory: Path, now: float) -> None:
    """Expired records are not evidence of anything; drop them opportunistically."""
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_file() and now - entry.stat().st_mtime > TTL_SECONDS:
                entry.unlink()
        except OSError:  # noqa: PERF203 - a racing redeem may remove it first
            continue


def digest(operation: str, preview: dict, target: str | None) -> str:
    """Bind the plan, the file it was computed against, and that file's bytes.

    The preview alone is a summary. Two boards can summarise identically, and
    one board can be edited into a state the summary no longer describes, so the
    content hash is what makes "the same target, unchanged" checkable.
    """
    blob = {
        "operation": operation,
        "preview": preview,
        "target": None,
        "target_sha256": None,
    }
    if target:
        path = Path(target)
        blob["target"] = str(path.resolve())
        try:
            sha = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    sha.update(chunk)
            blob["target_sha256"] = sha.hexdigest()
        except OSError as exc:
            raise ConfirmError(
                "E_NOT_FOUND", "confirmation target is unreadable", {"target": str(path)}
            ) from exc
    return hashlib.sha256(json.dumps(blob, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def issue(operation: str, preview: dict, target: str | None = None) -> str:
    """Record a pending write and return the token that redeems it once."""
    directory = _prepare()
    now = time.time()
    _sweep(directory, now)
    token = "ct_" + secrets.token_hex(16)
    record = {
        "version": RECORD_VERSION,
        "operation": operation,
        "digest": digest(operation, preview, target),
        "issued_at": now,
    }
    path = directory / token
    temporary = directory / f"{token}.partial"
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(record, handle)
        os.replace(temporary, path)  # A half-written record must never be redeemable.
        if os.name != "nt":
            os.chmod(path, 0o600)
    except OSError as exc:
        raise ConfirmError(
            "E_IO", "could not record the confirmation token", {"path": str(directory)}
        ) from exc
    return token


def redeem(token: str, operation: str, preview: dict, target: str | None = None) -> None:
    """Consume the token or raise. Returns only when the write may proceed."""
    if not isinstance(token, str) or not TOKEN_RE.match(token):
        # The token names a file, so the shape is checked before it is used as
        # one. A derived token from an older build lands here too, which is the
        # correct answer: it was never recorded, so it cannot be redeemed.
        raise ConfirmError(
            "E_CONFLICT",
            "confirm token is not a token this tool issued; re-run with --dry-run",
            {"got": token if isinstance(token, str) else None},
        )
    directory = state_dir()
    claim = directory / f"{token}.claim.{os.getpid()}.{secrets.token_hex(4)}"
    try:
        # Claim by rename: the source can only disappear once, so concurrent
        # confirms of the same token cannot both proceed.
        os.rename(directory / token, claim)
    except OSError as exc:
        raise ConfirmError(
            "E_CONFLICT",
            "confirm token is unknown, already used or expired; "
            "re-run with --dry-run to get a fresh one",
            {"token": token},
        ) from exc
    try:
        record = json.loads(claim.read_text(encoding="utf-8"))
        age = time.time() - float(record["issued_at"])
        stored = str(record["digest"])
        version = record["version"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        claim.unlink(missing_ok=True)
        raise ConfirmError("E_CONFLICT", "confirmation record is unreadable", {}) from exc
    claim.unlink(missing_ok=True)
    if version != RECORD_VERSION:
        raise ConfirmError("E_CONFLICT", "confirmation record has an unsupported version", {})
    if age > TTL_SECONDS:
        raise ConfirmError(
            "E_CONFLICT",
            "confirmation expired; re-run with --dry-run to get a fresh one",
            {"age_s": int(age), "ttl_s": TTL_SECONDS},
        )
    if not secrets.compare_digest(stored, digest(operation, preview, target)):
        raise ConfirmError(
            "E_CONFLICT",
            "the target or its state changed since this token was issued; "
            "re-run with --dry-run to get a fresh token",
            {"operation": operation},
        )
