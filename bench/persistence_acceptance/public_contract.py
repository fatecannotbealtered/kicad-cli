"""Bounded public EXE observations; no product module imports or confirmation replay."""

import json
import ntpath
import os
import re
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from .process_tree import observe
from .support import ASSETS, BINARY, ROOT, SETTINGS, Scene, now, put, read, sha, tree
from .surface_review import review_command, review_index


def validate_document(value, schema, documents):
    registry = Registry().with_resources(
        (key, Resource.from_contents(doc)) for key, doc in documents.items()
    )
    Draft202012Validator(schema, registry=registry).validate(value)


def infrastructure(process, binary, system_root):
    if not isinstance(process.get("image"), str) or not process["image"]:
        return False
    path = Path(process["image"]).resolve()
    console = (Path(system_root) / "System32/conhost.exe").resolve()
    shell = (Path(system_root) / "System32/cmd.exe").resolve()
    if path in {Path(binary).resolve(), console}:
        return True
    if path != shell:
        return False
    metadata = process.get("metadata")
    if not isinstance(metadata, dict):
        return False
    line = metadata.get("CommandLine")
    if not isinstance(line, str):
        return False
    # Deliberately accept a small complete grammar, not a suffix of cmd syntax.
    # Shell operators, expansion, redirects, nested commands and newlines cannot
    # occur in this grammar. Each descendant is still classified independently.
    match = re.fullmatch(
        r'[ \t]*(?:"(?P<quoted>[^"\r\n]+)"|(?P<bare>[^" \t\r\n]+))'
        r'(?P<switches>(?:[ \t]+/[ds]){0,2})[ \t]+/c[ \t]+(?:ver|"ver")[ \t]*',
        line,
        re.I,
    )
    if not match:
        return False
    executable = match["quoted"] or match["bare"]
    normalized = ntpath.normpath(executable).casefold()
    if normalized not in {"cmd", "cmd.exe", ntpath.normpath(str(shell)).casefold()}:
        return False
    switches = match["switches"].lower().split()
    return len(switches) == len(set(switches))


def selection_readback(case_id, plan):
    extension = plan["extensions"]
    key, expected = (
        ("nets", ["/OUT1", "/OUT3"])
        if case_id.startswith("unique-nets-")
        else ("layers", ["F.Cu", "B.Cu"])
    )
    if extension["params"].get(key) != expected:
        raise ValueError("Actual parsed multi-value selection differs from the declared input")
    if key == "nets" and extension.get("selected") != expected:
        raise ValueError("Planned net selection differs from the actual expanded parameters")
    return {"field": key, "actual_values": expected, "engineering_confirmation_executed": False}


