"""No-KiCad fault injection for the shared DRC boundary; not live FCC proof."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from kicad_cli import envelope, kicad_env  # noqa: E402
from kicad_cli.payload import drc_runner as drc  # noqa: E402
from kicad_cli.payload import kicad_lib  # noqa: E402


def report_for(board):
    return {
        "source": Path(board).name,
        "date": "2026-09-17T12:00:00+0000",
        "kicad_version": "10.0.6",
        "coordinate_units": "mm",
        "violations": [],
        "unconnected_items": [],
        "schematic_parity": [],
    }


def finding():
    return {
        "type": "clearance",
        "severity": "error",
        "description": "test only",
        "items": [
            {
                "uuid": "00000000-0000-0000-0000-000000000001",
                "description": "track",
                "pos": {"x": 1.0, "y": 2.0},
            }
        ],
    }


@pytest.fixture
def board(tmp_path, monkeypatch):
    path = tmp_path / "project" / "测试 board.kicad_pcb"
    path.parent.mkdir()
    path.write_text("test board A", encoding="utf-8")
    path.with_suffix(".kicad_pro").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(drc, "config_root", lambda: tmp_path / "absent-config")
    return path


def install_fake(monkeypatch, producer=None, rc=0):
    seen = []

    def fake(argv, **kwargs):
        seen.append((argv, kwargs))
        output = Path(argv[argv.index("-o") + 1])
        data = report_for(argv[-1]) if producer is None else producer(argv, kwargs)
        if data is not None:
            output.write_bytes(data if isinstance(data, bytes) else json.dumps(data).encode())
        return subprocess.CompletedProcess(argv, rc, b"upstream stdout", b"upstream stderr")

    monkeypatch.setattr(drc.subprocess, "run", fake)
    return seen


def test_success_preserves_findings_and_does_not_save_design(board, monkeypatch):
    def produce(argv, _kwargs):
        value = report_for(argv[-1])
        value["violations"] = [finding()]
        return value

    seen = install_fake(monkeypatch, produce)
    result = drc.run(str(board), sys.executable)
    assert result.report["violations"] == [finding()]  # rc=0 is not a clean board
    assert result.input_sha256[board.name]
    assert result.input_sha256[board.with_suffix(".kicad_dru").name] is None
    argv, kwargs = seen[0]
    assert kwargs["cwd"] == board.parent
    assert argv[-1] == str(board.resolve())
    assert "--save-board" not in argv and "--exit-code-violations" not in argv
    assert board.read_text(encoding="utf-8") == "test board A"
    assert not Path(argv[argv.index("-o") + 1]).parent.exists()


@pytest.mark.parametrize("rc", [1, 5, -9])
def test_nonzero_exit_is_failure_even_with_a_report(board, monkeypatch, rc):
    seen = install_fake(monkeypatch, rc=rc)
    with pytest.raises(drc.DrcError) as exc:
        drc.run(str(board), sys.executable)
    assert exc.value.code == "E_IO"
    assert exc.value.details["returncode"] == rc
    assert "stderr" in exc.value.details["_untrusted"]
    assert not Path(seen[0][0][-2]).parent.exists()


def test_existing_old_reports_cannot_mask_no_output(board, tmp_path, monkeypatch):
    stale = tmp_path / "kicad_layout_drc.json"
    stale.write_text(json.dumps(report_for("OTHER.kicad_pcb")), encoding="utf-8")
    monkeypatch.setattr(drc.tempfile, "tempdir", str(tmp_path))
    seen = install_fake(monkeypatch, lambda *_: None)
    with pytest.raises(drc.DrcError, match="no regular report"):
        drc.run(str(board), sys.executable)
    assert json.loads(stale.read_text())["source"] == "OTHER.kicad_pcb"
    assert not Path(seen[0][0][-2]).parent.exists()


@pytest.mark.parametrize(
    "broken",
    [
        b"not json",
        b"[]",
        b"null",
        b"{}",
        b'{"a": 1, "a": 2}',
        b'{"x": NaN}',
        b'{"x": Infinity}',
        b"\xff",
    ],
)
def test_invalid_json_is_not_a_clean_report(board, monkeypatch, broken):
    install_fake(monkeypatch, lambda *_: broken)
    with pytest.raises(drc.DrcError) as exc:
        drc.run(str(board), sys.executable)
    assert exc.value.code == "E_IO"


@pytest.mark.parametrize(
    "field,value",
    [
        ("violations", None),
        ("violations", [None]),
        ("violations", [{}]),
        ("unconnected_items", {}),
        ("schematic_parity", ""),
        ("coordinate_units", "in"),
        ("coordinate_units", "mils"),
        ("source", "OTHER.kicad_pcb"),
        ("date", None),
        ("kicad_version", ""),
        ("ignored_checks", {}),
    ],
)
def test_wrong_shapes_and_identity_fail_closed(board, monkeypatch, field, value):
    def produce(argv, _kwargs):
        data = report_for(argv[-1])
        data[field] = value
        return data

    install_fake(monkeypatch, produce)
    with pytest.raises(drc.DrcError) as exc:
        drc.run(str(board), sys.executable)
    assert exc.value.code == "E_IO"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda f: f.update(severity="unknown"),
        lambda f: f.update(excluded="false"),
        lambda f: f.update(items={}),
        lambda f: f["items"][0].update(uuid=1),
        lambda f: f["items"][0].update(pos={"x": True, "y": 2}),
        lambda f: f["items"][0].update(pos={"x": float("inf"), "y": 2}),
        lambda f: f["items"][0].update(pos={"x": 1e300, "y": float("nan")}),
    ],
)
def test_invalid_nested_findings_are_rejected(board, monkeypatch, mutate):
    item = finding()
    mutate(item)
    data = report_for(board)
    data["violations"] = [item]
    install_fake(monkeypatch, lambda *_: data)
    with pytest.raises(drc.DrcError) as exc:
        drc.run(str(board), sys.executable)
    assert exc.value.code == "E_IO"


def test_wrong_absolute_source_with_same_basename_is_rejected(board, monkeypatch):
    data = report_for(board)
    data["source"] = str(board.parent / "other" / board.name)
    install_fake(monkeypatch, lambda *_: data)
    with pytest.raises(drc.DrcError, match="invalid report"):
        drc.run(str(board), sys.executable)


@pytest.mark.parametrize("sidecar", [".kicad_pcb", ".kicad_pro", ".kicad_dru"])
def test_concurrent_input_changes_invalidate_the_report(board, monkeypatch, sidecar):
    def produce(argv, _kwargs):
        board.with_suffix(sidecar).write_text("changed", encoding="utf-8")
        return report_for(argv[-1])

    install_fake(monkeypatch, produce)
    with pytest.raises(drc.DrcError) as exc:
        drc.run(str(board), sys.executable)
    assert exc.value.code == "E_CONFLICT"
    assert board.with_suffix(sidecar).name in exc.value.details["changed_files"]


@pytest.mark.parametrize("kind", ["timeout", "launch"])
def test_execution_errors_cleanup_the_workspace(board, monkeypatch, kind):
    outputs = []

    def fail(argv, **_kwargs):
        outputs.append(Path(argv[-2]))
        if kind == "timeout":
            raise subprocess.TimeoutExpired(argv, 0.01)
        raise OSError("test launch failure")

    monkeypatch.setattr(drc.subprocess, "run", fail)
    with pytest.raises(drc.DrcError) as exc:
        drc.run(str(board), sys.executable, timeout=0.01)
    assert exc.value.code == ("E_TIMEOUT" if kind == "timeout" else "E_IO")
    assert not outputs[0].parent.exists()


def test_config_copy_preserves_settings_but_isolates_writes(board, tmp_path, monkeypatch):
    seed = tmp_path / "config-seed"
    seed.mkdir()
    (seed / "kicad_common.json").write_text('{"sentinel": true}', encoding="utf-8")
    monkeypatch.setattr(drc, "config_root", lambda: seed)

    def produce(argv, kwargs):
        private = Path(kwargs["env"]["KICAD_CONFIG_HOME"])
        assert (private / "kicad_common.json").read_text() == '{"sentinel": true}'
        (private / "kicad_common.json").write_text("upstream write", encoding="utf-8")
        return report_for(argv[-1])

    install_fake(monkeypatch, produce)
    drc.run(str(board), sys.executable)
    assert (seed / "kicad_common.json").read_text() == '{"sentinel": true}'


def test_parallel_same_basename_reports_do_not_mix(board, tmp_path, monkeypatch):
    barrier = threading.Barrier(4, timeout=10)
    seen = []
    lock = threading.Lock()
    paths = []
    for i in range(4):
        path = tmp_path / str(i) / board.name
        path.parent.mkdir()
        path.write_text(str(i), encoding="utf-8")
        paths.append(path)

    def fake(argv, **_kwargs):
        output = Path(argv[-2])
        path = Path(argv[-1])
        with lock:
            seen.append(output)
        barrier.wait()
        data = report_for(path)
        data["test_identity"] = path.read_text()
        output.write_text(json.dumps(data), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(drc.subprocess, "run", fake)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda p: drc.run(str(p), sys.executable), paths))
    assert [r.report["test_identity"] for r in results] == [str(i) for i in range(4)]
    assert len(set(seen)) == 4
    assert all(not p.parent.exists() for p in seen)


def test_report_size_limit_is_explicit(board, monkeypatch):
    monkeypatch.setattr(drc, "MAX_REPORT_BYTES", 10)
    install_fake(monkeypatch)
    with pytest.raises(drc.DrcError, match="supported size") as exc:
        drc.run(str(board), sys.executable)
    assert exc.value.details["max_report_bytes"] == 10


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_budget_does_not_launch(board, monkeypatch, timeout):
    monkeypatch.setattr(drc.subprocess, "run", lambda *a, **k: pytest.fail("launched"))
    with pytest.raises(drc.DrcError) as exc:
        drc.run(str(board), sys.executable, timeout)
    assert exc.value.code == "E_USAGE"


def test_missing_override_does_not_fall_through_to_path(board, monkeypatch):
    monkeypatch.setenv("KICAD_CLI_OFFICIAL", str(board.parent / "missing"))
    monkeypatch.setattr(drc.shutil, "which", lambda _: pytest.fail("fell through"))
    assert drc.find_official_cli() is None


def test_explicit_override_is_shared_by_shell_and_payload(board, monkeypatch):
    monkeypatch.setenv("KICAD_CLI_OFFICIAL", sys.executable)
    assert kicad_env.find_official_cli() == str(Path(sys.executable).resolve())
    assert kicad_lib.official_cli() == kicad_env.find_official_cli()


def test_macos_bundle_is_discovered(board, tmp_path, monkeypatch):
    root = tmp_path / "KiCad.app"
    directory = root / "Contents" / "MacOS"
    directory.mkdir(parents=True)
    name = "kicad-cli.exe" if os.name == "nt" else "kicad-cli"
    for fname in (name, "pcbnew"):
        (directory / fname).touch()
    monkeypatch.delenv("KICAD_CLI_OFFICIAL", raising=False)
    monkeypatch.setattr(drc.shutil, "which", lambda _: None)
    assert drc.find_official_cli(roots=[root]) == str((directory / name).resolve())


def test_cannot_resolve_the_running_cli_as_its_own_upstream(board, monkeypatch):
    monkeypatch.setattr(sys, "argv", [str(board)])
    monkeypatch.setenv("KICAD_CLI_OFFICIAL", str(board))
    assert drc.find_official_cli() is None


@pytest.mark.parametrize("adapter", ["shell", "payload"])
def test_both_adapters_preserve_error_semantics(board, monkeypatch, capsys, adapter):
    envelope.configure()
    monkeypatch.setenv("KICAD_CLI_OFFICIAL", sys.executable)
    install_fake(monkeypatch, rc=1)
    with pytest.raises(SystemExit) as exc:
        (kicad_env.run_drc if adapter == "shell" else kicad_lib.run_drc)(str(board))
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    assert exc.value.code == 1 and doc["error"]["code"] == "E_IO"
    assert captured.err == ""
    if adapter == "payload":
        assert doc["error"]["details"]["write_state"] == "unknown"


@pytest.mark.parametrize("name", ["pcb_route.py", "pcb_widen.py"])
def test_routing_and_widening_delegate_without_swallowing_failure(name):
    tree = ast.parse((REPO / "kicad_cli" / "payload" / name).read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run_drc")
    assert len(fn.body) == 1 and isinstance(fn.body[0], ast.Return)
    assert ast.unparse(fn.body[0].value) == "K.run_drc(path)"


def test_resolver_does_not_accept_a_shim_next_to_other_tools(board, monkeypatch):
    shim = board.parent / "kicad-cli.CMD"
    shim.touch()
    (board.parent / "pcbnew").touch()
    assert not drc.is_kicads_own(shim)


def test_config_root_cannot_recursively_include_workspace(board, tmp_path, monkeypatch):
    monkeypatch.setattr(drc.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(drc, "config_root", lambda: tmp_path)
    monkeypatch.setattr(drc.subprocess, "run", lambda *a, **k: pytest.fail("launched"))
    with pytest.raises(drc.DrcError) as exc:
        drc.run(str(board), sys.executable)
    assert exc.value.code == "E_CONFIG"


def test_real_child_process_roundtrip_in_both_import_modes(board, tmp_path):
    import fake_upstream

    script = tmp_path / "fake_drc.py"
    data = json.dumps(report_for(board))
    script.write_text(
        "import pathlib, sys\n"
        f"REPORT = {data!r}\n"
        "assert sys.argv[1:3] == ['pcb', 'drc']\n"
        "print('not a JSON envelope on upstream stdout')\n"
        "pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_text(REPORT, encoding='utf-8')\n",
        encoding="utf-8",
    )
    launcher = fake_upstream.make_launcher(tmp_path / "bin", "official-drc", script)
    env = dict(
        os.environ,
        KICAD_CLI_OFFICIAL=str(launcher),
        KICAD_CONFIG_HOME=str(tmp_path / "nonexistent-config"),
        PYTHONIOENCODING="utf-8",
    )
    env.pop("KICAD_CLI_TRACE", None)
    for source in (
        "from kicad_cli import envelope, kicad_env\nenvelope.ok(kicad_env.run_drc(sys.argv[1]))\n",
        f"sys.path.insert(0, {str(REPO / 'kicad_cli' / 'payload')!r})\n"
        "import kicad_lib\n"
        "kicad_lib.ok(kicad_lib.run_drc(sys.argv[1]))\n",
    ):
        proc = subprocess.run(
            [sys.executable, "-c", "import sys\n" + source, str(board)],
            cwd=REPO,
            env=env,
            capture_output=True,
            timeout=15,
        )
        assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
        assert json.loads(proc.stdout)["data"] == report_for(board)
        assert proc.stderr == b""
