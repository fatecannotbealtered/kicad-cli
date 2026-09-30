"""A board's custom rules: the `.kicad_dru` file beside it.

Custom rules can loosen what the board setup demands as well as tighten it
-- `(constraint hole_size (min 0.2mm))` under an FPGA -- so a check they
touch cannot be made from the board setup alone. Until they are read, the
checks a board's rules constrain are not made, and are said not to be.
"""

from __future__ import annotations

from pathlib import Path

from ..sexpr import Document, SexprError

# Which checks each kind of constraint decides.
CHECKS = {
    "clearance": ("clearance",),
    "physical_clearance": ("clearance",),
    "hole_clearance": ("hole_clearance",),
    "physical_hole_clearance": ("hole_clearance",),
    "edge_clearance": ("copper_edge_clearance", "silk_edge_clearance"),
    "hole_size": ("drill_out_of_range", "microvia_drill_out_of_range"),
    "hole_to_hole": ("hole_to_hole",),
    "courtyard_clearance": ("courtyards_overlap",),
    "silk_clearance": ("silk_overlap", "silk_over_copper"),
    "text_height": ("text_height",),
    "text_thickness": ("text_thickness",),
    "track_width": ("track_width",),
    "annular_width": ("annular_width",),
    "via_diameter": ("via_diameter",),
    "disallow": ("items_not_allowed",),
    "length": ("length_out_of_range",),
    "skew": ("skew_out_of_range",),
    "diff_pair_gap": ("diff_pair_gap_out_of_range",),
    "diff_pair_uncoupled": ("diff_pair_uncoupled_length_too_long",),
    "via_count": ("too_many_vias",),
    "connection_width": ("connection_width",),
    "track_angle": ("track_angle",),
    "track_segment_length": ("track_segment_length",),
    "min_resolved_spokes": ("starved_thermal",),
    "thermal_spoke_width": ("starved_thermal",),
    "thermal_relief_gap": ("starved_thermal",),
    "zone_connection": ("starved_thermal",),
    "creepage": ("creepage",),
    "solder_mask_expansion": ("solder_mask_bridge",),
    "bridged_mask": ("solder_mask_bridge",),
}


def constrained(project: Path | None) -> dict[str, str]:
    """The checks the project's custom rules decide: check -> the rule that
    constrains it first. The rules are the project's `.kicad_dru`."""
    if project is None:
        return {}
    source = project.with_suffix(".kicad_dru")
    if not source.is_file():
        return {}
    try:
        text = source.read_text(encoding="utf-8")
        # A line starting with # is a comment.
        text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        root = Document.parse(f"(rules {text})", lazy=False).root
    except (OSError, UnicodeDecodeError, SexprError):
        # A rules file KiCad could not read either: every check it might
        # decide is uncertain.
        return {check: "(unreadable)" for checks in CHECKS.values() for check in checks}
    out: dict[str, str] = {}
    for rule in root.find_all("rule"):
        name = rule.value(1) or ""
        for constraint in rule.find_all("constraint"):
            for check in CHECKS.get(constraint.atom(1) or "", ()):
                out.setdefault(check, name)
    return out
