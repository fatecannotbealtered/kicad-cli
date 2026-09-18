"""Which declared `E_*` codes does a run actually produce?

`reference` publishes an error table an agent is expected to branch on. A code
in that table that no command can produce is a branch nobody will ever take and
a promise nothing keeps; a code a command produces without declaring would be
worse still, and `errors.exit_code` makes that impossible by construction.

Measured the same way command dispatch is, and for the same reason: searching
the test sources for an error name counts a docstring mention and misses a
parametrised call. `envelope.fail` appends the code it emitted to
its own trace file, so a line there means a command really returned it.

Codes that do not apply are declared here rather than quietly excused. This
tool has no service, no account and no paged results, so several of the sixteen
cannot occur -- and saying which, with why, is the part that keeps the rest
meaningful.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from kicad_cli import errors  # noqa: E402

REPORT = REPO / ".error-coverage.json"

# Not applicable, with the reason. `release_readiness.evidence_held` makes the
# same claim in prose; this is the machine-checkable copy of it.
NOT_APPLICABLE = {
    "E_AUTH": "no service and no credentials to authenticate against",
    "E_FORBIDDEN": "no account, so no permission boundary to be refused by",
    "E_NETWORK": "operates on local files and a local KiCad; nothing is fetched",
    "E_RATE_LIMITED": "no service to be throttled by",
    "E_SERVER": "no server; upstream failures are local process failures (E_IO)",
    # Reached only by a bug in this tool, which a test cannot stage honestly.
    "E_UNKNOWN": "the catch-all for an unhandled exception; staging one would "
    "test the stage, not the tool",
    "E_INTERRUPTED": "requires a signal mid-command; covered by the write "
    "transaction's rollback tests rather than through the envelope",
}


def _traced_codes(pytestconfig) -> set[str]:
    trace = getattr(pytestconfig, "kicad_cli_error_trace", None)
    assert trace is not None, "conftest did not set up the error trace"
    assert trace.exists(), "no error was traced at all; the guard is measuring nothing"
    return {
        line.strip().removeprefix("error:")
        for line in trace.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("error:")
    }


def _why_this_run_cannot_measure(request, pytestconfig) -> str | None:
    if not getattr(pytestconfig, "kicad_cli_full_suite", False):
        return "partial selection; run the whole suite to measure error coverage"
    # Some codes can only be produced by driving a real KiCad -- E_INTEGRITY
    # needs a router that actually routes, with only the DRC verdict staged.
    # On a machine without one those tests skip, and counting their codes as
    # unreachable would report the absence of KiCad as a gap in the tool. Same
    # reasoning, and the same answer, as the dispatch guard.
    from kicad_demos import DEMOS

    if not DEMOS.is_dir():
        return (
            "no KiCad on this machine, so the tests that provoke the KiCad-only "
            "codes skipped; error coverage is measurable only where they can run"
        )
    if request.session.testsfailed:
        return f"{request.session.testsfailed} earlier failure(s); the trace is incomplete"
    return None


def test_the_not_applicable_list_only_names_real_codes():
    """A typo here would silently excuse nothing and hide a real gap."""
    unknown = sorted(set(NOT_APPLICABLE) - set(errors.CORE))
    assert not unknown, f"NOT_APPLICABLE names codes that do not exist: {unknown}"


def test_error_coverage_is_measured(request, pytestconfig):
    """Count it and write it down. The gate is the next test."""
    reason = _why_this_run_cannot_measure(request, pytestconfig)
    if reason:
        pytest.skip(reason)
    produced = _traced_codes(pytestconfig)
    expected = set(errors.CORE) - set(NOT_APPLICABLE)
    missing = sorted(expected - produced)
    undeclared = sorted(produced - set(errors.CORE))
    result = {
        "metric": "declared_error_code_coverage",
        "declared": len(errors.CORE),
        "applicable": len(expected),
        "produced": sorted(produced),
        "missing": missing,
        "not_applicable": sorted(NOT_APPLICABLE),
        "undeclared": undeclared,
    }
    REPORT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    summary = f"error code coverage {len(expected) - len(missing)}/{len(expected)} applicable"
    if missing:
        summary += "; never produced: " + ", ".join(missing)
    warnings.warn(summary, UserWarning, stacklevel=1)
    assert not undeclared, (
        f"commands produced codes `reference` does not declare: {undeclared}. "
        "An agent branching on the published table cannot handle these."
    )


def test_every_applicable_error_code_is_reached(request, pytestconfig):
    """A declared code no command produces is a branch that will never be taken."""
    reason = _why_this_run_cannot_measure(request, pytestconfig)
    if reason:
        pytest.skip(reason)
    produced = _traced_codes(pytestconfig)
    missing = sorted((set(errors.CORE) - set(NOT_APPLICABLE)) - produced)
    assert not missing, (
        f"{len(missing)} declared error code(s) were never produced by any test: "
        + ", ".join(missing)
        + ". Either a command should be able to return them and no test asks, or "
        "they belong in NOT_APPLICABLE with a reason."
    )


@pytest.mark.parametrize(
    ("argv", "code"),
    [
        (["board", "audit", "--board", "no-such-board.kicad_pcb"], "E_NOT_FOUND"),
        (["board", "audit"], "E_USAGE"),
        (["board", "audit", "--board", "x", "--oz", "nan"], "E_USAGE"),
    ],
)
def test_the_error_trace_records_what_left_the_process(argv, code, tmp_path):
    """Guard the guard: the measurement above is only worth its trace."""
    trace = tmp_path / "trace.txt"
    env = dict(os.environ, KICAD_CLI_ERROR_TRACE=str(trace), PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "kicad_cli.main", *argv, "--compact"],
        capture_output=True,
        cwd=REPO,
        env=env,
        timeout=120,
    )
    doc = json.loads(proc.stdout.decode("utf-8").splitlines()[0])
    assert doc["error"]["code"] == code
    assert f"error:{code}" in trace.read_text(encoding="utf-8")
