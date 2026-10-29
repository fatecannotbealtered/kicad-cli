"""Bounded official saved-board DRC before authorizing a footprint translation.

All native inputs are private relocated copies. No native command receives the
engineering board, and no report is replaced at confirmation time.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

from . import move_guard, move_native_process, move_preflight_inputs, move_readback, move_risk
from ._driver_sdk.models import DriverError
from .fileformat.board import Board
from .fileformat.sexpr import Document
from .move_transaction import regular
from .native.move import from_mm, move_footprint
from .payload import drc_runner
from .sync_inputs import Drift, InputError

SCHEMA = "kicad-cli://schemas/move-preflight/v1"
VERSION = "1"
NATIVE_VERSION = "10.0.6"
TIMEOUT = 180
DEFAULT_PREFERENCES = {
    "10.0/" + name + ".json"
    for name in (
        "3d_viewer",
        "cvpcb",
        "eeschema",
        "fpedit",
        "kicad",
        "kicad_common",
        "pcbnew",
        "symbol_editor",
    )
}
LIMITS = [
    "Official KiCad saved-board DRC using existing stored copper; no zone refill or save.",
    "Reported unconnected pairs are representative edges, not the complete electrical graph.",
    "A separate finite saved-file placement guard checks supported nontext envelopes; "
    "no arbitrary outline or exact polygon collision algorithm.",
    "No schematic parity, full artwork, supplier or manufacturing approval.",
    "Original tracks and vias do not follow a translated footprint; no automatic repair.",
]


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(raw):
    return json.loads(
        raw, object_pairs_hook=drc_runner._pairs, parse_constant=drc_runner._invalid_constant
    )


def _write(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _error(code, message, **details):
    # E_VERIFY_INCOMPLETE is reserved by the pinned SDK for a change that
    # already took effect and needs a receipt. Preflight never writes the
    # engineering target; keep the native/check failure and its raw evidence.
    if code == "E_VERIFY_INCOMPLETE":
        code = "E_NATIVE_FAILURE"
        details.setdefault("preflight_status", "incomplete")
    if code in {"E_NATIVE_FAILURE", "E_TIMEOUT"}:
        details.setdefault(
            "next_action",
            "Retain this preflight and its native evidence. Do not replay the started call; "
            "diagnose the recorded failure before planning a separate new cycle.",
        )
    if code == "E_CONFLICT":
        details.setdefault("conflict", "state_drift")
    if code == "E_VALIDATION":
        details.setdefault("problem", "invalid_value")
        details.setdefault("parameter", "--board")
    details.update(engineering_write_started=False, automatic_retry=False)
    return DriverError(code, message, details=details, dispatch_state="not_started")


def _files(root):
    """Retain membership, regular-file identities and exact bytes, including reports."""
    root = Path(root)
    regular_root = root.resolve(strict=True)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Preflight workspace is not a regular directory")
    rows, total = [], 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.resolve().is_relative_to(regular_root):
            raise ValueError("Linked preflight artifact")
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            rows.append({"relative": relative, "kind": "directory"})
        else:
            regular(path)
            raw = path.read_bytes()
            total += len(raw)
            if len(raw) > drc_runner.MAX_REPORT_BYTES or total > 1024 * 1024 * 1024:
                raise ValueError("Preflight artifact size limit exceeded")
            info = path.stat()
            rows.append(
                {
                    "relative": relative,
                    "kind": "file",
                    "sha256": _sha(raw),
                    "identity": [
                        info.st_dev,
                        info.st_ino,
                        info.st_ctime_ns,
                        info.st_mtime_ns,
                        info.st_size,
                    ],
                }
            )
        if len(rows) > 16384:
            raise ValueError("Preflight artifact membership limit exceeded")
    return rows


def _config(root):
    values = {}
    for row in _files(root):
        if row["kind"] != "file":
            continue
        relative = row["relative"]
        raw = (Path(root) / relative).read_bytes()
        if relative.endswith(".json"):
            value = _json(raw)
            if not isinstance(value, dict):
                raise ValueError("Preference JSON is not an object")
            values[relative] = value
        else:
            values[relative] = {"exact_bytes_sha256": row["sha256"]}
    return values


def _config_change(before, after, allowed_dirs, *, preparation=False):
    """Only declared defaults may appear during preparation; formal membership is exact."""
    added, removed = sorted(after.keys() - before.keys()), sorted(before.keys() - after.keys())
    if removed or (added and (not preparation or not set(added) <= DEFAULT_PREFERENCES)):
        raise ValueError("Unexpected preference file membership change")
    changes = []
    for name in sorted(before.keys() & after.keys()):
        if before[name] == after[name]:
            continue
        left, right = copy.deepcopy(before[name]), copy.deepcopy(after[name])
        if name != "10.0/kicad_common.json":
            raise ValueError("Unexpected non-working-directory preference modification: " + name)
        ls, rs = left.get("system"), right.get("system")
        if not isinstance(ls, dict) or not isinstance(rs, dict):
            raise ValueError("Invalid common preference system section")
        old, new = ls.pop("working_dir", None), rs.pop("working_dir", None)
        if left != right or not isinstance(new, str) or new not in allowed_dirs:
            raise ValueError("Unexpected common preference modification")
        changes.append(
            {"path": name, "pointer": "/system/working_dir", "before": old, "after": new}
        )
    return {"added_default_files": added, "removed_files": removed, "changes": changes}


def _project_state(project):
    return {row["relative"]: row["sha256"] for row in _files(project) if row["kind"] == "file"}


def _project_unchanged(before, project, *, preparation=False, board_name=None):
    after = _project_state(project)
    if any(after.get(name) != value for name, value in before.items()):
        raise ValueError("A native consumer changed its engineering input copy")
    additions = sorted(after.keys() - before.keys())
    allowed = Path(board_name).with_suffix(".kicad_prl").name if board_name else None
    if additions and (not preparation or additions != [allowed]):
        raise ValueError("Unexpected native project metadata additions")
    if additions and not isinstance(_json((Path(project) / allowed).read_bytes()), dict):
        raise ValueError("Invalid prepared project metadata")
    return additions


def _artifact(path):
    path = Path(path)
    regular(path)
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()

    def identity(info):
        return [info.st_dev, info.st_ino, info.st_ctime_ns, info.st_mtime_ns]

    if identity(before) != identity(after) or len(raw) != after.st_size:
        raise ValueError("A preflight artifact changed while being read")
    return {"sha256": _sha(raw), "identity": identity(after)}, raw


def _native_evidence(record, output):
    observed, raw = _artifact(output / "record.json")
    if _json(raw) != record:
        raise ValueError("Returned native record differs from its persisted record")
    for name in ("stdout", "stderr"):
        item, raw = _artifact(output / name)
        if item["sha256"] != record.get(name + "_sha256") or len(raw) != record.get(
            name + "_bytes"
        ):
            raise ValueError("Native output differs from its persisted record: " + name)
    return {"directory": str(output), "record": observed, "files": _files(output)}


def _verify_native(item):
    evidence = item["_evidence"]
    if _files(evidence["directory"]) != evidence["files"]:
        raise ValueError("Previously completed native call evidence changed")
    if "report" in item:
        actual, raw = _artifact(item["report"])
        if actual != evidence["report"] or actual["sha256"] != item["sha256"]:
            raise ValueError("Previously checked official report changed")
        value = drc_runner.validate_report(_json(raw), Path(evidence["board"]))
        if value != item["value"]:
            raise ValueError("Official report bytes differ from the returned checked value")


def _configuration(root):
    rows = _files(root)
    return {
        "files": {row["relative"]: row for row in rows if row["kind"] == "file"},
        "directories": {row["relative"] for row in rows if row["kind"] == "directory"},
        "values": _config(root),
    }


def _check_configuration(before, root, allowed, *, preparation=False):
    after = _configuration(root)
    delta = _config_change(before["values"], after["values"], allowed, preparation=preparation)
    for name in before["files"].keys() & after["files"].keys():
        if name != "10.0/kicad_common.json" and before["files"][name] != after["files"][name]:
            raise ValueError("A frozen preference file changed: " + name)
    allowed_directories = set(before["directories"])
    for name in delta["added_default_files"]:
        allowed_directories.update(p.as_posix() for p in Path(name).parents if str(p) != ".")
    if after["directories"] != allowed_directories:
        raise ValueError("Unexpected preference directory membership change")
    return after, delta


class _StagedGuard:
    """Consume the initial staged bytes; never re-freeze an observed drift as input."""

    def __init__(self, staging, workspace):
        self.staging, self.workspace = staging, workspace
        self.stages = staging["stages"]
        self.allowed = {entry["project"] for entry in self.stages.values()}
        initial = {row["relative"]: row["sha256"] for row in staging["files"]}
        if len(initial) != len(staging["files"]):
            raise ValueError("Duplicate initial staged file identity")
        current = _project_state(workspace)
        if current.pop("STAGING.json", None) is None or current != initial:
            raise ValueError("Initial staged files changed before preparation")
        if _json((workspace / "STAGING.json").read_bytes()) != staging:
            raise ValueError("The initial staging record changed")
        destinations = {}
        for row in staging["relocations"]:
            destination = Path(row["destination"])
            if not destination.is_relative_to(workspace):
                raise ValueError("A relocated input escaped the preflight workspace")
            relative = destination.relative_to(workspace).as_posix()
            if relative in destinations or initial.get(relative) != row["sha256"]:
                raise ValueError("A relocated input differs from its staged bytes")
            destinations[relative] = row["sha256"]
        if destinations != initial:
            raise ValueError("Initial staged files are not exactly the declared relocations")
        self.projects = {key: _files(value["project"]) for key, value in self.stages.items()}
        self.libraries = {
            key: _files(Path(value["project"]).parent / "libraries")
            for key, value in self.stages.items()
        }
        evidence = workspace / "evidence"
        self.tables = _files(evidence) if evidence.exists() else None
        self.configs = {
            key: _configuration(value["config_root"]) for key, value in self.stages.items()
        }
        self.records = {str(workspace / "STAGING.json"): _artifact(workspace / "STAGING.json")[0]}
        self.native = []
        self.frozen = False
        self.check(preparation=True)

    def _check_project(self, key, *, preparation):
        stage = self.stages[key]
        rows = _files(stage["project"])
        expected = self.projects[key]
        if rows == expected:
            return
        name = Path(stage["board"]).with_suffix(".kicad_prl").name
        additions = [row for row in rows if row not in expected]
        if (
            not preparation
            or key != "preparation"
            or any(row not in rows for row in expected)
            or len(additions) != 1
            or additions[0]["kind"] != "file"
            or additions[0]["relative"] != name
            or any(row["relative"] == name for row in expected)
            or not isinstance(_json((Path(stage["project"]) / name).read_bytes()), dict)
        ):
            raise ValueError("A staged engineering input or metadata changed: " + key)
        self.projects[key] = rows  # The sole declared PRL addition is now immutable.

    def check(self, *, preparation=False):
        if preparation and self.frozen:
            raise ValueError("Formal configuration cannot return to preparation")
        for key, stage in self.stages.items():
            self._check_project(key, preparation=preparation)
            if _files(Path(stage["project"]).parent / "libraries") != self.libraries[key]:
                raise ValueError("A staged footprint library changed: " + key)
            observed, _ = _check_configuration(
                self.configs[key],
                stage["config_root"],
                self.allowed,
                preparation=preparation and key == "preparation",
            )
            if preparation and key == "preparation":
                self.configs[key] = observed  # Previously introduced defaults remain bound.
        evidence = self.workspace / "evidence"
        if (_files(evidence) if evidence.exists() else None) != self.tables:
            raise ValueError("Original table evidence changed")
        for path, expected in self.records.items():
            if _artifact(path)[0] != expected:
                raise ValueError("A frozen preflight record changed")
        for item in self.native:
            _verify_native(item)
        for key, expected in (
            ("baseline", self.staging["baseline_sha256"]),
            ("preparation", self.staging["baseline_sha256"]),
            ("candidate", self.staging["candidate_sha256"]),
        ):
            if _artifact(self.stages[key]["board"])[0]["sha256"] != expected:
                raise ValueError("The exact staged board bytes changed: " + key)

    def remember(self, item):
        _verify_native(item)
        self.native.append(item)

    def freeze(self, expected_config, metadata):
        # Projects may gain only the verified preparation PRL; libraries never change.
        for key in ("baseline", "candidate"):
            stage = self.stages[key]
            expected_project = {
                row["relative"]: row["sha256"]
                for row in self.projects[key]
                if row["kind"] == "file"
            }
            expected_project.update(metadata)
            if _project_state(stage["project"]) != expected_project:
                raise ValueError("Formal project differs from its exact prepared projection")
            project_rows = _files(stage["project"])
            allowed_members = {row["relative"] for row in self.projects[key]} | metadata.keys()
            if any(row["relative"] not in allowed_members for row in project_rows):
                raise ValueError("Unexpected formal project directory membership")
            for row in self.projects[key]:
                if row not in project_rows:
                    raise ValueError("An original formal project input was replaced")
            if _project_state(stage["config_root"]) != expected_config[key]:
                raise ValueError("Formal configuration differs from its prepared projection")
            actual_config = _configuration(stage["config_root"])
            if (
                actual_config["directories"]
                != (self.configs[key]["directories"] | self.configs["preparation"]["directories"])
                or actual_config["files"]["10.0/fp-lib-table"]
                != self.configs[key]["files"]["10.0/fp-lib-table"]
            ):
                raise ValueError("The formal table identity or configuration directories changed")
            self.projects[key] = project_rows
            self.configs[key] = actual_config
        self.frozen = True
        self.check()
        value = {
            "schema_version": 1,
            "initial_staged_files": self.staging["files"],
            "initial_relocations": self.staging["relocations"],
            "projects": self.projects,
            "libraries": self.libraries,
            "configuration_files": {key: item["files"] for key, item in self.configs.items()},
            "mutable_pointer": "10.0/kicad_common.json#/system/working_dir",
            "allowed_working_directories": sorted(self.allowed),
            "formal_configuration_frozen": True,
        }
        _write(self.workspace / "FORMAL-FROZEN.json", value)
        for name in ("PREPARATION.json", "FORMAL-FROZEN.json"):
            self.records[str(self.workspace / name)] = _artifact(self.workspace / name)[0]
        self.check()


def _native(stage, workspace, official, label, *, version=False, environ=None):
    env = dict(os.environ if environ is None else environ)
    env.update(
        KICAD_CONFIG_HOME=stage["config_root"],
        KICAD_DOCUMENTS_HOME=str(Path(stage["config_root"]) / "documents"),
        KIPRJMOD=stage["project"],
    )
    output = workspace / "calls" / label
    output.parent.mkdir(parents=True, exist_ok=True)
    report = workspace / (label + ".json")
    command = (
        [official, "--version"]
        if version
        else [
            official,
            "pcb",
            "drc",
            "--format",
            "json",
            "--severity-all",
            "--units",
            "mm",
            "--all-track-errors",
            "-o",
            str(report),
            stage["board"],
        ]
    )
    record = move_native_process.run(
        command,
        cwd=Path(stage["project"]),
        env=env,
        output=output,
        timeout=30 if version else TIMEOUT,
    )
    execution = record["execution"]
    if execution != "completed_for_output_validation" or record["exit"] != 0:
        raise _error(
            "E_TIMEOUT" if execution == "timeout" else "E_NATIVE_FAILURE",
            "The official move preflight did not complete; retain this call, do not replay it.",
            phase=label,
            native_execution=execution,
            native_call_started=record.get("root_pid") is not None,
            native_exit=record.get("exit"),
            evidence_directory=str(output),
        )
    if not record.get("output_validation_permitted"):
        raise _error(
            "E_VERIFY_INCOMPLETE",
            "Native ownership or output observation is incomplete.",
            phase=label,
            evidence_directory=str(output),
            native_call_started=True,
        )
    try:
        evidence = _native_evidence(record, output)
    except (OSError, ValueError, TypeError) as exc:
        raise _error(
            "E_VERIFY_INCOMPLETE",
            "The persisted native record or output differs from the observed call.",
            phase=label,
            reason=str(exc),
            evidence_directory=str(output),
            native_call_started=True,
        ) from exc
    if version:
        value = (output / "stdout").read_text(encoding="utf-8").strip()
        if value != NATIVE_VERSION:
            raise _error(
                "E_CONFIG",
                "This bounded move preflight requires the validated KiCad version.",
                expected_version=NATIVE_VERSION,
                actual_version=value,
            )
        result = {"version": value, "record": str(output / "record.json"), "_evidence": evidence}
        _verify_native(result)
        return result
    try:
        regular(report)
        report_observation, raw = _artifact(report)
        if len(raw) > drc_runner.MAX_REPORT_BYTES:
            raise ValueError("Report exceeds the bounded size")
        value = drc_runner.validate_report(_json(raw), Path(stage["board"]))
        if value["kicad_version"] != NATIVE_VERSION:
            raise ValueError("Report version differs from the bound official version")
        evidence.update(report=report_observation, board=stage["board"])
    except (OSError, ValueError, TypeError) as exc:
        raise _error(
            "E_VERIFY_INCOMPLETE",
            "The official DRC report is missing or invalid.",
            phase=label,
            reason=str(exc),
            evidence_directory=str(output),
            native_call_started=True,
        ) from exc
    result = {
        "report": str(report),
        "sha256": _sha(raw),
        "record": str(output / "record.json"),
        "native_exit": record["exit"],
        "violation_result": any(
            value[g] for g in ("violations", "unconnected_items", "schematic_parity")
        ),
        "value": value,
        "_evidence": evidence,
    }
    _verify_native(result)
    return result


def _prepare_config(staging, workspace, official, environ, guard):
    stages = staging["stages"]
    prep = stages["preparation"]
    initial = _config(prep["config_root"])
    project_before = _project_state(prep["project"])
    guard.check(preparation=True)
    version = _native(prep, workspace, official, "prepare-version", version=True, environ=environ)
    guard.remember(version)
    guard.check(preparation=True)
    native = _native(prep, workspace, official, "prepare-drc", environ=environ)
    guard.remember(native)
    guard.check(preparation=True)
    allowed = {entry["project"] for entry in stages.values()}
    delta = _config_change(initial, _config(prep["config_root"]), allowed, preparation=True)
    added = _project_unchanged(
        project_before, prep["project"], preparation=True, board_name=Path(prep["board"]).name
    )
    # Read only after the preparation guard has validated every input. These
    # bytes, rather than the current formal files, define the allowed projection.
    prepared_bytes = {
        row["relative"]: (Path(prep["config_root"]) / row["relative"]).read_bytes()
        for row in _files(prep["config_root"])
        if row["kind"] == "file"
    }
    metadata_bytes = {name: (Path(prep["project"]) / name).read_bytes() for name in added}
    expected_config = {}
    guard.check(preparation=True)
    projections = []
    for label in ("baseline", "candidate"):
        formal = stages[label]
        destination = Path(formal["config_root"])
        expected_config[label] = {
            relative: _sha(raw)
            for relative, raw in prepared_bytes.items()
            if relative != "10.0/fp-lib-table"
        }
        expected_config[label]["10.0/fp-lib-table"] = guard.configs[label]["files"][
            "10.0/fp-lib-table"
        ]["sha256"]
        for relative in sorted(guard.configs["preparation"]["directories"]):
            (destination / relative).mkdir(parents=True, exist_ok=True)
        for relative, raw in sorted(prepared_bytes.items()):
            if relative != "10.0/fp-lib-table":
                # Only owned temporary copies are overwritten, before formal freezing.
                (destination / relative).write_bytes(raw)
                projections.append(
                    {
                        "source": str(Path(prep["config_root"]) / relative),
                        "destination": str(destination / relative),
                        "sha256": _sha(raw),
                    }
                )
        for name in added:
            target = Path(formal["project"]) / name
            with target.open("xb") as handle:
                handle.write(metadata_bytes[name])
            projections.append(
                {
                    "source": str(Path(prep["project"]) / name),
                    "destination": str(target),
                    "sha256": _sha(metadata_bytes[name]),
                }
            )
    result = {
        "version": {key: value for key, value in version.items() if key != "_evidence"},
        "initialization_report_sha256": native["sha256"],
        "configuration_delta": delta,
        "prepared_project_metadata": added,
        "formal_projections_before_freeze": projections,
        "formal_mutable_pointer": "10.0/kicad_common.json#/system/working_dir",
        "allowed_working_directories": sorted(allowed),
    }
    _write(workspace / "PREPARATION.json", result)
    guard.freeze(expected_config, {name: _sha(raw) for name, raw in metadata_bytes.items()})
    return result


def prepare(path, board, observed, requested, *, state_dir, operation_id, environ=None):
    """Return a fully bound passed/blocked result; incomplete checks raise without a token."""
    workspace = Path(state_dir).resolve() / "move-preflights" / operation_id
    if not operation_id or Path(operation_id).name != operation_id:
        raise _error("E_CONFIG", "Invalid private preflight operation identity")
    workspace.parent.mkdir(parents=True, exist_ok=True)
    try:
        binding = move_preflight_inputs.capture(path, board, workspace)
        candidate = Board(Document.parse(board.document.source), path)
        selected = {fp.reference: fp for fp in candidate.footprints}
        for ref, (x, y) in requested.items():
            move_footprint(candidate, selected[ref], from_mm(x), from_mm(y))
        candidate_raw = candidate.document.dumps().encode("utf-8")
        staged = move_preflight_inputs.stage(binding, candidate_raw)
        guard = _StagedGuard(staged, workspace)
        baseline_stage, candidate_stage = (
            staged["stages"][key] for key in ("baseline", "candidate")
        )
        before = move_readback.observe(baseline_stage["board"], sorted(requested))
        after = move_readback.observe(candidate_stage["board"], sorted(requested))
        _, failures = move_readback.compare(before, after, requested)
        if before.snapshot["revision"] != observed.snapshot["revision"] or failures:
            raise ValueError("The candidate is not exactly the requested protected translation")
        try:
            placement = move_guard.check(
                baseline_stage["board"], candidate_stage["board"], requested
            )
        except (OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
            raise _error(
                "E_VALIDATION",
                "The bounded footprint placement geometry is unsupported or incomplete.",
                reason=str(exc),
                parameter="--board",
                preflight_status="incomplete",
                native_call_started=False,
                evidence_directory=str(workspace),
            ) from exc
        placement_path = workspace / "PLACEMENT-GUARD.json"
        _write(placement_path, placement)
        guard.records[str(placement_path)] = _artifact(placement_path)[0]
        guard.check(preparation=True)
        if placement["status"] != "passed":
            raise _error(
                "E_VALIDATION",
                "The bounded placement guard refuses the complete footprint move batch.",
                parameter="--moves",
                preflight_status="blocked",
                placement_guard=placement,
                native_call_started=False,
                evidence_directory=str(workspace),
                _untrusted=["placement_guard"],
            )
        official = binding["selection"]["official_executable"]
        prepared = _prepare_config(staged, workspace, official, environ, guard)
        staged["configuration_prepared"] = True
        staged["formal_configuration_frozen"] = True
        move_preflight_inputs.validate(binding)
        formal, configuration = {}, {}
        for label in ("baseline", "candidate"):
            stage = staged["stages"][label]
            guard.check()
            move_preflight_inputs.validate(binding)
            formal[label] = _native(stage, workspace, official, label + "-drc", environ=environ)
            guard.remember(formal[label])
            guard.check()
            _, configuration[label] = _check_configuration(
                guard.configs[label], stage["config_root"], prepared["allowed_working_directories"]
            )
            move_preflight_inputs.validate(binding)
        guard.check()
        comparison = move_risk.compare_reports(
            formal["baseline"]["value"],
            formal["candidate"]["value"],
            Board.load(baseline_stage["board"]),
            Board.load(candidate_stage["board"]),
        )
        result = {
            "schema_version": 1,
            "version": VERSION,
            "status": comparison["status"],
            "source": {
                "kind": "official_native",
                "origin": "KiCad pcb drc",
                "official_native": True,
                "version": NATIVE_VERSION,
                "executable": {"path": official, "sha256": _sha(Path(official).read_bytes())},
            },
            "workspace": str(workspace),
            "input_binding": binding,
            "staging": staged,
            "formal": {
                key: {k: v for k, v in item.items() if k not in {"value", "_evidence"}}
                for key, item in formal.items()
            },
            "configuration": {"preparation": prepared, "formal_deltas": configuration},
            "comparison": comparison,
            "placement_guard": placement,
            "baseline_sha256": staged["baseline_sha256"],
            "candidate_sha256": staged["candidate_sha256"],
            "not_checked": LIMITS,
            "engineering_write_started": False,
            "_untrusted": ["comparison", "placement_guard", "staging", "input_binding"],
        }
        guard.check()
        move_preflight_inputs.validate(binding)
        _write(workspace / "RESULT.json", result)
        guard.check()
        result["artifacts"] = _files(workspace)
        guard.check()
        if _files(workspace) != result["artifacts"]:
            raise ValueError("Preflight artifacts changed while their final manifest was captured")
        return result
    except DriverError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        code = (
            "E_CONFLICT"
            if isinstance(exc, Drift)
            else "E_CONFIG"
            if isinstance(exc, InputError) and exc.details.get("dependency_unavailable")
            else "E_VALIDATION"
            if isinstance(exc, InputError)
            else "E_VERIFY_INCOMPLETE"
        )
        raise _error(
            code,
            "The bounded official move preflight could not be completed.",
            reason=str(exc),
            evidence_directory=str(workspace),
            preflight_status="incomplete",
        ) from exc


def validate(plan, *, state_dir):
    """Read-only revalidation before token dispatch and before the engineering save."""
    try:
        data = plan["extensions"]["preflight"]
        if data["schema_version"] != 1 or data["version"] != VERSION or data["status"] != "passed":
            raise ValueError("The preview has no complete, passing official preflight")
        workspace = Path(data["workspace"])
        root = (Path(state_dir) / "move-preflights").resolve(strict=True)
        if (
            not workspace.resolve(strict=True).is_relative_to(root)
            or workspace.parent.resolve() != root
        ):
            raise ValueError("Preflight workspace identity differs from this operation store")
        move_preflight_inputs.validate(data["input_binding"])
        if _files(workspace) != data["artifacts"]:
            raise ValueError("Preflight candidate, report, configuration or other artifact drifted")
        candidate = Path(data["staging"]["stages"]["candidate"]["board"])
        if _sha(candidate.read_bytes()) != data["candidate_sha256"]:
            raise ValueError("The checked candidate bytes changed")
        baseline = Path(data["staging"]["stages"]["baseline"]["board"])
        placement = move_guard.check(baseline, candidate, plan["extensions"]["moves"])
        if placement["status"] != "passed" or placement != data["placement_guard"]:
            raise ValueError("The bounded placement observation or requested geometry changed")
        if _json((workspace / "PLACEMENT-GUARD.json").read_bytes()) != placement:
            raise ValueError("The persisted bounded placement observation differs")
        return data
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise _error(
            "E_CONFLICT",
            "The original move preflight no longer authorizes this request.",
            reason=str(exc),
            preflight_status="drifted",
        ) from exc
