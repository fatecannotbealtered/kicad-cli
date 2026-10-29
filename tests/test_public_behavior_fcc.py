"""Public discovery/argument/format boundaries; no leaf trace is FCC proof."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((REPO / "kicad_cli/driver_contract.json").read_bytes())["reference"]


@pytest.fixture(autouse=True)
def artifact_binding(record_property):
    binary = os.environ.get("KICAD_CLI_TEST_BINARY")
    if binary:
        record_property(
            "fcc_artifact_sha256", hashlib.sha256(Path(binary).read_bytes()).hexdigest()
        )


def run(tmp_path, *args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", KICAD_CLI_STATE_DIR=str(tmp_path / "state"))
    env.pop("KICAD_CLI_SCOPE_FILE", None)
    binary = os.environ.get("KICAD_CLI_TEST_BINARY")
    prefix = [binary] if binary else [sys.executable, "-B", "-m", "kicad_cli.main"]
    proc = subprocess.run(
        [*prefix, *args],
        cwd=tmp_path if binary else REPO,
        env=env,
        capture_output=True,
        timeout=40,
    )
    return proc


@pytest.mark.parametrize("command", CONTRACT["commands"], ids=lambda c: c["path"])
def test_each_public_parameter_is_discoverable(tmp_path, command):
    proc = run(tmp_path, "reference", "--command", command["path"], "--compact")
    assert proc.returncode == 0
    doc = json.loads(proc.stdout)
    observed = doc["data"]["command"]
    assert observed["path"] == command["path"]
    assert observed["params"] == command["params"]
    assert observed["driver"]["effects"] == command["driver"]["effects"]
    help_result = run(tmp_path, *command["path"].split(), "--help")
    assert help_result.returncode == 0
    text = help_result.stdout.decode("utf-8")
    assert command["path"] in text
    for parameter in command["params"]:
        assert "--" + parameter["name"] in text
    if command["type"] == "write":
        assert "--confirm" in text and "--dry-run" in text
    else:
        assert "--fields" in text


@pytest.mark.parametrize(
    "args,code",
    [
        (["reference", "--format", "raw"], "E_USAGE"),
        (["reference", "--fields", "absent"], "E_VALIDATION"),
        (["reference", "--dry-run"], "E_USAGE"),
        (["reference", "--command", "board invalid"], "E_VALIDATION"),
        (["changelog", "--since", "invalid"], "E_VALIDATION"),
        (
            ["sch", "edit", "--schematic", "missing", "--changes", "missing", "--fields", "x"],
            "E_USAGE",
        ),
        (["sch", "edit", "--dry-run", "--confirm", "bogus"], "E_USAGE"),
        (
            [
                "sch",
                "edit",
                "--schematic",
                "missing",
                "--changes",
                "missing",
                "--request-id",
                "a",
                "--idempotency-key",
                "b",
                "--dry-run",
            ],
            "E_USAGE",
        ),
        (["board", "analyze", "ampacity", "--board", "missing", "--oz", "0"], "E_VALIDATION"),
        (["board", "analyze", "ampacity", "--board", "missing", "--dt", "-1"], "E_VALIDATION"),
        (["board", "analyze", "ampacity", "--board", "missing", "--dt", "nan"], "E_VALIDATION"),
        (["board", "check", "--board", "missing", "--severity", "invalid"], "E_VALIDATION"),
        (["board", "check", "--board", "missing", "--limit", "0"], "E_VALIDATION"),
        (["board", "check", "--board", "missing"], "E_NOT_FOUND"),
        (["sch", "edit", "--ignore-lock"], "E_USAGE"),
    ],
    ids=[
        "raw",
        "unknown-field",
        "read-dry-run",
        "unknown-command",
        "bad-version",
        "write-fields",
        "confirm-preview",
        "two-keys",
        "zero-oz",
        "negative-dt",
        "nan-dt",
        "bad-severity",
        "zero-limit",
        "missing-input",
        "removed-bypass",
    ],
)
def test_invalid_public_calls_have_consistent_error_exit_and_no_dispatch(tmp_path, args, code):
    proc = run(tmp_path, *args, "--compact")
    doc = json.loads(proc.stdout)  # Exactly one UTF-8 JSON document.
    assert doc["ok"] is False and doc["schema_version"] == "1.0", doc
    assert doc["error"]["code"] == code, doc
    declaration = CONTRACT["error_codes"][code]
    assert proc.returncode == declaration["exit"]
    assert doc["error"]["retryable"] == declaration["retryable"]
    assert doc["error"]["details"]["dispatch_state"] == "not_started"
    assert not list(tmp_path.rglob("*.kicad-cli.lock"))
    assert not list(tmp_path.rglob("*.kicad-cli.journal"))


def test_json_alias_projection_compact_and_text_formats(tmp_path):
    normal = run(tmp_path, "reference", "--fields", "tool,version")
    compact = run(
        tmp_path, "reference", "--fields", "tool,version", "--json", "--compact", "--quiet"
    )
    assert normal.returncode == compact.returncode == 0
    assert json.loads(normal.stdout)["data"] == json.loads(compact.stdout)["data"]
    assert len(compact.stdout) < len(normal.stdout)
    assert set(json.loads(compact.stdout)["data"]) == {"tool", "version"}
    text = run(tmp_path, "reference", "--fields", "tool,version", "--format", "text")
    assert text.returncode == 0 and b"kicad-cli" in text.stdout
    assert "raw" not in CONTRACT["global_options"][4]["enum"]
