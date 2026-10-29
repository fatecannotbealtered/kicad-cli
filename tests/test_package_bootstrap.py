"""Package startup without author paths, SDK-free diagnostics and strict source loading."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from kicad_cli import bootstrap, driver_runtime, main, sdk_loader


@pytest.fixture
def isolated_process(monkeypatch):
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
    monkeypatch.setattr(sys, "pycache_prefix", sys.pycache_prefix)
    for name in tuple(os.environ):
        if name.startswith("KICAD") or name in {"PYTHONPATH", "PYTHONHOME", "KIPRJMOD"}:
            os.environ.pop(name, None)


def configure(tmp_path, monkeypatch, **changes):
    cad = tmp_path / "cad"
    official = cad / "bin" / ("kicad-cli.exe" if sys.platform == "win32" else "kicad-cli")
    official.parent.mkdir(parents=True)
    official.write_bytes(b"declared test version probe")
    official.chmod(0o755)
    libraries = cad / "share" / "kicad" / "symbols"
    libraries.mkdir(parents=True)
    (libraries / "Device.kicad_sym").write_text("(kicad_symbol_lib)\n", encoding="utf-8")
    template = cad / "share" / "kicad" / "template" / "sym-lib-table"
    template.parent.mkdir()
    template.write_text(
        '(sym_lib_table (lib (name "Device") (type "KiCad") '
        '(uri "${KICAD10_SYMBOL_DIR}/Device.kicad_sym") (options "") (descr "")))\n',
        encoding="utf-8",
    )
    config = tmp_path / "runtime-config.json"
    record = {"format_version": 1, "kicad_root": "cad", "profile_dir": "owned-profile"}
    record.update(changes)
    config.write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setenv(bootstrap.CONFIG_ENV, str(config))
    return config, cad


def successful_probes(monkeypatch, version="10.0.6"):
    monkeypatch.setattr(driver_runtime, "_pin", lambda: None)
    monkeypatch.setattr(
        bootstrap.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=version.encode(), stderr=b""),
    )


def test_relative_configuration_initializes_only_owned_profile(
    tmp_path, monkeypatch, isolated_process
):
    config, cad = configure(tmp_path, monkeypatch)
    elsewhere = tmp_path / "unrelated-cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("PYTHONPATH", "PRIVATE_AUTHOR_PATH")
    monkeypatch.setenv("KICAD_CONFIG_HOME", "PRIVATE_GLOBAL_CONFIG")
    selected = bootstrap.apply_configuration()
    assert selected["kicad_root"] == cad
    profile = config.parent / "owned-profile"
    assert Path(os.environ["HOME"]) == profile / "home"
    assert Path(os.environ["KICAD_CONFIG_HOME"]) == profile / "kicad"
    assert Path(os.environ["KICAD_DOCUMENTS_HOME"]) == profile / "documents"
    assert Path(os.environ["KICAD_CLI_SCOPE_FILE"]) == profile / "scope.json"
    assert "PYTHONPATH" not in os.environ
    assert not (profile / "scope.json").exists()
    assert not (profile / "state" / "operations.sqlite3").exists()
    assert not list(elsewhere.iterdir())
    assert (profile / "kicad" / "10.0" / "sym-lib-table").read_bytes() == (
        cad / "share" / "kicad" / "template" / "sym-lib-table"
    ).read_bytes()


@pytest.mark.parametrize(
    "contents,kind",
    [
        ('{"format_version":1,"format_version":1}', "configuration_format_invalid"),
        (
            '{"format_version":1,"secret-author-path":"SECRET_TOKEN"}',
            "configuration_format_invalid",
        ),
        ('{"format_version":true}', "configuration_format_invalid"),
        ('{"format_version":1,"kicad_root":null,"profile_dir":"p"}', "configuration_value_invalid"),
        ("not json SECRET_TOKEN", "configuration_format_invalid"),
    ],
)
def test_bad_configuration_is_structured_and_redacted(
    tmp_path, monkeypatch, isolated_process, contents, kind
):
    config = tmp_path / "PRIVATE_AUTHOR_PATH" / "runtime-config.json"
    config.parent.mkdir()
    config.write_text(contents, encoding="utf-8")
    monkeypatch.setenv(bootstrap.CONFIG_ENV, str(config))
    doc, code = bootstrap.check_environment()
    assert code == 4 and doc["ok"] is False
    assert doc["error"]["details"]["failure_kind"] == kind
    text = json.dumps(doc)
    assert "PRIVATE_AUTHOR_PATH" not in text and "SECRET_TOKEN" not in text
    assert doc["error"]["details"]["dispatch_state"] == "not_started"


def test_frozen_configuration_is_beside_executable_not_cwd(tmp_path, monkeypatch, isolated_process):
    exe = tmp_path / "installed" / "kicad-cli.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.chdir(tmp_path)
    assert bootstrap.configuration_path() == exe.parent / "runtime-config.json"
    doc, code = bootstrap.check_environment()
    assert code == 4 and doc["error"]["details"]["failure_kind"] == "configuration_unavailable"
    assert str(tmp_path) not in json.dumps(doc)


def test_required_ready_does_not_grant_write_scope(tmp_path, monkeypatch, isolated_process):
    configure(tmp_path, monkeypatch)
    successful_probes(monkeypatch)
    doc, code = bootstrap.check_environment()
    assert code == 0 and doc["ok"] is True
    assert doc["data"]["read_ready"] is True
    assert doc["data"]["write_scope_ready"] is False
    checks = {row["name"]: row for row in doc["data"]["checks"]}
    assert checks["task_scope"]["status"] == "not_configured"
    assert checks["pinned_runtime"]["external_pin_required"] is False
    assert checks["isolated_symbol_table"]["status"] == "pass"
    assert checks["isolated_symbol_table"]["library_files_bundled"] is False
    assert checks["optional_scenarios"]["required_for_saved_sch"] is False
    assert checks["optional_scenarios"]["probed"] is False
    assert str(tmp_path) not in json.dumps(doc)


def test_existing_owned_symbol_table_is_never_replaced(tmp_path, monkeypatch, isolated_process):
    config, _ = configure(tmp_path, monkeypatch)
    table = config.parent / "owned-profile" / "kicad" / "10.0" / "sym-lib-table"
    table.parent.mkdir(parents=True)
    original = b"(sym_lib_table)\n"
    table.write_bytes(original)
    successful_probes(monkeypatch)
    doc, code = bootstrap.check_environment()
    assert code == 0
    assert table.read_bytes() == original
    row = next(r for r in doc["data"]["checks"] if r["name"] == "isolated_symbol_table")
    assert row["initialization"] == "existing"


def test_missing_installed_table_template_is_structured_and_does_not_invent_table(
    tmp_path, monkeypatch, isolated_process
):
    config, cad = configure(tmp_path, monkeypatch)
    (cad / "share" / "kicad" / "template" / "sym-lib-table").unlink()
    successful_probes(monkeypatch)
    doc, code = bootstrap.check_environment()
    assert code == 4
    row = next(r for r in doc["error"]["details"]["checks"] if r["name"] == "isolated_symbol_table")
    assert row["failure_kind"] == "symbol_table_template_missing"
    assert not (config.parent / "owned-profile" / "kicad" / "10.0" / "sym-lib-table").exists()
    assert str(tmp_path) not in json.dumps(doc)
    with pytest.raises(bootstrap.ConfigurationError, match="symbol_table_template_missing"):
        bootstrap.apply_configuration()


def test_version_mismatch_is_a_required_failure_without_raw_output(
    tmp_path, monkeypatch, isolated_process
):
    configure(tmp_path, monkeypatch)
    successful_probes(monkeypatch, "10.1.0")
    doc, code = bootstrap.check_environment()
    assert code == 4 and doc["ok"] is False
    checks = {row["name"]: row for row in doc["error"]["details"]["checks"]}
    assert checks["official_kicad"]["detected_version"] == "10.1.0"
    assert checks["official_kicad"]["expected_version"] == "10.0.6"
    assert checks["official_kicad"]["failure_kind"] == "version_mismatch"
    assert str(tmp_path) not in json.dumps(doc)


def test_integrity_failure_is_redacted_and_never_calls_a_fallback(
    tmp_path, monkeypatch, isolated_process
):
    configure(tmp_path, monkeypatch)
    successful_probes(monkeypatch)

    def fail():
        raise driver_runtime.PinMismatch("PRIVATE_AUTHOR_PATH SECRET_TOKEN")

    monkeypatch.setattr(driver_runtime, "_pin", fail)
    doc, code = bootstrap.check_environment()
    assert code == 4 and doc["ok"] is False
    checks = {row["name"]: row for row in doc["error"]["details"]["checks"]}
    assert checks["pinned_runtime"]["failure_kind"] == "pin_mismatch"
    assert "PRIVATE_AUTHOR_PATH" not in json.dumps(doc)
    assert "SECRET_TOKEN" not in json.dumps(doc)


def test_explicit_invalid_sdk_override_never_selects_embedded_sdk(tmp_path, monkeypatch):
    package = tmp_path / "package"
    local = package / "_driver_sdk"
    local.mkdir(parents=True)
    (local / "__init__.py").write_text("# must not be selected\n", encoding="utf-8")
    monkeypatch.setenv("KICAD_CLI_DRIVER_SDK_PATH", str(tmp_path / "missing"))
    with pytest.raises(FileNotFoundError):
        sdk_loader.root(package)


def test_sdk_free_flag_writes_one_envelope_and_rejects_extra_arguments(monkeypatch, capsys):
    monkeypatch.setattr(
        bootstrap,
        "check_environment",
        lambda: ({"ok": True, "schema_version": "1.0", "data": {"read_ready": True}}, 0),
    )
    with pytest.raises(SystemExit) as exited:
        main.main(["--check-environment", "--compact"])
    assert exited.value.code == 0
    assert json.loads(capsys.readouterr().out)["data"]["read_ready"] is True
    with pytest.raises(SystemExit) as exited:
        main.main(["--check-environment", "--confirm", "SECRET_TOKEN"])
    assert exited.value.code == 2
    doc = json.loads(capsys.readouterr().out)
    assert doc["error"]["code"] == "E_USAGE" and "SECRET_TOKEN" not in json.dumps(doc)


def test_normal_command_configuration_error_never_dispatches(
    tmp_path, monkeypatch, isolated_process, capsys
):
    path = tmp_path / "PRIVATE_AUTHOR_PATH" / "missing.json"
    monkeypatch.setenv(bootstrap.CONFIG_ENV, str(path))

    def dispatch(_):
        pytest.fail("Configuration failure dispatched a normal command")

    monkeypatch.setattr(driver_runtime, "execute", dispatch)
    with pytest.raises(SystemExit) as exited:
        main.main(["reference", "--compact"])
    assert exited.value.code == 4
    doc = json.loads(capsys.readouterr().out)
    assert doc["error"]["code"] == "E_CONFIG"
    assert "PRIVATE_AUTHOR_PATH" not in json.dumps(doc)


def test_frozen_local_sdk_executes_source_and_ignores_a_matching_stale_pyc(tmp_path):
    script = r"""
import json, os, pathlib, py_compile, sys
from kicad_cli import sdk_loader
package=pathlib.Path(sys.argv[1]); sdk=package/'_driver_sdk'; sdk.mkdir(parents=True)
(sdk/'__init__.py').write_text('from .runtime import VALUE\n',encoding='utf-8')
source=sdk/'runtime.py'; source.write_text("VALUE = 'cached'\n",encoding='utf-8')
py_compile.compile(str(source),doraise=True); stamp=source.stat()
source.write_text("VALUE = 'source'\n",encoding='utf-8')
os.utime(source,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
sys.frozen=True
sdk_loader.load(sdk,package)
module=sys.modules['kicad_cli._driver_sdk']
print(json.dumps({'value':module.VALUE,'source':module.__file__}))
"""
    env = dict(os.environ)
    env.pop("PYTHONHOME", None)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "package")],
        capture_output=True,
        env=env,
        check=True,
    )
    assert json.loads(result.stdout)["value"] == "source"
