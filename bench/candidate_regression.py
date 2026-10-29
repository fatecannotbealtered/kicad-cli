"""Freeze exact pytest IDs, then opt in to a serial owned Windows regression.

Run from a committed detached checkout. Configuration and raw invocation records
are local evidence; only the allowlisted review should enter a candidate package.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import inspect
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import pytest

EXE_MODULES = [
    "test_move_current_contract_exe",
    "test_move_stability_replyloss_exe",
    "test_move_safety_exe",
    "test_public_behavior_fcc",
    "test_public_contract_edges",
    "test_public_surface",
    "test_schematic_input_discovery",
    "test_sch_edit_contract",
    "test_standalone_dependencies",
    "test_standalone_failures",
    "test_rc_public_guidance",
    "test_sync_removal",
    "test_sync_removal_geometry",
    "test_hierarchy_edit_runtime",
    "test_sch_field_feedback",
    "test_change_workflow",
    "test_saved_project_exe",
    "test_saved_project_drift_exe",
    "test_candidate_move_acceptance",
]
PLAN_FORMAT = "kicad-cli.candidate-regression-plan.v3"
PYTEST_CONFIG_NAMES = {
    "pytest.ini",
    ".pytest.ini",
    "pyproject.toml",
    "tox.ini",
    "setup.cfg",
    "pytest.toml",
    ".pytest.toml",
}
PYTHON_INPUT_SUFFIXES = {".py", ".pyw", ".pyc", ".pyo", ".pyd", ".so", ".dll", ".pth"}
# These are local evidence/VCS caches, never pytest discovery or import roots.
# Tests that intentionally consume them must declare them in dependency_paths.
NON_INPUT_ROOTS = {".git", ".test-scratch", ".pytest_cache", ".ruff_cache"}
# Only these observer/delivery values are removed from the child environment.
# Every other supported value, including inherited PATH, is content-bound.
OBSERVER_ENV = {
    "CANDIDATE_REGRESSION_CONFIG",
    "CANDIDATE_REGRESSION_PLAN",
    "CANDIDATE_REGRESSION_PLAN_SHA256",
    "KICAD_CLI_TRACE",
    "KICAD_CLI_ERROR_TRACE",
    "PYTEST_CURRENT_TEST",
    "PYTEST_VERSION",
}
PATH_ENV = {
    "KICAD_CLI_TEST_BINARY",
    "KICAD_CLI_ACCEPT_CANDIDATE",
    "KICAD_CLI_DRIVER_SDK_PATH",
    "KICAD_CLI_ACCEPT_DRIVER_SDK",
    "KICAD_CLI_ROOT",
    "KICAD_CLI_ACCEPT_NATIVE_ROOT",
    "KICAD_CLI_PYTHON",
    "KICAD_CLI_OFFICIAL",
    "KICAD_CLI_IPC_CLIENT_PATH",
    "KICAD_CONFIG_HOME",
    "KICAD_CLI_STATE_DIR",
    "KICAD_CLI_SCOPE_FILE",
    "KICAD_CLI_ACCEPT_BASELINE",
    "KICAD_BENCH_NATIVE_QUARANTINE",
    "KIPRJMOD",
}
RUNTIME_PACKAGES = (
    "pytest",
    "pluggy",
    "packaging",
    "iniconfig",
    "pygments",
    "colorama",
    "jsonschema",
    "referencing",
    "attrs",
    "rpds-py",
    "jsonschema-specifications",
    "tomli",
)
_current = None
_calls = {}
_config = None
_streams = {}
_open = open
_encode = json.dumps


def sha(path):
    digest = hashlib.sha256()
    with _open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def put(path, value):
    with _open(path, "x", encoding="utf-8") as stream:
        stream.write(_encode(value, ensure_ascii=False, indent=2) + "\n")


def append_record(name, value):
    stream = _streams[name]
    stream.write(_encode(value, ensure_ascii=False) + "\n")
    stream.flush()


def now():
    return datetime.now(timezone.utc).isoformat()


def tracked_files(root):
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    return {name: sha(root / name) for name in names if name}


def load(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Duplicate plan/config key: " + key)
            value[key] = item
        return value

    return json.loads(Path(path).read_bytes(), object_pairs_hook=unique)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def snapshot(path):
    path = Path(os.path.abspath(path))
    result = {
        "path": str(path),
        "resolved_path": str(path.resolve()),
        "linked": linked(path),
        "kind": "absent",
    }
    if path.is_file():
        result.update(kind="file", sha256=sha(path))
    elif path.is_dir():
        nested_links = [p for p in path.rglob("*") if p.is_dir() and linked(p)]
        if nested_links:
            raise ValueError(
                "Nested linked dependency tree is unsupported: " + str(nested_links[0])
            )
        result.update(
            kind="directory",
            files={
                p.relative_to(path).as_posix(): {
                    "sha256": sha(p),
                    "linked": linked(p),
                    "resolved_path": str(p.resolve()) if linked(p) else None,
                }
                for p in sorted(path.rglob("*"))
                if p.is_file()
            },
        )
    return result


def runtime_dependencies():
    """Bind actual installed runner dependencies, rather than version labels alone."""
    rows = {}
    for name in RUNTIME_PACKAGES:
        try:
            dist = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            rows[name] = {"present": False}
            continue
        paths = {str(p): dist.locate_file(p) for p in dist.files or []}
        for path in list(paths.values()):
            if path.suffix == ".py":
                for cache in (path.parent / "__pycache__").glob("*"):
                    paths[str(cache)] = cache
        rows[name] = {
            "version": dist.version,
            "location": str(Path(dist.locate_file("")).resolve()),
            "files": {name: sha(path) for name, path in sorted(paths.items()) if path.is_file()},
        }
    return rows


def source_cache_identity(root):
    """-B stops writes; existing bytecode still has to remain identity-bound."""
    paths = set(root.glob("*.py[co]"))
    for name in ("__pycache__", "kicad_cli", "bench", "scripts", "tests", "skills", "docs"):
        paths.update((root / name).rglob("*.py[co]"))
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(paths) if p.is_file()}


def linked(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def source_pytest_inputs(root):
    """Discover executable/config inputs, including new untracked root conftests.

    This is a finite pytest-method boundary, not a Python security sandbox.
    Arbitrary file readers/import hooks in reviewed tests still need review.
    """
    rows = {}

    def fail_walk(error):
        raise error

    for parent, directories, files in os.walk(root, followlinks=False, onerror=fail_walk):
        parent = Path(parent)
        directories[:] = sorted(
            d for d in directories if parent != root or d not in NON_INPUT_ROOTS
        )
        for name in directories:
            path = parent / name
            if linked(path):
                raise ValueError("Linked executable/config tree is unsupported: " + str(path))
            if path.suffix.lower() in PYTHON_INPUT_SUFFIXES or name.lower() in PYTEST_CONFIG_NAMES:
                rows[path.relative_to(root).as_posix()] = {"kind": "directory"}
        for name in sorted(files):
            path = parent / name
            if (
                path.suffix.lower() not in PYTHON_INPUT_SUFFIXES
                and name.lower() not in PYTEST_CONFIG_NAMES
            ):
                continue
            if linked(path):
                raise ValueError("Linked executable/config input is unsupported: " + str(path))
            rows[path.relative_to(root).as_posix()] = {"kind": "file", "sha256": sha(path)}
    return rows


def runner_pytest_profile(config, root, inputs, env):
    """Pin config/root/conftest/plugin discovery before launching pytest."""
    name = config.get("pytest_config_file", "pyproject.toml")
    if not isinstance(name, str) or name not in PYTEST_CONFIG_NAMES:
        raise ValueError("pytest_config_file must name one supported source-root config file")
    path = root / name
    if not path.is_file() or linked(path):
        raise ValueError("Explicit pytest configuration must be a regular source-root file")
    # Use this bound pytest installation's reader, including native TOML mode
    # when supported. A second hand-written parser could miss active addopts.
    from _pytest.config.findpaths import load_config_dict_from_file
    from _pytest.outcomes import Failed

    try:
        parsed = load_config_dict_from_file(path) or {}
    except (pytest.UsageError, Failed) as exc:
        raise ValueError("Invalid explicit pytest configuration: " + str(exc)) from exc
    options = {key: getattr(value, "value", value) for key, value in parsed.items()}
    for option in ("addopts", "pythonpath", "required_plugins"):
        if options.get(option):
            raise ValueError(
                "Configure runner dependencies explicitly; unsupported pytest " + option
            )
    plugins = config.get("pytest_plugin_modules", [])
    if (
        not isinstance(plugins, list)
        or any(
            not isinstance(p, str) or not re.fullmatch(r"[A-Za-z_]\w*(\.\w+)*", p) for p in plugins
        )
        or len(plugins) != len(set(plugins))
    ):
        raise ValueError("pytest_plugin_modules must be unique explicit Python module names")
    import_roots = [root]
    if env.get("PYTHONPATH"):
        for value in env["PYTHONPATH"].split(os.pathsep):
            if not value or not Path(value).is_absolute():
                raise ValueError("PYTHONPATH entries must be explicit absolute dependency roots")
            if Path(value).resolve() not in import_roots:
                import_roots.append(Path(value).resolve())
    plugin_files = {}
    for plugin in plugins:
        relative = Path(*plugin.split("."))
        candidates = [p / relative.with_suffix(".py") for p in import_roots]
        candidates += [p / relative / "__init__.py" for p in import_roots]
        found = [p for p in candidates if p.is_file()]
        if len(found) != 1 or linked(found[0]):
            raise ValueError(
                "Declared pytest plugin must resolve once in bound Python roots: " + plugin
            )
        plugin_files[plugin] = snapshot(found[0])
    for name, row in inputs.items():
        if not name.lower().endswith(".py") or row["kind"] != "file":
            continue
        # pytest discovers pytest_plugins in conftests and collected test modules.
        # A declaration is executable method input, never an implicit dependency.
        if Path(name).name.lower() != "conftest.py" and not name.startswith("tests/"):
            continue
        tree = ast.parse((root / name).read_bytes(), filename=name)
        for node in ast.walk(tree):
            targets = (
                node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
            )
            if any(isinstance(t, ast.Name) and t.id == "pytest_plugins" for t in targets):
                try:
                    declared = ast.literal_eval(node.value)
                except (ValueError, TypeError, AttributeError) as exc:
                    raise ValueError(
                        "pytest_plugins must be a literal declared module list"
                    ) from exc
                declared = [declared] if isinstance(declared, str) else declared
                if not isinstance(declared, (list, tuple)) or any(
                    p not in plugins for p in declared
                ):
                    raise ValueError("Undeclared pytest_plugins in " + name)
    return {
        "config_file": snapshot(path),
        "config_options": options,
        "rootdir": str(root),
        "confcutdir": str(root),
        "import_mode": "prepend",
        "plugin_autoload": False,
        "explicit_plugins": ["bench.candidate_regression", *plugins],
        "plugin_files": plugin_files,
        "python_import_roots": list(map(str, import_roots)),
        "excluded_root_directories": sorted(NON_INPUT_ROOTS),
        "parent_configuration_and_conftests": "excluded by explicit -c/--rootdir/--confcutdir",
    }


def collection_modules(config, root):
    selected = config.get("selected_ids")
    if selected is None:
        if config["mode"] == "exe":
            return ["tests/" + name + ".py" for name in EXE_MODULES]
        return ["tests/"]
    if not isinstance(selected, list) or not selected:
        raise ValueError("selected_ids must be a nonempty list of exact test IDs")
    modules = set()
    for nodeid in selected:
        if not isinstance(nodeid, str) or "::" not in nodeid:
            raise ValueError("selected_ids must name exact tests/...py:: cases")
        module = nodeid.split("::", 1)[0]
        path = PurePosixPath(module)
        if (
            not module.startswith("tests/")
            or "\\" in module
            or path.suffix != ".py"
            or ".." in path.parts
            or str(path) != module
            or not (root / module).is_file()
            or not (root / module).resolve().is_relative_to((root / "tests").resolve())
        ):
            raise ValueError("Selected test module must be a real source tests/ file: " + module)
        modules.add(module)
    return sorted(modules)


def environment_for(config):
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("KICAD", "PYTHON", "CANDIDATE_"))
        and k != "KIPRJMOD"
        and k not in OBSERVER_ENV
    }
    if set(config["environment"]) & OBSERVER_ENV:
        raise ValueError("Observer environment keys cannot be configured")
    env.update(config["environment"])
    for variable in ("PYTEST_PLUGINS", "PYTEST_ADDOPTS"):
        if env.get(variable):
            raise ValueError(variable + " is unsupported; configure this runner explicitly")
    # Third-party auto-loaded pytest plugins would add undeclared executable methods.
    if env.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1") != "1":
        raise ValueError("Regression requires PYTEST_DISABLE_PLUGIN_AUTOLOAD=1")
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return env


def execution_binding(config, env):
    from scripts.fcc_evidence import digest_manifest, source_manifest

    root = Path(config["source"]).resolve()
    pytest_inputs = source_pytest_inputs(root)
    profile = runner_pytest_profile(config, root, pytest_inputs, env)
    dependencies = set(map(str, config.get("dependency_paths", [])))
    dependencies.update(
        v
        for k, v in env.items()
        if k in PATH_ENV or (k.startswith("KICAD") and k.endswith(("_DIR", "_FILE", "_PATH")))
    )
    if env.get("PYTHONPATH"):
        dependencies.update(env["PYTHONPATH"].split(os.pathsep))
    dependencies.discard(str(root))
    # The SDK's pinned spec is adjacent to its implementation directory.
    for key in ("KICAD_CLI_DRIVER_SDK_PATH", "KICAD_CLI_ACCEPT_DRIVER_SDK"):
        if env.get(key):
            dependencies.add(str(Path(env[key]).resolve().parent / "driver_spec"))
    return {
        "config": {k: v for k, v in config.items() if k != "output"},
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "source_manifest_sha256": digest_manifest(source_manifest(root)),
        "tracked_files_sha256": fingerprint(tracked_files(root)),
        "source_cache_identity": source_cache_identity(root),
        "pytest_source_inputs": pytest_inputs,
        "pytest_profile": profile,
        "method": {
            "runner_sha256": sha(__file__),
            "python": snapshot(sys.executable),
            "python_version": sys.version,
            "packages": runtime_dependencies(),
        },
        "binary": snapshot(config["binary"]) if config.get("binary") else None,
        "dependencies": [snapshot(path) for path in sorted(dependencies)],
        "effective_environment_sha256": {
            k: fingerprint(v) for k, v in sorted(env.items()) if k not in OBSERVER_ENV
        },
        "delivery_fields": ["output", "derived basetemp/JUnit/log paths", "config file location"],
        "environment_policy": "All effective values hashed; observer variables excluded explicitly",
    }


def read_plan(path, expected_sha):
    if not re.fullmatch("[0-9a-f]{64}", expected_sha or "") or sha(path) != expected_sha:
        raise ValueError("Original plan SHA-256 mismatch")
    plan = load(path)
    if (
        plan.get("format") != PLAN_FORMAT
        or not plan.get("ids")
        or not isinstance(plan.get("pytest_context"), dict)
    ):
        raise ValueError("Unsupported or empty original plan; collect a new reviewed plan")
    return plan


def check_binding(plan, actual):
    expected = plan["binding"]
    if fingerprint(actual) != fingerprint(expected):
        changed = [
            k
            for k in sorted(set(expected) | set(actual))
            if fingerprint(expected.get(k)) != fingerprint(actual.get(k))
        ]
        raise ValueError("Frozen execution binding drift: " + ", ".join(changed))
    # Also re-read every module actually registered by the reviewed collection.
    # A programmatically registered plugin cannot remain a late-only check.
    for row in plan["pytest_context"]["plugins"]:
        if snapshot(row["file"]["path"]) != row["file"]:
            raise ValueError("Frozen registered pytest plugin drift: " + row["name"])


def registered_plugins(session, binding):
    """Require actual plugin implementations to live in bound method inputs."""
    root = Path(_config["source"]).resolve()
    source_paths = {
        (root / name).resolve()
        for name, row in binding["pytest_source_inputs"].items()
        if row["kind"] == "file"
    }
    runtime_paths = {
        (Path(package["location"]) / name).resolve()
        for package in binding["method"]["packages"].values()
        if package.get("present", True)
        for name in package["files"]
    }
    dependencies = [Path(row["path"]).resolve() for row in binding["dependencies"]]
    rows = []
    for name, plugin in session.config.pluginmanager.list_name_plugin():
        if plugin is None:
            continue
        module = plugin if inspect.ismodule(plugin) else inspect.getmodule(type(plugin))
        if module is None:
            raise ValueError("Cannot locate registered pytest plugin implementation: " + name)
        filename = getattr(module, "__file__", None)
        if filename is None:
            if module.__name__ not in sys.builtin_module_names and module.__name__ != "builtins":
                raise ValueError("Unbound non-file pytest plugin implementation: " + name)
            continue
        path = Path(filename).resolve()
        if (
            path not in source_paths
            and path not in runtime_paths
            and not any(path == dep or path.is_relative_to(dep) for dep in dependencies)
        ):
            raise ValueError(
                "Registered pytest plugin has undeclared executable origin: " + str(path)
            )
        # Object plugin names often contain process-specific IDs. Their class
        # implementations are validated above; runtime distributions are bound
        # in full. Module plugin names and bytes remain exactly comparable.
        if inspect.ismodule(plugin):
            rows.append({"name": name, "file": snapshot(filename)})
    return sorted(rows, key=lambda row: row["name"])


def pytest_configure(config):
    global _config
    _config = load(os.environ["CANDIDATE_REGRESSION_CONFIG"])
    if not config.option.collectonly:
        # These observer handles predate every test fixture. A pure-planner
        # fixture may forbid Path.open while its report hook is still running;
        # it must not intercept the external observer or lose the session.
        for name in ("progress.jsonl", "PRIVATE-CALLS.jsonl", "PRIVATE-FAILURES.jsonl"):
            _streams[name] = _open(Path(_config["output"]) / name, "x", encoding="utf-8")

    def audit(event, args):
        if event != "subprocess.Popen" or _current is None:
            return
        executable, argv, cwd, _env = args
        binary = _config.get("binary")
        from bench.audit_program import direct

        candidate = bool(_config["mode"] == "exe" and binary and direct(executable, argv, binary))
        entry = {
            "executable": str(executable) if executable is not None else None,
            "argv": argv,
            "cwd": str(cwd),
            "direct_candidate": candidate,
            "at": now(),
        }
        _calls.setdefault(_current, []).append(entry)
        append_record("PRIVATE-CALLS.jsonl", {"nodeid": _current, **entry})

    sys.addaudithook(audit)


def pytest_collection_finish(session):
    from scripts.fcc_evidence import source_manifest

    root = Path(session.config.rootpath)
    manifest = source_manifest(root)
    rows = []
    for item in session.items:
        markers = [
            {"name": m.name, "args": [str(a) for a in m.args], "kwargs": m.kwargs}
            for m in item.iter_markers()
            if m.name in {"skip", "skipif"}
        ]
        rows.append(
            {"nodeid": item.nodeid, "skip_conditions": markers, "test_file_sha256": sha(item.path)}
        )
    binding = execution_binding(_config, dict(os.environ))
    actual = {
        "format": PLAN_FORMAT,
        "ids": rows,
        "binding": binding,
        "pytest_context": {
            "rootpath": str(session.config.rootpath),
            "inipath": str(session.config.inipath),
            "plugins": registered_plugins(session, binding),
        },
    }
    output = Path(_config["output"])
    if session.config.option.collectonly:
        put(output / "FROZEN-TESTS.json", actual)
        put(output / "source-manifest.json", manifest)
    else:
        frozen = read_plan(
            os.environ["CANDIDATE_REGRESSION_PLAN"],
            os.environ["CANDIDATE_REGRESSION_PLAN_SHA256"],
        )
        if fingerprint(actual) != fingerprint(frozen):
            raise RuntimeError("Collection, input or artifact drift since frozen plan")
        put(
            output / "EXECUTION-REVALIDATED.json",
            {
                "at": now(),
                "plan_sha256": sha(os.environ["CANDIDATE_REGRESSION_PLAN"]),
                "stage": "actual execution collection; before any test setup",
            },
        )


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    selected = _config.get("selected_ids")
    if selected is not None:
        known = {item.nodeid for item in items}
        if len(selected) != len(set(selected)) or set(selected) - known:
            raise ValueError("Unknown or duplicate explicitly selected test ID")
        deselected = [item for item in items if item.nodeid not in selected]
        items[:] = [item for item in items if item.nodeid in selected]
        config.hook.pytest_deselected(items=deselected)
        config.kicad_cli_full_suite = False


def pytest_runtest_setup(item):
    global _current
    _current = item.nodeid


def pytest_runtest_teardown(item):
    calls = _calls.get(item.nodeid, [])
    if any(c["direct_candidate"] for c in calls):
        item.user_properties.append(("fcc_artifact_sha256", sha(_config["binary"])))
    item.user_properties.append(
        ("candidate_direct_calls", str(sum(c["direct_candidate"] for c in calls)))
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    global _current
    outcome = yield
    report = outcome.get_result()
    # Preserve completed phases even if the host/session disappears before
    # pytest can write its terminal JUnit. This is partial evidence, not a
    # substitute for FINISH or the owned Job's natural completion witness.
    append_record(
        "progress.jsonl",
        {
            "at": now(),
            "nodeid": item.nodeid,
            "phase": report.when,
            "outcome": report.outcome,
            "duration": report.duration,
        },
    )
    if report.failed:
        detail = report.longreprtext
        append_record(
            "PRIVATE-FAILURES.jsonl",
            {"at": now(), "nodeid": item.nodeid, "phase": report.when, "private_failure": detail},
        )
        crash = re.search(r"0xc000[0-9a-f]{4}|\b322122\d{4}\b|spontaneous.*exception", detail, re.I)
        if crash or (
            "no envelope" in detail.lower() and "test_process_evidence" not in item.nodeid
        ):
            stop = Path(_config["output"]) / "NATIVE-STOP.json"
            if not stop.exists():
                put(
                    stop,
                    {
                        "at": now(),
                        "nodeid": item.nodeid,
                        "reason": "Process failure indicator; stop and classify raw evidence",
                        "private_failure": detail,
                    },
                )
            item.session.shouldstop = "Unexpected process failure; preserve this cycle, no replay"
    if report.when == "teardown":
        _current = None  # Session/JUnit metadata helpers are not this test's subprocesses.


def pytest_sessionfinish(session, exitstatus):
    if not session.config.option.collectonly:
        put(Path(_config["output"]) / "PRIVATE-CALLS.json", _calls)


def pytest_unconfigure(config):
    global _current
    _current = None
    for stream in _streams.values():
        stream.close()
    _streams.clear()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--plan", type=Path, help="Previously reviewed FROZEN-TESTS.json")
    parser.add_argument("--plan-sha256", help="SHA-256 recorded when reviewing that plan")
    args = parser.parse_args()
    if args.execute != bool(args.plan and args.plan_sha256) or bool(args.plan) != bool(
        args.plan_sha256
    ):
        parser.error("--execute requires both --plan and --plan-sha256; plan-only takes neither")
    config = load(args.config)
    root, output = Path(config["source"]).resolve(), Path(config["output"]).resolve()
    if config["mode"] not in {"source", "exe"}:
        raise ValueError("Mode must be source or exe")
    if output == root or root in output.parents:
        raise ValueError("Evidence output must be outside the frozen source tree")
    output.mkdir(parents=True, exist_ok=False)
    from scripts.fcc_evidence import digest_manifest, source_manifest

    plan = None
    try:
        manifest = source_manifest(root)
        tracked = tracked_files(root)
        put(output / "tracked-files-before.json", tracked)
        (output / "source-files.sha256.json").open("x", encoding="utf-8").write(
            json.dumps(manifest, sort_keys=True)
        )
        env = environment_for(config)
        modules = collection_modules(config, root)
        binding = execution_binding(config, env)
        if args.execute:
            plan = read_plan(args.plan, args.plan_sha256)
            check_binding(plan, binding)
    except (OSError, ValueError, KeyError, SyntaxError, UnicodeError) as exc:
        put(
            output / "REFUSED.json",
            {
                "at": now(),
                "stage": "preflight before collection/test launch",
                "reason": str(exc),
                "collection_processes_started": 0,
                "test_processes_started": 0,
                "native_processes_started": 0,
                "note": "Binding inspection can invoke git; this does not claim zero processes",
            },
        )
        raise
    # Keep the consumed plan bytes and exact runtime configuration, even when the
    # user's config file or original review directory later changes.
    put(output / "RUNTIME-CONFIG.json", config)
    env["CANDIDATE_REGRESSION_CONFIG"] = str(output / "RUNTIME-CONFIG.json")
    if plan is not None:
        with (output / "FROZEN-TESTS.json").open("xb") as stream:
            stream.write(args.plan.read_bytes())
        env["CANDIDATE_REGRESSION_PLAN"] = str(output / "FROZEN-TESTS.json")
        env["CANDIDATE_REGRESSION_PLAN_SHA256"] = args.plan_sha256
    command = [
        sys.executable,
        "-B",
        "-m",
        "pytest",
        "-c",
        str(root / config.get("pytest_config_file", "pyproject.toml")),
        "--rootdir",
        str(root),
        "--confcutdir",
        str(root),
        "--import-mode=prepend",
        *modules,
        "-p",
        "bench.candidate_regression",
        "-p",
        "no:cacheprovider",
        "--basetemp",
        str(output / "tmp"),
    ]
    for plugin in config.get("pytest_plugin_modules", []):
        command.extend(["-p", plugin])
    start = {
        "mode": config["mode"],
        "started_at": now(),
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "source": str(root),
        "argv": command,
        "explicit_environment": config["environment"],
        "source_manifest_sha256": digest_manifest(manifest),
        "binary_sha256": sha(config["binary"]) if config.get("binary") else None,
        "config_sha256": sha(args.config),
        "plan_origin": str(args.plan.resolve()) if args.plan else None,
        "reviewed_plan_sha256": args.plan_sha256,
        "binding": binding,
    }
    put(output / "START.json", start)
    sys.path.insert(0, str(root / "bench"))
    from process_evidence import run_recorded

    if not args.execute:
        collected, _ = run_recorded(
            output,
            "collection",
            command + ["--collect-only", "-q"],
            cwd=root,
            env=env,
            timeout=config.get("collection_timeout_seconds", 600),
            stdio="files",
            role="plan-collection-python-not-test-execution",
        )
        if collected["status"] != "passed":
            raise RuntimeError("Collection failed; original process evidence preserved")
        frozen = read_plan(output / "FROZEN-TESTS.json", sha(output / "FROZEN-TESTS.json"))
        check_binding(frozen, binding)
        result = {
            "status": "plan_only",
            "plan": str(output / "FROZEN-TESTS.json"),
            "plan_sha256": sha(output / "FROZEN-TESTS.json"),
            "collection_processes_started": 1,
            "test_processes_started": 0,
            "native_processes_started": "not inferred; collection imports must be reviewed",
            "collection_job_finalization": collected.get("job_finalization"),
        }
        put(output / "PLAN-ONLY.json", result)
        print(json.dumps(result))
        return 0
    # Check again immediately before launch; the plugin then rechecks the same
    # plan, method and exact collection inside the actual execution process.
    check_binding(plan, execution_binding(config, env))
    result, _ = run_recorded(
        output,
        "pytest",
        command
        + [
            "-v",
            "--tb=short",
            "-ra",
            "--maxfail=" + str(config.get("max_failures", 1)),
            "--junitxml",
            str(output / "junit.xml"),
        ],
        cwd=root,
        env=env,
        timeout=config.get("timeout_seconds", 14400),
        stdio="files",
        role="serial-regression-runner",
    )
    final = source_manifest(root)
    put(
        output / "FINISH.json",
        {
            **start,
            "exit": result["exit"],
            "finished_at": now(),
            "source_unchanged": final == manifest,
            "tracked_source_unchanged": final == manifest,
            "all_tracked_bytes_unchanged": tracked_files(root) == tracked,
            "source_manifest_sha256": digest_manifest(final),
            "job_finalization": result.get("job_finalization"),
            "execution": result["execution"],
            "evidence_sha256": {
                name: sha(output / name)
                for name in (
                    "junit.xml",
                    "PRIVATE-CALLS.json",
                    "PRIVATE-CALLS.jsonl",
                    "PRIVATE-FAILURES.jsonl",
                    "FROZEN-TESTS.json",
                    "pytest.stdout",
                    "pytest.stderr",
                    "pytest.call.json",
                    "progress.jsonl",
                )
                if (output / name).is_file()
            },
        },
    )
    print(
        json.dumps(
            {"exit": result["exit"], "execution": result["execution"], "output": str(output)}
        )
    )
    return result["exit"] or int(final != manifest)


if __name__ == "__main__":
    raise SystemExit(main())
