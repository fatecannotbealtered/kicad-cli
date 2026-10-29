"""Public deletion previews on owned synthetic geometry, not native DRC claims."""

import json
import math
import uuid

import pytest
from test_sync_removal import scene as scene
from test_sync_removal import selection, sync

from kicad_cli.fileformat.board import Board
from kicad_cli.fileformat.drc.shapes import Shape, distance, pad_copper
from kicad_cli.fileformat.sexpr import Document, copy

START = (1.044956, 0.566702)
END = (1.043651, 0.576616)


def node(text):
    return copy(Document.parse(text).root)


def pad(shape, at="0 0", size="2 2", extra=""):
    return node(
        f'(pad "1" smd {shape} (at {at}) (size {size}) '
        f'(layers "F.Cu" "F.Paste" "F.Mask") {extra} (uuid "{uuid.uuid4()}"))'
    )


def replace_pads(fp, replacement):
    for old in fp.find_all("pad"):
        fp.remove(old)
    fp.append(replacement)


def prepare_case(scene, side):
    _, board, request = scene
    doc = Document.load(board)
    wanted = json.loads(request.read_bytes())["pcb_uuid"]
    fp = next(p for p in doc.root.find_all("footprint") if p.find("uuid").value(1) == wanted)
    rounded = pad(
        "roundrect", extra="(roundrect_rratio 0.25) (chamfer_ratio 0.2) (chamfer top_left)"
    )
    if side == "target":
        replace_pads(fp, rounded)
        doc.root.append(
            node(
                f"(segment (start {200 + START[0]:.6f} {200 + START[1]:.6f}) "
                f"(end {200 + END[0]:.6f} {200 + END[1]:.6f}) (width 0.1) "
                f'(layer "F.Cu") (net 0) (uuid "{uuid.uuid4()}"))'
            )
        )
    else:
        # The selected circular pad occupies the track's first end cap;
        # the approximated roundrect belongs to a different, retained object.
        replace_pads(
            fp,
            pad("circle", at=f"{START[0]:.6f} {START[1]:.6f}", size="0.1 0.1"),
        )
        doc.root.append(
            node(
                f'(footprint "synthetic:Adjacent" (layer "F.Cu") '
                f'(uuid "{uuid.uuid4()}") (at 200 200) (attr smd board_only) '
                '(property "Reference" "MX1" (at 0 0) (layer "F.Fab")))'
            )
        )
        doc.root.find_all("footprint")[-1].append(rounded)
    doc.save(board)
    request.write_text(json.dumps(selection(board)), encoding="utf-8")
    parsed = Board.load(board)
    curve = next(
        p for f in parsed.footprints for p in f.pads if p.chamfer_ratio and p.chamfer_corners
    )
    points = (START, END) if side == "target" else (START,)
    capsule = Shape(tuple(((200 + x) * 1e6, (200 + y) * 1e6) for x, y in points), 50000)
    approximate_gap = min(distance(p, capsule) for p in pad_copper(curve))
    dx, dy = END[0] - START[0], END[1] - START[1]
    t = max(0, min(1, ((0.5 - START[0]) * dx + (0.5 - START[1]) * dy) / (dx * dx + dy * dy)))
    if side == "neighbor":
        t = 0
    true_gap = (math.hypot(START[0] + t * dx - 0.5, START[1] + t * dy - 0.5) - 0.55) * 1e6
    proof = {
        "kind": "source_geometry_counterexample",
        "side": side,
        "units": "nm",
        "analytic_unchamfered_corner_gap": true_gap,
        "inscribed_polygon_gap": approximate_gap,
        "approximation_max_error_nm": 5000,
        "native_reproduction": "not_run_in_this_test",
    }
    (board.parent / "geometry-proof.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
    assert -1500 < true_gap < -500
    assert approximate_gap > 1
    return board


@pytest.mark.parametrize("side", ["target", "neighbor"])
def test_chamfered_roundrect_cannot_be_accepted_using_inscribed_geometry(scene, side):
    board = prepare_case(scene, side)
    before = {p: p.read_bytes() for p in board.parent.glob("*.kicad_*")}
    process, result = sync(scene, "--dry-run")
    (board.parent / "public-preview.json").write_bytes(process.stdout)
    assert process.returncode == 2, (
        "A deletion preview must reject this unsupported curved approximation"
    )
    assert not result["ok"] and result["error"]["code"] == "E_VALIDATION"
    assert result["error"]["details"]["dispatch_state"] == "not_started"
    details = result["error"]["details"]
    assert "chamfer" in details["reason"].lower()
    assert details["inscribed_curve_error_bound_nm"] == 5000
    assert details["contact_numerical_guard_nm"] == 1
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize("shape", ["circle", "rect", "oval", "roundrect"])
def test_analytic_isolated_pad_shapes_keep_public_removal_available(scene, shape):
    _, board, request = scene
    doc = Document.load(board)
    identity = json.loads(request.read_bytes())["pcb_uuid"]
    fp = next(p for p in doc.root.find_all("footprint") if p.find("uuid").value(1) == identity)
    replace_pads(fp, pad(shape, extra="(roundrect_rratio 0.25)" if shape == "roundrect" else ""))
    doc.save(board)
    request.write_text(json.dumps(selection(board)), encoding="utf-8")
    before = board.read_bytes()
    process, result = sync(scene, "--dry-run")
    assert process.returncode == 0 and result["ok"]
    assert result["data"]["preview"]["extensions"]["removal"]["copper_impact"]["contacts"] == []
    assert board.read_bytes() == before
