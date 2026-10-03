"""A board's custom rules: the `.kicad_dru` file beside it.

A rule names its constraints -- `(constraint clearance (min 0.5mm))` -- and
when they apply: on which layers, `(layer outer)`, and to which items, a
condition in KiCad's expression language (`expression.py`). Measured on
`tests/fixtures/drc/drcrules` and KiCad's demo boards:

- of the rules constraining an item, the last in the file wins;
- a rule can loosen what the board setup and the net classes demand as well
  as tighten it -- `(constraint clearance (min 0.1mm))` under an FPGA;
- a condition about two items is asked both ways round, A and B swapped;
- a pad's or footprint's own clearance still takes precedence over any rule;
- the message names the rule: "(rule 'NAME' clearance 0.5000 mm; ...)".

The constraints read so far are `SUPPORTED`; a check another kind decides
is not made while that kind is not read, and is said not to be, as is one a
rule decides by a property or function this does not know.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..sexpr import Document, SexprError
from . import expression
from .expression import ExpressionError, Node, Unknown

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

# The kinds of constraint the checks here take from the rules.
SUPPORTED = (
    "clearance", "track_width", "via_diameter", "annular_width", "hole_size", "hole_clearance",
    "edge_clearance", "hole_to_hole",
)  # fmt: skip

# What a condition may ask of an item, as `Subject` answers it.
PROPERTIES = frozenset({"Type", "NetName", "NetClass", "Layer", "Width", "Pad_Type", "Reference"})
FUNCTIONS = frozenset({
    "intersectsArea", "insideArea", "enclosedByArea", "inDiffPair", "isPlated", "existsOnLayer",
    "memberOfFootprint", "hasNetclass", "isMicroVia", "isBlindBuriedVia",
})  # fmt: skip

_LENGTH = re.compile(r"^(-?\d+(?:\.\d*)?|-?\.\d+)(mm|mils?|in|um|nm)?$")


@dataclass
class Constraint:
    kind: str
    min: int | None = None
    max: int | None = None
    opt: int | None = None
    words: tuple[str, ...] = ()


@dataclass
class Rule:
    name: str
    layer: str | None = None
    condition: Node | None = None
    constraints: dict[str, Constraint] = field(default_factory=dict)
    severity: str | None = None
    unread: str = ""  # what of its condition is not understood


@dataclass
class Rules:
    rules: list[Rule] = field(default_factory=list)
    unreadable: bool = False

    def find(
        self, kind: str, a, b=None, layer: str | None = None, bound: str | None = None
    ) -> tuple[Rule, Constraint] | None:
        """The rule deciding a kind of constraint for an item, or a pair of
        them, on a layer: the last in the file whose layers and condition
        hold -- a pair's condition either way round. With `bound`, "min" or
        "max", the last that sets that bound: a rule that sets only a
        maximum leaves the minimum to the rules before it, and the board."""
        for rule in reversed(self.rules):
            constraint = rule.constraints.get(kind)
            if constraint is None or rule.unread:
                continue
            if bound is not None and getattr(constraint, bound) is None:
                continue
            if not _on(rule.layer, layer, a):
                continue
            if rule.condition is None or _holds(rule.condition, a, b, layer) or (
                b is not None and _holds(rule.condition, b, a, layer)
            ):  # fmt: skip
                return rule, constraint
        return None


def least(rules: Rules | None, kind: str, a, b=None, layer=None, board: int = 0,
          board_name: str = "board setup constraints"):  # fmt: skip
    """The least a kind of constraint allows an item or a pair: (value, who
    says so, the severity its rule gives) -- the last rule to set a minimum,
    else the board's."""
    if rules is not None and rules.rules and a is not None:
        found = rules.find(kind, a, b, layer, bound="min")
        if found is not None:
            rule, constraint = found
            return constraint.min, f"rule '{rule.name}'", rule.severity
    return board, board_name, None


def largest(rules: Rules | None, kind: str) -> int:
    """The largest minimum any rule sets for a kind: how far to look."""
    if rules is None:
        return 0
    return max(
        (c.min for rule in rules.rules for k, c in rule.constraints.items()
         if k == kind and c.min is not None),
        default=0,
    )  # fmt: skip


