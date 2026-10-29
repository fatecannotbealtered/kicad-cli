"""Configured replay fails before writing when required dependencies are absent."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_native_workflow_has_no_historical_installation_fallback(tmp_path):
    env = os.environ.copy()
    for key in ("KICAD_CLI_ROOT", "KICAD_CLI_PYTHON", "KICAD_CLI_OFFICIAL"):
        env.pop(key, None)
    target = tmp_path / "new-run"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(REPO / "bench/run_engineering_native.py"),
            "--output",
            str(target),
        ],
        env=env,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 2
    assert b"no historical installation fallback" in result.stderr
    assert not target.exists()


def test_explicit_missing_native_dependency_fails_before_creating_state(tmp_path):
    target = tmp_path / "new-run"
    env = os.environ.copy()
    for key in ("KICAD_CLI_ROOT", "KICAD_CLI_PYTHON", "KICAD_CLI_OFFICIAL"):
        env.pop(key, None)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(REPO / "bench/run_engineering_native.py"),
            "--kicad-root",
            str(tmp_path / "missing-native"),
            "--output",
            str(target),
        ],
        capture_output=True,
        env=env,
        timeout=15,
    )
    assert result.returncode == 2
    assert b"Missing native Python" in result.stderr
    assert not target.exists()
