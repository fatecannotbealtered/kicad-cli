"""Pure-source boundaries for unsupported bus semantics during scalar edits."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from kicad_cli.fileformat.circuit import Design  # noqa: E402
from kicad_cli.sch_geometry import run_geometry  # noqa: E402
from kicad_cli.sch_geometry_graph import GeometryGraphError, compare, witness  # noqa: E402
from kicad_cli.sch_objects import Catalog  # noqa: E402


def drawing(target: str = "", label_kind: str = "label", text: str = "D[0..1]") -> str:
    return f'''\ufeff(kicad_sch (version 20250114) (generator "bus boundary fixture")
 (uuid root) (paper "A4")
 (lib_symbols (symbol "fixture:ONE"
  (symbol "ONE_1_1"
   (pin passive line (at 0 0 0) (length 1) (name "A") (number "1")))))
 (bus (pts (xy 0 0) (xy 10 0))
  (stroke (width 0) (type default)) (uuid bus))
 (bus_entry (at 10 0) (size 2 2)
  (stroke (width 0) (type default)) (uuid entry))
 ({label_kind} "{text}" (at 5 0 0)
  (effects (font (size 1.27 1.27))) (uuid bus-label))
 (label "D0" (at 30 10 0) (effects (font (size 1.27 1.27))) (uuid scalar))
 {target}
)\r\n'''.replace("\n", "\r\n")


def saved(tmp_path: Path, target: str = "", **kwargs) -> Path:
    root = tmp_path / "bus.kicad_sch"
    root.write_bytes(drawing(target, **kwargs).encode("utf-8"))
    return root


def change(root: Path, uuid: str, delta: list[int]) -> dict:
    obj = next(obj for obj in Catalog(Design(root)).objects if obj.record["uuid"] == uuid)
    return {
        "op": "transform",
        "objects": [obj.record["selector"]],
        "translate_mm": delta,
        "affected_instances": ["/root"],
    }


@pytest.mark.parametrize("label_kind", ["label", "global_label", "hierarchical_label"])
@pytest.mark.parametrize("text", ["D[0..1]", "USB{DP DM}"])
def test_bus_expression_label_is_discoverable_but_cannot_be_selected(
    tmp_path: Path, capsys, label_kind: str, text: str
) -> None:
    root = saved(tmp_path, label_kind=label_kind, text=text)
    before = root.read_bytes()
    catalog = Catalog(Design(root))
    bus_label = next(obj.record for obj in catalog.objects if obj.record["uuid"] == "bus-label")
    scalar = next(obj.record for obj in catalog.objects if obj.record["uuid"] == "scalar")
    assert not bus_label["editable"]
    assert bus_label["instances"][0]["connection_status"] == "unavailable"
    assert "bus-expression" in bus_label["uneditable_reasons"][0]
    assert scalar["editable"]
    assert scalar["instances"][0]["connection_status"] == "complete"
    assert catalog.query(kind=label_kind)["items"]
    assert catalog.query(uuid="scalar", net=scalar["nets"][0])["count"] == 1
    with pytest.raises(SystemExit):
        run_geometry(root, [change(root, "bus-label", [0, 5])])
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "E_VALIDATION"
    assert "bus-expression" in str(result)
    assert root.read_bytes() == before


def test_bus_label_does_not_hide_junction_or_unrelated_edit(tmp_path: Path) -> None:
    root = saved(tmp_path, "(junction (at 5 0) (diameter 0) (color 0 0 0 0) (uuid dot))")
    before = root.read_bytes()
    catalog = Catalog(Design(root))
    assert catalog.query(kind="junction")["count"] == 1
    report, sources = run_geometry(root, [change(root, "scalar", [0, 5])])
    assert report["ok"], report
    assert root.read_bytes() == before
    assert sources[root.resolve()] == before.decode("utf-8").replace(
        '(label "D0" (at 30 10 0)', '(label "D0" (at 30 15 0)'
    )


TARGETS = {
    "wire": "(wire (pts (xy 20 10) (xy 22 10)) (stroke (width 0) (type default)) (uuid target))",
    "label": '(label "X" (at 20 10 0) (effects (font (size 1.27 1.27))) (uuid target))',
    "symbol": '(symbol (lib_id "fixture:ONE") (at 20 10 0) (unit 1) (uuid target) '
    '(property "Reference" "U1" (at 20 9 0)) (property "Value" "preserved" (at 20 11 0)))',
    "no_connect": "(no_connect (at 20 10) (uuid target))",
    "junction": "(junction (at 20 10) (diameter 0) (color 0 0 0 0) (uuid target))",
}


@pytest.mark.parametrize("kind", TARGETS)
@pytest.mark.parametrize("destination", [[3, 0], [11, 1]])
def test_new_contact_with_fixed_bus_or_entry_is_refused(
    tmp_path: Path, kind: str, destination: list[int]
) -> None:
    root = saved(tmp_path, TARGETS[kind])
    before = root.read_bytes()
    delta = [destination[0] - 20, destination[1] - 10]
    report, _ = run_geometry(root, [change(root, "target", delta)])
    assert not report["ok"]
    assert any(
        row["kind"] == "unsupported_bus_contact_changed"
        for row in report["geometry"]["connections"]["differences"]
    )
    assert root.read_bytes() == before


@pytest.mark.parametrize("kind", TARGETS)
def test_unrelated_scalar_object_can_move_on_same_bus_page(tmp_path: Path, kind: str) -> None:
    root = saved(tmp_path, TARGETS[kind])
    before = root.read_bytes()
    report, sources = run_geometry(root, [change(root, "target", [0, 4])])
    assert report["ok"], report
    candidate = sources[root.resolve()]
    original = before.decode("utf-8")
    for marker in ("bus", "bus_entry", "label"):
        # Every fixed unsupported source list and its attributes remain exact.
        source_node = next(
            node for node in Design(root).instances[0].schematic.root.lists() if node.head == marker
        )
        assert original[source_node.start : source_node.end] in candidate
    assert root.read_bytes() == before


def test_guard_catches_lost_and_sliding_boundary_contacts(tmp_path: Path) -> None:
    root = saved(tmp_path, TARGETS["wire"].replace("20 10", "2 0").replace("22 10", "4 0"))
    for delta in ([0, 1], [1, 0]):
        report, _ = run_geometry(root, [change(root, "target", list(delta))])
        assert not report["ok"], report
        assert "unsupported_bus_contact_changed" in str(report["geometry"]["connections"])


def test_wire_interior_crossing_bus_is_a_conservative_boundary(tmp_path: Path) -> None:
    target = TARGETS["wire"].replace("20 10", "20 9").replace("22 10", "20 11")
    root = saved(tmp_path, target)
    report, _ = run_geometry(root, [change(root, "target", [-17, -10])])
    assert not report["ok"]
    assert "unsupported_bus_contact_changed" in str(report["geometry"]["connections"])


def test_old_bus_label_attachment_reproducer_is_now_visible_to_witness(tmp_path: Path) -> None:
    root = saved(tmp_path)
    source = root.read_bytes().decode("utf-8")
    before = Design(root)
    after = Design(root, {root: source.replace("(at 5 0 0)", "(at 5 5 0)")})
    compared = compare(witness(before), witness(after))
    assert not compared["ok"]
    assert "unsupported_bus_contact_changed" in str(compared)


def test_bus_contact_work_limit_is_explicit(tmp_path: Path, monkeypatch) -> None:
    import kicad_cli.sch_geometry_graph as module

    root = saved(tmp_path, TARGETS["wire"])
    monkeypatch.setattr(module, "MAX_BUS_CONTACT_TESTS", 1)
    with pytest.raises(GeometryGraphError, match="bus contact comparison limit"):
        witness(Design(root))


def test_bus_boundary_limit_is_explicit(tmp_path: Path, monkeypatch) -> None:
    import kicad_cli.sch_geometry_graph as module

    root = saved(tmp_path)
    monkeypatch.setattr(module, "MAX_BUS_BOUNDARIES", 1)
    with pytest.raises(GeometryGraphError, match="bus boundary limit"):
        witness(Design(root))


@pytest.mark.parametrize("kind", TARGETS)
def test_isolated_bus_sheet_pin_is_a_fixed_boundary(tmp_path: Path, kind: str) -> None:
    sheet = """(sheet (at 45 45) (size 10 10) (uuid sheet)
      (property "Sheetname" "Child") (property "Sheetfile" "child.kicad_sch")
      (pin "D[0..1]" input (at 50 50 0) (uuid port)))"""
    root = saved(tmp_path, TARGETS[kind] + sheet)
    child = tmp_path / "child.kicad_sch"
    child.write_text("(kicad_sch (version 20250114) (uuid child) (lib_symbols))")
    before = {p: p.read_bytes() for p in (root, child)}
    report, _ = run_geometry(root, [change(root, "target", [30, 40])])
    assert not report["ok"]
    assert "unsupported_bus_contact_changed" in str(report["geometry"]["connections"])
    assert {p: p.read_bytes() for p in before} == before
