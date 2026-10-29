"""Public failure boundaries and an explicitly configured final-artifact workflow."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from driver_test_utils import grant

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("path", ["fab gerber", "fab drill", "board plot"])
def test_unusable_explicit_sdk_refuses_without_output(tmp_path, path, record_property):
    board = tmp_path / "board.kicad_pcb"
    shutil.copyfile(REPO / "tests/fixtures/sch_edit/edit.kicad_pcb", board)
    before = board.read_bytes()
    output = tmp_path / "outputs"
    # An existing invalid executable is deliberately selected. It cannot be
    # masked by discovering a different installation via a missing-path fallback.
    unusable = tmp_path / "invalid-native-python.exe"
    unusable.write_bytes(b"Owned non-executable SDK dependency negative\n")
    env = dict(
        os.environ,
        **grant(tmp_path, effects=["observe", "artifact"], outputs=[str(tmp_path)]),
        KICAD_CLI_PYTHON=str(unusable),
        KICAD_CLI_ROOT=str(tmp_path / "absent-root"),
        KICAD_CONFIG_HOME=str(tmp_path / "configuration"),
        KICAD_CLI_DRIVER_SDK_PATH=os.environ.get(
            "KICAD_CLI_ACCEPT_DRIVER_SDK", str(REPO / "kicad_cli/_driver_sdk")
        ),
    )
    binary = os.environ.get("KICAD_CLI_TEST_BINARY")
    prefix = [binary] if binary else [sys.executable, "-B", "-m", "kicad_cli.main"]
    args = [*path.split(), "--board", str(board), "--out", str(output), "--dry-run", "--compact"]
    if path == "board plot":
        args += ["--file-format", "svg"]
    for _ in range(2):
        proc = subprocess.run(
            prefix + args,
            cwd=tmp_path if binary else REPO,
            env=env,
            capture_output=True,
            timeout=60,
        )
        doc = json.loads(proc.stdout)
        assert not doc["ok"] and proc.returncode == 4
        assert doc["error"]["code"] == "E_BACKEND_UNAVAILABLE"
        assert doc["error"]["retryable"] is False
        assert doc["error"]["details"]["dispatch_state"] == "not_started"
        assert board.read_bytes() == before and not output.exists()
    if binary:
        record_property(
            "fcc_artifact_sha256", hashlib.sha256(Path(binary).read_bytes()).hexdigest()
        )


@pytest.fixture(scope="module")
def changed_case(tmp_path_factory):
    names = (
        "KICAD_CLI_TEST_BINARY",
        "KICAD_CLI_CHANGE_BASELINE",
        "KICAD_CLI_CHANGE_NATIVE_ROOT",
        "KICAD_CLI_CHANGE_CONFIG",
    )
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        pytest.skip("Explicit isolated change workflow inputs not selected: " + ", ".join(missing))
    output = tmp_path_factory.mktemp("engineering-change") / "run"
    command = [
        sys.executable,
        "-B",
        str(REPO / "bench/run_engineering_change.py"),
        "--binary",
        os.environ[names[0]],
        "--baseline",
        os.environ[names[1]],
        "--kicad-root",
        os.environ[names[2]],
        "--config-seed",
        os.environ[names[3]],
        "--output",
        str(output),
    ]
    working_dir = os.environ.get("KICAD_CLI_ACCEPT_CWD")
    external_sdk = os.environ.get("KICAD_CLI_ACCEPT_DRIVER_SDK")
    if working_dir:
        assert external_sdk, "Outside-cwd acceptance needs explicit driver SDK"
        command += ["--working-dir", working_dir, "--driver-sdk", external_sdk]
    proc = subprocess.run(command, cwd=working_dir or REPO, capture_output=True, timeout=2400)
    (output.parent / "runner.stdout").write_bytes(proc.stdout)
    (output.parent / "runner.stderr").write_bytes(proc.stderr)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    summary = json.loads((output / "summary.json").read_bytes())
    assert summary["status"] == "passed"
    from scripts.fcc_evidence import digest_manifest, source_manifest

    assert summary["source"]["source_manifest_sha256"] == digest_manifest(source_manifest(REPO))
    binary = Path(os.environ[names[0]])
    assert summary["source"]["binary"]["sha256"] == hashlib.sha256(binary.read_bytes()).hexdigest()
    if working_dir:
        assert summary["source"]["standalone"]
        assert summary["source"]["harness_product_modules"] == []
        assert Path(summary["source"]["working_directory"]) == Path(working_dir).resolve()
        events = json.loads((output / "events.json").read_bytes())
        assert all(Path(e["cwd"]) == Path(working_dir).resolve() for e in events)
    return output, summary


@pytest.mark.parametrize(
    "stage", ["edit-sync-translate", "explicit-VIN-copper", "thermal-silk-outputs"]
)
def test_frozen_change_workflow(changed_case, stage, record_property):
    root, result = changed_case
    record_property("fcc_artifact_sha256", result["source"]["binary"]["sha256"])
    record_property(
        "engineering_summary_sha256",
        hashlib.sha256((root / "summary.json").read_bytes()).hexdigest(),
    )
    events = {e["name"]: e for e in json.loads((root / "events.json").read_bytes())}
    if stage == "edit-sync-translate":
        names = ["edit", "sync", "move"]
        assert json.loads((root / "synced-intent.json").read_bytes())["status"] == "passed"
        assert json.loads((root / "moved-intent.json").read_bytes())["status"] == "passed"
        assert json.loads((root / "edited-schematic-intent.json").read_bytes())["passed"]
    elif stage == "explicit-VIN-copper":
        names = ["netclass", "repair", "rewidth", "widen"]
        for name in names:
            args = events[name + "-preview"]["argv"]
            assert args[args.index("--nets") + 1] == "VIN"
        tracks = [
            t for t in result["actual_copper"] if t["kind"] == "PCB_TRACK" and t["net"] == "VIN"
        ]
        assert len(tracks) == 3 and all(t["width_nm"] == 250000 for t in tracks)
        assert not result["drc"]["violations"] and not result["drc"]["unconnected_items"]
    else:
        names = ["native-refill", "refill-repeat", "silk", "gerber", "drill", "silk-svg"]
        assert result["manufacturing"]["round_holes"]["observed"] == 4
        assert (
            json.loads((root / "one-layer-negative.json").read_bytes())["independent"]["status"]
            == "failed"
        )
        assert json.loads((root / "changed-copper-gerber.json").read_bytes())["status"] == "passed"
        assert (
            json.loads((root / "clear-polarity-negative/result.json").read_bytes())["status"]
            == "passed_rejection"
        )
        outline = json.loads((root / "outline-same-bbox-negative/comparison.json").read_bytes())
        assert {c["id"] for c in outline["checks"] if not c["passed"]} == {"edge-geometry"}
        for form in ("direct", "comment"):
            for polarity in ("positive", "negative"):
                case = root / f"file-polarity-{form}-{polarity}" / "result.json"
                assert json.loads(case.read_bytes())["status"] == (
                    "passed" if polarity == "positive" else "passed_rejection"
                )
    for name in names:
        assert (
            events[name + "-preview"]["exit"]
            == events[name + "-receipt"]["exit"]
            == events[name + "-show"]["exit"]
            == 0
        )
        receipt = json.loads((root / (name + "-receipt.stdout")).read_bytes())["data"]["receipt"]
        assert receipt["operation_id"]


def test_change_runner_rejects_unfrozen_input_before_state(tmp_path):
    output = tmp_path / "run"
    proc = subprocess.run(
        [
            sys.executable,
            "-B",
            str(REPO / "bench/run_engineering_change.py"),
            "--baseline",
            str(tmp_path),
            "--kicad-root",
            str(tmp_path),
            "--config-seed",
            str(tmp_path),
            "--output",
            str(output),
        ],
        capture_output=True,
        timeout=15,
    )
    assert proc.returncode == 1 and b"Frozen baseline missing or changed" in proc.stderr
    assert not output.exists()
