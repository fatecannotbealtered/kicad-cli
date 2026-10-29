"""Separate physical phase evidence from the complete public partial-resolution loop."""

import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path


def verdict(facts):
    required = (
        "query_verified",
        "cleanup_attributable",
        "resolve_succeeded",
        "resolution_partial",
        "resolved_query_partial",
        "blocked_before_resolution",
        "new_preview_allowed",
        "duplicate_resolution_refused",
        "ordered_calls",
        "formal_files_preserved",
        "protected_files_preserved",
        "final_file_set_exact",
        "baselines_archived",
        "confirmation_not_replayed",
    )
    checks = {name: facts.get(name) is True for name in required}
    checks["observed_partial"] = facts.get("observed_outcome") == "partial"
    recovered = all(checks.values())
    hit = facts.get("stage_hit") is True
    return {
        "stage_hit": hit,
        "partial_recovery_passed": recovered,
        "checks": checks,
        "unknown_safety_passed": bool(
            facts.get("observed_outcome") == "unknown"
            and facts.get("blocked_before_resolution")
            and facts.get("unknown_still_blocked")
            and facts.get("formal_files_preserved")
            and facts.get("protected_files_preserved")
            and facts.get("confirmation_not_replayed")
        ),
        "status": "uncovered" if not hit else "passed" if recovered else "failed",
    }


def read_facts(directory, *, stage_hit, operation):
    """Use retained process receipts and current bytes, never their summary success flags."""
    directory = Path(directory)

    def load(path):
        return json.loads(path.read_bytes())

    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

    binding = load(directory / "SETUP.json")["binding"]
    records, envelopes = {}, {}
    for path in (directory / "calls").glob("*.call.json"):
        name = path.name.removesuffix(".call.json")
        record = load(path)
        stdout = path.with_name(name + ".stdout")
        if (
            record.get("stdout_sha256") == sha(stdout)
            and record.get("source_binding") == binding
            and record.get("execution") in {"completed", "nonzero_exit"}
        ):
            records[name], envelopes[name] = record, load(stdout)

    def response(name, code=0, error=None):
        rec, env = records.get(name, {}), envelopes.get(name, {})
        return (
            rec.get("exit") == code
            and env.get("ok") is (error is None)
            and (error is None or env.get("error", {}).get("code") == error)
        )

    def status(name):
        return envelopes.get(name, {}).get("data", {}).get("operation_status", {})

    def ordered(first, second):
        try:
            return datetime.fromisoformat(records[first]["finished_at"].replace("Z", "+00:00")) <= (
                datetime.fromisoformat(records[second]["started_at"].replace("Z", "+00:00"))
            )
        except (KeyError, ValueError):
            return False

    observed = status("fresh-show").get("extensions", {}).get("recovery_readback") or {}
    preview = envelopes.get("later-preview", {}).get("data", {}).get("preview") or {}
    witness = load(directory / "FROZEN-WITNESS.json")
    journal = (witness.get("journal") or {}).get("value") or witness["checkpoint"]["value"][
        "journal"
    ]
    rows = journal["files"]
    frozen = load(directory / "FROZEN.json")["files"]
    targets = {Path(row["path"]).name: witness["current"][row["path"]] for row in rows}
    current = {
        p.relative_to(directory / "project").as_posix(): sha(p)
        for p in (directory / "project").rglob("*")
        if p.is_file()
    }
    all_commands = [load(p).get("command", []) for p in (directory / "calls").glob("*.call.json")]
    archives = list((directory / "state/recovery" / operation).glob("baseline-*"))
    return {
        "stage_hit": stage_hit,
        "observed_outcome": observed.get("outcome"),
        "query_verified": response("fresh-show")
        and status("fresh-show").get("operation_id") == operation,
        "cleanup_attributable": observed.get("cleanup", {}).get("status") == "attributable",
        "resolve_succeeded": response("resolve"),
        "resolution_partial": status("resolve").get("operation_id") == operation
        and (status("resolve").get("resolution") or {}).get("outcome") == "partial",
        "resolved_query_partial": response("resolved-show")
        and status("resolved-show").get("operation_id") == operation
        and (status("resolved-show").get("resolution") or {}).get("outcome") == "partial",
        "blocked_before_resolution": response("unresolved-preview", 6, "E_CONFLICT"),
        "new_preview_allowed": response("later-preview")
        and preview.get("can_apply") is True
        and bool(preview.get("operation_id"))
        and preview.get("operation_id") != operation,
        "duplicate_resolution_refused": response("duplicate-resolve", 2, "E_VALIDATION"),
        "ordered_calls": all(
            ordered(a, b)
            for a, b in (
                ("fresh-show", "unresolved-preview"),
                ("unresolved-preview", "resolve"),
                ("resolve", "resolved-show"),
                ("resolve", "duplicate-resolve"),
                ("resolved-show", "later-preview"),
            )
        ),
        "formal_files_preserved": all(current.get(n) == h for n, h in targets.items()),
        "protected_files_preserved": all(
            current.get(n) == h for n, h in frozen.items() if n not in targets
        ),
        "final_file_set_exact": set(current) == set(frozen),
        "baselines_archived": Counter(map(sha, archives))
        == Counter(row["baseline"] for row in rows),
        "confirmation_not_replayed": not any("--confirm" in command for command in all_commands),
        "unknown_still_blocked": response("later-preview", 6, "E_CONFLICT"),
        "resolved_top_level_status": status("resolved-show").get("status"),
    }
