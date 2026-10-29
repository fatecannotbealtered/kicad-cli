"""Pure evidence/controller regression; no EXE, native process or ACL experiment."""

import base64
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import ValidationError

from bench.persistence_acceptance import environment_matrix as matrix
from bench.persistence_acceptance import support

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def synthetic_system_root_for_pure_helpers(tmp_path, monkeypatch):
    if "SystemRoot" not in os.environ:
        monkeypatch.setenv("SystemRoot", str(tmp_path / "synthetic-windows"))


@pytest.fixture
def frozen():
    return matrix.generate(
        json.loads((REPO / "docs/PUBLIC_COMMANDS.json").read_bytes()),
        exe_sha256="a" * 64,
        build_source_commit="b" * 40,
    )


@pytest.fixture
def documents():
    return {
        value["$id"]: value
        for value in (
            json.loads(path.read_bytes())
            for path in (REPO / "kicad_cli/driver_spec/schemas").glob("*.schema.json")
        )
    }


def selected(frozen, identity):
    return next(case for case in frozen["cases"] if case["id"] == identity)


def doctor(case):
    return {
        "ok": True,
        "schema_version": "1.0",
        "meta": {"duration_ms": 1},
        "data": {
            "checks": [
                {
                    "check": name,
                    "status": status,
                    "fix": None
                    if status == "pass"
                    else "Inspect KICAD_CLI_PYTHON and KICAD_CLI_OFFICIAL; keep unpublishable.",
                }
                for name, status in case["expected_checks"].items()
            ]
        },
    }


def failure(case):
    return {
        "ok": False,
        "schema_version": "1.0",
        "meta": {"duration_ms": 1},
        "error": {
            "code": case["expected_error"],
            "message": "Owned environment refusal",
            "retryable": False,
            "details": {
                "dispatch_state": "not_started",
                "next_action": "Fix KICAD_CLI_DRIVER_SDK_PATH beside driver_spec before retrying.",
                "phase": "runtime_bootstrap",
                "failure_kind": "pin_mismatch",
            },
        },
    }


def test_matrix_is_finite_and_declares_rejections_without_engineering_writes(frozen):
    cases = frozen["cases"]
    assert len(cases) == len({c["id"] for c in cases}) == 17
    assert all(not c["engineering_write_allowed"] for c in cases)
    assert all("--confirm" not in c["argv"] for c in cases)
    assert {c["preparation"] for c in cases} >= {
        "python-relative",
        "python-missing",
        "official-relative",
        "official-missing",
        "deny-state",
        "exclusive-state",
        "legacy-conflict",
    }
    for c in cases:
        if c["expected_error"]:
            assert c["expected_dispatch_state"] == "not_started"
            assert (
                c["expected_exit"]
                == frozen["reference"]["error_codes"][c["expected_error"]]["exit"]
            )
        else:
            assert c["expected_exit"] == 0


def test_generator_rejects_unavailable_public_contract(frozen):
    reference = copy.deepcopy(frozen["reference"])
    reference["commands"] = [c for c in reference["commands"] if c["path"] != "doctor"]
    with pytest.raises(ValueError, match="public commands"):
        matrix.generate(reference, exe_sha256="a" * 64, build_source_commit="b" * 40)
    reference = copy.deepcopy(frozen["reference"])
    reference["error_codes"]["E_IO"]["retryable"] = True
    with pytest.raises(ValueError, match="non-retryable"):
        matrix.generate(reference, exe_sha256="a" * 64, build_source_commit="b" * 40)


@pytest.mark.parametrize("identity", ["ENV-doctor-python-missing", "ENV-doctor-deny-state"])
def test_doctor_completion_does_not_mean_all_environment_checks_pass(frozen, documents, identity):
    case = selected(frozen, identity)
    checked = matrix.check_response(case, doctor(case), documents=documents, native_paths={})
    assert checked["diagnostic_completed"]
    assert checked["all_checks_passed"] is False
    assert "fail" in checked["checks"].values()


@pytest.mark.parametrize(
    "mutation", ["implicit-fallback", "missing-check", "duplicate-check", "no-fix"]
)
def test_inaccurate_doctor_observations_are_not_accepted(frozen, documents, mutation):
    case = selected(frozen, "ENV-doctor-python-missing")
    envelope = doctor(case)
    rows = envelope["data"]["checks"]
    row = next(row for row in rows if row["check"] == "native_backend")
    if mutation == "implicit-fallback":
        row.update(status="pass", fix=None)
    elif mutation == "missing-check":
        rows.remove(row)
    elif mutation == "duplicate-check":
        rows.append(dict(row))
    else:
        row["fix"] = None
    with pytest.raises((AssertionError, KeyError, ValidationError)):
        matrix.check_response(case, envelope, documents=documents, native_paths={})


