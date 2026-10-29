"""Independent native observation versus predeclared circuit/zone requirements.

No product model/writer imports. Lists and counters retain physical duplicate
pad numbers; any intended multiplicity must be declared before board generation.
"""

from __future__ import annotations

from collections import Counter
from uuid import UUID


def pcb_intent(observation, intent, *, edited=False):
    checks = []
    actual = observation["footprints"]
    counts = Counter(p["ref"] for p in actual)
    expected = {p["ref"]: p for p in intent["parts"]}
    checks.append(
        {
            "id": "component-ref-multiset",
            "expected": dict.fromkeys(expected, 1),
            "observed": dict(counts),
            "passed": counts == Counter(expected.keys()),
        }
    )
    uuids = [p["uuid"] for p in actual]
    try:
        valid = all(UUID(v).int != 0 for v in uuids) and len(set(uuids)) == len(uuids)
    except ValueError:
        valid = False
    checks.append(
        {
            "id": "physical-component-identities",
            "passed": valid,
            "expected": "Unique nonzero native UUIDs",
            "observed": uuids,
        }
    )
    for part in actual:
        ref = part["ref"]
        if ref not in expected:
            continue
        planned = expected[ref]
        value = (
            intent["edit"][ref + ".Value"]["after"]
            if edited and ref + ".Value" in intent["edit"]
            else planned["value"]
        )
        checks.append(
            {
                "id": ref + ".footprint-value",
                "expected": [planned["footprint"], value],
                "observed": [part["footprint"], part["value"]],
                "passed": part["footprint"] == planned["footprint"] and part["value"] == value,
                "source_symbol": planned["symbol"],
            }
        )
        mapping = intent["physical_pad_mapping"][ref]
        wanted = Counter({r["number"]: r["occurrences"] for r in mapping})
        observed = Counter(p["number"] for p in part["pads"])
        checks.append(
            {
                "id": ref + ".physical-pad-numbers",
                "expected": dict(wanted),
                "observed": dict(observed),
                "passed": wanted == observed,
            }
        )
        for row in mapping:
            nets = [
                p["net"].removeprefix("/") for p in part["pads"] if p["number"] == row["number"]
            ]
            checks.append(
                {
                    "id": ref + "." + row["number"] + ".physical-net",
                    "logical_pin": row["logical_pin"],
                    "expected": [row["net"]] * row["occurrences"],
                    "observed": nets,
                    "passed": len(nets) == row["occurrences"]
                    and all(n == row["net"] for n in nets),
                }
            )
    return {
        "status": "passed" if all(c["passed"] for c in checks) else "failed",
        "checks": checks,
        "scope": (
            "Declared refs/values/symbol-footprint IDs and every physical pad occurrence/net; "
            "unique native UUIDs"
        ),
        "duplicate_rule": (
            "Every physical occurrence counted; declared same-number occurrences must all map "
            "to the same frozen logical pin/net. Undeclared extras refused."
        ),
    }


def thermal_intent(observation, intent):
    ground = intent["ground"]
    zones = observation["zone_geometry"]
    wanted_layers = Counter((layer,) for layer in ground["layers"])
    actual_layers = Counter(tuple(z["layers"]) for z in zones)
    checks = [
        {
            "id": "zone-layer-multiset",
            "expected": [list(k) for k in wanted_layers.elements()],
            "observed": [list(k) for k in actual_layers.elements()],
            "passed": actual_layers == wanted_layers,
        }
    ]
    expected = {
        "net": ground["net"],
        "pad_connection_name": ground["connect"],
        "min_thickness_nm": round(ground["minimum_width_mm"] * 1e6),
        "clearance_nm": round(ground["clearance_mm"] * 1e6),
        "thermal_gap_nm": round(ground["thermal_gap_mm"] * 1e6),
        "thermal_spoke_nm": round(ground["thermal_spoke_mm"] * 1e6),
        "rule_area": False,
    }
    for zone in zones:
        for key, value in expected.items():
            actual = zone[key]
            if key == "net":
                actual = actual.removeprefix("/")
            checks.append(
                {
                    "id": zone["uuid"] + "." + key,
                    "expected": value,
                    "observed": actual,
                    "passed": actual == value,
                    "unit": "nm" if key.endswith("_nm") else None,
                    "tolerance": 0,
                }
            )
        checks.append(
            {
                "id": zone["uuid"] + ".actual-fill",
                "expected": "Filled contours on every declared layer",
                "observed": {
                    layer: len(zone["filled_polygons"].get(layer, [])) for layer in zone["layers"]
                },
                "passed": zone["filled"]
                and all(zone["filled_polygons"].get(layer) for layer in zone["layers"]),
            }
        )
    connected = observation["native_connectivity"]
    expected_pins = set(intent["nets"][ground["net"]])
    ground_vias = [v for v in connected["vias"] if v["net"] == ground["net"]]
    valid = bool(ground_vias) and all(
        expected_pins <= {p["ref"] + "." + p["number"] for p in v["component_pads"]}
        and set(ground["layers"])
        <= {layer for z in v["component_zones"] if z["filled"] for layer in z["layers"]}
        for v in ground_vias
    )
    checks.append(
        {
            "id": "native-ground-via-component",
            "expected": {"pins": sorted(expected_pins), "filled_layers": ground["layers"]},
            "observed": ground_vias,
            "passed": valid,
        }
    )
    direct = bool(ground_vias) and all(
        set(ground["layers"])
        <= {
            row["layer"]
            for row in via.get("direct_zone_contacts", [])
            if row["net"].removeprefix("/") == ground["net"]
            and row["filled"]
            and row["via_on_layer"]
            and row["direct_copper_intersection"]
        }
        for via in ground_vias
    )
    checks.append(
        {
            "id": "native-ground-via-direct-layer-contact",
            "expected": {"net": ground["net"], "layers": ground["layers"]},
            "observed": [
                {"uuid": v["uuid"], "contacts": v.get("direct_zone_contacts", [])}
                for v in ground_vias
            ],
            "passed": direct,
            "method": (
                "Fresh native effective via shape versus saved fill on each declared layer; "
                "zero added clearance"
            ),
        }
    )
    return {
        "status": "passed" if all(c["passed"] for c in checks) else "failed",
        "checks": checks,
        "scope": (
            "Exact frozen thermal configuration, actual saved fill and native connectivity "
            "of ground stitching; native DRC separate"
        ),
    }
