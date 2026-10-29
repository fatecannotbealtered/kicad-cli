"""Source bootstrap with only an existing .vendor, in fresh no-site processes.

Select KICAD_CLI_ACCEPT_VENDOR when a checkout has no local cache. This fixture
copies that existing cache; it never installs packages or borrows global imports.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "bench"))
from process_evidence import run_recorded  # noqa: E402

BOOTSTRAP = r"""
import atexit, importlib.util, json, sys
from pathlib import Path

root, report = map(Path, sys.argv[1:3])
args = sys.argv[3:]
names = ("jsonschema", "referencing", "jsonschema_specifications", "attrs", "attr", "rpds")
before = {name: importlib.util.find_spec(name) is None for name in names}
assert sys.flags.isolated and sys.flags.no_site and all(before.values())
executed = []

def audit(event, values):
    if event == "exec":
        path = str(values[0].co_filename).replace("\\", "/")
        if "/_driver_sdk/" in path or "/driver_spec/" in path:
            executed.append(path)

def finish():
    modules = {}
    for name in (*names, "kicad_cli._driver_sdk"):
        module = sys.modules.get(name)
        modules[name] = getattr(module, "__file__", None)
    report.write_text(json.dumps({"isolated": sys.flags.isolated,
        "no_site": sys.flags.no_site, "initially_absent": before,
        "modules": modules, "executed_pinned_files": executed,
        "sys_path": sys.path}, indent=2), encoding="utf-8")