@pytest.mark.parametrize(
    "field,value",
    [
        ("phase", "unrelated_phase"),
        ("failure_kind", "runtime_unavailable"),
        ("dispatch_state", "unknown"),
        ("next_action", "Ignore the fixed runtime pin."),
    ],
)
def test_sdk_pin_refusal_needs_specific_bootstrap_evidence(frozen, documents, field, value):
    case = selected(frozen, "ENV-sdk-pin-mismatch")
    envelope = failure(case)
    assert (
        matrix.check_response(case, envelope, documents=documents, native_paths={})["error_code"]
        == "E_CONFIG"
    )
    envelope["error"]["details"][field] = value
    with pytest.raises((AssertionError, ValidationError)):
        matrix.check_response(case, envelope, documents=documents, native_paths={})


@pytest.mark.parametrize("ids", [["not-in-matrix"], ["ENV-board-missing", "ENV-board-missing"]])
def test_case_selection_cannot_invent_or_duplicate_observations(frozen, monkeypatch, ids):
    monkeypatch.setattr(support, "SETTINGS", {"environment_case_ids": ids})
    with pytest.raises(ValueError, match="Unknown or repeated"):
        matrix._select(frozen)


def test_owned_permission_cases_require_separate_explicit_enable(frozen, monkeypatch):
    monkeypatch.setattr(support, "SETTINGS", {})
    with pytest.raises(PermissionError, match="allow_owned_permission_controls"):
        matrix._select(frozen)
    monkeypatch.setattr(support, "SETTINGS", {"environment_case_ids": ["ENV-board-missing"]})
    assert len(matrix._select(frozen)) == 1


def test_control_path_must_be_owned_child(tmp_path):
    root = tmp_path / "new-owned-copy"
    root.mkdir()
    assert matrix.owned_path(root / "state", root) == root / "state"
    for invalid in (root, tmp_path / "historical-evidence", root / ".." / "historical-evidence"):
        with pytest.raises(ValueError):
            matrix.owned_path(invalid, root)


def test_controls_do_nothing_without_execution_authorization(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    (state / "operations.sqlite3").write_bytes(b"Owned inert fixture")
    monkeypatch.setattr(support, "SETTINGS", {"allow_owned_permission_controls": True})
    monkeypatch.setattr(matrix, "_powershell", lambda *a, **k: pytest.fail("ACL helper executed"))
    with pytest.raises(PermissionError):
        with matrix.denied_state(state, tmp_path, tmp_path):
            pytest.fail("Permission control executed")
    with pytest.raises(PermissionError):
        with matrix.exclusive_state(state / "operations.sqlite3", tmp_path, tmp_path):
            pytest.fail("File lock executed")
    assert (state / "operations.sqlite3").read_bytes() == b"Owned inert fixture"


@pytest.mark.parametrize(
    "condition", ["missing-native-probe", "unclassified-descendant", "incomplete-tree"]
)
def test_process_coverage_is_independent_of_response_success(
    frozen, documents, tmp_path, monkeypatch, condition
):
    from bench.persistence_acceptance import process_tree

    case = selected(frozen, "ENV-doctor-valid")
    binary = tmp_path / "candidate.exe"
    binary.write_bytes(b"inert candidate fixture")
    native_paths = {name: tmp_path / (name + ".exe") for name in ("python", "official")}
    for path in native_paths.values():
        path.write_bytes(b"inert native fixture")
    monkeypatch.setattr(support, "BINARY", binary)
    processes = [{"image": str(binary), "owned": True, "creation_filetime": 1}]
    for path in native_paths.values():
        processes.append(
            {
                "image": str(path),
                "image_sha256": support.sha(path),
                "owned": True,
                "creation_filetime": 2,
            }
        )
    if condition == "missing-native-probe":
        processes.pop()
    if condition == "unclassified-descendant":
        processes.append(
            {
                "image": str(tmp_path / "unobserved-handler.exe"),
                "owned": True,
                "creation_filetime": 3,
            }
        )

    def observe(argv, root, env, folder):
        folder.mkdir()
        support.put(folder / "stdout", doctor(case))
        (folder / "stderr").write_bytes(b"")
        support.put(folder / "TRACE.json", {"synthetic": True})
        return {
            "complete_tree": condition != "incomplete-tree",
            "processes": processes,
            "exit": 0,
            "stdout_sha256": support.sha(folder / "stdout"),
            "stderr_sha256": support.sha(folder / "stderr"),
        }

    monkeypatch.setattr(process_tree, "observe", observe)
    with pytest.raises(AssertionError):
        matrix._call(
            SimpleNamespace(root=tmp_path),
            case,
            {},
            case["argv"],
            tmp_path / "call",
            native_paths,
            documents,
        )


@pytest.mark.skipif(
    os.name != "nt", reason="Windows DACL context; pure native argument tests remain portable"
)
def test_acl_restoration_is_attempted_after_a_controlled_call_fails(tmp_path, monkeypatch):
    state = tmp_path / "owned-state"
    state.mkdir()
    original = {
        "sddl": "synthetic-original-dacl",
        "owner": "synthetic-owner",
        "sid": "synthetic-sid",
    }
    calls = []
    writes = []

    def powershell(script, path, **kwargs):
        assert path == state
        calls.append((script, kwargs))
        return {"sddl": "synthetic-deny-dacl"} if "AddAccessRule" in script else original

    def write_dacl(path, root, sddl):
        assert path == state and root == tmp_path
        writes.append(sddl)
        return {"owner_group_sacl_written": False, "fixture_only": True}

    original_open = Path.open

    def path_open(path, *args, **kwargs):
        if path == state / "denied-write-probe":
            error = PermissionError("synthetic denied write")
            error.winerror = 5
            raise error
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(
        support,
        "SETTINGS",
        {"execute": True, "authorized_by": "pure fixture", "allow_owned_permission_controls": True},
    )
    monkeypatch.setattr(matrix, "_powershell", powershell)
    monkeypatch.setattr(matrix, "_write_dacl", write_dacl)
    monkeypatch.setattr(
        matrix, "_owner", lambda: {"pid": 0, "creation_filetime": 1, "synthetic": True}
    )
    monkeypatch.setattr(Path, "open", path_open)
    with pytest.raises(RuntimeError, match="retained observed failure"):
        with matrix.denied_state(state, tmp_path, tmp_path):
            raise RuntimeError("retained observed failure")
    restored = support.read(tmp_path / "ACL-RESTORED.json")
    assert restored["restored"] and restored["after"] == original
    assert writes == ["synthetic-deny-dacl", original["sddl"]]
    assert all("Set-Acl" not in script for script, _kwargs in calls)
    assert (state / "restored-write-probe").read_bytes() == matrix.PROBE


def test_matrix_generation_does_not_start_processes_or_change_acl(tmp_path, monkeypatch):
    binary = tmp_path / "inert.exe"
    binary.write_bytes(b"Not an executable; hashed only")
    output = tmp_path / "frozen.json"
    monkeypatch.setattr(
        matrix.subprocess, "run", lambda *a, **k: pytest.fail("Process started during plan")
    )
    monkeypatch.setattr(
        matrix, "_powershell", lambda *a, **k: pytest.fail("ACL helper started during plan")
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "environment_matrix",
            "--reference",
            str(REPO / "docs/PUBLIC_COMMANDS.json"),
            "--exe",
            str(binary),
            "--build-source-commit",
            "b" * 40,
            "--out",
            str(output),
        ],
    )
    matrix.main()
    assert len(support.read(output)["cases"]) == 17
    assert set(tmp_path.iterdir()) == {binary, output}


