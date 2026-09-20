"""The second routing engine, and the line we do not cross to get it.

The grid router does not push and shove: nets are routed one at a time against
copper it treats as immovable, so a connection needing an existing track to
move aside is not found. Freerouting does push and shove. On the 15-part board
measured here both engines came out fully connected and fabricable, and
Freerouting used 172 mm of copper against 178, with 11 vias against 23.

Freerouting is GPL-3.0 and this project is MIT, so it is not redistributed --
exactly as KiCad, also GPL-3.0, is not. `NOTICE.md` states that position and
this is its second application: find what the user installed, call it at arm's
length over the command line, ship none of its bytes.

Most of this needs neither KiCad nor Freerouting, because discovery is a
question about the filesystem and the log parsing is a question about text.
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

import freerouting as F  # noqa: E402
import pcb_freeroute as FR  # noqa: E402

needs_kicad = pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
needs_engine = pytest.mark.skipif(
    not F.status()["usable"], reason="Freerouting is optional and not configured here"
)

NETLIST = """(export (version "E")
  (components
    (comp (ref "R1") (value "10k") (footprint "Resistor_SMD:R_0805_2012Metric"))
    (comp (ref "R2") (value "10k") (footprint "Resistor_SMD:R_0805_2012Metric"))
    (comp (ref "R3") (value "10k") (footprint "Resistor_SMD:R_0805_2012Metric")))
  (nets
    (net (code 1) (name "GND")
      (node (ref "R1") (pin "2"))
      (node (ref "R2") (pin "2"))
      (node (ref "R3") (pin "2")))
    (net (code 2) (name "SIG")
      (node (ref "R1") (pin "1"))
      (node (ref "R2") (pin "1")))
    (net (code 3) (name "SIG2")
      (node (ref "R2") (pin "1"))
      (node (ref "R3") (pin "1")))))
