"""Finite source-only report proof; no final EXE or native execution evidence."""

from __future__ import annotations

import copy

import pytest

from kicad_cli.fileformat.board import Board
from kicad_cli.move_risk import RiskIncomplete, compare_reports, object_index


def pad_id(number):
    return f"a0000000-0000-4000-8000-{number:012d}"


def footprint_id(number):
    return f"f0000000-0000-4000-8000-{number:012d}"


def saved_board(tmp_path, name, *, moved=False, edit=None):
    footprints = []
    for number in range(1, 8):
        net = "SIG" if number < 5 else "POWER" if number < 7 else "SOLO"
        x, y = (35, 20) if moved and number == 3 else (number * 10, 10)
        footprints.append(f'''(footprint "fixture:one-pad" (layer "F.Cu")
          (at {x} {y} 0) (uuid "{footprint_id(number)}")
          (property "Reference" "TP{number}" (at 0 -1) (layer "F.SilkS"))
          (pad "1" smd roundrect (at 0 0 0) (size 1 1)
            (layers "F.Cu" "F.Mask") (roundrect_rratio 0.25)
            (net "{net}") (uuid "{pad_id(number)}")))''')
    source = (
        """(kicad_pcb (version 20260206)
      (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
      """
        + "\n".join(footprints)
        + ")"
    )
    path = tmp_path / name / "saved.kicad_pcb"
    path.parent.mkdir()
    path.write_text(edit(source) if edit else source, encoding="utf-8")
    return Board.load(path)


def finding(board, pair, *, kind="unconnected_items", severity="error", excluded=False):
    index = object_index(board)
    items = []
    for number in pair:
        identity = pad_id(number)
        x, y = index[identity]["position_nm"]
        items.append(
            {
                "uuid": identity,
                "description": "Synthetic fixture",
                "pos": {"x": x / 1_000_000, "y": y / 1_000_000},
            }
        )
    return {
        "type": kind,
        "severity": severity,
        "excluded": excluded,
        "description": "Synthetic source-only report",
        "items": items,
    }


def report(board, *, after=False, pairs=None):
    if pairs is None:
        pairs = [(1, 3), (3, 2), (2, 4), (5, 6)] if after else [(1, 2), (2, 3), (3, 4), (5, 6)]
    return {
        "source": board.path.name,
        "date": "2026-10-18T06:00:00",
        "kicad_version": "10.0.6",
        "coordinate_units": "mm",
        "violations": [],
        "schematic_parity": [],
        "ignored_checks": [],
        "included_severities": ["error", "warning", "exclusion"],
        "unconnected_items": [finding(board, pair) for pair in pairs],
    }


def compare(before, after, old=None, new=None):
    return compare_reports(old or report(before), new or report(after, after=True), before, after)


def assert_ineligible(result):
    assert result["status"] == "blocked"
    connection = result["connection_changes"]
    assert connection["isolated_pad_tree_reselection"]["status"] == "not_applicable"
    assert connection["blocking_new_count"] > 0


def test_complete_isolated_pad_trees_allow_reselection_without_rewriting_raw_reports(tmp_path):
    before, after = saved_board(tmp_path, "before"), saved_board(tmp_path, "after", moved=True)
    old, new = report(before), report(after, after=True)
    raw_old, raw_new = copy.deepcopy(old), copy.deepcopy(new)
    result = compare(before, after, old, new)
    assert result["status"] == "passed"
    connection = result["connection_changes"]
    assert connection["counts"] == {"new": 2, "removed": 2, "retained": 2}
    assert connection["blocking_new_count"] == 0
    assert connection["equivalent_anchor_replacements"] == []
    proof = connection["isolated_pad_tree_reselection"]
    assert proof["status"] == "proved"
    assert proof["official_native"] is False
    assert len(proof["isolated_physical_pads"]["baseline"]) == 7
    assert proof["isolated_physical_pads"]["baseline"][pad_id(3)]["world_nm"] == [
        30_000_000,
        10_000_000,
    ]
    assert proof["isolated_physical_pads"]["candidate"][pad_id(3)]["world_nm"] == [
        35_000_000,
        20_000_000,
    ]
    assert {
        row["net"]: len(row["physical_pad_uuids"])
        for row in proof["complete_report_trees"]["candidate"]
    } == {"POWER": 2, "SIG": 4, "SOLO": 1}
    assert proof["reselected_edges"]["new"] == connection["reported_connection_edges"]["new"]
    assert old == raw_old and new == raw_new
    assert connection["complete_connectivity_graph"] is False


