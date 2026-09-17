"""One standard-library DRC boundary for both the CLI and KiCad payloads.

Importable as a package or a sibling script; no pcbnew import and no stdout.
Reports live in a new private directory for every invocation. The report's
basename-only source (KiCad's native format) is checked, but isolation, not the
basename or a wall-clock timestamp, is what prevents reuse of another run.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ENV_OFFICIAL = "KICAD_CLI_OFFICIAL"
MAX_REPORT_BYTES = 64 * 1024 * 1024
REPORT_GROUPS = ("violations", "unconnected_items", "schematic_parity")


class DrcError(Exception):
    """An execution/protocol failure, never a finding about board quality."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class DrcResult:
    board: str
    executable: str
    report: dict[str, Any]
    input_sha256: dict[str, str | None]
    duration_ms: int


def is_kicads_own(candidate: Path) -> bool:
    """Structural discovery guard, not a publisher-authenticity guarantee."""
    try:
        if not candidate.is_file() or candidate.suffix.lower() in (".cmd", ".bat", ".py", ".js"):
            return False
        directory = candidate.resolve().parent
        siblings = {p.stem.lower() for p in directory.iterdir()}
        return (
            bool({"pcbnew", "eeschema", "kicad"} & siblings)
            or (directory.parent / "share" / "kicad").is_dir()
        )
    except OSError:
        return False


def _is_self(candidate: Path) -> bool:
    ours = [Path(sys.argv[0])] if sys.argv else []
    if getattr(sys, "frozen", False):
        ours.append(Path(sys.executable))
    return any(p.is_file() and candidate.samefile(p) for p in ours)


def find_official_cli(
    roots: list[Path] | None = None, interpreter: str | None = None
) -> str | None:
    """Honor explicit configuration; otherwise only accept a KiCad installation.

    An invalid override must not fall through to a different installation.
    Explicit paths are a user's trust decision, including test executables.
    """
    override = os.environ.get(ENV_OFFICIAL)
    if override:
        try:
            candidate = Path(override).expanduser().resolve()
            return str(candidate) if candidate.is_file() and not _is_self(candidate) else None
        except (OSError, RuntimeError):
            return None
    if roots is None:
        roots = []
        for hint in (
            os.environ.get("KICAD_CLI_ROOT", ""),
            r"C:\Program Files\KiCad",
            r"C:\Program Files (x86)\KiCad",
            "/usr/lib/kicad",
            "/usr",
            "/Applications/KiCad/KiCad.app",
        ):
            if not hint:
                continue
            root = Path(hint)
            if root.is_dir():
                roots.append(root)
                try:
                    roots.extend(sorted((p for p in root.iterdir() if p.is_dir()), reverse=True))
                except OSError:
                    continue
    name = "kicad-cli.exe" if os.name == "nt" else "kicad-cli"
    candidates = []
    if interpreter:
        candidates.append(Path(interpreter).absolute().parent / name)
    for root in roots:
        candidates.extend((root / "bin" / name, root / "Contents" / "MacOS" / name))
    found = shutil.which(name)
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        try:
            if is_kicads_own(candidate) and not _is_self(candidate):
                return str(candidate.resolve())
        except (OSError, RuntimeError):
            continue
    return None


