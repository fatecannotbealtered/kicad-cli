"""Current public driver calls on explicitly owned integration-test copies.

No compatibility envelope is synthesized: callers inspect the real preview or
receipt. Domain assertions may use the existing domain_receipt helper explicitly.
"""

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import pytest
from driver_test_utils import grant

REPO = Path(__file__).resolve().parents[1]
PATH_FLAGS = {"--board", "--schematic", "--netlist", "--spec", "--changes", "--out"}
WRITES = {
    tuple(c["path"].split())
    for c in json.loads((REPO / "kicad_cli/driver_contract.json").read_bytes())["reference"][
        "commands"
    ]
    if c["type"] == "write"
}


@pytest.fixture(autouse=True)
def public_cli_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("KICAD_CLI_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("KICAD_CLI_STATE_DIR", str(tmp_path / "ambient-state"))
    monkeypatch.setenv("KICAD_CLI_SCOPE_FILE", str(tmp_path / "absent-scope.json"))


def owned_environment(argv, overrides=None):
    paths = [Path(argv[i + 1]).resolve() for i, word in enumerate(argv[:-1]) if word in PATH_FLAGS]
    env = dict(os.environ, KICAD_CLI_STRICT="1", PYTHONIOENCODING="utf-8")
    env.update(overrides or {})
    writing = any(tuple(argv[: len(path)]) == path for path in WRITES)
    if paths and writing:
        parents = [p if p.is_dir() else p.parent for p in paths]
        root = Path(os.path.commonpath(parents))
        # The test runner declares its newly created basetemp. Refuse accidental
        # grants for installed demos or any undeclared engineering originals.
        owned = os.environ.get("KICAD_CLI_TEST_ROOT")
        if not owned or not root.is_relative_to(Path(owned).resolve()):
            raise ValueError("Integration write requires a declared owned test root")
        scope = root / "move-scope.json"
        if not scope.exists():
            grant(root, effects=["observe", "change", "persist", "artifact"], outputs=[str(root)])
        env.update(KICAD_CLI_SCOPE_FILE=str(scope), KICAD_CLI_STATE_DIR=str(root / "driver-state"))
    return env


def invoke_details(argv, overrides=None, *, suppress_trace=False):
    env = owned_environment(argv, overrides)
    if suppress_trace:
        env.pop("KICAD_CLI_TRACE", None)
    directory = Path(env["KICAD_CLI_TEST_ROOT"]) / "public-calls"
    directory.mkdir(exist_ok=True)
    sys.path.insert(0, str(REPO / "bench"))
    from process_evidence import run_recorded

    name = "call-" + str(len(list(directory.glob("*.call.json"))))
    record, value = run_recorded(
        directory,
        name,
        [sys.executable, "-B", "-m", "kicad_cli.main", *argv, "--compact"],
        cwd=REPO,
        env=env,
        timeout=3600,
        stdout_json=True,
        role="source-public-integration",
    )
    assert record["execution"] in {"completed", "nonzero_exit"}, record
    assert value is not None, ("no envelope", record)
    error = (directory / (name + ".stderr")).read_text(encoding="utf-8")
    return value, error, record["exit"]


def invoke(argv, overrides=None):
    value, _error, code = invoke_details(argv, overrides)
    return value, code


def planned(value):
    assert value["ok"] and "preview" in value["data"], value
    assert value["data"]["preview"]["can_apply"], value
    assert value["data"]["confirm_token"], value
    return value["data"]["preview"]


def engineering_trial(value):
    plan = planned(value)
    assert plan["level"] == "trial_observed", plan
    return plan["extensions"]["trial"]["result"]


def copy_export_fixture(directory):
    """Use the existing saved two-via fixture; no external drawing-sheet dependency."""
    owned = Path(os.environ["KICAD_CLI_TEST_ROOT"]).resolve()
    if not directory.resolve().is_relative_to(owned):
        raise ValueError("Export fixture must stay inside the declared test root")
    directory.mkdir(parents=True, exist_ok=False)
    for suffix in (".kicad_pcb", ".kicad_pro"):
        shutil.copyfile(
            REPO / "tests/fixtures/sch_edit" / ("edit" + suffix), directory / ("edit" + suffix)
        )
    return directory / "edit.kicad_pcb"


def saved_path(value, suffix):
    """Use the authenticated saved readback, never a task's private trial path."""
    receipt = value.get("driver_receipt") or value["data"]["receipt"]
    rows = [
        r for r in receipt["extensions"]["readback"]["objects"] if Path(r["path"]).suffix == suffix
    ]
    assert len(rows) == 1
    path = Path(rows[0]["path"])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == rows[0]["sha256"]
    return path


def native_metrics(board):
    """Independent installed pcbnew readback; the public ampacity schema has no length total."""
    from payload_reference import kicad_python

    python = kicad_python()
    assert python, "Existing native interpreter is required for these integration measurements"
    before = board.read_bytes()
    code = (
        "import json,sys,pcbnew; b=pcbnew.LoadBoard(sys.argv[1]); "
        "tracks=[t for t in b.GetTracks() if t.GetClass()!='PCB_VIA']; "
        "layers={b.GetLayerName(t.GetLayer()) for t in tracks}; "
        "print(json.dumps({'copper_mm':sum(t.GetLength() for t in tracks)/1e6,"
        "'by_layer_mm':{l:sum(t.GetLength() for t in tracks "
        "if b.GetLayerName(t.GetLayer())==l)/1e6 for l in layers},"
        "'native_version':pcbnew.GetBuildVersion()}))"
    )
    directory = Path(os.environ["KICAD_CLI_TEST_ROOT"]) / "native-readback"
    directory.mkdir(exist_ok=True)
    sys.path.insert(0, str(REPO / "bench"))
    from process_evidence import run_recorded

    record, value = run_recorded(
        directory,
        "read-" + str(len(list(directory.glob("*.call.json")))),
        [python, "-B", "-c", code, str(board)],
        cwd=board.parent,
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
        timeout=120,
        stdout_json=True,
        role="native-source-readback",
    )
    assert record["exit"] == 0 and record["execution"] == "completed", record
    assert board.read_bytes() == before
    return value
