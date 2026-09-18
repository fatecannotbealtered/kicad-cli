"""The unified output envelope (CLI-SPEC section 3) and the write gate (section 7).

Every command exits through ``ok()``, ``fail()`` or ``need_confirm()``. Nothing
else writes to stdout, because the contract enforcement is ``exact``: an agent
parsing our output must never meet a key we did not promise.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

from . import errors
from .payload import confirm_store

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


def declared_fields() -> list[str] | None:
    """The field list this command's output_schema promises, if it has one."""
    return _OPTS.get("schema_fields")


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


def write_utf8(stream: Any, text: str) -> None:
    """Emit UTF-8 regardless of the console the caller happens to have.

    JSON exchanged between systems is UTF-8 (RFC 8259 section 8.1), and an
    agent decodes ours as UTF-8 because there is nothing else it could
    reasonably assume. ``sys.stdout`` does not honour that on its own: it
    encodes with the process locale, so on a zh-CN Windows console every
    Chinese string in a payload note or error message went out as GBK and the
    document would not decode at all. The suite never saw it because every
    test in it sets PYTHONIOENCODING=utf-8 -- the one environment variable
    that hides this exact defect.
    """
    buffer = getattr(stream, "buffer", None)
    if buffer is None:  # A substituted text stream, e.g. a capture fixture.
        stream.write(text)
        stream.flush()
        return
    stream.flush()  # Keep ordering if anything text-level is still pending.
    buffer.write(text.encode("utf-8"))
    buffer.flush()


def _emit(doc: dict[str, Any], code: int) -> None:
    if _OPTS.get("format") == "text":
        # Human-facing only. Never parse this; agents use the default json format.
        payload = doc.get("data") if doc.get("ok") else doc.get("error")
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    else:
        sep = (",", ":") if _OPTS.get("compact") else None
        text = json.dumps(doc, ensure_ascii=False, default=str, separators=sep)
    write_utf8(sys.stdout, text + "\n")
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
        write_utf8(
            sys.stderr,
            f"contract violation in {name}: "
            f"undeclared {sorted(got - want)}, missing {sorted(want - got)}\n",
        )
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


def need_confirm(operation: str, preview: dict[str, Any], target: str | None = None) -> None:
    """Ask for confirmation, and show what is being confirmed.

    The preview travels with the token deliberately. A gate that hands back an
    opaque token and nothing else asks the caller to authorise something they
    cannot see, which is a worse failure than having no gate at all: it turns a
    deliberate decision into a reflex.
    """
    try:
        token = confirm_store.issue(operation, preview, target)
    except confirm_store.ConfirmError as exc:
        fail(exc.code, str(exc), exc.details)
    _emit(
        {
            "ok": False,
            "schema_version": SCHEMA_VERSION,
            "error": {
                "code": "E_CONFIRMATION_REQUIRED",
                "message": (
                    "write operation needs confirmation; re-run the same command "
                    f"with --confirm <token> within {confirm_store.TTL_SECONDS}s"
                ),
                "details": {
                    "confirm_token": token,
                    "operation": operation,
                    "preview": preview,
                    "expires_in_s": confirm_store.TTL_SECONDS,
                    "single_use": True,
                },
                "retryable": False,
            },
            "meta": {"duration_ms": _duration_ms()},
        },
        errors.exit_code("E_CONFIRMATION_REQUIRED"),
    )


def check_confirm(
    given: str | None, operation: str, preview: dict[str, Any], target: str | None = None
) -> None:
    """Single entry point for the write gate. Returns only when execution may proceed."""
    if not given:
        need_confirm(operation, preview, target)
    try:
        confirm_store.redeem(given, operation, preview, target)
    except confirm_store.ConfirmError as exc:
        fail(exc.code, str(exc), exc.details)


def progress(message: str) -> None:
    """Progress goes to stderr so stdout stays a single JSON document."""
    if not _OPTS.get("quiet"):
        write_utf8(sys.stderr, message + "\n")