def config_root() -> Path:
    override = os.environ.get("KICAD_CONFIG_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "kicad"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Preferences" / "kicad"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "kicad"


def input_state(board: Path) -> dict[str, str | None]:
    """Fingerprint the board and the two local rule/config sidecars, streaming."""
    result = {}
    for path in (board, board.with_suffix(".kicad_pro"), board.with_suffix(".kicad_dru")):
        try:
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            result[path.name] = digest.hexdigest()
        except FileNotFoundError:
            if path == board:
                raise
            result[path.name] = None
    return result


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate JSON key")
        obj[key] = value
    return obj


def _invalid_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def validate_report(report: Any, board: Path) -> dict[str, Any]:
    """Validate the shape consumed by routing, not a second DRC implementation.

    Unknown additional fields are retained for forward compatibility. Missing
    groups or malformed findings cannot be interpreted as a clean report.
    """
    if not isinstance(report, dict):
        raise ValueError("report must be an object")
    for key in ("source", "date", "kicad_version"):
        if not isinstance(report.get(key), str) or not report[key].strip():
            raise ValueError(f"missing or invalid {key}")
    source = report["source"]
    source_path = Path(source)
    if source_path != Path(board.name):
        if not source_path.is_absolute():
            source_path = board.parent / source_path
        if source_path.resolve() != board:
            raise ValueError("report source does not match requested board")
    if report.get("coordinate_units") != "mm":
        raise ValueError("report coordinate_units must be mm")
    for group in REPORT_GROUPS:
        if not isinstance(report.get(group), list):
            raise ValueError(f"missing or invalid {group}")
        for finding in report[group]:
            if not isinstance(finding, dict):
                raise ValueError("finding must be an object")
            for key in ("type", "description", "severity"):
                if not isinstance(finding.get(key), str):
                    raise ValueError(f"missing or invalid finding {key}")
            if finding["severity"] not in ("error", "warning", "exclusion"):
                raise ValueError("unrecognized finding severity")
            if "excluded" in finding and not isinstance(finding["excluded"], bool):
                raise ValueError("finding excluded must be a boolean")
            if not isinstance(finding.get("items"), list):
                raise ValueError("finding items must be an array")
            for item in finding["items"]:
                if not isinstance(item, dict) or not all(
                    isinstance(item.get(key), str) for key in ("uuid", "description")
                ):
                    raise ValueError("invalid finding item")
                pos = item.get("pos")
                if not isinstance(pos, dict) or not all(
                    type(pos.get(axis)) in (int, float) and math.isfinite(pos[axis])
                    for axis in ("x", "y")
                ):
                    raise ValueError("invalid finding coordinates")
    if "ignored_checks" in report and not isinstance(report["ignored_checks"], list):
        raise ValueError("ignored_checks must be an array")
    return report


def run(board: str, executable: str | None, timeout: float = 1800) -> DrcResult:
    """Read a fresh official DRC report or raise DrcError; never retry a write.

    No --exit-code-violations is requested: only returncode 0 is accepted.
    No --save-board or --refill-zones is requested. Existing stored zone fill
    and project rules are checked; this does not validate schematic parity.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        raise DrcError("E_USAGE", "DRC timeout must be a finite positive number")
    if not executable or not Path(executable).is_absolute() or not Path(executable).is_file():
        raise DrcError(
            "E_CONFIG",
            "official KiCad executable is unavailable",
            {
                "hint": "set KICAD_CLI_OFFICIAL to KiCad's binary; run kicad-cli doctor",
            },
        )
    started = time.monotonic()
    try:
        path = Path(board).expanduser().resolve()
        if not path.is_file():
            raise DrcError("E_NOT_FOUND", "board file is not a regular file", {"board": str(path)})
        before = input_state(path)
        with tempfile.TemporaryDirectory(prefix="kicadcli-drc-") as directory:
            workspace = Path(directory)
            output = workspace / "report.json"
            config = workspace / "config"
            seed = config_root().resolve()
            if seed == workspace or seed in workspace.parents:
                raise DrcError("E_CONFIG", "KiCad config root contains the temporary workspace")
            if seed.exists():
                shutil.copytree(seed, config)
            else:
                config.mkdir()
            env = dict(os.environ, KICAD_CONFIG_HOME=str(config))
            argv = [
                executable,
                "pcb",
                "drc",
                "--format",
                "json",
                "--severity-all",
                "--units",
                "mm",
                "-o",
                str(output),
                str(path),
            ]
            proc = subprocess.run(
                argv, capture_output=True, timeout=timeout, cwd=path.parent, env=env
            )
            if proc.returncode != 0:
                raise DrcError(
                    "E_IO",
                    "KiCad DRC process failed",
                    {
                        "returncode": proc.returncode,
                        "stderr": (proc.stderr or b"").decode("utf-8", "replace")[-1200:],
                        "stdout": (proc.stdout or b"").decode("utf-8", "replace")[-1200:],
                        "_untrusted": ["stderr", "stdout"],
                    },
                )
            if not output.is_file() or output.is_symlink():
                raise DrcError("E_IO", "KiCad DRC produced no regular report")
            with output.open("rb") as handle:
                raw = handle.read(MAX_REPORT_BYTES + 1)
            if len(raw) > MAX_REPORT_BYTES:
                raise DrcError(
                    "E_IO",
                    "DRC report exceeds the supported size",
                    {
                        "max_report_bytes": MAX_REPORT_BYTES,
                    },
                )
            report = validate_report(
                json.loads(raw, object_pairs_hook=_pairs, parse_constant=_invalid_constant), path
            )
            try:
                after = input_state(path)
            except OSError as exc:
                raise DrcError("E_CONFLICT", "design became unavailable during DRC") from exc
            if before != after:
                raise DrcError(
                    "E_CONFLICT",
                    "design changed during DRC; discard this report",
                    {
                        "changed_files": [key for key in before if before[key] != after[key]],
                    },
                )
        return DrcResult(
            str(path), executable, report, before, int((time.monotonic() - started) * 1000)
        )
    except subprocess.TimeoutExpired as exc:
        raise DrcError("E_TIMEOUT", "KiCad DRC timed out", {"timeout_s": timeout}) from exc
    except (ValueError, UnicodeError, OverflowError, RecursionError) as exc:
        raise DrcError(
            "E_IO", "KiCad DRC returned an invalid report", {"reason": str(exc)[:200]}
        ) from exc
    except OSError as exc:
        raise DrcError("E_IO", "KiCad DRC I/O failed", {"reason": str(exc)[:200]}) from exc
