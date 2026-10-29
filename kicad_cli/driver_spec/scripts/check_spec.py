"""Validate the specification: schemas, examples, state relations, the pinned base and document links.

This checker never contacts an application, device, schema server or Git remote. It does
not establish that any receipt or evidence was produced by a real tool.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import types
from collections import Counter
from functools import lru_cache
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import unquote, urljoin, urlsplit

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable

from contract_io import InputError, canonical_relative_path, path_identity

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_PREFIX = "urn:tool-driver-cli-spec:"
CLAUSE_DOCUMENTS = {
    "LAYERING.md": "LAY", "CORE-SPEC.md": "COR", "DRIVER-SPEC.md": "DRV",
    "VERIFICATION-SPEC.md": "VER", "DELIVERY-SPEC.md": "DEL", "SKILL-ADDENDUM.md": "SKL",
}
RESERVED_PARAMS = {"format", "json", "fields", "compact", "quiet", "dry-run", "confirm", "dangerous",
                   "request-id", "idempotency-key", "version", "help"}
TIERS = {"T0": 0, "T1": 1, "T2": 2}
PHASES_READ = {"observe", "verify", "status", "result"}
BASE_BUILTINS = {"reference", "context", "doctor", "changelog"}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def normalized_bytes(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


@lru_cache(maxsize=1)
def driver_contract() -> dict:
    return load(ROOT / "contract" / "driver-contract.json")


@lru_cache(maxsize=1)
def base_contract() -> dict:
    return load(ROOT / "base" / "ai-native-cli-spec" / "contract.json")


@lru_cache(maxsize=1)
def public_schemas() -> dict[str, dict]:
    return {schema["$id"]: schema for schema in (load(path) for path in sorted((ROOT / "schemas").glob("*.schema.json")))}


@lru_cache(maxsize=1)
def validators():
    schemas = {p.name: load(p) for p in sorted((ROOT / "schemas").glob("*.schema.json"))}
    registry = Registry()
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    return {
        name: Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
        for name, schema in schemas.items()
    }


def schema_nodes(schema, base: str, pointer: str = ""):
    """Visit real subschemas, excluding schema containers and annotation data."""
    if isinstance(schema, bool):
        yield pointer, base, schema
        return
    if not isinstance(schema, dict):
        return
    base = urljoin(base, schema.get("$id", base))
    yield pointer, base, schema
    for key in ["$defs", "definitions", "properties", "patternProperties", "dependentSchemas"]:
        for name, child in schema.get(key, {}).items():
            escaped = name.replace("~", "~0").replace("/", "~1")
            yield from schema_nodes(child, base, f"{pointer}/{key}/{escaped}")
    for key in ["items", "additionalProperties", "unevaluatedProperties", "unevaluatedItems", "contains",
                "propertyNames", "if", "then", "else", "not", "contentSchema"]:
        if key in schema:
            yield from schema_nodes(schema[key], base, f"{pointer}/{key}")
    for key in ["allOf", "anyOf", "oneOf", "prefixItems"]:
        for index, child in enumerate(schema.get(key, [])):
            yield from schema_nodes(child, base, f"{pointer}/{key}/{index}")


# ---------------------------------------------------------------- error codes


def code_table() -> dict[str, dict]:
    """Core codes from the pinned base plus the driver extension codes, each with exit and retryable."""
    table = {code: {"exit": entry["exit"], "retryable": entry["retryable"], "layer": "core"}
             for code, entry in base_contract()["error_codes"]["core"].items()}
    for code, entry in driver_contract()["error_codes"]["extensions"].items():
        table[code] = {"exit": entry["exit"], "retryable": entry["retryable"], "layer": "driver"}
    return table


def allowed_exits() -> set[int]:
    return {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 130}


def error_table_errors(error_codes: dict) -> list[str]:
    errors = []
    table = code_table()
    for code, entry in error_codes.items():
        known = table.get(code)
        if known:
            if entry["exit"] != known["exit"] or entry["retryable"] != known["retryable"]:
                errors.append(f"{code} must keep the {known['layer']} exit {known['exit']} and retryable {str(known['retryable']).lower()}")
        elif entry["exit"] not in allowed_exits():
            errors.append(f"{code} uses an exit code the base contract does not define")
        elif entry["exit"] == 9 and "HUMAN" not in code and "2FA" not in code:
            errors.append(f"{code} cannot use exit 9, which the base reserves for human-action codes")
    return errors


# ---------------------------------------------------------------- receipts


MODIFYING = set(driver_contract()["effects"]["confirm_gated"]) if (ROOT / "contract" / "driver-contract.json").exists() else set()


def receipt_conditions(receipt: dict) -> dict[str, bool]:
    outcome = receipt["outcome"]
    dimensions = [outcome["execution"], outcome["change"], outcome["persistence"], outcome["delivery"]]
    status = outcome["verification"]["status"]
    return {
        "unknown": "unknown" in dimensions,
        "running": outcome["execution"] == "running",
        "partial": "partial" in [outcome["change"], outcome["persistence"], outcome["delivery"]],
        "stopped": outcome["execution"] in {"failed", "interrupted", "not_started"},
        "lasting": outcome["change"] == "applied" or outcome["persistence"] == "saved"
                   or outcome["delivery"] == "delivered" or (bool(receipt["artifacts"]) and "artifact" in receipt["effects"]),
        "verification_failed": status == "failed",
        "verification_missing": status == "incomplete" or (outcome["change"] == "applied" and status == "not_run"),
    }


def receipt_rule(receipt: dict) -> dict:
    """Return the first matching rule of contract.receipt_codes: {when, ok, codes}."""
    conditions = receipt_conditions(receipt)
    for rule in driver_contract()["receipt_codes"]["rules"]:
        if all(conditions[name] for name in rule["when"]):
            return rule
    raise ValueError("receipt_codes needs a final catch-all rule")


def receipt_errors(value: dict) -> list[str]:
    errors = []
    outcome = value["outcome"]
    verification = outcome["verification"]
    before, after = value["snapshot_before"], value["snapshot_after"]
    effects = set(value["effects"])
    profile = verification.get("extensions", {}).get("verification_result")
    marker = profile.get("profile") if isinstance(profile, dict) else None
    if isinstance(marker, str) and marker.startswith(PUBLIC_PREFIX + "verification-result:"):
        problems = instance_errors("verification-result", profile)
        errors.extend(problems)
        if not problems:
            errors.extend(verification_helpers().receipt_profile_errors(value, profile))
    if outcome["change"] in {"applied", "partial", "rolled_back"} and value["authorization_scope_id"] is None:
        errors.append("known modifications must retain their applicable authorization reference")
    if outcome["change"] == "unknown" and value["authorization_scope_id"] is None and not value.get("authorization_unavailable_reason"):
        errors.append("unknown authorization must be disclosed without inventing a scope")
    if outcome["change"] in {"applied", "partial", "rolled_back"} and not effects & {"change", "persist", "configure", "physical"}:
        errors.append("a receipt that changed its target must declare a modifying effect")
    if outcome["execution"] == "not_started" and outcome["change"] != "not_applied":
        errors.append("not_started cannot claim an applied or unknown change")
    if verification["status"] == "passed":
        if not after or verification["snapshot_id"] != after["id"]:
            errors.append("passed readback must identify snapshot_after")
        if any(c["required"] and c["status"] != "passed" for c in verification["checks"]):
            errors.append("all required checks must pass")
        if any(c["required"] for c in verification["not_checked"]):
            errors.append("required checks cannot be omitted from passed verification")
    if verification["status"] == "not_applicable" and (
        any(c["required"] for c in verification["checks"]) or any(c["required"] for c in verification["not_checked"])
    ):
        errors.append("not_applicable cannot bypass applicable required checks")
    if outcome["change"] == "rolled_back" and (not before or not after or not verification["evidence_refs"]):
        errors.append("rollback needs preimage, fresh readback and evidence")
    if receipt_conditions(value)["unknown"] and value["recovery"]["action"] == "retry":
        errors.append("unknown outcomes must be reconciled before retry")
    if outcome["persistence"] == "saved":
        if not after:
            errors.append("saved state needs a post-operation snapshot")
        if "persist" not in effects:
            errors.append("saved state needs the declared persist effect")
    if ("deliver" in effects) != (outcome["delivery"] != "not_applicable"):
        errors.append("delivery is not_applicable exactly when deliver is not a declared effect")
    statuses = [delivery["status"] for delivery in value["deliveries"]]
    expectations = {
        "delivered": bool(statuses) and all(status == "delivered" for status in statuses),
        "rejected": "rejected" in statuses and "delivered" not in statuses,
        "partial": "delivered" in statuses and any(status != "delivered" for status in statuses),
        "not_attempted": all(status == "not_attempted" for status in statuses),
    }
    if outcome["delivery"] in expectations and not expectations[outcome["delivery"]]:
        errors.append("outcome.delivery must agree with the per-destination delivery results")
    if outcome["delivery"] == "rejected" and outcome["execution"] == "completed":
        errors.append("a rejected delivery means the requested operation did not complete")
    stage = value["failed_stage"]
    if outcome["execution"] in {"failed", "interrupted"} and stage is None:
        errors.append("a failed or interrupted operation must name the stage that failed")
    if outcome["execution"] == "completed" and stage not in {None, "verify"}:
        errors.append("a completed execution can only name verify as its failed stage")
    if stage == "verify" and verification["status"] not in {"failed", "incomplete"}:
        errors.append("failed_stage verify needs a failed or incomplete verification")
    if stage in {"persist", "deliver"} and stage not in effects:
        errors.append("failed_stage must be a stage the operation declared")
    if value.get("replayed") and not value.get("request_id"):
        errors.append("a replayed receipt must carry the idempotency key that matched")
    return errors


# ---------------------------------------------------------------- plans and responses


def plan_errors(value: dict) -> list[str]:
    errors = []
    trial = value["trial_snapshot"]
    items = value.get("items")
    if items is not None:
        keys = [item["key"] for item in items]
        operations = [item["operation_id"] for item in items]
        if value["total"] != len(items) or value["targets"] != keys:
            errors.append("a batch plan lists every target once, in order, and counts them (base §15.2)")
        if len(keys) != len(set(keys)):
            errors.append("batch targets are deduplicated before planning")
        if len(operations) != len(set(operations)) or value["operation_id"] in operations:
            errors.append("every batch item runs under its own operation_id, distinct from the batch")
        if value["can_apply"] and not all(item["can_apply"] for item in items):
            errors.append("a batch that can be applied has no item that cannot")
    base_snapshot = value.get("base_snapshot") or (items[0]["base_snapshot"] if items else None)
    if value["level"] == "trial_observed":
        if not trial or trial["source"] != "trial" or (base_snapshot and trial["id"] == base_snapshot["id"]):
            errors.append("trial_observed needs an independent trial snapshot")
        if "physical" in value["effects"]:
            errors.append("a real physical effect cannot be an isolated observed trial")
    elif trial is not None:
        errors.append("only a trial_observed plan carries a trial snapshot")
    outputs = value["outputs"] + [output for item in items or [] for output in item["outputs"]]
    if any(output["effect"] not in value["effects"] for output in outputs):
        errors.append("every planned output must belong to a declared effect")
    blocked = [item for item in items or [] if not item["can_apply"]]
    if not value["can_apply"] and not (value["constraints"] or value["unknowns"] or any(item.get("unknowns") for item in blocked)):
        errors.append("a plan that cannot be applied must say which constraint or unknown blocks it")
    return errors


def details_errors(error: dict) -> list[str]:
    errors = []
    code, details = error["code"], error["details"]
    contract = driver_contract()
    known = code_table().get(code)
    if known and error["retryable"] != known["retryable"]:
        errors.append(f"{code} must report retryable {str(known['retryable']).lower()}")
    table = contract["error_details"]["required"]
    seen = set()
    for key in table["every_error"] + table.get(code, []):
        if key in seen:
            continue
        seen.add(key)
        options = key.split("|")
        if not any(option in details for option in options):
            label = "every driver error" if key in table["every_error"] else code
            errors.append(f"{label} needs details.{' or details.'.join(options)}")
    for key in contract["error_details"].get("conflict_requires", {}).get(details.get("conflict"), []):
        if key not in details:
            errors.append(f"conflict {details['conflict']} needs details.{key}")
    state = details.get("dispatch_state")
    if state in {"dispatched", "unknown"}:
        if "operation_id" not in details:
            errors.append("a dispatched or uncertain call must name its operation_id")
        if not any(key in details for key in ["receipt", "workflow", "operation_status"]) and code not in {"E_OUTCOME_UNKNOWN", "E_HUMAN_REQUIRED"}:
            errors.append("after dispatch, a failure without a receipt can only be E_OUTCOME_UNKNOWN or E_HUMAN_REQUIRED")
    if code == "E_OUTCOME_UNKNOWN" and state == "not_started":
        errors.append("E_OUTCOME_UNKNOWN cannot claim that nothing was dispatched")
    for name in ["receipt", "workflow", "operation_status"]:
        if name in details:
            errors.extend(semantic_errors(name.replace("_", "-"), details[name]))
    receipt = details.get("receipt")
    if receipt:
        rule = receipt_rule(receipt)
        if rule["ok"]:
            errors.append("a fully successful receipt cannot accompany a failure")
        elif code not in rule["codes"]:
            errors.append(f"receipt outcome requires one of {rule['codes']}, not {code}")
    workflow = details.get("workflow")
    if workflow:
        errors.extend(workflow_failure_errors(workflow, code))
    if "_untrusted" in details:
        errors.extend(untrusted_errors(details, "error.details"))
    return errors


def untrusted_errors(container: dict, label: str) -> list[str]:
    names = [entry.split(".", 1)[0].split("[", 1)[0] for entry in container.get("_untrusted", [])]
    return [f"{label}._untrusted names a field it does not contain: {name}" for name in names if name not in container]


def workflow_failure_errors(workflow: dict, code: str) -> list[str]:
    steps = workflow["steps"]
    if all(step["status"] == "completed" for step in steps):
        return ["a workflow that completed every step is not a failure"]
    if any(step["status"] == "unknown" for step in steps):
        return [] if code == "E_OUTCOME_UNKNOWN" else ["a workflow with an unknown step fails with E_OUTCOME_UNKNOWN"]
    lasting = any((step["status"] == "completed" and set(step["effects"]) & MODIFYING)
                  or (step["receipt"] is not None and receipt_conditions(step["receipt"])["lasting"]) for step in steps)
    if lasting:
        return [] if code == "E_PARTIAL" else ["a workflow that applied some steps fails with E_PARTIAL"]
    step_codes = {step["error"]["code"] for step in steps if step.get("error")}
    if code not in step_codes | {"E_NATIVE_FAILURE"}:
        return ["a workflow that changed nothing reports a failed step's code"]
    return []


def batch_errors(value: dict) -> list[str]:
    errors = []
    items = value["items"]
    summary = value["summary"]
    skipped = sum(1 for item in items if "skipped_reason" in item)
    attempted = len(items) - skipped
    succeeded = sum(1 for item in items if item["ok"])
    failed = attempted - succeeded
    unknown = sum(1 for item in items if item.get("error", {}).get("code") == "E_OUTCOME_UNKNOWN")
    if summary["total"] != attempted or summary["succeeded"] != succeeded or summary["failed"] != failed:
        errors.append("batch summary counts the attempted items exactly (base §15.5); skipped items are counted separately")
    after_dispatch = {"E_PARTIAL", "E_OUTCOME_UNKNOWN", "E_VERIFY_FAILED", "E_VERIFY_INCOMPLETE"}
    for item in items:
        dispatched = item["ok"] or item.get("error", {}).get("code") in after_dispatch
        if dispatched and "receipt" not in item and "operation_id" not in item:
            errors.append("a dispatched batch item carries its receipt or operation_id")
    if summary.get("skipped", 0) != skipped or (skipped and "skipped" not in summary):
        errors.append("skipped items must be counted in summary.skipped")
    if summary.get("unknown", 0) != unknown or (unknown and "unknown" not in summary):
        errors.append("items with unknown outcomes must be counted in summary.unknown")
    targets = [item["target"] for item in items]
    if len(targets) != len(set(targets)):
        errors.append("batch targets are deduplicated before execution")
    for item in items:
        receipt = item.get("receipt")
        if receipt is None:
            continue
        errors.extend(receipt_errors(receipt))
        if "operation_id" in item and item["operation_id"] != receipt["operation_id"]:
            errors.append("item and receipt operation identities must agree")
        rule = receipt_rule(receipt)
        if rule["ok"] != item["ok"]:
            errors.append("an item's ok must follow its receipt")
        elif not item["ok"] and "error" in item and item["error"]["code"] not in rule["codes"]:
            errors.append(f"item receipt requires one of {rule['codes']}")
    return errors


def query_page_errors(value: dict) -> list[str]:
    errors = []
    limits = value.get("limits")
    unread = value.get("unread_scopes", [])
    explained = bool(unread) or bool(limits and limits["truncation_reason"])
    if value["count"] != len(value["items"]):
        errors.append("query count must equal the returned item count")
    if (value["snapshot"] is None) == (value["snapshot_unavailable_reason"] is None):
        errors.append("a missing snapshot needs a reason, and a present snapshot needs none")
    if value.get("consistency", {}).get("mode") == "snapshot" and value["snapshot"] is None:
        errors.append("snapshot consistency needs the snapshot it read")
    continuation = value.get("next_cursor") is not None or "next_offset" in value
    if value["has_more"] and not continuation and not explained:
        errors.append("has_more needs a continuation or a reason it cannot continue")
    if "next_offset" in value and not value["has_more"]:
        errors.append("next_offset appears only when has_more is true")
    if value.get("next_cursor") is not None and not value["has_more"]:
        errors.append("next_cursor appears only when has_more is true")
    if value["truncated"] and not explained:
        errors.append("truncated results need a truncation reason or unread scopes")
    if unread and not value["truncated"]:
        errors.append("a page with unread scopes is incomplete and must say truncated")
    if limits and limits["effective"] is not None and value["count"] > limits["effective"]:
        errors.append("query items cannot exceed the effective output limit")
    return errors


def batch_status(statuses: list[str]) -> str:
    """The status of a finished batch from its items: an item still active or unknown leaves the batch unknown;
    an item that never started counts as failed."""
    if any(status in {"unknown", "pending", "running", "cancel_requested"} for status in statuses):
        return "unknown"
    if all(status == "succeeded" for status in statuses):
        return "succeeded"
    if any(status in {"succeeded", "partial"} for status in statuses):
        return "partial"
    return "failed"


def operation_status_errors(value: dict) -> list[str]:
    errors = []
    status = value["status"]
    receipt = value["receipt"]
    if receipt:
        errors.extend(receipt_errors(receipt))
        if receipt["operation_id"] != value["operation_id"]:
            errors.append("status and receipt operation identities must agree")
        identity = ["mode", "engine_id", "instance_id", "object_id", "locator"]
        if value["target"] is None or any(value["target"][key] != receipt["target"][key] for key in identity):
            errors.append("status and receipt must describe the same target")
        rule = receipt_rule(receipt)
        if status == "succeeded" and not rule["ok"]:
            errors.append("succeeded status needs a fully successful receipt")
        if status == "partial" and "E_PARTIAL" not in rule["codes"]:
            errors.append("partial status needs a partial receipt")
        if status == "failed" and (rule["ok"] or rule["codes"] == ["E_OUTCOME_UNKNOWN"]):
            errors.append("failed status needs a receipt that shows a known failure")
        if status == "unknown" and rule["codes"] != ["E_OUTCOME_UNKNOWN"]:
            errors.append("unknown status cannot hold a receipt with a known outcome")
        if status == "pending" and receipt["outcome"]["execution"] != "not_started":
            errors.append("pending status cannot describe an already-started operation")
        if status == "cancelled" and receipt["outcome"]["execution"] not in {"not_started", "interrupted"}:
            errors.append("cancelled status needs a stopped or never-started execution")
    elif status in {"succeeded", "partial"} and value.get("items") is None:
        errors.append(f"{status} status needs the receipt it was derived from")
    elif status in {"failed", "cancelled"} and value.get("items") is None and value["started_at"] is not None:
        errors.append("a terminal dispatched operation needs effect readback; without its receipt the outcome is unknown")
    items = value.get("items")
    if items is not None:
        plan = value.get("plan") or {}
        if receipt is not None or value["target"] is not None:
            errors.append("a batch record has no single target or receipt; each item is its own operation")
        if plan and [(item["key"], item["operation_id"]) for item in items] != [(item["key"], item["operation_id"]) for item in plan.get("items", [])]:
            errors.append("a batch record lists the items of its plan, in plan order")
        statuses = [item["status"] for item in items]
        if status == "planned" and set(statuses) != {"planned"}:
            errors.append("a planned batch has only planned items")
        if status in {"succeeded", "partial", "failed", "unknown"} and status != batch_status(statuses):
            errors.append(f"a finished batch reports {batch_status(statuses)} for these items")
    if status == "planned" and (value["started_at"] is not None or value["plan_id"] is None):
        errors.append("a planned operation has a plan and has not started")
    plan = value.get("plan")
    if plan:
        errors.extend(plan_errors(plan))
        if plan["operation_id"] != value["operation_id"] or plan["plan_id"] != value["plan_id"]:
            errors.append("the recorded plan belongs to this operation")
    if status == "cancelled" and not value["stop_observed"]:
        errors.append("cancelled needs an observed stop; otherwise report cancel_requested")
    if value["stop_observed"]:
        if status in {"planned", "pending", "running", "cancel_requested"}:
            errors.append("an active task cannot claim an observed stop")
        if value["stop_evidence_ref"] is None:
            errors.append("an observed stop needs its evidence reference")
    if value["resolution"] is not None and status not in {"unknown", "partial"}:
        errors.append("only unknown or partial operations are resolved after the fact")
    if value["blocked"] is not None and status not in {"pending", "running"}:
        errors.append("only a pending or running operation can be blocked")
    if value["record"]["availability"] != "available" and (status != "unknown" or receipt is not None):
        errors.append("an expired or unavailable record cannot report a known outcome")
    if value["commands"]["lookup"]["argv"][:2] != ["operation", "show"]:
        errors.append("the lookup command is the built-in operation show")
    return errors


def workflow_errors(value: dict) -> list[str]:
    errors = []
    steps = value["steps"]
    ids = [step["id"] for step in steps]
    if len(ids) != len(set(ids)):
        errors.append("workflow step identities must be unique")
    by_id = {step["id"]: step for step in steps}
    for step in steps:
        if any(dependency not in by_id for dependency in step["depends_on"]):
            errors.append("workflow dependencies must identify existing steps")
        receipt = step["receipt"]
        modifying = bool(set(step["effects"]) & MODIFYING)
        executing = step["phase"] not in {"plan", "submit", "status"}
        if receipt:
            errors.extend(receipt_errors(receipt))
            if receipt["operation_id"] != step["operation_id"]:
                errors.append("step and receipt operation identities must agree")
            if not set(receipt["effects"]).issubset(set(step["effects"])):
                errors.append("step effects cannot hide effects reported by its receipt")
            rule = receipt_rule(receipt)
            if step["status"] == "completed" and not rule["ok"]:
                errors.append("a completed step needs a fully successful receipt")
            if step["status"] in {"failed", "unknown"} and step["error"] and step["error"]["code"] not in rule["codes"]:
                errors.append("a step's error code must follow its receipt")
        if step["status"] == "completed" and modifying and executing and not receipt:
            errors.append("completed modifying steps cannot substitute output refs for a receipt")
        if step["status"] in {"running", "completed", "failed", "cancelled", "unknown"}:
            for dependency in step["depends_on"]:
                parent = by_id.get(dependency)
                if parent is None:
                    continue
                if parent["status"] != "completed":
                    errors.append("a dependent step cannot run before its prerequisite completes")
                if parent["receipt"]:
                    verification = parent["receipt"]["outcome"]["verification"]
                    if any(c["required"] and c["status"] != "passed" for c in verification["checks"]) or any(c["required"] for c in verification["not_checked"]):
                        errors.append("dependent steps cannot bypass required prerequisite checks")
    if value["failure_policy"] == "stop" and any(step["status"] in {"failed", "unknown"} for step in steps):
        if any(step["status"] == "running" for step in steps):
            errors.append("a stop policy cannot keep steps running after a failure")
    incoming = {identifier: sum(dependency in by_id for dependency in step["depends_on"]) for identifier, step in by_id.items()}
    downstream = {identifier: [] for identifier in by_id}
    for identifier, step in by_id.items():
        for dependency in step["depends_on"]:
            if dependency in by_id:
                downstream[dependency].append(identifier)
    ready = [identifier for identifier, count in incoming.items() if count == 0]
    visited = 0
    while ready:
        identifier = ready.pop()
        visited += 1
        for child in downstream[identifier]:
            incoming[child] -= 1
            if incoming[child] == 0:
                ready.append(child)
    if visited != len(by_id):
        errors.append("workflow dependencies must be acyclic")
    counts = Counter(step["status"] for step in steps)
    summary = value["summary"]
    if summary["total"] != len(steps) or any(summary[status] != counts[status] for status in summary if status != "total"):
        errors.append("workflow summary must exactly count the step results")
    return errors


def response_errors(value: dict) -> list[str]:
    errors = []
    if value["ok"]:
        data = value["data"]
        preview = data.get("preview")
        if preview:
            errors.extend(plan_errors(preview))
            if data.get("expires_at") != preview["expires_at"]:
                errors.append("the confirm token and its plan expire together")
        receipt = data.get("receipt")
        if receipt:
            errors.extend(receipt_errors(receipt))
            rule = receipt_rule(receipt)
            if not rule["ok"]:
                errors.append(f"this receipt cannot be reported as ok; it requires {rule['codes']}")
        workflow = data.get("workflow")
        if workflow:
            errors.extend(workflow_errors(workflow))
            if any(step["status"] != "completed" for step in workflow["steps"]):
                errors.append("a successful workflow completes every requested step")
        status = data.get("operation_status")
        if status:
            errors.extend(operation_status_errors(status))
        if "items" in data and "summary" in data:
            errors.extend(instance_errors("batch", data))
        elif "items" in data and "count" in data:
            errors.extend(instance_errors("query-page", data))
        if "_untrusted" in data:
            errors.extend(untrusted_errors(data, "data"))
    else:
        errors.extend(details_errors(value["error"]))
    return errors


# ---------------------------------------------------------------- reference


def tier_floor(effects: set[str]) -> int:
    minimum = driver_contract()["effects"]["minimum_risk_tier"]
    return max((TIERS[minimum[effect]] for effect in effects), default=0)


def builtin_commands() -> set[str]:
    """Base self-description commands (CLI-SPEC §11) plus the driver operation commands (COR-025)."""
    return BASE_BUILTINS | set(driver_contract()["self_description"]["builtin_commands"])


def operation_states() -> list[str]:
    return public_schemas()[PUBLIC_PREFIX + "operation-status:2.0"]["properties"]["status"]["enum"]


def path_errors(paths: list[str]) -> list[str]:
    errors = []
    split = [tuple(path.split()) for path in paths]
    if len(split) != len(set(split)):
        errors.append("command paths must be unique")
    for path in split:
        if any(other != path and other[:len(path)] == path for other in split):
            errors.append(f"a command path cannot also be a group: {' '.join(path)}")
    missing = sorted(builtin_commands() - set(paths))
    if missing:
        errors.append(f"the built-in commands are listed: missing {', '.join(missing)}")
    return errors


def classification_errors(label: str, ctype: str, dangerous: bool, effects: set[str]) -> list[str]:
    """Type, danger and built-in rules shared by reference, reference --index and reference --command."""
    contract = driver_contract()["effects"]
    if label in contract["exempt"]:
        if ctype != "write" or effects != {"observe"}:
            return [f"{label}: the base self-update is declared type write with effects [observe] (base §14)"]
        return []
    errors = []
    if (ctype == "write") != bool(effects & set(contract["confirm_gated"])):
        errors.append(f"{label}: type is write exactly when an effect other than observe is declared")
    if effects & set(contract["dangerous"]) and not dangerous:
        errors.append(f"{label}: deliver, configure and physical commands are dangerous")
    if label in builtin_commands() and (ctype != "read" or effects != {"observe"}):
        errors.append(f"{label}: built-in commands are read commands that only observe")
    return errors


def builtin_param_errors(command: dict) -> list[str]:
    errors = []
    label = command["path"]
    params = {param["name"]: param for param in command["params"]}
    if label in {"operation show", "operation resolve"} and not params.get("id", {}).get("required"):
        errors.append(f"{label}: takes the required --id")
    if label == "operation list" and set(params.get("status", {}).get("enum", [])) != set(operation_states()):
        errors.append("operation list: --status accepts exactly the operation states")
    if label == "operation resolve":
        if set(params.get("outcome", {}).get("enum", [])) != {"applied", "not_applied", "partial"}:
            errors.append("operation resolve: --outcome is applied, not_applied or partial")
        if not params.get("evidence", {}).get("required"):
            errors.append("operation resolve: takes the required --evidence")
    return errors


def command_errors(tool: str, command: dict, schemas: dict, error_codes: dict, json_schemas: dict,
                   tier: int | None = None, level: str | None = None) -> list[str]:
    errors = []
    driver = command["driver"]
    label = command["path"]
    effects = set(driver["effects"])
    exempt = label in driver_contract()["effects"]["exempt"]
    examples = command["examples"]
    errors.extend(classification_errors(label, command["type"], bool(command.get("dangerous")), effects))
    errors.extend(builtin_param_errors(command))
    if tier is not None and tier_floor(effects) > tier:
        errors.append(f"{label}: its effects need a higher tool risk_tier")
    if command["output_schema"] not in schemas:
        errors.append(f"{label}: output_schema must name an entry in schemas")
    prefix = f"{tool} {label}"
    if any(not example.startswith(prefix) for example in examples):
        errors.append(f"{label}: examples are runnable calls that start with '{prefix}'")
    if exempt:
        if any("--confirm" in example for example in examples):
            errors.append(f"{label}: the base self-update never takes --confirm (base §14)")
    elif command["type"] == "write":
        if not any("--dry-run" in example for example in examples) or not any("--confirm" in example for example in examples):
            errors.append(f"{label}: a write command shows the --dry-run and --confirm pair")
        if "plan" not in driver["phases"] or not set(driver["phases"]) & {"apply", "submit"}:
            errors.append(f"{label}: a write command declares the plan phase and apply or submit")
    elif not set(driver["phases"]) <= PHASES_READ:
        errors.append(f"{label}: a read command cannot declare plan, apply or submit")
    if command.get("dangerous") and not any("--dangerous" in example for example in examples):
        errors.append(f"{label}: a dangerous command's examples carry --dangerous")
    if "operation_status" in driver["profiles"] and "submit" not in driver["phases"]:
        errors.append(f"{label}: the operation_status profile belongs to submit commands")
    names = [param["name"] for param in command["params"]]
    if len(names) != len(set(names)):
        errors.append(f"{label}: parameter names must be unique")
    for param in command["params"]:
        if param["name"] in RESERVED_PARAMS:
            errors.append(f"{label}: --{param['name']} is a base global flag")
        errors.extend(f"{label}: {problem}" for problem in param_errors(param))
    if any(code not in error_codes for code in driver["error_codes"]):
        errors.append(f"{label}: command error codes must be declared in error_codes")
    if driver["maturity"] in {"verified", "stable"} and not driver["evidence_refs"]:
        errors.append(f"{label}: verified commands need scoped evidence")
    if level == "stable" and driver["maturity"] == "draft":
        errors.append(f"{label}: a stable release cannot expose draft commands")
    for stage in driver.get("stages", []):
        if not set(stage["effects"]) <= effects:
            errors.append(f"{label}: stage effects must be declared command effects")
    for key in ["request_schema", "result_schema"]:
        identifier = driver.get(key)
        if identifier is not None and identifier not in json_schemas and identifier not in public_schemas():
            errors.append(f"{label}: {key} names an unknown schema")
    return errors


def reference_errors(value: dict) -> list[str]:
    errors = []
    commands = value["commands"]
    schemas = value["schemas"]
    error_codes = value["error_codes"]
    errors.extend(path_errors([command["path"] for command in commands]))
    capability_ids = [command["driver"]["capability_id"] for command in commands]
    if len(capability_ids) != len(set(capability_ids)):
        errors.append("capability IDs must be unique")
    errors.extend(error_table_errors(error_codes))
    exits = {"0"} | {str(entry["exit"]) for entry in error_codes.values()}
    if not exits <= set(value["exit_codes"]):
        errors.append("exit_codes must explain every exit code the error table uses")
    for label, entry in schemas.items():
        if any(field.split(".", 1)[0] not in entry["fields"] for field in entry["untrusted_fields"]):
            errors.append(f"schema {label} marks untrusted fields it does not list")
    json_schemas = value.get("json_schemas", {})
    errors.extend(json_schema_errors(json_schemas, public_schemas()))
    level = value["release_readiness"]["level"]
    if level == "stable" and value["release_readiness"]["fcc_status"] != "verified":
        errors.append("a stable release needs verified functional contract coverage")
    for command in commands:
        errors.extend(command_errors(value["tool"], command, schemas, error_codes, json_schemas,
                                     TIERS[value["risk_tier"]], level))
    return errors


def reference_index_errors(value: dict) -> list[str]:
    errors = path_errors([command["path"] for command in value["commands"]])
    tier = TIERS[value["risk_tier"]]
    for command in value["commands"]:
        effects = set(command["effects"])
        errors.extend(classification_errors(command["path"], command["type"], command["dangerous"], effects))
        if tier_floor(effects) > tier:
            errors.append(f"{command['path']}: its effects need a higher tool risk_tier")
    return errors


def reference_command_errors(value: dict) -> list[str]:
    errors = []
    command = value["command"]
    driver = command["driver"]
    public = public_schemas()
    if list(value["schemas"]) != [command["output_schema"]]:
        errors.append("reference --command returns exactly the schemas entry its output_schema names")
    if set(value["error_codes"]) != set(driver["error_codes"]):
        errors.append("reference --command returns exactly the error codes the command declares")
    errors.extend(error_table_errors(value["error_codes"]))
    named = {driver[key] for key in ["request_schema", "result_schema"] if driver.get(key)}
    entry = value["schemas"].get(command["output_schema"], {})
    reference = entry.get("json_schema", {}).get("$ref")
    if isinstance(reference, str):
        named.add(reference.split("#", 1)[0])
    if set(value["json_schemas"]) != {name for name in named if not name.startswith(PUBLIC_PREFIX)}:
        errors.append("reference --command returns the product JSON Schemas the command names, and only those")
    if set(value["public_schemas"]) != {name for name in named if name.startswith(PUBLIC_PREFIX)}:
        errors.append("reference --command lists the public driver schemas the command names, by URN")
    if any(name not in public for name in value["public_schemas"]):
        errors.append("public_schemas name schemas of the pinned specification")
    errors.extend(json_schema_errors(value["json_schemas"], public))
    errors.extend(command_errors(value["tool"], command, value["schemas"], value["error_codes"], value["json_schemas"]))
    return errors


def param_errors(param: dict) -> list[str]:
    errors = []
    kinds = {"string": str, "path": str, "integer": int, "number": (int, float), "boolean": bool}

    def matches(value) -> bool:
        if param["type"] == "json":
            return True
        if param["type"] in {"integer", "number"} and isinstance(value, bool):
            return False
        return isinstance(value, kinds[param["type"]])

    if "default" in param:
        values = param["default"] if param["multiple"] and isinstance(param["default"], list) else [param["default"]]
        if param["required"]:
            errors.append(f"--{param['name']} is required and cannot also have a default")
        if not all(matches(item) for item in values):
            errors.append(f"--{param['name']} default does not match its type")
    if any(not matches(item) for item in param.get("enum", [])):
        errors.append(f"--{param['name']} enum values do not match its type")
    return errors


def json_schema_errors(json_schemas: dict, public: dict) -> list[str]:
    errors = []
    registry = Registry()
    for schema in public.values():
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    for identifier, schema in json_schemas.items():
        if identifier.startswith(PUBLIC_PREFIX):
            errors.append(f"public driver schema {identifier} is referenced by URN, never inlined")
            continue
        try:
            if schema.get("$id") != identifier or schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
                errors.append("product JSON Schema identity or dialect mismatch")
                continue
            if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", identifier) or "#" in identifier:
                errors.append("product schema IDs must be absolute resource identities without fragments")
                continue
            Draft202012Validator.check_schema(schema)
            registry = registry.with_resource(identifier, Resource.from_contents(schema))
        except SchemaError:
            errors.append("invalid product JSON Schema definition")
    if errors:
        return errors
    registry = registry.crawl()
    for identifier, schema in json_schemas.items():
        for _, base, node in schema_nodes(schema, identifier):
            if isinstance(node, dict) and "$ref" in node:
                try:
                    registry.resolver(base).lookup(node["$ref"])
                except Unresolvable:
                    errors.append("product schema references must resolve offline")
    return errors


# ---------------------------------------------------------------- other instances


def context_errors(value: dict) -> list[str]:
    errors = []
    authorization = value["authorization"]
    errors.extend(authorization_errors(authorization))
    roles = {component["role"] for component in value["environment"]["components"]}
    if "cli" not in roles:
        errors.append("context identifies the running CLI component")
    connection = value["environment"]["connection"]
    if connection["status"] == "connected" and connection["instance_id"] is None:
        errors.append("a connected backend names its instance")
    if not value["state"]["available"] and "reason" not in value["state"]:
        errors.append("an unavailable operation store explains why")
    return errors


def authorization_errors(value: dict) -> list[str]:
    errors = []
    granted = value["status"] == "granted"
    if granted and value["scope_id"] is None:
        errors.append("a granted scope names its scope_id")
    if value["status"] == "read_only" and (value["scope_id"] is not None or set(value["effects"]) - {"observe"} or value["dangerous"]):
        errors.append("read_only allows observe and nothing else")
    if not granted and value["status"] != "read_only" and value["reason"] is None:
        errors.append("an expired, revoked or invalid scope explains why")
    return errors


def doctor_errors(value: dict) -> list[str]:
    names = {check["check"] for check in value["checks"]}
    missing = [name for name in driver_contract()["self_description"]["doctor"]["required_checks"] if name not in names]
    return [f"doctor is missing the {name} check" for name in missing]


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_absolute(path: str) -> bool:
    return PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute()


def well_formed_prefix(prefix: str) -> bool:
    """A destination or locator prefix: an absolute path, or a URL without user info, query, fragment or dot segments."""
    if not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", prefix):
        return is_absolute(prefix)
    try:
        parts = urlsplit(prefix)
        segments = []
        for segment in parts.path.split("/"):
            if re.search(r"%(?![0-9a-fA-F]{2})", segment):
                return False
            segments.append(unquote(segment, errors="strict"))
    except ValueError:
        return False
    return ("@" not in parts.netloc and not parts.query and not parts.fragment and "\\" not in prefix
            and not any(segment in {".", ".."} or "/" in segment or "\\" in segment
                        or any(ord(character) < 32 for character in segment) for segment in segments))


def scope_errors(value: dict) -> list[str]:
    errors = []
    effects = set(value["effects"])
    if effects & set(driver_contract()["effects"]["dangerous"]) and not value["dangerous"]:
        errors.append("a scope that allows deliver, configure or physical effects must set dangerous")
    if effects & {"change", "persist"} and not value["roots"] and not value.get("targets"):
        errors.append("a write scope names the roots or targets it covers")
    if "artifact" in effects and not value["outputs"]:
        errors.append("an artifact scope names its output roots")
    if "deliver" in effects and not value.get("destinations"):
        errors.append("a deliver scope names the destinations it allows")
    if any(not is_absolute(path) for path in value["roots"] + value["outputs"]):
        errors.append("scope roots and outputs are absolute directories")
    prefixes = value.get("destinations", []) + [selector["locator_prefix"] for selector in value.get("targets", []) if "locator_prefix" in selector]
    if any(not well_formed_prefix(prefix) for prefix in prefixes):
        errors.append("destinations and locator prefixes are absolute paths or URLs without user info, queries, fragments or dot segments")
    if value["expires_at"] is not None and parse_time(value["expires_at"]) <= parse_time(value["granted_at"]):
        errors.append("a scope expires after it was granted")
    return errors


def semver_key(value: str):
    core = value.split("+", 1)[0].split("-", 1)
    numbers = tuple(int(part) for part in core[0].split("."))
    return numbers, (1,) if len(core) == 1 else (0, core[1])


def changelog_errors(value: dict) -> list[str]:
    errors = []
    versions = [entry["version"] for entry in value["entries"]]
    if [semver_key(version) for version in versions] != sorted((semver_key(version) for version in versions), reverse=True):
        errors.append("changelog entries are newest first")
    if "since" in value and any(semver_key(version) <= semver_key(value["since"]) for version in versions):
        errors.append("--since returns only versions newer than since")
    if "count" in value and value["count"] != len(value["entries"]):
        errors.append("changelog count must equal the returned entries")
    return errors


def adoption_lock_errors(value: dict) -> list[str]:
    errors = []
    paths = []
    for item in value["files"]:
        try:
            canonical_relative_path(item["path"])
            canonical_relative_path(item["source_path"])
        except InputError:
            errors.append("adoption paths must stay within their declared relative roots")
        paths.append(path_identity(item["path"]))
    if len(paths) != len(set(paths)):
        errors.append("adoption paths must be unique on case-insensitive filesystems")
    return errors


def release_manifest_errors(value: dict) -> list[str]:
    errors = []
    paths = []
    for component in value["components"]:
        path = component["path"]
        normalized = PurePosixPath(path)
        if path != normalized.as_posix() or any(p in {".", ".."} for p in normalized.parts):
            errors.append("component paths must be canonical relative package paths")
        if path == "manifest.json":
            errors.append("manifest cannot contain its own checksum")
        try:
            canonical_relative_path(path)
        except InputError:
            errors.append("component paths must be safe on the declared target platforms")
        paths.append(path_identity(path))
    if len(paths) != len(set(paths)):
        errors.append("component paths must be unique on case-insensitive targets too")
    if value["source_dirty"]:
        errors.append("release manifests cannot describe uncommitted source")
    if value["artifact_kind"] == "cli_product" and not any(c["role"] == "cli" for c in value["components"]):
        errors.append("a CLI product must identify its CLI component")
    entries = [entry for group in value["compatibility"].values() for entry in group]
    if any(entry["validation"] == "verified" and not entry["evidence_refs"] for entry in entries):
        errors.append("a verified compatibility entry names its evidence")
    return errors


def evidence_errors(value: dict) -> list[str]:
    errors = []
    if value["package_sha256"] is None and not value["limitations"]:
        errors.append("development evidence without a package must explain its limit")
    if value["kind"] in {"real_application", "artifact_consumer"} and (not value["target"] or not value["snapshot_id"]):
        errors.append("real-result evidence must identify the target and checked snapshot")
    if value["kind"] in {"real_application", "artifact_consumer", "simulated_adapter"} and not value["commands"]:
        errors.append("operational evidence records the commands it ran")
    return errors


@lru_cache(maxsize=1)
def verification_helpers():
    """Use only this pinned checkout/starter's pure helper, never a path from adapter data."""
    source = ROOT / "packages" / "tool_driver_sdk" / "verification.py"
    if not source.is_file():
        source = ROOT.parent / "vendor" / "sdk" / "tool_driver_sdk" / "verification.py"
    module = types.ModuleType("_pinned_verification_result")
    exec(compile(source.read_bytes(), str(source), "exec"), module.__dict__)
    return module


