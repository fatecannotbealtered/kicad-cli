"""A KiCad that does what the test tells it to.

`kicad-cli` reaches KiCad two ways, and both are resolved by absolute path from
an environment variable (`KICAD_CLI_OFFICIAL`, `KICAD_CLI_PYTHON`). That makes
them substitutable, which is the whole reason this file can exist.

Why fake an upstream we can run for real: the real KiCad cannot be made to fail
on demand. It will not refuse to launch, time out, return an empty file, or
succeed only on the third try — and those are exactly the paths that decide
whether a healthy board gets reported as broken. They were reasoned about and
shipped untested until this existed. The happy path stays the real binary's job;
this covers what the real binary will not do.

It also makes a meaningful part of the suite run on a machine with no KiCad at
all, where everything else skips.

Behaviour is chosen with `FAKE_KICAD_BEHAVIOUR`:

    ok             succeed, writing plausible output
    no_output      exit 0 and write nothing (the observed Windows transient)
    launch_fail    exit non-zero with a message on stderr
    hang           stay busy past the caller's timeout, then exit
    garbage        write a file that is not the expected format
    flaky          fail twice, then succeed -- proves retry recovers

`FAKE_KICAD_CALLS` names a counter file so `flaky` can tell attempts apart.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OFFICIAL_STUB = HERE / "fake_official_cli.py"
PAYLOAD_STUB = HERE / "fake_payload_python.py"


def make_launcher(tmp: Path, name: str, script: Path) -> Path:
    """Wrap a stub script in something the CLI can exec as a binary.

    The CLI builds `[exe, ...args]` and hands it to subprocess, so the stub has
    to be executable in its own right rather than importable. A one-line
    launcher around the running interpreter is enough and keeps the stubs
    readable as plain Python.
    """
    tmp.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        exe = tmp / f"{name}.bat"
        exe.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        exe = tmp / name
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return exe


def env(tmp: Path, behaviour: str = "ok") -> dict:
    """Environment that points kicad-cli at the fakes.

    Both boundaries are always substituted, even when the command under test
    only uses one. Leaving the other to resolve normally would make the test
    mean something different on a machine with KiCad than on one without --
    which is exactly the property this file exists to remove.
    """
    out = dict(os.environ, PYTHONIOENCODING="utf-8")
    out["FAKE_KICAD_BEHAVIOUR"] = behaviour
    out["FAKE_KICAD_CALLS"] = str(tmp / "calls.txt")
    out["KICAD_CLI_OFFICIAL"] = str(make_launcher(tmp, "kicad-cli", OFFICIAL_STUB))
    out["KICAD_CLI_PYTHON"] = str(make_launcher(tmp, "kpython", PAYLOAD_STUB))
    # Runs against a fake deliberately do not count towards functional contract
    # coverage: dropping the trace means a command cannot be declared covered on
    # the strength of a stub alone. Coverage has to come from the real thing.
    out.pop("KICAD_CLI_TRACE", None)
    return out


def attempts(tmp: Path) -> int:
    counter = tmp / "calls.txt"
    return len(counter.read_text(encoding="utf-8").split()) if counter.exists() else 0
