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

from . import __version__, arguments, envelope, registry

_GLOBAL_FLAGS = {f"--{p['name']}" for p in registry.GLOBAL_OPTIONS if p["type"] == "boolean"}
_GLOBAL_OPTS = {f"--{p['name']}" for p in registry.GLOBAL_OPTIONS if p["type"] != "boolean"}

_USAGE = f"""kicad-cli {__version__} - AI-native KiCad PCB CLI

  kicad-cli reference              describe every command in machine-readable form
  kicad-cli context                report the resolved KiCad install and config
  kicad-cli doctor                 environment and release-readiness checks
  kicad-cli changelog [--since V]  what changed between versions
  kicad-cli board audit  --board <file>
  kicad-cli board parity --board <file>

Agents: call `kicad-cli reference`. This text is for humans and may change.
"""


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

    # Reset per-invocation state for embedded callers as well as subprocesses.
    envelope.configure()
    commands = registry.build()
    command, opts = arguments.parse(argv, commands, registry.GLOBAL_OPTIONS)
    fields = opts.get("fields")
    schema = registry.SCHEMAS[command["output_schema"]]
    envelope.configure(
        fmt=opts.get("format", "json"),
        compact=opts.get("compact", False),
        fields=fields.split(",") if fields else None,
        quiet=opts.get("quiet", False),
        schema_name=command["output_schema"],
        schema_fields=schema.get("fields"),
    )

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
