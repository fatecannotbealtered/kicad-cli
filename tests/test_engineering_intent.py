"""Independent acceptance counts every physical pad occurrence."""

from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))
from engineering_intent import pcb_intent, thermal_intent  # noqa: E402


def test_duplicate_numbered_pads_require_declared_multiplicity_and_same_net():
    intent = {
        "parts": [
            {"ref": "J1", "symbol": "Connector:Test", "footprint": "Test:Double", "value": "test"}
        ],
        "edit": {},
        "physical_pad_mapping": {
            "J1": [{"number": "1", "logical_pin": "1", "net": "GND", "occurrences": 2}]
        },
    }
    observed = {
        "footprints": [
            {
                "ref": "J1",
                "uuid": "e1abb43b-d984-496e-9e53-ea5e5449300e",
                "footprint": "Test:Double",
                "value": "test",
                "pads": [{"number": "1", "net": "GND"}, {"number": "1", "net": "GND"}],
            }
        ]
    }
    assert pcb_intent(observed, intent)["status"] == "passed"
    wrong = deepcopy(observed)
    wrong["footprints"][0]["pads"][1]["net"] = "VIN"
    assert pcb_intent(wrong, intent)["status"] == "failed"
    missing = deepcopy(observed)
    missing["footprints"][0]["pads"].pop()
    assert pcb_intent(missing, intent)["status"] == "failed"
    extra = deepcopy(observed)
    extra["footprints"][0]["pads"].append({"number": "1", "net": "GND"})
    assert pcb_intent(extra, intent)["status"] == "failed"


def test_global_component_does_not_prove_direct_stitch_contact_on_both_layers():
    ground = {
        "net": "GND",
        "layers": ["F.Cu", "B.Cu"],
        "connect": "thermal",
        "minimum_width_mm": 0.25,
        "clearance_mm": 0.2,
        "thermal_gap_mm": 0.5,
        "thermal_spoke_mm": 0.5,
    }
    zones = [
        {
            "uuid": layer,
            "layers": [layer],
            "net": "GND",
            "pad_connection_name": "thermal",
            "min_thickness_nm": 250000,
            "clearance_nm": 200000,
            "thermal_gap_nm": 500000,
            "thermal_spoke_nm": 500000,
            "rule_area": False,
            "filled": True,
            "filled_polygons": {layer: ["observed-fill"]},
        }
        for layer in ground["layers"]
    ]
    via = {
        "uuid": "via",
        "net": "GND",
        "component_pads": [{"ref": "R1", "number": "2"}],
        "component_zones": zones,
        "direct_zone_contacts": [
            {
                "layer": layer,
                "net": "GND",
                "filled": True,
                "via_on_layer": True,
                "direct_copper_intersection": True,
            }
            for layer in ground["layers"]
        ],
    }
    intent = {"ground": ground, "nets": {"GND": ["R1.2"]}}
    observed = {"zone_geometry": zones, "native_connectivity": {"vias": [via]}}
    assert thermal_intent(observed, intent)["status"] == "passed"
    damaged = deepcopy(observed)
    damaged["native_connectivity"]["vias"][0]["direct_zone_contacts"][1][
        "direct_copper_intersection"
    ] = False
    result = thermal_intent(damaged, intent)
    assert [c["id"] for c in result["checks"] if not c["passed"]] == [
        "native-ground-via-direct-layer-contact"
    ]
    assert damaged["native_connectivity"]["vias"][0]["component_zones"] == zones
