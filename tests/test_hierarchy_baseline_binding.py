"""Frozen observation integrity via public compare/review entries.

Copy races use synthetic process boundaries; they are not native/ERC evidence.
"""

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
METHOD = ROOT / "bench/hierarchy_edit"
READERS = ROOT / "bench/rc_trial/execution/consumers"
sys.path.insert(0, str(METHOD))
spec = importlib.util.spec_from_file_location("hierarchy_review_entry", METHOD / "review.py")
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


def read(path):
    return json.loads(path.read_bytes())


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def alter(observation, kind):
    # R20 is the second instance of one physical child symbol. Its position and
    # non-electrical Description must not become a new expected baseline.
    for unit in observation["units"]:
        if unit["ref"] not in {"R10", "R20"}:
            continue
        if kind == "pose":
            unit["position_mm"][0] += 10.16
            next(n for n in unit["raw_symbol"] if isinstance(n, list) and n[0] == "at")[1] = str(
                unit["position_mm"][0]
            )
        else:
            unit["stored_fields"]["Description"] = "wrong baseline must not hide this field"
            next(
                n
                for n in unit["raw_symbol"]
                if isinstance(n, list) and n[:2] == ["property", "Description"]
            )[2] = unit["stored_fields"]["Description"]


def compare_cli(tmp_path, defect=None):
    req = tmp_path / "requirements"
    shutil.copytree(METHOD / "requirements", req)
    current = read(ROOT / "tests/fixtures/hierarchy_acceptance/H3-observation.json")
    if defect:
        baseline = read(req / "baseline-observation.json")
        alter(baseline, defect)
        alter(current, defect)
        put(req / "baseline-observation.json", baseline)
    current_path = tmp_path / "current.json"
    put(current_path, current)
    output = tmp_path / "comparison.json"
    argv = [sys.executable, str(METHOD / "compare.py")]
    for key, value in {
        "baseline": req / "baseline-observation.json",
        "current": current_path,
        "frozen": req / "FROZEN.json",
        "erc": ROOT / "tests/fixtures/hierarchy_acceptance/H3-erc.json",
        "drc": ROOT / "tests/fixtures/hierarchy_acceptance/H3-drc.json",
        "output": output,
        "readers": READERS,
        "stage": "H3",
    }.items():
        argv.extend(["--" + key, str(value)])
    process = subprocess.run(argv, capture_output=True, timeout=30)
    put(
        tmp_path / "public-comparison-evidence.json",
        {
            "exit": process.returncode,
            "stdout": process.stdout.decode("utf-8", "replace"),
            "stderr": process.stderr.decode("utf-8", "replace"),
            "frozen_unchanged": (req / "FROZEN.json").read_bytes()
            == (METHOD / "requirements/FROZEN.json").read_bytes(),
            "expected_baseline_sha256": read(req / "FROZEN.json")["input_preparation"][
                "baseline_observation_sha256"
            ],
            "actual_baseline_sha256": hashlib.sha256(
                (req / "baseline-observation.json").read_bytes()
            ).hexdigest(),
        },
    )
    return process, output


def test_original_pinned_observation_is_accepted(tmp_path):
    process, output = compare_cli(tmp_path)
    assert process.returncode == 0, process.stderr
    assert read(output)["bounded_edit_verified"]


def test_hash_and_parse_use_the_same_baseline_read(monkeypatch):
    from hierarchy_frozen import read_verified_baseline

    path = METHOD / "requirements/baseline-observation.json"
    original = Path.read_bytes
    calls = []

    def changing_file(self):
        if self == path:
            calls.append(self)
            if len(calls) > 1:
                return b'{"wrong_second_read": true}'
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", changing_file)
    _, baseline, binding = read_verified_baseline(path.with_name("FROZEN.json"), path)
    assert len(calls) == 1
    assert "units" in baseline and "wrong_second_read" not in baseline
    assert binding["baseline_observation_sha256"] == hashlib.sha256(original(path)).hexdigest()


