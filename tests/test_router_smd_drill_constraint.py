"""Finite source/mock drill constraint evidence; no native process or final EXE.

Expected material distances below are fixed synthetic cases.  The actual
engineering acceptance uses separately read native shapes and actual drills.
"""

from __future__ import annotations

import math
from fractions import Fraction
from types import SimpleNamespace

import pytest

from kicad_cli.fileformat.board import Board
from kicad_cli.fileformat.sexpr import Document
from kicad_cli.native import route
from kicad_cli.native.router import MM, B, Copper, F, Router
from kicad_cli.native.smd_drill_guard import (
    SmdDrillGuard,
    SmdPad,
    clearance_nm,
    not_requested,
)


def source(*, shape="roundrect", layer='"F.Cu" "F.Paste" "F.Mask"', angle="0", extra=""):
    return f"""(kicad_pcb (version 20260206)
      (layers (0 "F.Cu" signal) (2 "B.Cu" signal))
      (footprint "fixture:rect" (layer "F.Cu") (at 5 5)
        (property "Reference" "R1" (at 0 -1) (layer "F.SilkS"))
        (pad "1" smd {shape} (at 0 0 {angle}) (size 2 1)
          (layers {layer}) (roundrect_rratio 0.25) (net "VIN")
          (uuid "b0000000-0000-4000-8000-000000000001") {extra})))"""


def read_guard(text=None, gap=0.2):
    board = Board(Document.parse(source() if text is None else text))
    return SmdDrillGuard.read([(fp, pad) for fp in board.footprints for pad in fp.pads], gap)


def rect_guard(*, net="VIN", gap=200_000):
    pad = SmdPad(
        "R1.1:fixed", net, "F.Cu", "rect", 5_000_000, 5_000_000, 2_000_000, 1_000_000, Fraction(0)
    )
    return SmdDrillGuard((pad,), gap)


@pytest.mark.parametrize("net", ["VIN", "OTHER", ""])
def test_same_net_and_no_net_smd_material_is_not_exempt(net):
    guard = rect_guard(net=net)
    assert not guard.allows(5_000_000, 5_000_000, 300_000)
    assert not guard.allows(6_100_000, 5_000_000, 300_000)


@pytest.mark.parametrize(
    "x,allowed", [(6_100_000, False), (6_349_999, False), (6_350_000, True), (6_350_001, True)]
)
def test_drill_edge_and_predeclared_margin_not_just_center(x, allowed):
    # Pad right edge is 6mm.  The hole radius is .15mm and the gap is .2mm.
    assert rect_guard().allows(x, 5_000_000, 300_000) is allowed


def test_exact_rounded_corner_is_not_replaced_by_a_bounding_box():
    pad = SmdPad(
        "TP1.1:fixed", "VIN", "F.Cu", "roundrect", 0, 0, 2_000_000, 2_000_000, Fraction(1_000_000)
    )
    guard = SmdDrillGuard((pad,), 100_000)
    # This point is within the pad AABB but its true material gap is
    # sqrt(.9**2+.9**2)-1 > .05mm drill radius + .1mm declared gap.
    assert guard.allows(900_000, 900_000, 100_000)
    assert not guard.allows(800_000, 800_000, 100_000)


def test_drill_diameter_uses_actual_hole_not_copper_annulus():
    guard = rect_guard()
    assert guard.allows(6_400_000, 5_000_000, 300_000)
    assert not guard.allows(6_400_000, 5_000_000, 500_000)


def test_positive_gap_equality_and_zero_gap_tangency_are_explicit():
    assert rect_guard().allows(6_350_000, 5_000_000, 300_000)
    assert not rect_guard(gap=0).allows(6_150_000, 5_000_000, 300_000)


@pytest.mark.parametrize("value", [True, "0.2", -0.1, 100.1, float("nan"), float("inf")])
def test_invalid_requested_gap_refuses(value):
    with pytest.raises(ValueError, match="finite number"):
        clearance_nm(value)


def test_fractional_nanometre_gap_is_never_rounded_inward():
    assert clearance_nm(0.2000000001) == 200_001
    guard = read_guard(gap=0.2000000001)
    assert guard.report()["clearance_mm"] == 0.2000000001
    assert guard.gap_nm == 200_001


