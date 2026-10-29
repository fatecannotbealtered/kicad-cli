"""Finite creation feedback, using only owned file-format models and no native process."""

import hashlib
from pathlib import Path

import pytest

from kicad_cli.engineering_feedback import can_apply, feedback
from kicad_cli.fileformat.sexpr import Document, copy, quote
from kicad_cli.native.sch_create_feedback import inspect_created


def sheet(tmp_path):
    path = tmp_path / "owned.kicad_sch"
    path.write_text(
        """(kicad_sch (version 20260101) (uuid root) (paper "A4")
        (lib_symbols (symbol "Test:Triple"
          (symbol "Triple_1_1" (rectangle (start -2 -2) (end 2 2)))
          (symbol "Triple_2_1" (rectangle (start -2 -2) (end 2 2)))
          (symbol "Triple_3_1" (rectangle (start -2 -2) (end 2 2)))))
        (symbol (lib_id "Test:Triple") (at 30 35 0) (unit 1) (uuid unit-a)
          (property "Reference" "U1" (at 30 27 0) (effects (font (size 1.27 1.27))))
          (property "Value" "BUFFER" (at 30 30 0) (effects (font (size 1.27 1.27)))))
        (symbol (lib_id "Test:Triple") (at 70 35 0) (unit 2) (uuid unit-b)
          (property "Reference" "U1" (at 70 27 0) (effects (font (size 1.27 1.27)))))
        (symbol (lib_id "Test:Triple") (at 110 35 0) (unit 3) (uuid unit-power)
          (property "Reference" "U1" (at 110 27 0) (effects (font (size 1.27 1.27)))))
        (wire (pts (xy 45 60) (xy 50 60)) (uuid own-stub))
        (label "SIG_A" (at 50 60 0) (uuid label-a)
          (effects (font (size 1.27 1.27)) (justify left bottom))))""",
        encoding="utf-8",
    )
    return path


def change(path, mutate):
    doc = Document.load(path)
    mutate(doc.root)
    doc.save(path)


def test_report_is_bound_to_saved_bytes_and_lists_each_physical_unit(tmp_path):
    path = sheet(tmp_path)
    original = path.read_bytes()
    report = inspect_created(path)
    assert report["source"] == {"file": path.name, "sha256": hashlib.sha256(original).hexdigest()}
    assert report["geometry_check"] == "partial_conservative_schematic_bounds"
    assert {(r["ref"], r["unit"]) for r in report["symbols"]} == {("U1", 1), ("U1", 2), ("U1", 3)}
    assert report["counts"]["placed_symbol_units"] == 3
    assert report["findings"] == []
    assert len(report["unmeasured_objects"]) == 3
    assert all(row["field"] == "Reference" for row in report["unmeasured_objects"])
    assert path.read_bytes() == original


def test_native_multiunit_reference_suffix_is_explicitly_unmeasured(tmp_path):
    report = inspect_created(sheet(tmp_path))
    refs = [row for row in report["fields"] if row["field"] == "Reference"]
    assert len(refs) == 3 and all(row["status"] == "unmeasured" for row in refs)
    assert all("suffix" in row["reason"] for row in refs)
    assert all("box_mm" not in row for row in refs)
    value = next(row for row in report["fields"] if row["field"] == "Value")
    assert value["geometry_check"] == "partial_conservative_schematic_bounds"


