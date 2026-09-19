"""What the router says when it has stopped making progress.

`repair` only finds paths through the gaps in existing copper, and what blocks
it is that copper, so once it stalls it stalls for good. "Repeat until it stops
improving" is true and stops one sentence short: an agent following it runs
three passes, sees no change, and stops holding a board that cannot be built.

Measured on a 15-part ATmega328P: `repair` stuck at 1 unconnected across three
passes, `--mode full` routed all 35. The advice is a note rather than an
automatic mode switch, because `full` deletes every existing track including
hand-drawn ones.

It lives in `kicad_lib` beside `err_count`, whose comment asks for exactly
this -- shared judgement in one place rather than restated per script. That it
needs neither KiCad nor pcbnew is why it can be tested at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "kicad_cli" / "payload"))

from kicad_lib import progress_note  # noqa: E402


def test_a_stalled_repair_names_the_next_command():
    note = progress_note("repair", before_un=1, after_un=1)
    assert "--mode full" in note
    assert "1" in note


def test_a_repair_that_is_still_making_progress_does_not_cry_wolf():
    """Three left out of eight is going fine; telling it to wipe the board now
    would throw away the five it just routed."""
    note = progress_note("repair", before_un=8, after_un=3)
    assert "--mode full" not in note


def test_a_finished_board_is_not_told_to_re_route_itself():
    note = progress_note("repair", before_un=4, after_un=0)
    assert "--mode full" not in note


def test_nothing_to_do_is_not_a_stall():
    """Zero before and zero after is a routed board, not a stuck one."""
    assert "--mode full" not in progress_note("repair", before_un=0, after_un=0)


def test_a_repair_that_went_backwards_still_gets_the_advice():
    """More unconnected than it started with is at least as stuck."""
    assert "--mode full" in progress_note("repair", before_un=2, after_un=3)


def test_full_mode_is_not_told_to_run_full_mode():
    """It is already the fallback; recommending it to itself is a loop."""
    assert "--mode full" not in progress_note("full", before_un=5, after_un=5)


def test_rewidth_mode_is_not_told_to_run_full_mode():
    assert "--mode full" not in progress_note("rewidth", before_un=5, after_un=5)
