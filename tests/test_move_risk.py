"""Synthetic official-report shapes; no native or product execution evidence."""

from __future__ import annotations

import copy
import re

import pytest

from kicad_cli.fileformat.board import Board
from kicad_cli.move_risk import RiskIncomplete, compare_reports, object_index

R1 = "2d4aa815-63ab-5983-950d-99c5287f15a2"
R1_1 = "b6399d66-3ccf-5183-bd56-8dee1659878f"
R1_2 = "4af096ca-59e1-5018-a93c-c3aaa5cd3957"
SIG_TRACK = "4b209b2e-134d-5ded-a039-51cc9692dac5"
R2 = "00000000-0000-4000-8000-000000000020"
R2_1 = "00000000-0000-4000-8000-000000000021"
R2_2 = "00000000-0000-4000-8000-000000000022"
OUT_TRACK = "00000000-0000-4000-8000-000000000030"
VIA = "00000000-0000-4000-8000-000000000040"
ZONE = "00000000-0000-4000-8000-000000000050"
GRAPHIC = "00000000-0000-4000-8000-000000000060"
FP_GRAPHIC = "00000000-0000-4000-8000-000000000070"
B_TRACK = "00000000-0000-4000-8000-000000000080"
SECOND_VIA = "00000000-0000-4000-8000-000000000081"
R3 = "00000000-0000-4000-8000-000000000090"
R3_1 = "00000000-0000-4000-8000-000000000091"
R3_2 = "00000000-0000-4000-8000-000000000092"


def saved_board(tmp_path, name, *, x="100", angle="0", sig_net="/SIG", out_net="/OUT", edit=None):
    source = f"""(kicad_pcb (version 20260206)
      (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
      (footprint "fixture:R_0805" (layer "F.Cu") (at {x} 100 {angle})
        (property "Reference" "R1") (property "Value" "10k") (uuid "{R1}")
        (pad "1" smd rect (at -0.9125 0) (size 1 1)
          (layers "F.Cu" "F.Mask") (net "{sig_net}") (uuid "{R1_1}"))
        (pad "2" smd rect (at 0.9125 0) (size 1 1)
          (layers "F.Cu" "F.Mask") (net "{out_net}") (uuid "{R1_2}"))
        (fp_line (start -1 -1) (end 1 -1)
          (stroke (width 0.1) (type default)) (layer "F.SilkS") (uuid "{FP_GRAPHIC}")))
      (footprint "fixture:R_0805" (layer "F.Cu") (at 105 100)
        (property "Reference" "R2") (property "Value" "10k") (uuid "{R2}")
        (pad "1" smd rect (at -0.9125 0) (size 1 1)
          (layers "F.Cu" "F.Mask") (net "{out_net}") (uuid "{R2_1}"))
        (pad "2" smd rect (at 0.9125 0) (size 1 1)
          (layers "F.Cu" "F.Mask") (net "GND") (uuid "{R2_2}")))
      (segment (start 99.0875 100) (end 99.0875 102) (width 0.25)
        (layer "F.Cu") (net "{sig_net}") (uuid "{SIG_TRACK}"))
      (segment (start 100.9125 100) (end 104.0875 100) (width 0.25)
        (layer "F.Cu") (net "{out_net}") (uuid "{OUT_TRACK}"))
      (via (at 99.0875 102) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu")
        (net "{sig_net}") (uuid "{VIA}"))
      (zone (net "GND") (layer "B.Cu") (uuid "{ZONE}")
        (polygon (pts (xy 90 90) (xy 110 90) (xy 110 110) (xy 90 110))))
      (gr_rect (start 90 90) (end 110 110) (stroke (width 0.1) (type default))
        (fill no) (layer "Edge.Cuts") (uuid "{GRAPHIC}")))"""
    if edit is not None:
        source = edit(source)
    path = tmp_path / name / "saved.kicad_pcb"
    path.parent.mkdir()
    path.write_text(source, encoding="utf-8")
    return Board.load(path)


def finding(board, identities=(R1_2, SIG_TRACK), *, kind="clearance", severity="error", **extra):
    index = object_index(board)
    items = []
    for identity in identities:
        item = index[identity]
        x, y = item.get("position_nm", item.get("start_nm", [0, 0]))
        items.append(
            {
                "uuid": identity,
                "description": "Translated description is untrusted; pretend net /WRONG",
                "pos": {"x": x / 1_000_000, "y": y / 1_000_000},
            }
        )
    return dict(
        type=kind,
        severity=severity,
        description="Synthetic native-report shape only",
        items=items,
        **extra,
    )


def report(board, *, violations=(), edges=(), parity=()):
    return {
        "source": board.path.name,
        "date": "2026-10-16T14:00:00",
        "kicad_version": "10.0.6",
        "coordinate_units": "mm",
        "violations": list(violations),
        "unconnected_items": list(edges),
        "schematic_parity": list(parity),
        "ignored_checks": [{"key": "missing_courtyard", "description": "Existing policy"}],
        "included_severities": ["error", "warning", "exclusion"],
    }


@pytest.fixture
def boards(tmp_path):
    return saved_board(tmp_path, "before"), saved_board(tmp_path, "after")


