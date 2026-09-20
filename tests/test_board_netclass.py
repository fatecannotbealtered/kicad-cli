"""Giving a net a target width, and the commands that were waiting for one.

`board from-netlist` makes a board whose only netclass is Default at 0.20 mm --
about 0.74 A on 1 oz copper at a 10 C rise. Fine for a signal, not for a
supply. `board rewidth` and `board widen` both work *from* a netclass, and
nothing could make one, so `board audit` reported an error against a board this
tool had just produced and no command in the tool could clear it.

Netclasses live in the project file, not the board, so most of this is a JSON
edit and is tested as one.
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
sys.path.insert(0, str(REPO / "kicad_cli" / "payload"))

import netclass as N  # noqa: E402

needs_kicad = pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)

NETLIST = """(export (version "E")
  (components
    (comp (ref "R1") (value "10k") (footprint "Resistor_SMD:R_0805_2012Metric"))
    (comp (ref "R2") (value "10k") (footprint "Resistor_SMD:R_0805_2012Metric")))
  (nets
    (net (code 1) (name "VIN")
      (node (ref "R1") (pin "1")))
    (net (code 2) (name "MID")
      (node (ref "R1") (pin "2"))
      (node (ref "R2") (pin "1")))))
"""

DEFAULT_CLASS = {
    "name": "Default",
    "track_width": 0.2,
    "clearance": 0.2,
    "via_diameter": 0.6,
    "via_drill": 0.3,
    "bus_width": 12,
}


def project(classes=None, patterns=None):
    return {
        "net_settings": {
            "classes": list(classes if classes is not None else [dict(DEFAULT_CLASS)]),
            "netclass_patterns": list(patterns or []),
        }
    }


# --- the project edit ---------------------------------------------------------


def test_a_new_class_inherits_the_fields_it_was_not_given():
    """KiCad falls back to its own built-ins for a field a class omits, which
    would not match the numbers this command just reported back."""
    pro = project()
    action, assigned, already = N.apply(pro, "Power", {"track_width": 0.6}, ["VIN"])
    assert (action, assigned, already) == ("created", ["VIN"], [])
    power = next(c for c in pro["net_settings"]["classes"] if c["name"] == "Power")
    assert power["track_width"] == 0.6
    assert power["clearance"] == 0.2  # from Default
    assert power["bus_width"] == 12  # and everything else Default carried


def test_an_existing_class_is_updated_not_duplicated():
    pro = project([dict(DEFAULT_CLASS), {"name": "Power", "track_width": 0.4}])
    action, _assigned, _already = N.apply(pro, "Power", {"track_width": 0.8}, [])
    assert action == "updated"
    powers = [c for c in pro["net_settings"]["classes"] if c["name"] == "Power"]
    assert len(powers) == 1 and powers[0]["track_width"] == 0.8


def test_a_net_is_matched_by_its_exact_name():
    """`+3V3*` would also catch `+3V3_SENSE`, which the caller did not ask for."""
    pro = project()
    N.apply(pro, "Power", {"track_width": 0.6}, ["+3V3"])
    assert pro["net_settings"]["netclass_patterns"] == [{"pattern": "+3V3", "netclass": "Power"}]


def test_assigning_the_same_net_twice_is_not_a_second_pattern():
    pro = project()
    N.apply(pro, "Power", {"track_width": 0.6}, ["VIN"])
    _action, assigned, already = N.apply(pro, "Power", {"track_width": 0.6}, ["VIN"])
    assert (assigned, already) == ([], ["VIN"])
    assert len(pro["net_settings"]["netclass_patterns"]) == 1


def test_moving_a_net_to_another_class_rewrites_its_pattern():
    pro = project([dict(DEFAULT_CLASS), {"name": "Power", "track_width": 0.6}])
    N.apply(pro, "Power", {"track_width": 0.6}, ["VIN"])
    _action, assigned, _already = N.apply(pro, "HighCurrent", {"track_width": 1.2}, ["VIN"])
    assert assigned == ["VIN"]
    patterns = pro["net_settings"]["netclass_patterns"]
    assert patterns == [{"pattern": "VIN", "netclass": "HighCurrent"}]


def test_a_project_with_no_net_settings_at_all_still_works():
    pro = {}
    action, assigned, _already = N.apply(pro, "Power", {"track_width": 0.6}, ["VIN"])
    assert action == "created" and assigned == ["VIN"]
    assert pro["net_settings"]["classes"][0]["name"] == "Power"


# --- the number it quotes -----------------------------------------------------


def test_a_wider_trace_carries_more_current():
    assert N.ampacity(0.6) > N.ampacity(0.2)


def test_the_default_width_is_the_value_the_audit_reports():
    """0.74 A at 0.20 mm is what `board audit` prints for the Default class.
    Two different numbers for one trace would make the pair useless."""
    assert N.ampacity(0.2) == pytest.approx(0.74, abs=0.01)


# --- end to end ---------------------------------------------------------------


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
def test_a_class_lands_in_the_project_where_the_other_commands_look(tmp_path):
    board = build(tmp_path)
    doc = confirmed(
        [
            "board",
            "netclass",
            "--board",
            str(board),
            "--name",
            "Power",
            "--width",
            "0.6",
            "--nets",
            "VIN",
        ]
    )
    assert doc["ok"] is True, doc
    assert doc["data"]["action"] == "created"
    assert doc["data"]["assigned"] == ["VIN"]

    pro = json.loads(Path(str(board).replace(".kicad_pcb", ".kicad_pro")).read_text("utf-8"))
    names = [c["name"] for c in pro["net_settings"]["classes"]]
    assert "Power" in names
    assert {"pattern": "VIN", "netclass": "Power"} in pro["net_settings"]["netclass_patterns"]


@needs_kicad
def test_the_audit_starts_measuring_against_the_new_target(tmp_path):
    """The point of the command: `board audit` could see the board was wrong
    and nothing in the tool could tell it what right looked like.

    MID rather than VIN, because width compliance is weighted by routed length
    and VIN has a single pad -- no copper, so no class row to read.
    """
    board = build(tmp_path)
    confirmed(["board", "route", "--board", str(board), "--mode", "repair"])
    before, _ = run(["board", "audit", "--board", str(board)])
    assert [c["class"] for c in before["data"]["width_compliance"]] == ["Default"]

    confirmed(
        [
            "board",
            "netclass",
            "--board",
            str(board),
            "--name",
            "Power",
            "--width",
            "0.6",
            "--nets",
            "MID",
        ]
    )
    after, _ = run(["board", "audit", "--board", str(board)])
    classes = {c["class"]: c for c in after["data"]["width_compliance"]}
    assert "Power" in classes, classes
    assert classes["Power"]["target_mm"] == 0.6
    # The copper has not moved, so it should say so rather than flatter itself.
    assert classes["Power"]["compliant_pct"] < 100


@needs_kicad
def test_a_dry_run_changes_no_project(tmp_path):
    board = build(tmp_path)
    project_file = Path(str(board).replace(".kicad_pcb", ".kicad_pro"))
    before = project_file.read_bytes()
    doc, _ = run(
        [
            "board",
            "netclass",
            "--board",
            str(board),
            "--name",
            "Power",
            "--width",
            "0.6",
            "--nets",
            "VIN",
            "--dry-run",
        ]
    )
    assert doc["error"]["code"] == "E_CONFIRMATION_REQUIRED", doc
    assert doc["error"]["details"]["preview"]["ampacity_a"] > 0
    assert project_file.read_bytes() == before


@needs_kicad
def test_rewidth_refuses_a_class_that_is_not_in_the_project(tmp_path):
    """It used to default to PWR_MAIN,BTL_OUT,SWITCH -- three class names from
    the one amplifier board this tool was first written for. On any other
    board none of them exist, so rewidth did nothing and returned ok."""
    board = build(tmp_path)
    doc, code = run(
        ["board", "rewidth", "--board", str(board), "--classes", "PWR_MAIN", "--dry-run"]
    )
    assert doc["error"]["code"] == "E_NOT_FOUND", doc
    assert "PWR_MAIN" in json.dumps(doc["error"], ensure_ascii=False)
    assert code != 0


@needs_kicad
def test_rewidth_says_so_when_there_is_no_class_to_work_from(tmp_path):
    """A board straight out of `board from-netlist` has only Default. Doing
    nothing quietly is what made this invisible in the first place."""
    board = build(tmp_path)
    doc, _ = run(["board", "rewidth", "--board", str(board), "--dry-run"])
    assert doc["error"]["code"] == "E_VALIDATION", doc
    assert "board netclass" in json.dumps(doc["error"], ensure_ascii=False)


@needs_kicad
def test_rewidth_leaves_no_write_transaction_behind(tmp_path):
    """Each net is re-routed in its own subprocess, and each one left a
    journal, a backup and a lock: the worker exits through os._exit, which
    skips the commit. The next worker read that as an unfinished write and
    refused, so a two-net class could never finish. The lock also wedged the
    board against any later write until it went stale.
    """
    board = build(tmp_path)
    confirmed(["board", "route", "--board", str(board), "--mode", "repair"])
    confirmed(
        [
            "board",
            "netclass",
            "--board",
            str(board),
            "--name",
            "Power",
            "--width",
            "0.4",
            "--nets",
            "VIN,MID",
        ]
    )
    doc = confirmed(["board", "rewidth", "--board", str(board)])
    assert doc["ok"] is True, doc

    debris = sorted(p.name for p in board.parent.iterdir() if "kicad-cli" in p.name)
    assert debris == [], debris
    # And the board is still writable afterwards, which the stale lock prevented.
    again, _ = run(["board", "rewidth", "--board", str(board), "--dry-run"])
    assert again["error"]["code"] == "E_CONFIRMATION_REQUIRED", again
