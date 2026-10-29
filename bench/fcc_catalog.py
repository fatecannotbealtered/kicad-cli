"""Read-only FCC catalogue, with exact cases kept separate by source and EXE.

Run ``python -m bench.fcc_catalog --index <private.json> --out <new.json>``.
The private index names a repository, candidate and retained pytest run directories.
Every run needs id, kind (source/frozen), source_root, run_dir, source_commit,
junit_sha256, finish_sha256 and source_manifest_sha256; frozen runs also need
binary and binary_sha256. The index's candidate needs build_source_commit,
binary and binary_sha256. Repository absolute paths stay in that local index.

Only immutable JUnit, finish, source manifest, source files and optional executable
bytes are read. No product CLI or engineering operation is executed. Output is
exclusive UTF-8/LF. Use --check to compare an existing catalogue without writing.
This is a planning/evidence index, not FCC admission or a new passing test run.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path, PurePosixPath, PureWindowsPath

from scripts import fcc_evidence as fcc

CONTRACT = "kicad_cli/driver_contract.json"
PIN = "9607a35fe0ea33fe822e1f2ad2704eae260d4018"
PATH_PARAMETERS = {"board", "schematic", "netlist", "spec", "changes", "remove-footprint", "out"}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def checked_hash(path, expected, label):
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or fcc.sha(path) != expected:
        raise ValueError(f"{label}: file digest differs from the private index")


def portable(value):
    """Fail before export if local paths/secrets entered an allowlisted projection."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in {"argv", "confirm_token", "secret", "hmac"} or (
                key.lower() == "environment" and not isinstance(child, int)
            ):
                raise ValueError("Private material is not an FCC catalogue field")
            portable(key)
            portable(child)
    elif isinstance(value, list):
        for child in value:
            portable(child)
    elif isinstance(value, str):
        if (
            PureWindowsPath(value).is_absolute()
            or re.search(r"[a-zA-Z]:[/\\]", value)
            or value.startswith(("/", "\\\\", "ct_", "sk-"))
        ):
            raise ValueError("Local path or secret-like value in public catalogue")


def validate_inventory(inventory, reference):
    """Check identities, not merely 33/99 counts or set-deduplicated observations."""
    identifiers = [row["id"] for row in inventory["behaviors"]]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Duplicate FCC behavior ID")
    paths = [command["path"] for command in reference["commands"]]
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate command declaration")
    expected_commands = {f"command:{path}:normal" for path in paths}
    actual_commands = {name for name in identifiers if name.startswith("command:")}
    if actual_commands != expected_commands:
        raise ValueError("FCC command identities differ from the declared tree")
    expected_parameters = {}
    for command in reference["commands"]:
        names = [p["name"] for p in command["params"]]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate command parameter")
        if command["output_schema"] not in reference["schemas"]:
            raise ValueError("Undeclared command output schema")
        selection = command.get("selection_schema")
        if selection:
            urn, _, fragment = selection.partition("#/")
            schema = reference["json_schemas"].get(urn)
            for part in fragment.split("/") if fragment else []:
                schema = schema.get(part) if isinstance(schema, dict) else None
            if schema is None:
                raise ValueError("Unresolved selection schema reference")
        for parameter in command["params"]:
            for phase in ("discovery", "normal", "boundary"):
                key = f"parameter:{command['path']}:{parameter['name']}:{phase}"
                expected_parameters[key] = parameter
    actual_parameters = {name for name in identifiers if name.startswith("parameter:")}
    if actual_parameters != set(expected_parameters):
        raise ValueError("FCC parameter identities differ from the declared tree")
    for row in inventory["behaviors"]:
        if len(row["tests"]) != len(set(row["tests"])):
            raise ValueError("Duplicate required test ID")
        source_tests = row.get("source_tests", [])
        if len(source_tests) != len(set(source_tests)):
            raise ValueError("Duplicate retained source test ID")
        if source_tests and not row.get("source_test_scope"):
            raise ValueError("Retained source tests require an explicit separate scope")
        if row["id"].startswith("parameter:") and row["id"].endswith(":discovery"):
            if row["promise"] != expected_parameters[row["id"]]:
                raise ValueError("FCC copied parameter declaration differs")
    return {
        "command_paths": sorted(paths),
        "commands": len(paths),
        "command_parameters": len(expected_parameters) // 3,
        "global_parameters": len(reference["global_options"]),
        "identities_and_parameter_declarations_equal": True,
        "command_output_and_selection_schema_references_resolve": True,
        "scope": "Repository declarations only; actual EXE discovery is a separate observation.",
    }


