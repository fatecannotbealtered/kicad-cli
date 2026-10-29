"""Finite, opt-in public EXE environment refusals on newly owned local copies.

Generating a matrix only reads its declared reference and binary. Execution is
integrated by the developer acceptance entry after ``support.configure``. No
product module, SDK implementation or internal handler is imported here.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import ctypes
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

from . import support

PREFIX = "urn:tool-driver-cli-spec:"
PIN = "9607a35fe0ea33fe822e1f2ad2704eae260d4018"
PROBE = b"Owned acceptance permission probe; no engineering content.\n"


def generate(reference, *, exe_sha256, build_source_commit):
    """Freeze accurate public calls and separate diagnostic success from health."""
    reference = reference.get("data", reference)
    paths = {c["path"] for c in reference["commands"]}
    required = {"reference", "doctor", "context", "fab gerber", "board check", "operation list"}
    if not required <= paths:
        raise ValueError("The declared public commands do not support this finite matrix")
    cases = []

    def add(name, command, *, preparation="ordinary", error=None, checks=None, native=()):
        argv = command.split()
        if command == "fab gerber":
            argv += ["--board", "{board}", "--out", "{output}", "--dry-run"]
        elif command == "board check":
            argv += ["--board", "{missing_board}"]
        expected_exit = 0 if error is None else reference["error_codes"][error]["exit"]
        if error is not None and reference["error_codes"][error]["retryable"]:
            raise ValueError("This refusal matrix requires non-retryable environment errors")
        schema = "response" if error else "doctor" if command == "doctor" else "context"
        cases.append(
            {
                "id": name,
                "argv": [*argv, "--compact"],
                "preparation": preparation,
                "output_schema": schema,
                "expected_exit": expected_exit,
                "expected_error": error,
                "expected_checks": checks or {},
                "native_allowed": bool(native),
                "required_native_images": list(native),
                "engineering_write_allowed": False,
                "state_write_allowed": preparation not in {"sdk-drift", "spec-drift", "deny-state"},
                "expected_dispatch_state": "not_started" if error else None,
                "protected": ["project", "configuration", "source SDK/spec", "native executables"],
                "contract_sources": [
                    "reference " + command,
                    "fixed SDK " + PIN,
                    "tests/test_standalone_dependencies.py",
                ],
            }
        )

    for name in ("sdk", "spec"):
        add(
            "ENV-" + name + "-pin-mismatch",
            "reference",
            preparation=name + "-drift",
            error="E_CONFIG",
        )
    common = {"release_readiness": "fail", "authorization": "pass", "session_backend": "warn"}
    add(
        "ENV-doctor-valid",
        "doctor",
        checks=common | {"native_backend": "pass", "official_cli": "pass", "state_store": "pass"},
        native=("python", "official"),
    )
    for name in ("python", "official"):
        for problem in ("missing", "relative"):
            add(
                f"ENV-doctor-{name}-{problem}",
                "doctor",
                preparation=name + "-" + problem,
                checks=common
                | {
                    "native_backend": "fail" if name == "python" else "pass",
                    "official_cli": "fail" if name == "official" else "pass",
                    "state_store": "pass",
                },
                native=("official" if name == "python" else "python",),
            )
    add(
        "ENV-explicit-paths-win-over-legacy",
        "context",
        preparation="legacy-conflict",
        native=("python", "official"),
    )
    for problem in ("missing", "relative"):
        add(
            "ENV-export-python-" + problem,
            "fab gerber",
            preparation="python-" + problem,
            error="E_BACKEND_UNAVAILABLE",
        )
    add("ENV-board-missing", "board check", error="E_NOT_FOUND")
    for kind in ("file", "directory"):
        add(
            "ENV-output-existing-" + kind,
            "fab gerber",
            preparation="existing-" + kind,
            error="E_CONFLICT",
        )
    for mechanism in ("deny-state", "exclusive-state"):
        add(
            "ENV-doctor-" + mechanism,
            "doctor",
            preparation=mechanism,
            checks=common
            | {"native_backend": "pass", "official_cli": "pass", "state_store": "fail"},
            native=("python", "official"),
        )
        add("ENV-list-" + mechanism, "operation list", preparation=mechanism, error="E_IO")
    return {
        "matrix_version": "1",
        "family": "environment-refusals",
        "binding": {"source_commit": build_source_commit, "exe_sha256": exe_sha256},
        "sdk_pin": PIN,
        "reference": reference,
        "cases": cases,
        "execution": {
            "serial": True,
            "engineering_confirmations": 0,
            "stop_on_first_failure": True,
            "permission_controls_require": (
                "allow_owned_permission_controls=true and explicit execution authorization"
            ),
            "exclusive_state_setup": (
                "One separately recorded public operation list initializes each new ledger "
                "before an owned share-zero file handle is acquired."
            ),
        },
        "limits": [
            "Local isolation is not another machine, a clean installation "
            "or cross-platform support.",
            "Expected missing-dependency refusals are not successful support "
            "of those environments.",
            "Doctor ok/exit=0 proves diagnostic completion; named check statuses "
            "are assessed independently.",
            "No application session, global ACL, security setting or original dependency "
            "is changed.",
            "Output collisions are pre-preview refusals; post-preview drift is "
            "a separate lifecycle matrix.",
            "Previous standalone dependency evidence remains separate; these observations "
            "are not replacement FCC rows.",
        ],
    }


def owned_path(path, root):
    """Never change ACLs or hold files through a link or outside this fresh root."""
    path, root = Path(path).absolute(), Path(root).absolute()
    if ".." in path.parts or path == root or not path.is_relative_to(root):
        raise ValueError("Control target must be a child of the new owned execution root")
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Control target contains a link or reparse point")
    return path


def _owner():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.GetProcessTimes.argtypes = [ctypes.c_void_p] * 5
    times = [ctypes.c_ulonglong() for _ in range(4)]
    if not kernel.GetProcessTimes(kernel.GetCurrentProcess(), *[ctypes.byref(t) for t in times]):
        raise ctypes.WinError(ctypes.get_last_error())
    kernel.GetCommandLineW.restype = ctypes.c_wchar_p
    return {
        "pid": os.getpid(),
        "creation_filetime": times[0].value,
        "command_line": kernel.GetCommandLineW(),
        "ownership": "current acceptance controller only",
    }


def _powershell(script, path, *, sddl=None):
    shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    # A pwsh host can place its incompatible Security module ahead of 5.1's.
    # Restrict discovery in this helper only, including case-insensitive aliases.
    env = {key: value for key, value in os.environ.items() if key.casefold() != "psmodulepath"}
    env["PSModulePath"] = str(shell.parent / "Modules")
    env["KICAD_ACCEPT_OWNED_PATH"] = str(path)
    if sddl is not None:
        env["KICAD_ACCEPT_ORIGINAL_SDDL"] = sddl
    prefix = (
        "$ErrorActionPreference='Stop'; "
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
        "Import-Module -Name ($PSHOME + "
        "'\\Modules\\Microsoft.PowerShell.Security\\Microsoft.PowerShell.Security.psd1') "
        "-ErrorAction Stop; "
    )
    command = base64.b64encode((prefix + script).encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        [
            str(shell),
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            command,
        ],
        capture_output=True,
        env=env,
        timeout=30,
        creationflags=0x08000000,
    )
    if result.returncode:
        raise RuntimeError("Owned ACL helper failed: " + result.stderr.decode("utf-8", "replace"))
    return json.loads(result.stdout)


def _dacl_write_args(path, root, control, acl):
    """Access-only native parameters; inherited/protected state stays explicit."""
    path = owned_path(path, root)
    if not control & 0x4 or not acl:
        raise ValueError("An explicit present, non-null DACL is required")
    flags = 0x4 | (0x80000000 if control & 0x1000 else 0x20000000)
    # SE_FILE_OBJECT, DACL_SECURITY_INFORMATION and protection only. Null owner,
    # primary group and SACL avoid resolving or rewriting domain principals.
    return str(path), 1, flags, None, None, acl, None


def _write_dacl(path, root, sddl):
    path = owned_path(path, root)
    if (
        os.name != "nt"
        or not support.SETTINGS.get("execute")
        or not support.SETTINGS.get("authorized_by")
        or not support.SETTINGS.get("allow_owned_permission_controls")
    ):
        raise PermissionError("Owned DACL writes require explicit execution authorization")
    if not path.is_dir():
        raise ValueError("Owned DACL target must be an existing directory")
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer, number = ctypes.c_void_p, ctypes.c_uint32
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        ctypes.c_wchar_p,
        number,
        ctypes.POINTER(pointer),
        ctypes.POINTER(number),
    ]
    api.GetSecurityDescriptorControl.argtypes = [pointer, pointer, pointer]
    api.GetSecurityDescriptorDacl.argtypes = [pointer, pointer, pointer, pointer]
    api.SetNamedSecurityInfoW.argtypes = [
        ctypes.c_wchar_p,
        number,
        number,
        pointer,
        pointer,
        pointer,
        pointer,
    ]
    api.SetNamedSecurityInfoW.restype = number
    kernel.LocalFree.argtypes = [pointer]
    kernel.LocalFree.restype = pointer
    descriptor = pointer()
    if not api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), None
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        control, revision = ctypes.c_uint16(), number()
        present, defaulted, acl = ctypes.c_int(), ctypes.c_int(), pointer()
        if not api.GetSecurityDescriptorControl(
            descriptor, ctypes.byref(control), ctypes.byref(revision)
        ) or not api.GetSecurityDescriptorDacl(
            descriptor, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        args = _dacl_write_args(path, root, control.value, acl)
        status = api.SetNamedSecurityInfoW(*args)
        if status:
            raise ctypes.WinError(status)
        return {
            "method": "SetNamedSecurityInfoW",
            "security_information": args[2],
            "owner_group_sacl_written": False,
            "dacl_control": control.value & 0x150C,
        }
    finally:
        kernel.LocalFree(descriptor)


@contextlib.contextmanager
def denied_state(path, root, evidence):
    """Save/restore the exact owned directory DACL, with actual denied-write proof."""
    path = owned_path(path, root)
    if (
        os.name != "nt"
        or not support.SETTINGS.get("execute")
        or not support.SETTINGS.get("authorized_by")
        or not support.SETTINGS.get("allow_owned_permission_controls")
    ):
        raise PermissionError("Owned Windows permission controls were not explicitly enabled")
    if any(path.iterdir()):
        raise ValueError("The denied state directory must be a new empty owned directory")
    read_acl = (
        "$a=Get-Acl -LiteralPath $env:KICAD_ACCEPT_OWNED_PATH; "
        "@{sddl=$a.Sddl; owner=$a.Owner; "
        "sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value} "
        "| ConvertTo-Json -Compress"
    )
    before = _powershell(read_acl, path)
    witness = {
        "method": "owned directory deny Write for current SID",
        "owner": _owner(),
        "before": before,
    }
    support.put(evidence / "ACL-BEFORE.json", witness)
    try:
        prepared = _powershell(
            "$a=Get-Acl -LiteralPath $env:KICAD_ACCEPT_OWNED_PATH; "
            "$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User; "
            "$r=[System.Security.AccessControl.FileSystemAccessRule]::new("
            "$sid,'Write','ContainerInherit,ObjectInherit','None','Deny'); "
            "$a.AddAccessRule($r); "
            "@{sddl=$a.Sddl} | ConvertTo-Json -Compress",
            path,
        )
        witness["native_apply"] = _write_dacl(path, root, prepared["sddl"])
        witness["during"] = _powershell(read_acl, path)
        try:
            with (path / "denied-write-probe").open("xb") as handle:
                handle.write(PROBE)
        except PermissionError as exc:
            witness["write_denied"] = {"exception": type(exc).__name__, "winerror": exc.winerror}
        else:
            raise RuntimeError(
                "The owned permission denial did not take effect; no refusal coverage"
            )
        support.put(evidence / "ACL-HELD.json", witness)
        yield witness
    finally:
        owned_path(path, root)
        try:
            witness["native_restore"] = _write_dacl(path, root, before["sddl"])
            after = _powershell(read_acl, path)
            if after != before:
                raise RuntimeError("Owned state ACL did not restore exactly")
            with (path / "restored-write-probe").open("xb") as handle:
                handle.write(PROBE)
            witness.update(restored=True, after=after)
        except BaseException as exc:
            witness.update(restored=False, restoration_failure=type(exc).__name__ + ": " + str(exc))
            raise
        finally:
            support.put(evidence / "ACL-RESTORED.json", witness)


@contextlib.contextmanager
def exclusive_state(path, root, evidence):
    """Hold exactly one owned database file, never an application process."""
    path = owned_path(path, root)
    if not support.SETTINGS.get("execute") or not support.SETTINGS.get("authorized_by"):
        raise PermissionError("Owned file occupation requires explicit execution authorization")
    if os.name != "nt" or not path.is_file():
        raise ValueError("Exclusive state witness requires the new Windows ledger file")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = ctypes.c_void_p
    kernel.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        handle,
        ctypes.c_uint32,
        ctypes.c_uint32,
        handle,
    ]
    kernel.CreateFileW.restype = handle
    kernel.CloseHandle.argtypes = [handle]
    invalid = ctypes.c_void_p(-1).value

    def open_file():
        return kernel.CreateFileW(str(path), 0x80000000 | 0x40000000, 0, None, 3, 0x80, None)

    held = open_file()
    if held == invalid:
        raise ctypes.WinError(ctypes.get_last_error())
    witness = {
        "method": "CreateFileW existing owned SQLite file, share mode zero",
        "owner": _owner(),
        "path": str(path),
        "acquired_at": support.now(),
    }
    try:
        second = open_file()
        if second != invalid:
            kernel.CloseHandle(second)
            raise RuntimeError("Exclusive file sharing did not block a second handle")
        error = ctypes.get_last_error()
        if error != 32:
            raise RuntimeError("Expected ERROR_SHARING_VIOLATION, observed " + str(error))
        witness["second_handle_error"] = error
        support.put(evidence / "LOCK-HELD.json", witness)
        yield witness
    finally:
        if not kernel.CloseHandle(held):
            raise ctypes.WinError(ctypes.get_last_error())
        second = open_file()
        witness.update(released_at=support.now(), released=second != invalid)
        if second != invalid:
            kernel.CloseHandle(second)
        support.put(evidence / "LOCK-RELEASED.json", witness)
        if not witness["released"]:
            raise RuntimeError("Owned state file remains inaccessible after handle release")


def check_response(case, envelope, *, documents, native_paths):
    from .public_contract import validate_document

    validate_document(envelope, {"$ref": PREFIX + "response:2.0"}, documents)
    error = case["expected_error"]
    if error:
        assert envelope["ok"] is False
        value = envelope["error"]
        assert value["code"] == error and value["retryable"] is False
        details = value["details"]
        assert details["dispatch_state"] == "not_started" and details["next_action"]
        if case["preparation"] in {"sdk-drift", "spec-drift"}:
            assert (
                details["phase"] == "runtime_bootstrap"
                and details["failure_kind"] == "pin_mismatch"
            )
            assert "KICAD_CLI_DRIVER_SDK_PATH" in details["next_action"]
            assert "driver_spec" in details["next_action"]
        if case["preparation"].startswith("existing-"):
            assert (
                details["conflict"] == "state_drift" and "new directory" in details["next_action"]
            )
        return {
            "error_code": error,
            "dispatch_state": details["dispatch_state"],
            "next_action": details["next_action"],
        }
    assert envelope["ok"] is True
    data = envelope["data"]
    validate_document(data, {"$ref": PREFIX + case["output_schema"] + ":2.0"}, documents)
    if case["output_schema"] == "doctor":
        rows = data["checks"]
        assert len({row["check"] for row in rows}) == len(rows), "Duplicate doctor check"
        checks = {row["check"]: row for row in rows}
        for name, status in case["expected_checks"].items():
            assert checks[name]["status"] == status, (name, checks[name])
            if status == "fail":
                assert checks[name]["fix"]
        if checks["native_backend"]["status"] == "fail":
            assert "KICAD_CLI_PYTHON" in checks["native_backend"]["fix"]
        if checks["official_cli"]["status"] == "fail":
            assert "KICAD_CLI_OFFICIAL" in checks["official_cli"]["fix"]
        return {
            "diagnostic_completed": True,
            "checks": {name: checks[name]["status"] for name in case["expected_checks"]},
            "all_checks_passed": all(row["status"] == "pass" for row in rows),
        }
    components = {c["name"]: c for c in data["environment"]["components"]}
    for role, name in (
        ("python", "KiCad external pcbnew interpreter"),
        ("official", "KiCad official CLI"),
    ):
        component = components[name]
        assert component["identity_status"] == "observed"
        assert component["build_id"] == support.sha(native_paths[role])
    return {
        "explicit_component_hashes_matched": True,
        "legacy_environment_overrode_explicit_paths": False,
    }


def permission_selfcheck():
    """One harmless deny/restore cycle before any candidate permission case."""
    if not support.SETTINGS.get("allow_owned_permission_controls"):
        raise PermissionError("Permission selfcheck requires explicit owned DACL authorization")
    root = support.ROOT
    root.mkdir(parents=True, exist_ok=False)
    target = root / "empty owned permission probe"
    target.mkdir()
    support.put(
        root / "PLAN.json",
        {
            "attempts": 1,
            "candidate_invocations": 0,
            "engineering_confirmations": 0,
            "owner": _owner(),
            "scope": "only a new empty child directory DACL, deny Write and restore exact original",
            "on_policy_or_domain_error": "preserve diagnostics, do not bypass or retry",
            "script_sha256": support.sha(__file__),
        },
    )
    result = {"status": "failed", "candidate_invocations": 0}
    try:
        with denied_state(target, root, root) as witness:
            assert witness["write_denied"]
        assert witness["restored"]
        result.update(status="passed", witness=witness)
    except BaseException as exc:
        result["failure"] = type(exc).__name__ + ": " + str(exc)
        result["candidate_permission_cases"] = "not_covered"
    support.put(root / "RESULT.json", result)
    print(json.dumps({k: result[k] for k in ("status", "candidate_invocations")}))
    return result


def _select(matrix):
    ids = support.SETTINGS.get("environment_case_ids", [c["id"] for c in matrix["cases"]])
    all_ids = {c["id"] for c in matrix["cases"]}
    if len(ids) != len(set(ids)) or set(ids) - all_ids:
        raise ValueError("Unknown or repeated environment-matrix IDs")
    cases = [case for case in matrix["cases"] if case["id"] in ids]
    if any(c["preparation"] == "deny-state" for c in cases) and not support.SETTINGS.get(
        "allow_owned_permission_controls"
    ):
        raise PermissionError(
            "Selected owned ACL cases require allow_owned_permission_controls=true"
        )
    return cases


def _prepare(scene, case, area, native_paths):
    area.mkdir()
    state = area / "state"
    state.mkdir()
    env = scene.env.copy()
    env["KICAD_CLI_STATE_DIR"] = str(state)
    kind = case["preparation"]
    if kind in {"sdk-drift", "spec-drift"}:
        source_sdk = Path(support.SETTINGS["sdk"])
        source = source_sdk.parent
        for name, original in (
            ("_driver_sdk", source_sdk),
            ("driver_spec", source / "driver_spec"),
        ):
            shutil.copytree(original, area / name, ignore=shutil.ignore_patterns("__pycache__"))
        relative = (
            "_driver_sdk/__init__.py"
            if kind == "sdk-drift"
            else "driver_spec/scripts/check_spec.py"
        )
        selected = owned_path(area / relative, scene.root)
        before = support.sha(selected)
        with selected.open("ab") as stream:
            stream.write(b"\n# owned acceptance pin mismatch; do not import this copy\n")
        original_selected = source_sdk / "__init__.py" if kind == "sdk-drift" else source / relative
        support.put(
            area / "DEPENDENCY-DRIFT.json",
            {
                "relative": relative,
                "before": before,
                "after": support.sha(selected),
                "source_unchanged": support.sha(original_selected) == before,
            },
        )
        env["KICAD_CLI_DRIVER_SDK_PATH"] = str(area / "_driver_sdk")
    for name, variable in (("python", "KICAD_CLI_PYTHON"), ("official", "KICAD_CLI_OFFICIAL")):
        if kind == name + "-missing":
            env[variable] = str(area / ("missing-" + name + ".exe"))
        elif kind == name + "-relative":
            env[variable] = os.path.relpath(native_paths[name], scene.root)
    if kind == "legacy-conflict":
        env["KICAD_HOME"] = str(area / "unrelated absent legacy installation")
        # PATH supplies no KiCad candidate; the explicit absolute paths must win.
        env["PATH"] = (
            str(area / "unrelated absent PATH")
            + os.pathsep
            + str(Path(os.environ["SystemRoot"]) / "System32")
        )
    output = area / "requested output"
    if kind == "existing-directory":
        output.mkdir()
        (output / "preserve.bin").write_bytes(PROBE)
    elif kind == "existing-file":
        output.write_bytes(PROBE)
    replacements = {
        "{board}": str(scene.project / "rc4.kicad_pcb"),
        "{missing_board}": str(area / "missing board.kicad_pcb"),
        "{output}": str(output),
    }
    args = [replacements.get(arg, arg) for arg in case["argv"]]
    return env, args, state, output


def _output_state(path):
    if path.is_dir():
        return {"kind": "directory", "files": support.tree(path)}
    if path.is_file():
        return {"kind": "file", "sha256": support.sha(path)}
    return {"kind": "absent"}


def _call(scene, case, env, args, folder, native_paths, documents):
    from .process_tree import observe
    from .public_contract import infrastructure

    trace = observe([support.BINARY, *args], scene.root, env, folder)
    assert trace["complete_tree"], "Process observer did not cover the complete owned tree"
    observed = set()
    for process in trace["processes"]:
        assert process["owned"] and process["creation_filetime"]
        if infrastructure(process, support.BINARY, os.environ["SystemRoot"]):
            continue
        path = Path(process["image"]).resolve()
        matched = [role for role in case["required_native_images"] if path == native_paths[role]]
        assert matched, "Unexpected native or unclassified child: " + str(path)
        assert process["image_sha256"] == support.sha(path)
        observed.update(matched)
    assert observed == set(case["required_native_images"]), (
        "Required diagnostic probes were not observed"
    )
    stdout, stderr = (folder / "stdout").read_bytes(), (folder / "stderr").read_bytes()
    assert not stdout.startswith(b"\xef\xbb\xbf") and b"\r" not in stdout
    stderr.decode("utf-8")
    envelope = json.loads(stdout.decode("utf-8"))
    assert trace["exit"] == case["expected_exit"], envelope
    checks = check_response(case, envelope, documents=documents, native_paths=native_paths)
    return {
        "exit": trace["exit"],
        "observer_complete": True,
        "no_native_dispatch_observed": not observed,
        "native_roles_observed": sorted(observed),
        "trace_sha256": support.sha(folder / "TRACE.json"),
        "stdout_sha256": trace["stdout_sha256"],
        "stderr_sha256": trace["stderr_sha256"],
        "checks": checks,
    }


def run():
    """Execute a frozen finite selection serially; retain the first failed cycle."""
    if not support.SETTINGS.get("execute") or not support.SETTINGS.get("authorized_by"):
        raise PermissionError("Environment execution requires explicit owned-copy authorization")
    matrix_file = Path(support.SETTINGS["environment_matrix"])
    matrix = support.read(matrix_file)
    expected = generate(
        matrix["reference"],
        exe_sha256=support.SETTINGS["exe_sha256"],
        build_source_commit=support.SETTINGS["build_source_commit"],
    )
    if matrix != expected:
        raise ValueError(
            "Frozen environment matrix or EXE binding differs from the reviewed generator"
        )
    cases = _select(matrix)
    scene = support.Scene(support.ROOT, "RC-ECO")
    root = scene.root
    sdk = Path(support.SETTINGS["sdk"])
    spec = sdk.parent / "driver_spec"
    dependencies = {"sdk": support.tree(sdk), "spec": support.tree(spec)}
    documents = {
        support.read(p)["$id"]: support.read(p) for p in (spec / "schemas").glob("*.schema.json")
    }
    native_paths = {
        "python": Path(scene.env["KICAD_CLI_PYTHON"]).resolve(),
        "official": Path(scene.env["KICAD_CLI_OFFICIAL"]).resolve(),
    }
    native_hashes = {role: support.sha(path) for role, path in native_paths.items()}
    original = support.tree(scene.project)
    support.put(root / "FROZEN-ENVIRONMENT-MATRIX.json", matrix)
    support.put(
        root / "ENVIRONMENT-START.json",
        {
            "at": support.now(),
            "binding": scene.binding,
            "matrix_sha256": support.sha(matrix_file),
            "script_sha256": support.sha(__file__),
            "observer_sha256": support.sha(Path(__file__).with_name("process_tree.py")),
            "selected_ids": [c["id"] for c in cases],
            "project": original,
            "configuration": scene.config_hashes,
            "dependencies": dependencies,
            "native_executables": native_hashes,
        },
    )
    work = root / "owned environment cases"
    work.mkdir()
    results = []
    for case in cases:
        row = {"id": case["id"], "status": "failed"}
        area = work / case["id"]
        try:
            env, args, state, output = _prepare(scene, case, area, native_paths)
            before_output = _output_state(output)
            support.put(
                area / "PREPARED.json",
                {
                    "argv": args,
                    "environment": {
                        k: v for k, v in env.items() if k.startswith("KICAD") or k == "PATH"
                    },
                    "project": original,
                    "output": before_output,
                },
            )
            mechanism = case["preparation"]
            if mechanism == "exclusive-state":
                from .process_tree import observe
                from .public_contract import infrastructure, validate_document

                setup = scene.calls / (case["id"] + "-setup")
                trace = observe(
                    [support.BINARY, "operation", "list", "--compact"], root, env, setup
                )
                assert trace["complete_tree"] and trace["exit"] == 0
                assert all(
                    infrastructure(p, support.BINARY, os.environ["SystemRoot"])
                    for p in trace["processes"]
                )
                initialized = support.read(setup / "stdout")
                validate_document(initialized, {"$ref": PREFIX + "response:2.0"}, documents)
                validate_document(
                    initialized["data"], {"$ref": PREFIX + "query-page:2.0"}, documents
                )
                assert initialized["ok"] and initialized["data"]["items"] == []
                row["separate_public_setup"] = {
                    "trace_sha256": support.sha(setup / "TRACE.json"),
                    "exit": trace["exit"],
                }
            before_state = support.tree(state)
            manager = (
                denied_state(state, root, area)
                if mechanism == "deny-state"
                else exclusive_state(state / "operations.sqlite3", root, area)
                if mechanism == "exclusive-state"
                else contextlib.nullcontext(None)
            )
            with manager as control:
                row.update(
                    _call(scene, case, env, args, scene.calls / case["id"], native_paths, documents)
                )
                if mechanism == "deny-state":
                    assert support.tree(state) == before_state
                if not case["state_write_allowed"]:
                    assert support.tree(state) == before_state
            if mechanism == "exclusive-state":
                assert support.tree(state) == before_state, "A refused call changed the held ledger"
            if control:
                row["control"] = control
                assert control["restored" if mechanism == "deny-state" else "released"]
            assert _output_state(output) == before_output
            assert support.tree(scene.project) == original
            assert support.tree(scene.original) == scene.origin["files"]
            assert support.tree(root / "configuration") == scene.config_hashes
            assert {"sdk": support.tree(sdk), "spec": support.tree(spec)} == dependencies
            assert {role: support.sha(path) for role, path in native_paths.items()} == native_hashes
            row.update(
                status="passed",
                project_unchanged=True,
                output_unchanged=True,
                configuration_unchanged=True,
                dependencies_unchanged=True,
            )
        except BaseException as exc:
            row["failure"] = type(exc).__name__ + ": " + str(exc)
            row["retained_trace_bindings"] = {
                name: support.sha(scene.calls / name / "TRACE.json")
                for name in (case["id"], case["id"] + "-setup")
                if (scene.calls / name / "TRACE.json").is_file()
            }
            snapshots = {}
            for name, path in (
                ("project", scene.project),
                ("configuration", root / "configuration"),
                ("sdk", sdk),
                ("spec", spec),
                ("case_area", area),
            ):
                try:
                    snapshots[name] = {"files": support.tree(path)}
                except OSError as error:
                    snapshots[name] = {"read_error": type(error).__name__ + ": " + str(error)}
            support.put(root / "AFTER-FAILURE.json", snapshots)
            row["after_failure_sha256"] = support.sha(root / "AFTER-FAILURE.json")
            results.append(row)
            result = {
                "status": "failed",
                "binding": scene.binding,
                "cases": results,
                "remaining": [c["id"] for c in cases[len(results) :]],
                "finished_at": support.now(),
            }
            support.put(root / "ENVIRONMENT-RESULT.json", result)
            print(
                json.dumps({"id": case["id"], "status": "failed", "failure": row["failure"]}),
                flush=True,
            )
            return result
        support.put(area / "CHECK.json", row)
        results.append(row)
        print(json.dumps({"id": case["id"], "status": "passed"}), flush=True)
    result = {
        "status": "passed",
        "binding": scene.binding,
        "matrix_sha256": support.sha(matrix_file),
        "cases": results,
        "project_files_after": support.tree(scene.project),
        "finished_at": support.now(),
    }
    support.put(root / "ENVIRONMENT-RESULT.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Freeze an environment matrix; no processes or permission changes"
    )
    parser.add_argument("--reference", required=True)
    parser.add_argument("--exe", required=True)
    parser.add_argument("--build-source-commit", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    matrix = generate(
        support.read(args.reference),
        exe_sha256=support.sha(args.exe),
        build_source_commit=args.build_source_commit,
    )
    support.put(args.out, matrix)
    print(
        json.dumps(
            {
                "action": "frozen_plan_only",
                "cases": len(matrix["cases"]),
                "matrix_sha256": support.sha(args.out),
            }
        )
    )


if __name__ == "__main__":
    main()
