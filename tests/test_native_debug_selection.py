"""Pure diagnostic-scope checks; no KiCad, debugger or engineering process starts."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))
from process_evidence import native_debug_enabled


def test_disabled_diagnostics_do_not_resolve_a_program_or_root():
    assert native_debug_enabled(None, None) is False


@pytest.mark.parametrize("value", ["true", 1, None])
def test_configuration_cannot_implicitly_enable_debugging(value):
    with pytest.raises(ValueError, match="explicit boolean"):
        native_debug_enabled(None, None, enabled=value)


def test_misspelled_source_control_refuses_before_any_launch(tmp_path, monkeypatch):
    import native_process
    import process_evidence

    monkeypatch.setenv("KICAD_BENCH_NATIVE_DEBUG", "true")
    monkeypatch.setattr(
        process_evidence, "run_recorded", lambda *a, **kw: pytest.fail("Unexpected launch")
    )
    with pytest.raises(ValueError, match="must be 0 or 1"):
        native_process.run([sys.executable], tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows-only native debug selection")
@pytest.mark.parametrize(
    "relative,expected",
    [
        ("bin/python.exe", True),
        ("bin/kicad-cli.exe", True),
        ("other/python.exe", False),
        ("other/kicad-cli.exe", False),
        ("bin/kicad-cli.exe.other", False),
    ],
)
def test_only_the_declared_native_images_are_selected(tmp_path, relative, expected):
    assert native_debug_enabled([tmp_path / relative], tmp_path, enabled=True) is expected


@pytest.mark.skipif(os.name != "nt", reason="Windows-only native debug selection")
def test_candidate_same_basename_and_relative_program_are_not_debugged(tmp_path):
    native = tmp_path / "native"
    candidate = tmp_path / "candidate/bin/kicad-cli.exe"
    assert native_debug_enabled([candidate], native, enabled=True) is False
    assert native_debug_enabled(["kicad-cli.exe"], native, enabled=True) is False


@pytest.mark.skipif(os.name != "nt", reason="Windows-only native debug selection")
@pytest.mark.parametrize("root", [None, "relative-native"])
def test_ambiguous_installation_refuses_before_launch(root):
    with pytest.raises(ValueError, match="absolute declared"):
        native_debug_enabled([sys.executable], root, enabled=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows-only native debug selection")
@pytest.mark.parametrize("native", [True, False])
def test_source_helper_keeps_control_settings_out_of_child_environment(
    tmp_path, monkeypatch, native
):
    import native_process
    import process_evidence

    root = tmp_path / "native"
    program = root / "bin/python.exe" if native else tmp_path / "host/python.exe"
    monkeypatch.setenv("KICAD_BENCH_NATIVE_ROOT", str(root))
    monkeypatch.setenv("KICAD_BENCH_NATIVE_DEBUG", "1")
    captured = {}

    def record(folder, name, command, **kwargs):
        captured.update(kwargs)
        (folder / "process.stdout").write_bytes(b"{}")
        (folder / "process.stderr").write_bytes(b"")
        return {"execution": "completed", "exit": 0}, None

    monkeypatch.setattr(process_evidence, "run_recorded", record)
    folder = tmp_path / "evidence"
    folder.mkdir()
    native_process.run([str(program)], folder)
    assert captured["debug"] is native
    assert "KICAD_BENCH_NATIVE_ROOT" not in captured["env"]
    assert "KICAD_BENCH_NATIVE_DEBUG" not in captured["env"]
    assert os.environ["KICAD_BENCH_NATIVE_DEBUG"] == "1"


@pytest.mark.skipif(os.name != "nt", reason="Windows-only native debug selection")
@pytest.mark.parametrize("enabled,native", [(False, True), (True, True), (True, False)])
def test_acceptance_scene_never_debugs_candidate_as_a_native_image(
    tmp_path, monkeypatch, enabled, native
):
    import process_evidence

    from bench.persistence_acceptance import support

    install = tmp_path / "native"
    program = install / "bin/kicad-cli.exe" if native else tmp_path / "candidate/kicad-cli.exe"
    original = tmp_path / "original"
    original.mkdir()
    (tmp_path / "configuration").mkdir()
    scene = object.__new__(support.Scene)
    scene.root, scene.original = tmp_path, original
    scene.calls, scene.env, scene.binding = tmp_path / "calls", {}, {}
    scene.origin, scene.config_hashes = {"files": {}}, {}
    monkeypatch.setattr(support, "SETTINGS", {"native_root": str(install), "native_debug": enabled})
    captured = {}

    def record(*args, **kwargs):
        captured.update(kwargs)
        return {"execution": "completed", "exit": 0}, {"ok": True}

    monkeypatch.setattr(process_evidence, "run_recorded", record)
    assert scene.raw_call("probe", [program])[0] == 0
    assert captured["debug"] is (enabled and native)
