"""Admission check for this repository's stable-only publishing workflow.

This checks declared readiness and version identity; it does not manufacture
or independently certify the live/FCC evidence behind those declarations.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STABLE_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
REQUIRED_EVIDENCE = {
    "functional_contract_coverage_100",
    "mock_upstream_contract_tests",
    "recorded_live_smoke_for_stable",
}


class GateError(Exception):
    """The candidate cannot be evaluated safely."""


def validate(doc: Any, version: Any, tag: str) -> list[str]:
    """Return every admission failure without trusting truthy string booleans."""
    problems: list[str] = []
    if not isinstance(version, str) or not STABLE_VERSION.fullmatch(version):
        problems.append(
            "package version must be stable MAJOR.MINOR.PATCH; no beta/latest promotion"
        )
    if not isinstance(version, str) or tag != f"v{version}":
        problems.append("tag must equal v + package.json version")
    if not isinstance(doc, dict) or doc.get("ok") is not True:
        return [*problems, "reference must return an ok:true JSON envelope"]
    if doc.get("schema_version") != "1.0":
        problems.append("unsupported reference envelope schema_version")
    data = doc.get("data")
    if not isinstance(data, dict):
        return [*problems, "reference.data must be an object"]
    if data.get("tool") != "kicad-cli" or data.get("version") != version:
        problems.append("reference tool/version does not match this package")
    readiness = data.get("release_readiness")
    if not isinstance(readiness, dict):
        return [*problems, "release_readiness must be an object"]
    if readiness.get("level") != "stable":
        problems.append("publishing requires stable; beta/unpublishable/unknown are blocked")
    for name in ("fcc_required", "mock_upstream_required", "live_smoke_required_for_stable"):
        if readiness.get(name) is not True:
            problems.append(f"{name} must be true for this KiCad tool")
    for name in ("fcc_status", "mock_upstream_status", "live_smoke_status"):
        if readiness.get(name) != "verified":
            problems.append(f"{name} must be verified")
    required = readiness.get("required_evidence")
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        problems.append("required_evidence must be a string array")
    elif not REQUIRED_EVIDENCE <= set(required):
        problems.append("required_evidence omits a stable-release prerequisite")
    held = readiness.get("evidence_held")
    if (
        not isinstance(held, list)
        or not held
        or not all(isinstance(item, str) and item.strip() for item in held)
    ):
        problems.append("evidence_held must contain inspectable evidence descriptions")
    if not isinstance(readiness.get("reason"), str) or not readiness["reason"].strip():
        problems.append("release_readiness.reason is missing")
    return problems


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GateError("duplicate JSON key in reference")
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise GateError("non-finite JSON value in reference")


def read_reference(binary: Path | None = None) -> dict[str, Any]:
    """Exercise the source or frozen artifact, never a PATH lookalike.

    stdout must be exactly one JSON document. Child output is not replayed into
    CI logs; report stable diagnostics instead of interpolating untrusted text.
    """
    if binary is None:
        argv = [sys.executable, "-m", "kicad_cli.main", "reference", "--compact"]
    else:
        target = binary.expanduser().resolve()
        if not target.is_file():
            raise GateError("frozen binary does not exist")
        argv = [str(target), "reference", "--compact"]
    env = dict(os.environ)
    env.pop("KICAD_CLI_TRACE", None)  # A release gate is not live FCC evidence.
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GateError("candidate reference could not complete") from exc
    if proc.returncode != 0:
        raise GateError("candidate reference exited unsuccessfully")
    if len(proc.stdout) > 4 * 1024 * 1024:
        raise GateError("reference output exceeds the release-check limit")
    try:
        return json.loads(proc.stdout, object_pairs_hook=_object, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise GateError("reference stdout is not one valid JSON document") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="Exact candidate tag (vMAJOR.MINOR.PATCH)")
    parser.add_argument("--binary", type=Path, help="Check this frozen binary instead of source")
    args = parser.parse_args(argv)
    try:
        version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
        problems = validate(read_reference(args.binary), version, args.tag)
    except (OSError, ValueError, KeyError, GateError) as exc:
        # Never publish after a checker failure. No child output/secret values.
        problems = [
            str(exc) if isinstance(exc, GateError) else "release metadata could not be read"
        ]
    print(json.dumps({"ok": not problems, "gate": "stable-release", "problems": problems}))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
