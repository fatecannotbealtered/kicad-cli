"""One portable entry for the packaged, finite Windows C10 trial.

Use the selected Host Python with -B -S -X pycache_prefix=<verified-empty-prefix>.
All process launchers, consumers and renderers come from this package; external
KiCad, driver SDK/spec and bounded Host/Node providers remain explicit inputs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from pathlib import Path

if __package__ in (None, ""):
    # Direct absolute-file invocation is the portable entry. Only this shipped
    # Source directory is added; no developer checkout or product module loads.
    sys.path.insert(0, str(Path(__file__).absolute().parent.parent))
    from trial_tools import preparation as core
    from trial_tools.eco import SCHEMA, Trial, complete_request
    from trial_tools.reading import ident, load, write_new
else:
    from . import preparation as core
    from .eco import SCHEMA, Trial, complete_request
    from .reading import ident, load, write_new

PACKAGE = Path(__file__).absolute().parent.parent


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    sub = value.add_subparsers(dest="phase", required=True)
    sub.add_parser("reference", help="Read the packaged phase and evidence contract")
    setup = sub.add_parser(
        "prepare",
        help="Initialize both sacrificial consumer profiles, then freeze complete configuration",
    )
    setup.add_argument("--work", required=True)
    setup.add_argument("--dependencies", required=True, help="Explicit external dependency JSON")
    setup.add_argument(
        "--configuration", required=True, help="Prospective configuration/metadata plan JSON"
    )
    for name in (
        "start",
        "edit-preview",
        "edit-confirm",
        "preview",
        "review-record",
        "confirm",
        "saved-check",
        "final-review-record",
        "repeat-confirm-check",
        "query",
        "review-template",
    ):
        phase = sub.add_parser(name)
        phase.add_argument("--work", required=True)
        if name == "edit-confirm":
            phase.add_argument("--ack-preview-sha256", required=True)
        if name in ("review-record", "final-review-record"):
            phase.add_argument(
                "--ack",
                required=True,
                help="Owned acknowledgment authored after actual full-image viewing",
            )
        if name == "query":
            phase.add_argument("--operation", choices=("edit", "sync"), default="sync")
        if name == "review-template":
            phase.add_argument("--stage", choices=("candidate", "final"), required=True)
            phase.add_argument(
                "--output",
                required=True,
                help="New acknowledgment draft inside owned work; starts pending/unviewed",
            )
    return value


def reference() -> dict:
    return {
        "schema": SCHEMA,
        "status": "private_unpublishable_finite_trial_methods",
        "entry": (
            "python -B -S -X pycache_prefix=<verified-empty-caller-prefix> "
            "<Source>/trial_tools/trial.py PHASE --work ABSOLUTE_OWNED_WORK"
        ),
        "child_python_policy": (
            "Native and Host consumers use ReadyContext.python_flags(profile): "
            "-B -S -X pycache_prefix=<owned-profile/cache/bytecode-empty>. "
            "Prefix identity and complete empty membership are checked before and after every run."
        ),
        "preparation_policy": (
            "READY requires Native12 + Host4 + Node4 = 20 top-level consumer calls. "
            "Whole-tree process instance counts are separate observations, not 20 by definition. "
            "Renderer warmup artifacts are preparation only, never engineering or image review."
        ),
        "preparation_stability": (
            "Freeze complete profile configuration/HOME/cache after both initial Node renders. "
            "The stability renders must preserve that original inventory without baseline refresh. "
            "A matching repeated prepare adds zero Native, Host and Node consumers; "
            "Older READY records without this qualification require a new package/root/plan."
        ),
        "preparation_addendum": "windows-internal-trial/RENDERER_PREPARATION_ADDENDUM.md",
        "phases": [
            "prepare",
            "start",
            "edit-preview",
            "edit-confirm",
            "preview",
            "review-template",
            "review-record",
            "confirm",
            "saved-check",
            "final-review-record",
            "repeat-confirm-check",
            "query",
        ],
        "stops": [
            "edit-confirm requires an explicit exact-preview digest acknowledgment",
            "preview creates the public candidate, independent checks and seven full PCB images",
            "confirm requires actual candidate viewing and a separate acknowledgment",
            "saved-check uses new processes and fourteen fresh full images",
        ],
        "dependencies": {
            "required": ["product_exe", "official_cli", "native_python", "sdk_root", "spec_root"],
            "optional": ["host_python", "kicad_root", "dependency_files", "aliases", "path_map"],
            "path_map": "Exact manifest path to current external file; bytes must match",
            "automatic_prospective_binding": (
                "Packaged 341 providers, reading runtime, fresh aliases, Host and packaged methods"
            ),
        },
        "configuration": {
            "environment": "Only explicit additional variables; owned paths cannot be overridden",
            "seed_files": "Per-profile relative config path to existing seed file",
            "metadata_allowances": (
                "Only predeclared kicad_common.json /system/working_dir owned-path migration; "
                "otherwise complete file set, identity and bytes are strict"
            ),
        },
        "replay": (
            "Completed phases return verified checkpoints with zero new processes. "
            "Incomplete process phases refuse replay. Query uses the original ID and state."
        ),
        "approval": (
            "Review templates start pending. Author reviewer, image viewed flags and verdict "
            "only after actual viewing."
        ),
        "private_evidence": (
            "Scope, tokens, raw replies and traces stay in owned work. Redact before sharing."
        ),
        "optional_regression": (
            "repeat-confirm-check requires known success and valid original TTL; one unkeyed "
            "spent-token refusal, exact exit6/E_CONFLICT/token_used and unchanged logical state."
        ),
        "limits": [
            "No product internals imported, no private SCH/PCB edits, no rerouting",
            "Disk identity/hash and process/module observations do not authenticate per-load bytes",
            "Historical Host qualification requires exact current method/Node/provider identity",
            "Synthetic offline tests do not establish Native execution or engineering acceptance",
        ],
    }


def dispatch(args: argparse.Namespace):
    if args.phase == "reference":
        return reference()
    if args.phase == "prepare":
        dependencies, config = complete_request(
            PACKAGE, load(Path(args.dependencies)), load(Path(args.configuration))
        )
        reused_ready = (Path(args.work) / "READY.json").is_file()
        ready = core.prepare(PACKAGE, args.work, dependencies, config)
        classified = ready.record["preparation_call_counts"]
        cumulative = {role: classified[role]["completed"] for role in ("native", "host", "node")}
        cumulative["total"] = sum(cumulative.values())
        added = {role: 0 if reused_ready else count for role, count in cumulative.items()}
        return {
            "status": "READY",
            "work_root": str(ready.work_root),
            "preparation_calls": len(ready.record["preparation_calls"]),
            "completed_consumer_calls": cumulative,
            "new_consumer_calls": added,
            "reused_ready": reused_ready,
            "preparation_only": True,
            "synthetic": ready.record["synthetic"],
            "formal_cad_writes": 0,
            "next": "start",
        }
    trial = Trial(args.work)
    if args.phase == "edit-confirm":
        return trial.edit_confirm(args.ack_preview_sha256)
    if args.phase in ("review-record", "final-review-record"):
        return trial.review_record(args.ack, final=args.phase == "final-review-record")
    if args.phase == "query":
        result = trial.query(args.operation)
        # Raw original-operation output stays in its owned observer log. The
        # public method summary never republishes its authority or raw trace.
        result.pop("reply", None)
        return result
    if args.phase == "review-template":
        trial._load_context()
        request_path = trial.work / "output" / args.stage / "REVIEW-REQUEST.json"
        request = load(request_path)
        destination = Path(args.output).absolute()
        if not destination.is_relative_to(trial.work):
            raise core.TrialError(
                "E_TRIAL_PATH", "Acknowledgment draft must stay inside owned work"
            )
        write_new(
            destination,
            {
                "schema": "trial-actual-view-ack/v1",
                "request_sha256": ident(request_path)["sha256"],
                "operation_id": request["operation_id"],
                "reviewer": "",
                "statement": request["statement_required"],
                "images": [
                    {"path": row["path"], "sha256": row["sha256"], "actual_viewed": False}
                    for row in request["images"]
                ],
                "verdict": "pending",
            },
        )
        return {
            "status": "pending_unviewed_draft",
            "draft": ident(destination),
            "action": "View every full image, then author the acknowledgment before review-record",
        }
    return getattr(trial, args.phase.replace("-", "_"))()


def preparation_dependency_diagnostics(value) -> list[dict]:
    """Expose only finite dependency locators/repairs, never arbitrary Core data."""
    rows = value if isinstance(value, list) else [value]
    safe = []
    for row in rows[:32]:
        if isinstance(row, str):
            row = {"path": row, "repair": "Restore the explicitly declared dependency file."}
        if not isinstance(row, dict):
            continue
        item = {
            key: row[key]
            for key in ("name", "path", "repair", "reason")
            if isinstance(row.get(key), str)
            and len(row[key]) <= 2048
            and not any(char in row[key] for char in ("\n", "\r", "\0"))
        }
        for key in ("expected", "actual"):
            identity = row.get(key)
            if not isinstance(identity, dict):
                continue
            summary = {}
            if isinstance(identity.get("sha256"), str) and re.fullmatch(
                r"[0-9a-fA-F]{64}", identity["sha256"]
            ):
                summary["sha256"] = identity["sha256"]
            if type(identity.get("size")) is int and identity["size"] >= 0:
                summary["size"] = identity["size"]
            if summary:
                item[key] = summary
        if item:
            safe.append(item)
    return safe


def main(argv=None) -> int:
    started = time.monotonic()
    args = parser().parse_args(argv)
    try:
        result = dispatch(args)
        envelope, exit_code = {"ok": True, "schema_version": "1.0", "data": result}, 0
    except Exception as error:
        # Detailed core failures can carry raw process command lines. Preserve
        # them privately; emit only the finite method refusal and evidence path.
        evidence = None
        work = Path(getattr(args, "work", ""))
        if work.is_absolute() and (work / "OWNERSHIP.json").is_file():
            evidence = work / "state" / ("method-refusal-" + uuid.uuid4().hex + ".json")
            write_new(
                evidence,
                {
                    "type": type(error).__name__,
                    "reason": str(error),
                    "code": getattr(error, "code", "E_TRIAL_REJECTED"),
                    "details": getattr(error, "details", None),
                },
            )
        code = getattr(error, "code", "E_TRIAL_REJECTED")
        message = str(error)
        public_details = {
            "private_evidence": str(evidence) if evidence else None,
            "action": "Preserve artifacts. Query unknown writes; do not reconfirm.",
        }
        if args.phase == "prepare":
            # Preparation has no formal operation ID. Neither a missing provider
            # nor a stopped sacrificial consumer authorizes a confirmation/query.
            public_details.update(
                phase="prepare",
                formal_cad_writes=0,
                formal_operation_id=None,
                action=(
                    "Preserve any INCOMPLETE directory and diagnostics. Repair the declaration, "
                    "then use a new owned root and new plan. Do not automatically retry a started "
                    "consumer; preparation has no formal operation ID to query."
                ),
            )
            if code == "E_TRIAL_DEPENDENCY":
                message = "Preparation dependency is missing, unsupported or differs from its pin."
                public_details["dependencies"] = preparation_dependency_diagnostics(
                    getattr(error, "details", None)
                )
            elif code == "E_TRIAL_NATIVE_STOP":
                message = "A preparation consumer stopped; retain its private diagnostics."
            else:
                message = "Preparation failed; retain diagnostics and use a new root and plan."
        envelope, exit_code = (
            {
                "ok": False,
                "schema_version": "1.0",
                "error": {
                    "code": code,
                    "message": message,
                    "retryable": False,
                    "details": public_details,
                },
            },
            1,
        )
    envelope["meta"] = {"duration_ms": round((time.monotonic() - started) * 1000)}
    print(json.dumps(envelope, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
