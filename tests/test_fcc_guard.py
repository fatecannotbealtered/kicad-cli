"""FCC guard (CLI-SPEC §13): every leaf command declared by `reference` must be
exercised by at least one command-level test.

Measurement and enforcement are separate, and they were not. The guard used to
return early unless `release_readiness.fcc_status` already said "verified", so
while the status was "unknown" nothing was counted at all -- and the status
cannot honestly become "verified" without the count. Coverage was therefore not
failing; it was unmeasured, which reads the same in a green run and is worse,
because nobody can see what is missing. So `test_fcc_coverage_is_measured`
always counts where counting is possible and records the result, and
`test_fcc_is_enforced_when_it_is_claimed` is the gate that fails a dishonest
"verified".

What counts as coverage is measured, not inferred. The first version searched
the test sources for the literal argument tuple a command would be called with,
which was wrong in both directions: a command named in a docstring counted,
while `run("fab", fmt, ...)` in a parametrised test did not, so three of the
four plot commands read as untested when each had a real test. The CLI appends
the command it dispatched to `KICAD_CLI_TRACE` (see `main._trace`), and conftest
points every test at one file. A line there means the command ran.
"""

import json
import os
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
REPORT = REPO / ".fcc-coverage.json"


def _run_reference():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    # Deliberately untraced: a guard must not be able to produce its own
    # evidence. `reference` is covered because test_contract.py calls it.
    env.pop("KICAD_CLI_TRACE", None)
    result = subprocess.run(
        [sys.executable, "-m", "kicad_cli.main", "reference", "--json"],
        capture_output=True,
        timeout=30,
        env=env,
        cwd=REPO,
    )
    return result.returncode, result.stdout.decode("utf-8", errors="replace")


def _collect_leaves(commands, out):
    for command in commands:
        children = command.get("children") or []
        if children:
            _collect_leaves(children, out)
            continue
        path = (command.get("path") or "").strip()
        if path.startswith("kicad-cli"):
            path = path[len("kicad-cli") :].strip()
        if path:
            out.append(path)


def _declared_and_leaves():
    code, stdout = _run_reference()
    assert code == 0, stdout
    envelope = json.loads(stdout[stdout.find("{") :])
    assert envelope["ok"] is True
    leaves = []
    _collect_leaves(envelope["data"]["commands"], leaves)
    assert leaves, "reference enumerated zero leaf commands; the FCC guard would be a no-op"
    return envelope["data"]["release_readiness"]["fcc_status"], leaves


def _why_this_run_cannot_measure(request, pytestconfig):
    """Reasons a trace would be incomplete through no fault of the coverage."""
    # A partial run produces a partial trace, and the commands it would report
    # missing are the ones nobody asked to run.
    if not getattr(pytestconfig, "kicad_cli_full_suite", False):
        return "partial selection; run the whole suite to measure coverage"
    # Same for a machine with no KiCad, which is what CI is. The live tests skip
    # there, so most commands are never dispatched -- the first CI run reported
    # 17 of 22 "never dispatched" when the truth was that 46 tests had skipped.
    # Counting a collected-then-skipped test as absent coverage is the same
    # mistake as counting a docstring mention as present.
    from kicad_demos import DEMOS

    if not DEMOS.is_dir():
        return (
            "no KiCad on this machine, so the live tests skipped and most commands were "
            "never dispatched; coverage is measurable only where they can run"
        )
    # And the same for a run that already failed: the trace stops where it did.
    if request.session.testsfailed:
        return f"{request.session.testsfailed} earlier failure(s); the trace is incomplete"
    return None


def _measure(pytestconfig, leaves):
    trace = getattr(pytestconfig, "kicad_cli_trace", None)
    assert trace is not None, "conftest did not set up the trace"
    assert trace.exists(), (
        "no command was traced at all. Either conftest's KICAD_CLI_TRACE is not "
        "reaching the subprocesses, or main._trace has been removed - in both "
        "cases the guard is measuring nothing and must not pass."
    )
    exercised = {line.strip() for line in trace.read_text(encoding="utf-8").splitlines()}
    exercised.discard("")
    covered = sorted(set(leaves) & exercised)
    return {
        "metric": "command_dispatch_coverage",
        "not_measured": ["flag coverage", "error-code coverage", "documented-behavior coverage"],
        "leaves": len(leaves),
        "covered": len(covered),
        "percent": round(100.0 * len(covered) / len(leaves), 1),
        "missing": sorted(set(leaves) - exercised),
        "undeclared": sorted(exercised - set(leaves)),
    }


def test_fcc_coverage_is_measured(request, pytestconfig):
    """Count it and write it down, whatever the declared status says.

    This does not fail on a gap. A gap is a fact about the suite that the owner
    needs in front of them; turning it into a red run while the tool honestly
    declares `fcc_status: unknown` would only push someone to skip the guard
    again. `test_fcc_is_enforced_when_it_is_claimed` is where a gap becomes a
    failure, and it does so exactly when the tool claims there is none.
    """
    _declared, leaves = _declared_and_leaves()
    reason = _why_this_run_cannot_measure(request, pytestconfig)
    if reason:
        pytest.skip(reason)
    result = _measure(pytestconfig, leaves)
    REPORT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    # "Command dispatch coverage", not "FCC", and the distinction is the whole
    # point: this counts leaf commands that some test actually ran. CLI-SPEC
    # asks for every documented *behavior* -- flags, modes, error codes -- to
    # have a command-level test, which is a strictly larger set. Reporting this
    # number as FCC is how a guard like this starts lying.
    summary = (
        f"command dispatch coverage {result['covered']}/{result['leaves']} "
        f"({result['percent']}%); not flag/error coverage, so not FCC on its own"
    )
    if result["missing"]:
        summary += "; never dispatched: " + ", ".join(result["missing"])
    # A warning survives pytest's output capture on a passing test, so the
    # number is visible in the run summary instead of only in a file nobody
    # opens. This is the number that was black while the guard skipped itself.
    warnings.warn(summary, UserWarning, stacklevel=1)
    assert result["percent"] >= 0  # The measurement ran; the gate is the next test.


def test_fcc_is_enforced_when_it_is_claimed(request, pytestconfig):
    """A "verified" claim must be backed by a full trace, or the suite fails."""
    declared, leaves = _declared_and_leaves()
    if declared != "verified":
        pytest.skip(
            f"fcc_status is declared {declared!r}, so there is no claim to enforce; "
            "test_fcc_coverage_is_measured reports the current number either way"
        )
    reason = _why_this_run_cannot_measure(request, pytestconfig)
    if reason:
        pytest.skip(reason)
    result = _measure(pytestconfig, leaves)
    assert not result["missing"], (
        f"fcc_status claims 'verified' but {len(result['missing'])}/{result['leaves']} "
        "leaf commands were never dispatched by any test: " + ", ".join(result["missing"])
    )
    assert not result["undeclared"], (
        "tests dispatched commands that `reference` does not declare, so callers "
        f"cannot discover them: {', '.join(result['undeclared'])}"
    )
