"""Error paths that no other test reaches, staged deliberately.

`test_error_coverage` measures which declared `E_*` a run produced; this is
where the ones nothing else provokes get produced. They live apart because the
guard sorts itself to the end of the run, and a test in the same file would
therefore run after the guard that is supposed to count it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import fake_upstream
import pytest
from kicad_demos import DEMOS, SKIP_REASON

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_a_worsened_board_is_put_back_and_reported_as_e_integrity(tmp_path):
    """The one applicable code no other test reached, and the reason it matters.

    Routing runs against the real KiCad here; only the judge is substituted. A
    router that makes a board worse is not something a test can ask for, and
    "error count rose, so put the board back" is the judgement that makes
    `--mode full` safe to have at all -- it clears every track before it starts.
    """
    for item in (DEMOS / "ecc83").iterdir():
        if item.is_file():
            shutil.copy2(item, tmp_path / item.name)
    board = next(tmp_path.glob("*.kicad_pcb"))
    original = board.read_bytes()

    oracle = fake_upstream.make_launcher(
        tmp_path / "bin", "kicad-cli", Path(__file__).parent / "fake_drc_oracle.py"
    )
    env = dict(
        os.environ,
        PYTHONIOENCODING="utf-8",
        KICAD_CLI_OFFICIAL=str(oracle),
        FAKE_DRC_CALLS=str(tmp_path / "drc-calls.txt"),
        # Clean on the baseline call, one error on every call after it.
        FAKE_DRC_WORSEN_AFTER="1",
    )

    def run(extra):
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "kicad_cli.main",
                "board",
                "route",
                "--board",
                str(board),
                "--mode",
                "full",
                *extra,
                "--compact",
            ],
            capture_output=True,
            cwd=REPO,
            env=env,
            timeout=3600,
        )
        for line in proc.stdout.decode("utf-8", "replace").splitlines():
            if line.startswith("{"):
                return json.loads(line)
        raise AssertionError(proc.stderr.decode("utf-8", "replace")[:400])

    plan = run(["--dry-run"])
    doc = run(["--confirm", plan["error"]["details"]["confirm_token"]])

    assert doc["ok"] is False
    assert doc["error"]["code"] == "E_INTEGRITY"
    details = doc["error"]["details"]
    assert details["errors_final"] > details["errors_baseline"]
    assert details["write_state"] == "rolled_back"
    assert board.read_bytes() == original, "the board was not put back"
    assert not list(tmp_path.glob("*.kicad-cli.*")), "transaction artefacts were left behind"


def test_a_missing_kicad_interpreter_is_e_config_whatever_else_is_running(tmp_path):
    """E_CONFIG was only ever produced by `board live` failing to connect.

    That made its coverage depend on KiCad *not* running: open the editor and
    the suite silently stopped exercising the code entirely, which is how a
    machine's ambient state ends up deciding what a test run proves. This
    produces it from configuration instead, so the answer does not move.
    """
    board = tmp_path / "demo.kicad_pcb"
    board.write_text("(kicad_pcb)", encoding="utf-8")
    env = dict(
        os.environ,
        PYTHONIOENCODING="utf-8",
        KICAD_CLI_PYTHON=str(tmp_path / "no-such-python.exe"),
        KICAD_CLI_ROOT=str(tmp_path / "no-such-kicad"),
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kicad_cli.main",
            "board",
            "audit",
            "--board",
            str(board),
            "--compact",
        ],
        capture_output=True,
        cwd=REPO,
        env=env,
        timeout=300,
    )
    doc = json.loads(proc.stdout.decode("utf-8").splitlines()[0])
    assert doc["ok"] is False
    assert doc["error"]["code"] == "E_CONFIG", doc
    assert doc["error"]["details"].get("hint"), "a config failure must say what to set"
