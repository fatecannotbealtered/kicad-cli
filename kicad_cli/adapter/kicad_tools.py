"""KiCad's own command-line program: ERC, DRC and every export.

The checks and exports are KiCad's, run by the same code as the buttons in KiCad's editors,
so their results match what a person sees there. Each run gets a private temporary folder
for its report; the program always runs in the project folder, where it finds the project's
rules and library tables. It runs on a copy of the person's KiCad settings, because it rewrites
its settings file on every run (it records its working folder there).

Runs are bounded so that a call returns before the runtime's deadline (DCLI-065): a read
gets ``READ_TIMEOUT_S``, a write ``WRITE_TIMEOUT_S``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .._driver import AdapterError
from .fileformat import lib_tables
from .install import NO_WINDOW, Installation

READ_TIMEOUT_S = 25
WRITE_TIMEOUT_S = 40
MAX_REPORT_BYTES = 64 * 1024 * 1024


def run(kicad: Installation, args: list[str], cwd: Path, timeout: float,
        reading: bool = True) -> subprocess.CompletedProcess[bytes]:
    """Run KiCad's program; a timeout or a failure to start is an adapter error. A read that
    takes too long is E_TIMEOUT; an export is written to a staging folder, so one that takes too
    long leaves the target as it was."""
    env = dict(os.environ)
    env.pop("KICAD_CLI_FAKE_HOST", None)
    started = time.monotonic()
    try:
        with private_settings() as home:
            env["KICAD_CONFIG_HOME"] = home
            done = subprocess.run([str(kicad.cli), *args], cwd=str(cwd), capture_output=True, timeout=timeout,
                                  creationflags=NO_WINDOW, stdin=subprocess.DEVNULL, env=env)
            left = timeout - (time.monotonic() - started)
            if reading and crashed(done.returncode) and left > timeout / 2:
                # KiCad's program now and then crashes on start; a read writes only to a temporary
                # folder, so it runs once more in the time that is left.
                done = subprocess.run([str(kicad.cli), *args], cwd=str(cwd), capture_output=True, timeout=left,
                                      creationflags=NO_WINDOW, stdin=subprocess.DEVNULL, env=env)
            return done
    except subprocess.TimeoutExpired:
        raise AdapterError("E_TIMEOUT" if reading else "E_NATIVE_FAILURE",
                           f"KiCad's program did not finish within {timeout:g} s: {' '.join(args[:3])}.",
                           dispatch_state="not_started", knowledge=["checks-time-limit"]) from None
    except OSError as exc:
        raise AdapterError("E_BACKEND_UNAVAILABLE", f"KiCad's program could not start: {exc}.",
                           dispatch_state="not_started", knowledge=["kicad-not-found"]) from None


@contextmanager
def private_settings() -> Iterator[str]:
    """A folder to use as KICAD_CONFIG_HOME for one run: a copy of the person's KiCad settings
    (library tables, path variables, colours), without the 3D model cache. KiCad's program
    rewrites kicad_common.json on every run; the copy keeps the person's own file as it was."""
    source = lib_tables.default_config_dir()
    with tempfile.TemporaryDirectory(prefix="kicad-cli-settings-") as home:
        target = Path(home) / lib_tables.VERSION
        if source is not None and source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("3d", "*.lck"))
        else:
            target.mkdir()
        yield home


def crashed(code: int) -> bool:
    """Whether an exit code is a crash rather than an answer: a Windows exception status such as
    0xC0000409, or a signal elsewhere."""
    return code < 0 or code >= 0xC0000000


def _tail(done: subprocess.CompletedProcess[bytes]) -> str:
    text = (done.stderr or b"").decode("utf-8", "replace").strip() or (done.stdout or b"").decode("utf-8", "replace").strip()
    return text[-600:]


def _report(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AdapterError("E_NATIVE_FAILURE", "KiCad's program wrote no report.")
    if path.stat().st_size > MAX_REPORT_BYTES:
        raise AdapterError("E_NATIVE_FAILURE", "KiCad's report is too large to read.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AdapterError("E_NATIVE_FAILURE", f"KiCad's report cannot be read: {exc}.") from None


def drc(kicad: Installation, board: Path, *, parity: bool, refill_zones: bool = True,
        timeout: float = READ_TIMEOUT_S) -> dict[str, Any]:
    """KiCad's design rules check of a saved board, as KiCad's JSON report.

    Zones are refilled in memory first, never saved, so the check sees current fills.
    """
    with tempfile.TemporaryDirectory(prefix="kicad-cli-drc-") as tmp:
        out = Path(tmp) / "drc.json"
        args = ["pcb", "drc", "--format", "json", "--units", "mm", "--severity-all", "--output", str(out)]
        if parity:
            args.append("--schematic-parity")
        if refill_zones:
            args.append("--refill-zones")
        done = run(kicad, [*args, str(board)], board.parent, timeout)
        if done.returncode != 0:
            raise AdapterError("E_NATIVE_FAILURE", f"KiCad's DRC failed (exit {done.returncode}): {_tail(done)}")
        return _report(out)


def erc(kicad: Installation, schematic: Path, timeout: float = READ_TIMEOUT_S) -> dict[str, Any]:
    """KiCad's electrical rules check of a saved schematic, as KiCad's JSON report."""
    with tempfile.TemporaryDirectory(prefix="kicad-cli-erc-") as tmp:
        out = Path(tmp) / "erc.json"
        args = ["sch", "erc", "--format", "json", "--units", "mm", "--severity-all", "--output", str(out), str(schematic)]
        done = run(kicad, args, schematic.parent, timeout)
        if done.returncode != 0:
            raise AdapterError("E_NATIVE_FAILURE", f"KiCad's ERC failed (exit {done.returncode}): {_tail(done)}")
        return _report(out)


def netlist(kicad: Installation, schematic: Path, timeout: float = READ_TIMEOUT_S) -> str:
    """A fresh KiCad netlist (s-expression format) of a saved schematic, as text."""
    with tempfile.TemporaryDirectory(prefix="kicad-cli-net-") as tmp:
        out = Path(tmp) / "design.net"
        args = ["sch", "export", "netlist", "--format", "kicadsexpr", "--output", str(out), str(schematic)]
        done = run(kicad, args, schematic.parent, timeout)
        if done.returncode != 0 or not out.is_file():
            raise AdapterError("E_NATIVE_FAILURE", f"KiCad's netlist export failed (exit {done.returncode}): {_tail(done)}")
        return out.read_text(encoding="utf-8")


def export(kicad: Installation, args: list[str], source: Path, staging: Path,
           timeout: float = WRITE_TIMEOUT_S) -> list[Path]:
    """Run one export into ``staging`` and return the files it wrote there."""
    staging.mkdir(parents=True, exist_ok=True)
    done = run(kicad, [*args, str(source)], source.parent, timeout, reading=False)
    files = sorted(p for p in staging.rglob("*") if p.is_file())
    if done.returncode != 0 or not files:
        raise AdapterError("E_NATIVE_FAILURE", f"KiCad's export failed (exit {done.returncode}): {_tail(done)}",
                           dispatch_state="dispatched")
    return files


def move_into(files: list[Path], staging: Path, target: Path) -> list[Path]:
    """Move staged files to the target folder, keeping their relative paths."""
    placed = []
    for f in files:
        dest = target / f.relative_to(staging)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), str(dest))
        placed.append(dest)
    return placed
