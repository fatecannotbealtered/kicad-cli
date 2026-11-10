"""Helpers for tests that run the whole tool: a write as an Agent runs it (the preview, then the
confirmation), and a folder reached through a link, as macOS temporary folders and Windows short
names are."""
from __future__ import annotations

import io
import json
import os
import re
import sys
from pathlib import Path

from kicad_cli import TOOL
from kicad_cli._driver.targets import file_key


def write_through_tool(tmp: Path, *argv: str, dangerous: bool = False) -> dict:
    """Runs one write with a scope over ``tmp`` and a state folder inside it, confirming it when it asks."""
    state = tmp / "state"
    state.mkdir(exist_ok=True)
    scope = tmp / "scope.json"
    scope.write_text(json.dumps({"scope_id": "test", "effects": ["artifact", "change"], "dangerous": dangerous,
                                 "targets": [file_key(str(tmp)) + "/"], "expires_at": "2099-01-01T00:00:00Z"}),
                     encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("KICAD_CLI_")}
    env.update(KICAD_CLI_STATE_DIR=str(state), KICAD_CLI_SCOPE_FILE=str(scope))

    def run(args: list[str]) -> dict:
        out = io.StringIO()
        TOOL.run(args, env, out)
        return json.loads(out.getvalue())

    reply = run(list(argv))
    if not reply["ok"] and reply["error"]["code"] == "E_CONFIRMATION_REQUIRED":
        next_action = reply["error"]["details"]["next_action"]
        token = re.search(r"--confirm (\w[\w-]*)", next_action).group(1)
        reply = run([*argv, "--confirm", token, *(["--dangerous"] if "--dangerous" in next_action else [])])
    return reply


def link(target: Path, at: Path) -> bool:
    """Makes ``at`` a second name for the folder ``target``: a junction on Windows, a symbolic
    link elsewhere. False when the system does not allow it."""
    try:
        if sys.platform == "win32":
            import _winapi

            _winapi.CreateJunction(str(target), str(at))
        else:
            os.symlink(target, at, target_is_directory=True)
    except OSError:
        return False
    return True
