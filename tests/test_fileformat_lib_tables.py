"""KiCad's library tables, read as files: which library a nickname names."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from kicad_cli.fileformat import lib_tables  # noqa: E402


def table(kind: str, *entries: str) -> bytes:
    head = "sym_lib_table" if kind == "sym-lib-table" else "fp_lib_table"
    return (f"({head} (version 7)\n" + "".join(f"  {e}\n" for e in entries) + ")\n").encode()


def lib(name: str, uri: str, kind: str = "KiCad", extra: str = "") -> str:
    return f'(lib (name "{name}") (type "{kind}") (uri "{uri}") (options "") (descr ""){extra})'


def symbol_library(*symbols: str, version: int = 20251024) -> bytes:
    return (
        f'(kicad_symbol_lib (version {version}) (generator "test") ' + " ".join(symbols) + ")\n"
    ).encode()


R = (
    '(symbol "R" (pin_names (offset 0)) (in_bom yes) (on_board yes)'
    ' (property "Reference" "R" (at 0 0 0) (effects (font (size 1.27 1.27))))'
    ' (property "Value" "R" (at 0 0 0) (effects (font (size 1.27 1.27))))'
    ' (property "Description" "Resistor" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))'
    ' (symbol "R_0_1" (rectangle (start -1 -2) (end 1 2) (stroke (width 0.254) (type default))'
    " (fill (type none))))"
    ' (symbol "R_1_1" (pin passive line (at 0 3.81 270) (length 1.27)'
    ' (name "~" (effects (font (size 1.27 1.27))))'
    ' (number "1" (effects (font (size 1.27 1.27)))))))'
)
DERIVED = (
    '(symbol "R_small" (extends "R")'
    ' (property "Value" "R_small" (at 0 0 0) (effects (font (size 1.27 1.27))))'
    ' (property "Description" "Small" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes))))'
)


def project(tmp_path: Path, **tables: bytes) -> Path:
    directory = tmp_path / "project"
    directory.mkdir()
    for name, content in tables.items():
        (directory / name.replace("_", "-")).write_bytes(content)
    return directory


def test_a_projects_table_names_its_own_libraries(tmp_path):
    directory = project(
        tmp_path, sym_lib_table=table("sym-lib-table", lib("mine", "${KIPRJMOD}/mine.kicad_sym"))
    )
    tables = lib_tables.Tables.of(directory, None, tmp_path / "no-config")
    mine = tables.symbols["mine"]
    assert (mine.kind, mine.uri) == ("KiCad", "${KIPRJMOD}/mine.kicad_sym")
    assert mine.path == directory / "mine.kicad_sym"
    # As KiCad names it in a message: the project's folder, then the uri's own separators.
    assert mine.location == f"{directory}/mine.kicad_sym"
    assert tables.footprints == {}


def test_the_users_table_is_read_first_and_the_projects_wins_a_nickname_both_use(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "sym-lib-table").write_bytes(
        table(
            "sym-lib-table",
            lib("shared", "C:/users/shared.kicad_sym"),
            lib("theirs", "C:/users/theirs.kicad_sym"),
        )
    )
    directory = project(
        tmp_path,
        sym_lib_table=table("sym-lib-table", lib("shared", "${KIPRJMOD}/shared.kicad_sym")),
    )
    tables = lib_tables.Tables.of(directory, None, config)
    assert tables.symbols["shared"].path == directory / "shared.kicad_sym"
    assert tables.symbols["theirs"].path == Path("C:/users/theirs.kicad_sym")


def test_a_nested_table_is_read_and_a_disabled_library_is_as_good_as_absent(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (tmp_path / "installed-table").write_bytes(
        table("fp-lib-table", lib("Resistor_SMD", "${KICAD10_FOOTPRINT_DIR}/Resistor_SMD.pretty"))
    )
    (config / "fp-lib-table").write_bytes(
        table(
            "fp-lib-table",
            lib("KiCad", str(tmp_path / "installed-table").replace("\\", "/"), kind="Table"),
            lib("off", "C:/off.pretty", extra=" (disabled)"),
        )
    )
    kicad = tmp_path / "kicad"
    tables = lib_tables.Tables.of(project(tmp_path), kicad, config)
    assert set(tables.footprints) == {"Resistor_SMD"}
    assert tables.footprints["Resistor_SMD"].path == (
        kicad / "share" / "kicad" / "footprints" / "Resistor_SMD.pretty"
    )


def test_every_kicad_versions_variables_name_this_installation(tmp_path, monkeypatch):
    """A project from KiCad 6 names ${KICAD6_SYMBOL_DIR}; KiCad 10 still defines it."""
    for name in [n for n in os.environ if n.startswith("KICAD")]:
        monkeypatch.delenv(name)
    kicad = tmp_path / "kicad"
    variables = lib_tables._variables(tmp_path, kicad, None)
    symbols = str(kicad / "share" / "kicad" / "symbols")
    assert variables["KICAD6_SYMBOL_DIR"] == variables["KICAD10_SYMBOL_DIR"] == symbols
    assert variables["KICAD_SYMBOL_DIR"] == symbols
    assert variables["KICAD8_3DMODEL_DIR"] == str(kicad / "share" / "kicad" / "3dmodels")
    assert variables["KIPRJMOD"] == str(tmp_path)


def test_kicads_settings_then_the_environment_define_variables(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    (config / "kicad_common.json").write_text(
        json.dumps({"environment": {"vars": {"MYLIBS": "D:/libs", "KICAD_USER": "D:/settings"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("KICAD_USER", "D:/environment")
    variables = lib_tables._variables(tmp_path, None, config)
    assert variables["MYLIBS"] == "D:/libs"
    assert variables["KICAD_USER"] == "D:/environment"


def test_a_variable_nothing_defines_is_left_as_it_is():
    assert (
        lib_tables.expand("${KIPRJMOD}/a/$(OLD)/${NOPE}", {"KIPRJMOD": "P", "OLD": "o"})
        == "P/a/o/${NOPE}"
    )


def test_a_librarys_symbols_are_named_and_a_derived_one_is_given_whole(tmp_path):
    (tmp_path / "project").mkdir()
    (tmp_path / "project" / "mine.kicad_sym").write_bytes(symbol_library(R, DERIVED))
    (tmp_path / "project" / "sym-lib-table").write_bytes(
        table("sym-lib-table", lib("mine", "${KIPRJMOD}/mine.kicad_sym"))
    )
    tables = lib_tables.Tables.of(tmp_path / "project", None, tmp_path / "no-config")
    mine = tables.symbols["mine"]
    assert set(tables.symbol_names(mine)) == {"R", "R_small"}
    node, version = tables.symbol(mine, "R_small")
    assert version == 20251024
    assert node.value(1) == "mine:R_small"
    fields = {p.value(1): p.value(2) for p in node.find_all("property")}
    assert fields == {"Reference": "R", "Value": "R_small", "Description": "Small"}
    # The parent's drawing, named as the derived symbol's own.
    assert [u.value(1) for u in node.find_all("symbol")] == ["R_small_0_1", "R_small_1_1"]
    assert tables.symbol(mine, "Q") is None


def test_a_rescue_librarys_symbol_is_found_by_its_name_after_the_colon(tmp_path):
    path = tmp_path / "rescue.kicad_sym"
    path.write_bytes(symbol_library(R.replace('(symbol "R" ', '(symbol "project-rescue:R" ', 1)))
    tables = lib_tables.Tables()
    rescue = lib_tables.Library("rescue", "KiCad", str(path), path)
    assert set(tables.symbol_names(rescue)) == {"R"}
    assert tables.symbol(rescue, "R") is not None


def test_a_library_that_does_not_read_as_one_has_no_names(tmp_path):
    broken = tmp_path / "broken.kicad_sym"
    broken.write_bytes(b"(kicad_symbol_lib (symbol ")
    tables = lib_tables.Tables()
    assert tables.symbol_names(lib_tables.Library("b", "KiCad", str(broken), broken)) is None
    missing = tmp_path / "missing.kicad_sym"
    assert tables.symbol_names(lib_tables.Library("m", "KiCad", str(missing), missing)) is None
    legacy = tmp_path / "old.lib"
    legacy.write_bytes(b"EESchema-LIBRARY Version 2.4\n")
    assert tables.symbol_names(lib_tables.Library("o", "Legacy", str(legacy), legacy)) is None


def test_kicads_configuration_directory_can_be_moved(tmp_path, monkeypatch):
    monkeypatch.setenv("KICAD_CONFIG_HOME", str(tmp_path))
    assert lib_tables.default_config_dir() == tmp_path / lib_tables.VERSION
