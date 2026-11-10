"""Finding KiCad: its installation, its version and its own command-line program.

KiCad's command-line program has the same name as this tool, so it is never looked up
through PATH alone: a candidate must sit inside a KiCad installation (next to pcbnew and
eeschema, or under a ``share/kicad`` tree) and must not be this program.

Where to look, in order:

1. ``KICAD_CLI_KICAD_DIR``, when set: the KiCad installation directory, and nothing else.
2. The standard installation directories of each system.
3. A ``kicad-cli`` found in PATH that passes the checks above.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ENV_DIR = "KICAD_CLI_KICAD_DIR"
EXE = "kicad-cli.exe" if os.name == "nt" else "kicad-cli"
VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


@dataclass(frozen=True)
class Installation:
    root: Path
    cli: Path
    version: str

    @property
    def share(self) -> Path:
        return resources(self.root)


def application(root: str | Path) -> Path | None:
    """The macOS application bundle of an installation, if it is one."""
    root = Path(root)
    if root.name.endswith(".app"):
        return root
    app = root / "KiCad.app"
    return app if app.is_dir() else None


def resources(root: str | Path) -> Path:
    """KiCad's shared data: libraries, templates and so on."""
    app = application(root)
    return app / "Contents" / "SharedSupport" if app else Path(root) / "share" / "kicad"


def cli_in(root: str | Path) -> Path:
    app = application(root)
    return app / "Contents" / "MacOS" / "kicad-cli" if app else Path(root) / "bin" / EXE


def standard_roots() -> list[Path]:
    """Where KiCad's installers put it, newest version folder first."""
    hints: list[Path] = []
    if sys.platform == "win32":
        for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            if os.environ.get(var):
                hints.append(Path(os.environ[var]) / "KiCad")
        if os.environ.get("LOCALAPPDATA"):
            hints.append(Path(os.environ["LOCALAPPDATA"]) / "Programs" / "KiCad")
    elif sys.platform == "darwin":
        hints += [Path("/Applications/KiCad/KiCad.app"), Path.home() / "Applications" / "KiCad" / "KiCad.app"]
    else:
        hints += [Path("/usr"), Path("/usr/local")]
    roots: list[Path] = []
    for hint in hints:
        if not hint.is_dir():
            continue
        roots.append(hint)
        if sys.platform == "win32":
            try:
                versions = [p for p in hint.iterdir() if p.is_dir() and VERSION_RE.match(p.name + ".0")]
            except OSError:
                versions = []
            roots.extend(sorted(versions, key=lambda p: _version_key(p.name), reverse=True))
    return roots


def _version_key(name: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", name)[:3])


def is_kicads_own(candidate: Path) -> bool:
    """A program inside a KiCad installation, not a wrapper script."""
    try:
        if not candidate.is_file() or candidate.suffix.lower() in (".cmd", ".bat", ".py", ".js", ".ps1"):
            return False
        directory = candidate.resolve().parent
        siblings = {p.stem.lower() for p in directory.iterdir()}
        return bool({"pcbnew", "eeschema", "kicad"} & siblings) or (directory.parent / "share" / "kicad").is_dir() \
            or (directory.parent / "SharedSupport").is_dir()
    except OSError:
        return False


def is_self(candidate: Path) -> bool:
    ours = [Path(sys.argv[0])] if sys.argv and sys.argv[0] else []
    if getattr(sys, "frozen", False):
        ours.append(Path(sys.executable))
    for path in ours:
        try:
            if path.is_file() and candidate.samefile(path):
                return True
        except OSError:
            continue
    return False


def root_of(cli: Path) -> Path:
    """The installation directory a KiCad program sits in."""
    cli = cli.resolve()
    if cli.parent.name == "MacOS" and cli.parent.parent.name == "Contents":
        return cli.parent.parent.parent
    return cli.parent.parent


def version_of(cli: Path, timeout: float = 30) -> str | None:
    """The version KiCad's program reports, such as 10.0.6."""
    try:
        done = subprocess.run([str(cli), "version"], capture_output=True, timeout=timeout,
                              creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    match = VERSION_RE.search(done.stdout.decode("utf-8", "replace"))
    return match.group(0) if done.returncode == 0 and match else None


def candidates() -> list[Path]:
    override = os.environ.get(ENV_DIR)
    if override is not None:
        root = Path(override).expanduser()
        return [cli_in(root)] if override and root.is_absolute() else []
    found = [cli_in(root) for root in standard_roots()]
    on_path = shutil.which(EXE)
    if on_path:
        found.append(Path(on_path))
    return found


def find() -> Installation | None:
    """The first KiCad installation whose program answers with a version."""
    seen: set[str] = set()
    for cli in candidates():
        try:
            key = str(cli.resolve()).casefold()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        if not is_kicads_own(cli) or is_self(cli):
            continue
        version = version_of(cli)
        if version:
            return Installation(root=root_of(cli), cli=cli.resolve(), version=version)
    return None


def supported(version: str) -> bool:
    """The range kicad-cli declares: >=10.0.6 <11."""
    match = VERSION_RE.match(version)
    if not match:
        return False
    major, minor, patch = (int(x) for x in match.groups())
    return major == 10 and (minor, patch) >= (0, 6)
