"""Reference designators you can actually read.

The last class of problem this chain left on a finished board. Every board it
made came out with silkscreen warnings, and the reference designator was one
side of all of them: six of the text over another footprint's silkscreen,
fourteen of it over a pad.

Not purely cosmetic. A refdes you cannot read is a part you cannot hand-place,
rework, or check against a BOM, and silk over a pad is clipped by the solder
mask opening, so it prints as half a character.

The geometry is the same problem `board place` solves, one level down: boxes
that must not overlap, inside a boundary. So it is tested the same way -- as
arithmetic on boxes, with no KiCad -- plus the end-to-end run that shows the
warnings actually go away.
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
sys.path.insert(0, str(REPO / "kicad_cli" / "payload"))

import silkscreen as S  # noqa: E402

needs_kicad = pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)

NETLIST = """(export (version "E")
  (components
    (comp (ref "R1") (value "10k") (footprint "Resistor_SMD:R_0603_1608Metric"))
    (comp (ref "R2") (value "10k") (footprint "Resistor_SMD:R_0603_1608Metric"))
    (comp (ref "C1") (value "100nF") (footprint "Capacitor_SMD:C_0603_1608Metric"))
    (comp (ref "C2") (value "100nF") (footprint "Capacitor_SMD:C_0603_1608Metric")))
  (nets
    (net (code 1) (name "A") (node (ref "R1") (pin "1")) (node (ref "C1") (pin "1")))
    (net (code 2) (name "B") (node (ref "R2") (pin "1")) (node (ref "C2") (pin "1")))
    (net (code 3) (name "C") (node (ref "R1") (pin "2")) (node (ref "R2") (pin "2")))))
