"""Every shape-affecting flag combination, checked against its declaration.

`test_contract.py` runs each command once. That is enough to catch a command
whose output never matched its schema, and not enough to catch one whose output
matches for the flags a test happens to pass and not for the others. It missed
exactly that: `board route` had three modes and three different key sets behind
one declaration, and only `--mode repair` was ever run, so the suite was green
while two thirds of the command's contract was wrong.

So the unit here is the *combination*, not the command. A flag that can change
which keys come back belongs in this list, and a new one belongs here with it.
Strict mode makes the envelope refuse to emit anything that does not match the
declaration, so a drifted contract fails the run rather than misleading a
caller.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from kicad_demos import DEMOS, SKIP_REASON

REPO = Path(__file__).resolve().parents[1]

WRITE_COMMANDS = {
    "board route",
    "board stitch",
    "board rewidth",
    "board widen",
    "board move",
    "fab gerber",
    "fab pdf",
    "fab svg",
    "fab dxf",
    "fab drill",
    "sch relink",
}
NEEDS_OUT = {"fab gerber", "fab pdf", "fab svg", "fab dxf", "fab drill"}
NO_BOARD = {"reference", "context", "doctor", "changelog"}
# `sch create` takes a specification rather than a board, so it needs a fixture
# this sweep does not have. Its combinations live in test_sch_create.py; naming
# it here keeps the omission deliberate rather than accidental.
COVERED_ELSEWHERE = {"sch create"}

# Combinations that can change the emitted key set. Numeric-only options are
# represented once: a number cannot add or remove a key, and pretending
# otherwise would buy minutes of KiCad time for nothing.
CASES: list[tuple[str, list[str]]] = [
    ("reference", []),
    ("reference", ["--command", "board audit"]),
    ("context", []),
    ("doctor", []),
    ("changelog", []),
    ("changelog", ["--since", "0.1.0"]),
    ("board audit", []),
    ("board audit", ["--oz", "2", "--dt", "20"]),
    ("board parity", []),
    ("board plane", []),
    ("sch link", []),
    ("sch audit", []),
    ("sch sync-preview", []),
    ("board stitch", ["--net", "GND"]),
    ("board stitch", ["--net", "GND", "--bridge"]),
    ("board widen", []),
    ("board move", ["--moves", "R5:50,50"]),
    ("board rewidth", []),
    # The three modes that shared one declaration and did not share a shape.
    ("board route", ["--mode", "repair"]),
    ("board route", ["--mode", "repair", "--ripup"]),
    ("board route", ["--mode", "full"]),
    ("board route", ["--mode", "rewidth"]),
    ("fab gerber", []),
    ("fab gerber", ["--layers", "F.Cu,B.Cu"]),
    ("fab pdf", []),
    ("fab svg", []),
    ("fab dxf", []),
    ("fab drill", []),
    ("fab drill", ["--map", "gerber"]),
    ("fab drill", ["--map", "none"]),
    ("fab drill", ["--merge", "--inch", "--aux-origin"]),
]


def _demo(into: Path) -> Path:
    into.mkdir(parents=True, exist_ok=True)
    for item in (DEMOS / "interf_u").iterdir():
        if item.is_file():
            shutil.copy2(item, into / item.name)
    return next(into.glob("*.kicad_pcb"))


def _run(argv: list[str]) -> tuple[dict | None, str, int]:
    env = dict(os.environ, KICAD_CLI_STRICT="1", PYTHONIOENCODING="utf-8")
    env.pop("KICAD_CLI_TRACE", None)  # Coverage evidence is measured elsewhere.
    proc = subprocess.run(
        [sys.executable, "-m", "kicad_cli.main", *argv, "--compact"],
        capture_output=True,
        cwd=REPO,
        env=env,
        timeout=3600,
    )
    err = proc.stderr.decode("utf-8", "replace")
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith("{"):
            return json.loads(line), err, proc.returncode
    return None, err, proc.returncode


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
@pytest.mark.parametrize(
    ("path", "extra"), CASES, ids=lambda v: v if isinstance(v, str) else "-".join(v) or "bare"
)
def test_every_flag_combination_matches_its_declaration(path, extra, tmp_path):
    argv = path.split()
    board = None
    if path not in NO_BOARD:
        board = _demo(tmp_path / "design")
        argv += ["--board", str(board)]
    argv += extra
    if path in NEEDS_OUT:
        argv += ["--out", str(tmp_path / "fab")]

    if path in WRITE_COMMANDS:
        plan, err, _ = _run([*argv, "--dry-run"])
        assert plan is not None, f"no envelope from dry run: {err[:300]}"
        assert plan["ok"] is False and plan["error"]["code"] == "E_CONFIRMATION_REQUIRED", plan
        argv += ["--confirm", plan["error"]["details"]["confirm_token"]]

    doc, err, code = _run(argv)
    # Strict mode writes the mismatch to stderr and emits nothing, so an empty
    # stdout here is the failure worth reading.
    assert "contract violation" not in err, f"{path} {' '.join(extra)}: {err.strip()}"
    assert doc is not None, f"no envelope: rc={code} {err[:300]}"
    assert doc["ok"] is True, doc