def test_definition(root, selector):
    """Resolve an exact inventory selector without importing a test or CLI module."""
    base, bracket, variant = selector.partition("[")
    module, _, function = base.rpartition(".")
    if not re.fullmatch(r"tests(?:\.[A-Za-z0-9_]+)+", module):
        raise ValueError("Invalid required test module")
    relative = module.replace(".", "/") + ".py"
    path = root / relative
    functions = {
        node.name
        for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.FunctionDef)
    }
    if function not in functions or (bracket and not variant.endswith("]")):
        raise ValueError(f"Required test definition is missing: {selector}")
    return {
        "id": selector,
        "variant_id": variant[:-1] if bracket else None,
        "definition": relative,
        "definition_sha256": fcc.sha(path),
    }


def parameter_requirements(command, parameter, phase):
    """Planning obligations, not inferred parameter coverage or invented bounds."""
    if phase == "discovery":
        return ["exact help/reference declaration, defaults, effects and Schema references"]
    requirements = ["valid value with command-specific semantic readback"]
    if not parameter["required"]:
        requirements.append("omitted value follows the declared default/conditional-selection rule")
    if "default" in parameter:
        requirements.append("explicit declared default agrees with omission where permitted")
    if "enum" in parameter:
        requirements.extend(f"enum member: {json.dumps(v)}" for v in parameter["enum"])
    if parameter["name"] in PATH_PARAMETERS:
        requirements.extend(["existing/missing target kind", "Chinese and space-containing path"])
    if parameter["multiple"]:
        requirements.extend(["CSV values", "repeated option values", "mixed CSV/repeated values"])
    if phase == "boundary":
        requirements = ["malformed value; exact error envelope, exit and dispatch stage"]
        if parameter["required"]:
            requirements.append("missing required parameter")
        requirements.append("multiple simultaneous errors follow reviewed priority")
        if "enum" in parameter:
            requirements.append("value outside declared enum")
        if parameter["type"] in {"number", "integer"}:
            requirements.extend(
                [
                    "invalid numeric syntax/nonfinite values",
                    "declared numeric edges and adjacent values",
                ]
            )
        if parameter["type"] == "boolean":
            requirements.append(
                "bare/explicit true/false, invalid boolean and repetition semantics"
            )
        elif not parameter["multiple"]:
            requirements.append("repeated scalar option; do not assume global-boolean semantics")
        if parameter["multiple"] or parameter["name"] == "zones":
            requirements.extend(
                [
                    "duplicate selection within/across syntax forms",
                    "JSON array text is not list input",
                ]
            )
        if parameter["name"] in PATH_PARAMETERS:
            requirements.extend(["wrong file kind", "unreadable/unwritable owned path"])
        if parameter["name"] == "out":
            requirements.append("existing output conflict without overwrite")
    if command.get("selection_schema"):
        requirements.append("this command's conditional-selection Schema and omission rules")
    return requirements


def contract_for(row, reference):
    parts = row["id"].split(":")
    result = {"sources": row["sources"], "case_scope": row["case_scope"]}
    planning = []
    if parts[0] in {"command", "parameter"}:
        command = next(c for c in reference["commands"] if c["path"] == parts[1])
        schema = reference["schemas"][command["output_schema"]]
        result.update(
            command=command["path"],
            command_type=command["type"],
            effects=command["driver"]["effects"],
            phases=command["driver"]["phases"],
            output_schema=command["output_schema"],
            output_json_schema=schema.get("json_schema"),
            selection_schema=command.get("selection_schema"),
            declared_errors={
                code: reference["error_codes"][code]
                for code in command["driver"].get("error_codes", [])
            },
        )
        if parts[0] == "parameter":
            parameter = next(p for p in command["params"] if p["name"] == parts[2])
            result["parameter"] = parameter
            planning = parameter_requirements(command, parameter, parts[3])
        else:
            planning = [
                "declared normal output with independently asserted command semantics",
                "read/write and preview phases follow this command's declaration",
            ]
    elif parts[0] == "error":
        result["error"] = {"code": parts[1], **reference["error_codes"][parts[1]]}
        planning = [
            "taxonomy mapping only" if parts[2] == "mapping" else "actual public error emission",
            "reserved/reachable applicability must be reviewed; absence is not N/A",
        ]
    elif parts[0] in {"global", "format"}:
        result["global_options_reference"] = "reference.global_options"
        result["global_options"] = [
            option
            for option in reference["global_options"]
            if parts[1] == option["name"] or option["name"] in {"format", "fields", "dry-run"}
        ]
        planning = ["exact global mode with its applicable command type and error priority"]
    elif parts[0] == "environment":
        result["dependency_configuration_reference"] = "reference.dependency_configuration"
        result["variable"] = parts[1]
        planning = [
            "valid explicit selection",
            "missing/invalid explicit selection does not silently fall back",
            "unset/default and conflicting environment selection",
            "doctor execution success is separate from check-item success",
        ]
    else:
        planning = [str(row["promise"])]
    return result, planning