def run():
    matrix_file = Path(__file__).with_name("public_cases.json")
    matrix = read(matrix_file)
    declaration = read(ASSETS["public_surface"])
    if sha(BINARY) != matrix["exe_sha256"]:
        raise ValueError("Frozen public matrix belongs to a different candidate EXE")
    selected_ids = SETTINGS.get("public_case_ids", [c["id"] for c in matrix["cases"]])
    if len(set(selected_ids)) != len(selected_ids) or set(selected_ids) - {
        c["id"] for c in matrix["cases"]
    }:
        raise ValueError("Explicit public-case selection contains unknown or duplicate IDs")
    cases = [c for c in matrix["cases"] if c["id"] in selected_ids]
    scene = Scene(ROOT, "RC-ECO")
    original = tree(scene.project)
    spec = Path(SETTINGS["sdk"]).parent / "driver_spec/schemas"
    documents = {read(p)["$id"]: read(p) for p in spec.glob("*.schema.json")}
    schema_hashes = {p.name: sha(p) for p in spec.glob("*.schema.json")}
    put(ROOT / "PUBLIC-MATRIX.json", matrix)
    put(
        ROOT / "PUBLIC-START.json",
        {
            "at": now(),
            "binding": scene.binding,
            "matrix_sha256": sha(matrix_file),
            "script_sha256": sha(Path(__file__)),
            "observer_sha256": sha(Path(__file__).with_name("process_tree.py")),
            "schema_files": schema_hashes,
            "project_files": original,
            "selected_case_ids": selected_ids,
        },
    )
    results = []
    prefix = "urn:tool-driver-cli-spec:"
    for case in cases:
        folder = ROOT / "calls" / case["id"]
        row = {"id": case["id"], "status": "failed", "schema": case["output_schema"]}
        try:
            env = scene.env.copy()
            env["KICAD_CLI_STATE_DIR"] = str(folder / "state")
            if case["sdk"] == "missing":
                env["KICAD_CLI_DRIVER_SDK_PATH"] = str(ROOT / "absent-sdk")
            elif case["sdk"] == "unset":
                env.pop("KICAD_CLI_DRIVER_SDK_PATH")
            elif case["sdk"] == "relative":
                env["KICAD_CLI_DRIVER_SDK_PATH"] = os.path.relpath(SETTINGS["sdk"], ROOT)
            if case["native_root"] == "missing":
                env["KICAD_CLI_ROOT"] = str(ROOT / "absent-native")
                env.pop("KICAD_CLI_PYTHON", None)
                env.pop("KICAD_CLI_OFFICIAL", None)
            output = folder / "artifacts"
            args = [
                a.replace("{board}", str(scene.project / "rc4.kicad_pcb")).replace(
                    "{out}", str(output)
                )
                for a in case["argv"]
            ]
            trace = observe([BINARY, *args], ROOT, env, folder)
            row["exit"] = trace["exit"]
            shell = (Path(os.environ["SystemRoot"]) / "System32/cmd.exe").resolve()
            additional = [
                p
                for p in trace["processes"]
                if not infrastructure(p, BINARY, os.environ["SystemRoot"])
            ]
            row["metadata_helpers"] = [
                {"pid": p["pid"], "command": "cmd /c ver", "purpose": "OS version query"}
                for p in trace["processes"]
                if Path(p["image"]).resolve() == shell
                and infrastructure(p, BINARY, os.environ["SystemRoot"])
            ]
            row["no_native_dispatch_observed"] = not additional
            row["processes"] = [
                {
                    "pid": p["pid"],
                    "creation_filetime": p["creation_filetime"],
                    "image_name": Path(p["image"]).name,
                    "image_sha256": p["image_sha256"],
                    "owned": p["owned"],
                    "exit": p["exit"],
                    "observed_at": p["observed_at"],
                    "exited_at": p["exited_at"],
                }
                for p in trace["processes"]
            ]
            row["trace_sha256"] = sha(folder / "TRACE.json")
            row["stdout_sha256"] = trace["stdout_sha256"]
            assert trace["exit"] == case["expected_exit"], "Unexpected exit"
            assert case["native_allowed"] or not additional, (
                "Unexpected native/other child dispatch"
            )
            if case["output_schema"] == "text-help":
                data = (folder / "stdout").read_text(encoding="utf-8")
                assert "kicad-cli" in data and "bootstrap" in data
            else:
                envelope = read(folder / "stdout")
                validate_document(envelope, {"$ref": prefix + "response:2.0"}, documents)
                error = case["expected_error"]
                assert envelope["ok"] is (error is None)
                if error:
                    value = envelope["error"]
                    assert value["code"] == error and value["retryable"] is False
                    assert value["details"]["dispatch_state"] == "not_started"
                    assert value["details"]["next_action"]
                    row["error_code"] = value["code"]
                    row["dispatch_state"] = value["details"]["dispatch_state"]
                    if error == "E_CONFIG":
                        assert value["details"]["phase"] == "runtime_bootstrap"
                    if case["id"].startswith("malformed"):
                        assert "CSV" in value["details"]["next_action"]
                else:
                    data = envelope["data"]
                    schema = case["output_schema"]
                    selected = data["preview"] if schema == "plan" else data
                    validate_document(selected, {"$ref": prefix + schema + ":2.0"}, documents)
                    if schema == "reference-index":
                        row["index_review"] = review_index(data, declaration["commands"])
                    elif schema == "reference-command":
                        command = data["command"]
                        assert command["path"] == case["argv"][2]
                        expected = next(
                            c for c in declaration["commands"] if c["path"] == command["path"]
                        )
                        row["command_review"] = review_command(
                            data, expected, declaration["schemas"], declaration["json_schemas"]
                        )
                        schemas = documents | data["json_schemas"]
                        key = (
                            "layers"
                            if command["path"] == "board plot"
                            else ("zones" if command["path"] == "board zone refill" else "nets")
                        )
                        values = ["F.Cu", "B.Cu"] if key == "layers" else ["/OUT1", "/OUT3"]
                        validate_document(
                            {key: values}, {"$ref": command["selection_schema"]}, schemas
                        )
                        rejected = False
                        try:
                            validate_document(
                                {key: [values[0], values[0]]},
                                {"$ref": command["selection_schema"]},
                                schemas,
                            )
                        except Exception as exc:
                            from jsonschema import ValidationError

                            if not isinstance(exc, ValidationError):
                                raise
                            rejected = True
                        assert rejected, "Review schema failed to reject duplicate values"
                        row["selection_schema"] = command["selection_schema"]
                    elif schema == "plan":
                        assert selected["can_apply"]
                        row["plan_level"] = selected["level"]
                        row["can_apply"] = selected["can_apply"]
                        row["base_revision"] = selected["base_snapshot"]["revision"]
                        row["selection_readback"] = selection_readback(case["id"], selected)
            assert tree(scene.project) == original and not output.exists()
            row.update(
                status="passed", protected_project_unchanged=True, requested_output_absent=True
            )
        except BaseException as exc:
            row["failure"] = type(exc).__name__ + ": " + str(exc)
            if (folder / "TRACE.json").is_file():
                row["trace_sha256"] = sha(folder / "TRACE.json")
            results.append(row)
            put(
                ROOT / "PUBLIC-RESULT.json",
                {
                    "status": "failed",
                    "binding": scene.binding,
                    "cases": results,
                    "remaining": [c["id"] for c in cases[len(results) :]],
                },
            )
            print(
                json.dumps({"id": case["id"], "status": "failed", "failure": row["failure"]}),
                flush=True,
            )
            return {"status": "failed"}
        results.append(row)
        put(folder / "CHECK.json", row)
        print(json.dumps({"id": case["id"], "status": "passed"}), flush=True)
    assert schema_hashes == {p.name: sha(p) for p in spec.glob("*.schema.json")}
    result = {
        "status": "passed",
        "binding": scene.binding,
        "matrix_sha256": sha(matrix_file),
        "new_observations": len(results),
        "reused_EXE_cases": len(matrix["reused_EXE_tests"]["ids"])
        if matrix["reused_EXE_tests"].get("exe_sha256") == sha(BINARY)
        else 0,
        "historical_EXE_cases": len(matrix["reused_EXE_tests"]["ids"]),
        "cases": results,
        "project_files_after": tree(scene.project),
        "finished_at": now(),
    }
    put(ROOT / "PUBLIC-RESULT.json", result)
    return result