def _on(scope: str | None, layer: str | None, item=None) -> bool:
    """Whether a rule's layers take in the layer a check is on -- or, for a
    check on no one layer, any layer of the item: a via is on the inner
    layers it passes."""
    if scope is None:
        return True
    layers = [layer] if layer is not None else sorted(getattr(item, "layers", ()) or ())
    for name in layers:
        if scope == "outer" and name in ("F.Cu", "B.Cu"):
            return True
        if scope == "inner" and name.startswith("In") and name.endswith(".Cu"):
            return True
        if scope == name:
            return True
    return not layers


def _holds(condition: Node, a, b, layer: str | None) -> bool:
    def scope(name: str):
        if name == "A":
            return a.on(layer) if a is not None else None
        if name == "B":
            return b.on(layer) if b is not None else None
        return None

    try:
        return expression._truth(expression.evaluate(condition, scope))
    except Unknown:
        return False


def _source(project: Path | None) -> Path | None:
    if project is None:
        return None
    source = project.with_suffix(".kicad_dru")
    return source if source.is_file() else None


def _root(source: Path):
    text = source.read_text(encoding="utf-8")
    # A line starting with # is a comment.
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    return Document.parse(f"(rules {text})", lazy=False).root


def load(project: Path | None) -> Rules:
    """The project's rules, read; none when it has no `.kicad_dru`."""
    source = _source(project)
    if source is None:
        return Rules()
    try:
        root = _root(source)
    except (OSError, UnicodeDecodeError, SexprError):
        return Rules(unreadable=True)
    rules = []
    for node in root.find_all("rule"):
        rule = Rule(node.value(1) or "")
        layer = node.find("layer")
        if layer is not None:
            rule.layer = layer.value(1)
        severity = node.find("severity")
        if severity is not None:
            rule.severity = severity.value(1)
        condition = node.find("condition")
        if condition is not None and (condition.value(1) or "").strip():
            try:
                rule.condition = expression.parse(condition.value(1) or "")
                rule.unread = _unread(rule.condition)
            except ExpressionError as exc:
                rule.unread = f"its condition does not read ({exc})"
        for item in node.find_all("constraint"):
            kind = item.atom(1) or ""
            constraint = Constraint(kind)
            words = []
            for part in item.items[2:]:
                if isinstance(part, str):
                    words.append(part)
                    continue
                value = _length(part.atom(1) or "")
                if part.head in ("min", "max", "opt") and value is not None:
                    setattr(constraint, part.head, value)
            constraint.words = tuple(words)
            rule.constraints[kind] = constraint
        rules.append(rule)
    return Rules(rules)


def _length(text: str) -> int | None:
    m = _LENGTH.match(text.strip())
    if m is None:
        return None
    unit = m.group(2) or "mm"
    return round(float(m.group(1)) * expression.UNITS[unit])


def _unread(node: Node) -> str:
    """What of a condition this does not know, if anything."""
    if node.kind == "property" and node.value[1] not in PROPERTIES:
        return f"{node.value[0]}.{node.value[1]}"
    if node.kind == "call" and node.value[1] not in FUNCTIONS:
        return f"{node.value[0]}.{node.value[1]}()"
    if node.kind == "name":
        return node.value
    for arg in node.args:
        found = _unread(arg)
        if found:
            return found
    return ""


def constrained(project: Path | None) -> dict[str, str]:
    """The checks the project's custom rules decide and this does not take
    from them: check -> why, naming the first rule that does."""
    source = _source(project)
    if source is None:
        return {}
    rules = load(project)
    if rules.unreadable:
        # A rules file KiCad could not read either: every check it might
        # decide is uncertain.
        return {
            check: "the project's custom rules (.kicad_dru) do not read"
            for checks in CHECKS.values() for check in checks
        }  # fmt: skip
    out: dict[str, str] = {}
    for rule in rules.rules:
        for kind in rule.constraints:
            if kind in SUPPORTED and not rule.unread:
                continue
            why = (
                f"the project's custom rule '{rule.name}' decides it by asking {rule.unread}, "
                "which is not read yet" if rule.unread
                else f"the project's custom rule '{rule.name}' decides it by a {kind} "
                "constraint, which is not read yet"
            )  # fmt: skip
            for check in CHECKS.get(kind, ()):
                out.setdefault(check, why)
    return out
