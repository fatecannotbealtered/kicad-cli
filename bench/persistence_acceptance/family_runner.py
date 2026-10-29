"""Run a pre-frozen public family through the real executable and external observer."""

import json
import os
import re
from pathlib import Path

from .process_tree import observe
from .public_contract import infrastructure, validate_document
from .support import BINARY, ROOT, SETTINGS, Scene, now, put, read, sha, tree
from .surface_review import review_command, review_index


def check_output(case, value, reference, schemas):
    if case["kind"] == "help":
        expected = next(c for c in reference["commands"] if c["path"] == case["command"])
        assert "usage: kicad-cli " + case["command"] in value
        names = re.findall(r"^  --([\w-]+)  ", value, re.MULTILINE)
        assert names == [p["name"] for p in expected["params"]], "Help parameter identity differs"
        for parameter in expected["params"]:
            line = next(
                line
                for line in value.splitlines()
                if line.startswith("  --" + parameter["name"] + "  ")
            )
            assert line.endswith("(required)" if parameter["required"] else "(optional)")
        defaults = {p["name"]: p["default"] for p in expected["params"] if "default" in p}
        assert "Literal defaults: " + json.dumps(defaults, sort_keys=True) in value
        return {"parameters": len(expected["params"]), "literal_defaults": defaults}
    validate_document(value, {"$ref": "urn:tool-driver-cli-spec:response:2.0"}, schemas)
    if case["kind"] == "error":
        assert value["ok"] is False and value["error"]["code"] == case["expected_error"], value
        error = value["error"]
        assert error["retryable"] is False and error["details"]["next_action"]
        assert error["details"]["dispatch_state"] == "not_started"
        if case.get("expected_parameter"):
            assert error["details"].get("parameter") == case["expected_parameter"], error
        return {
            "error_code": error["code"],
            "dispatch_state": error["details"]["dispatch_state"],
            "parameter": error["details"].get("parameter"),
        }
    assert value["ok"] is True
    data = value["data"]
    if case.get("schema"):
        validate_document(
            data, {"$ref": "urn:tool-driver-cli-spec:" + case["schema"] + ":2.0"}, schemas
        )
    if case["kind"] == "full-reference":
        for key in (
            "commands",
            "schemas",
            "json_schemas",
            "global_options",
            "error_codes",
            "exit_codes",
        ):
            assert data[key] == reference[key], key
        return {"commands": len(data["commands"]), "global_options": len(data["global_options"])}
    if case["kind"] == "index":
        return review_index(data, reference["commands"])
    if case["kind"] == "reference-command":
        command = next(c for c in reference["commands"] if c["path"] == case["command"])
        return review_command(data, command, reference["schemas"], reference["json_schemas"])
    if case["kind"] == "projection":
        assert data == case["expected_data"]
    if case["kind"] == "version":
        assert data["version"] == reference["version"] and data["tool"] == reference["tool"]
    return {"data_contract_checked": True}


def run():
    matrix_file = Path(SETTINGS["family_matrix"])
    matrix = read(matrix_file)
    scene = Scene(ROOT, "RC-ECO")
    original = tree(scene.project)
    ids = SETTINGS.get("family_case_ids", [c["id"] for c in matrix["cases"]])
    if set(ids) - {c["id"] for c in matrix["cases"]} or len(ids) != len(set(ids)):
        raise ValueError("Unknown or repeated finite-matrix IDs")
    cases = [c for c in matrix["cases"] if c["id"] in ids]
    spec = Path(SETTINGS["sdk"]).parent / "driver_spec/schemas"
    schemas = {read(p)["$id"]: read(p) for p in spec.glob("*.schema.json")}
    put(ROOT / "FROZEN-MATRIX.json", matrix)
    put(
        ROOT / "FAMILY-START.json",
        {
            "at": now(),
            "binding": scene.binding,
            "matrix_sha256": sha(matrix_file),
            "selected_ids": ids,
            "project_files": original,
            "script_sha256": sha(Path(__file__)),
            "observer_sha256": sha(Path(__file__).with_name("process_tree.py")),
        },
    )
    results = []
    for index, case in enumerate(cases):
        folder = ROOT / "calls" / f"request-{index:03d}"
        row = {"id": case["id"], "kind": case["kind"], "status": "failed"}
        try:
            env = scene.env.copy()
            env["KICAD_CLI_STATE_DIR"] = str(folder / "state")
            args = [
                s.replace("{board}", str(scene.project / "rc4.kicad_pcb")) for s in case["argv"]
            ]
            trace = observe([BINARY, *args], ROOT, env, folder)
            row.update(
                exit=trace["exit"],
                observer_complete=trace["complete_tree"],
                trace_sha256=sha(folder / "TRACE.json"),
                stdout_sha256=trace["stdout_sha256"],
                stderr_sha256=trace["stderr_sha256"],
            )
            row["no_unclassified_or_native_process"] = all(
                infrastructure(p, BINARY, os.environ["SystemRoot"]) for p in trace["processes"]
            )
            assert row["no_unclassified_or_native_process"], "Unexpected native/unknown process"
            stdout, stderr = (folder / "stdout").read_bytes(), (folder / "stderr").read_bytes()
            assert not stdout.startswith(b"\xef\xbb\xbf") and b"\r" not in stdout
            stderr.decode("utf-8")
            value = stdout.decode("utf-8") if case["kind"] == "help" else json.loads(stdout)
            assert trace["exit"] == case["expected_exit"], value
            row["checks"] = check_output(case, value, matrix["reference"], schemas)
            row["project_unchanged"] = tree(scene.project) == original
            assert row["project_unchanged"]
            row["status"] = "passed"
        except BaseException as exc:
            row["failure"] = type(exc).__name__ + ": " + str(exc)
            if (folder / "TRACE.json").is_file():
                row["trace_sha256"] = sha(folder / "TRACE.json")
            row["project_unchanged"] = tree(scene.project) == original
            results.append(row)
            put(
                ROOT / "FAMILY-RESULT.json",
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
        put(folder / "CHECK.json", row)
        results.append(row)
        print(json.dumps({"id": case["id"], "status": "passed"}), flush=True)
    result = {
        "status": "passed",
        "binding": scene.binding,
        "matrix_sha256": sha(matrix_file),
        "cases": results,
        "project_files_after": tree(scene.project),
        "finished_at": now(),
    }
    put(ROOT / "FAMILY-RESULT.json", result)
    return result