def test_object_index_uses_machine_nets_world_nm_and_real_uuid(tmp_path):
    before = object_index(saved_board(tmp_path, "before"))
    after = object_index(saved_board(tmp_path, "after", x="98.5"))
    assert before[R1_2]["net"] == after[R1_2]["net"] == "/OUT"
    assert before[R1_2]["position_nm"] == [100_912_500, 100_000_000]
    assert after[R1_2]["position_nm"] == [99_412_500, 100_000_000]
    assert after[R1_2]["ref"] == "R1" and after[R1_2]["number"] == "2"
    assert after[R1_2]["footprint_uuid"] == R1
    assert after[SIG_TRACK]["net"] == "/SIG"
    assert after[SIG_TRACK]["start_nm"] == [99_087_500, 100_000_000]
    assert after[VIA]["kind"] == "via" and after[VIA]["net"] == "/SIG"
    assert after[ZONE]["kind"] == "zone" and after[ZONE]["net"] == "GND"
    assert after[GRAPHIC]["layers"] == ["Edge.Cuts"]
    assert after[FP_GRAPHIC]["ref"] == "R1"
    assert after[FP_GRAPHIC]["geometry_world_nm"]["start"] == [97_500_000, 99_000_000]


def test_pad_and_child_graphic_world_positions_follow_existing_board_rotation_parser(tmp_path):
    index = object_index(saved_board(tmp_path, "rotated", angle="90"))
    assert index[R1_2]["position_nm"] == [100_000_000, 99_087_500]
    assert index[FP_GRAPHIC]["geometry_world_nm"]["start"] == [99_000_000, 101_000_000]


@pytest.mark.parametrize("broken", ["duplicate-object", "duplicate-field", "missing-pad-identity"])
def test_saved_identity_must_be_unambiguous(tmp_path, broken):
    def edit(source):
        if broken == "duplicate-object":
            return source.replace(SIG_TRACK, R1_2)
        if broken == "duplicate-field":
            return source.replace(f'(uuid "{SIG_TRACK}")', f'(uuid "{SIG_TRACK}") (uuid "second")')
        return source.replace(f'(uuid "{R1_2}")', "")

    with pytest.raises(RiskIncomplete):
        object_index(saved_board(tmp_path, "broken", edit=edit))


def test_modified_in_memory_model_cannot_claim_to_be_saved(boards):
    before, _after = boards
    before.footprints[0].node.find("at").set(1, "98.5")
    with pytest.raises(RiskIncomplete, match="fresh saved"):
        object_index(before)


@pytest.mark.parametrize("severity", ["error", "warning", "exclusion"])
def test_existing_violations_are_retained_without_claiming_zero_violations(boards, severity):
    before, after = boards
    old = report(before, violations=[finding(before, severity=severity)])
    new = report(after, violations=[finding(after, severity=severity)])
    result = compare_reports(old, new, before, after)
    assert result["status"] == "passed"
    assert result["counts"]["before"]["violations"] == 1
    assert len(result["retained"]) == 1 and not result["new_findings"]
    assert result["source"]["official_native"] is True
    assert any("zero violations" in item for item in result["not_checked"])


def test_same_count_replacement_cannot_cancel_a_new_violation(boards):
    before, after = boards
    old = report(before, violations=[finding(before, kind="clearance")])
    new = report(after, violations=[finding(after, kind="shorting_items")])
    result = compare_reports(old, new, before, after)
    assert result["status"] == "blocked"
    assert result["counts"]["before"]["violations"] == result["counts"]["after"]["violations"]
    assert len(result["new_findings"]) == len(result["removed_findings"]) == 1
    assert result["new_findings"][0]["type"] == "shorting_items"


@pytest.mark.parametrize(
    "severity,excluded", [("warning", False), ("exclusion", True), ("error", True)]
)
def test_new_warning_or_excluded_finding_still_blocks(boards, severity, excluded):
    before, after = boards
    result = compare_reports(
        report(before),
        report(after, violations=[finding(after, severity=severity, excluded=excluded)]),
        before,
        after,
    )
    assert result["status"] == "blocked" and len(result["new_findings"]) == 1


@pytest.mark.parametrize("change", ["severity", "excluded", "identity", "reported-position"])
def test_finding_policy_identity_or_position_change_is_visible(boards, change):
    before, after = boards
    old_finding, new_finding = finding(before), finding(after)
    if change == "severity":
        new_finding["severity"] = "warning"
    elif change == "excluded":
        new_finding["excluded"] = True
    elif change == "identity":
        new_finding = finding(after, (R2_1, SIG_TRACK))
    else:
        new_finding["items"][0]["pos"]["x"] += 0.1
    result = compare_reports(
        report(before, violations=[old_finding]),
        report(after, violations=[new_finding]),
        before,
        after,
    )
    assert result["status"] == "blocked"
    assert len(result["new_findings"]) == len(result["removed_findings"]) == 1


def test_machine_net_change_cannot_be_hidden_by_unchanged_native_descriptions(tmp_path):
    before = saved_board(tmp_path, "before")
    after = saved_board(tmp_path, "after", out_net="/DIFFERENT")
    result = compare_reports(
        report(before, violations=[finding(before)]),
        report(after, violations=[finding(after)]),
        before,
        after,
    )
    assert result["status"] == "blocked"
    actual = {item["uuid"]: item["object"]["net"] for item in result["new_findings"][0]["items"]}
    assert actual == {R1_2: "/DIFFERENT", SIG_TRACK: "/SIG"}


