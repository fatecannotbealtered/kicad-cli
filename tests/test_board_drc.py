"""Asking whether a board is manufacturable, without writing to it.

Every write command here already runs DRC -- it is the referee for `board
route` and `board place`, and the thing they roll back against. There was no
way to simply ask. "Is this board manufacturable?" is the question at the end
of the chain, and answering it required performing a write, which is the wrong
shape for a question.

The two properties worth defending are that a violation is not an error of this
command, and that a truncated list never produces a truncated count.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from kicad_demos import DEMOS, SKIP_REASON

REPO = Path(__file__).resolve().parents[1]

needs_kicad = pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)

NETLIST = """(export (version "E")
  (components
    (comp (ref "R1") (value "10k") (footprint "Resistor_SMD:R_0805_2012Metric"))
    (comp (ref "R2") (value "10k") (footprint "Resistor_SMD:R_0805_2012Metric")))
  (nets
    (net (code 1) (name "MID")
      (node (ref "R1") (pin "2"))
      (node (ref "R2") (pin "1")))))
"""


def run(argv: list[str]) -> tuple[dict | None, int]:
    env = dict(os.environ, KICAD_CLI_STRICT="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "kicad_cli.main", *argv, "--compact"],
        capture_output=True,
        cwd=REPO,
        env=env,
        timeout=3600,
    )
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith("{"):
            return json.loads(line), proc.returncode
    return None, proc.returncode


def confirmed(argv: list[str]) -> dict:
    plan, _ = run([*argv, "--dry-run"])
    assert plan is not None and plan["error"]["code"] == "E_CONFIRMATION_REQUIRED", plan
    doc, _ = run([*argv, "--confirm", plan["error"]["details"]["confirm_token"]])
    return doc


def build(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    netlist = directory / "circuit.net"
    netlist.write_text(NETLIST, encoding="utf-8")
    board = directory / "x.kicad_pcb"
    doc = confirmed(["board", "from-netlist", "--netlist", str(netlist), "--out", str(board)])
    assert doc["ok"] is True, doc
    return board


@needs_kicad
def test_an_unrouted_board_is_reported_as_not_ready_to_fabricate(tmp_path):
    board = build(tmp_path)
    doc, code = run(["board", "drc", "--board", str(board)])
    assert doc["ok"] is True, doc
    assert code == 0, "finding a problem is not a failure of the command"
    data = doc["data"]
    assert data["ok_to_fabricate"] is False
    assert data["unconnected_count"] >= 1
    assert data["oracle"] == "kicad-cli pcb drc"


@needs_kicad
def test_a_missing_connection_names_the_pads_at_both_ends(tmp_path):
    """Every unconnected entry carries the description "Missing connection
    between items", which identifies nothing. The pads are in the items."""
    board = build(tmp_path)
    doc, _ = run(["board", "drc", "--board", str(board)])
    first = doc["data"]["unconnected"][0]
    assert len(first["items"]) >= 2, first
    joined = " ".join(first["items"])
    assert "R1" in joined and "R2" in joined, joined


@needs_kicad
def test_a_routed_board_comes_back_ready_to_fabricate(tmp_path):
    board = build(tmp_path)
    routed = confirmed(["board", "route", "--board", str(board), "--mode", "repair"])
    assert routed["ok"] is True, routed
    doc, _ = run(["board", "drc", "--board", str(board)])
    assert doc["data"]["unconnected_count"] == 0, doc["data"]["unconnected"]
    assert doc["data"]["counts"].get("error", 0) == 0
    assert doc["data"]["ok_to_fabricate"] is True


@needs_kicad
def test_filtering_by_severity_does_not_shrink_the_counts(tmp_path):
    """A list trimmed to fit is reasonable; a count trimmed to fit is a lie.

    `--severity error` on a board whose only problems are warnings must still
    report those warnings in `counts` and `violations_total`, or an agent
    reading the filtered envelope concludes the board is clean.
    """
    board = build(tmp_path)
    everything, _ = run(["board", "drc", "--board", str(board)])
    errors_only, _ = run(["board", "drc", "--board", str(board), "--severity", "error"])
    assert errors_only["data"]["counts"] == everything["data"]["counts"]
    assert errors_only["data"]["violations_total"] == everything["data"]["violations_total"]
    assert all(v["severity"] == "error" for v in errors_only["data"]["violations"])


@needs_kicad
def test_a_limit_trims_the_list_and_says_so(tmp_path):
    board = build(tmp_path)
    doc, _ = run(["board", "drc", "--board", str(board), "--limit", "1"])
    data = doc["data"]
    assert data["violations_shown"] <= 1
    assert len(data["unconnected"]) <= 1
    # The totals are of the whole report, not of what survived the limit.
    assert data["unconnected_count"] >= len(data["unconnected"])


def test_a_severity_that_is_not_a_severity_is_refused(tmp_path):
    """Checked before KiCad is needed: it is a question about the argument."""
    doc, code = run(["board", "drc", "--board", "nope.kicad_pcb", "--severity", "fatal"])
    assert doc["error"]["code"] == "E_USAGE", doc
    assert code != 0


def test_a_board_that_is_not_there_is_reported_as_missing(tmp_path):
    doc, _ = run(["board", "drc", "--board", str(tmp_path / "absent.kicad_pcb")])
    assert doc["error"]["code"] == "E_NOT_FOUND", doc
