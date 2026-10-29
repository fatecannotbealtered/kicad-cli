"""Finite hierarchy declarations and captured host paths; no native consumers."""

import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from kicad_cli import engineering_inputs, engineering_prepare
from kicad_cli.engineering_feedback import can_apply, feedback
from kicad_cli.sch_hierarchy_contract import hierarchy_plan, hierarchy_problems
from kicad_cli.schematic import spec_problems
from kicad_cli.sync_inputs import Drift


def intent():
    return {
        "parts": [
            {"ref": "U1", "symbol": "Amplifier_Operational:LM358", "sheet_id": "root"},
            {"ref": "R10", "symbol": "Device:R", "sheet_id": "A", "value": "12k"},
            {"ref": "C10", "symbol": "Device:C", "sheet_id": "A", "in_bom": False},
            {"ref": "R20", "symbol": "Device:R", "sheet_id": "B", "value": "10k"},
            {"ref": "C20", "symbol": "Device:C", "sheet_id": "B"},
        ],
        "nets": [
            {"name": "A_RC", "connect": ["U1.3", "R10.1"]},
            {"name": "A_OUT", "connect": ["R10.2", "C10.1"]},
            {"name": "B_RC", "connect": ["U1.5", "R20.1"]},
            {"name": "B_OUT", "connect": ["R20.2", "C20.1"]},
            {"name": "GND", "connect": ["U1.4", "C10.2", "C20.2"]},
            {"name": "+3V3", "connect": ["U1.8", "U1.1"]},
        ],
        "hierarchy": {
            "schema": "direct-two-child/v1",
            "namespace_uuid": "00000000-0000-4000-8000-000000000001",
            "root": {"id": "root", "title": "Power and buffers"},
            "children": [
                {
                    "id": "A",
                    "name": "CHANNEL_A",
                    "file": "channel_a.kicad_sch",
                    "title": "Channel A",
                    "local_nets": {"A_RC": "RC", "A_OUT": "OUT"},
                    "ports": [{"name": "RC", "direction": "output", "net": "A_RC"}],
                },
                {
                    "id": "B",
                    "name": "CHANNEL_B",
                    "file": "channel_b.kicad_sch",
                    "title": "Channel B",
                    "local_nets": {"B_RC": "RC", "B_OUT": "OUT"},
                    "ports": [{"name": "RC", "direction": "output", "net": "B_RC"}],
                },
            ],
            "shared_power_nets": ["+3V3", "GND"],
            "canonical_nets": {
                "A_RC": "/CHANNEL_A/RC",
                "A_OUT": "/CHANNEL_A/OUT",
                "B_RC": "/CHANNEL_B/RC",
                "B_OUT": "/CHANNEL_B/OUT",
                "+3V3": "+3V3",
                "GND": "GND",
            },
        },
    }


