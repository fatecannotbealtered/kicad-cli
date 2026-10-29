"""Public dependency selection from an owned cwd with spaces and Unicode.

Source and frozen invocations are separate runs. Only the explicit native
matrix uses an installed KiCad; IPC-unreachable is a labelled client double.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "bench"))
from process_evidence import run_recorded  # noqa: E402


@pytest.fixture
def scene(tmp_path, record_property):
    cwd = tmp_path / "仓库外 工作目录"
    cwd.mkdir()
    deps = tmp_path / "已有 依赖"
    sdk = Path(os.environ.get("KICAD_CLI_ACCEPT_DRIVER_SDK", REPO / "kicad_cli/_driver_sdk"))
    for name in ("_driver_sdk", "driver_spec"):
        shutil.copytree(
            sdk.parent / name, deps / name, ignore=shutil.ignore_patterns("__pycache__")
        )
    binary = os.environ.get("KICAD_CLI_TEST_BINARY")
    if binary:
        record_property(
            "fcc_artifact_sha256", hashlib.sha256(Path(binary).read_bytes()).hexdigest()
        )
    env = {
        k: v
        for k, v in os.environ.items()
        if (not k.startswith("KICAD_CLI_") or k in {"KICAD_CLI_TRACE", "KICAD_CLI_ERROR_TRACE"})
        and k not in {"PYTHONPATH", "PYTHONHOME", "KICAD_CONFIG_HOME"}
    }
    env.update(
        PYTHONIOENCODING="utf-8",
        PYTHONDONTWRITEBYTECODE="1",
        KICAD_CLI_DRIVER_SDK_PATH=str(deps / "_driver_sdk"),
        KICAD_CLI_STATE_DIR=str(cwd / "state"),
        KICAD_CLI_SCOPE_FILE=str(cwd / "no-scope.json"),
        KICAD_CONFIG_HOME=str(cwd / "configuration"),
        UNRELATED_TEST_SECRET="unrelated-value-must-not-leak",
    )
    prefix = (
        [binary]
        if binary
        else [
            sys.executable,
            "-I",
            "-B",
            "-c",
            "import sys; sys.path.insert(0,sys.argv.pop(1)); "
            "from kicad_cli.main import main; main(sys.argv[1:])",
            str(REPO),
        ]
    )

    calls = cwd / "calls"
    calls.mkdir()
    counter = 0

    def call(args, *, json_output=True):
        nonlocal counter
        counter += 1
        name = f"call-{counter:02d}"
        record, value = run_recorded(
            calls,
            name,
            prefix + args,
            cwd=cwd,
            env=env,
            timeout=100,
            stdout_json=json_output,
            role="product-cli",
        )
        if os.name == "nt":
            assert record["job_finalization"]["natural_tree_complete"], record
        proc = subprocess.CompletedProcess(
            prefix + args,
            record["exit"],
            (calls / (name + ".stdout")).read_bytes(),
            (calls / (name + ".stderr")).read_bytes(),
        )
        assert b"unrelated-value-must-not-leak" not in proc.stdout + proc.stderr
        doc = value if json_output else proc.stdout.decode("utf-8")
        return proc, doc

    return cwd, deps, env, call, binary


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "unset",
        "missing",
        "relative",
        "not-package",
        "no-spec",
        "sdk-mismatch",
        "spec-mismatch",
    ],
)
def test_driver_sdk_bootstrap_matrix(scene, case):
    cwd, deps, env, call, binary = scene
    if case == "unset":
        env.pop("KICAD_CLI_DRIVER_SDK_PATH")
    elif case == "missing":
        env["KICAD_CLI_DRIVER_SDK_PATH"] = str(cwd / "缺失 SDK")
    elif case == "relative":
        env["KICAD_CLI_DRIVER_SDK_PATH"] = os.path.relpath(deps / "_driver_sdk", cwd)
    elif case == "not-package":
        env["KICAD_CLI_DRIVER_SDK_PATH"] = str(deps)
    elif case == "no-spec":
        # Select an SDK-only copy; preparing absence must not depend on a
        # Windows directory rename succeeding.
        sdk_only = cwd / "sdk-without-spec" / "_driver_sdk"
        shutil.copytree(deps / "_driver_sdk", sdk_only)
        env["KICAD_CLI_DRIVER_SDK_PATH"] = str(sdk_only)
    elif case == "sdk-mismatch":
        with (deps / "_driver_sdk/__init__.py").open("a", encoding="utf-8") as f:
            f.write("\nraise RuntimeError('unauthenticated code must never run')\n")
    elif case == "spec-mismatch":
        with (deps / "driver_spec/scripts/check_spec.py").open("a", encoding="utf-8") as f:
            f.write("\nraise RuntimeError('unauthenticated code must never run')\n")
    proc, doc = call(["reference", "--compact"])
    works = case == "valid" or (case == "unset" and not binary)
    if works:
        assert proc.returncode == 0 and doc["ok"]
        assert len(doc["data"]["commands"]) == 33
        assert (
            doc["data"]["dependency_configuration"]["bootstrap"]["variable"]
            == "KICAD_CLI_DRIVER_SDK_PATH"
        )
    else:
        assert proc.returncode == 4 and not doc["ok"]
        error = doc["error"]
        assert error["code"] == "E_CONFIG" and error["retryable"] is False
        assert error["details"]["dispatch_state"] == "not_started"
        assert error["details"]["phase"] == "runtime_bootstrap"
        assert "KICAD_CLI_DRIVER_SDK_PATH" in error["details"]["next_action"]
        assert "driver_spec" in error["details"]["next_action"]
        assert not (cwd / "state/operations.sqlite3").exists()
        if case == "missing":
            for command in ("context", "doctor"):
                failed, body = call([command, "--compact"])
                assert failed.returncode == 4 and body["error"]["code"] == "E_CONFIG"
                assert body["error"]["details"]["phase"] == "runtime_bootstrap"
    help_proc, help_text = call(["--help"], json_output=False)
    assert help_proc.returncode == 0 and "KICAD_CLI_DRIVER_SDK_PATH" in help_text


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "root-unset",
        "python-unset",
        "python-missing",
        "python-no-pcbnew",
        "python-relative",
        "root-missing",
        "official-missing",
        "official-unset",
        "official-relative",
        "official-driver",
    ],
)
def test_native_dependency_matrix(scene, case):
    cwd, deps, env, call, binary = scene
    root = os.environ.get("KICAD_CLI_ACCEPT_NATIVE_ROOT")
    if not root:
        pytest.skip("Explicit existing native installation not selected for standalone matrix")
    root = Path(root).resolve()
    python = root / "bin/python.exe"
    official = root / "bin/kicad-cli.exe"
    env.update(
        KICAD_CLI_ROOT=str(root), KICAD_CLI_PYTHON=str(python), KICAD_CLI_OFFICIAL=str(official)
    )
    if case == "root-unset":
        env.pop("KICAD_CLI_ROOT")
    elif case == "python-unset":
        env.pop("KICAD_CLI_PYTHON")
    elif case == "python-missing":
        env["KICAD_CLI_PYTHON"] = str(cwd / "missing-python.exe")
    elif case == "python-no-pcbnew":
        env["KICAD_CLI_PYTHON"] = sys.executable
    elif case == "python-relative":
        env["KICAD_CLI_PYTHON"] = os.path.relpath(python, cwd)
    elif case == "root-missing":
        env["KICAD_CLI_ROOT"] = str(cwd / "missing-installation")
        env.pop("KICAD_CLI_PYTHON")
        env.pop("KICAD_CLI_OFFICIAL")
    elif case == "official-missing":
        env["KICAD_CLI_OFFICIAL"] = str(cwd / "missing-official.exe")
    elif case == "official-unset":
        env.pop("KICAD_CLI_OFFICIAL")
    elif case == "official-relative":
        env["KICAD_CLI_OFFICIAL"] = os.path.relpath(official, cwd)
    elif case == "official-driver":
        candidate = binary or os.environ.get("KICAD_CLI_ACCEPT_CANDIDATE")
        if not candidate:
            pytest.skip("Explicit candidate required to test same-named official CLI confusion")
        assert Path(candidate).is_file(), "The wrong-tool case requires a real candidate executable"
        env["KICAD_CLI_OFFICIAL"] = candidate
    proc, doc = call(["doctor", "--compact"])
    assert proc.returncode == 0 and doc["ok"]  # Doctor completed; not every check passed.
    checks = {r["check"]: r for r in doc["data"]["checks"]}
    native_ok = case not in {
        "python-missing",
        "python-no-pcbnew",
        "python-relative",
        "root-missing",
    }
    official_ok = case not in {
        "official-missing",
        "official-relative",
        "official-driver",
        "root-missing",
    }
    assert checks["native_backend"]["status"] == ("pass" if native_ok else "fail")
    assert checks["official_cli"]["status"] == ("pass" if official_ok else "fail")
    text = json.dumps(checks)
    assert "KICAD_HOME" not in text
    if not native_ok:
        assert "KICAD_CLI_ROOT" in text and "KICAD_CLI_PYTHON" in text
    if not official_ok:
        assert "KICAD_CLI_OFFICIAL" in text
    assert checks["session_backend"]["status"] == "warn"
    observed = call(["context", "--compact"])[1]["data"]["environment"]["components"]
    assert next(c for c in observed if c["name"] == "KiCad external pcbnew interpreter")[
        "identity_status"
    ] == ("observed" if native_ok else "unavailable")
    # No native backend or optional GUI is needed to observe this saved file.
    board = cwd / "只读 板.kicad_pcb"
    shutil.copyfile(REPO / "tests/fixtures/sch_edit/edit.kicad_pcb", board)
    before = board.read_bytes()
    proc, doc = call(["board", "check", "--board", str(board), "--compact"])
    assert proc.returncode == 0 and doc["ok"] and board.read_bytes() == before
    listing, operations = call(["operation", "list", "--compact"])
    assert listing.returncode == 0 and operations["data"]["items"] == []
    if not native_ok:
        scope = {
            "scope_id": "owned-native-refusal",
            "tool": "kicad-cli",
            "granted_by": "User-authorized isolated export test",
            "granted_at": "2026-10-09T00:00:00Z",
            "expires_at": None,
            "effects": ["observe", "artifact"],
            "dangerous": False,
            "roots": [str(cwd)],
            "outputs": [str(cwd)],
        }
        Path(env["KICAD_CLI_SCOPE_FILE"]).write_text(json.dumps(scope), encoding="utf-8")
        output = cwd / "new-output"
        proc, doc = call(
            ["fab", "gerber", "--board", str(board), "--out", str(output), "--dry-run", "--compact"]
        )
        assert proc.returncode == 4 and doc["error"]["code"] == "E_BACKEND_UNAVAILABLE", doc
        assert doc["error"]["details"]["dispatch_state"] == "not_started"
        assert not output.exists() and board.read_bytes() == before


@pytest.mark.parametrize("case", ["missing", "relative", "disconnected-double"])
def test_optional_ipc_failure_does_not_disable_saved_files(scene, case):
    cwd, deps, env, call, _binary = scene
    client = cwd / "IPC client"
    if case == "disconnected-double":
        (client / "kipy").mkdir(parents=True)
        (client / "kipy/__init__.py").write_text(
            "class KiCad:\n    def get_version(self):\n"
            "        raise ConnectionError('Owned disconnected test double')\n",
            encoding="utf-8",
        )
    env["KICAD_CLI_IPC_CLIENT_PATH"] = str(client) if case != "relative" else "IPC client"
    proc, doc = call(["session", "inspect", "--compact"])
    assert proc.returncode == 4 and doc["error"]["code"] == "E_CONFIG"
    details = doc["error"]["details"]
    assert details["dispatch_state"] == "not_started"
    assert details["backend"] == (
        "unreachable" if case == "disconnected-double" else "client_missing"
    )
    assert "KICAD_CLI_IPC_CLIENT_PATH" in details["next_action"]
    assert "Saved-file" in details["note"]
    proc, doc = call(["reference", "--compact"])
    assert proc.returncode == 0 and doc["ok"]
    board = cwd / "without GUI.kicad_pcb"
    shutil.copyfile(REPO / "tests/fixtures/sch_edit/edit.kicad_pcb", board)
    before = board.read_bytes()
    proc, doc = call(["board", "check", "--board", str(board), "--compact"])
    assert proc.returncode == 0 and doc["ok"] and board.read_bytes() == before
