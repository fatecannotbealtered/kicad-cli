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

Since we share the name ``kicad-cli``, never blindly invoke a PATH namesake.
Explicit absolute configuration is authoritative. Unconfigured official-CLI
discovery accepts only installation-shaped candidates and rejects this process.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from . import envelope
from .installation import python_candidates, resources
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
    if override is not None:
        path = Path(override).expanduser()
        if not override or not path.is_absolute():
            return []
        # An explicit installation is exclusive, not a hint before other installs.
        hints = [str(path.resolve())]
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


def find_kicad_root() -> str | None:
    """The installation directory, for the parts of KiCad that are just files.

    Symbol and footprint libraries are read, not imported, so a command that
    only needs those needs neither pcbnew nor the official binary -- only where
    KiCad put its `share/kicad` tree. Resolved the same way as everything else
    here, so one KICAD_CLI_ROOT still answers for all of it.
    """
    for root in _candidate_roots():
        if (resources(root) / "symbols").is_dir():
            return str(root)
    return None


def find_python() -> str | None:
    """Absolute path of the Python interpreter that can ``import pcbnew``."""
    override = os.environ.get(ENV_PYTHON)
    if override is not None:
        path = Path(override).expanduser()
        return str(path.resolve()) if override and path.is_absolute() and path.is_file() else None
    if (
        ENV_ROOT not in os.environ
        and not getattr(sys, "frozen", False)
        and _can_import_pcbnew(sys.executable)
    ):
        return sys.executable
    for root in _candidate_roots():
        for cand in python_candidates(root):
            if _can_import_pcbnew(str(cand)):
                return str(cand)
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
            [python, "-B", "-c", "import pcbnew; print(pcbnew.GetBuildVersion())"],
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


@lru_cache(maxsize=16)
def official_version(executable: str) -> str | None:
    """Read-only identity probe; a selected path is not proof of native capability."""
    try:
        result = subprocess.run([executable, "--version"], capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.decode("utf-8", "replace").strip()
    return (
        value
        if result.returncode == 0 and re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][\w.~-]+)?", value)
        else None
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
    # We decode this pipe as UTF-8 below, so say so rather than inheriting
    # whatever locale KiCad's interpreter starts with. The payload writes UTF-8
    # itself; this covers the bytes it does not write, such as an interpreter
    # traceback, which would otherwise arrive mangled in the E_IO details.
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)
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