@pytest.mark.parametrize("defect", ["pose", "field"])
def test_regenerated_baseline_cannot_hide_changed_schematic(tmp_path, defect):
    process, output = compare_cli(tmp_path, defect)
    assert process.returncode == 2, process.stdout
    assert not output.exists()
    error = json.loads(process.stdout)["error"]
    assert error["code"] == "HIERARCHY_BASELINE_MISMATCH"
    assert "frozen" in error["message"].lower()


class NativeEntered(RuntimeError):
    pass


@pytest.fixture
def review_session(tmp_path, monkeypatch):
    package = tmp_path / "package"
    method = package / "hierarchy-edit"
    shutil.copytree(METHOD / "requirements", method / "requirements")
    for source in METHOD.glob("*.py"):
        shutil.copyfile(source, method / source.name)
    base = package / "rc/execution"
    (base / "consumers").mkdir(parents=True)
    project = base / "project"
    shutil.copytree(METHOD / "fixtures", project)
    config = base / "config"
    config.mkdir()
    dependency = tmp_path / "dependency.bin"
    dependency.write_bytes(b"Synthetic boundary only; never executed")
    info = {"path": str(dependency), "sha256": entry.sha(dependency)}
    sdk = tmp_path / "_driver_sdk"
    sdk.mkdir()
    (tmp_path / "driver_spec").mkdir()
    put(
        package / "rc/agent-packet/DEPENDENCIES.json",
        {
            "environment": {
                "KICAD_CLI_DRIVER_SDK_PATH": str(sdk),
                "KICAD_CLI_SCOPE_FILE": str(dependency),
                "KICAD_CLI_OFFICIAL": str(dependency),
                "KICAD_CLI_PYTHON": str(dependency),
            },
            "readonly_consumer_python": sys.executable,
            "libraries": {"fixture": info},
            "native_files": {"python": info},
        },
    )
    put(
        package / "rc/agent-packet/REVISION.json",
        {"candidate": str(dependency), "sha256": info["sha256"]},
    )
    state = SimpleNamespace(
        package=package,
        method=method,
        project=project,
        config=config,
        work=base / "check",
        calls=[],
    )
    fake = ModuleType("process_evidence")

    def called(*args, **kwargs):
        state.calls.append(args[1])
        raise NativeEntered("Synthetic first process boundary, not native validation")

    fake.run_recorded = called
    monkeypatch.setitem(sys.modules, "process_evidence", fake)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "review.py",
            "check",
            "--package-root",
            str(package),
            "--out",
            str(state.work),
            "--project",
            str(project),
            "--config-root",
            str(config),
            "--stage",
            "H3",
        ],
    )
    return state


def test_valid_review_binding_reaches_native_boundary(review_session):
    with pytest.raises(NativeEntered):
        entry.main()
    assert review_session.calls == ["initialization-xml"]


@pytest.mark.parametrize("defect", ["replace", "pose", "field"])
def test_invalid_observation_is_refused_before_native_start(review_session, defect):
    baseline = review_session.method / "requirements/baseline-observation.json"
    value = read(baseline)
    if defect == "replace":
        baseline.write_bytes(baseline.read_bytes() + b"\n")
    else:
        alter(value, defect)
        put(baseline, value)
    with pytest.raises(ValueError, match="[Bb]aseline.*frozen|frozen.*[Bb]aseline"):
        entry.main()
    assert not review_session.calls
    assert not (review_session.work / "READY.json").exists()


@pytest.mark.parametrize("side", ["original", "copied"])
def test_baseline_drift_during_copy_is_not_adopted(review_session, monkeypatch, side):
    copy = shutil.copytree

    def drifting(src, dst, *args, **kwargs):
        result = copy(src, dst, *args, **kwargs)
        if Path(src) == review_session.method / "requirements":
            target = Path(src) if side == "original" else Path(dst)
            path = target / "baseline-observation.json"
            value = read(path)
            alter(value, "pose")
            put(path, value)
        return result

    monkeypatch.setattr(entry.shutil, "copytree", drifting)
    with pytest.raises(ValueError):
        entry.main()
    assert not review_session.calls
    assert not (review_session.work / "READY.json").exists()
    assert not (review_session.work / "snapshot/MANIFEST.json").exists()
