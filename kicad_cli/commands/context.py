"""``kicad-cli context`` -- what this run would actually use."""

from __future__ import annotations

import os
import sys
from typing import Any

from .. import envelope, kicad_env


def run(_args: dict[str, Any]) -> None:
    python = kicad_env.find_python()
    official = kicad_env.find_official_cli()
    version = kicad_env.kicad_version(python) if python else None

    envelope.ok(
        {
            # No server, no tenant: this tool operates on local design files.
            "env": "local",
            "account": None,
            "config": {
                "kicad_version": version,
                "kicad_python": python,
                "official_kicad_cli": official,
                "shell_python": sys.executable,
                "overrides": {
                    kicad_env.ENV_ROOT: os.environ.get(kicad_env.ENV_ROOT),
                    kicad_env.ENV_PYTHON: os.environ.get(kicad_env.ENV_PYTHON),
                    kicad_env.ENV_OFFICIAL: os.environ.get(kicad_env.ENV_OFFICIAL),
                },
            },
            # Nothing to authenticate against; declared explicitly so an agent
            # does not go hunting for a credential step that does not exist.
            "credentials": {"configured": True, "kind": "none_required"},
        }
    )
