"""The authorization scope (DSEC-010 to DSEC-018).

The runtime only reads the scope file: from ``<PREFIX>_SCOPE_FILE`` or
``<state_dir>/scope.json``, nowhere else. A file that breaks DSEC-012 is ``invalid``; one past
``expires_at`` is ``expired``. A write is covered when its effects are granted, the dangerous
permission is there when needed, and every target key matches a prefix (DSEC-014).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .contract import Contract
from .records import expired
from .targets import covers

SNIPPET_HOURS = 8  # the expiry a whole-file snippet suggests (DSEC-015)

UTC = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z$")


@dataclass
class Scope:
    path: Path
    status: str  # granted, missing, invalid, expired
    data: dict[str, Any] = field(default_factory=dict)
    hash: str | None = None
    problem: str | None = None

    @property
    def scope_id(self) -> str:
        return self.data.get("scope_id", "") if self.status != "invalid" else ""

    @property
    def confirm_always(self) -> bool:
        return self.status == "granted" and self.data.get("confirm") == "always"

    def missing(self, effects: list[str], dangerous: bool, keys: list[str], halt: bool = False) -> dict[str, Any] | None:
        """What the scope lacks for a write, or ``None`` when it covers it (DSEC-014, DSEC-015)."""
        granted = self.status == "granted"
        allowed = self.data.get("effects", []) if granted else []
        prefixes = self.data.get("targets", []) if granted else []
        need_effects = [] if halt else [e for e in effects if e not in allowed]
        need_dangerous = bool(dangerous and not halt and not (granted and self.data.get("dangerous") is True))
        need_targets = [k for k in keys if not any(covers(p, k) for p in prefixes)]
        if not granted:
            need_effects = [] if halt else list(effects)
            need_dangerous = bool(dangerous and not halt)
            need_targets = list(keys)
        if not need_effects and not need_dangerous and not need_targets:
            return None
        missing = {"effects": need_effects, "targets": need_targets, "dangerous": need_dangerous}
        snippet: dict[str, Any] = {}
        wanted = list(dict.fromkeys(e for e in effects if e != "observe"))
        if not halt and wanted and keys:
            # A scope file that grants just this write, ready to write as it is (DSEC-015).
            until = datetime.now(timezone.utc) + timedelta(hours=SNIPPET_HOURS)
            snippet = {"scope_id": "grant", "effects": wanted, "dangerous": bool(dangerous),
                       "targets": list(dict.fromkeys(keys)), "expires_at": until.strftime("%Y-%m-%dT%H:%M:%SZ")}
            if self.confirm_always:
                snippet["confirm"] = "always"
            return {"missing": missing, "snippet": snippet}
        if need_effects:
            snippet["effects"] = need_effects
        if need_targets:
            snippet["targets"] = need_targets
        if need_dangerous:
            snippet["dangerous"] = True
        if not snippet:  # a halt command under a missing scope still needs its targets
            snippet["targets"] = list(keys)
        return {"missing": missing, "snippet": snippet}

    def summary(self) -> dict[str, Any]:
        """The ``authorization`` key of ``context`` (DSEC-016)."""
        out: dict[str, Any] = {"status": self.status, "path": str(self.path)}
        if self.status in ("granted", "expired"):
            out.update(scope_id=self.data["scope_id"], effects=list(self.data["effects"]),
                       dangerous=self.data["dangerous"], expires_at=self.data["expires_at"])
        return out


def load(path: Path, contract: Contract) -> Scope:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return Scope(path, "missing")
    except OSError as exc:
        return Scope(path, "invalid", problem=f"cannot read the file: {exc.strerror}")
    digest = hashlib.sha256(raw).hexdigest()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return Scope(path, "invalid", hash=digest, problem=f"not JSON: {exc}")
    problem = _check(data, contract)
    if problem:
        return Scope(path, "invalid", hash=digest, problem=problem)
    if expired(data["expires_at"]):
        return Scope(path, "expired", data=data, hash=digest)
    return Scope(path, "granted", data=data, hash=digest)


def _check(data: Any, contract: Contract) -> str | None:
    """The rules of DSEC-012; ``contract/schemas/scope.schema.json`` holds them as a schema."""
    fields = contract["scope_file"]["fields"]
    if not isinstance(data, dict):
        return "the file must hold one JSON object"
    unknown = sorted(set(data) - set(fields))
    if unknown:
        return f"unknown fields {unknown}"
    for name in ("scope_id", "effects", "dangerous", "targets", "expires_at"):
        if name not in data:
            return f"missing field {name}"
    if not isinstance(data["scope_id"], str) or not data["scope_id"]:
        return "scope_id must be a non-empty string"
    writes = [e for e in contract.effects if e != "observe"]
    effects = data["effects"]
    if not isinstance(effects, list) or not effects or len(set(map(str, effects))) != len(effects):
        return "effects must be a non-empty list without repeats"
    bad = [e for e in effects if e not in writes]
    if bad:
        return f"effects may hold only {writes}; found {bad}"
    if not isinstance(data["dangerous"], bool):
        return "dangerous must be true or false"
    targets = data["targets"]
    if not isinstance(targets, list) or not targets or len(set(map(str, targets))) != len(targets):
        return "targets must be a non-empty list without repeats"
    for target in targets:
        if not isinstance(target, str) or not target.startswith(("file:///", "untitled:", "device:")):
            return f"target {target!r} is not a target key prefix"
    if not isinstance(data["expires_at"], str) or not UTC.match(data["expires_at"]):
        return "expires_at must be a UTC time ending in Z"
    if "confirm" in data and data["confirm"] not in contract["scope_file"]["confirm_values"]:
        return "confirm may only be always"
    return None
