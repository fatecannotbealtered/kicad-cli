"Saved design tasks with prepared bytes, scoped writes and observable recovery."

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
from pathlib import Path

from . import drc_baseline, engineering_inputs
from . import engineering_readback as consumer
from ._driver_sdk import tokens
from ._driver_sdk.models import DriverError
from ._driver_sdk.verification import PROFILE, build_verification
from .document_transaction import (
    DocumentTransaction,
    authenticated,
    names,
    owned,
    resolve,
    revision,
)
from .move_driver_adapter import MoveAdapter, target_of
from .move_transaction import digest, regular, write_new
from .sync_inputs import Drift, InputError, _read

COMMANDS = frozenset(
    {
        "sch create",
        "sch edit",
        "project links repair",
        "board from-netlist",
        "board footprint place",
        "board zone stitch",
        "board silkscreen place",
        "library build",
        "board route patch",
        "sch export",
    }
)
UNCHECKED = [
    {
        "field_path": "/native_engineering",
        "required": False,
        "reason": (
            "Official ERC/DRC, independent frozen pin intent, drawing "
            "readability and manufacturing acceptance are separate gates; this "
            "receipt is a bounded saved-document commit."
        ),
    }
]


def target(path):
    return target_of(Path(path))


def primary(plan):
    return Path(plan["extensions"]["primary"])


def error(code, message, **details):
    if code == "E_VALIDATION":
        details.setdefault("problem", "invalid_value")
        details.setdefault("parameter", "--board/--schematic/--spec")
    if code == "E_CONFLICT":
        details.setdefault("conflict", "state_drift")
    return DriverError(code, message, details=details, dispatch_state="not_started")


def snapshot(path, rows, inputs):
    return {
        "id": "snap-" + tokens.digest([str(path), rows, inputs]),
        "revision": revision(path),
        "source": "disk",
        "observed_at": drc_baseline._now(),
        "dependencies": [
            {"identity": Path(r["path"]).as_uri(), "revision": r["sha256"] or "absent"}
            for r in inputs
            if r["path"] != str(path)
        ],
    }


def readback(path, rows, inputs):
    return {
        "objects": rows,
        "protected_sha256": tokens.digest(rows),
        "snapshot": snapshot(path, rows, inputs),
        "source": "fresh saved file-set consumer; schematic graph is custom/shared",
        "inputs": inputs,
    }