def verification_result_errors(value: dict) -> list[str]:
    return verification_helpers().profile_errors(value)


SEMANTIC = {
    "receipt": receipt_errors, "plan": plan_errors, "response": response_errors, "reference": reference_errors,
    "reference-index": reference_index_errors, "reference-command": reference_command_errors,
    "operation-status": operation_status_errors, "workflow": workflow_errors, "batch": batch_errors,
    "query-page": query_page_errors, "context": context_errors, "doctor": doctor_errors, "scope": scope_errors,
    "changelog": changelog_errors, "adoption-lock": adoption_lock_errors, "release-manifest": release_manifest_errors,
    "evidence": evidence_errors, "verification-result": verification_result_errors,
}


def semantic_errors(kind: str, value: dict) -> list[str]:
    if kind == "product-registry":
        from generate_product import GenerationError, build_contract
        try:
            build_contract(value)
        except GenerationError as exc:
            return exc.errors or [str(exc)]
        return []
    checker = SEMANTIC.get(kind)
    return checker(value) if checker else []


def instance_errors(kind: str, value) -> list[str]:
    """Schema plus semantic errors for a nested instance."""
    checked = validators()[kind + ".schema.json"]
    problems = [f"{kind}: {error.message}" for error in checked.iter_errors(value)]
    return problems or semantic_errors(kind, value)


