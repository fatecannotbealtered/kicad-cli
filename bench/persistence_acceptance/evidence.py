"""Allowlisted synthetic projections; secret material and local paths never exported."""

import datetime
import hashlib
import hmac
import json
import re
from pathlib import Path


def load(path):
    return json.loads(Path(path).read_bytes())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def signed_valid(value, state):
    body = dict(value)
    signature = body.pop("hmac", "")
    key = bytes.fromhex((state / "confirm.secret").read_text(encoding="ascii"))
    actual = hmac.new(
        key, json.dumps(body, sort_keys=True, separators=(",", ":")).encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, actual)


def stage_hit(row):
    states = row["frozen_file_states"]
    values = [
        "baseline"
        if r["observed"] == r["baseline"]
        else "intended"
        if r["observed"] == r["intended"]
        else "other"
        for r in states
    ]
    owned = row["process"]["owned_job_member"] and row["authentication"]["locally_verified"]
    case = row["case"].split("-", 1)[0]
    if case in {"F0", "F4"}:
        result = set(values) == {"baseline"}
    elif case == "F1":
        result = any(p["bytes"] > 0 and p["intended_prefix"] for p in row["pending_files"]) or set(
            values
        ) == {"baseline", "intended"}
    elif case == "F5":
        result = (
            set(values) == {"baseline", "intended"}
            and row["process"]["birth_rechecked_before_attach"]
            and bool(row["process"]["creation_filetime"])
        )
    elif case == "F2":
        result = (
            set(values) == {"intended"}
            and row["cleanup_phase"] in {"cleaning", "cleaned"}
            and not row["complete_receipt_delivered"]
        )
    elif case == "F3":
        result = row["cleanup_phase"] == "cleaning"
    else:
        result = False
    return bool(owned and result)


def assert_publishable(value):
    forbidden = {
        "confirm_token",
        "hmac",
        "secret",
        "argv",
        "environment",
        "CommandLine",
        "raw_dump",
    }

    def visit(node):
        if isinstance(node, dict):
            if forbidden.intersection(node):
                raise ValueError("Secret/raw-process field in public projection")
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, str) and re.search(
            r"\b(?:ct_|sk-)[A-Za-z0-9]|(?<![A-Za-z0-9])[A-Za-z]:[\\/]|\\\\", node
        ):
            raise ValueError("Token or local absolute path in public projection")

    visit(value)


