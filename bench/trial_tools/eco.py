"""Finite public C10 ECO orchestration, with durable, non-replaying phases.

Only the selected product executable can change engineering files, through
public sch edit and project sync --candidate-out. All raw replies, authority
tokens, scope and observer traces stay in the executor's owned work directory.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import os
import sqlite3
import sys
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import preparation as core
from .identity import IdentityError, capture, ordinary_path, verify_aliases, verify_inventory
from .reading import STEM, VIEWS, Readers, ident, load, qualify_runtime, require, write_new

PRODUCT_SHA256 = "7ded617e0a06efba811bd50b2ea5ab6c12ee0b1bd879121920d1cdb0f95ceb98"
OLD_FP = b"Capacitor_SMD:C_0805_2012Metric"
NEW_FP = b"Capacitor_SMD:C_0603_1608Metric"
SCHEMA = "packaged-public-c10-trial/v1"


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def require_repeat_helpers(ready) -> list[dict[str, Any]]:
    """Require both finite helper qualifications in the original READY."""
    recorded = ready.record.get("public_refusal_helpers", [])
    declared = core._helper_declarations(
        ready.record["request"]["config"], ready.record["dependencies"]
    )
    wanted = {("conhost.exe", "root"), ("cmd.exe", "worker")}
    present = {(Path(row["path"]).name.casefold(), row["parent"]) for row in recorded}
    if recorded != declared or present != wanted or len(recorded) != 2:
        raise core.TrialError(
            "E_TRIAL_HELPER_QUALIFICATION",
            "Original READY lacks the two prospective SDK refusal helper qualifications",
            details={
                "missing": [name + ":" + parent for name, parent in sorted(wanted - present)],
                "new_processes": 0,
                "formal_cad_writes": 0,
                "action": "Preserve this READY; do not add qualifications to it. "
                "Use a separately prepared new plan for future work.",
            },
        )
    return recorded


def unexpired(reply: Mapping[str, Any], *, now: datetime | None = None) -> None:
    try:
        text = reply["data"]["expires_at"]
        end = datetime.fromisoformat(text.replace("Z", "+00:00"))
        require(end.tzinfo is not None, "Preview expiry lacks a timezone")
        require(
            (now or datetime.now(timezone.utc)) < end,
            "Original preview expired; do not confirm or renew it",
        )
    except (KeyError, TypeError, ValueError) as error:
        raise core.TrialError("E_TRIAL_EXPIRED", str(error)) from error


def exact_one_file_change(
    before: Mapping[str, Any], after: Mapping[str, Any], name: str, expected_sha256: str
) -> bool:
    """Permit one declared product replacement, protecting all other identities."""
    if (
        before.get("kind") != "tree"
        or after.get("kind") != "tree"
        or before.get("root") != after.get("root")
        or before.get("directories") != after.get("directories")
        or set(before["files"]) != set(after["files"])
        or name not in after["files"]
    ):
        return False
    return after["files"][name]["sha256"] == expected_sha256 and all(
        before["files"][key] == after["files"][key] for key in before["files"] if key != name
    )


def verify_refusal_transport(result, ready, argv, declaration) -> dict[str, Any]:
    """Bind the Core's finite transport decision to its unchanged raw artifacts.

    Core proves exact argv, birth and parent identities. This consumer checks
    the recorded policy, hashes, classifications and finite admitted image set;
    JSON refusal and unchanged state remain separately required by the phase.
    """
    out = Path(result["observer_directory"])
    trace, generic = result["trace"], result["generic_classification"]
    adapter, final = result["public_refusal_adapter"], result["classification"]
    require(
        load(out / "TRACE.json") == trace
        and ident(out / "TRACE.json")["sha256"] == result["trace_sha256"],
        "Refusal raw trace binding differs",
    )
    require(
        ident(out / "stdout")["sha256"] == result["stdout_sha256"] == trace["stdout_sha256"],
        "Refusal raw stdout binding differs",
    )
    require(
        load(out / "GENERIC-CLASSIFICATION.json") == generic
        and load(out / "CLASSIFICATION.json") == final
        and load(out / "PUBLIC-REFUSAL-ADAPTER.json") == adapter,
        "Refusal classification/adapter artifact binding differs",
    )
    policy = load(Path(result["execution_policy"]))
    helpers = ready.record["public_refusal_helpers"]
    require(
        policy["argv"] == list(map(str, argv))
        and policy["expected_exit_codes"] == [6]
        and policy["expected_public_refusal"] == declaration
        and policy["public_refusal_helpers"] == helpers,
        "Refusal did not use the prospectively recorded exact policy",
    )
    require(
        adapter["name"] == "exact-7ded-project-sync-token-used-sdk-refusal/v1"
        and adapter["applied"] is True
        and adapter["declaration"] == declaration
        and adapter["optional_helpers"] == helpers
        and adapter["generic_status"] == generic.get("status")
        and adapter["functional_output_verified"] is False
        and final["functional_output_verified"] is False
        and final["observation_complete"] is True
        and final["may_validate_functional_output"] is True
        and final["stop_required"] is False,
        "Refusal transport policy is incomplete",
    )
    product = ready.record["dependencies"]["paths"]["product_exe"]
    product_path = os.path.normcase(str(ordinary_path(product)))
    processes = trace.get("processes", [])
    require(processes, "Refusal process set is missing")
    helper_paths = []
    if adapter["admitted"] is True:
        require(
            not adapter["issues"]
            and generic["status"] == "unexpected_exit"
            and final["status"] == "exact_public_sdk_refusal_transport"
            and adapter["expected_sdk_exit"] == 6,
            "Exact refusal adapter did not admit this observation",
        )
        roots = [
            row
            for row in processes
            if row.get("process_instance_id")
            in (adapter["root_instance"], adapter["worker_instance"])
        ]
        require(
            len(roots) == 2 and adapter["root_instance"] != adapter["worker_instance"],
            "Refusal root/worker identities are incomplete",
        )
        declared = {os.path.normcase(str(ordinary_path(row["path"]))): row for row in helpers}
        for row in processes:
            path = os.path.normcase(str(ordinary_path(row["image"])))
            if row in roots:
                require(
                    path == product_path and row.get("image_sha256") == PRODUCT_SHA256,
                    "Refusal root/worker product identity differs",
                )
            else:
                require(
                    path in declared
                    and path not in helper_paths
                    and row.get("image_sha256") == declared[path]["identity"]["sha256"]
                    and row.get("exit") == 0,
                    "Unexpected, repeated or changed helper/Native process in refusal",
                )
                helper_paths.append(path)
    else:
        # A generic observation can already be complete when no nonzero child
        # exception requires adaptation. It must retain the all-product boundary.
        require(
            final == generic
            and all(
                os.path.normcase(str(ordinary_path(row["image"]))) == product_path
                and row.get("image_sha256") == PRODUCT_SHA256
                for row in processes
            ),
            "Generic refusal contains an unqualified helper or Native process",
        )
    return {
        "trace": ident(out / "TRACE.json"),
        "stdout": ident(out / "stdout"),
        "generic": ident(out / "GENERIC-CLASSIFICATION.json"),
        "classification": ident(out / "CLASSIFICATION.json"),
        "adapter": ident(out / "PUBLIC-REFUSAL-ADAPTER.json"),
        "execution_policy": ident(result["execution_policy"]),
        "admitted_helpers": helper_paths,
        "functional_json_checked_separately": True,
    }


def _dependency_failure(name, path, *, reason, expected=None, actual=None):
    """Construct finite public preparation diagnostics, without exception dumps."""
    row = {
        "name": name,
        "path": str(path),
        "reason": reason,
        "repair": (
            "Restore the explicitly declared provider at this path, or correct its "
            "prospective path_map; then use a new owned root and new plan."
        ),
    }
    for key, value in (("expected", expected), ("actual", actual)):
        if isinstance(value, Mapping):
            row[key] = {field: value[field] for field in ("sha256", "size") if field in value}
    return core.TrialError(
        "E_TRIAL_DEPENDENCY", "A prospective provider is unavailable or changed", details=[row]
    )


def _provider_identity(name, path, expected=None):
    try:
        actual = ident(path)
    except (OSError, IdentityError, ValueError) as error:
        raise _dependency_failure(
            name,
            path,
            reason="Provider is missing, unreadable or has an unsupported file identity.",
            expected=expected,
        ) from error
    if expected is not None and any(actual[key] != expected[key] for key in ("sha256", "size")):
        raise _dependency_failure(
            name,
            path,
            reason="Provider bytes differ from the declared pin.",
            expected=expected,
            actual=actual,
        )
    return actual


def _alias_error_path(error, fallback):
    if isinstance(error, OSError) and isinstance(error.filename, str):
        return error.filename
    details = getattr(error, "details", None)
    if isinstance(details, str) and Path(details).is_absolute():
        return details
    if isinstance(details, Mapping):
        for key in ("alias", "path", "canonical"):
            if isinstance(details.get(key), str) and Path(details[key]).is_absolute():
                return details[key]
    return fallback


def _qualified_runtime_dependencies(package, path_map, groups):
    """Translate current external-provider failures before method qualification."""
    reading = package / "trial_tools/resources/reading-tools"
    runtime, system = load(reading / "RUNTIME-123.json"), load(reading / "SYSTEM-52.json")
    paths = set()
    for row in [runtime["node"], *runtime["files"], *system["files"]]:
        path = path_map.get(row["path"], row["path"])
        paths.add(str(path))
        _provider_identity("renderer_provider", path, row)
    for group in groups:
        if (
            not isinstance(group, Mapping)
            or not isinstance(group.get("canonical"), str)
            or not isinstance(group.get("aliases"), (list, tuple))
            or any(not isinstance(path, str) for path in group["aliases"])
        ):
            raise core.TrialError("E_TRIAL_CONFIG", "Renderer alias declaration is malformed")
        try:
            verify_aliases([group])
        except (OSError, IdentityError, ValueError) as error:
            raise _dependency_failure(
                "renderer_alias",
                _alias_error_path(error, group["canonical"]),
                reason="Current alias file identity, size or SHA proof failed.",
            ) from error
    try:
        return qualify_runtime(package, path_map, groups)
    except (OSError, IdentityError) as error:
        path = _alias_error_path(error, None)
        if path is not None and str(path) in paths:
            raise _dependency_failure(
                "renderer_provider", path, reason="Runtime provider changed during qualification."
            ) from error
        raise
    except ValueError as error:
        # Only the qualifier's finite provider-specific messages are translated.
        for prefix in (
            "Historical renderer provider bytes differ: ",
            "Current extended renderer alias proof missing: ",
        ):
            if str(error).startswith(prefix) and str(error)[len(prefix) :] in paths:
                raise _dependency_failure(
                    "renderer_provider",
                    str(error)[len(prefix) :],
                    reason="Runtime provider or alias proof changed.",
                ) from error
        raise


def c10_source_edit_binding(raw: bytes, checker_path: Path, baseline_raw: bytes) -> dict[str, Any]:
    """Locate one real C10 instance/property and calculate its sole byte edit.

    The packaged independent checker already has a read-only, exact-span Sexpr
    parser. Load that method, never a product module. Nothing here writes CAD;
    the public sch edit command remains the only engineering editor.
    """
    namespace = {"__file__": str(checker_path), "__name__": "_trial_c10_span_reader"}
    exec(compile(checker_path.read_bytes(), str(checker_path), "exec"), namespace)
    parse, head = namespace["parse"], namespace["head"]
    children, one, val, atom = (namespace[name] for name in ("children", "one", "val", "Atom"))

    def quoted(value):
        require(
            isinstance(value, atom) and value.raw.startswith(b'"') and value.raw.endswith(b'"'),
            "Quoted source identity/property atom required",
        )
        return value.value

    def canonical_uuid(value):
        require(
            isinstance(value, str) and str(uuid.UUID(value)) == value,
            "Canonical source UUID required",
        )
        return value

    def locate(source):
        tree = parse(source)
        require(head(tree) == "kicad_sch", "Expected a complete KiCad schematic root")
        seen = set()

        def unique_uuids(node):
            if head(node) == "uuid":
                require(len(node) == 2, "Malformed source UUID")
                identity = canonical_uuid(quoted(node[1]))
                require(identity not in seen, "Duplicate source UUID")
                seen.add(identity)
            for child in node:
                if isinstance(child, list):
                    unique_uuids(child)

        unique_uuids(tree)
        targets = []
        # lib_symbols contains definitions, including nested symbols and generic
        # Reference fields. Only immediate root children are placed instances.
        for symbol in children(tree, "symbol"):
            properties = {}
            for prop in children(symbol, "property"):
                require(len(prop) >= 3, "Malformed placed-symbol property")
                name = quoted(prop[1])
                require(name not in properties, "Duplicate placed-symbol property " + name)
                quoted(prop[2])
                properties[name] = prop
            require("Reference" in properties, "Placed symbol has no unique Reference")
            if quoted(properties["Reference"][2]) == "C10":
                targets.append((symbol, properties))
        require(len(targets) == 1, "C10 placed instance is missing or ambiguous")
        symbol, props = targets[0]
        identity = canonical_uuid(val(symbol, "uuid"))
        require(
            val(symbol, "lib_id") == "Device:C" and val(symbol, "unit") == "1",
            "C10 is not the declared Device:C unit1 instance",
        )
        require(
            "Value" in props and quoted(props["Value"][2]) == "10n",
            "C10 value differs from the frozen 10n intent",
        )
        instances = one(symbol, "instances")
        project = one(instances, "project")
        require(
            len(project) >= 3 and quoted(project[1]) == STEM,
            "C10 instance project differs or is malformed",
        )
        path = one(project, "path")
        require(len(path) >= 4, "C10 instance path is incomplete")
        instance_path = quoted(path[1])
        path_parts = instance_path.split("/")
        require(
            len(path_parts) == 3 and path_parts[0] == "",
            "C10 requires the frozen two-level hierarchy path",
        )
        for component in path_parts[1:]:
            canonical_uuid(component)
        require(
            val(path, "reference") == "C10" and val(path, "unit") == "1",
            "C10 instance Reference/unit differs from its placed symbol",
        )
        require("Footprint" in props, "C10 Footprint property is missing")
        prop = props["Footprint"]
        token = prop[2]
        require(
            quoted(token) == OLD_FP.decode() and token.raw == b'"' + OLD_FP + b'"',
            "C10 Footprint is not the exact declared 0805 atom",
        )
        require(
            source[token.start : token.end] == token.raw,
            "Source parser did not return exact UTF-8 byte offsets",
        )
        return {
            "symbol_uuid": identity,
            "reference": "C10",
            "lib_id": "Device:C",
            "unit": 1,
            "instance": {"project": STEM, "path": instance_path, "reference": "C10", "unit": 1},
            "symbol_span": [symbol.start, symbol.end],
            "reference_property_span": [props["Reference"].start, props["Reference"].end],
            "footprint_property_span": [prop.start, prop.end],
            "footprint_value_span": [token.start, token.end],
        }

    expected, target = locate(baseline_raw), locate(raw)
    require(
        all(
            target[key] == expected[key]
            for key in ("symbol_uuid", "reference", "lib_id", "unit", "instance")
        ),
        "C10 target identity differs from the immutable original",
    )
    start, end = target["footprint_value_span"]
    prop_start, prop_end = target["footprint_property_span"]
    replacement = b'"' + NEW_FP + b'"'
    predicted = raw[:start] + replacement + raw[end:]
    return {
        "schema": "trial-exact-c10-source-footprint-binding/v1",
        "source": "channel_a.kicad_sch",
        "source_sha256": digest(raw),
        "baseline_sha256": digest(baseline_raw),
        "span_units": "UTF-8 byte offsets, half-open; parser tokenizes bytes",
        "span_reader": ident(checker_path),
        "target": target,
        "old_value": OLD_FP.decode(),
        "new_value": NEW_FP.decode(),
        "old_property_sha256": digest(raw[prop_start:prop_end]),
        "old_property_base64": base64.b64encode(raw[prop_start:prop_end]).decode("ascii"),
        "before_token_sha256": digest(raw[start:end]),
        "after_token_utf8": replacement.decode(),
        "expected_sha256": digest(predicted),
        "protected_bytes_sha256": digest(raw[:start] + raw[end:]),
        "public_request": {"op": "set", "ref": "C10", "footprint": NEW_FP.decode()},
        "engineering_files_written": 0,
    }


def qualify_provider_errata(package, dependencies, config, selected, expected_files, path_map):
    """Qualify seven explicit stable providers before any new consumer starts.

    This supplements new requests only. It cannot update an existing READY or
    retroactively qualify the previously observed CAD process chain.
    """
    manifest = load(package / "trial_tools/resources/TRIAL-PROVIDER-ERRATA.json")
    labels = {
        "cmd.exe",
        "msctf.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
        "wldp.dll",
        "native/_ctypes.pyd",
        "native/ffi-8.dll",
    }
    rows = manifest.get("files", [])
    if (
        manifest.get("schema") != "prospective-trial-provider-errata/v1"
        or not isinstance(rows, list)
        or len(rows) != len(labels)
        or any(
            not isinstance(row, dict)
            or not isinstance(row.get("label"), str)
            or not isinstance(row.get("path"), str)
            or not Path(row["path"]).is_absolute()
            for row in rows
        )
        or {row.get("label") for row in rows} != labels
    ):
        raise core.TrialError("E_TRIAL_CONFIG", "The finite seven-provider errata is incomplete")
    identities = {}
    for row in rows:
        path = path_map.get(row["path"], row["path"])
        observed = _provider_identity(row["label"], path, row)
        selected.add(str(path))
        expected_files[str(path)] = {key: row[key] for key in ("sha256", "size")}
        identities[row["label"]] = observed
    helpers = copy.deepcopy(manifest["public_refusal_helpers"])
    for helper in helpers:
        old = helper["path"]
        helper["path"] = path_map.get(old, old)
        if helper["parent"] == "root":
            helper["argv"][0] = "\\??\\" + helper["path"]
        else:
            helper["argv"][0] = helper["path"]
    config.setdefault("public_refusal_helpers", helpers)
    dependencies["prospective_provider_errata"] = {
        "manifest": ident(package / "trial_tools/resources/TRIAL-PROVIDER-ERRATA.json"),
        "files": identities,
        "scope": "New request only; no historical READY or CAD provenance upgrade",
    }


def complete_request(
    package: Path, dependencies: Mapping[str, Any], config: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Make all packaged consumers prospective before the first preparation call.

    path_map is an explicit per-file relocation map for the finite packaged
    external-provider declaration. No old directory, controller or result is
    opened. Unknown/missing/mismatched providers refuse before Native startup.
    """
    deps, conf = copy.deepcopy(dict(dependencies)), copy.deepcopy(dict(config))
    if conf.get("renderer_warmup", True) is not True:
        raise core.TrialError("E_TRIAL_CONFIG", "Packaged renderer preparation cannot be disabled")
    conf["renderer_warmup"] = True
    resources = package / "trial_tools/resources"
    path_map = deps.get("path_map", {})
    if not isinstance(path_map, Mapping):
        raise core.TrialError("E_TRIAL_CONFIG", "path_map must be an explicit per-file mapping")
    selected = set(map(str, deps.get("dependency_files", ())))
    expected_files = copy.deepcopy(dict(deps.get("expected_files", {})))
    for row in load(resources / "EXTERNAL-DEPENDENCIES.json")["files"]:
        path = path_map.get(row["path"], row["path"])
        _provider_identity("external_provider", path, row)
        selected.add(str(path))
        expected_files[str(path)] = {key: row[key] for key in ("sha256", "size")}
    deps.setdefault("host_python", str(Path(sys.executable).absolute()))
    selected.add(deps["host_python"])
    # The currently running Host is observed prospectively, without inventing
    # historical qualification for its interpreter or sweeping its stdlib.
    deps["caller_host_observation"] = _provider_identity(
        "caller_host", Path(sys.executable).absolute()
    )
    selected.add(deps["caller_host_observation"]["path"])
    groups = list(deps.get("aliases", ()))
    # Prior paths provide declarations only. The preparation identity backend
    # establishes fresh current file-ID/size/SHA proof; no historical PASS is used.
    for group in load(resources / "PRIOR-ALIAS-PROJECTION.json")["alias_groups"]:
        canonical = path_map.get(group["selected_for_package"], group["selected_for_package"])
        aliases = [
            path_map.get(row["path"], row["path"])
            for row in group["representations"]
            if row["path"] != group["selected_for_package"]
        ]
        declared = {"canonical": canonical, "aliases": aliases}
        if declared not in groups:
            groups.append(declared)
    deps["aliases"] = groups
    runtime = _qualified_runtime_dependencies(package, path_map, groups)
    # Caller-supplied runtime metadata cannot replace this current qualification.
    conf["renderer_runtime"] = runtime
    for row in [runtime["node"], *runtime["runtime_files"], *runtime["system_files"]]:
        selected.add(row["path"])
        # qualify_runtime has compared every row to exact historical manifests.
        expected_files[row["path"]] = {key: row[key] for key in ("sha256", "size")}
    qualify_provider_errata(package, deps, conf, selected, expected_files, path_map)
    deps["dependency_files"] = sorted(selected)
    deps["expected_files"] = expected_files
    seeds = conf.setdefault("seed_files", {})
    for profile in ("writer", "consumer"):
        target = seeds.setdefault(profile, {})
        for name in ("fp-lib-table", "sym-lib-table"):
            target.setdefault("10.0/" + name, str(resources / "seeds" / name))
    methods = set(map(str, conf.get("method_files", ())))
    for root in (package / "trial_tools", package / "physical-footprint-eco/methods"):
        methods.update(
            str(path.relative_to(package))
            for path in root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        )
    methods.update(
        "windows-internal-trial/" + name
        for name in (
            "CRITERIA.md",
            "RENDERER_PREPARATION_ADDENDUM.md",
            "READONLY_CONTINUATION_CRITERIA.md",
        )
    )
    conf["method_files"] = sorted(methods)
    environment = conf.setdefault("environment", {})
    if environment.get("NODE_OPTIONS") or environment.get("NODE_PATH"):
        raise core.TrialError("E_TRIAL_CONFIG", "Ambient Node injection refused")
    environment.update(
        NODE_OPTIONS="",
        NODE_PATH="",
        NODE_DISABLE_COMPILE_CACHE="1",
        SHARP_IGNORE_GLOBAL_LIBVIPS="1",
    )
    return deps, conf


