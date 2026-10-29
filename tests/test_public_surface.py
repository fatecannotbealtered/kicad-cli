"""Complete surface discovery/refusal, separately from task semantics and FCC."""

import hashlib
import json
import os
import re

import pytest
from test_standalone_dependencies import REPO, scene  # noqa: F401

from bench.persistence_acceptance.surface_review import review_command, review_index
from kicad_cli import driver_runtime, registry
from scripts.public_surface import inventory, markdown

REFERENCE = json.loads((REPO / "kicad_cli/driver_contract.json").read_bytes())["reference"]
COMMANDS = {c["path"]: c for c in REFERENCE["commands"]}


@pytest.mark.skipif(
    bool(os.environ.get("KICAD_CLI_TEST_BINARY")), reason="Source-only parser audit"
)
def test_generated_surface_and_actual_parser_are_complete():
    data = inventory()
    assert json.loads((REPO / "docs/PUBLIC_COMMANDS.json").read_bytes()) == data
    assert (REPO / "docs/PUBLIC_COMMANDS.md").read_text(encoding="utf-8") == markdown(data)
    registered = {c["path"]: c for c in registry.build()}
    with driver_runtime.create_cli() as cli:
        from kicad_cli._driver_sdk.runtime import BOOL_FLAGS, VALUE_FLAGS

        assert {"--" + g["name"] for g in data["global_options"]} == BOOL_FLAGS | VALUE_FLAGS
        assert set(COMMANDS) == set(registered) == driver_runtime.MIGRATED
        assert set(COMMANDS) == {" ".join(p) for p in cli.commands}
        for path, command in COMMANDS.items():
            assert registered[path]["params"] == command["params"]
            assert cli.commands[tuple(path.split())] == command
            raw = {}
            for p in command["params"]:
                if p["required"]:
                    value = p.get(
                        "enum", ["1" if p["type"] in {"number", "integer"} else "sample"]
                    )[0]
                    raw[p["name"]] = [str(value)]
            if path == "board route rewidth":
                raw["nets"] = ["GND"]
            parsed = cli._params(command, raw, None)
            for p in command["params"]:
                if p["name"] not in raw:
                    assert (p["name"] in parsed) == ("default" in p)
                    if "default" in p:
                        assert parsed[p["name"]] == p["default"]


@pytest.mark.skipif(
    bool(os.environ.get("KICAD_CLI_TEST_BINARY")), reason="Source-only parser audit"
)
@pytest.mark.parametrize(
    "path,name",
    [(path, p["name"]) for path, c in sorted(COMMANDS.items()) for p in c["params"]],
    ids=[path + ":" + p["name"] for path, c in sorted(COMMANDS.items()) for p in c["params"]],
)
def test_each_parameter_parser_contract(path, name):
    """Syntax/types/defaults only: no task handler, native or EXE acceptance."""
    with driver_runtime.create_cli() as cli:
        from kicad_cli._driver_sdk.runtime import UsageProblem

        command = COMMANDS[path]
        selected = next(p for p in command["params"] if p["name"] == name)

        def sample(p):
            if p.get("enum"):
                return str(p["enum"][0])
            return {"number": "1", "integer": "1", "boolean": "true"}.get(p["type"], "sample")

        raw = {p["name"]: [sample(p)] for p in command["params"] if p["required"]}
        if path == "board route rewidth":
            raw["classes" if name == "classes" else "nets"] = ["GND"]
        raw[name] = [sample(selected)]
        value = cli._params(command, raw, None)[name]
        expected = {"number": float, "integer": int, "boolean": bool}.get(selected["type"], str)
        if selected["multiple"]:
            assert isinstance(value, list) and all(isinstance(v, expected) for v in value)
        else:
            assert isinstance(value, expected)
            raw[name] *= 2
            with pytest.raises(UsageProblem) as error:
                cli._params(command, raw, None)
            assert error.value.problem == "conflicting_flags"


@pytest.mark.parametrize("path", sorted(COMMANDS))
def test_every_leaf_help_reference_and_zero_write_refusal(scene, path):  # noqa: F811
    cwd, _deps, _env, call, _binary = scene
    protected = cwd / "protected"
    protected.mkdir()
    sentinel = protected / "keep.kicad_pcb"
    sentinel.write_bytes(b"sentinel: never open or modify for invalid arguments")
    before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
    command = COMMANDS[path]
    proc, help_text = call([*path.split(), "--help"], json_output=False)
    assert proc.returncode == 0 and "usage: kicad-cli " + path in help_text
    assert all("--" + p["name"] in help_text for p in command["params"])
    assert all(flag in help_text for flag in ("--help", "--version", "--json"))
    defaults = {p["name"]: p["default"] for p in command["params"] if "default" in p}
    assert "Literal defaults: " + json.dumps(defaults, sort_keys=True) in help_text
    assert "Effects: " + ", ".join(command["driver"]["effects"]) in help_text
    proc, reference = call(["reference", "--command", path, "--compact"])
    assert proc.returncode == 0 and reference["ok"]
    review_command(reference["data"], command, REFERENCE["schemas"], REFERENCE["json_schemas"])
    assert reference["data"]["command"] == command
    assert command["output_schema"] in reference["data"]["schemas"]
    for args in (
        [*path.split(), "--unknown-onboarding-option"],
        *([path.split()] if any(p["required"] for p in command["params"]) else []),
    ):
        proc, error = call([*args, "--compact"])
        assert proc.returncode == 2 and error["error"]["code"] == "E_USAGE", error
        assert error["error"]["retryable"] is False
        assert error["error"]["details"]["dispatch_state"] == "not_started"
        assert error["error"]["details"]["next_action"]
        assert hashlib.sha256(sentinel.read_bytes()).hexdigest() == before
        assert list(protected.iterdir()) == [sentinel]
        assert not (cwd / "state").exists()