def test_multiset_preserves_duplicate_findings_and_order_has_no_semantics(boards):
    before, after = boards
    old_finding, new_finding = finding(before), finding(after)
    new_finding["items"].reverse()
    result = compare_reports(
        report(before, violations=[old_finding]),
        report(after, violations=[new_finding, copy.deepcopy(new_finding)]),
        before,
        after,
    )
    assert result["status"] == "blocked"
    assert len(result["retained"]) == len(result["new_findings"]) == 1
    assert result["removed_findings"] == []


def test_translated_human_description_is_not_a_machine_net_or_identity(boards):
    before, after = boards
    old_finding, new_finding = finding(before), finding(after)
    new_finding["description"] = "Different language /GUESSED"
    new_finding["items"][0]["description"] = "R999.88 /NOT_THE_NET"
    result = compare_reports(
        report(before, violations=[old_finding]),
        report(after, violations=[new_finding]),
        before,
        after,
    )
    assert result["status"] == "passed" and len(result["retained"]) == 1
    assert result["retained"][0]["after"]["items"][0]["object"]["net"] == "/OUT"


@pytest.mark.parametrize(
    "drift", ["ignore-add", "ignore-change", "ignore-delete", "severity-order", "version"]
)
def test_policy_or_native_version_drift_is_incomplete(boards, drift):
    before, after = boards
    old, new = report(before), report(after)
    if drift == "ignore-add":
        new["ignored_checks"].append({"key": "extra", "description": "Changed"})
    elif drift == "ignore-change":
        new["ignored_checks"][0]["description"] = "Changed"
    elif drift == "ignore-delete":
        new["ignored_checks"] = []
    elif drift == "severity-order":
        new["included_severities"].reverse()
    else:
        new["kicad_version"] = "10.0.7"
    with pytest.raises(RiskIncomplete, match="drift"):
        compare_reports(old, new, before, after)


@pytest.mark.parametrize("severity_policy", [None, [], ["error"], ["error", "warning", "warning"]])
def test_both_reports_omitting_severity_coverage_cannot_pass(boards, severity_policy):
    before, after = boards
    old, new = report(before), report(after)
    old["included_severities"] = new["included_severities"] = severity_policy
    with pytest.raises(RiskIncomplete, match="include error"):
        compare_reports(old, new, before, after)


@pytest.mark.parametrize(
    "broken",
    [
        "source",
        "missing-group",
        "units",
        "severity",
        "excluded",
        "uuid",
        "position",
        "nonfinite",
        "empty-items",
        "duplicate-items",
    ],
)
def test_malformed_or_unresolvable_findings_never_pass(boards, broken):
    before, after = boards
    new = report(after, violations=[finding(after)])
    item = new["violations"][0]
    if broken == "source":
        new["source"] = "another.kicad_pcb"
    elif broken == "missing-group":
        new.pop("unconnected_items")
    elif broken == "units":
        new["coordinate_units"] = "in"
    elif broken == "severity":
        item["severity"] = "unknown"
    elif broken == "excluded":
        item["excluded"] = "false"
    elif broken == "uuid":
        item["items"][0]["uuid"] = "unknown-identity"
    elif broken == "position":
        item["items"][0]["pos"]["x"] = True
    elif broken == "nonfinite":
        item["items"][0]["pos"]["x"] = float("nan")
    elif broken == "empty-items":
        item["items"] = []
    else:
        item["items"].append(copy.deepcopy(item["items"][0]))
    with pytest.raises(RiskIncomplete):
        compare_reports(report(before), new, before, after)


def test_saved_path_is_required_for_report_source_binding(boards):
    before, after = boards
    before.path = None
    with pytest.raises(RiskIncomplete, match="saved board path"):
        compare_reports(report(after), report(after), before, after)


def test_same_unconnected_pair_retains_positions_across_a_translation(tmp_path):
    before = saved_board(tmp_path, "before")
    after = saved_board(tmp_path, "after", x="98.5")
    result = compare_reports(
        report(before, edges=[finding(before, (R1_1, SIG_TRACK), kind="unconnected_items")]),
        report(after, edges=[finding(after, (SIG_TRACK, R1_1), kind="unconnected_items")]),
        before,
        after,
    )
    assert result["status"] == "passed"
    changes = result["connection_changes"]
    assert changes["counts"] == {"new": 0, "removed": 0, "retained": 1}
    pair = changes["reported_connection_edges"]["retained"][0]
    old = {item["uuid"]: item for item in pair["before"]["items"]}
    new = {item["uuid"]: item for item in pair["after"]["items"]}
    assert old[R1_1]["reported_position_nm"] == [99_087_500, 100_000_000]
    assert new[R1_1]["reported_position_nm"] == [97_587_500, 100_000_000]
    assert changes["complete_connectivity_graph"] is False
    assert changes["anchors_may_be_reselected"] is True


