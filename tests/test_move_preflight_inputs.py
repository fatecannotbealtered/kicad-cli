"""Pure host dependency binding and isolation checks; no KiCad subprocesses."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from kicad_cli import move_preflight_inputs as inputs
from kicad_cli.fileformat.board import Board
from kicad_cli.fileformat.sexpr import Document
from kicad_cli.sync_inputs import Drift, InputError

FIXTURE = Path(__file__).parent / "fixtures/sch_edit"


@pytest.fixture
def case(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    for name in ["edit.kicad_pcb", "edit.kicad_pro", "fp-lib-table"]:
        shutil.copyfile(FIXTURE / name, project / name)
    shutil.copytree(FIXTURE / "fixture.pretty", project / "fixture.pretty")
    config = tmp_path / "configuration"
    version = config / "10.0"
    version.mkdir(parents=True)
    (version / "kicad_common.json").write_text('{"environment":{"vars":{}}}', encoding="utf-8")
    (version / "pcbnew.json").write_text('{"unmodified":true}', encoding="utf-8")
    (version / "empty").mkdir()
    installation = tmp_path / "installation"
    (installation / "bin").mkdir(parents=True)
    official = installation / "bin/kicad-cli.exe"
    official.write_bytes(b"synthetic-not-executable")
    (official.parent / "native.dll").write_bytes(b"synthetic-DLL")
    (installation / "share/kicad/resources").mkdir(parents=True)
    (installation / "share/kicad/resources/data").write_bytes(b"native-resource")
    monkeypatch.setenv("KICAD_CONFIG_HOME", str(config))
    monkeypatch.setenv("KICAD_CLI_ROOT", str(installation))
    monkeypatch.setenv("KICAD_CLI_OFFICIAL", str(official))
    monkeypatch.setattr(inputs.kicad_env, "find_kicad_root", lambda: str(installation))
    monkeypatch.setattr(inputs.kicad_env, "find_official_cli", lambda: str(official))
    return {
        "board": project / "edit.kicad_pcb",
        "project": project,
        "config": config,
        "installation": installation,
        "official": official,
        "workspace": tmp_path / "workspace",
    }


def capture(case):
    return inputs.capture(case["board"], Board.load(case["board"]), case["workspace"])


def test_exact_staging_keeps_sources_rules_and_configuration_untouched(case):
    before = {
        path: path.read_bytes()
        for root in [case["project"], case["config"]]
        for path in root.rglob("*")
        if path.is_file()
    }
    binding = capture(case)
    assert json.loads(json.dumps(binding)) == binding
    assert not case["workspace"].exists()
    staged = inputs.stage(binding, case["board"].read_bytes())
    assert set(staged["stages"]) == {"baseline", "candidate", "preparation"}
    for value in staged["stages"].values():
        board = Path(value["board"])
        assert board.read_bytes() == before[case["board"]]
        assert (
            board.with_suffix(".kicad_pro").read_bytes()
            == before[case["project"] / "edit.kicad_pro"]
        )
        assert not board.with_suffix(".kicad_dru").exists()
        table = Document.load(board.parent / "fp-lib-table")
        libraries = table.root.find_all("lib")
        assert libraries
        for library in libraries:
            resolved = Path(library.find("uri").value(1)).resolve(strict=True)
            assert resolved.is_relative_to(case["workspace"].resolve())
            assert not resolved.is_relative_to(case["project"].resolve())
            assert not resolved.is_relative_to(case["config"].resolve())
            assert resolved.name.endswith(".pretty")
            assert all(p.is_file() for p in resolved.glob("*.kicad_mod"))
        assert (Path(value["config_root"]) / "10.0/empty").is_dir()
        assert Path(value["declared_preparation_directory"]) == (
            Path(value["config_root"]) / "10.0/colors"
        )
        assert Path(value["declared_preparation_directory"]).is_dir()
        assert not list(Path(value["declared_preparation_directory"]).iterdir())
        documents = Path(value["config_root"]) / "documents/KiCad/10.0"
        assert {p.name for p in documents.iterdir()} == {
            "3dmodels",
            "3rdparty",
            "footprints",
            "plugins",
            "projects",
            "scripting",
            "symbols",
            "template",
        }
        assert not [p for p in documents.rglob("*") if p.is_file()]
        if os.name == "nt":
            fallback = board.parent / "KiCad/10.0"
            assert fallback.is_dir() and not list(fallback.iterdir())
    assert all(path.read_bytes() == raw for path, raw in before.items())
    for row in staged["relocations"]:
        assert inputs._sha(Path(row["destination"]).read_bytes()) == row["sha256"]
    inputs.validate(binding)


def test_distinct_candidate_cannot_overwrite_the_baseline_or_preparation(case):
    binding = capture(case)
    candidate = case["board"].read_bytes().replace(b"(at 100 100)", b"(at 98.5 100)", 1)
    assert candidate != case["board"].read_bytes()
    staged = inputs.stage(binding, candidate)
    assert Path(staged["stages"]["candidate"]["board"]).read_bytes() == candidate
    assert Path(staged["stages"]["baseline"]["board"]).read_bytes() == case["board"].read_bytes()
    assert Path(staged["stages"]["preparation"]["board"]).read_bytes() == case["board"].read_bytes()


@pytest.mark.parametrize(
    "name", ["edit.kicad_pcb", "edit.kicad_pro", "fp-lib-table", "fixture.pretty/R_0805.kicad_mod"]
)
@pytest.mark.parametrize("change", ["modify", "delete", "replace_same_bytes"])
def test_required_source_changes_refuse_before_any_stage(case, name, change):
    binding = capture(case)
    path = case["project"] / name
    raw = path.read_bytes()
    if change == "modify":
        path.write_bytes(raw + b"\n")
    elif change == "delete":
        path.unlink()
    else:
        replacement = path.with_name(path.name + ".replacement")
        replacement.write_bytes(raw)
        os.replace(replacement, path)
    with pytest.raises((Drift, InputError)):
        inputs.stage(binding, case["board"].read_bytes() if case["board"].exists() else b"invalid")
    assert not case["workspace"].exists()


@pytest.mark.parametrize("suffix", [".kicad_pro", ".kicad_dru"])
def test_absent_rule_or_project_addition_is_drift(case, suffix):
    path = case["board"].with_suffix(suffix)
    path.unlink(missing_ok=True)
    binding = capture(case)
    path.write_text("{}" if suffix == ".kicad_pro" else "(version 1)", encoding="utf-8")
    with pytest.raises(Drift):
        inputs.validate(binding)


def test_present_custom_rules_are_exact_and_deletion_refuses(case):
    rules = case["board"].with_suffix(".kicad_dru")
    raw = b'(version 1)\n(rule "keep" (constraint clearance (min 0.2mm)))\n'
    rules.write_bytes(raw)
    binding = capture(case)
    staged = inputs.stage(binding, case["board"].read_bytes())
    assert all(
        Path(row["board"]).with_suffix(".kicad_dru").read_bytes() == raw
        for row in staged["stages"].values()
    )
    rules.unlink()
    with pytest.raises(Drift):
        inputs.validate(binding)


@pytest.mark.parametrize(
    "change", ["new_file", "new_directory", "modify", "delete", "replace_same_bytes"]
)
def test_entire_configuration_tree_is_bound(case, change):
    binding = capture(case)
    path = case["config"] / "10.0/pcbnew.json"
    raw = path.read_bytes()
    if change == "new_file":
        (case["config"] / "undocumented.json").write_bytes(b"{}")
    elif change == "new_directory":
        (case["config"] / "new-empty").mkdir()
    elif change == "modify":
        path.write_bytes(b'{"unmodified":false}')
    elif change == "delete":
        path.unlink()
    else:
        replacement = case["config"] / "replacement"
        replacement.write_bytes(raw)
        os.replace(replacement, path)
    with pytest.raises((Drift, InputError)):
        inputs.validate(binding)


@pytest.mark.parametrize("change", ["exe", "DLL", "new_DLL", "resource", "new_resource"])
def test_official_tool_dependency_drift_is_rejected(case, change):
    binding = capture(case)
    if change == "exe":
        case["official"].write_bytes(b"changed")
    elif change == "DLL":
        (case["official"].parent / "native.dll").write_bytes(b"changed")
    elif change == "new_DLL":
        (case["official"].parent / "new.dll").write_bytes(b"new")
    elif change == "resource":
        (case["installation"] / "share/kicad/resources/data").unlink()
    else:
        (case["installation"] / "share/kicad/resources/new").write_bytes(b"new")
    with pytest.raises((Drift, InputError)):
        inputs.validate(binding)


def test_resolved_variable_is_bound_without_disclosing_its_value(case, monkeypatch):
    table = case["project"] / "fp-lib-table"
    table.write_text(
        '(fp_lib_table (lib (name "fixture")(type "KiCad")(uri "${KICAD_TEST_FIXTURES}")))',
        encoding="utf-8",
    )
    monkeypatch.setenv("KICAD_TEST_FIXTURES", str(case["project"] / "fixture.pretty"))
    binding = capture(case)
    assert binding["libraries"]["variables_sha256"]["KICAD_TEST_FIXTURES"] == inputs._sha(
        str(case["project"] / "fixture.pretty").encode()
    )
    monkeypatch.setenv("KICAD_TEST_FIXTURES", str(case["project"]))
    with pytest.raises(Drift):
        inputs.validate(binding)


@pytest.mark.parametrize(
    "uri,kind",
    [
        ("https://example.invalid/library", "KiCad"),
        ("${MISSING_LIBRARY_VARIABLE}", "KiCad"),
        ("${KIPRJMOD}/fixture.pretty", "Github"),
    ],
)
def test_unsupported_library_resolution_refuses_capture(case, uri, kind):
    (case["project"] / "fp-lib-table").write_text(
        f'(fp_lib_table (lib (name "fixture")(type "{kind}")(uri "{uri}")))', encoding="utf-8"
    )
    with pytest.raises(InputError):
        capture(case)
    assert not case["workspace"].exists()


def test_nested_and_absolute_table_libraries_are_copied_and_relocated(case):
    nested = case["project"].parent / "nested.fp-table"
    library = (case["project"] / "fixture.pretty").as_posix()
    nested.write_text(
        f'(fp_lib_table (lib (name "fixture")(type "KiCad")(uri "{library}")))',
        encoding="utf-8",
    )
    (case["project"] / "fp-lib-table").write_text(
        f'(fp_lib_table (lib (name "included")(type "Table")(uri "{nested.as_posix()}")))',
        encoding="utf-8",
    )
    binding = capture(case)
    assert str(nested) in binding["libraries"]["consulted_tables"]
    staged = inputs.stage(binding, case["board"].read_bytes())
    assert any(
        row["source"] == str(nested) and row["role"] == "original_table_exact_evidence"
        for row in staged["relocations"]
    )
    nested.write_bytes(nested.read_bytes() + b"\n")
    with pytest.raises(Drift):
        inputs.validate(binding)


def test_cyclic_nested_tables_are_refused(case):
    table = case["project"] / "fp-lib-table"
    table.write_text(
        '(fp_lib_table (lib (name "loop")(type "Table")(uri "${KIPRJMOD}/fp-lib-table")))',
        encoding="utf-8",
    )
    with pytest.raises(InputError, match="Cyclic"):
        capture(case)


@pytest.mark.parametrize(
    "target", ["configuration_file", "library_file", "configuration_directory"]
)
def test_links_are_rejected_before_following(case, tmp_path, target):
    external = tmp_path / "external"
    external.mkdir()
    (external / "data").write_bytes(b"not read through a link")
    path = (
        case["config"] / "10.0/link"
        if target.startswith("configuration")
        else case["project"] / "fixture.pretty/R_0805.kicad_mod"
    )
    if path.exists():
        path.unlink()
    try:
        path.symlink_to(
            external if target.endswith("directory") else external / "data",
            target_is_directory=target.endswith("directory"),
        )
    except OSError as exc:
        pytest.skip(f"Host cannot create test symlinks: {exc}")
    with pytest.raises(InputError, match="links"):
        capture(case)


def test_stage_cannot_reuse_a_populated_workspace(case):
    binding = capture(case)
    case["workspace"].mkdir()
    (case["workspace"] / "unrelated").write_bytes(b"preserve")
    with pytest.raises(InputError, match="new and empty"):
        inputs.stage(binding, case["board"].read_bytes())
    assert (case["workspace"] / "unrelated").read_bytes() == b"preserve"


@pytest.mark.parametrize("label", ["project", "config", "installation"])
def test_workspace_must_not_pollute_protected_input_trees(case, label):
    case["workspace"] = case[label] / "temporary-preflight"
    with pytest.raises(InputError, match="separate"):
        capture(case)
    assert not case["workspace"].exists()


def test_capture_rejects_a_board_parsed_from_different_bytes(case):
    board = Board.load(case["board"])
    case["board"].write_bytes(case["board"].read_bytes() + b"\n")
    with pytest.raises(Drift, match="exact captured"):
        inputs.capture(case["board"], board, case["workspace"])


def test_configuration_initialization_only_mutates_the_preparation_clone(case):
    binding = capture(case)
    staged = inputs.stage(binding, case["board"].read_bytes())
    prepared = Path(staged["stages"]["preparation"]["config_root"]) / "10.0/pcbnew.json"
    prepared.write_text('{"initialized":true}', encoding="utf-8")
    assert (case["config"] / "10.0/pcbnew.json").read_text(
        encoding="utf-8"
    ) == '{"unmodified":true}'
    for label in ["baseline", "candidate"]:
        assert (Path(staged["stages"][label]["config_root"]) / "10.0/pcbnew.json").read_text(
            encoding="utf-8"
        ) == '{"unmodified":true}'
    inputs.validate(binding)


def test_configuration_absence_is_bound_and_cloned_to_empty_private_root(case, monkeypatch):
    shutil.rmtree(case["config"])
    binding = capture(case)
    assert binding["configuration_tree"]["present"] is False
    staged = inputs.stage(binding, case["board"].read_bytes())
    assert all(Path(row["config_root"]).is_dir() for row in staged["stages"].values())
    case["config"].mkdir()
    with pytest.raises(Drift, match="configuration"):
        inputs.validate(binding)


def test_library_directory_addition_is_not_an_undeclared_input(case):
    binding = capture(case)
    (case["project"] / "fixture.pretty/new.kicad_mod").write_bytes(b'(footprint "new")')
    with pytest.raises(Drift, match="library directory"):
        inputs.validate(binding)


def test_library_options_are_refused_instead_of_silently_dropped(case):
    table = case["project"] / "fp-lib-table"
    table.write_bytes(table.read_bytes().replace(b'(options "")', b'(options "unsupported=1")'))
    with pytest.raises(InputError, match="options"):
        capture(case)


@pytest.mark.parametrize("raw", [b"{", b'{"rule":1,"rule":2}', b'{"rule":NaN}', b"[]"])
@pytest.mark.parametrize("target", ["project", "configuration"])
def test_malformed_rules_or_preferences_cannot_silently_fall_back(case, raw, target):
    path = (
        case["board"].with_suffix(".kicad_pro")
        if target == "project"
        else case["config"] / "10.0/pcbnew.json"
    )
    path.write_bytes(raw)
    with pytest.raises(InputError, match="JSON"):
        capture(case)


def test_hardlinked_dependency_is_rejected(case, tmp_path):
    library = case["project"] / "fixture.pretty/R_0805.kicad_mod"
    os.link(library, tmp_path / "other-owner.kicad_mod")
    with pytest.raises(InputError, match="single regular"):
        capture(case)


def test_tool_manifest_is_bounded_before_native_dispatch(case, monkeypatch):
    monkeypatch.setattr(inputs, "MAX_TOOL_FILES", 1)
    with pytest.raises(InputError, match="bounds"):
        capture(case)
