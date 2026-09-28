"""``board plane`` moved off pcbnew: the old implementation is the reference.

Every field must match the payload's on every board KiCad ships -- coverage,
the reference map, every gap and split in its order, the nearest stitching
capacitor and its distance. The plane check samples a point every half
millimetre along every track and asks which pour it lies in, so this is also
the widest test there is of `Polygon.contains` against pcbnew: tens of
thousands of points per board, some of them exactly on a pour's edge.
"""

from __future__ import annotations

import pytest
from payload_reference import DEMO_BOARDS, assert_same, kicad_env, native, needs_payload


def single_layer_board(tmp_path):
    board = tmp_path / "one.kicad_pcb"
    board.write_text(
        '(kicad_pcb (version 20260206) (generator "t")\n'
        '\t(layers (0 "F.Cu" signal) (25 "Edge.Cuts" user))\n)\n',
        encoding="utf-8",
    )
    return board


def test_a_single_layer_board_has_no_plane_to_check(tmp_path):
    """The payload never reached this refusal: pcbnew will not load a board
    with one copper layer, returns nothing, and the payload died on the next
    line. Refused here the way the payload meant to refuse it."""
    doc = native("board", "plane", "--board", str(single_layer_board(tmp_path)))
    assert doc["ok"] is False
    assert doc["error"]["code"] == "E_VALIDATION"
    assert doc["error"]["details"] == {"copper_layers": 1, "write_state": "not_started"}


@needs_payload
@pytest.mark.parametrize("board", DEMO_BOARDS, ids=lambda p: p.name)
def test_the_native_plane_check_says_exactly_what_the_payload_said(board):
    old = kicad_env.run_payload("plane", ["--board", str(board)], timeout=3600)
    assert_same(old, native("board", "plane", "--board", str(board)))


@needs_payload
@pytest.mark.parametrize("step", ["0.25", "2"])
def test_the_sample_step_is_honoured_the_same_way(step):
    """The step changes every count; one board at two steps is enough to show
    it reaches the sampling rather than stopping at the argument parser."""
    board = next(b for b in DEMO_BOARDS if b.name == "pic_programmer.kicad_pcb")
    old = kicad_env.run_payload("plane", ["--board", str(board), "--step", step], timeout=3600)
    assert_same(old, native("board", "plane", "--board", str(board), "--step", step))
