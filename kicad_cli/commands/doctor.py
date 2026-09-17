"""``kicad-cli doctor`` -- environment and release-readiness checks.

Every non-pass check must carry a fix the caller can actually run. A check that
only reports a problem costs an agent a round trip.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .. import envelope, kicad_env
from . import reference


def _check(name: str, status: str, fix: str | None, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"check": name, "status": status, "fix": fix}
    out.update(extra)
    return out


def _ipc_server_state() -> tuple[str, str | None]:
    """Whether KiCad's IPC API server is enabled in user preferences.

    Off by default. It is the only supported way to reach the interactive
    router, so an agent that wants push-and-shove needs this on.
    """
    roots = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(Path(appdata) / "kicad")
    roots.append(Path.home() / ".config" / "kicad")
    roots.append(Path.home() / "Library" / "Preferences" / "kicad")
    for root in roots:
        if not root.exists():
            continue
        for cfg in sorted(root.glob("*/kicad_common.json"), reverse=True):
            try:
                data = json.loads(cfg.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            api = data.get("api") or {}
            if api.get("enable_server"):
                return "pass", None
            return "warn", (
                "IPC API server is disabled; enable it in KiCad under "
                "Preferences > Plugins > Enable IPC API server if you need "
                f"interactive-router access ({cfg})"
            )
    return "warn", "could not find kicad_common.json; IPC API server state unknown"


def run(_args: dict[str, Any]) -> None:
    checks: list[dict[str, Any]] = []

    python = kicad_env.find_python()
    if python:
        version = kicad_env.kicad_version(python)
        checks.append(_check("kicad_python", "pass", None, path=python, kicad_version=version))
    else:
        checks.append(
            _check(
                "kicad_python",
                "fail",
                "install KiCad, or point "
                f"{kicad_env.ENV_ROOT} at the KiCad installation, or {kicad_env.ENV_PYTHON} "
                "at the interpreter that can import pcbnew "
                "(usually <KiCad>/bin/python)",
            )
        )

    official = kicad_env.find_official_cli()
    if official:
        checks.append(_check("drc_oracle", "pass", None, path=official))
    else:
        checks.append(
            _check(
                "drc_oracle",
                "fail",
                f"set {kicad_env.ENV_ROOT} to the KiCad installation, or "
                f"{kicad_env.ENV_OFFICIAL} to KiCad's own kicad-cli binary; "
                "it is the DRC oracle every write command verifies itself against",
            )
        )

    status, fix = _ipc_server_state()
    checks.append(_check("ipc_api_server", status, fix))

    level = reference.RELEASE_READINESS["level"]
    rr_status = {"stable": "pass", "beta": "warn"}.get(level, "fail")
    checks.append(
        _check(
            "release_readiness",
            rr_status,
            None
            if rr_status == "pass"
            else "see release_readiness.reason and evidence_held in `kicad-cli reference` "
            "for which precondition is missing",
            level=level,
        )
    )

    envelope.ok({"checks": checks})
