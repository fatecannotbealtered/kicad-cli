"""Strict external transport shared by bounded final-EXE move acceptance tests.

No tested writer or readback implementation is used to validate responses.
The declared, separately frozen specification supplies the offline JSON Schemas.
Complete debug events permit functional checking; they do not establish it.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource

REPO = Path(__file__).resolve().parents[1]
ARTIFACT_SHA256 = "21c46f431918e3b48263f7e49e7177f042eb1b300dba6cddfc062dc0d87a1225"
# Only these refusals occur in the frozen move acceptance. A different SDK
# exit code requires its own reviewed case; it is not implicitly admitted.
MOVE_REFUSAL_EXITS = frozenset({2, 3, 4, 6, 7})


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        assert key not in result, "Repeated public JSON member"
        result[key] = value
    return result


def _reject_constant(_value):
    raise AssertionError("Non-JSON numeric constant")


def _finite(value):
    number = float(value)
    assert math.isfinite(number), "Nonfinite JSON number"
    return number


def strict_json(raw):
    assert not raw.startswith(b"\xef\xbb\xbf"), "Public JSON must not contain a BOM"
    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_unique,
        parse_constant=_reject_constant,
        parse_float=_finite,
    )


def _offline_only(uri):
    raise NoSuchResource(ref=uri)


def windows_argv(command):
    """Parse an already recorded string with WinAPI; never execute that string."""
    import ctypes as C

    if os.name != "nt":
        raise OSError("CommandLineToArgvW requires Windows")
    if not isinstance(command, str) or not command or "\0" in command:
        raise ValueError("Missing or invalid process command line")
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
        return [pointer[i] for i in range(count.value)]
    finally:
        kernel.LocalFree(C.cast(pointer, C.c_void_p))


def _image_path(value, cwd):
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError("Missing or invalid image path")
    path = Path(value)
    return str((path if path.is_absolute() else Path(cwd) / path).resolve()).casefold()


def candidate_exit_decision(generic, trace, expected_argv, *, cwd, allowed_exit_codes):
    """Admit one exact PyInstaller child propagation after generic validation.

    All generic decisions except unexpected_exit are returned unchanged. This
    policy neither normalizes a trace nor changes any child exit/event. An
    admitted SDK refusal still needs the strict public Schema, error mapping,
    exact test expectation and independent engineering-protection checks.
    """
    policy = {
        "name": "exact-21c46-pyinstaller-sdk-refusal-v1",
        "generic_status": generic["status"],
        "applied": False,
        "admitted": False,
        "functional_output_verified": False,
        "issues": [],
    }
    if generic["status"] != "unexpected_exit":
        policy["decision"] = "generic_decision_preserved"
        return generic, policy
    policy["applied"] = True
    issues = []
    if (
        not generic.get("observation_complete")
        or generic.get("issues")
        or generic.get("second_chance_notifications")
        or generic.get("fail_fast_status_notifications")
        or generic.get("functional_output_verified") is not False
        or generic.get("may_validate_functional_output") is not False
        or not generic.get("stop_required")
    ):
        issues.append("generic_critical_evidence_not_eligible")
    processes = trace.get("processes", [])
    from bench.native_observation import root_process

    try:
        root = root_process(trace)
    except (KeyError, TypeError, ValueError):
        root = {}
        issues.append("unique_root_required")
    root_exit = root.get("exit")
    if (
        type(root_exit) is not int
        or root_exit not in MOVE_REFUSAL_EXITS
        or type(trace.get("exit")) is not int
        or trace.get("exit") != root_exit
    ):
        issues.append("root_not_frozen_move_refusal_exit")
    if root_exit not in allowed_exit_codes:
        issues.append("root_exit_not_allowed_by_invocation")
    if root.get("image_sha256") != ARTIFACT_SHA256:
        issues.append("root_candidate_sha256_mismatch")
    children = [p for p in processes if p is not root]
    nonzero = [p for p in children if p.get("exit") != 0]
    if any(type(p.get("exit")) is not int for p in children):
        issues.append("child_exit_not_integer")
    if len(nonzero) != 1:
        issues.append("exactly_one_nonzero_child_required")
    child = nonzero[0] if len(nonzero) == 1 else {}
    if child.get("image_sha256") != ARTIFACT_SHA256:
        issues.append("child_candidate_sha256_mismatch")
    if type(child.get("exit")) is not int or child.get("exit") != root_exit:
        issues.append("child_exit_does_not_match_root")
    metadata = child.get("metadata", {})
    if type(metadata.get("ParentProcessId")) is not int or metadata.get(
        "ParentProcessId"
    ) != root.get("pid"):
        issues.append("candidate_child_not_direct_root_child")
    if trace.get("observation_schema") == 3 and child.get("parent_process_instance_id") != root.get(
        "process_instance_id"
    ):
        issues.append("candidate_child_parent_instance_differs")
    root_birth, child_birth = root.get("creation_filetime"), child.get("creation_filetime")
    if (
        type(root_birth) is not int
        or type(child_birth) is not int
        or root_birth <= 0
        or child_birth < root_birth
    ):
        issues.append("candidate_child_birth_not_bound_after_root")
    try:
        expected = list(expected_argv)
        if (
            not expected
            or not expected[0]
            or any(not isinstance(v, str) or "\0" in v for v in expected)
        ):
            raise ValueError("Missing reviewed invocation")
        if expected[1:4] != ["board", "footprint", "move"]:
            issues.append("not_frozen_move_command")
        image = _image_path(root.get("image"), cwd)
        if image != _image_path(expected[0], cwd):
            issues.append("root_image_differs_from_reviewed_invocation")
        if image != _image_path(child.get("image"), cwd):
            issues.append("child_image_differs_from_root")
        candidates = [
            row
            for row in children
            if row.get("image_sha256") == ARTIFACT_SHA256
            or _image_path(row.get("image"), cwd) == image
        ]
        if len(candidates) != 1 or candidates[0] is not child:
            issues.append("exactly_one_candidate_child_identity_required")
        for label, process in (("root", root), ("child", child)):
            actual = windows_argv(process.get("metadata", {}).get("CommandLine"))
            if not actual or _image_path(actual[0], cwd) != image or actual[1:] != expected[1:]:
                issues.append(label + "_command_differs_from_reviewed_invocation")
    except (OSError, TypeError, ValueError):
        issues.append("candidate_command_or_image_parse_incomplete")
    policy["issues"] = sorted(set(issues))
    if issues:
        policy["decision"] = "candidate_policy_refused"
        return generic, policy
    policy.update(
        admitted=True,
        decision="one_exact_candidate_child_propagated_expected_sdk_refusal",
        root_pid=root["pid"],
        child_pid=child["pid"],
        root_process_instance_id=root.get("process_instance_id"),
        child_process_instance_id=child.get("process_instance_id"),
        sdk_exit=root_exit,
    )
    decision = dict(
        generic,
        status="candidate_expected_sdk_exit",
        may_validate_functional_output=True,
        stop_required=False,
        functional_output_verified=False,
        interpretation=(
            "Generic complete observation retained unexpected_exit. One direct same-image "
            "21c46 PyInstaller child propagated the same frozen move SDK refusal code. "
            "Validate exact output, persistence and engineering protection independently."
        ),
    )
    return decision, policy


def inspect_candidate_trace(directory, expected_argv, *, cwd, allowed_exit_codes):
    from bench.native_observation import inspect_trace

    trace_path = Path(directory) / "TRACE.json"
    before = trace_path.read_bytes()
    allowed_exit_codes = tuple(allowed_exit_codes)
    generic = inspect_trace(directory, allowed_exit_codes=allowed_exit_codes)
    after = trace_path.read_bytes()
    if before != after:
        generic = dict(
            generic,
            inspection_status_before_trace_drift=generic["status"],
            status="observation_incomplete",
            observation_complete=False,
            may_validate_functional_output=False,
            stop_required=True,
            functional_output_verified=False,
            issues=sorted(set([*generic["issues"], "trace_changed_during_generic_inspection"])),
        )
    # Critical/missing/fatal statuses remain authoritative. The unchanged raw
    # trace is read only after the generic persisted-hash/event checks finish.
    trace = strict_json(after) if before == after else {}
    decision, policy = candidate_exit_decision(
        generic, trace, expected_argv, cwd=cwd, allowed_exit_codes=allowed_exit_codes
    )
    if before == after and generic["observation_complete"]:
        policy["verified_evidence"] = {
            "trace_sha256": hashlib.sha256(after).hexdigest(),
            "stdout_sha256": trace["stdout_sha256"],
        }
    return decision, policy


class ObservationBindingError(RuntimeError):
    """A required observed input changed before functional output validation."""


def _same_json_value(left, right):
    # Do not silently normalize tuple/list, integer/boolean or other Python-only
    # differences between observe's result and its persisted JSON observation.
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _same_json_value(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_json_value(a, b) for a, b in zip(left, right, strict=True)
        )
    return left == right


def verified_output(directory, returned_trace, policy):
    """Bind the actual Schema input and observe result to the inspected bytes."""
    directory = Path(directory)
    binding = policy.get("verified_evidence", {})
    trace_bytes = (directory / "TRACE.json").read_bytes()
    if hashlib.sha256(trace_bytes).hexdigest() != binding.get("trace_sha256"):
        raise ObservationBindingError("verified_trace_bytes_changed")
    trace = strict_json(trace_bytes)
    if not _same_json_value(returned_trace, trace):
        raise ObservationBindingError("returned_trace_differs_from_verified_json_trace")
    stdout = (directory / "stdout").read_bytes()
    if hashlib.sha256(stdout).hexdigest() != binding.get("stdout_sha256") or binding.get(
        "stdout_sha256"
    ) != trace.get("stdout_sha256"):
        raise ObservationBindingError("verified_stdout_hash_mismatch")
    if (directory / "TRACE.json").read_bytes() != trace_bytes:
        raise ObservationBindingError("verified_trace_changed_during_output_read")
    return trace, stdout


class FinalMoveExe:
    """Use only the explicitly selected immutable candidate in a new owned root."""

    def __init__(self, binary, cwd, env, output, record_property):
        assert os.name == "nt", "Owned instance-bound Windows observations are required"
        self.binary = Path(binary).resolve(strict=True)
        assert digest(self.binary) == ARTIFACT_SHA256
        record_property("fcc_artifact_sha256", ARTIFACT_SHA256)
        self.cwd, self.env = Path(cwd).resolve(strict=True), dict(env)
        self.output = Path(output)
        self.output.mkdir(exist_ok=False)
        self.index = 0
        sdk = Path(self.env["KICAD_CLI_DRIVER_SDK_PATH"])
        assert sdk.is_absolute() and sdk.is_dir()
        spec = sdk.parent / "driver_spec"
        self.schemas = {}
        registry = Registry(retrieve=_offline_only)
        for path in sorted((spec / "schemas").glob("*.schema.json")):
            value = strict_json(path.read_bytes())
            Draft202012Validator.check_schema(value)
            identity = value["$id"]
            assert identity not in self.schemas
            self.schemas[identity] = value
            registry = registry.with_resource(identity, Resource.from_contents(value))
        self.validator = Draft202012Validator(
            self.schemas["urn:tool-driver-cli-spec:response:2.0"],
            registry=registry,
            format_checker=FormatChecker(),
        )
        self.errors = strict_json((REPO / "kicad_cli/driver_contract.json").read_bytes())[
            "reference"
        ]["error_codes"]
        self.allowed_exit_codes = tuple(sorted({0, *(v["exit"] for v in self.errors.values())}))

    def call(self, *arguments):
        from bench.persistence_acceptance.process_tree import observe

        # The artifact is rechecked for each dispatch, in addition to the runner's
        # frozen external-artifact manifest and execution-cycle revalidation.
        assert digest(self.binary) == ARTIFACT_SHA256
        self.index += 1
        output = self.output / f"{self.index:03d}"
        command = [str(self.binary), *map(str, arguments), "--compact"]
        try:
            trace = observe(
                command,
                self.cwd,
                self.env,
                output,
                timeout=120,
            )
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            pytest.exit("Stop this EXE cycle; preserved observation failure: " + repr(exc), 1)
        classification, policy = inspect_candidate_trace(
            output, command, cwd=self.cwd, allowed_exit_codes=self.allowed_exit_codes
        )
        if classification["may_validate_functional_output"]:
            try:
                trace, stdout = verified_output(output, trace, policy)
            except (ObservationBindingError, OSError, AssertionError, ValueError) as exc:
                classification = dict(
                    classification,
                    status="observation_incomplete",
                    observation_complete=False,
                    may_validate_functional_output=False,
                    stop_required=True,
                    functional_output_verified=False,
                    issues=sorted(
                        set([*classification["issues"], "functional_input_binding_failed"])
                    ),
                )
                policy["functional_input_binding_failure"] = str(exc)
        (output / "CLASSIFICATION.json").write_text(
            json.dumps(
                dict(
                    classification, generic_status=policy["generic_status"], candidate_policy=policy
                ),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if not classification["may_validate_functional_output"]:
            pytest.exit("Stop this EXE cycle; preserved fatal/incomplete observation", 1)
        value = strict_json(stdout)
        self.validator.validate(value)
        assert value["schema_version"] == "1.0" and type(value["ok"]) is bool
        if value["ok"]:
            assert trace["exit"] == 0
        else:
            declared = self.errors[value["error"]["code"]]
            assert trace["exit"] == declared["exit"]
            assert type(value["error"]["retryable"]) is bool
            assert value["error"]["retryable"] is declared["retryable"]
        return trace["exit"], value