def test_declared_names_and_deterministic_distinct_document_port_identities():
    spec = intent()
    assert not spec_problems(spec) and not hierarchy_problems(spec, "dual")
    planned = hierarchy_plan(spec, "dual")
    assert planned == hierarchy_plan(deepcopy(spec), "dual")
    assert planned["output_names"] == [
        "dual.kicad_sch",
        "channel_a.kicad_sch",
        "channel_b.kicad_sch",
        "dual.net",
    ]
    assert planned["canonical_nets"]["A_RC"] != planned["canonical_nets"]["B_RC"]
    ids = [planned["root"]["uuid"]]
    for child in planned["children"]:
        ids += [child["sheet_uuid"], child["document_uuid"]]
        assert (
            child["instance_path"] == planned["root"]["instance_path"] + "/" + child["sheet_uuid"]
        )
        for port in child["ports"]:
            ids += [port["uuid"], port["label_uuid"]]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    "case",
    [
        "missing-pin",
        "wrong-port",
        "missing-child",
        "nested",
        "shared-file",
        "root-collision",
        "parent-path",
        "absolute-path",
        "reserved",
        "case-id",
        "duplicate-ref",
        "missing-assignment",
        "wrong-canonical",
        "joined-locals",
        "cross-both",
        "bus",
        "missing-namespace",
        "missing-unit-page",
        "unsupported-flags",
        "pin-in-two-nets",
    ],
)
def test_unsupported_or_inconsistent_hierarchy_refuses_before_library_resolution(case):
    spec = intent()
    hierarchy, child = spec["hierarchy"], spec["hierarchy"]["children"][0]
    if case == "missing-pin":
        child["ports"] = []
    elif case == "wrong-port":
        child["ports"][0]["name"] = "TYPO"
    elif case == "missing-child":
        hierarchy["children"].pop()
    elif case == "nested":
        child["children"] = []
    elif case == "shared-file":
        hierarchy["children"][1]["file"] = child["file"].upper()
    elif case == "root-collision":
        child["file"] = "dual.kicad_sch"
    elif case == "parent-path":
        child["file"] = "../escape.kicad_sch"
    elif case == "absolute-path":
        child["file"] = "C:\\escape.kicad_sch"
    elif case == "reserved":
        child["file"] = "CON.kicad_sch"
    elif case == "case-id":
        hierarchy["children"][1]["id"] = "a"
    elif case == "duplicate-ref":
        spec["parts"][1]["ref"] = "u1"
    elif case == "missing-assignment":
        del spec["parts"][1]["sheet_id"]
    elif case == "wrong-canonical":
        hierarchy["canonical_nets"]["A_RC"] = "/RC"
    elif case == "joined-locals":
        child["local_nets"]["A_OUT"] = "RC"
    elif case == "cross-both":
        spec["nets"][0]["connect"].append("R20.1")
    elif case == "bus":
        child["local_nets"]["A_RC"] = "BUS[0..3]"
    elif case == "missing-namespace":
        del hierarchy["namespace_uuid"]
    elif case == "missing-unit-page":
        spec["parts"][0]["sheet_id"] = "missing"
    elif case == "unsupported-flags":
        spec["parts"][0]["dnp"] = True
    elif case == "pin-in-two-nets":
        spec["nets"][1]["connect"].append("U1.3")
    assert spec_problems(spec) or hierarchy_problems(spec, "dual")


def test_old_single_page_schema_and_plan_remain_compatible():
    spec = intent()
    del spec["hierarchy"]
    for part in spec["parts"]:
        del part["sheet_id"]
    assert not spec_problems(spec) and hierarchy_plan(spec, "dual") is None
    spec["parts"][0]["sheet_id"] = "root"
    assert spec_problems(spec)


def captured(tmp_path, monkeypatch):
    spec = tmp_path / "dual.json"
    spec.write_text(json.dumps(intent()), encoding="utf-8")
    monkeypatch.setattr(engineering_inputs.kicad_env, "find_kicad_root", lambda: None)
    monkeypatch.setattr(engineering_inputs.lib_tables, "default_config_dir", lambda: None)
    monkeypatch.setattr(
        engineering_inputs.lib_tables.Tables,
        "of",
        lambda *args: SimpleNamespace(symbols={}, footprints={}),
    )
    return spec


def test_capture_binds_spec_raw_bytes_full_absent_set_and_consulted_project(tmp_path, monkeypatch):
    spec = captured(tmp_path, monkeypatch)
    inputs, primary = engineering_inputs.capture(
        "sch create", {"spec": str(spec), "out": str(tmp_path)}
    )
    assert primary == tmp_path / "dual.kicad_sch"
    for name in hierarchy_plan(intent(), "dual")["output_names"] + ["dual.kicad_pro"]:
        assert tmp_path / name in inputs.files and inputs.files[tmp_path / name] is None
    assert inputs.files[spec] == spec.read_bytes()
    (tmp_path / "channel_b.kicad_sch").write_bytes(b"unexpected output appeared")
    with pytest.raises(Drift):
        inputs.validate()


@pytest.mark.parametrize(
    "name",
    ["dual.kicad_sch", "channel_a.kicad_sch", "channel_b.kicad_sch", "dual.net", "dual.kicad_pro"],
)
def test_existing_output_conflict_does_not_overwrite_any_member(tmp_path, monkeypatch, name):
    spec = captured(tmp_path, monkeypatch)
    target = tmp_path / name
    target.write_bytes(b"retained pre-existing engineering input")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    with pytest.raises(FileExistsError):
        engineering_inputs.capture("sch create", {"spec": str(spec), "out": str(tmp_path)})
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == before


