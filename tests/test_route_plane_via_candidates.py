"""Controlled Host tests of finite ordinary plane-via candidate search.

No public command, Native process, project operation store or engineering
board is used. Actual analytic guards and the real Host grid router are used.
Historical counterexamples and their predeclared method live beside this file
in repro_plane_via_candidates.py; these tests assert the repaired behavior.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from repro_plane_via_candidates import make_case

from kicad_cli.native import route
from kicad_cli.native.smd_drill_guard import DrillConstraintError
from kicad_cli.native.via_hole_guard import ViaHoleConstraintError, ViaHoleGuard, separated


def fanout(case):
    layout, router, classes, created, _guard = case
    log = {}
    served = route.fanout_planes(layout, router, classes, {"GND"}, log)
    return served, log, [piece for piece in created if piece.kind == "via"]


def bind_rules(case, tmp_path):
    path = tmp_path / "synthetic.kicad_pcb"
    project = path.with_suffix(".kicad_pro")
    project.write_text(
        json.dumps({"board": {"design_settings": {"rules": {"min_hole_to_hole": 0.25}}}}),
        encoding="utf-8",
    )
    case[0].path = str(path)
    case[1].copper.via_hole_guard = ViaHoleGuard.read(path)
    return project


def test_fanout_skips_first_candidate_at_existing_same_net_hole():
    case = make_case(route, old_hole=True)
    served, log, vias = fanout(case)
    assert served == {"GND"} and log["fanout"] == {"GND": "1/1"}
    assert len(vias) == 1
    assert vias[0].start != (5_000_000, 5_000_000)
    assert separated((*vias[0].start, vias[0].drill), (5_000_000, 5_000_000, 300_000), 250_000)


def test_fanout_skips_first_candidate_inside_same_net_smd_margin():
    case = make_case(route, smd_gap_nm=100_000)
    served, log, vias = fanout(case)
    assert served == {"GND"} and log["fanout"] == {"GND": "1/1"}
    assert len(vias) == 1
    assert case[1].copper.smd_drill_guard.allows(*vias[0].start, 300_000)
    assert vias[0].start != (5_000_000, 5_000_000)


@pytest.mark.parametrize("constraint", ["hole", "smd"])
def test_fanout_all_candidates_illegal_reports_unserved_without_creating_copper(constraint):
    case = make_case(
        route,
        old_hole=constraint == "hole",
        smd_gap_nm=100_000_000 if constraint == "smd" else None,
    )
    if constraint == "hole":
        case[4].gap_nm = 20_000_000
    served, log, vias = fanout(case)
    assert served == set() and log["fanout"] == {"GND": "0/1"}
    assert not vias and not case[3]


def test_fanout_accounts_for_earlier_new_hole_on_same_net():
    case = make_case(route, pads=((5, 5), (5.3, 5)))
    served, log, vias = fanout(case)
    assert served == {"GND"} and log["fanout"] == {"GND": "2/2"}
    assert len(vias) == 2 and len(case[1].holes) == 2
    assert vias[0].start == (5_000_000, 5_000_000)
    assert vias[1].start != (5_300_000, 5_000_000)
    assert separated((*vias[0].start, vias[0].drill), (*vias[1].start, vias[1].drill), 250_000)


def test_candidate_checks_prior_uncommitted_path_hole():
    _layout, router, _classes, _created, _guard = make_case(route)
    router.planned_holes.append((5_000_000, 5_000_000, 300_000))
    assert not route.via_spot_ok(router, 5, 5, 0.6, 1, drill=0.3)
    assert route.via_spot_ok(router, 5.6, 5, 0.6, 1, drill=0.3)


def test_candidate_uses_actual_drill_independently_of_annulus_diameter():
    _layout, router, _classes, _created, _guard = make_case(route)
    router.paint_via(5, 5, 0.9, 1, drill_nm=100_000, position_nm=(5_000_000, 5_000_000))
    assert route.via_spot_ok(router, 5.4, 5, 0.8, 1, drill=0.1)
    assert not route.via_spot_ok(router, 5.4, 5, 0.8, 1, drill=0.3)


@pytest.mark.parametrize("x, allowed", [(5.549999, False), (5.55, True)])
def test_candidate_enforces_exact_smd_margin_at_saved_nm(x, allowed):
    _layout, router, _classes, _created, _guard = make_case(route, smd_gap_nm=100_000)
    assert route.via_spot_ok(router, x, 5, 0.6, 1, drill=0.3) is allowed


def test_candidate_keeps_foreign_copper_and_board_bounds_checks():
    _layout, router, _classes, _created, _guard = make_case(route)
    router.paint_seg(route.F, 5, 5, 6, 5, 0.2, 2)
    assert not route.via_spot_ok(router, 5.5, 5, 0.6, 1, drill=0.3)
    assert not route.via_spot_ok(router, 0, 5, 0.6, 1, drill=0.3)


def test_fanout_tries_next_legal_spot_when_first_connection_path_fails(monkeypatch):
    case = make_case(route, old_hole=True)
    router = case[1]
    original = router.route
    attempts = []

    def plan(*args, **kwargs):
        attempts.append(args[2][0])
        return None if len(attempts) == 1 else original(*args, **kwargs)

    monkeypatch.setattr(router, "route", plan)
    served, _log, vias = fanout(case)
    assert served == {"GND"} and len(vias) == 1
    assert len(attempts) >= 2 and vias[0].start != tuple(route.MM(v) for v in attempts[0][:2])


def test_fanout_refuses_rule_drift_before_search(tmp_path):
    case = make_case(route)
    project = bind_rules(case, tmp_path)
    project.write_text("{}", encoding="utf-8")
    with pytest.raises(ViaHoleConstraintError, match="drifted"):
        fanout(case)
    assert not case[3]


def test_fanout_refuses_rule_drift_after_search_before_creating_copper(tmp_path, monkeypatch):
    case = make_case(route, old_hole=True)
    project = bind_rules(case, tmp_path)
    original = case[1].route

    def plan(*args, **kwargs):
        result = original(*args, **kwargs)
        project.write_text("{}", encoding="utf-8")
        return result

    monkeypatch.setattr(case[1], "route", plan)
    with pytest.raises(ViaHoleConstraintError, match="drifted"):
        fanout(case)
    assert not case[3]


@pytest.mark.parametrize("constraint", ["hole", "smd", "rule-drift", "reserved-hole"])
def test_place_via_retains_final_guard_before_mutation(tmp_path, constraint):
    case = make_case(
        route, old_hole=constraint == "hole", smd_gap_nm=100_000 if constraint == "smd" else None
    )
    if constraint == "rule-drift":
        bind_rules(case, tmp_path).write_text("{}", encoding="utf-8")
    elif constraint == "reserved-hole":
        case[1].planned_holes.append((5_000_000, 5_000_000, 300_000))
    with pytest.raises(DrillConstraintError):
        route.place_via(case[0], case[1], 5, 5, 1, 0.6, 0.3)
    assert not case[3]


@pytest.mark.parametrize(
    "diameter, drill",
    [(0.3, 0.3), (0.3, 0.4), (0.6, 0), (0.6, -0.1), (0.6, float("nan")), (float("inf"), 0.3)],
)
def test_unsupported_proposed_via_dimensions_refuse(diameter, drill):
    case = make_case(route)
    with pytest.raises(ViaHoleConstraintError, match="ordinary circular through-via"):
        route.via_spot_ok(case[1], 5, 5, diameter, 1, drill=drill)
    with pytest.raises(ViaHoleConstraintError, match="ordinary circular through-via"):
        route.place_via(case[0], case[1], 5, 5, 1, diameter, drill)
    assert not case[3]


@pytest.mark.parametrize("unsupported", ["inner-plane", "multi-layer-zone", "four-layer-stack"])
def test_unsupported_plane_scope_refuses_before_any_copper(unsupported):
    case = make_case(route)
    board = case[0].board
    if unsupported == "inner-plane":
        board.zones[0].layers = ["In1.Cu"]
    elif unsupported == "multi-layer-zone":
        board.zones[0].layers = ["F.Cu", "B.Cu"]
    else:
        board.copper_layers = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
    with pytest.raises(ValueError, match="Plane fan-out supports only"):
        fanout(case)
    assert not case[3]


def test_no_plane_request_does_not_apply_plane_support_gate():
    case = make_case(route)
    case[0].board.copper_layers = ["F.Cu", "In1.Cu", "B.Cu"]
    log = {}
    assert route.fanout_planes(case[0], case[1], case[2], set(), log) == set()
    assert log["fanout"] == {} and not case[3]


def test_unsupported_later_net_via_dimensions_refuse_before_any_net_adds_copper():
    case = make_case(route)
    layout = case[0]
    layout.board.zones.append(SimpleNamespace(**{**vars(layout.board.zones[0]), "net": "Z_BAD"}))
    layout.codes["Z_BAD"] = 2
    layout.find = lambda net: layout.codes.get(net)
    case[2].params = lambda net: (0.2, 0.2, 0.3, 0.3) if net == "Z_BAD" else (0.2, 0.2, 0.6, 0.3)
    with pytest.raises(ViaHoleConstraintError, match="ordinary circular through-via"):
        route.fanout_planes(layout, case[1], case[2], {"GND", "Z_BAD"}, {})
    assert not case[3]


def test_front_plane_covering_pad_does_not_add_a_via():
    case = make_case(route)
    zone = case[0].board.zones[0]
    zone.layers = ["F.Cu"]
    zone.filled = {"F.Cu": zone.filled["B.Cu"]}
    served, log, vias = fanout(case)
    assert served == {"GND"} and log["fanout"] == {"GND": "1/1"}
    assert not vias and not case[3]


def test_fanout_uses_actual_smaller_drill_when_initial_position_is_legal():
    case = make_case(route, old_hole=True, pads=((5.4, 5),))
    case[1].holes[0] = (5_000_000, 5_000_000, 100_000)
    case[2].params = lambda _net: (0.2, 0.2, 0.8, 0.1)
    served, _log, vias = fanout(case)
    assert served == {"GND"} and len(vias) == 1
    assert vias[0].start == (5_400_000, 5_000_000)
    assert vias[0].width == 800_000 and vias[0].drill == 100_000