def read_run(spec):
    """Authenticate retained bytes and reuse fcc_evidence's conservative matcher."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", spec["id"]):
        raise ValueError("Run IDs must be portable identifiers")
    if spec["kind"] not in {"source", "frozen"}:
        raise ValueError("Run kind must distinguish source and frozen EXE")
    source, folder = Path(spec["source_root"]), Path(spec["run_dir"])
    files = {
        "junit": folder / "junit.xml",
        "finish": folder / "finish.json",
        "source_manifest": folder / "source-files.sha256.json",
    }
    for name, path in files.items():
        checked_hash(path, spec[name + "_sha256"], spec["id"] + ":" + name)
    finish = fcc.load(files["finish"])
    expected_mode = "source" if spec["kind"] == "source" else "exe"
    if (
        finish["source_commit"] != spec["source_commit"]
        or finish["mode"] != expected_mode
        or finish.get("source_manifest_sha256") != spec["source_manifest_sha256"]
        or finish.get("all_tracked_bytes_unchanged") is not True
        or finish.get("tracked_source_unchanged") is not True
    ):
        raise ValueError("Run source/mode/unchanged binding is incomplete")
    if not re.fullmatch(r"[0-9a-f]{40}", spec["source_commit"]):
        raise ValueError("Source commit must be a full SHA")
    manifest = fcc.load(files["source_manifest"])
    for name, expected in manifest.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name:
            raise ValueError("Unsafe source manifest path")
        checked_hash(source / name, expected, "retained source " + name)
    source_digest = fcc.digest_manifest(fcc.source_manifest(source))
    xml = ET.parse(files["junit"])
    props = {p.get("name"): p.get("value") for p in xml.findall(".//testsuite/properties/property")}
    if (
        props.get("fcc_source_digest") != source_digest
        or props.get("fcc_source_unchanged") != "true"
    ):
        raise ValueError("JUnit is not bound to the retained source digest")
    binary_hash = None
    if spec["kind"] == "frozen":
        binary_hash = spec["binary_sha256"]
        checked_hash(spec["binary"], binary_hash, "executed EXE")
        if finish.get("binary_sha256") != binary_hash:
            raise ValueError("Finish/EXE binding differs")
    inventory = fcc.load(source / fcc.INVENTORY)
    measured = fcc.measure(
        inventory, [files["junit"]], source_digest=source_digest, binary_sha256=binary_hash
    )
    observations = fcc.junit(files["junit"], binary_sha256=binary_hash)
    raw = fcc.junit(files["junit"])
    retained = None
    if spec.get("authenticate_retained_removal"):
        if spec["kind"] != "frozen":
            raise ValueError("Retained confirmation binding requires a frozen artifact run")
        from bench.persistence_acceptance.retained_pytest import review

        retained = review(folder, binary_hash)
        module, _, name = retained["case_id"].partition("::")
        selector = module.removesuffix(".py").replace("/", ".") + "." + name
        if retained["original_junit_sha256"] != fcc.sha(files["junit"]) or raw.get(selector) != [
            "passed"
        ]:
            raise ValueError("Authenticated save does not identify an original passing test")
        # No JUnit/marker is rewritten. This is a read-only authenticated
        # execution witness, not a new test, invocation or confirmation replay.
        observations[selector] = raw[selector]
    binding = {
        "id": spec["id"],
        "source_commit": spec["source_commit"],
        "source_digest": source_digest,
        "inventory_sha256": fcc.sha(source / fcc.INVENTORY),
        "contract_sha256": fcc.sha(source / CONTRACT),
        "artifact": {"kind": spec["kind"], "sha256": binary_hash},
        "evidence": {name + "_sha256": fcc.sha(path) for name, path in files.items()},
        "exit_code": finish["exit"],
        "started_at": finish["started_at"],
        "finished_at": finish["finished_at"],
        "bound_case_counts": dict(Counter(s for states in observations.values() for s in states)),
        "all_junit_case_counts": dict(Counter(s for states in raw.values() for s in states)),
        "historical_inventory_counts": {
            "enumerated": len(measured),
            **dict(Counter(row["status"] for row in measured)),
        },
    }
    if retained is not None:
        binding["retained_saved_operation_binding"] = retained
        binding["historical_inventory_count_basis"] = (
            "Original JUnit markers only; separately authenticated retained save "
            "also participates in exact candidate case observations."
        )
    return {
        "binding": binding,
        "source_root": source,
        "inventory": {row["id"]: row for row in inventory["behaviors"]},
        "measured": {row["id"]: row for row in measured},
        "observations": observations,
        "raw": raw,
    }


def status_of(states):
    if "failed" in states:
        return "failed"
    return "passed" if states and set(states) == {"passed"} else "not_run"


def row_observation(row, run):
    old = run["inventory"].get(row["id"])
    exact = {selector: run["observations"].get(selector, []) for selector in row["tests"]}
    return {
        "run_id": run["binding"]["id"],
        "requirement_relation": (
            "same_reviewed_requirement"
            if old == row
            else "revised_requirement"
            if old is not None
            else "absent_from_run_inventory"
        ),
        "historical_inventory_status": run["measured"].get(row["id"], {}).get("status"),
        "exact_case_observations": exact,
        "unbound_or_different_exe_cases": [
            selector
            for selector in row["tests"]
            if selector in run["raw"] and selector not in run["observations"]
        ],
        "current_source_admission": False,
    }


def candidate_observation(row, runs, binary_sha256):
    matched = [
        run
        for run in runs
        if run["binding"]["artifact"] == {"kind": "frozen", "sha256": binary_sha256}
    ]
    cases = []
    for selector in row["tests"]:
        states, source_ids = [], []
        for run in matched:
            if run["inventory"].get(row["id"]) == row:
                found = run["observations"].get(selector, [])
                states.extend(found)
                if found:
                    source_ids.append(run["binding"]["id"])
        cases.append({"id": selector, "status": status_of(states), "run_ids": source_ids})
    status = status_of([case["status"] for case in cases])
    reason = None
    if status != "passed":
        reason = (
            row.get("gap", "No reviewed exact test binding exists")
            if not cases
            else "Failed, skipped, missing or unbound required cases on this exact EXE; "
            "historical source/other EXE/revised requirements cannot supply missing evidence."
        )
    return {"status": status, "cases": cases, "unexecuted_or_failed_reason": reason}


def retained_source_observations(row, runs, definitions):
    """Keep original source evidence visible without admitting it as EXE coverage."""
    selectors = row.get("source_tests", [])
    if not selectors:
        return []
    evidence = []
    for run in runs:
        if run["binding"]["artifact"]["kind"] != "source":
            continue
        historical = run["inventory"].get(row["id"], {})
        bound = set(historical.get("tests", []) + historical.get("source_tests", []))
        cases = []
        for selector in selectors:
            states = run["observations"].get(selector, [])
            old_definition = (
                test_definition(run["source_root"], selector) if selector in bound else None
            )
            cases.append(
                {
                    "id": selector,
                    "status": status_of(states) if selector in bound else "not_run",
                    "observations": states,
                    "historically_bound": selector in bound,
                    "definition_sha256": old_definition["definition_sha256"]
                    if old_definition
                    else None,
                    "matches_current_definition": old_definition == definitions[selector],
                }
            )
        evidence.append(
            {
                "run_id": run["binding"]["id"],
                "cases": cases,
                "current_source_admission": False,
                "candidate_exe_admission": False,
            }
        )
    return evidence


def collect(index):
    root = Path(index["repository"])
    inventory = fcc.load(root / fcc.INVENTORY)
    reference = fcc.load(root / CONTRACT)["reference"]
    tree = validate_inventory(inventory, reference)
    candidate = index["candidate"]
    checked_hash(candidate["binary"], candidate["binary_sha256"], "candidate EXE")
    if not re.fullmatch(r"[0-9a-f]{40}", candidate["build_source_commit"]):
        raise ValueError("Candidate build source must be a full SHA")
    runs = [read_run(spec) for spec in index["runs"]]
    if len({run["binding"]["id"] for run in runs}) != len(runs):
        raise ValueError("Duplicate run ID")
    definitions = {
        selector: test_definition(root, selector)
        for selector in sorted(
            {
                test
                for row in inventory["behaviors"]
                for test in row["tests"] + row.get("source_tests", [])
            }
        )
    }
    rows = []
    for row in inventory["behaviors"]:
        contract, planning = contract_for(row, reference)
        rows.append(
            {
                "id": row["id"],
                "promise": row["promise"],
                "applicability": "retained_requirement",
                "applicable_contract": contract,
                "required_test_ids": row["tests"],
                "required_variants": [definitions[selector] for selector in row["tests"]],
                "retained_source_test_ids": row.get("source_tests", []),
                "retained_source_definitions": [
                    definitions[selector] for selector in row.get("source_tests", [])
                ],
                "retained_source_scope": row.get("source_test_scope"),
                "retained_source_evidence": retained_source_observations(row, runs, definitions),
                "variant_basis": row.get(
                    "required_case_basis",
                    "Exact reviewed inventory IDs; no prefix matching or inferred collections.",
                ),
                "remaining_variant_review": planning,
                "variant_review_limit": (
                    "Planning obligations are not extra FCC rows or passing assertions. "
                    "The exact inventory test set only certifies what its assertions cover."
                ),
                "declared_gap": row.get("gap"),
                "evidence": [row_observation(row, run) for run in runs],
                "same_candidate_exe": candidate_observation(row, runs, candidate["binary_sha256"]),
            }
        )
    report = {
        "format": "kicad-cli.fcc-catalogue.v1",
        "specification_commit": PIN,
        "generator_sha256": fcc.sha(Path(__file__)),
        "matcher_sha256": fcc.sha(root / "scripts/fcc_evidence.py"),
        "inventory_sha256": fcc.sha(root / fcc.INVENTORY),
        "contract_sha256": fcc.sha(root / CONTRACT),
        "inventory_exhaustive": inventory["exhaustive"],
        "source_baseline_commit": index["source_baseline_commit"],
        "candidate": {
            "build_source_commit": candidate["build_source_commit"],
            "binary_sha256": candidate["binary_sha256"],
        },
        "tree": tree,
        "counts": {
            "enumerated": len(rows),
            "with_exact_test_binding": sum(bool(row["required_test_ids"]) for row in rows),
            "without_exact_test_binding": sum(not row["required_test_ids"] for row in rows),
            "by_category": dict(Counter(row["id"].split(":")[0] for row in rows)),
            "same_candidate_exe": dict(
                Counter(row["same_candidate_exe"]["status"] for row in rows)
            ),
        },
        "runs": [run["binding"] for run in runs],
        "behaviors": rows,
        "fcc_status": "incomplete",
        "release_readiness": "unpublishable",
        "limits": [
            "33/33 is migration only; 451 reviewed rows are a non-exhaustive denominator.",
            "This catalogue performs no new CLI execution or native engineering acceptance.",
            "Retained digests are verified separately and never transferred to current source.",
            "EXE reuse requires exact per-case markers and unchanged reviewed requirements.",
            "Failures dominate passes; a skipped or absent required member remains not_run.",
            "An absent environment or test is not automatically not_applicable.",
            "Native geometry/intent/visual evidence and independent-use results stay separate.",
            "Private paths stay in the replay index; public IDs and digests identify inputs.",
        ],
    }
    portable(report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="Compare only; do not replace output")
    args = parser.parse_args(argv)
    report = collect(fcc.load(args.index))
    if args.check:
        if fcc.load(args.out) != report:
            raise ValueError("Catalogue differs; preserve it and choose a new output file")
    else:
        with args.out.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    print(json.dumps(report["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
