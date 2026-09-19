"""Placement, and the claim that it improved something.

`board from-netlist` lays out a grid ordered by reference designator, which is
a position for every part and a layout for none of them: the netlist says which
parts belong together and nothing read it. This is the command that does.

Most of what follows needs no KiCad. The placer is arithmetic over boxes and
nets, so it is tested as arithmetic -- which also means the one property that
matters most, that running it twice gives the same board, can be checked
exactly rather than eyeballed.

The last test is the one that earns the command: it routes a grid board and an
auto-placed board built from the same netlist, and compares the copper.
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

import pcb_autoplace as A  # noqa: E402

needs_kicad = pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)

NETLIST = """(export (version "E")
  (components
    (comp (ref "R1") (value "10k") (footprint "Resistor_SMD:R_0805_2012Metric"))
    (comp (ref "R2") (value "10k") (footprint "Resistor_SMD:R_0805_2012Metric"))
    (comp (ref "R3") (value "10k") (footprint "Resistor_SMD:R_0805_2012Metric"))
    (comp (ref "R4") (value "10k") (footprint "Resistor_SMD:R_0805_2012Metric")))
  (nets
    (net (code 1) (name "A") (node (ref "R1") (pin "1")) (node (ref "R4") (pin "1")))
    (net (code 2) (name "B") (node (ref "R2") (pin "1")) (node (ref "R3") (pin "1")))
    (net (code 3) (name "C") (node (ref "R1") (pin "2")) (node (ref "R3") (pin "2")))))
