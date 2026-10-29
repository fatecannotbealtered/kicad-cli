"""Selection, effective settings and geometry criteria for saved copper tasks."""

from __future__ import annotations

import copy
import math

from ._driver_sdk.models import DriverError
from .fileformat.drc.settings import DEFAULT_CLASS, NetClasses, Settings
from .native.netclass import OPTIONS, apply

DIMENSIONS = ("track_width", "clearance", "via_diameter", "via_drill")


def validate(project):
    settings = project.get("net_settings") or {}
    classes = settings.get("classes") if settings.get("classes") is not None else []
    patterns = (
        settings.get("netclass_patterns") if settings.get("netclass_patterns") is not None else []
    )
    assigned = (
        settings.get("netclass_assignments")
        if settings.get("netclass_assignments") is not None
        else {}
    )
    if (
        not isinstance(classes, list)
        or not isinstance(patterns, list)
        or not isinstance(assigned, dict)
    ):
        raise ValueError("Malformed netclass containers")
    names = []
    for row in classes:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("name"), str)
            or not row["name"].strip()
        ):
            raise ValueError("Every netclass needs a nonempty name")
        names.append(row["name"])
        for key in (*DEFAULT_CLASS, "priority"):
            value = row.get(key)
            if value is not None and (
                type(value) not in {int, float}
                or not math.isfinite(value)
                or (key != "priority" and value < 0)
            ):
                raise ValueError("Invalid netclass dimension or priority: " + key)
    if len(set(names)) != len(names):
        raise ValueError("Duplicate netclass names")
    for row in patterns:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("pattern"), str)
            or row.get("netclass") not in names
        ):
            raise ValueError("Unresolved netclass pattern")
    for net, value in assigned.items():
        given = value if isinstance(value, list) else [value]
        if (
            not isinstance(net, str)
            or not given
            or any(not isinstance(c, str) or c not in names for c in given)
        ):
            raise ValueError("Unresolved explicit netclass assignment")
    return NetClasses(settings)


def effective(project, nets):
    resolver = validate(project)
    return {
        net: {
            "name": resolver.of(net).name,
            "values_nm": resolver.of(net).values,
            "sources": resolver.of(net).sources,
        }
        for net in nets
    }


def select(params, observed, command):
    all_nets = observed["nets"]
    given = params.get("nets") or []
    classes = params.get("classes") or []
    if given and classes:
        raise ValueError("Use nets or classes, not both")
    if given and any(net not in all_nets for net in given):
        raise DriverError(
            "E_NOT_FOUND",
            "Selected nets are absent from board.",
            details={"missing": [n for n in given if n not in all_nets]},
            dispatch_state="not_started",
        )
    resolver = validate(observed["project"])
    if classes and any(name not in resolver.classes for name in classes):
        raise DriverError(
            "E_NOT_FOUND",
            "Selected netclasses are absent from project.",
            details={"missing": [n for n in classes if n not in resolver.classes]},
            dispatch_state="not_started",
        )
    selected = (
        list(given)
        if given
        else [n for n in all_nets if any(c in resolver.of(n).name.split(",") for c in classes)]
        if classes
        else all_nets
    )
    if command in {"board route rewidth", "project netclass set"} and not (given or classes):
        raise ValueError("An explicit nets/classes selection is required")
    if not selected:
        raise ValueError("Selection is empty")
    if command == "board route rewidth" and any("," in n for n in selected):
        raise ValueError("Existing rewidth engine cannot represent comma-containing net names")
    return sorted(set(selected))


def set_class(project, params, selected, nets):
    prepared = copy.deepcopy(project)
    name = params["name"].strip()
    if not name or "," in name:
        raise ValueError("Netclass needs a nonempty unambiguous name")
    dimensions = {key: params[option] for option, key in OPTIONS if option in params}
    for key, value in dimensions.items():
        if type(value) not in {int, float} or not math.isfinite(value) or value <= 0 or value > 100:
            raise ValueError("Positive finite dimensions at most 100 mm required: " + key)
    priority = params.get("priority")
    if priority is not None and (
        type(priority) is not int or not -2147483647 <= priority < 2147483647 or name == "Default"
    ):
        raise ValueError(
            "Non-default class priority requires a signed 32-bit integer below Default"
        )
    apply(
        prepared,
        name,
        dict(dimensions, **({"priority": priority} if priority is not None else {})),
        selected,
    )
    # Labels and patterns combine; conflicting higher-priority patterns must
    # refuse unless the requested priority establishes the actual target values.
    settings = prepared["net_settings"]
    if settings.get("netclass_assignments") is None:
        settings["netclass_assignments"] = {}
    assigned = settings["netclass_assignments"]
    for net in selected:
        assigned[net] = [name]
    before, after = effective(project, nets), effective(prepared, nets)
    changed = [net for net in nets if before[net] != after[net]]
    for net in selected:
        if any(
            after[net]["values_nm"][key] != round(value * 1e6) for key, value in dimensions.items()
        ):
            raise ValueError("Assignment did not establish requested effective values")
    for net in changed:
        values = after[net]["values_nm"]
        if values["via_diameter"] <= values["via_drill"]:
            raise ValueError("Via diameter must exceed drill")
    return prepared, changed, {"before": before, "after": after}


def quality(observed, selected, params, *, command, before=None):
    rules = effective(observed["project"], selected)
    floor = round(float(params.get("neck", 0.2)) * 1e6)
    per_net = []
    for net in selected:
        objects = [o for o in observed["objects"] if o["net"] == net]
        copper = [o for o in objects if o["kind"] != "via"]
        target = rules[net]["values_nm"]["track_width"]
        narrow = [o["id"] for o in copper if o["width_nm"] < target - 1]
        undersize = [o["id"] for o in copper if o["width_nm"] < min(floor, target) - 1]
        per_net.append(
            {
                "net": net,
                "effective_class": rules[net],
                "tracks": len(copper),
                "vias": sum(o["kind"] == "via" for o in objects),
                "target_width_nm": target,
                "minimum_width_nm": min((o["width_nm"] for o in copper), default=None),
                "below_target": narrow,
                "below_neck_floor": undersize,
                "target_met": bool(copper) and not narrow,
            }
        )
    return {
        "unit": "nm",
        "tolerance_nm": 1,
        "per_net": per_net,
        "all_selected_target_met": all(row["target_met"] for row in per_net),
        "ampacity_assessed": False,
        "existing_copper_changed": (observed["objects"] != before["objects"])
        if before is not None
        else None,
        "limits": "Actual saved geometry only. Class defaults do not prove actual widths; "
        "no electrical load, copper thickness or thermal capacity basis.",
    }


def validate_dimensions(params, project_path):
    settings = Settings.of(project_path)
    if "neck" in params:
        neck = params["neck"]
        if not math.isfinite(neck) or not settings.rules["min_track_width"] <= neck <= 100:
            raise ValueError("Neck must be finite and at least the board minimum track width")


def require_class_minimums(project, selected, project_path):
    minimums = Settings.of(project_path).rules
    for net, row in effective(project, selected).items():
        values = row["values_nm"]
        for dimension, rule in [
            ("track_width", "min_track_width"),
            ("clearance", "min_clearance"),
            ("via_diameter", "min_via_diameter"),
            ("via_drill", "min_through_hole_diameter"),
        ]:
            if values[dimension] < round(minimums[rule] * 1e6):
                raise ValueError(f"Effective {dimension} for {net} is below board {rule}")
        if values["via_diameter"] - values["via_drill"] < round(
            2 * minimums["min_via_annular_width"] * 1e6
        ):
            raise ValueError("Effective via annulus is below board minimum")
