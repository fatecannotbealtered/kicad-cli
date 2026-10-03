"""Zones filled by this tool (`zonefill.py`) held to KiCad's own fills of the
same zones (`tests/fixtures/zonefill/`, filled by KiCad 10): each zone's fill
in as many pieces as KiCad's, covering the same copper but for the error an
arc's chords allow; and nothing this tool's DRC finds in it that it does not
find in KiCad's."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kicad_cli.fileformat import drc, polygons, zonefill  # noqa: E402
from kicad_cli.fileformat.board import Board  # noqa: E402
from kicad_cli.fileformat.drc.settings import Settings  # noqa: E402

FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "zonefill" / "zonefill" / "zonefill.kicad_pcb"
)


def _filled():
    board = Board.load(FIXTURE)
    filler = zonefill.Filler(board, Settings.of(FIXTURE.with_suffix(".kicad_pro")))
    return board, filler


def _zones():
    board = Board.load(FIXTURE)
    return [(i, z.outlines[0][0]) for i, z in enumerate(board.zones) if not z.rule_area]


@pytest.mark.parametrize("index", [i for i, _ in _zones()], ids=[f"{p[0] // 10**6}-{p[1] // 10**6}"
                                                              for _, p in _zones()])  # fmt: skip
def test_a_zone_is_filled_as_kicad_fills_it(index):
    board, filler = _filled()
    zone = board.zones[index]
    ours = filler.fill(zone)
    for layer in zone.layers:
        theirs = polygons.union(zone.filled.get(layer, []))
        mine = ours.layers[layer]
        pieces = lambda rings: sum(1 for r in rings if polygons.area(r) > 0)  # noqa: E731
        assert pieces(mine) == pieces(theirs)
        # The copper only one of the two covers: arcs drawn in chords, which
        # KiCad and this tool place a little differently.
        differ = polygons.total_area(polygons.xor(theirs, mine)) / 1e12
        assert differ < 0.06 + 0.002 * polygons.total_area(theirs) / 1e12, differ


def test_a_fill_written_back_reads_as_written(tmp_path):
    board, filler = _filled()
    fills = [filler.fill(z) for z in board.zones if not z.rule_area]
    for fill in fills:
        zonefill.write(fill)
    out = tmp_path / FIXTURE.name
    board.document.save(out)
    again = Board.load(out)
    for fill, zone in zip(fills, [z for z in again.zones if not z.rule_area], strict=True):
        for layer in zone.layers:
            mine = fill.layers[layer]
            read = polygons.union(zone.filled.get(layer, []))
            assert polygons.total_area(polygons.xor(read, mine)) == 0


def test_drc_finds_in_this_fill_what_it_finds_in_kicads(tmp_path):
    """No clearance, short or island in this tool's fill that KiCad's has not."""
    work = tmp_path / "board"
    shutil.copytree(FIXTURE.parent, work)
    ours = work / FIXTURE.name
    board = Board.load(ours)
    filler = zonefill.Filler(board, Settings.of(ours.with_suffix(".kicad_pro")))
    for zone in board.zones:
        if not zone.rule_area:
            zonefill.write(filler.fill(zone))
    board.document.save(ours)

    def found(path):
        report = drc.check(path)
        return sorted((v.rule, v.message.split(";")[0]) for v in report.violations), len(
            report.unconnected
        )

    assert found(ours) == found(FIXTURE)
