"""Freeze the requested ECO against the accepted independent baseline before writing."""

import hashlib
import json
import os
from pathlib import Path

base = Path(__file__).resolve().parent
root = Path(os.environ.get("RC4_WORK_ROOT", str(base))).resolve()
packet = base.parent / "agent-packet"
baseline = json.loads(
    (root / "snapshots/baseline/review/native-observation-xml-identity.json").read_text(
        encoding="utf-8-sig"
    )
)
requested = json.loads((packet / "requirements/intent-eco.json").read_text(encoding="utf-8-sig"))
layout = json.loads((root / "plans/baseline-layout-v2.json").read_text(encoding="utf-8-sig"))
r2 = next(f for f in baseline["footprints"] if f["ref"] == "R2")
target = [
    r2["position_nm"][0] + round(requested["eco"]["translation"]["delta_x_mm"] * 1e6),
    r2["position_nm"][1] + round(requested["eco"]["translation"]["delta_y_mm"] * 1e6),
]
assert r2["position_nm"] == [25000000, 20000000] and target == [26500000, 20000000]
plan = {
    "frozen_before_eco": True,
    "baseline_board_sha256": baseline["board_sha256"],
    "intent_sha256": hashlib.sha256(
        (packet / "requirements/intent-eco.json").read_bytes()
    ).hexdigest(),
    "C2": {"from": "100nF", "to": "220nF"},
    "R2": {
        "from_nm": r2["position_nm"],
        "to_nm": target,
        "from_mm": [25, 20],
        "to_mm": [26.5, 20],
        "uuid": r2["uuid"],
        "source_path": r2["source_path"],
        "rotation_deg": r2["rotation_deg"],
        "layer": r2["layer"],
    },
    "repair_native_nets": ["/IN2", "/OUT2"],
    "protected_native_nets": ["/IN1", "/OUT1", "/IN3", "/OUT3", "/IN4", "/OUT4"],
    "protected_before": {
        "components": [
            f
            for f in baseline["footprints"]
            if f["ref"] in ["J1", "J2", "R1", "C1", "R3", "C3", "R4", "C4"]
        ],
        "signal_copper": [
            t
            for t in baseline["tracks"]
            if t["logical_net"] in ["IN1", "OUT1", "IN3", "OUT3", "IN4", "OUT4"]
        ],
        "outline": baseline["edges"],
        "zone_definitions": baseline["zone_definitions"],
    },
    "only_permitted_differences": (
        "C2 value, R2 translation, copper on the two selected nets, GND fill "
        "polygons; all identities, zone definitions, rules and other "
        "component/pad properties remain protected."
    ),
}
layout["final_pcb_poses"]["R2"]["xy_mm"] = [26.5, 20]
for target_path, data in (
    (root / "plans/eco-absolute-target.json", plan),
    (root / "plans/eco-layout.json", layout),
):
    with target_path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2)
print(
    json.dumps(
        {
            "plan": str(root / "plans/eco-absolute-target.json"),
            "sha256": hashlib.sha256(
                (root / "plans/eco-absolute-target.json").read_bytes()
            ).hexdigest(),
            "R2": plan["R2"],
            "protected_signal_segment_count": len(plan["protected_before"]["signal_copper"]),
        },
        indent=2,
    )
)
