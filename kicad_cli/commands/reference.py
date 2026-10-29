"""``kicad-cli reference`` -- the machine-readable capability description."""

from __future__ import annotations

from typing import Any

from .. import __version__, driver_runtime, envelope, errors, registry

# This tree includes native-engine migrations after the recorded 1.0.0 artifact.
# Do not inherit a stable claim from a different source tree or turn a skipped
# live suite into proof. Keep the remaining blockers inspectable.
RELEASE_READINESS: dict[str, Any] = {
    "level": "unpublishable",
    "fcc_required": True,
    "fcc_status": "unknown",
    "mock_upstream_required": True,
    "mock_upstream_status": "verified",
    "live_smoke_required_for_stable": True,
    # The September record describes another source tree. Targeted native file
    # comparisons do not establish a current frozen-artifact or chain E2E run.
    "live_smoke_status": "unknown",
    "reason": "Native-engine migration in progress. The 2026-09-18 live record in "
    "docs/evidence/live-smoke-1.0.0+029cac55bc56.md describes source 029cac55bc56, "
    "not this tree. A full live smoke/E2E and frozen-binary record for the current "
    "candidate is still required. Functional contract coverage remains unknown: "
    "historical dispatch and error-code counts do not cover the migrated commands, "
    "and global options and error-details shape still require measurement. Nothing "
    "checks at release time that the recorded evidence describes the tree being tagged. "
    "Confirm-token and DRC-isolation live validation and consistent verification and "
    "rollback across write modes remain release blockers.",
    "required_evidence": [
        "functional_contract_coverage_100",
        "mock_upstream_contract_tests",
        "recorded_live_smoke_for_stable",
    ],
    "evidence_held": [
        "tests/test_agent_arguments.py: typed parser and no-dispatch rejection tests",
        "tests/test_agent_reference.py: machine discovery and command scoping tests",
        "tests/test_mock_upstream.py: substituted KiCad success and failure paths",
        "tests/test_contract_flag_coverage.py: strict-mode flag-combination tests",
        "tests/test_untrusted_fields.py: untrusted_fields measured by injection",
        "tests/test_error_coverage.py: declared error-code reachability tests",
        "docs/evidence/live-smoke-1.0.0+029cac55bc56.md: historical full suite and "
        "frozen binary for source 029cac55bc56, not this candidate",
        "docs/evidence/live-smoke-1.0.0.md: historical baseline, not this candidate",
    ],
}


def run(args: dict[str, Any]) -> None:
    commands = registry.build()
    schemas = registry.SCHEMAS
    if args.get("command"):
        selected = [c for c in commands if c["path"] == args["command"]]
        if not selected:
            envelope.fail("E_NOT_FOUND", "command is not registered", {"command": args["command"]})
        commands = selected
        schemas = {c["output_schema"]: schemas[c["output_schema"]] for c in selected}
    envelope.ok(
        {
            "tool": "kicad-cli",
            "version": __version__,
            "risk_tier": "T1",
            "global_options": registry.GLOBAL_OPTIONS,
            "release_readiness": RELEASE_READINESS,
            "compatibility": registry.COMPATIBILITY,
            "runtime_migration": driver_runtime.migration_status(registry.build()),
            "commands": [{k: v for k, v in c.items() if k != "handler"} for c in commands],
            "schemas": schemas,
            "exit_codes": {name: spec[0] for name, spec in errors.CORE.items()},
            "error_codes": {
                name: {"exit": spec[0], "retryable": spec[1]} for name, spec in errors.CORE.items()
            },
        }
    )
