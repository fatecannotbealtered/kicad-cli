"""Exact duplicates are distinct from overlap, branch contact and test contacts."""

from kicad_cli.pcb_patch import inspect


def test_distinct_copper_classes_and_test_contact_are_exposed(tmp_path):
    path = tmp_path / "classes.kicad_pcb"
    text = '(kicad_pcb (version 20260206) (layers (0 "F.Cu" signal))'
    rows = [
        ("a", (0, 0), (4, 0), 0.2),
        ("duplicate", (4, 0), (0, 0), 0.2),
        ("partial", (2, 0), (6, 0), 0.2),
        ("different_width", (0, 0), (4, 0), 0.3),
        ("branch", (4, 0), (4, 3), 0.2),
    ]
    for name, a, b, width in rows:
        text += (
            f"(segment (start {a[0]} {a[1]}) (end {b[0]} {b[1]}) (width {width}) "
            f'(layer "F.Cu") (net "SIG") (uuid "{name}"))'
        )
    text += (
        '(footprint "Test:Pad" (layer "F.Cu") (at 4 3) (uuid "tp")'
        '(property "Reference" "TP1" (at 0 0) (layer "F.SilkS"))'
        '(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net "SIG") (uuid "pad"))))'
    )
    path.write_text(text)
    data = inspect(path, "SIG")
    assert data["duplicate_group_count"] == 1 and data["extra_objects"] == 1
    assert set(data["duplicate_groups"][0]["ids"]) == {"a", "duplicate"}
    classes = {r["classification"] for r in data["partial_overlaps"]}
    assert classes == {"partial_collinear_overlap", "coincident_centerline_different_width"}
    assert all("branch" not in row["ids"] for row in data["partial_overlaps"])
    assert any(pad["ref"] == "TP1" for pad in data["nets"][0]["pads"])
    assert not data["partial_overlap_coverage"]["truncated"]
