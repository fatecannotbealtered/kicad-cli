"""Declaration-driven driver CLI on the base ai-native contract.

The runtime owns the protocol: argument parsing, the --dry-run/--confirm gate, confirm tokens,
task scope checks, operation records and envelopes. The adapter owns native facts: targets,
snapshots, plans, receipts and read-back. Nothing here invents a native outcome.
"""

from __future__ import annotations

import json
import math
import os
import platform
import re
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import tokens
from .verification import PROFILE_PREFIX, guard_receipt
from .changelog import since as changelog_since
from .models import GATED, Call, DriverError
from .scope import ScopeResolver, forbidden
from .store import (Blocked, BudgetSpent, KeyMismatch, KeyUsed, LeaseHeld, StateStore, StoreError, TokenUsed, effective_status,
                    target_key)
from .validation import ContractError, ContractValidationError, ContractValidator, clone_json

VALUE_FLAGS = frozenset({"--format", "--fields", "--confirm", "--request-id", "--idempotency-key"})
BOOL_FLAGS = frozenset({"--json", "--compact", "--quiet", "--dry-run", "--dangerous", "--version", "--help"})
WRITE_ONLY_FLAGS = (("--dry-run", "dry_run"), ("--confirm", "confirm"), ("--dangerous", "dangerous"),
                    ("--request-id", "request_id"), ("--idempotency-key", "idempotency_key"))
BUILTINS = {("reference",), ("context",), ("doctor",), ("changelog",), ("operation", "show"), ("operation", "list"),
            ("operation", "resolve")}
UPDATE = ("update",)
PLAN_TTL = timedelta(minutes=10)
LEASE_SECONDS = 30.0
STATUS_LEASE_SECONDS = 600.0
MAX_JSON_BYTES = 8 * 1024 * 1024
INT_MIN, INT_MAX = -(2 ** 63), 2 ** 63 - 1
LIVE = frozenset({"pending", "running", "cancel_requested"})
SDK_DETAILS = frozenset({"dispatch_state", "operation_id", "lookup", "lookup_argv", "next_action", "receipt"})
STATUS_EXTRAS = ("stage", "progress", "blocked", "stop_observed", "stop_evidence_ref", "wait_timeout_ms", "execution_deadline")

NEXT_ACTION = {
    "E_OUTCOME_UNKNOWN": "Do not resend. Run the lookup, read the target back, then record what you saw with operation resolve before any further write.",
    "E_PARTIAL": "Read the receipt and continue from what actually changed with a new --dry-run; never resend the whole request.",
    "E_VERIFY_FAILED": "Inspect the target: the change took effect but a required check failed. Do not repeat the change.",
    "E_VERIFY_INCOMPLETE": "Read the target back yourself before relying on this change; do not repeat it.",
    "E_NATIVE_FAILURE": "Nothing lasting changed. Read the native reason in details, fix the cause and plan again.",
    "E_BUSY": "Nothing was sent. Wait briefly and run the same command again.",
    "E_BACKEND_UNAVAILABLE": "Run doctor and tell the user what the application, licence or component needs.",
    "E_HUMAN_REQUIRED": "Tell the user what to do (details.action), wait until they are done, then run details.resume.",
    "E_UNSUPPORTED": "Choose another command; see reference --index.",
    "E_PROTOCOL": "If details.dispatch_state is not not_started, query the operation before any further write.",
    "E_IO": "Check the state directory reported by context.state, then try again.",
    "E_UNKNOWN": "Report this to the tool maintainer with the details; nothing was dispatched.",
    "E_INTERRUPTED": "Nothing was dispatched; run the command again.",
    "E_TIMEOUT": "Nothing was dispatched; retry after a short wait.",
    "E_NOT_FOUND": "Check the identifier; list the candidates first.",
    "E_CONFIG": "Fix the configuration named in the message; run doctor.",
    "E_FORBIDDEN": "Stop and ask the user to grant what details.scope.missing names.",
}
CONFLICT_ACTION = {
    "state_drift": "The target changed since the plan was made. Observe it again and run --dry-run again.",
    "token_expired": "The confirm token expired. Run the same command with --dry-run again.",
    "token_used": "This confirm token was already used. Run --dry-run again; if the earlier call lost its reply, run operation show first.",
    "token_mismatch": "The arguments or the token do not match the dry-run. Run --dry-run with exactly these arguments.",
    "scope_changed": "The task scope changed since the plan was made. Run --dry-run again under the current scope.",
    "unresolved_operation": "An earlier operation on this target has an unknown outcome. Run the lookup, read the target back, record it with operation resolve, then plan again.",
    "lock_held": "Another operation on this target is still running. Wait for it with the lookup command, then plan again; do not resolve it while it runs.",
    "idempotency_mismatch": "This idempotency key was used for different arguments. Use a new key or the original arguments.",
}


@dataclass(frozen=True)
class Output:
    stdout: bytes
    exit_code: int
    envelope: dict | None = None
    stderr: str = ""

    def write(self, stdout=None, stderr=None) -> int:
        (stdout or sys.stdout.buffer).write(self.stdout)
        if self.stderr:
            (stderr or sys.stderr).write(self.stderr)
        return self.exit_code


class UsageProblem(Exception):
    def __init__(self, problem: str, message: str, *, next_action: str, parameter: str | None = None,
                 expected=None, code: str = "E_USAGE"):
        super().__init__(message)
        self.problem, self.message, self.next_action = problem, message, next_action
        self.parameter, self.expected, self.code = parameter, expected, code


def _iso(value: float | datetime | None) -> str | None:
    if value is None:
        return None
    moment = value if isinstance(value, datetime) else datetime.fromtimestamp(value, timezone.utc)
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _epoch(now):
    """A store clock that holds the time source, not the CLI (no reference cycle through self)."""
    return lambda: now().timestamp()


