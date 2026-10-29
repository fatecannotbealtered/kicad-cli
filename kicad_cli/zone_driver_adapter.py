"""Scoped saved-file zone tasks with durable writes and fresh observations.

No legacy zone command or envelope is called. The filler and bounded custom
DRC share writer defect sources; native SDK acceptance remains a separate gate.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import drc_baseline, kicad_env, zone_plan, zone_readback
from ._driver_sdk.models import DriverError
from ._driver_sdk.verification import PROFILE, build_verification
from .fileformat.board import Board
from .fileformat.drc.settings import Settings
from .move_driver_adapter import MoveAdapter, file_path, observation_data, target_of
from .move_transaction import MoveTransaction, digest

SCOPE = (
    "Selected zone saved definition/fill representation, non-target semantics, "
    "and bounded custom findings."
)
UNCHECKED = [
    {"field_path": path, "required": False, "reason": reason}
    for path, reason in [
        (
            "/native_DRC",
            "Custom checker shares writer geometry/parser; no official native DRC here.",
        ),
        (
            "/fill_geometry",
            "Serialized fill equality does not certify copper geometry, connectivity or thermals.",
        ),
        (
            "/non_target_bytes",
            "Zone insertion changes whitespace; preservation covers parsed semantics.",
        ),
        ("/manufacturing", "No full artwork, supplier, electrical or fabrication acceptance."),
        (
            "/libraries",
            "Installed library contents are not fingerprinted by the custom finding gate.",
        ),
    ]
]


def _dependencies(path):
    result = []
    for extension in (".kicad_pro", ".kicad_dru"):
        name = path.with_suffix(extension)
        try:
            sha = digest(name)
        except FileNotFoundError:
            sha = None
        result.append({"path": str(name), "present": sha is not None, "sha256": sha})
    return result


def _snapshot_dependencies(observed, dependencies):
    observed.snapshot["dependencies"] = [
        {"identity": Path(item["path"]).as_uri(), "revision": item["sha256"] or "absent"}
        for item in dependencies
    ]
    return observed


def _incomplete(reason, snapshot=None):
    return {
        "status": "incomplete",
        "scope": SCOPE,
        "snapshot_id": snapshot,
        "coverage": "none",
        "checks": [],
        "not_checked": [{"id": "zone-readback", "required": True, "reason": reason}],
        "evidence_refs": [],
        "reason": reason,
    }


class ZoneAdapter(MoveAdapter):
    def _capture(self, path):
        report = drc_baseline.capture(path, kicad_root=kicad_env.find_kicad_root())[1]
        drc_baseline.require_saved_input(path, report)
        return drc_baseline.record(report)

    def _prepare(self, command, board, params, baseline):
        if command == "board zone create":
            return zone_plan.create(board, params, baseline)
        return zone_plan.refill(board, params, Settings.of(board.path.with_suffix(".kicad_pro")))

    def validate_plan(self, record):
        plan = record["plan"]
        path = file_path(plan["target"])
        if _dependencies(path) != plan["extensions"]["dependencies"]:
            raise DriverError(
                "E_CONFLICT",
                "Project/custom-rule dependencies changed since the approved plan.",
                details={"conflict": "state_drift"},
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
        try:
            board = Board.load(path)
            baseline = hashlib.sha256(board.document.source.encode()).hexdigest()
            dependencies = _dependencies(path)
            before_drc = self._capture(path)
            prepared, selected, task = self._prepare(call.command, board, call.params, baseline)
            before = zone_readback.observe(
                path, selected, allow_missing=call.command.endswith("create")
            )
            intended = prepared.document.dumps().encode()
            expected, protected, _ = zone_readback.inspect_bytes(intended, selected)
            if protected != before.protected_sha256 or (
                call.command.endswith("refill")
                and {item["id"]: item["definition_sha256"] for item in expected}
                != {item["id"]: item["definition_sha256"] for item in before.objects}
            ):
                raise DriverError(
                    "E_VALIDATION",
                    "Prepared zone task changed protected definitions or semantics.",
                    details={"problem": "invalid_value", "parameter": "--board"},
                    dispatch_state="not_started",
                )
            if (
                before.snapshot["revision"] != baseline
                or digest(path) != baseline
                or _dependencies(path) != dependencies
                or before_drc["provenance"]["input"]["sha256"] != baseline
            ):
                raise DriverError(
                    "E_CONFLICT",
                    "Saved inputs changed during zone planning.",
                    details={"conflict": "state_drift"},
                    dispatch_state="not_started",
                )
        except zone_plan.ZonePlanError as exc:
            details = dict(exc.details)
            if exc.code in {"E_USAGE", "E_VALIDATION"}:
                details.update(
                    problem="invalid_value" if exc.code == "E_VALIDATION" else "conflicting_flags",
                    parameter="--board" if call.command.endswith("create") else "--zones",
                )
            raise DriverError(
                exc.code, str(exc), details=details, dispatch_state="not_started"
            ) from exc
        except drc_baseline.Incomplete as exc:
            raise DriverError(
                "E_CONFIG",
                "Zone baseline custom check did not complete.",
                details={"checker": exc.provenance, "reason": str(exc)},
                dispatch_state="not_started",
            ) from exc
        except (ValueError, UnicodeError) as exc:
            raise DriverError(
                "E_VALIDATION",
                "The zone inputs cannot be observed unambiguously.",
                details={"problem": "invalid_value", "parameter": "--board", "reason": str(exc)},
                dispatch_state="not_started",
            ) from exc
        _snapshot_dependencies(before, dependencies)
        return {
            "target": target,
            "base_snapshot": before.snapshot,
            "changes": [
                {
                    "action": "create" if call.command.endswith("create") else "refill",
                    "resource": "copper-zone",
                    "id": identity,
                    "summary": "Create an unfilled copper zone"
                    if call.command.endswith("create")
                    else "Replace selected saved zone fill",
                    "after": next(item for item in expected if item["id"] == identity),
                }
                for identity in selected
            ],
            "outputs": [{"path": str(path), "effect": "persist", "exists": True}],
            "constraints": [
                "Preserve non-target board semantics and selected refill definitions.",
                "Bind present/absent project and custom-rule dependencies; "
                "reject new bounded custom findings.",
            ],
            "unknowns": [item["reason"] for item in UNCHECKED],
            "extensions": {
                "before": observation_data(before),
                "selected": selected,
                "task": task,
                "params": call.params,
                "dependencies": dependencies,
                "drc_before": before_drc,
                "expected": expected,
                "intended_sha256": hashlib.sha256(intended).hexdigest(),
            },
            "_untrusted": [
                "changes",
                "extensions.before",
                "extensions.expected",
                "extensions.drc_before",
                "extensions.params",
            ],
        }

    def profile(self, plan, after, comparison):
        requirements, observations = [], []
        expected = plan["extensions"]["expected"]
        actual = {item["id"]: item for item in after.objects}

        def add(identifier, field, wanted, found, *, checker=False):
            requirements.append(
                {
                    "id": identifier,
                    "kind": "field",
                    "field_path": field,
                    "expected": {"value": wanted, "unit": None},
                    "comparison": {"operator": "equals"},
                    "allowed_sources": ["custom_checker" if checker else "file_consumer"],
                    "independence": {
                        "required": False,
                        "writer_sources": [
                            "fileformat.board",
                            "fileformat.sexpr",
                            "fileformat.zonefill",
                        ],
                        "expectation_sources": [
                            "drc_baseline.custom_checker"
                            if checker
                            else "zone_readback.lexical_decimal",
                            "zone_plan.prepared_serialization",
                        ],
                    },
                }
            )
            observations.append(
                {
                    "requirement_id": identifier,
                    "observed": {"value": found, "unit": None},
                    "source": {
                        "kind": "custom_checker" if checker else "file_consumer",
                        "completion": "completed",
                        "target": plan["target"],
                        "snapshot": after.snapshot,
                        "method": "Saved-file custom finding multiset gate "
                        + drc_baseline.IDENTITY_VERSION
                        if checker
                        else "Saved-byte lexical/Decimal zone consumer v" + after.source["version"],
                        "evidence_ref": "#/extensions/finding_comparison/final"
                        if checker
                        else "#/extensions/readback/observed",
                        "defect_sources": [
                            "fileformat.board",
                            "fileformat.geometry",
                            "drc_baseline.custom_checker",
                        ]
                        if checker
                        else ["zone_readback.lexical_decimal", "move_readback.lexer"],
                        "independence_basis": (
                            "Custom DRC shares writer geometry/parser; "
                            "source/version/input/rules/coverage retained."
                        )
                        if checker
                        else (
                            "Consumer imports no writer model/filler; definition, preservation "
                            "and fill expectations share this consumer/prepared serialization. "
                            "No independent geometric fill acceptance."
                        ),
                        "coverage": {"checked_paths": [field], "not_checked": []},
                    },
                }
            )

        add(
            "selected-zone-ids",
            "/zones/ids",
            sorted(item["id"] for item in expected),
            sorted(actual),
        )
        for index, item in enumerate(expected):
            found = actual.get(item["id"], {})
            for field, wanted in item.items():
                add(f"zone-{index}-{field}", f"/zones/{index}/{field}", wanted, found.get(field))
        add(
            "non-target-semantics",
            "/protected_sha256",
            plan["extensions"]["before"]["protected_sha256"],
            after.protected_sha256,
        )
        add(
            "custom-finding-gate",
            "/custom_DRC/no_new_findings",
            "passed",
            comparison["status"],
            checker=True,
        )
        profile = {
            "profile": PROFILE,
            "target": plan["target"],
            "snapshot": after.snapshot,
            "requirements": requirements,
            "observations": observations,
            "not_checked": UNCHECKED,
        }
        return build_verification(profile, scope=SCOPE, validator=self.validator)

    def apply(self, call, plan):
        path = file_path(plan["target"])
        self.require_current_scope(call, plan)
        self._guard(path)
        self.validate_plan({"plan": plan})
        board = Board.load(path)
        baseline = plan["base_snapshot"]["revision"]
        if hashlib.sha256(board.document.source.encode()).hexdigest() != baseline:
            raise DriverError(
                "E_CONFLICT",
                "Board drifted before zone dispatch reached the writer.",
                details={"conflict": "state_drift"},
                dispatch_state="not_started",
            )
        prepared, selected, _ = self._prepare(
            call.command, board, plan["extensions"]["params"], baseline
        )
        intended = hashlib.sha256(prepared.document.dumps().encode()).hexdigest()
        if (
            intended != plan["extensions"]["intended_sha256"]
            or selected != plan["extensions"]["selected"]
        ):
            raise DriverError(
                "E_CONFLICT",
                "Prepared zone result differs from the approved plan.",
                details={"conflict": "state_drift"},
                dispatch_state="not_started",
            )
        transaction = MoveTransaction(path, call.operation_id, baseline, intended)
        try:
            transaction.arm()
        except (OSError, ValueError) as exc:
            code = "E_CONFLICT" if isinstance(exc, (FileExistsError, ValueError)) else "E_IO"
            details = {"reason": str(exc)}
            if code == "E_CONFLICT":
                details["conflict"] = "state_drift" if isinstance(exc, ValueError) else "lock_held"
            raise DriverError(
                code,
                "The zone transaction could not start.",
                details=details,
                dispatch_state="not_started",
            ) from exc
        stage, after, comparison, verification = "persist", None, None, None
        try:
            self.require_current_scope(call, plan)
            self.validate_plan({"plan": plan})
            if digest(path) != baseline:
                raise ValueError("board changed after the zone lock was acquired")
            prepared.document.save(path)
            stage = "verify"
            after = _snapshot_dependencies(
                zone_readback.observe(path, selected, allow_missing=True), _dependencies(path)
            )
            comparison = drc_baseline.compare_records(
                plan["extensions"]["drc_before"], self._capture(path)
            )
            if comparison["final"]["provenance"]["input"]["sha256"] != after.snapshot["revision"]:
                raise drc_baseline.Incomplete(
                    "Custom checker and zone consumer read different board bytes"
                )
            verification = self.profile(plan, after, comparison)
            if comparison["status"] != "passed" or verification["status"] != "passed":
                raise DriverError(
                    "E_VERIFY_INCOMPLETE"
                    if comparison["status"] == "incomplete"
                    else "E_VERIFY_FAILED",
                    "The saved zone did not satisfy its bounded verification criteria.",
                )
            self.validate_plan({"plan": plan})
            if digest(path) != after.snapshot["revision"]:
                raise ValueError("board changed after the zone readback")
            transaction.cleanup()
        except Exception as exc:
            attempted = verification or _incomplete(str(exc))
            restoration, error, current, no_change = None, None, None, False
            try:
                no_change = digest(path) == baseline
                transaction.rollback()
                current = _snapshot_dependencies(
                    zone_readback.observe(path, selected, allow_missing=True), _dependencies(path)
                )
                restoration = {
                    "matches_baseline": current.snapshot["revision"] == baseline,
                    "snapshot": current.snapshot,
                    "source": current.source,
                }
                if not restoration["matches_baseline"]:
                    raise OSError("independent restoration differs from the board baseline")
            except Exception as failure:
                error, current = str(failure), None
            verify = _incomplete(
                "Requested zone task was not committed.",
                current.snapshot["id"] if current else None,
            )
            if current:
                verify.update(
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
                verify,
                after,
                comparison,
                execution="failed",
                change="unknown" if error else "not_applied" if no_change else "rolled_back",
                persistence="unknown" if error else "unchanged",
                failed_stage=stage,
            )
            receipt["extensions"].update(
                attempted_verification=attempted,
                restoration=restoration,
                restoration_error=error,
                failure_reason=str(exc),
            )
            thermals = [
                finding["report"]
                for finding in (comparison or {}).get("new_findings", [])
                if finding.get("identity", {}).get("rule") == "starved_thermal"
            ]
            if thermals:
                receipt["extensions"]["thermal_advice"] = {
                    "source": "bounded custom checker; correlate with a fresh official KiCad DRC",
                    "findings": thermals,
                    "next_action": "Keep thermal connections. Inspect the named pad/zone "
                    "in an owned copy: provide required connected spokes by changing "
                    "nearby copper/obstacles or thermal gap/bridge settings, then re-plan "
                    "and refill. Spokes reaching only isolated copper do not establish "
                    "useful connectivity. Keep the minimum spoke rule and default thermal mode.",
                    "limits": "The custom checker is not the native connectivity/rule resolver. "
                    "Same-layer spoke counts can differ from KiCad's connected-track counts.",
                }
            raise DriverError(
                "E_OUTCOME_UNKNOWN"
                if error
                else exc.code
                if isinstance(exc, DriverError)
                else "E_VERIFY_INCOMPLETE"
                if isinstance(exc, drc_baseline.Incomplete)
                else "E_NATIVE_FAILURE",
                "Zone write did not complete; inspect its durable record and actual readback.",
                details={"write_state": "unknown" if error else "rolled_back"},
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
            comparison,
            execution="completed",
            change="applied" if changed else "not_applied",
            persistence="saved" if changed else "unchanged",
            failed_stage=None,
        )

    def receipt(self, call, plan, current, verification, attempted, comparison, **outcome):
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
                    "change": "zone created"
                    if call.command.endswith("create")
                    else "zone refilled",
                    "evidence_ref": "#/extensions/readback/observed",
                }
                for item in current.objects
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
                "expected": plan["extensions"]["expected"],
                "observed": attempted.objects if attempted else [],
                "readback": observation_data(attempted) if attempted else None,
                "finding_comparison": comparison,
                "coverage": {
                    "checked": [
                        "selected saved zone representation",
                        "non-target semantics",
                        "bounded custom finding identities",
                    ],
                    "not_checked": [item["reason"] for item in UNCHECKED],
                },
            },
            "_untrusted": [
                "changed_items",
                "extensions.expected",
                "extensions.observed",
                "extensions.readback",
                "extensions.finding_comparison",
            ],
        }

    def recovery(self, record):
        plan = record["plan"]
        path = file_path(plan["target"])
        after = _snapshot_dependencies(
            zone_readback.observe(path, plan["extensions"]["selected"], allow_missing=True),
            _dependencies(path),
        )
        comparison = drc_baseline.compare_records(
            plan["extensions"]["drc_before"], self._capture(path)
        )
        if comparison["final"]["provenance"]["input"]["sha256"] != after.snapshot["revision"]:
            raise OSError("Custom checker and zone consumer read different board bytes")
        verification = self.profile(plan, after, comparison)
        outcome = (
            "not_applied"
            if after.snapshot["revision"] == plan["base_snapshot"]["revision"]
            else "applied"
            if verification["status"] == "passed"
            else "partial"
        )
        if [
            {"identity": Path(item["path"]).as_uri(), "revision": item["sha256"] or "absent"}
            for item in _dependencies(path)
        ] != after.snapshot["dependencies"] or digest(path) != after.snapshot["revision"]:
            raise OSError("saved zone inputs changed during recovery observation")
        return {
            "operation_id": record["operation_id"],
            "target": plan["target"],
            "outcome": outcome,
            "snapshot": after.snapshot,
            "readback": observation_data(after),
            "verification": verification,
            "expected": plan["extensions"]["expected"],
            "finding_comparison": comparison,
            "limits": "Observe current saved state only; external attribution unknown. "
            "Never repeat a lost write or overwrite its observed board.",
        }
