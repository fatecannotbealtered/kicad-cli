"""Offline refusal boundaries: no KiCad, Java or existing project is invoked."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_native_freeroute import engine

from kicad_cli.fileformat import specctra
from kicad_cli.native import freeroute


class Refused(Exception):
    def __init__(self, code, details):
        self.code, self.details = code, details


@pytest.fixture
def refusal(monkeypatch):
    def fail(code, _message, details=None):
        raise Refused(code, details or {})

    monkeypatch.setattr(freeroute.envelope, "fail", fail)


def test_a_retry_cannot_reuse_the_previous_session(tmp_path, monkeypatch, refusal):
    session = tmp_path / "board.ses"
    session.write_text("the previous attempt", encoding="utf-8")

    def run(*_args, **_kwargs):
        assert not session.exists()
        return subprocess.CompletedProcess([], 0, b"", b"")

    monkeypatch.setattr(freeroute.subprocess, "run", run)
    with pytest.raises(Refused) as caught:
        freeroute.run_freerouting(
            "unused-java", "unused.jar", "board.dsn", str(session), 1, 1, str(tmp_path), "balanced"
        )
    assert caught.value.code == "E_IO"
    assert not session.exists()


def test_a_failed_process_cannot_supply_a_session(tmp_path, monkeypatch, refusal):
    session = tmp_path / "board.ses"

    def run(*_args, **_kwargs):
        session.write_text("(session x (routes (network_out)))", encoding="utf-8")
        return subprocess.CompletedProcess([], 7, b"partial result", b"failed")

    monkeypatch.setattr(freeroute.subprocess, "run", run)
    with pytest.raises(Refused) as caught:
        freeroute.run_freerouting(
            "unused-java", "unused.jar", "board.dsn", str(session), 1, 1, str(tmp_path), "balanced"
        )
    assert caught.value.code == "E_IO"
    assert caught.value.details["exit_code"] == 7


@pytest.mark.parametrize(
    "text",
    [
        "(session x)",
        "(session x (routes))",
        "(session x (routes (resolution um 0) (network_out)))",
        "(session x (routes (resolution nonsense 10) (network_out)))",
        "(session x (routes (resolution um NaN) (network_out)))",
        "(session x (routes (network_out (net GND (wire (path F.Cu 2000 0 0 10))))))",
        "(session x (routes (network_out (net GND (wire (path F.Cu 2000 0 0 NaN 10))))))",
        "(session x (routes (network_out (net GND (via x 10)))))",
    ],
)
def test_malformed_results_are_not_silent_empty_sessions(text):
    with pytest.raises(specctra.SessionError):
        specctra.read_session(text)


@pytest.mark.parametrize(
    "wire",
    [
        ("another-board-net", "F.Cu", 200_000, [(0, 0), (1_000_000, 0)]),
        ("GND", "not-exported", 200_000, [(0, 0), (1_000_000, 0)]),
        ("GND", "F.Cu", 0, [(0, 0), (1_000_000, 0)]),
    ],
)
def test_an_invalid_session_is_refused_before_copper_is_taken_up(wire):
    original = object()
    removed, added = [], []
    layout = SimpleNamespace(
        pieces=[original],
        find=lambda name: {"GND": 1}.get(name),
        remove=removed.append,
        add=added.append,
    )
    session = specctra.Session(wires=[wire])
    with pytest.raises(specctra.SessionError):
        freeroute.apply_session(layout, session, {"F.Cu": "F.Cu"})
    assert layout.pieces == [original]
    assert removed == added == []


def test_a_concurrent_edit_is_preserved_by_the_real_command(tmp_path):
    """No KiCad dependency: the router stand-in edits this new scratch board."""
    board = tmp_path / "board.kicad_pcb"
    board.write_text(
        '(kicad_pcb (version 20250131) (generator "offline-test") '
        "(general (thickness 1.6)) "
        '(layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user)) '
        '(setup) (net 0 "") (net 1 "GND") '
        "(gr_rect (start 0 0) (end 10 10) (stroke (width 0.05) (type solid)) "
        '(fill none) (layer "Edge.Cuts")) '
        '(segment (start 2 2) (end 3 3) (width 0.2) (layer "F.Cu") (net 1)))',
        encoding="utf-8",
    )
    board.with_suffix(".kicad_pro").write_text("{}", encoding="utf-8")
    original = board.read_bytes()
    session = tmp_path / "back.ses"
    session.write_text(
        "(session x (routes (resolution um 10) (network_out "
        "(net GND (wire (path F.Cu 2000 20000 -20000 30000 -30000))))))",
        encoding="utf-8",
    )
    env = engine(tmp_path, session)
    from driver_test_utils import grant

    env.update(grant(tmp_path))
    env["FAKE_FREEROUTING_CHANGE_BOARD"] = str(board)
    argv = [
        sys.executable,
        "-m",
        "kicad_cli.main",
        "board",
        "route",
        "rebuild",
        "--board",
        str(board),
        "--engine",
        "freerouting",
        "--compact",
    ]
    repo = Path(__file__).resolve().parents[1]
    asked = subprocess.run([*argv, "--dry-run"], env=env, cwd=repo, capture_output=True, timeout=60)
    preview = json.loads(asked.stdout.decode("utf-8"))
    assert preview["error"]["code"] == "E_CONFLICT", preview
    assert preview["error"]["details"]["dispatch_state"] == "not_started"
    assert board.read_bytes() == original + b"\n"
    for suffix in (".kicad-cli.lock", ".kicad-cli.journal", ".kicad-cli.backup"):
        assert not Path(str(board) + suffix).exists()