"""


def part(ref, x, y, half=1.0, fixed=False):
    """A stand-in footprint: a square courtyard centred on its own origin."""
    return {
        "ref": ref,
        "fp": None,
        "x": float(x),
        "y": float(y),
        "fixed": fixed,
        "dx": 0.0,
        "dy": 0.0,
        "hw": half,
        "hh": half,
    }


def pair(a="A", b="B"):
    parts = {a: part(a, 0, 0), b: part(b, 40, 0)}
    nets = {"N": [{"ref": a, "ox": 0.0, "oy": 0.0}, {"ref": b, "ox": 0.0, "oy": 0.0}]}
    return parts, nets


# --- the metric itself -------------------------------------------------------


def test_wirelength_is_the_half_perimeter_of_each_net():
    """The number the command is judged by is checked against one done by hand."""
    parts = {"A": part("A", 0, 0), "B": part("B", 3, 4)}
    nets = {"N": [{"ref": "A", "ox": 0.0, "oy": 0.0}, {"ref": "B", "ox": 0.0, "oy": 0.0}]}
    assert A.hpwl(parts, nets) == pytest.approx(7.0)  # width 3 + height 4


def test_wirelength_counts_where_the_pad_is_not_where_the_part_is():
    """A pad offset is most of the distance on a small part; ignoring it would
    make the metric disagree with the copper it is supposed to predict."""
    parts = {"A": part("A", 0, 0), "B": part("B", 10, 0)}
    nets = {"N": [{"ref": "A", "ox": 1.0, "oy": 0.0}, {"ref": "B", "ox": -1.0, "oy": 0.0}]}
    assert A.hpwl(parts, nets) == pytest.approx(8.0)


# --- the placer ---------------------------------------------------------------


def test_two_connected_parts_end_up_next_to_each_other():
    parts, nets = pair()
    before = A.hpwl(parts, nets)
    A.run(parts, nets, iterations=200, clearance=0.5, bounds=None)
    assert A.hpwl(parts, nets) < before / 4


def test_a_fixed_part_does_not_move_and_the_other_comes_to_it():
    """Locking a connector to the board edge is how a person states an intent
    the netlist cannot. Overruling it would make the command unusable on any
    board that has one."""
    parts, nets = pair()
    parts["A"]["fixed"] = True
    A.run(parts, nets, iterations=200, clearance=0.5, bounds=None)
    assert (parts["A"]["x"], parts["A"]["y"]) == (0.0, 0.0)
    assert parts["B"]["x"] < 5.0


def test_the_same_board_twice_gives_the_same_placement():
    """Without this an agent cannot tell a real change from a re-run."""
    results = []
    for _ in range(2):
        parts, nets = pair()
        parts["C"] = part("C", 7, 9)
        nets["M"] = [{"ref": "C", "ox": 0.0, "oy": 0.0}, {"ref": "A", "ox": 0.0, "oy": 0.0}]
        A.run(parts, nets, iterations=120, clearance=0.5, bounds=None)
        results.append({r: (round(p["x"], 6), round(p["y"], 6)) for r, p in parts.items()})
    assert results[0] == results[1]


def test_parts_are_pushed_apart_until_their_courtyards_clear():
    parts = {"A": part("A", 0, 0), "B": part("B", 0.1, 0.1)}
    nets = {"N": [{"ref": "A", "ox": 0.0, "oy": 0.0}, {"ref": "B", "ox": 0.0, "oy": 0.0}]}
    A.run(parts, nets, iterations=60, clearance=0.5, bounds=None)
    clash, _outside, min_gap = A.inspect(parts, None)
    assert clash == []
    assert min_gap >= 0.0


def test_a_huge_net_does_not_drag_everything_into_a_heap():
    """Ground reaches nearly every part. Treating it as an attraction would
    place the whole board on top of itself, so it is excluded by fanout -- the
    same reason a real layout solves ground with a pour and not with traces."""
    parts = {r: part(r, i * 10, 0) for i, r in enumerate("ABCDEFGHIJ")}
    ground = {"GND": [{"ref": r, "ox": 0.0, "oy": 0.0} for r in parts]}
    assert len(ground["GND"]) > A.FANOUT_CAP
    before = {r: (p["x"], p["y"]) for r, p in parts.items()}
    A.run(parts, nets=ground, iterations=50, clearance=0.5, bounds=None)
    assert {r: (p["x"], p["y"]) for r, p in parts.items()} == before


def test_placement_stays_inside_the_board_outline():
    """Outside Edge.Cuts is not a board, however short the wires are."""
    parts = {"A": part("A", 0, 0), "B": part("B", 60, 60)}
    nets = {"N": [{"ref": "A", "ox": 0.0, "oy": 0.0}, {"ref": "B", "ox": 0.0, "oy": 0.0}]}
    bounds = (0.0, 0.0, 20.0, 20.0)
    A.run(parts, nets, iterations=100, clearance=0.5, bounds=bounds)
    _clash, outside, _gap = A.inspect(parts, bounds)
    assert outside == []


# --- what gets reported -------------------------------------------------------


def test_touching_the_requested_margin_exactly_is_not_called_a_collision():
    """This was a real defect: the check folded --clearance into the overlap
    test, so every pair the legaliser had separated *correctly* -- to exactly
    the margin asked for -- came back as a courtyard clash. Five of them on the
    first board it ran on, none of which overlapped.
    """
    parts = {"A": part("A", 0, 0, half=1.0), "B": part("B", 2.5, 0, half=1.0)}
    clash, _outside, min_gap = A.inspect(parts, None)
    assert clash == []
    assert min_gap == pytest.approx(0.5)


def test_a_real_overlap_is_still_called_a_collision():
    parts = {"A": part("A", 0, 0, half=1.0), "B": part("B", 1.5, 0, half=1.0)}
    clash, _outside, min_gap = A.inspect(parts, None)
    assert clash == [["A", "B"]]
    assert min_gap < 0


def test_a_part_outside_the_outline_is_named():
    parts = {"A": part("A", 50, 5, half=1.0)}
    _clash, outside, _gap = A.inspect(parts, (0.0, 0.0, 20.0, 20.0))
    assert outside == ["A"]


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
    return board


def copper_mm(board: Path) -> float:
    """Total routed track length, read back through the tool rather than claimed."""
    doc, _ = run(["board", "audit", "--board", str(board)])
    assert doc["ok"] is True, doc
    return doc["data"]["copper_mm"]


@needs_kicad
def test_a_dry_run_reports_the_wirelength_and_moves_nothing(tmp_path):
    board = build(tmp_path)
    before = board.read_bytes()
    doc, _ = run(["board", "place", "--board", str(board), "--dry-run"])
    assert doc["error"]["code"] == "E_CONFIRMATION_REQUIRED", doc
    preview = doc["error"]["details"]["preview"]
    assert preview["hpwl_before_mm"] > 0
    assert preview["movable"] == 4
    assert board.read_bytes() == before, "a dry run must not move a part"


@needs_kicad
def test_placing_shortens_the_wirelength_and_leaves_no_overlap(tmp_path):
    board = build(tmp_path)
    doc = confirmed(["board", "place", "--board", str(board)])
    assert doc["ok"] is True, doc
    data = doc["data"]
    assert data["improved"] is True
    assert data["hpwl_after_mm"] < data["hpwl_before_mm"]
    assert data["courtyard_clash"] == [], data["courtyard_clash"]
    assert data["outside_outline"] == []
    assert data["min_courtyard_gap_mm"] >= 0


@needs_kicad
def test_drc_is_the_referee_and_it_reports_what_it_cost(tmp_path):
    """Packing parts closer is the point, and it has a price in silkscreen.

    `board route` had to learn this the hard way -- it reported `ok` while
    trace width compliance went from 100% to 0%. A write command that makes
    something worse must say which something.
    """
    board = build(tmp_path)
    doc = confirmed(["board", "place", "--board", str(board)])
    verify = doc["data"]["verify"]
    assert verify["ran"] is True
    assert verify["oracle"] == "kicad-cli pcb drc"
    assert verify["drc_after"].get("error", 0) <= verify["drc_before"].get("error", 0)
    assert "warnings_added" in verify


@needs_kicad
def test_a_kept_part_stays_where_it_was(tmp_path):
    board = build(tmp_path)
    plan, _ = run(["board", "place", "--board", str(board), "--keep", "R1", "--dry-run"])
    assert plan["error"]["details"]["preview"]["fixed"] == 1
    doc = confirmed(["board", "place", "--board", str(board), "--keep", "R1"])
    assert doc["ok"] is True, doc
    assert all(move["ref"] != "R1" for move in doc["data"]["moves"]), doc["data"]["moves"]


@needs_kicad
def test_keeping_a_part_that_is_not_on_the_board_is_refused(tmp_path):
    board = build(tmp_path)
    doc, code = run(["board", "place", "--board", str(board), "--keep", "R9", "--dry-run"])
    assert doc["error"]["code"] == "E_NOT_FOUND", doc
    assert "R9" in json.dumps(doc["error"])
    assert code != 0


@needs_kicad
def test_an_auto_placed_board_routes_with_less_copper_than_the_grid(tmp_path):
    """The whole justification for the command, measured rather than argued.

    Both boards come from the same netlist and are routed by the same router;
    the only difference is that one of them was placed. If this does not hold,
    the wirelength metric is not measuring anything worth having.
    """
    grid = build(tmp_path / "grid")
    placed = build(tmp_path / "placed")

    doc = confirmed(["board", "place", "--board", str(placed)])
    assert doc["ok"] is True, doc

    for name, board in (("grid", grid), ("placed", placed)):
        routed = confirmed(["board", "route", "--board", str(board), "--mode", "repair"])
        assert routed["ok"] is True, (name, routed)
        assert routed["data"]["unconnected_after"] == 0, (name, routed["data"])

    grid_copper, placed_copper = copper_mm(grid), copper_mm(placed)
    assert placed_copper < grid_copper, {"grid": grid_copper, "placed": placed_copper}
