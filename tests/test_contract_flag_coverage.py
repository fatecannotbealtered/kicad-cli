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

from pathlib import Path

import pytest
from kicad_demos import DEMOS, SKIP_REASON, copy_demo
from public_test_cli import copy_export_fixture, invoke_details, planned
from public_test_cli import public_cli_scope as public_cli_scope

REPO = Path(__file__).resolve().parents[1]

WRITE_COMMANDS = {
    "board route repair",
    "board route rebuild",
    "board zone stitch",
    "board route rewidth",
    "board track widen",
    "board footprint move",
    "fab gerber",
    "board plot",
    "fab drill",
    "project links repair",
}
NEEDS_OUT = {"fab gerber", "board plot", "fab drill"}
NO_BOARD = {"reference", "context", "doctor", "changelog"}
# `sch create` takes a specification rather than a board, so it needs a fixture
# this sweep does not have. Its combinations live in test_sch_create.py; naming
# it here keeps the omission deliberate rather than accidental.
COVERED_ELSEWHERE = {"sch create", "sch edit"}

# Combinations that can change the emitted key set. Numeric-only options are
# represented once: a number cannot add or remove a key, and pretending
# otherwise would buy minutes of KiCad time for nothing.
CASES: list[tuple[str, list[str]]] = [
    ("reference", []),
    ("reference", ["--command", "board analyze ampacity"]),
    ("context", []),
    ("doctor", []),
    ("changelog", []),
    ("changelog", ["--since", "0.1.0"]),
    ("board analyze ampacity", []),
    ("board analyze ampacity", ["--oz", "2", "--dt", "20"]),
    ("project parity", []),
    ("board analyze reference-plane", []),
    ("project links inspect", []),
    ("sch check", []),
    ("board zone stitch", ["--net", "GND"]),
    ("board zone stitch", ["--net", "GND", "--bridge"]),
    ("board track widen", []),
    ("board footprint move", ["--moves", "R5:50,50"]),
    ("board route rewidth", ["--classes", "Power"]),
    # The three modes that shared one declaration and did not share a shape.
    ("board route repair", []),
    ("board route repair", ["--ripup"]),
    ("board route rebuild", []),
    ("board route rewidth", ["--nets", "GND"]),
    ("fab gerber", []),
    ("fab gerber", ["--layers", "F.Cu,B.Cu"]),
    ("board plot", ["--file-format", "pdf"]),
    ("board plot", ["--file-format", "svg"]),
    ("board plot", ["--file-format", "dxf"]),
    ("fab drill", []),
    ("fab drill", ["--map", "gerber"]),
    ("fab drill", ["--map", "none"]),
    ("fab drill", ["--merge", "--inch", "--aux-origin"]),
]


def _demo(into: Path) -> Path:
    copy_demo(DEMOS / "interf_u", into, whole=False)
    return next(into.glob("*.kicad_pcb"))


def _run(argv: list[str]) -> tuple[dict | None, str, int]:
    # Early parser errors can precede --compact and legitimately be pretty
    # JSON. Decode the complete document and retain actual exit/tree evidence.
    return invoke_details(argv, suppress_trace=True)


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
@pytest.mark.parametrize(
    ("path", "extra"), CASES, ids=lambda v: v if isinstance(v, str) else "-".join(v) or "bare"
)
def test_every_flag_combination_matches_its_declaration(path, extra, tmp_path):
    argv = path.split()
    board = None
    if path not in NO_BOARD:
        if path in NEEDS_OUT:
            board = copy_export_fixture(tmp_path / "design")
        elif path == "board route rebuild":
            # Reuse the existing small input generator only. The actual
            # preview/confirm below still uses the public subprocess entry.
            from test_driver_power_runtime import make_case

            board, _ = make_case(tmp_path / "design")
        else:
            board = _demo(tmp_path / "design")
        argv += (
            ["--schematic", str(board.with_suffix(".kicad_sch"))]
            if path == "sch check"
            else ["--board", str(board)]
        )
    argv += extra
    if path in NEEDS_OUT:
        argv += ["--out", str(tmp_path / "fab")]

    if "--ripup" in extra:
        # Retain the old case as an explicit removed-option refusal. It is not
        # a successful route variant and must not be counted as one.
        before = board.read_bytes()
        doc, _, code = _run([*argv, "--dry-run"])
        assert code == 2 and doc["error"]["code"] == "E_USAGE", doc
        assert doc["error"]["details"]["parameter"] == "--ripup"
        assert board.read_bytes() == before
        return

    if path in WRITE_COMMANDS:
        plan, err, _ = _run([*argv, "--dry-run"])
        assert plan is not None, f"no envelope from dry run: {err[:300]}"
        planned(plan)
        token = plan["data"]["confirm_token"]
        argv += ["--confirm", token]

    doc, err, code = _run(argv)
    # Strict mode writes the mismatch to stderr and emits nothing, so an empty
    # stdout here is the failure worth reading.
    assert "contract violation" not in err, f"{path} {' '.join(extra)}: {err.strip()}"
    assert doc is not None, f"no envelope: rc={code} {err[:300]}"
    assert doc["ok"] is True, doc