"""


def entry(layer=0, box=(0.0, 0.0, 2.0, 1.0), anchor=(0.0, 0.0, 2.0, 1.0)):
    return {"ref": None, "name": "R1", "layer": layer, "box": box, "anchor": anchor, "home": (0, 0)}


# --- the geometry -------------------------------------------------------------


def test_text_over_a_pad_is_a_clash_whatever_layer_the_text_is_on():
    """Silk is clipped by the solder mask, and the mask opening follows the
    copper -- so the layer the text sits on does not save it."""
    assert S.clashes(entry(), (0.0, 0.0, 2.0, 1.0), [(1.0, 0.5, 3.0, 2.0)], [], [], 0.15)


def test_text_clear_of_everything_is_not_a_clash():
    assert not S.clashes(entry(), (0.0, 0.0, 2.0, 1.0), [(9.0, 9.0, 10.0, 10.0)], [], [], 0.15)


def test_silkscreen_on_the_other_side_of_the_board_is_not_in_the_way():
    """Top-layer text cannot collide with bottom-layer silkscreen."""
    other_side = [(7, (0.0, 0.0, 2.0, 1.0))]
    assert not S.clashes(entry(layer=5), (0.0, 0.0, 2.0, 1.0), [], other_side, [], 0.15)
    same_side = [(5, (0.0, 0.0, 2.0, 1.0))]
    assert S.clashes(entry(layer=5), (0.0, 0.0, 2.0, 1.0), [], same_side, [], 0.15)


def test_a_reference_is_not_counted_as_blocking_itself():
    """Every reference is in the obstacle list, including the one being moved."""
    mine = entry()
    assert not S.clashes(mine, mine["box"], [], [], [mine], 0.15)


def test_clearance_is_respected_not_just_bare_overlap():
    """Touching is not clear: KiCad's silkscreen check wants a gap.

    Same layer on both sides, because a clearance question only arises between
    things that share one -- which the layer test above is about.
    """
    near = (2.05, 0.0, 4.0, 1.0)  # 0.05 mm away, inside the 0.15 mm clearance
    assert S.clashes(entry(layer=5), (0.0, 0.0, 2.0, 1.0), [], [(5, near)], [], 0.15)
    far = (2.5, 0.0, 4.0, 1.0)  # 0.5 mm away, clear
    assert not S.clashes(entry(layer=5), (0.0, 0.0, 2.0, 1.0), [], [(5, far)], [], 0.15)


# --- staying on the board -----------------------------------------------------


def test_text_pushed_past_the_board_edge_is_refused():
    """Silk outside Edge.Cuts breaks no rule and prints nothing. The first
    version put J2 and Y1 at y=51 on a board that ends at 48.75: DRC clean,
    and no text on the board."""
    bounds = (0.0, 0.0, 40.0, 40.0)
    assert S.inside((1.0, 1.0, 3.0, 2.0), bounds)
    assert not S.inside((1.0, 39.5, 3.0, 41.0), bounds)
    assert not S.inside((-1.0, 1.0, 1.0, 2.0), bounds)


def test_a_board_with_no_outline_does_not_constrain_anything():
    assert S.inside((-100.0, -100.0, 100.0, 100.0), None)


def test_relocation_keeps_the_text_inside_the_board():
    """A part in the corner must not have its label pushed off the edge."""
    crowded = entry(box=(0.0, 0.0, 2.0, 1.0), anchor=(0.0, 0.0, 2.0, 1.0))
    bounds = (0.0, 0.0, 20.0, 20.0)
    position, box = S.relocate(crowded, [], [], [], 0.15, bounds)
    assert position is not None
    assert S.inside(box, bounds), box


def test_a_reference_with_nowhere_to_go_is_reported_rather_than_forced():
    """Boxed in on every side and hard against the boundary. Leaving it where
    it was and naming it beats moving it somewhere wrong."""
    crowded = entry(box=(0.0, 0.0, 2.0, 1.0), anchor=(0.0, 0.0, 2.0, 1.0))
    wall = [(-50.0, -50.0, 50.0, 50.0)]
    position, _box = S.relocate(crowded, wall, [], [], 0.15, (0.0, 0.0, 3.0, 2.0))
    assert position is None


# --- end to end ---------------------------------------------------------------


def run(argv: list[str]) -> tuple[dict | None, int]:
    env = dict(os.environ, KICAD_CLI_STRICT="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "kicad_cli.main", *argv, "--compact"],
        capture_output=True,
        cwd=REPO,
        env=env,
        timeout=3600,
    )
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith("{"):
            return json.loads(line), proc.returncode
    return None, proc.returncode


def confirmed(argv: list[str]) -> dict:
    plan, _ = run([*argv, "--dry-run"])
    assert plan is not None and plan["error"]["code"] == "E_CONFIRMATION_REQUIRED", plan
    doc, _ = run([*argv, "--confirm", plan["error"]["details"]["confirm_token"]])
    return doc


def build(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    netlist = directory / "circuit.net"
    netlist.write_text(NETLIST, encoding="utf-8")
    board = directory / "x.kicad_pcb"
    doc = confirmed(["board", "from-netlist", "--netlist", str(netlist), "--out", str(board)])
    assert doc["ok"] is True, doc
    confirmed(["board", "place", "--board", str(board)])
    return board


def silk_warnings(board: Path) -> int:
    doc, _ = run(["board", "drc", "--board", str(board), "--limit", "200"])
    assert doc["ok"] is True, doc
    return sum(1 for v in doc["data"]["violations"] if str(v["type"]).startswith("silk"))


@needs_kicad
def test_a_dry_run_names_the_crowded_references_and_moves_nothing(tmp_path):
    board = build(tmp_path)
    before = board.read_bytes()
    doc, _ = run(["board", "silkscreen", "--board", str(board), "--dry-run"])
    assert doc["error"]["code"] == "E_CONFIRMATION_REQUIRED", doc
    preview = doc["error"]["details"]["preview"]
    assert preview["references"] >= 4
    assert isinstance(preview["crowded_refs"], list)
    assert board.read_bytes() == before


@needs_kicad
def test_moving_the_references_clears_the_silkscreen_warnings(tmp_path):
    """The claim, measured with KiCad's own DRC rather than asserted: on the
    15-part board this took 20 silkscreen warnings to zero."""
    board = build(tmp_path)
    confirmed(["board", "route", "--board", str(board), "--mode", "repair"])
    before = silk_warnings(board)

    doc = confirmed(["board", "silkscreen", "--board", str(board)])
    assert doc["ok"] is True, doc
    data = doc["data"]
    assert data["moved"] + len(data["stuck"]) == data["crowded"], data

    after = silk_warnings(board)
    assert after <= before, {"before": before, "after": after, "moves": data["moves"]}
    if data["moved"]:
        assert data["max_move_mm"] > 0 and data["mean_move_mm"] > 0


@needs_kicad
def test_it_reports_how_far_the_text_had_to_go(tmp_path):
    """Legible is the floor. A refdes 6 mm from its own part in a row of 0603s
    is readable and still tells you nothing, so the distance is reported."""
    board = build(tmp_path)
    doc = confirmed(["board", "silkscreen", "--board", str(board)])
    data = doc["data"]
    if data["moved"]:
        assert data["max_move_mm"] >= data["mean_move_mm"]
        assert str(data["mean_move_mm"]) in data["note"] or "mm" in data["note"]
    else:
        assert data["max_move_mm"] is None


@needs_kicad
def test_running_it_twice_finds_nothing_left_to_do(tmp_path):
    """It converges. A pass that keeps finding work on an unchanged board
    would mean the move and the check disagree about what counts as clear."""
    board = build(tmp_path)
    confirmed(["board", "silkscreen", "--board", str(board)])
    doc, _ = run(["board", "silkscreen", "--board", str(board), "--dry-run"])
    if doc["ok"] is False and doc["error"]["code"] == "E_CONFIRMATION_REQUIRED":
        second = confirmed(["board", "silkscreen", "--board", str(board)])
        assert second["data"]["moved"] == 0, second["data"]["moves"]
