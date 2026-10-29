"""Canonical task boundaries and actual zone file effects, not native acceptance."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from driver_test_utils import grant, grant_once
from test_native_pour import BOARD

from kicad_cli import arguments, main, registry
from kicad_cli.fileformat.board import Board
from kicad_cli.fileformat.sexpr import Document
from kicad_cli.native import zones
from kicad_cli.zone_driver_adapter import ZoneAdapter

REPO = Path(__file__).resolve().parents[1]


def call(argv, tmp_path):
    env = dict(
        os.environ,
        **grant_once(tmp_path),
        KICAD_CLI_STATE=str(tmp_path / "state"),
        PYTHONDONTWRITEBYTECODE="1",
    )
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "kicad_cli.main", *argv, "--compact"],
        cwd=REPO,
        env=env,
        capture_output=True,
        timeout=30,
    )
    return proc.returncode, json.loads(proc.stdout)


def confirmed(argv, tmp_path):
    code, asked = call([*argv, "--dry-run"], tmp_path)
    assert code == 0 and asked["ok"], asked
    token = asked["data"]["confirm_token"]
    code, done = call([*argv, "--confirm", token], tmp_path)
    assert code == 0 and done["ok"], done
    return done


@pytest.fixture
def board(tmp_path):
    target = tmp_path / "zones.kicad_pcb"
    target.write_text(BOARD, encoding="utf-8")
    return target


def creation(board):
    return ["board", "zone", "create", "--board", str(board), "--net", "GND", "--layer", "F.Cu"]


def test_discovery_help_and_removed_paths(tmp_path):
    _, result = call(["reference"], tmp_path)
    paths = {c["path"] for c in result["data"]["commands"]}
    from kicad_cli.driver_runtime import MIGRATED, declarations

    declared = {c["path"] for c in declarations()["reference"]["commands"]}
    assert len(paths) == 43
    assert paths == declared == MIGRATED
    assert "sch objects" in paths
    assert "board zone refill" in paths and "project sync" in paths
    assert not paths.intersection(registry.COMMAND_MIGRATIONS)
    assert "fab package" not in paths
    assert (
        result["data"]["compatibility"]["runtime_contract"]["driver_2_0_conformance"]
        == "not_claimed"
    )
    help_text = subprocess.check_output(
        [sys.executable, "-B", "-m", "kicad_cli.main", "--help"], cwd=REPO
    ).decode()
    assert all("kicad-cli " + path in help_text for path in paths)


def test_discovery_examples_have_complete_accepted_arguments():
    """Examples are executable discovery: missing required files must be caught."""
    commands = registry.build()
    for command in commands:
        for example in command["examples"]:
            argv = shlex.split(example)
            assert argv.pop(0) == "kicad-cli"
            selected, _ = arguments.parse(argv, commands, registry.GLOBAL_OPTIONS)
            assert selected["path"] == command["path"], example


@pytest.mark.parametrize(
    "argv",
    [
        ["board", "route", "repair", "--engine", "freerouting"],
        ["board", "route", "repair", "--mode", "full"],
        ["board", "route", "rebuild", "--ripup"],
        ["board", "route", "rewidth"],
        ["board", "zone", "refill"],
        ["board", "zone", "refill", "--all=false"],
    ],
)
def test_invalid_task_combinations_never_touch_board(argv, board, tmp_path):
    before = board.read_bytes()
    code, result = call([*argv, "--board", str(board), "--dry-run"], tmp_path)
    assert code == 2
    expected = "E_VALIDATION" if argv[-1] == "freerouting" else "E_USAGE"
    assert result["error"]["code"] == expected
    assert board.read_bytes() == before


def test_create_does_not_fill_and_refill_does_not_create(board, tmp_path):
    before = board.read_bytes()
    _, preview = call([*creation(board), "--dry-run"], tmp_path)
    assert board.read_bytes() == before
    assert preview["data"]["preview"]["effects"] == ["change", "persist"]
    created = confirmed(creation(board), tmp_path)
    loaded = Board.load(board)
    assert len(loaded.zones) == 1 and loaded.zones[0].filled == {}
    assert created["data"]["receipt"]["extensions"]["observed"][0]["filled_polygons"] == 0
    identity = created["data"]["receipt"]["extensions"]["observed"][0]["id"]
    definition = zones._definition(loaded.zones[0])
    protected = zones._protected(loaded, {identity})
    refill = confirmed(
        ["board", "zone", "refill", "--board", str(board), "--zones", identity], tmp_path
    )
    observed = Board.load(board)
    assert len(observed.zones) == 1 and observed.zones[0].filled["F.Cu"]
    assert zones._definition(observed.zones[0]) == definition
    assert zones._protected(observed, {identity}) == protected
    receipt = refill["data"]["receipt"]
    assert receipt["snapshot_after"]["revision"] == hashlib.sha256(board.read_bytes()).hexdigest()
    assert receipt["extensions"]["finding_comparison"]["status"] == "passed"
    assert receipt["extensions"]["readback"]["source"]["name"] == "kicad_cli.zone_readback"


def test_duplicate_creation_and_unknown_selection_preserve_bytes(board, tmp_path):
    confirmed(creation(board), tmp_path)
    before = board.read_bytes()
    _, duplicate = call([*creation(board), "--dry-run"], tmp_path)
    assert duplicate["error"]["code"] == "E_CONFLICT"
    _, unknown = call(
        ["board", "zone", "refill", "--board", str(board), "--zones", "missing", "--dry-run"],
        tmp_path,
    )
    assert unknown["error"]["code"] == "E_NOT_FOUND"
    assert board.read_bytes() == before


@pytest.mark.parametrize("fault", ["ignore_save", "new_finding", "lost_check"])
def test_failed_zone_attempt_reads_back_restoration(board, tmp_path, monkeypatch, capsys, fault):
    monkeypatch.setenv("KICAD_CLI_STATE", str(tmp_path / "embedded-state"))
    for key, value in grant(tmp_path).items():
        monkeypatch.setenv(key, value)
    argv = creation(board)
    with pytest.raises(SystemExit):
        main.main([*argv, "--dry-run"])
    asked = json.loads(capsys.readouterr().out)
    assert asked["ok"], asked
    token = asked["data"]["confirm_token"]
    before = board.read_bytes()
    if fault == "ignore_save":
        monkeypatch.setattr(Document, "save", lambda *_args: None)
    elif fault == "new_finding":
        compare = zones.drc_baseline.compare_records

        def replacement(*args, **kwargs):
            return {
                **compare(*args, **kwargs),
                "status": "failed",
                "new_findings": [{"id": "injected"}],
            }

        monkeypatch.setattr(zones.drc_baseline, "compare_records", replacement)
    else:

        def interrupted(*args, **kwargs):
            raise OSError("injected saved-check failure")

        monkeypatch.setattr(ZoneAdapter, "_capture", interrupted)
    with pytest.raises(SystemExit):
        main.main([*argv, "--confirm", token])
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] in ("E_VERIFY_FAILED", "E_VERIFY_INCOMPLETE", "E_NATIVE_FAILURE")
    assert (
        result["error"]["details"]["receipt"]["extensions"]["restoration"]["matches_baseline"]
        is True
    )
    assert board.read_bytes() == before
