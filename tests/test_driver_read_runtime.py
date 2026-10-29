"""Four real readers as one batch; faults do not become clean saved verdicts."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from kicad_cli import driver_runtime, read_inputs
from kicad_cli.commands import board, erc, sch
from kicad_cli.fileformat.sexpr import Document
from kicad_cli.read_driver_adapter import ReadAdapter

FIXTURE = Path(__file__).parent / "fixtures/sch_edit"
PATHS = ["board check", "sch check", "project parity", "project links inspect"]


@pytest.fixture
def case(tmp_path, monkeypatch):
    shutil.copytree(FIXTURE, tmp_path / "project")
    monkeypatch.setenv("KICAD_CLI_STATE", str(tmp_path / "legacy-state"))
    return tmp_path / "project/edit.kicad_pcb"


def run(case, path, *flags):
    params = (
        ["--schematic", str(case.with_suffix(".kicad_sch"))]
        if path == "sch check"
        else ["--board", str(case)]
    )
    env = dict(os.environ, KICAD_CLI_STATE_DIR=str(case.parent / "read-state"))
    env.pop("KICAD_CLI_SCOPE_FILE", None)
    with driver_runtime.create_cli(environ=env, home=case.parent) as cli:
        result = cli.run([*path.split(), *params, *flags])
        cli.validator.validate("response", result.envelope)
        return result.envelope


@pytest.mark.parametrize("path", PATHS)
def test_four_genuine_readers_without_scope_store_or_handler_dispatch(case, path, monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError("legacy envelope handler dispatched")

    for module, name in [(board, "drc"), (board, "parity"), (sch, "link"), (erc, "audit")]:
        monkeypatch.setattr(module, name, forbidden)
    before = {p: p.read_bytes() for p in case.parent.rglob("*") if p.is_file()}
    result = run(case, path)
    assert result["ok"], result
    actual = result["data"]["observation"]
    assert actual["source"]["completed"] and actual["source"]["completed_at"].endswith("Z")
    assert actual["source"]["code_sha256"] and not actual["source"]["official_native"]
    assert actual["snapshot"]["revision"] and actual["input_drift_checked"]
    assert actual["state"] == "saved_files" and actual["live_editor_observed"] is False
    assert not (case.parent / "read-state").exists()
    assert {p: p.read_bytes() for p in before} == before
    assert not any(
        p.suffix == ".kicad_mod" for p in map(lambda f: Path(f["path"]), actual["inputs"])
    )


@pytest.mark.parametrize("path", PATHS)
def test_declared_project_drift_refuses_all_four_results(case, path, monkeypatch):
    original = read_inputs.Inputs.validate
    calls = 0

    def drift(self):
        nonlocal calls
        calls += 1
        if calls == 2:
            case.with_suffix(".kicad_pro").write_text('{"board":{}}', encoding="utf-8")
        return original(self)

    monkeypatch.setattr(read_inputs.Inputs, "validate", drift)
    result = run(case, path)
    assert not result["ok"] and result["error"]["code"] == "E_CONFLICT"
    assert result["error"]["details"]["dispatch_state"] == "not_started"


@pytest.mark.parametrize("path", ["project parity", "project links inspect", "sch check"])
def test_missing_reachable_sheet_cannot_produce_a_complete_count(case, path):
    case.with_suffix(".kicad_sch").write_text(
        """(kicad_sch (version 20250114) (uuid "11111111-1111-1111-1111-111111111111")
        (lib_symbols) (sheet (at 0 0) (size 10 10) (uuid "22222222-2222-2222-2222-222222222222")
          (property "Sheetname" "Missing") (property "Sheetfile" "missing.kicad_sch")))""",
        encoding="utf-8",
    )
    result = run(case, path)
    assert not result["ok"] and result["error"]["code"] == "E_VALIDATION"
    assert result["error"]["details"]["missing_sheets"]


@pytest.mark.parametrize(
    "settings", ["[]", "{", '{"erc":{"rule_severities":[]}}', '{"erc":{"pin_map":[]}}']
)
def test_present_malformed_configuration_never_becomes_defaults(case, settings):
    case.with_suffix(".kicad_pro").write_text(settings, encoding="utf-8")
    for path in PATHS:
        result = run(case, path)
        assert not result["ok"] and result["error"]["code"] == "E_VALIDATION", (path, result)


def test_links_and_parity_measure_saved_values_and_separate_matching_basis(case):
    baseline = run(case, "project parity")
    assert baseline["ok"] and baseline["data"]["status"] == "PASS", baseline
    doc = Document.load(case)
    fp = next(
        f
        for f in doc.root.find_all("footprint")
        if any(p.value(1) == "Reference" and p.value(2) == "R1" for p in f.find_all("property"))
    )
    next(p for p in fp.find_all("property") if p.value(1) == "Value").set(2, '"different"')
    fp.find("path").set(1, '"/11111111-1111-1111-1111-111111111111"')
    doc.save(case)
    parity = run(case, "project parity")["data"]
    links = run(case, "project links inspect")["data"]
    assert parity["status"] == "FAIL" and any(
        d["kind"] == "value_mismatch" for d in parity["diffs"]
    )
    assert links["status"] == "FAIL" and links["dangling"] == 1
    actual = next(p for p in parity["observed_footprints"] if p["ref"] == "R1")
    assert actual["value"] == "different" and actual["path"].endswith("111111111111")
    assert "reference" in parity["comparison_basis"]


def test_check_limits_counts_and_coverage_remain_honest(case):
    full = run(case, "board check")["data"]
    short = run(case, "board check", "--limit", "1")["data"]
    assert (
        full["counts"] == short["counts"]
        and full["unconnected_count"] == short["unconnected_count"]
    )
    assert short["truncated"] and len(short["unconnected"]) == 1
    assert short["ok_to_fabricate"] == short["limited_checks_passed"]
    assert short["observation"]["coverage"]["native_drc_required_rules"] == ["starved_thermal"]
    assert not short["observation"]["coverage"]["manufacturing_approval"]
    assert "starved_thermal" in short["partial"] and "lib_footprint_issues" in short["not_checked"]
    for invalid in ["0", "-1", "10001"]:
        result = run(case, "board check", "--limit", invalid)
        assert not result["ok"] and result["error"]["code"] == "E_VALIDATION"


def test_erc_reports_configured_exclusions_as_unapplied(case):
    project = case.with_suffix(".kicad_pro")
    project.write_text(
        json.dumps({"erc": {"erc_exclusions": ["owned diagnostic exclusion"]}}), encoding="utf-8"
    )
    data = run(case, "sch check")["data"]
    assert (
        data["configured_exclusions"] == ["owned diagnostic exclusion"]
        and data["exclusions_applied"] is False
    )
    assert data["detail_samples"]["configured"]


def test_captured_netlist_and_erc_do_not_reopen_project_bytes(case, monkeypatch):
    inputs = read_inputs.capture(schematic=case.with_suffix(".kicad_sch"))
    original = Path.read_bytes

    def no_project(self):
        if self.suffix == ".kicad_pro":
            raise AssertionError("project reopened outside immutable bytes")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", no_project)
    ReadAdapter().sch_check(inputs)
    from kicad_cli.fileformat import netlist  # noqa: PLC0415

    netlist.build(inputs.design)
