"""Finding identities and provenance for the grid router's own DRC baseline.

This is a custom checker, not KiCad's official DRC. A successful comparison
only covers non-excluded errors in Report.violations; unconnected, warnings
and rules the engine does not implement remain explicitly outside its gate.
"""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .fileformat import drc
from .fileformat.drc.settings import Settings

IDENTITY_VERSION = "rule-item-position-v1"


class Incomplete(ValueError):
    """A checker invocation could not provide a comparable saved-file report."""

    def __init__(self, message, *, provenance=None):
        super().__init__(message)
        self.provenance = provenance or {}


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _inputs(path: Path, *, progress=None) -> dict:
    paths = [path, path.with_suffix(".kicad_pro"), path.with_suffix(".kicad_dru")]
    result = {}
    for source in paths:
        key = ".kicad_pcb" if source == path else source.suffix
        try:
            data = source.read_bytes()
        except FileNotFoundError:
            if source == path:
                raise
            result[key] = {"path": str(source), "present": False, "sha256": None}
            if progress is not None:
                progress["dependencies"] = [v for k, v in result.items() if k != ".kicad_pcb"]
            continue
        result[key] = {"path": str(source), "present": True, "sha256": _digest(data)}
        if progress is not None:
            progress["input"] = result.get(".kicad_pcb")
            progress["dependencies"] = [v for k, v in result.items() if k != ".kicad_pcb"]
        if source.suffix == ".kicad_pro":
            project = json.loads(data.decode("utf-8"))
            if not isinstance(project, dict):
                raise Incomplete(
                    "project rules must be a JSON object; defaults cannot hide a malformed project"
                )
    return result


def _packaged_sources(package: Path) -> tuple[dict, str]:
    archive = package / "checker-sources.zip"
    try:
        with zipfile.ZipFile(archive) as packed:
            names = packed.namelist()
            if len(names) != len(set(names)):
                raise Incomplete("Packaged checker source archive has duplicate entries")
            files = {name: _digest(packed.read(name)) for name in names if name.endswith(".py")}
            manifest = json.loads(packed.read("source-manifest.json"))
        if (
            not isinstance(manifest, dict)
            or manifest.get("format") != 1
            or manifest.get("version") != __version__
            or manifest.get("files") != files
        ):
            raise Incomplete(
                "Packaged checker source archive does not match its version/hash manifest"
            )
        return files, _digest(archive.read_bytes())
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        raise Incomplete(
            f"Packaged checker source archive is unavailable or invalid: {exc}"
        ) from exc


def _source() -> dict:
    package = Path(__file__).parent
    archived = bool(getattr(sys, "frozen", False))
    if archived:
        files, archive_sha = _packaged_sources(package)
    else:
        files = {
            p.relative_to(package).as_posix(): _digest(p.read_bytes())
            for p in sorted(package.rglob("*.py"))
            if not {"_driver_sdk", "driver_spec"}.intersection(p.relative_to(package).parts)
        }
        archive_sha = None
    required = {"drc_baseline.py", "fileformat/drc/__init__.py", "fileformat/drc/settings.py"}
    if not required.issubset(files):
        raise Incomplete(
            "Checker source fingerprint unavailable: required checker files are missing"
        )
    return {
        "kind": "custom_checker",
        "origin": "kicad_cli.fileformat.drc",
        "version": __version__,
        "identity_version": IDENTITY_VERSION,
        "code_sha256": _digest(_canonical(files)),
        "code_scope": "kicad_cli Python source in a verified archive; external pinned SDK excluded"
        if archived
        else "kicad_cli source and baseline observer on disk; SDK separately pinned",
        "source_archive_sha256": archive_sha,
        "source_fingerprint_basis": "packaging input source; "
        "build checks it did not change while packaging; "
        "not running-bytecode attestation, signature or reproducible-build proof"
        if archived
        else "observed source files on disk; not running-bytecode attestation",
        "official_native": False,
        "method": "fileformat.drc.check on the saved board with captured project/custom rules",
        "defect_sources": [
            "kicad-fileformat-parser",
            "kicad-fileformat-geometry",
            "kicad-fileformat-drc",
        ],
        "independence_basis": "Saved-file custom check shares parser/geometry with the writer; "
        "not an independent official native acceptance.",
    }


