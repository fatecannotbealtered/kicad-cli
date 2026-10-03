"""``board pour`` moved off pcbnew: the old payload is the reference.

Each demo board is copied twice. The payload pours a net over a layer of
one copy, dry run then confirmation, and the port the same net over the
same layer of the other. Then:

- the previews and the reports must match, field for field -- but the
  filled area, which this tool's zone filler makes a tenth of a percent
  different from pcbnew's;
- and this tool's DRC must find the same in the two boards: what pcbnew
  filled and what this tool filled are held to the same rules.
"""

from __future__ import annotations

import shutil
from collections import Counter
from pathlib import Path

import pytest
from payload_reference import DEMO_BOARDS, REPO, differences, kicad_env, native, needs_payload

from kicad_cli.fileformat import drc, polygons
from kicad_cli.fileformat.board import Board
from kicad_cli.native.pour import board_rect

# Boards the native filler fills in seconds: the large demos take minutes.
SMALL = [b for b in DEMO_BOARDS if b.stem in (
    "ecc83-pp", "ecc83-pp_v2", "interf_u", "pic_programmer", "kit-dev-coldfire-xilinx_5213",
    "StickHub", "multichannel_mixer",
)]  # fmt: skip


def copy_project(board: Path, into: Path) -> Path:
    into.mkdir(parents=True)
    for sibling in board.parent.glob(board.stem + ".kicad_*"):
        if sibling.suffix in (".kicad_pcb", ".kicad_pro", ".kicad_prl"):
            shutil.copy2(sibling, into / sibling.name)
    for extra in ("fp-lib-table",):
        if (board.parent / extra).exists():
            shutil.copy2(board.parent / extra, into / extra)
    return into / board.name


def pour_for(board: Path) -> tuple[str, str]:
    """The net most pads are on, over the first copper layer no zone of
    another net shares the pour's priority on: between two zones of one
    priority KiCad lets the larger uuid win, and a new zone's uuid is drawn
    at random -- by pcbnew and by this tool alike."""
    loaded = Board.load(board)
    counts = Counter(pad.net for fp in loaded.footprints for pad in fp.pads if pad.net)
    for net, _ in counts.most_common():
        for layer in loaded.copper_layers:
            zones = [z for z in loaded.zones if layer in z.layers and not z.rule_area]
            if any(z.net == net and z.layers[0] == layer for z in zones):
                continue  # a pour there already: refused
            if any(z.net != net and z.priority == 0 for z in zones):
                continue  # a coin toss
            return net, layer
    pytest.skip("no layer to pour on but where a coin toss decides")


def neutral(doc: dict, path: Path) -> dict:
    import json  # noqa: PLC0415

    return json.loads(json.dumps(doc, ensure_ascii=False).replace(json.dumps(str(path))[1:-1], "B"))


@needs_payload
@pytest.mark.parametrize("board", SMALL, ids=lambda p: p.stem)
def test_the_native_pour_reports_what_the_payload_reported(board, tmp_path):
    net, layer = pour_for(board)
    theirs = copy_project(board, tmp_path / "payload")
    ours = copy_project(board, tmp_path / "native")
    argv = ["--net", net, "--layer", layer]

    asked = kicad_env.run_payload("pour", ["--board", str(theirs), *argv])
    token = asked["error"]["details"]["confirm_token"]
    done = kicad_env.run_payload("pour", ["--board", str(theirs), *argv, "--confirm", token])
    new_asked = native("board", "pour", "--board", str(ours), *argv)
    token = new_asked["error"]["details"]["confirm_token"]
    new_done = native("board", "pour", "--board", str(ours), *argv, "--confirm", token)

    old_preview = neutral(asked["error"]["details"]["preview"], theirs)
    new_preview = neutral(new_asked["error"]["details"]["preview"], ours)
    assert differences(old_preview, new_preview) == []
    assert done.get("ok") and new_done.get("ok"), (done, new_done)
    old, new = neutral(done["data"], theirs), neutral(new_done["data"], ours)
    # Text on the copper is cut out by a box round it until the stroke font
    # is in: up to that box's area less copper than pcbnew's.
    boxes = sum(polygons.area(r) for r in _text_boxes(ours, layer)) / 1e12
    assert old["filled_mm2"] - boxes - 0.5 - 0.005 * old["filled_mm2"] <= new["filled_mm2"]
    assert new["filled_mm2"] <= old["filled_mm2"] * 1.005 + 0.5
    old.pop("filled_mm2"), new.pop("filled_mm2")
    assert differences(old, new) == []

    def found(path: Path):
        report = drc.check(path)
        return sorted((v.rule, v.message.split(";")[0]) for v in report.violations), len(
            report.unconnected
        )

    assert found(ours) == found(theirs)