def project_case(directory):
    directory = Path(directory)
    result = load(directory / "RESULT.json")
    witness = load(directory / "FROZEN-WITNESS.json")
    freeze = load(directory / "FROZEN.json")
    stop = load(directory / "CONTROL-FINISH.json")
    record = witness["journal"] or witness["checkpoint"]
    if not signed_valid(record["value"], directory / "state"):
        raise ValueError("Original signed witness is invalid")
    journal = (
        witness["journal"]["value"]
        if witness["journal"]
        else witness["checkpoint"]["value"]["journal"]
    )
    if journal["operation_id"] != result["operation_id"]:
        raise ValueError("Signed journal belongs to another operation")
    files = journal["files"]
    alias = {r["path"]: "document-" + str(i + 1) for i, r in enumerate(files)}

    def checkpoint_projection(checkpoint):
        if not checkpoint:
            return None
        if not signed_valid(checkpoint, directory / "state"):
            raise ValueError("Checkpoint authentication failed")
        actions = []
        for action in checkpoint["actions"]:
            path = action["path"]
            actions.append(
                {
                    "document": next(
                        (name for source, name in alias.items() if path.startswith(source)), None
                    ),
                    "kind": Path(path).suffix,
                    "expected_sha256": action["sha256"],
                }
            )
        return {
            "phase": checkpoint["phase"],
            "cursor": checkpoint["cursor"],
            "pending": checkpoint["pending"],
            "actions": actions,
            "locally_verified": True,
        }

    final_checkpoint = (
        directory
        / "state/document-transactions"
        / (hashlib.sha256(result["operation_id"].encode()).hexdigest() + ".json")
    )
    process = witness["writer"]["process"]
    stamp = process.get("CreationDate", "")
    birth = (
        datetime.datetime.fromtimestamp(
            int(re.search(r"-?\d+", stamp).group()) / 1000, datetime.timezone.utc
        ).isoformat()
        if stamp.startswith("/Date")
        else stamp
    )
    held = next(
        e for e in witness["events"] if e["action"] == "debug_event_held_all_threads_suspended"
    )
    states = [
        {
            "alias": alias[r["path"]],
            "fixture_file": Path(r["path"]).name,
            "baseline": r["baseline"],
            "intended": r["intended"],
            "observed": witness["current"][r["path"]],
        }
        for r in files
    ]
    after = [
        {"alias": alias[r["path"]], "sha256": digest(directory / "project" / Path(r["path"]).name)}
        for r in files
    ]
    controls = {}
    for name in (
        "duplicate-confirm",
        "duplicate-resolve",
        "inappropriate-applied-resolve",
        "later-preview",
        "resolve",
        "unresolved-preview",
    ):
        call = directory / "calls" / (name + ".call.json")
        if call.exists():
            data = load(call)
            envelope = load(call.with_name(name + ".stdout"))
            controls[name] = {
                "exit": data.get("exit"),
                "error_code": envelope.get("error", {}).get("code"),
                "ok": envelope.get("ok"),
            }
    primary_names = {Path(r["path"]).name for r in files}
    protected = {name: sha for name, sha in freeze["files"].items() if name not in primary_names}
    protected_after = {name: digest(directory / "project" / name) for name in protected}
    if protected != protected_after:
        raise ValueError("Protected synthetic input changed")
    row = {
        "case": result["id"],
        "input_kind": "Synthetic saved U4 fixture; valid trailing whitespace preparation",
        "binding": result["binding"],
        "method_files_sha256": {
            name: freeze[name]
            for name in ("matrix_sha256", "controller_sha256", "harness_sha256")
            if name in freeze
        },
        "declared_case": result["case"],
        "started_at": result["started_at"],
        "stopped_at": stop["at"],
        "finished_at": result["finished_at"],
        "operation_id": result["operation_id"],
        "frozen_file_states": states,
        "final_file_states": after,
        "protected_files": [
            {"alias": "protected-" + str(i + 1), "before": sha, "after": sha}
            for i, sha in enumerate(protected.values())
        ],
        "process": {
            "pid": process["ProcessId"],
            "parent_pid": process["ParentProcessId"],
            "born_utc": birth,
            "frozen_at": held["at"],
            "owned_job_member": held["owned_job_member"],
            "debug_event": held["event_code"],
            "creation_filetime": held.get("creation_filetime"),
            "birth_rechecked_before_attach": held.get("birth_rechecked", False),
        },
        "controlled_exit": {
            key: stop["stop"].get(key)
            for key in ("parent_exit", "writer_exit", "external_requested_exit")
        },
        "authentication": {
            "locally_verified": True,
            "original_signed_record_sha256": record["sha256"],
            "verification_boundary": (
                "Local HMAC checked against retained secret. No key or authenticator published; "
                "reviewer can reproduce verification on a new authorized run."
            ),
        },
        "pending_files": [
            {"bytes": p["bytes"], "sha256": p["sha256"], "intended_prefix": p["is_intended_prefix"]}
            for p in witness["pending_files"].values()
        ],
        "cleanup_phase": result.get("actual_checkpoint_phase"),
        "cleanup_before_stop": checkpoint_projection(
            witness["checkpoint"]["value"] if witness["checkpoint"] else None
        ),
        "cleanup_after_recovery": checkpoint_projection(
            load(final_checkpoint) if final_checkpoint.exists() else None
        ),
        "complete_receipt_delivered": result.get("complete_receipt_delivered"),
        "public_query_status": result.get("fresh_status"),
        "observed_outcome": result.get("observed_outcome"),
        "registered_outcome": result.get("resolved"),
        "resolved_public_status": result.get("resolved_public_status"),
        "original_confirmation_never_replayed": result.get("original_confirmation_never_replayed"),
        "negative_and_recovery_calls": controls,
        "baseline_archive_hashes": sorted(result.get("archives", {}).values()),
        "external_drift": None,
        "private_raw_witness_sha256": digest(directory / "FROZEN-WITNESS.json"),
    }
    drift = directory / "EXTERNAL-DRIFT.json"
    if drift.exists():
        d = load(drift)
        row["external_drift"] = {
            "before": d["before"],
            "after": d["after"],
            "bytes_appended": 1,
            "only_after_owned_stop": True,
        }
    row["stage_hit_from_published_facts"] = stage_hit(row)
    if row["stage_hit_from_published_facts"] != result["hit"]:
        raise ValueError("Stage interpretation disagrees with original result")
    if row["case"].startswith("F5"):
        from .f5 import read_facts, verdict

        row["f5_facts"] = read_facts(
            directory,
            stage_hit=row["stage_hit_from_published_facts"],
            operation=result["operation_id"],
        )
        row["f5_acceptance"] = verdict(row["f5_facts"])
    assert_publishable(row)
    return row


