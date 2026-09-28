"""``board audit`` moved off pcbnew: the old implementation is the reference.

The audit now runs in this process on the file (`kicad_cli/native/audit.py`).
Until the payload it replaced is deleted along with SWIG, that payload is the
most exacting reference there is: same inputs, and every field of the output
must match -- numbers, lists and their order, the findings' wording, and the
error envelope when a board has no project file. Anything the port changes
on purpose belongs in a later change that says so.
"""

from __future__ import annotations

import pytest
from payload_reference import DEMO_BOARDS, assert_same, kicad_env, native, needs_payload


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
    doc = native("board", "audit", "--board", str(board))
    assert doc["ok"] is False
    assert doc["error"]["code"] == "E_VALIDATION", doc
    assert reason in doc["error"]["details"]["reason"], doc


@needs_payload
@pytest.mark.parametrize("board", DEMO_BOARDS, ids=lambda p: p.name)
def test_the_native_audit_says_exactly_what_the_payload_said(board):
    old = kicad_env.run_payload("audit", ["--board", str(board)], timeout=1800)
    assert_same(old, native("board", "audit", "--board", str(board)))
