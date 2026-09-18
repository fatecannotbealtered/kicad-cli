"""``kicad-cli reference`` -- the machine-readable capability description."""

from __future__ import annotations

from typing import Any

from .. import __version__, envelope, errors, registry

# This branch is a contract-hardening candidate, not the recorded 1.0.0 artifact.
# Do not inherit a stable claim from a different source tree or turn a skipped
# live suite into proof. Keep the remaining blockers inspectable.
RELEASE_READINESS: dict[str, Any] = {
    "level": "unpublishable",
    "fcc_required": True,
    "fcc_status": "unknown",
    "mock_upstream_required": True,
    "mock_upstream_status": "verified",
    "live_smoke_required_for_stable": True,
    "live_smoke_status": "missing",
    "reason": "Contract-hardening work in progress. The recorded 1.0.0 smoke run is "
    "historical, not evidence for this candidate. Fresh live coverage is required. "
    "Confirm-token authentication/expiry/replay protection, live validation of DRC isolation, and "
    "consistent verification/rollback across write modes remain release blockers.",
    "required_evidence": [
        "functional_contract_coverage_100",
        "mock_upstream_contract_tests",
        "recorded_live_smoke_for_stable",
    ],
    "evidence_held": [
        "tests/test_agent_arguments.py: typed parser and no-dispatch rejection tests",
        "tests/test_agent_reference.py: machine discovery and command scoping tests",
        "tests/test_mock_upstream.py: substituted KiCad success and failure paths",
        "docs/evidence/live-smoke-1.0.0.md: historical baseline only",
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
            "commands": [{k: v for k, v in c.items() if k != "handler"} for c in commands],
            "schemas": schemas,
            "exit_codes": {name: spec[0] for name, spec in errors.CORE.items()},
            "error_codes": {
                name: {"exit": spec[0], "retryable": spec[1]} for name, spec in errors.CORE.items()
            },
        }
    )
