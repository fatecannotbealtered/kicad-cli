"""Saved power-net tasks with captured trials, owned writes and fresh recovery.

The trial runs existing domain primitives on disposable captured inputs. Its
saved candidate is HMAC-bound through the public driver plan. Native KiCad DRC
and independent circuit-intent/manufacturing acceptance remain separate gates.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import drc_baseline, power_readback, power_rules, read_inputs
from ._driver_sdk import tokens
from ._driver_sdk.models import DriverError
from ._driver_sdk.verification import PROFILE, build_verification
from .move_driver_adapter import MoveAdapter, file_path, target_of
from .move_transaction import (
    MoveTransaction,
    digest,
    owned_journal,
    paths,
    process_alive,
    regular,
    write_new,
)
from .native.via_hole_guard import LIMITS as VIA_HOLE_LIMITS
from .native.via_hole_guard import RULE as VIA_HOLE_RULE
from .native.via_hole_guard import SCOPE as VIA_HOLE_SCOPE
from .native.via_hole_guard import SOURCE as VIA_HOLE_SOURCE
from .native.via_hole_guard import ViaHoleGuard
from .sync_inputs import Drift, InputError

COMMANDS = {
    "project netclass set",
    "board route repair",
    "board route rebuild",
    "board route rewidth",
    "board track widen",
}
SCOPE = (
    "Saved effective netclass settings, actual copper geometry and protected semantics; "
    "bounded custom finding identities."
)
UNCHECKED = [
    {"field_path": field, "required": False, "reason": reason}
    for field, reason in [
        (
            "/native_DRC",
            "Custom geometry/checker shares writer defect sources; "
            "official native DRC is separate.",
        ),
        (
            "/design_intent",
            "Pad net preservation is not an independent intended circuit comparison.",
        ),
        ("/ampacity", "No load, copper thickness or temperature-rise design basis supplied."),
        ("/manufacturing", "Full Gerber/drill/BOM/CPL geometry acceptance is separate."),
        (
            "/libraries",
            "Candidate checker intentionally defers libraries; "
            "native engineering workflow binds and checks them separately.",
        ),
        (
            "/editor_memory",
            "Saved file adapter has no live editor mutation or close/reopen operation.",
        ),
    ]
]

DRILL_SCOPE = (
    "ordinary F.Cu/B.Cu through-via drill disk to all supported SMD copper pads, independent of net"
)
DRILL_SOURCE = "kicad_cli.native.smd_drill_guard analytic rect/roundrect; not native DRC"
DRILL_FIELDS = {
    "status",
    "enabled",
    "executed",
    "clearance_mm",
    "scope",
    "same_net_exempt",
    "source",
    "checked_pad_count",
    "shape_counts",
    "planner_calls",
    "limits",
}
VIA_HOLE_FIELDS = {
    "status",
    "source",
    "scope",
    "same_net_exempt",
    "binding",
    "planner_calls",
    "checked_selected_via_count",
    "checked_pair_count",
    "limits",
}


def route_via_hole_constraint(command, params, trial, inputs):
    """Require the actual finite grid witness and its captured rule-file identities."""
    if not command.startswith("board route ") or params.get("engine", "grid") != "grid":
        return None
    result = trial.get("result")
    value = result.get("via_hole_spacing") if isinstance(result, dict) else None
    if not isinstance(value, dict) or set(value) != VIA_HOLE_FIELDS:
        raise ValueError("Prepared grid route omitted or changed its via-hole spacing witness")
    binding = value.get("binding")
    expected_files = {}
    for suffix, key in [(".kicad_pro", "project_sha256"), (".kicad_dru", "custom_rules_sha256")]:
        path = Path(params["board"]).expanduser().resolve().with_suffix(suffix)
        rows = [row for row in inputs if Path(row["path"]).resolve() == path]
        if len(rows) != 1:
            raise ValueError("Captured via-hole rule dependency is missing or ambiguous")
        expected_files[key] = rows[0]["sha256"]
    if (
        value["status"] != "enforced"
        or value["source"] != VIA_HOLE_SOURCE
        or value["scope"] != VIA_HOLE_SCOPE
        or value["same_net_exempt"] is not False
        or not isinstance(binding, dict)
        or set(binding) != {"rule", "minimum_nm", "defaulted_to_kicad10", *expected_files}
        or binding.get("rule") != VIA_HOLE_RULE
        or type(binding.get("minimum_nm")) is not int
        or binding["minimum_nm"] < 0
        or type(binding.get("defaulted_to_kicad10")) is not bool
        or any(binding.get(k) != v for k, v in expected_files.items())
        or any(
            type(value.get(k)) is not int or value[k] < 0
            for k in ["planner_calls", "checked_selected_via_count", "checked_pair_count"]
        )
        or value["limits"] != VIA_HOLE_LIMITS
        or binding != ViaHoleGuard.read(params["board"]).binding
    ):
        raise ValueError("Prepared via-hole spacing witness does not match captured rules or scope")
    return copy.deepcopy(value)


def validate_drill_request(command, params):
    """Validate the optional constraint before starting disposable candidate preparation."""
    requested = params.get("smd-drill-clearance-mm")
    if requested is None:
        return None
    if command != "board route rewidth" or params.get("engine", "grid") != "grid":
        raise ValueError("SMD drill clearance is supported only by grid route rewidth")
    if (
        isinstance(requested, bool)
        or not isinstance(requested, (int, float))
        or not math.isfinite(requested)
        or not 0 <= requested <= 100
    ):
        raise ValueError("SMD drill clearance must be a finite number in 0..100 mm")
    return requested


def route_drill_constraint(command, params, trial):
    """Expose an actual prepared-policy witness, never infer success from request flags."""
    requested = validate_drill_request(command, params)
    if command != "board route rewidth":
        return None
    if requested is None:
        return {
            "status": "not_requested",
            "enabled": False,
            "executed": False,
            "clearance_mm": None,
            "scope": DRILL_SCOPE,
            "same_net_exempt": False,
            "source": DRILL_SOURCE,
            "checked_pad_count": 0,
            "shape_counts": {"rect": 0, "roundrect": 0},
            "planner_calls": 0,
            "limits": ["No SMD drill-to-pad constraint was requested or checked."],
        }
    result = trial.get("result")
    witness = result.get("smd_drill_clearance") if isinstance(result, dict) else None
    if not isinstance(witness, dict) or set(witness) != DRILL_FIELDS:
        raise ValueError("Prepared route omitted or changed the SMD drill constraint witness")
    counts = witness.get("shape_counts")
    if (
        witness["status"] != "enforced"
        or witness["enabled"] is not True
        or witness["executed"] is not True
        or isinstance(witness["clearance_mm"], bool)
        or not isinstance(witness["clearance_mm"], (int, float))
        or witness["clearance_mm"] != requested
        or witness["scope"] != DRILL_SCOPE
        or witness["same_net_exempt"] is not False
        or witness["source"] != DRILL_SOURCE
        or type(witness["checked_pad_count"]) is not int
        or witness["checked_pad_count"] < 0
        or type(witness["planner_calls"]) is not int
        or witness["planner_calls"] < 0
        or not isinstance(counts, dict)
        or set(counts) != {"rect", "roundrect"}
        or any(type(value) is not int or value < 0 for value in counts.values())
        or sum(counts.values()) != witness["checked_pad_count"]
        or not isinstance(witness["limits"], list)
        or not witness["limits"]
        or any(not isinstance(row, str) or not row for row in witness["limits"])
    ):
        raise ValueError("Prepared route SMD drill constraint does not match the frozen request")
    return copy.deepcopy(witness)


def _error(code, message, **details):
    if code == "E_VALIDATION":
        details.update(problem="invalid_value", parameter="--board")
    if code == "E_CONFLICT":
        details.setdefault("conflict", "state_drift")
    return DriverError(code, message, details=details, dispatch_state="not_started")


def _snapshot(path, observed, inputs):
    dependencies = [
        {"identity": Path(row["path"]).as_uri(), "revision": row["sha256"] or "absent"}
        for row in inputs
        if row["path"] != str(path)
    ]
    return {
        "id": "disk:" + tokens.digest([observed["board_sha256"], dependencies]),
        "source": "disk",
        "revision": observed["board_sha256"],
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "dependencies": dependencies,
        "extensions": {"board": str(path), "project_sha256": observed["project_sha256"]},
    }


def _readback(path, observed, inputs):
    source = drc_baseline._source()
    return {
        "snapshot": _snapshot(path, observed, inputs),
        "objects": observed["objects"],
        "pads": observed["pads"],
        "protected_sha256": observed["protected_sha256"],
        "project_sha256": observed["project_sha256"],
        "source": {
            "kind": "independent_consumer",
            "name": "kicad_cli.power_readback",
            "version": "1",
            "code_sha256": source["code_sha256"],
            "code_scope": source["code_scope"],
            "completed": True,
            "input_sha256": observed["board_sha256"],
            "limits": "Fresh lexical/Decimal saved copper and JSON consumption, "
            "not native connectivity or DRC.",
        },
    }


def _map_paths(value, stage, original):
    # Preserve the actual trial source separately; normalize only its relocated
    # input locators for equivalence against the captured engineering inputs.
    if isinstance(value, dict):
        return {key: _map_paths(item, stage, original) for key, item in value.items()}
    if isinstance(value, list):
        return [_map_paths(item, stage, original) for item in value]
    if isinstance(value, str) and value.startswith(str(stage) + os.sep):
        return str(original / Path(value).relative_to(stage))
    return value


def _incomplete(reason, snapshot=None):
    return {
        "status": "incomplete",
        "scope": SCOPE,
        "snapshot_id": snapshot,
        "coverage": "none",
        "checks": [],
        "evidence_refs": [],
        "not_checked": [{"id": "power-readback", "required": True, "reason": reason}],
        "reason": reason,
    }


class PowerAdapter(MoveAdapter):
    state_dir = None
    environ = None
    secret_provider = None
    binding_provider = None

    def journal_path(self, record):
        return Path(record["plan"]["extensions"]["write_path"]).resolve(strict=True)

    def _check_inputs(self, plan, *, after=False):
        captured = read_inputs.capture(board=file_path(plan["target"]))
        expected = copy.deepcopy(plan["extensions"]["inputs"])
        if after:
            for row in expected:
                if row["path"] == plan["extensions"]["write_path"]:
                    row["sha256"] = plan["extensions"]["intended_sha256"]
        if captured.manifest != expected:
            raise _error("E_CONFLICT", "Power-task saved inputs drifted from the bound snapshot.")
        return captured

    def _candidate(self, plan):
        name = Path(plan["extensions"]["candidate"])
        regular(name)
        root = (self.state_dir / "power-trials").resolve()
        if (
            not name.resolve(strict=True).is_relative_to(root)
            or digest(name) != plan["extensions"]["intended_sha256"]
        ):
            raise _error(
                "E_CONFLICT", "Prepared power candidate changed or escaped its owned stage."
            )
        return name.read_bytes()

    def validate_plan(self, record):
        plan = record["plan"]
        self._check_inputs(plan)
        self._candidate(plan)
        external = plan["extensions"].get("external_router")
        if external:
            for row in external:
                regular(Path(row["path"]))
                if digest(Path(row["path"])) != row["sha256"]:
                    raise _error("E_CONFLICT", "External router build changed after preview.")

    def _trial(self, call, inputs, selected):
        stage = self.state_dir / "power-trials" / call.operation_id
        candidate_dir = stage / "candidate"
        candidate_dir.mkdir(parents=True, exist_ok=False)
        for row in inputs.manifest:
            if row["present"]:
                write_new(candidate_dir / Path(row["path"]).name, inputs.raw(row["path"]))
        candidate = candidate_dir / inputs.board.name
        params = dict(call.params)
        params["mode"] = call.command.rsplit(" ", 1)[-1]
        external = []
        if params.get("engine") == "freerouting":
            from .payload.freerouting import status

            installed = status()
            if not installed["usable"]:
                raise _error(
                    "E_CONFIG",
                    "Configured Freerouting/Java is unavailable.",
                    backend=installed,
                    next_action="Use --engine grid or explicitly configure "
                    "an existing compatible router/Java.",
                )
            for key in ("jar", "java"):
                name = Path(installed[key]).resolve(strict=True)
                regular(name)
                external.append({"path": str(name), "sha256": digest(name)})
                params[key] = str(name)
        request = stage / "request.json"
        payload = {
            "owner": "kicad-cli.power-trial.v1",
            "board": str(candidate),
            "command": call.command,
            "selected": selected,
            "params": params,
            "engineering_target": target_of(inputs.board),
            "binding": self.binding_provider(),
            "scope_digest": tokens.digest(call.extras["scope"].record),
            "captured": {str(p): digest(p) for p in candidate_dir.iterdir()},
        }
        payload["hmac"] = hmac.new(
            self.secret_provider(), tokens.canonical(payload), hashlib.sha256
        ).hexdigest()
        write_new(request, json.dumps(payload).encode())
        argv = (
            [sys.executable, "--internal-power-trial", str(request)]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-B", "-m", "kicad_cli.power_prepare", "--request", str(request)]
        )
        env = dict(
            self.environ or os.environ,
            PYTHONIOENCODING="utf-8",
            KICAD_CLI_STATE_DIR=str(self.state_dir),
            KICAD_CLI_SCOPE_FILE=str(call.extras["scope"].grant_file),
        )
        try:
            child = subprocess.run(
                argv,
                capture_output=True,
                env=env,
                timeout=1900,
                cwd=Path(__file__).resolve().parent.parent
                if not getattr(sys, "frozen", False)
                else None,
            )
        except subprocess.TimeoutExpired as exc:
            raise _error(
                "E_CONFIG",
                "Candidate preparation timed out; primary inputs were not written.",
                stage=str(stage),
            ) from exc
        write_new(stage / "stdout.txt", child.stdout)
        write_new(stage / "stderr.txt", child.stderr)
        try:
            value = json.loads(child.stdout)
            failure = value.get("error") if isinstance(value, dict) else None
            if (
                call.command.startswith("board route ")
                and value.get("ok") is False
                and isinstance(failure, dict)
                and failure.get("code") == "E_VALIDATION"
                and isinstance(failure.get("details"), dict)
                and failure["details"].get("constraint")
                in {"smd-drill-clearance-mm", "project-min-hole-to-hole"}
                and isinstance(failure["details"].get("reason"), str)
                and isinstance(failure["details"].get("objects"), list)
            ):
                constraint = failure["details"]["constraint"]
                raise DriverError(
                    "E_VALIDATION",
                    "Finite route drill constraint could not be established; "
                    "primary engineering inputs were not written.",
                    details={
                        "problem": "invalid_value",
                        "parameter": "--smd-drill-clearance-mm"
                        if constraint == "smd-drill-clearance-mm"
                        else "--board",
                        "constraint": constraint,
                        "reason": failure["details"]["reason"],
                        "objects": copy.deepcopy(failure["details"]["objects"]),
                        "phase": "candidate_validation",
                        "stage": str(stage),
                        "returncode": child.returncode,
                        "candidate_error": failure,
                        "primary_write_state": "not_applied",
                        "operation_registered": False,
                        "next_action": "Inspect the declared pad/via identities and supported "
                        "shape/layer limits before requesting a new preview. No operation was "
                        "registered to confirm or resolve; independent native DRC is separate.",
                        "_untrusted": ["reason", "objects", "stage", "candidate_error"],
                    },
                    dispatch_state="not_started",
                )
            if (
                value.get("ok") is False
                and isinstance(failure, dict)
                and failure.get("code") == "E_INTEGRITY"
            ):
                raise _error(
                    "E_VALIDATION",
                    "Prepared candidate failed geometry verification; "
                    "the trial did not write primary inputs.",
                    phase="candidate_validation",
                    stage=str(stage),
                    returncode=child.returncode,
                    candidate_error=failure,
                    primary_write_state="not_applied",
                    operation_registered=False,
                    next_action="Inspect board check and candidate_error findings before "
                    "choosing a geometry operation for the explicit affected nets. "
                    "Then request a new preview; no operation was registered to resolve. "
                    "Official native DRC remains a separate requirement.",
                )
            if (
                child.returncode
                or value.get("ok") is not True
                or digest(candidate) != value["candidate_sha256"]
            ):
                raise ValueError("Private candidate preparation did not complete")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise _error(
                "E_CONFIG",
                "Power candidate failed; primary inputs are unchanged.",
                stage=str(stage),
                returncode=child.returncode,
            ) from exc
        return candidate, value, external

    def plan(self, call):
        from .driver_runtime import _trace

        _trace(call.command)
        try:
            try:
                validate_drill_request(call.command, call.params)
            except ValueError as exc:
                raise DriverError(
                    "E_VALIDATION",
                    str(exc),
                    details={
                        "problem": "invalid_value",
                        "parameter": "--smd-drill-clearance-mm",
                    },
                    dispatch_state="not_started",
                ) from exc
            path = Path(call.params["board"]).expanduser().resolve(strict=True)
            target = target_of(path)
            call.extras["scope"].require(effects=call.effects, target=target)
            trial_dir = self.state_dir / "power-trials" / call.operation_id
            write_path = (
                path.with_suffix(".kicad_pro") if call.command == "project netclass set" else path
            )
            call.extras["scope"].require(
                effects=call.effects,
                target=target,
                outputs=[
                    {"path": str(trial_dir), "effect": "persist"},
                    {"path": str(write_path), "effect": "persist"},
                ],
            )
            self._guard(path)
            inputs = read_inputs.capture(board=path)
            before = power_readback.inspect(
                inputs.raw(path), inputs.raw(path.with_suffix(".kicad_pro")), []
            )
            if before["project"] is None:
                raise FileNotFoundError("An existing matching .kicad_pro is required")
            selected = power_rules.select(call.params, before, call.command)
            if (
                call.command.startswith("board route ")
                and call.params.get("engine", "grid") == "grid"
                and set(before["copper_layers"]) != {"F.Cu", "B.Cu"}
            ):
                raise ValueError("Grid routing requires exactly front/back copper layers")
            before = power_readback.inspect(
                inputs.raw(path), inputs.raw(path.with_suffix(".kicad_pro")), selected
            )
            power_rules.validate_dimensions(call.params, path.with_suffix(".kicad_pro"))
            original_drc = drc_baseline.record(drc_baseline.capture(path)[1])
            changes, external = [], []
            if call.command == "project netclass set":
                prepared, affected, class_change = power_rules.set_class(
                    before["project"], call.params, selected, before["nets"]
                )
                power_rules.require_class_minimums(
                    prepared, affected, path.with_suffix(".kicad_pro")
                )
                stage = self.state_dir / "power-trials" / call.operation_id / "candidate"
                stage.mkdir(parents=True, exist_ok=False)
                for row in inputs.manifest:
                    if row["present"]:
                        payload = (
                            (json.dumps(prepared, indent=2, ensure_ascii=False) + "\n").encode()
                            if Path(row["path"]).suffix == ".kicad_pro"
                            else inputs.raw(row["path"])
                        )
                        write_new(stage / Path(row["path"]).name, payload)
                candidate = stage / path.with_suffix(".kicad_pro").name
                candidate_board = candidate.with_suffix(".kicad_pcb")
                trial_drc = drc_baseline.record(drc_baseline.capture(candidate_board)[1])
                # Only approved netclass settings differ. Both originals are
                # retained, with the precise approved provenance delta visible.
                aligned = copy.deepcopy(original_drc)
                aligned["provenance"]["dependencies"] = _map_paths(
                    trial_drc["provenance"]["dependencies"], stage, path.parent
                )
                aligned["provenance"]["rules"] = trial_drc["provenance"]["rules"]
                trial = {
                    "result": {"effective_classes": class_change, "affected_nets": affected},
                    "before": aligned,
                    "drc": trial_drc,
                }
                changes = [
                    {
                        "action": "set",
                        "resource": "effective-netclass",
                        "id": net,
                        "summary": "Set effective netclass for " + net,
                        "before": class_change["before"][net],
                        "after": class_change["after"][net],
                    }
                    for net in affected
                ]
                write_path = path.with_suffix(".kicad_pro")
            else:
                candidate, trial, external = self._trial(call, inputs, selected)
                stage, candidate_board, write_path = candidate.parent, candidate, path
            observed = power_readback.observe(candidate_board, selected)
            comparison = drc_baseline.compare_records(
                _map_paths(trial["before"], stage, path.parent),
                _map_paths(trial["drc"], stage, path.parent),
            )
            if (
                observed["protected_sha256"] != before["protected_sha256"]
                or observed["pads"] != before["pads"]
            ):
                raise ValueError(
                    "Prepared candidate changed unrelated copper, components, "
                    "pads or zone definitions"
                )
            inputs.validate()
            if call.command != "project netclass set":
                old = {o["id"]: o for o in before["objects"]}
                new = {o["id"]: o for o in observed["objects"]}
                changes = [
                    {
                        "action": "replace",
                        "resource": "copper",
                        "id": uid,
                        "summary": "Change selected saved copper",
                        "before": old.get(uid),
                        "after": new.get(uid),
                    }
                    for uid in sorted(old.keys() | new.keys())
                    if old.get(uid) != new.get(uid)
                ]
            quality = power_rules.quality(
                observed, selected, call.params, command=call.command, before=before
            )
            drill_constraint = route_drill_constraint(call.command, call.params, trial)
            via_hole_constraint = route_via_hole_constraint(
                call.command, call.params, trial, inputs.manifest
            )
            return {
                "target": target,
                "level": "trial_observed",
                "trial_snapshot": dict(
                    _snapshot(
                        candidate_board,
                        observed,
                        read_inputs.capture(board=candidate_board).manifest,
                    ),
                    source="trial",
                ),
                "base_snapshot": _snapshot(path, before, inputs.manifest),
                "changes": changes,
                "outputs": [
                    {"path": str(write_path), "effect": "persist", "exists": True},
                    {"path": str(trial_dir), "effect": "persist", "exists": True},
                ],
                "constraints": [
                    "Exact candidate bytes/effective class inputs; preserve pad nets/components "
                    "and nonselected copper. Zone fills may change, definitions are protected.",
                    "New bounded custom finding identities refused; "
                    "native intent/DRC/manufacturing remain separate gates.",
                    "Apply is blocked when comparison is failed/incomplete or copper is below "
                    "the requested neck floor. Target-width gaps remain explicitly reported.",
                ],
                "unknowns": [item["reason"] for item in UNCHECKED],
                "can_apply": comparison["status"] == "passed"
                and not any(r["below_neck_floor"] for r in quality["per_net"])
                if call.command != "project netclass set"
                else comparison["status"] == "passed",
                "extensions": {
                    **({"smd_drill_clearance": drill_constraint} if drill_constraint else {}),
                    **({"via_hole_spacing": via_hole_constraint} if via_hole_constraint else {}),
                    "inputs": inputs.manifest,
                    "selected": selected,
                    "params": call.params,
                    "before": _readback(path, before, inputs.manifest),
                    "original_drc": original_drc,
                    "drc_before": comparison["baseline"],
                    "trial": trial,
                    "finding_comparison": comparison,
                    "expected": observed,
                    "quality": quality,
                    "write_path": str(write_path),
                    "candidate": str(candidate),
                    "intended_sha256": digest(candidate),
                    "external_router": external,
                    "engineering_target": "Board locator interlocks linked project and copper "
                    "mutations; outputs name the actual file written.",
                },
                "_untrusted": [
                    "changes",
                    "extensions.expected",
                    "extensions.trial",
                    "extensions.before",
                    "extensions.original_drc",
                ],
            }
        except DriverError:
            raise
        except FileNotFoundError as exc:
            raise _error("E_NOT_FOUND", "Required saved power-task input does not exist.") from exc
        except Drift as exc:
            raise _error("E_CONFLICT", str(exc)) from exc
        except drc_baseline.Incomplete as exc:
            raise _error(
                "E_CONFIG", "Power-task custom check did not complete.", reason=str(exc)
            ) from exc
        except (ValueError, InputError, UnicodeError) as exc:
            raise _error(
                "E_VALIDATION", "Power task inputs/candidate cannot be verified.", reason=str(exc)
            ) from exc

    def profile(self, plan, current, comparison):
        ext = plan["extensions"]
        observed = current["observed"]
        criteria = [
            (
                "saved-board-bytes",
                "/board_sha256",
                ext["expected"]["board_sha256"],
                observed["board_sha256"],
                False,
            ),
            ("candidate-copper", "/copper", ext["expected"]["objects"], observed["objects"], False),
            ("pad-net-preservation", "/pads", ext["expected"]["pads"], observed["pads"], False),
            (
                "protected-semantics",
                "/protected",
                ext["expected"]["protected_sha256"],
                observed["protected_sha256"],
                False,
            ),
            (
                "effective-project",
                "/project",
                ext["expected"]["project_sha256"],
                observed["project_sha256"],
                False,
            ),
            ("no-new-findings", "/custom_DRC", "passed", comparison["status"], True),
        ]
        requirements, observations = [], []
        for identity, field, wanted, found, checker in criteria:
            kind = "custom_checker" if checker else "file_consumer"
            requirements.append(
                {
                    "id": identity,
                    "kind": "field",
                    "field_path": field,
                    "expected": {"value": wanted, "unit": None},
                    "comparison": {"operator": "equals"},
                    "allowed_sources": [kind],
                    "independence": {
                        "required": False,
                        "writer_sources": [
                            "power_geometry",
                            "fileformat.board",
                            "fileformat.sexpr",
                            "fileformat.zonefill",
                        ],
                        "expectation_sources": ["power_geometry.prepared_candidate"],
                    },
                }
            )
            observations.append(
                {
                    "requirement_id": identity,
                    "observed": {"value": found, "unit": None},
                    "source": {
                        "kind": kind,
                        "completion": "unknown"
                        if checker and comparison["status"] == "incomplete"
                        else "completed",
                        "target": plan["target"],
                        "snapshot": current["readback"]["snapshot"],
                        "method": "Bounded custom finding multiset comparison"
                        if checker
                        else "Fresh lexical Decimal/JSON saved-file consumer",
                        "defect_sources": ["fileformat.drc", "fileformat.board", "fileformat.sexpr"]
                        if checker
                        else ["power_readback.lexical_decimal", "python.json"],
                        "evidence_ref": "#/extensions/finding_comparison"
                        if checker
                        else "#/extensions/readback",
                        "independence_basis": "Fresh saved consumer imports no copper writer. "
                        "Candidate expectations and custom finding gate still share writer "
                        "defect sources; independent engineering intent/native DRC are separate.",
                        "coverage": {"checked_paths": [field], "not_checked": []},
                    },
                }
            )
        return build_verification(
            {
                "profile": PROFILE,
                "target": plan["target"],
                "snapshot": current["readback"]["snapshot"],
                "requirements": requirements,
                "observations": observations,
                "not_checked": UNCHECKED,
            },
            scope=SCOPE,
            validator=self.validator,
        )

    def _observe(self, plan):
        path = file_path(plan["target"])
        inputs = read_inputs.capture(board=path)
        observed = power_readback.inspect(
            inputs.raw(path),
            inputs.raw(path.with_suffix(".kicad_pro")),
            plan["extensions"]["selected"],
        )
        drc = drc_baseline.record(drc_baseline.capture(path)[1])
        inputs.validate()
        comparison = drc_baseline.compare_records(plan["extensions"]["drc_before"], drc)
        return {
            "observed": observed,
            "readback": _readback(path, observed, inputs.manifest),
        }, comparison

    def receipt(self, call, plan, current, comparison, verification, **outcome):
        ext = plan["extensions"]
        return {
            "operation_id": call.operation_id,
            "capability_id": call.capability_id,
            "plan_id": call.plan_id,
            "target": plan["target"],
            "authorization_scope_id": plan["authorization_scope_id"],
            "effects": plan["effects"],
            "snapshot_before": plan["base_snapshot"],
            "snapshot_after": current["readback"]["snapshot"] if current else None,
            "failed_stage": outcome.pop("failed_stage"),
            "outcome": dict(outcome, delivery="not_applicable", verification=verification),
            "changed_items": [
                {"id": row["id"], "change": row["summary"], "evidence_ref": "#/extensions/readback"}
                for row in plan["changes"]
            ]
            if outcome["change"] == "applied"
            else [],
            "artifacts": [],
            "deliveries": [],
            "recovery": {
                "action": "none" if outcome["execution"] == "completed" else "inspect",
                "reason": "Use authenticated operation show before continuing; "
                "never replay unknown writes.",
                "supported": True,
            },
            "extensions": {
                **(
                    {"via_hole_spacing": copy.deepcopy(ext["via_hole_spacing"])}
                    if "via_hole_spacing" in ext
                    else {}
                ),
                **(
                    {"smd_drill_clearance": copy.deepcopy(ext["smd_drill_clearance"])}
                    if "smd_drill_clearance" in ext
                    else {}
                ),
                "write_path": ext["write_path"],
                "intended_sha256": ext["intended_sha256"],
                "readback": current["readback"] if current else None,
                "expected": ext["expected"],
                "quality": power_rules.quality(
                    current["observed"],
                    ext["selected"],
                    ext["params"],
                    command=call.command,
                    before=ext["before"],
                )
                if current
                else None,
                "finding_comparison": comparison,
                "coverage": {
                    "checked": [
                        "saved candidate copper dimensions/net/layer/vias",
                        "effective netclass project bytes",
                        "protected component/pad/copper semantics",
                        "bounded custom finding identities",
                    ],
                    "not_checked": [row["reason"] for row in UNCHECKED],
                },
            },
            "_untrusted": [
                "changed_items",
                "extensions.readback",
                "extensions.expected",
                "extensions.finding_comparison",
            ],
        }

    def apply(self, call, plan):
        path = file_path(plan["target"])
        self.require_current_scope(call, plan)
        self._guard(path)
        self.validate_plan({"plan": plan})
        write_path = Path(plan["extensions"]["write_path"])
        baseline = next(
            r["sha256"] for r in plan["extensions"]["inputs"] if r["path"] == str(write_path)
        )
        transaction = MoveTransaction(
            write_path, call.operation_id, baseline, plan["extensions"]["intended_sha256"]
        )
        pending = write_path.with_name(write_path.name + "." + call.operation_id + ".pending")
        try:
            transaction.arm()
        except (OSError, ValueError) as exc:
            raise _error(
                "E_CONFLICT" if isinstance(exc, (FileExistsError, ValueError)) else "E_IO",
                "Power transaction could not start.",
                reason=str(exc),
            ) from exc
        current, comparison, verification, stage = None, None, None, "persist"
        try:
            self.require_current_scope(call, plan)
            self.validate_plan({"plan": plan})
            write_new(pending, self._candidate(plan))
            self._check_inputs(plan)
            os.replace(pending, write_path)
            stage = "verify"
            self._check_inputs(plan, after=True)
            current, comparison = self._observe(plan)
            verification = self.profile(plan, current, comparison)
            if verification["status"] != "passed":
                raise DriverError(
                    "E_VERIFY_FAILED", "Saved power candidate failed bounded verification."
                )
            self._check_inputs(plan, after=True)
            transaction.cleanup()
        except Exception as exc:
            attempted = verification or _incomplete(str(exc))
            error, restored, no_change = None, None, False
            try:
                no_change = digest(write_path) == baseline
                transaction.rollback()
                self._check_inputs(plan)
                restored, _ = self._observe(plan)
                pending.unlink(missing_ok=True)
            except Exception as failure:
                error = str(failure)
            restored_verify = _incomplete(
                "Requested power task not committed.",
                restored["readback"]["snapshot"]["id"] if restored and not error else None,
            )
            if restored and not error:
                restored_verify.update(
                    coverage="partial",
                    checks=[
                        {
                            "id": "restored-baseline",
                            "status": "passed",
                            "required": True,
                            "evidence_ref": "#/extensions/baseline_restored",
                        }
                    ],
                    evidence_refs=["#/extensions/baseline_restored"],
                )
            receipt = self.receipt(
                call,
                plan,
                restored,
                comparison,
                restored_verify,
                execution="failed",
                change="unknown" if error else "not_applied" if no_change else "rolled_back",
                persistence="unknown" if error else "unchanged",
                failed_stage=stage,
            )
            receipt["extensions"].update(
                attempted_verification=attempted,
                attempted_readback=current,
                restoration_error=error,
                baseline_restored=bool(restored) and not error,
                failure_reason=str(exc),
                pending_path=str(pending),
            )
            raise DriverError(
                "E_OUTCOME_UNKNOWN"
                if error
                else "E_VERIFY_FAILED"
                if stage == "verify"
                else "E_NATIVE_FAILURE",
                "Power write did not complete; inspect actual files and the durable operation.",
                receipt=receipt,
                dispatch_state="dispatched",
            ) from exc
        changed = baseline != plan["extensions"]["intended_sha256"]
        return self.receipt(
            call,
            plan,
            current,
            comparison,
            verification,
            execution="completed",
            change="applied" if changed else "not_applied",
            persistence="saved" if changed else "unchanged",
            failed_stage=None,
        )

    def recovery(self, record):
        plan = record["plan"]
        current, comparison = self._observe(plan)
        before, observed = plan["extensions"]["before"], current["readback"]
        same = (
            observed["snapshot"]["revision"] == before["snapshot"]["revision"]
            and observed["snapshot"]["dependencies"] == before["snapshot"]["dependencies"]
        )
        verified = self.profile(plan, current, comparison)
        return {
            "operation_id": record["operation_id"],
            "target": plan["target"],
            "outcome": "not_applied"
            if same
            else "applied"
            if verified["status"] == "passed"
            else "partial",
            "snapshot": observed["snapshot"],
            "readback": observed,
            "verification": verified,
            "expected": plan["extensions"]["expected"],
            "finding_comparison": comparison,
            "limits": "Current saved state only; no attribution, replay "
            "or automatic restore of external edits.",
        }

    def resolve_recovery(self, record, current, state_dir):
        path = self.journal_path(record)
        journal = owned_journal(path, record["operation_id"])
        if not journal:
            return
        if process_alive(journal["pid"]):
            raise OSError("Power writer has not been proven stopped")
        lock, backup, journal_path = paths(path)
        archive = (
            state_dir
            / "recovery"
            / (tokens.digest(record["operation_id"]) + "-baseline-" + digest(backup) + path.suffix)
        )
        if not archive.exists():
            write_new(archive, backup.read_bytes())
        observed_sha = (
            current["snapshot"]["revision"]
            if path.suffix == ".kicad_pcb"
            else current["readback"]["project_sha256"]
        )
        if digest(archive) != journal["baseline"] or digest(path) != observed_sha:
            raise OSError("Power baseline archive or current file drifted during resolution")
        for sidecar in (backup, lock, journal_path):
            sidecar.unlink()