def test_acl_helper_uses_system_modules_without_mutating_host_environment(tmp_path, monkeypatch):
    inherited = "Z:/owned fixture/pwsh7/Modules;Z:/owned fixture/other modules"
    monkeypatch.setenv("PSModulePath", inherited)
    before = dict(matrix.os.environ)
    observed = {}

    def run(argv, **options):
        observed.update(argv=argv, **options)
        return SimpleNamespace(returncode=0, stdout=b'{"fixture_only":true}', stderr=b"")

    monkeypatch.setattr(matrix.subprocess, "run", run)
    assert matrix._powershell("@{fixture_only=$true} | ConvertTo-Json", tmp_path) == {
        "fixture_only": True
    }
    expected_shell = (
        Path(matrix.os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    assert observed["argv"][0] == str(expected_shell)
    env = observed["env"]
    assert [(key, value) for key, value in env.items() if key.casefold() == "psmodulepath"] == [
        ("PSModulePath", str(expected_shell.parent / "Modules"))
    ]
    program = base64.b64decode(observed["argv"][-1]).decode("utf-16-le")
    assert "Import-Module -Name ($PSHOME + " in program
    assert r"\Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1" in program
    assert inherited not in program
    assert dict(matrix.os.environ) == before


@pytest.mark.parametrize("control,flags", [(0x8404, 0x20000004), (0x9404, 0x80000004)])
def test_native_acl_request_writes_only_dacl_and_original_protection(tmp_path, control, flags):
    state = tmp_path / "owned state"
    acl = object()
    args = matrix._dacl_write_args(state, tmp_path, control, acl)
    assert args == (str(state), 1, flags, None, None, acl, None)
    assert args[2] & (0x1 | 0x2 | 0x8) == 0  # No owner, group or SACL information.


@pytest.mark.parametrize("control,acl", [(0, object()), (0x1000, object()), (0x4, None)])
def test_native_acl_request_rejects_absent_or_null_dacl(tmp_path, control, acl):
    with pytest.raises(ValueError, match="non-null DACL"):
        matrix._dacl_write_args(tmp_path / "state", tmp_path, control, acl)


def test_native_acl_request_refuses_outside_owned_root(tmp_path):
    with pytest.raises(ValueError, match="owned execution root"):
        matrix._dacl_write_args(tmp_path / "other", tmp_path / "owned", 0x4, object())


def test_native_dacl_write_requires_execution_authorization(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(support, "SETTINGS", {"allow_owned_permission_controls": True})
    with pytest.raises(PermissionError, match="explicit execution"):
        matrix._write_dacl(state, tmp_path, "D:(A;;FA;;;SY)")