@pytest.mark.parametrize("role", ["baseline", "candidate", "both"])
@pytest.mark.parametrize(
    "changed,wrong_position",
    [
        (SIG_TRACK, {"x": 99.0875, "y": 102.0}),
        (SIG_TRACK, {"x": 99.0875, "y": 101.0}),
        (SIG_TRACK, {"x": -123.456789, "y": 987.654321}),
        (SIG_TRACK, {"x": 99.0875001, "y": 100.0}),
        (R1_1, {"x": 100.9125, "y": 100.0}),
        (VIA, {"x": 99.087501, "y": 102.0}),
    ],
    ids=["track-end", "track-midpoint", "arbitrary", "fractional-nm", "pad-center", "via-center"],
)
def test_same_pair_reference_must_match_each_saved_board_before_retention(
    tmp_path, role, changed, wrong_position, monkeypatch
):
    before = saved_board(tmp_path, "before")
    after = saved_board(tmp_path, "after", x="98.5")
    identities = (changed, SIG_TRACK) if changed != SIG_TRACK else (R1_1, SIG_TRACK)
    old = report(before, edges=[finding(before, identities, kind="unconnected_items")])
    new = report(after, edges=[finding(after, identities, kind="unconnected_items")])
    for label, value in (("baseline", old), ("candidate", new)):
        if role in {label, "both"}:
            item = next(
                item for item in value["unconnected_items"][0]["items"] if item["uuid"] == changed
            )
            item["pos"] = copy.deepcopy(wrong_position)
    originals = copy.deepcopy((old, new))
    saved_bytes = [before.path.read_bytes(), after.path.read_bytes()]

    def partition_must_not_start(*_args, **_kwargs):
        pytest.fail("Grouping started before all report reference points were verified")

    monkeypatch.setattr("kicad_cli.move_risk._partition", partition_must_not_start)
    with pytest.raises(RiskIncomplete, match="report reference point.*GetPosition"):
        compare_reports(old, new, before, after)
    assert (old, new) == originals
    assert [before.path.read_bytes(), after.path.read_bytes()] == saved_bytes


def test_candidate_moved_pad_cannot_reuse_its_old_world_reference(tmp_path):
    before = saved_board(tmp_path, "before")
    after = saved_board(tmp_path, "after", x="98.5")
    old = report(before, edges=[finding(before, (R1_1, SIG_TRACK), kind="unconnected_items")])
    new = report(after, edges=[finding(after, (R1_1, SIG_TRACK), kind="unconnected_items")])
    new["unconnected_items"][0]["items"][0]["pos"] = copy.deepcopy(
        old["unconnected_items"][0]["items"][0]["pos"]
    )
    with pytest.raises(RiskIncomplete, match="Candidate.*saved pad GetPosition"):
        compare_reports(old, new, before, after)


@pytest.mark.parametrize("kind", ["zone", "arc"])
@pytest.mark.parametrize("role", ["baseline", "candidate"])
def test_unproved_edge_reference_kind_is_incomplete_even_for_the_same_pair(tmp_path, kind, role):
    def edit(source):
        return (
            source[:-1]
            + f'''(arc (start 90 90) (mid 91 91) (end 92 90) (width 0.25)
            (layer "F.Cu") (net "GND") (uuid "{SECOND_VIA}")))'''
        )

    before = saved_board(tmp_path, "before", edit=edit)
    after = saved_board(tmp_path, "after", x="98.5", edit=edit)
    identity = ZONE if kind == "zone" else SECOND_VIA
    old, new = report(before), report(after)
    model, target = (before, old) if role == "baseline" else (after, new)
    target["unconnected_items"] = [finding(model, (identity, R2_2), kind="unconnected_items")]
    with pytest.raises(RiskIncomplete, match="unsupported object kind: " + kind):
        compare_reports(old, new, before, after)


@pytest.mark.parametrize("angle", ["0", "90", "180"])
def test_valid_world_pad_reference_can_change_while_same_pair_is_retained(tmp_path, angle):
    before = saved_board(tmp_path, "before", angle=angle)
    after = saved_board(tmp_path, "after", x="98.5", angle=angle)
    old = report(before, edges=[finding(before, (R1_1, VIA), kind="unconnected_items")])
    new = report(after, edges=[finding(after, (VIA, R1_1), kind="unconnected_items")])
    originals = copy.deepcopy((old, new))
    result = compare_reports(old, new, before, after)
    assert result["status"] == "passed"
    changes = result["connection_changes"]
    assert changes["counts"] == {"new": 0, "removed": 0, "retained": 1}
    retained = changes["reported_connection_edges"]["retained"][0]
    for board, label in ((before, "before"), (after, "after")):
        indexed = object_index(board)
        for item in retained[label]["items"]:
            assert item["reported_position_nm"] == indexed[item["uuid"]]["position_nm"]
    assert (old, new) == originals


def test_new_reported_endpoint_pair_blocks_even_when_an_old_pair_disappears(boards):
    before, after = boards
    result = compare_reports(
        report(before, edges=[finding(before, (R1_1, SIG_TRACK), kind="unconnected_items")]),
        report(after, edges=[finding(after, (R1_2, OUT_TRACK), kind="unconnected_items")]),
        before,
        after,
    )
    assert result["status"] == "blocked"
    assert result["connection_changes"]["counts"] == {"new": 1, "removed": 1, "retained": 0}


def test_same_pair_machine_net_change_is_not_retained(tmp_path):
    before = saved_board(tmp_path, "before")
    after = saved_board(tmp_path, "after", sig_net="/DIFFERENT")
    result = compare_reports(
        report(before, edges=[finding(before, (R1_1, SIG_TRACK), kind="unconnected_items")]),
        report(after, edges=[finding(after, (R1_1, SIG_TRACK), kind="unconnected_items")]),
        before,
        after,
    )
    assert result["status"] == "blocked"
    assert result["connection_changes"]["counts"]["new"] == 1


def test_general_violation_position_change_conservatively_blocks(tmp_path):
    before = saved_board(tmp_path, "before")
    after = saved_board(tmp_path, "after", x="98.5")
    result = compare_reports(
        report(before, violations=[finding(before)]),
        report(after, violations=[finding(after)]),
        before,
        after,
    )
    assert result["status"] == "blocked"
    assert len(result["new_findings"]) == len(result["removed_findings"]) == 1


