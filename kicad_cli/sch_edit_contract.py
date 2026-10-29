"""The published edit-file shapes, also used before engineering preparation."""

from __future__ import annotations

import json
import math
from pathlib import Path

REQUEST = "urn:kicad-cli:sch-edit-request:1"
SCHEMA = REQUEST + "#/$defs/changes_file"
NEXT = (
    "Read reference --command 'sch edit' and its $defs.changes_file; correct the named "
    "field or target, then request a new preview. Do not confirm a rejected change."
)


class Changes(list):
    """List semantics retained, with the caller's JSON-pointer prefix for errors."""

    def __init__(self, values, prefix=""):
        super().__init__(values)
        self.path_prefix = prefix


def file_schema():
    contract = json.loads(Path(__file__).with_name("driver_contract.json").read_bytes())
    return contract["reference"]["json_schemas"][REQUEST]["$defs"]["changes_file"]


def pointer(parts):
    return "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def _specific_errors(error):
    """Keep the actual operation/reference branch's leaf diagnostics.

    JSON Schema's outer oneOf message otherwise hides an invalid nested unit
    behind the entire action object. Other branches are not repair suggestions.
    """
    options = error.schema.get(error.validator) if isinstance(error.schema, dict) else None
    if (
        error.validator not in {"oneOf", "anyOf"}
        or not error.context
        or not isinstance(options, list)
    ):
        yield error
        return
    instance = error.instance
    selected = []
    if isinstance(instance, dict):
        if isinstance(instance.get("op"), str):
            selected = [
                i
                for i, option in enumerate(options)
                if option.get("properties", {}).get("op", {}).get("const") == instance["op"]
            ]
        if not selected:
            for discriminator in ("selector", "new", "pin", "object"):
                if discriminator in instance:
                    selected = [
                        i
                        for i, option in enumerate(options)
                        if discriminator in option.get("properties", {})
                    ]
                    if selected:
                        break
    if not selected:

        def matches(option):
            kind = option.get("type")
            return (kind == "object" and isinstance(instance, dict)) or (
                kind == "array" and isinstance(instance, list)
            )

        selected = [i for i, option in enumerate(options) if matches(option)]
    children = [
        child for child in error.context if child.schema_path and child.schema_path[0] in selected
    ]
    if not children:
        yield error
    else:
        for child in children:
            yield from _specific_errors(child)


def problems(value):
    from jsonschema import Draft202012Validator

    schema = file_schema()
    changes = value.get("changes") if isinstance(value, dict) else value
    prefix = ["changes"] if isinstance(value, dict) else []
    if not isinstance(changes, list) or not changes:
        return [{"path": pointer(prefix), "problem": "A nonempty changes array is required"}]
    found = []
    for index, change in enumerate(changes):
        base = [*prefix, index]
        if (
            not isinstance(change, dict)
            or not isinstance(change.get("op"), str)
            or change["op"] not in schema["$defs"]
        ):
            found.append(
                {
                    "index": index,
                    "path": pointer([*base, "op"]),
                    "problem": "Use one of " + ", ".join(schema["$defs"]),
                }
            )
            continue
        errors = Draft202012Validator(schema["$defs"][change["op"]]).iter_errors(change)
        for error in (leaf for parent in errors for leaf in _specific_errors(parent)):
            paths = [[*base, *error.absolute_path]]
            if error.validator == "required":
                paths = [
                    [*paths[0], key] for key in error.validator_value if key not in error.instance
                ]
            elif error.validator == "additionalProperties" and isinstance(error.instance, dict):
                paths = [
                    [*paths[0], key]
                    for key in error.instance
                    if key not in error.schema.get("properties", {})
                ]
            elif "propertyNames" in error.absolute_schema_path and isinstance(error.instance, str):
                paths = [[*paths[0], error.instance]]
            found.extend(
                {
                    "index": index,
                    "path": pointer(path),
                    "problem": error.message[:300],
                    "op": change["op"],
                }
                for path in paths
            )
        if change["op"] == "transform":
            for key in ("translate_mm", "pivot_mm", "rotate_deg"):
                value = change.get(key)
                values = enumerate(value) if isinstance(value, list) else [(None, value)]
                for coordinate, number in values:
                    if isinstance(number, (int, float)) and not isinstance(number, bool):
                        try:
                            finite = math.isfinite(number)
                        except OverflowError:
                            finite = False
                        if not finite:
                            found.append(
                                {
                                    "index": index,
                                    "path": pointer(
                                        [*base, key, coordinate]
                                        if coordinate is not None
                                        else [*base, key]
                                    ),
                                    "problem": "Transform numeric values must be finite",
                                    "op": "transform",
                                }
                            )
    if any(isinstance(change, dict) and change.get("op") == "transform" for change in changes):
        for index, change in enumerate(changes):
            if isinstance(change, dict) and change.get("op") != "transform":
                found.append(
                    {
                        "index": index,
                        "path": pointer([*prefix, index, "op"]),
                        "problem": "Transform operations require a separate all-transform batch",
                        "op": change.get("op"),
                    }
                )
    circuit = [
        index
        for index, change in enumerate(changes)
        if isinstance(change, dict) and change.get("op") == "circuit"
    ]
    if circuit:
        if len(changes) != 1:
            found.append(
                {
                    "path": pointer(prefix),
                    "problem": "A circuit request is one all-action batch; "
                    "do not mix it with legacy operations or transform",
                }
            )

        def finite(value, location):
            if isinstance(value, dict):
                for key, child in value.items():
                    finite(child, [*location, key])
            elif isinstance(value, list):
                for key, child in enumerate(value):
                    finite(child, [*location, key])
            elif isinstance(value, float) and not math.isfinite(value):
                found.append(
                    {
                        "path": pointer(location),
                        "problem": "Circuit numeric values must be finite",
                        "op": "circuit",
                    }
                )

        for index in circuit:
            finite(changes[index], [*prefix, index])
    return found


def details(found):
    return {
        "parameter": "--changes",
        "schema_ref": SCHEMA,
        "next_action": NEXT,
        "problems": found[:40],
        "problem_count": len(found),
    }
