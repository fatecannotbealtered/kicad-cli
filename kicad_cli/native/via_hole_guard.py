"""Finite circular through-via hole spacing, independent of copper ownership.

The saved project minimum is a drill-edge distance, not a copper-annulus
clearance.  This planner constraint does not replace official DRC, read PTH
pad holes, or certify a manufacturing process.
"""

from __future__ import annotations

import hashlib
import json
from bisect import bisect_left, bisect_right
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

from ..fileformat.sexpr import Document
from .smd_drill_guard import DrillConstraintError

NM = 1_000_000
SOURCE = "kicad_cli.native.via_hole_guard analytic circular through-via drills; not native DRC"
SCOPE = (
    "selected ordinary F.Cu/B.Cu through-via drills against all retained and proposed "
    "through-via drills, independent of net"
)
RULE = "board.design_settings.rules.min_hole_to_hole"
DEFAULT_MM = 0.25  # KiCad 10 new-project minimum; distinct from min_hole_clearance.
LIMITS = [
    "Only circular ordinary F.Cu/B.Cu through-via drills; unsupported via geometry refuses.",
    "Custom hole_to_hole constraints are unsupported and refuse; other custom rules remain "
    "separate checks. The effective saved global minimum is enforced regardless of severity.",
    "No PTH/NPTH pad-hole, copper-annulus, solder-mask, process or manufacturing approval; "
    "official native DRC and independent connectivity remain separate checks.",
    "Positive-gap equality is accepted using exact squared integer-nanometre distances; "
    "the project minimum is rounded outward to whole nanometres.",
]


class ViaHoleConstraintError(DrillConstraintError):
    constraint = "project-min-hole-to-hole"

    def record(self):
        result = super().record()
        result["message"] = "Finite project via-hole spacing could not be established."
        return result


def _digest(raw):
    return hashlib.sha256(raw).hexdigest() if raw is not None else None


def _length(value, *, positive=False):
    if type(value) not in (int, float) or not Decimal(str(value)).is_finite():
        raise ViaHoleConstraintError("Project hole-to-hole minimum must be a finite number")
    if value < 0 or (positive and value <= 0):
        raise ViaHoleConstraintError("Via drill dimensions and project minimum must be nonnegative")
    return int((Decimal(str(value)) * NM).to_integral_value(rounding=ROUND_CEILING))


def _hole(x, y, drill):
    if any(type(n) is not int for n in (x, y, drill)) or drill <= 0:
        raise ViaHoleConstraintError(
            "Positive actual circular via drill and integer-nm position required"
        )
    return x, y, drill


def separated(a, b, gap):
    """Exact drill-edge comparison; no net identity or annulus approximation."""
    x, y, drill = a
    xx, yy, other = b
    return 4 * ((x - xx) ** 2 + (y - yy) ** 2) >= (drill + other + 2 * gap) ** 2