atexit.register(finish)
sys.addaudithook(audit)
sys.path.insert(0, str(root))
from kicad_cli.main import main
main(args)
"""


def hashes(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


@pytest.fixture
def isolated_source(tmp_path):
    vendor = Path(os.environ.get("KICAD_CLI_ACCEPT_VENDOR", REPO / ".vendor"))
    if not vendor.is_dir():
        pytest.skip("Existing source .vendor not selected; no global dependency substitution")
    root = tmp_path / "源码 副本"
    for name in ("kicad_cli", ".vendor"):
        shutil.copytree(
            vendor if name == ".vendor" else REPO / name,
            root / name,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    external = tmp_path / "外部 固定依赖"
    for name in ("_driver_sdk", "driver_spec"):
        shutil.copytree(root / "kicad_cli" / name, external / name)
    cwd = tmp_path / "仓库外 调用"
    cwd.mkdir()
    (cwd / "calls").mkdir()
    board = cwd / "unchanged.kicad_pcb"
    shutil.copyfile(REPO / "tests/fixtures/sch_edit/edit.kicad_pcb", board)
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("KICAD") and k not in {"PYTHONPATH", "PYTHONHOME"}
    }
    env.update(
        KICAD_CLI_STATE_DIR=str(cwd / "state"),
        KICAD_CLI_SCOPE_FILE=str(cwd / "absent-scope.json"),
        KICAD_CONFIG_HOME=str(cwd / "configuration"),
        KICAD_CLI_ROOT=str(cwd / "absent-native"),
        KICAD_CLI_PYTHON=str(cwd / "absent-python"),
        KICAD_CLI_OFFICIAL=str(cwd / "absent-official"),
        KICAD_CLI_TRACE=str(cwd / "dispatch.txt"),
    )

    def call(command):
        before = {"source": hashes(root), "external": hashes(external), "board": board.read_bytes()}
        record, doc = run_recorded(
            cwd / "calls",
            command,
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                "-c",
                BOOTSTRAP,
                str(root),
                str(cwd / "imports.json"),
                command,
                "--compact",
            ],
            cwd=cwd,
            env=env,
            timeout=60,
            stdout_json=True,
            role="isolated-source-cli",
        )
        audit = json.loads((cwd / "imports.json").read_bytes())
        assert audit["isolated"] and audit["no_site"]
        assert all(audit["initially_absent"].values())
        assert "PYTHONPATH" not in env and "PYTHONHOME" not in env
        assert all("site-packages" not in path for path in audit["sys_path"])
        assert before == {
            "source": hashes(root),
            "external": hashes(external),
            "board": board.read_bytes(),
        }
        store = cwd / "state/operations.sqlite3"
        if record["exit"] != 0:
            assert not store.exists()
        elif store.exists():
            with sqlite3.connect(store.as_uri() + "?mode=ro", uri=True) as connection:
                assert connection.execute("SELECT COUNT(*) FROM operations").fetchone() == (0,)
        return record, doc, audit

    return root, external, cwd, env, call


@pytest.mark.parametrize("selection", ["default", "external"])
@pytest.mark.parametrize("command", ["reference", "context", "doctor"])
def test_source_vendor_discovery(isolated_source, selection, command):
    root, external, cwd, env, call = isolated_source
    if selection == "external":
        env["KICAD_CLI_DRIVER_SDK_PATH"] = str(external / "_driver_sdk")
    record, doc, audit = call(command)
    assert record["exit"] == 0 and doc["ok"], doc
    for name in ("jsonschema", "referencing"):
        assert Path(audit["modules"][name]).is_relative_to(root / ".vendor")
    expected_sdk = external if selection == "external" else root / "kicad_cli"
    assert (
        Path(audit["modules"]["kicad_cli._driver_sdk"]) == expected_sdk / "_driver_sdk/__init__.py"
    )
    assert audit["executed_pinned_files"]
    assert (cwd / "dispatch.txt").read_text().splitlines() == [command]


@pytest.mark.parametrize("selection", ["default", "external"])
@pytest.mark.parametrize("dependency", ["jsonschema", "referencing"])
def test_source_missing_dependency_is_not_pin_mismatch(isolated_source, selection, dependency):
    root, external, cwd, env, call = isolated_source
    (root / ".vendor" / dependency).rename(root / ("retained-" + dependency))
    if selection == "external":
        env["KICAD_CLI_DRIVER_SDK_PATH"] = str(external / "_driver_sdk")
    record, doc, _ = call("reference")
    assert record["exit"] == 4 and not doc["ok"]
    error = doc["error"]
    assert error["code"] == "E_CONFIG" and error["retryable"] is False
    assert error["details"]["failure_kind"] == "missing_dependency"
    assert error["details"]["missing_module"] == dependency
    assert error["details"]["dispatch_state"] == "not_started"
    assert ".vendor" in error["details"]["next_action"]
    assert not (cwd / "dispatch.txt").exists()


@pytest.mark.parametrize("damage", ["wrong-path", "no-spec", "sdk", "spec", "extra-schema"])
def test_all_pin_checks_precede_sdk_execution(isolated_source, damage):
    root, external, cwd, env, call = isolated_source
    env["KICAD_CLI_DRIVER_SDK_PATH"] = str(external / "_driver_sdk")
    if damage == "wrong-path":
        env["KICAD_CLI_DRIVER_SDK_PATH"] = str(external / "absent")
    elif damage == "no-spec":
        (external / "driver_spec").rename(external / "retained-spec")
    elif damage == "sdk":
        with (external / "_driver_sdk/__init__.py").open("a", encoding="utf-8") as stream:
            stream.write("\nraise RuntimeError('must not execute')\n")
    elif damage == "spec":
        with (external / "driver_spec/scripts/check_spec.py").open("a", encoding="utf-8") as stream:
            stream.write("\nraise RuntimeError('must not execute')\n")
    else:
        (external / "driver_spec/schemas/unattributed.schema.json").write_text(
            "{}", encoding="utf-8"
        )
    record, doc, audit = call("reference")
    assert record["exit"] == 4 and not doc["ok"]
    assert doc["error"]["code"] == "E_CONFIG"
    assert doc["error"]["details"]["dispatch_state"] == "not_started"
    assert doc["error"]["details"]["failure_kind"] == (
        "pin_mismatch" if damage in {"sdk", "spec", "extra-schema"} else "runtime_unavailable"
    )
    assert not audit["executed_pinned_files"]
    assert not audit["modules"]["kicad_cli._driver_sdk"]
    assert not audit["modules"]["jsonschema"]
    assert not (cwd / "dispatch.txt").exists()


def test_frozen_dependencies_do_not_add_checkout_vendor(tmp_path, monkeypatch):
    from kicad_cli import driver_runtime

    vendor = tmp_path / ".vendor"
    vendor.mkdir()
    monkeypatch.setattr(driver_runtime, "PACKAGE", tmp_path / "kicad_cli")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "path", list(sys.path))
    before = list(sys.path)
    driver_runtime._dependencies()
    assert sys.path == before
