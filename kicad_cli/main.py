"""Entry point: global flags and dispatch.

Nothing here forwards to the official ``kicad-cli``. We share its name on
purpose and reach KiCad through its own libraries, so there is no binary behind
this one to fall through to -- the commands that exist are the commands that
work.

Argument parsing is hand-rolled rather than argparse because the contract is
the product here: an agent reads ``reference``, not ``--help``, and argparse
would insist on owning ``--help`` output, exit codes and error text that the
spec defines differently.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from . import __version__, envelope, registry

_GLOBAL_FLAGS = {"--compact", "--quiet", "--dry-run", "--json"}
_GLOBAL_OPTS = {"--format", "--fields", "--confirm"}

_USAGE = f"""kicad-cli {__version__} - AI-native KiCad PCB CLI

  kicad-cli reference              describe every command in machine-readable form
  kicad-cli context                report the resolved KiCad install and config
  kicad-cli doctor                 environment and release-readiness checks
  kicad-cli changelog [--since V]  what changed between versions
  kicad-cli board audit  --board <file>
  kicad-cli board parity --board <file>

Agents: call `kicad-cli reference`. This text is for humans and may change.
"""


def _parse(argv: list[str]) -> tuple[list[str], dict[str, Any]]:
    """Split argv into positional path segments and a flat option map.

    Names are kept exactly as typed. An earlier version normalised hyphens to
    underscores, which silently broke every hyphenated option the registry
    declares -- ``--ignore-lock`` landed under ``ignore_lock`` while the guard
    read ``ignore-lock``, so the lock override never once took effect. Both
    spellings are stored now, because commands are written against the name in
    the registry and there is no reason to make that a trap.
    """
    positional: list[str] = []
    opts: dict[str, Any] = {}

    def put(key: str, value: Any) -> None:
        opts[key] = value
        opts[key.replace("-", "_")] = value

    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in _GLOBAL_FLAGS:
            put(tok.lstrip("-"), True)
            i += 1
        elif tok.startswith("--"):
            key = tok[2:]
            if "=" in key:
                key, value = key.split("=", 1)
                put(key, value)
                i += 1
            elif i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                put(key, argv[i + 1])
                i += 2
            else:
                put(key, True)
                i += 1
        else:
            positional.append(tok)
            i += 1
    return positional, opts


def _reject_unknown(command: dict[str, Any], argv: list[str]) -> None:
    """Refuse options this command does not declare.

    Unknown options used to be accepted and ignored. On a read that wastes a
    call; on a write it is worse than that. ``board rewidth --nets VSYS`` looks
    like it names a target, and ``rewidth`` has no ``--nets`` -- so the option
    was dropped, a confirm token was issued, and the preview quietly described
    re-routing the default netclasses instead. The write gate cannot catch that:
    the plan it shows is a valid plan, just not the one that was asked for.
    """
    declared = {p["name"] for p in command["params"]}
    declared |= {p["name"].replace("-", "_") for p in command["params"]}
    allowed = (
        declared | {f.lstrip("-") for f in _GLOBAL_FLAGS} | {o.lstrip("-") for o in _GLOBAL_OPTS}
    )
    unknown = []
    for tok in argv:
        if not tok.startswith("--"):
            continue
        name = tok[2:].split("=", 1)[0]
        if name not in allowed and name.replace("_", "-") not in allowed:
            unknown.append(tok.split("=", 1)[0])
    if unknown:
        envelope.fail(
            "E_USAGE",
            "unknown option for this command",
            {
                "command": command["path"],
                "unknown": sorted(set(unknown)),
                "accepted": sorted({p["name"] for p in command["params"]}),
                "hint": "run `kicad-cli reference` for this command's parameters; "
                "an option that is not declared here belongs to a different command",
            },
        )


def _trace(path: str) -> None:
    """Record that this command reached its handler, if ``KICAD_CLI_TRACE`` names
    a file to record into.

    Only the test suite sets it. The coverage guard used to decide whether a
    command had a test by searching the test sources for the literal argument
    tuple, which is both too loose and too tight: a command name inside a
    docstring counted as coverage, while ``run("fab", fmt)`` in a parametrised
    test did not. Three of the four plot commands were reported untested when
    they each had a real one. A line written here means the command was
    dispatched for real.

    Best effort on purpose -- a coverage aid must never be able to fail a
    command the user asked for.
    """
    target = os.environ.get("KICAD_CLI_TRACE")
    if not target:
        return
    try:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(path + "\n")
    except OSError:
        pass


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0] in ("-h", "--help", "help"):
        sys.stdout.write(_USAGE)
        sys.exit(0)

    positional, opts = _parse(argv)

    fmt = opts.get("format") or ("json" if opts.get("json") else "json")
    if fmt not in ("json", "text", "raw"):
        envelope.fail("E_USAGE", "--format must be json, text or raw", {"got": fmt})
    fields = opts.get("fields")
    commands = registry.build()
    command, _rest = registry.lookup(commands, positional)
    schema = registry.SCHEMAS.get(command["output_schema"], {}) if command else {}
    envelope.configure(
        fmt=fmt,
        compact=bool(opts.get("compact")),
        fields=[f.strip() for f in fields.split(",")] if isinstance(fields, str) else None,
        quiet=bool(opts.get("quiet")),
        schema_name=command["output_schema"] if command else None,
        schema_fields=schema.get("fields"),
    )
    if command is None:
        envelope.fail(
            "E_USAGE",
            "unknown command",
            {
                "got": " ".join(positional) or "(none)",
                "hint": "run `kicad-cli reference` for the machine-readable command list",
            },
        )

    _reject_unknown(command, argv)

    _trace(command["path"])

    try:
        command["handler"](opts)
    except KeyboardInterrupt:
        envelope.fail("E_INTERRUPTED", "interrupted by signal; nothing was half-applied")
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - last resort, must still emit an envelope
        envelope.fail("E_UNKNOWN", f"unhandled error: {exc}", {"command": command["path"]})


if __name__ == "__main__":
    main()