def test_fixed_r1_negative_explains_out_pad_sig_copper_and_reported_disconnection(tmp_path):
    before = saved_board(tmp_path, "before")
    after = saved_board(tmp_path, "after", x="98.5")
    old = report(before, edges=[finding(before, (R2_1, OUT_TRACK), kind="unconnected_items")])
    new = report(
        after,
        violations=[
            finding(after, kind="shorting_items"),
            finding(after, kind="solder_mask_bridge"),
        ],
        edges=[
            finding(after, (R2_1, OUT_TRACK), kind="unconnected_items"),
            finding(after, (R1_1, SIG_TRACK), kind="unconnected_items"),
        ],
    )
    old_copy, new_copy = copy.deepcopy(old), copy.deepcopy(new)
    result = compare_reports(old, new, before, after)
    assert old == old_copy and new == new_copy
    assert result["status"] == "blocked"
    assert {item["type"] for item in result["new_findings"]} == {
        "shorting_items",
        "solder_mask_bridge",
    }
    related = {item["uuid"]: item["object"] for item in result["new_findings"][0]["items"]}
    assert (related[R1_2]["ref"], related[R1_2]["number"], related[R1_2]["net"]) == (
        "R1",
        "2",
        "/OUT",
    )
    assert related[SIG_TRACK]["kind"] == "segment" and related[SIG_TRACK]["net"] == "/SIG"
    assert result["connection_changes"]["counts"] == {"new": 1, "removed": 0, "retained": 1}
    moved = result["moved_footprints"]
    assert len(moved) == 1 and moved[0]["uuid"] == R1
    assert moved[0]["before_position_nm"] == [100_000_000, 100_000_000]
    assert moved[0]["after_position_nm"] == [98_500_000, 100_000_000]
    assert moved[0]["copper_follows_footprint"] is False
    pads = {pad["number"]: pad for pad in moved[0]["pads"]}
    assert pads["2"]["before_world_nm"] == [100_912_500, 100_000_000]
    assert pads["2"]["after_world_nm"] == [99_412_500, 100_000_000]
    assert pads["2"]["net_before"] == pads["2"]["net_after"] == "/OUT"


def _change_object(source, kind, identity, transform):
    pattern = rf'\({kind}\b.*?\(uuid "{identity}"\)\)'
    match = re.search(pattern, source, flags=re.DOTALL)
    assert match is not None
    return source[: match.start()] + transform(match[0]) + source[match.end() :]


def _alias_board(tmp_path, name, *, moved=False, edit=None):
    def alias_scene(source):
        # A remote /SIG pad is unchanged; only unrelated R3 is translated.
        source = source.replace(f'(net "/OUT") (uuid "{R2_1}")', f'(net "/SIG") (uuid "{R2_1}")')
        x = "109.5" if moved else "110"
        extra = f"""(footprint "fixture:R_0805" (layer "F.Cu") (at {x} 110)
          (property "Reference" "R3") (property "Value" "10k") (uuid "{R3}")
          (pad "1" smd rect (at -0.9125 0) (size 1 1)
            (layers "F.Cu" "F.Mask") (net "EN") (uuid "{R3_1}"))
          (pad "2" smd rect (at 0.9125 0) (size 1 1)
            (layers "F.Cu" "F.Mask") (net "GND") (uuid "{R3_2}")))
        (segment (start 99.0875 102) (end 104.0875 102) (width 0.25)
          (layer "B.Cu") (net "/SIG") (uuid "{B_TRACK}"))"""
        source = source[:-1] + extra + ")"
        return edit(source) if edit else source

    return saved_board(tmp_path, name, edit=alias_scene)


def _alias_finding(board, identity, *, common=R2_1):
    # KiCad 10.0.6 reports track.GetPosition() (start), not the joint endpoint.
    return finding(board, (identity, common), kind="unconnected_items")


def _alias_comparison(before, after, *, old=SIG_TRACK, new=VIA, old_edit=None, new_edit=None):
    old_row, new_row = _alias_finding(before, old), _alias_finding(after, new)
    if old_edit:
        old_edit(old_row)
    if new_edit:
        new_edit(new_row)
    return compare_reports(
        report(before, edges=[old_row]), report(after, edges=[new_row]), before, after
    )


@pytest.mark.parametrize("old,new", [(SIG_TRACK, VIA), (VIA, SIG_TRACK), (SIG_TRACK, B_TRACK)])
def test_one_unchanged_via_junction_proves_a_unique_report_anchor_alias(tmp_path, old, new):
    before = _alias_board(tmp_path, "before")
    after = _alias_board(tmp_path, "after", moved=True)
    result = _alias_comparison(before, after, old=old, new=new)
    assert result["status"] == "passed"
    changes = result["connection_changes"]
    # Raw official changes are preserved, rather than rewritten into retained edges.
    assert changes["counts"] == {"new": 1, "removed": 1, "retained": 0}
    assert changes["reported_connection_edges"]["new"][0]["items"][0]["uuid"] == new
    assert changes["reported_connection_edges"]["removed"][0]["items"][0]["uuid"] == old
    assert changes["blocking_new_count"] == 0 and changes["blocking_new"] == []
    assert changes["equivalent_anchor_replacement_count"] == 1
    proof = changes["equivalent_anchor_replacements"][0]["proof"]
    assert proof["common_endpoint_uuid"] == R2_1
    assert proof["common_parent_footprint_uuid"] == R2
    assert proof["via_uuid"] == VIA and proof["anchor_nm"] == [99_087_500, 102_000_000]
    assert proof["net"] == "/SIG" and proof["moved_pad_nets"] == ["EN", "GND"]
    assert proof["net_disjoint_from_moved_pad_nets"] is True
    assert proof["via_diameter_nm"] == 600_000 and proof["via_drill_nm"] == 300_000
    assert len(proof["segments"]) == (2 if new == B_TRACK else 1)
    for segment in proof["segments"]:
        assert 4 * segment["length_squared_nm2"] > proof["via_diameter_nm"] ** 2
        assert segment["annular_contact_test_passed"] is True
        assert segment["shared_copper_layers"]
    assert all(
        item["before_sha256"] == item["after_sha256"]
        and item["geometry_and_machine_metadata_equal"]
        for item in proof["unchanged_objects"]
    )