def _text_boxes(board: Path, layer: str):
    from kicad_cli.fileformat import zonefill  # noqa: PLC0415

    loaded = Board.load(board)
    out = []
    for node in loaded.root.lists():
        on = node.find("layer")
        if node.head == "gr_text" and on is not None and on.value(1) == layer:
            ring = zonefill._text_box(node, node.value(1), None)
            if ring:
                out += polygons.offset([ring], 600_000)
    return polygons.union(out)


def test_the_pour_is_inset_from_the_board_edge(tmp_path):
    """Copper at the board edge comes out exposed: the cut has tolerance."""
    board = tmp_path / "b.kicad_pcb"
    board.write_bytes(BOARD.encode("utf-8"))
    left, top, right, bottom = board_rect(Board.load(board), 0.5)
    # The outline is drawn 0.1 mm wide: its box takes half of that outward.
    assert (left, top, right, bottom) == (450_000, 450_000, 39_550_000, 29_550_000)


BOARD = """(kicad_pcb (version 20241229) (generator "t") (general (thickness 1.6)) (paper "A4")
	(layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
	(gr_rect (start 0 0) (end 40 30) (stroke (width 0.1) (type solid)) (fill no)
		(layer "Edge.Cuts"))
	(footprint "R:R" (layer "F.Cu") (at 10 10)
		(property "Reference" "R1" (at 0 0 0) (layer "F.SilkS"))
		(pad "1" smd rect (at -1 0) (size 1 1) (layers "F.Cu") (net "GND"))
		(pad "2" smd rect (at 1 0) (size 1 1) (layers "F.Cu") (net "SIG"))
	)
)
"""


def test_a_pour_needs_no_kicad_and_fills_round_the_pads(tmp_path):
    board = tmp_path / "b.kicad_pcb"
    board.write_bytes(BOARD.encode("utf-8"))
    argv = ["board", "pour", "--board", str(board), "--net", "GND", "--layer", "F.Cu"]
    asked = native(*argv)
    done = native(*argv, "--confirm", asked["error"]["details"]["confirm_token"])
    assert done["ok"], done
    poured = Board.load(board)
    (zone,) = poured.zones
    assert zone.net == "GND" and zone.layers == ["F.Cu"] and zone.filled["F.Cu"]
    report = drc.check(board)
    assert [v for v in report.violations if v.rule in ("clearance", "shorting_items")] == []
    # Within the board's edge clearance (0.5 mm), less two pads' worth.
    assert 38.999 * 28.999 - 12 < done["data"]["filled_mm2"] < 38.999 * 28.999


def test_pouring_the_same_net_on_the_same_layer_twice_is_refused(tmp_path):
    board = tmp_path / "b.kicad_pcb"
    board.write_bytes(BOARD.encode("utf-8"))
    argv = ["board", "pour", "--board", str(board), "--net", "GND", "--layer", "F.Cu"]
    asked = native(*argv)
    native(*argv, "--confirm", asked["error"]["details"]["confirm_token"])
    again = native(*argv)
    assert again["error"]["code"] == "E_CONFLICT", again


def test_a_net_or_a_layer_the_board_has_not_got_is_named(tmp_path):
    board = tmp_path / "b.kicad_pcb"
    board.write_bytes(BOARD.encode("utf-8"))
    net = native("board", "pour", "--board", str(board), "--net", "NOPE", "--layer", "F.Cu")
    assert net["error"]["code"] == "E_NOT_FOUND" and "NOPE" in str(net["error"])
    layer = native("board", "pour", "--board", str(board), "--net", "GND", "--layer", "In7.Cu")
    assert layer["error"]["code"] == "E_NOT_FOUND"
    silk = native("board", "pour", "--board", str(board), "--net", "GND", "--layer", "Edge.Cuts")
    assert silk["error"]["code"] == "E_VALIDATION"


assert REPO.exists()
