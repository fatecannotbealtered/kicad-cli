"""Reproduction fidelity and input ownership are independent of native success."""

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))
from native_repro import freeze, verify


def original(tmp_path):
    source = tmp_path / "old"
    project = source / "negative"
    project.mkdir(parents=True)
    (project / "x.kicad_sch").write_text('(kicad_sch (property "Footprint" "Test:Pad"))')
    for suffix in (".kicad_pcb", ".kicad_pro"):
        (project / ("x" + suffix)).write_text("{}")
    library = tmp_path / "Test.pretty"
    library.mkdir()
    (library / "Pad.kicad_mod").write_text("(footprint)")
    symbol = tmp_path / "Test.kicad_sym"
    symbol.write_text("(kicad_symbol_lib)")
    (project / "fp-lib-table").write_text(
        f'(fp_lib_table (lib (name "Test") (uri "{library.as_posix()}")))'
    )
    (project / "sym-lib-table").write_text(
        f'(sym_lib_table (lib (name "Test") (uri "{symbol.as_posix()}")))'
    )
    exe = tmp_path / "installation/bin/native.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"owned fixture, not executed")
    config = source / "configuration/10.0"
    config.mkdir(parents=True)
    (config / "kicad_common.json").write_text("{}")
    binding = {
        "official_cli": {"path": str(exe), "sha256": hashlib.sha256(exe.read_bytes()).hexdigest()},
        "native_python": {"path": sys.executable},
    }
    (source / "source-environment.json").write_text(json.dumps(binding))
    (source / "events.json").write_text(
        json.dumps(
            [
                {
                    "name": "negative-erc",
                    "exit": 3221226505,
                    "argv": [
                        "sch",
                        "erc",
                        "--format",
                        "json",
                        "--output",
                        str(project / "erc.json"),
                        str(project / "x.kicad_sch"),
                    ],
                }
            ]
        )
    )
    (source / "negative-erc.stdout").write_bytes(b"")
    (source / "negative-erc.stderr").write_bytes(b"")
    return source, exe


def test_capsule_contains_tables_dependencies_and_explicit_fidelity_gaps(tmp_path):
    source, exe = original(tmp_path)
    root = tmp_path / "capsule"
    manifest = freeze(source, root, exe, tmp_path)
    assert {p["copy"] for p in manifest["project"]} >= {
        "project/fp-lib-table",
        "project/sym-lib-table",
    }
    assert len(manifest["dependencies"]) == 2 and manifest["declared_file_inputs_complete"]
    assert manifest["historical_exact_reproduction"] is False and manifest["gaps"]
    verify(manifest, root)
    Path(manifest["dependencies"][0]["source"]).write_text("changed")
    with pytest.raises(ValueError, match="dependency drift"):
        verify(manifest, root)


def test_missing_local_table_remains_missing_not_recovered_from_host(tmp_path):
    source, exe = original(tmp_path)
    (source / "negative/fp-lib-table").unlink()
    manifest = freeze(source, tmp_path / "capsule", exe, tmp_path)
    assert not manifest["declared_file_inputs_complete"]
    assert any(r["path"] == "fp-lib-table" for r in manifest["unavailable"])


def test_changed_native_binary_is_refused_before_copy(tmp_path):
    source, exe = original(tmp_path)
    exe.write_bytes(b"not the retained native binary")
    root = tmp_path / "capsule"
    with pytest.raises(ValueError, match="differs"):
        freeze(source, root, exe, tmp_path)
    assert not root.exists()


@pytest.mark.parametrize("location", ["capsule", "experiment"])
@pytest.mark.parametrize("drift", ["modify", "add", "delete"])
def test_configuration_drift_refused_before_native_dispatch(tmp_path, monkeypatch, location, drift):
    import run_native_stability as runner

    source, exe = original(tmp_path)
    capsule = tmp_path / "capsule"
    manifest = freeze(source, capsule, exe, tmp_path)
    baseline = json.dumps(manifest, sort_keys=True)

    def change(config):
        path = config / "10.0/kicad_common.json"
        if drift == "modify":
            path.write_text('{"changed": true}')
        elif drift == "add":
            (config / "extra.json").write_text("{}")
        else:
            path.unlink()

    if location == "capsule":
        change(capsule / "configuration")
    else:
        copytree = runner.shutil.copytree

        def copy_and_drift(src, dst, *args, **kwargs):
            result = copytree(src, dst, *args, **kwargs)
            if Path(dst).name == "configuration":
                change(Path(dst))
            return result

        monkeypatch.setattr(runner.shutil, "copytree", copy_and_drift)
    monkeypatch.setattr(
        runner, "run_recorded", lambda *a, **kw: pytest.fail("Native process must not start")
    )
    output = tmp_path / "experiments"
    monkeypatch.setattr(sys, "argv", ["runner", "--capsule", str(capsule), "--output", str(output)])
    with pytest.raises(ValueError, match="configuration"):
        runner.main()
    assert not list(output.rglob("native.call.json"))
    assert (
        json.dumps(json.loads((capsule / "manifest.json").read_bytes()), sort_keys=True) == baseline
    )


def test_configuration_copy_is_compared_to_original_frozen_bytes(tmp_path):
    import shutil

    from native_repro import verify_configuration

    source, exe = original(tmp_path)
    capsule = tmp_path / "capsule"
    manifest = freeze(source, capsule, exe, tmp_path)
    relocated = tmp_path / "new-location/configuration"
    shutil.copytree(capsule / "configuration", relocated)
    evidence = verify_configuration(manifest, relocated)
    assert evidence["status"] == "passed"
    assert evidence["files"] == {"10.0/kicad_common.json": hashlib.sha256(b"{}").hexdigest()}
    assert evidence["relocated_to"] == str(relocated.resolve())


def test_legacy_absolute_configuration_manifest_is_verified_without_rebaselining(tmp_path):
    from native_repro import verify_configuration

    source, exe = original(tmp_path)
    capsule = tmp_path / "capsule"
    manifest = freeze(source, capsule, exe, tmp_path)
    for row in manifest["configuration_files"]:
        row["path"] = str(capsule / "configuration" / row.pop("relative_path"))
    verify_configuration(manifest, capsule / "configuration")
    (capsule / "configuration/10.0/kicad_common.json").write_text("changed")
    with pytest.raises(ValueError, match="configuration"):
        verify(manifest, capsule)


def test_missing_historical_configuration_is_not_created_as_a_baseline(tmp_path):
    import shutil

    source, exe = original(tmp_path)
    shutil.rmtree(source / "configuration")
    capsule = tmp_path / "capsule"
    manifest = freeze(source, capsule, exe, tmp_path)
    assert not manifest["configuration_present"]
    verify(manifest, capsule)
    (capsule / "configuration").mkdir()
    with pytest.raises(ValueError, match="configuration"):
        verify(manifest, capsule)