def export_previous(evidence, output):
    root = Path(evidence)
    rows = [project_case(root / path) for path in load(root / "FAULT-CASE-OUTPUTS.json").values()]
    result = {
        "format": "synthetic-persistence-witness.v1",
        "execution_policy": "Read-only derivation; no successful operation replayed",
        "cases": rows,
        "limits": [
            "F1 shows pending content, not a formal partial save.",
            "Old controller observed birth while frozen. The delivered controller additionally "
            "checks birth before and after attachment.",
            "Local authentication audit is not a public cryptographic attestation; "
            "secrets and raw engineering files remain local.",
        ],
    }
    assert_publishable(result)
    with Path(output).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                "cases": len(rows),
                "actual_stage_hits": sum(r["stage_hit_from_published_facts"] for r in rows),
            }
        )
    )


def project_refill(directory):
    """Read an already completed run and its separate visual review; never reapply."""
    directory = Path(directory)
    result = load(directory / "RESULT.json")
    visual = load(directory / "visual/VISUAL-REVIEW.json")
    if result["status"] != "automated_passed_visual_pending" or visual["status"] != "passed":
        raise ValueError("Both automated observations and independent visual verdict are required")
    from .refill_binding import validate

    identity = validate(directory, result)
    if not all(visual.get(key) for key in ("reviewer", "reviewed_at", "observations", "limits")):
        raise ValueError("Visual review must identify its reviewer, time, observations and limits")
    if visual["images"] != result["visual_images"]:
        raise ValueError("Visual review is bound to different rendered artifacts")
    for side in ("front", "back"):
        if not visual["observations"].get(side):
            raise ValueError("Every full-board image requires its own observation")
        if visual["images"][side]["png_sha256"] != digest(directory / "visual" / (side + ".png")):
            raise ValueError("Visual verdict does not bind current image")
    selected = (
        "case",
        "binding",
        "started_at",
        "finished_at",
        "preparation",
        "refill",
        "repeat",
        "baseline",
        "prepared",
        "after",
        "geometry",
        "unchanged_copper_objects",
        "protected_non_targets",
    )
    output = {key: result[key] for key in selected}
    frozen = load(directory / "FROZEN.json")
    output["frozen_original_files"] = frozen["files"]
    output["intent_sha256"] = frozen["intent_sha256"]
    output["preparation_contract"] = frozen["preparation"]
    output["final_files"] = {name: digest(directory / "project" / name) for name in frozen["files"]}
    output["native_fill_before"] = load(directory / "before/fill.json")
    output["native_fill_after"] = load(directory / "after/fill.json")
    output["visual_review"] = visual
    output["final_identity_validation"] = identity
    output["status"] = "passed"
    assert_publishable(output)
    return output


def export_case(directory, output, *, kind):
    value = project_case(directory) if kind == "fault" else project_refill(directory)
    with Path(output).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    return {"case": value["case"], "public_summary_sha256": digest(output)}