def test_reported_conductor_reference_points_can_differ_from_the_proved_junction(tmp_path):
    before = _alias_board(tmp_path, "before")
    after = _alias_board(tmp_path, "after", moved=True)

    def reference_point(board):
        def edit(row):
            obj = object_index(board)[row["items"][0]["uuid"]]
            x, y = obj["position_nm"] if obj["kind"] == "via" else obj["start_nm"]
            row["items"][0]["pos"] = {"x": x / 1_000_000, "y": y / 1_000_000}

        return edit

    result = _alias_comparison(
        before,
        after,
        old=B_TRACK,
        new=SIG_TRACK,
        old_edit=reference_point(before),
        new_edit=reference_point(after),
    )
    assert result["status"] == "passed"
    changes = result["connection_changes"]
    assert changes["counts"] == {"new": 1, "removed": 1, "retained": 0}
    proof = changes["equivalent_anchor_replacements"][0]["proof"]
    assert proof["reported_position_version"] == "10.0.6"
    assert proof["junction_position_nm"] == [99_087_500, 102_000_000]
    assert proof["anchor_nm"] == proof["junction_position_nm"]
    assert proof["old_reported_position_nm"] != proof["new_reported_position_nm"]
    raw = changes["reported_connection_edges"]
    assert (
        proof["old_reported_position_nm"] == raw["removed"][0]["items"][0]["reported_position_nm"]
    )
    assert proof["new_reported_position_nm"] == raw["new"][0]["items"][0]["reported_position_nm"]
    assert changes["blocking_new_count"] == 0


@pytest.mark.parametrize("position", ["midpoint", "end"])
def test_only_the_actual_track_getposition_start_is_an_accepted_report_reference(
    tmp_path, position
):
    before = _alias_board(tmp_path, "before")
    after = _alias_board(tmp_path, "after", moved=True)

    def invalid_reference(row):
        row["items"][0]["pos"] = {"x": 99.0875, "y": 101.0 if position == "midpoint" else 102.0}

    with pytest.raises(RiskIncomplete, match="Candidate.*saved segment GetPosition"):
        _alias_comparison(before, after, old=B_TRACK, new=SIG_TRACK, new_edit=invalid_reference)


def test_unverified_native_report_version_makes_edge_reference_check_incomplete(tmp_path):
    before = _alias_board(tmp_path, "before")
    after = _alias_board(tmp_path, "after", moved=True)
    old = report(before, edges=[_alias_finding(before, B_TRACK)])
    new = report(after, edges=[_alias_finding(after, SIG_TRACK)])
    old["kicad_version"] = new["kicad_version"] = "10.0.7"
    with pytest.raises(RiskIncomplete, match="reference points are unverified for KiCad 10.0.7"):
        compare_reports(old, new, before, after)


@pytest.mark.parametrize("common_position", ["start", "end"])
def test_common_track_uses_its_original_getposition_start_without_reversing_it(
    tmp_path, common_position
):
    def same_net(source):
        return _change_object(
            source,
            "segment",
            OUT_TRACK,
            lambda node: node.replace('(net "/OUT")', '(net "/SIG")'),
        )

    before = _alias_board(tmp_path, "before", edit=same_net)
    after = _alias_board(tmp_path, "after", moved=True, edit=same_net)
    original = [before.path.read_bytes(), after.path.read_bytes()]
    old = _alias_finding(before, SIG_TRACK, common=OUT_TRACK)
    new = _alias_finding(after, VIA, common=OUT_TRACK)
    if common_position == "end":
        old["items"][1]["pos"] = new["items"][1]["pos"] = {"x": 104.0875, "y": 100.0}
    if common_position == "end":
        with pytest.raises(RiskIncomplete, match="Baseline.*saved segment GetPosition"):
            compare_reports(report(before, edges=[old]), report(after, edges=[new]), before, after)
    else:
        result = compare_reports(
            report(before, edges=[old]), report(after, edges=[new]), before, after
        )
        assert result["status"] == "passed"
        assert result["connection_changes"]["equivalent_anchor_replacement_count"] == 1
    assert [before.path.read_bytes(), after.path.read_bytes()] == original
    assert object_index(after)[OUT_TRACK]["start_nm"] == [100_912_500, 100_000_000]
    assert object_index(after)[OUT_TRACK]["end_nm"] == [104_087_500, 100_000_000]


