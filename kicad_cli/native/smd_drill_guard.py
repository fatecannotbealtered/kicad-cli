"""Optional, finite drill-to-SMD-copper constraint for grid re-routing.

This is a routing constraint, not a manufacturing checker.  A round drill
disk is tested against the actual axis-aligned rectangle or rounded rectangle,
not against a pad bounding box.  Net ownership is deliberately absent here:
same-net copper is still material into which an ordinary via must not drill.
The requested gap is a synthetic/user design constraint, not vendor approval.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from fractions import Fraction

NM = 1_000_000
SOURCE = "kicad_cli.native.smd_drill_guard analytic rect/roundrect; not native DRC"
SCOPE = (
    "ordinary F.Cu/B.Cu through-via drill disk to all supported SMD copper pads, independent of net"
)
LIMITS = [
    "Only zero-degree rectangular or rounded-rectangular SMD pads on exactly one outer "
    "copper layer, without chamfers, offsets or custom padstacks; unsupported inputs refuse.",
    "No solder process, mask/tenting/filling, assembly or manufacturing approval.",
    "Copper annulus clearance, layer contact, connectivity and official native DRC "
    "remain separate checks; existing unselected vias are not relocated or certified.",
    "The requested gap is rounded outward to whole nanometres for planning; equality at a "
    "positive effective gap is accepted, while zero-gap tangency is rejected.",
]


class DrillConstraintError(ValueError):
    """Only the explicitly requested finite SMD drill constraint failed."""

    constraint = "smd-drill-clearance-mm"

    def __init__(self, reason, *, objects=()):
        super().__init__(reason)
        self.objects = [dict(row) for row in objects]

    def record(self):
        return {
            "code": "E_VALIDATION",
            "message": "Requested finite SMD drill constraint could not be established.",
            "details": {
                "constraint": self.constraint,
                "reason": str(self),
                "objects": self.objects,
                "_untrusted": ["reason", "objects"],
            },
        }


def _pad_object(footprint, pad):
    node = pad.node
    uid = (node.find("uuid") or node.find("tstamp")) if node is not None else None
    return {
        "kind": "pad",
        "reference": footprint.reference,
        "number": pad.number,
        "uuid": uid.value(1) if uid is not None else None,
    }


def _via_object(via):
    node = via.node
    uid = (node.find("uuid") or node.find("tstamp")) if node is not None else None
    return {
        "kind": "via",
        "uuid": uid.value(1) if uid is not None else None,
        "net": via.net,
        "position_nm": list(via.position),
        "drill_nm": via.drill,
        "diameter_nm": via.diameter,
        "layers": list(via.layers),
    }


def clearance_nm(value) -> int | None:
    """Round a declared gap outward to nm, never weakening a fractional-nm request."""
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
        raise DrillConstraintError(
            "smd-drill-clearance-mm requires a finite number from 0 to 100 mm"
        )
    return int((Decimal(str(value)) * NM).to_integral_value(rounding=ROUND_CEILING))


@dataclass(frozen=True)
class SmdPad:
    identity: str
    net: str
    layer: str
    shape: str
    x: int
    y: int
    width: int
    height: int
    radius: Fraction

    @classmethod
    def read(cls, footprint, pad):
        """Reject unread geometry instead of silently treating its AABB as exact."""

        def rejection(reason):
            return DrillConstraintError(reason, objects=[_pad_object(footprint, pad)])

        identity = f"{footprint.reference}.{pad.number}"
        node = pad.node
        if node is None:
            raise rejection("SMD drill constraint requires saved pad geometry: " + identity)
        safe_heads = {
            "at",
            "size",
            "layers",
            "roundrect_rratio",
            "net",
            "uuid",
            "tstamp",
            "pinfunction",
            "pintype",
            "solder_mask_margin",
            "solder_paste_margin",
            "solder_paste_margin_ratio",
            "zone_connect",
            "thermal_bridge_angle",
            "thermal_bridge_width",
            "thermal_gap",
            "die_length",
            "clearance",
            "property",
            "locked",
            "teardrops",
        }
        heads = [item.head for item in node.lists()]
        unknown = sorted(str(head) for head in set(heads) - safe_heads)
        if unknown:
            raise rejection(
                "Unsupported saved SMD geometry fields for drill constraint: "
                + identity
                + " "
                + ",".join(unknown)
            )
        if any(heads.count(head) != 1 for head in ("at", "size", "layers")):
            raise rejection("SMD drill constraint requires unique at/size/layers: " + identity)
        at, size, layer_node = (node.find(head) for head in ("at", "size", "layers"))
        if (
            len(at.values()) not in (2, 3)
            or at.lists()
            or len(size.values()) != 2
            or size.lists()
            or layer_node.lists()
            or len(set(layer_node.values())) != len(layer_node.values())
            or any(isinstance(item, str) and item != "locked" for item in node.items[4:])
        ):
            raise rejection(
                "Unread saved SMD geometry declaration for drill constraint: " + identity
            )
        if any(heads.count(head) > 1 for head in ("roundrect_rratio", "uuid", "tstamp")):
            raise rejection("SMD drill constraint rejects duplicate geometry/identity: " + identity)
        uid = node.find("uuid") or node.find("tstamp")
        if heads.count("uuid") + heads.count("tstamp") != 1 or uid is None or not uid.value(1):
            raise rejection("SMD drill constraint requires a saved pad identity: " + identity)
        identity += ":" + uid.value(1)
        layers = set(pad.layers)
        copper = {layer for layer in layers if layer.endswith(".Cu")}
        allowed_layers = {"F.Cu", "B.Cu", "F.Paste", "B.Paste", "F.Mask", "B.Mask"}
        if len(copper) != 1 or not copper <= {"F.Cu", "B.Cu"} or not layers <= allowed_layers:
            raise rejection("Unsupported SMD layers for drill constraint: " + identity)
        if (
            pad.shape not in {"rect", "roundrect"}
            or not math.isfinite(pad.angle)
            or pad.angle % 360 != 0
            or pad.drill is not None
            or pad.drill_offset != (0, 0)
            or pad.rect_delta != (0, 0)
            or pad.chamfer_ratio != 0
            or pad.chamfer_corners
        ):
            raise rejection(
                "Unsupported SMD shape/angle/padstack for drill constraint: " + identity
            )
        width, height = pad.size
        if any(type(value) is not int or value <= 0 for value in (width, height)):
            raise rejection("Invalid SMD dimensions for drill constraint: " + identity)
        if any(type(value) is not int for value in pad.position):
            raise rejection("Invalid SMD position for drill constraint: " + identity)
        ratio_node = node.find("roundrect_rratio")
        if pad.shape == "roundrect" and ratio_node is None:
            raise rejection("Missing saved SMD corner radius for drill constraint: " + identity)
        if ratio_node is not None and (len(ratio_node.values()) != 1 or ratio_node.lists()):
            raise rejection("Unread saved SMD corner radius for drill constraint: " + identity)
        try:
            ratio = Fraction(ratio_node.atom(1)) if ratio_node is not None else Fraction(0)
        except (ValueError, ZeroDivisionError, TypeError) as exc:
            raise rejection(
                "Invalid saved SMD corner radius for drill constraint: " + identity
            ) from exc
        if not 0 <= ratio <= Fraction(1, 2):
            raise rejection("Invalid SMD corner radius for drill constraint: " + identity)
        if pad.shape == "rect" and ratio != 0:
            raise rejection("Unexpected rectangle radius for drill constraint: " + identity)
        radius = ratio * min(width, height) if pad.shape == "roundrect" else Fraction(0)
        return cls(
            identity, pad.net, next(iter(copper)), pad.shape, *pad.position, width, height, radius
        )

    def conflicts(self, x: int, y: int, drill_nm: int, gap_nm: int) -> bool:
        """Exact analytic Minkowski test in rational nm, without polygon/AABB substitution.

        A rounded rectangle is its inset rectangle plus its corner disk.
        The drill disk plus requested gap expands that disk, preserving the
        same inset rectangle.  Equality is accepted when a positive declared
        gap is exactly met.  At zero gap, tangency is conservatively rejected.
        """
        dx = max(abs(x - self.x) - Fraction(self.width, 2) + self.radius, 0)
        dy = max(abs(y - self.y) - Fraction(self.height, 2) + self.radius, 0)
        reach = self.radius + Fraction(drill_nm, 2) + gap_nm
        squared, limit = dx * dx + dy * dy, reach * reach
        return squared < limit if gap_nm > 0 else squared <= limit


class SmdDrillGuard:
    def __init__(self, pads: tuple[SmdPad, ...], gap_nm: int, requested_mm=None):
        self.pads = pads
        self.gap_nm = gap_nm
        self.requested_mm = gap_nm / NM if requested_mm is None else float(requested_mm)
        self.planner_calls = 0
        self._cache = {}

    @classmethod
    def read(cls, pairs, value):
        gap = clearance_nm(value)
        if gap is None:
            return None
        pads = []
        for footprint, pad in pairs:
            if pad.kind not in {"smd", "thru_hole", "np_thru_hole", "connect"}:
                raise DrillConstraintError(
                    "Unknown pad type for SMD drill constraint",
                    objects=[_pad_object(footprint, pad)],
                )
            if pad.kind == "smd":
                pads.append(SmdPad.read(footprint, pad))
        if len({pad.identity.rsplit(":", 1)[-1] for pad in pads}) != len(pads):
            raise DrillConstraintError(
                "Duplicate SMD identities for drill constraint",
                objects=[{"kind": "pad", "identity": pad.identity} for pad in pads],
            )
        return cls(tuple(pads), gap, value)

    def allows(self, x: int, y: int, drill_nm: int) -> bool:
        if type(drill_nm) is not int or drill_nm <= 0:
            raise DrillConstraintError(
                "Positive actual via drill diameter required by SMD drill constraint"
            )
        return not any(pad.conflicts(x, y, drill_nm, self.gap_nm) for pad in self.pads)

    def require_clearance(self, x, y, drill_nm, *, via=None):
        """Keep a refused hole and its actual conflicting pad material identifiable."""
        if self.allows(x, y, drill_nm):
            return
        hole = via or {
            "kind": "planned_via",
            "uuid": None,
            "position_nm": [x, y],
            "drill_nm": drill_nm,
        }
        pads = [
            {
                "kind": "pad",
                "identity": pad.identity,
                "net": pad.net,
                "layer": pad.layer,
                "shape": pad.shape,
            }
            for pad in self.pads
            if pad.conflicts(x, y, drill_nm, self.gap_nm)
        ]
        raise DrillConstraintError(
            "Selected candidate via drill violates requested SMD copper clearance",
            objects=[hole, *pads],
        )

    def check_selected_vias(self, board, selected):
        """Re-read actual candidate holes, including any per-net restored old copper.

        This product-side defence is not the independent native acceptance.
        An unsuccessful route that restores a formerly permitted via-in-pad
        must not claim the newly requested constraint was met.
        """
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
        for via in board.vias:
            if via.net not in selected:
                continue
            if (
                via.kind != "through"
                or via.layers != ["F.Cu", "B.Cu"]
                or via.node is None
                or any(child.head not in allowed for child in via.node.lists())
                or any(
                    len(via.node.find_all(head)) != 1 for head in ("at", "size", "drill", "layers")
                )
                or any(
                    isinstance(item, str) and item not in {"locked", "free"}
                    for item in via.node.items[1:]
                )
                or any(
                    len(via.node.find(head).values()) != length or via.node.find(head).lists()
                    for head, length in (("at", 2), ("size", 1), ("drill", 1), ("layers", 2))
                )
                or via.diameter <= via.drill
            ):
                raise DrillConstraintError(
                    "Unsupported selected via geometry for SMD drill constraint",
                    objects=[_via_object(via)],
                )
            self.require_clearance(*via.position, via.drill, via=_via_object(via))

    def rows(self, xs: tuple[int, ...], ys: tuple[int, ...], drill_nm: int) -> list[int]:
        """Forbidden actual saved grid positions, irrespective of signal layer or net.

        Bounding boxes only restrict the positions enumerated.  Every marked
        position is decided by the analytic material test above.  Coordinates
        are the exact integer-nm positions the writer will save.
        """
        self.allows(0, 0, drill_nm)  # also validate the actual drill dimension
        self.planner_calls += 1
        key = (xs, ys, drill_nm)
        if key not in self._cache:
            rows = [0] * len(ys)
            reach = Fraction(drill_nm, 2) + self.gap_nm
            for pad in self.pads:
                a = bisect_left(xs, pad.x - Fraction(pad.width, 2) - reach)
                b = bisect_right(xs, pad.x + Fraction(pad.width, 2) + reach)
                c = bisect_left(ys, pad.y - Fraction(pad.height, 2) - reach)
                d = bisect_right(ys, pad.y + Fraction(pad.height, 2) + reach)
                for j in range(c, d):
                    for i in range(a, b):
                        if pad.conflicts(xs[i], ys[j], drill_nm, self.gap_nm):
                            rows[j] |= 1 << i
            self._cache[key] = rows
        return self._cache[key]

    def report(self):
        return {
            "status": "enforced",
            "enabled": True,
            "executed": True,
            "clearance_mm": self.requested_mm,
            "scope": SCOPE,
            "same_net_exempt": False,
            "source": SOURCE,
            "checked_pad_count": len(self.pads),
            "shape_counts": {
                shape: sum(pad.shape == shape for pad in self.pads)
                for shape in ("rect", "roundrect")
            },
            "planner_calls": self.planner_calls,
            "limits": list(LIMITS),
        }


def not_requested():
    return {
        "status": "not_requested",
        "enabled": False,
        "executed": False,
        "clearance_mm": None,
        "scope": SCOPE,
        "same_net_exempt": False,
        "source": SOURCE,
        "checked_pad_count": 0,
        "shape_counts": {"rect": 0, "roundrect": 0},
        "planner_calls": 0,
        "limits": ["Drill-to-SMD clearance was not requested and was not checked.", *LIMITS],
    }