# ---------------------------------------------------------------- base, contract and documents


def base_errors() -> list[str]:
    errors = []
    lock = load(ROOT / "base" / "base-lock.json")
    contract = driver_contract()
    if lock["spec"] != "ai-native-cli-spec" or lock["tag"] != contract["base"]["tag"]:
        errors.append("the driver contract and the base lock must pin the same base tag")
    if not re.fullmatch(r"[0-9a-f]{40}", lock["commit"]):
        errors.append("the base lock records the tagged commit")
    listed = set()
    for item in lock["files"]:
        path = ROOT / item["path"]
        listed.add(item["path"])
        if not path.is_file() or hashlib.sha256(normalized_bytes(path)).hexdigest() != item["sha256"]:
            errors.append(f"vendored base file differs from its pinned bytes: {item['path']}")
    present = {p.relative_to(ROOT).as_posix() for p in (ROOT / "base").rglob("*") if p.is_file() and p.name != "base-lock.json"}
    if present != listed:
        errors.append("base/ must contain exactly the files its lock lists")
    if len({base_contract()["contract_version"], lock["contract_version"], contract["base"]["contract_version"]}) != 1:
        errors.append("the base contract, the base lock and the driver contract name different base contract versions")
    core = base_contract()["error_codes"]["core"]
    for code, entry in contract["error_codes"]["extensions"].items():
        if code in core:
            errors.append(f"{code} is a base core code and cannot be redefined")
        if entry["exit"] not in allowed_exits():
            errors.append(f"{code} uses an exit the base does not define")
        if entry["exit"] == 9 and code != "E_HUMAN_REQUIRED":
            errors.append(f"{code} cannot use the human-action exit 9")
    known = base_contract()["error_codes"]["extension"]["known_extensions"]
    for code, entry in known.items():
        ours = contract["error_codes"]["extensions"].get(code)
        if ours and (ours["exit"], ours["retryable"]) != (entry["exit"], entry["retryable"]):
            errors.append(f"{code} must match the base known extension")
    return errors


