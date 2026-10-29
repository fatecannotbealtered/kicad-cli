"""Read-only input drift keeps its structured conflict across the public boundary."""

import json
import os
import subprocess
import sys
from pathlib import Path


def test_public_input_drift_is_not_hidden_as_contract_failure(tmp_path):
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "absent.kicad_pcb").write_text("(kicad_pcb (version 20260128))")
    (tmp_path / "absent.kicad_pro").write_text('{"board":{}}')
    shim = (
        "from kicad_cli import module_project; from kicad_cli.sync_inputs import Drift; "
        "exec('def changed(*a, **k):\\n raise Drift(\"fixture drift\", changed_files=[])'); "
        "module_project.capture_board_context=changed; "
        "from kicad_cli.main import main; main(__import__('sys').argv[1:])"
    )
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            shim,
            "board",
            "routing",
            "inspect",
            "--board",
            str(tmp_path / "absent.kicad_pcb"),
            "--compact",
        ],
        cwd=root,
        env=dict(os.environ, KICAD_CLI_STATE_DIR=str(tmp_path / "state")),
        capture_output=True,
        timeout=60,
    )
    value = json.loads(process.stdout)
    assert process.returncode == 6, value
    assert value["error"]["code"] == "E_CONFLICT"
    assert value["error"]["details"]["conflict"] == "state_drift"
    assert value["error"]["details"]["dispatch_state"] == "not_started"
