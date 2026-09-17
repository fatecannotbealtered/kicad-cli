"""The unified output envelope (CLI-SPEC section 3) and the write gate (section 7).

Every command exits through ``ok()``, ``fail()`` or ``need_confirm()``. Nothing
else writes to stdout, because the contract enforcement is ``exact``: an agent
parsing our output must never meet a key we did not promise.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from typing import Any

from . import errors

SCHEMA_VERSION = "1.0"

_T0 = time.monotonic()
_OPTS: dict[str, Any] = {
    "format": "json",
    "compact": False,
    "fields": None,
    "quiet": False,
    "schema_name": None,
    "schema_fields": None,
}


def configure(
    *,
    fmt: str = "json",
    compact: bool = False,
    fields: list[str] | None = None,
    quiet: bool = False,
    schema_name: str | None = None,
    schema_fields: list[str] | None = None,
) -> None:
    _OPTS.update(
        format=fmt,
        compact=compact,
        fields=fields,
        quiet=quiet,
        schema_name=schema_name,
        schema_fields=schema_fields,
    )


def _duration_ms() -> int:
    return int((time.monotonic() - _T0) * 1000)


def _project(data: Any) -> Any:
    """Apply ``--fields``. Only top-level keys of an object, or of each row of a list."""
    fields = _OPTS.get("fields")
    if not fields:
        return data
    keep = set(fields)
    if isinstance(data, list):
        return [
            {k: v for k, v in row.items() if k in keep} if isinstance(row, dict) else row
            for row in data
        ]
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if k in keep}
    return data


def _emit(doc: dict[str, Any], code: int) -> None:
    if _OPTS.get("format") == "text":
        # Human-facing only. Never parse this; agents use the default json format.
        payload = doc.get("data") if doc.get("ok") else doc.get("error")
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")
    else:
        sep = (",", ":") if _OPTS.get("compact") else None
        sys.stdout.write(json.dumps(doc, ensure_ascii=False, default=str, separators=sep) + "\n")
    sys.stdout.flush()
    sys.exit(code)


def _check_schema(data: Any) -> None:
    """In strict mode, refuse to emit data that does not match what we promised.

    The contract is enforced exactly: an agent must never meet a key we did not
    advertise, nor miss one we did. Nothing checked that until now -- the
    registry held the declaration and the command held the truth, and only a
    careful reader would notice them drifting apart.

    This runs only when KICAD_CLI_STRICT is set, which the test suite does. A
    schema that has fallen behind its command is a defect to fix at development
    time, not a reason to fail a command that is otherwise working for someone.
    """
    expected = _OPTS.get("schema_fields")
    if not expected or not isinstance(data, dict) or not os.environ.get("KICAD_CLI_STRICT"):
        return
    got, want = set(data), set(expected)
    if got != want:
        name = _OPTS.get("schema_name")
        sys.stderr.write(
            f"contract violation in {name}: "
            f"undeclared {sorted(got - want)}, missing {sorted(want - got)}\n"
        )
        sys.stderr.flush()
        sys.exit(1)


def ok(data: Any, notices: list[dict[str, Any]] | None = None) -> None:
    _check_schema(data)
    meta: dict[str, Any] = {"duration_ms": _duration_ms()}
    if notices:
        meta["notices"] = notices
    _emit({"ok": True, "schema_version": SCHEMA_VERSION, "data": _project(data), "meta": meta}, 0)


def fail(code: str, message: str, details: dict[str, Any] | None = None) -> None:
    _emit(
        {
            "ok": False,
            "schema_version": SCHEMA_VERSION,
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "retryable": errors.retryable(code),
            },
            "meta": {"duration_ms": _duration_ms()},
        },
        errors.exit_code(code),
    )


def confirm_token(operation: str, preview: dict[str, Any]) -> str:
    """Token binds to operation + the previewed state.

    Binding it to the preview is the point: if the board changed between the
    dry run and the confirm, the token no longer matches and we refuse with
    E_CONFLICT instead of applying a plan that was computed against stale state.
    """
    blob = operation + json.dumps(preview, sort_keys=True, default=str)
    return "ct_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def need_confirm(operation: str, preview: dict[str, Any]) -> None:
    """Ask for confirmation, and show what is being confirmed.

    The preview travels with the token deliberately. A gate that hands back an
    opaque token and nothing else asks the caller to authorise something they
    cannot see, which is a worse failure than having no gate at all: it turns a
    deliberate decision into a reflex.
    """
    token = confirm_token(operation, preview)
    _emit(
        {
            "ok": False,
            "schema_version": SCHEMA_VERSION,
            "error": {
                "code": "E_CONFIRMATION_REQUIRED",
                "message": (
                    "write operation needs confirmation; "
                    "re-run the same command with --confirm <token>"
                ),
                "details": {
                    "confirm_token": token,
                    "operation": operation,
                    "preview": preview,
                },
                "retryable": False,
            },
            "meta": {"duration_ms": _duration_ms()},
        },
        errors.exit_code("E_CONFIRMATION_REQUIRED"),
    )


def check_confirm(given: str | None, operation: str, preview: dict[str, Any]) -> None:
    """Single entry point for the write gate. Returns only when execution may proceed."""
    token = confirm_token(operation, preview)
    if not given:
        need_confirm(operation, preview)
    if given != token:
        fail(
            "E_CONFLICT",
            "confirm token does not match current state; "
            "re-run with --dry-run to get a fresh token",
            {"expected": token, "got": given},
        )


def progress(message: str) -> None:
    """Progress goes to stderr so stdout stays a single JSON document."""
    if not _OPTS.get("quiet"):
        sys.stderr.write(message + "\n")
        sys.stderr.flush()
