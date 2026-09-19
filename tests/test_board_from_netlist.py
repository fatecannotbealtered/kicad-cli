"""The step between a schematic and a board.

KiCad implements "Update PCB from Schematic" and does not expose it headlessly,
which is the sentence `docs/COMPATIBILITY.md` has always carried. It is a fact
about the dialog, not about the work: loading a footprint, placing it and
joining pads into nets are all things pcbnew does. Without this command the
chain had a hole in the middle -- you could describe a circuit and you could
improve a board, and nothing joined the two.

The last test is the one that matters. It runs the whole chain, because the
parts working separately has never been the claim.
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

NETLIST = """(export (version "E")
  (components
    (comp (ref "R1") (value "10k") (footprint "Resistor_SMD:R_0805_2012Metric"))
    (comp (ref "R2") (value "10k") (footprint "Resistor_SMD:R_0805_2012Metric")))
  (nets
    (net (code 1) (name "IN")
      (node (ref "R1") (pin "1")))
    (net (code 2) (name "MID")
      (node (ref "R1") (pin "2"))
      (node (ref "R2") (pin "1")))
    (net (code 3) (name "GND")
      (node (ref "R2") (pin "2")))))
"""

needs_kicad = pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)


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


def write_netlist(directory: Path, text: str = NETLIST) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "circuit.net"
    path.write_text(text, encoding="utf-8")
    return path


def confirmed(argv: list[str]) -> dict:
    plan, _ = run([*argv, "--dry-run"])
    assert plan is not None and plan["error"]["code"] == "E_CONFIRMATION_REQUIRED", plan
    doc, _ = run([*argv, "--confirm", plan["error"]["details"]["confirm_token"]])
    return doc


@needs_kicad
def test_a_dry_run_plans_the_board_and_writes_nothing(tmp_path):
    netlist = write_netlist(tmp_path)
    board = tmp_path / "out.kicad_pcb"
    doc, _ = run(
        ["board", "from-netlist", "--netlist", str(netlist), "--out", str(board), "--dry-run"]
    )
    assert doc["error"]["code"] == "E_CONFIRMATION_REQUIRED", doc
    preview = doc["error"]["details"]["preview"]
    assert preview["components"] == 2
    assert preview["nets"] == 3
    assert not board.exists(), "a dry run must not create the board"


@needs_kicad
def test_a_built_board_carries_every_footprint_and_every_connection(tmp_path):
    netlist = write_netlist(tmp_path)
    board = tmp_path / "out.kicad_pcb"
    doc = confirmed(["board", "from-netlist", "--netlist", str(netlist), "--out", str(board)])
    assert doc["ok"] is True, doc
    data = doc["data"]
    assert data["footprints"] == 2
    assert data["nets"] == 3
    # Four pads across two resistors, and the netlist names all four.
    assert data["pads_connected"] == 4
    assert data["unmatched_nodes"] == []
    assert board.is_file()

    # Our own reader agrees, which is the check that matters more than the
    # counts the writer reports about itself.
    audit, _ = run(["board", "audit", "--board", str(board)])
    assert audit["ok"] is True, audit
    assert audit["data"]["copper_layers"], audit


@needs_kicad
def test_a_node_the_footprint_has_no_pad_for_is_reported_rather_than_dropped(tmp_path):
    """Silently skipping it would deliver a board missing a connection.

    A netlist naming a pad the footprint does not have is a mismatch between
    symbol and footprint -- a design problem, not an execution one, so it comes
    back as a fact about the board rather than as nothing at all.
    """
    text = NETLIST.replace('(node (ref "R2") (pin "2"))', '(node (ref "R2") (pin "9"))')
    netlist = write_netlist(tmp_path, text)
    board = tmp_path / "out.kicad_pcb"
    doc = confirmed(["board", "from-netlist", "--netlist", str(netlist), "--out", str(board)])
    assert doc["ok"] is True, doc
    assert doc["data"]["unmatched_nodes"] == [{"net": "GND", "node": "R2.9"}], doc["data"]


def test_a_footprint_that_is_not_installed_fails_by_name(tmp_path):
    text = NETLIST.replace("Resistor_SMD:R_0805_2012Metric", "Nope_SMD:NoSuchFootprint", 1)
    netlist = write_netlist(tmp_path, text)
    doc, code = run(
        [
            "board",
            "from-netlist",
            "--netlist",
            str(netlist),
            "--out",
            str(tmp_path / "out.kicad_pcb"),
            "--dry-run",
        ]
    )
    assert doc["error"]["code"] == "E_VALIDATION", doc
    assert "NoSuchFootprint" in json.dumps(doc["error"], ensure_ascii=False)
    assert code == 2


def test_a_component_with_no_footprint_is_named(tmp_path):
    text = NETLIST.replace(' (footprint "Resistor_SMD:R_0805_2012Metric")', "", 1)
    doc, _ = run(
        [
            "board",
            "from-netlist",
            "--netlist",
            str(write_netlist(tmp_path, text)),
            "--out",
            str(tmp_path / "out.kicad_pcb"),
            "--dry-run",
        ]
    )
    assert doc["error"]["code"] == "E_VALIDATION", doc
    problem = doc["error"]["details"]["problems"][0]
    assert problem["ref"] == "R1" and "no footprint" in problem["problem"], problem


def test_an_empty_netlist_is_refused(tmp_path):
    doc, _ = run(
        [
            "board",
            "from-netlist",
            "--netlist",
            str(write_netlist(tmp_path, '(export (version "E") (components))')),
            "--out",
            str(tmp_path / "out.kicad_pcb"),
            "--dry-run",
        ]
    )
    assert doc["error"]["code"] == "E_VALIDATION", doc
    assert "no components" in json.dumps(doc["error"], ensure_ascii=False)


@needs_kicad
@pytest.mark.skipif(
    __import__("importlib.util", fromlist=["util"]).find_spec("skidl") is None,
    reason="the whole chain needs the schematic generator",
)
def test_the_whole_chain_runs_from_a_specification_to_gerbers(tmp_path):
    """Specification to manufacturing files, entirely through this tool.

    Each command working on its own was never the claim. This is the claim.
    """
    spec = tmp_path / "divider.json"
    spec.write_text(
        json.dumps(
            {
                "title": "divider",
                "parts": [
                    {
                        "ref": "R1",
                        "symbol": "Device:R",
                        "value": "10k",
                        "footprint": "Resistor_SMD:R_0805_2012Metric",
                    },
                    {
                        "ref": "R2",
                        "symbol": "Device:R",
                        "value": "10k",
                        "footprint": "Resistor_SMD:R_0805_2012Metric",
                    },
                ],
                "nets": [
                    {"name": "IN", "connect": ["R1.1", "R2.1"]},
                    {"name": "OUT", "connect": ["R1.2", "R2.2"]},
                ],
            }
        ),
        encoding="utf-8",
    )

    created = confirmed(["sch", "create", "--spec", str(spec), "--out", str(tmp_path)])
    assert created["ok"] is True, created
    netlist = created["data"]["written"]["netlist"]

    board = tmp_path / "divider.kicad_pcb"
    built = confirmed(["board", "from-netlist", "--netlist", netlist, "--out", str(board)])
    assert built["ok"] is True, built
    assert built["data"]["footprints"] == 2

    routed = confirmed(["board", "route", "--board", str(board), "--mode", "repair"])
    assert routed["ok"] is True, routed
    assert routed["data"]["unconnected_after"] <= routed["data"]["unconnected_before"]

    fab = tmp_path / "fab"
    plotted = confirmed(["fab", "gerber", "--board", str(board), "--out", str(fab)])
    assert plotted["ok"] is True, plotted
    assert list(fab.glob("*.gbr")), "no Gerbers at the end of the chain"
