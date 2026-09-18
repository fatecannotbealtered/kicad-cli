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
    # A live run for this candidate is now recorded, with its source commit, on
    # one platform and one KiCad version. That is what the spec asks for and it
    # is not the same as broad coverage -- the reason says which.
    "live_smoke_status": "verified",
    "reason": "Contract-hardening work in progress. A live run for this candidate is "
    "recorded in docs/evidence/live-smoke-1.0.0+029cac55bc56.md: the full suite and the "
    "frozen binary, against KiCad 10.0.6 on Windows 11, with the source commit named. "
    "One platform and one KiCad version, with no second machine behind it. Functional "
    "contract coverage is still unknown: command dispatch and declared error codes are "
    "measured and complete, global options and error-details shape are not. Nothing "
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
        "tests/test_contract_flag_coverage.py: 31 flag combinations under strict mode",
        "tests/test_untrusted_fields.py: untrusted_fields measured by injection",
        "tests/test_error_coverage.py: 9/9 applicable error codes produced, 7 declared "
        "not applicable with a reason",
        "docs/evidence/live-smoke-1.0.0+029cac55bc56.md: full suite and frozen binary "
        "against KiCad 10.0.6, Windows 11, source commit recorded",
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
            "commands": [{k: v for k, v in c.items() if k != "handler"} for c in commands],
            "schemas": schemas,
            "exit_codes": {name: spec[0] for name, spec in errors.CORE.items()},
            "error_codes": {
                name: {"exit": spec[0], "retryable": spec[1]} for name, spec in errors.CORE.items()
            },
        }
    )
