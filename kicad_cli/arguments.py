"""Typed command-boundary validation, driven by the same registry as reference.

No KiCad processes or design files are touched here. Invalid requests must fail
before a handler can turn them into a different, valid-looking write preview.
"""

from __future__ import annotations

import math
from typing import Any

from . import envelope

_BOOLEAN = {"true": True, "false": False, "1": True, "0": False}


def _usage(message: str, **details: Any) -> None:
    envelope.fail("E_USAGE", message, details)


def _scan(argv: list[str], types: dict[str, str]) -> tuple[list[str], dict[str, list[Any]]]:
    """Locate the command without guessing that every flag consumes a value.

    The union of registry types is used only for token boundaries. Acceptance,
    conversion and mode constraints are checked against the selected command.
    """
    positional: list[str] = []
    raw: dict[str, list[Any]] = {}
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--":
            positional.extend(argv[i + 1 :])
            break
        if not token.startswith("--"):
            positional.append(token)
            i += 1
            continue
        key, sep, value = token[2:].partition("=")
        key = key.replace("_", "-")
        if not key:
            _usage("empty option name")
        if not sep:
            following = argv[i + 1] if i + 1 < len(argv) else None
            if types.get(key) == "boolean":
                value = True
                if following is not None and following.lower() in _BOOLEAN:
                    value = following
                    i += 1
            elif following is not None and not following.startswith("--"):
                value = following
                i += 1
            else:
                value = None
        raw.setdefault(key, []).append(value)
        i += 1
    return positional, raw


def _convert(value: Any, spec: dict[str, Any]) -> Any:
    name, kind = spec["name"], spec["type"]
    if kind == "boolean":
        if value is True:
            return True
        if isinstance(value, str) and value.lower() in _BOOLEAN:
            return _BOOLEAN[value.lower()]
        _usage("boolean option expects true, false, 1 or 0", param=name)
    if not isinstance(value, str) or not value.strip():
        _usage("option requires a non-empty value", param=name)
    if kind == "number":
        try:
            number = float(value)
        except ValueError:
            _usage("option requires a finite number", param=name)
        if not math.isfinite(number):
            _usage("option requires a finite number", param=name)
        if "minimum" in spec and number < spec["minimum"]:
            _usage("number is below its minimum", param=name, minimum=spec["minimum"])
        if "exclusive_minimum" in spec and number <= spec["exclusive_minimum"]:
            _usage(
                "number must exceed its minimum",
                param=name,
                exclusive_minimum=spec["exclusive_minimum"],
            )
        return number
    if "enum" in spec and value not in spec["enum"]:
        _usage("option is not one of the accepted values", param=name, accepted=spec["enum"])
    if spec.get("separator"):
        values = [part.strip() for part in value.split(spec["separator"])]
        if not all(values):
            _usage("list option contains an empty item", param=name)
        return spec["separator"].join(values)
    return value


def parse(
    argv: list[str], commands: list[dict[str, Any]], globals_: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a selected handler definition and fully validated options."""
    types = {p["name"]: p["type"] for c in commands for p in c["params"]}
    types.update({p["name"]: p["type"] for p in globals_})
    positional, raw = _scan(argv, types)
    command = next((c for c in commands if c["path"].split() == positional), None)
    if command is None:
        _usage(
            "unknown command or unexpected positional arguments",
            got=" ".join(positional) or "(none)",
            hint="run `kicad-cli reference` for the machine-readable command list",
        )
    specs = {p["name"]: p for p in [*globals_, *command["params"]]}
    unknown = sorted(set(raw) - specs.keys())
    if unknown:
        _usage(
            "unknown option for this command",
            command=command["path"],
            unknown=[f"--{name}" for name in unknown],
            accepted=sorted(p["name"] for p in command["params"]),
        )
    opts: dict[str, Any] = {}
    for name, spec in specs.items():
        if name not in raw:
            if spec.get("required"):
                _usage("required option is missing", command=command["path"], param=name)
            if spec.get("default") is not None:
                opts[name] = spec["default"]
            continue
        values = raw[name]
        if len(values) > 1 and not spec.get("multiple"):
            _usage("option may not be repeated", param=name)
        converted = [_convert(value, spec) for value in values]
        # Existing payloads accept CSV for repeated layer options. Keep their
        # calling convention, but never discard an earlier occurrence.
        opts[name] = (
            spec.get("separator", ",").join(converted) if spec.get("multiple") else converted[0]
        )

    for name in raw:
        spec = specs[name]
        for other, allowed in spec.get("when", {}).items():
            if opts.get(other) not in allowed:
                _usage(
                    "option is not supported in this mode", param=name, condition={other: allowed}
                )
        for other in spec.get("conflicts_with", []):
            if other in raw:
                _usage("options cannot be combined", params=[name, other])
        for other in spec.get("requires", []):
            if opts.get(name) and not opts.get(other):
                _usage("option requires another option", param=name, required=other)
    if "dry-run" in raw and "confirm" in raw:
        _usage("--dry-run and --confirm cannot be combined", params=["dry-run", "confirm"])
    if command["type"] != "write" and (opts.get("dry-run") or opts.get("confirm")):
        _usage("write controls cannot be used on a read command", command=command["path"])
    if opts.get("json") and opts.get("format", "json") != "json":
        _usage("--json conflicts with a non-JSON --format", params=["json", "format"])
    for group in command.get("required_any", []):
        if not any(opts.get(name) for name in group):
            _usage("at least one target option is required", params=group)
    # Both spellings remain available to existing handlers. Normalize before
    # duplicate checking above, so aliases cannot bypass scalar uniqueness.
    for name, value in list(opts.items()):
        opts[name.replace("-", "_")] = value
    return command, opts
