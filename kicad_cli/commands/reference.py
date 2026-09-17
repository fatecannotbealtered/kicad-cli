"""``kicad-cli reference`` -- the machine-readable capability description."""

from __future__ import annotations

from typing import Any

from .. import __version__, envelope, errors, registry

# Release gate (CLI-SPEC section 11). Every field here is a claim someone will
# act on, so each one names the evidence that makes it true. Lower it the moment
# a piece of that evidence stops holding.
RELEASE_READINESS: dict[str, Any] = {
    # stable, because the spec defines the three preconditions and all three are
    # now met with inspectable evidence. It had been left at beta on the reading
    # that "the suite needs a local KiCad" was itself disqualifying. It is not:
    # the spec asks for mock upstream tests *because* the real upstream is often
    # unavailable, and for one recorded live run -- not for live runs in CI.
    "level": "stable",
    "fcc_required": True,
    # Every command in the registry is run for real and its output compared,
    # field for field, against the schema it advertises. Strict mode makes the
    # envelope refuse to emit anything else, so a drifted contract fails the
    # suite instead of misleading a caller. Write commands go through the
    # confirmation gate and are applied to copies of KiCad's own demo projects.
    # verified, not merely present: tests/test_fcc_guard.py enumerates the leaf
    # commands here and fails when one of them is never dispatched during the
    # run. It used to skip itself unless this said "verified", so it had never
    # run, and four commands had no test at all -- board live and three of the
    # fab plots. Coverage is now measured from a dispatch trace rather than by
    # searching the test sources, which counted a command named in a docstring
    # and missed one called through a parametrised fixture.
    "fcc_status": "verified",
    "mock_upstream_required": True,
    # tests/test_mock_upstream.py substitutes both KiCad boundaries -- the
    # official binary and the bundled interpreter -- and runs against a board
    # fixture in this repo, so it needs no KiCad at all. It exists for the paths
    # a real KiCad will not produce on demand: refusing to launch, exiting zero
    # with no output (the Windows transient the retries are for), hanging,
    # emitting rubbish, and failing twice before succeeding. Those had been
    # reasoned about and shipped untested.
    "mock_upstream_status": "verified",
    "live_smoke_required_for_stable": True,
    # Recorded in docs/evidence/live-smoke-1.0.0.md: the verbatim output of the
    # suite against KiCad 10.0.6, not a summary of it. ("present" used to sit
    # here and is not one of the four values the spec allows, so it was
    # unreadable to anything consuming this field mechanically.)
    "live_smoke_status": "verified",
    "reason": "All three preconditions hold with evidence that can be inspected: every "
    "command is covered and the coverage is measured from a dispatch trace rather than "
    "asserted; the failure paths are covered against a substituted KiCad; and one live "
    "run against KiCad 10.0.6 is recorded verbatim. Two limits this level does not "
    "erase: that live run is one platform and one KiCad version, and the SWIG binding "
    "most commands depend on is scheduled for removal in KiCad 11. See "
    "docs/COMPATIBILITY.md for what each command would need then.",
    "required_evidence": [
        "functional_contract_coverage_100",
        "mock_upstream_contract_tests",
        "recorded_live_smoke_for_stable",
    ],
    "evidence_held": [
        "functional_contract_coverage_100: tests/test_contract.py runs every command "
        "with KICAD_CLI_STRICT and asserts the emitted fields equal the declared schema; "
        "tests/test_fcc_guard.py fails if any of the 22 leaf commands is never dispatched",
        "skill_contract: tests/test_skill.py checks SKILL.md against the live registry, "
        "so the Skill cannot advertise a command, field or option that does not exist",
        "mock_upstream_contract_tests: tests/test_mock_upstream.py substitutes both KiCad "
        "boundaries and covers success, output schema, validation, empty results, upstream "
        "failure, timeout, retry recovery, protocol noise, exit codes and the stdout/stderr "
        "boundary. It needs no KiCad, so it runs where the live tests skip. Auth, permission, "
        "pagination and rate limiting are not applicable: no service, no credentials, no "
        "paged results",
        "recorded_live_smoke_for_stable: docs/evidence/live-smoke-1.0.0.md is the verbatim "
        "output of the full suite against KiCad 10.0.6 on Windows, 99 passed",
    ],
}


def run(_args: dict[str, Any]) -> None:
    commands = registry.build()
    envelope.ok(
        {
            "tool": "kicad-cli",
            "version": __version__,
            "release_readiness": RELEASE_READINESS,
            "commands": [{k: v for k, v in c.items() if k != "handler"} for c in commands],
            "schemas": registry.SCHEMAS,
            "exit_codes": {name: spec[0] for name, spec in errors.CORE.items()},
        }
    )