@pytest.mark.parametrize(
    "changed", ["old-anchor", "new-anchor", "common-anchor", "severity", "excluded", "type"]
)
def test_report_anchor_or_flags_must_match_exactly_for_alias_proof(tmp_path, changed):
    before = _alias_board(tmp_path, "before")
    after = _alias_board(tmp_path, "after", moved=True)

    def mutate(row):
        if changed == "old-anchor" or changed == "new-anchor":
            row["items"][0]["pos"]["x"] += 0.000001
        elif changed == "common-anchor":
            row["items"][1]["pos"]["x"] += 0.000001
        elif changed == "severity":
            row["severity"] = "warning"
        elif changed == "excluded":
            row["excluded"] = True
        else:
            row["type"] = "different_relation"

    def compare():
        return _alias_comparison(
            before,
            after,
            old_edit=mutate if changed == "old-anchor" else None,
            new_edit=None if changed == "old-anchor" else mutate,
        )

    if changed in {"old-anchor", "new-anchor", "common-anchor"}:
        with pytest.raises(RiskIncomplete, match="report reference point.*GetPosition"):
            compare()
        return
    result = compare()
    assert result["status"] == "blocked"
    assert result["connection_changes"]["blocking_new_count"] == 1
    assert result["connection_changes"]["equivalent_anchor_replacements"] == []


@pytest.mark.parametrize("changed", ["via-bytes", "track-width", "common-parent", "common-pose"])
def test_alias_participants_and_common_parent_must_be_completely_unchanged(tmp_path, changed):
    before = _alias_board(tmp_path, "before")

    def edit(source):
        if changed == "via-bytes":
            return _change_object(source, "via", VIA, lambda node: node.replace("(size", "(size "))
        if changed == "track-width":
            return _change_object(
                source,
                "segment",
                SIG_TRACK,
                lambda node: node.replace("(width 0.25)", "(width 0.3)"),
            )
        if changed == "common-parent":
            return source.replace(
                '(property "Reference" "R2") (property "Value" "10k")',
                '(property "Reference" "R2") (property "Value" "22k")',
            )
        return source.replace('(layer "F.Cu") (at 105 100)', '(layer "F.Cu") (at 105.001 100)')

    after = _alias_board(tmp_path, "after", moved=True, edit=edit)
    result = _alias_comparison(before, after)
    assert result["status"] == "blocked"
    assert result["connection_changes"]["equivalent_anchor_replacements"] == []


@pytest.mark.parametrize(
    "unsafe",
    [
        "zero-width",
        "negative-width",
        "short-segment",
        "exact-outer-radius",
        "not-at-endpoint",
        "different-net",
        "different-layer",
        "no-annulus",
        "zero-drill",
        "zero-diameter",
        "unknown-via-field",
        "via-padstack",
        "bare-via-atom",
        "track-extra-geometry",
        "via-size-extra-atom",
    ],
)
def test_alias_requires_the_declared_simple_annular_contact_geometry(tmp_path, unsafe):
    def edit(source):
        def track(node):
            if unsafe == "zero-width":
                return node.replace("(width 0.25)", "(width 0)")
            if unsafe == "negative-width":
                return node.replace("(width 0.25)", "(width -0.25)")
            if unsafe == "short-segment":
                return node.replace("(start 99.0875 100)", "(start 99.0875 101.9)")
            if unsafe == "exact-outer-radius":
                return node.replace("(start 99.0875 100)", "(start 99.0875 101.7)")
            if unsafe == "not-at-endpoint":
                return node.replace("(end 99.0875 102)", "(end 99.087501 102)")
            if unsafe == "different-net":
                return node.replace('(net "/SIG")', '(net "/DIFFERENT")')
            if unsafe == "different-layer":
                return node.replace('(layer "F.Cu")', '(layer "In1.Cu")')
            if unsafe == "track-extra-geometry":
                return node[:-1] + " (teardrops (enabled yes)))"
            return node

        def via(node):
            if unsafe == "no-annulus":
                return node.replace("(drill 0.3)", "(drill 0.6)")
            if unsafe == "zero-drill":
                return node.replace("(drill 0.3)", "(drill 0)")
            if unsafe == "zero-diameter":
                return node.replace("(size 0.6)", "(size 0)")
            if unsafe == "unknown-via-field":
                return node[:-1] + " (remove_unused_layers yes))"
            if unsafe == "via-padstack":
                return node[:-1] + ' (padstack (layer "F.Cu" (size 0.1))))'
            if unsafe == "bare-via-atom":
                return node.replace("(via", "(via free", 1)
            if unsafe == "via-size-extra-atom":
                return node.replace("(size 0.6)", "(size 0.6 0.7)")
            return node

        source = _change_object(source, "segment", SIG_TRACK, track)
        return _change_object(source, "via", VIA, via)

    before = _alias_board(tmp_path, "before", edit=edit)
    after = _alias_board(tmp_path, "after", moved=True, edit=edit)
    result = _alias_comparison(before, after)
    assert result["status"] == "blocked"
    assert result["connection_changes"]["blocking_new_count"] == 1
    assert result["connection_changes"]["equivalent_anchor_replacements"] == []


def test_segment_just_longer_than_outer_radius_has_exact_integer_proof(tmp_path):
    def edit(source):
        return _change_object(
            source,
            "segment",
            SIG_TRACK,
            lambda node: node.replace("(start 99.0875 100)", "(start 99.0875 101.699999)"),
        )

    before = _alias_board(tmp_path, "before", edit=edit)
    after = _alias_board(tmp_path, "after", moved=True, edit=edit)
    result = _alias_comparison(before, after)
    proof = result["connection_changes"]["equivalent_anchor_replacements"][0]["proof"]
    assert result["status"] == "passed"
    assert proof["segments"][0]["length_squared_nm2"] == 300_001**2


