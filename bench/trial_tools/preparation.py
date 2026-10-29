"""Owned preparation and strict configuration guards for this finite trial.

Preparation uses sacrificial copies, the selected official KiCad consumers and
the packaged read-only Native reader. Preparation outputs are not acceptance
evidence. This module neither imports the product nor edits a formal project.
The private runner argument exists for harmless offline unit fixtures only; such
fixtures are explicitly recorded as synthetic and cannot establish Native use.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .identity import (
    IdentityError,
    capture,
    file_identity,
    inventory_diff,
    ordinary_path,
    tree_inventory,
    verify_aliases,
    verify_inventory,
)

SCHEMA = "finite-windows-trial-preparation/v1"
PRODUCT_SHA256 = "7ded617e0a06efba811bd50b2ea5ab6c12ee0b1bd879121920d1cdb0f95ceb98"
PROFILES = ("writer", "consumer")
RENDERER_PREPARATION_SCHEMA = "trial-required-renderer-preparation/v1"
PREPARATION_COUNTS = {"native": 12, "host": 4, "node": 4}
PROJECT_FILES = (
    "channel_a.kicad_sch",
    "channel_b.kicad_sch",
    "hier_dual_rc.kicad_sch",
    "hier_dual_rc.kicad_pcb",
    "hier_dual_rc.kicad_pro",
    "hier_dual_rc.kicad_prl",
    "hier_dual_rc.net",
)
_RESERVED_ENV = {
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "TEMP",
    "TMP",
    "TMPDIR",
    "KICAD_CONFIG_HOME",
    "KICAD_DOCUMENTS_HOME",
    "KICAD_CACHE_HOME",
    "KICAD_CLI_STATE_DIR",
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONUSERBASE",
    "PYTHONPYCACHEPREFIX",
    "KICAD_CLI_SCOPE_FILE",
    "KICAD_CLI_ROOT",
    "KICAD_CLI_PYTHON",
    "KICAD_CLI_OFFICIAL",
    "KICAD_CLI_DRIVER_SDK_PATH",
}


class TrialError(RuntimeError):
    """A non-retryable trial refusal; retain the work and use a new plan/root."""

    def __init__(self, code: str, message: str, *, details: Any = None):
        super().__init__(message)
        self.code = code
        self.details = details
        self.retryable = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _plain(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _put_new(path: Path, value: Any) -> None:
    ordinary_path(path.parent)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(_plain(value), stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _within(value: str | Path, root: Path, *, exists: bool = True) -> Path:
    path = ordinary_path(value, exists=exists)
    try:
        path.relative_to(root)
    except ValueError:
        raise TrialError(
            "E_TRIAL_PATH", "Path must remain inside the owned work root", details=str(path)
        ) from None
    return path


def _package_file(package: Path, value: str | Path) -> Path:
    path = Path(value)
    path = path if path.is_absolute() else package / path
    path = ordinary_path(path)
    if not path.is_relative_to(package) or not path.is_file():
        raise TrialError(
            "E_TRIAL_METHOD", "Method must be a packaged ordinary file", details=str(path)
        )
    return path


def _dependency_record(dependencies: Mapping[str, Any]) -> dict[str, Any]:
    required_files = ("official_cli", "native_python", "product_exe")
    if "host_python" in dependencies:
        required_files += ("host_python",)
    required_dirs = ("sdk_root", "spec_root")
    missing, paths = [], {}
    for name in (*required_files, *required_dirs):
        value = dependencies.get(name)
        try:
            path = ordinary_path(value) if value else None
            valid = path is not None and (
                path.is_file() if name in required_files else path.is_dir()
            )
        except (OSError, TypeError, IdentityError):
            valid, path = False, None
        if not valid:
            missing.append(
                {
                    "name": name,
                    "path": str(value),
                    "repair": (
                        "Supply an existing explicit external dependency path; "
                        "do not install or change global preferences."
                    ),
                }
            )
        else:
            paths[name] = str(path)
    for value in dependencies.get("dependency_files", ()):
        try:
            file_identity(value)
        except (OSError, TypeError, IdentityError) as error:
            missing.append(
                {
                    "name": "dependency_files",
                    "path": str(value),
                    "reason": str(error),
                    "repair": (
                        "Restore the declared finite dependency file, "
                        "then use a fresh owned work root."
                    ),
                }
            )
    if missing:
        raise TrialError(
            "E_TRIAL_DEPENDENCY",
            "Required dependency is missing or unsupported; no Native process started",
            details=missing,
        )
    if (
        paths["official_cli"] == paths["product_exe"]
        or file_identity(paths["official_cli"])["sha256"]
        == file_identity(paths["product_exe"])["sha256"]
    ):
        raise TrialError(
            "E_TRIAL_DEPENDENCY",
            "Select the official KiCad CLI separately from the product executable",
            details=[
                {
                    "name": name,
                    "path": paths[name],
                    "reason": "official_cli_matches_product_executable",
                    "repair": (
                        "Select the installed KiCad bin/kicad-cli.exe as official_cli and "
                        "the separately supplied product executable as product_exe."
                    ),
                }
                for name in ("official_cli", "product_exe")
            ],
        )
    root = ordinary_path(dependencies.get("kicad_root", Path(paths["official_cli"]).parent.parent))
    if Path(paths["official_cli"]).parent != Path(paths["native_python"]).parent:
        raise TrialError(
            "E_TRIAL_DEPENDENCY",
            "Official CLI and Native Python must use the same selected KiCad bin directory",
            details=[
                {
                    "name": name,
                    "path": paths[name],
                    "reason": "official_cli_and_native_python_bins_differ",
                    "repair": (
                        "Select official_cli and native_python from the same existing "
                        "KiCad installation bin directory."
                    ),
                }
                for name in ("official_cli", "native_python")
            ],
        )
    if Path(paths["sdk_root"]).parent != Path(paths["spec_root"]).parent:
        raise TrialError(
            "E_TRIAL_DEPENDENCY",
            "The driver SDK and pinned specification must be adjacent external directories",
            details=[
                {
                    "name": name,
                    "path": paths[name],
                    "reason": "sdk_and_spec_roots_are_not_adjacent",
                    "repair": (
                        "Select the existing pinned driver SDK and specification directories "
                        "that share one external parent directory."
                    ),
                }
                for name in ("sdk_root", "spec_root")
            ],
        )
    aliases = verify_aliases(dependencies.get("aliases", ()))
    finite_files = set(map(str, dependencies.get("dependency_files", ())))
    finite_files.update(paths[name] for name in required_files)
    files = {str(ordinary_path(path)): file_identity(path) for path in sorted(finite_files)}
    for path, expected in dependencies.get("expected_files", {}).items():
        canonical = str(ordinary_path(path))
        if canonical not in files:
            raise TrialError(
                "E_TRIAL_DEPENDENCY",
                "Pinned dependency was not declared in dependency_files",
                details=canonical,
            )
        if (
            not expected
            or set(expected) - {"sha256", "size"}
            or any(files[canonical][key] != value for key, value in expected.items())
        ):
            raise TrialError(
                "E_TRIAL_DEPENDENCY",
                "Current dependency differs from its prospective size/SHA pin; "
                "no Native process started",
                details={"path": canonical, "expected": expected, "actual": files[canonical]},
            )
    return {
        "paths": {**paths, "kicad_root": str(root)},
        "files": files,
        "trees": {name: tree_inventory(paths[name]) for name in required_dirs},
        "aliases": aliases,
        "alias_groups": _plain(dependencies.get("aliases", ())),
        "source_scope": (
            "Explicit finite files only; Host supervisor stdlib is not exhaustively "
            "source-authenticated. DLL path/base/image_size and disk hashes "
            "do not authenticate bytes at load time."
        ),
    }


def _validate_allowances(value: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in value:
        if (
            set(item) != {"profile", "path", "json_pointer", "kind"}
            or item["profile"] not in PROFILES
            or item["path"] != "10.0/kicad_common.json"
            or item["json_pointer"] != "/system/working_dir"
            or item["kind"] != "owned_work_path"
        ):
            raise TrialError(
                "E_TRIAL_CONFIG",
                "Unsupported metadata allowance; declare only the concrete "
                "working_dir migration before preparation",
                details=_plain(item),
            )
        if dict(item) in result:
            raise TrialError("E_TRIAL_CONFIG", "Duplicate metadata allowance")
        result.append(dict(item))
    return result


def _method_record(package: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    defaults = {
        "observer": "trial_tools/resources/native-observer/integration.py",
        "reader": "physical-footprint-eco/methods/saved_board_native_read.py",
        "baseline_inputs": "physical-footprint-eco/methods/requirements/BASELINE-INPUTS.json",
        "physical_intent": "physical-footprint-eco/methods/requirements/PHYSICAL-PIN-INTENT42.json",
    }
    paths = {
        name: _package_file(package, config.get(name, default))
        for name, default in defaults.items()
    }
    methods = {path for path in paths.values()}
    for name in (
        "observer02_support",
        "observer02_metadata",
        "observer02_owned_process",
        "observer02_win_debugging",
        "observer02_native_observation",
        "observer02_process_tree",
    ):
        methods.add(_package_file(package, paths["observer"].parent / (name + ".py")))
    methods.update({Path(__file__).absolute(), Path(__file__).with_name("identity.py").absolute()})
    methods.add(Path(__file__).with_name("reading.py").absolute())
    for name in ("render_pcb.cjs", "svg_reading.py", "pcb-qualification.json"):
        methods.add(_package_file(package, "trial_tools/resources/reading-tools/" + name))
    for value in config.get("method_files", ()):
        methods.add(_package_file(package, value))
    return {
        "paths": {key: str(value) for key, value in paths.items()},
        "files": {str(path): file_identity(path) for path in sorted(methods)},
    }


def _profile_paths(work: Path, name: str) -> dict[str, str]:
    root = work / "profiles" / name
    home = root / "home"
    return {
        "root": str(root),
        **{part: str(root / part) for part in ("config", "cache", "temp", "logs", "state")},
        "home": str(home),
        "roaming": str(home / "AppData" / "Roaming"),
        "local": str(home / "AppData" / "Local"),
        "documents": str(home / "Documents"),
        "bytecode_empty": str(root / "cache" / "bytecode-empty"),
    }


@dataclass
class ReadyContext:
    record: dict[str, Any]

    @property
    def work_root(self) -> Path:
        return Path(self.record["work_root"])

    @property
    def package_root(self) -> Path:
        return Path(self.record["package_root"])

    @property
    def preparation_project(self) -> Path:
        return self.work_root / "PREPARATION" / "project"

    def profile_root(self, profile: str) -> Path:
        return Path(self.record["profiles"][_profile(profile)]["root"])

    def python_flags(self, profile: str) -> list[str]:
        """Source-reading flags for selected Native/Host Python child commands."""
        prefix = self.record["profiles"][_profile(profile)]["bytecode_empty"]
        return ["-B", "-S", "-X", "pycache_prefix=" + prefix]

    def env(self, profile: str) -> dict[str, str]:
        paths = self.record["profiles"][_profile(profile)]
        dependencies = self.record["dependencies"]["paths"]
        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper() not in _RESERVED_ENV
            and not key.upper().startswith(("KICAD", "PYTHON", "XDG_"))
        }
        native_root = Path(dependencies["kicad_root"])
        env.update(
            {
                "HOME": paths["home"],
                "USERPROFILE": paths["home"],
                "APPDATA": paths["roaming"],
                "LOCALAPPDATA": paths["local"],
                "XDG_CONFIG_HOME": paths["config"],
                "XDG_CACHE_HOME": paths["cache"],
                "TEMP": paths["temp"],
                "TMP": paths["temp"],
                "TMPDIR": paths["temp"],
                "KICAD_CONFIG_HOME": paths["config"],
                "KICAD_DOCUMENTS_HOME": paths["documents"],
                "KICAD_CACHE_HOME": paths["cache"],
                "KICAD_CLI_STATE_DIR": paths["state"],
                "KICAD_CLI_SCOPE_FILE": str(self.work_root / "scope.json"),
                "KICAD_CLI_DRIVER_SDK_PATH": dependencies["sdk_root"],
                "KICAD_CLI_ROOT": str(native_root),
                "KICAD_CLI_PYTHON": dependencies["native_python"],
                "KICAD_CLI_OFFICIAL": dependencies["official_cli"],
                "KICAD10_SYMBOL_DIR": str(native_root / "share/kicad/symbols"),
                "KICAD10_FOOTPRINT_DIR": str(native_root / "share/kicad/footprints"),
                "KICAD10_3DMODEL_DIR": str(native_root / "share/kicad/3dmodels"),
                "PYTHONIOENCODING": "utf-8",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
                "PYTHONPYCACHEPREFIX": paths["bytecode_empty"],
            }
        )
        env.update(self.record.get("environment", {}))
        return env

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.record)

    def __getitem__(self, key: str) -> Any:
        return self.record[key]


@dataclass
class FrozenContext:
    ready: ReadyContext
    inputs: dict[str, Any]
    stopped: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "work_root": str(self.ready.work_root),
            "inputs": copy.deepcopy(self.inputs),
            "stopped": self.stopped,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> FrozenContext:
        if value.get("schema") != SCHEMA or value.get("stopped"):
            raise TrialError("E_TRIAL_CONTEXT", "Frozen context is incompatible or stopped")
        result = cls(load_ready(value["work_root"]), copy.deepcopy(value["inputs"]))
        verify_ready(result.ready, inputs=result.inputs)
        return result


def _profile(profile: str) -> str:
    if profile not in PROFILES:
        raise TrialError("E_TRIAL_PROFILE", "Profile must be writer or consumer")
    return profile


def _windows_argv(command: str) -> list[str]:
    """Parse a recorded command line with WinAPI; never execute that string."""
    import ctypes as C

    if os.name != "nt" or not isinstance(command, str) or not command or "\0" in command:
        raise ValueError("A recorded Windows command line is required")
    shell = C.WinDLL("shell32", use_last_error=True)
    kernel = C.WinDLL("kernel32", use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [C.c_wchar_p, C.POINTER(C.c_int)]
    shell.CommandLineToArgvW.restype = C.POINTER(C.c_wchar_p)
    kernel.LocalFree.argtypes = [C.c_void_p]
    kernel.LocalFree.restype = C.c_void_p
    count = C.c_int()
    pointer = shell.CommandLineToArgvW(command, C.byref(count))
    if not pointer:
        raise C.WinError(C.get_last_error())
    try:
        return [pointer[index] for index in range(count.value)]
    finally:
        kernel.LocalFree(C.cast(pointer, C.c_void_p))


def _helper_declarations(config: Mapping[str, Any], deps: Mapping[str, Any]) -> list[dict]:
    declarations = []
    for value in config.get("public_refusal_helpers", ()):
        if set(value) != {"path", "argv", "parent"}:
            raise TrialError("E_TRIAL_CONTRACT", "Public refusal helper declaration is incomplete")
        path = ordinary_path(value["path"])
        args = value["argv"]
        kind = path.name.casefold()
        system = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / path.name
        if (
            kind not in {"conhost.exe", "cmd.exe"}
            or str(path).casefold() != str(system).casefold()
            or str(path) not in deps["files"]
            or not isinstance(args, list)
            or any(not isinstance(arg, str) or "\0" in arg for arg in args)
            or not args
            or (kind == "conhost.exe" and (args[1:] != ["0x4"] or value["parent"] != "root"))
            or (kind == "cmd.exe" and (args[1:] != ["/c", "ver"] or value["parent"] != "worker"))
        ):
            raise TrialError(
                "E_TRIAL_CONTRACT", "Helper is outside the two finite optional system shapes"
            )
        image_arg = args[0]
        if kind == "conhost.exe":
            if not image_arg.startswith("\\??\\"):
                raise TrialError(
                    "E_TRIAL_CONTRACT", "Console helper requires its declared NT DOS prefix"
                )
            image_arg = image_arg[4:]
        if str(Path(image_arg)).casefold() != str(path).casefold():
            raise TrialError("E_TRIAL_CONTRACT", "Helper argv image differs from its declared file")
        if any(item["path"].casefold() == str(path).casefold() for item in declarations):
            raise TrialError("E_TRIAL_CONTRACT", "Duplicate optional helper declaration")
        declarations.append(
            {
                "path": str(path),
                "argv": args,
                "parent": value["parent"],
                "identity": deps["files"][str(path)],
                "max_count": 1,
                "exit": 0,
            }
        )
    return declarations


def _refusal_declaration(value: Mapping[str, Any] | None, exits: Sequence[int]) -> dict | None:
    if value is None:
        if tuple(exits) != (0,):
            raise TrialError(
                "E_TRIAL_CONTRACT",
                "A nonzero expected exit requires the finite public refusal declaration",
            )
        return None
    if (
        set(value) != {"code", "exit_code", "reason", "operation_id"}
        or value["code"] != "E_CONFLICT"
        or value["exit_code"] != 6
        or value["reason"] != "token_used"
        or tuple(exits) != (6,)
        or not isinstance(value["operation_id"], str)
        or not value["operation_id"]
    ):
        raise TrialError(
            "E_TRIAL_CONTRACT",
            "Only the predeclared known-success sync token_used refusal is supported",
        )
    return dict(value)


def _exact_public_sdk_refusal(generic, trace, expected_argv, *, ready, declaration):
    """Permit checking one exact ordinary SDK refusal, preserving generic evidence.

    This is a transport policy, not JSON error/receipt verification. Its output
    remains functional_output_verified=false until the separate ECO checks pass.
    """
    policy = {
        "name": "exact-7ded-project-sync-token-used-sdk-refusal/v1",
        "generic_status": generic.get("status"),
        "applied": True,
        "admitted": False,
        "functional_output_verified": False,
        "declaration": declaration,
        "optional_helpers": ready.record["public_refusal_helpers"],
        "issues": [],
    }
    issues = []
    if (
        generic.get("status") != "unexpected_exit"
        or not generic.get("observation_complete")
        or generic.get("issues")
        or generic.get("second_chance_notifications")
        or generic.get("fail_fast_status_notifications")
        or generic.get("functional_output_verified") is not False
        or generic.get("may_validate_functional_output") is not False
        or generic.get("stop_required") is not True
        or trace.get("observation_schema") != 4
    ):
        issues.append("generic_observation_not_eligible")
        policy["issues"] = issues
        return generic, policy
    product = ready.record["dependencies"]["paths"]["product_exe"]
    if ready.record["dependencies"]["files"][product]["sha256"] != PRODUCT_SHA256:
        issues.append("artifact_is_not_the_exact_7ded_candidate")
    expected = list(map(str, expected_argv))
    flags = {arg.split("=", 1)[0] for arg in expected}
    if (
        not expected
        or expected[0].casefold() != product.casefold()
        or expected[1:3] != ["project", "sync"]
        or "--confirm" not in flags
        or flags & {"--dry-run", "--request-id", "--idempotency-key"}
    ):
        issues.append("invocation_is_not_the_frozen_no_key_sync_confirmation")
    processes = trace.get("processes", [])
    roots = [
        row
        for row in processes
        if row.get("process_instance_id") == trace.get("root_process_instance_id")
    ]
    root = roots[0] if len(roots) == 1 else {}
    if len(roots) != 1:
        issues.append("unique_root_required")
    same = [
        row
        for row in processes
        if row is not root
        and (
            str(row.get("image", "")).casefold() == product.casefold()
            or row.get("image_sha256") == PRODUCT_SHA256
        )
    ]
    worker = same[0] if len(same) == 1 else {}
    if len(same) != 1:
        issues.append("one_exact_product_worker_required")
    for role, row in (("root", root), ("worker", worker)):
        try:
            actual = _windows_argv(row["metadata"]["CommandLine"])
            if (
                row.get("image_sha256") != PRODUCT_SHA256
                or str(row.get("image", "")).casefold() != product.casefold()
                or row.get("identity_status") != "bound"
                or type(row.get("exit")) is not int
                or row["exit"] != 6
                or not actual
                or actual[0].casefold() != product.casefold()
                or actual[1:] != expected[1:]
            ):
                issues.append(role + "_image_exit_or_argv_differs")
        except (KeyError, OSError, TypeError, ValueError):
            issues.append(role + "_metadata_incomplete")
    if (
        type(trace.get("exit")) is not int
        or trace.get("exit") != 6
        or worker.get("metadata", {}).get("ParentProcessId") != root.get("pid")
        or worker.get("parent_process_instance_id") != root.get("process_instance_id")
        or worker.get("parent_binding") != "observed-parent"
        or type(root.get("creation_filetime")) is not int
        or type(worker.get("creation_filetime")) is not int
        or root.get("creation_filetime", 0) <= 0
        or worker.get("creation_filetime", -1) < root.get("creation_filetime", 0)
    ):
        issues.append("worker_exit_parent_or_birth_not_bound")
    helper_counts = {}
    for row in processes:
        if row is root or row is worker:
            continue
        matches = [
            item
            for item in ready.record["public_refusal_helpers"]
            if item["path"].casefold() == str(row.get("image", "")).casefold()
        ]
        if len(matches) != 1:
            issues.append("undeclared_extra_child")
            continue
        item = matches[0]
        helper_counts[item["path"]] = helper_counts.get(item["path"], 0) + 1
        parent = root if item["parent"] == "root" else worker
        try:
            actual = _windows_argv(row["metadata"]["CommandLine"])
            if (
                not actual
                or actual[0].casefold() != item["argv"][0].casefold()
                or actual[1:] != item["argv"][1:]
                or row.get("image_sha256") != item["identity"]["sha256"]
                or type(row.get("exit")) is not int
                or row["exit"] != 0
                or row.get("identity_status") != "bound"
                or row.get("metadata", {}).get("ParentProcessId") != parent.get("pid")
                or row.get("parent_process_instance_id") != parent.get("process_instance_id")
                or row.get("parent_binding") != "observed-parent"
                or type(row.get("creation_filetime")) is not int
                or row.get("creation_filetime", -1) < parent.get("creation_filetime", 0)
            ):
                issues.append("declared_helper_identity_argv_exit_or_birth_differs")
        except (KeyError, OSError, TypeError, ValueError):
            issues.append("helper_metadata_incomplete")
        if helper_counts[item["path"]] > 1:
            issues.append("repeated_optional_helper")
    final = trace.get("job_finalization", {})
    if (
        trace.get("timed_out")
        or trace.get("job_active_before_close") != 0
        or not final.get("natural_tree_complete")
        or final.get("controlled_termination")
        or final.get("control_events")
    ):
        issues.append("whole_job_did_not_end_naturally")
    policy["issues"] = sorted(set(issues))
    if issues:
        return generic, policy
    policy.update(
        admitted=True,
        root_instance=root["process_instance_id"],
        worker_instance=worker["process_instance_id"],
        expected_sdk_exit=6,
    )
    return dict(
        generic,
        status="exact_public_sdk_refusal_transport",
        may_validate_functional_output=True,
        stop_required=False,
        functional_output_verified=False,
    ), policy


def _observation_runner(
    argv, *, cwd, env, out, timeout, ready, expected_exit_codes=(0,), expected_public_refusal=None
):
    """The sole real-process launcher; all children use the packaged observer."""
    path = Path(ready.record["methods"]["paths"]["observer"])
    raw = path.read_bytes()
    namespace = {"__file__": str(path), "__name__": "_finite_trial_observer_integration"}
    exec(compile(raw, str(path), "exec"), namespace)
    observe, inspect = namespace["observer"]()
    trace = observe(list(map(str, argv)), str(cwd), env, str(out), timeout=timeout)
    trace_before = (Path(out) / "TRACE.json").read_bytes()
    classification = inspect(out, allowed_exit_codes=expected_exit_codes)
    trace_after = (Path(out) / "TRACE.json").read_bytes()
    if trace_before != trace_after or json.loads(trace_after) != trace:
        classification = dict(
            classification,
            status="observation_incomplete",
            observation_complete=False,
            may_validate_functional_output=False,
            stop_required=True,
            functional_output_verified=False,
            issues=[*classification.get("issues", []), "trace_binding_changed"],
        )
    generic = copy.deepcopy(classification)
    _put_new(Path(out) / "GENERIC-CLASSIFICATION.json", generic)
    adapter = None
    if expected_public_refusal is not None:
        classification, adapter = _exact_public_sdk_refusal(
            generic, trace, argv, ready=ready, declaration=expected_public_refusal
        )
        _put_new(Path(out) / "PUBLIC-REFUSAL-ADAPTER.json", adapter)
    trace_identity = file_identity(Path(out) / "TRACE.json")
    stdout_identity = file_identity(Path(out) / "stdout")
    if trace_identity["sha256"] != hashlib.sha256(trace_after).hexdigest() or stdout_identity[
        "sha256"
    ] != trace.get("stdout_sha256"):
        classification = dict(
            classification,
            status="observation_incomplete",
            observation_complete=False,
            may_validate_functional_output=False,
            stop_required=True,
            functional_output_verified=False,
            issues=[*classification.get("issues", []), "verified_trace_or_stdout_changed"],
        )
    _put_new(Path(out) / "CLASSIFICATION.json", classification)
    return {
        "trace": trace,
        "classification": classification,
        "generic_classification": generic,
        "public_refusal_adapter": adapter,
        "synthetic": False,
        "trace_sha256": trace_identity["sha256"],
        "stdout_sha256": stdout_identity["sha256"],
    }


def _run(
    ready: ReadyContext,
    argv,
    *,
    profile,
    cwd,
    out,
    timeout,
    expected_outputs,
    runner,
    expected_exit_codes=(0,),
    expected_public_refusal=None,
):
    if timeout <= 0:
        raise TrialError("E_TRIAL_USAGE", "Timeout must be positive")
    executable = ordinary_path(argv[0])
    if str(executable) not in ready.record["dependencies"]["files"]:
        raise TrialError(
            "E_TRIAL_DEPENDENCY",
            "Executable must be explicitly frozen in dependency_files",
            details=str(executable),
        )
    if (
        str(executable) == ready.record["dependencies"]["paths"]["native_python"]
        or str(executable) == ready.record["dependencies"]["paths"].get("host_python")
        or executable.name.casefold() in {"python.exe", "pythonw.exe", "python3.exe"}
    ):
        arguments = list(map(str, argv))
        if (
            arguments[1:5] != ready.python_flags(profile)
            or len(arguments) < 6
            or (arguments[5].startswith("-") and arguments[5] not in {"-c", "-m"})
        ):
            raise TrialError(
                "E_TRIAL_BYTECODE",
                "Native/Host Python commands require the owned -B -S -X pycache_prefix flags "
                "followed directly by the selected script, -c or -m",
                details={"required_flags": ready.python_flags(profile)},
            )
    cwd = _within(cwd, ready.work_root)
    if not cwd.is_dir():
        raise TrialError("E_TRIAL_PATH", "Working directory must exist")
    out = _within(out, ready.work_root, exists=False)
    if not out.is_relative_to(ready.work_root / "logs") or out.exists():
        raise TrialError("E_TRIAL_PATH", "Observer output must be a new directory under owned logs")
    output_paths = [_within(path, ready.work_root, exists=False) for path in expected_outputs]
    if any(path.exists() for path in output_paths):
        raise TrialError(
            "E_TRIAL_OUTPUT", "Expected consumer outputs must be new paths, never stale evidence"
        )
    _verify_bytecode_prefixes(ready)
    keywords = dict(cwd=cwd, env=ready.env(profile), out=out, timeout=timeout, ready=ready)
    try:
        if runner is None:
            result = _observation_runner(
                list(map(str, argv)),
                **keywords,
                expected_exit_codes=expected_exit_codes,
                expected_public_refusal=expected_public_refusal,
            )
        else:
            result = runner(list(map(str, argv)), **keywords)
    except BaseException as error:
        try:
            error.bytecode_prefix_observation = _verify_bytecode_prefixes(ready)
        except BaseException as bytecode_error:
            error.bytecode_prefix_observation = {
                "status": "INCOMPLETE",
                "error": _error_record(bytecode_error),
            }
        raise
    bytecode_error = None
    try:
        bytecode_observation = _verify_bytecode_prefixes(ready)
    except BaseException as error:
        bytecode_error = error
        bytecode_observation = {"status": "REJECTED", "error": _error_record(error)}
    if not isinstance(result, dict):
        raise TrialError(
            "E_TRIAL_OBSERVATION",
            "Runner did not return an observation",
            details={"bytecode_prefix_observation": bytecode_observation},
        )
    if runner is not None:
        result["synthetic"] = True
    result["expected_exit_codes"] = list(expected_exit_codes)
    result["bytecode_prefix_observation"] = bytecode_observation
    classification = result.get("classification", {})
    trace = result.get("trace", {})
    finalization = trace.get("job_finalization", {})
    if (
        not classification.get("observation_complete")
        or not classification.get("may_validate_functional_output")
        or classification.get("stop_required")
        or trace.get("timed_out")
        or trace.get("job_active_before_close") != 0
        or not finalization.get("natural_tree_complete")
        or finalization.get("controlled_termination")
        or finalization.get("control_events")
    ):
        raise TrialError(
            "E_TRIAL_NATIVE_STOP",
            "Native observation failed, timed out or lacks natural complete-Job termination; "
            "do not retry",
            details=result,
        )
    if bytecode_error is not None:
        raise bytecode_error
    missing = [
        str(path)
        for path in output_paths
        if not path.exists()
        or (path.is_file() and path.stat().st_size == 0)
        or (path.is_dir() and not any(path.iterdir()))
    ]
    if missing:
        raise TrialError(
            "E_TRIAL_OUTPUT", "Preparation/consumer output is missing or empty", details=missing
        )
    result["observer_directory"] = str(out)
    result["output_inventory"] = {str(path): capture(path) for path in output_paths}
    return result


def _initialization_commands(ready: ReadyContext, profile: str):
    paths = ready.record["dependencies"]["paths"]
    methods = ready.record["methods"]["paths"]
    project = ready.preparation_project
    sch, pcb = project / "hier_dual_rc.kicad_sch", project / "hier_dual_rc.kicad_pcb"
    output = ready.work_root / "output" / "preparation" / profile
    official = paths["official_cli"]
    yield (
        "sch-svg",
        [official, "sch", "export", "svg", "--output", str(output / "sch-svg") + os.sep, str(sch)],
        [output / "sch-svg"],
    )
    yield (
        "sch-xml",
        [
            official,
            "sch",
            "export",
            "netlist",
            "--format",
            "kicadxml",
            "--output",
            str(output / "netlist.xml"),
            str(sch),
        ],
        [output / "netlist.xml"],
    )
    yield (
        "sch-erc",
        [
            official,
            "sch",
            "erc",
            "--format",
            "json",
            "--output",
            str(output / "erc.json"),
            str(sch),
        ],
        [output / "erc.json"],
    )
    yield (
        "pcb-drc-parity",
        [
            official,
            "pcb",
            "drc",
            "--schematic-parity",
            "--format",
            "json",
            "--output",
            str(output / "drc.json"),
            str(pcb),
        ],
        [output / "drc.json"],
    )
    yield (
        "pcb-svg",
        [
            official,
            "pcb",
            "export",
            "svg",
            "--layers",
            "F.Cu,B.Cu,F.SilkS,B.SilkS,Edge.Cuts",
            "--mode-single",
            "--page-size-mode",
            "1",
            "--exclude-drawing-sheet",
            "--output",
            str(output / "pcb.svg"),
            str(pcb),
        ],
        [output / "pcb.svg"],
    )
    yield (
        "native-loadboard",
        [
            paths["native_python"],
            *ready.python_flags(profile),
            methods["reader"],
            str(pcb),
            methods["baseline_inputs"],
            methods["physical_intent"],
        ],
        [],
    )


def _verify_dependencies(record: Mapping[str, Any]) -> None:
    for path, expected in record["files"].items():
        actual = file_identity(path)
        if expected != actual:
            raise TrialError(
                "E_TRIAL_DEPENDENCY_DRIFT",
                "Declared dependency identity or bytes changed",
                details={"path": path, "before": expected, "after": actual},
            )
    for name, expected in record["trees"].items():
        try:
            verify_inventory(expected)
        except IdentityError as error:
            raise TrialError(
                "E_TRIAL_DEPENDENCY_DRIFT",
                "Finite SDK/specification tree membership or identity changed",
                details={"name": name, "differences": error.details},
            ) from error
    actual_aliases = verify_aliases(record["alias_groups"])
    if actual_aliases != record["aliases"]:
        raise TrialError(
            "E_TRIAL_ALIAS_DRIFT", "Current alias identity changed", details=actual_aliases
        )


def _verify_bytecode_prefixes(ready: ReadyContext) -> dict[str, Any]:
    """Require the same registered empty prefix directories, never clean them."""
    expected = ready.record.get("bytecode_prefixes")
    if not isinstance(expected, dict) or set(expected) != set(PROFILES):
        raise TrialError(
            "E_TRIAL_BYTECODE",
            "Preparation lacks the required empty bytecode-prefix guard; use a new owned root/plan",
        )
    observed = {}
    violations = {}
    for profile in PROFILES:
        baseline = expected[profile]
        try:
            actual = tree_inventory(ready.record["profiles"][profile]["bytecode_empty"])
            observed[profile] = actual
            diff = inventory_diff(baseline, actual)
            if (
                baseline["files"]
                or set(baseline["directories"]) != {"."}
                or actual["files"]
                or set(actual["directories"]) != {"."}
                or diff
            ):
                violations[profile] = {
                    "status": "REJECTED",
                    "differences": diff,
                    "inventory": actual,
                }
        except BaseException as error:
            violations[profile] = {"status": "INCOMPLETE", "error": _error_record(error)}
    if violations:
        raise TrialError(
            "E_TRIAL_BYTECODE",
            "Owned bytecode prefix is no longer the registered empty ordinary directory; "
            "preserve it and stop without clearing or retrying",
            details=violations,
        )
    return {"status": "EMPTY", "profiles": observed, "native_calls": 0}


def _verify_methods(record: Mapping[str, Any]) -> None:
    for path, expected in record["files"].items():
        if file_identity(path) != expected:
            raise TrialError("E_TRIAL_METHOD_DRIFT", "Frozen packaged method changed", details=path)


def _metadata_document(root: Path, relative: str) -> Any:
    def unique_object(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise TrialError("E_TRIAL_CONFIG", "Duplicate JSON key in configuration metadata")
            result[key] = item
        return result

    value = json.loads(
        (root / relative).read_text(encoding="utf-8"), object_pairs_hook=unique_object
    )
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("system"), dict)
        or not isinstance(value["system"].get("working_dir"), str)
    ):
        raise TrialError(
            "E_TRIAL_CONFIG",
            "Declared working_dir field is missing or malformed",
            details=str(root / relative),
        )
    return value


def _json_members(raw: str, start: int):
    """Yield object member value spans, preserving every other source character."""
    decoder = json.JSONDecoder()

    def whitespace(index):
        while index < len(raw) and raw[index] in " \t\r\n":
            index += 1
        return index

    index = whitespace(start)
    if raw[index] != "{":
        raise TrialError("E_TRIAL_CONFIG", "Expected a JSON object for metadata projection")
    index = whitespace(index + 1)
    while raw[index] != "}":
        key, index = decoder.raw_decode(raw, index)
        index = whitespace(index)
        if not isinstance(key, str) or raw[index] != ":":
            raise TrialError("E_TRIAL_CONFIG", "Malformed metadata member")
        begin = whitespace(index + 1)
        value, end = decoder.raw_decode(raw, begin)
        yield key, begin, end, value
        index = whitespace(end)
        if raw[index] == "}":
            break
        if raw[index] != ",":
            raise TrialError("E_TRIAL_CONFIG", "Malformed metadata delimiter")
        index = whitespace(index + 1)


def _working_dir_projection(raw: str) -> str:
    """Only replace the concrete /system/working_dir JSON string value token."""
    for key, begin, _, value in _json_members(raw, 0):
        if key == "system" and isinstance(value, dict):
            for inner_key, inner_begin, inner_end, inner_value in _json_members(raw, begin):
                if inner_key == "working_dir" and isinstance(inner_value, str):
                    return (
                        raw[:inner_begin] + '"<predeclared-owned-work-directory>"' + raw[inner_end:]
                    )
    raise TrialError("E_TRIAL_CONFIG", "Declared working_dir metadata token is missing")


def _owned_metadata_path(value: str, work: Path) -> bool:
    if not value or not Path(value).is_absolute():
        return False
    try:
        return ordinary_path(value).is_relative_to(work)
    except (OSError, IdentityError):
        return False


def _verify_config(ready: ReadyContext, profile: str) -> list[dict[str, Any]]:
    expected = ready.record["configuration"][profile]
    actual = tree_inventory(expected["root"])
    diff = inventory_diff(expected, actual)
    allowed = []
    for item in ready.record["metadata_allowances"]:
        if item["profile"] != profile or item["path"] not in diff.get("changed", {}):
            continue
        relative = item["path"]
        before, after = expected["files"][relative], actual["files"][relative]
        if any(before[key] != after[key] for key in ("dev", "file_id")):
            continue
        original = copy.deepcopy(ready.record["metadata_baseline"][profile][relative])
        current = _metadata_document(Path(expected["root"]), relative)
        raw = (Path(expected["root"]) / relative).read_bytes().decode("utf-8")
        before_value, after_value = (
            original["system"]["working_dir"],
            current["system"]["working_dir"],
        )
        if not _owned_metadata_path(after_value, ready.work_root):
            continue
        if (
            _working_dir_projection(raw)
            != ready.record["metadata_text_baseline"][profile][relative]
        ):
            continue
        original["system"]["working_dir"] = current["system"]["working_dir"] = (
            "<predeclared-owned-work-directory>"
        )
        if original != current:
            continue
        allowed.append(
            {
                "profile": profile,
                "path": relative,
                "json_pointer": item["json_pointer"],
                "before_value": before_value,
                "after_value": after_value,
                "before_identity": before,
                "after_identity": after,
            }
        )
        del diff["changed"][relative]
        if not diff["changed"]:
            del diff["changed"]
    if diff:
        raise TrialError(
            "E_TRIAL_CONFIG_DRIFT",
            "Complete configuration inventory changed",
            details={"profile": profile, "differences": diff},
        )
    for name, inventory in ready.record["profile_configuration"][profile].items():
        try:
            verify_inventory(inventory)
        except IdentityError as error:
            raise TrialError(
                "E_TRIAL_CONFIG_DRIFT",
                "Complete profile configuration inventory changed",
                details={"profile": profile, "domain": name, "differences": error.details},
            ) from error
    return allowed


def _verify_preparation_consumer(name: str, result: Mapping[str, Any]) -> None:
    """Check that each initialization actually reached its selected consumer."""
    import xml.etree.ElementTree as ET

    outputs = result["output_inventory"]
    if name in {"sch-svg", "pcb-svg"}:
        if len(outputs) != 1:
            raise TrialError("E_TRIAL_OUTPUT", "SVG initialization requires one declared output")
        filename, inventory = next(iter(outputs.items()))
        target = Path(filename)
        if name == "sch-svg":
            if (
                inventory.get("kind") != "tree"
                or inventory.get("root") != str(target)
                or not target.is_dir()
            ):
                raise TrialError("E_TRIAL_OUTPUT", "SCH SVG initialization requires a directory")
            svgs = [path for path in target.rglob("*.svg") if path.is_file()]
        else:
            if (
                inventory.get("kind") != "file"
                or inventory.get("path") != str(target)
                or target.suffix.lower() != ".svg"
                or not target.is_file()
            ):
                raise TrialError("E_TRIAL_OUTPUT", "PCB SVG initialization requires one .svg file")
            svgs = [target]
        if not svgs or any(
            ET.parse(path).getroot().tag != "{http://www.w3.org/2000/svg}svg" for path in svgs
        ):
            raise TrialError(
                "E_TRIAL_OUTPUT", "SVG initialization produced no readable SVG consumer output"
            )
    elif name == "sch-xml":
        path = Path(next(iter(outputs)))
        if ET.parse(path).getroot().tag != "export":
            raise TrialError("E_TRIAL_OUTPUT", "Netlist initialization did not produce KiCad XML")
    elif name in {"sch-erc", "pcb-drc-parity"}:
        path = Path(next(iter(outputs)))
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or not document.get("$schema"):
            raise TrialError("E_TRIAL_OUTPUT", "Rule check did not produce schema-bearing JSON")
    elif name == "native-loadboard":
        path = Path(result["observer_directory"]) / "stdout"
        document = json.loads(path.read_text(encoding="utf-8"))
        if (
            document.get("schema") != "physical-footprint-eco-native-read/v1"
            or document.get("native_version") != "10.0.6"
            or document.get("input_unchanged") is not True
            or document.get("project_unchanged") is not True
        ):
            raise TrialError(
                "E_TRIAL_OUTPUT", "Native reader did not establish read-only LoadBoard"
            )


def _renderer_request(package: Path, config: Mapping[str, Any], deps: Mapping[str, Any]) -> dict:
    """Validate the mandatory current renderer declaration before any consumer."""
    from .reading import _warmup_runtime

    if config.get("renderer_warmup") is not True or not isinstance(
        config.get("renderer_runtime"), dict
    ):
        raise TrialError("E_TRIAL_CONFIG", "Qualified renderer warmup is mandatory before READY")
    if "host_python" not in deps["paths"]:
        raise TrialError(
            "E_TRIAL_DEPENDENCY",
            "Renderer warmup requires the declared Host Python",
            details=[
                {
                    "name": "host_python",
                    "path": "<not declared>",
                    "reason": "mandatory_renderer_host_missing",
                    "repair": "Declare the existing Host Python and qualified renderer "
                    "before preparation.",
                }
            ],
        )
    declared = config["renderer_runtime"]
    sharp_entry = declared.get("sharp_entry")
    try:
        sharp_canonical = str(ordinary_path(sharp_entry))
    except (OSError, TypeError, IdentityError):
        sharp_canonical = None
    if sharp_canonical not in deps["files"] or sharp_canonical not in {
        str(Path(row["path"]).absolute()) for row in declared.get("runtime_files", [])
    }:
        raise TrialError(
            "E_TRIAL_DEPENDENCY",
            "Renderer sharp entry is not prospectively frozen",
            details=[
                {
                    "name": "sharp_entry",
                    "path": str(sharp_entry),
                    "reason": "renderer_entry_not_bound",
                    "repair": "Declare the exact qualified sharp entry in the renderer runtime "
                    "before preparation.",
                }
            ],
        )
    for row in [
        declared.get("node", {}),
        *declared.get("runtime_files", []),
        *declared.get("system_files", []),
    ]:
        path = row.get("path")
        frozen = deps["files"].get(path)
        if not frozen or any(frozen.get(key) != row.get(key) for key in ("sha256", "size")):
            raise TrialError(
                "E_TRIAL_DEPENDENCY",
                "Renderer provider was not prospectively frozen",
                details=[
                    {
                        "name": "renderer_provider",
                        "path": str(path),
                        "reason": "renderer_provider_not_bound",
                        "repair": "Declare the exact current renderer files and pins "
                        "before any consumer starts.",
                    }
                ],
            )
    # Current source/provider qualification uses only finite file reads. It does
    # not launch Node or count historical qualification as this preparation.
    current = _warmup_runtime(
        ReadyContext(
            {
                "package_root": str(package),
                "request": {
                    "config": _plain(config),
                    "dependencies": {"aliases": deps["alias_groups"]},
                },
                "dependencies": deps,
            }
        )
    )
    if current != declared:
        raise TrialError(
            "E_TRIAL_CONFIG", "Renderer declaration differs from current qualification"
        )
    return copy.deepcopy(current)


def _preparation_bindings(ready: ReadyContext) -> None:
    _verify_dependencies(ready.record["dependencies"])
    _verify_methods(ready.record["methods"])
    verify_inventory(ready.record["source_inventory"])
    verify_inventory(ready.record["preparation_input"])
    _verify_bytecode_prefixes(ready)
    plan = ready.record.get("preparation_plan")
    if plan and file_identity(plan["path"]) != plan["identity"]:
        raise TrialError("E_TRIAL_CONTEXT", "Prospective preparation plan changed")
    for invocation in ready.record["preparation_calls"]:
        for expected in invocation["result"]["output_inventory"].values():
            verify_inventory(expected)


def _preparation_step(ready, profile, task, role, config, runner):
    _preparation_bindings(ready)
    name = task["name"]
    invocation = {
        "name": name,
        "profile": profile,
        "role": role,
        "argv": list(map(str, task["argv"])),
        "expected_outputs": list(map(str, task["expected_outputs"])),
        "cwd": str(ready.preparation_project),
        "started_at": _now(),
        "preparation_only": True,
        "task": _plain(task),
    }
    generated_inputs = {
        key: capture(task[key]["path"])
        for key in ("plan", "declaration")
        if isinstance(task.get(key), Mapping) and "path" in task[key]
    }
    invocation["generated_input_bindings"] = generated_inputs
    _put_new(ready.work_root / "state" / f"preparation-{profile}-{name}.start.json", invocation)
    ready.record["preparation_call_counts"][role]["attempted"] += 1
    result = _run(
        ready,
        task["argv"],
        profile=profile,
        cwd=ready.preparation_project,
        out=ready.work_root / "logs/preparation" / profile / name,
        timeout=float(config.get("timeout", 120)),
        expected_outputs=task["expected_outputs"],
        runner=runner,
    )
    if role == "native":
        _verify_preparation_consumer(name, result)
    for expected in generated_inputs.values():
        verify_inventory(expected)
    _preparation_bindings(ready)
    invocation.update(finished_at=_now(), result=result)
    ready.record["preparation_calls"].append(invocation)
    ready.record["preparation_call_counts"][role]["completed"] += 1
    _put_new(ready.work_root / "state" / f"preparation-{profile}-{name}.finish.json", invocation)
    return result


def _configuration_snapshot(ready: ReadyContext) -> dict:
    return {
        "configuration": {
            name: tree_inventory(ready.record["profiles"][name]["config"]) for name in PROFILES
        },
        "profile_configuration": {
            name: {
                domain: tree_inventory(ready.record["profiles"][name][domain])
                for domain in ("home", "roaming", "local", "cache")
            }
            for name in PROFILES
        },
    }


def _verify_renderer_baseline(baseline: Mapping[str, Any]) -> None:
    differences = {}
    for group, profiles in baseline.items():
        for profile, value in profiles.items():
            rows = {"config": value} if group == "configuration" else value
            for domain, expected in rows.items():
                try:
                    verify_inventory(expected)
                except BaseException as error:
                    differences[profile + "/" + domain] = _error_record(error)
    if differences:
        raise TrialError(
            "E_TRIAL_RENDERER_STABILITY",
            "Renderer repeat changed the complete predeclared configuration/HOME/cache "
            "inventory; stop without refreezing",
            details=differences,
        )


def _verify_renderer_ready(ready: ReadyContext) -> None:
    qualification = ready.record.get("renderer_preparation", {})
    if (
        qualification.get("schema") != RENDERER_PREPARATION_SCHEMA
        or qualification.get("status") != "passed_preparation_only"
        or qualification.get("required_counts") != PREPARATION_COUNTS
        or set(qualification.get("initial", {})) != set(PROFILES)
        or set(qualification.get("stability", {})) != set(PROFILES)
    ):
        raise TrialError(
            "E_TRIAL_INCOMPLETE",
            "READY lacks mandatory actual renderer warmup/stability qualification; "
            "use a new owned root and plan",
        )
    counts = ready.record.get("preparation_call_counts", {})
    if any(
        counts.get(role) != {"attempted": count, "completed": count}
        for role, count in PREPARATION_COUNTS.items()
    ):
        raise TrialError(
            "E_TRIAL_INCOMPLETE", "Required Native/Host/Node preparation counts differ"
        )
    baseline = qualification["configuration_baseline"]
    if any(
        ready.record[group] != baseline[group]
        for group in ("configuration", "profile_configuration")
    ):
        raise TrialError(
            "E_TRIAL_CONTEXT", "READY replaced the pre-stability configuration baseline"
        )
    baseline_record = qualification["baseline_record"]
    if file_identity(baseline_record["path"]) != baseline_record["identity"]:
        raise TrialError("E_TRIAL_CONTEXT", "Original renderer stability baseline record changed")
    plan = ready.record["preparation_plan"]
    if file_identity(plan["path"]) != plan["identity"]:
        raise TrialError("E_TRIAL_CONTEXT", "Prospective preparation plan record changed")
    for phase in ("initial", "stability"):
        for profile in PROFILES:
            report = qualification[phase][profile]
            artifacts = {name: report[name] for name in ("plan", "declaration", "result", "image")}
            artifacts.update(
                {
                    "geometry_check_" + str(index): value
                    for index, value in enumerate(report["geometry_checks"])
                }
            )
            for name, expected in artifacts.items():
                actual = file_identity(expected["path"])
                if any(actual[key] != expected[key] for key in ("sha256", "size")):
                    raise TrialError(
                        "E_TRIAL_CONTEXT",
                        "Qualified renderer artifact changed",
                        details={
                            "phase": phase,
                            "profile": profile,
                            "artifact": name,
                            "path": expected["path"],
                        },
                    )
    for invocation in ready.record["preparation_calls"]:
        for expected in invocation.get("generated_input_bindings", {}).values():
            verify_inventory(expected)
        for expected in invocation["result"]["output_inventory"].values():
            verify_inventory(expected)


def prepare(
    package_root: str | Path,
    work_root: str | Path,
    dependencies: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
    *,
    _runner=None,
) -> ReadyContext:
    """Prepare a new owned root once. Any incomplete root is never resumed."""
    config = dict(config or {})
    config.setdefault("renderer_warmup", True)
    package = ordinary_path(package_root)
    work = ordinary_path(work_root, exists=False)
    if not package.is_dir() or work == package or package.is_relative_to(work):
        raise TrialError("E_TRIAL_PATH", "Select a separate new owned work directory")
    request = {
        "package_root": str(package),
        "dependencies": _plain(dependencies),
        "config": _plain(config),
    }
    if work.exists():
        if not (work / "OWNERSHIP.json").is_file():
            raise TrialError(
                "E_TRIAL_OWNERSHIP",
                "Existing unregistered directory is refused; nothing was overwritten",
            )
        if not (work / "READY.json").is_file():
            raise TrialError(
                "E_TRIAL_INCOMPLETE",
                "Preparation is incomplete; retain it and choose a new directory and plan",
            )
        ready = load_ready(work)
        if ready.record["request"] != request:
            raise TrialError(
                "E_TRIAL_CONFLICT",
                "Existing READY belongs to a different dependency/configuration plan",
            )
        verify_ready(ready)
        return ready
    # Every dependency, alias, packaged method and seed is checked before mkdir
    # or any Native call. Missing files include a concrete repair in the error.
    deps = _dependency_record(dependencies)
    helpers = _helper_declarations(config, deps)
    methods = _method_record(package, config)
    # Use the same exact prospective mapping the public wrapper qualified.
    renderer_runtime = _renderer_request(package, config, deps)
    allowances = _validate_allowances(config.get("metadata_allowances", ()))
    environment = {str(key): str(value) for key, value in config.get("environment", {}).items()}
    if any(key.upper() in _RESERVED_ENV or key.upper().startswith("PYTHON") for key in environment):
        raise TrialError(
            "E_TRIAL_CONFIG",
            "Environment cannot redirect owned configuration, profile, temp, "
            "state or Python initialization",
        )
    source = Path(config.get("starting_project", "physical-footprint-eco/starting-project"))
    source = source if source.is_absolute() else package / source
    source = ordinary_path(source)
    if not source.is_relative_to(package):
        raise TrialError(
            "E_TRIAL_PROJECT", "Preparation source must be the packaged original seven-file project"
        )
    source_inventory = tree_inventory(source)
    if set(source_inventory["files"]) != set(PROJECT_FILES):
        raise TrialError(
            "E_TRIAL_PROJECT",
            "Preparation source must contain exactly the original seven engineering files",
        )
    seeds = {}
    for profile in PROFILES:
        seeds[profile] = {}
        for relative, value in config.get("seed_files", {}).get(profile, {}).items():
            relative_path = Path(relative)
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise TrialError(
                    "E_TRIAL_CONFIG",
                    "Configuration seed path escapes its profile",
                    details=relative,
                )
            path = ordinary_path(value)
            seeds[profile][relative] = {"path": str(path), "identity": file_identity(path)}
    work.mkdir(exist_ok=False)
    _put_new(
        work / "OWNERSHIP.json",
        {"schema": SCHEMA, "work_root": str(work), "created_at": _now(), "state": "INCOMPLETE"},
    )
    record = {
        "schema": SCHEMA,
        "status": "INCOMPLETE",
        "request": request,
        "work_root": str(work),
        "package_root": str(package),
        "created_at": _now(),
        "dependencies": deps,
        "methods": methods,
        "source_inventory": source_inventory,
        "profiles": {name: _profile_paths(work, name) for name in PROFILES},
        "environment": environment,
        "metadata_allowances": allowances,
        "seed_sources": seeds,
        "public_refusal_helpers": helpers,
        "preparation_calls": [],
        "preparation_call_counts": {
            role: {"attempted": 0, "completed": 0} for role in PREPARATION_COUNTS
        },
        "renderer_runtime": renderer_runtime,
        "renderer_preparation": {
            "schema": RENDERER_PREPARATION_SCHEMA,
            "status": "INCOMPLETE",
            "required_counts": dict(PREPARATION_COUNTS),
            "initial": {},
            "stability": {},
        },
        "formal_cad_writes": 0,
        "no_concurrent_writer_precondition": True,
        "observation_scope": (
            "Ordinary serial file observations, not an atomic snapshot. "
            "Preparation outputs never count as formal acceptance."
        ),
        "synthetic": _runner is not None,
    }
    ready = ReadyContext(record)
    try:
        for relative in (
            "PREPARATION/project",
            "logs/preparation/writer",
            "logs/preparation/consumer",
            "state",
            "output/preparation/writer",
            "output/preparation/consumer",
        ):
            (work / relative).mkdir(parents=True, exist_ok=False)
        for profile in PROFILES:
            for key, value in record["profiles"][profile].items():
                Path(value).mkdir(parents=True, exist_ok=key != "root")
            for relative, seed in seeds[profile].items():
                destination = Path(record["profiles"][profile]["config"]) / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as stream:
                    stream.write(Path(seed["path"]).read_bytes())
                if (
                    file_identity(seed["path"]) != seed["identity"]
                    or file_identity(destination)["sha256"] != seed["identity"]["sha256"]
                ):
                    raise TrialError(
                        "E_TRIAL_CONFIG",
                        "Configuration seed changed during its owned copy",
                        details=relative,
                    )
        record["bytecode_prefixes"] = {
            name: tree_inventory(record["profiles"][name]["bytecode_empty"]) for name in PROFILES
        }
        _verify_bytecode_prefixes(ready)
        for name in PROJECT_FILES:
            with (ready.preparation_project / name).open("xb") as stream:
                stream.write((source / name).read_bytes())
        record["preparation_input"] = tree_inventory(ready.preparation_project)
        _put_new(
            work / "state/PREPARATION-PLAN.json",
            {
                "schema": RENDERER_PREPARATION_SCHEMA,
                "declared_before_consumers": _now(),
                "required_counts": PREPARATION_COUNTS,
                "renderer_runtime": renderer_runtime,
                "native_calls": [
                    {"profile": profile, "name": name, "argv": argv, "expected_outputs": outputs}
                    for profile in PROFILES
                    for name, argv, outputs in _initialization_commands(ready, profile)
                ],
                "renderer_calls": [
                    {
                        "profile": profile,
                        "host_tasks": ["renderer-derive", "renderer-geometry"],
                        "node_tasks": ["renderer-node-initial", "renderer-node-stability"],
                        "python_flags": ready.python_flags(profile),
                        "source_svg": str(work / "output/preparation" / profile / "pcb.svg"),
                        "dynamic_binding": "Derived/proof/check bytes and exact Node plan SHA "
                        "are frozen in new plans before each Node launch.",
                    }
                    for profile in PROFILES
                ],
                "stability": "First Node rounds complete before one full config/HOME/cache "
                "baseline; second Node rounds must match it exactly. "
                "No exclusion, clearing or refreezing.",
                "preparation_only": True,
                "engineering_acceptance": False,
            },
        )
        record["preparation_plan"] = {
            "path": str(work / "state/PREPARATION-PLAN.json"),
            "identity": file_identity(work / "state/PREPARATION-PLAN.json"),
        }
        for profile in PROFILES:
            for name, argv, outputs in _initialization_commands(ready, profile):
                _preparation_step(
                    ready,
                    profile,
                    {"name": name, "argv": argv, "expected_outputs": outputs},
                    "native",
                    config,
                    _runner,
                )
        from .reading import (
            build_renderer_warmup_node_plan,
            renderer_warmup_declaration,
            validate_renderer_warmup,
        )

        declarations = {}
        for profile in PROFILES:
            _preparation_bindings(ready)
            declaration = renderer_warmup_declaration(ready, profile)
            declarations[profile] = declaration
            for task in declaration["host_tasks"]:
                _preparation_step(ready, profile, task, "host", config, _runner)
            task = build_renderer_warmup_node_plan(ready, declaration, "initial")
            _preparation_step(ready, profile, task, "node", config, _runner)
            record["renderer_preparation"]["initial"][profile] = validate_renderer_warmup(
                ready, declaration, task
            )
            _preparation_bindings(ready)
        baseline = _configuration_snapshot(ready)
        record["renderer_preparation"]["configuration_baseline"] = baseline
        baseline_path = work / "state/RENDERER-CONFIGURATION-BASELINE.json"
        _put_new(baseline_path, {"declared_before_stability_nodes": _now(), **baseline})
        baseline_identity = file_identity(baseline_path)
        for profile in PROFILES:
            _verify_renderer_baseline(baseline)
            task = build_renderer_warmup_node_plan(ready, declarations[profile], "stability")
            _preparation_step(ready, profile, task, "node", config, _runner)
            record["renderer_preparation"]["stability"][profile] = validate_renderer_warmup(
                ready, declarations[profile], task
            )
            initial_image = record["renderer_preparation"]["initial"][profile]["image"]
            stable_image = record["renderer_preparation"]["stability"][profile]["image"]
            if any(initial_image[key] != stable_image[key] for key in ("sha256", "size")):
                raise TrialError(
                    "E_TRIAL_RENDERER_STABILITY",
                    "Same-input renderer repeat produced different PNG bytes",
                )
            _preparation_bindings(ready)
            _verify_renderer_baseline(baseline)
            if file_identity(baseline_path) != baseline_identity:
                raise TrialError(
                    "E_TRIAL_CONTEXT", "Renderer pre-stability baseline record changed"
                )
        record.update(copy.deepcopy(baseline))
        record["renderer_preparation"].update(
            status="passed_preparation_only",
            baseline_record={"path": str(baseline_path), "identity": baseline_identity},
            preparation_only=True,
            engineering_acceptance=False,
        )
        record["metadata_baseline"] = {name: {} for name in PROFILES}
        record["metadata_text_baseline"] = {name: {} for name in PROFILES}
        for item in allowances:
            profile, relative = item["profile"], item["path"]
            document = _metadata_document(Path(record["profiles"][profile]["config"]), relative)
            if not _owned_metadata_path(document["system"]["working_dir"], work):
                raise TrialError(
                    "E_TRIAL_CONFIG",
                    "Initial declared working_dir must already identify "
                    "an existing owned directory",
                )
            record["metadata_baseline"][profile][relative] = document
            raw = (
                (Path(record["profiles"][profile]["config"]) / relative)
                .read_bytes()
                .decode("utf-8")
            )
            record["metadata_text_baseline"][profile][relative] = _working_dir_projection(raw)
        record.update(
            status="READY",
            completed_at=_now(),
            classification={
                "configuration": "strict complete file membership, file identity, bytes and mtime",
                "logs": "owned logs",
                "state": "owned state",
                "cache": "complete per-profile cache frozen after first Node warmup; no exclusions",
                "bytecode": (
                    "per-profile registered empty prefix; strict complete membership/identity"
                ),
                "temp": "per-profile temp",
                "outputs": "owned output/preparation; excluded from formal acceptance",
            },
        )
        verify_ready(ready, _require_persisted=False)
        _put_new(work / "READY.json", record)
        return ready
    except BaseException as error:
        _put_new(
            work / "INCOMPLETE.json",
            {
                "schema": SCHEMA,
                "status": "INCOMPLETE",
                "at": _now(),
                "reason": type(error).__name__ + ": " + str(error),
                "code": getattr(error, "code", None),
                "details": _plain(getattr(error, "details", None)),
                "bytecode_prefix_observation": _plain(
                    getattr(error, "bytecode_prefix_observation", None)
                ),
                "completed_calls": record["preparation_calls"],
                "preparation_call_counts": record["preparation_call_counts"],
                "action": (
                    "Preserve this directory. No automatic retry or resume; "
                    "prepare a new owned root under a new plan."
                ),
            },
        )
        raise


def load_ready(work_root: str | Path) -> ReadyContext:
    work = ordinary_path(work_root)
    if (
        not (work / "OWNERSHIP.json").is_file()
        or not (work / "READY.json").is_file()
        or (work / "INCOMPLETE.json").exists()
    ):
        raise TrialError("E_TRIAL_INCOMPLETE", "Owned preparation has no complete READY record")
    record = json.loads((work / "READY.json").read_text(encoding="utf-8"))
    if (
        record.get("schema") != SCHEMA
        or record.get("status") != "READY"
        or record.get("work_root") != str(work)
    ):
        raise TrialError("E_TRIAL_CONTEXT", "READY identity is incompatible with this work root")
    ready = ReadyContext(record)
    verify_ready(ready)
    return ready


def verify_ready(
    ready: ReadyContext, *, inputs: Mapping[str, Any] | None = None, _require_persisted: bool = True
) -> dict[str, Any]:
    if ready.record.get("status") != "READY":
        raise TrialError("E_TRIAL_INCOMPLETE", "Preparation is not READY")
    _verify_renderer_ready(ready)
    ordinary_path(ready.work_root)
    if _require_persisted:
        if json.loads((ready.work_root / "READY.json").read_text(encoding="utf-8")) != ready.record:
            raise TrialError("E_TRIAL_CONTEXT", "Persisted READY differs from the selected context")
        if (ready.work_root / "INCOMPLETE.json").exists():
            raise TrialError("E_TRIAL_INCOMPLETE", "Preparation is marked incomplete")
    bytecode = _verify_bytecode_prefixes(ready)
    _verify_dependencies(ready.record["dependencies"])
    _verify_methods(ready.record["methods"])
    verify_inventory(ready.record["source_inventory"])
    verify_inventory(ready.record["preparation_input"])
    metadata_changes = [change for profile in PROFILES for change in _verify_config(ready, profile)]
    if inputs:
        for expected in inputs.values():
            verify_inventory(expected)
    return {
        "ready": True,
        "verified_at": _now(),
        "native_calls": 0,
        "config_metadata_changes": metadata_changes,
        "synthetic_preparation": ready.record["synthetic"],
        "bytecode_prefix_observation": bytecode,
    }


def freeze_context(ready: ReadyContext, *, inputs: Mapping[str, str | Path]) -> FrozenContext:
    verify_ready(ready)
    frozen = {}
    for label, value in inputs.items():
        if not isinstance(label, str) or not label:
            raise TrialError("E_TRIAL_USAGE", "Frozen input labels must be nonempty strings")
        frozen[label] = capture(value)
    if not frozen:
        raise TrialError(
            "E_TRIAL_USAGE",
            "Freeze the formal engineering inputs and validator methods before running",
        )
    return FrozenContext(ready, frozen)


def _verify_validator_sources(context: FrozenContext, changes: Mapping[str, Callable]) -> None:
    bound = set(context.ready.record["methods"]["files"])
    for inventory in context.inputs.values():
        if inventory["kind"] == "file":
            bound.add(inventory["path"])
        else:
            bound.update(str(Path(inventory["root"]) / relative) for relative in inventory["files"])
    for label, validator in changes.items():
        code = getattr(validator, "__code__", None)
        if (
            code is None
            or not Path(code.co_filename).is_absolute()
            or str(ordinary_path(code.co_filename)) not in bound
        ):
            raise TrialError(
                "E_TRIAL_CONTRACT",
                "Finite input validator source must be frozen before the invocation",
                details=label,
            )


def _error_record(error: BaseException) -> dict[str, Any]:
    return {
        "type": type(error).__name__,
        "message": str(error),
        "code": getattr(error, "code", None),
        "details": _plain(getattr(error, "details", None)),
    }


def _failure_post_observation(ready: ReadyContext, before: Mapping[str, Any]) -> dict[str, Any]:
    """Read each bounded configuration/input independently after a stopped call.

    No process is launched, no validator is executed and no baseline is advanced.
    A failed or unknown Job termination does not establish a no-writer condition;
    these are best-effort serial observations and can remain incomplete.
    """
    result: dict[str, Any] = {
        "status": "OBSERVED",
        "observed_at": _now(),
        "acceptance_evidence": False,
        "baseline_advanced": False,
        "native_calls": 0,
        "termination_inferred_from_files": False,
        "scope": (
            "Best-effort serial observations after failure; not an atomic snapshot "
            "or proof that a Native writer/complete Job has ended."
        ),
        "configuration": {},
        "bytecode_prefixes": {},
        "inputs": {},
        "observation_errors": [],
    }

    def observe(label, expected, destination):
        try:
            actual = capture(expected.get("root", expected.get("path")))
            destination[label] = {
                "status": "OBSERVED",
                "inventory": actual,
                "differences": inventory_diff(expected, actual),
            }
        except BaseException as error:
            item = {"status": "INCOMPLETE", "error": _error_record(error)}
            destination[label] = item
            result["observation_errors"].append({"label": label, **item})
            result["status"] = "INCOMPLETE"

    for profile in PROFILES:
        if profile in ready.record.get("bytecode_prefixes", {}):
            observe(
                profile, ready.record["bytecode_prefixes"][profile], result["bytecode_prefixes"]
            )
        observe(
            profile + "/config", ready.record["configuration"][profile], result["configuration"]
        )
        for domain, expected in ready.record["profile_configuration"][profile].items():
            observe(profile + "/" + domain, expected, result["configuration"])
    for label, expected in before.items():
        observe(label, expected, result["inputs"])
    return result


def guarded_run(
    argv: Sequence[str | Path],
    *,
    context: FrozenContext,
    profile: str,
    cwd: str | Path,
    out: str | Path,
    timeout: float = 120,
    expected_outputs: Sequence[str | Path] = (),
    expected_exit_codes: Sequence[int] = (0,),
    expected_public_refusal: Mapping[str, Any] | None = None,
    allowed_input_changes: Mapping[str, Callable[[dict, dict], bool]] | None = None,
    _runner=None,
) -> dict[str, Any]:
    """Run once under the observer, strict guards and predeclared finite validators."""
    if context.stopped:
        raise TrialError(
            "E_TRIAL_STOPPED", "This formal context has stopped; preserve its evidence"
        )
    changes = dict(allowed_input_changes or {})
    if (
        not argv
        or set(changes) - set(context.inputs)
        or any(not callable(value) for value in changes.values())
    ):
        raise TrialError(
            "E_TRIAL_USAGE", "Every allowed input change requires a validator for a frozen input"
        )
    profile = _profile(profile)
    if (
        not expected_exit_codes
        or len(set(expected_exit_codes)) != len(expected_exit_codes)
        or any(type(code) is not int or code < 0 or code > 255 for code in expected_exit_codes)
    ):
        raise TrialError(
            "E_TRIAL_USAGE", "Expected exits must be distinct ordinary codes from 0 to 255"
        )
    refusal = _refusal_declaration(expected_public_refusal, expected_exit_codes)
    ready = context.ready
    before = copy.deepcopy(context.inputs)
    policy_path = None
    invocation_attempted = False
    try:
        initial = verify_ready(ready, inputs=before)
        _verify_validator_sources(context, changes)
        policy_path = ready.work_root / "state" / ("run-plan-" + uuid.uuid4().hex + ".json")
        _put_new(
            policy_path,
            {
                "schema": SCHEMA,
                "declared_before_launch": _now(),
                "argv": list(map(str, argv)),
                "profile": profile,
                "cwd": str(cwd),
                "observer_directory": str(out),
                "expected_exit_codes": list(expected_exit_codes),
                "expected_public_refusal": refusal,
                "public_refusal_helpers": ready.record["public_refusal_helpers"] if refusal else [],
                "expected_outputs": list(map(str, expected_outputs)),
                "allowed_input_changes": {
                    label: {"source": fn.__code__.co_filename, "qualname": fn.__qualname__}
                    for label, fn in changes.items()
                },
                "inputs_before": before,
            },
        )
        invocation_attempted = True
        result = _run(
            ready,
            argv,
            profile=profile,
            cwd=cwd,
            out=out,
            timeout=timeout,
            expected_outputs=expected_outputs,
            runner=_runner,
            expected_exit_codes=expected_exit_codes,
            expected_public_refusal=refusal,
        )
        final = verify_ready(ready)
        after = {
            label: capture(item.get("root", item.get("path"))) for label, item in before.items()
        }
        violations = {}
        for label, original in before.items():
            diff = inventory_diff(original, after[label])
            if diff and (
                label not in changes or changes[label](original, after[label]) is not True
            ):
                violations[label] = diff
        if violations:
            raise TrialError(
                "E_TRIAL_INPUT_DRIFT",
                "Frozen input changed outside this invocation's finite contract",
                details=violations,
            )
        result.update(
            execution_policy=str(policy_path),
            inputs_before=before,
            inputs_after=after,
            config_metadata_changes={
                "before": initial["config_metadata_changes"],
                "after": final["config_metadata_changes"],
            },
        )
        context.inputs = after
        return result
    except BaseException as error:
        context.stopped = True
        try:
            post = _failure_post_observation(ready, before)
        except BaseException as post_error:
            # Preserve the original Native/guard exception even when the bounded
            # observer itself cannot complete. Never infer current files here.
            post = {
                "status": "INCOMPLETE",
                "acceptance_evidence": False,
                "baseline_advanced": False,
                "native_calls": 0,
                "observation_errors": [
                    {"label": "post_observer", "error": _error_record(post_error)}
                ],
            }
        error.post_failure_observation = post
        failure = ready.work_root / "state" / ("formal-stop-" + uuid.uuid4().hex + ".json")
        try:
            _put_new(
                failure,
                {
                    "schema": SCHEMA,
                    "at": _now(),
                    "reason": type(error).__name__ + ": " + str(error),
                    "code": getattr(error, "code", None),
                    "details": _plain(getattr(error, "details", None)),
                    "original_failure": _error_record(error),
                    "execution_policy": str(policy_path) if policy_path else None,
                    "invocation_attempted": invocation_attempted,
                    "inputs_before": before,
                    "post_failure_observation": post,
                    "out": str(out),
                    "automatic_retry": False,
                },
            )
            error.failure_record = str(failure)
        except BaseException as persistence_error:
            error.failure_record_error = _error_record(persistence_error)
            error.add_note(
                "Stopped-call evidence could not be persisted; the original failure "
                "remains authoritative and post observations are attached to this exception."
            )
        raise
