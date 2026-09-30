"""What a board's project asks DRC to check, and how hard.

A board's rules live beside it, in its `.kicad_pro`: the board setup's
minimums (`board.design_settings.rules`), each check's severity
(`rule_severities`, "ignore" turning it off), the violations someone has
excluded (`drc_exclusions`), and the net classes (`net_settings`). Whatever
the project leaves out, a new KiCad 10 project's value stands in -- and a
board with no project at all is checked as a new project would check it.
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

NM = 1_000_000

# The checks a KiCad 10 project knows, at the severity a new project gives
# them ("ignore" is off).
SEVERITIES = {
    "annular_width": "error", "clearance": "error", "connection_width": "warning",
    "copper_edge_clearance": "error", "copper_sliver": "warning",
    "courtyards_overlap": "error", "creepage": "error",
    "diff_pair_gap_out_of_range": "error", "diff_pair_uncoupled_length_too_long": "error",
    "drill_out_of_range": "error", "duplicate_footprints": "warning",
    "extra_footprint": "warning", "footprint": "error", "footprint_filters_mismatch": "ignore",
    "footprint_symbol_field_mismatch": "warning", "footprint_symbol_mismatch": "warning",
    "footprint_type_mismatch": "ignore", "hole_clearance": "error", "hole_to_hole": "warning",
    "holes_co_located": "warning", "invalid_outline": "error", "isolated_copper": "warning",
    "item_on_disabled_layer": "error", "items_not_allowed": "error",
    "length_out_of_range": "error", "lib_footprint_issues": "warning",
    "lib_footprint_mismatch": "warning", "malformed_courtyard": "error",
    "microvia_drill_out_of_range": "error", "mirrored_text_on_front_layer": "warning",
    "missing_courtyard": "ignore", "missing_footprint": "warning",
    "missing_tuning_profile": "warning", "net_conflict": "warning",
    "nonmirrored_text_on_back_layer": "warning", "npth_inside_courtyard": "error",
    "padstack": "warning", "padstack_invalid": "error", "pth_inside_courtyard": "error",
    "shorting_items": "error", "silk_edge_clearance": "warning",
    "silk_over_copper": "warning", "silk_overlap": "warning", "skew_out_of_range": "error",
    "solder_mask_bridge": "error", "starved_thermal": "error", "text_height": "warning",
    "text_on_edge_cuts": "error", "text_thickness": "warning",
    "through_hole_pad_without_hole": "error", "too_many_vias": "error", "track_angle": "error",
    "track_dangling": "warning", "track_not_centered_on_via": "ignore",
    "track_on_post_machined_layer": "error", "track_segment_length": "error",
    "track_width": "error", "tracks_crossing": "error",
    "tuning_profile_track_geometries": "ignore", "unconnected_items": "error",
    "unresolved_variable": "error", "via_dangling": "warning", "via_diameter": "error",
    "zones_intersect": "error",
}  # fmt: skip

# The board setup's minimums a new KiCad 10 project has, in millimetres.
RULES = {
    "min_clearance": 0.0, "min_connection": 0.0, "min_copper_edge_clearance": 0.5,
    "min_groove_width": 0.0, "min_hole_clearance": 0.25, "min_hole_to_hole": 0.25,
    "min_microvia_diameter": 0.2, "min_microvia_drill": 0.1, "min_resolved_spokes": 2,
    "min_silk_clearance": 0.0, "min_text_height": 0.8, "min_text_thickness": 0.08,
    "min_through_hole_diameter": 0.3, "min_track_width": 0.2, "min_via_annular_width": 0.1,
    "min_via_diameter": 0.5, "solder_mask_to_copper_clearance": 0.0, "max_error": 0.005,
}  # fmt: skip

# What a net class leaves undefined comes from Default, and what Default
# leaves undefined from these: a new project's Default class, in millimetres.
DEFAULT_CLASS = {
    "clearance": 0.2, "track_width": 0.2, "via_diameter": 0.6, "via_drill": 0.3,
    "microvia_diameter": 0.3, "microvia_drill": 0.1, "diff_pair_width": 0.2,
    "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
}  # fmt: skip
NULL_UUID = "00000000-0000-0000-0000-000000000000"


@dataclass(frozen=True)
class NetClass:
    """A net's class as DRC sees it: its name -- several, when patterns put
    the net in several -- and each value, in nm, from the first of them to
    define it."""

    name: str
    values: dict[str, int] = field(hash=False, compare=False)

    def nm(self, key: str) -> int:
        return self.values[key]


class NetClasses:
    """Which class a net is in: by name first (`netclass_assignments`), then
    by pattern, a wildcard ("osc*") or a regular expression ("uio\\d+") that
    matches the whole name. Classes of several are ordered by priority, the
    lowest number first, and each value is the first one of them to set it."""

    def __init__(self, settings: dict) -> None:
        self.classes = {c.get("name"): c for c in settings.get("classes") or [] if c.get("name")}
        self.assigned = settings.get("netclass_assignments") or {}
        self.patterns = [
            (entry.get("pattern", ""), entry.get("netclass", "Default"))
            for entry in settings.get("netclass_patterns") or []
        ]
        self._cache: dict[str, NetClass] = {}

    def of(self, net: str) -> NetClass:
        found = self._cache.get(net)
        if found is None:
            found = self._cache[net] = self._resolve(net)
        return found

    def _resolve(self, net: str) -> NetClass:
        if net in self.assigned:
            given = self.assigned[net]
            matched = list(given if isinstance(given, list) else [given])
        else:
            matched = [name for pattern, name in self.patterns if net and _matches(pattern, net)]
        matched = [c for c in dict.fromkeys(matched) if c != "Default" and c in self.classes]
        matched.sort(key=lambda c: self.classes[c].get("priority", 0))
        values: dict[str, int] = {}
        chain = [self.classes[c] for c in matched] + [self.classes.get("Default", {})]
        for key, fallback in DEFAULT_CLASS.items():
            value = next((c[key] for c in chain if isinstance(c.get(key), (int, float))), fallback)
            values[key] = round(value * NM)
        undefined = any(
            not any(isinstance(self.classes[c].get(k), (int, float)) for c in matched)
            for k in DEFAULT_CLASS
        )
        names = matched + (["Default"] if not matched or undefined else [])
        return NetClass(",".join(names), values)


def _matches(pattern: str, net: str) -> bool:
    if fnmatch.fnmatchcase(net, pattern):
        return True
    try:
        return re.fullmatch(pattern, net) is not None
    except re.error:
        return False


@dataclass
class Settings:
    rules: dict[str, float]  # as the project has them: lengths in mm
    severities: dict[str, str]
    # (rule, the items' uuids) -> the comment someone left with it
    exclusions: dict[tuple[str, frozenset[str]], str]
    netclasses: NetClasses
    project: Path | None = None
    # Checks the project's custom rules decide: check -> the first rule that
    # does (`rules.py`). Not made until the rules are read.
    custom: dict[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, project: Path | None) -> Settings:
        data: dict = {}
        if project is not None and project.is_file():
            try:
                data = json.loads(project.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
        design = (data.get("board") or {}).get("design_settings") or {}
        rules = dict(RULES)
        rules.update({k: v for k, v in (design.get("rules") or {}).items()
                      if isinstance(v, (int, float))})  # fmt: skip
        severities = dict(SEVERITIES)
        severities.update(design.get("rule_severities") or {})
        from . import rules as custom_rules  # noqa: PLC0415

        return cls(
            rules=rules,
            severities=severities,
            exclusions=_exclusions(design.get("drc_exclusions") or []),
            netclasses=NetClasses(data.get("net_settings") or {}),
            project=project,
            custom=custom_rules.constrained(project),
        )

    def nm(self, rule: str) -> int:
        """A board setup length, in nm."""
        return round(self.rules[rule] * NM)


def _exclusions(entries: list) -> dict[tuple[str, frozenset[str]], str]:
    """KiCad writes an exclusion as "rule|x|y|uuid|uuid", with a comment
    since KiCad 9. It is matched here by its rule and items: where KiCad put
    the marker is not reproduced."""
    out = {}
    for entry in entries:
        text, comment = (entry[0], entry[1] if len(entry) > 1 else "") if isinstance(
            entry, list) else (entry, "")  # fmt: skip
        if not isinstance(text, str):
            continue
        parts = text.split("|")
        if len(parts) < 4:
            continue
        uuids = frozenset(u for u in parts[3:] if u and u != NULL_UUID)
        out[(parts[0], uuids)] = comment or ""
    return out
