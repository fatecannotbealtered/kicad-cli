"""Finite placement guard boundaries; all files and consumers here are host-only."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest
from driver_test_utils import grant
from jsonschema import Draft202012Validator
from test_move_preflight_binding import bounded_case as bounded_case

from kicad_cli import driver_runtime, move_guard, move_preflight, move_readback
from kicad_cli._driver_sdk.models import DriverError
from kicad_cli.fileformat.board import Board
from kicad_cli.fileformat.sexpr import Document
from kicad_cli.native.move import from_mm, move_footprint


def _id(name):
    return str(uuid5(NAMESPACE_URL, "finite-move-guard:" + name))


def _footprint(ref, x, y):
    return f'''(footprint "fixture:smd"
      (layer "F.Cu") (uuid "{_id(ref)}") (at {x} {y})
      (property "Reference" "{ref}" (at 0 -2 0) (layer "F.SilkS")
        (uuid "{_id(ref + ":reference")}"))
      (property "Value" "protected text" (at 0 2 0) (layer "F.Fab")
        (uuid "{_id(ref + ":value")}"))
      (fp_rect (start -1 -1) (end 1 1)
        (stroke (width 0.05) (type solid)) (fill no) (layer "F.CrtYd")
        (uuid "{_id(ref + ":court")}"))
      (pad "1" smd roundrect (at 0 0) (size 1 1)
        (layers "F.Cu" "F.Mask" "F.Paste") (roundrect_rratio 0.25)
        (net "GND") (uuid "{_id(ref + ":pad")}")))'''


@pytest.fixture
def board_pair(tmp_path):
    edges = []
    corners = ((10, 10), (50, 10), (50, 40), (10, 40))
    for i, a in enumerate(corners):
        b = corners[(i + 1) % 4]
        edges.append(f'''(gr_line (start {a[0]} {a[1]}) (end {b[0]} {b[1]})
          (stroke (width 0.1) (type default)) (layer "Edge.Cuts")
          (uuid "{_id("edge" + str(i))}"))''')
    raw = '(kicad_pcb (version 20260206) (layers (0 "F.Cu" signal))\n'
    raw += "\n".join(_footprint(ref, x, 20) for ref, x in (("R1", 20), ("R2", 30), ("R3", 40)))
    raw += "\n" + "\n".join(edges) + ")\n"
    before, after = tmp_path / "before.kicad_pcb", tmp_path / "after.kicad_pcb"
    before.write_text(raw, encoding="utf-8")
    after.write_bytes(before.read_bytes())
    return before, after


def _translate(pair, requested):
    before, after = pair
    board = Board(Document.parse(before.read_text(encoding="utf-8")), after)
    selected = {fp.reference: fp for fp in board.footprints}
    for ref, xy in requested.items():
        move_footprint(board, selected[ref], *(from_mm(v) for v in xy))
    after.write_text(board.document.dumps(), encoding="utf-8")
    return move_guard.check(before, after, requested)


def test_clear_batch_keeps_every_field_local_and_measures_conservative_envelopes(board_pair):
    result = _translate(board_pair, {"R1": [21, 21], "R2": [31, 21]})
    assert result["status"] == "passed"
    assert result["conflicts"] == []
    assert result["outline_nm"] == [10_000_000, 10_000_000, 50_000_000, 40_000_000]
    assert result["targets"][0]["after_bounds_nm"] == [
        19_950_000,
        19_950_000,
        22_050_000,
        22_050_000,
    ]
    assert len(result["candidate_footprints"]) == 3
    assert result["source"]["official_native"] is False
    assert result["method"] == "conservative_nontext_envelopes_full_graphic_stroke_guard_band"


@pytest.mark.parametrize(
    "layers,accepted",
    [
        ('"F.Cu" "F.Paste" "F.Mask"', True),
        ('"F.Mask" "F.Cu"', True),
        ('"F.Cu" "F.Mask" "F.Mask"', False),
        ('"F.Cu" "B.Cu" "F.Mask"', False),
    ],
)
def test_smd_layer_set_order_is_not_geometry(board_pair, layers, accepted):
    before, after = board_pair
    before.write_text(
        before.read_text().replace('"F.Cu" "F.Mask" "F.Paste"', layers), encoding="utf-8"
    )
    after.write_bytes(before.read_bytes())
    if accepted:
        assert _translate(board_pair, {"R1": [21, 21]})["status"] == "passed"
    else:
        with pytest.raises(move_guard.GuardError, match="front copper/mask"):
            _translate(board_pair, {"R1": [21, 21]})


@pytest.mark.parametrize("shape", ["rect", "roundrect"])
def test_plain_testpoint_without_paste_has_same_material_envelope(board_pair, shape):
    before, after = board_pair
    original = _footprint("R1", 20, 20)
    testpoint = _footprint("TP1", 20, 20).replace(
        '(layers "F.Cu" "F.Mask" "F.Paste")', '(layers "F.Cu" "F.Mask")'
    )
    if shape == "rect":
        testpoint = testpoint.replace("smd roundrect", "smd rect").replace(
            " (roundrect_rratio 0.25)", ""
        )
    raw = before.read_text(encoding="utf-8")
    assert original in raw
    before.write_text(raw.replace(original, testpoint, 1), encoding="utf-8")
    after.write_bytes(before.read_bytes())
    result = _translate(board_pair, {"TP1": [21, 21]})
    assert result["status"] == "passed"
    target = result["targets"][0]
    assert target["ref"] == "TP1"
    assert target["id"] == _id("TP1")
    assert target["before_bounds_nm"] == [18_950_000, 18_950_000, 21_050_000, 21_050_000]
    assert target["after_bounds_nm"] == [19_950_000, 19_950_000, 22_050_000, 22_050_000]
    assert result["conflicts"] == []


@pytest.mark.parametrize("position", [[9, 20], [11.05, 20], [49, 20], [20, 10], [20, 40]])
def test_outside_or_touching_board_edge_blocks_complete_batch(board_pair, position):
    result = _translate(board_pair, {"R1": position, "R3": [40, 21]})
    assert result["status"] == "blocked"
    assert {"criterion": "strict_rectangular_edge_containment", "refs": ["R1"]} in result[
        "conflicts"
    ]


@pytest.mark.parametrize(
    "requested,refs",
    [
        ({"R1": [30, 20]}, ["R1", "R2"]),
        ({"R1": [25, 20], "R2": [25, 20]}, ["R1", "R2"]),
        ({"R1": [27.9, 20]}, ["R1", "R2"]),
    ],
)
def test_target_target_and_target_nontarget_touch_or_overlap_block(board_pair, requested, refs):
    result = _translate(board_pair, requested)
    assert result["status"] == "blocked"
    assert {"criterion": "conservative_envelopes_not_strictly_disjoint", "refs": refs} in result[
        "conflicts"
    ]


def test_one_nanometre_positive_box_gap_is_supported(board_pair):
    assert _translate(board_pair, {"R1": [27.899999, 20]})["status"] == "passed"


@pytest.mark.parametrize(
    "old,new",
    [
        ('(layer "F.Cu")', '(layer "B.Cu")'),
        ("(at 20 20)", "(at 20 20 90)"),
        ('(pad "1" smd roundrect', '(pad "1" smd oval'),
        ("(at 0 0) (size 1 1)", "(at 0 0 90) (size 1 1)"),
        ("(fp_rect", "(fp_arc"),
        ('(layer "F.CrtYd")', '(layer "F.Fab")'),
        ("(end 50 10)", "(end 49 10)"),
        ('(layer "Edge.Cuts")', '(layer "Dwgs.User")'),
        ("(at 20 20)", "(at 20.0000001 20)"),
        ("(size 1 1)", "(size 0 1)"),
        ("(at 20 20)", "(at 20 20) (zone (net 0))"),
        ("(end 1 1)", "(end -1 -1)"),
        (_id("R1:pad"), _id("R2:pad")),
    ],
)
def test_unsupported_or_incomplete_geometry_is_refused_before_candidate_acceptance(
    board_pair, old, new
):
    before, after = board_pair
    before.write_text(before.read_text(encoding="utf-8").replace(old, new, 1), encoding="utf-8")
    after.write_bytes(before.read_bytes())
    with pytest.raises(ValueError):
        move_guard.check(before, after, {"R1": [20, 20]})


def test_locked_selected_member_refuses_whole_batch(board_pair):
    before, after = board_pair
    before.write_text(
        before.read_text(encoding="utf-8").replace("(at 20 20)", "(at 20 20) (locked yes)", 1),
        encoding="utf-8",
    )
    after.write_bytes(before.read_bytes())
    with pytest.raises(move_guard.GuardError, match="Locked selected"):
        _translate(board_pair, {"R1": [21, 20], "R3": [41, 20]})


@pytest.mark.parametrize(
    "old,new",
    [
        ('"protected text"', '"changed text"'),
        ("(at 0 2 0)", "(at 0 3 0)"),
        ("(size 1 1)", "(size 1.1 1)"),
        (_id("R1:pad"), _id("changed-pad")),
        ("(at 30 20)", "(at 31 20)"),
    ],
)
def test_local_fields_pad_geometry_identity_and_nontarget_pose_remain_protected(
    board_pair, old, new
):
    _translate(board_pair, {"R1": [21, 20]})
    before, after = board_pair
    after.write_text(after.read_text(encoding="utf-8").replace(old, new, 1), encoding="utf-8")
    with pytest.raises(move_guard.GuardError, match="protected saved content"):
        move_guard.check(before, after, {"R1": [21, 20]})


def test_independent_guard_schema_records_exact_scope_and_binds_status(board_pair):
    result = _translate(board_pair, {"R1": [21, 21]})
    declaration = json.loads(
        (Path(__file__).parents[1] / "kicad_cli/driver_contract.json").read_bytes()
    )
    schemas = declaration["reference"]["json_schemas"]
    schema = schemas[move_guard.SCHEMA]
    Draft202012Validator(schema).validate(result)
    preflight = schemas[move_preflight.SCHEMA]
    assert "placement_guard" in preflight["required"]
    assert preflight["properties"]["placement_guard"] == {"$ref": move_guard.SCHEMA}
    changed = copy.deepcopy(result)
    changed["status"] = "blocked"
    assert list(Draft202012Validator(schema).iter_errors(changed))


def test_extra_member_even_outside_body_geometry_is_protected(board_pair):
    _translate(board_pair, {"R1": [21, 20]})
    before, after = board_pair
    after.write_text(
        after.read_text(encoding="utf-8").replace(
            '(property "Reference" "R1"',
            '(property "Extra" "added member" (at 0 0) (layer "F.Fab"))\n'
            '(property "Reference" "R1"',
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(move_guard.GuardError, match="protected saved content"):
        move_guard.check(before, after, {"R1": [21, 20]})


@pytest.mark.parametrize("requested", [{"R1": [90, 90]}, {"R1": [105, 100]}])
def test_blocked_geometry_starts_no_native_consumer_and_preserves_source(bounded_case, requested):
    path = bounded_case["board"]
    raw = path.read_bytes()
    with pytest.raises(DriverError) as found:
        move_preflight.prepare(
            path,
            Board.load(path),
            move_readback.observe(path, sorted(requested)),
            requested,
            state_dir=bounded_case["root"] / "state",
            operation_id="blocked-geometry",
        )
    assert found.value.code == "E_VALIDATION"
    assert found.value.details["preflight_status"] == "blocked"
    assert found.value.details["native_call_started"] is False
    assert bounded_case["seen"] == []
    assert path.read_bytes() == raw


@pytest.mark.parametrize("kind", ["missing", "changed-bounds", "changed-request"])
def test_confirm_reconsumes_original_placement_witness(bounded_case, kind):
    path = bounded_case["board"]
    requested = {"R1": [100, 100.5]}
    state_dir = bounded_case["root"] / "state"
    data = move_preflight.prepare(
        path,
        Board.load(path),
        move_readback.observe(path, ["R1"]),
        requested,
        state_dir=state_dir,
        operation_id="bound-placement-witness",
    )
    plan = {"extensions": {"preflight": data, "moves": requested}}
    assert move_preflight.validate(plan, state_dir=state_dir) is data
    if kind == "missing":
        del data["placement_guard"]
    elif kind == "changed-bounds":
        data["placement_guard"]["targets"][0]["after_bounds_nm"][0] += 1
    else:
        plan["extensions"]["moves"] = {"R1": [100, 101]}
    with pytest.raises(DriverError) as found:
        move_preflight.validate(plan, state_dir=state_dir)
    assert found.value.code == "E_CONFLICT"
    assert found.value.dispatch_state == "not_started"


@pytest.mark.parametrize("kind", ["input", "authorization"])
def test_preview_then_input_or_authorization_drift_refuses_before_save(bounded_case, kind):
    root, path = bounded_case["root"], bounded_case["board"]
    env = dict(os.environ, **grant(root))
    arguments = ["board", "footprint", "move", "--board", str(path), "--moves", "R1:100,100.5"]
    with driver_runtime.create_cli(environ=env, home=root) as cli:
        response = cli.run([*arguments, "--dry-run"]).envelope
        assert response["ok"], response
        token = response["data"]["confirm_token"]
        if kind == "input":
            path.with_suffix(".kicad_dru").write_bytes(b"(version 1)\n; changed input\n")
        else:
            scope = Path(env["KICAD_CLI_SCOPE_FILE"])
            value = json.loads(scope.read_bytes())
            value["revoked"] = True
            scope.write_text(json.dumps(value), encoding="utf-8")
        raw, seen = path.read_bytes(), list(bounded_case["seen"])
        refused = cli.run([*arguments, "--confirm", token]).envelope
    assert refused["ok"] is False
    assert refused["error"]["code"] in {"E_CONFLICT", "E_FORBIDDEN"}
    assert bounded_case["seen"] == seen
    assert path.read_bytes() == raw
    assert not Path(str(path) + ".kicad-cli.journal").exists()
