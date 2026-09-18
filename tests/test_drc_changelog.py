"""DRC changes must be visible through the actual Agent changelog command."""

import json
import os
import subprocess
import sys
from pathlib import Path


def test_drc_boundary_changes_are_machine_discoverable():
    proc = subprocess.run(
        [sys.executable, "-m", "kicad_cli.main", "changelog", "--compact"],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
        capture_output=True,
        timeout=15,
    )
    assert proc.returncode == 0
    data = json.loads(proc.stdout)["data"]
    unreleased = next(entry for entry in data["entries"] if entry["version"] == "Unreleased")
    assert any("Share one DRC runner" in item for item in unreleased["changes"]["fixed"])