def _platform() -> tuple[str, str]:
    system = {"win32": "windows", "linux": "linux", "darwin": "darwin"}.get(sys.platform, sys.platform)
    machine = platform.machine().lower()
    arch = {"amd64": "amd64", "x86_64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine or "unknown")
    return system, arch


def batch_status(statuses: list[str]) -> str:
    """Status of a finished batch from its items (same rule as the specification checker)."""
    if not statuses or any(status in {"unknown", *LIVE} for status in statuses):
        return "unknown"
    if all(status == "succeeded" for status in statuses):
        return "succeeded"
    if any(status in {"succeeded", "partial"} for status in statuses):
        return "partial"
    return "failed"


def _status_for(rule: dict) -> str:
    """Operation record status for a receipt rule (operation-status semantics)."""
    if rule["ok"]:
        return "succeeded"
    if rule["codes"] == ["E_OUTCOME_UNKNOWN"]:
        return "unknown"
    if rule["codes"] == ["E_PARTIAL"]:
        return "partial"
    return "failed"


def _own(details: dict) -> dict:
    """Adapter details without the keys the SDK owns (dispatch state, identities, lookup, next step)."""
    return {key: value for key, value in details.items() if key not in SDK_DETAILS}


class DriverCLI:
    def __init__(self, contract: dict, adapter, *, validator: ContractValidator, state_dir=None, environ=None, home=None,
                 now=None, lease_seconds: float = LEASE_SECONDS, status_lease_seconds: float = STATUS_LEASE_SECONDS):
        self.contract = contract
        self.reference = contract["reference"]
        self.tool = self.reference["tool"]
        self.version = self.reference["version"]
        self.adapter = adapter
        self.validator = validator
        self.environ = os.environ if environ is None else environ
        self.home = Path(home) if home else Path.home()
        prefix = self.tool.upper().replace("-", "_")
        configured = self.environ.get(prefix + "_STATE_DIR")
        self.state_dir = Path(state_dir or configured or (self.home / f".{self.tool}"))
        self.record_profile = self.reference["driver_profile"]["record"]
        self.retain_days = self.record_profile.get("retain_days") or 30
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.lease_seconds = lease_seconds
        self.status_lease_seconds = status_lease_seconds
        self.commands = {tuple(command["path"].split()): command for command in self.reference["commands"]}
        self.codes = dict(validator.code_table())
        for code, entry in self.reference["error_codes"].items():
            self.codes[code] = {"exit": entry["exit"], "retryable": entry["retryable"]}
        self.scopes = ScopeResolver(self.tool, validator=validator, environ=self.environ, home=self.home, clock=self.now)
        self._store: StateStore | None = None
        self._warnings: list[str] = []
        self._in_flight: str | None = None
        self._abort = False

    # ================================================================ lifecycle

    def close(self) -> None:
        if self._store is not None:
            self._store.close()
            self._store = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False

    # ================================================================ entry point

    def run(self, argv, stdin=None) -> Output:
        started = time.monotonic()
        self._warnings = []
        self._in_flight = None
        self._abort = False
        flags = {"format": "json", "compact": False, "quiet": False}
        dispatched = None
        try:
            path, command, flags, raw, positional, words = self._parse(list(argv))
            if flags.get("help"):
                return self._help(path)
            if flags.get("version"):
                if words or positional or raw:
                    raise UsageProblem("conflicting_flags", "--version is used on its own.", parameter="--version",
                                       next_action=f"Run '{self.tool} --version' without a command.")
                envelope = self._ok({"tool": self.tool, "version": self.version, "driver_profile": self.reference["driver_profile"]})
            else:
                if command is None:
                    raise self._unknown_command(words)
                if positional:
                    raise UsageProblem("invalid_value", f"{command['path']} takes no positional arguments.",
                                       expected=[f"--{param['name']}" for param in command["params"]],
                                       next_action=f"Pass values with --name flags; see '{self.tool} reference --command \"{command['path']}\"'.")
                params = self._params(command, raw, stdin)
                self._check_flags(command, flags)
                if path in BUILTINS:
                    envelope = self._builtin(path, params, flags)
                elif path == UPDATE:
                    envelope = self._update(command, params, flags)
                elif command["type"] == "write" or set(command["driver"]["effects"]) & GATED:
                    envelope, dispatched = self._write(command, params, flags)
                else:
                    envelope = self._read(command, params, flags)
        except UsageProblem as problem:
            details = {"problem": problem.problem, "next_action": problem.next_action, "dispatch_state": "not_started"}
            if problem.parameter:
                details["parameter"] = problem.parameter
            if problem.expected is not None:
                details["expected"] = problem.expected
            envelope = self._fail(problem.code, problem.message, details)
        except DriverError as error:
            if self._committed():
                envelope, dispatched = self._stopped(error, "E_UNKNOWN", "unused", {})
            else:
                envelope = self._from_error(error)
        except StoreError as exc:
            envelope, dispatched = self._stopped(exc, "E_IO", "The operation record store is unavailable; nothing was dispatched.",
                                                 {"state_dir": str(self.state_dir), "cause": str(exc)})
        except BaseException as exc:  # noqa: BLE001 - every exit path still prints one envelope
            if isinstance(exc, (SystemExit, GeneratorExit)) and not self._in_flight:
                raise
            if isinstance(exc, KeyboardInterrupt):
                envelope, dispatched = self._stopped(exc, "E_INTERRUPTED", "Interrupted before anything was dispatched.", {})
            else:
                envelope, dispatched = self._stopped(exc, "E_UNKNOWN", f"The tool failed before dispatching anything ({type(exc).__name__}).", {})
        finally:
            self._in_flight = None
        return self._finish(envelope, started, flags, dispatched)

    def _committed(self) -> str | None:
        """The operation whose dispatch may have committed in this call, if an exception escaped after it."""
        operation = self._in_flight
        if not operation:
            return None
        try:
            record = self._store.get(operation) if self._store else None
        except Exception:  # noqa: BLE001 - an unreadable record leaves the outcome unknown
            record = None
        return None if record is not None and record["status"] == "planned" else operation

    def _stopped(self, exc, code: str, message: str, details: dict):
        """An exception escaped. If a dispatch may have committed, the outcome is unknown; otherwise nothing was sent."""
        operation = self._committed()
        if operation:
            self._settle_unknown(operation)
            return self._unknown(operation, f"The call stopped after dispatch ({type(exc).__name__}); the outcome is unknown."), operation
        details = dict(details, dispatch_state="not_started")
        return self._fail(code, message, details), None

    # ================================================================ parsing

    def _parse(self, argv):
        """Arity-aware parsing: the command path comes first, then flags; a declared parameter always takes its value."""
        flags = {"format": "json", "compact": False, "quiet": False}
        index = 0
        words = []
        while index < len(argv) and not argv[index].startswith("-"):
            words.append(argv[index])
            index += 1
        path, extra = None, list(words)
        for length in range(len(words), 0, -1):
            if tuple(words[:length]) in self.commands:
                path, extra = tuple(words[:length]), words[length:]
                break
        command = self.commands.get(path) if path else None
        declared = {param["name"]: param for param in command["params"]} if command else {}
        raw: dict[str, list[str]] = {}
        positional = list(extra)
        seen = set()
        rest = argv[index:]
        position = 0
        while position < len(rest):
            token = rest[position]
            if token == "-h":
                flags["help"] = True
                position += 1
                continue
            if not token.startswith("--"):
                if command is not None and token.startswith("-") and token != "-" and not _negative_number(token):
                    raise UsageProblem("unknown_flag", f"{command['path']} does not accept single-dash flags.", parameter=token[:2],
                                       expected=[f"--{key}" for key in declared] + self._applicable_globals(command),
                                       next_action="Use the --name form of the listed flags.")
                positional.append(token)
                position += 1
                continue
            name, has_value, inline = token.partition("=")
            if name in VALUE_FLAGS:
                value, position = self._value(rest, position, name, has_value, inline)
                key = name[2:].replace("-", "_")
                if key in seen and key != "format":
                    raise UsageProblem("conflicting_flags", f"{name} is given twice.", parameter=name, next_action=f"Pass {name} once.")
                seen.add(key)
                flags[key] = value
            elif name in BOOL_FLAGS:
                value = True if not has_value else self._boolean(inline, name)
                if name == "--json":
                    if value:
                        flags["format"] = "json"
                else:
                    flags[name[2:].replace("-", "_")] = value
                position += 1
            elif command is not None and name[2:] in declared:
                param = declared[name[2:]]
                if param["type"] == "boolean" and not has_value:
                    value = "true"
                    position += 1
                else:
                    value, position = self._value(rest, position, name, has_value, inline, param)
                raw.setdefault(name[2:], []).append(value)
            elif command is not None:
                raise UsageProblem("unknown_flag", f"{command['path']} does not accept {name}.", parameter=name,
                                   expected=[f"--{key}" for key in declared] + self._applicable_globals(command),
                                   next_action=f"Use the listed flags; see '{self.tool} reference --command \"{command['path']}\"'."
                                   + (" Plan with --dry-run and apply with --confirm <confirm_token>." if name == "--phase" else ""))
            else:
                positional.append(token)
                position += 1
        if flags["format"] not in {"json", "text"}:
            raise UsageProblem("invalid_value", "--format accepts json or text.", parameter="--format",
                               expected=["json", "text"], next_action="Use --format json (default) or --format text.")
        return path, command, flags, raw, positional, words

    def _value(self, tokens_, position, name, has_value, inline, param=None):
        if has_value:
            return inline, position + 1
        if position + 1 < len(tokens_) and not tokens_[position + 1].startswith("--"):
            return tokens_[position + 1], position + 2
        raise UsageProblem("missing_parameter", f"{name} needs a value.", parameter=name,
                           expected=self._describe(param) if param else None,
                           next_action=f"Pass a value after {name}; write a value that starts with -- as {name}=<value>.")

    @staticmethod
    def _boolean(text: str, name: str) -> bool:
        if text not in {"true", "false"}:
            raise UsageProblem("invalid_value", f"{name} takes no value, or =true / =false.", parameter=name,
                               expected=["true", "false"], next_action=f"Write {name} on its own.", code="E_VALIDATION")
        return text == "true"

    def _unknown_command(self, words) -> UsageProblem:
        if words and any(path[0] == words[0] for path in self.commands):
            options = sorted(" ".join(path) for path in self.commands if path[0] == words[0])
            return UsageProblem("unknown_command", "That is not a complete command.", expected=options,
                                next_action="Pick one of the listed commands; see reference --index.")
        return UsageProblem("unknown_command", "No command was given." if not words else "That is not a command.",
                            expected=sorted({path[0] for path in self.commands}),
                            next_action=f"Run '{self.tool} reference --index' to list the commands.")

    def _params(self, command, raw, stdin):
        declared = {param["name"]: param for param in command["params"]}
        values: dict = {}
        for name, items in raw.items():
            param = declared[name]
            if not param["multiple"] and len(items) > 1:
                raise UsageProblem("conflicting_flags", f"--{name} is given twice.", parameter=f"--{name}",
                                   next_action=f"Pass --{name} once.")
            parsed = []
            for item in items:
                pieces = _split_list(item) if param["multiple"] and param["type"] in {"string", "integer", "number"} else [item]
                parsed.extend(self._convert(param, piece, stdin) for piece in pieces)
            if param.get("deprecated"):
                self._warnings.append(f"warning: --{name} is deprecated; see reference --command \"{command['path']}\"")
            if param["multiple"]:
                unique = []
                for value in parsed:
                    if value not in unique:
                        unique.append(value)
                if not unique:
                    raise UsageProblem("invalid_value", f"--{name} needs at least one value.", parameter=f"--{name}",
                                       expected=self._describe(param), next_action=f"Pass one or more values to --{name}.",
                                       code="E_VALIDATION")
                values[name] = unique
            else:
                values[name] = parsed[0]
        for name, param in declared.items():
            if name in values:
                continue
            if param["required"]:
                raise UsageProblem("missing_parameter", f"{command['path']} needs --{name}.", parameter=f"--{name}",
                                   expected=self._describe(param), next_action=f"Add --{name} {self._describe(param)}.")
            if "default" in param:
                values[name] = param["default"]
        return values

    def _convert(self, param, raw, stdin):
        kind = param["type"]
        name = f"--{param['name']}"
        expected = self._describe(param)
        try:
            if kind in {"string", "path"}:
                if not raw:
                    raise ValueError("empty")
                raw.encode("utf-8")
                value = raw
            elif kind == "integer":
                if not re.fullmatch(r"-?(?:0|[1-9][0-9]{0,18})", raw):
                    raise ValueError("not an integer")
                value = int(raw)
                if not INT_MIN <= value <= INT_MAX:
                    raise ValueError("out of range")
            elif kind == "number":
                if not re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", raw):
                    raise ValueError("not a number")
                value = float(raw)
                if not math.isfinite(value):
                    raise ValueError("not finite")
            elif kind == "boolean":
                if raw not in {"true", "false"}:
                    raise ValueError("not a boolean")
                value = raw == "true"
            else:
                payload = self._json_source(raw, stdin, name)
                value = json.loads(payload, parse_constant=_reject_constant, parse_float=_finite_float)
                json.dumps(value, ensure_ascii=False).encode("utf-8")
        except UsageProblem:
            raise
        except (ValueError, OverflowError, RecursionError, UnicodeError) as exc:
            raise UsageProblem("invalid_value", f"{name} needs {expected}.", parameter=name, expected=expected,
                               next_action=f"Pass {name} as {expected}.", code="E_VALIDATION") from exc
        if "enum" in param and value not in param["enum"]:
            raise UsageProblem("invalid_value", f"{name} must be one of the listed values.", parameter=name,
                               expected=param["enum"], next_action=f"Pass one of the listed values to {name}.", code="E_VALIDATION")
        return value

    def _json_source(self, raw, stdin, name):
        try:
            if raw == "-":
                stream = stdin if stdin is not None else sys.stdin.buffer
                payload = stream.read(MAX_JSON_BYTES + 1)
            elif raw.startswith("@"):
                path = Path(raw[1:])
                if not path.is_file():
                    raise UsageProblem("invalid_value", f"{name} names a file that does not exist.", parameter=name,
                                       next_action=f"Pass {name} @<existing file> or inline JSON.", code="E_VALIDATION")
                with path.open("rb") as stream:
                    payload = stream.read(MAX_JSON_BYTES + 1)
            else:
                payload = raw.encode("utf-8")
        except OSError as exc:
            raise UsageProblem("invalid_value", f"{name} could not be read.", parameter=name,
                               next_action=f"Pass {name} as inline JSON or a readable @file.", code="E_VALIDATION") from exc
        if len(payload) > MAX_JSON_BYTES:
            raise UsageProblem("input_too_large", f"{name} is larger than 8 MiB.", parameter=name,
                               next_action="Pass a smaller document or split the request.", code="E_VALIDATION")
        if payload.startswith(b"\xef\xbb\xbf"):
            raise UsageProblem("invalid_value", f"{name} must be UTF-8 JSON without a BOM.", parameter=name,
                               next_action="Remove the byte-order mark.", code="E_VALIDATION")
        return payload.decode("utf-8")

    @staticmethod
    def _describe(param) -> str:
        unit = f" in {param['unit']}" if "unit" in param else ""
        if "enum" in param:
            return "one of " + ", ".join(map(str, param["enum"]))
        return {"string": "a non-empty string", "path": "a path", "integer": "an integer", "number": "a finite number" + unit,
                "boolean": "true or false", "json": "JSON (inline, @file or - for stdin)"}[param["type"]] + ("" if param["type"] == "number" else unit)

    def _applicable_globals(self, command) -> list[str]:
        common = ["--format", "--compact", "--quiet"]
        if tuple(command["path"].split()) == UPDATE:
            return common + ["--dry-run"]
        if command["type"] == "write":
            return common + ["--dry-run", "--confirm", "--dangerous", "--request-id", "--idempotency-key"]
        return common + ["--fields"]

    def _check_flags(self, command, flags):
        path = tuple(command["path"].split())
        write = command["type"] == "write" or bool(set(command["driver"]["effects"]) & GATED)
        if path == UPDATE:
            for flag, key in WRITE_ONLY_FLAGS[1:]:
                if _given(flags, key):
                    raise UsageProblem("conflicting_flags", f"update is one call and does not take {flag}.", parameter=flag,
                                       next_action="Run update on its own; use update --check or update --dry-run to look first.")
        else:
            for flag, key in WRITE_ONLY_FLAGS:
                if _given(flags, key) and not write:
                    raise UsageProblem("conflicting_flags", f"{flag} applies only to commands that change something.",
                                       parameter=flag, next_action=f"Drop {flag}; {command['path']} does not change anything.")
        if flags.get("fields") and write:
            raise UsageProblem("conflicting_flags", "--fields applies to read commands.", parameter="--fields",
                               next_action="Drop --fields; a receipt is always returned whole.")
        if _given(flags, "request_id") and _given(flags, "idempotency_key"):
            raise UsageProblem("conflicting_flags", "--request-id and --idempotency-key are the same key.",
                               parameter="--idempotency-key", next_action="Pass only one of them.")
        key = _idempotency_key(flags)
        if key is not None:
            flag = "--request-id" if _given(flags, "request_id") else "--idempotency-key"
            if not 1 <= len(key) <= 512 or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in key):
                raise UsageProblem("invalid_value", f"{flag} must be 1 to 512 printable characters.", parameter=flag,
                                   expected="1-512 printable characters", next_action=f"Pass a shorter {flag}.", code="E_VALIDATION")
            if "batch" in command["driver"]["profiles"]:
                raise UsageProblem("conflicting_flags", f"{command['path']} is a batch; each item runs under its own operation_id.",
                                   parameter=flag, next_action=f"Drop {flag}; after a lost reply, query the item operations instead.")

    # ================================================================ envelopes

    def _ok(self, data: dict) -> dict:
        return {"ok": True, "schema_version": "1.0", "data": data, "meta": {"duration_ms": 0}}

    def _fail(self, code: str, message: str, details: dict) -> dict:
        entry = self.codes.get(code, {"retryable": False})
        if "next_action" not in details:
            details["next_action"] = NEXT_ACTION.get(code, "Read the message and details, then decide; do not resend blindly.")
        return {"ok": False, "schema_version": "1.0",
                "error": {"code": code, "message": message, "retryable": entry["retryable"], "details": details},
                "meta": {"duration_ms": 0}}

    def _conflict(self, reason: str, message: str, **extra) -> dict:
        details = {"dispatch_state": "not_started", "conflict": reason, "next_action": CONFLICT_ACTION[reason]}
        details.update(extra)
        return self._fail("E_CONFLICT", message, details)

    def _lookup(self, operation_id: str) -> dict:
        return {"lookup": f"{self.tool} operation show --id {operation_id}", "lookup_argv": ["operation", "show", "--id", operation_id]}

    def _blocked(self, record: dict, live: bool) -> dict:
        operation = record["operation_id"]
        if live:
            return self._conflict("lock_held", f"Operation {operation} on this target is still running.",
                                  active_operation=operation, **self._lookup(operation))
        return self._conflict("unresolved_operation", f"Operation {operation} on this target has an unknown outcome.",
                              active_operation=operation, **self._lookup(operation))

    def _from_error(self, error: DriverError, operation_id: str | None = None) -> dict:
        details = dict(error.details)
        details.setdefault("dispatch_state", error.dispatch_state)
        if operation_id:
            details.setdefault("operation_id", operation_id)
        if error.receipt is not None:
            details["receipt"] = error.receipt
        return self._fail(error.code, error.message, details)

    def _unknown(self, operation_id: str, message: str, receipt: dict | None = None, extra: dict | None = None) -> dict:
        details = {"dispatch_state": "unknown" if receipt is None else "dispatched", "operation_id": operation_id,
                   **self._lookup(operation_id), "next_action": NEXT_ACTION["E_OUTCOME_UNKNOWN"]}
        if receipt is not None:
            details["receipt"] = receipt
        details.update(_own(extra or {}))
        return self._fail("E_OUTCOME_UNKNOWN", message, details)

    def _finish(self, envelope: dict, started: float, flags: dict, dispatched: str | None) -> Output:
        envelope["meta"]["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
        try:
            self.validator.validate("response", envelope)
        except (ContractError, ContractValidationError) as exc:
            problems = list(getattr(exc, "errors", [str(exc)]))[:10]
            if dispatched:
                try:
                    self._store_open().demote(dispatched, "result_failed_contract")
                except StoreError:
                    pass
                envelope = self._unknown(dispatched, "The adapter's result does not satisfy the contract, so the outcome cannot be established.",
                                         extra={"contract_errors": problems})
            else:
                envelope = self._fail("E_UNKNOWN", "The tool produced a result that does not satisfy its own contract; nothing was dispatched.",
                                      {"dispatch_state": "not_started", "contract_errors": problems})
            envelope["meta"]["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
        code = 0 if envelope["ok"] else self.codes.get(envelope["error"]["code"], {"exit": 1})["exit"]
        stderr = "" if flags.get("quiet") else "".join(line + "\n" for line in self._warnings)
        return Output(self._render(envelope, flags), code, envelope, stderr)

    def _render(self, envelope: dict, flags: dict) -> bytes:
        if flags.get("format") == "text":
            text = "\n".join(_text_lines(envelope)) + "\n"
            return text.encode("utf-8", "backslashreplace")
        separators = (",", ":") if flags.get("compact") else None
        indent = None if flags.get("compact") else 2
        try:
            text = json.dumps(envelope, ensure_ascii=False, separators=separators, indent=indent, allow_nan=False)
            return (text + "\n").encode("utf-8")
        except UnicodeEncodeError:
            # Unpaired surrogates from the command line stay escaped instead of breaking the output.
            text = json.dumps(envelope, ensure_ascii=True, separators=separators, indent=indent, allow_nan=False)
            return (text + "\n").encode("ascii")

    # ================================================================ help

    def _help(self, path) -> Output:
        if path:
            command = self.commands[path]
            lines = [f"usage: {self.tool} {command['path']} [flags]", "", command["description"], ""]
            for param in command["params"]:
                need = "required" if param["required"] else "optional"
                lines.append(f"  --{param['name']}  {self._describe(param)} ({need})")
            lines += ["", "global flags: " + " ".join(self._applicable_globals(command))]
            if path != UPDATE and (command["type"] == "write" or set(command["driver"]["effects"]) & GATED):
                lines += ["", "This command changes something: run it with --dry-run, read data.preview,",
                          "then run it again with --confirm <confirm_token>."]
        else:
            lines = [f"usage: {self.tool} <command> [flags]", "", "commands:"]
            for key in sorted(self.commands):
                command = self.commands[key]
                mark = " [write]" if command["type"] == "write" else ""
                mark += " [dangerous]" if command.get("dangerous") else ""
                lines.append(f"  {' '.join(key)}{mark}  {command['description']}")
            lines += ["", "Machine-readable discovery: reference --index, then reference --command <path>.",
                      "global flags: --format json|text --compact --fields --quiet --dry-run --confirm --dangerous --request-id --version --help"]
        return Output(("\n".join(lines) + "\n").encode("utf-8", "backslashreplace"), 0, None, "")

    # ================================================================ state helpers

    def _store_open(self) -> StateStore:
        if self._store is None:
            self._store = StateStore(self.state_dir, retain_days=self.retain_days, clock=_epoch(self.now))
        self._store.connection()
        return self._store

    def _scope(self):
        return self.scopes.resolve()

    def _digest(self, command, params) -> str:
        return tokens.digest({"command": command["path"], "params": params})

    def _settle_unknown(self, operation_id: str) -> None:
        try:
            store = self._store_open()
            record = store.get(operation_id)
            if record is not None and record["status"] == "dispatched":
                store.finish(operation_id, "unknown")
        except Exception:  # noqa: BLE001 - an expiring lease still turns the record unknown
            pass

    @contextmanager
    def _heartbeat(self, operation_id: str):
        """Renew the operation's lease while the native call runs, from a separate store connection."""
        stop = threading.Event()
        state_dir, retain, clock, lease = self.state_dir, self.retain_days, _epoch(self.now), self.lease_seconds

        def beat():
            store = StateStore(state_dir, retain_days=retain, clock=clock)
            try:
                while not stop.wait(max(lease / 3, 0.05)):
                    try:
                        store.heartbeat(operation_id, lease)
                    except StoreError:
                        pass
            finally:
                store.close()

        thread = threading.Thread(target=beat, name=f"lease-{operation_id}", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=5)

    def _check_budget(self, store: StateStore, scope, units: int) -> None:
        limit = scope.max_operations
        if limit is None:
            return
        used = store.started_under(scope.scope_id)
        if used + units > limit:
            raise forbidden(scope, {"limit": f"limits.max_operations is {limit} and {used} operations already ran under this scope"})

    # ================================================================ builtins

    def _builtin(self, path, params, flags) -> dict:
        if path == ("reference",):
            return self._project(self._reference(params), flags, list_key=None)
        if path == ("context",):
            return self._project(self._ok(self._context()), flags, list_key=None)
        if path == ("doctor",):
            return self._project(self._ok(self._doctor()), flags, list_key=None)
        if path == ("changelog",):
            return self._project(self._changelog(params), flags, list_key="entries")
        if path == ("operation", "show"):
            return self._operation_show(params)
        if path == ("operation", "list"):
            return self._project(self._operation_list(params), flags, list_key="items")
        return self._operation_resolve(params)

    def _reference(self, params) -> dict:
        if params.get("index") and params.get("command"):
            raise UsageProblem("conflicting_flags", "--index and --command select different views.", parameter="--command",
                               next_action="Use --index for the list, then --command <path> for one command.")
        reference = self.reference
        if params.get("index"):
            return self._ok({"view": "index", "tool": self.tool, "version": self.version, "risk_tier": reference["risk_tier"],
                             "driver_profile": reference["driver_profile"],
                             "commands": [{"path": c["path"], "type": c["type"], "dangerous": bool(c.get("dangerous")),
                                           "description": c["description"], "effects": c["driver"]["effects"],
                                           "maturity": c["driver"]["maturity"]} for c in reference["commands"]]})
        selected = params.get("command")
        if selected:
            command = self.commands.get(tuple(selected.split()))
            if command is None:
                raise UsageProblem("unknown_capability", "That is not a command path.", parameter="--command",
                                   expected=sorted(c["path"] for c in reference["commands"]),
                                   next_action="Use a path from reference --index.", code="E_VALIDATION")
            label = command["output_schema"]
            entry = reference["schemas"][label]
            names = {command["driver"].get(key) for key in ("request_schema", "result_schema")}
            reference_ = entry.get("json_schema", {}).get("$ref")
            if isinstance(reference_, str):
                names.add(reference_.split("#", 1)[0])
            names.discard(None)
            products = {key: value for key, value in reference.get("json_schemas", {}).items() if key in names}
            return self._ok({"view": "command", "tool": self.tool, "version": self.version,
                             "driver_profile": reference["driver_profile"], "command": command, "schemas": {label: entry},
                             "error_codes": {code: reference["error_codes"][code] for code in command["driver"]["error_codes"]},
                             "json_schemas": products,
                             "public_schemas": sorted(name for name in names if name.startswith("urn:tool-driver-cli-spec:"))})
        return self._ok(reference)

    def _cli_component(self) -> dict:
        cli = {"name": self.tool, "role": "cli", "version": self.version, "build_id": self.contract.get("build_id"),
               "location": "disk", "identity_status": "observed"}
        if cli["build_id"] is None:
            cli.update(identity_status="unavailable", reason="The generated contract records no build identity.")
        return cli

    def _hook(self, name: str, default):
        hook = getattr(self.adapter, name, None)
        if not callable(hook):
            return default, None
        try:
            return hook(), None
        except Exception as exc:  # noqa: BLE001 - a failing hook degrades context and doctor, never breaks them
            return default, f"{name}() failed ({type(exc).__name__})"

    def _context(self) -> dict:
        scope = self._scope()
        system, arch = _platform()
        unconnected = {"status": "unknown", "instance_id": None, "generation": None, "ownership": None,
                       "reason": "The adapter reports no connection."}
        environment, env_problem = self._hook("environment", {"components": [], "connection": unconnected})
        credentials, cred_problem = self._hook("credentials", {"configured": False, "required": False})
        store = StateStore(self.state_dir, retain_days=self.retain_days)
        available, reason = store.available()
        state = {"dir": str(self.state_dir), "available": available, "protection": store.protection(),
                 "record_retention_days": self.retain_days}
        if not available:
            state["reason"] = reason
        store.close()

        def build(environment_, credentials_):
            environment_ = environment_ if isinstance(environment_, dict) else {}
            return {"version": self.version, "credentials": credentials_, "driver_profile": self.reference["driver_profile"],
                    "authorization": scope.authorization,
                    "environment": {"platform": system, "arch": arch,
                                    "components": [self._cli_component(), *list(environment_.get("components") or [])],
                                    "connection": environment_.get("connection") or unconnected},
                    "state": state}

        fallback = {"configured": False, "required": False}
        data = build(environment, fallback)
        if env_problem or not self._valid("context", data):
            reason = env_problem or "environment() returned data that does not satisfy the context schema"
            degraded = {"components": [{"name": "adapter environment", "role": "adapter", "version": None, "build_id": None,
                                        "location": "unknown", "identity_status": "unavailable", "reason": reason}],
                        "connection": dict(unconnected, reason=reason)}
            data = build(degraded, fallback)
        candidate = dict(data, credentials=credentials)
        if not cred_problem and self._valid("context", candidate):
            return candidate
        data["credentials"] = {"configured": False,
                               "reason": cred_problem or "credentials() returned data that does not satisfy the context schema"}
        return data

    def _valid(self, kind: str, value) -> bool:
        try:
            self.validator.validate(kind, value)
            return True
        except (ContractError, ContractValidationError):
            return False

    def _doctor(self) -> dict:
        checks = []
        readiness = self.reference["release_readiness"]
        level = readiness["level"]
        checks.append({"check": "release_readiness", "status": {"stable": "pass", "beta": "warn"}.get(level, "fail"),
                       "fix": None if level == "stable" else readiness.get("reason") or "Record the evidence release_readiness lists before declaring stable.",
                       "message": f"release_readiness is {level}"})
        scope = self._scope()
        status = scope.summary["status"]
        if status == "granted":
            checks.append({"check": "authorization", "status": "pass", "fix": None,
                           "message": f"scope {scope.summary['scope_id']} allows {', '.join(scope.summary['effects'])}"})
        elif status == "read_only":
            checks.append({"check": "authorization", "status": "warn",
                           "fix": f"Read-only. For changes, the user grants a task scope in {scope.grant_file}.",
                           "message": "no task scope; only observe is allowed"})
        else:
            checks.append({"check": "authorization", "status": "fail", "fix": f"The user fixes or replaces {scope.grant_file}.",
                           "message": scope.summary["reason"]})
        store = StateStore(self.state_dir, retain_days=self.retain_days)
        available, reason = store.available()
        store.close()
        checks.append({"check": "state_store", "status": "pass" if available else "fail",
                       "fix": None if available else f"Make {self.state_dir} a writable directory that is not a link.",
                       **({} if available else {"message": reason})})
        extra, problem = self._hook("doctor_checks", [])
        accepted = []
        for check in extra if isinstance(extra, list) else []:
            try:
                # One adapter check is a fragment; required checks belong to the complete report.
                self.validator.validate_shape("doctor", {"checks": [check]})
                accepted.append(check)
            except (ContractError, ValueError, TypeError):
                problem = problem or "doctor_checks() returned a check that does not satisfy the doctor schema"
        if problem or not isinstance(extra, list):
            accepted = [check for check in accepted if check.get("check") != "native_backend"]
            accepted.insert(0, {"check": "native_backend", "status": "fail", "fix": "Fix the adapter's doctor_checks() so it observes the application.",
                                "message": problem or "doctor_checks() did not return a list"})
        elif not any(check.get("check") == "native_backend" for check in accepted):
            accepted.insert(0, {"check": "native_backend", "status": "fail", "fix": "Implement doctor_checks() in the adapter to observe the application.",
                                "message": "the adapter reports no native backend check"})
        checks.extend(accepted)
        return {"checks": checks}

    def _changelog(self, params) -> dict:
        entries = list(self.contract.get("changelog", []))
        data = {"current_version": self.version}
        if params.get("since"):
            if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?", params["since"]):
                raise UsageProblem("invalid_value", "--since needs a version such as 1.2.0.", parameter="--since",
                                   expected="MAJOR.MINOR.PATCH", next_action="Pass the version you last used.", code="E_VALIDATION")
            entries = changelog_since(entries, params["since"])
            data["since"] = params["since"]
        data.update(count=len(entries), entries=entries)
        return self._ok(data)

    # ================================================================ operation records

    def _status_of(self, record: dict) -> dict:
        now = self.now()
        stamp = now.timestamp()
        status = effective_status(record, stamp)
        extra = record.get("extra") or {}
        stage = extra.get("stage")
        if record["status"] == "dispatched":
            stage = "apply" if status == "running" else "dispatched_without_result"
        if status == "planned" and record["plan_expires_at"] and record["plan_expires_at"] < stamp:
            stage = "plan_expired"
        plan = record.get("plan") or {}
        receipt = record.get("receipt")
        operation = record["operation_id"]
        value = {
            "operation_id": operation, "capability_id": record["capability_id"], "request_id": record.get("idempotency_key"),
            "plan_id": record.get("plan_id"), "plan": record.get("plan"),
            "target": (receipt or {}).get("target") or plan.get("target"),
            "status": status, "stage": stage, "observed_at": _iso(now), "started_at": _iso(record.get("started_at")),
            "finished_at": _iso(record.get("finished_at")), "wait_timeout_ms": extra.get("wait_timeout_ms"),
            "execution_deadline": extra.get("execution_deadline"), "progress": extra.get("progress"),
            "record": None,
            "commands": {"lookup": {"argv": ["operation", "show", "--id", operation], "read_only": True}, "result": None, "cancel": None},
            "blocked": extra.get("blocked"), "resolution": record.get("resolution"),
            "stop_observed": bool(extra.get("stop_observed", False)), "stop_evidence_ref": extra.get("stop_evidence_ref"),
            "receipt": receipt,
        }
        kept = {name: extra[name] for name in ("late_result", "rejected_receipt") if extra.get(name) is not None}
        if kept:
            value["extensions"] = kept
        if record.get("kind") == "batch":
            items = self._store_open().items_of(operation)
            value["items"] = [{"key": item["item_key"], "operation_id": item["operation_id"], "status": effective_status(item, stamp)}
                              for item in items]
            value["target"] = None
            if status not in {"planned", "running"}:
                value["status"] = batch_status([item["status"] for item in value["items"]])
        unresolved = value["status"] in {"unknown", *LIVE} and record.get("resolution") is None
        profile = self.record_profile
        value["record"] = {"availability": "available",
                           "retain_until": None if unresolved else _iso(record["updated_at"] + self.retain_days * 86400),
                           "survives_cli_exit": bool(profile.get("survives_cli_exit", True)),
                           "survives_reconnect": bool(profile.get("survives_reconnect", True)),
                           "survives_instance_restart": bool(profile.get("survives_instance_restart", True)),
                           "limitations": list(profile.get("limitations") or [
                               "This is the CLI's local record; after an application restart the native outcome may still need a read-back."])}
        return value

    def _record(self, operation_id: str) -> dict:
        record = self._store_open().get(operation_id)
        if record is None:
            raise DriverError("E_NOT_FOUND", "No operation record has this ID.",
                              details={"problem": "unknown_operation", "parameter": "--id",
                                       "next_action": "Run operation list to see the records; an expired record does not mean the operation never ran."})
        return record

    def _operation_show(self, params) -> dict:
        self._store_open().settle_batches(batch_status)
        record = self._record(params["id"])
        hook = getattr(self.adapter, "status", None)
        if (callable(hook) and record.get("kind") != "batch" and record["status"] in LIVE and record.get("resolution") is None):
            try:
                update = hook(record)
            except Exception as exc:  # noqa: BLE001 - fall back to the local record
                self._warnings.append(f"warning: the adapter's status query failed ({type(exc).__name__}); showing the local record")
                update = None
            if update:
                self._apply_status_update(record, update)
                record = self._record(params["id"])
        return self._ok({"operation_status": self._status_of(record)})

    def _apply_status_update(self, record: dict, update) -> None:
        """Store what the adapter observed about a submitted operation, only if the result stays consistent."""
        store = self._store_open()
        operation = record["operation_id"]
        if not isinstance(update, dict):
            self._warnings.append("warning: the adapter's status query returned no object; showing the local record")
            return
        status = update.get("status")
        extra = {key: update[key] for key in STATUS_EXTRAS if key in update}
        receipt = None
        if update.get("receipt") is not None:
            receipt = self._complete_receipt(update["receipt"], record, record.get("plan") or {}, record.get("idempotency_key"))
            if receipt is None:
                self._warnings.append("warning: the adapter's receipt does not satisfy the contract; showing the local record")
                return
            derived = _status_for(self.validator.receipt_rule(receipt))
            stopped = receipt["outcome"]["execution"] in {"interrupted", "not_started"}
            status = "cancelled" if status == "cancelled" and extra.get("stop_observed") and stopped else derived
        elif status in {"succeeded", "partial", "failed", "cancelled"}:
            # A stopped job does not establish whether target changes remain. Keep the target blocked.
            extra["reported_terminal_status"] = status
            status = "unknown"
            self._warnings.append("warning: terminal status without an effect readback is unknown; the target remains unresolved")
        elif status not in {*LIVE, "unknown"}:
            self._warnings.append("warning: the adapter's status is not a known operation state; showing the local record")
            return
        lease = self.now().timestamp() + self.status_lease_seconds if status in LIVE else None
        candidate = dict(record, status=status, receipt=receipt, extra=dict(record.get("extra") or {}, **extra), lease_until=lease)
        if not self._valid("operation-status", self._status_of(candidate)):
            self._warnings.append("warning: the adapter's status does not satisfy the contract; showing the local record")
            return
        if status in LIVE:
            updated = store.update_status(operation, status, candidate["extra"], self.status_lease_seconds)
        else:
            updated = store.finish(operation, status, receipt, candidate["extra"])
        if not updated:
            self._warnings.append("warning: the operation was resolved or finished meanwhile; the adapter's status is kept as late_result")

    def _operation_list(self, params) -> dict:
        limit = params.get("limit", 20)
        offset = params.get("offset", 0)
        if not 1 <= limit <= 200:
            raise UsageProblem("invalid_value", "--limit must be between 1 and 200.", parameter="--limit", expected="1..200",
                               next_action="Pass a smaller --limit.", code="E_VALIDATION")
        if offset < 0:
            raise UsageProblem("invalid_value", "--offset cannot be negative.", parameter="--offset", expected="0 or more",
                               next_action="Pass --offset 0 or the next_offset of the previous page.", code="E_VALIDATION")
        store = self._store_open()
        store.settle_batches(batch_status)
        rows, total = store.list(params.get("status"), limit, offset)
        stamp = self.now().timestamp()
        items = []
        for row in rows:
            status = effective_status(row, stamp)
            if row.get("kind") == "batch" and status not in {"planned", "running"}:
                status = batch_status([effective_status(item, stamp) for item in store.items_of(row["operation_id"])])
            items.append({"operation_id": row["operation_id"], "capability_id": row["capability_id"], "command": row["command"],
                          "kind": row.get("kind") or "single", "status": status, "resolved": row.get("resolution") is not None,
                          "created_at": _iso(row["created_at"])})
        more = offset + len(rows) < total
        data = {"items": items, "count": len(items), "has_more": more, "truncated": False, "offset": offset,
                "scope": {"description": f"Operation records in {self.state_dir}"},
                "snapshot": None, "snapshot_unavailable_reason": "The local record store has no revision; records can change between pages.",
                "sort": [{"field": "created_at", "direction": "desc"}],
                "limits": {"requested": limit, "effective": limit, "truncation_reason": None}}
        if more:
            data["next_offset"] = offset + len(rows)
        return self._ok(data)

    def _operation_resolve(self, params) -> dict:
        record = self._record(params["id"])
        operation = record["operation_id"]
        if record.get("kind") == "batch":
            raise UsageProblem("invalid_value", "A batch is resolved item by item.", parameter="--id",
                               expected=[item["operation_id"] for item in self._store_open().items_of(operation)],
                               next_action="Run operation show on the batch, then resolve each unknown or partial item.", code="E_VALIDATION")
        status = effective_status(record, self.now().timestamp())
        if status in LIVE:
            return self._conflict("lock_held", f"Operation {operation} is still running; it cannot be resolved yet.",
                                  active_operation=operation, **self._lookup(operation))
        if status not in {"unknown", "partial"}:
            raise UsageProblem("invalid_value", f"Operation {operation} is {status}; only unknown or partial operations are resolved.",
                               parameter="--id", next_action="Run operation show to see its recorded outcome.", code="E_VALIDATION")
        if record.get("resolution") is not None:
            raise UsageProblem("invalid_value", f"Operation {operation} was already resolved.", parameter="--id",
                               next_action="Run operation show to see the recorded resolution.", code="E_VALIDATION")
        scope = self._scope()
        plan = record.get("plan") or {}
        scope.require(effects=record["effects"], target=plan.get("target"))
        resolution = {"outcome": params["outcome"], "evidence": params["evidence"], "resolved_at": _iso(self.now()),
                      "recorded_by": "operation resolve (agent or person at this terminal)"}
        try:
            record = self._store_open().resolve(operation, resolution)
        except LeaseHeld:
            return self._conflict("lock_held", f"Operation {operation} is still running; it cannot be resolved yet.",
                                  active_operation=operation, **self._lookup(operation))
        except TokenUsed:
            raise UsageProblem("invalid_value", f"Operation {operation} was already resolved.", parameter="--id",
                               next_action="Run operation show to see the recorded resolution.", code="E_VALIDATION") from None
        return self._ok({"operation_status": self._status_of(record)})

    # ================================================================ projection

    def _project(self, envelope: dict, flags: dict, list_key: str | None) -> dict:
        fields = flags.get("fields")
        if not fields or not envelope["ok"]:
            return envelope
        wanted = [item.strip() for item in fields.split(",") if item.strip()]
        data = envelope["data"]
        container = data[list_key] if list_key and isinstance(data.get(list_key), list) else None
        sample = container[0] if container else data
        available = sorted(key for key in (sample or {}) if key != "_untrusted")
        if container is not None and not container:
            return envelope
        missing = [name for name in wanted if name.split(".", 1)[0] not in (sample or {})]
        if missing:
            raise UsageProblem("invalid_value", "--fields names fields this result does not have.", parameter="--fields",
                               expected=available, next_action="Pick fields from the expected list.", code="E_VALIDATION")
        if container is not None:
            data[list_key] = [_pick(item, wanted) for item in container]
        else:
            keep = {name.split(".", 1)[0] for name in wanted}
            projected = _pick(data, wanted)
            if "_untrusted" in data:
                projected["_untrusted"] = [name for name in data["_untrusted"] if name.split(".", 1)[0] in keep]
            envelope["data"] = projected
        return envelope

    # ================================================================ read commands

    def _read(self, command, params, flags) -> dict:
        scope = self._scope()
        call = Call(command=command["path"], capability_id=command["driver"]["capability_id"], params=params,
                    effects=tuple(command["driver"]["effects"]), dangerous=bool(command.get("dangerous")),
                    authorization=scope.authorization, extras={"scope": scope})
        data = self.adapter.run(call)
        return self._project(self._ok(self._checked_output(command, data)), flags, "items" if isinstance(data, dict) and "items" in data else None)

    def _checked_output(self, command, data) -> dict:
        if not isinstance(data, dict):
            raise DriverError("E_UNKNOWN", "The adapter returned no data object; nothing was dispatched.",
                              details={"next_action": NEXT_ACTION["E_UNKNOWN"]})
        entry = self.reference["schemas"][command["output_schema"]]
        if "_untrusted" not in data and entry["untrusted_fields"]:
            marked = [name for name in entry["untrusted_fields"] if name.split(".", 1)[0] in data]
            if marked:
                data["_untrusted"] = marked
        try:
            if "query_page" in command["driver"]["profiles"]:
                self.validator.validate("query-page", data)
            if "json_schema" in entry:
                self.validator.validate_product(entry["json_schema"], data, self.reference.get("json_schemas", {}))
        except (ContractError, ContractValidationError) as exc:
            raise DriverError("E_UNKNOWN", "The adapter's result does not satisfy the declared output schema.",
                              details={"contract_errors": list(getattr(exc, "errors", [str(exc)]))[:10],
                                       "next_action": NEXT_ACTION["E_UNKNOWN"]}) from exc
        return data

    def _update(self, command, params, flags) -> dict:
        """Base §14 self-update: one call, no confirm token and no task scope; the adapter does the work."""
        hook = getattr(self.adapter, "update", None)
        if not callable(hook):
            raise DriverError("E_UNSUPPORTED", "This build has no self-update.",
                              details={"next_action": "Update the tool with the package manager it was installed with."})
        call = Call(command=command["path"], capability_id=command["driver"]["capability_id"], params=params,
                    effects=tuple(command["driver"]["effects"]), dry_run=bool(flags.get("dry_run")))
        return self._ok(self._checked_output(command, hook(call)))

    # ================================================================ write commands

    def _write(self, command, params, flags):
        dangerous = bool(command.get("dangerous"))
        if flags.get("dry_run") and flags.get("confirm"):
            raise UsageProblem("conflicting_flags", "--dry-run and --confirm are two separate calls.", parameter="--confirm",
                               next_action="Run --dry-run first, then the same command with --confirm <confirm_token>.")
        if not flags.get("dry_run") and not flags.get("confirm"):
            raise UsageProblem("confirmation_required", f"{command['path']} changes something and needs a plan first.",
                               code="E_CONFIRMATION_REQUIRED",
                               next_action="Run the same command with --dry-run, read data.preview, then run it with --confirm <confirm_token>.")
        if dangerous and not flags.get("dangerous"):
            raise UsageProblem("dangerous_flag_required", f"{command['path']} is dangerous and needs --dangerous on both calls.",
                               code="E_CONFIRMATION_REQUIRED", parameter="--dangerous",
                               next_action="Confirm with the user first (STOP CHECKPOINT), then add --dangerous.")
        scope = self._scope()
        missing = scope.missing(effects=command["driver"]["effects"], dangerous=dangerous)
        if missing:
            raise forbidden(scope, missing)
        batch = "batch" in command["driver"]["profiles"]
        if flags.get("dry_run"):
            return (self._dry_run_batch if batch else self._dry_run)(command, params, scope), None
        return (self._confirm_batch if batch else self._confirm)(command, params, flags, scope)

    def _plan_body(self, fields: dict, driver: dict, scope, expires: datetime) -> dict:
        return {"authorization_scope_id": scope.scope_id, "effects": fields.get("effects", list(driver["effects"])),
                "level": fields.get("level", "plan"), "changes": fields.get("changes", []), "outputs": fields.get("outputs", []),
                "unknowns": fields.get("unknowns", []), "constraints": fields.get("constraints", []),
                "trial_snapshot": fields.get("trial_snapshot"), "expires_at": _iso(expires)}

    def _checked_plan(self, plan: dict, driver: dict) -> dict:
        try:
            self.validator.validate("plan", plan)
        except (ContractError, ContractValidationError) as exc:
            raise DriverError("E_UNKNOWN", "The adapter's plan does not satisfy the plan schema; nothing was dispatched.",
                              details={"contract_errors": list(getattr(exc, "errors", [str(exc)]))[:10],
                                       "next_action": NEXT_ACTION["E_UNKNOWN"]}) from exc
        if not set(plan["effects"]) <= set(driver["effects"]):
            raise DriverError("E_UNKNOWN", "The adapter planned an effect the command does not declare; nothing was dispatched.",
                              details={"next_action": NEXT_ACTION["E_UNKNOWN"]})
        outputs = plan["outputs"] + [output for item in plan.get("items", []) for output in item["outputs"]]
        if "deliver" in plan["effects"] and not any(output["effect"] == "deliver" for output in outputs):
            raise DriverError("E_UNKNOWN", "The adapter planned a delivery without naming its destination in outputs; nothing was dispatched.",
                              details={"next_action": NEXT_ACTION["E_UNKNOWN"]})
        return plan

    def _dry_run(self, command, params, scope) -> dict:
        store = self._store_open()
        self._check_budget(store, scope, 1)
        operation_id, plan_id = "op-" + uuid.uuid4().hex[:24], "plan-" + uuid.uuid4().hex[:24]
        driver = command["driver"]
        call = Call(command=command["path"], capability_id=driver["capability_id"], params=params, effects=tuple(driver["effects"]),
                    dangerous=bool(command.get("dangerous")), dry_run=True, operation_id=operation_id, plan_id=plan_id,
                    authorization=scope.authorization, extras={"scope": scope})
        fields = self.adapter.plan(call)
        if not isinstance(fields, dict):
            raise DriverError("E_UNKNOWN", "The adapter returned no plan object; nothing was dispatched.",
                              details={"next_action": NEXT_ACTION["E_UNKNOWN"]})
        expires = self.now() + PLAN_TTL
        plan = {"plan_id": plan_id, "operation_id": operation_id, "capability_id": driver["capability_id"],
                "target": fields.get("target"), "base_snapshot": fields.get("base_snapshot"),
                **self._plan_body(fields, driver, scope, expires), "can_apply": fields.get("can_apply", True)}
        for key in ("_untrusted", "extensions"):
            if key in fields:
                plan[key] = fields[key]
        self._checked_plan(plan, driver)
        missing = scope.missing(effects=plan["effects"], dangerous=bool(command.get("dangerous")), target=plan["target"],
                                outputs=plan["outputs"])
        if missing:
            raise forbidden(scope, missing)
        key = target_key(plan["target"])
        blocking = store.blocking([key])
        if blocking:
            return self._blocked(*blocking)
        store.add_plan({"operation_id": operation_id, "plan_id": plan_id, "capability_id": driver["capability_id"],
                        "command": command["path"], "target_key": key, "request_digest": self._digest(command, params),
                        "scope_id": scope.scope_id, "effects": plan["effects"], "plan": plan, "plan_expires_at": expires.timestamp()})
        token = tokens.issue(store.secret(), {"op": operation_id, "plan": plan_id, "cap": driver["capability_id"],
                                              "digest": self._digest(command, params), "scope": scope.scope_id,
                                              "exp": expires.timestamp()})
        return self._ok({"preview": plan, "confirm_token": token, "expires_at": plan["expires_at"]})

    def _dry_run_batch(self, command, params, scope) -> dict:
        store = self._store_open()
        batch_id, plan_id = "op-" + uuid.uuid4().hex[:24], "plan-" + uuid.uuid4().hex[:24]
        driver = command["driver"]
        call = Call(command=command["path"], capability_id=driver["capability_id"], params=params, effects=tuple(driver["effects"]),
                    dangerous=bool(command.get("dangerous")), dry_run=True, operation_id=batch_id, plan_id=plan_id,
                    authorization=scope.authorization, extras={"scope": scope})
        fields = self.adapter.plan(call)
        items = fields.get("items") if isinstance(fields, dict) else None
        if not isinstance(items, list) or not items or not all(isinstance(item, dict) for item in items):
            raise DriverError("E_UNKNOWN", "The adapter returned no batch items; nothing was dispatched.",
                              details={"next_action": NEXT_ACTION["E_UNKNOWN"]})
        self._check_budget(store, scope, len(items))
        expires = self.now() + PLAN_TTL
        body = self._plan_body(fields, driver, scope, expires)
        preview_items, records = [], []
        for index, item in enumerate(items):
            operation = "op-" + uuid.uuid4().hex[:24]
            entry = {"key": item.get("key"), "operation_id": operation, "target": item.get("target"),
                     "base_snapshot": item.get("base_snapshot"), "changes": item.get("changes", []), "outputs": item.get("outputs", []),
                     "can_apply": item.get("can_apply", True)}
            if item.get("unknowns"):
                entry["unknowns"] = item["unknowns"]
            preview_items.append(entry)
            item_plan = {"plan_id": "plan-" + uuid.uuid4().hex[:24], "operation_id": operation, "capability_id": driver["capability_id"],
                         "target": entry["target"], "base_snapshot": entry["base_snapshot"],
                         **dict(body, changes=entry["changes"], outputs=entry["outputs"],
                                unknowns=[*body["unknowns"], *item.get("unknowns", [])], trial_snapshot=item.get("trial_snapshot")),
                         "can_apply": entry["can_apply"]}
            records.append({"operation_id": operation, "plan_id": item_plan["plan_id"], "capability_id": driver["capability_id"],
                            "command": command["path"], "kind": "item", "batch_id": batch_id, "item_index": index,
                            "item_key": entry["key"], "target_key": target_key(entry["target"]), "scope_id": scope.scope_id,
                            "effects": body["effects"], "plan": item_plan, "plan_expires_at": expires.timestamp()})
        preview = {"plan_id": plan_id, "operation_id": batch_id, "capability_id": driver["capability_id"], "total": len(items),
                   "targets": [entry["key"] for entry in preview_items], "items": preview_items, **body,
                   "can_apply": bool(fields.get("can_apply", True)) and all(entry["can_apply"] for entry in preview_items)}
        self._checked_plan(preview, driver)
        for record in records:
            self._checked_plan(record["plan"], driver)
        missing: dict = {}
        for record in records:
            for name, value in scope.missing(effects=body["effects"], dangerous=bool(command.get("dangerous")),
                                             target=record["plan"]["target"], outputs=record["plan"]["outputs"]).items():
                if isinstance(value, list):
                    missing.setdefault(name, [])
                    missing[name].extend(item for item in value if item not in missing[name])
                else:
                    missing[name] = value
        if missing:
            raise forbidden(scope, missing)
        blocking = store.blocking([record["target_key"] for record in records])
        if blocking:
            return self._blocked(*blocking)
        digest = self._digest(command, params)
        store.add_plan({"operation_id": batch_id, "plan_id": plan_id, "capability_id": driver["capability_id"], "command": command["path"],
                        "kind": "batch", "target_key": None, "request_digest": digest, "scope_id": scope.scope_id,
                        "effects": body["effects"], "plan": preview, "plan_expires_at": expires.timestamp()}, records)
        token = tokens.issue(store.secret(), {"op": batch_id, "plan": plan_id, "cap": driver["capability_id"], "digest": digest,
                                              "scope": scope.scope_id, "exp": expires.timestamp()})
        return self._ok({"preview": preview, "confirm_token": token, "expires_at": preview["expires_at"]})

    def _confirmable(self, command, params, flags, scope, kind: str):
        """Shared confirm checks; returns (store, claims, record, token) or an E_CONFLICT envelope."""
        store = self._store_open()
        token = flags["confirm"]
        try:
            claims = tokens.verify(store.secret(), token, self.now().timestamp())
        except tokens.TokenError as exc:
            return self._conflict(exc.reason, "The confirm token is not valid for this call.")
        driver = command["driver"]
        record = store.get(claims["op"])
        if claims.get("cap") != driver["capability_id"]:
            return self._conflict("token_mismatch", "The confirm token belongs to another command.")
        if record is None:
            return self._conflict("token_expired", "The plan behind this token is no longer recorded.")
        if (record.get("kind") or "single") != kind:
            return self._conflict("token_mismatch", "The confirm token belongs to another kind of plan.")
        if record["status"] != "planned":
            return self._conflict("token_used", "The plan behind this token was already confirmed.", operation_id=record["operation_id"])
        digest = self._digest(command, params)
        if digest != claims.get("digest") or digest != record["request_digest"]:
            return self._conflict("token_mismatch", "The arguments differ from the dry-run that issued this token.")
        plan = record["plan"]
        if not plan["can_apply"]:
            return self._conflict("state_drift", "The plan said it cannot be applied; see its constraints and unknowns.",
                                  plan_id=plan["plan_id"])
        plans = [item["plan"] for item in store.items_of(record["operation_id"])] if kind == "batch" else [plan]
        for checked in plans:
            missing = scope.missing(effects=checked["effects"], dangerous=bool(command.get("dangerous")), target=checked["target"],
                                    outputs=checked["outputs"])
            if missing:
                raise forbidden(scope, missing)
        if scope.scope_id != claims.get("scope"):
            return self._conflict("scope_changed", "The task scope changed since the plan was made.")
        revision = getattr(self.adapter, "revision", None)
        if callable(revision):
            for checked in plans:
                current = revision(checked["target"])
                if current is not None and current != checked["base_snapshot"]["revision"]:
                    return self._conflict("state_drift", "The target changed since the plan was made.", plan_id=plan["plan_id"],
                                          expected={"revision": checked["base_snapshot"]["revision"]})
        return store, claims, record, token

    def _dispatch(self, store, record, claims, token, *, key, target_keys, exclude, units, scope):
        """Commit the dispatch; returns None, or an envelope when the store refused it before anything was sent."""
        self._in_flight = record["operation_id"]
        try:
            store.dispatch(record["operation_id"], fingerprint=tokens.fingerprint(token), token_expires=claims["exp"], key=key,
                           digest=record["request_digest"], target_keys=target_keys, exclude=exclude,
                           budget=scope.max_operations, units=units, lease=self.lease_seconds)
            return None
        except TokenUsed:
            self._in_flight = None
            return self._conflict("token_used", "This confirm token was already used.", operation_id=record["operation_id"])
        except Blocked as blocked:
            self._in_flight = None
            return self._blocked(blocked.record, blocked.live)
        except KeyMismatch as mismatch:
            self._in_flight = None
            return self._conflict("idempotency_mismatch", "This idempotency key was used with different arguments.",
                                  operation_id=mismatch.record["operation_id"])
        except KeyUsed as used:
            self._in_flight = None
            return self._replay(used.record, key)
        except BudgetSpent as spent:
            self._in_flight = None
            raise forbidden(scope, {"limit": f"limits.max_operations is {spent.limit} and {spent.used} operations already ran under this scope"}) from None

    def _replay(self, previous: dict, key: str) -> dict:
        """The key already ran with the same arguments: return its recorded receipt, never dispatch again."""
        if previous.get("receipt") is not None:
            replay = dict(previous["receipt"], replayed=True, request_id=key)
            return self._receipt_envelope(replay, "The earlier operation with this key already ran; its receipt is returned.")
        operation = previous["operation_id"]
        if previous.get("resolution") is None:
            return self._blocked(previous, effective_status(previous, self.now().timestamp()) in LIVE)
        return self._conflict("token_used", f"The earlier operation with this key already ran (recorded by read-back as "
                                            f"{previous['resolution']['outcome']}); it is not run again.",
                              active_operation=operation, **self._lookup(operation))

    def _confirm(self, command, params, flags, scope):
        checked = self._confirmable(command, params, flags, scope, "single")
        if isinstance(checked, dict):
            return checked, None
        store, claims, record, token = checked
        key = _idempotency_key(flags)
        refused = self._dispatch(store, record, claims, token, key=key, target_keys=[record["target_key"]], exclude=(),
                                 units=1, scope=scope)
        if refused is not None:
            return refused, None
        operation_id = record["operation_id"]
        plan = record["plan"]
        driver = command["driver"]
        call = Call(command=command["path"], capability_id=driver["capability_id"], params=params, effects=tuple(plan["effects"]),
                    dangerous=bool(command.get("dangerous")), operation_id=operation_id, plan_id=plan["plan_id"],
                    authorization=scope.authorization, idempotency_key=key, extras={"scope": scope})
        try:
            envelope = self._dispatched(command, call, record, plan, key, tokens.fingerprint(token))
        except BaseException as exc:  # noqa: BLE001 - after dispatch every failure leaves the outcome open
            self._settle_unknown(operation_id)
            envelope = self._unknown(operation_id, f"The call stopped after dispatch ({type(exc).__name__}); the outcome is unknown.")
        self._in_flight = None
        return envelope, operation_id

    def _dispatched(self, command, call, record, plan, key, fingerprint) -> dict:
        store = self._store
        operation_id = record["operation_id"]
        asynchronous = ("operation_status" in command["driver"]["profiles"] and callable(getattr(self.adapter, "submit", None))
                        and callable(getattr(self.adapter, "status", None)))
        try:
            with self._heartbeat(operation_id):
                if asynchronous:
                    update = self.adapter.submit(call, plan)
                else:
                    fields = self.adapter.apply(call, plan)
        except DriverError as error:
            return self._after_dispatch_error(error, record, plan, key, fingerprint)
        if asynchronous:
            return self._submitted(record, update)
        receipt = self._complete_receipt(fields, record, plan, key)
        if receipt is None:
            store.finish(operation_id, "unknown")
            return self._unknown(operation_id, "The adapter's receipt does not satisfy the contract, so the outcome cannot be established.")
        if not store.finish(operation_id, _status_for(self.validator.receipt_rule(receipt)), receipt):
            self._warnings.append(f"warning: operation {operation_id} was resolved while it ran; this result is kept as late_result")
        return self._receipt_envelope(receipt, "The operation did not complete as requested.")

    def _submitted(self, record: dict, update) -> dict:
        store = self._store
        operation_id = record["operation_id"]
        status = update.get("status") if isinstance(update, dict) else None
        if status not in {"pending", "running"}:
            store.finish(operation_id, "unknown")
            return self._unknown(operation_id, "The adapter accepted the submission but reported neither pending nor running.")
        extra = {key: update[key] for key in STATUS_EXTRAS if key in update}
        candidate = dict(record, status=status, extra=extra, lease_until=self.now().timestamp() + self.status_lease_seconds,
                         started_at=record.get("started_at") or self.now().timestamp())
        if not self._valid("operation-status", self._status_of(candidate)):
            extra = {}
        store.update_status(operation_id, status, extra, self.status_lease_seconds)
        return self._ok({"operation_status": self._status_of(store.get(operation_id))})

    def _complete_receipt(self, fields, record, plan, key) -> dict | None:
        if not isinstance(fields, dict):
            return None
        try:
            receipt = clone_json(fields)
        except ContractError:
            return None
        receipt.setdefault("operation_id", record["operation_id"])
        receipt.setdefault("capability_id", record["capability_id"])
        receipt.setdefault("plan_id", plan.get("plan_id"))
        receipt.setdefault("target", plan.get("target"))
        receipt.setdefault("authorization_scope_id", plan.get("authorization_scope_id"))
        receipt.setdefault("effects", plan.get("effects"))
        receipt.setdefault("snapshot_before", plan.get("base_snapshot"))
        for name in ("changed_items", "artifacts", "deliveries"):
            receipt.setdefault(name, [])
        receipt.setdefault("failed_stage", None)
        if key:
            receipt["request_id"] = key
        outcome = receipt.get("outcome")
        verification = outcome.get("verification") if isinstance(outcome, dict) else None
        if isinstance(verification, dict) and isinstance(verification.get("extensions"), dict):
            extensions = verification["extensions"]
            candidate = extensions.get("verification_result")
            marker = candidate.get("profile") if isinstance(candidate, dict) else None
            if isinstance(marker, str) and marker.startswith(PROFILE_PREFIX):
                try:
                    profile = self.validator.validate("verification-result", candidate)
                    receipt = guard_receipt(receipt, profile)
                except (ContractError, KeyError, TypeError, ValueError):
                    return None
        if not self._valid("receipt", receipt):
            return None
        expected = {"operation_id": record["operation_id"], "capability_id": record["capability_id"],
                    "plan_id": plan.get("plan_id"), "authorization_scope_id": plan.get("authorization_scope_id")}
        if any(receipt.get(name) != value for name, value in expected.items()):
            return None
        identity_keys = ("mode", "engine_id", "instance_id", "object_id", "locator")
        if not isinstance(receipt.get("target"), dict) or any(
                receipt["target"].get(name) != (plan.get("target") or {}).get(name) for name in identity_keys):
            return None
        if not isinstance(receipt.get("effects"), list) or set(receipt["effects"]) != set(plan.get("effects", [])):
            return None
        return receipt

    def _receipt_envelope(self, receipt: dict, message: str, error: DriverError | None = None) -> dict:
        rule = self.validator.receipt_rule(receipt)
        if rule["ok"] and error is None:
            return self._ok({"receipt": receipt})
        if rule["ok"]:
            return self._unknown(receipt["operation_id"], "The adapter reported an error with a fully successful receipt; the outcome is uncertain.",
                                 extra={"reported_code": error.code})
        code = error.code if error is not None and error.code in rule["codes"] else rule["codes"][0]
        details = _own(error.details) if error is not None else {}
        if code == "E_OUTCOME_UNKNOWN":
            return self._unknown(receipt["operation_id"], error.message if error else "The operation's outcome is unknown.",
                                 receipt=receipt, extra=details)
        details.update(dispatch_state="dispatched", operation_id=receipt["operation_id"], receipt=receipt,
                       next_action=(error.details.get("next_action") if error is not None else None)
                       or NEXT_ACTION.get(code, "Read the receipt before doing anything else."))
        return self._fail(code, error.message if error else message, details)

    def _after_dispatch_error(self, error: DriverError, record: dict, plan: dict, key, fingerprint) -> dict:
        store = self._store
        operation_id = record["operation_id"]
        if error.receipt is not None:
            receipt = self._complete_receipt(error.receipt, record, plan, key)
            if receipt is None:
                store.finish(operation_id, "unknown")
                return self._unknown(operation_id, "The adapter's receipt does not satisfy the contract, so the outcome cannot be established.")
            rule = self.validator.receipt_rule(receipt)
            if rule["ok"]:
                store.finish(operation_id, "unknown", None, {"rejected_receipt": receipt, "reported_code": error.code})
            else:
                store.finish(operation_id, _status_for(rule), receipt)
            return self._receipt_envelope(receipt, error.message, error)
        if error.surely_not_started:
            # Nothing reached the native side: the plan waits again and the same confirm token stays valid.
            store.undispatch(operation_id, fingerprint, {"last_refusal": {"code": error.code, "at": _iso(self.now())}})
            details = _own(error.details)
            details.update(dispatch_state="not_started", operation_id=operation_id,
                           next_action=error.details.get("next_action") or NEXT_ACTION.get(error.code, "Nothing was sent; fix the cause.")
                           + " The confirm token stays valid until it expires.")
            return self._fail(error.code, error.message, details)
        store.finish(operation_id, "unknown")
        if error.code == "E_HUMAN_REQUIRED":
            details = _own(error.details)
            details.update(dispatch_state=error.dispatch_state if error.explicit else "unknown", operation_id=operation_id,
                           next_action=error.details.get("next_action") or NEXT_ACTION["E_HUMAN_REQUIRED"])
            return self._fail("E_HUMAN_REQUIRED", error.message, details)
        return self._unknown(operation_id, error.message, extra=dict(_own(error.details), reported_code=error.code))

    # ================================================================ batch

    def _confirm_batch(self, command, params, flags, scope):
        checked = self._confirmable(command, params, flags, scope, "batch")
        if isinstance(checked, dict):
            return checked, None
        store, claims, record, token = checked
        batch_id = record["operation_id"]
        items = store.items_of(batch_id)
        refused = self._dispatch(store, record, claims, token, key=None, target_keys=[item["target_key"] for item in items],
                                 exclude=[item["operation_id"] for item in items], units=len(items), scope=scope)
        if refused is not None:
            return refused, None
        continue_on_error = bool(params.get("continue-on-error", True))
        results = []
        stopped_by = None
        try:
            with self._heartbeat(batch_id):
                for item in items:
                    if stopped_by is not None or self._abort:
                        reason = stopped_by or "The batch was interrupted before this item."
                        store.release(item["operation_id"], {"stage": "skipped"})
                        results.append({"target": item["item_key"], "ok": False, "skipped_reason": reason})
                        continue
                    result = self._run_item(command, params, scope, item)
                    results.append(result)
                    if not result["ok"] and not continue_on_error:
                        stopped_by = f"--continue-on-error false stopped the batch after {item['item_key']} failed."
        finally:
            stamp = self.now().timestamp()
            statuses = [effective_status(item, stamp) for item in store.items_of(batch_id)]
            store.finish(batch_id, batch_status(statuses))
        self._in_flight = None
        attempted = [result for result in results if "skipped_reason" not in result]
        summary = {"total": len(attempted), "succeeded": sum(result["ok"] for result in attempted),
                   "failed": sum(not result["ok"] for result in attempted)}
        skipped = len(results) - len(attempted)
        unknown = sum(result.get("error", {}).get("code") == "E_OUTCOME_UNKNOWN" for result in attempted)
        if skipped:
            summary["skipped"] = skipped
        if unknown:
            summary["unknown"] = unknown
        data = {"operation_id": batch_id, "items": results, "summary": summary, "continue_on_error": continue_on_error}
        return self._ok(data), batch_id

    def _item_error(self, key, operation_id, code, receipt=None) -> dict:
        result = {"target": key, "ok": False, "operation_id": operation_id,
                  "error": {"code": code, "retryable": self.codes.get(code, {"retryable": False})["retryable"]}}
        if receipt is not None:
            result["receipt"] = receipt
        return result

    def _run_item(self, command, params, scope, item: dict) -> dict:
        store = self._store
        plan = item["plan"]
        operation_id, key = item["operation_id"], item["item_key"]
        revision = getattr(self.adapter, "revision", None)
        if callable(revision):
            try:
                current = revision(plan["target"])
            except DriverError as error:
                store.release(operation_id, {"stage": "revision_unavailable", "reported_code": error.code})
                return self._item_error(key, operation_id, error.code)
            except Exception as exc:  # noqa: BLE001 - nothing was sent for this item; stop the batch
                self._abort = True
                store.release(operation_id, {"stage": "revision_unavailable", "reported_code": type(exc).__name__})
                return self._item_error(key, operation_id, "E_UNKNOWN")
            if current is not None and current != plan["base_snapshot"]["revision"]:
                store.release(operation_id, {"stage": "state_drift"})
                return self._item_error(key, operation_id, "E_CONFLICT")
        try:
            store.start_item(operation_id, item["target_key"], self.lease_seconds)
        except (Blocked, TokenUsed):
            store.release(operation_id, {"stage": "target_busy"})
            return self._item_error(key, operation_id, "E_CONFLICT")
        call = Call(command=command["path"], capability_id=command["driver"]["capability_id"], params=params,
                    effects=tuple(plan["effects"]), dangerous=bool(command.get("dangerous")), operation_id=operation_id,
                    plan_id=plan["plan_id"], authorization=scope.authorization, item_key=key, extras={"scope": scope})
        try:
            with self._heartbeat(operation_id):
                fields = self.adapter.apply(call, plan)
        except DriverError as error:
            if error.receipt is not None:
                receipt = self._complete_receipt(error.receipt, item, plan, None)
                if receipt is None:
                    store.finish(operation_id, "unknown")
                    return self._item_error(key, operation_id, "E_OUTCOME_UNKNOWN")
                rule = self.validator.receipt_rule(receipt)
                if rule["ok"]:
                    store.finish(operation_id, "unknown", None, {"rejected_receipt": receipt, "reported_code": error.code})
                    return self._item_error(key, operation_id, "E_OUTCOME_UNKNOWN")
                store.finish(operation_id, _status_for(rule), receipt)
                code = error.code if error.code in rule["codes"] else rule["codes"][0]
                return self._item_error(key, operation_id, code, receipt)
            if error.surely_not_started:
                store.release(operation_id, {"stage": "refused", "reported_code": error.code})
                return self._item_error(key, operation_id, error.code)
            store.finish(operation_id, "unknown")
            return self._item_error(key, operation_id, "E_OUTCOME_UNKNOWN")
        except BaseException:  # noqa: BLE001 - stop the batch; this item's outcome is unknown
            self._abort = True
            self._settle_unknown(operation_id)
            return self._item_error(key, operation_id, "E_OUTCOME_UNKNOWN")
        receipt = self._complete_receipt(fields, item, plan, None)
        if receipt is None:
            store.finish(operation_id, "unknown")
            return self._item_error(key, operation_id, "E_OUTCOME_UNKNOWN")
        rule = self.validator.receipt_rule(receipt)
        store.finish(operation_id, _status_for(rule), receipt)
        if rule["ok"]:
            return {"target": key, "ok": True, "operation_id": operation_id, "receipt": receipt}
        return self._item_error(key, operation_id, rule["codes"][0], receipt)


def _given(flags: dict, key: str) -> bool:
    """The flag was on the command line, even with an empty value."""
    return flags.get(key) is not None and flags.get(key) is not False


def _idempotency_key(flags: dict) -> str | None:
    return flags["request_id"] if _given(flags, "request_id") else flags.get("idempotency_key")


def _negative_number(token: str) -> bool:
    return bool(re.fullmatch(r"-(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", token))


def _split_list(raw: str) -> list[str]:
    return [item for item in (part.strip() for part in raw.split(",")) if item]


def _reject_constant(value):
    raise ValueError("non-JSON numeric constant")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("non-finite number")
    return value


def _pick(value: dict, names: list[str]) -> dict:
    result: dict = {}
    for name in names:
        head, _, tail = name.partition(".")
        if head not in value:
            continue
        if tail and isinstance(value[head], dict):
            result.setdefault(head, {}).update(_pick(value[head], [tail]))
        else:
            result[head] = value[head]
    return result


def _text_lines(value, indent: int = 0) -> list[str]:
    pad = "  " * indent
    lines = []
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item:
                lines.append(f"{pad}{key}:")
                lines.extend(_text_lines(item, indent + 1))
            else:
                lines.append(f"{pad}{key}: {json.dumps(item, ensure_ascii=False)}")
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}-")
                lines.extend(_text_lines(item, indent + 1))
            else:
                lines.append(f"{pad}- {json.dumps(item, ensure_ascii=False)}")
    return lines


def load_contract(path: Path | str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def serve(contract: dict, adapter, *, vendor_root: Path | str, argv=None, **options) -> int:
    """Run one invocation and write it to stdout/stderr; returns the exit code."""
    try:
        validator = ContractValidator(vendor_root)
    except Exception as exc:  # noqa: BLE001 - bootstrap failure must still produce one envelope
        envelope = {"ok": False, "schema_version": "1.0", "meta": {"duration_ms": 0},
                    "error": {"code": "E_CONFIG", "message": "The pinned specification copy could not be loaded; nothing was dispatched.",
                              "retryable": False, "details": {"dispatch_state": "not_started", "cause": type(exc).__name__,
                                                              "next_action": "Run check_project.py and restore the pinned copy."}}}
        sys.stdout.buffer.write((json.dumps(envelope, ensure_ascii=False) + "\n").encode("utf-8"))
        return 4
    with DriverCLI(contract, adapter, validator=validator, **options) as cli:
        return cli.run(sys.argv[1:] if argv is None else argv).write()
