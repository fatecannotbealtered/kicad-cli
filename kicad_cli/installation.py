"""Paths inside the explicitly selected KiCad installation; no PATH fallback."""

from __future__ import annotations

import os
from pathlib import Path


def application(root: str | Path) -> Path | None:
    root = Path(root)
    if root.name.endswith(".app"):
        return root
    app = root / "KiCad.app"
    return app if app.is_dir() else None


def resources(root: str | Path) -> Path:
    app = application(root)
    return app / "Contents/SharedSupport" if app else Path(root) / "share/kicad"


def official_cli(root: str | Path) -> Path:
    app = application(root)
    if app:
        return app / "Contents/MacOS/kicad-cli"
    return Path(root) / "bin" / ("kicad-cli.exe" if os.name == "nt" else "kicad-cli")


def python_candidates(root: str | Path) -> list[Path]:
    root = Path(root)
    candidates = [
        root / name for name in ("bin/python.exe", "bin/python3.exe", "bin/python3", "bin/python")
    ]
    app = application(root)
    if app:
        framework = app / "Contents/Frameworks/Python.framework/Versions"
        candidates.append(framework / "Current/bin/python3")
        candidates.extend(sorted(framework.glob("*/bin/python3"), reverse=True))
    seen, result = set(), []
    for candidate in candidates:
        if candidate.is_file() and candidate.resolve() not in seen:
            seen.add(candidate.resolve())
            result.append(candidate)
    return result


def compatible_version(value: str) -> bool:
    """The 10.0 patch series used by the shipped file formats and basic smoke."""
    import re

    match = re.fullmatch(r"10\.0\.(\d+)(?:[-+][\w.~-]+)?", value)
    return bool(match and int(match[1]) >= 6)
