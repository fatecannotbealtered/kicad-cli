import copy
import json
from pathlib import Path

import pytest

from bench.persistence_acceptance.surface_review import resolve_schema, review_command, review_index

REFERENCE = json.loads(
    (Path(__file__).resolve().parents[1] / "kicad_cli/driver_contract.json").read_bytes()
)["reference"]


def index():
    return {
        "commands": [
            {
                "path": c["path"],
                "type": c["type"],
                "description": c["description"],
                "dangerous": c.get("dangerous", False),
                "effects": c["driver"]["effects"],
                "maturity": c["driver"]["maturity"],
            }
            for c in REFERENCE["commands"]
        ]
    }


def test_exact_paths_pass_without_assuming_index_contains_parameters():
    assert review_index(index(), REFERENCE["commands"])["unique_paths"] == 33


@pytest.mark.parametrize("kind", ["duplicate", "substitute", "omit", "metadata"])
def test_correct_count_does_not_establish_correct_inventory(kind):
    value = index()
    if kind == "duplicate":
        value["commands"][-1] = copy.deepcopy(value["commands"][0])
    elif kind == "substitute":
        value["commands"][-1]["path"] = "invented command"
    elif kind == "omit":
        value["commands"].pop()
    else:
        value["commands"][0]["effects"] = ["persist"]
    with pytest.raises(ValueError):
        review_index(value, REFERENCE["commands"])


@pytest.mark.parametrize("kind", ["parameter", "output-schema", "input-schema"])
def test_selected_parameter_and_schema_contracts_must_match(kind):
    command = next(c for c in REFERENCE["commands"] if c["path"] == "sch edit")
    value = {
        "command": copy.deepcopy(command),
        "schemas": {
            command["output_schema"]: copy.deepcopy(REFERENCE["schemas"][command["output_schema"]])
        },
        "json_schemas": copy.deepcopy(REFERENCE["json_schemas"]),
    }
    assert review_command(value, command, REFERENCE["schemas"], REFERENCE["json_schemas"])[
        "parameters_equal"
    ]
    if kind == "parameter":
        value["command"]["params"][0]["required"] = False
    elif kind == "output-schema":
        value["schemas"] = {"wrong": {}}
    else:
        value["json_schemas"] = {}
    with pytest.raises(ValueError):
        review_command(value, command, REFERENCE["schemas"], REFERENCE["json_schemas"])


@pytest.mark.parametrize("role", ["input_schema", "selection_schema", "request_schema"])
@pytest.mark.parametrize("mutation", ["empty", "type", "required", "enum"])
def test_resolvable_same_name_cannot_weaken_frozen_input_definition(role, mutation):
    command = copy.deepcopy(next(c for c in REFERENCE["commands"] if c["path"] == "sch edit"))
    command.pop("input_schema", None)
    command.pop("selection_schema", None)
    command["driver"].pop("request_schema", None)
    uri = "urn:independent-frozen:test"
    target = command["driver"] if role == "request_schema" else command
    target[role] = uri + "#/$defs/input"
    frozen = {
        uri: {
            "$id": uri,
            "$defs": {
                "input": {
                    "type": "object",
                    "required": ["mode"],
                    "properties": {"mode": {"type": "string", "enum": ["safe"]}},
                }
            },
        }
    }
    value = {
        "command": copy.deepcopy(command),
        "schemas": {
            command["output_schema"]: copy.deepcopy(REFERENCE["schemas"][command["output_schema"]])
        },
        "json_schemas": copy.deepcopy(frozen),
    }
    assert review_command(value, command, REFERENCE["schemas"], frozen)["schema_definitions_equal"]
    node = value["json_schemas"][uri]["$defs"]["input"]
    if mutation == "empty":
        node.clear()
    elif mutation == "type":
        node["type"] = ["object", "string", "null"]
    elif mutation == "required":
        node["required"] = []
    else:
        del node["properties"]["mode"]["enum"]
    with pytest.raises(ValueError):
        review_command(value, command, REFERENCE["schemas"], frozen)


@pytest.mark.parametrize(
    "reference", ["urn:missing", "urn:frozen#/absent", "urn:frozen#wrong", "urn:frozen#/type"]
)
def test_bad_or_missing_schema_fragment_is_not_a_definition(reference):
    command = copy.deepcopy(next(c for c in REFERENCE["commands"] if c["path"] == "sch edit"))
    command["driver"]["request_schema"] = reference
    command.pop("input_schema", None)
    command.pop("selection_schema", None)
    frozen = {"urn:frozen": {"type": "object"}}
    value = {
        "command": copy.deepcopy(command),
        "schemas": {
            command["output_schema"]: copy.deepcopy(REFERENCE["schemas"][command["output_schema"]])
        },
        "json_schemas": copy.deepcopy(frozen),
    }
    with pytest.raises(ValueError):
        review_command(value, command, REFERENCE["schemas"], frozen)
    with pytest.raises(ValueError):
        resolve_schema(frozen, reference)


def test_caller_must_supply_independent_frozen_definitions():
    with pytest.raises(ValueError, match="Independent frozen"):
        review_command({}, {})
