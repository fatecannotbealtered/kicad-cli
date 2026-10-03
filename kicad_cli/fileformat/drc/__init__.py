"""The design rules check, as KiCad's DRC checks a board -- in this process.

Each check was measured, not assumed: `tests/fixtures/drc/` holds boards
drawn to ask KiCad's own DRC one question per case, and its answers are
recorded beside them; the demo boards KiCad installs are the rest of the
evidence. What each check found is written where it is made (`local.py`,
`holes.py`, `zones.py`, `connections.py`, `courtyards.py`).

The rules and their severities are the project's (`settings.py`): what it
leaves out, a new KiCad 10 project supplies. A check at "ignore" is not run.
An excluded violation is still reported, marked excluded, as KiCad's report
marks it.

What is not checked here is said to be, per check, in `not_checked()` --
never silently passed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..board import Board
from .items import Item, millimetres
from .settings import SEVERITIES, Settings

__all__ = [
    "CHECKED", "NOT_CHECKED", "PARTIAL", "Item", "Report", "Settings", "Violation", "check",
]  # fmt: skip

NM = 1_000_000


@dataclass
class Violation:
    rule: str
    severity: str
    message: str
    items: list[Item] = field(default_factory=list)
    excluded: bool = False
    comment: str = ""

    def uuids(self) -> frozenset[str]:
        return frozenset(i.uuid for i in self.items if i.uuid)

    def to_dict(self) -> dict:
        out = {
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "items": [i.to_dict() for i in self.items],
        }
        if self.excluded:
            out["excluded"] = True
            if self.comment:
                out["comment"] = self.comment
        return out


@dataclass
class Report:
    violations: list[Violation]
    unconnected: list[Violation]
    not_checked: dict[str, str]  # rule -> why
    ignored: list[str]  # rules the project turned off
    partial: dict[str, str] = field(default_factory=dict)  # rule -> what it leaves out
    # the project's custom rules: its .kicad_dru, whether it is read, why not
    custom_rules: dict = field(default_factory=dict)


class Run:
    """One check of one board: the board, its settings, what was found."""

    def __init__(self, board: Board, settings: Settings, kicad_root=None) -> None:
        self.board = board
        self.settings = settings
        # KiCad's installation: the library tables name its libraries
        self.kicad_root = kicad_root
        self.path: Path | None = board.path
        self.found: list[Violation] = []
        self.unconnected: list[Violation] = []
        self._seen: set[tuple[str, frozenset[str], str]] = set()
        self._joined = None
        self._copper = None

    def connectivity(self):
        """Which copper of a net touches which (`connectivity.py`), worked
        out once for every check that asks."""
        if self._joined is None:
            from .. import connectivity  # noqa: PLC0415

            self._joined = connectivity.connect(self.board)
        return self._joined

    def copper(self):
        """The board's copper as DRC measures it (`copper.py`), once."""
        if self._copper is None:
            from .copper import Copper  # noqa: PLC0415

            self._copper = Copper(self.board, self.connectivity())
        return self._copper

    def on(self, rule: str) -> bool:
        """Whether a check is made: the project has not turned it off, and
        no custom rule of its decides it."""
        return self.enabled(rule) and rule not in self.settings.custom

    def enabled(self, rule: str) -> bool:
        return self.settings.severities.get(rule, SEVERITIES.get(rule, "error")) != "ignore"

    def report(
        self,
        rule: str,
        message: str,
        items: list[Item],
        severity: str | None = None,
        once: bool = True,
    ) -> None:
        """One violation, once: KiCad names some twice, from two of its
        checks; this names each once -- but where two things are alike in
        all KiCad says of them (two islands of one zone), `once=False`.
        `severity` is the custom rule's own, where the rule deciding it
        gives one -- "ignore" reports nothing."""
        if not self.on(rule) or severity == "ignore":
            return
        uuids = frozenset(i.uuid for i in items if i.uuid)
        key = (rule, uuids, message)
        if once and key in self._seen:
            return
        self._seen.add(key)
        comment = self.settings.exclusions.get((rule, uuids))
        if severity == "exclusion":
            severity, comment = None, comment or ""
        violation = Violation(
            rule,
            severity or self.settings.severities.get(rule, SEVERITIES.get(rule, "error")),
            message,
            items,
            excluded=comment is not None,
            comment=comment or "",
        )
        (self.unconnected if rule == "unconnected_items" else self.found).append(violation)


def mm(value: float) -> str:
    """A length in a message, as KiCad writes it (`items.millimetres`)."""
    return millimetres(value)


def _checks():
    from . import (  # noqa: PLC0415
        areas,
        clearance,
        connections,
        courtyards,
        fills,
        holes,
        library,
        local,
        mask,
        outline,
        pairs,
        ruled,
        zones,
    )  # fmt: skip

    return (
        clearance.check,
        areas.check,
        outline.check,
        mask.check,
        local.layers,
        local.tracks,
        local.vias,
        local.pads,
        local.footprint_types,
        local.texts,
        holes.check,
        zones.intersecting,
        connections.check,
        courtyards.check,
        library.check,
        ruled.check,
        fills.check,
        pairs.check,
    )


