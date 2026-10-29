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

import pytest
from kicad_demos import DEMOS, SKIP_REASON

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_domain_route_rolls_back_a_staged_new_finding(tmp_path):
    """Source-domain fault injection, not public driver E_INTEGRITY coverage.

    The public driver isolates preparation in a child and reports rejected
    geometry as E_VALIDATION. Its contract is tested in test_driver_power_runtime.
    This existing algorithm/rollback probe must inject its judge in the same
    isolated process as the domain operation; it never calls a product EXE.
    """
    for item in (DEMOS / "ecc83").iterdir():
        if item.is_file():
            shutil.copy2(item, tmp_path / item.name)
    board = next(tmp_path.glob("*.kicad_pcb"))
    original = board.read_bytes()

    script = """
import json,sys
from pathlib import Path
from kicad_cli import drc_baseline,envelope
from kicad_cli.fileformat import drc
from kicad_cli.native import route
params=json.loads(sys.argv[1]); original=Path(params['board']).read_bytes()
def judge(path, **context):
    changed=Path(path).read_bytes()!=original
    count,report=drc_baseline.capture(path)
    report.violations=[drc.Violation('track_width','error','staged test finding',
        [drc.Item('track','test-error-path-track','synthetic test item',(0,0))])] if changed else []
    return int(changed),report
route.errors=judge
envelope.configure(compact=True)
route.run(params['board'],params)
"""
    env = dict(os.environ, KICAD_CLI_STATE=str(tmp_path / "domain-state"), PYTHONIOENCODING="utf-8")
    for key in ("KICAD_CLI_TRACE", "KICAD_CLI_ERROR_TRACE"):
        env.pop(key, None)  # Private primitives must not certify public error/dispatch coverage.
    sys.path.insert(0, str(REPO / "bench"))
    from process_evidence import run_recorded

    def run(name, token=None):
        params = {"board": str(board), "mode": "full"}
        if token:
            params["confirm"] = token
        record, value = run_recorded(
            tmp_path,
            name,
            [sys.executable, "-B", "-c", script, json.dumps(params)],
            cwd=REPO,
            env=env,
            timeout=600,
            stdout_json=True,
            role="source-domain-fault-injection",
        )
        assert record["execution"] == "nonzero_exit" and value is not None, record
        return value

    plan = run("domain-preview")
    assert plan["error"]["code"] == "E_CONFIRMATION_REQUIRED", plan
    doc = run("domain-confirm", plan["error"]["details"]["confirm_token"])

    assert doc["ok"] is False
    assert doc["error"]["code"] == "E_INTEGRITY"
    details = doc["error"]["details"]
    assert details["errors_final"] > details["errors_baseline"]
    assert details["write_state"] == "rolled_back"
    assert board.read_bytes() == original, "the board was not put back"
    assert not list(tmp_path.glob("*.kicad-cli.*")), "transaction artefacts were left behind"


def test_a_missing_kicad_interpreter_is_e_config_whatever_else_is_running(tmp_path):
    """E_CONFIG was only ever produced by `session inspect` failing to connect.

    That made its coverage depend on KiCad *not* running: open the editor and
    the suite silently stopped exercising the code entirely, which is how a
    machine's ambient state ends up deciding what a test run proves. This
    produces it from configuration instead, so the answer does not move.

    Through `board silkscreen place`, which still needs KiCad's interpreter: the
    commands that served here before it read the files themselves now, and a
    missing interpreter is nothing to them.
    """
    board = tmp_path / "demo.kicad_pcb"
    board.write_text("(kicad_pcb)", encoding="utf-8")
    env = dict(
        os.environ,
        PYTHONIOENCODING="utf-8",
        KICAD_CLI_PYTHON=str(tmp_path / "no-such-python.exe"),
        KICAD_CLI_ROOT=str(tmp_path / "no-such-kicad"),
    )
    from driver_test_utils import grant_once

    env.update(grant_once(tmp_path))
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kicad_cli.main",
            "board",
            "silkscreen",
            "place",
            "--board",
            str(board),
            "--dry-run",
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
