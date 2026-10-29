"""Do not mistake unchanged component partitions for unchanged contacts."""

import pytest

from kicad_cli.fileformat.circuit import Design
from kicad_cli.sch_geometry import run_geometry
from kicad_cli.sch_geometry_contacts import ContactLimit, capture
from kicad_cli.sch_geometry_graph import compare, witness
from kicad_cli.sch_objects import Catalog


def drawing(wires):
    body = "\n".join(
        f"(wire (pts (xy {a[0]} {a[1]}) (xy {b[0]} {b[1]})) (uuid w{i}))"
        for i, (a, b) in enumerate(wires)
    )
    return f"(kicad_sch (version 20250114) (uuid root) (lib_symbols) {body})"


def saved(tmp_path, wires):
    root = tmp_path / "contacts.kicad_sch"
    root.write_text(drawing(wires), encoding="utf-8")
    return root


def request(root, indexes, **transform):
    catalog = Catalog(Design(root))
    objects = [obj.record["selector"] for obj in catalog.objects if obj.record["uuid"] in indexes]
    return {"op": "transform", "objects": objects, "affected_instances": ["/root"], **transform}


U = [((0, 0), (10, 0)), ((0, 0), (0, 10)), ((0, 10), (10, 10))]
CYCLE = U + [((10, 0), (10, 10))]


def test_contact_can_change_while_all_three_component_partitions_remain_equal(tmp_path):
    root = saved(tmp_path, U)
    before = root.read_bytes()
    report, sources = run_geometry(root, [request(root, ["w1"], translate_mm=[10, 0])])
    old = witness(Design(root))
    new = witness(Design(root, sources))
    assert old["relations"] == new["relations"]  # Retained failing pre-fix premise.
    assert old["explicit_names"] == new["explicit_names"]
    assert not report["ok"]
    assert "drawing_contact_attachment_changed" in str(report["verified"]["problems"])
    assert root.read_bytes() == before


def test_redundant_cycle_cannot_hide_a_new_wire_overlap(tmp_path):
    root = saved(tmp_path, CYCLE)
    report, sources = run_geometry(root, [request(root, ["w0"], translate_mm=[0, 10])])
    old, new = witness(Design(root)), witness(Design(root, sources))
    assert old["relations"] == new["relations"]
    assert not report["ok"]
    assert any(
        row["kind"] == "drawing_contact_added"
        for row in report["geometry"]["connections"]["differences"]
    )


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_whole_connected_cycle_preserves_source_local_contacts(tmp_path, angle):
    root = saved(tmp_path, CYCLE)
    report, sources = run_geometry(
        root,
        [
            request(
                root,
                ["w0", "w1", "w2", "w3"],
                translate_mm=[2.5401, -1.27],
                rotate_deg=angle,
                pivot_mm=[6.35, 8.89],
            )
        ],
    )
    assert report["ok"], report
    assert compare(witness(Design(root)), witness(Design(root, sources)))["ok"]


def test_new_unjunctioned_crossing_is_conservatively_visible(tmp_path):
    root = saved(tmp_path, [((0, 0), (10, 0)), ((15, -5), (15, 5))])
    report, _ = run_geometry(root, [request(root, ["w1"], translate_mm=[-10, 0])])
    assert not report["ok"]
    assert "drawing_contact_added" in str(report["geometry"]["connections"])


def test_exact_contact_work_limit_is_explicit(monkeypatch):
    import kicad_cli.sch_geometry_contacts as module

    monkeypatch.setattr(module, "MAX_CONTACT_COMPARISONS", 0)
    rows = {str(i): {"item": None, "position": (0, i), "instance_path": "/root"} for i in range(2)}
    with pytest.raises(ContactLimit, match="contact comparison limit"):
        capture(rows, [])
