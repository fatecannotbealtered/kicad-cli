"""Independent frozen-input counterexamples for the usable hierarchy consumer."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
METHOD = ROOT / "bench/hierarchy_edit"
REQ = METHOD / "usable/requirements"


def run_comparison(tmp_path, defect=None, stage="U0"):
    captured = ROOT / "tests/fixtures/usable_hierarchy_intermediate"
    current = json.loads(
        (
            (captured / "observation.json") if stage == "U3" else REQ / "baseline-observation.json"
        ).read_bytes()
    )
    frozen = json.loads((REQ / "FROZEN.json").read_bytes())
    if stage == "U3":
        frozen["native_findings"]["erc_baseline"] = json.loads(
            (captured / "native-erc.json").read_bytes()
        )
        frozen["native_findings"]["drc_baseline"] = json.loads(
            (captured / "native-drc.json").read_bytes()
        )
        rows = frozen["native_findings"]["erc_baseline"]["sheets"][0]["violations"]
        if defect == "foreign-label":
            rows[0]["items"][0]["uuid"] = "unrelated-label-with-the-same-count"
        elif defect == "missing-warning":
            rows.pop()
        elif defect == "extra-warning":
            rows.append(json.loads(json.dumps(rows[0])))
        elif defect == "error-severity":
            rows[0]["severity"] = "error"
        elif defect == "other-erc-type":
            rows[0]["type"] = "pin_to_pin"
    if defect == "power_pad":
        next(f for f in current["footprints"] if f["ref"] == "U1")["pads"][-1]["net"] = ""
    elif defect == "third_unit":
        current["units"] = [
            u for u in current["units"] if not (u["ref"] == "U1" and u["unit"] == 3)
        ]
    elif defect == "second_instance":
        next(u for u in current["units"] if u["ref"] == "R20")["position_mm"][0] += 2.54
    elif defect == "copper":
        current["tracks"][0]["net"] = "wrong"
    elif defect == "unselected_drawing":
        next(
            n
            for n in current["raw_schematics"]["edit.kicad_sch"][1:]
            if isinstance(n, list) and n[0] == "wire"
        )[1][1][1] = "999"
    elif defect == "baseline_label_angle":
        label = next(
            n
            for n in current["raw_schematics"]["edit_child.kicad_sch"][1:]
            if isinstance(n, list) and n[0] == "label"
        )
        next(n for n in label if isinstance(n, list) and n[0] == "at")[3] = "90"
    elif defect == "native_drc":
        frozen["native_findings"]["drc_baseline"]["violations"].append(
            {"type": "shorting_items", "severity": "error", "items": []}
        )
    paths = {
        "current": tmp_path / "current.json",
        "erc": tmp_path / "erc.json",
        "drc": tmp_path / "drc.json",
    }
    for key, value in {
        "current": current,
        "erc": frozen["native_findings"]["erc_baseline"],
        "drc": frozen["native_findings"]["drc_baseline"],
    }.items():
        paths[key].write_text(json.dumps(value), encoding="utf-8")
    output = tmp_path / "comparison.json"
    argv = [sys.executable, str(METHOD / "compare.py"), "--stage", stage]
    for key, value in {
        **paths,
        "frozen": REQ / "FROZEN.json",
        "baseline": REQ / "baseline-observation.json",
        "output": output,
        "readers": ROOT / "bench/rc_trial/execution/consumers",
    }.items():
        argv.extend(["--" + key, str(value)])
    result = subprocess.run(argv, capture_output=True, timeout=30)
    return result, json.loads(output.read_bytes())


def test_predeclared_usable_baseline_has_complete_physical_mapping(tmp_path):
    process, result = run_comparison(tmp_path)
    assert process.returncode == 0, process.stderr
    assert result["bounded_edit_verified"] and result["delivery_eligible"]
    assert result["observed"] == {
        "footprints": 10,
        "physical_pads": 29,
        "component_units": 12,
        "power_symbols": 2,
    }
    assert result["native_erc"]["verdict"] == result["native_drc"]["verdict"] == "passed"


@pytest.mark.parametrize(
    "defect",
    [
        "power_pad",
        "third_unit",
        "second_instance",
        "copper",
        "unselected_drawing",
        "baseline_label_angle",
        "native_drc",
    ],
)
def test_usable_baseline_rejects_protected_or_native_regressions(tmp_path, defect):
    process, result = run_comparison(tmp_path, defect)
    assert process.returncode == 2
    assert not result["bounded_edit_verified"] and not result["delivery_eligible"]
    assert result["failures"]


def test_declared_disconnect_keeps_native_failures_and_is_not_delivery_eligible(tmp_path):
    process, result = run_comparison(tmp_path, stage="U3")
    assert process.returncode == 0, process.stdout + process.stderr
    assert result["bounded_edit_verified"] and not result["delivery_eligible"]
    assert result["native_erc"] == {
        "verdict": "failed_with_findings",
        "violations": {"isolated_pin_label": 3},
    }
    assert result["native_drc"]["verdict"] == "failed_with_findings"
    assert result["native_drc"]["violations"] == 5


@pytest.mark.parametrize(
    "defect",
    ["foreign-label", "missing-warning", "extra-warning", "error-severity", "other-erc-type"],
)
def test_declared_disconnect_does_not_excuse_other_or_missing_erc_findings(tmp_path, defect):
    process, result = run_comparison(tmp_path, defect, "U3")
    assert process.returncode == 2
    assert not result["bounded_edit_verified"] and not result["delivery_eligible"]
    assert any(
        f["check"] == "native_erc_findings_match_declared_intermediate_labels"
        for f in result["failures"]
    )
