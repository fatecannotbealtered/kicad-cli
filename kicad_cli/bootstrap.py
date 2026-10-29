"""SDK-free package configuration and saved-SCH environment diagnostics.

This module never grants a scope, edits CAD files, installs software or changes
machine/user settings. It may initialize only the profile selected by the user
configuration. Diagnostic output deliberately excludes actual host paths.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from .installation import compatible_version, official_cli, resources

CONFIG_NAME = "runtime-config.json"
CONFIG_ENV = "KICAD_CLI_RUNTIME_CONFIG"
SPEC_COMMIT = "9607a35fe0ea33fe822e1f2ad2704eae260d4018"
EXPECTED_KICAD = "10.0.6"
CONFIG_FIELDS = frozenset({"format_version", "kicad_root", "profile_dir", "driver_sdk_path"})


class ConfigurationError(ValueError):
    def __init__(self, kind, field, next_action):
        super().__init__(kind)
        self.kind = kind
        self.field = field
        self.next_action = next_action


def configuration_path():
    explicit = os.environ.get(CONFIG_ENV)
    if explicit is not None:
        if not explicit or not Path(explicit).is_absolute():
            raise ConfigurationError(
                "configuration_path_invalid",
                CONFIG_ENV,
                "Use an absolute runtime-config.json path; no fallback was attempted.",
            )
        return Path(explicit)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / CONFIG_NAME
    return None


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError("duplicate configuration field")
        result[key] = value
    return result


def _resolve(base, value, field):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ConfigurationError(
            "configuration_value_invalid", field, f"Set {field} to the intended directory."
        )
    path = Path(value)
    try:
        return (path if path.is_absolute() else base / path).resolve()
    except (OSError, ValueError):
        raise ConfigurationError(
            "configuration_value_invalid", field, f"Correct {field}; no fallback was attempted."
        ) from None


def read_configuration():
    path = configuration_path()
    if path is None:
        return None
    try:
        raw = path.read_bytes()
    except (OSError, ValueError):
        raise ConfigurationError(
            "configuration_unavailable",
            CONFIG_NAME,
            "Copy runtime-config.example.json beside the EXE as runtime-config.json, "
            "then set kicad_root and an owned profile_dir.",
        ) from None
    try:
        if len(raw) > 64 * 1024:
            raise ValueError("configuration too large")
        config = json.loads(raw, object_pairs_hook=_pairs)
        if not isinstance(config, dict) or set(config) - CONFIG_FIELDS:
            raise ValueError("unsupported configuration fields")
        if type(config.get("format_version")) is not int or config["format_version"] != 1:
            raise ValueError("unsupported configuration format")
    except (TypeError, ValueError, UnicodeError):
        raise ConfigurationError(
            "configuration_format_invalid",
            CONFIG_NAME,
            "Use format_version 1 with kicad_root, profile_dir and optional driver_sdk_path; "
            "duplicate and unknown fields are refused.",
        ) from None
    base = path.resolve().parent
    resolved = {
        "file": path,
        "kicad_root": _resolve(base, config.get("kicad_root"), "kicad_root"),
        "profile_dir": _resolve(base, config.get("profile_dir"), "profile_dir"),
    }
    if "driver_sdk_path" in config:
        resolved["driver_sdk_path"] = _resolve(base, config["driver_sdk_path"], "driver_sdk_path")
    return resolved


def _initialize_symbol_table(config, strict):
    target = config["profile_dir"] / "kicad" / "10.0" / "sym-lib-table"
    template = resources(config["kicad_root"]) / "template/sym-lib-table"
    problem = None
    status = "existing"
    try:
        if target.exists() or target.is_symlink():
            if not target.is_file():
                problem = "isolated_symbol_table_invalid"
        elif not template.is_file():
            problem = "symbol_table_template_missing"
        else:
            raw = template.read_bytes()
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with target.open("xb") as stream:
                    stream.write(raw)
                status = "initialized_from_installed_template"
            except FileExistsError:
                # Another invocation won initialization. Never replace its table.
                if not target.is_file():
                    problem = "isolated_symbol_table_invalid"
    except (OSError, ValueError):
        problem = "isolated_symbol_table_unavailable"
    config["isolated_symbol_table"] = {
        "status": "fail" if problem else status,
        "failure_kind": problem,
        "next_action": (
            "Use the legal installation's share/kicad/template/sym-lib-table, or an "
            "explicitly prepared table in the owned profile's kicad/10.0 directory. "
            "Existing tables are never overwritten and no library is downloaded or invented."
        ),
    }
    if problem and strict:
        raise ConfigurationError(
            problem, "isolated_symbol_table", config["isolated_symbol_table"]["next_action"]
        )


def apply_configuration(config=None, *, strict_symbols=True):
    config = read_configuration() if config is None else config
    if config is None:
        return None
    profile = config["profile_dir"]
    installation = config["kicad_root"]
    # The explicit override remains strict. A missing/invalid override never
    # silently selects the embedded SDK. The package needs no such override.
    sdk_override = os.environ.get("KICAD_CLI_DRIVER_SDK_PATH")
    if "driver_sdk_path" in config:
        sdk_override = str(config["driver_sdk_path"])
    for key in tuple(os.environ):
        if key.startswith("KICAD") or key in {"PYTHONHOME", "PYTHONPATH", "KIPRJMOD"}:
            os.environ.pop(key, None)
    directories = {
        "HOME": profile / "home",
        "USERPROFILE": profile / "home",
        "APPDATA": profile / "appdata",
        "LOCALAPPDATA": profile / "localappdata",
        "TEMP": profile / "tmp",
        "TMP": profile / "tmp",
        "TMPDIR": profile / "tmp",
        "XDG_CONFIG_HOME": profile / "xdg-config",
        "XDG_CACHE_HOME": profile / "xdg-cache",
        "KICAD_CONFIG_HOME": profile / "kicad",
        "KICAD_DOCUMENTS_HOME": profile / "documents",
        "KICAD_CLI_STATE_DIR": profile / "state",
    }
    try:
        for directory in set(directories.values()):
            directory.mkdir(parents=True, exist_ok=True)
        # A fresh lookup prefix cannot import an older helper/SDK .pyc. The
        # SDK itself additionally uses a source-only loader after pin checks.
        cache = profile / "pycache" / f"session-{os.getpid()}-{time.time_ns()}"
        cache.mkdir(parents=True, exist_ok=False)
    except (OSError, ValueError):
        raise ConfigurationError(
            "profile_unavailable", "profile_dir", "Choose a writable owned profile_dir."
        ) from None
    _initialize_symbol_table(config, strict_symbols)
    os.environ.update({key: str(value) for key, value in directories.items()})
    os.environ["KICAD_CLI_SCOPE_FILE"] = str(profile / "scope.json")
    os.environ["KICAD_CLI_ROOT"] = str(installation)
    os.environ["KICAD_CLI_OFFICIAL"] = str(official_cli(installation))
    for kind, name in (
        ("SYMBOL", "symbols"),
        ("FOOTPRINT", "footprints"),
        ("TEMPLATE", "template"),
    ):
        os.environ[f"KICAD10_{kind}_DIR"] = str(resources(installation) / name)
    if sdk_override is not None:
        os.environ["KICAD_CLI_DRIVER_SDK_PATH"] = sdk_override
    os.environ[CONFIG_ENV] = str(config["file"].resolve())
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    sys.dont_write_bytecode = True
    sys.pycache_prefix = str(cache)
    return config


def configuration_error(exc):
    return {
        "ok": False,
        "schema_version": "1.0",
        "meta": {"duration_ms": 0},
        "error": {
            "code": "E_CONFIG",
            "retryable": False,
            "message": "Package configuration is not ready; nothing was dispatched.",
            "details": {
                "phase": "package_bootstrap",
                "dispatch_state": "not_started",
                "failure_kind": exc.kind,
                "field": exc.field,
                "next_action": exc.next_action,
            },
        },
    }


def _check(name, status, required, next_action, **details):
    return {
        "name": name,
        "status": status,
        "required_for_saved_sch": required,
        "next_action": next_action,
        **details,
    }


def check_environment():
    started = time.monotonic()
    try:
        config = apply_configuration(strict_symbols=False)
    except ConfigurationError as exc:
        return configuration_error(exc), 4
    checks = []
    checks.append(
        _check(
            "package_configuration",
            "pass" if config is not None else "fail",
            True,
            "Use runtime-config.json beside the EXE; source callers may set "
            "KICAD_CLI_RUNTIME_CONFIG explicitly.",
            configured=config is not None,
            profile_initialized=config is not None,
            global_settings_modified=False,
        )
    )
    symbol_table = config["isolated_symbol_table"] if config else None
    checks.append(
        _check(
            "isolated_symbol_table",
            "pass" if symbol_table and symbol_table["status"] != "fail" else "fail",
            True,
            symbol_table["next_action"]
            if symbol_table
            else "Configure an owned profile and the legal installation's symbol-table template.",
            initialization=symbol_table["status"] if symbol_table else "not_configured",
            failure_kind=symbol_table["failure_kind"] if symbol_table else "configuration_missing",
            existing_table_overwritten=False,
            library_files_bundled=False,
        )
    )
    from . import driver_runtime

    try:
        driver_runtime._pin()
        checks.append(
            _check(
                "pinned_runtime",
                "pass",
                True,
                "Keep the matched candidate intact; no separate SDK install is needed "
                "when using the embedded runtime.",
                specification_commit=SPEC_COMMIT,
                external_pin_required=False,
                mode=(
                    "explicit_external_override"
                    if "KICAD_CLI_DRIVER_SDK_PATH" in os.environ
                    else "embedded"
                    if getattr(sys, "frozen", False)
                    else "source"
                ),
            )
        )
    except Exception as exc:
        kind = (
            "pin_mismatch"
            if isinstance(exc, driver_runtime.PinMismatch)
            else "python_dependency_missing"
            if isinstance(exc, ImportError)
            else "runtime_unavailable"
        )
        checks.append(
            _check(
                "pinned_runtime",
                "fail",
                True,
                "Use the complete matched candidate and its authenticated runtime. "
                "Correct an explicit SDK override or remove it to use the bundled runtime; "
                "never alter hashes, skip validation or download a substitute automatically.",
                failure_kind=kind,
                specification_commit=SPEC_COMMIT,
            )
        )
    try:
        for module in ("jsonschema", "referencing"):
            importlib.import_module(module)
        checks.append(
            _check(
                "python_validation",
                "pass",
                True,
                "The frozen candidate includes Python and validation dependencies.",
                mode="frozen" if getattr(sys, "frozen", False) else "source",
            )
        )
    except ImportError:
        checks.append(
            _check(
                "python_validation",
                "fail",
                True,
                "Use the matched frozen EXE; source development needs "
                "declared Python dependencies.",
            )
        )
    official = official_cli(config["kicad_root"]) if config else None
    version = None
    problem = "official_program_missing"
    if official is not None and official.is_file():
        if official.resolve() == Path(sys.executable).resolve():
            problem = "official_program_is_product"
        else:
            try:
                result = subprocess.run(
                    [str(official), "--version"], capture_output=True, timeout=20
                )
                value = result.stdout.decode("utf-8", errors="strict").strip()
                if result.returncode == 0 and re.fullmatch(
                    r"\d+\.\d+\.\d+(?:[-+][\w.~-]+)?", value
                ):
                    version = value
                    problem = "version_mismatch" if not compatible_version(version) else None
                else:
                    problem = "version_output_invalid"
            except (OSError, subprocess.SubprocessError, UnicodeError):
                problem = "official_program_unavailable"
    checks.append(
        _check(
            "official_kicad",
            "pass" if problem is None else "fail",
            True,
            "Select a legal existing KiCad10.0 installation (patch 6 or later) in kicad_root; "
            "do not point it at this product EXE or install/change system software automatically.",
            expected_version=EXPECTED_KICAD,
            version_requirement=">=10.0.6,<10.1.0",
            detected_version=version,
            failure_kind=problem,
        )
    )
    library = resources(config["kicad_root"]) / "symbols" if config else None
    try:
        library_present = (
            library is not None and library.is_dir() and any(library.glob("*.kicad_sym"))
        )
    except OSError:
        library_present = False
    checks.append(
        _check(
            "symbol_resources",
            "pass" if library_present else "fail",
            True,
            "Make the required legal KiCad symbol resources readable; query each actual "
            "project symbol before writing. No libraries are downloaded automatically.",
            actual_project_libraries_checked=False,
        )
    )
    scope_present = config is not None and (config["profile_dir"] / "scope.json").is_file()
    checks.append(
        _check(
            "task_scope",
            "present_requires_command_validation" if scope_present else "not_configured",
            False,
            "Create a fresh scope only for an authorized user task; the selected command's "
            "dry-run validates its effects and targets. Scope presence is not authorization.",
            automatically_granted=False,
        )
    )
    checks.append(
        _check(
            "optional_scenarios",
            "not_required_for_saved_sch",
            False,
            "Native Python/pcbnew are for PCB; kipy and a live GUI are for IPC; "
            "Host Python/Node/sharp are independent verification methods, not this EXE's "
            "saved-SCH runtime. Configure only the scenario actually requested.",
            probed=False,
        )
    )
    ready = all(c["status"] == "pass" for c in checks if c["required_for_saved_sch"])
    return {
        "ok": ready,
        "schema_version": "1.0",
        "meta": {"duration_ms": round((time.monotonic() - started) * 1000)},
        **(
            {
                "data": {
                    "check": "saved_sch_package_environment",
                    "read_ready": True,
                    "write_scope_ready": False,
                    "write_scope_status": (
                        "requires_selected_command_validation"
                        if scope_present
                        else "not_configured"
                    ),
                    "checks": checks,
                    "path_disclosure": "host paths and raw exception/output text omitted",
                    "effects": [
                        "initialize_configured_profile",
                        "read_dependencies",
                        "run_official_version",
                    ],
                    "cad_files_written": False,
                    "license_scope": (
                        "specified internal trial recipients only; not public/npm distribution"
                    ),
                }
            }
            if ready
            else {
                "error": {
                    "code": "E_CONFIG",
                    "retryable": False,
                    "message": (
                        "Saved-SCH package environment is not ready; nothing was dispatched."
                    ),
                    "details": {
                        "phase": "package_environment_check",
                        "dispatch_state": "not_started",
                        "read_ready": False,
                        "write_scope_ready": False,
                        "checks": checks,
                    },
                }
            }
        ),
    }, 0 if ready else 4


def write_document(document, compact=False):
    value = json.dumps(document, ensure_ascii=False, indent=None if compact else 2)
    sys.stdout.buffer.write((value + "\n").encode("utf-8"))
