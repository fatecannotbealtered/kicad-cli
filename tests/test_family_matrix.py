import json
from pathlib import Path

import pytest

from bench.persistence_acceptance.family_matrix import generate


def test_frozen_matrix_covers_each_declared_identity_and_parameter_class():
    reference = json.loads(
        (Path(__file__).resolve().parents[1] / "kicad_cli/driver_contract.json").read_bytes()
    )["reference"]
    result = generate(reference)
    cases = {row["id"]: row for row in result["cases"]}
    assert len(cases) == len(result["cases"])
    for command in reference["commands"]:
        path = command["path"]
        assert cases["help:" + path]["argv"] == [*path.split(), "--help"]
        assert cases["reference:" + path]["command"] == path
        assert cases["unknown:" + path]["expected_parameter"] == "--unknown-first-version-flag"
        for p in command["params"]:
            name = p["name"]
            if p["required"]:
                row = cases[f"required:{path}:{name}"]
                assert not any(s.startswith("--" + name + "=") for s in row["argv"])
            if not p["multiple"]:
                row = cases[f"scalar-repeat:{path}:{name}"]
                assert sum(s.startswith("--" + name + "=") for s in row["argv"]) == 2
            if p.get("enum"):
                assert cases[f"invalid-enum:{path}:{name}"]["expected_error"] == "E_VALIDATION"
    assert all(not c["engineering_write_allowed"] for c in result["cases"])


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "required"])
def test_leaf_help_requires_exact_parameter_identities_and_requirement(mutation):
    from bench.persistence_acceptance.family_runner import check_output

    reference = {"commands": [{"path": "example", "params": [{"name": "input", "required": True}]}]}
    case = {"kind": "help", "command": "example"}
    value = "usage: kicad-cli example [flags]\n  --input  a string (required)\nLiteral defaults: {}"
    assert check_output(case, value, reference, {})["parameters"] == 1
    if mutation == "missing":
        value = value.replace("  --input  a string (required)\n", "")
    elif mutation == "extra":
        value += "\n  --invented  a string (optional)"
    elif mutation == "duplicate":
        value += "\n  --input  a string (required)"
    else:
        value = value.replace("(required)", "(optional)")
    with pytest.raises(AssertionError):
        check_output(case, value, reference, {})
