"""Repository FCC evidence, separate from leaf dispatch counts and declarations.

This is a local evidence format, not a replacement driver protocol. A test
binding is reviewable: neither a passing trace nor a hand-written status is
accepted as the result of a behavior case.
"""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

INVENTORY = "docs/fcc/behavior-inventory.json"
PUBLIC_FILES = [
    "README.md",
    "README_zh.md",
    "WINDOWS_TRIAL.md",
    "ADVANCED_SCHEMATIC_EDIT.md",
    "HIERARCHICAL_EDIT.md",
    "SAVED_FOOTPRINT_MOVE.md",
    "FIRST_VERSION_SCOPE.md",
    "CHANGELOG.md",
    "skills/kicad-cli/SKILL.md",
    "skills/kicad-cli/reference/common.md",
    "kicad_cli/main.py",
    "kicad_cli/driver_contract.json",
    "kicad_cli/driver_runtime.py",
    "docs/DRIVER_ENGINEERING_TASKS.md",
    "docs/COMMAND-SURFACE_zh.md",
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("Duplicate evidence key")
            value[key] = item
        return value

    return json.loads(Path(path).read_bytes(), object_pairs_hook=pairs)


def source_manifest(root):
    paths = set()
    for folder in ("kicad_cli", "bench", "scripts", "tests", "skills", "docs"):
        for path in (root / folder).rglob("*"):
            if (
                path.is_file()
                and "__pycache__" not in path.parts
                and "evidence" not in path.parts
                and path.suffix in {".py", ".json", ".md", ".js"}
            ):
                paths.add(path.relative_to(root).as_posix())
    paths.update(p for p in PUBLIC_FILES if (root / p).is_file())
    paths.update({"build.py", "package.json", "pyproject.toml", INVENTORY})
    return {name: sha(root / name) for name in sorted(paths)}


def digest_manifest(manifest):
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


def junit(path, *, binary_sha256=None):
    observations = {}
    tree = ET.parse(path)
    for case in tree.iter("testcase"):
        if binary_sha256 is not None:
            properties = {
                p.get("name"): p.get("value") for p in case.findall("./properties/property")
            }
            if properties.get("fcc_artifact_sha256") != binary_sha256:
                continue
        name = case.get("classname", "") + "." + case.get("name", "")
        status = (
            "failed"
            if case.find("failure") is not None or case.find("error") is not None
            else "not_run"
            if case.find("skipped") is not None
            else "passed"
        )
        observations.setdefault(name, []).append(status)
    return observations


def measure(inventory, runs, *, source_digest, binary_sha256=None):
    observations = {}
    for run in runs:
        tree = ET.parse(run)
        properties = {
            p.get("name"): p.get("value") for p in tree.findall(".//testsuite/properties/property")
        }
        if (
            properties.get("fcc_source_digest") != source_digest
            or properties.get("fcc_source_unchanged") != "true"
        ):
            continue
        for name, statuses in junit(run, binary_sha256=binary_sha256).items():
            observations.setdefault(name, []).extend(statuses)
    rows = []
    for behavior in inventory["behaviors"]:
        cases = []
        for selector in behavior.get("tests", []):
            # A naked function is one non-parameterized case, not an open-ended
            # group inferred from this run. Collections must enumerate their
            # complete required case IDs in the reviewed inventory.
            hits = {selector: observations[selector]} if selector in observations else {}
            states = [s for group in hits.values() for s in group]
            status = (
                "failed"
                if "failed" in states
                else ("passed" if states and set(states) == {"passed"} else "not_run")
            )
            cases.append({"selector": selector, "status": status, "observations": hits})
        status = (
            "failed"
            if any(c["status"] == "failed" for c in cases)
            else ("passed" if cases and all(c["status"] == "passed" for c in cases) else "not_run")
        )
        reason = behavior.get("gap", "Required behavior case was not executed in these runs")
        if behavior.get("not_applicable"):
            # No environment is not a basis for N/A. N/A must have an explicit
            # reviewed applicability reason, and stays outside the numerator.
            reason = behavior["not_applicable"]
            status = "not_applicable"
        rows.append(
            {
                "id": behavior["id"],
                "status": status,
                "cases": cases,
                "reason": None if status == "passed" else reason,
            }
        )
    return rows


def collect(root, runs, binary=None):
    inventory_path = root / INVENTORY
    inventory = load(inventory_path)
    manifest = source_manifest(root)
    rows = measure(
        inventory,
        runs,
        source_digest=digest_manifest(manifest),
        binary_sha256=sha(binary) if binary else None,
    )
    count = Counter(row["status"] for row in rows)
    applicable = len(rows) - count["not_applicable"]
    return {
        "format": "kicad-cli.fcc-evidence.v1",
        "term": "Functional Contract Coverage",
        "specification_commit": "9607a35fe0ea33fe822e1f2ad2704eae260d4018",
        "inventory_sha256": sha(inventory_path),
        "source_manifest": manifest,
        "source_digest": digest_manifest(manifest),
        "artifact": {"kind": "frozen", "sha256": sha(binary)} if binary else {"kind": "source"},
        "runs": [
            {"path": str(Path(p).resolve()), "sha256": sha(p), "kind": "pytest-junit"} for p in runs
        ],
        "behaviors": rows,
        "counts": {"enumerated": len(rows), "applicable": applicable, **dict(count)},
        "inventory_percent": round(count["passed"] * 100 / applicable, 2) if applicable else None,
        "fcc_status": "verified"
        if inventory.get("exhaustive") is True and applicable and count["passed"] == applicable
        else "incomplete",
        "limits": inventory["limits"],
        "engineering_acceptance_is_separate": True,
    }


def admission(root, report_path, binary=None):
    """Recompute results from intact execution evidence; fail closed on gaps."""
    if report_path is None or not Path(report_path).is_file():
        return ["bound public-behavior FCC evidence is missing"]
    try:
        report = load(report_path)
        if report.get("format") != "kicad-cli.fcc-evidence.v1":
            return ["unsupported FCC evidence format"]
        problems = []
        for run in report["runs"]:
            if run["kind"] != "pytest-junit" or sha(run["path"]) != run["sha256"]:
                return ["FCC execution evidence is missing or changed"]
        fresh = collect(root, [r["path"] for r in report["runs"]], binary)
        for key in (
            "inventory_sha256",
            "source_manifest",
            "source_digest",
            "artifact",
            "behaviors",
            "counts",
        ):
            if report.get(key) != fresh[key]:
                problems.append(f"FCC evidence binding differs: {key}")
        inventory = load(root / INVENTORY)
        if inventory.get("exhaustive") is not True:
            problems.append(
                "public promise review is incomplete; enumerated coverage is not full FCC"
            )
        if not fresh["counts"].get("applicable") or fresh["fcc_status"] != "verified":
            problems.append("public-behavior cases include failed or unexecuted requirements")
        if report.get("fcc_status") != fresh["fcc_status"]:
            problems.append("FCC declaration disagrees with execution evidence")
        return problems
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError):
        return ["FCC evidence could not be independently read"]
