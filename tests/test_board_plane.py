"""``board plane`` finds return-path breaks, which DRC never reports.

The trap worth a test of its own: KiCad's copper layer IDs are not the physical
stack order. On a four-layer board F.Cu is 0, B.Cu is 2, In1.Cu is 4 and In2.Cu
is 6, so sorting by ID puts the bottom layer second. Every "adjacent layer" then
points at the wrong copper and the whole analysis is confidently wrong.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from kicad_demos import DEMOS, SKIP_REASON

REPO = Path(__file__).resolve().parents[1]


def run(*argv: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "kicad_cli.main", *argv, "--compact"],
        capture_output=True,
        cwd=REPO,
        timeout=3600,
    )
    out = proc.stdout.decode("utf-8", "replace")
    for line in out.splitlines():
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(f"no envelope: {out[:300]}{proc.stderr.decode()[:300]}")


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_the_stack_is_reported_in_physical_order(tmp_path: Path) -> None:
    """F.Cu first, inner layers in number order, B.Cu last.

    Sorting the same layers by their KiCad ids gives F.Cu, B.Cu, In1.Cu, In2.Cu
    -- the bottom layer second -- so this ordering is the thing standing between
    the analysis and a confidently wrong answer about which copper is adjacent
    to what.
    """
    work = tmp_path / "cm5"
    shutil.copytree(DEMOS / "cm5_minima", work)
    board = next(work.glob("*.kicad_pcb"))

    stack = run("board", "plane", "--board", str(board))["data"]["stackup"]
    assert stack[0] == "F.Cu"
    assert stack[-1] == "B.Cu", "B.Cu must be last; by layer id it would come second"
    inner = stack[1:-1]
    assert inner == [f"In{i}.Cu" for i in range(1, len(inner) + 1)], stack


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_every_layer_gets_an_adjacent_reference(tmp_path: Path) -> None:
    """An earlier version classified layers into planes and signals by pour
    coverage and analysed only the signals. On a board where every layer has a
    pour -- an ordinary four-layer board -- that left nothing to analyse and the
    command returned success having checked nothing."""
    work = tmp_path / "cm5"
    shutil.copytree(DEMOS / "cm5_minima", work)
    board = next(work.glob("*.kicad_pcb"))

    r = run("board", "plane", "--board", str(board))
    assert r["ok"] is True, r
    d = r["data"]
    assert d["samples"] > 0, "nothing was sampled, so nothing was checked"
    assert set(d["reference_map"]) == set(d["stackup"]), "a layer was left without a reference"
    stack = d["stackup"]
    assert d["reference_map"]["F.Cu"] == stack[1]
    assert d["reference_map"]["B.Cu"] == stack[-2], "the bottom layer references the one above it"
    for i, layer in enumerate(stack[:-1]):
        assert d["reference_map"][layer] == stack[i + 1], f"{layer} references a non-adjacent layer"
    assert 0.0 <= d["backed_fraction"] <= 1.0


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_gaps_are_grouped_by_place(tmp_path: Path) -> None:
    """One hole in a plane hits every trace crossing it. Reporting each trace
    separately asks the reader to investigate the same hole many times."""
    work = tmp_path / "cm5"
    shutil.copytree(DEMOS / "cm5_minima", work)
    board = next(work.glob("*.kicad_pcb"))

    d = run("board", "plane", "--board", str(board))["data"]
    if not d["segments_without_reference"]:
        pytest.skip("this board has no gaps to group")
    assert d["hotspots"], "gaps were found but not grouped"
    assert len(d["hotspots"]) <= d["segments_without_reference"]
    assert sum(c["segments"] for c in d["hotspots"]) <= d["segments_without_reference"]
    for c in d["hotspots"]:
        assert c["nets"], "a hotspot with no nets explains nothing"
        assert len(c["near_mm"]) == 2