def test_geometry_uses_one_captured_document_when_path_changes(tmp_path, monkeypatch):
    path = sheet(tmp_path)
    original = path.read_bytes()
    change(path, lambda root: root.find_all("symbol")[1].find("at").set(1, "30"))
    replacement = path.read_bytes()
    path.write_bytes(original)
    read_bytes = Path.read_bytes
    calls = []

    def replace_after_capture(selected):
        raw = read_bytes(selected)
        if selected == path:
            calls.append(selected)
            path.write_bytes(replacement)
        return raw

    monkeypatch.setattr(Path, "read_bytes", replace_after_capture)
    report = inspect_created(path)
    assert len(calls) == 1
    assert report["source"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert report["findings"] == []
    assert read_bytes(path) == replacement


@pytest.mark.parametrize(
    "case", ["same-bytes", "changed-bytes", "missing-report", "missing-source"]
)
def test_preparation_binds_geometry_to_intended_candidate_bytes(tmp_path, monkeypatch, case):
    from kicad_cli import engineering_prepare
    from kicad_cli.native import sch_create
    from kicad_cli.payload import write_txn

    primary = tmp_path / "candidate" / "owned.kicad_sch"
    original = tmp_path / "formal" / "owned.kicad_sch"
    spec = tmp_path / "spec.json"
    spec.write_text("{}", encoding="utf-8")
    observed = b"owned source observation A"
    saved = b"owned replacement B" if case == "changed-bytes" else observed

    def controlled_create(*args):
        primary.parent.mkdir()
        primary.write_bytes(saved)
        primary.with_suffix(".net").write_bytes(b"owned test netlist")
        if case == "missing-report":
            return {}
        report = {
            "schema": "sch-create-geometry/v1",
            "geometry_check": "partial_conservative_schematic_bounds",
            "findings": [],
            "unmeasured_objects": [],
        }
        if case != "missing-source":
            report["source"] = {
                "file": primary.name,
                "sha256": hashlib.sha256(observed).hexdigest(),
            }
        return {"creation_geometry": report}

    monkeypatch.setattr(sch_create, "create", controlled_create)
    monkeypatch.setattr(write_txn, "commit", lambda: None)
    result = engineering_prepare.prepare(
        {
            "command": "sch create",
            "params": {"spec": str(spec)},
            "mapping": {str(spec.resolve()): str(spec.resolve())},
            "primary": str(primary),
            "original_primary": str(original),
        }
    )
    assert not original.exists()
    if case == "changed-bytes":
        assert result["ok"] is False and result["error"]["code"] == "E_CONFLICT"
        assert primary.read_bytes() == saved
    else:
        assert result["ok"] is True
        assert result["files"][0]["intended"] == hashlib.sha256(saved).hexdigest()
        value = feedback("sch create", result["result"])
        assert can_apply(value) is (case == "same-bytes")
        if case != "same-bytes":
            assert value["geometry_check"] == "incomplete"


def test_overlapping_symbol_units_are_reported(tmp_path):
    path = sheet(tmp_path)
    change(path, lambda root: root.find_all("symbol")[1].find("at").set(1, "30"))
    report = inspect_created(path)
    row = next(r for r in report["findings"] if r["status"] == "review_symbol_overlap")
    assert row["symbol_uuid"] == "unit-a" and row["conflicts"][0]["symbol_uuid"] == "unit-b"


@pytest.mark.parametrize("at", [(10, 35), (200, 170)], ids=["frame", "title-block"])
def test_outside_page_and_title_block_are_reported(tmp_path, at):
    path = sheet(tmp_path)

    def move(root):
        place = root.find_all("symbol")[0].find("at")
        place.set(1, str(at[0]))
        place.set(2, str(at[1]))

    change(path, move)
    report = inspect_created(path)
    row = next(r for r in report["findings"] if r.get("symbol_uuid") == "unit-a")
    assert row["status"] == "review_page_bounds"
    assert row["inside_page_bounds"] is False or row["overlaps_title_block"] is True


def test_label_text_crossing_a_foreign_wire_is_reported(tmp_path):
    path = sheet(tmp_path)
    change(
        path,
        lambda root: root.append(copy(Document.parse("(wire (pts (xy 53 58) (xy 53 62)))").root)),
    )
    report = inspect_created(path)
    row = next(r for r in report["findings"] if r["status"] == "review_label_conflicts")
    assert row["text"] == "SIG_A"
    assert row["conflicts"] == [{"kind": "wire", "from_mm": [53, 58], "to_mm": [53, 62]}]


def test_label_on_its_own_stub_is_not_a_conflict(tmp_path):
    report = inspect_created(sheet(tmp_path))
    assert report["labels"][0]["status"] == "no_overlap_in_measured_bounds"
    assert report["labels"][0]["conflicts"] == []


@pytest.mark.parametrize(
    "style",
    [
        "font",
        "field-rotation",
        "label-font",
        "label-angle",
        "label-multiline",
        "graphic",
        "symbol-angle",
    ],
)
def test_unsupported_style_is_explicit(tmp_path, style):
    path = sheet(tmp_path)

    def mutate(root):
        field = root.find_all("symbol")[0].find_all("property")[1]
        label = root.find("label")
        if style == "font":
            field.find("effects").find("font").find("size").set(1, "2.54")
        elif style == "field-rotation":
            field.find("at").set(3, "90")
        elif style == "label-font":
            label.find("effects").find("font").find("size").set(1, "2.54")
        elif style == "label-angle":
            label.find("at").set(3, "45")
        elif style == "label-multiline":
            label.set(1, quote("SIG\nA"))
        elif style == "graphic":
            body = root.find("lib_symbols").find("symbol").find("symbol")
            body.append(copy(Document.parse("(arc (start 0 0) (mid 2 3) (end 4 0))").root))
        else:
            root.find_all("symbol")[0].find("at").set(3, "45")

    change(path, mutate)
    original = path.read_bytes()
    report = inspect_created(path)
    assert report["unmeasured_objects"]
    assert all(row["reason"] for row in report["unmeasured_objects"])
    assert report["geometry_check"] == (
        "incomplete" if style == "symbol-angle" else "partial_conservative_schematic_bounds"
    )
    assert "native" in report["limit"] and path.read_bytes() == original


def test_feedback_exposes_real_findings_and_keeps_visual_limits(tmp_path):
    path = sheet(tmp_path)
    change(path, lambda root: root.find_all("symbol")[1].find("at").set(1, "30"))
    report = inspect_created(path)
    value = feedback("sch create", {"creation_geometry": report})
    assert value["creation_geometry"] is report and value["geometry_findings"]
    assert value["definite_violations"] == [] and can_apply(value)
    assert value["requires_visual_review"] and value["unsupported"]


def test_missing_report_is_incomplete():
    value = feedback("sch create", {})
    assert value["geometry_check"] == "incomplete" and not can_apply(value)


def test_unknown_paper_is_not_measured_as_default_a4(tmp_path):
    path = sheet(tmp_path)
    change(path, lambda root: root.find("paper").set(1, quote("UNSUPPORTED")))
    value = feedback("sch create", {"creation_geometry": inspect_created(path)})
    assert value["geometry_check"] == "incomplete" and not can_apply(value)
    assert any(row["kind"] == "page" for row in value["unmeasured_objects"])


def test_label_overlapping_another_label_is_reported_once(tmp_path):
    path = sheet(tmp_path)

    def duplicate(root):
        label = copy(root.find("label"))
        label.set(1, quote("SIG_B"))
        label.find("uuid").set(1, "label-b")
        root.append(label)

    change(path, duplicate)
    report = inspect_created(path)
    assert all(len(row["conflicts"]) == 1 for row in report["labels"])


@pytest.mark.parametrize("intrusion", [True, False], ids=["through-body", "own-stub-boundary"])
def test_wire_body_overlap_does_not_confuse_own_stub_contact(tmp_path, intrusion):
    path = sheet(tmp_path)
    wire = (
        "(wire (pts (xy 25 35) (xy 35 35)))" if intrusion else "(wire (pts (xy 25 35) (xy 28 35)))"
    )
    change(path, lambda root: root.append(copy(Document.parse(wire).root)))
    report = inspect_created(path)
    rows = [row for row in report["findings"] if row["status"] == "review_wire_body_conflicts"]
    assert bool(rows) is intrusion
    if intrusion:
        assert rows[0]["conflicts"][0]["symbol_uuid"] == "unit-a"
