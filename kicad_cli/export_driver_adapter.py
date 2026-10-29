"""Scoped SDK exports with immutable input copies and exclusive output publication.

Existing payload exporters are retained. Receipts come from actual format consumers,
not from a legacy command's envelope. Failed or interrupted staging is retained.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import export_native, export_readback, read_inputs
from ._driver_sdk import tokens
from ._driver_sdk.models import DriverError
from .drc_baseline import _source
from .fileformat.board import Board
from .fileformat.sexpr import Document
from .move_driver_adapter import MoveAdapter, file_path, target_of
from .move_transaction import digest, process_alive, regular, write_new
from .sync_inputs import InputError

COMMANDS = {"fab gerber", "fab drill", "board plot"}
DEFAULT_LAYERS = "F.Cu,B.Cu,F.Paste,B.Paste,F.SilkS,B.SilkS,F.Mask,B.Mask,Edge.Cuts"
OWNER = "kicad-cli.export.v1"
SCOPE = "Exclusive output inventory, bounded format consumption and captured input preservation."


def stamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def absent(path):
    if os.path.lexists(path):
        raise DriverError(
            "E_CONFLICT",
            "Export destinations and operation sidecars must be new.",
            details={
                "conflict": "state_drift",
                "path": str(path),
                "next_action": "Keep existing content; choose a new directory and preview again.",
            },
            dispatch_state="not_started",
        )


def location(path):
    path = Path(os.path.abspath(Path(path).expanduser()))
    if str(path).startswith(("\\\\", "//")):
        raise ValueError("network exports are not supported")
    for parent in [path, *path.parents]:
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("export paths cannot contain links or reparse points")
    return path.resolve()


def update_journal(path, value):
    # A single operation owns these records; never replace a foreign target.
    regular(path)
    temporary = path.with_name(path.name + ".next")
    write_new(temporary, tokens.canonical(value))
    os.replace(temporary, path)


def checkpoint(stage, plan):
    """Named injection seam for owned acceptance harnesses; no public bypass flag."""


class ExportAdapter(MoveAdapter):
    def _capture(self, path):
        inputs = read_inputs.capture(board=path)
        for row in inputs.manifest:
            if row["present"]:
                regular(Path(row["path"]))
        project = inputs.raw(path.with_suffix(".kicad_pro"))
        settings = json.loads(project) if project else {}
        pcb_settings = settings.get("pcbnew")
        if pcb_settings is None:
            pcb_settings = {}
        if not isinstance(pcb_settings, dict):
            raise InputError("Present PCB project settings must be an object.")
        if pcb_settings.get("page_layout_descr_file"):
            raise InputError("External drawing sheets are outside this bounded export adapter.")
        board = Board(Document.parse(inputs.raw(path).decode("utf-8")), path)
        if not board.layers:
            raise InputError("Export requires a board with declared layers.")

        def rendered_variables(node):
            if node.head in {"property", "fp_text", "gr_text", "gr_text_box"}:
                value = node.value(2 if node.head in {"property", "fp_text"} else 1) or ""
                yield from re.findall(r"\$\{([^}]+)\}", value)
            for nested in node.lists():
                yield from rendered_variables(nested)

        variables = settings.get("text_variables") or {}
        pending, visited = set(rendered_variables(board.root)), set()
        while pending:
            name = pending.pop()
            if name in visited:
                continue
            visited.add(name)
            if name in {"KIPRJMOD", "FILEPATH", "PROJECT_PATH"}:
                raise InputError(
                    "Path-dependent rendered text cannot be preserved in an input copy."
                )
            value = variables.get(name)
            if isinstance(value, str):
                pending.update(re.findall(r"\$\{([^}]+)\}", value))
        return inputs, board

    def validate_plan(self, record):
        plan = record["plan"]
        try:
            inputs, _ = self._capture(file_path(plan["target"]))
        except (OSError, ValueError) as exc:
            raise DriverError(
                "E_CONFLICT",
                "Export inputs became unavailable or invalid after preview.",
                details={"conflict": "state_drift", "reason": str(exc)},
                dispatch_state="not_started",
            ) from exc
        if (
            inputs.manifest != plan["extensions"]["inputs"]
            or export_native.probe() != plan["extensions"]["native"]
        ):
            raise DriverError(
                "E_CONFLICT",
                "Export inputs or loaded SDK changed after preview.",
                details={"conflict": "state_drift"},
                dispatch_state="not_started",
            )
        for key in ("output", "stage", "lock"):
            path = Path(plan["extensions"][key])
            if location(path) != path:
                raise DriverError(
                    "E_CONFLICT",
                    "Output path resolution changed.",
                    details={"conflict": "state_drift"},
                    dispatch_state="not_started",
                )
            absent(path)

    def plan(self, call):
        from .driver_runtime import _trace

        _trace(call.command)
        try:
            path = location(call.params["board"]).resolve(strict=True)
            inputs, board = self._capture(path)
            fmt = (
                "excellon"
                if call.command == "fab drill"
                else "gerber"
                if call.command == "fab gerber"
                else call.params["file-format"]
            )
            fallback = (
                path.parent / "fab" / ("drill" if fmt == "excellon" else "gerber")
                if call.command != "board plot"
                else path.parent / "plots" / fmt
            )
            output = location(call.params.get("out") or fallback)
            stage = output.with_name(output.name + ".kicad-stage-" + call.operation_id)
            lock = output.with_name(output.name + ".kicad-export-lock")
            target = target_of(path)
            outputs = [
                {"path": str(p), "effect": "artifact", "exists": False}
                for p in (output, stage, lock)
            ]
            call.extras["scope"].require(effects=call.effects, target=target, outputs=outputs)
            for name in (output, stage, lock):
                absent(name)
            layers = []
            if fmt != "excellon":
                names = call.params.get("layers") or DEFAULT_LAYERS.split(",")
                if isinstance(names, str):
                    names = names.split(",")
                aliases = {layer.name: layer.name for layer in board.layers}
                aliases.update(
                    {layer.user_name: layer.name for layer in board.layers if layer.user_name}
                )
                for name in names:
                    if name not in aliases or aliases[name] in layers:
                        raise ValueError("Unknown or repeated plot layer: " + name)
                    layers.append(aliases[name])
            native = export_native.probe()
            consumer = _source()
            inputs.validate()
        except FileNotFoundError as exc:
            raise DriverError(
                "E_NOT_FOUND", "The explicit saved board is missing.", dispatch_state="not_started"
            ) from exc
        except (ValueError, UnicodeError) as exc:
            raise DriverError(
                "E_VALIDATION",
                "Export inputs or output paths are invalid.",
                details={
                    "problem": "invalid_value",
                    "parameter": "--board/--out/--layers",
                    "reason": str(exc),
                },
                dispatch_state="not_started",
            ) from exc
        bundle = tokens.digest({"inputs": inputs.manifest, "native": native})
        return {
            "target": target,
            "base_snapshot": {
                "id": "export-source-" + bundle,
                "source": "disk",
                "revision": digest(path),
                "observed_at": stamp(),
                "dependencies": [
                    {"identity": Path(row["path"]).as_uri(), "revision": row["sha256"] or "absent"}
                    for row in inputs.manifest
                ],
            },
            "changes": [
                {
                    "action": "export",
                    "resource": "artifact-directory",
                    "id": output.as_uri(),
                    "summary": "Generate "
                    + fmt
                    + " from one captured saved source; preserve existing outputs.",
                }
            ],
            "outputs": outputs,
            "constraints": [
                "New destination only; no existing directory or file is overwritten.",
                "SDK receives captured input copies and operation-owned staging only.",
                "Publish after format consumption, input/SDK checks and renewed scope checks.",
            ],
            "unknowns": [
                "No full artwork, electrical, manufacturing or supplier approval.",
                "GUI memory, external drawing sheets and custom font provenance are not covered.",
                "Publication creates a new directory and exclusive files; loss can be partial.",
            ],
            "extensions": {
                "inputs": inputs.manifest,
                "native": native,
                "format": fmt,
                "layers": layers,
                "params": call.params,
                "output": str(output),
                "stage": str(stage),
                "lock": str(lock),
                "source_bundle": bundle,
                "consumer_source": {
                    "kind": "file_consumer",
                    "origin": "kicad_cli.export_readback",
                    "version": "1",
                    "code_sha256": consumer["code_sha256"],
                    "code_scope": consumer["code_scope"],
                    "limits": consumer["source_fingerprint_basis"],
                },
            },
            "_untrusted": ["changes", "extensions.inputs", "extensions.params"],
        }

    def _consume(self, plan, directory, report=None):
        observed = export_readback.inventory(directory)
        fmt = plan["extensions"]["format"]
        if fmt == "excellon":
            drill = [item for item in observed if item["format"] == "excellon"]
            reports = [item for item in observed if item["format"] == "drill-report"]
            if not drill or len(reports) != 1:
                raise ValueError("Missing drill files or report")
            units = "inch" if plan["extensions"]["params"].get("inch") else "mm"
            if any(item["units"] != units for item in drill):
                raise ValueError("Drill units differ from the requested profile")
            count = sum(len(item["holes"]) + len(item["slots"]) for item in drill)
            if sum(reports[0]["totals"].values()) != count:
                raise ValueError("Drill coordinate/slot count differs from the native report")
            map_fmt = plan["extensions"]["params"].get("map", "pdf")
            maps = [item for item in observed if item["format"] not in {"excellon", "drill-report"}]
            if (map_fmt == "none" and maps) or (
                map_fmt != "none" and (not maps or any(item["format"] != map_fmt for item in maps))
            ):
                raise ValueError("Requested drill map format is missing or mismatched")
        elif len(observed) != len(plan["extensions"]["layers"]) or any(
            item["format"] != fmt for item in observed
        ):
            raise ValueError("Export format/layer file inventory is incomplete")
        if report is not None:
            expected_identity = {
                k: v for k, v in plan["extensions"]["native"].items() if k != "payloads"
            }
            if report.get("loaded_identity") != expected_identity:
                raise ValueError("Exporter loaded another SDK identity")
            declared = [Path(item["file"]) for item in report.get("files", [])]
            if any(p.parent.resolve() != directory.resolve() for p in declared) or sorted(
                p.name for p in declared
            ) != sorted(item["name"] for item in observed):
                raise ValueError("SDK report does not match the independently read directory")
        return observed

    def _verification(self, plan, observed=None, reason=None):
        complete = observed is not None and reason is None
        return {
            "status": "passed" if complete else "incomplete",
            "scope": SCOPE,
            "snapshot_id": plan["base_snapshot"]["id"],
            "coverage": "complete" if complete else "none",
            "checks": [
                {
                    "id": "export-file-consumer",
                    "required": True,
                    "status": "passed",
                    "evidence_ref": "#/extensions/readback",
                }
            ]
            if complete
            else [],
            "not_checked": [
                {
                    "id": "full-artwork-and-engineering",
                    "required": False,
                    "reason": "File framing and inventory do not certify full geometry, "
                    "circuit intent or manufacturing.",
                }
            ]
            + (
                []
                if complete
                else [
                    {
                        "id": "export-file-consumer",
                        "required": True,
                        "reason": reason or "Export did not complete.",
                    }
                ]
            ),
            "evidence_refs": ["#/extensions/readback"] if complete else [],
            **({} if complete else {"reason": reason or "Export did not complete."}),
        }

    def _artifacts(self, plan, directory):
        # Even invalid/partial artifacts are retained and reported as effects.
        result = []
        location(directory)
        if directory.exists():
            paths = sorted(directory.iterdir())
            if len(paths) > 128 or sum(p.stat().st_size for p in paths) > export_readback.MAX_TOTAL:
                raise ValueError("artifact inventory bounds exceeded")
            for path in paths:
                regular(path)
                if path.stat().st_size > export_readback.MAX_BYTES:
                    raise ValueError("artifact size bound exceeded")
                result.append(
                    {
                        "id": path.name,
                        "path": str(path),
                        "sha256": digest(path),
                        "source_snapshot_id": plan["base_snapshot"]["id"],
                    }
                )
        return result

    def apply(self, call, plan):
        self.require_current_scope(call, plan)
        self.validate_plan({"plan": plan})
        source = file_path(plan["target"])
        inputs, _ = self._capture(source)
        ext = plan["extensions"]
        output, stage, lock = (Path(ext[k]) for k in ("output", "stage", "lock"))
        journal = {
            "owner": OWNER,
            "operation_id": call.operation_id,
            "board": str(source),
            "output": str(output),
            "stage": str(stage),
            "pid": os.getpid(),
            "native_pid": None,
            "native_stopped": False,
            "phase": "prepared",
            "source_bundle": ext["source_bundle"],
        }
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            write_new(lock, tokens.canonical(journal))
        except OSError as exc:
            raise DriverError(
                "E_CONFLICT" if isinstance(exc, FileExistsError) else "E_IO",
                "Export output lock could not be acquired.",
                details={"conflict": "lock_held", "reason": str(exc)},
                dispatch_state="not_started",
            ) from exc
        report, observed, failure, published = None, None, None, False
        failed_stage = "apply"
        try:
            stage.mkdir()
            captured, files = stage / "input", stage / "files"
            captured.mkdir()
            files.mkdir()
            for row in inputs.manifest:
                if row["present"]:
                    write_new(captured / Path(row["path"]).name, inputs.raw(row["path"]))
            checkpoint("before_native", plan)
            inputs.validate()
            journal["phase"] = "spawning"
            update_journal(lock, journal)

            def child(pid):
                journal.update(native_pid=pid, phase="generating")
                update_journal(lock, journal)

            argv = ["--board", str(captured / source.name), "--out", str(files)]
            if ext["format"] == "excellon":
                argv += ["--map", ext["params"].get("map", "pdf")]
                argv += [
                    "--" + name.replace("-", "_")
                    for name in ("merge", "inch", "aux-origin")
                    if ext["params"].get(name)
                ]
                report = export_native.run(ext["native"], "drill", argv, child)
            else:
                argv += ["--fmt", ext["format"], "--layers", ",".join(ext["layers"])]
                report = export_native.run(ext["native"], "plot", argv, child)
            journal.update(native_stopped=True, phase="generated")
            update_journal(lock, journal)
            write_new(stage / "native-report.json", tokens.canonical(report))
            if not report.get("ok"):
                raise ValueError("SDK export failed: " + str(report.get("error")))
            failed_stage = "verify"
            observed = self._consume(plan, files, report.get("data") or {})
            # Captured copies and source are both rechecked; native loading must not
            # mutate source settings or substitute a live file during generation.
            for row in inputs.manifest:
                if row["present"] and digest(captured / Path(row["path"]).name) != row["sha256"]:
                    raise ValueError("SDK changed a captured input")
            inputs.validate()
            if export_native.probe() != ext["native"]:
                raise ValueError("SDK dependency drift during generation")
            manifest = {
                "operation_id": call.operation_id,
                "source_snapshot": plan["base_snapshot"],
                "inputs": ext["inputs"],
                "profile": {
                    "format": ext["format"],
                    "layers": ext["layers"],
                    "params": ext["params"],
                },
                "native": ext["native"],
                "observed": observed,
            }
            write_new(stage / "manifest.json", tokens.canonical(manifest))
            checkpoint("before_publish", plan)
            failed_stage = "apply"
            self.require_current_scope(call, plan)
            inputs.validate()
            absent(output)
            journal["phase"] = "publishing"
            update_journal(lock, journal)
            # mkdir and xb never replace even an empty existing destination.
            # Publication is explicitly multi-file, with observable partial states.
            output.mkdir()
            output_identity = (output.lstat().st_dev, output.lstat().st_ino)
            published = True
            for item in observed:
                self.require_current_scope(call, plan)
                location(output)
                if (output.lstat().st_dev, output.lstat().st_ino) != output_identity:
                    raise ValueError("New output directory was replaced during publication")
                write_new(output / item["name"], (files / item["name"]).read_bytes())
                checkpoint("published_file:" + item["name"], plan)
            checkpoint("after_publish", plan)
            failed_stage = "verify"
            actual = self._consume(plan, output)
            if actual != observed:
                raise ValueError("Published files differ from the consumed staging snapshot")
            inputs.validate()
            journal["phase"] = "completed"
            update_journal(lock, journal)
            write_new(stage / "terminal-journal.json", tokens.canonical(journal))
            lock.unlink()
            checkpoint("after_cleanup", plan)
        except (
            DriverError,
            OSError,
            ValueError,
            KeyboardInterrupt,
            subprocess.SubprocessError,
        ) as exc:
            failure = str(exc) or type(exc).__name__
        directory = output if published else stage / "files"
        try:
            if published and not output.is_dir():
                raise ValueError("Published output directory disappeared")
            artifacts = self._artifacts(plan, directory)
        except (OSError, ValueError) as exc:
            raise DriverError(
                "E_OUTCOME_UNKNOWN",
                "Generated artifacts cannot be completely observed; retain the output lock.",
                details={"reason": str(exc)},
                dispatch_state="dispatched",
            ) from exc
        verification = self._verification(plan, observed if not failure else None, failure)
        after_snapshot, current_inputs = None, None
        try:
            actual_inputs, _ = self._capture(source)
            current_inputs = actual_inputs.manifest
            after_snapshot = {
                "id": "export-source-"
                + tokens.digest({"inputs": current_inputs, "native": ext["native"]}),
                "source": "disk",
                "revision": digest(source),
                "observed_at": stamp(),
                "dependencies": [
                    {"identity": Path(row["path"]).as_uri(), "revision": row["sha256"] or "absent"}
                    for row in current_inputs
                ],
            }
        except (OSError, ValueError):
            pass  # No saved-state echo can substitute for an unavailable observation.
        if (
            failure
            and not artifacts
            and (journal["phase"] == "prepared" or journal["native_stopped"])
        ):
            # No requested files were created and no native process can still write.
            # Keep input copies/diagnostics but do not strand a known failed lock.
            regular(lock)
            if json.loads(lock.read_bytes()) != journal:
                raise DriverError(
                    "E_OUTCOME_UNKNOWN",
                    "Export lock changed during cleanup.",
                    dispatch_state="dispatched",
                )
            journal["phase"] = "failed-no-artifacts"
            write_new(stage / "terminal-journal.json", tokens.canonical(journal))
            lock.unlink()
        receipt = {
            "snapshot_after": after_snapshot,
            "outcome": {
                "execution": "failed" if failure else "completed",
                "change": "not_applied",
                "persistence": "not_applicable",
                "delivery": "not_applicable",
                "verification": verification,
            },
            "failed_stage": failed_stage if failure else None,
            "artifacts": artifacts,
            "recovery": {
                "action": "inspect" if failure else "none",
                "reason": "Keep staging and query operation show before another export."
                if failure
                else "Source is preserved; outputs are newly generated local artifacts.",
                "supported": True,
            },
            "extensions": {
                "readback": observed,
                "readback_source": {
                    **ext["consumer_source"],
                    "completed": not bool(failure),
                    "completed_at": stamp(),
                },
                "expected": {
                    "format": ext["format"],
                    "layers": ext["layers"],
                    "drill_units": ("inch" if ext["params"].get("inch") else "mm")
                    if ext["format"] == "excellon"
                    else None,
                    "source_inputs": ext["inputs"],
                    "existing_outputs_overwritten": False,
                },
                "native_report": report,
                "inputs": ext["inputs"],
                "observed_source_inputs": current_inputs,
                "output_dir": str(output),
                "staging_dir": str(stage),
                "published": published,
                "failure": failure,
                "source_bundle": ext["source_bundle"],
                "source_preservation": "checked" if not failure else "incomplete",
                "coverage": "bounded file framing/inventory; geometry acceptance is separate",
            },
            "_untrusted": [
                "artifacts",
                "extensions.readback",
                "extensions.native_report",
                "extensions.inputs",
            ],
        }
        if failure:
            raise DriverError(
                "E_NATIVE_FAILURE",
                "Export did not complete; generated files and recovery records are retained.",
                receipt=receipt,
                dispatch_state="dispatched",
            )
        return receipt

    def _journal(self, record):
        plan = record["plan"]
        lock = Path(plan["extensions"]["lock"])
        try:
            regular(lock)
        except FileNotFoundError:
            lock = Path(plan["extensions"]["stage"]) / "terminal-journal.json"
            regular(lock)
        if lock.stat().st_size > 64 * 1024:
            raise ValueError("oversized export journal")
        journal = json.loads(lock.read_bytes())
        ext = plan["extensions"]
        if any(
            journal.get(k) != v
            for k, v in {
                "owner": OWNER,
                "operation_id": record["operation_id"],
                "board": str(file_path(plan["target"])),
                "output": ext["output"],
                "stage": ext["stage"],
                "source_bundle": ext["source_bundle"],
            }.items()
        ):
            raise ValueError("unattributed export journal")
        if journal["phase"] == "spawning" and journal["native_pid"] is None:
            raise ValueError(
                "Child-start window has no confirmed native identity; inspect manually."
            )
        if journal["native_pid"] is not None and process_alive(journal["native_pid"]):
            raise ValueError("Native export process is still alive or its termination is unknown.")
        return lock, journal

    def recovery(self, record):
        plan, ext = record["plan"], record["plan"]["extensions"]
        lock, journal = self._journal(record)
        directory = (
            Path(ext["output"]) if Path(ext["output"]).exists() else Path(ext["stage"]) / "files"
        )
        artifacts = self._artifacts(plan, directory)
        try:
            observed = self._consume(plan, directory)
            manifest_path = Path(ext["stage"]) / "manifest.json"
            regular(manifest_path)
            manifest = json.loads(manifest_path.read_bytes())
            complete = (
                manifest.get("operation_id") == record["operation_id"]
                and manifest.get("observed") == observed
                and manifest.get("inputs") == ext["inputs"]
            )
        except (OSError, ValueError):
            observed, complete = None, False
        current, _ = self._capture(file_path(plan["target"]))
        protected = tokens.digest({"inputs": current.manifest, "journal_sha256": digest(lock)})
        outcome = (
            "not_applied"
            if not artifacts
            else "applied"
            if complete and directory == Path(ext["output"])
            else "partial"
        )
        snapshot = {
            "id": "export-observed-" + tokens.digest(artifacts),
            "source": "disk",
            "revision": tokens.digest({"artifacts": artifacts, "protected": protected}),
            "observed_at": stamp(),
        }
        return {
            "operation_id": record["operation_id"],
            "target": plan["target"],
            "outcome": outcome,
            "snapshot": snapshot,
            "readback": {"objects": artifacts, "protected_sha256": protected, "formats": observed},
            "native_process": {"pid": journal["native_pid"], "stopped": True},
            "limits": "Observe and register only; no regeneration, staged publication, "
            "overwrite, restoration or artifact deletion.",
        }

    def resolve_recovery(self, record, current, state_dir):
        lock, journal = self._journal(record)
        if self.recovery(record)["snapshot"]["revision"] != current["snapshot"]["revision"]:
            raise ValueError("Export changed during recovery registration")
        archive = (
            state_dir
            / "recovery"
            / (tokens.digest(record["operation_id"]) + "-export-journal.json")
        )
        if not archive.exists():
            write_new(archive, tokens.canonical(journal))
        if json.loads(archive.read_bytes()) != journal:
            raise ValueError("Archived export journal differs")
        if lock == Path(record["plan"]["extensions"]["lock"]):
            lock.unlink()
