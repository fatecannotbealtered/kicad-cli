"""The old payloads as the reference for the commands that replaced them.

A command moved off pcbnew must say exactly what its payload said -- every
number, every list in its order, every word -- on every board KiCad ships.
Until the payloads are deleted along with SWIG, they are the most exacting
reference there is; anything a port changes on purpose belongs in a later
change that says so.

Test scaffolding, deleted with the payloads.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from kicad_demos import DEMOS, SKIP_REASON

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from kicad_cli import kicad_env  # noqa: E402


def kicad_python() -> str | None:
    try:
        return kicad_env.find_python()
    except Exception:  # noqa: BLE001 - resolution must never break collection
        return None


needs_payload = pytest.mark.skipif(
    not DEMOS.exists() or kicad_python() is None,
    reason=f"needs KiCad's interpreter to run the old payload ({SKIP_REASON})",
)

DEMO_BOARDS = sorted(DEMOS.rglob("*.kicad_pcb")) if DEMOS.exists() else []


def native(*argv: str) -> dict:
    """The command as an agent runs it, in strict mode, its envelope parsed."""
    env = dict(os.environ, KICAD_CLI_STRICT="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "kicad_cli.main", *argv],
        capture_output=True,
        cwd=REPO,
        env=env,
        timeout=3600,
    )
    return json.loads(proc.stdout.decode("utf-8"))


def differences(old, new, where="data") -> list[str]:
    if isinstance(old, dict) and isinstance(new, dict):
        out = []
        for key in sorted(set(old) | set(new)):
            if key not in old or key not in new:
                out.append(f"{where}.{key}: only in {'the payload' if key in old else 'native'}")
            else:
                out += differences(old[key], new[key], f"{where}.{key}")
        return out
    if isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            return [f"{where}: {len(old)} items from the payload, {len(new)} native"]
        out = []
        for i, (a, b) in enumerate(zip(old, new, strict=True)):
            out += differences(a, b, f"{where}[{i}]")
        return out
    return [] if old == new else [f"{where}: payload {old!r:.100}, native {new!r:.100}"]


def assert_same(old: dict, new: dict) -> None:
    """Same outcome; the same error, or every field of the data the same."""
    assert old.get("ok") == new.get("ok"), (old.get("error"), new.get("error"))
    if not old.get("ok"):
        keep = ("code", "message", "details")
        assert {k: old["error"][k] for k in keep} == {k: new["error"][k] for k in keep}
        return
    assert differences(old["data"], new["data"]) == []
