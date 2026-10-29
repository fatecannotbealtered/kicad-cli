"""Prospective operation-show fixtures and final-EXE transport.

Only StateStore's public API prepares these disposable records. Their plans and
receipts are controlled inputs, not evidence of a CAD preview, save, or Native
execution. The response under test always comes from the selected final EXE.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from move_exe_transport import digest, strict_json, verified_output, windows_argv
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource

REPO = Path(__file__).resolve().parents[1]
ARTIFACT_SHA256 = "7ded617e0a06efba811bd50b2ea5ab6c12ee0b1bd879121920d1cdb0f95ceb98"
BUILD_SOURCE = "b045b2a48626f53fa5523b6c12bb940e7307b17b"
SPEC_COMMIT = "9607a35fe0ea33fe822e1f2ad2704eae260d4018"
PIN_SHA256 = "e26dabd06dd559f70ce07535f1b589497dba0a002b5cee59049ff855671b432f"
# Optional helpers observed in the already qualified public EXE method. Their
# exact bytes and argv remain prerequisites, never a general process allowlist.
HELPERS = {
    "conhost.exe": {
        "sha256": "e449bce01f275cd08f3d4e64bb73b3b43ae845a0dbdb3e6131426e66537705e5",
        "arguments": ["0x4"],
        "prefix": "\\??\\",
    },
    "cmd.exe": {
        "sha256": "97ac98b1a92c286054cce55239cfccdfc23a5517bd07fe693072c9ca96c7dabb",
        "arguments": ["/c", "ver"],
        "prefix": "",
    },
}


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def iso(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat().replace("+00:00", "Z")


def put(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def tree(directory):
    """Observe names, bytes and timestamps separately; do not infer SQL I/O."""
    directory = Path(directory)
    return {
        "members": sorted(p.relative_to(directory).as_posix() for p in directory.rglob("*")),
        "files": {
            p.relative_to(directory).as_posix(): {
                "sha256": digest(p),
                "size": p.stat().st_size,
                "mtime_ns": p.stat().st_mtime_ns,
            }
            for p in sorted(directory.rglob("*"))
            if p.is_file()
        },
    }


def logical_state(directory):
    """Read only this newly owned DB, with no SDK migration/prune side effects."""
    database = Path(directory) / "operations.sqlite3"
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        return {
            table: {
                "columns": [
                    row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
                ],
                "rows": [
                    dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY {key}")
                ],
            }
            for table, key in (
                ("operations", "operation_id"),
                ("consumed_tokens", "fingerprint"),
                ("scope_usage", "scope_id"),
            )
        }


def ordinary_initialized_state(state, scope_id):
    stamp = time.time()
    operations = state["operations"]["rows"]
    assert operations
    for row in operations:
        assert row["kind"] == "single" and row["batch_id"] is None
        assert row["status"] in {"planned", "succeeded"}
        assert row["scope_id"] == scope_id
        assert row["resolution"] is None and row["lease_until"] is None
        assert row["plan_expires_at"] > stamp
        assert row["updated_at"] > stamp - 86400
    assert all(row["expires_at"] > stamp for row in state["consumed_tokens"]["rows"])
    assert all(row["scope_id"] == scope_id for row in state["scope_usage"]["rows"])


class FixedSDK:
    def __init__(self, directory):
        assert Path(directory).is_absolute()
        self.directory = Path(directory).resolve(strict=True)
        assert self.directory.is_absolute() and self.directory.is_dir()
        pin_path = REPO / "kicad_cli/driver_spec/PIN.json"
        assert digest(pin_path) == PIN_SHA256
        pin = strict_json(pin_path.read_bytes())
        assert pin["commit"] == SPEC_COMMIT
        self.files = {}
        for name, entry in pin["files"].items():
            assert name.startswith(("_driver_sdk/", "driver_spec/"))
            path = self.directory.parent / name
            assert path.resolve().is_relative_to(self.directory.parent)
            assert digest(path) == entry["sha256"], name
            self.files[str(path)] = entry["sha256"]
        schema_paths = sorted((self.directory.parent / "driver_spec/schemas").glob("*.schema.json"))
        assert {str(p) for p in schema_paths} == {
            path for path in self.files if "/schemas/" in path.replace("\\", "/")
        }
        self.schemas, registry = {}, Registry(retrieve=self._offline)
        for path in schema_paths:
            value = strict_json(path.read_bytes())
            Draft202012Validator.check_schema(value)
            assert value["$id"] not in self.schemas
            self.schemas[value["$id"]] = value
            registry = registry.with_resource(value["$id"], Resource.from_contents(value))
        self.validators = {
            name: Draft202012Validator(value, registry=registry, format_checker=FormatChecker())
            for name, value in self.schemas.items()
        }

    @staticmethod
    def _offline(uri):
        raise NoSuchResource(ref=uri)

    def validate(self, name, value):
        self.validators[f"urn:tool-driver-cli-spec:{name}:2.0"].validate(value)
        if hasattr(self, "semantic_validator"):
            self.semantic_validator.validate(name, value)

    def unchanged(self):
        assert {path: digest(path) for path in self.files} == self.files

    def store(self, directory):
        # Load the authenticated external SDK as its own package, without
        # importing the product's create_cli or any source implementation.
        name = "_trial_operation_fixed_9607_sdk"
        if name not in sys.modules:
            specification = importlib.util.spec_from_file_location(
                name,
                self.directory / "__init__.py",
                submodule_search_locations=[str(self.directory)],
            )
            assert specification is not None and specification.loader is not None
            module = importlib.util.module_from_spec(specification)
            sys.modules[name] = module
            previous, previous_prefix = sys.dont_write_bytecode, sys.pycache_prefix
            empty_cache = Path(directory).parent / "sdk-empty-bytecode"
            empty_cache.mkdir(exist_ok=False)
            try:
                sys.dont_write_bytecode = True
                sys.pycache_prefix = str(empty_cache)
                specification.loader.exec_module(module)
                assert not list(empty_cache.iterdir())
            finally:
                sys.dont_write_bytecode = previous
                sys.pycache_prefix = previous_prefix
        module = sys.modules[name]
        assert Path(module.__file__).resolve() == self.directory / "__init__.py"
        self.semantic_validator = module.ContractValidator(self.directory.parent / "driver_spec")
        return module.StateStore(directory)


def seed_record(sdk, scene, command, status, *, tag):
    """Public Store APIs only; the receipt describes fixture preparation."""
    stamp = time.time()
    operation = "op-" + uuid.uuid4().hex
    plan_id = "plan-" + uuid.uuid4().hex
    capability = {"sch edit": "sch-edit", "project sync": "project-sync"}[command]
    path = scene.project / ("edit.kicad_sch" if command == "sch edit" else "edit.kicad_pcb")
    target = {
        "mode": "file",
        "engine_id": "kicad-cli.saved-board",
        "instance_id": None,
        "object_id": path.as_uri(),
        "locator": path.as_uri(),
        "extensions": {"fixture_only": True},
    }
    snapshot = {
        "id": "snapshot-" + digest(path),
        "source": "disk",
        "revision": digest(path),
        "observed_at": iso(stamp),
        "dependencies": [
            {"identity": p.as_uri(), "revision": digest(p)}
            for p in sorted(scene.project.rglob("*"))
            if p.is_file() and p != path
        ],
    }
    plan = {
        "operation_id": operation,
        "plan_id": plan_id,
        "capability_id": capability,
        "authorization_scope_id": scene.scope_id,
        "effects": ["change", "persist"],
        "target": target,
        "base_snapshot": snapshot,
        "level": "plan",
        "changes": [],
        "outputs": [],
        "unknowns": ["Controlled SDK state input; no CAD command was executed."],
        "constraints": ["Fixture only; public operation show is the sole tested command."],
        "trial_snapshot": None,
        "can_apply": True,
        "expires_at": iso(stamp + 86400),
        "extensions": {"fixture_original": tag, "actual_cad_execution": False},
    }
    sdk.validate("plan", plan)
    receipt = None
    record = {
        "operation_id": operation,
        "plan_id": plan_id,
        "capability_id": capability,
        "command": command,
        "scope_id": scene.scope_id,
        "effects": plan["effects"],
        "plan": plan,
        "plan_expires_at": stamp + 86400,
        "target_key": "fixture|" + path.as_uri(),
        "request_digest": hashlib.sha256(tag.encode()).hexdigest(),
    }
    store = sdk.store(scene.state)
    try:
        sdk.validate("plan", plan)
        assert store.available() == (True, None)
        store.add_plan(record)
        if status == "succeeded":
            verification = {
                "status": "not_applicable",
                "scope": "Controlled record fixture; no CAD verification is claimed.",
                "snapshot_id": snapshot["id"],
                "coverage": "none",
                "checks": [],
                "not_checked": [],
                "evidence_refs": [],
                "reason": "Only Store input preparation completed; no CAD task ran.",
            }
            receipt = {
                "operation_id": operation,
                "capability_id": capability,
                "plan_id": plan_id,
                "request_id": "fixture-" + tag,
                "target": target,
                "authorization_scope_id": scene.scope_id,
                "effects": plan["effects"],
                "snapshot_before": snapshot,
                "snapshot_after": snapshot,
                "outcome": {
                    "execution": "completed",
                    "change": "not_applied",
                    "persistence": "unchanged",
                    "delivery": "not_applicable",
                    "verification": verification,
                },
                "failed_stage": None,
                "changed_items": [],
                "artifacts": [],
                "deliveries": [],
                "recovery": {
                    "action": "none",
                    "reason": "Fixture record only.",
                    "supported": False,
                },
                "extensions": {"fixture_original": tag, "actual_cad_execution": False},
            }
            sdk.validate("receipt", receipt)
            store.dispatch(
                operation,
                fingerprint=hashlib.sha256((tag + operation).encode()).hexdigest(),
                token_expires=stamp + 86400,
                key=receipt["request_id"],
                digest=record["request_digest"],
                target_keys=[record["target_key"]],
                lease=30,
            )
            assert store.finish(operation, "succeeded", receipt)
        assert store.get(operation)["plan"] == plan
        assert store.get(operation)["receipt"] == receipt
    finally:
        store.close()
    original = {"operation_id": operation, "plan": plan, "receipt": receipt, "status": status}
    put(scene.evidence / (tag + "-FIXTURE-ORIGINAL.json"), original)
    return original


def _normalized(value):
    return os.path.normcase(os.path.abspath(value))


def inspect_public_processes(trace, command):
    """Exact operation-show parent/worker and optional frozen system helpers."""
    from bench.native_observation import root_process

    assert trace["observation_schema"] == 3
    root = root_process(trace)
    image = _normalized(command[0])
    processes = trace["processes"]
    candidates = [
        row
        for row in processes
        if _normalized(row["image"]) == image or row.get("image_sha256") == ARTIFACT_SHA256
    ]
    assert len(candidates) == 2 and root in candidates
    worker = next(row for row in candidates if row is not root)
    assert command[1:3] == ["operation", "show"]
    for row in candidates:
        assert row["image_sha256"] == ARTIFACT_SHA256 and _normalized(row["image"]) == image
        actual = windows_argv(row["metadata"]["CommandLine"])
        assert _normalized(actual[0]) == image and actual[1:] == command[1:]
        assert row["identity_status"] == "bound" and row["owned"] is True
    assert worker["parent_process_instance_id"] == root["process_instance_id"]
    assert worker["parent_binding"] == "observed-parent"
    assert worker["metadata"]["ParentProcessId"] == root["pid"]
    assert worker["creation_filetime"] >= root["creation_filetime"]
    assert root["parent_binding"] == "external-observer"
    assert root["parent_process_instance_id"] is None
    assert root["exit"] == worker["exit"] == trace["exit"]
    helpers = []
    for row in processes:
        if row in candidates:
            continue
        name = Path(row["image"]).name.casefold()
        assert name in HELPERS, "Undeclared child process"
        policy = HELPERS[name]
        expected = Path(os.environ["SystemRoot"]) / "System32" / name
        assert _normalized(row["image"]) == _normalized(str(expected))
        assert row["image_sha256"] == policy["sha256"] == digest(expected)
        actual = windows_argv(row["metadata"]["CommandLine"])
        first = actual[0]
        if policy["prefix"]:
            assert first.startswith(policy["prefix"])
            first = first[len(policy["prefix"]) :]
        assert _normalized(first) == _normalized(str(expected))
        assert actual[1:] == policy["arguments"]
        assert row["parent_process_instance_id"] in {
            candidate["process_instance_id"] for candidate in candidates
        }
        parent = next(
            candidate
            for candidate in candidates
            if candidate["process_instance_id"] == row["parent_process_instance_id"]
        )
        assert row["parent_binding"] == "observed-parent"
        assert row["metadata"]["ParentProcessId"] == parent["pid"]
        assert row["creation_filetime"] >= parent["creation_filetime"]
        assert row["exit"] == 0 and name not in helpers
        if name == "cmd.exe":
            assert row["parent_process_instance_id"] == worker["process_instance_id"]
        helpers.append(name)
    native_modules = [
        {"instance": instance, "path": row["path"]}
        for instance, rows in trace["module_inventory"].items()
        for row in rows
        if Path(row.get("path") or "").name.casefold().startswith(("pcbnew", "_pcbnew", "eeschema"))
    ]
    assert not native_modules, "Undeclared Native/pcbnew module"
    return {
        "candidate_process_instances": len(candidates),
        "system_helpers": helpers,
        "native_modules": native_modules,
    }


class TrialOperationExe:
    def __init__(self, scene, sdk, record_property):
        assert os.name == "nt" and sys.version_info[:2] == (3, 12)
        self.scene, self.sdk, self.calls = scene, sdk, []
        assert Path(os.environ["KICAD_CLI_TEST_BINARY"]).is_absolute()
        self.binary = Path(os.environ["KICAD_CLI_TEST_BINARY"]).resolve(strict=True)
        assert self.binary.is_absolute() and digest(self.binary) == ARTIFACT_SHA256
        record_property("fcc_artifact_sha256", digest(self.binary))
        self.record_property = record_property
        self.errors = strict_json((REPO / "kicad_cli/driver_contract.json").read_bytes())[
            "reference"
        ]["error_codes"]

    def call(self, arguments, *, expected_exit):
        from bench.native_observation import inspect_trace
        from bench.persistence_acceptance.process_tree import observe

        assert digest(self.binary) == ARTIFACT_SHA256
        self.sdk.unchanged()
        output = self.scene.evidence / f"call-{len(self.calls) + 1:03d}"
        command = [str(self.binary), *arguments, "--compact"]
        before = {
            "protected": tree(self.scene.protected),
            "state_files": tree(self.scene.state),
            "state_logical": logical_state(self.scene.state),
        }
        ordinary_initialized_state(before["state_logical"], self.scene.scope_id)
        put(self.scene.evidence / f"call-{len(self.calls) + 1:03d}-BEFORE.json", before)
        put(
            self.scene.evidence / f"call-{len(self.calls) + 1:03d}-REQUEST.json",
            {"argv": command, "started_at": now()},
        )
        try:
            trace = observe(command, self.scene.root, self.scene.env, output, timeout=120)
            raw_trace = (output / "TRACE.json").read_bytes()
            generic = inspect_trace(output, allowed_exit_codes=(expected_exit,))
            assert (output / "TRACE.json").read_bytes() == raw_trace
            persisted = strict_json(raw_trace)
            policy = {
                "name": "7ded-operation-show-exact-sdk-refusal-v1",
                "generic_status": generic["status"],
                "applied": False,
                "admitted": False,
                "functional_output_verified": False,
            }
            put(output / "GENERIC-CLASSIFICATION.json", generic)
            # Only generic unexpected_exit can be qualified as ordinary SDK
            # worker propagation. Native/critical/incomplete decisions stop.
            assert generic["observation_complete"] and not generic["issues"]
            assert not generic["second_chance_notifications"]
            assert not generic["fail_fast_status_notifications"]
            assert generic["status"] in {
                "completed",
                "completed_with_forwarded_first_chance",
                "unexpected_exit",
            }
            processes = inspect_public_processes(persisted, command)
            if generic["status"] == "unexpected_exit":
                assert expected_exit in {2, 3} and persisted["exit"] == expected_exit
                policy.update(applied=True, admitted=True)
                classification = dict(
                    generic,
                    status="expected_sdk_refusal_propagated",
                    may_validate_functional_output=True,
                    stop_required=False,
                )
            else:
                classification = generic
            assert classification["may_validate_functional_output"]
            policy["verified_evidence"] = {
                "trace_sha256": hashlib.sha256(raw_trace).hexdigest(),
                "stdout_sha256": persisted["stdout_sha256"],
            }
            trace, stdout = verified_output(output, trace, policy)
            put(output / "PUBLIC-EXIT-POLICY.json", policy)
            put(output / "PROCESS-QUALIFICATION.json", processes)
            put(output / "CLASSIFICATION.json", classification)
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            put(self.scene.evidence / "OBSERVATION-STOP.json", {"at": now(), "error": repr(exc)})
            pytest.exit(
                "Stop this EXE cycle; preserved operation-show observation failure: " + repr(exc), 1
            )
        value = strict_json(stdout)
        assert type(value["ok"]) is bool
        self.sdk.validate("response", value)
        assert trace["exit"] == expected_exit
        if value["ok"]:
            assert trace["exit"] == 0
            assert set(value["data"]) == {"operation_status"}
            self.sdk.validate("operation-status", value["data"]["operation_status"])
        else:
            declaration = self.errors[value["error"]["code"]]
            assert trace["exit"] == declaration["exit"]
            assert value["error"]["retryable"] is declaration["retryable"]
        after = {
            "protected": tree(self.scene.protected),
            "state_files": tree(self.scene.state),
            "state_logical": logical_state(self.scene.state),
        }
        put(output / "AFTER.json", after)
        # Full protected input membership, bytes and timestamps must survive.
        assert after["protected"] == before["protected"]
        assert after["state_logical"] == before["state_logical"]
        assert after["state_files"]["members"] == before["state_files"]["members"]
        # DB bytes/mtime are observations, not a global no-SQL-write promise.
        put(
            output / "STATE-FILE-OBSERVATION.json",
            {
                "before": before["state_files"],
                "after": after["state_files"],
                "bytes_equal": {
                    name: row["sha256"] == after["state_files"]["files"][name]["sha256"]
                    for name, row in before["state_files"]["files"].items()
                },
                "mtime_equal": {
                    name: row["mtime_ns"] == after["state_files"]["files"][name]["mtime_ns"]
                    for name, row in before["state_files"]["files"].items()
                },
                "assertion": (
                    "Same logical operations/consumed_tokens/scope_usage and state members; "
                    "DB bytes/mtime separately observed."
                ),
            },
        )
        self.sdk.unchanged()
        call = {
            "argv": command,
            "exit": trace["exit"],
            "finished_at": now(),
            "trace_sha256": digest(output / "TRACE.json"),
            "stdout_sha256": digest(output / "stdout"),
            "stderr_sha256": digest(output / "stderr"),
            "whole_job": trace["job_finalization"],
        }
        put(output / "CALL-RESULT.json", call)
        self.calls.append(call)
        # candidate_direct_calls is supplied by candidate_regression's Popen
        # audit hook. Do not synthesize or overwrite that certification field.
        return trace["exit"], value


def make_scene(tmp_path, record_property):
    root = tmp_path.resolve(strict=True)
    protected = root / "protected"
    project = protected / "新建 SCH sync 输入 中文 空格"
    shutil.copytree(
        REPO / "tests/fixtures/sch_edit",
        project,
        ignore=shutil.ignore_patterns("*.py", "__pycache__"),
    )
    evidence, state = root / "evidence", root / "state"
    evidence.mkdir()
    state.mkdir()
    scope_id = "trial-operation-show-" + uuid.uuid4().hex
    scope = protected / "scope.json"
    put(
        scope,
        {
            "scope_id": scope_id,
            "tool": "kicad-cli",
            "granted_by": "Disposable operation-show contract test",
            "granted_at": now(),
            "expires_at": None,
            "effects": ["observe", "change", "persist"],
            "dangerous": False,
            "roots": [str(root)],
            "outputs": [],
            "notes": "Controlled SDK records only; no engineering authorization.",
        },
    )
    for folder in (
        "configuration/10.0",
        "candidate",
        "runtime/profile/Roaming",
        "runtime/profile/Local",
        "runtime/temp",
        "runtime/documents",
        "runtime/cache",
    ):
        (protected / folder).mkdir(parents=True, exist_ok=True)
    (protected / "configuration/10.0/kicad_common.json").write_text(
        '{"environment":{"vars":{}}}\n', encoding="utf-8"
    )
    (protected / "candidate/review.kicad_pcb").write_bytes(
        b"Controlled candidate sentinel; never consumed as CAD.\n"
    )
    put(
        protected / "candidate/review.manifest.json",
        {
            "fixture_only": True,
            "candidate_sha256": digest(protected / "candidate/review.kicad_pcb"),
        },
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        in {
            "SYSTEMROOT",
            "WINDIR",
            "SYSTEMDRIVE",
            "COMSPEC",
            "PATH",
            "PATHEXT",
            "PROCESSOR_ARCHITECTURE",
            "NUMBER_OF_PROCESSORS",
        }
    }
    sdk_path = os.environ["KICAD_CLI_ACCEPT_DRIVER_SDK"]
    sdk = FixedSDK(sdk_path)
    (protected / "runtime/empty-bytecode").mkdir()
    env.update(
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPYCACHEPREFIX=str(protected / "runtime/empty-bytecode"),
        PYTHONIOENCODING="utf-8",
        PYTHONHASHSEED="0",
        KICAD_CLI_DRIVER_SDK_PATH=str(sdk.directory),
        KICAD_CLI_STATE_DIR=str(state),
        KICAD_CLI_SCOPE_FILE=str(scope),
        KICAD_CONFIG_HOME=str(protected / "configuration"),
        KICAD_CLI_ROOT=str(protected / "no-native-installation"),
        KICAD_CLI_PYTHON=str(protected / "no-native-python.exe"),
        KICAD_CLI_OFFICIAL=str(protected / "no-official-kicad.exe"),
        USERPROFILE=str(protected / "runtime/profile"),
        APPDATA=str(protected / "runtime/profile/Roaming"),
        LOCALAPPDATA=str(protected / "runtime/profile/Local"),
        TEMP=str(protected / "runtime/temp"),
        TMP=str(protected / "runtime/temp"),
        KICAD_DOCUMENTS_HOME=str(protected / "runtime/documents"),
        KICAD_CACHE_HOME=str(protected / "runtime/cache"),
    )
    scene = SimpleNamespace(
        root=root,
        protected=protected,
        project=project,
        evidence=evidence,
        state=state,
        scope_id=scope_id,
        env=env,
        sdk=sdk,
    )
    scene.exe = TrialOperationExe(scene, sdk, record_property)
    put(
        evidence / "INPUT-BINDING.json",
        {
            "at": now(),
            "product_sha256": ARTIFACT_SHA256,
            "build_source_commit": BUILD_SOURCE,
            "fixed_specification_commit": SPEC_COMMIT,
            "fixed_sdk_spec_files": sdk.files,
            "environment": env,
            "fixture_method": (
                "Authenticated 9607 public StateStore.add_plan/dispatch/finish; no CAD execution"
            ),
            "harness": {
                str(Path(__file__).resolve()): digest(__file__),
                str(REPO / "tests/test_trial_operation_show_exe.py"): digest(
                    REPO / "tests/test_trial_operation_show_exe.py"
                ),
                str(REPO / "tests/move_exe_transport.py"): digest(
                    REPO / "tests/move_exe_transport.py"
                ),
            },
        },
    )
    return scene