def report(name, raw):
    return {
        "schema": "sch-create-geometry/v1",
        "source": {"file": name, "sha256": hashlib.sha256(raw).hexdigest()},
        "geometry_check": "partial_conservative_schematic_bounds",
        "findings": [],
        "unmeasured_objects": [],
    }


@pytest.mark.parametrize(
    "case",
    [
        "complete",
        "stale-child",
        "missing-page",
        "duplicate-page",
        "wrong-written",
        "mapping-missing",
    ],
)
def test_three_page_candidate_requires_exact_output_mapping_and_each_page_bytes(
    tmp_path, monkeypatch, case
):
    from kicad_cli.native import sch_create
    from kicad_cli.payload import write_txn

    spec = tmp_path / "dual.json"
    spec.write_text(json.dumps(intent()), encoding="utf-8")
    original, candidate = tmp_path / "formal", tmp_path / "candidate"
    original.mkdir()
    candidate.mkdir()
    planned = hierarchy_plan(intent(), "dual")
    mapping = {str(spec.resolve()): str(spec.resolve())} | {
        str(original / name): str(candidate / name) for name in planned["output_names"]
    }
    if case == "mapping-missing":
        del mapping[str(original / "channel_b.kicad_sch")]

    def controlled_create(*args):
        pages = []
        for name in planned["output_names"]:
            raw = ("controlled disposable candidate " + name).encode()
            (candidate / name).write_bytes(raw)
            if name.endswith(".kicad_sch"):
                pages.append(report(name, raw))
        if case == "stale-child":
            pages[1]["source"]["sha256"] = "0" * 64
        if case == "missing-page":
            pages.pop()
        if case == "duplicate-page":
            pages[2] = deepcopy(pages[1])
        written = {
            "schematic": str(candidate / "dual.kicad_sch"),
            "children": [str(candidate / row["file"]) for row in planned["children"]],
            "netlist": str(candidate / "dual.net"),
        }
        if case == "wrong-written":
            written["children"].pop()
        return {
            "hierarchy": planned,
            "written": written,
            "creation_geometry": {
                "schema": "sch-create-geometry/v2",
                "geometry_check": "partial_conservative_schematic_bounds",
                "pages": pages,
                "findings": [],
                "unmeasured_objects": [],
            },
        }

    monkeypatch.setattr(sch_create, "create", controlled_create)
    monkeypatch.setattr(write_txn, "commit", lambda: None)
    request = {
        "command": "sch create",
        "params": {"spec": str(spec)},
        "mapping": mapping,
        "primary": str(candidate / "dual.kicad_sch"),
        "original_primary": str(original / "dual.kicad_sch"),
    }
    if case in {"missing-page", "duplicate-page", "wrong-written", "mapping-missing"}:
        with pytest.raises(ValueError):
            engineering_prepare.prepare(request)
    else:
        value = engineering_prepare.prepare(request)
        if case == "stale-child":
            assert value["ok"] is False and value["error"]["code"] == "E_CONFLICT"
        else:
            assert value["ok"] is True
            assert {row["path"] for row in value["files"]} == {
                str(original / name) for name in planned["output_names"]
            }
            assert can_apply(feedback("sch create", value["result"]))
    assert list(original.iterdir()) == []


@pytest.mark.parametrize(
    "case", ["missing-child", "duplicate-child", "incomplete-child", "root-only"]
)
def test_feedback_does_not_use_root_report_to_claim_complete_hierarchy(case):
    planned = hierarchy_plan(intent(), "dual")
    pages = [
        report(name, name.encode())
        for name in planned["output_names"]
        if name.endswith(".kicad_sch")
    ]
    geometry = {
        "schema": "sch-create-geometry/v2",
        "geometry_check": "partial_conservative_schematic_bounds",
        "pages": pages,
        "findings": [],
        "unmeasured_objects": [],
    }
    if case == "missing-child":
        pages.pop()
    elif case == "duplicate-child":
        pages[2] = deepcopy(pages[1])
    elif case == "incomplete-child":
        pages[1]["geometry_check"] = "incomplete"
    else:
        geometry = pages[0]
    value = feedback("sch create", {"hierarchy": planned, "creation_geometry": geometry})
    assert value["geometry_check"] == "incomplete" and not can_apply(value)
