"""Exact public command identities and per-command parameter/schema references."""

from collections import Counter


def review_index(index, commands):
    expected = {c["path"]: c for c in commands}
    actual = [c["path"] for c in index["commands"]]
    if len(expected) != len(commands) or Counter(actual) != Counter(expected.keys()):
        raise ValueError("Public index paths differ, repeat or omit a registered command")
    for row in index["commands"]:
        command = expected[row["path"]]
        fields = {
            "type": command["type"],
            "description": command["description"],
            "dangerous": command.get("dangerous", False),
            "effects": command["driver"]["effects"],
            "maturity": command["driver"]["maturity"],
        }
        if any(row.get(key) != value for key, value in fields.items()):
            raise ValueError("Public index metadata differs: " + row["path"])
    return {"exact_paths": True, "unique_paths": len(actual), "index_metadata": True}


def resolve_schema(documents, reference):
    uri, _, fragment = reference.partition("#")
    if uri not in documents or (fragment and not fragment.startswith("/")):
        raise ValueError("Schema URI or JSON pointer is absent or invalid")
    value = documents[uri]
    for key in fragment[1:].split("/") if fragment else []:
        key = key.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or key not in value:
            raise ValueError("Selected command schema fragment cannot be resolved")
        value = value[key]
    if not isinstance(value, (dict, bool)):
        raise ValueError("Reference does not identify a Schema definition")
    return uri, value


def review_command(data, expected, schemas=None, json_schemas=None):
    if schemas is None or json_schemas is None:
        raise ValueError("Independent frozen output and product Schema definitions are required")
    if data["command"] != expected:
        raise ValueError("Selected command parameters or declaration differ: " + expected["path"])
    if set(data["schemas"]) != {expected["output_schema"]}:
        raise ValueError("Selected command output schema reference is missing or extra")
    if data["schemas"][expected["output_schema"]] != schemas[expected["output_schema"]]:
        raise ValueError("Selected output schema definition differs from full reference")
    references = {
        key: expected[key]
        for key in ("input_schema", "selection_schema", "routing_constraint_schema")
        if key in expected
    }
    if expected["driver"].get("request_schema"):
        references["driver.request_schema"] = expected["driver"]["request_schema"]
    for name, reference in references.items():
        uri, frozen = resolve_schema(json_schemas, reference)
        _uri, observed = resolve_schema(data.get("json_schemas", {}), reference)
        if data["json_schemas"][uri] != json_schemas[uri] or observed != frozen:
            raise ValueError(
                "Selected product Schema definition differs from frozen reference: " + name
            )
    return {
        "parameters_equal": True,
        "parameter_count": len(expected["params"]),
        "schema_references_resolved": True,
        "schema_definitions_equal": True,
    }
