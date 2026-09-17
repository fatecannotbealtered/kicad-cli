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
GUARD = "test_fcc_guard.py"


def pytest_configure(config) -> None:
    trace = Path(config.rootdir) / ".fcc-trace"
    trace.unlink(missing_ok=True)
    os.environ["KICAD_CLI_TRACE"] = str(trace)
    config.kicad_cli_trace = trace


def pytest_collection_modifyitems(config, items) -> None:
    # The sort is stable and the key is a bool, so this only lifts the guard to
    # the end and leaves every other test in collection order.
    items.sort(key=lambda item: GUARD in item.nodeid)

    collected = {Path(item.fspath).name for item in items}
    expected = {p.name for p in TESTS.glob("test_*.py")}
    config.kicad_cli_full_suite = collected >= expected


def pytest_unconfigure(config) -> None:
    os.environ.pop("KICAD_CLI_TRACE", None)
    trace = getattr(config, "kicad_cli_trace", None)
    if trace is not None:
        trace.unlink(missing_ok=True)
