"""A selected macOS app and a prefix install expose the same actual resources."""

import pytest

from kicad_cli import bootstrap, installation
from kicad_cli.fileformat import lib_tables


@pytest.mark.parametrize("mac", [False, True])
def test_selected_installation_initializes_real_resource_table(tmp_path, mac):
    root = tmp_path / ("KiCad.app" if mac else "prefix")
    share = root / ("Contents/SharedSupport" if mac else "share/kicad")
    (share / "template").mkdir(parents=True)
    (share / "symbols").mkdir()
    raw = (
        b'(sym_lib_table (lib (name "Local") (type "KiCad") '
        b'(uri "${KICAD10_SYMBOL_DIR}/Local.kicad_sym")))'
    )
    (share / "template/sym-lib-table").write_bytes(raw)
    symbol = share / "symbols/Local.kicad_sym"
    symbol.write_text("(kicad_symbol_lib (version 20250114))")
    profile = tmp_path / "owned-profile"
    config = {"kicad_root": root, "profile_dir": profile}
    bootstrap._initialize_symbol_table(config, True)
    assert (profile / "kicad/10.0/sym-lib-table").read_bytes() == raw
    tables = lib_tables.Tables.of(tmp_path, root, profile / "kicad/10.0")
    assert tables.symbols["Local"].path == symbol
    assert installation.resources(root) == share


def test_macos_uses_official_program_and_real_framework_python_only(tmp_path):
    app = tmp_path / "KiCad.app"
    binary = app / "Contents/MacOS/kicad-cli"
    binary.parent.mkdir(parents=True)
    binary.touch()
    python = app / "Contents/Frameworks/Python.framework/Versions/3.9/bin/python3"
    python.parent.mkdir(parents=True)
    python.touch()
    python.with_name("python3-config").touch()
    assert installation.official_cli(app) == binary
    assert installation.official_cli(tmp_path) == binary
    assert installation.python_candidates(app) == [python]


@pytest.mark.parametrize("value", ["10.0.6", "10.0.7", "10.0.7-ubuntu24.04.1"])
def test_supported_patch_versions(value):
    assert installation.compatible_version(value)


@pytest.mark.parametrize("value", ["9.0.8", "10.0.5", "10.1.0", "11.0.0", "wrong"])
def test_wrong_or_older_version_is_not_accepted(value):
    assert not installation.compatible_version(value)