@pytest.mark.parametrize("inner_enabled", [False, True])
def test_alias_needs_enabled_track_copper_and_actual_outer_through_via_ends(
    tmp_path, inner_enabled
):
    def edit(source):
        if inner_enabled:
            source = source.replace(
                '(0 "F.Cu" signal) (2 "B.Cu" signal)',
                '(0 "F.Cu" signal) (1 "In1.Cu" signal) (2 "B.Cu" signal)',
            )
        source = _change_object(
            source,
            "segment",
            SIG_TRACK,
            lambda node: node.replace('(layer "F.Cu")', '(layer "In1.Cu")'),
        )
        return _change_object(
            source,
            "via",
            VIA,
            lambda node: node.replace('(layers "F.Cu" "B.Cu")', '(layers "F.Cu" "In1.Cu")'),
        )

    before = _alias_board(tmp_path, "before", edit=edit)
    after = _alias_board(tmp_path, "after", moved=True, edit=edit)
    indexed = object_index(before)
    assert indexed[SIG_TRACK]["simple_anchor_alias_geometry"] is inner_enabled
    assert indexed[VIA]["simple_anchor_alias_geometry"] is False
    result = _alias_comparison(before, after)
    assert result["status"] == "blocked"
    assert result["connection_changes"]["blocking_new_count"] == 1
    assert result["connection_changes"]["equivalent_anchor_replacements"] == []


@pytest.mark.parametrize("when", ["before", "after", "both"])
def test_any_moved_pad_net_intersection_keeps_new_edge_blocking(tmp_path, when):
    def moved_net(source):
        return source.replace(f'(net "EN") (uuid "{R3_1}")', f'(net "/SIG") (uuid "{R3_1}")')

    before = _alias_board(tmp_path, "before", edit=moved_net if when != "after" else None)
    after = _alias_board(
        tmp_path, "after", moved=True, edit=moved_net if when != "before" else None
    )
    result = _alias_comparison(before, after)
    assert result["status"] == "blocked"
    assert result["connection_changes"]["equivalent_anchor_replacements"] == []


@pytest.mark.parametrize("witness", ["absent", "two", "changed", "different-net", "wrong-center"])
def test_segment_to_segment_needs_one_unique_unchanged_direct_via(tmp_path, witness):
    def edit(source):
        def change(node):
            if witness == "absent":
                return ""
            if witness == "two":
                return node + node.replace(VIA, SECOND_VIA)
            if witness == "different-net":
                return node.replace('(net "/SIG")', '(net "/OTHER")')
            if witness == "wrong-center":
                return node.replace("(at 99.0875 102)", "(at 99.087501 102)")
            return node.replace("(size 0.6)", "(size 0.7)")

        return _change_object(source, "via", VIA, change)

    before = _alias_board(tmp_path, "before", edit=None if witness == "changed" else edit)
    after = _alias_board(tmp_path, "after", moved=True, edit=edit)
    result = _alias_comparison(before, after, new=B_TRACK)
    assert result["status"] == "blocked"
    assert result["connection_changes"]["equivalent_anchor_replacements"] == []


def test_ambiguous_one_to_many_report_replacement_cannot_be_matched_greedily(tmp_path):
    before = _alias_board(tmp_path, "before")
    after = _alias_board(tmp_path, "after", moved=True)
    old = _alias_finding(before, SIG_TRACK)
    result = compare_reports(
        report(before, edges=[old, copy.deepcopy(old)]),
        report(after, edges=[_alias_finding(after, VIA)]),
        before,
        after,
    )
    changes = result["connection_changes"]
    assert result["status"] == "blocked"
    assert changes["counts"] == {"new": 1, "removed": 2, "retained": 0}
    assert changes["equivalent_anchor_replacements"] == []


def test_one_old_edge_with_two_candidate_aliases_is_reverse_ambiguous(tmp_path):
    before = _alias_board(tmp_path, "before")
    after = _alias_board(tmp_path, "after", moved=True)
    result = compare_reports(
        report(before, edges=[_alias_finding(before, SIG_TRACK)]),
        report(after, edges=[_alias_finding(after, VIA), _alias_finding(after, B_TRACK)]),
        before,
        after,
    )
    changes = result["connection_changes"]
    assert result["status"] == "blocked"
    assert changes["counts"] == {"new": 2, "removed": 1, "retained": 0}
    assert changes["blocking_new_count"] == 2
    assert changes["equivalent_anchor_replacements"] == []
    assert {item["items"][0]["uuid"] for item in changes["blocking_new"]} == {VIA, B_TRACK}


def test_valid_alias_cannot_hide_a_new_violation_or_an_unrelated_new_pair(tmp_path):
    before = _alias_board(tmp_path, "before")
    after = _alias_board(tmp_path, "after", moved=True)
    result = compare_reports(
        report(before, edges=[_alias_finding(before, SIG_TRACK)]),
        report(
            after,
            violations=[finding(after, kind="shorting_items")],
            edges=[
                _alias_finding(after, VIA),
                finding(after, (R1_2, OUT_TRACK), kind="unconnected_items"),
            ],
        ),
        before,
        after,
    )
    assert result["status"] == "blocked"
    assert len(result["new_findings"]) == 1
    assert result["connection_changes"]["equivalent_anchor_replacement_count"] == 1
    assert result["connection_changes"]["blocking_new_count"] == 1
