"""Finite synthetic/source evidence; no native process or public EXE is started.

Distances and effective rules are predeclared here, not inferred from the
engineering candidate or copied from a router success result.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kicad_cli.fileformat.board import Board
from kicad_cli.fileformat.sexpr import Document
from kicad_cli.native import route
from kicad_cli.native.router import MM, B, Copper, F, Router
from kicad_cli.native.via_hole_guard import (
    RULE,
    SOURCE,
    ViaHoleConstraintError,
    ViaHoleGuard,
    separated,
)


def board_text(vias=""):
    return (
        '(kicad_pcb (version 20260206) (layers (0 "F.Cu" signal) (2 "B.Cu" signal)) ' + vias + ")"
    )


def via(x=5, y=5, *, net="GND", drill="0.3", extra="", uid="hole-1"):
    return (
        f"(via (at {x} {y}) (size 0.6) (drill {drill}) "
        f'(layers "F.Cu" "B.Cu") (net "{net}") (uuid "{uid}") {extra})'
    )


def policy(tmp_path, value=0.25):
    path = tmp_path / "input.kicad_pcb"
    path.write_text(board_text(), encoding="utf-8")
    path.with_suffix(".kicad_pro").write_text(
        json.dumps(
            {
                "board": {
                    "design_settings": {
                        "rules": {"min_hole_to_hole": value, "min_hole_clearance": 0.91},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return path, ViaHoleGuard.read(path)


@pytest.mark.parametrize("net", ["GND", "OTHER", ""])
def test_same_net_and_unassigned_drills_have_no_ownership_exemption(tmp_path, net):
    path, guard = policy(tmp_path)
    path.write_text(board_text(via(net=net) + via(5.5, uid="hole-2")), encoding="utf-8")
    with pytest.raises(ViaHoleConstraintError, match="Selected saved via drills"):
        guard.check_selected_vias(Board.load(path), {"GND"})


@pytest.mark.parametrize(
    "distance,allowed", [(500_000, False), (549_999, False), (550_000, True), (550_001, True)]
)
def test_drill_edge_gap_uses_both_exact_radii(distance, allowed):
    assert separated((0, 0, 300_000), (distance, 0, 300_000), 250_000) is allowed


def test_different_actual_drill_diameters_and_diagonal_distance():
    assert not separated((0, 0, 300_000), (600_000, 0, 500_000), 250_000)
    assert separated((0, 0, 300_000), (650_000, 0, 500_000), 250_000)
    assert not separated((0, 0, 300_000), (388_908, 388_908, 300_000), 250_000)
    assert separated((0, 0, 300_000), (388_909, 388_909, 300_000), 250_000)


def test_project_hole_to_hole_is_distinct_from_hole_to_copper(tmp_path):
    _path, guard = policy(tmp_path)
    assert guard.gap_nm == 250_000
    assert guard.binding["rule"] == RULE
    assert not guard.binding["defaulted_to_kicad10"]


def test_absent_saved_minimum_declares_the_kicad10_default(tmp_path):
    path = tmp_path / "default.kicad_pcb"
    guard = ViaHoleGuard.read(path)
    assert guard.gap_nm == 250_000
    assert guard.binding["defaulted_to_kicad10"]
    assert guard.binding["project_sha256"] is None


def test_fractional_nm_rule_is_rounded_outward(tmp_path):
    _path, guard = policy(tmp_path, 0.2500000001)
    assert guard.gap_nm == 250_001


@pytest.mark.parametrize("value", [True, None, [], {}, "0.25", -0.1, float("nan"), float("inf")])
def test_unread_project_minimum_refuses(tmp_path, value):
    with pytest.raises(ViaHoleConstraintError):
        policy(tmp_path, value)


@pytest.mark.parametrize("text", ["[]", "{", '{"board":[]}', '{"board":{"design_settings":null}}'])
def test_unread_project_structure_refuses(tmp_path, text):
    path = tmp_path / "input.kicad_pcb"
    path.with_suffix(".kicad_pro").write_text(text, encoding="utf-8")
    with pytest.raises(ViaHoleConstraintError, match="Unreadable"):
        ViaHoleGuard.read(path)


@pytest.mark.parametrize("change", ["modify", "delete", "replace", "new-custom"])
def test_shared_policy_rejects_rule_input_drift(tmp_path, change):
    path, guard = policy(tmp_path)
    project = path.with_suffix(".kicad_pro")
    if change == "modify":
        project.write_text(project.read_text().replace("0.25", "0.26"), encoding="utf-8")
    elif change == "delete":
        project.unlink()
    elif change == "replace":
        project.write_text(project.read_text() + "\n", encoding="utf-8")
    else:
        path.with_suffix(".kicad_dru").write_text("(version 1)\n", encoding="utf-8")
    with pytest.raises(ViaHoleConstraintError, match="drifted"):
        guard.require_binding(path)


@pytest.mark.parametrize(
    "text",
    [
        '(version 1) (rule "strict" (constraint hole_to_hole (min 0.4mm)))',
        '(version 1) (rule "loose" (condition "A.NetName == \'GND\'") '
        "(constraint hole_to_hole (min 0.1mm)))",
        '(version 1) (rule "unread"',
    ],
)
def test_custom_hole_rule_or_unread_file_is_explicitly_unsupported(tmp_path, text):
    path, _guard = policy(tmp_path)
    path.with_suffix(".kicad_dru").write_text(text, encoding="utf-8")
    with pytest.raises(ViaHoleConstraintError):
        ViaHoleGuard.read(path)


def test_other_custom_rule_is_bound_but_not_claimed_as_evaluated(tmp_path):
    path, _guard = policy(tmp_path)
    path.with_suffix(".kicad_dru").write_text(
        '# retained rule\n(version 1) (rule "width" (constraint track_width (min 0.2mm)))',
        encoding="utf-8",
    )
    guard = ViaHoleGuard.read(path)
    assert guard.binding["custom_rules_sha256"] is not None
    assert any("separate checks" in line for line in guard.report()["limits"])


@pytest.mark.parametrize(
    "edit",
    [
        lambda s: s.replace("(via ", "(via micro "),
        lambda s: s.replace('"F.Cu" "B.Cu"', '"F.Cu" "In1.Cu"'),
        lambda s: s.replace("(drill 0.3)", "(drill oval 0.3 0.4)"),
        lambda s: s.replace("(drill 0.3)", ""),
        lambda s: s.replace("(drill 0.3)", "(drill 0.3) (drill 0.4)"),
        lambda s: s.replace("(drill 0.3)", "(drill 0)"),
        lambda s: s.replace("(drill 0.3)", "(drill nan)"),
        lambda s: s.replace("(drill 0.3)", "(drill 0.3 (offset 0.1 0))"),
        lambda s: s.replace("(size 0.6)", "(size 0.3)"),
        lambda s: s.replace("(size 0.6)", "(size 0.6) (padstack (mode custom))"),
        lambda s: s.replace("(at 5 5)", "(at nan 5)"),
    ],
    ids=[
        "micro",
        "layer",
        "oval",
        "missing",
        "duplicate",
        "zero",
        "nonfinite",
        "offset",
        "no-annulus",
        "padstack",
        "position",
    ],
)
def test_unsupported_saved_via_geometry_refuses(edit):
    board = Board(Document.parse(board_text(edit(via()))))
    with pytest.raises(ViaHoleConstraintError):
        ViaHoleGuard.saved_holes(board)


def test_exact_mask_uses_drill_edges_and_saved_nm_not_annulus(tmp_path):
    _path, guard = policy(tmp_path)
    assert guard.rows((500_000, 549_999, 550_000), (0,), 300_000, [(0, 0, 300_000)]) == [0b011]


def test_planner_avoids_existing_same_net_drill(tmp_path):
    _path, guard = policy(tmp_path)
    router = Router(Copper((0, 0, 10, 10), [], [], [], via_hole_guard=guard))
    router.paint_via(5, 5, 0.6, 1, drill_nm=300_000, position_nm=(5_000_000, 5_000_000))
    path = router.route(1, [(5, 5, F)], [(5, 8, B)], 0.2, 0.2, 0.6, 0.3)
    assert path is not None
    holes = router.path_holes(path, 300_000)
    assert holes
    assert all(separated(hole, router.holes[0], 250_000) for hole in holes)


def test_planner_avoids_a_prior_uncommitted_path_hole(tmp_path):
    _path, guard = policy(tmp_path)
    router = Router(Copper((0, 0, 10, 10), [], [], [], via_hole_guard=guard))
    router.reserve_path_holes([(F, 50, 50, 0.2), (B, 50, 50, 0.2)], 300_000)
    assert not router.holes
    path = router.route(1, [(5, 5, F)], [(5, 8, B)], 0.2, 0.2, 0.6, 0.3)
    assert path is not None
    assert all(
        separated(hole, router.planned_holes[0], 250_000)
        for hole in router.path_holes(path, 300_000)
    )


def test_commit_checks_all_proposed_holes_before_creating_any_copper(tmp_path):
    _path, guard = policy(tmp_path)
    router = Router(Copper((0, 0, 10, 10), [], [], [], via_hole_guard=guard))
    created = []
    # Two layer changes on the same net, .5mm apart; some tracks occur first.
    path = [
        (F, 45, 50, 0.2),
        (F, 50, 50, 0.2),
        (B, 50, 50, 0.2),
        (B, 55, 50, 0.2),
        (F, 55, 50, 0.2),
    ]
    with pytest.raises(ViaHoleConstraintError, match="effective project"):
        route.commit(SimpleNamespace(add=created.append), router, path, 1, 0.2, 0.6, 0.3)
    assert not created and not router.holes


def test_rewidth_reserves_uncommitted_paths_and_clears_width_trial_reservations(
    tmp_path, monkeypatch
):
    _path, guard = policy(tmp_path)
    router = Router(Copper((0, 0, 10, 10), [], [], [], via_hole_guard=guard))
    pads = [
        SimpleNamespace(net="GND", position=(x, 1_000_000))
        for x in (1_000_000, 2_000_000, 3_000_000)
    ]
    layout = SimpleNamespace(
        find=lambda _n: 1,
        pads=[(None, p) for p in pads],
        codes={"GND": 1},
        pieces=[],
        fill_and_save=lambda: None,
    )
    calls = []

    def plan(*_args, **_kwargs):
        calls.append(list(router.planned_holes))
        if len(calls) == 2:
            assert calls[-1] == [(5_000_000, 5_000_000, 300_000)]
            return None
        if len(calls) == 3:
            assert not calls[-1]  # previous failed width trial has no live reservation
        x = 50 if len(calls) in (1, 3) else 60
        return [(F, x, 50, 0.2), (B, x, 50, 0.2)]

    monkeypatch.setattr(route, "Layout", lambda *a, **kw: layout)
    monkeypatch.setattr(route, "rebuild", lambda _layout: router)
    monkeypatch.setattr(route, "pad_layers", lambda *a: [F])
    monkeypatch.setattr(router, "route", plan)
    monkeypatch.setattr(route, "commit", lambda *a, **kw: [])
    classes = SimpleNamespace(params=lambda _n: (0.4, 0.2, 0.6, 0.3))
    result, _ = route.rewidth_one("candidate.kicad_pcb", classes, 0.2, "GND")
    assert result.startswith("重布") and len(calls) == 4
    assert not router.planned_holes


def test_legal_saved_pair_reloads_with_exact_policy_and_report(tmp_path):
    path, guard = policy(tmp_path)
    path.write_text(board_text(via() + via(5.55, uid="hole-2")), encoding="utf-8")
    guard.check_selected_vias(Board.load(path), {"GND"})
    result = guard.report()
    assert set(result) == {
        "status",
        "source",
        "scope",
        "same_net_exempt",
        "binding",
        "planner_calls",
        "checked_selected_via_count",
        "checked_pair_count",
        "limits",
    }
    assert result["status"] == "enforced" and result["source"] == SOURCE
    assert not result["same_net_exempt"]
    assert result["checked_selected_via_count"] == 2 and result["checked_pair_count"] == 1


def test_shared_policy_is_rechecked_on_layout_reload(tmp_path):
    path, guard = policy(tmp_path)
    first = route.Layout(str(path), via_hole_guard=guard)
    _ = first.copper
    path.with_suffix(".kicad_pro").write_text("{}", encoding="utf-8")
    with pytest.raises(ViaHoleConstraintError, match="drifted"):
        _ = route.Layout(str(path), via_hole_guard=guard).copper


def test_structured_failure_names_finite_rule_not_smd_request(tmp_path):
    _path, guard = policy(tmp_path)
    with pytest.raises(ViaHoleConstraintError) as caught:
        guard.require_clearance(500_000, 0, 300_000, [(0, 0, 300_000)])
    record = caught.value.record()
    assert record["code"] == "E_VALIDATION"
    assert record["details"]["constraint"] == "project-min-hole-to-hole"
    assert record["details"]["objects"][0]["position_nm"] == [500_000, 0]
    assert MM(0.5500009) == 550_000