def test_nonconnection_violation_remains_blocking_despite_valid_reselection(tmp_path):
    before, after = saved_board(tmp_path, "before"), saved_board(tmp_path, "after", moved=True)
    new = report(after, after=True)
    new["violations"] = [finding(after, (1, 2), kind="silk_overlap", severity="warning")]
    result = compare(before, after, new=new)
    assert result["status"] == "blocked"
    assert result["connection_changes"]["isolated_pad_tree_reselection"]["status"] == "proved"
    assert result["new_findings"][0]["type"] == "silk_overlap"


@pytest.mark.parametrize(
    "defect",
    [
        "missing-power-edge",
        "missing-signal-pad",
        "duplicate",
        "cycle",
        "wrong-net",
        "substituted-identity",
    ],
)
@pytest.mark.parametrize("side", ["baseline", "candidate"])
def test_exact_report_tree_must_cover_every_physical_pad(tmp_path, defect, side):
    before, after = saved_board(tmp_path, "before"), saved_board(tmp_path, "after", moved=True)
    old, new = report(before), report(after, after=True)
    target = old if side == "baseline" else new
    model = before if side == "baseline" else after
    pairs = {
        "missing-power-edge": [(1, 3), (3, 2), (2, 4)],
        "missing-signal-pad": [(1, 3), (3, 2), (5, 6)],
        "duplicate": [(1, 3), (3, 1), (2, 4), (5, 6)],
        "cycle": [(1, 2), (2, 3), (3, 1), (5, 6)],
        "wrong-net": [(1, 3), (3, 2), (2, 5), (5, 6)],
        "substituted-identity": [(1, 3), (3, 2), (2, 7), (5, 6)],
    }[defect]
    target["unconnected_items"] = [finding(model, pair) for pair in pairs]
    with pytest.raises(RiskIncomplete, match="isolated-pad report tree"):
        compare(before, after, old, new)


@pytest.mark.parametrize(
    "flag,value",
    [("type", "clearance"), ("severity", "warning"), ("severity", "exclusion"), ("excluded", True)],
)
@pytest.mark.parametrize("side", ["baseline", "candidate"])
def test_changed_report_trees_cannot_hide_nonstandard_flags(tmp_path, flag, value, side):
    before, after = saved_board(tmp_path, "before"), saved_board(tmp_path, "after", moved=True)
    old, new = report(before), report(after, after=True)
    target = old if side == "baseline" else new
    target["unconnected_items"][0][flag] = value
    with pytest.raises(RiskIncomplete, match="finding flags"):
        compare(before, after, old, new)


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize("pair_index", [0, 3])
def test_retained_and_reselected_reference_points_still_require_saved_pad_centres(
    tmp_path, side, pair_index
):
    before, after = saved_board(tmp_path, "before"), saved_board(tmp_path, "after", moved=True)
    old, new = report(before), report(after, after=True)
    target = old if side == "baseline" else new
    target["unconnected_items"][pair_index]["items"][0]["pos"]["x"] += 0.000001
    with pytest.raises(RiskIncomplete, match="reference point"):
        compare(before, after, old, new)


@pytest.mark.parametrize(
    "old,new",
    [
        ('(net "SIG")', '(net "CHANGED")'),
        ("(size 1 1)", "(size 1.1 1)"),
        ("(at 0 0 0)", "(at 0.1 0 0)"),
        ("(at 35 20 0)", "(at 35 20 90)"),
        (f'(uuid "{pad_id(3)}")', f'(uuid "{pad_id(3)}-replacement")'),
        (f'(uuid "{footprint_id(3)}")', f'(uuid "{footprint_id(3)}-replacement")'),
    ],
)
def test_membership_geometry_identity_and_parent_protection_prevent_relaxation(tmp_path, old, new):
    before = saved_board(tmp_path, "before")
    after = saved_board(
        tmp_path, "after", moved=True, edit=lambda source: source.replace(old, new, 1)
    )
    if "-replacement" in new:
        # A native report using the old UUID is independently rejected before tree matching.
        unchanged = saved_board(tmp_path, "unchanged", moved=True)
        with pytest.raises(RiskIncomplete, match="unknown UUID|identities changed"):
            compare(before, after, new=report(unchanged, after=True))
    else:
        assert_ineligible(compare(before, after))


