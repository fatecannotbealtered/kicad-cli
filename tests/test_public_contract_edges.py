"""Actual process boundaries for bootstrap help and existing selection guards."""

import hashlib
import json
import os
import shutil

import pytest
from test_standalone_dependencies import REPO, scene  # noqa: F401

HELP_CASES = [
    ([], 0, None),
    (["--help"], 0, None),
    (["-h"], 0, None),
    (["help"], 0, None),
    (["--help", "--help=false"], 2, "E_USAGE"),
    (["--help=false", "--help"], 0, None),
    (["--help=true"], 0, None),
    (["--help", "--compact=invalid"], 2, "E_VALIDATION"),
    (["--compact=invalid", "--help"], 2, "E_VALIDATION"),
    (["--help", "--help=invalid"], 2, "E_VALIDATION"),
    (["--help=invalid", "--help"], 2, "E_VALIDATION"),
]


@pytest.mark.parametrize("sdk", ["valid", "missing", "unset"])
@pytest.mark.parametrize(
    "args,valid_exit,valid_error",
    HELP_CASES,
    ids=["argv-" + str(i) for i in range(len(HELP_CASES))],
)
def test_root_help_bootstrap_and_pinned_boolean_order(scene, sdk, args, valid_exit, valid_error):  # noqa: F811
    cwd, _deps, env, call, binary = scene
    if sdk == "missing":
        env["KICAD_CLI_DRIVER_SDK_PATH"] = str(cwd / "absent-sdk")
    elif sdk == "unset":
        env.pop("KICAD_CLI_DRIVER_SDK_PATH")
    bootstrap = not args or args in (["--help"], ["-h"], ["help"])
    available = sdk == "valid" or (sdk == "unset" and not binary)
    expected_exit = valid_exit if bootstrap or available else 4
    expected_error = valid_error if bootstrap or available else "E_CONFIG"
    sentinel = cwd / "protected.kicad_sch"
    sentinel.write_bytes(b"protected engineering sentinel")
    proc, text = call(args, json_output=False)
    assert proc.returncode == expected_exit, text
    if expected_error:
        result = json.loads(text)
        assert not result["ok"] and result["error"]["code"] == expected_error
        assert result["error"]["retryable"] is False
        assert result["error"]["details"]["dispatch_state"] == "not_started"
        assert result["error"]["details"]["next_action"]
        if not available:
            assert result["error"]["details"]["phase"] == "runtime_bootstrap"
    else:
        assert "kicad-cli" in text and "bootstrap" in text
        assert b"\r" not in proc.stdout and not proc.stdout.startswith(b"\xef\xbb\xbf")
    assert sentinel.read_bytes() == b"protected engineering sentinel"
    assert not (cwd / "state").exists()


def project(scene_value):
    cwd, _deps, env, _call, _binary = scene_value
    target = cwd / "owned RC"
    shutil.copytree(REPO / "bench/saved_examples/RC-ECO", target)
    scope = {
        "scope_id": "owned-public-contract-edges",
        "tool": "kicad-cli",
        "granted_by": "User-authorized isolated contract regression",
        "granted_at": "2026-10-13T00:00:00Z",
        "expires_at": None,
        "effects": ["observe", "change", "persist", "artifact"],
        "dangerous": False,
        "roots": [str(cwd)],
        "outputs": [str(cwd)],
    }
    from pathlib import Path

    Path(env["KICAD_CLI_SCOPE_FILE"]).write_text(json.dumps(scope), encoding="utf-8")
    return target


def hashes(target):
    return {
        p.relative_to(target).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in target.rglob("*")
        if p.is_file()
    }


REFUSALS = [
    (["board", "route", "rewidth", "--nets", "OUT1,OUT1"], "E_USAGE"),
    (["board", "route", "rewidth", "--nets", "OUT1", "--nets", "OUT1"], "E_USAGE"),
    (["board", "route", "rewidth", "--nets", '["OUT1","OUT1"]'], "E_USAGE"),
    (["board", "route", "rewidth", "--classes", "Default,Default"], "E_USAGE"),
    (["board", "route", "rewidth", "--classes", "Default", "--classes", "Default"], "E_USAGE"),
    (["board", "route", "rewidth", "--classes", '["Default","Default"]'], "E_USAGE"),
    (
        ["project", "netclass", "set", "--name", "P", "--width", "0.25", "--nets", "OUT1,OUT1"],
        "E_USAGE",
    ),
    (["board", "plot", "--file-format", "svg", "--layers", "F.Cu,F.Cu"], "E_USAGE"),
    (["board", "plot", "--file-format", "svg", "--layers", "F.Cu", "--layers", "F.Cu"], "E_USAGE"),
    (["board", "plot", "--file-format", "svg", "--layers", '["F.Cu","F.Cu"]'], "E_USAGE"),
    (["fab", "gerber", "--layers", "F.Cu,F.Cu"], "E_USAGE"),
    (["board", "route", "rewidth", "--nets", "OUT1", "--classes", "Default"], "E_USAGE"),
    (["board", "route", "rewidth"], "E_USAGE"),
    (["board", "zone", "refill"], "E_USAGE"),
    (["board", "zone", "refill", "--zones", "same,same"], "E_VALIDATION"),
    (["board", "zone", "refill", "--zones", "same", "--all"], "E_USAGE"),
]


