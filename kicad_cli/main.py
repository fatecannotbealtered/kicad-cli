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

from . import __version__, envelope, registry

_GLOBAL_FLAGS = {f"--{p['name']}" for p in registry.GLOBAL_OPTIONS if p["type"] == "boolean"}
_GLOBAL_OPTS = {f"--{p['name']}" for p in registry.GLOBAL_OPTIONS if p["type"] != "boolean"}

_USAGE = f"""kicad-cli {__version__} - AI-native KiCad PCB CLI

  kicad-cli reference              describe every command in machine-readable form
  kicad-cli context                report the resolved KiCad install and config
  kicad-cli doctor                 environment and release-readiness checks
  kicad-cli --check-environment    SDK-free package prerequisites, without CAD writes
  kicad-cli changelog [--since V]  what changed between versions
  kicad-cli board analyze ampacity --board <file>
  kicad-cli project parity --board <file>

Agents: call `kicad-cli reference`. This text is for humans and may change.
Discovery: --help (-h), --version; --json aliases --format json.
Command paths precede flags. Read reference for all parameters and defaults.

Internal trial EXE: the matched package includes its authenticated SDK/spec.
Set kicad_root and an owned profile_dir in runtime-config.json beside the EXE.
Run --check-environment [--compact] for structured missing/version/hash diagnostics.
Only that configured profile is initialized; no global settings or scope are granted.
An explicit KICAD_CLI_DRIVER_SDK_PATH override remains strict and never falls back.
KICAD_CLI_IPC_CLIENT_PATH is optional for file tasks; session inspect is read-only.
An npm install is the previously published package, not this candidate.
SDK-free bootstrap accepts no arguments, one --help/-h/help, or --check-environment
with optional --compact. Normal commands use the authenticated pinned parser.
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

    if len(argv) == 2 and argv[0] == "--internal-engineering-trial":
        from .engineering_prepare import main as trial

        sys.argv = [sys.argv[0], "--request", argv[1]]
        sys.exit(trial())

    if len(argv) == 2 and argv[0] == "--internal-power-trial":
        # Frozen private worker. It only accepts owned disposable candidates,
        # never primary engineering files or a public legacy handler.
        from .power_prepare import main as trial

        sys.argv = [sys.argv[0], "--request", argv[1]]
        sys.exit(trial())

    if "--check-environment" in argv:
        from . import bootstrap

        if (
            any(value not in {"--check-environment", "--compact"} for value in argv)
            or argv.count("--check-environment") != 1
            or argv.count("--compact") > 1
        ):
            bootstrap.write_document(
                {
                    "ok": False,
                    "schema_version": "1.0",
                    "meta": {"duration_ms": 0},
                    "error": {
                        "code": "E_USAGE",
                        "retryable": False,
                        "message": "Use --check-environment with only optional --compact.",
                        "details": {"dispatch_state": "not_started"},
                    },
                },
                compact="--compact" in argv,
            )
            sys.exit(2)
        document, code = bootstrap.check_environment()
        bootstrap.write_document(document, compact="--compact" in argv)
        sys.exit(code)

    if not argv or argv in (["-h"], ["--help"], ["help"]):
        paths = "\n".join("  kicad-cli " + c["path"] for c in registry.build())
        from .driver_runtime import declarations

        globals_ = " ".join("--" + g["name"] for g in declarations()["reference"]["global_options"])
        text = (
            _USAGE
            + "\nGlobal options (availability depends on command):\n  "
            + globals_
            + "\n\nAvailable commands:\n"
            + paths
            + "\n"
        )
        sys.stdout.buffer.write(text.encode("utf-8"))
        sys.exit(0)

    # Reset per-invocation state for embedded callers as well as subprocesses.
    envelope.configure()
    from . import bootstrap

    try:
        bootstrap.apply_configuration()
    except bootstrap.ConfigurationError as exc:
        bootstrap.write_document(bootstrap.configuration_error(exc))
        sys.exit(4)
    from . import driver_runtime

    # Every public path is migrated. Unknown/incomplete paths and root flags
    # must use the same pinned parser and structured errors as known commands.
    sys.exit(driver_runtime.execute(argv))


if __name__ == "__main__":
    main()