def capture(path: str | Path, *, kicad_root=None) -> tuple[int, drc.Report]:
    """Read/check saved bytes and retain actual rules, coverage and source identity.

    Present invalid/unreadable rules or input drift never silently become a
    zero-error result. Absent .kicad_pro is an explicit engine-default input.
    """
    progress = {
        "source": {
            "kind": "custom_checker",
            "origin": "kicad_cli.fileformat.drc",
            "version": __version__,
            "completion": "unknown",
            "started_at": _now(),
        },
        "input": {"path": str(path), "sha256": None, "read_status": "unavailable"},
        "dependencies": [],
        "rules": None,
        "coverage": None,
    }
    try:
        progress["source"].update(_source())
        return _capture(Path(path), kicad_root=kicad_root, progress=progress)
    except (OSError, ValueError) as exc:
        progress["source"]["finished_at"] = _now()
        raise Incomplete(str(exc), provenance=progress) from exc


def _capture(path: Path, *, kicad_root, progress) -> tuple[int, drc.Report]:
    started = _now()
    source = progress["source"]
    inputs = _inputs(path, progress=progress)
    settings = Settings.of(path.with_suffix(".kicad_pro"))
    report = drc.check(path, settings=settings, kicad_root=kicad_root)
    if inputs != _inputs(path):
        raise Incomplete("board/project/custom-rule bytes changed while the custom DRC was running")
    exclusions = [
        {"rule": rule, "uuids": sorted(uuids), "comment": comment}
        for (rule, uuids), comment in sorted(settings.exclusions.items())
    ]
    report.provenance = {
        "source": {
            **source,
            "completion": "completed",
            "started_at": started,
            "finished_at": _now(),
        },
        "input": inputs[".kicad_pcb"],
        "dependencies": [inputs[".kicad_pro"], inputs[".kicad_dru"]],
        "rules": {
            "project_policy": "project plus KiCad-10-compatible engine defaults"
            if inputs[".kicad_pro"]["present"]
            else "explicit engine defaults: no project file",
            "minimums_mm": settings.rules,
            "severities": settings.severities,
            "exclusions": exclusions,
            "net_classes": {
                "classes": settings.netclasses.classes,
                "assignments": settings.netclasses.assigned,
                "patterns": settings.netclasses.patterns,
            },
            "custom_rules": report.custom_rules,
            "custom_constraints_not_checked": settings.custom,
        },
        "coverage": {
            "gate": "non-excluded severity=error in Report.violations; "
            "unconnected findings and warnings outside gate",
            "checked_rules": sorted(
                set(drc.CHECKED) - set(report.not_checked) - set(report.ignored)
            ),
            "not_checked": report.not_checked,
            "partial": report.partial,
            "ignored": report.ignored,
            "whole_kicad_drc": False,
            "library_root": str(kicad_root) if kicad_root is not None else None,
            "library_content_fingerprint": "not_recorded; installed/project library tables "
            "and contents are not bound by this gate",
        },
    }
    count = sum(v.severity == "error" and not v.excluded for v in report.violations)
    return count, report


def _finding(violation: drc.Violation) -> tuple[dict, list[str]]:
    problems = []
    items = []
    if not violation.rule:
        problems.append("missing rule identity")
    if not violation.items:
        problems.append(
            "error has no object identity/position; message alone cannot establish finding identity"
        )
    for item in violation.items:
        if not item.uuid or item.uuid == "00000000-0000-0000-0000-000000000000":
            problems.append("error item has no stable UUID")
        if len(item.position) != 2 or any(type(n) is not int for n in item.position):
            problems.append("error item position is not two integer nanometres")
        items.append({"kind": item.kind, "uuid": item.uuid, "position_nm": list(item.position)})
    items.sort(key=lambda item: _canonical(item))
    identity = {"rule": violation.rule, "severity": violation.severity, "items": items}
    return {
        "id": "finding-" + _digest(_canonical(identity)),
        "identity": identity,
        "report": violation.to_dict(),
    }, problems


