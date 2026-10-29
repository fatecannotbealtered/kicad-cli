"""Compare accepted baseline to reopened ECO against the frozen two-change intent."""

import copy
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

baseline_path, eco_path, plan_path, output = map(Path, sys.argv[1:5])
if output.exists():
    raise SystemExit("Existing report refused")
baseline = json.loads(baseline_path.read_text(encoding="utf-8-sig"))
eco = json.loads(eco_path.read_text(encoding="utf-8-sig"))
plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
failures = []


def require(ok, check, **facts):
    if not ok:
        failures.append({"check": check, **facts})


def canonical(items):
    return Counter(json.dumps(x, sort_keys=True) for x in items)


desired = copy.deepcopy(baseline["footprints"])
dx = plan["R2"]["to_nm"][0] - plan["R2"]["from_nm"][0]
dy = plan["R2"]["to_nm"][1] - plan["R2"]["from_nm"][1]
for footprint in desired:
    if footprint["ref"] == "C2":
        footprint["value"] = "220nF"
    if footprint["ref"] == "R2":
        footprint["position_nm"] = [
            footprint["position_nm"][0] + dx,
            footprint["position_nm"][1] + dy,
        ]
        for pad in footprint["pads"]:
            pad["position_nm"] = [pad["position_nm"][0] + dx, pad["position_nm"][1] + dy]
for expected in desired:
    actual = next((p for p in eco["footprints"] if p["ref"] == expected["ref"]), None)
    require(actual == expected, "only_requested_component_differences", ref=expected["ref"])
require(len(eco["footprints"]) == len(desired), "component_count")
protected = plan["protected_native_nets"]
before_copper = [t for t in baseline["tracks"] if t["net"] in protected]
after_copper = [t for t in eco["tracks"] if t["net"] in protected]
require(
    canonical(before_copper) == canonical(after_copper), "protected_six_signal_nets_exact_copper"
)
before_ground = [t for t in baseline["tracks"] if t["net"] == "GND"]
after_ground = [t for t in eco["tracks"] if t["net"] == "GND"]
require(canonical(before_ground) == canonical(after_ground), "ground_tracks_vias_unchanged")
require(
    canonical(baseline["edges"]) == canonical(eco["edges"]),
    "outline_complete_segment_identity_geometry_multiset",
)
require(
    canonical(baseline["zone_definitions"]) == canonical(eco["zone_definitions"]),
    "zone_definitions_unchanged",
)
require(baseline["project"] == eco["project"], "entire_project_configuration_unchanged")
require(baseline["raw_net_to_logical"] == eco["raw_net_to_logical"], "raw_net_mapping_unchanged")
require(eco["physical_intent_passed"], "eco_physical_intent")
selected_changed = canonical(
    t for t in baseline["tracks"] if t["net"] in plan["repair_native_nets"]
) != canonical(t for t in eco["tracks"] if t["net"] in plan["repair_native_nets"])
require(selected_changed, "selected_two_net_copper_repaired")
report = {
    "passed": not failures,
    "failures": failures,
    "baseline_board_sha256": baseline["board_sha256"],
    "eco_board_sha256": eco["board_sha256"],
    "absolute_R2_target_nm": plan["R2"]["to_nm"],
    "protected_nets": protected,
    "protected_signal_items": len(before_copper),
    "ground_track_via_items": len(before_ground),
    "only_selected_signal_copper_changed": selected_changed,
    "ground_fill_changed": baseline["saved_filled_polygons"] != eco["saved_filled_polygons"],
    "comparison": (
        "Exact saved native object identity/pose/value/physical pin mapping "
        "and complete outline/zone/project definitions. Filled polygons may "
        "change and require separate contact/native DRC checks."
    ),
    "input_hashes": {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (baseline_path, eco_path, plan_path)
    },
}
output.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
raise SystemExit(0 if not failures else 1)
