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

import hashlib
import json
from pathlib import Path

import pytest
from driver_test_utils import domain_receipt
from kicad_demos import DEMOS, SKIP_REASON
from public_test_cli import engineering_trial, invoke, planned, saved_path
from public_test_cli import public_cli_scope as public_cli_scope

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
    value, code = invoke(argv)
    return domain_receipt(value), code


def write_netlist(directory: Path, text: str = NETLIST) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "circuit.net"
    path.write_text(text, encoding="utf-8")
    return path


def confirmed(argv: list[str]) -> dict:
    plan, _ = run([*argv, "--dry-run"])
    planned(plan)
    doc, _ = run([*argv, "--confirm", plan["data"]["confirm_token"]])
    return doc


@needs_kicad
def test_a_dry_run_plans_the_board_and_writes_nothing(tmp_path):
    netlist = write_netlist(tmp_path)
    board = tmp_path / "out.kicad_pcb"
    doc, _ = run(
        ["board", "from-netlist", "--netlist", str(netlist), "--out", str(board), "--dry-run"]
    )
    preview = engineering_trial(doc)
    assert preview["components"] == ["R1", "R2"]
    assert preview["pads_connected"] == 4
    assert not board.exists(), "a dry run must not create the board"


@needs_kicad
def test_a_built_board_carries_every_footprint_and_every_connection(tmp_path):
    netlist = write_netlist(tmp_path)
    board = tmp_path / "out.kicad_pcb"
    doc = confirmed(["board", "from-netlist", "--netlist", str(netlist), "--out", str(board)])
    assert doc["ok"] is True, doc
    data = doc["data"]
    assert data["components"] == ["R1", "R2"]
    # Four pads across two resistors, and the netlist names all four.
    assert data["pads_connected"] == 4
    assert board.is_file()
    assert saved_path(doc, ".kicad_pcb") == board
    from kicad_cli.fileformat.board import Board

    actual = Board.load(board)
    assert len(actual.footprints) == 2

    # Our own reader agrees, which is the check that matters more than the
    # counts the writer reports about itself.
    audit, _ = run(["board", "analyze", "ampacity", "--board", str(board)])
    assert audit["ok"] is True, audit
    observation = audit["data"]["observation"]
    assert observation["state"] == "saved_files" and observation["input_drift_checked"]
    assert observation["snapshot"]["revision"] == hashlib.sha256(board.read_bytes()).hexdigest()
    assert observation["source"]["official_native"] is False


@needs_kicad
def test_a_node_the_footprint_has_no_pad_for_is_reported_rather_than_dropped(tmp_path):
    """Silently skipping it would deliver a board missing a connection.

    A netlist naming a missing footprint pad is refused before the primary
    board exists. The fixed driver must not save a silently incomplete board.
    """
    text = NETLIST.replace('(node (ref "R2") (pin "2"))', '(node (ref "R2") (pin "9"))')
    netlist = write_netlist(tmp_path, text)
    board = tmp_path / "out.kicad_pcb"
    before = netlist.read_bytes()
    doc, code = run(
        ["board", "from-netlist", "--netlist", str(netlist), "--out", str(board), "--dry-run"]
    )
    assert code == 2 and doc["error"]["code"] == "E_VALIDATION", doc
    assert doc["error"]["details"]["dispatch_state"] == "not_started"
    assert not board.exists() and netlist.read_bytes() == before


@needs_kicad
def test_a_footprint_that_is_not_installed_fails_by_name(tmp_path):
    """This one does need KiCad: without it there are no libraries to be absent from."""
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


def test_a_component_with_no_footprint_is_named(tmp_path, monkeypatch):
    """And this one does not: the netlist named nothing, which is not a KiCad question.

    Pointing the installation lookup at nothing proves the judgement did not
    consult it -- otherwise a machine without KiCad is told to install one when
    the real problem is in the file it was handed.
    """
    monkeypatch.setenv("KICAD_CLI_ROOT", str(tmp_path / "no-kicad-here"))
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
    netlist = str(saved_path(created, ".net"))

    board = tmp_path / "divider.kicad_pcb"
    built = confirmed(["board", "from-netlist", "--netlist", netlist, "--out", str(board)])
    assert built["ok"] is True, built
    assert built["data"]["components"] == ["R1", "R2"]

    before, _ = run(["board", "check", "--board", str(board)])
    routed = confirmed(["board", "route", "repair", "--board", str(board)])
    assert routed["ok"] is True, routed
    after, _ = run(["board", "check", "--board", str(board)])
    assert after["data"]["unconnected_count"] <= before["data"]["unconnected_count"]

    fab = tmp_path / "fab"
    plotted = confirmed(["fab", "gerber", "--board", str(board), "--out", str(fab)])
    assert plotted["ok"] is True, plotted
    assert list(fab.glob("*.gbr")), "no Gerbers at the end of the chain"