@pytest.mark.parametrize(
    "target_x,target_size", [("11", "1"), ("11.000001", "1.000001"), ("11.000000", "1.000001")]
)
def test_candidate_touch_overlap_and_odd_nanometre_bounds_are_ineligible(
    tmp_path, target_x, target_size
):
    def edit(source):
        return source.replace("(at 20 10 0)", f"(at {target_x} 10 0)").replace(
            "(size 1 1)", f"(size {target_size} {target_size})"
        )

    before = saved_board(tmp_path, "before")
    after = saved_board(tmp_path, "after", moved=True, edit=edit)
    result = compare(before, after)
    assert_ineligible(result)
    assert (
        "touch or overlap"
        in result["connection_changes"]["isolated_pad_tree_reselection"]["reason"]
    )


@pytest.mark.parametrize(
    "addition",
    [
        '(segment (start 1 1) (end 2 1) (width 0.2) (layer "F.Cu") (net "SIG") '
        '(uuid "b0000000-0000-4000-8000-000000000001"))',
        '(arc (start 1 1) (mid 2 2) (end 3 1) (width 0.2) (layer "F.Cu") (net "SIG") '
        '(uuid "b0000000-0000-4000-8000-000000000001"))',
        '(via (at 1 1) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "SIG") '
        '(uuid "b0000000-0000-4000-8000-000000000001"))',
        '(zone (net "SIG") (layer "F.Cu") (uuid "b0000000-0000-4000-8000-000000000001"))',
        '(gr_text "copper" (at 1 1) (layer "F.Cu") (uuid "b0000000-0000-4000-8000-000000000001"))',
        '(gr_line (start 1 1) (end 2 2) (stroke (width 0.2) (type default)) (layer "In1.Cu") '
        '(uuid "b0000000-0000-4000-8000-000000000001"))',
        '(gr_text "copper" (at 1 1) (layers "*.Cu") (uuid "b0000000-0000-4000-8000-000000000001"))',
        '(future_conductor (at 1 1) (uuid "b0000000-0000-4000-8000-000000000001"))',
    ],
)
def test_routed_copper_zone_or_any_copper_graphic_excludes_the_proof(tmp_path, addition):
    def edit(source):
        return source[:-1] + addition + ")"

    before = saved_board(tmp_path, "before", edit=edit)
    after = saved_board(tmp_path, "after", moved=True, edit=edit)
    assert_ineligible(compare(before, after))


@pytest.mark.parametrize(
    "addition",
    [
        '(fp_text user "copper" (at 1 1) (layer "F.Cu"))',
        '(property "User" "copper" (at 1 1) (layer "*.Cu"))',
        '(fp_line (start 1 1) (end 2 2) (stroke (width 0.2) (type default)) (layer "F.Cu"))',
        '(zone (net "SIG") (layer "F.Cu") (uuid "b0000000-0000-4000-8000-000000000001"))',
    ],
)
def test_footprint_copper_text_graphics_and_zones_are_ineligible(tmp_path, addition):
    def edit(source):
        reference = '(property "Reference" "TP1" (at 0 -1) (layer "F.SilkS"))'
        return source.replace(reference, reference + addition)

    before = saved_board(tmp_path, "before", edit=edit)
    after = saved_board(tmp_path, "after", moved=True, edit=edit)
    assert_ineligible(compare(before, after))


