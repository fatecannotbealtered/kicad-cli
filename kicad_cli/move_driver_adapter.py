"""Real saved-board adapter for the incremental driver-runtime move pilot."""

from __future__ import annotations

import hashlib
import math
import os
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import move_preflight, move_readback
from ._driver_sdk.models import DriverError
from ._driver_sdk.verification import PROFILE, build_verification
from .fileformat.board import Board, BoardError
from .fileformat.sexpr import SexprError
from .move_lock import require_unlocked
from .move_transaction import MoveTransaction, digest, regular
from .native import move

ENGINE = "kicad-cli.saved-board"
SCOPE = (
    "Selected footprint identity, pose, and all non-pose content; "
    "bound official candidate DRC is separate from this saved-byte verification."
)
UNCHECKED = [
    {"field_path": path, "required": False, "reason": reason}
    for path, reason in [
        (
            "/post_save_official_DRC",
            "No second official DRC is run after saving; the exact saved bytes "
            "must equal the previously checked candidate.",
        ),
        ("/connectivity", "Move readback does not certify connectivity or routing."),
        ("/manufacturing", "No manufacturing, full artwork, supplier or electrical acceptance."),
    ]
]


def parse_moves(text: str) -> dict[str, tuple[float, float]]:
    moves = {}
    try:
        for item in text.split(";"):
            if not item.strip():
                continue
            reference, coordinates = item.split(":")
            reference = reference.strip()
            x, y = map(float, coordinates.split(","))
            if (
                not reference
                or reference in moves
                or not all(math.isfinite(v) and math.isfinite(v * 1_000_000) for v in (x, y))
            ):
                raise ValueError("empty, duplicate or nonfinite move")
            moves[reference] = (x, y)
        if not moves:
            raise ValueError("no moves")
    except (TypeError, ValueError) as exc:
        raise DriverError(
            "E_VALIDATION",
            "--moves requires unique finite REF:x,y entries.",
            details={
                "problem": "invalid_value",
                "parameter": "--moves",
                "next_action": "Use REF:x,y separated by semicolons, in millimetres.",
            },
            dispatch_state="not_started",
        ) from exc
    return moves


def file_path(target: dict) -> Path:
    parsed = urlsplit(target["locator"])
    if parsed.scheme != "file" or parsed.netloc:
        raise ValueError("not a local file target")
    name = unquote(parsed.path)
    if os.name == "nt":
        name = name.lstrip("/")
    return Path(name).resolve(strict=True)


def target_of(path: Path) -> dict:
    uri = path.as_uri()
    return {
        "mode": "file",
        "engine_id": ENGINE,
        "instance_id": None,
        "object_id": uri,
        "locator": uri,
        "extensions": {
            "identity_kind": "resolved_file_locator",
            "identity_limit": "No required persistent board UUID; "
            "selected footprint IDs and byte revision are bound separately.",
        },
    }


def observation_data(observation) -> dict:
    return {
        "snapshot": observation.snapshot,
        "objects": observation.objects,
        "protected_sha256": observation.protected_sha256,
        "source": observation.source,
    }


def restored_observation(data: dict):
    return move_readback.Observation(
        data["snapshot"], data["objects"], data["protected_sha256"], data["source"]
    )


