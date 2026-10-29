"""Real fixture writes/dependency drift; official KiCad acceptance is separate."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

import pytest
from driver_test_utils import grant
from test_board_update import edit

from kicad_cli import driver_runtime, sync_inputs, sync_plan, sync_readback
from kicad_cli.fileformat.sexpr import Document

FIXTURE = Path(__file__).resolve().parent / "fixtures/sch_edit"


@pytest.fixture
def case(tmp_path, monkeypatch):
    for item in FIXTURE.iterdir():
        if item.suffix == ".py" or item.name == "__pycache__":
            continue
        if item.is_dir():
            shutil.copytree(item, tmp_path / item.name)
        else:
            shutil.copyfile(item, tmp_path / item.name)
    config = tmp_path / "configuration"
    config.mkdir()
    monkeypatch.setenv("KICAD_CONFIG_HOME", str(config))
    monkeypatch.setenv("KICAD_CLI_STATE", str(tmp_path / "legacy-state"))
    env = dict(os.environ, **grant(tmp_path), PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    return tmp_path / "edit.kicad_pcb", env


def run(case, argv, **options):
    with driver_runtime.create_cli(environ=case[1], home=case[0].parent, **options) as cli:
        result = cli.run(argv)
        assert result.envelope is not None, result.stdout
        cli.validator.validate("response", result.envelope)
        return result.envelope


def command(case, *flags):
    return run(
        case,
        [
            "project",
            "sync",
            "--board",
            str(case[0]),
            "--schematic",
            str(case[0].with_suffix(".kicad_sch")),
            *flags,
        ],
    )


def apply(case):
    planned = command(case, "--dry-run")
    assert planned["ok"], planned
    done = command(case, "--confirm", planned["data"]["confirm_token"])
    assert done["ok"], done
    return planned["data"]["preview"], done["data"]["receipt"]


def test_noop_sync_is_scoped_durable_actual_readback_without_input_writes(case):
    before = {
        p: p.read_bytes()
        for p in case[0].parent.rglob("*")
        if p.is_file() and p.suffix in {".kicad_sch", ".kicad_mod", ".kicad_pcb"}
    }
    plan, receipt = apply(case)
    assert plan["changes"] == []
    assert receipt["outcome"]["change"] == "not_applied"
    assert receipt["outcome"]["persistence"] == "unchanged"
    assert receipt["outcome"]["verification"]["status"] == "passed"
    actual = sync_readback.observe(case[0])
    assert receipt["extensions"]["observed"] == actual.objects
    assert {p: p.read_bytes() for p in before} == before
    status = run(case, ["operation", "show", "--id", receipt["operation_id"]])
    assert status["data"]["operation_status"]["receipt"] == receipt


def test_values_fields_marks_and_net_rename_have_full_saved_consumer_requirements(case):
    edit(
        case[0],
        [
            {"op": "set", "ref": "R1", "value": "22k"},
            {"op": "set", "ref": "R3", "dnp": True, "fields": {"MPN": "RC0805"}},
            {"op": "rename", "net": "/SIG", "to": "DATA"},
        ],
    )
    before = sync_readback.observe(case[0])
    plan, receipt = apply(case)
    assert receipt["extensions"]["task_check"]["status"] == "passed"
    actual = {item["ref"]: item for item in receipt["extensions"]["observed"]}
    assert actual["R1"]["value"] == "22k"
    assert "dnp" in actual["R3"]["attributes"] and actual["R3"]["fields"]["MPN"] == "RC0805"
    assert actual["R1"]["pads"][0]["net"] == "/DATA"
    for old in before.objects:
        assert actual[old["ref"]]["id"] == old["id"]
        assert actual[old["ref"]]["position_nm"] == old["position_nm"]
    assert receipt["extensions"]["rows"]["renamed_nets"] == [{"from": "/SIG", "to": "/DATA"}]
    assert plan["extensions"]["expected"] == sync_readback.observe(case[0]).objects


def test_front_replacement_reads_bound_library_geometry_and_keeps_uuid_pose(case):
    old = next(item for item in sync_readback.observe(case[0]).objects if item["ref"] == "R2")
    edit(case[0], [{"op": "set", "ref": "R2", "footprint": "fixture:R_0603"}])
    plan, receipt = apply(case)
    files = plan["extensions"]["inputs"]["files"]
    assert any(row["path"].endswith("R_0603.kicad_mod") and row["present"] for row in files)
    actual = next(item for item in receipt["extensions"]["observed"] if item["ref"] == "R2")
    assert (actual["id"], actual["path"], actual["position_nm"], actual["rotation_deg"]) == (
        old["id"],
        old["path"],
        old["position_nm"],
        old["rotation_deg"],
    )
    assert actual["fpid"] == "fixture:R_0603"
    assert {abs(pad["position_nm"][0]) for pad in actual["pads"]} == {825_000}


@pytest.mark.parametrize(
    "kind",
    [
        "library",
        "root-sheet",
        "nested-sheet",
        "project-table",
        "nested-table",
        "configuration",
        "variable",
        "disappear",
        "appear",
    ],
)
def test_preview_then_input_or_resolution_drift_refuses_before_dispatch(case, monkeypatch, kind):
    board = case[0]
    edit(board, [{"op": "set", "ref": "R2", "footprint": "fixture:R_0603"}])
    config = board.parent / "configuration/10.0"
    config.mkdir()
    nested = board.parent / "nested-fp-table"
    nested.write_text("(fp_lib_table (version 7))", encoding="utf-8")
    table = board.parent / "fp-lib-table"
    raw = table.read_text("utf-8")
    table.write_text(
        raw[:-2] + '\n(lib (name nested)(type Table)(uri "${KIPRJMOD}/nested-fp-table"))\n)\n',
        encoding="utf-8",
    )
    common = config / "kicad_common.json"
    common.write_text('{"environment":{"vars":{}}}', encoding="utf-8")
    absent = config / "fp-lib-table"
    if kind == "variable":
        monkeypatch.setenv("KICAD_SYNC_FIXTURE_DIR", str(board.parent / "fixture.pretty"))
        table.write_text(
            table.read_text("utf-8").replace(
                "${KIPRJMOD}/fixture.pretty", "${KICAD_SYNC_FIXTURE_DIR}"
            ),
            encoding="utf-8",
        )
    planned = command(case, "--dry-run")
    assert planned["ok"], planned
    before = board.read_bytes()
    paths = {
        "library": board.parent / "fixture.pretty/R_0603.kicad_mod",
        "root-sheet": board.with_suffix(".kicad_sch"),
        "nested-sheet": board.parent / "edit_child.kicad_sch",
        "project-table": table,
        "nested-table": nested,
        "configuration": common,
        "disappear": board.parent / "fixture.pretty/R_0603.kicad_mod",
        "appear": absent,
    }
    if kind == "variable":
        monkeypatch.setenv("KICAD_SYNC_FIXTURE_DIR", str(board.parent / "other.pretty"))
    elif kind == "disappear":
        paths[kind].unlink()
    elif kind == "appear":
        absent.write_text("(fp_lib_table (version 7))", encoding="utf-8")
    else:
        path = paths[kind]
        path.write_bytes(path.read_bytes() + b"\n")
    refused = command(case, "--confirm", planned["data"]["confirm_token"])
    assert refused["error"]["code"] == "E_CONFLICT", refused
    assert refused["error"]["details"]["dispatch_state"] == "not_started"
    assert board.read_bytes() == before and not list(board.parent.glob("*.kicad-cli.*"))
    status = run(case, ["operation", "show", "--id", planned["data"]["preview"]["operation_id"]])
    assert status["data"]["operation_status"]["status"] == "planned"


def test_captured_bytes_are_immutable_and_planner_never_reopens_disk(case, monkeypatch):
    edit(case[0], [{"op": "set", "ref": "R2", "footprint": "fixture:R_0603"}])
    inputs = sync_inputs.capture(case[0], case[0].with_suffix(".kicad_sch"))
    expected = sync_plan.prepare(inputs)
    library = case[0].parent / "fixture.pretty/R_0603.kicad_mod"
    library.write_bytes(library.read_bytes().replace(b"0.825", b"1.425"))
    with pytest.raises(sync_inputs.Drift):
        sync_inputs.validate(inputs.manifest)
    with pytest.raises(TypeError):
        inputs.files[library] = b"mutated"
    monkeypatch.setattr(Path, "open", lambda *_a, **_kw: pytest.fail("pure planner opened disk"))
    actual = sync_plan.prepare(inputs)
    assert actual == expected


def test_missing_required_library_never_returns_partial_pass(case):
    edit(case[0], [{"op": "set", "ref": "R2", "footprint": "fixture:absent_for_scoped_audit"}])
    before = case[0].read_bytes()
    refused = command(case, "--dry-run")
    assert refused["error"]["code"] == "E_VALIDATION", refused
    assert refused["error"]["details"]["missing_footprints"] == ["fixture:absent_for_scoped_audit"]
    assert case[0].read_bytes() == before


@pytest.mark.parametrize("raw", ["[]", '{"environment":[]}', '{"environment":{"vars":[]}}'])
def test_malformed_present_configuration_refuses_without_board_write(case, raw):
    config = case[0].parent / "configuration/10.0"
    config.mkdir()
    (config / "kicad_common.json").write_text(raw, encoding="utf-8")
    before = case[0].read_bytes()
    refused = command(case, "--dry-run")
    assert refused["error"]["code"] == "E_VALIDATION", refused
    assert "must contain an object" in refused["error"]["details"]["reason"]
    assert case[0].read_bytes() == before


@pytest.mark.parametrize("raw", ['{"environment":null}', '{"environment":{"vars":null}}'])
def test_empty_native_configuration_is_valid_and_bound_in_preview(case, raw):
    config = case[0].parent / "configuration/10.0"
    config.mkdir()
    common = config / "kicad_common.json"
    common.write_text(raw, encoding="utf-8")
    before = case[0].read_bytes()
    planned = command(case, "--dry-run")
    assert planned["ok"], planned
    files = planned["data"]["preview"]["extensions"]["inputs"]["files"]
    assert any(row["path"] == str(common) and row["present"] for row in files)
    assert case[0].read_bytes() == before and common.read_text("utf-8") == raw


def test_missing_referenced_sheet_refuses_complete_source_task(case):
    (case[0].parent / "edit_child.kicad_sch").unlink()
    before = case[0].read_bytes()
    refused = command(case, "--dry-run")
    assert refused["error"]["code"] == "E_VALIDATION", refused
    assert refused["error"]["details"]["missing_sheets"]
    assert case[0].read_bytes() == before


@pytest.mark.parametrize("fault", ["ignore-save", "foreign-board", "library-after-save"])
def test_actual_failed_save_rolls_back_only_recognized_board_bytes(case, monkeypatch, fault):
    edit(case[0], [{"op": "set", "ref": "R2", "footprint": "fixture:R_0603"}])
    planned = command(case, "--dry-run")
    assert planned["ok"], planned
    before = case[0].read_bytes()
    save = Document.save
    save_paths, persisted_revisions = [], []

    def faulty(document, path):
        save_paths.append(Path(path).resolve())
        if fault == "ignore-save":
            return
        save(document, path)
        persisted_revisions.append(hashlib.sha256(path.read_bytes()).hexdigest())
        if fault == "foreign-board":
            path.write_bytes(path.read_bytes() + b"\n")
        else:
            library = path.parent / "fixture.pretty/R_0603.kicad_mod"
            library.write_bytes(library.read_bytes() + b"\n")

    monkeypatch.setattr(Document, "save", faulty)
    refused = command(case, "--confirm", planned["data"]["confirm_token"])
    assert not refused["ok"], refused
    assert save_paths == [case[0].resolve()]
    if fault != "ignore-save":
        assert persisted_revisions == [planned["data"]["preview"]["extensions"]["intended_sha256"]]
    if fault == "library-after-save":
        with driver_runtime.create_cli(environ=case[1], home=case[0].parent) as cli:
            cli.validator.validate("response", refused)
        assert "contract_errors" not in refused["error"]["details"]
        assert refused["error"]["code"] == "E_CONFLICT", refused
        assert refused["error"]["details"]["conflict"] == "state_drift"
        assert refused["error"]["details"]["dispatch_state"] == "dispatched"
    receipt = refused["error"]["details"]["receipt"]
    if fault == "library-after-save":
        assert receipt["failed_stage"] == "verify"
        assert receipt["outcome"]["change"] == "rolled_back"
    if fault == "foreign-board":
        assert refused["error"]["code"] == "E_OUTCOME_UNKNOWN"
        assert case[0].read_bytes() != before
        operation = planned["data"]["preview"]["operation_id"]
        backup = case[0].with_name(case[0].name + "." + operation + ".backup")
        assert backup.read_bytes() == before
    else:
        assert receipt["extensions"]["restoration"]["matches_baseline"]
        assert case[0].read_bytes() == before
        assert not list(case[0].parent.glob("*.kicad-cli.*"))