@pytest.mark.parametrize(
    "old,new",
    [
        ("smd roundrect", "smd custom"),
        ("smd roundrect", "thru_hole roundrect"),
        ("(at 0 0 0)", "(at 0 0 90)"),
        ('(layers "F.Cu" "F.Mask")', '(layers "*.Cu" "*.Mask")'),
        ("(size 1 1)", "(size 1 1) (drill 0.3)"),
        ("(size 1 1)", "(size 1 1) (offset 0.1 0)"),
        ("(size 1 1)", '(size 1 1) (padstack (layer "F.Cu" (size 2 2)))'),
        ("(size 1 1)", "(size 1 1) (chamfer_ratio 0.2) (chamfer top_left)"),
        ("(roundrect_rratio 0.25)", ""),
        ("(roundrect_rratio 0.25)", "(roundrect_rratio 0.6)"),
        ("(roundrect_rratio 0.25)", "(roundrect_rratio nan)"),
        ("(roundrect_rratio 0.25)", "(roundrect_rratio 0.25) (roundrect_rratio 0.2)"),
        (f'(uuid "{pad_id(1)}")', f'(uuid "{pad_id(1)}") (tstamp "legacy-pad")'),
        (f'(uuid "{footprint_id(1)}")', f'(uuid "{footprint_id(1)}") (tstamp "legacy-fp")'),
        ('(net "SIG")', '(net "")'),
    ],
)
def test_unsupported_or_ambiguous_pad_geometry_never_uses_reselection(tmp_path, old, new):
    def edit(source):
        return source.replace(old, new, 1)

    before = saved_board(tmp_path, "before", edit=edit)
    after = saved_board(tmp_path, "after", moved=True, edit=edit)
    assert_ineligible(compare(before, after))


def test_supported_rect_pads_and_non_copper_graphics_are_eligible(tmp_path):
    def edit(source):
        source = source.replace("smd roundrect", "smd rect").replace("(roundrect_rratio 0.25)", "")
        return source[:-1] + '(gr_text "readable" (at 1 1) (layer "F.SilkS"))' + ")"

    before = saved_board(tmp_path, "before", edit=edit)
    after = saved_board(tmp_path, "after", moved=True, edit=edit)
    assert compare(before, after)["status"] == "passed"


@pytest.mark.parametrize(
    "fields",
    [
        "",
        '(layer "")',
        '(layer "unknown")',
        '(layer "F.SilkS" extra)',
        '(layer "F.SilkS") (layer "F.SilkS")',
        '(layers "F.SilkS")',
        '(layer "F.SilkS") (layers "F.SilkS")',
        '(layer "User.5")',
    ],
)
@pytest.mark.parametrize(
    "kind",
    [
        "gr_line",
        "gr_text",
        "gr_text_box",
        "dimension",
        "fp_line",
        "fp_text",
        "fp_text_box",
        "property",
    ],
)
def test_unknown_missing_or_ambiguous_drawable_layer_is_never_assumed_non_copper(
    tmp_path, fields, kind
):
    def edit(source):
        if kind in {"gr_line", "fp_line"}:
            addition = f"({kind} (start 1 1) (end 2 2) {fields})"
        elif kind == "dimension":
            addition = f"(dimension (type aligned) {fields})"
        else:
            addition = f'({kind} "fixture" (at 1 1) {fields})'
        if kind.startswith("fp_") or kind == "property":
            reference = '(property "Reference" "TP1" (at 0 -1) (layer "F.SilkS"))'
            return source.replace(reference, reference + addition)
        return source[:-1] + addition + ")"

    before = saved_board(tmp_path, "before", edit=edit)
    after = saved_board(tmp_path, "after", moved=True, edit=edit)
    assert_ineligible(compare(before, after))


def test_same_pad_pair_may_keep_a_legitimate_moved_reference_point(tmp_path):
    before, after = saved_board(tmp_path, "before"), saved_board(tmp_path, "after", moved=True)
    pairs = [(1, 2), (2, 3), (3, 4), (5, 6)]
    result = compare(before, after, new=report(after, pairs=pairs))
    assert result["status"] == "passed"
    assert result["connection_changes"]["counts"] == {"new": 0, "removed": 0, "retained": 4}
    assert result["connection_changes"]["isolated_pad_tree_reselection"]["status"] == "not_needed"
