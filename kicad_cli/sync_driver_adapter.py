"""Real scoped project sync dispatch, immutable dependencies and saved readback.

The legacy board.update command is never invoked or converted into a receipt.
This adapter changes one PCB; schematics/libraries/config remain observed inputs.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import document_transaction, sync_candidate, sync_inputs, sync_plan, sync_readback
from ._driver_sdk.models import DriverError
from ._driver_sdk.verification import PROFILE, build_verification
from .fileformat.sexpr import Document
from .move_driver_adapter import MoveAdapter, file_path, observation_data, target_of
from .move_transaction import MoveTransaction, digest

SCOPE = (
    "Captured schematic defaults task, actual saved footprints/pads/nets "
    "and protected board semantics."
)
UNCHECKED = [
    {"field_path": path, "required": False, "reason": reason}
    for path, reason in [
        ("/native_application", "No native Update PCB dialog or GUI write is invoked."),
        ("/DRC", "No runtime native DRC/parity, routing, electrical or manufacturing acceptance."),
        (
            "/replacement_geometry",
            "Library geometry expectation shares prepared serialization/consumer; "
            "native SDK acceptance is separate.",
        ),
        (
            "/options",
            "Defaults only; no bottom replacement, override locks, reference relink "
            "or automatic extra-footprint deletion. Explicit isolated removal is separate.",
        ),
        (
            "/bytes",
            "Preservation covers protected parsed semantics; serialization can change whitespace.",
        ),
    ]
]


def _dependencies(observation, manifest):
    observation.snapshot["dependencies"] = [
        {"identity": Path(row["path"]).as_uri(), "revision": row["sha256"] or "absent"}
        for row in manifest["files"]
        if row["path"] != manifest["board"]
    ]
    return observation


def _incomplete(reason, snapshot=None):
    return {
        "status": "incomplete",
        "scope": SCOPE,
        "snapshot_id": snapshot,
        "coverage": "none",
        "checks": [],
        "not_checked": [{"id": "sync-readback", "required": True, "reason": reason}],
        "evidence_refs": [],
        "reason": reason,
    }


class SyncAdapter(MoveAdapter):
    state_dir = secret_provider = guard_documents = None

    def validate_plan(self, record, *, ignore_board=False):
        try:
            plan = record["plan"]
            ext = plan["extensions"]
            sync_inputs.validate(ext["inputs"], ignore_board=ignore_board)
            if ext.get("candidate_review"):
                review = ext["candidate_review"]
                if (
                    review["operation_id"] != plan["operation_id"]
                    or review["plan_id"] != plan["plan_id"]
                    or review["candidate"]["sha256"] != ext["intended_sha256"]
                ):
                    raise sync_candidate.CandidateError(
                        "Candidate review differs from its enclosing plan.", "plan_mismatch"
                    )
                sync_candidate.validate(
                    review,
                    ext["inputs"],
                    plan["changes"],
                    state_dir=self.state_dir,
                    key=self.secret_provider(),
                )
        except (OSError, ValueError, KeyError) as exc:
            raise DriverError(
                "E_CONFLICT",
                "Approved sync inputs changed or became unavailable.",
                details={
                    "conflict": "state_drift",
                    "reason": str(exc),
                    **(
                        exc.details
                        if isinstance(exc, (sync_inputs.InputError, sync_candidate.CandidateError))
                        else {}
                    ),
                },
                dispatch_state="not_started",
            ) from exc

    def plan(self, call):
        from .driver_runtime import _trace  # noqa: PLC0415

        _trace(call.command)
        try:
            path = Path(call.params["board"]).expanduser().resolve(strict=True)
        except FileNotFoundError as exc:
            raise DriverError(
                "E_NOT_FOUND", "The explicit PCB does not exist.", dispatch_state="not_started"
            ) from exc
        target = target_of(path)
        call.extras["scope"].require(effects=call.effects, target=target)
        self._guard(path)
        self.guard_documents([path])
        try:
            removal_path = call.params.get("remove-footprint")
            inputs = sync_inputs.capture(path, call.params["schematic"], removal_path)
            prepared = sync_plan.prepare(inputs, removal_path)
            before = _dependencies(sync_readback.observe(path), inputs.manifest)
            if before.snapshot["revision"] != prepared["baseline"]:
                raise sync_inputs.Drift("The PCB changed during sync planning.")
            sync_inputs.validate(inputs.manifest)
        except sync_inputs.Drift as exc:
            raise DriverError(
                "E_CONFLICT",
                str(exc),
                details={"conflict": "state_drift", **exc.details},
                dispatch_state="not_started",
            ) from exc
        except FileNotFoundError as exc:
            raise DriverError(
                "E_NOT_FOUND",
                "A required sync input does not exist.",
                details={"reason": str(exc)},
                dispatch_state="not_started",
            ) from exc
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise DriverError(
                "E_VALIDATION",
                "The complete sync task cannot be prepared safely.",
                details={
                    "problem": "invalid_value",
                    "parameter": "--schematic",
                    "reason": str(exc),
                    "next_action": "Correct all listed source/library/identity conditions, "
                    "then dry-run again.",
                    **(
                        exc.details
                        if isinstance(exc, (sync_plan.PlanError, sync_inputs.InputError))
                        else {}
                    ),
                },
                dispatch_state="not_started",
            ) from exc
        old = {item["id"]: item for item in before.objects}
        changes = [
            {
                "action": "synchronize" if item["id"] in old else "create",
                "resource": "footprint",
                "id": item["id"],
                "summary": "Sync footprint " + item["ref"],
                "before": old.get(item["id"]),
                "after": item,
            }
            for item in prepared["expected"]
            if item != old.get(item["id"])
        ]
        if prepared["removal"]:
            removed = prepared["removal"]["before"]
            changes.append(
                {
                    "action": "delete",
                    "resource": "footprint",
                    "id": removed["id"],
                    "summary": "Remove explicitly selected isolated footprint " + removed["ref"],
                    "before": removed,
                    "after": None,
                }
            )
        intended = hashlib.sha256(prepared["intended"]).hexdigest()
        plan = {
            "target": target,
            "base_snapshot": before.snapshot,
            "changes": changes,
            "outputs": [{"path": str(path), "effect": "persist", "exists": True}],
            "constraints": [
                "One PCB write; schematic hierarchy/libraries/config are "
                "immutable observed dependencies.",
                "Reject byte, existence, resolved-identity or path-variable drift "
                "before dispatch and save.",
                "Require every source part/pin; preserve matched UUID paths/poses "
                "and unrelated copper semantics.",
            ],
            "unknowns": [item["reason"] for item in UNCHECKED]
            + [
                "Unselected unmatched PCB footprints are retained. Only an exact "
                "--remove-footprint selection can delete one isolated footprint.",
                "Project netclass assignments are not remapped here. After net renaming, "
                "inspect effective rules and use project netclass set if needed.",
                "Pin-net changes do not repair or remove existing copper. Check actual "
                "connectivity/native DRC and explicitly select affected nets for routing.",
            ],
            "extensions": {
                "params": call.params,
                "inputs": inputs.manifest,
                "before": observation_data(before),
                "expected": prepared["expected"],
                "tasks": prepared["tasks"],
                "protected_sha256": prepared["protected_sha256"],
                "rows": prepared["rows"],
                "renamed_nets": prepared["renamed_nets"],
                "added": prepared["added"],
                "replaced": prepared["replaced"],
                "removal": prepared["removal"],
                "schematic_identity": prepared["schematic_identity"],
                "cleanup_protocol": "document-set.v2",
                "files": [
                    {"path": str(path), "baseline": prepared["baseline"], "intended": intended}
                ],
                "support_boundary": {
                    "retained_unmatched_references": sorted(
                        item["ref"]
                        for item in prepared["expected"]
                        if item["ref"] not in {task["ref"] for task in prepared["tasks"]}
                    ),
                    "pcb_footprint_deletion": "explicit_single_isolated_identity_only",
                    "netclass_assignment_remap": "not_performed",
                    "existing_copper": "preserved_except_uniform_net_renames",
                    "new_footprints": "require_placement_and_connectivity_review",
                },
                "intended_sha256": intended,
            },
            "_untrusted": [
                "target.locator",
                "target.object_id",
                "outputs",
                "changes",
                "extensions",
            ],
        }
        if call.params.get("candidate-out"):
            try:

                def recheck(outputs):
                    self.require_current_scope(call, {"target": target, "outputs": outputs})
                    sync_inputs.validate(inputs.manifest)

                review, outputs = sync_candidate.prepare(
                    call,
                    prepared,
                    inputs.manifest,
                    changes,
                    state_dir=self.state_dir,
                    key=self.secret_provider(),
                    recheck=recheck,
                )
                self.validator.validate_schema(sync_candidate.SCHEMA, review, self.json_schemas)
                plan["extensions"]["candidate_review"] = review
                plan["outputs"].extend(outputs)
            except sync_candidate.CandidateError as exc:
                raise DriverError(
                    "E_CONFLICT",
                    str(exc),
                    details={"conflict": "state_drift", **exc.details},
                    dispatch_state="not_started",
                ) from exc
            except FileExistsError as exc:
                raise DriverError(
                    "E_CONFLICT",
                    "Candidate output or preparation workspace already exists.",
                    details={"conflict": "state_drift", "candidate_conflict": "already_exists"},
                    dispatch_state="not_started",
                ) from exc
            except sync_inputs.Drift as exc:
                raise DriverError(
                    "E_CONFLICT",
                    str(exc),
                    details={"conflict": "state_drift", **exc.details},
                    dispatch_state="not_started",
                ) from exc
            except OSError as exc:
                raise DriverError(
                    "E_IO",
                    "Candidate materialization failed before any project write.",
                    details={"reason": str(exc)},
                    dispatch_state="not_started",
                ) from exc
        return plan

    def profile(self, plan, after, dependency_status):
        task_check = sync_readback.fulfillment(after.objects, plan["extensions"]["tasks"])
        wanted = [
            (
                "prepared-byte-revision",
                "/snapshot/revision",
                plan["extensions"]["intended_sha256"],
                after.snapshot["revision"],
            ),
            ("saved-objects", "/footprints", plan["extensions"]["expected"], after.objects),
            (
                "protected-semantics",
                "/protected_sha256",
                plan["extensions"]["protected_sha256"],
                after.protected_sha256,
            ),
            ("source-task-fulfilled", "/task/status", "passed", task_check["status"]),
            ("inputs-stable", "/dependencies/status", "passed", dependency_status),
        ]
        requirements, observations = [], []
        for name, field, expected, actual in wanted:
            requirements.append(
                {
                    "id": name,
                    "kind": "field",
                    "field_path": field,
                    "expected": {"value": expected, "unit": None},
                    "comparison": {"operator": "equals"},
                    "allowed_sources": ["file_consumer"],
                    "independence": {
                        "required": False,
                        "writer_sources": [
                            "fileformat.board",
                            "fileformat.sexpr",
                            "native.board_update",
                        ],
                        "expectation_sources": [
                            "fileformat.circuit",
                            "fileformat.netlist",
                            "sync_readback.lexical_decimal",
                            "sync_plan.prepared_serialization",
                        ],
                    },
                }
            )
            observations.append(
                {
                    "requirement_id": name,
                    "observed": {"value": actual, "unit": None},
                    "source": {
                        "kind": "file_consumer",
                        "completion": "completed",
                        "target": plan["target"],
                        "snapshot": after.snapshot,
                        "method": "Fresh saved sync consumer v" + after.source["version"],
                        "evidence_ref": "#/extensions/readback",
                        "defect_sources": [
                            "sync_readback.lexical_decimal",
                            "move_readback.lexer",
                            "sync_inputs.dependency_observer",
                        ],
                        "independence_basis": "Actual saved consumer imports no writer model. "
                        "Source expectations share custom schematic/netlist model; "
                        "geometry and preservation expectations share "
                        "this consumer/prepared serialization. No native application acceptance.",
                        "coverage": {"checked_paths": [field], "not_checked": []},
                    },
                }
            )
        return build_verification(
            {
                "profile": PROFILE,
                "target": plan["target"],
                "snapshot": after.snapshot,
                "requirements": requirements,
                "observations": observations,
                "not_checked": UNCHECKED,
            },
            scope=SCOPE,
            validator=self.validator,
        ), task_check

    def apply(self, call, plan):
        path, ext = file_path(plan["target"]), plan["extensions"]
        self.require_current_scope(call, plan)
        self._guard(path)
        self.guard_documents([path])
        self.validate_plan({"plan": plan})
        removal_path = ext["params"].get("remove-footprint")
        inputs = sync_inputs.capture(path, ext["params"]["schematic"], removal_path)
        if inputs.manifest != ext["inputs"]:
            raise DriverError(
                "E_CONFLICT",
                "The captured sync dependency set differs from its plan.",
                details={"conflict": "state_drift"},
                dispatch_state="not_started",
            )
        prepared = sync_plan.prepare(inputs, removal_path)
        intended = hashlib.sha256(prepared["intended"]).hexdigest()
        if (
            intended != ext["intended_sha256"]
            or prepared["expected"] != ext["expected"]
            or prepared["tasks"] != ext["tasks"]
        ):
            raise DriverError(
                "E_CONFLICT",
                "Prepared sync result differs from its approved plan.",
                details={"conflict": "state_drift"},
                dispatch_state="not_started",
            )
        candidate_bytes = prepared["intended"]
        if ext.get("candidate_review"):
            candidate_bytes = sync_candidate.validate(
                ext["candidate_review"],
                ext["inputs"],
                plan["changes"],
                state_dir=self.state_dir,
                key=self.secret_provider(),
            )
            if candidate_bytes != prepared["intended"]:
                raise DriverError(
                    "E_CONFLICT",
                    "Reviewed candidate differs from the recomputed sync bytes.",
                    details={"conflict": "state_drift", "candidate_conflict": "planner_mismatch"},
                    dispatch_state="not_started",
                )
        baseline = plan["base_snapshot"]["revision"]
        document_cleanup = ext.get("cleanup_protocol") == "document-set.v2"
        transaction = (
            document_transaction.DocumentTransaction(
                path,
                call.operation_id,
                ext["files"],
                state_dir=self.state_dir,
                key=self.secret_provider(),
            )
            if document_cleanup
            else MoveTransaction(path, call.operation_id, baseline, intended)
        )
        try:
            transaction.arm()
        except (OSError, ValueError) as exc:
            code = "E_CONFLICT" if isinstance(exc, (FileExistsError, ValueError)) else "E_IO"
            details = {"reason": str(exc)}
            if code == "E_CONFLICT":
                details["conflict"] = "state_drift" if isinstance(exc, ValueError) else "lock_held"
            raise DriverError(
                code,
                "The sync transaction could not start.",
                details=details,
                dispatch_state="not_started",
            ) from exc
        stage, after, verification, task_check = "persist", None, None, None
        cleanup_started = False
        try:
            self.require_current_scope(call, plan)
            self.validate_plan({"plan": plan})
            if digest(path) != baseline:
                raise sync_inputs.Drift("PCB drifted after acquiring the sync lock.")
            Document.parse(candidate_bytes.decode("utf-8")).save(path)
            stage = "verify"
            after = sync_readback.observe(path)
            self.validate_plan({"plan": plan}, ignore_board=True)
            _dependencies(after, ext["inputs"])
            verification, task_check = self.profile(plan, after, "passed")
            if verification["status"] != "passed":
                raise DriverError(
                    "E_VERIFY_FAILED", "The actual saved PCB did not fulfill its sync task."
                )
            self.validate_plan({"plan": plan}, ignore_board=True)
            if digest(path) != after.snapshot["revision"]:
                raise sync_inputs.Drift("PCB changed after the independent sync readback.")
            cleanup_started = True
            transaction.cleanup()
        except Exception as exc:
            attempted = verification or _incomplete(str(exc))
            restoration, restoration_error, current, no_change = None, None, None, False
            try:
                if document_cleanup and cleanup_started:
                    raise OSError(
                        "Verified saved PCB preserved while attributable cleanup is pending"
                    )
                no_change = digest(path) == baseline
                self.require_current_scope(call, plan)
                transaction.rollback()
                current = sync_readback.observe(path)
                restoration = {
                    "matches_baseline": current.snapshot["revision"] == baseline,
                    "snapshot": current.snapshot,
                    "source": current.source,
                }
                if not restoration["matches_baseline"]:
                    raise OSError("Independent restoration differs from the PCB baseline.")
            except Exception as failure:
                restoration_error, current = str(failure), None
                if document_cleanup and cleanup_started:
                    try:
                        current = sync_readback.observe(path)
                    except (OSError, ValueError):
                        current = None
            saved_change = (
                (
                    "not_applied"
                    if current.snapshot["revision"] == baseline
                    else "applied"
                    if current.snapshot["revision"] == intended
                    else None
                )
                if current and cleanup_started
                else None
            )
            verified = _incomplete(
                "Verified save retained; cleanup remains incomplete."
                if saved_change is not None
                else "Requested sync was not established after failure.",
                current.snapshot["id"] if current else None,
            )
            if current and restoration:
                verified.update(
                    coverage="partial",
                    checks=[
                        {
                            "id": "restored-baseline",
                            "status": "passed",
                            "required": True,
                            "evidence_ref": "#/extensions/restoration",
                        }
                    ],
                    evidence_refs=["#/extensions/restoration"],
                )
            receipt = self.receipt(
                call,
                plan,
                current,
                verified,
                after,
                task_check,
                execution="failed",
                change=saved_change
                if saved_change is not None
                else "unknown"
                if restoration_error
                else "not_applied"
                if no_change
                else "rolled_back",
                persistence="saved"
                if current and cleanup_started and current.snapshot["revision"] == intended
                else "unknown"
                if restoration_error
                else "unchanged",
                failed_stage=stage,
            )
            receipt["extensions"].update(
                attempted_verification=attempted,
                restoration=restoration,
                restoration_error=restoration_error,
                failure_reason=str(exc),
                failure_code=exc.code if isinstance(exc, DriverError) else None,
                failure_details=exc.details if isinstance(exc, DriverError) else None,
            )
            failure_details = {"write_state": receipt["outcome"]["change"]}
            if isinstance(exc, DriverError) and exc.code in {"E_CONFLICT", "E_FORBIDDEN"}:
                # These codes retain their canonical structured details even
                # when a post-save failure has an independently observed restore.
                failure_details.update(exc.details)
            # The inner input check also serves pre-dispatch callers. This
            # enclosing transaction has dispatched, regardless of its cause.
            failure_details["dispatch_state"] = "dispatched"
            raise DriverError(
                "E_PARTIAL"
                if saved_change == "applied"
                else "E_OUTCOME_UNKNOWN"
                if restoration_error
                else exc.code
                if isinstance(exc, DriverError)
                and exc.code
                in {"E_VERIFY_FAILED", "E_VERIFY_INCOMPLETE", "E_CONFLICT", "E_FORBIDDEN"}
                else "E_NATIVE_FAILURE",
                "Sync did not complete; inspect its durable record and actual readback.",
                details=failure_details,
                receipt=receipt,
                dispatch_state="dispatched",
            ) from exc
        changed = after.snapshot["revision"] != baseline
        return self.receipt(
            call,
            plan,
            after,
            verification,
            after,
            task_check,
            execution="completed",
            change="applied" if changed else "not_applied",
            persistence="saved" if changed else "unchanged",
            failed_stage=None,
        )

    def receipt(self, call, plan, current, verification, attempted, task_check, **outcome):
        return {
            "operation_id": call.operation_id,
            "capability_id": call.capability_id,
            "plan_id": call.plan_id,
            "target": plan["target"],
            "authorization_scope_id": plan["authorization_scope_id"],
            "effects": plan["effects"],
            "snapshot_before": plan["base_snapshot"],
            "snapshot_after": current.snapshot if current else None,
            "failed_stage": outcome.pop("failed_stage"),
            "outcome": dict(outcome, delivery="not_applicable", verification=verification),
            "changed_items": [
                {
                    "id": item["id"],
                    "change": item["summary"],
                    "evidence_ref": "#/extensions/readback",
                }
                for item in plan["changes"]
            ]
            if outcome["change"] == "applied"
            else [],
            "artifacts": [],
            "deliveries": [],
            "recovery": {
                "action": "none" if outcome["execution"] == "completed" else "inspect",
                "reason": "Read operation show before another write.",
                "supported": True,
            },
            "extensions": {
                **(
                    {"candidate_review": plan["extensions"]["candidate_review"]}
                    if plan["extensions"].get("candidate_review")
                    else {}
                ),
                "expected": plan["extensions"]["expected"],
                "tasks": plan["extensions"]["tasks"],
                "rows": plan["extensions"]["rows"],
                "inputs": plan["extensions"]["inputs"],
                "observed": attempted.objects if attempted else [],
                "readback": observation_data(attempted) if attempted else None,
                "task_check": task_check,
                "coverage": {
                    "checked": [
                        "complete saved objects/pads/nets",
                        "captured source requirements",
                        "protected board/copper semantics",
                        "dependency stability",
                    ],
                    "not_checked": [item["reason"] for item in UNCHECKED],
                },
            },
            "_untrusted": ["changed_items", "extensions"],
        }

    def recovery(self, record):
        plan, ext = record["plan"], record["plan"]["extensions"]
        path = file_path(plan["target"])
        after = sync_readback.observe(path)
        dependency_status, dependency_reason = "passed", None
        try:
            sync_inputs.validate(ext["inputs"], ignore_board=True)
            _dependencies(after, ext["inputs"])
        except (OSError, ValueError, KeyError) as exc:
            dependency_status, dependency_reason = "failed", str(exc)
        verification, task_check = self.profile(plan, after, dependency_status)
        if digest(path) != after.snapshot["revision"]:
            raise OSError("Saved PCB changed during recovery observation.")
        cleanup = None
        if ext.get("cleanup_protocol") == "document-set.v2":
            try:
                document_transaction.owned(
                    path,
                    record["operation_id"],
                    ext["files"],
                    state_dir=self.state_dir,
                    key=self.secret_provider(),
                    required=True,
                )
                cleanup = {"status": "attributable"}
            except (OSError, ValueError, KeyError) as exc:
                cleanup = {"status": "refused", "reason": str(exc)}
        return {
            "operation_id": record["operation_id"],
            "target": plan["target"],
            "outcome": "not_applied"
            if after.snapshot["revision"] == plan["base_snapshot"]["revision"]
            else "applied"
            if verification["status"] == "passed"
            else "partial",
            "snapshot": after.snapshot,
            "readback": observation_data(after),
            "verification": verification,
            "expected": ext["expected"],
            "task_check": task_check,
            "dependency_status": dependency_status,
            "dependency_reason": dependency_reason,
            **({"cleanup": cleanup} if cleanup is not None else {}),
            "limits": "Observe actual current saved state; attribution to a lost write is unknown. "
            "Never repeat a write or overwrite external board/source/library changes.",
        }

    def resolve_recovery(self, record, current, state_dir):
        path = file_path(record["plan"]["target"])
        document_transaction.resolve(
            path,
            record["operation_id"],
            state_dir / "recovery" / record["operation_id"],
            {str(path): current["snapshot"]["revision"]},
            record["plan"]["extensions"]["files"],
            state_dir=state_dir,
            key=self.secret_provider(),
            required=True,
        )
