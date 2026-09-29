"""``sch relink`` reads a board back without pcbnew: its links and its census.

The payload this replaces, `payload/verify_links.py`, had pcbnew load the
board and report each footprint's link, the references carrying two links,
and how many footprints, tracks, zones, drawings and nets it holds -- what
`sch relink` compares before and after its edit. On every board KiCad ships,
this reads the same, count for count and link for link.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from payload_reference import DEMO_BOARDS, differences, kicad_env, needs_payload

from kicad_cli.native import links

MINI = Path(__file__).resolve().parent / "fixtures" / "mini" / "mini.kicad_pcb"


def payload(board: Path) -> dict:
    # A launch fails now and then on Windows with no output; that is not the
    # board's doing, and the reference is worth a second try.
    for _ in range(3):
        doc = kicad_env.run_payload("verify_links", ["--board", str(board)])
        if doc.get("ok"):
            return doc["data"]
    raise AssertionError(f"the payload could not read {board.name}: {doc}")


@needs_payload
@pytest.mark.parametrize("board", DEMO_BOARDS, ids=lambda p: p.stem)
def test_a_board_reads_back_as_pcbnew_reads_it(board: Path) -> None:
    theirs = payload(board)
    ours = links.read(board)
    for key in ("links", "duplicate_references", "census"):
        assert differences(theirs[key], ours[key], key) == []


def test_a_link_is_spelled_as_pcbnew_spells_it() -> None:
    assert links.path_string("/a/b") == "/a/b"
    assert links.path_string("/a/b/") == "/a/b"
    assert links.path_string("a//b") == "/a/b"
    assert links.path_string("") == ""


def test_the_census_needs_no_kicad() -> None:
    """The fixture is hand-written: a net table of two nets and nothing on
    the board. pcbnew counts the unconnected net as a net."""
    read = links.read(MINI)
    assert read["links"] == {}
    assert read["duplicate_references"] == []
    assert read["census"] == {"footprints": 0, "tracks": 0, "zones": 0, "drawings": 0, "nets": 3}
