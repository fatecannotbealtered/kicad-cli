"""``board audit`` moved off pcbnew: the old implementation is the reference.

The audit now runs in this process on the file (`kicad_cli/native/audit.py`).
Until the payload it replaced is deleted along with SWIG, that payload is the
most exacting reference there is: same inputs, and every field of the output
must match -- numbers, lists and their order, the findings' wording, and the
error envelope when a board has no project file. Anything the port changes
on purpose belongs in a later change that says so.
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


def native(board: Path) -> dict:
    env = dict(os.environ, KICAD_CLI_STRICT="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "kicad_cli.main", "board", "audit", "--board", str(board)],
        capture_output=True,
        cwd=REPO,
        env=env,
        timeout=1800,
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


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("(kicad_pcb)", "no (version"),
        ("this is not a board", "no expression"),
        ("(kicad_sch (version 20250114))", "not a board"),
        ("(kicad_pcb (version 20260206)", "unbalanced"),
    ],
)
def test_a_file_that_is_not_a_board_says_why(text, reason, tmp_path):
    """The payload died on these: pcbnew returned nothing, the next line raised,
    and the caller got E_IO wrapped around a traceback. Bad input is E_VALIDATION
    with the reason. Needs no KiCad, so it runs everywhere."""
    board = tmp_path / "x.kicad_pcb"
    board.write_text(text, encoding="utf-8")
    (tmp_path / "x.kicad_pro").write_text("{}", encoding="utf-8")
    doc = native(board)
    assert doc["ok"] is False
    assert doc["error"]["code"] == "E_VALIDATION", doc
    assert reason in doc["error"]["details"]["reason"], doc


@needs_payload
@pytest.mark.parametrize(
    "board",
    sorted(DEMOS.rglob("*.kicad_pcb")) if DEMOS.exists() else [],
    ids=lambda p: p.name,
)
def test_the_native_audit_says_exactly_what_the_payload_said(board):
    old = kicad_env.run_payload("audit", ["--board", str(board)], timeout=1800)
    new = native(board)
    assert old.get("ok") == new.get("ok"), (old.get("error"), new.get("error"))
    if not old.get("ok"):
        keep = ("code", "message", "details")
        assert {k: old["error"][k] for k in keep} == {k: new["error"][k] for k in keep}
        return
    assert differences(old["data"], new["data"]) == []