# The rules checked here.
CHECKED = (
    "track_width", "via_diameter", "annular_width", "drill_out_of_range",
    "microvia_drill_out_of_range", "padstack", "padstack_invalid", "footprint_type_mismatch",
    "text_height", "text_thickness", "mirrored_text_on_front_layer",
    "nonmirrored_text_on_back_layer", "text_on_edge_cuts", "hole_to_hole", "holes_co_located",
    "zones_intersect", "unconnected_items", "track_dangling", "via_dangling",
    "courtyards_overlap", "malformed_courtyard", "missing_courtyard", "pth_inside_courtyard",
    "npth_inside_courtyard", "clearance", "shorting_items", "tracks_crossing", "hole_clearance",
    "copper_edge_clearance", "items_not_allowed", "invalid_outline", "item_on_disabled_layer",
    "through_hole_pad_without_hole", "unresolved_variable", "generic_error", "generic_warning",
    "solder_mask_bridge", "lib_footprint_issues", "lib_footprint_mismatch",
    "track_segment_length", "too_many_vias", "track_angle", "isolated_copper", "starved_thermal",
    "diff_pair_gap_out_of_range",
)  # fmt: skip

# Checked only when KiCad's installation is known: the library tables name
# their libraries by paths in it.
LIBRARY = ("lib_footprint_issues", "lib_footprint_mismatch")

# Rules checked, but not everything they cover: rule -> what is left out.
PARTIAL = {
    "clearance": "text on copper layers is not measured yet: it needs the stroke font",
    "shorting_items": "text on copper layers is not measured yet: it needs the stroke font",
    "copper_edge_clearance": "text on copper layers is not measured yet: it needs the stroke font",
    "solder_mask_bridge": "text on a mask layer is not an opening yet: it needs the stroke font",
}

# The rules KiCad has and this does not check yet, and why.
NOT_CHECKED = {
    "connection_width": "the narrowest width of copper is not measured yet",
    "copper_sliver": "slivers of copper are not measured yet",
    "silk_overlap": "silkscreen needs the stroke font, which this tool does not have yet",
    "silk_over_copper": "silkscreen needs the stroke font, which this tool does not have yet",
    "silk_edge_clearance": "silkscreen needs the stroke font, which this tool does not have yet",
    "length_out_of_range": "the lengths of nets are not measured yet",
    "diff_pair_uncoupled_length_too_long": "the coupled length of a pair's route is not "
    "measured yet",
    "skew_out_of_range": "the lengths of nets are not measured yet",
    "creepage": "creepage is not measured yet",
    "track_on_post_machined_layer": "not checked yet",
    "track_not_centered_on_via": "not checked yet",
    "footprint": "not checked yet",
    "missing_tuning_profile": "not checked yet",
    "tuning_profile_track_geometries": "not checked yet",
    # Schematic parity is `board parity`'s, as it is KiCad's --schematic-parity.
    "duplicate_footprints": "schematic parity: `board parity`",
    "extra_footprint": "schematic parity: `board parity`",
    "missing_footprint": "schematic parity: `board parity`",
    "net_conflict": "schematic parity: `board parity`",
    "footprint_symbol_mismatch": "schematic parity: `board parity`",
    "footprint_filters_mismatch": "schematic parity: `board parity`",
    "footprint_symbol_field_mismatch": "schematic parity: `board parity`",
}  # fmt: skip


def check(
    path: str | Path,
    settings: Settings | None = None,
    board: Board | None = None,
    kicad_root: str | Path | None = None,
) -> Report:
    """Every violation of the board at `path`, by the rules of its project.
    A board already read -- an edit not yet written -- is checked as it is.
    The footprint library rules are checked when `kicad_root`, KiCad's
    installation, is given."""
    path = Path(path)
    board = board if board is not None else Board.load(path)
    if settings is None:
        settings = Settings.of(path.with_suffix(".kicad_pro"))
    run = Run(board, settings, kicad_root)
    run.path = path
    for step in _checks():
        step(run)
    not_checked = {rule: why for rule, why in NOT_CHECKED.items() if run.enabled(rule)}
    if kicad_root is None:
        for rule in LIBRARY:
            if run.enabled(rule):
                not_checked[rule] = (
                    "KiCad's installation was not found, and the library tables name "
                    "their libraries by paths in it"
                )
    for rule, why in settings.custom.items():
        if run.enabled(rule) and rule not in not_checked:
            not_checked[rule] = why
    ignored = sorted(rule for rule in settings.severities if not run.enabled(rule))
    partial = {rule: why for rule, why in PARTIAL.items() if run.on(rule)}
    dru = settings.dru
    custom = {}
    if dru is not None and dru.source is not None:
        custom = {"file": str(dru.source), "read": not dru.unreadable, "rules": len(dru.rules)}
        if dru.unreadable:
            custom["why"] = f"KiCad does not read it, and ignores every rule in it: {dru.why}"
    return Report(run.found, run.unconnected, not_checked, ignored, partial, custom)
