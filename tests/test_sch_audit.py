"""``sch audit`` has one job: stop a clean ERC from being read as a clean design.

The checks below are about the reasoning, not the plumbing -- that rules KiCad
ships disabled are named as such, that turning them back on is a measurement
rather than a claim, and that a rare finding is not buried under a common one.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from kicad_demos import DEMOS, SKIP_REASON

REPO = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(REPO))
from kicad_cli.commands.erc import (  # noqa: E402
    KICAD_DEFAULT_IGNORED,
    _filters_match,
    _patterns,
    _sample,
    _unflatten,
)


def run(*argv: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "kicad_cli.main", *argv, "--compact"],
        capture_output=True,
        cwd=REPO,
        timeout=1800,
    )
    out = proc.stdout.decode("utf-8", "replace")
    for line in out.splitlines():
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(f"no envelope on stdout: {out[:400]}")


def test_a_rare_finding_survives_sampling() -> None:
    """The bug this guards against shipped once: 91 benign rows before 6 that
    mattered, and a head-of-list sample showed only the benign ones."""
    rows = [{"type": "common", "description": f"c{i}"} for i in range(91)]
    rows += [{"type": "rare", "description": f"r{i}"} for i in range(6)]
    kinds = {r["type"] for r in _sample(rows)}
    assert kinds == {"common", "rare"}


def test_repeated_messages_collapse_to_their_shape() -> None:
    rows = [
        {
            "type": "footprint_filter",
            "description": f"assigned ({x}) does not match (R_*)",
            "sheet": "a",
        }
        for x in ("r1", "r2", "r3")
    ]
    groups = _patterns(rows)
    assert len(groups) == 1
    assert groups[0]["count"] == 3
    assert "(...)" in groups[0]["pattern"]


def test_a_filter_with_a_library_part_is_matched_against_the_whole_id() -> None:
    """Getting this wrong made three connectors look like real mismatches."""
    full = "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"
    assert _filters_match(full, ["Connector*:*_1x??_*"]) is True
    # The same filter must not be satisfied by the bare name alone.
    flattened = "ProjectLib:PinHeader_1x02_P2.54mm_Vertical"
    assert _filters_match(flattened, ["Connector*:*_1x??_*"]) is False
    # A filter without a library part applies to the name only.
    assert _filters_match("AnyLib:R_0603_1608Metric", ["R_*"]) is True


def test_unflattening_recovers_the_original_library_id() -> None:
    flat = "ProjectLib:Resistor_SMD__R_0603_1608Metric"
    assert _unflatten(flat) == "Resistor_SMD:R_0603_1608Metric"
    assert _unflatten("Resistor_SMD:R_0603") == "Resistor_SMD:R_0603"


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_reports_shipped_defaults_as_defaults(tmp_path: Path) -> None:
    work = tmp_path / "cm5"
    shutil.copytree(DEMOS / "cm5_minima", work)
    board = next(work.glob("*.kicad_pcb"))

    r = run("sch", "audit", "--board", str(board))
    assert r["ok"] is True, r
    s = r["data"]["silenced_rules"]
    assert set(s["kicad_default"]) <= KICAD_DEFAULT_IGNORED
    assert s["disabled_in_this_project"] == [], "this project changed nothing"
    assert r["data"]["with_rules_enabled"]["ran"] is True


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_a_locally_disabled_rule_is_called_out(tmp_path: Path) -> None:
    """A rule this project turned off is a different conversation from one
    KiCad ships off, so the two must not be reported together."""
    work = tmp_path / "ecc83"
    shutil.copytree(DEMOS / "ecc83", work)
    board = next(work.glob("*.kicad_pcb"))
    project = board.with_suffix(".kicad_pro")
    data = json.loads(project.read_text(encoding="utf-8"))
    # pin_not_connected is not in KiCad's default-off set.
    data["erc"]["rule_severities"]["pin_not_connected"] = "ignore"
    project.write_text(json.dumps(data, indent=2), encoding="utf-8")

    d = run("sch", "audit", "--board", str(board))["data"]
    assert "pin_not_connected" in d["silenced_rules"]["disabled_in_this_project"]
    assert "pin_not_connected" not in d["silenced_rules"]["kicad_default"]
    assert any(f["id"] == "A3-locally-silenced" for f in d["findings"])


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_the_unmuted_run_never_reports_less(tmp_path: Path) -> None:
    """Enabling rules can only add violations. If this ever fails, the second
    run is not comparable to the first and the whole finding is worthless."""
    work = tmp_path / "ih"
    shutil.copytree(DEMOS / "complex_hierarchy", work)
    board = next(work.glob("*.kicad_pcb"))
    d = run("sch", "audit", "--board", str(board))["data"]
    assert d["with_rules_enabled"]["additional_violations"] >= 0
    counted = sum(d["with_rules_enabled"]["by_type"].values())
    assert counted == d["with_rules_enabled"]["additional_violations"]
