"""Task scope (DRV-009): a record a person writes and the CLI only reads.

Absent, expired, revoked or invalid scopes leave the CLI read-only. The scope narrows what an agent
may do; it is not proof of human approval when the agent can write the same file (DRV-042).
"""

from __future__ import annotations

import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import unquote, urlparse, urlsplit
from urllib.request import url2pathname

from .models import DANGEROUS, DriverError
from .validation import parse_json

MAX_SCOPE_BYTES = 64 * 1024
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
ROOTED = frozenset({"change", "persist"})
URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def _normalized(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.realpath(path)))


def _literal(path: str) -> str:
    """Normalized without touching the file system or the network."""
    return os.path.normcase(os.path.abspath(path)).rstrip("\\/")


def _network(path: str) -> bool:
    return path.startswith(("\\\\", "//"))


def is_absolute(path: str) -> bool:
    return PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute()


def inside(path: str, roots: list[str]) -> bool:
    """path lies in one of the absolute roots, after links are resolved. A network path is never resolved
    unless a root names that share literally: resolving it would contact the host."""
    if not isinstance(path, str) or not is_absolute(path):
        return False
    roots = [root for root in roots if isinstance(root, str) and is_absolute(root)]
    if _network(path) or _network(_literal(path)):
        literal = _literal(path)
        if not any(_network(root) and (literal == _literal(root) or literal.startswith(_literal(root) + os.sep)) for root in roots):
            return False
    candidate = _normalized(path)
    for root in roots:
        base = _normalized(root).rstrip("\\/")
        if candidate == base or candidate.startswith(base + os.sep):
            return True
    return False


def file_path_of(locator: str) -> str | None:
    """The local path of a file:// locator, decoded once; None for other schemes.

    Raises ValueError for a file URI that names another host or a network share, or that does not
    convert to a local path: such a path cannot be checked against local roots, so callers treat it
    as outside every root.
    """
    parsed = urlparse(locator)
    if parsed.scheme != "file":
        return None
    if parsed.netloc not in ("", "localhost"):
        raise ValueError("file URI names another host")
    try:
        path = url2pathname(parsed.path) if os.name == "nt" else unquote(parsed.path)
    except OSError as exc:
        raise ValueError("file URI does not convert to a local path") from exc
    if _network(path):
        raise ValueError("file URI names a network share")
    return path


def well_formed_prefix(prefix: str) -> bool:
    """A destination or locator prefix: an absolute local path, or a URL without user info, query,
    fragment, back slashes or dot segments."""
    if not isinstance(prefix, str) or not prefix:
        return False
    if not URL.match(prefix):
        return is_absolute(prefix)
    try:
        parts = urlsplit(prefix)
        safe_segments = all(_safe_url_segment(segment) for segment in parts.path.split("/"))
    except ValueError:
        return False
    return ("@" not in parts.netloc and not parts.query and not parts.fragment and "\\" not in prefix
            and safe_segments)


def _safe_url_segment(segment: str) -> bool:
    """Decode once; escaped separators must not introduce a different path hierarchy."""
    if re.search(r"%(?![0-9a-fA-F]{2})", segment):
        return False
    decoded = unquote(segment, errors="strict")
    return decoded not in {".", ".."} and not any(character in decoded for character in "/\\") and not any(ord(character) < 32 for character in decoded)


def within(value: str, prefix: str) -> bool:
    """value is the prefix itself or lies under it. URLs compare scheme and host exactly (ignoring case) and
    the path by whole segments; file URIs and local paths compare as resolved paths. User info, back slashes,
    queries, fragments and dot segments in the value never match."""
    if not isinstance(value, str) or not value or not well_formed_prefix(prefix):
        return False
    if not URL.match(prefix):
        return not URL.match(value) and inside(value, [prefix])
    if not URL.match(value) or "\\" in value:
        return False
    if not well_formed_prefix(value):
        return False
    actual, granted = urlsplit(value), urlsplit(prefix)
    if (actual.scheme.lower(), actual.netloc.lower()) != (granted.scheme.lower(), granted.netloc.lower()):
        return False
    if "@" in actual.netloc or actual.query or actual.fragment or any(unquote(s) in {".", ".."} for s in actual.path.split("/")):
        return False
    if granted.scheme.lower() == "file":
        try:
            path, root = file_path_of(value), file_path_of(prefix)
        except ValueError:
            return False
        return bool(path and root) and inside(path, [root])
    base = granted.path.rstrip("/").split("/") if granted.path.strip("/") else [""]
    segments = actual.path.split("/") if actual.path else [""]
    return segments[:len(base)] == base


