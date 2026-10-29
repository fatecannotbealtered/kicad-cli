"""Counterexamples for independent acceptance; no product writer imports."""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))
from consume_changed_copper import consume_tracks
from consume_engineering_outputs import gerber
from run_engineering_change import change_checks


def artwork(path, polarity="D", extra=""):
    path.write_text(
        "%FSLAX46Y46*%\n%MOMM*%\nG01*\n%ADD10C,0.250000*%\n"
        f"%LP{polarity}*%\n{extra}D10*\nX1000000Y-1000000D02*\n"
        "X2000000Y-1000000D01*\nM02*\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("polarity", ["C"])
def test_clear_polarity_cannot_prove_changed_copper(tmp_path, polarity):
    (tmp_path / "gerber").mkdir()
    for layer in ("F_Cu", "B_Cu"):
        artwork(tmp_path / "gerber" / (layer + "-" + layer + ".gbr"), polarity)
    native = {
        "origin_nm": [0, 0],
        "track_geometry": [
            {
                "net": "VIN",
                "kind": "PCB_TRACK",
                "layer": "F.Cu",
                "start_nm": [1000000, 1000000],
                "end_nm": [2000000, 1000000],
                "width_nm": 250000,
            }
        ],
    }
    with pytest.raises(ValueError, match="polarity"):
        consume_tracks(tmp_path, native, ["VIN"])


def test_dark_straight_geometry_is_still_consumed(tmp_path):
    path = tmp_path / "dark.gbr"
    artwork(path)
    assert gerber(path)["straight_strokes"] == [((1000000, 1000000), (2000000, 1000000), 250000)]


@pytest.mark.parametrize("form", ["direct", "comment"])
@pytest.mark.parametrize("polarity", ["Positive", "Negative", "Unknown"])
def test_file_polarity_attributes_have_identical_semantics(tmp_path, form, polarity):
    command = "TF.FilePolarity," + polarity
    extra = f"%{command}*%\n" if form == "direct" else f"G04 #@! {command}*\n"
    path = tmp_path / "attribute.gbr"
    artwork(path, extra=extra)
    if polarity == "Positive":
        assert gerber(path)["straight_strokes"] == [
            ((1000000, 1000000), (2000000, 1000000), 250000)
        ]
    else:
        with pytest.raises(ValueError, match="polarity"):
            gerber(path)


def test_ordinary_comment_is_not_an_attribute(tmp_path):
    path = tmp_path / "comment.gbr"
    artwork(path, extra="G04 Ordinary note: TF.FilePolarity,Negative*\n")
    assert len(gerber(path)["straight_strokes"]) == 1


@pytest.mark.parametrize("extra", ["%SRX2Y1I3J0*%\n", "%LMX*%\n", "G02*\n", "G99*\n"])
def test_unknown_or_transformed_geometry_is_not_ignored(tmp_path, extra):
    path = tmp_path / "unsupported.gbr"
    artwork(path, extra=extra)
    with pytest.raises(ValueError):
        gerber(path)


def observations():
    baseline = {
        "footprints": [],
        "locked_refs": [],
        "identity_text": [],
        "track_geometry": [],
        "edge_bbox_nm": [0, 0, 10, 10],
        "zone_geometry": [],
        "edge_geometry": {
            "status": "supported",
            "segments": [
                {"start_nm": [0, 0], "end_nm": [0, 10], "width_nm": 1},
                {"start_nm": [0, 0], "end_nm": [10, 0], "width_nm": 1},
                {"start_nm": [0, 10], "end_nm": [10, 10], "width_nm": 1},
                {"start_nm": [10, 0], "end_nm": [10, 10], "width_nm": 1},
            ],
        },
    }
    intent = {
        "parts": [],
        "edit": {},
        "physical_pad_mapping": {},
        "baseline_positions_mm": {},
        "pose": {"locked_refs": []},
        "copper": {"protected_nets": ["GND"]},
    }
    return baseline, intent


def test_equal_bbox_different_closed_outline_must_fail():
    baseline, intent = observations()
    changed = copy.deepcopy(baseline)
    changed["edge_geometry"]["segments"][1:2] = [
        {"start_nm": [0, 0], "end_nm": [3, 0], "width_nm": 1},
        {"start_nm": [3, 0], "end_nm": [3, 2], "width_nm": 1},
        {"start_nm": [3, 2], "end_nm": [7, 2], "width_nm": 1},
        {"start_nm": [7, 0], "end_nm": [7, 2], "width_nm": 1},
        {"start_nm": [7, 0], "end_nm": [10, 0], "width_nm": 1},
    ]
    assert changed["edge_bbox_nm"] == baseline["edge_bbox_nm"]
    result = change_checks(changed, baseline, intent, moved=False, edited=False)
    assert result["status"] == "failed"
    assert any(c["id"] == "edge-geometry" and not c["passed"] for c in result["checks"])


def test_unverified_outline_is_not_equal_by_default():
    baseline, intent = observations()
    baseline["edge_geometry"] = {"status": "unsupported", "reason": "arc"}
    result = change_checks(copy.deepcopy(baseline), baseline, intent, moved=False, edited=False)
    assert result["status"] == "failed"