@pytest.mark.parametrize(
    "edit",
    [
        lambda text: text.replace("smd roundrect", "smd oval"),
        lambda text: text.replace("(at 0 0 0)", "(at 0 0 90)"),
        lambda text: text.replace('"F.Cu" "F.Paste" "F.Mask"', '"In1.Cu" "F.Mask"'),
        lambda text: text.replace('"F.Cu" "F.Paste" "F.Mask"', '"F.Mask"'),
        lambda text: text.replace('"F.Cu" "F.Paste" "F.Mask"', '"F.Cu" "B.Cu"'),
        lambda text: text.replace('"F.Cu" "F.Paste" "F.Mask"', '"F.Cu" "Unknown.Layer"'),
        lambda text: text.replace("(roundrect_rratio 0.25)", ""),
        lambda text: text.replace("(roundrect_rratio 0.25)", "(roundrect_rratio nan)"),
        lambda text: text.replace("(roundrect_rratio 0.25)", "(roundrect_rratio 0.6)"),
        lambda text: text.replace("(roundrect_rratio 0.25)", "(padstack (mode custom))"),
        lambda text: text.replace("(size 2 1)", "(size 2 1) (size 3 1)"),
        lambda text: text.replace("(size 2 1)", "(size 2 1) (chamfer_ratio 0.2)"),
        lambda text: text.replace("(size 2 1)", "(size 2 1) (unknown_material_shape yes)"),
        lambda text: text.replace("(size 2 1)", "(size 2 1) (drill 0.3 (offset 0.1 0))"),
        lambda text: text.replace('(uuid "b0000000-0000-4000-8000-000000000001")', ""),
        lambda text: text.replace("smd roundrect", "unread_kind roundrect"),
    ],
    ids=[
        "shape",
        "rotation",
        "inner-layer",
        "no-copper-layer",
        "two-copper-layers",
        "unknown-layer",
        "missing-radius",
        "nonfinite-radius",
        "large-radius",
        "padstack",
        "duplicate-size",
        "chamfer",
        "unknown-material",
        "drill-offset",
        "missing-id",
        "unknown-pad-kind",
    ],
)
def test_unread_or_unsupported_saved_geometry_refuses(edit):
    with pytest.raises(ValueError):
        read_guard(edit(source()))


def test_saved_rect_and_roundrect_material_and_identity_are_read():
    guard = read_guard()
    assert guard.pads[0].radius == 250_000
    assert guard.pads[0].identity == "R1.1:b0000000-0000-4000-8000-000000000001"
    assert guard.report()["shape_counts"] == {"rect": 0, "roundrect": 1}
    text = source(shape="rect").replace("(roundrect_rratio 0.25)", "")
    assert read_guard(text).pads[0].radius == 0


def test_both_outer_layers_are_protected_against_ordinary_through_drill():
    guard = read_guard(source(layer='"B.Cu" "B.Mask"'))
    assert guard.pads[0].layer == "B.Cu"
    assert not guard.allows(5_000_000, 5_000_000, 300_000)


def test_planner_masks_same_net_pads_before_selecting_layer_changes():
    guard = rect_guard()
    copper = Copper(
        (0, 0, 10, 10),
        [],
        [
            {
                "box": (4_000_000, 4_500_000, 6_000_000, 5_500_000),
                "code": 1,
                "front": True,
                "back": False,
            },
        ],
        [],
        smd_drill_guard=guard,
    )
    router = Router(copper)
    path = router.route(1, [(5, 5, F)], [(5, 8, B)], 0.2, 0.2, 0.6, 0.3)
    assert path is not None
    changed = [a for a, b in zip(path, path[1:], strict=False) if a[0] != b[0]]
    assert changed
    for _layer, i, j, _width in changed:
        x, y = router.wx(i), router.wy(j)
        dx, dy = max(abs(x - 5) - 1, 0), max(abs(y - 5) - 0.5, 0)
        assert math.hypot(dx, dy) >= 0.35 - 1e-12
    assert guard.report()["planner_calls"] == 1