def contract_errors() -> list[str]:
    errors = []
    contract = driver_contract()
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if contract["spec_version"] != version:
        errors.append("driver contract spec_version differs from VERSION")
    profile = public_schemas()[PUBLIC_PREFIX + "common:2.0"]["$defs"]["driver_profile"]["properties"]
    if contract["schema_version"] != profile["schema_version"]["const"]:
        errors.append("driver contract schema_version differs from the schemas")
    rules = contract["receipt_codes"]["rules"]
    if rules[-1]["when"] or not rules[-1]["ok"]:
        errors.append("receipt rules end with the success catch-all")
    conditions = set(contract["receipt_codes"]["conditions"])
    if any(name not in conditions for rule in rules for name in rule["when"]):
        errors.append("receipt rules use only declared conditions")
    codes = code_table()
    if any(code not in codes for rule in rules for code in rule["codes"]):
        errors.append("receipt rules use only core or driver error codes")
    return errors


def document_errors() -> list[str]:
    errors, identifiers = [], set()
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    status = (ROOT / "STATUS").read_text(encoding="utf-8").strip()
    if status not in {"draft", "experimental", "stable"}:
        errors.append("STATUS must declare a supported publication maturity")
    for path in [ROOT / "README.md", ROOT / "AGENTS.md", *sorted((ROOT / "docs").glob("*.md")), ROOT / "examples" / "README.md"]:
        text = path.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
                continue
            target = target.split("#", 1)[0]
            if not (path.parent / target).exists():
                errors.append(f"{path.relative_to(ROOT)}: missing local link {target}")
        if path.name in CLAUSE_DOCUMENTS:
            if version not in text:
                errors.append(f"{path.name}: missing current spec version")
            if f"状态：`{status}`" not in text:
                errors.append(f"{path.name}: maturity differs from STATUS")
            found = re.findall(r"^### ([A-Z]{3}-\d{3})\b", text, re.M)
            if not found:
                errors.append(f"{path.name}: no addressable clauses")
            for identifier in found:
                if not identifier.startswith(CLAUSE_DOCUMENTS[path.name] + "-") or identifier in identifiers:
                    errors.append(f"{path.name}: invalid or duplicate clause {identifier}")
                identifiers.add(identifier)
    missing = set(CLAUSE_DOCUMENTS) - {p.name for p in (ROOT / "docs").glob("*.md")}
    if missing:
        errors.append(f"missing clause documents: {sorted(missing)}")
    return errors


