"""Geometry assertions on generated objects, separate native public repro included in bench."""

import json
from pathlib import Path

import pytest
from test_module_operations import native_result, route_case

from kicad_cli import library_build, pcb_patch
from kicad_cli.fileformat.sexpr import Document
from kicad_cli.fileformat.sexpr import copy as clone

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "body,pads,margin",
    [
        ([10, 8], [[-0.5, 0, 0.6, 0.6], [0.5, 0, 0.6, 0.6]], 0.25),
        ([2, 2], [[-4, -2, 1, 1], [4, 2, 1, 1]], 0.25),
        ([2, 2], [[3, 2, 2, 1], [0, -1, 0.6, 0.6]], 0.3),
        ([2, 2], [[-0.5, 0, 1, 1], [0.5, 0, 1, 1]], 0.000001),
    ],
)
def test_courtyard_encloses_actual_body_and_pad_geometry(body, pads, margin):
    spec = json.loads((REPO / "examples/library/sn74lvc1g14/library.json").read_bytes())
    spec["footprint"].update(
        body_mm=body,
        courtyard_mm=margin,
        pads=[
            {"number": str(i), "at_mm": [x, y], "size_mm": [w, h]}
            for i, (x, y, w, h) in enumerate(pads, 1)
        ],
    )
    shape = library_build.footprint(spec)
    court = next(n for n in shape.find_all("fp_rect") if n.find("layer").value(1) == "F.CrtYd")
    fab = next(n for n in shape.find_all("fp_rect") if n.find("layer").value(1) == "F.Fab")
    a = list(map(float, court.find("start").values()))
    b = list(map(float, court.find("end").values()))
    pen = float(court.find("stroke").find("width").value(1)) / 2
    envelopes = [[*map(float, fab.find("start").values()), *map(float, fab.find("end").values())]]
    for pad in shape.find_all("pad"):
        x, y = map(float, pad.find("at").values())
        w, h = map(float, pad.find("size").values())
        envelopes.append([x - w / 2, y - h / 2, x + w / 2, y + h / 2])
    for x, y, X, Y in envelopes:
        assert x - a[0] - pen >= margin - 1e-9
        assert y - a[1] - pen >= margin - 1e-9
        assert b[0] - pen - X >= margin - 1e-9
        assert b[1] - pen - Y >= margin - 1e-9


@pytest.mark.parametrize("field", ["position", "size", "body", "margin"])
def test_sub_nm_footprint_inputs_are_explicitly_unsupported(field):
    spec = json.loads((REPO / "examples/library/sn74lvc1g14/library.json").read_bytes())
    f = spec["footprint"]
    if field == "position":
        f["pads"][0]["at_mm"][0] = 1.3000005
    elif field == "size":
        f["pads"][0]["size_mm"][0] = 1.1000005
    elif field == "body":
        f["body_mm"][0] = 1.6000005
    else:
        f["courtyard_mm"] = 0.0000005
    with pytest.raises(ValueError, match="integer-nm precision"):
        library_build.validate_spec(spec)


@pytest.mark.parametrize("mid", [(2, -4), (2, 4), (-4, 2), (4, 2)])
def test_all_arc_removal_is_refused_before_native_or_write(tmp_path, monkeypatch, mid):
    path, spec = route_case(tmp_path)
    doc = Document.load(path)
    doc.root.append(
        clone(
            Document.parse(
                f"(arc (start 0 0) (mid {mid[0]} {mid[1]}) (end 4 0) "
                '(width 0.2) (layer "F.Cu") (net "SIG") (uuid "arc"))'
            ).root
        )
    )
    path.write_text(doc.dumps())
    original = path.read_bytes()
    spec["remove_ids"] = ["arc"]
    spec["segments"] = []
    spec["minimum_improvement_mm"] = 0
    monkeypatch.setattr(
        pcb_patch, "native_check", lambda _: pytest.fail("arc refusal must precede native checks")
    )
    with pytest.raises(pcb_patch.RouteError) as caught:
        pcb_patch.prepare(path, spec)
    assert caught.value.details["route_failure"]["category"] == "unsupported_geometry"
    assert path.read_bytes() == original


def test_straight_copper_boundary_contact_and_one_nm_violation(tmp_path, monkeypatch):
    path, spec = route_case(tmp_path)
    original = path.read_bytes()
    spec["bounds_nm"] = [-100000, -100000, 4100000, 3100000]
    monkeypatch.setattr(pcb_patch, "native_check", lambda _: native_result())
    result, _ = pcb_patch.prepare(path, spec)
    assert result["path_after_mm"] == 4
    path.write_bytes(original)
    spec["bounds_nm"][0] += 1
    with pytest.raises(pcb_patch.RouteError, match="copper envelope"):
        pcb_patch.prepare(path, spec)
    assert path.read_bytes() == original
