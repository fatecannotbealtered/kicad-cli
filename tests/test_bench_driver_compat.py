"""The development caller must execute plans rather than report them as writes."""

import importlib.util
import shutil
from pathlib import Path

from driver_test_utils import grant

from kicad_cli import move_readback

ROOT = Path(__file__).resolve().parents[1]


def test_benchmark_confirms_real_driver_plan_and_reads_actual_saved_state(tmp_path, monkeypatch):
    board = tmp_path / "edit.kicad_pcb"
    shutil.copyfile(ROOT / "tests/fixtures/sch_edit/edit.kicad_pcb", board)
    for key, value in grant(tmp_path).items():
        monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location("bench_chain", ROOT / "bench/chain.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.confirmed(
        ["board", "footprint", "move", "--board", str(board), "--moves", "R1:101.25,100.5"]
    )
    assert result["ok"] and "receipt" in result["data"], result
    assert "preview" not in result["data"]
    assert move_readback.observe(board, ["R1"]).objects[0]["position_nm"] == [
        101250000,
        100500000,
    ]


def test_power_recipe_runner_changes_actual_width_and_marks_native_checks_missing(
    tmp_path, monkeypatch
):
    from test_driver_power_runtime import make_case

    board, env = make_case(tmp_path)
    for key in ("KICAD_CLI_SCOPE_FILE", "KICAD_CLI_STATE_DIR"):
        monkeypatch.setenv(key, env[key])
    spec = importlib.util.spec_from_file_location(
        "power_workflow", ROOT / "bench/power_workflow.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.run(
        board,
        [
            {
                "command": "project netclass set",
                "args": ["--name", "Power", "--nets", "VIN", "--width", "0.3"],
            },
            {
                "command": "board track widen",
                "args": ["--nets", "VIN"],
                "require_target_width": True,
            },
        ],
    )
    assert report["status"] == "passed_bounded_saved_task"
    assert (
        report["native_DRC"] == report["independent_intent"] == report["manufacturing"] == "not_run"
    )
    from kicad_cli import power_readback

    assert all(
        t["width_nm"] == 300000
        for t in power_readback.observe(board, ["VIN"])["objects"]
        if t["net"] == "VIN"
    )
