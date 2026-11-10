#!/usr/bin/env python3
"""The CI runners for the platform packages in package.json, as a GitHub Actions matrix.

    python scripts/platforms.py systems     one runner per shipped operating system (the merge gate)
    python scripts/platforms.py packages    one runner per platform package (platform delivery)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNERS = {("win32", "x64"): "windows-latest", ("win32", "arm64"): "windows-11-arm",
           ("linux", "x64"): "ubuntu-latest", ("linux", "arm64"): "ubuntu-24.04-arm",
           ("darwin", "x64"): "macos-15-intel", ("darwin", "arm64"): "macos-latest"}


def main(argv: list[str]) -> int:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    pattern = re.escape(package["name"]) + r"-(win32|darwin|linux)-(x64|arm64)"
    pairs = [m.groups() for m in (re.fullmatch(pattern, n) for n in package.get("optionalDependencies", {})) if m]
    if argv == ["systems"]:
        matrix = [{"os": system, "runner": RUNNERS[(system, "x64")]} for system in sorted({s for s, _ in pairs})]
    elif argv == ["packages"]:
        matrix = [{"os": s, "cpu": c, "package": f"{package['name']}-{s}-{c}", "runner": RUNNERS[(s, c)]} for s, c in pairs]
    else:
        print(__doc__, file=sys.stderr)
        return 2
    print(f"matrix={json.dumps(matrix)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