"""


# --- discovery ----------------------------------------------------------------


def test_an_explicit_jar_is_honoured(tmp_path, monkeypatch):
    jar = tmp_path / "freerouting-2.4.1.jar"
    jar.write_bytes(b"not really a jar")
    monkeypatch.setenv(F.ENV_JAR, str(jar))
    assert F.find_jar() == str(jar.resolve())


def test_an_explicit_jar_that_is_not_there_is_not_silently_replaced(tmp_path, monkeypatch):
    """Falling back to some other jar would run a program the caller did not
    name. An invalid override is an error to surface, not a hint to improve on."""
    monkeypatch.setenv(F.ENV_JAR, str(tmp_path / "absent.jar"))
    assert F.find_jar() is None


def test_an_explicit_java_is_honoured(tmp_path, monkeypatch):
    java = tmp_path / "java.exe"
    java.write_bytes(b"")
    monkeypatch.setenv(F.ENV_JAVA, str(java))
    assert F.find_java() == str(java.resolve())


def test_status_says_what_to_install_when_there_is_no_jar(tmp_path, monkeypatch):
    monkeypatch.setenv(F.ENV_JAR, str(tmp_path / "absent.jar"))
    state = F.status()
    assert state["usable"] is False
    assert F.ENV_JAR in state["reason"]


def test_status_names_the_missing_java_rather_than_the_jar(tmp_path, monkeypatch):
    """Two different missing things need two different sentences."""
    jar = tmp_path / "freerouting.jar"
    jar.write_bytes(b"")
    monkeypatch.setenv(F.ENV_JAR, str(jar))
    monkeypatch.setenv(F.ENV_JAVA, str(tmp_path / "no-java"))
    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.setattr(F.shutil, "which", lambda _name: None)
    state = F.status()
    assert state["usable"] is False
    assert "java" in state["reason"].lower()


@needs_engine
def test_asking_the_version_does_not_litter_the_caller_directory(tmp_path, monkeypatch):
    """Freerouting writes its log to <cwd>/<language>/freerouting.log, so a
    bare `--help` grows an `en/` folder wherever it was run from. `doctor`
    calls this on every invocation; it did that in the repository root and the
    folder was very nearly committed."""
    monkeypatch.chdir(tmp_path)
    state = F.status()
    assert state["version"], state
    assert sorted(p.name for p in tmp_path.iterdir()) == [], "the probe left files behind"


# --- reading what Freerouting said --------------------------------------------


def test_the_final_score_line_is_read_for_what_was_left_unrouted():
    line = (
        "2026-09-20 17:41:00.634 INFO  Auto-routing stage completed: started with 34 "
        "unrouted nets, completed in 1.82 seconds, final score: 999.99 (0 unrouted and "
        "0 violations), using 0.89 total CPU seconds."
    )
    assert FR.summarize(line) == (0, 0)


def test_an_incomplete_route_is_reported_as_the_number_it_left():
    line = "final score: 972.22 (1 unrouted and 3 violations), using 0.89 CPU seconds"
    assert FR.summarize(line) == (1, 3)


def test_output_with_no_score_line_reports_nothing_rather_than_zero():
    """Zero unrouted is a claim. Not knowing is a different claim, and saying
    the first when the second is true would read as a clean route."""
    assert FR.summarize("Freerouting v2.4.1\nSaving 'board.ses'...") == (None, None)


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
    confirmed(["board", "place", "--board", str(board)])
    return board


def test_an_unconfigured_engine_says_what_to_install(tmp_path, monkeypatch):
    """And names the fallback. An agent that cannot route is stuck; an agent
    told `--engine grid` needs no external program is not."""
    env = dict(os.environ)
    env[F.ENV_JAR] = str(tmp_path / "absent.jar")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kicad_cli.main",
            "board",
            "route",
            "--board",
            str(tmp_path / "nope.kicad_pcb"),
            "--engine",
            "freerouting",
            "--dry-run",
            "--compact",
        ],
        capture_output=True,
        cwd=REPO,
        env=env,
        timeout=600,
    )
    doc = json.loads(
        next(
            line
            for line in proc.stdout.decode("utf-8", "replace").splitlines()
            if line.startswith("{")
        )
    )
    assert doc["error"]["code"] == "E_CONFIG", doc
    details = json.dumps(doc["error"]["details"], ensure_ascii=False)
    assert F.ENV_JAR in details
    assert "--engine grid" in details


@needs_kicad
def test_the_default_engine_is_the_one_that_needs_nothing_installed(tmp_path):
    board = build(tmp_path)
    doc = confirmed(["board", "route", "--board", str(board), "--mode", "repair"])
    assert doc["ok"] is True, doc
    assert doc["data"]["engine"] == "grid", doc["data"]


@needs_kicad
@needs_engine
def test_freerouting_routes_the_board_and_reports_its_own_verdict_separately(tmp_path):
    """`freerouting_violations` is its clearance model; `verify.errors_final`
    is KiCad's. They disagree -- it reported 0 violations on a board KiCad
    found 22 width errors on -- so both are reported and KiCad's is the one
    the rollback is judged against."""
    board = build(tmp_path)
    doc = confirmed(["board", "route", "--board", str(board), "--engine", "freerouting"])
    assert doc["ok"] is True, doc
    data = doc["data"]
    assert data["engine"] == "freerouting"
    assert data["unconnected_after"] == 0, data
    assert data["verify"]["errors_final"] <= data["verify"]["errors_baseline"]
    assert "freerouting_violations" in data


@needs_kicad
@needs_engine
def test_fanout_necking_is_brought_back_to_the_board_minimum(tmp_path):
    """Freerouting necks its fanout stubs below this board's own minimum track
    width, which KiCad DRC reports as track_width errors -- 22 of them on the
    ATmega board. They are widened back to the board's minimum, and the count
    is reported rather than absorbed."""
    board = build(tmp_path)
    doc = confirmed(["board", "route", "--board", str(board), "--engine", "freerouting"])
    data = doc["data"]
    assert data["min_track_width_mm"] > 0
    assert isinstance(data["necked_tracks_widened"], int)

    drc, _ = run(["board", "drc", "--board", str(board)])
    assert drc["data"]["counts"].get("error", 0) == 0, drc["data"]["violations"]
    assert drc["data"]["unconnected_count"] == 0


# --- layer policy --------------------------------------------------------------


@needs_kicad
@needs_engine
def test_plane_first_keeps_signals_off_the_ground_plane(tmp_path):
    """Best practice on a two-layer board is signals on top and the bottom left
    as solid a ground plane as possible. Freerouting has no layer-preference
    switch -- `ScoringSettings.preferredDirectionTraceCost` and
    `RouterSettings.layers` are both `transient`, so neither the JSON config nor
    a `-dr` rules file reaches them, which four byte-identical runs confirmed.

    Via cost is the lever that works, through the environment-variable settings
    source. Measured on this board, same placement and pour:

        balanced      21.5% of copper on B.Cu, 11 vias, backed_fraction 0.73
        plane-first    5.1% of copper on B.Cu,  7 vias, backed_fraction 0.87
    """
    board = build(tmp_path)
    confirmed(["board", "pour", "--board", str(board), "--net", "GND", "--layer", "B.Cu"])
    doc = confirmed(
        [
            "board",
            "route",
            "--board",
            str(board),
            "--engine",
            "freerouting",
            "--layer-policy",
            "plane-first",
        ]
    )
    assert doc["ok"] is True, doc
    data = doc["data"]
    assert data["unconnected_after"] == 0, data
    by_layer = data["copper_by_layer_mm"]
    total = sum(by_layer.values())
    assert total > 0 and by_layer.get("B.Cu", 0) / total < 0.5, by_layer


@needs_kicad
@needs_engine
def test_plane_first_falls_back_rather_than_leaving_a_net_open(tmp_path):
    """It trades routability for layer purity, and that trade is not always
    worth it: on this board with the ground pour removed, plane-first leaves a
    connection unrouted. A board short one connection cannot be built, however
    clean its layer split, so it re-routes with `balanced` and says so -- the
    same shape as `sch create` falling back to auto-stubbing.
    """
    board = build(tmp_path)  # deliberately no `board pour`
    doc = confirmed(
        [
            "board",
            "route",
            "--board",
            str(board),
            "--engine",
            "freerouting",
            "--layer-policy",
            "plane-first",
        ]
    )
    assert doc["ok"] is True, doc
    data = doc["data"]
    assert data["layer_policy"] == "plane-first"
    assert data["unconnected_after"] == 0, "a fallback that still leaves nets open is no fallback"
    if data["layer_policy_applied"] == "balanced":
        assert data["layer_policy_fallback"]["to"] == "balanced"
        assert "plane-first" in doc["data"]["note"]


@needs_kicad
@needs_engine
def test_balanced_on_a_poured_board_says_what_plane_first_would_buy(tmp_path):
    """The knowledge is measured and would otherwise be undiscoverable."""
    board = build(tmp_path)
    confirmed(["board", "pour", "--board", str(board), "--net", "GND", "--layer", "B.Cu"])
    doc = confirmed(["board", "route", "--board", str(board), "--engine", "freerouting"])
    assert doc["data"]["layer_policy_applied"] == "balanced"
    assert "plane-first" in doc["data"]["note"]


@needs_kicad
def test_audit_reports_copper_per_layer(tmp_path):
    """The total cannot show it: 200 mm all on top and 200 mm half on the
    bottom are two different boards, and only one of them has a plane left."""
    board = build(tmp_path)
    confirmed(["board", "route", "--board", str(board), "--mode", "repair"])
    doc, _ = run(["board", "audit", "--board", str(board)])
    by_layer = doc["data"]["copper_by_layer_mm"]
    assert by_layer, "a routed board has copper on at least one layer"
    assert abs(sum(by_layer.values()) - doc["data"]["copper_mm"]) < 1.0, by_layer