def clause_text(text: str, identifier: str) -> str:
    match = re.search(rf"^### {identifier}\b.*?(?=^##)", text + "\n## ", re.M | re.S)
    return match.group(0) if match else ""


def prose_errors() -> list[str]:
    """Tables and enumerations the prose repeats must match the machine contract."""
    errors = []
    contract = driver_contract()
    docs = ROOT / "docs"
    layering = (docs / "LAYERING.md").read_text(encoding="utf-8")
    rows = {code: (int(exit_code), retryable == "true")
            for code, exit_code, retryable in re.findall(r"^\| `(E_[A-Z0-9_]+)` \| (\d+) \| (true|false) \|", layering, re.M)}
    expected = {code: (entry["exit"], entry["retryable"]) for code, entry in contract["error_codes"]["extensions"].items()}
    if rows != expected:
        errors.append("LAYERING.md: the LAY-004 table differs from contract error_codes.extensions")
    core = (docs / "CORE-SPEC.md").read_text(encoding="utf-8")
    lists = [("COR-005", contract["error_details"]["problem"]), ("COR-005", contract["error_details"]["conflict_reason"]),
             ("COR-024", contract["error_details"]["human_action"]), ("COR-016", operation_states()),
             ("COR-008", contract["effects"]["all"])]
    for identifier, values in lists:
        section = clause_text(core, identifier)
        missing = [value for value in values if f"`{value}`" not in section]
        if missing:
            errors.append(f"CORE-SPEC.md {identifier} does not list {', '.join(missing)}")
    problem_line = next((line for line in clause_text(core, "COR-005").splitlines() if "`problem` 的取值为" in line), "")
    if set(re.findall(r"`([a-z_]+)`", problem_line)) - {"problem"} != set(contract["error_details"]["problem"]):
        errors.append("CORE-SPEC.md COR-005 lists exactly the problem values of the contract")
    return errors