class Trial:
    def __init__(self, work_root: str | Path, *, _runner=None):
        self.ready = core.load_ready(work_root)
        self.work, self.package = self.ready.work_root, self.ready.package_root
        self.state = self.work / "method-state"
        self._runner = _runner
        self.context = None
        self.dependencies = dict(self.ready.record["dependencies"]["paths"])
        self.dependencies["host_python"] = self.ready.record["request"]["dependencies"].get(
            "host_python", sys.executable
        )

    def _context_path(self) -> Path:
        return self.state / "CONTEXT.json"

    def _save_context(self) -> None:
        # Mutable bookkeeping is owned state, never an engineering input or an
        # approval. Formal inputs and phase artifacts retain separate identities.
        path = self._context_path()
        temp = path.with_name("CONTEXT-" + uuid.uuid4().hex + ".tmp")
        write_new(temp, self.context.as_dict())
        os.replace(temp, path)

    def _load_context(self) -> None:
        self.context = core.FrozenContext.from_dict(load(self._context_path()))

    def bind(self, label: str, path: str | Path) -> None:
        core.verify_ready(self.ready, inputs=self.context.inputs)
        value = capture(path)
        if label in self.context.inputs:
            require(self.context.inputs[label] == value, "Bound input identity changed: " + label)
        self.context.inputs[label] = value
        self._save_context()

    def _phase(self, name: str) -> dict[str, Any] | None:
        self._load_context()
        finished = self.state / (name + ".complete.json")
        if finished.exists():
            value = load(finished)
            for row in value.get("bound_outputs", []):
                verify_inventory(row)
            return {**value["summary"], "reused_checkpoint": True, "new_processes": 0}
        attempted = self.state / (name + ".attempt.json")
        if attempted.exists():
            raise core.TrialError(
                "E_TRIAL_NO_REPLAY",
                "Phase started without completion; preserve it and query the original operation",
                details={"phase": name},
            )
        return None

    def _attempt(self, name: str) -> None:
        write_new(
            self.state / (name + ".attempt.json"),
            {"schema": SCHEMA, "phase": name, "started_at": stamp(), "automatic_retry": False},
        )

    def _finish(self, name: str, summary: Mapping[str, Any], outputs=()) -> dict[str, Any]:
        core.verify_ready(self.ready, inputs=self.context.inputs)
        result = {
            "schema": SCHEMA,
            "phase": name,
            "completed_at": stamp(),
            "summary": dict(summary),
            "bound_outputs": [capture(x) for x in outputs],
        }
        write_new(self.state / (name + ".complete.json"), result)
        self.bind("checkpoint:" + name, self.state / (name + ".complete.json"))
        return dict(summary)

    def run(
        self,
        name: str,
        argv,
        *,
        profile="consumer",
        expected_outputs=(),
        allowed=None,
        expected_exit_codes=(0,),
        expected_public_refusal=None,
    ):
        out = self.work / "logs" / "formal" / name
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = core.guarded_run(
                argv,
                context=self.context,
                profile=profile,
                cwd=self.work,
                out=out,
                timeout=120,
                expected_outputs=expected_outputs,
                allowed_input_changes=allowed or {},
                expected_exit_codes=expected_exit_codes,
                expected_public_refusal=expected_public_refusal,
                _runner=self._runner,
            )
        finally:
            self._save_context()
        result["stdout_path"] = str(out / "stdout")
        for path in expected_outputs:
            self.bind(str(path), path)
        return result

    def product(self, name: str, arguments, *, allowed=None) -> dict[str, Any]:
        result = self.run(
            name,
            [self.dependencies["product_exe"], *arguments, "--compact"],
            profile="writer",
            allowed=allowed,
        )
        reply = load(Path(result["stdout_path"]))
        require(reply.get("ok") is True, "Public product command refused: " + name)
        path = self.state / (name + ".reply.json")
        write_new(path, reply)
        self.bind("reply:" + name, path)
        return reply

    def _edit_args(self):
        return [
            "sch",
            "edit",
            "--schematic",
            self.work / "project" / (STEM + ".kicad_sch"),
            "--changes",
            self.work / "intent/changes.json",
        ]

    def _sync_args(self):
        return [
            "project",
            "sync",
            "--board",
            self.work / "project" / (STEM + ".kicad_pcb"),
            "--schematic",
            self.work / "project" / (STEM + ".kicad_sch"),
            "--candidate-out",
            self.work / "review" / (STEM + ".kicad_pcb"),
        ]

    def start(self) -> dict[str, Any]:
        if self._context_path().exists():
            saved = self._phase("start")
            require(saved is not None, "Incomplete start cannot replay")
            return saved
        require(not self.state.exists(), "Unregistered or incomplete method state exists")
        core.verify_ready(self.ready)
        require(
            not self.ready.record["synthetic"] or self._runner is not None,
            "Synthetic preparation cannot enter a real ECO",
        )
        require(
            ident(self.dependencies["product_exe"])["sha256"] == PRODUCT_SHA256,
            "The selected executable is not the packaged exact candidate",
        )
        request = self.ready.record["request"]
        runtime = qualify_runtime(
            self.package,
            request["dependencies"].get("path_map", {}),
            request["dependencies"].get("aliases", []),
        )
        declared = self.ready.record["dependencies"]["files"]
        for row in [runtime["node"], *runtime["runtime_files"], *runtime["system_files"]]:
            require(
                str(ordinary_path(row["path"])) in declared,
                "Renderer provider was not declared before preparation",
            )
        require(
            str(ordinary_path(self.dependencies["host_python"])) in declared,
            "Host Python was not declared before preparation",
        )
        source = Path(self.ready.record["source_inventory"]["root"])
        baseline = load(
            self.package / "physical-footprint-eco/methods/requirements/BASELINE-INPUTS.json"
        )
        require(
            set(baseline["files"]) == set(core.PROJECT_FILES), "Baseline seven-file set differs"
        )
        for name in core.PROJECT_FILES:
            raw = (source / name).read_bytes()
            require(
                raw == base64.b64decode(baseline["files"][name]["base64"])
                and digest(raw) == baseline["files"][name]["sha256"],
                "Original 0805 baseline differs: " + name,
            )
        old = (source / "channel_a.kicad_sch").read_bytes()
        edit_binding = c10_source_edit_binding(
            old,
            self.package / "physical-footprint-eco/methods/check_footprint_eco.py",
            base64.b64decode(baseline["files"]["channel_a.kicad_sch"]["base64"]),
        )
        for relative in ("method-state", "intent", "project", "review"):
            (self.work / relative).mkdir(exist_ok=False)
        for name in core.PROJECT_FILES:
            with (self.work / "project" / name).open("xb") as stream:
                stream.write((source / name).read_bytes())
        scope = {
            "scope_id": "owned-c10-" + uuid.uuid4().hex,
            "tool": "kicad-cli",
            "granted_by": "human-user explicitly authorized this finite internal trial",
            "granted_at": stamp(),
            "expires_at": None,
            "effects": ["observe", "change", "persist"],
            "dangerous": False,
            "roots": [str(self.work)],
            "outputs": [str(self.work / "review"), str(self.work / "output")],
            "limits": {"max_operations": 2},
            "notes": "Only C10 Footprint edit then sync candidate-out; protect copper and rules.",
        }
        write_new(self.work / "scope.json", scope)
        write_new(
            self.work / "intent/changes.json",
            [{"op": "set", "ref": "C10", "footprint": NEW_FP.decode()}],
        )
        edit_binding["public_request_file"] = ident(self.work / "intent/changes.json")
        write_new(self.work / "intent/C10-SOURCE-BINDING.json", edit_binding)
        write_new(self.work / "intent/RUNTIME.json", runtime)
        plan = {
            "schema": SCHEMA,
            "frozen_at": stamp(),
            "product_sha256": PRODUCT_SHA256,
            "initial_project": {
                name: ident(self.work / "project" / name) for name in core.PROJECT_FILES
            },
            "edit_expected_sha256": edit_binding["expected_sha256"],
            "c10_footprint_edit": ident(self.work / "intent/C10-SOURCE-BINDING.json"),
            "permission": {
                "C10": "one Footprint atom and exact library replacement subtree",
                "value": "10n",
                "side": "F.Cu",
                "angle_deg": 0,
                "position_mm": [23, 22],
                "pins": {"1": "/CHANNEL_A/SENSE", "2": "GND"},
                "R10": "12k",
                "R20": "10k",
                "protected": [
                    "all other schematic bytes",
                    "other 19 footprints",
                    "100 tracks",
                    "12 vias",
                    "42 pad nets",
                    "10 net matrix",
                    "hierarchy",
                    "rules and 4 ERC/5 DRC ignores",
                ],
            },
            "candidate_views": [
                {"name": n, "layers": list(layers), "mirrored": m} for n, layers, m in VIEWS[:7]
            ],
            "final_views": [
                {"name": n, "layers": list(layers), "mirrored": m} for n, layers, m in VIEWS
            ],
            "final_schematic_pages": ["root", "A", "B"],
            "preview_expiry": "original expires_at only; never extended",
            "python_command_flags": {
                profile: self.ready.python_flags(profile) for profile in ("writer", "consumer")
            },
            "method_boundary": "Packaged methods; disk observations without per-load proof",
            "synthetic": self.ready.record["synthetic"],
        }
        write_new(self.work / "intent/PLAN.json", plan)
        self.context = core.freeze_context(
            self.ready,
            inputs={
                "project": self.work / "project",
                "intent": self.work / "intent",
                "scope": self.work / "scope.json",
                "packaged-methods": self.package / "trial_tools",
                "physical-methods": self.package / "physical-footprint-eco/methods",
            },
        )
        self._save_context()
        write_new(
            self.state / "start.attempt.json",
            {"schema": SCHEMA, "phase": "start", "started_at": stamp()},
        )
        for name, args in (
            ("reference-edit", ["reference", "--command", "sch edit"]),
            ("reference-sync", ["reference", "--command", "project sync"]),
            ("context", ["context"]),
            ("doctor", ["doctor"]),
        ):
            self.product(name, args)
        return self._finish(
            "start",
            {
                "status": "frozen",
                "project": str(self.work / "project"),
                "next": "edit-preview",
                "formal_cad_writes": 0,
            },
            [self.work / "intent/PLAN.json"],
        )

    def edit_preview(self) -> dict[str, Any]:
        saved = self._phase("edit-preview")
        if saved:
            return saved
        require((self.state / "start.complete.json").exists(), "Start must freeze before CAD")
        self._attempt("edit-preview")
        reply = self.product("edit-preview", [*self._edit_args(), "--dry-run"])
        require(reply["data"]["preview"]["can_apply"] is True, "Schematic preview cannot apply")
        unexpired(reply)
        preview = self.state / "edit-preview.reply.json"
        return self._finish(
            "edit-preview",
            {
                "status": "awaiting_exact_schematic_preview_review",
                "preview": ident(preview),
                "operation_id": reply["data"]["preview"]["operation_id"],
                "expires_at": reply["data"]["expires_at"],
                "next": "edit-confirm --ack-preview-sha256 <exact preview digest>",
            },
            [preview],
        )

    def edit_confirm(self, acknowledgment: str) -> dict[str, Any]:
        saved = self._phase("edit-confirm")
        if saved:
            return saved
        preview_path = self.state / "edit-preview.reply.json"
        require(
            acknowledgment == ident(preview_path)["sha256"],
            "Explicit exact schematic-preview acknowledgment differs",
        )
        reply = load(preview_path)
        unexpired(reply)
        expected = load(self.work / "intent/PLAN.json")["edit_expected_sha256"]

        def validator(before, after):
            return exact_one_file_change(before, after, "channel_a.kicad_sch", expected)

        self._attempt("edit-confirm")
        self.product(
            "edit-confirm",
            [*self._edit_args(), "--confirm", reply["data"]["confirm_token"]],
            allowed={"project": validator},
        )
        require(
            ident(self.work / "project/channel_a.kicad_sch")["sha256"] == expected,
            "Saved schematic is not the exact allowed atom change",
        )
        # Persist the original success before querying. A lost later query never
        # authorizes replaying the successful write.
        result = self._finish(
            "edit-confirm",
            {
                "status": "schematic_saved",
                "next": "preview",
                "operation_id": reply["data"]["preview"]["operation_id"],
                "confirm_calls": 1,
            },
            [self.state / "edit-confirm.reply.json"],
        )
        self.product("edit-show", ["operation", "show", "--id", result["operation_id"]])
        return result

    def _readers(self) -> Readers:
        return Readers(
            self.package,
            self.work,
            self.dependencies,
            load(self.work / "intent/RUNTIME.json"),
            self.run,
            self.bind,
            python_flags=self.ready.python_flags("consumer"),
        )

    def _review_request(
        self,
        stage: str,
        checks: Mapping[str, Any],
        images: list[dict[str, Any]],
        operation_id: str,
        candidate: Mapping[str, Any],
    ) -> Path:
        request = {
            "schema": "trial-actual-view-request/v1",
            "stage": stage,
            "operation_id": operation_id,
            "candidate": dict(candidate),
            "checks": dict(checks),
            "images": images,
            "statement_required": (
                "I viewed every listed complete image and accept this exact snapshot "
                "within the stated finite scope."
            ),
            "actual_view_performed": False,
        }
        path = self.work / "output" / stage / "REVIEW-REQUEST.json"
        write_new(path, request)
        self.bind("review-request:" + stage, path)
        return path

    def preview(self) -> dict[str, Any]:
        saved = self._phase("preview")
        if saved:
            return saved
        require(
            (self.state / "edit-confirm.complete.json").exists(), "Schematic edit has not completed"
        )
        self._attempt("preview")
        reply = self.product("sync-preview", [*self._sync_args(), "--dry-run"])
        require(reply["data"]["preview"]["can_apply"] is True, "Sync preview cannot apply")
        unexpired(reply)
        review = reply["data"]["preview"]["extensions"]["candidate_review"]
        operation = reply["data"]["preview"]["operation_id"]
        require(review["operation_id"] == operation, "Candidate review operation differs")
        candidate = self.work / "review" / (STEM + ".kicad_pcb")
        require(Path(review["candidate"]["path"]) == candidate, "Candidate output path differs")
        for name in ("candidate", "manifest"):
            row = review[name]
            require(
                ident(row["path"])["sha256"] == row["sha256"],
                "Published candidate/manifest hash differs",
            )
            self.bind("review:" + name, Path(row["path"]))
        observation = self.work / "candidate-seven"
        observation.mkdir(exist_ok=False)
        migrations = []
        for name in core.PROJECT_FILES:
            source = candidate if name.endswith(".kicad_pcb") else self.work / "project" / name
            target = observation / name
            with target.open("xb") as stream:
                stream.write(source.read_bytes())
            require(
                ident(source)["sha256"] == ident(target)["sha256"], "Observation-copy bytes differ"
            )
            migrations.append(
                {"source": ident(source), "target": ident(target), "byte_exact": True}
            )
        self.bind("candidate-seven", observation)
        binding = self.state / "CANDIDATE-BINDING.json"
        write_new(
            binding,
            {
                "schema": SCHEMA,
                "operation_id": operation,
                "expires_at": reply["data"]["expires_at"],
                "review": review,
                "path_migrations": migrations,
            },
        )
        self.bind("candidate-binding", binding)
        readers = self._readers()
        checks = readers.checks("candidate", observation)
        unexpired(reply)
        images = readers.pcb_images("candidate", observation)
        unexpired(reply)
        request = self._review_request("candidate", checks, images, operation, ident(candidate))
        return self._finish(
            "preview",
            {
                "status": "awaiting_actual_candidate_image_review",
                "operation_id": operation,
                "expires_at": reply["data"]["expires_at"],
                "review_request": ident(request),
                "images": images,
                "next": "review-record --ack <owned acknowledgment JSON>; then confirm",
            },
            [request, candidate],
        )

    def review_record(self, acknowledgment: str | Path, *, final: bool = False) -> dict[str, Any]:
        stage, phase = ("final", "final-review-record") if final else ("candidate", "review-record")
        saved = self._phase(phase)
        if saved:
            return saved
        if not final:
            unexpired(load(self.state / "sync-preview.reply.json"))
        path = ordinary_path(acknowledgment)
        require(path.is_relative_to(self.work), "Acknowledgment must be an owned work file")
        request_path = self.work / "output" / stage / "REVIEW-REQUEST.json"
        request, ack = load(request_path), load(path)
        validate_acknowledgment(request, ident(request_path)["sha256"], ack)
        self.bind("human-ack:" + stage, path)
        record = self.state / (stage + "-ACTUAL-VIEWS.json")
        write_new(
            record,
            {
                "schema": "trial-bound-actual-views/v1",
                "recorded_at": stamp(),
                "request": ident(request_path),
                "acknowledgment": ident(path),
                "actual_view_performed": True,
                "reviewer": ack["reviewer"],
                "operation_id": request["operation_id"],
            },
        )
        self.bind("actual-views:" + stage, record)
        return self._finish(
            phase,
            {
                "status": "actual_views_recorded",
                "stage": stage,
                "review_record": ident(record),
                "next": "complete" if final else "confirm",
            },
            [record, path],
        )

    def confirm(self) -> dict[str, Any]:
        saved = self._phase("confirm")
        if saved:
            return saved
        require(
            (self.state / "review-record.complete.json").exists(),
            "Explicit actual candidate-view record missing",
        )
        reply = load(self.state / "sync-preview.reply.json")
        unexpired(reply)
        request_path = self.work / "output/candidate/REVIEW-REQUEST.json"
        request = load(request_path)
        viewed = load(self.state / "candidate-ACTUAL-VIEWS.json")
        require(viewed["request"] == ident(request_path), "Actual view request binding drift")
        validate_acknowledgment(
            request, ident(request_path)["sha256"], load(Path(viewed["acknowledgment"]["path"]))
        )
        for row in request["checks"].values():
            require(
                ident(row["path"]) == row and load(Path(row["path"]))["status"] == "passed",
                "Required candidate check is missing, changed or failed",
            )
        candidate = request["candidate"]
        require(ident(candidate["path"]) == candidate, "Candidate identity changed")

        def validator(before, after):
            return exact_one_file_change(before, after, STEM + ".kicad_pcb", candidate["sha256"])

        self._attempt("confirm")
        self.product(
            "sync-confirm",
            [*self._sync_args(), "--confirm", reply["data"]["confirm_token"]],
            allowed={"project": validator},
        )
        require(
            ident(self.work / "project" / (STEM + ".kicad_pcb"))["sha256"] == candidate["sha256"],
            "Saved PCB differs from exact candidate",
        )
        result = self._finish(
            "confirm",
            {
                "status": "exact_candidate_saved",
                "confirm_calls": 1,
                "operation_id": request["operation_id"],
                "next": "saved-check",
            },
            [self.state / "sync-confirm.reply.json"],
        )
        self.product("sync-show", ["operation", "show", "--id", request["operation_id"]])
        return result

    def saved_check(self) -> dict[str, Any]:
        saved = self._phase("saved-check")
        if saved:
            return saved
        require(
            (self.state / "confirm.complete.json").exists(),
            "Original sync success checkpoint missing",
        )
        self._attempt("saved-check")
        readers = self._readers()
        checks = readers.checks("final", self.work / "project")
        images = readers.pcb_images("final", self.work / "project", final=True)
        images += readers.schematic_images("final", self.work / "project")
        request = self._review_request(
            "final",
            checks,
            images,
            load(self.state / "confirm.complete.json")["summary"]["operation_id"],
            ident(self.work / "project" / (STEM + ".kicad_pcb")),
        )
        return self._finish(
            "saved-check",
            {
                "status": "saved_checks_passed_pending_actual_final_views",
                "review_request": ident(request),
                "images": images,
                "next": "final-review-record --ack <owned acknowledgment JSON>",
            },
            [request],
        )

    def repeat_confirm_check(self) -> dict[str, Any]:
        """Optional known-success regression; never an unknown-write recovery.

        This sends one deliberately spent, unkeyed token under an exact public
        refusal policy. It is separate from the single successful CAD confirm.
        SQLite rows are read-only internal metadata observations, not public
        receipt authority or a supported general state-editing interface.
        """
        saved = self._phase("repeat-confirm-check")
        if saved:
            return saved
        require(
            (self.state / "confirm.complete.json").exists(),
            "Optional repeat check requires original success checkpoint",
        )
        preview = load(self.state / "sync-preview.reply.json")
        unexpired(preview)
        operation = preview["data"]["preview"]["operation_id"]
        receipt = load(self.state / "sync-confirm.reply.json").get("data", {}).get("receipt")
        known = load(self.state / "sync-show.reply.json").get("data", {}).get("operation_status")
        require(
            isinstance(receipt, dict) and isinstance(known, dict),
            "Original public receipt and operation query are required",
        )
        require(
            known["status"] == "succeeded"
            and known["operation_id"] == operation
            and known["receipt"] == receipt
            and receipt["operation_id"] == operation,
            "Original receipt/query is not known succeeded",
        )
        require_repeat_helpers(self.ready)
        state = Path(self.ready.env("writer")["KICAD_CLI_STATE_DIR"])
        database = ordinary_path(state / "operations.sqlite3")

        def rows():
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
                connection.execute("PRAGMA query_only=ON")
                return {
                    table: connection.execute("SELECT * FROM " + table + " ORDER BY 1").fetchall()
                    for table in ("operations", "consumed_tokens", "scope_usage")
                }

        before_rows, before_files = rows(), capture(state)
        matches = [row for row in before_rows["operations"] if row[0] == operation]
        require(len(matches) == 1, "Original operation is missing or ambiguous")
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
            original = connection.execute(
                "SELECT command,idempotency_key,status FROM operations WHERE operation_id=?",
                (operation,),
            ).fetchone()
        require(
            original == ("project sync", None, "succeeded"),
            "Regression covers only original successful project sync without an idempotency key",
        )
        preprobe = self.state / "REPEAT-CONFIRM-BEFORE.json"
        write_new(
            preprobe,
            {
                "schema": "trial-repeat-private-before-observation/v1",
                "operation_id": operation,
                "recorded_at": stamp(),
                "logical_tables": before_rows,
                "writer_state_inventory": before_files,
                "private_only": True,
            },
        )
        self.bind("repeat-confirm-before", preprobe)
        self._attempt("repeat-confirm-check")
        refusal_argv = [
            self.dependencies["product_exe"],
            *self._sync_args(),
            "--confirm",
            preview["data"]["confirm_token"],
            "--compact",
        ]
        refusal_declaration = {
            "code": "E_CONFLICT",
            "exit_code": 6,
            "reason": "token_used",
            "operation_id": operation,
        }
        result = self.run(
            "repeat-confirm-negative",
            refusal_argv,
            profile="writer",
            expected_exit_codes=(6,),
            expected_public_refusal=refusal_declaration,
        )
        reply = load(Path(result["stdout_path"]))
        error = reply.get("error", {})
        details = error.get("details", {})
        require(
            reply.get("ok") is False
            and error.get("code") == "E_CONFLICT"
            and error.get("retryable") is False
            and details.get("conflict") == "token_used"
            and details.get("dispatch_state") == "not_started"
            and details.get("operation_id") == operation,
            "Repeat did not produce the exact spent-token refusal",
        )
        transport = verify_refusal_transport(result, self.ready, refusal_argv, refusal_declaration)
        after_rows, after_files = rows(), capture(state)
        require(
            before_rows == after_rows, "Repeat changed operations, consumed tokens or scope usage"
        )
        report = self.state / "REPEAT-CONFIRM-CHECK.json"
        write_new(
            report,
            {
                "schema": "trial-known-success-spent-token-check/v1",
                "status": "passed",
                "operation_id": operation,
                "expected_exit": 6,
                "conflict": "token_used",
                "dispatch_state": "not_started",
                "logical_state_rows_unchanged": True,
                "scope_usage_unchanged": True,
                "new_successful_cad_confirms": 0,
                "negative_calls": 1,
                "native_processes": 0,
                "private_reply": ident(result["stdout_path"]),
                "transport_binding": transport,
                "internal_metadata_observation": {"before": before_files, "after": after_files},
                "limit": "Only this unkeyed token; matching-key receipt replay remains legal.",
            },
        )
        self.bind("repeat-confirm-report", report)
        return self._finish(
            "repeat-confirm-check",
            {
                "status": "expected_token_used_refusal_verified",
                "operation_id": operation,
                "negative_calls": 1,
                "new_successful_cad_confirms": 0,
                "report": ident(report),
            },
            [report],
        )

    def query(self, operation: str = "sync") -> dict[str, Any]:
        require(operation in ("edit", "sync"), "Query operation must be edit or sync")
        preview = self.state / (operation + "-preview.reply.json")
        reply = load(preview)
        operation_id = reply["data"]["preview"]["operation_id"]
        # A query remains possible after a stopped/unknown write. It freezes only
        # the immutable authority/method bindings, observes current engineering
        # separately and never advances/replaces the stopped formal context.
        saved_context = self.context
        frozen = load(self._context_path())
        immutable = {
            label: value
            for label, value in frozen["inputs"].items()
            if label in ("intent", "scope", "packaged-methods", "physical-methods")
            or label.startswith("reply:")
        }
        core.verify_ready(self.ready, inputs=immutable)
        self.context = core.freeze_context(
            self.ready,
            inputs={
                "intent": self.work / "intent",
                "scope": self.work / "scope.json",
                "original-preview": preview,
                "methods": self.package / "trial_tools",
            },
        )
        name = "query-" + operation + "-" + uuid.uuid4().hex
        out = self.work / "logs/formal" / name
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            core.guarded_run(
                [
                    self.dependencies["product_exe"],
                    "operation",
                    "show",
                    "--id",
                    operation_id,
                    "--compact",
                ],
                context=self.context,
                profile="writer",
                cwd=self.work,
                out=out,
                _runner=self._runner,
            )
            observed = load(out / "stdout")
            status = observed.get("data", {}).get("operation_status", {})
            return {
                "operation_id": operation_id,
                "state_directory": self.ready.env("writer")["KICAD_CLI_STATE_DIR"],
                "reply": observed,
                "formal_context_advanced": False,
                "observed_status": status.get("status"),
                "observed_stage": status.get("stage"),
                "private_reply": ident(out / "stdout"),
                "engineering_observation": capture(self.work / "project"),
                "new_confirm_calls": 0,
            }
        finally:
            self.context = saved_context


def validate_acknowledgment(
    request: Mapping[str, Any], request_digest: str, ack: Mapping[str, Any]
) -> None:
    require(
        set(ack)
        == {
            "schema",
            "request_sha256",
            "operation_id",
            "reviewer",
            "statement",
            "images",
            "verdict",
        },
        "Acknowledgment has missing or unknown fields",
    )
    require(
        ack["schema"] == "trial-actual-view-ack/v1"
        and ack["request_sha256"] == request_digest
        and ack["operation_id"] == request["operation_id"],
        "Acknowledgment snapshot/operation differs",
    )
    require(
        isinstance(ack["reviewer"], str) and bool(ack["reviewer"].strip()),
        "Reviewer identity required",
    )
    require(
        ack["statement"] == request["statement_required"] and ack["verdict"] == "accepted",
        "Explicit actual image-view statement and accepted verdict required",
    )
    expected = [
        {"path": row["path"], "sha256": row["sha256"], "actual_viewed": True}
        for row in request["images"]
    ]
    require(
        ack["images"] == expected and all(row["actual_viewed"] is True for row in ack["images"]),
        "Every exact image must be explicitly acknowledged once in the listed order",
    )
    for row in request["images"]:
        require(ident(row["path"]) == row, "Reviewed image changed")