@pytest.mark.parametrize(
    "args", [["not-a-command"], ["board"], ["board", "route"], ["--compact", "reference"]]
)
def test_unknown_and_incomplete_paths_use_pinned_errors_without_state(scene, args):  # noqa: F811
    cwd, _deps, _env, call, _binary = scene
    proc, result = call([*args, "--compact"])
    assert proc.returncode == 2 and result["error"]["code"] == "E_USAGE"
    assert result["error"]["details"]["dispatch_state"] == "not_started"
    assert result["error"]["details"]["next_action"]
    assert not (cwd / "state").exists()


def test_root_discovery_flags_and_global_parser_semantics(scene):  # noqa: F811
    cwd, _deps, _env, call, _binary = scene
    proc, version = call(["--version", "--compact"])
    assert proc.returncode == 0 and version["data"]["version"] == REFERENCE["version"]
    proc, full = call(["reference", "--compact=false", "--compact", "--format=text", "--json"])
    assert proc.returncode == 0 and full["data"]["global_options"] == REFERENCE["global_options"]
    proc, index = call(["reference", "--index", "--compact"])
    assert proc.returncode == 0 and index["data"]["view"] == "index"
    review_index(index["data"], REFERENCE["commands"])
    assert {c["path"] for c in index["data"]["commands"]} == set(COMMANDS)
    for command in index["data"]["commands"]:
        assert command["effects"] == COMMANDS[command["path"]]["driver"]["effects"]
    proc, text = call(["--help"], json_output=False)
    assert proc.returncode == 0
    assert b"\r" not in proc.stdout and not proc.stdout.startswith(b"\xef\xbb\xbf")
    assert all("--" + g["name"] in text for g in REFERENCE["global_options"])
    found = {
        line.strip().removeprefix("kicad-cli ")
        for line in text.split("Available commands:")[1].splitlines()
        if line.strip()
    }
    assert found == set(COMMANDS)
    for args in (
        ["reference", "--compact=1"],
        ["reference", "--compact", "false"],
        ["reference", "--version"],
        ["reference", "--index", "--command", "board check"],
    ):
        proc, error = call(args)
        assert proc.returncode == 2 and error["error"]["code"] in {"E_USAGE", "E_VALIDATION"}
        assert error["error"]["details"]["dispatch_state"] == "not_started"
    assert not (cwd / "state").exists()


@pytest.mark.parametrize(
    "args",
    [
        ["board", "route", "rewidth"],
        ["board", "zone", "refill", "--zones", "one", "--zones", "two"],
    ],
    ids=["rewidth-needs-selection", "zone-selection-scalar"],
)
def test_documented_selection_refusals_preserve_target(scene, args):  # noqa: F811
    cwd, _deps, _env, call, _binary = scene
    board = cwd / "protected.kicad_pcb"
    board.write_bytes((REPO / "tests/fixtures/sch_edit/edit.kicad_pcb").read_bytes())
    before = board.read_bytes()
    proc, error = call([*args, "--board", str(board), "--dry-run", "--compact"])
    assert proc.returncode == 2 and error["error"]["code"] == "E_USAGE", error
    assert error["error"]["retryable"] is False
    assert error["error"]["details"]["dispatch_state"] == "not_started"
    assert error["error"]["details"]["next_action"]
    assert board.read_bytes() == before and not (cwd / "state").exists()
    assert not list(cwd.glob("*.kicad-cli.*"))


def test_changelog_since_uses_released_versions_without_state(scene):  # noqa: F811
    cwd, _deps, _env, call, _binary = scene
    proc, all_entries = call(["changelog", "--compact"])
    assert proc.returncode == 0 and all_entries["ok"]
    entries = all_entries["data"]["entries"]
    assert entries and all(re.fullmatch(r"\d+\.\d+\.\d+", e["version"]) for e in entries)
    proc, newer = call(["changelog", "--since", "0.0.0", "--compact"])
    assert proc.returncode == 0 and newer["data"]["entries"] == entries
    latest = max((e["version"] for e in entries), key=lambda v: tuple(map(int, v.split("."))))
    proc, none = call(["changelog", "--since", latest, "--compact"])
    assert proc.returncode == 0 and none["data"]["entries"] == [] and none["data"]["count"] == 0
    proc, invalid = call(["changelog", "--since", "Unreleased", "--compact"])
    assert proc.returncode == 2 and invalid["error"]["code"] == "E_VALIDATION"
    assert invalid["error"]["details"]["dispatch_state"] == "not_started"
    assert invalid["error"]["details"]["next_action"]
    assert not (cwd / "state").exists()
