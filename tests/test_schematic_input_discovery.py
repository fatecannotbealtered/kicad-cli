"""An independent caller can discover file inputs and diagnose refused previews."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from driver_test_utils import grant

REPO = Path(__file__).resolve().parents[1]
REQUEST = "urn:kicad-cli:sch-create-request:1"


@pytest.fixture(autouse=True)
def artifact_binding(record_property):
    binary = os.environ.get("KICAD_CLI_TEST_BINARY")
    if binary:
        record_property(
            "fcc_artifact_sha256", hashlib.sha256(Path(binary).read_bytes()).hexdigest()
        )


def call(tmp_path, *words):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("KICAD_CLI_") or key in {"KICAD_CLI_TRACE", "KICAD_CLI_ERROR_TRACE"}
    }
    env.update(grant(tmp_path, effects=["observe", "change", "persist"]))
    env.update(KICAD_CLI_ROOT=str(tmp_path / "no-installation"), PYTHONIOENCODING="utf-8")
    binary = os.environ.get("KICAD_CLI_TEST_BINARY")
    if binary:
        env["KICAD_CLI_DRIVER_SDK_PATH"] = os.environ.get(
            "KICAD_CLI_ACCEPT_DRIVER_SDK", str(REPO / "kicad_cli/_driver_sdk")
        )
    prefix = [binary] if binary else [sys.executable, "-B", "-m", "kicad_cli.main"]
    process = subprocess.run(
        [*prefix, *words, "--compact"],
        cwd=tmp_path if binary else REPO,
        env=env,
        capture_output=True,
        timeout=60,
    )
    return process.returncode, json.loads(process.stdout)


def test_selected_create_reference_explains_document_and_automatic_layout(tmp_path):
    code, result = call(tmp_path, "reference", "--command", "sch create")
    assert code == 0 and result["ok"]
    data = result["data"]
    assert data["command"]["driver"]["request_schema"] == REQUEST
    request = data["json_schemas"][REQUEST]
    document = request["$defs"]["circuit_spec"]
    from jsonschema import Draft202012Validator

    Draft202012Validator.check_schema(document)
    validator = Draft202012Validator(document)
    blank = {"mode": "blank", "title": "Empty root"}
    validator.validate(blank)
    example = next(item for item in document["examples"] if "parts" in item)
    validator.validate(example)
    # Required circuit fields belong to the ordinary branch, rather than the
    # union root: blank is explicit, while normal circuits remain nonempty.
    for required in ("parts", "nets"):
        missing = {key: value for key, value in example.items() if key != required}
        errors = list(validator.iter_errors(missing))
        assert any(
            error.validator == "required" and set(error.validator_value) == {"parts", "nets"}
            for error in errors
        )
    assert not validator.is_valid({"parts": [], "nets": []})
    assert document["properties"]["parts"]["minItems"] == 1
    assert document["properties"]["nets"]["minItems"] == 1
    assert document["additionalProperties"] is False
    for key, value in (
        ("parts", example["parts"]),
        ("nets", example["nets"]),
        ("hierarchy", {}),
        ("paper", "A4"),
    ):
        assert not validator.is_valid({**blank, key: value})
    assert "connect" in document["properties"]["nets"]["items"]["required"]
    assert document["properties"]["parts"]["items"]["additionalProperties"] is False
    assert "automatic" in document["description"].lower()
    assert "unsupported" in document["description"].lower()
    assert (
        "existing"
        in next(p for p in data["command"]["params"] if p["name"] == "out")["description"].lower()
    )


@pytest.mark.parametrize("bad", ["missing-connect", "coordinates", "top-level-layout"])
def test_invalid_spec_names_the_file_field_before_any_library_or_write(tmp_path, bad):
    spec = {
        "parts": [{"ref": "R1", "symbol": "Device:R"}],
        "nets": [{"name": "N", "connect": ["R1.1", "R1.2"]}],
    }
    if bad == "missing-connect":
        spec["nets"][0]["pins"] = spec["nets"][0].pop("connect")
        expected_field, expected_word = "/nets/0", "connect"
    elif bad == "coordinates":
        spec["parts"][0]["at"] = [50, 50]
        expected_field, expected_word = "/parts/0", "at"
    else:
        spec["layout"] = {"groups": [["R1"]]}
        expected_field, expected_word = "/", "layout"
    source = tmp_path / "circuit.json"
    source.write_text(json.dumps(spec), encoding="utf-8")
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    code, result = call(
        tmp_path, "sch", "create", "--spec", str(source), "--out", str(tmp_path), "--dry-run"
    )
    assert code == 2 and not result["ok"], result
    error = result["error"]
    assert error["code"] == "E_VALIDATION"
    assert error["details"]["parameter"] == "--spec"
    assert error["details"]["dispatch_state"] == "not_started"
    assert error["details"]["schema_ref"] == REQUEST + "#/$defs/circuit_spec"
    assert all(
        isinstance(problem["field"], str)
        and problem["field"].startswith("/")
        and isinstance(problem["problem"], str)
        for problem in error["details"]["problems"]
    )
    assert any(
        p["field"] == expected_field and expected_word in p["problem"]
        for p in error["details"]["problems"]
    )
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original
    assert not list(tmp_path.glob("*.kicad_*")) and not list(tmp_path.glob("*.net"))
    _, observed = call(tmp_path, "operation", "list")
    assert observed["data"]["items"] == []


def test_create_missing_output_directory_names_out_and_next_step(tmp_path):
    source = tmp_path / "circuit.json"
    source.write_text("{}", encoding="utf-8")
    out = tmp_path / "not-created"
    code, result = call(
        tmp_path, "sch", "create", "--spec", str(source), "--out", str(out), "--dry-run"
    )
    assert code == 2 and not result["ok"]
    details = result["error"]["details"]
    assert details["parameter"] == "--out"
    assert "directory" in details["next_action"].lower()
    assert not out.exists()


def test_link_findings_recommend_current_public_commands_without_writing(tmp_path):
    from kicad_cli.fileformat.sexpr import Document

    project = tmp_path / "owned-project"
    shutil.copytree(REPO / "tests/fixtures/sch_edit", project)
    board = project / "edit.kicad_pcb"
    document = Document.load(board)
    footprints = document.root.find_all("footprint")
    footprints[0].remove(footprints[0].find("path"))
    footprints[1].find("path").set(1, '"/11111111-1111-1111-1111-111111111111"')
    document.save(board)
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    code, result = call(tmp_path, "project", "links", "inspect", "--board", str(board))
    assert code == 0 and result["ok"], result
    data = result["data"]
    assert data["status"] == "FAIL" and data["unlinked"] == data["dangling"] == 1
    findings = {item["id"]: item for item in data["findings"]}
    assert "project links repair" in findings["L1-unlinked"]["fix"]
    assert "project links inspect" in findings["L1-unlinked"]["fix"]
    assert "project links repair" in findings["L2-dangling"]["fix"]
    assert any("project parity" in item for item in data["not_checked"])
    assert "sch relink" not in json.dumps(data) and "board parity" not in json.dumps(data)
    assert {p: p.read_bytes() for p in before} == before
