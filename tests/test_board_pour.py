"""Copper pours, and the router finding out they exist.

`board stitch` joins the islands of a pour; `board plane` audits what sits
under each track. Both assume a pour exists and neither could make one, so a
board out of this chain had zero zones and `board plane` returned FAIL with
`backed_fraction: 0.0` -- every one of 170 track segments with no copper
beneath it, on a board DRC was perfectly happy with. Return current has to go
around, and the loop it encloses is what radiates.

The router only takes a pour into account when asked: `--use-planes` is off by
default because turning it on regresses KiCad's own `interf_u` demo. The last
three tests cover both sides of that switch and the copper it saves.
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

import pour as P  # noqa: E402

needs_kicad = pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)

NETLIST = """(export (version "E")
  (components
    (comp (ref "R1") (value "10k") (footprint "Resistor_SMD:R_0805_2012Metric"))
    (comp (ref "R2") (value "10k") (footprint "Resistor_SMD:R_0805_2012Metric"))
    (comp (ref "R3") (value "10k") (footprint "Resistor_SMD:R_0805_2012Metric")))
  (nets
    (net (code 1) (name "GND")
      (node (ref "R1") (pin "2"))
      (node (ref "R2") (pin "2"))
      (node (ref "R3") (pin "2")))
    (net (code 2) (name "SIG")
      (node (ref "R1") (pin "1"))
      (node (ref "R2") (pin "1")))
    (net (code 3) (name "SIG2")
      (node (ref "R2") (pin "1"))
      (node (ref "R3") (pin "1")))))
