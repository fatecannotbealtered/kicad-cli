"""Existing measured geometry can block apply; render judgment remains separate."""

from test_driver_engineering_runtime import (  # noqa: F401 - owned public fixture
    cli,
    schematic_case,
)

from kicad_cli.engineering_feedback import can_apply, feedback


def test_native_silk_remaining_conflicts_and_incomplete_measurements_are_not_clean():
    bad = feedback(
        "board silkscreen place",
        {
            "references": 1,
            "clearance_mm": 0.15,
            "checks": [{"ref": "R1", "inside_edge": True, "clear": False}],
        },
    )
    assert bad["geometry_check"] == "measured_native_rectangular_bounds"
    assert bad["definite_violations"][0]["ref"] == "R1" and not can_apply(bad)
    unknown = feedback("board silkscreen place", {"references": 1, "checks": []})
    assert unknown["geometry_check"] == "incomplete" and not can_apply(unknown)
    good = feedback(
        "board silkscreen place",
        {"references": 1, "checks": [{"ref": "R1", "inside_edge": True, "clear": True}]},
    )
    assert can_apply(good) and good["requires_visual_review"] and good["unsupported"]


def test_public_preview_blocks_a_measured_conflict_without_writing(schematic_case):  # noqa: F811
    root, env, args = schematic_case
    target = root / "edit.kicad_sch"
    original = target.read_bytes()
    shim = """
import sys
from kicad_cli import engineering_feedback
from kicad_cli.main import main
engineering_feedback.feedback = lambda *a, **k: {
 'definite_violations':[{'ref':'R1','clear':False}],
 'geometry_check':'measured_native_rectangular_bounds',
 'requires_visual_review':['Source-only controlled measurement'], 'unsupported':[]}
main(sys.argv[1:])
"""
    process, result = cli(env, *args, "--dry-run", shim=shim)
    assert process.returncode == 0 and result["ok"], result
    assert result["data"]["preview"]["can_apply"] is False
    assert target.read_bytes() == original
