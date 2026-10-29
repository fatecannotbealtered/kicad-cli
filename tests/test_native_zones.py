"""The separate zone commands must produce files the real SDK can load.

Default regression skips this consumer without an explicitly selected SDK.
This tests saved files, including the intermediate unfilled state; observing
only a later refill would conceal an invalid create output.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from driver_test_utils import grant
from test_native_pour import BOARD

REPO = Path(__file__).resolve().parents[1]
SDK_PYTHON = os.environ.get("KICAD_CLI_PYTHON")


def cli(argv):
    from native_process import directory, run

    record, stdout, _ = run(
        [sys.executable, "-B", "-m", "kicad_cli.main", *argv, "--compact"],
        directory("zones-public"),
        cwd=REPO,
        timeout=60,
        role="source-public-zone",
    )
    assert record["exit"] == 0, record
    return json.loads(stdout)


def apply(argv):
    asked = cli([*argv, "--dry-run"])
    assert asked["ok"], asked
    done = cli([*argv, "--confirm", asked["data"]["confirm_token"]])
    assert done["ok"], done
    return done


def read_native(board):
    from native_process import directory, run

    script = (
        "import json,sys,wx; wx.Log.SetActiveTarget(wx.LogStderr()); import pcbnew; "
        "b=pcbnew.LoadBoard(sys.argv[1]); "
        "assert b is not None, 'Native LoadBoard rejected the saved file'; "
        "print(json.dumps({'version':pcbnew.GetBuildVersion(), 'zones': "
        "[{'uuid':z.m_Uuid.AsString(), 'net':z.GetNetname(), 'filled':z.IsFilled(), "
        "'polygon_count':sum(z.GetFilledPolysList(l).OutlineCount() "
        "for l in z.GetLayerSet().Seq())} for z in b.Zones()]}))"
    )
    record, stdout, _ = run(
        [SDK_PYTHON, "-I", "-B", "-c", script, str(board)],
        directory("zones-native-read"),
        cwd=board.parent,
        timeout=60,
    )
    assert record["exit"] == 0, record
    return json.loads(stdout)


@pytest.mark.skipif(not SDK_PYTHON, reason="No explicitly selected KiCad SDK interpreter")
def test_native_reads_both_unfilled_create_and_selected_refill(tmp_path, monkeypatch):
    monkeypatch.setenv("KICAD_CLI_STATE", str(tmp_path / "state"))
    for key, value in grant(tmp_path).items():
        monkeypatch.setenv(key, value)
    board = tmp_path / "zones.kicad_pcb"
    board.write_text(BOARD, encoding="utf-8")
    created = apply(
        ["board", "zone", "create", "--board", str(board), "--net", "GND", "--layer", "F.Cu"]
    )
    actual = read_native(board)["zones"]
    identity = created["data"]["receipt"]["extensions"]["observed"][0]["id"]
    assert actual == [{"uuid": identity, "net": "GND", "filled": False, "polygon_count": 0}]
    apply(["board", "zone", "refill", "--board", str(board), "--zones", identity])
    filled = read_native(board)["zones"]
    assert len(filled) == 1 and filled[0]["uuid"] == identity
    assert filled[0]["net"] == "GND" and filled[0]["filled"]
    assert filled[0]["polygon_count"] > 0