@pytest.mark.parametrize(
    "args,error", REFUSALS, ids=["selection-" + str(i) for i in range(len(REFUSALS))]
)
def test_selection_refusals_are_not_dispatched_and_preserve_files(scene, args, error):  # noqa: F811
    _cwd, _deps, _env, call, _binary = scene
    target = project(scene)
    original = hashes(target)
    proc, result = call([*args, "--board", str(target / "rc4.kicad_pcb"), "--dry-run", "--compact"])
    assert proc.returncode == 2 and result["error"]["code"] == error, result
    assert result["error"]["details"]["dispatch_state"] == "not_started"
    assert result["error"]["retryable"] is False
    assert result["error"]["details"]["next_action"]
    assert hashes(target) == original
    proc, listing = call(["operation", "list", "--compact"])
    assert proc.returncode == 0 and listing["data"]["items"] == []
    assert hashes(target) == original


@pytest.mark.parametrize(
    "kind,value,missing",
    [
        ("nets", '["/OUT1","/OUT3"]', ['["/OUT1"', '"/OUT3"]']),
        ("classes", '["RC4"]', ['["RC4"]']),
    ],
)
def test_unique_json_array_text_is_not_a_selection_input(scene, kind, value, missing):  # noqa: F811
    _cwd, _deps, _env, call, _binary = scene
    target = project(scene)
    original = hashes(target)
    proc, result = call(
        [
            "board",
            "route",
            "rewidth",
            "--" + kind,
            value,
            "--board",
            str(target / "rc4.kicad_pcb"),
            "--dry-run",
            "--compact",
        ]
    )
    assert proc.returncode == 3 and result["error"]["code"] == "E_NOT_FOUND", result
    assert result["error"]["details"]["missing"] == missing
    assert result["error"]["details"]["dispatch_state"] == "not_started"
    assert hashes(target) == original
    proc, listing = call(["operation", "list", "--compact"])
    assert proc.returncode == 0 and listing["data"]["items"] == []


@pytest.mark.parametrize("kind", ["nets", "classes", "netclass-nets", "layers"])
def test_unique_selection_preview_and_review_schema(scene, kind):  # noqa: F811
    cwd, _deps, env, call, _binary = scene
    if kind == "layers":
        native = os.environ.get("KICAD_CLI_ACCEPT_NATIVE_ROOT")
        if not native:
            pytest.skip("Explicit native installation required for the unique-layer preview")
        env["KICAD_CLI_ROOT"] = native
    target = project(scene)
    original = hashes(target)
    if kind == "layers":
        path = "board plot"
        args = [
            "--file-format",
            "svg",
            "--layers",
            "F.Cu",
            "--layers",
            "B.Cu",
            "--out",
            str(cwd / "new plots"),
        ]
        fragment = {"layers": ["F.Cu", "B.Cu"]}
    elif kind == "netclass-nets":
        path = "project netclass set"
        args = ["--name", "ContractReview", "--width", "0.25", "--nets", "/OUT1,/OUT3"]
        fragment = {"nets": ["/OUT1", "/OUT3"]}
    else:
        path = "board route rewidth"
        names = ["/OUT1", "/OUT3"] if kind == "nets" else ["RC4"]
        args = ["--" + kind, ",".join(names)]
        fragment = {kind: names}
    proc, reference = call(["reference", "--command", path, "--compact"])
    assert proc.returncode == 0
    command = reference["data"]["command"]
    schema_uri, definition = command["selection_schema"].split("#/$defs/")
    schema = reference["data"]["json_schemas"][schema_uri]["$defs"][definition]
    from jsonschema import Draft202012Validator

    validator = Draft202012Validator(schema)
    validator.validate(fragment)
    key = next(iter(fragment))
    duplicated = {key: [fragment[key][0], fragment[key][0]]}
    assert list(validator.iter_errors(duplicated))
    proc, help_text = call([*path.split(), "--help"], json_output=False)
    assert proc.returncode == 0 and "Selection constraints:" in help_text
    proc, preview = call(
        [*path.split(), *args, "--board", str(target / "rc4.kicad_pcb"), "--dry-run", "--compact"]
    )
    assert proc.returncode == 0 and preview["data"]["preview"]["can_apply"], preview
    assert hashes(target) == original
    assert not (cwd / "new plots").exists()


@pytest.mark.parametrize("value", ["[broken", "[1]"])
@pytest.mark.parametrize("kind", ["nets", "layers"])
def test_invalid_list_hints_only_recommend_supported_syntax(scene, kind, value):  # noqa: F811
    _cwd, _deps, _env, call, _binary = scene
    target = project(scene)
    original = hashes(target)
    args = ["board", "route", "rewidth"] if kind == "nets" else ["board", "plot"]
    proc, result = call(
        [*args, "--" + kind, value, "--board", str(target / "rc4.kicad_pcb"), "--dry-run"]
    )
    assert proc.returncode == 2 and result["error"]["code"] == "E_USAGE", result
    details = result["error"]["details"]
    assert details["dispatch_state"] == "not_started"
    assert "CSV" in details["next_action"] and "repeat" in details["next_action"]
    assert "array" not in details["next_action"]
    assert hashes(target) == original