def test_mask_uses_exact_saved_nm_positions_and_is_separate_from_net_ownership():
    guard = rect_guard()
    rows = guard.rows((6_100_000, 6_349_999, 6_350_000), (5_000_000,), 300_000)
    assert rows == [0b011]


def test_shared_policy_rechecks_input_geometry_on_rewidth_reload(tmp_path):
    path = tmp_path / "board.kicad_pcb"
    path.write_text(source(), encoding="utf-8")
    first = route.Layout(str(path), smd_drill_clearance_mm=0.2)
    guard = first.copper.smd_drill_guard
    path.write_text(source().replace("(size 2 1)", "(size 3 1)"), encoding="utf-8")
    with pytest.raises(ValueError, match="drifted"):
        _ = route.Layout(str(path), smd_drill_guard=guard).copper


def test_selected_via_reread_does_not_certify_old_unselected_vias():
    guard = rect_guard()
    via_text = """(via (at 5 5) (size 0.6) (drill 0.3)
      (layers "F.Cu" "B.Cu") (net "GND"))"""
    board = Board(Document.parse(source()[:-1] + via_text + ")"))
    guard.check_selected_vias(board, {"VIN"})
    with pytest.raises(ValueError, match="violates requested"):
        guard.check_selected_vias(board, {"GND"})


def test_rewidth_replans_same_class_width_and_passes_actual_guard(monkeypatch):
    victim = route.Piece(
        "segment", (1_000_000, 1_000_000), (2_000_000, 1_000_000), 200_000, "F.Cu", 1
    )
    pads = [SimpleNamespace(net="VIN", position=(x, 1_000_000)) for x in (1_000_000, 2_000_000)]
    removed, constructor, planned = [], [], []
    layout = SimpleNamespace(
        find=lambda _name: 1,
        pads=[(None, pad) for pad in pads],
        codes={"VIN": 1},
        pieces=[victim],
        remove=removed.append,
        fill_and_save=lambda: None,
    )
    guard = object()

    def fake_layout(path, **kwargs):
        constructor.append((path, kwargs))
        return layout

    def plan(*args, **kwargs):
        planned.append((args, kwargs))
        return [(F, 10, 10, 0.2), (F, 20, 10, 0.2)]

    monkeypatch.setattr(route, "Layout", fake_layout)
    monkeypatch.setattr(route, "rebuild", lambda _layout: SimpleNamespace(route=plan))
    monkeypatch.setattr(route, "pad_layers", lambda _layout, _pad: [F])
    monkeypatch.setattr(route, "commit", lambda *args, **kwargs: [victim])
    classes = SimpleNamespace(params=lambda _name: (0.2, 0.2, 0.6, 0.3))
    result, _snaps = route.rewidth_one(
        "candidate.kicad_pcb", classes, 0.2, "VIN", smd_drill_guard=guard
    )
    assert result.startswith("重布")
    assert removed == [victim]
    assert constructor == [("candidate.kicad_pcb", {"smd_drill_guard": guard})]
    assert planned[0][0][3:7] == (0.2, 0.2, 0.6, 0.3)


def test_commit_rejects_bypassed_forbidden_via_before_creating_it():
    guard = rect_guard()
    router = Router(Copper((0, 0, 10, 10), [], [], [], smd_drill_guard=guard))
    created = []
    layout = SimpleNamespace(add=created.append)
    with pytest.raises(ValueError, match="violates requested"):
        route.commit(layout, router, [(F, 50, 50, 0.2), (B, 50, 50, 0.2)], 1, 0.2, 0.6, 0.3)
    assert not created


def test_unrequested_policy_remains_explicitly_unchecked():
    assert clearance_nm(None) is None
    result = not_requested()
    assert not result["enabled"] and not result["executed"]
    assert result["status"] == "not_requested"
    assert result["clearance_mm"] is None
    assert result["planner_calls"] == 0
    assert "not checked" in result["limits"][0]
    assert len(result) == 11


def test_actual_grid_point_conversion_is_the_writer_conversion():
    # Regression witness for truncation, not rounded display coordinates.
    assert MM(6.3500009) == 6_350_000