class EngineeringAdapter(MoveAdapter):
    state_dir = environ = secret_provider = binding_provider = record_provider = None

    def _guard_documents(self, paths):
        # A cleanup may have removed a secondary lock before reply/registration.
        # Keep every member blocked using the authenticated control-plane record.
        directory = self.state_dir / "document-transactions"
        requested = {str(Path(p)) for p in paths}
        if directory.exists():
            for checkpoint in directory.glob("*.json"):
                data = authenticated(checkpoint, self.secret_provider())
                involved = {data["primary"], *(r["path"] for r in data["journal"]["files"])}
                if not requested.intersection(involved):
                    continue
                record = self.record_provider(data["operation_id"])
                if data["phase"] != "cleaned" or (
                    record is None
                    or (record.get("receipt") is None and record.get("resolution") is None)
                ):
                    raise error(
                        "E_CONFLICT",
                        "Unregistered document cleanup requires operation show/resolve",
                        active_operation=data["operation_id"],
                    )
        for path in paths:
            name = Path(path)
            for lock in (
                name.parent / ("~" + name.name + ".lck"),
                name.parent / ("~" + name.stem + ".lck"),
                name.parent / ("~" + name.stem + ".kicad_pcb.lck"),
            ):
                if lock.exists():
                    raise error(
                        "E_CONFLICT", "Editor lock must be resolved by its owner", path=str(lock)
                    )
            lock, journal = names(name)
            if lock.exists() or journal.exists():
                raise error(
                    "E_CONFLICT",
                    "An unresolved document write requires observation",
                    path=str(name),
                )

    def _trial(self, call, inputs, path):
        root = self.state_dir / "engineering-trials" / call.operation_id
        stage = root / "candidate"
        stage.mkdir(parents=True, exist_ok=False)
        documents = [str(p.parent) for p in inputs.files if p.suffix == ".kicad_sch"]
        common = (
            Path(os.path.commonpath([str(path.parent), *documents])) if documents else path.parent
        )
        common = getattr(inputs, "stage_root", common)
        mapping = {}
        for source, raw in inputs.files.items():
            name = (
                stage / source.relative_to(common)
                if source.is_relative_to(common)
                else stage / "dependencies" / tokens.digest(str(source))[:12] / source.name
            )
            mapping[str(source)] = str(name)
            if raw is not None:
                name.parent.mkdir(parents=True, exist_ok=True)
                write_new(name, raw)
            elif inputs.hierarchy and source.suffix == ".kicad_sch":
                # Mirror already-authorized existing source directories only
                # inside this operation's disposable candidate tree.
                name.parent.mkdir(parents=True, exist_ok=True)
        candidate = Path(mapping[str(path)])
        candidate.parent.mkdir(parents=True, exist_ok=True)
        params = dict(call.params)
        for key in ("spec", "schematic", "changes", "board", "netlist", "project", "out"):
            if params.get(key):
                params[key] = str(Path(params[key]).expanduser().resolve())
        request = {
            "owner": "kicad-cli.engineering-trial.v1",
            "command": call.command,
            "params": params,
            "primary": str(candidate),
            "original_primary": str(path),
            "mapping": mapping,
            "hierarchy_incremental": inputs.hierarchy,
            "target": target(path),
            "effects": list(call.effects),
            "output_effect": "artifact" if call.command == "sch export" else "persist",
            "binding": self.binding_provider(),
            "scope_digest": tokens.digest(call.extras["scope"].record),
            "captured": {str(p): digest(p) for p in stage.rglob("*") if p.is_file()},
        }
        request["hmac"] = hmac.new(
            self.secret_provider(), tokens.canonical(request), hashlib.sha256
        ).hexdigest()
        request_path = root / "request.json"
        write_new(request_path, json.dumps(request).encode())
        argv = (
            [sys.executable, "--internal-engineering-trial", str(request_path)]
            if getattr(sys, "frozen", False)
            else [
                sys.executable,
                "-B",
                "-m",
                "kicad_cli.engineering_prepare",
                "--request",
                str(request_path),
            ]
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
                cwd=None
                if getattr(sys, "frozen", False)
                else Path(__file__).resolve().parent.parent,
            )
        except subprocess.TimeoutExpired as exc:
            raise error(
                "E_CONFIG",
                "Candidate preparation timed out; primary documents unchanged",
                stage=str(root),
            ) from exc
        write_new(root / "stdout.txt", child.stdout)
        write_new(root / "stderr.txt", child.stderr)
        try:
            value = json.loads(child.stdout)
            if child.returncode or value.get("ok") is not True:
                raise ValueError("Preparation failed")
        except (ValueError, TypeError) as exc:
            try:
                failed = json.loads(child.stdout).get("error", {})
            except (ValueError, AttributeError):
                failed = {}
            code = (
                failed.get("code")
                if failed.get("code")
                in {"E_CONFIG", "E_NOT_FOUND", "E_IO", "E_VALIDATION", "E_CONFLICT", "E_TIMEOUT"}
                else "E_VALIDATION"
            )
            details = dict(failed.get("details", {}))
            details.update(
                stage=str(root), returncode=child.returncode, native_message=failed.get("message")
            )
            if call.command == "sch edit" and code == "E_VALIDATION":
                # The domain helper has human-readable legacy `problem` text.
                # The public SDK reserves details.problem for a closed enum;
                # preserve the domain diagnostic as a structured field problem.
                domain_problem = details.pop("problem", None)
                if domain_problem and domain_problem != "invalid_value":
                    problems = list(details.get("problems", []))
                    problems.append(
                        {"path": details.get("path", "/"), "problem": str(domain_problem)}
                    )
                    details["problems"] = problems[:40]
                    details["problem_count"] = len(problems)
                details.update(
                    problem="invalid_value",
                    parameter="--changes",
                    schema_ref="urn:kicad-cli:sch-edit-request:1#/$defs/changes_file",
                    next_action="Read reference --command 'sch edit' for file shape and examples; "
                    "fix the reported change before requesting a new preview. "
                    "Render edited text again and synchronize PCB separately with project sync.",
                )
            raise error(
                code, "Candidate invariants incomplete; primary inputs unchanged", **details
            ) from exc
        for row in value["files"]:
            name = Path(row["candidate"])
            regular(name)
            if (
                not name.resolve().is_relative_to(stage.resolve())
                or digest(name) != row["intended"]
            ):
                raise error("E_CONFLICT", "Candidate escaped stage or changed")
            actual = Path(row["path"])
            if actual not in inputs.files:
                raise error("E_VALIDATION", "Candidate introduced an undeclared primary output")
            row["baseline"] = (
                consumer.sha(inputs.files[actual]) if inputs.files[actual] is not None else "absent"
            )
            if call.command in {"sch create", "board from-netlist"} and row["baseline"] != "absent":
                initialized = (
                    call.command == "board from-netlist"
                    and call.params.get("initialize-empty-project")
                    and actual.suffix == ".kicad_pro"
                    and json.loads(inputs.files[actual]) == {}
                )
                if not initialized:
                    raise error(
                        "E_CONFLICT", "Creation refuses existing output files", path=str(actual)
                    )
        return root, value

    def plan(self, call):
        from .driver_runtime import _trace

        _trace(call.command)
        try:
            inputs, path = engineering_inputs.capture(call.command, call.params)
            from . import engineering_backend

            try:
                native = (
                    engineering_backend.observe()
                    if call.command
                    in {"board zone stitch", "board silkscreen place", "board route patch"}
                    else None
                )
                if call.command == "sch export":
                    native = engineering_backend.observe_schematic()
                if call.command == "board route patch":
                    from . import kicad_env

                    official = kicad_env.find_official_cli()
                    if official is None:
                        raise ValueError("Native DRC executable unavailable")
                    native["files"].append({"path": official, "sha256": digest(Path(official))})
            except (ValueError, OSError) as exc:
                raise error(
                    "E_CONFIG",
                    "External native backend is unavailable",
                    reason=str(exc),
                    hint=(
                        "Set KICAD_CLI_ROOT or KICAD_CLI_PYTHON to an "
                        "existing supported installation; run doctor"
                    ),
                ) from exc
            scope = call.extras["scope"]
            effect = "artifact" if call.command == "sch export" else "persist"
            scope.require(effects=call.effects, target=target(path))
            stage = self.state_dir / "engineering-trials" / call.operation_id
            scope.require(
                effects=call.effects,
                target=target(path),
                outputs=[{"path": str(stage), "effect": effect}],
            )
            self._guard_documents(
                [
                    p
                    for p in inputs.files
                    if p.suffix in {".kicad_pcb", ".kicad_sch", ".kicad_pro", ".net"}
                ]
            )
            _, trial = self._trial(call, inputs, path)
            engineering_backend.validate(native)
            inputs.validate()
            rows = trial["files"]
            before = consumer.observe(rows)
            expected = consumer.observe([{"path": r["candidate"]} for r in rows])
            for actual, row in zip(expected, rows, strict=True):
                actual["path"] = row["path"]
            changes = [
                {
                    "action": "create" if r["baseline"] == "absent" else "replace",
                    "resource": "document",
                    "id": r["path"],
                    "summary": call.command + " saved document",
                    "before": r["baseline"],
                    "after": r["intended"],
                }
                for r in rows
                if r["baseline"] != r["intended"]
            ]
            comparison = trial.get("finding_comparison")
            from .engineering_feedback import can_apply, feedback

            engineering_feedback = feedback(call.command, trial.get("result"))
            outputs = [
                {"path": r["path"], "effect": effect, "exists": r["baseline"] != "absent"}
                for r in rows
            ] + [{"path": str(stage), "effect": effect, "exists": True}]
            scope.require(effects=call.effects, target=target(path), outputs=outputs)
            return {
                "target": target(path),
                "level": "trial_observed",
                "base_snapshot": snapshot(path, before, inputs.manifest),
                "trial_snapshot": dict(
                    snapshot(
                        Path(trial.get("primary_candidate", rows[0]["candidate"])),
                        expected,
                        inputs.manifest,
                    ),
                    source="trial",
                ),
                "changes": changes,
                "outputs": outputs,
                "constraints": [
                    (
                        "Save only authenticated prepared bytes; creation requires absent "
                        "outputs; no unmatched object deletion"
                    ),
                    (
                        "Each file is replaced atomically; a document set is not atomic. "
                        "Interruption can leave partial files"
                    ),
                    "Preserve bound inputs and identities; native acceptance is separate",
                ],
                "unknowns": [v["reason"] for v in UNCHECKED],
                "can_apply": (comparison is None or comparison["status"] == "passed")
                and can_apply(engineering_feedback),
                "extensions": {
                    "primary": str(path),
                    "cleanup_protocol": "document-set.v2",
                    "files": rows,
                    "inputs": inputs.manifest,
                    "environment": inputs.context,
                    "output_directories": inputs.directories,
                    "hierarchy_incremental": inputs.hierarchy,
                    "save_order": [row["path"] for row in rows],
                    "native_backend": native,
                    "expected": expected,
                    "before": before,
                    "trial": trial,
                    "engineering_feedback": engineering_feedback,
                },
                "_untrusted": [
                    "changes",
                    "extensions.expected",
                    "extensions.before",
                    "extensions.trial",
                ],
            }
        except DriverError:
            raise
        except FileExistsError as exc:
            raise error("E_CONFLICT", str(exc)) from exc
        except FileNotFoundError as exc:
            raise error(
                "E_NOT_FOUND", "Required engineering input is missing", path=str(exc)
            ) from exc
        except Drift as exc:
            raise error("E_CONFLICT", str(exc), **getattr(exc, "details", {})) from exc
        except (ValueError, InputError, KeyError, TypeError, OSError) as exc:
            raise error(
                "E_VALIDATION" if not isinstance(exc, OSError) else "E_IO",
                "Engineering task cannot be prepared",
                reason=str(exc),
                **getattr(exc, "details", {}),
            ) from exc

    def _inputs(self, plan, after=False):
        from . import engineering_backend
        from .sch_hierarchy_inputs import validate_directories

        try:
            validate_directories(plan["extensions"].get("output_directories", []))
        except (ValueError, OSError) as exc:
            raise error("E_CONFLICT", "Hierarchy output directory drift", reason=str(exc)) from exc

        try:
            engineering_backend.validate(plan["extensions"].get("native_backend"))
        except (ValueError, OSError, TypeError) as exc:
            raise error("E_CONFLICT", "External native backend drift", reason=str(exc)) from exc
        replacements = (
            {row["path"]: row["intended"] for row in plan["extensions"]["files"]} if after else {}
        )
        if engineering_inputs.Inputs.environment() != plan["extensions"]["environment"]:
            raise error("E_CONFLICT", "Engineering dependency environment changed")
        for old in plan["extensions"]["inputs"]:
            found, _ = _read(old["path"], capture_identity="identity" in old)
            expected = dict(old)
            if old["path"] in replacements:
                expected.pop("identity", None)
                found.pop("identity", None)
                expected.update(present=True, sha256=replacements[old["path"]])
            if expected != found:
                raise error("E_CONFLICT", "Engineering input drift", path=old["path"])

    def validate_plan(self, record):
        plan = record["plan"]
        self._inputs(plan)
        for row in plan["extensions"]["files"]:
            name = Path(row["candidate"])
            regular(name)
            if (
                not name.resolve().is_relative_to((self.state_dir / "engineering-trials").resolve())
                or digest(name) != row["intended"]
            ):
                raise error("E_CONFLICT", "Prepared document candidate changed")

    def _observe(self, plan):
        rows = consumer.observe(plan["extensions"]["files"])
        return readback(
            primary(plan), rows, [{"path": r["path"], "sha256": r["sha256"]} for r in rows]
        )

    def _verification(self, plan, observed):
        field = "/saved_documents"
        return build_verification(
            {
                "profile": PROFILE,
                "target": plan["target"],
                "snapshot": observed["snapshot"],
                "requirements": [
                    {
                        "id": "prepared-document-readback",
                        "kind": "field",
                        "field_path": field,
                        "expected": {"value": plan["extensions"]["expected"], "unit": None},
                        "comparison": {"operator": "equals"},
                        "allowed_sources": ["file_consumer"],
                        "independence": {
                            "required": False,
                            "writer_sources": ["native domain preparation", "fileformat"],
                            "expectation_sources": ["captured task and prepared candidate"],
                        },
                    }
                ],
                "observations": [
                    {
                        "requirement_id": "prepared-document-readback",
                        "observed": {"value": observed["objects"], "unit": None},
                        "source": {
                            "kind": "file_consumer",
                            "completion": "completed",
                            "target": plan["target"],
                            "snapshot": observed["snapshot"],
                            "method": "Fresh lexical/Decimal/JSON and custom schematic readback",
                            "defect_sources": [
                                "engineering_readback",
                                "sync_readback",
                                "fileformat.netlist",
                            ],
                            "evidence_ref": "#/extensions/readback",
                            "independence_basis": (
                                "Board parser is independent of writer; schematic connectivity and "
                                "candidate expectations share models. Separate native intent "
                                "acceptance required"
                            ),
                            "coverage": {"checked_paths": [field], "not_checked": []},
                        },
                    }
                ],
                "not_checked": UNCHECKED,
            },
            scope="prepared saved engineering documents",
            validator=self.validator,
        )

    def _receipt(
        self,
        call,
        plan,
        observed,
        verification,
        *,
        execution,
        change,
        persistence,
        failed_stage=None,
    ):
        artifact_only = plan["effects"] == ["artifact"]
        artifacts = [
            {
                "id": Path(row["path"]).name,
                "path": row["path"],
                "sha256": row["sha256"],
                "source_snapshot_id": plan["base_snapshot"]["id"],
            }
            for row in (observed or {}).get("objects", [])
            if artifact_only and row["sha256"] != "absent"
        ]
        return {
            "operation_id": call.operation_id,
            "capability_id": call.capability_id,
            "plan_id": call.plan_id,
            "target": plan["target"],
            "authorization_scope_id": plan["authorization_scope_id"],
            "effects": plan["effects"],
            "snapshot_before": plan["base_snapshot"],
            "snapshot_after": observed["snapshot"] if observed else None,
            "failed_stage": failed_stage,
            "outcome": {
                "execution": execution,
                "change": "not_applied" if artifact_only else change,
                "persistence": "not_applicable" if artifact_only else persistence,
                "delivery": "not_applicable",
                "verification": verification,
            },
            "changed_items": [
                {"id": row["id"], "change": row["summary"], "evidence_ref": "#/extensions/readback"}
                for row in plan["changes"]
            ]
            if change == "applied" and not artifact_only
            else [],
            "artifacts": artifacts,
            "deliveries": [],
            "recovery": {
                "supported": True,
                "action": "none" if execution == "completed" else "inspect",
                "reason": "Read every saved file before resolution; verify actual restoration",
            },
            "extensions": {
                "readback": observed,
                "expected": plan["extensions"]["expected"],
                "task": plan["extensions"]["trial"]["result"],
                "finding_comparison": plan["extensions"]["trial"]["finding_comparison"],
                "coverage": {
                    "checked": [
                        "bound saved document bytes and structured fields",
                        "prepared task protection checks",
                    ],
                    "not_checked": [row["reason"] for row in UNCHECKED],
                },
            },
            "_untrusted": [
                "changed_items",
                "extensions.readback",
                "extensions.expected",
                "extensions.task",
            ],
        }

    def apply(self, call, plan):
        from .candidate_output import OutputError
        from .hierarchy_publication import reserve

        self.require_current_scope(call, plan)
        self.validate_plan({"plan": plan})
        try:
            with reserve(plan) as parents:
                return self._apply_documents(call, plan, parents)
        except OutputError as exc:
            raise error(
                "E_CONFIG",
                "New hierarchy sources require the supported local Windows NTFS backend",
                reason=str(exc),
                reason_code=exc.reason,
            ) from exc

    def _apply_documents(self, call, plan, parents):
        self.require_current_scope(call, plan)
        self.validate_plan({"plan": plan})
        rows = plan["extensions"]["files"]
        self._guard_documents([str(primary(plan)), *[r["path"] for r in rows]])
        transaction = DocumentTransaction(
            primary(plan),
            call.operation_id,
            rows,
            state_dir=self.state_dir,
            key=self.secret_provider(),
        )
        try:
            transaction.arm()
        except (OSError, ValueError) as exc:
            raise error(
                "E_CONFLICT" if isinstance(exc, (FileExistsError, ValueError)) else "E_IO",
                "Document transaction could not start",
                reason=str(exc),
            ) from exc
        observed = None
        stage = "persist"
        saved_count = 0
        cleanup_started = False
        try:
            self.require_current_scope(call, plan)
            self.validate_plan({"plan": plan})
            for row in rows:
                self.require_current_scope(call, plan)
                path = Path(row["path"])
                if revision(path) != row["baseline"]:
                    raise OSError("Saved document drift during commit")
                if plan["extensions"].get("hierarchy_incremental") and row["baseline"] == "absent":
                    from .hierarchy_publication import publish_new

                    publish_new(row, call.operation_id, parents)
                    saved_count += 1
                    continue
                pending = path.with_name(path.name + "." + call.operation_id + ".pending")
                write_new(pending, Path(row["candidate"]).read_bytes())
                if digest(pending) != row["intended"]:
                    raise OSError("Pending candidate bytes differ")
                os.replace(pending, path)
                saved_count += 1
            stage = "verify"
            self._inputs(plan, after=True)
            observed = self._observe(plan)
            verified = self._verification(plan, observed)
            if verified["status"] != "passed":
                raise ValueError("Saved document set does not match prepared structured readback")
            self.require_current_scope(call, plan)
            self._inputs(plan, after=True)
            cleanup_started = True
            transaction.cleanup()
            return self._receipt(
                call,
                plan,
                observed,
                verified,
                execution="completed",
                change="applied" if plan["changes"] else "not_applied",
                persistence="saved",
            )
        except Exception as exc:
            restored, failure = False, None
            try:
                if plan["extensions"].get("hierarchy_incremental"):
                    raise OSError(
                        "Incremental hierarchy save preserves actual files and pending sources; "
                        "query this original operation before further writes"
                    )
                if cleanup_started:
                    raise OSError(
                        "Saved documents are preserved while archive/cleanup recovery is pending"
                    )
                self.require_current_scope(call, plan)
                transaction.rollback()
                self._inputs(plan)
                observed = self._observe(plan)
                restored = True
            except Exception as refusal:
                failure = str(refusal)
                try:
                    observed = self._observe(plan)
                except (ValueError, OSError):
                    observed = None
            verification = {
                "status": "incomplete",
                "coverage": "partial" if restored else "none",
                "scope": "failed engineering commit",
                "reason": str(exc),
                "snapshot_id": observed["snapshot"]["id"] if observed else None,
                "checks": [],
                "evidence_refs": [],
                "not_checked": [
                    {
                        "id": "requested-task",
                        "required": True,
                        "reason": "Requested task was not established after the failed commit",
                    }
                ],
            }
            change = (
                "rolled_back"
                if restored and saved_count
                else "not_applied"
                if restored
                else "unknown"
            )
            persistence = "unchanged" if restored else "unknown"
            if not restored and observed:
                hashes = {r["path"]: r["sha256"] for r in observed["objects"]}
                baseline = {r["path"]: r["baseline"] for r in rows}
                intended = {r["path"]: r["intended"] for r in rows}
                change = (
                    "not_applied"
                    if hashes == baseline
                    else "applied"
                    if hashes == intended
                    else "partial"
                    if all(hashes[p] in {baseline[p], intended[p]} for p in hashes)
                    else "unknown"
                )
                persistence = (
                    "unchanged"
                    if hashes == baseline
                    else "saved"
                    if hashes == intended
                    else "partial"
                    if all(hashes[p] in {baseline[p], intended[p]} for p in hashes)
                    else "unknown"
                )
            receipt = self._receipt(
                call,
                plan,
                observed,
                verification,
                execution="failed" if restored else "unknown",
                change=change,
                persistence=persistence,
                failed_stage=stage,
            )
            receipt["extensions"].update(restore_observed=restored, restore_error=failure)
            if plan["extensions"].get("hierarchy_incremental"):
                states = []
                for row in rows:
                    try:
                        actual = revision(row["path"])
                        states.append({"path": row["path"], "sha256": actual})
                    except OSError as observation_error:
                        states.append(
                            {"path": row["path"], "sha256": None, "error": str(observation_error)}
                        )
                receipt["extensions"]["save_order"] = [row["path"] for row in rows]
                receipt["extensions"]["document_states"] = states
                receipt["extensions"]["saved_documents"] = [
                    row["path"]
                    for row, state in zip(rows, states, strict=True)
                    if state["sha256"] == row["intended"]
                ]
                receipt["extensions"]["pending_documents"] = [
                    str(pending)
                    for row in rows
                    if (
                        pending := Path(row["path"]).with_name(
                            Path(row["path"]).name + "." + call.operation_id + ".pending"
                        )
                    ).exists()
                ]
            raise DriverError(
                "E_IO" if restored else "E_UNKNOWN",
                "Engineering save failed; inspect the actual document set",
                details={"reason": str(exc), "restore_error": failure},
                receipt=receipt,
                dispatch_state="dispatched",
            ) from exc

    def recovery(self, record):
        plan = record["plan"]
        observed = self._observe(plan)
        hashes = {r["path"]: r["sha256"] for r in observed["objects"]}
        baseline = {r["path"]: r["baseline"] for r in plan["extensions"]["files"]}
        intended = {r["path"]: r["intended"] for r in plan["extensions"]["files"]}
        verified = self._verification(plan, observed)
        try:
            owned(
                primary(plan),
                record["operation_id"],
                plan["extensions"]["files"],
                state_dir=self.state_dir,
                key=self.secret_provider(),
                required=plan["extensions"].get("cleanup_protocol") == "document-set.v2",
            )
            cleanup = {"status": "attributable"}
        except (OSError, ValueError, KeyError) as exc:
            cleanup = {"status": "refused", "reason": str(exc)}
        return {
            "operation_id": record["operation_id"],
            "target": plan["target"],
            "outcome": "not_applied"
            if hashes == baseline
            else "applied"
            if hashes == intended and verified["status"] == "passed"
            else "partial"
            if all(hashes[p] in {baseline[p], intended[p]} for p in hashes)
            else "unknown",
            "snapshot": observed["snapshot"],
            "readback": observed,
            "verification": verified,
            "cleanup": cleanup,
            "limits": "Current saved files; no inferred historical rollback or replay",
        }

    def resolve_recovery(self, record, current, state_dir):
        observed = {r["path"]: r["sha256"] for r in current["readback"]["objects"]}
        resolve(
            primary(record["plan"]),
            record["operation_id"],
            state_dir / "recovery" / record["operation_id"],
            observed,
            record["plan"]["extensions"]["files"],
            state_dir=state_dir,
            key=self.secret_provider(),
            required=record["plan"]["extensions"].get("cleanup_protocol") == "document-set.v2",
        )