class MoveAdapter:
    def __init__(self, validator):
        self.validator = validator
        self.scope_provider = None
        self.state_dir = self.environ = None
        self.json_schemas = {}

    def require_current_scope(self, call, plan):
        scope = self.scope_provider() if self.scope_provider else call.extras["scope"]
        scope.require(effects=call.effects, target=plan["target"], outputs=plan["outputs"])
        if scope.record != call.extras["scope"].record:
            raise DriverError(
                "E_CONFLICT",
                "Scope changed before the native file write.",
                details={"conflict": "scope_changed"},
                dispatch_state="not_started",
            )

    def revision(self, target):
        try:
            path = file_path(target)
            regular(path)
            return digest(path)
        except (OSError, ValueError):
            return "target-unavailable"  # A disappearing target is drift, never an unchecked apply.

    def _guard(self, path: Path) -> None:
        regular(path)
        locks = sorted(p.name for p in path.parent.glob("~*.lck"))
        if locks:
            raise DriverError(
                "E_BUSY",
                "The saved project has native KiCad lock files.",
                details={
                    "lock_files": locks,
                    "next_action": "Inspect native lock ownership. Close an owned editor only "
                    "within authorization; keep unexplained lock files.",
                },
                dispatch_state="not_started",
            )

    def plan(self, call):
        from .driver_runtime import _trace

        _trace(call.command)
        try:
            path = Path(call.params["board"]).expanduser().resolve(strict=True)
        except FileNotFoundError as exc:
            raise DriverError(
                "E_NOT_FOUND",
                "The explicit board file does not exist.",
                dispatch_state="not_started",
            ) from exc
        target = target_of(path)
        call.extras["scope"].require(effects=call.effects, target=target)
        self._guard(path)
        requested = parse_moves(call.params["moves"])
        try:
            board = Board.load(path)
            counts = {ref: sum(fp.reference == ref for fp in board.footprints) for ref in requested}
            missing = [ref for ref, count in counts.items() if count == 0]
            duplicate = [ref for ref, count in counts.items() if count > 1]
            if missing:
                raise DriverError(
                    "E_NOT_FOUND",
                    "Requested footprints are absent.",
                    details={"refs": missing},
                    dispatch_state="not_started",
                )
            if duplicate:
                raise DriverError(
                    "E_VALIDATION",
                    "Requested references are ambiguous.",
                    details={"problem": "invalid_value", "parameter": "--moves", "refs": duplicate},
                    dispatch_state="not_started",
                )
            observed = move_readback.observe(path, sorted(requested))
        except (move_readback.ReadbackError, SexprError, BoardError, UnicodeError) as exc:
            raise DriverError(
                "E_VALIDATION",
                "The board or selected objects cannot be observed unambiguously.",
                details={
                    "problem": "invalid_value",
                    "parameter": "--board",
                    "reason": str(exc),
                    "next_action": "Use a valid board with one uniquely identified "
                    "footprint per requested reference.",
                },
                dispatch_state="not_started",
            ) from exc
        if (
            hashlib.sha256(board.document.source.encode()).hexdigest()
            != observed.snapshot["revision"]
        ):
            raise DriverError(
                "E_CONFLICT",
                "The board changed during planning.",
                details={"conflict": "state_drift"},
                dispatch_state="not_started",
            )
        require_unlocked(board, requested, observed)
        preflight = move_preflight.prepare(
            path,
            board,
            observed,
            requested,
            state_dir=self.state_dir,
            operation_id=call.operation_id,
            environ=self.environ,
        )
        self.validator.validate_schema(move_preflight.SCHEMA, preflight, self.json_schemas)
        changes = [
            {
                "action": "translate",
                "resource": "footprint",
                "id": item["id"],
                "summary": "Move footprint " + item["ref"],
                "before": item,
                "after": {"position_mm": list(requested[item["ref"]])},
            }
            for item in observed.objects
        ]
        return {
            "can_apply": preflight["status"] == "passed",
            "target": target,
            "base_snapshot": observed.snapshot,
            "changes": changes,
            "outputs": [{"path": str(path), "effect": "persist", "exists": True}],
            "constraints": [
                "Only selected footprint translation; preserve all other saved bytes.",
                "Requested positions are absolute millimetres; angle, layer "
                "and the saved outline remain unchanged.",
            ],
            "unknowns": [
                *move_preflight.LIMITS,
            ],
            "extensions": {
                "before": observation_data(observed),
                "moves": requested,
                "preflight": preflight,
                "engineering_feedback": {
                    "geometry_check": "official_native_drc_limited",
                    "can_apply": preflight["status"] == "passed",
                    "copper_follows_footprint": False,
                    "official_drc_comparison": preflight["comparison"],
                    "board_contains_copper": bool(board.tracks or board.vias),
                    "requires_visual_review": [
                        "Saved footprint/field positions against pads and the complete board edge"
                    ],
                    "unsupported": [
                        "Rotation, flipping, outline resizing or automatic trace "
                        "repair in this translation command"
                    ],
                },
                "_untrusted": ["before.objects", "moves", "preflight", "engineering_feedback"],
            },
            "_untrusted": [
                "changes",
                "extensions.before.objects",
                "extensions.moves",
                "extensions.preflight",
                "extensions.engineering_feedback",
            ],
        }

    def validate_plan(self, record):
        plan = record["plan"]
        data = plan.get("extensions", {}).get("preflight")
        if data is None:
            raise DriverError(
                "E_CONFLICT",
                "This preview predates the required official move preflight.",
                details={"conflict": "state_drift", "reason": "missing_move_preflight"},
                dispatch_state="not_started",
            )
        self.validator.validate_schema(move_preflight.SCHEMA, data, self.json_schemas)
        move_preflight.validate(plan, state_dir=self.state_dir)
        path = file_path(plan["target"])
        self._guard(path)
        require_unlocked(
            Board.load(path),
            plan["extensions"]["moves"],
            restored_observation(plan["extensions"]["before"]),
        )

    def profile(self, target, before, after, requested):
        requirements, observations = [], []
        expected, failures = move_readback.compare(before, after, requested)

        def add(identifier, field, wanted, actual, unit=None, tolerance=None, independent=False):
            comparison = (
                {"operator": "equals"}
                if tolerance is None
                else {"operator": "within_tolerance", "absolute": tolerance}
            )
            requirements.append(
                {
                    "id": identifier,
                    "kind": "field",
                    "field_path": field,
                    "expected": {"value": wanted, "unit": unit},
                    "comparison": comparison,
                    "allowed_sources": ["file_consumer"],
                    "independence": {
                        "required": independent,
                        "writer_sources": ["native.move", "fileformat.board", "fileformat.sexpr"],
                        "expectation_sources": [
                            "task.move-parameters",
                            "move_driver_adapter.pose_expectation",
                        ]
                        if independent
                        else ["move_readback.lexical_decimal"],
                    },
                }
            )
            source = {
                "kind": "file_consumer",
                "completion": "completed",
                "target": target,
                "snapshot": after.snapshot,
                "method": "Independent saved-byte lexical/Decimal consumer v"
                + after.source["version"],
                "evidence_ref": "#/extensions/readback/observed",
                "defect_sources": ["move_readback.lexical_decimal"],
                "independence_basis": "Consumer imports no writer board/lexer/conversion; "
                "pose expectation is derived separately from the request. "
                "Preservation checks use the same consumer before and after.",
                "coverage": {"checked_paths": [field], "not_checked": []},
            }
            observations.append(
                {
                    "requirement_id": identifier,
                    "observed": {"value": actual, "unit": unit},
                    "source": source,
                }
            )

        actual = {item["ref"]: item for item in after.objects}
        for index, wanted in enumerate(expected):
            item = actual[wanted["ref"]]
            for axis in range(2):
                add(
                    f"pose-{index}-{axis}",
                    f"/footprints/{index}/position_nm/{axis}",
                    float(Decimal(str(requested[wanted["ref"]][axis])) * Decimal(1_000_000)),
                    item["position_nm"][axis],
                    "nm",
                    1,
                    independent=True,
                )
            for key in ("id", "identity_kind", "rotation_deg", "layer"):
                add(
                    f"identity-{index}-{key}",
                    f"/footprints/{index}/{key}",
                    wanted[key],
                    item[key],
                    "deg" if key == "rotation_deg" else None,
                )
        add("non-pose-bytes", "/protected_sha256", before.protected_sha256, after.protected_sha256)
        profile = {
            "profile": PROFILE,
            "target": target,
            "snapshot": after.snapshot,
            "requirements": requirements,
            "observations": observations,
            "not_checked": UNCHECKED,
        }
        return (
            build_verification(profile, scope=SCOPE, validator=self.validator),
            expected,
            failures,
        )

    def apply(self, call, plan):
        path = file_path(plan["target"])
        self.require_current_scope(call, plan)
        self._guard(path)
        self.validate_plan({"plan": plan})
        before = restored_observation(plan["extensions"]["before"])
        requested = {key: tuple(value) for key, value in plan["extensions"]["moves"].items()}
        board = Board.load(path)
        if (
            hashlib.sha256(board.document.source.encode()).hexdigest()
            != before.snapshot["revision"]
        ):
            raise DriverError(
                "E_CONFLICT",
                "The saved target drifted before dispatch reached the writer.",
                details={"conflict": "state_drift"},
                dispatch_state="not_started",
            )
        selected = {fp.reference: fp for fp in board.footprints}
        require_unlocked(board, requested, before)
        for ref, (x, y) in requested.items():
            move.move_footprint(board, selected[ref], move.from_mm(x), move.from_mm(y))
        intended = hashlib.sha256(board.document.dumps().encode()).hexdigest()
        if intended != plan["extensions"]["preflight"]["candidate_sha256"]:
            raise DriverError(
                "E_CONFLICT",
                "The requested writer bytes differ from the checked candidate.",
                details={"conflict": "state_drift", "reason": "candidate_mismatch"},
                dispatch_state="not_started",
            )
        transaction = MoveTransaction(
            path, call.operation_id, before.snapshot["revision"], intended
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
                "The move transaction could not start.",
                details=details,
                dispatch_state="not_started",
            ) from exc
        stage, after, verification, expected, failures = "persist", None, None, [], []
        try:
            self.require_current_scope(call, plan)
            self.validate_plan({"plan": plan})
            if digest(path) != before.snapshot["revision"]:
                raise ValueError("target changed after the move lock was acquired")
            board.document.save(path)
            if digest(path) != intended:
                raise ValueError("Saved bytes differ from the exact native-checked candidate")
            stage = "verify"
            after = move_readback.observe(path, sorted(requested))
            verification, expected, failures = self.profile(
                plan["target"], before, after, requested
            )
            if verification["status"] != "passed":
                raise ValueError("saved independent readback does not match the approved move")
            if digest(path) != after.snapshot["revision"]:
                raise ValueError("target changed after the independent saved readback")
            transaction.cleanup()
        except Exception as exc:  # rollback only recognised writer bytes, otherwise durable unknown
            restoration, restoration_error, restored = None, None, None
            no_change = digest(path) == before.snapshot["revision"]
            try:
                transaction.rollback()
                restored = move_readback.observe(path, sorted(requested))
                restoration = {
                    "matches_baseline": restored.snapshot["revision"]
                    == before.snapshot["revision"],
                    "snapshot": restored.snapshot,
                    "source": restored.source,
                }
                if not restoration["matches_baseline"]:
                    raise OSError("independent restoration readback differs from baseline")
            except Exception as rollback_error:
                restoration_error = str(rollback_error)
            # An exception may have an empty message. Its occurrence, rather
            # than the diagnostic string's truthiness, decides the outcome.
            restoration_failed = restoration_error is not None
            current = restored if not restoration_failed else None
            verify = verification or {
                "status": "incomplete",
                "scope": SCOPE,
                "snapshot_id": None,
                "coverage": "none",
                "checks": [],
                "not_checked": [{"id": "saved-readback", "required": True, "reason": str(exc)}],
                "evidence_refs": [],
                "reason": "The independent consumer did not finish.",
            }
            # A rollback receipt binds verification to the attempted snapshot in raw evidence,
            # not falsely to the restored snapshot. Preserve it outside the guarded outcome.
            attempted = verify
            verify = {
                "status": "incomplete",
                "scope": SCOPE,
                "snapshot_id": current.snapshot["id"] if current else None,
                "coverage": "partial" if current else "none",
                "checks": [
                    {
                        "id": "restored-baseline",
                        "status": "passed",
                        "required": True,
                        "evidence_ref": "#/extensions/restoration",
                    }
                ]
                if current
                else [],
                "not_checked": [
                    {
                        "id": "requested-move",
                        "required": True,
                        "reason": "Requested move was not committed.",
                    }
                ],
                "evidence_refs": ["#/extensions/restoration"] if current else [],
                "reason": "Move failed or its lasting outcome is unknown.",
            }
            receipt = self.receipt(
                call,
                plan,
                current,
                verify,
                expected,
                after,
                failures,
                execution="failed",
                change="unknown"
                if restoration_failed
                else "not_applied"
                if no_change
                else "rolled_back",
                persistence="unknown" if restoration_failed else "unchanged",
                failed_stage=stage,
            )
            receipt["extensions"].update(
                attempted_verification=attempted,
                restoration=restoration,
                restoration_error=restoration_error,
                failure_reason=str(exc),
            )
            # A known failed transaction still needs the original code's
            # semantic details (for example E_CONFLICT.conflict). Discarding
            # them makes a valid failure receipt fail the SDK contract and
            # incorrectly demotes an observed unchanged target to unknown.
            details = (
                dict(exc.details) if isinstance(exc, DriverError) and not restoration_failed else {}
            )
            details["write_state"] = "unknown" if restoration_failed else "rolled_back"
            raise DriverError(
                "E_OUTCOME_UNKNOWN"
                if restoration_failed
                else exc.code
                if isinstance(exc, DriverError)
                else "E_NATIVE_FAILURE",
                "The move did not complete; inspect the real readback and recovery state.",
                details=details,
                receipt=receipt,
                dispatch_state="dispatched",
            ) from exc
        changed = after.snapshot["revision"] != before.snapshot["revision"]
        return self.receipt(
            call,
            plan,
            after,
            verification,
            expected,
            after,
            failures,
            execution="completed",
            change="applied" if changed else "not_applied",
            persistence="saved" if changed else "unchanged",
            failed_stage=None,
        )

    def receipt(self, call, plan, current, verification, expected, attempted, failures, **outcome):
        changed = outcome["change"] == "applied"
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
                    "change": "footprint translated",
                    "evidence_ref": "#/extensions/readback/observed",
                }
                for item in current.objects
            ]
            if changed
            else [],
            "artifacts": [],
            "deliveries": [],
            "recovery": {
                "action": "none" if outcome["execution"] == "completed" else "inspect",
                "reason": "Read operation show before any further write.",
                "supported": True,
            },
            "extensions": {
                "expected": expected,
                "observed": attempted.objects if attempted else [],
                "source_after": attempted.source if attempted else None,
                "failures": failures,
                "readback": observation_data(attempted) if attempted else None,
                "units": {"position": "nm", "position_tolerance_nm": 1},
                "coverage": {
                    "checked": [
                        "selected poses",
                        "non-pose saved bytes",
                        "exact saved bytes equal the bound official native DRC candidate",
                    ],
                    "not_checked": "Post-save official DRC rerun, complete connectivity graph, "
                    "full artwork and manufacturing",
                },
                "preflight": plan["extensions"].get("preflight"),
            },
            "_untrusted": [
                "changed_items",
                "extensions.expected",
                "extensions.observed",
                "extensions.readback",
                "extensions.preflight",
            ],
        }

    def recovery(self, record):
        plan = record["plan"]
        path = file_path(plan["target"])
        regular(path)
        before = restored_observation(plan["extensions"]["before"])
        requested = plan["extensions"]["moves"]
        after = move_readback.observe(path, sorted(requested))
        verification, expected, failures = self.profile(plan["target"], before, after, requested)
        outcome = (
            "not_applied"
            if after.snapshot["revision"] == before.snapshot["revision"]
            else "applied"
            if verification["status"] == "passed"
            else "partial"
        )
        if digest(path) != after.snapshot["revision"]:
            raise OSError("target drifted during recovery observation")
        return {
            "operation_id": record["operation_id"],
            "target": plan["target"],
            "outcome": outcome,
            "snapshot": after.snapshot,
            "readback": observation_data(after),
            "verification": verification,
            "expected": expected,
            "failures": failures,
            "limits": "Current state is observed; intervening external edits have unknown "
            "attribution. No change is replayed or restored.",
        }