def record(report: drc.Report | None) -> dict:
    """Keep raw violations, exclusions and unconnected items alongside identities."""
    if report is None:
        return {
            "provenance": {},
            "errors": [],
            "identity_complete": False,
            "identity_issues": ["missing DRC report"],
        }
    errors, issues = [], []
    for violation in report.violations:
        if violation.severity == "error" and not violation.excluded:
            finding, problems = _finding(violation)
            errors.append(finding)
            issues.extend(problems)
    if errors and report.custom_rules.get("rules", 0):
        issues.append(
            "custom rule names/origins are not structured per finding; "
            "rule code alone cannot distinguish replacement custom-rule errors"
        )
    return {
        # Operation plans cross a JSON store boundary. Normalize tuple-based
        # pattern pairs now, so a later fresh check compares the same types.
        "provenance": json.loads(json.dumps(report.provenance)),
        "errors": sorted(errors, key=lambda finding: finding["id"]),
        "identity_complete": not issues,
        "identity_issues": sorted(set(issues)),
        "violations": [v.to_dict() for v in report.violations],
        "unconnected": [v.to_dict() for v in report.unconnected],
    }


def compare(before: drc.Report | None, after: drc.Report | None, *, enabled=True) -> dict:
    """Reject any new identity or extra instance, even if total errors decrease."""
    return compare_records(record(before), record(after), enabled=enabled)


def compare_records(baseline: dict, final: dict, *, enabled=True) -> dict:
    """Compare captured records, including a durable authenticated plan baseline."""
    result = {
        "criterion": "no_new_findings",
        "identity_version": IDENTITY_VERSION,
        "identity_fields": ["rule", "severity", "sorted item kind/UUID/position_nm"],
        "multiplicity": "multiset; duplicate findings cannot collapse into one",
        "observed_state": "Final report measures saved attempt before enclosing rollback/commit; "
        "write_state separately reports transaction recovery.",
        "baseline": baseline,
        "final": final,
        "new_findings": [],
        "removed_findings": [],
        "status": "not_run" if not enabled else "incomplete",
        "incomplete_reasons": [],
    }
    if not enabled:
        return result
    reasons = result["incomplete_reasons"]
    for label, observation in (("baseline", baseline), ("final", final)):
        if not observation["identity_complete"]:
            reasons.extend(label + ": " + issue for issue in observation["identity_issues"])
        provenance = observation["provenance"]
        source = provenance.get("source", {})
        if (
            source.get("completion") != "completed"
            or source.get("kind") != "custom_checker"
            or source.get("origin") != "kicad_cli.fileformat.drc"
            or not source.get("version")
            or source.get("identity_version") != IDENTITY_VERSION
            or not source.get("code_sha256")
            or not provenance.get("input", {}).get("sha256")
            or not provenance.get("rules")
            or not provenance.get("coverage")
        ):
            reasons.append(
                label + ": incomplete custom-checker origin/completion/version/input/rules/coverage"
            )
    old, new = baseline["provenance"], final["provenance"]
    for field in ("dependencies", "rules", "coverage"):
        if old.get(field) != new.get(field):
            reasons.append(field + " changed between checks")
    for field in ("kind", "origin", "version", "identity_version", "code_sha256"):
        if old.get("source", {}).get(field) != new.get("source", {}).get(field):
            reasons.append("checker " + field + " changed between checks")
    if reasons:
        return result
    before_count = Counter(f["id"] for f in baseline["errors"])
    after_count = Counter(f["id"] for f in final["errors"])
    new_ids, removed_ids = after_count - before_count, before_count - after_count
    result["new_findings"] = [
        {**next(f for f in final["errors"] if f["id"] == identity), "occurrences": count}
        for identity, count in sorted(new_ids.items())
    ]
    result["removed_findings"] = [
        {**next(f for f in baseline["errors"] if f["id"] == identity), "occurrences": count}
        for identity, count in sorted(removed_ids.items())
    ]
    result["status"] = "failed" if new_ids else "passed"
    return result


def require_saved_input(path: str | Path, report: drc.Report) -> None:
    """Do not commit bytes different from the saved input the checker measured."""
    inputs = _inputs(Path(path))
    if inputs[".kicad_pcb"] != report.provenance.get("input") or [
        inputs[".kicad_pro"],
        inputs[".kicad_dru"],
    ] != report.provenance.get("dependencies"):
        raise Incomplete("saved inputs changed after custom DRC readback")
