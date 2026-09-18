"""Locating KiCad, and running payloads inside its interpreter.

Two facts shape this module.

First, ``pcbnew`` is a compiled extension that ships *inside* a KiCad install.
It cannot be pip-installed or bundled, so anything that touches a board has to
execute under KiCad's own Python. The CLI shell stays portable; the payload is
a guest in KiCad's interpreter.

Second, we expose no upstream subcommands. This tool is not a wrapper around
KiCad's own CLI; it is its own surface. The official binary supplies DRC, ERC
and netlist exports as internal
dependencies, reported by ``doctor``. DRC invocations share a single
standard-library runner with the payload scripts.

Since we deliberately share the name ``kicad-cli``, that one lookup must never
go through ``PATH``: it would find this tool and recurse. We resolve it by
absolute path only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from . import envelope
from .payload import drc_runner

ENV_PYTHON = "KICAD_CLI_PYTHON"
ENV_OFFICIAL = "KICAD_CLI_OFFICIAL"

# Where KiCad usually puts its bundled interpreter, most specific first.
_PY_HINTS = (
    "bin/python.exe",
    "bin/python3.exe",
    "bin/python3",
    "bin/python",
    "Contents/Frameworks/Python.framework/Versions/Current/bin/python3",
)

ENV_ROOT = "KICAD_CLI_ROOT"

# Standard install locations only. A developer's own path used to be listed
# here, which is not something to ship in product code -- and it hid the fact
# that an unusual install had no supported way to be found. That is what
# KICAD_CLI_ROOT is for: one variable pointing at the installation, instead of
# setting the interpreter and the binary separately.
_ROOT_HINTS = (
    r"C:\Program Files\KiCad",
    r"C:\Program Files (x86)\KiCad",
    "/usr/lib/kicad",
    "/usr",
    "/Applications/KiCad/KiCad.app",
)


def _candidate_roots() -> list[Path]:
    roots: list[Path] = []
    hints = list(_ROOT_HINTS)
    override = os.environ.get(ENV_ROOT)
    if override:
        # Searched first: someone who sets this means it.
        hints.insert(0, override)
    for hint in hints:
        p = Path(hint)
        if not p.exists():
            continue
        roots.append(p)
        # KiCad on Windows nests a versioned directory, e.g. C:\Program Files\KiCad\10.0
        try:
            roots.extend(sorted((c for c in p.iterdir() if c.is_dir()), reverse=True))
        except OSError:
            pass
    return roots


def find_python() -> str | None:
    """Absolute path of the Python interpreter that can ``import pcbnew``."""
    override = os.environ.get(ENV_PYTHON)
    if override and Path(override).exists():
        return override
    if not getattr(sys, "frozen", False) and _can_import_pcbnew(sys.executable):
        return sys.executable
    for root in _candidate_roots():
        for rel in _PY_HINTS:
            cand = root / rel
            if cand.exists() and _can_import_pcbnew(str(cand)):
                return str(cand)
    return None


def _can_import_pcbnew(python: str) -> bool:
    return kicad_version(python) is not None


@lru_cache(maxsize=16)
def kicad_version(python: str) -> str | None:
    """Probe once per interpreter per invocation; discovery also needs this result.

    Process-local only: a new CLI invocation probes afresh, so installations,
    environment overrides and versions are not persisted as stale facts.
    """
    try:
        r = subprocess.run(
            [python, "-c", "import pcbnew; print(pcbnew.GetBuildVersion())"],
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", "replace").strip() or None


def find_official_cli() -> str | None:
    """Use the same installation resolver as standalone KiCad payloads."""
    return drc_runner.find_official_cli(
        roots=_candidate_roots(), interpreter=os.environ.get(ENV_PYTHON)
    )


def _is_kicads_own(candidate: Path) -> bool:
    return drc_runner.is_kicads_own(candidate)


def run_payload(payload: str, args: list[str], timeout: int = 1800) -> dict:
    """Run a payload module inside KiCad's interpreter and return its envelope.

    The payload prints one JSON document on the first line of stdout. Later
    lines may be noise: KiCad's SWIG layer writes memory-leak diagnostics
    straight to the C-level stdout, and those bytes are flushed after ours.
    So we scan for the first line that parses, rather than loading the stream.
    """
    python = find_python()
    if not python:
        envelope.fail(
            "E_CONFIG",
            "could not find a Python interpreter with pcbnew; KiCad may not be installed",
            {
                "hint": f"set {ENV_ROOT} to the KiCad installation directory -- "
                f"or {ENV_PYTHON} to the interpreter itself; run: kicad-cli doctor"
            },
        )
    here = Path(__file__).resolve().parent
    cmd = [python, "-B", "-u", str(here / "payload" / f"{payload}.py"), *args]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        envelope.fail("E_TIMEOUT", f"payload {payload} timed out after {timeout}s")
    except OSError as exc:
        envelope.fail("E_IO", f"could not start payload {payload}: {exc}")
    out = (r.stdout or b"").decode("utf-8", "replace").splitlines()
    for line in out:
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    envelope.fail(
        "E_IO",
        f"payload {payload} returned no envelope",
        {
            "exit_code": r.returncode,
            "stderr": (r.stderr or b"").decode("utf-8", "replace")[-600:],
        },
    )
    raise AssertionError("unreachable")  # pragma: no cover


def run_drc(board: str, timeout: int = 1800) -> dict:
    """Compatibility adapter: execution failures never become clean DRC reports."""
    try:
        return drc_runner.run(board, find_official_cli(), timeout).report
    except drc_runner.DrcError as exc:
        envelope.fail(exc.code, str(exc), exc.details)
    raise AssertionError("unreachable")
