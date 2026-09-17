"""PyInstaller entry used by release.yml to produce a single-file binary.

Run `scripts/smoke_binary.py dist/kicad-cli[.exe]` after this. A binary that
builds is not a binary that runs: the first version of this file handed
`kicad_cli/main.py` to PyInstaller directly, which builds cleanly and then dies
on its first relative import.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SEP = ";" if sys.platform == "win32" else ":"


def data(source: Path, dest: str) -> list[str]:
    return ["--add-data", f"{source}{SEP}{dest}"]


def main() -> int:
    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--name",
        "kicad-cli",
        # The entry script imports the package rather than being a module of
        # it, so the frozen binary keeps its package context.
        "--paths",
        str(ROOT),
        # Payloads are read from disk by path at runtime, so they must ride
        # along as data rather than being frozen into the import graph.
        *data(ROOT / "kicad_cli" / "payload", "kicad_cli/payload"),
        *data(ROOT / "CHANGELOG.md", "."),
    ]

    # The IPC client is vendored, not installed, so PyInstaller has to be told
    # where to find it -- as an import path, not as data. Carried as data it
    # rides along but is never analysed, so none of its dependencies come with
    # it: the binary shipped kipy and then failed to import it with "No module
    # named 'platform'". Without this, `board live` is dead in the shipped
    # binary while working perfectly from a checkout, and its own error message
    # tells the user to run a pip command that cannot help them.
    vendor = ROOT / ".vendor"
    if vendor.is_dir():
        args += ["--paths", str(vendor), "--collect-submodules", "kipy"]
    else:
        print("warning: .vendor is missing, so `board live` will not work in this build")
        print("         run: pip install --target .vendor kicad-python")

    args.append(str(ROOT / "entry.py"))
    return subprocess.call(args)


if __name__ == "__main__":
    raise SystemExit(main())
