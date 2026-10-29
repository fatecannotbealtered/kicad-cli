"""Frozen intent versus native reader data; mutations are source-only controls."""

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench/hierarchy_edit"))
sys.path.insert(0, str(ROOT / "bench/rc_trial/execution/consumers"))
from sexpr_reader import children, parse  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "hierarchy_compare", ROOT / "bench/hierarchy_edit/compare.py"
)
comparator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparator)


def records():
    requirements = ROOT / "bench/hierarchy_edit/requirements"
    fixture = ROOT / "tests/fixtures/hierarchy_acceptance"

    def read(path):
        return json.loads(path.read_bytes())

    return (
        read(requirements / "baseline-observation.json"),
        read(fixture / "H3-observation.json"),
        read(requirements / "FROZEN.json"),
        read(fixture / "H3-erc.json"),
        read(fixture / "H3-drc.json"),
        children(parse((requirements / "frozen-baseline.kicad_pcb").read_text()), "footprint"),
    )


def result(current=None):
    baseline, observed, frozen, erc, drc, raw = records()
    return comparator.compare(baseline, current or observed, frozen, "H3", erc, drc, raw)


def test_real_H3_observation_matches_pre_edit_frozen_intent_but_native_findings_remain_failed():
    actual = result()
    assert actual["bounded_edit_verified"]
    assert actual["native_erc"]["verdict"] == "failed_with_findings"
    assert actual["native_drc"]["verdict"] == "failed_with_findings"
    assert actual["native_drc"]["unconnected"] == 9


@pytest.mark.parametrize(
    "defect", ["second-unit-field", "other-instance", "pad-rounding", "wrong-pin-coherent"]
)
def test_all_instances_units_and_full_pad_geometry_are_checked(defect):
    current = copy.deepcopy(records()[1])
    if defect in {"second-unit-field", "other-instance"}:
        unit = next(
            u
            for u in current["units"]
            if (u["ref"], u["unit"]) == (("U1", 2) if defect == "second-unit-field" else ("R20", 1))
        )
        unit["stored_fields"]["Value"] = "wrong-value"
        next(n for n in children(unit["raw_symbol"], "property") if n[1] == "Value")[2] = (
            "wrong-value"
        )
    elif defect == "pad-rounding":
        pad = children(current["raw_footprints"][0], "pad")[0]
        next(n for n in pad if isinstance(n, list) and n[0] == "roundrect_rratio")[1] = "0.1"
    else:
        for ref in ("R11", "R21"):
            wanted_net = "/CHILD_A/SENSE" if ref == "R11" else "/CHILD_B/SENSE"
            changed = {"1": f"unconnected-({ref}-Pad1)", "2": wanted_net}
            for unit in current["units"]:
                if unit["ref"] == ref:
                    for pin in unit["pins"]:
                        pin["net"] = changed[pin["number"]]
            for fp in current["footprints"]:
                if fp["ref"] == ref:
                    for pad in fp["pads"]:
                        pad["net"] = changed[pad["number"]]
            for fp in current["raw_footprints"]:
                if next(p[2] for p in children(fp, "property") if p[1] == "Reference") == ref:
                    for pad in children(fp, "pad"):
                        next(n for n in pad if isinstance(n, list) and n[0] == "net")[1] = changed[
                            pad[1]
                        ]
            for net in current["xml_nets"]:
                for member in net["members"]:
                    if member["ref"] == ref:
                        member["pin"] = "2" if member["pin"] == "1" else "1"
                        if net["name"].startswith("unconnected-"):
                            net["name"] = changed["1"]
        assert all(
            p["net"]
            == next(
                n["name"]
                for n in current["xml_nets"]
                for m in n["members"]
                if m["ref"] == fp["ref"] and m["pin"] == p["number"]
            )
            for fp in current["footprints"]
            if fp["ref"] in {"R11", "R21"}
            for p in fp["pads"]
        )
    actual = result(current)
    assert not actual["bounded_edit_verified"]
    checks = [f["check"] for f in actual["failures"]]
    if defect == "wrong-pin-coherent":
        assert "complete_official_pin_network_graph" in checks
    elif defect == "pad-rounding":
        assert "entire_saved_footprint_geometry_including_rounding_and_chamfers" in checks
    else:
        assert any(c.startswith("full_unit_fields_pose_and_pin_membership:") for c in checks)