class ViaHoleGuard:
    def __init__(self, binding):
        self.binding = dict(binding)
        self.gap_nm = binding["minimum_nm"]
        self.planner_calls = 0
        self.checked_selected_via_count = 0
        self.checked_pair_count = 0

    @classmethod
    def read(cls, board_path):
        project = Path(board_path).with_suffix(".kicad_pro")
        custom = project.with_suffix(".kicad_dru")
        try:
            raw = project.read_bytes() if project.exists() else None
            data = json.loads(raw) if raw is not None else {}
            if not isinstance(data, dict):
                raise ValueError("project root is not an object")
            rules = data
            for key in ("board", "design_settings", "rules"):
                rules = rules.get(key, {})
                if not isinstance(rules, dict):
                    raise ValueError("project rule container is not an object: " + key)
            defaulted = "min_hole_to_hole" not in rules
            minimum = _length(rules.get("min_hole_to_hole", DEFAULT_MM))
            custom_raw = custom.read_bytes() if custom.exists() else None
            if custom_raw is not None:
                text = "\n".join(
                    line
                    for line in custom_raw.decode("utf-8-sig").splitlines()
                    if not line.lstrip().startswith("#")
                )
                parsed = Document.parse(f"(rules {text})", lazy=False).root
                if any(node.value(1) == "hole_to_hole" for node in parsed.walk("constraint")):
                    raise ViaHoleConstraintError(
                        "Custom hole_to_hole rules are unsupported by the finite via-hole planner"
                    )
        except ViaHoleConstraintError:
            raise
        except (OSError, ValueError, TypeError) as exc:
            raise ViaHoleConstraintError(
                "Unreadable project/custom via-hole rules: " + str(exc)
            ) from exc
        return cls(
            {
                "rule": RULE,
                "minimum_nm": minimum,
                "defaulted_to_kicad10": defaulted,
                "project_sha256": _digest(raw),
                "custom_rules_sha256": _digest(custom_raw),
            }
        )

    def require_binding(self, board_path):
        if self.read(board_path).binding != self.binding:
            raise ViaHoleConstraintError(
                "Project/custom via-hole rules drifted during route preparation"
            )

    @staticmethod
    def saved_holes(board):
        """Read all retained via drills without silently projecting a custom padstack."""
        allowed = {
            "at",
            "size",
            "drill",
            "layers",
            "net",
            "uuid",
            "tstamp",
            "free",
            "locked",
            "capping",
            "covering",
            "plugging",
            "filling",
            "tenting",
        }
        out = []
        for node in board.root.find_all("via"):
            uid = node.find("uuid") or node.find("tstamp")
            identity = {
                "kind": "via",
                "uuid": uid.value(1) if uid is not None else None,
            }
            if (
                any(child.head not in allowed for child in node.lists())
                or any(len(node.find_all(head)) != 1 for head in ("at", "size", "drill", "layers"))
                or any(
                    isinstance(item, str) and item not in {"locked", "free"}
                    for item in node.items[1:]
                )
                or any(
                    len(node.find(head).values()) != length or node.find(head).lists()
                    for head, length in (("at", 2), ("size", 1), ("drill", 1), ("layers", 2))
                )
                or node.find("layers").values() != ["F.Cu", "B.Cu"]
            ):
                raise ViaHoleConstraintError(
                    "Unsupported saved circular through-via drill geometry", objects=[identity]
                )
            # Parsed integer dimensions alone must not hide a nonfinite or unread token.
            try:
                for head in ("at", "size", "drill"):
                    if any(not Decimal(value).is_finite() for value in node.find(head).values()):
                        raise ValueError("nonfinite via dimension")
                via = board._via(node)
                identity.update(position_nm=list(via.position), net=via.net)
                if (
                    via.kind != "through"
                    or via.layers != ["F.Cu", "B.Cu"]
                    or via.diameter <= via.drill
                ):
                    raise ValueError("not an ordinary circular through-via with copper annulus")
                hole = _hole(*via.position, via.drill)
            except (ValueError, ArithmeticError) as exc:
                raise ViaHoleConstraintError(
                    "Unreadable saved via drill", objects=[identity]
                ) from exc
            out.append((via, hole, identity))
        return out

    def allows(self, x, y, drill, holes):
        proposed = _hole(x, y, drill)
        return all(separated(proposed, _hole(*old), self.gap_nm) for old in holes)

    def require_clearance(self, x, y, drill, holes):
        if not self.allows(x, y, drill, holes):
            raise ViaHoleConstraintError(
                "Proposed via drill violates the effective project hole-to-hole minimum",
                objects=[{"kind": "planned_via", "position_nm": [x, y], "drill_nm": drill}],
            )

    def rows(self, xs, ys, drill, holes):
        _hole(0, 0, drill)
        self.planner_calls += 1
        rows = [0] * len(ys)
        for old in holes:
            x, y, other = _hole(*old)
            # Outward search bounds only; every marked point uses the exact circle test.
            reach = (drill + other + 1) // 2 + self.gap_nm
            for j in range(bisect_left(ys, y - reach), bisect_right(ys, y + reach)):
                for i in range(bisect_left(xs, x - reach), bisect_right(xs, x + reach)):
                    if not separated((xs[i], ys[j], drill), old, self.gap_nm):
                        rows[j] |= 1 << i
        return rows

    def check_selected_vias(self, board, selected):
        self.require_binding(board.path)
        rows = self.saved_holes(board)
        pairs = set()
        self.checked_selected_via_count = sum(via.net in selected for via, _hole_, _id in rows)
        for i, (via, hole, identity) in enumerate(rows):
            if via.net not in selected:
                continue
            for j, (_other, second, other_id) in enumerate(rows):
                if i == j:
                    continue
                pairs.add(tuple(sorted((i, j))))
                if not separated(hole, second, self.gap_nm):
                    raise ViaHoleConstraintError(
                        "Selected saved via drills violate the project hole-to-hole minimum",
                        objects=[identity, other_id],
                    )
        self.checked_pair_count = len(pairs)

    def report(self):
        return {
            "status": "enforced",
            "source": SOURCE,
            "scope": SCOPE,
            "same_net_exempt": False,
            "binding": dict(self.binding),
            "planner_calls": self.planner_calls,
            "checked_selected_via_count": self.checked_selected_via_count,
            "checked_pair_count": self.checked_pair_count,
            "limits": list(LIMITS),
        }
