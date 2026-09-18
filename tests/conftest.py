"""Wiring for the coverage guard.

Every test in this suite drives the CLI as a subprocess, so pointing
``KICAD_CLI_TRACE`` at one file here makes each of them record which commands
actually reached a handler. `test_fcc_guard.py` reads that file instead of
grepping the test sources for command names.

Two things have to be arranged for that to work, and both live here rather than
in the guard: the guard must run after everything it is measuring, and it must
be able to tell a full suite run from ``pytest -k something``, because in the
second case the commands it finds missing are simply the ones nobody asked to
run.
"""

from __future__ import annotations

import os
from pathlib import Path

TESTS = Path(__file__).resolve().parent
# Anything that reads a trace has to run after everything that writes one.
# The error-coverage guard sorts alphabetically before test_mock_upstream, so
# it measured a trace that was still being written and reported four codes as
# unreachable that the suite does produce.
GUARDS = ("test_fcc_guard.py", "test_error_coverage.py")


def pytest_configure(config) -> None:
    trace = Path(config.rootdir) / ".fcc-trace"
    trace.unlink(missing_ok=True)
    os.environ["KICAD_CLI_TRACE"] = str(trace)
    config.kicad_cli_trace = trace
    # Which E_* a run really produced is the same kind of question and a
    # separate answer: the dispatch trace means "a command ran", and a test
    # asserts that file does not exist when a request is refused before any
    # command does.
    errors = Path(config.rootdir) / ".error-trace"
    errors.unlink(missing_ok=True)
    os.environ["KICAD_CLI_ERROR_TRACE"] = str(errors)
    config.kicad_cli_error_trace = errors


def pytest_collection_modifyitems(config, items) -> None:
    # The sort is stable and the key is a bool, so this only lifts the guards to
    # the end and leaves every other test in collection order.
    items.sort(key=lambda item: any(guard in item.nodeid for guard in GUARDS))

    collected = {Path(item.fspath).name for item in items}
    expected = {p.name for p in TESTS.glob("test_*.py")}
    config.kicad_cli_full_suite = collected >= expected


def pytest_unconfigure(config) -> None:
    for variable, attribute in (
        ("KICAD_CLI_TRACE", "kicad_cli_trace"),
        ("KICAD_CLI_ERROR_TRACE", "kicad_cli_error_trace"),
    ):
        os.environ.pop(variable, None)
        trace = getattr(config, attribute, None)
        if trace is not None:
            trace.unlink(missing_ok=True)