"""


class FakeBox:
    def __init__(self, x, y, w, h):
        self._x, self._y, self._w, self._h = x, y, w, h

    def GetWidth(self):
        return self._w

    def GetHeight(self):
        return self._h

    def GetX(self):
        return self._x

    def GetY(self):
        return self._y

    def GetRight(self):
        return self._x + self._w

    def GetBottom(self):
        return self._y + self._h


class FakePcbnew:
    @staticmethod
    def FromMM(v):
        return int(v * 1_000_000)

    @staticmethod
    def ToMM(v):
        return v / 1_000_000


class FakeBoard:
    def __init__(self, box):
        self._box = box

    def GetBoardEdgesBoundingBox(self):
        return self._box


def test_the_pour_is_inset_from_the_board_edge():
    """Copper at the board edge comes out exposed: the cut has tolerance."""
    mm = FakePcbnew.FromMM
    board = FakeBoard(FakeBox(mm(0), mm(0), mm(40), mm(30)))
    left, top, right, bottom = P.board_rect(FakePcbnew, board, 0.5)
    assert FakePcbnew.ToMM(left) == pytest.approx(0.5)
    assert FakePcbnew.ToMM(top) == pytest.approx(0.5)
    assert FakePcbnew.ToMM(right) == pytest.approx(39.5)
    assert FakePcbnew.ToMM(bottom) == pytest.approx(29.5)


def test_a_margin_wider_than_the_board_is_refused_rather_than_inverted():
    """Without the check the rectangle comes out with right < left, which is
    not a small pour -- it is a zone turned inside out."""
    mm = FakePcbnew.FromMM
    board = FakeBoard(FakeBox(mm(0), mm(0), mm(4), mm(4)))
    with pytest.raises(SystemExit):
        P.board_rect(FakePcbnew, board, 5.0)


def test_a_board_with_no_outline_has_nowhere_to_stop_pouring():
    board = FakeBoard(FakeBox(0, 0, 0, 0))
    with pytest.raises(SystemExit):
        P.board_rect(FakePcbnew, board, 0.5)


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


@needs_kicad
def test_a_pour_gives_the_plane_audit_something_to_find(tmp_path):
    board = build(tmp_path)
    before, _ = run(["board", "plane", "--board", str(board)])
    assert before["data"]["plane_layers"] == []

    doc = confirmed(["board", "pour", "--board", str(board), "--net", "GND", "--layer", "B.Cu"])
    assert doc["ok"] is True, doc
    assert doc["data"]["filled_mm2"] > 0, "a pour that filled nothing is not a pour"
    assert doc["data"]["net"] == "GND" and doc["data"]["layer"] == "B.Cu"

    after, _ = run(["board", "plane", "--board", str(board)])
    assert after["data"]["plane_layers"] == ["B.Cu"]


@needs_kicad
def test_a_dry_run_pours_nothing(tmp_path):
    board = build(tmp_path)
    before = board.read_bytes()
    doc, _ = run(
        ["board", "pour", "--board", str(board), "--net", "GND", "--layer", "B.Cu", "--dry-run"]
    )
    assert doc["error"]["code"] == "E_CONFIRMATION_REQUIRED", doc
    assert board.read_bytes() == before


@needs_kicad
def test_a_net_the_board_does_not_have_is_named(tmp_path):
    board = build(tmp_path)
    doc, code = run(
        ["board", "pour", "--board", str(board), "--net", "NOPE", "--layer", "B.Cu", "--dry-run"]
    )
    assert doc["error"]["code"] == "E_NOT_FOUND", doc
    assert "NOPE" in json.dumps(doc["error"], ensure_ascii=False)
    assert code != 0


@needs_kicad
def test_a_layer_that_is_not_copper_is_refused(tmp_path):
    """Silkscreen cannot carry a net, and filling it would be nonsense output
    rather than an error the caller can see."""
    board = build(tmp_path)
    doc, _ = run(
        [
            "board",
            "pour",
            "--board",
            str(board),
            "--net",
            "GND",
            "--layer",
            "F.Silkscreen",
            "--dry-run",
        ]
    )
    assert doc["error"]["code"] in ("E_VALIDATION", "E_NOT_FOUND"), doc


@needs_kicad
def test_pouring_the_same_net_on_the_same_layer_twice_is_refused(tmp_path):
    """Two stacked zones on one net is not twice the copper; it is a board
    nobody can reason about."""
    board = build(tmp_path)
    confirmed(["board", "pour", "--board", str(board), "--net", "GND", "--layer", "B.Cu"])
    doc, _ = run(
        ["board", "pour", "--board", str(board), "--net", "GND", "--layer", "B.Cu", "--dry-run"]
    )
    assert doc["error"]["code"] == "E_CONFLICT", doc


@needs_kicad
def test_routing_over_a_pour_keeps_the_board_connected(tmp_path):
    """The pour must survive the router, and the router must survive the pour.

    `--mode full` clears every track and routes from scratch with a filled
    zone already on the board; `fill_and_save` refills it afterwards. Neither
    step may leave a connection open.
    """
    board = build(tmp_path)
    confirmed(["board", "pour", "--board", str(board), "--net", "GND", "--layer", "B.Cu"])
    routed = confirmed(["board", "route", "--board", str(board), "--mode", "full"])
    assert routed["ok"] is True, routed
    assert routed["data"]["unconnected_after"] == 0, routed["data"]

    plane, _ = run(["board", "plane", "--board", str(board)])
    assert plane["data"]["plane_layers"] == ["B.Cu"], "the pour did not survive routing"


@needs_kicad
def test_the_router_ignores_the_pour_unless_asked(tmp_path):
    """Default behaviour is unchanged, and that is deliberate.

    Letting a pour carry its own net is a large win on a board from this
    chain and takes KiCad's own `interf_u` demo from 3 DRC errors to 5, where
    `--mode full` then rolls back and fails on a board that used to route. So
    it is a flag: off by default, and nothing that routes today changes.
    """
    board = build(tmp_path)
    confirmed(["board", "pour", "--board", str(board), "--net", "GND", "--layer", "B.Cu"])
    routed = confirmed(["board", "route", "--board", str(board), "--mode", "full"])
    assert routed["ok"] is True, routed
    assert routed["data"]["plane_nets"] == [], routed["data"]
    assert routed["data"]["unconnected_after"] == 0, routed["data"]


@needs_kicad
def test_use_planes_lets_the_pour_carry_its_own_net(tmp_path):
    """`mode_full` read plane nets from a `log` key nothing ever wrote, so the
    set was always empty and the whole path was dead: `escape_pins` skipped no
    pin, `fanout_planes` iterated nothing, `plane_served` always reported [].
    Ground was routed pad to pad with a ground plane sitting right there.

    `fanout` is the evidence: the count of pads given a via down to the plane,
    unreachable before. Measured on the 15-part board: copper 255.1 mm ->
    177.5 mm with both boards fully connected.
    """
    board = build(tmp_path)
    confirmed(["board", "pour", "--board", str(board), "--net", "GND", "--layer", "B.Cu"])
    routed = confirmed(["board", "route", "--board", str(board), "--mode", "full", "--use-planes"])
    assert routed["ok"] is True, routed
    assert routed["data"]["plane_nets"] == ["GND"], routed["data"]
    assert routed["data"]["plane_served"] == ["GND"], routed["data"]
    assert routed["data"]["unconnected_after"] == 0, routed["data"]


@needs_kicad
def test_leaving_ground_to_the_plane_costs_less_copper(tmp_path):
    """The reason the flag exists, measured rather than argued.

    Same netlist, same placer, same router, same pour. The only difference is
    whether ground pads reach the plane through a via or through a trace to
    every other ground pad.
    """
    off = build(tmp_path / "off")
    on = build(tmp_path / "on")
    for board in (off, on):
        confirmed(["board", "pour", "--board", str(board), "--net", "GND", "--layer", "B.Cu"])
    confirmed(["board", "route", "--board", str(off), "--mode", "full"])
    confirmed(["board", "route", "--board", str(on), "--mode", "full", "--use-planes"])

    copper = {}
    for name, board in (("off", off), ("on", on)):
        doc, _ = run(["board", "audit", "--board", str(board)])
        copper[name] = doc["data"]["copper_mm"]
        drc, _ = run(["board", "drc", "--board", str(board)])
        assert drc["data"]["unconnected_count"] == 0, (name, drc["data"])
    assert copper["on"] < copper["off"], copper