def _matches(selector: dict, target: dict) -> bool:
    for key in ("engine_id", "instance_id", "object_id"):
        if key in selector and selector[key] != target.get(key):
            return False
    prefix = selector.get("locator_prefix")
    return prefix is None or within(str(target.get("locator", "")), prefix)


class Scope:
    def __init__(self, summary: dict, record: dict | None, grant_file: str):
        self.summary = summary
        self.record = record
        self.grant_file = grant_file

    @property
    def granted(self) -> bool:
        return self.summary["status"] == "granted"

    @property
    def authorization(self) -> dict:
        """The context.authorization summary (schema fields only)."""
        return {key: value for key, value in self.summary.items() if key != "tool"}

    @property
    def scope_id(self) -> str | None:
        return self.summary["scope_id"] if self.granted else None

    @property
    def max_operations(self) -> int | None:
        if not self.granted or not self.record:
            return None
        return (self.record.get("limits") or {}).get("max_operations")

    def missing(self, *, effects, dangerous: bool = False, target: dict | None = None, outputs=(), paths=()) -> dict:
        """What this call needs and the scope does not grant; empty when the scope covers the call."""
        effects = set(effects)
        allowed = set(self.summary["effects"]) | {"observe"} if self.granted else {"observe"}
        missing: dict = {}
        needed = sorted(effects - allowed)
        if needed:
            missing["effects"] = needed
        if (dangerous or effects & DANGEROUS) and not (self.granted and self.summary["dangerous"]):
            missing["dangerous"] = True
        if target is not None and self.granted and self.record and self.record.get("targets"):
            if not any(_matches(selector, target) for selector in self.record["targets"]):
                missing["targets"] = [target["locator"]]
        outside = []
        if target is not None and target.get("mode") == "file" and effects & ROOTED:
            try:
                file_target = file_path_of(str(target.get("locator", "")))
            except ValueError:
                file_target = None
            if file_target is None or not inside(file_target, self.summary["roots"]):
                outside.append(file_target or str(target.get("locator", "")))
        destinations = []
        for output in outputs:
            if output["effect"] == "deliver":
                if not any(within(output["path"], prefix) for prefix in self.summary["destinations"]):
                    destinations.append(output["path"])
                continue
            roots = {"artifact": self.summary["outputs"], "persist": self.summary["roots"]}.get(output["effect"])
            if roots is not None and not inside(output["path"], roots):
                outside.append(output["path"])
        for path, effect in paths:
            roots = self.summary["outputs"] if effect == "artifact" else self.summary["roots"]
            if not inside(path, roots):
                outside.append(path)
        if outside:
            missing["paths"] = outside
        if destinations:
            missing["destinations"] = destinations
        return missing

    def require(self, *, effects, dangerous: bool = False, target: dict | None = None, outputs=(), paths=()) -> None:
        """Raise E_FORBIDDEN before anything is dispatched when the scope does not cover the call."""
        missing = self.missing(effects=effects, dangerous=dangerous, target=target, outputs=outputs, paths=paths)
        if missing:
            raise forbidden(self, missing)


def forbidden(scope: Scope, missing: dict) -> DriverError:
    example = {
        "scope_id": "<scope-id>", "tool": scope.summary.get("tool", "<tool>"), "granted_by": "<person>", "granted_at": "<ISO 8601 UTC>",
        "expires_at": None, "effects": sorted({"observe", *missing.get("effects", [])}),
        "dangerous": bool(missing.get("dangerous")), "roots": ["<absolute directory>"] if missing.get("paths") else [],
        "outputs": [], "destinations": ["<destination prefix>"] if missing.get("destinations") else [],
    }
    return DriverError(
        "E_FORBIDDEN", "The task scope does not allow this call.",
        details={"scope": {"authorization": scope.authorization, "missing": missing, "grant_file": scope.grant_file, "grant_example": example},
                 "next_action": "Stop and ask the user to grant the missing scope in the grant file; do not write it yourself unless the user tells you to."},
        dispatch_state="not_started")


