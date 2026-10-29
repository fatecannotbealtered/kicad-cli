"""Opt-in verification records. These guards compare declared observations, not application truth.

No application, file reader, checker, unit conversion or external code is run here.
Adapters must obtain observations and evidence independently before constructing this record.
"""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from decimal import Decimal, localcontext

PROFILE = "urn:tool-driver-cli-spec:verification-result:2.0"
PROFILE_PREFIX = "urn:tool-driver-cli-spec:verification-result:"
EXTENSION = "verification_result"
UNTRUSTED_PATH = "outcome.verification.extensions." + EXTENSION
TARGET_KEYS = ("mode", "engine_id", "instance_id", "object_id", "locator", "extensions")
STRUCTURED_SOURCES = {"application_readback", "file_consumer", "device_readback", "native_checker", "custom_checker"}
POINTER = re.compile(r"(?:/(?:[^~/]|~[01])*)*\Z")


def _equal(left, right):
    """JSON equality: booleans are not numbers; object member order is immaterial."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_equal(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return type(left) is type(right) and left == right


def _same_target(left, right):
    return isinstance(left, dict) and isinstance(right, dict) and all(_equal(left.get(key), right.get(key)) for key in TARGET_KEYS)


def _same_snapshot(left, right):
    # Reading the same immutable state later need not repeat its original observation timestamp.
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    return _equal({k: v for k, v in left.items() if k != "observed_at"},
                  {k: v for k, v in right.items() if k != "observed_at"})


def _at(value, pointer):
    for token in pointer.split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict):
            value = value[token]
        elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", token):
            value = value[int(token)]
        else:
            raise ValueError("finding identity path is unavailable")
    return value


def _identities(findings, paths):
    if not isinstance(findings, list):
        raise ValueError("no_new_findings needs finding arrays, not counts")
    result = set()
    for finding in findings:
        keys = [_at(finding, path) for path in paths]
        if any(isinstance(key, bool) or not isinstance(key, (str, int)) or key == "" for key in keys):
            raise ValueError("finding identity must be a nonempty string or integer")
        identity = json.dumps(keys, ensure_ascii=False, separators=(",", ":"))
        if identity in result:
            raise ValueError("finding identities are ambiguous; add identifying fields")
        result.add(identity)
    return result


def profile_errors(profile):
    """Additional relations after verification-result Schema validation. Incomplete evidence is valid data."""
    errors = []
    requirements = profile["requirements"]
    ids = [item["id"] for item in requirements]
    observed_ids = [item["requirement_id"] for item in profile["observations"]]
    if len(ids) != len(set(ids)):
        errors.append("verification requirement IDs must be unique")
    if len(observed_ids) != len(set(observed_ids)):
        errors.append("each requirement has at most one observation; use distinct IDs for distinct methods")
    if set(observed_ids) - set(ids):
        errors.append("observations must identify declared requirements")
    for requirement in requirements:
        if not POINTER.fullmatch(requirement["field_path"]):
            errors.append("field_path must be an RFC 6901 JSON pointer")
        for path in requirement["comparison"].get("identity_fields", []):
            if not POINTER.fullmatch(path):
                errors.append("finding identity fields must be JSON pointers")
    for observation in profile["observations"]:
        coverage = observation["source"]["coverage"]
        for path in coverage["checked_paths"] + [item["field_path"] for item in coverage["not_checked"]]:
            if not POINTER.fullmatch(path):
                errors.append("coverage fields must be JSON pointers")
        if set(coverage["checked_paths"]) & {item["field_path"] for item in coverage["not_checked"]}:
            errors.append("a source cannot declare the same field checked and unchecked")
    for item in profile["not_checked"]:
        if not POINTER.fullmatch(item["field_path"]):
            errors.append("unchecked fields must be JSON pointers")

    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            return False
        if isinstance(value, dict):
            return all(finite(item) for item in value.values())
        if isinstance(value, list):
            return all(finite(item) for item in value)
        return True

    if not finite(profile):
        errors.append("verification values and tolerances must be finite JSON")
    return errors


def _compare(requirement, observation):
    expected, observed = requirement["expected"], observation["observed"]
    if not _equal(expected["unit"], observed["unit"]):
        return "incomplete", "expected and observed units differ; no implicit unit conversion"
    comparison = requirement["comparison"]
    operator = comparison["operator"]
    left, right = expected["value"], observed["value"]
    if operator == "equals":
        matched = _equal(left, right)
    elif operator == "within_tolerance":
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in (left, right)):
            return "incomplete", "numeric tolerance requires numeric observations and expectations"
        texts = [str(value) for value in (left, right, comparison["absolute"])]
        with localcontext() as context:
            context.prec = max(28, *(len(text) for text in texts)) + 4
            matched = abs(Decimal(texts[0]) - Decimal(texts[1])) <= Decimal(texts[2])
    else:
        try:
            baseline = _identities(left, comparison["identity_fields"])
            actual = _identities(right, comparison["identity_fields"])
        except (KeyError, IndexError, ValueError) as error:
            return "incomplete", str(error)
        matched = actual.issubset(baseline)
    return ("passed", None) if matched else ("failed", "observed value does not satisfy the declared comparison")


def evaluate(profile, *, target=None, snapshot=None, bind=False):
    """Derive generic verification checks from a Schema-validated record; never acquire or invent observations."""
    binding_reason = None
    if profile["snapshot"] is None:
        binding_reason = "current target snapshot is unavailable"
    elif bind and (not _same_target(profile["target"], target) or not _same_snapshot(profile["snapshot"], snapshot)):
        binding_reason = "verification record is not bound to this receipt's current target and snapshot"
    observations = {item["requirement_id"]: item for item in profile["observations"]}
    checks, unchecked, refs = [], [], []
    for requirement in profile["requirements"]:
        identifier = requirement["id"]
        observation = observations.get(identifier)
        status, reason = "incomplete", binding_reason
        source = observation["source"] if observation else None
        if reason is None and source is None:
            reason = "required observation is missing"
        if reason is None and source["completion"] != "completed":
            reason = "observation source did not complete: " + source["completion"]
        if reason is None and (not _same_target(source["target"], profile["target"])
                               or not _same_snapshot(source["snapshot"], profile["snapshot"])):
            reason = "observation belongs to a different target or snapshot"
        if reason is None and source["kind"] not in requirement["allowed_sources"]:
            reason = "observation source is not allowed for this requirement"
        allowed = STRUCTURED_SOURCES | ({"screenshot"} if requirement["kind"] == "visual" else set())
        if reason is None and source["kind"] not in allowed:
            reason = "input echo, writer self-check and screenshots do not establish structural readback"
        independence = requirement["independence"]
        if reason is None and independence["required"]:
            defects = set(source["defect_sources"])
            excluded = set(independence["writer_sources"]) | set(independence["expectation_sources"])
            if (not defects or not source["independence_basis"].strip()
                    or not independence["writer_sources"] or not independence["expectation_sources"]):
                reason = "independent verification lacks its declared defect-source basis"
            elif defects & excluded:
                reason = "observation shares a declared defect source with the writer or expectation"
        if reason is None and requirement["field_path"] not in source["coverage"]["checked_paths"]:
            reason = "required field is outside the source's checked coverage"
        if reason is None and observation["observed"] is None:
            reason = "actual observed value is unavailable"
        if reason is None:
            status, reason = _compare(requirement, observation)
        check = {"id": identifier, "status": status, "required": True}
        if source:
            check["evidence_ref"] = source["evidence_ref"]
            refs.append(source["evidence_ref"])
        if reason:
            check["reason"] = reason
        checks.append(check)
        if status == "incomplete":
            unchecked.append({"id": identifier, "required": True, "reason": reason})
    exclusions = profile["not_checked"] + [item for observation in profile["observations"]
                                           for item in observation["source"]["coverage"]["not_checked"]]
    for item in exclusions:
        entry = {"id": "field:" + item["field_path"], "required": item["required"], "reason": item["reason"]}
        if entry not in unchecked:
            unchecked.append(entry)
    statuses = {check["status"] for check in checks}
    status = "failed" if "failed" in statuses else (
        "incomplete" if "incomplete" in statuses or any(item["required"] for item in unchecked) else "passed")
    coverage = "complete" if "incomplete" not in statuses and not any(item["required"] for item in unchecked) else (
        "partial" if any(item["observed"] is not None for item in profile["observations"]) else "none")
    result = {"status": status, "snapshot_id": (snapshot or {}).get("id") if bind else (profile["snapshot"] or {}).get("id"),
              "coverage": coverage, "checks": checks, "not_checked": unchecked, "evidence_refs": list(dict.fromkeys(refs))}
    if status == "incomplete":
        result["reason"] = "Required readback or independent verification is incomplete; inspect checks and not_checked."
    return result


def build_verification(profile, *, scope: str, validator):
    """Adapter helper: validate the optional profile and derive the existing verification object."""
    profile = validator.validate("verification-result", profile)
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError("verification scope must name what was checked")
    result = dict(evaluate(profile), scope=scope, extensions={EXTENSION: profile})
    return result


def guard_receipt(receipt, profile):
    """Preserve native dimensions and raw data while refusing an overclaimed verification result."""
    result = deepcopy(receipt)
    verification = result["outcome"]["verification"]
    computed = evaluate(profile, target=result["target"], snapshot=result["snapshot_after"], bind=True)
    owned = {check["id"] for check in computed["checks"]}
    old_status = verification["status"]
    old_checks = verification["checks"]
    prior = {check["id"]: check for check in old_checks}
    for index, check in enumerate(computed["checks"]):
        old = prior.get(check["id"])
        if old and (old["status"] == "failed" or (old["status"] == "incomplete" and check["status"] == "passed")):
            computed["checks"][index] = old
    computed["checks"] = [check for check in old_checks if check["id"] not in owned] + computed["checks"]
    computed["not_checked"] = [item for item in verification["not_checked"] if item["id"] not in owned] + computed["not_checked"]
    computed["evidence_refs"] = list(dict.fromkeys(verification["evidence_refs"] + computed["evidence_refs"]))
    # An existing failure or incomplete independent check is never upgraded by this optional profile.
    required = [check["status"] for check in computed["checks"] if check["required"]]
    if old_status == "failed" or "failed" in required:
        computed["status"] = "failed"
    elif old_status == "incomplete" or any(status != "passed" for status in required) or any(item["required"] for item in computed["not_checked"]):
        computed["status"] = "incomplete"
    if computed["status"] == "incomplete":
        computed.setdefault("reason", verification.get("reason") or "Required verification is incomplete.")
    if old_status in {"failed", "incomplete"} and verification["coverage"] != "complete":
        computed["coverage"] = verification["coverage"]
    if (any(status not in {"passed", "failed"} for status in required)
            or any(item["required"] for item in computed["not_checked"])) and computed["coverage"] == "complete":
        computed["coverage"] = "partial" if computed["evidence_refs"] else "none"
    verification.update(computed)
    result["_untrusted"] = list(dict.fromkeys(result.get("_untrusted", []) + [UNTRUSTED_PATH]))
    if verification["status"] in {"failed", "incomplete"} and result["failed_stage"] is None:
        result["failed_stage"] = "verify"
    return result


def receipt_profile_errors(receipt, profile):
    computed = evaluate(profile, target=receipt["target"], snapshot=receipt["snapshot_after"], bind=True)
    reported = receipt["outcome"]["verification"]
    errors = []
    checks = {item["id"]: item for item in reported["checks"]}
    if len(checks) != len(reported["checks"]):
        errors.append("profile verification check IDs must be unique")
    for check in computed["checks"]:
        actual = checks.get(check["id"])
        if actual is None or not actual["required"]:
            errors.append("profile requirements must remain required generic checks: " + check["id"])
        elif actual["status"] == "passed" and check["status"] != "passed":
            errors.append("generic check cannot overclaim its structured observation: " + check["id"])
    if computed["status"] != "passed" and reported["status"] not in {"failed", "incomplete"}:
        errors.append("structured readback does not support the reported verification status")
    for item in computed["not_checked"]:
        if item not in reported["not_checked"]:
            errors.append("profile unchecked fields and reasons must be retained")
    if UNTRUSTED_PATH not in receipt.get("_untrusted", []):
        errors.append("structured observations must be marked as untrusted data")
    return errors
