"""Owned three-part copies: refusal/replay must match actual saved bytes."""

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest
from test_standalone_dependencies import REPO, scene  # noqa: F401 - public-process fixture

CASES = {
    "missing-scope": (4, "E_FORBIDDEN"),
    "outside-scope": (4, "E_FORBIDDEN"),
    "missing-persist-permission": (4, "E_FORBIDDEN"),
    "illegal-parameter": (2, "E_USAGE"),
    "missing-input": (3, "E_NOT_FOUND"),
    "parameter-drift": (6, "E_CONFLICT"),
    "input-drift": (6, "E_CONFLICT"),
    "dependency-drift": (6, "E_CONFLICT"),
    "sdk-drift": (4, "E_CONFIG"),
    "scope-drift": (6, "E_CONFLICT"),
    "repeat-confirm": (6, "E_CONFLICT"),
    "existing-output": (6, "E_CONFLICT"),
}


def hashes(root):
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.iterdir()
        if p.suffix in {".kicad_sch", ".kicad_pcb", ".kicad_pro"}
    }


@pytest.mark.parametrize("case", list(CASES))
def test_owned_workflow_refusals(scene, case):  # noqa: F811 - imported pytest fixture
    cwd, deps, env, call, _binary = scene
    selected = os.environ.get("KICAD_CLI_ACCEPT_BASELINE")
    if not selected:
        pytest.skip("Explicit frozen three-part input not selected for outside-cwd refusals")
    baseline = Path(selected).resolve()
    frozen = hashes(baseline)
    assert (
        frozen
        == json.loads((REPO / "bench/engineering_change_intent.json").read_bytes())[
            "baseline_files"
        ]
    )
    project = cwd / "独立 工程"
    project.mkdir()
    for name in frozen:
        shutil.copyfile(baseline / name, project / name)
    before = hashes(project)
    assert len(before) == 3
    change = cwd / "修改.json"
    change.write_text(
        json.dumps({"changes": [{"op": "set", "ref": "C1", "value": "470nF"}]}), encoding="utf-8"
    )
    scope_path = Path(env["KICAD_CLI_SCOPE_FILE"])
    scope = {
        "scope_id": "owned-standalone-refusal",
        "tool": "kicad-cli",
        "granted_by": "User-authorized isolated copies only",
        "granted_at": "2026-10-09T00:00:00Z",
        "expires_at": None,
        "effects": ["observe", "change", "persist", "artifact"],
        "dangerous": False,
        "roots": [str(cwd)],
        "outputs": [str(cwd)],
        "notes": "No original input writes",
    }
    if case != "missing-scope":
        if case == "outside-scope":
            allowed = cwd / "other allowed directory"
            allowed.mkdir()
            scope["roots"] = [str(allowed)]
        if case == "missing-persist-permission":
            scope["effects"] = ["observe", "change"]
        scope_path.write_text(json.dumps(scope), encoding="utf-8")
    args = [
        "sch",
        "edit",
        "--schematic",
        str(project / "decoupling.kicad_sch"),
        "--changes",
        str(change),
    ]
    operation = None
    expected, error = CASES[case]
    (cwd / "expected.json").write_text(
        json.dumps({"exit": expected, "error": error, "before": before}), encoding="utf-8"
    )
    sentinel = None
    sdk_before = None
    if case in {
        "input-drift",
        "dependency-drift",
        "sdk-drift",
        "scope-drift",
        "repeat-confirm",
        "parameter-drift",
    }:
        proc, preview = call(args + ["--dry-run", "--compact"])
        assert proc.returncode == 0 and preview["ok"]
        operation = preview["data"]["preview"]["operation_id"]
        token = preview["data"]["confirm_token"]
        args += ["--confirm", token, "--compact"]
        if case == "parameter-drift":
            other = cwd / "different-changes.json"
            other.write_bytes(change.read_bytes())
            args[args.index(str(change))] = str(other)
        elif case == "input-drift":
            p = project / "decoupling.kicad_sch"
            p.write_bytes(p.read_bytes() + b"\n")
            before = hashes(project)  # Preserve the external edit; no silent restore.
        elif case == "dependency-drift":
            env["KICAD_CLI_PYTHON"] = str(cwd / "different-native-python.exe")
        elif case == "sdk-drift":
            sdk_file = deps / "_driver_sdk/__init__.py"
            sdk_before = sdk_file.read_bytes()
            sdk_file.write_bytes(sdk_before + b"\n# owned dependency drift\n")
        elif case == "scope-drift":
            scope["notes"] += " revised authorization"
            scope_path.write_text(json.dumps(scope), encoding="utf-8")
        else:
            proc, applied = call(args)
            assert proc.returncode == 0 and applied["ok"]
            assert applied["data"]["receipt"]["operation_id"] == operation
            before = hashes(project)
            assert before != frozen
    elif case == "existing-output":
        native_root = os.environ.get("KICAD_CLI_ACCEPT_NATIVE_ROOT")
        if not native_root:
            pytest.skip("Existing native installation required for export refusal")
        env["KICAD_CLI_ROOT"] = str(Path(native_root).resolve())
        output = cwd / "已有 输出"
        output.mkdir()
        sentinel = output / "keep.txt"
        sentinel.write_bytes(b"owned existing output, preserve")
        args = [
            "fab",
            "gerber",
            "--board",
            str(project / "decoupling.kicad_pcb"),
            "--out",
            str(output),
            "--dry-run",
            "--compact",
        ]
    else:
        args += ["--dry-run", "--compact"]
        if case == "illegal-parameter":
            args += ["--not-a-parameter"]
        elif case == "missing-input":
            args[args.index(str(change))] = str(cwd / "missing-changes.json")
    proc, refused = call(args)
    refused_exit = proc.returncode
    assert proc.returncode == expected and not refused["ok"], refused
    assert refused["error"]["code"] == error, refused
    assert refused["error"]["retryable"] is False
    assert refused["error"]["details"]["dispatch_state"] == "not_started"
    assert refused["error"]["details"]["next_action"]
    assert hashes(project) == before and hashes(baseline) == frozen
    if sdk_before is not None:
        assert refused["error"]["details"]["phase"] == "runtime_bootstrap"
        sdk_file.write_bytes(sdk_before)  # Restore our exact known-good copy to enable observation.
    if sentinel:
        assert sentinel.read_bytes() == b"owned existing output, preserve"
        assert list(sentinel.parent.iterdir()) == [sentinel]
    if operation:
        proc, shown = call(["operation", "show", "--id", operation, "--compact"])
        assert proc.returncode == 0 and shown["ok"]
    proc, listed = call(["operation", "list", "--compact"])
    assert proc.returncode == 0 and listed["ok"]
    records = listed["data"]["items"]
    assert len(records) == (1 if operation else 0)
    if operation:
        assert records[0]["operation_id"] == operation
        assert records[0]["status"] == ("succeeded" if case == "repeat-confirm" else "planned")
    assert hashes(project) == before and hashes(baseline) == frozen
    assert not list(project.glob("*.kicad-cli.lock"))
    (cwd / "result.json").write_text(
        json.dumps(
            {
                "case": case,
                "expected_exit": expected,
                "observed_exit": refused_exit,
                "error": error,
                "after": before,
                "original_unchanged": True,
                "operation_id": operation,
                "operation_records": records,
                "status": "passed",
            }
        ),
        encoding="utf-8",
    )