def example_errors() -> list[str]:
    failures = []
    checked = validators()
    index = load(ROOT / "examples/index.json")
    if index["spec_version"] != (ROOT / "VERSION").read_text(encoding="utf-8").strip():
        failures.append("example index version differs from VERSION")
    seen = set()
    for item in index["examples"]:
        relative = item["path"]
        if relative in seen:
            failures.append(f"duplicate example {relative}")
        seen.add(relative)
        value = load(ROOT / relative)
        validator = checked[item["schema"]]
        schema_errors = list(validator.iter_errors(value))
        semantic = [] if schema_errors else semantic_errors(item["schema"].removesuffix(".schema.json"), value)
        valid = not schema_errors and not semantic
        if valid != item["valid"]:
            reason = [e.message for e in schema_errors] + semantic
            failures.append(f"{relative}: expected valid={item['valid']}, got {valid}: {reason}")
    actual = {p.relative_to(ROOT).as_posix() for d in ["valid", "invalid"] for p in (ROOT / "examples" / d).glob("*.json")}
    if actual != seen:
        failures.append("example index must enumerate every positive and negative fixture")
    return failures


def envelope(started: float, failures: list[str]) -> dict:
    meta = {"duration_ms": round((time.monotonic() - started) * 1000, 3)}
    if not failures:
        return {"ok": True, "schema_version": "1.0", "meta": meta,
                "data": {"scope": "specification_structure_and_static_examples", "errors": [],
                         "limitations": ["Static checks do not show that any receipt or evidence came from a real tool."]}}
    return {"ok": False, "schema_version": "1.0", "meta": meta,
            "error": {"code": "E_VALIDATION", "message": "the specification does not pass its own checks", "retryable": False,
                      "details": {"errors": failures, "error_count": len(failures),
                                  "next_action": "fix the listed files and run scripts/check_spec.py again"}}}


def main() -> int:
    started = time.monotonic()
    failures = []
    try:
        failures.extend(base_errors())
        failures.extend(contract_errors())
        failures.extend(example_errors())
        failures.extend(document_errors())
        failures.extend(prose_errors())
    except (ValueError, OSError, KeyError, TypeError) as exc:
        failures.append(f"{type(exc).__name__}: {exc}")
    text = json.dumps(envelope(started, failures), ensure_ascii=False) + "\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(text.encode("utf-8"))
        buffer.flush()
    else:
        sys.stdout.write(text)
    return 0 if not failures else 2


if __name__ == "__main__":
    sys.exit(main())