class ScopeResolver:
    def __init__(self, tool: str, *, validator=None, environ=None, home=None, clock=None):
        self.tool = tool
        self.env_var = tool.upper().replace("-", "_") + "_SCOPE_FILE"
        self.environ = os.environ if environ is None else environ
        self.home = Path(home) if home else Path.home()
        self.validator = validator
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def default_path(self) -> Path:
        return self.home / f".{self.tool}" / "scope.json"

    def _summary(self, status, source, *, record=None, reason=None) -> dict:
        granted = status == "granted"
        named = isinstance(record, dict) and status != "invalid"
        return {
            "status": status,
            "scope_id": record.get("scope_id") if named and isinstance(record.get("scope_id"), str) else None,
            "source": source,
            "effects": sorted(set(record["effects"]) | {"observe"}) if granted else ["observe"],
            "roots": list(record["roots"]) if granted else [],
            "outputs": list(record["outputs"]) if granted else [],
            "destinations": list(record.get("destinations", [])) if granted else [],
            "dangerous": bool(record["dangerous"]) if granted else False,
            "expires_at": record.get("expires_at") if named else None,
            "reason": reason,
            "tool": self.tool,
        }

    def resolve(self) -> Scope:
        configured = self.environ.get(self.env_var)
        path = Path(configured) if configured else self.default_path
        kind = "env_file" if configured else "file"
        grant_file = str(path)
        source = {"kind": kind, "path": grant_file}

        def invalid(reason, record=None):
            return Scope(self._summary("invalid", source, record=record, reason=reason), None, grant_file)

        try:
            info = path.lstat()
        except FileNotFoundError:
            if configured:
                return invalid(f"{self.env_var} names a file that does not exist")
            return Scope(self._summary("read_only", {"kind": "none", "path": None}), None, grant_file)
        except OSError as exc:
            return invalid(f"cannot read the scope file: {exc}")
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & REPARSE_POINT or not stat.S_ISREG(info.st_mode):
            return invalid("the scope file must be a regular file, not a link")
        if info.st_size > MAX_SCOPE_BYTES:
            return invalid("the scope file is larger than 64 KiB")
        try:
            record = parse_json(path.read_bytes())
        except (OSError, UnicodeError, ValueError, RecursionError):
            return invalid("the scope file is not one UTF-8 JSON object")
        if not isinstance(record, dict):
            return invalid("the scope file is not one UTF-8 JSON object")
        if self.validator is not None:
            try:
                self.validator.validate("scope", record)
            except Exception as exc:  # noqa: BLE001 - any validation failure leaves the CLI read-only
                problems = list(getattr(exc, "errors", ()))
                return invalid("the scope file does not satisfy the scope schema" + (f": {problems[0]}" if problems else ""))
        if record.get("tool") != self.tool:
            return invalid("the scope file names another tool", record)
        if any(not isinstance(item, str) or not is_absolute(item) for item in [*record.get("roots", []), *record.get("outputs", [])]):
            return invalid("roots and outputs must be absolute directories")
        prefixes = [*record.get("destinations", []), *(selector.get("locator_prefix") for selector in record.get("targets", [])
                                                         if isinstance(selector, dict) and "locator_prefix" in selector)]
        if any(not well_formed_prefix(prefix) for prefix in prefixes):
            return invalid("destinations and locator prefixes must be absolute paths or URLs without user info, queries or dot segments")
        if record.get("revoked"):
            return Scope(self._summary("revoked", source, record=record, reason="the scope was revoked"), record, grant_file)
        expires = record.get("expires_at")
        if expires is not None:
            try:
                moment = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
            except ValueError:
                return invalid("expires_at is not an ISO 8601 time")
            if moment.tzinfo is None:
                return invalid("expires_at has no time zone; write it in UTC with Z")
            if moment <= self.clock():
                return Scope(self._summary("expired", source, record=record, reason=f"the scope expired at {expires}"), record, grant_file)
        return Scope(self._summary("granted", source, record=record), record, grant_file)
