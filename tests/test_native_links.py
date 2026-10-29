"""``project links repair`` reads a board back without pcbnew: its links and its census.

The payload this replaces, `payload/verify_links.py`, had pcbnew load the
board and report each footprint's link, the references carrying two links,
and how many footprints, tracks, zones, drawings and nets it holds -- what
`project links repair` compares before and after its edit. On every board KiCad ships,
this reads the same, count for count and link for link.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from native_process import payload as sdk_payload
from payload_reference import DEMO_BOARDS, differences, needs_payload

from kicad_cli.native import links

MINI = Path(__file__).resolve().parent / "fixtures" / "mini" / "mini.kicad_pcb"


def payload(board: Path) -> dict:
    doc = sdk_payload("verify_links", ["--board", str(board)])
    assert doc.get("ok"), doc
    return doc["data"]


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
