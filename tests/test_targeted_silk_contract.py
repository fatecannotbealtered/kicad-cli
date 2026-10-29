"""Finite host selection tests; native font/geometry and EXE acceptance are separate."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from kicad_cli import engineering_silk as silk
from kicad_cli.engineering_feedback import can_apply, feedback
from kicad_cli.fileformat.board import Board
from kicad_cli.fileformat.sexpr import Document
from kicad_cli.sync_inputs import InputError

FP1 = "00000000-0000-4000-8000-000000000001"
REF1 = "00000000-0000-4000-8000-000000000002"
FP2 = "00000000-0000-4000-8000-000000000003"
REF2 = "00000000-0000-4000-8000-000000000004"


def target():
    return {
        "reference": "C10",
        "footprint_uuid": FP1,
        "reference_uuid": REF1,
        "layer": "F.SilkS",
        "from_mm": [10, 8],
        "to_mm": [10, 7],
    }


def board_text():
    return f'''(kicad_pcb (version 20260101) (generator "finite-test")
      (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (37 "F.SilkS" user))
      (setup (pad_to_mask_clearance 0))
      (footprint "Test:C" (layer "F.Cu") (uuid "{FP1}") (at 10 10)
        (property "Reference" "C10" (at 0 -2 0) (layer "F.SilkS") (uuid "{REF1}")
          (effects (font (size 1 1) (thickness 0.15))))
        (property "Value" "100n" (at 0 2 0) (layer "F.Fab"))
        (pad "1" smd rect (at -1 0) (size 1 1) (layers "F.Cu" "F.Mask") (net "GND")))
      (footprint "Test:C" (layer "F.Cu") (uuid "{FP2}") (at 15 10)
        (property "Reference" "C11" (at 0 -2 0) (layer "F.SilkS") (uuid "{REF2}")
          (effects (font (size 1 1) (thickness 0.15))))
        (pad "1" smd rect (at -1 0) (size 1 1) (layers "F.Cu" "F.Mask") (net "GND")))
      (segment (start 9 10) (end 14 10) (width 0.2) (layer "F.Cu") (net "GND"))
      (via (at 12 10) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "GND"))
    )'''


def report(rows=None):
    rows = rows if rows is not None else [target()]
    moves, checks = [], []
    for row in rows:
        old, new = silk.point_nm(row["from_mm"]), silk.point_nm(row["to_mm"])
        dx, dy = new[0] - old[0], new[1] - old[1]
        box = [old[0] - 1_000_000, old[1] - 1_000_000, old[0] + 1_000_000, old[1] + 1_000_000]
        style = {
            "text": row["reference"],
            "visible": True,
            "font_name": "",
            "font_property": "KiCad Font",
            "font_pointer_present": False,
            "size_nm": [1_000_000, 1_000_000],
            "thickness_nm": 150_000,
            "effective_pen_width_nm": 150_000,
            "auto_thickness": False,
            "angle_degrees": 0.0,
            "mirrored": False,
            "bold": False,
            "italic": False,
            "horizontal_justify": 0,
            "vertical_justify": 0,
            "layer": 37,
            "keep_upright": True,
            "multiline_allowed": False,
            "line_spacing": 1.0,
            "knockout": False,
        }
        common = {
            "ref": row["reference"],
            "footprint_uuid": row["footprint_uuid"],
            "reference_uuid": row["reference_uuid"],
            "layer": row["layer"],
            "from_nm": old,
            "to_nm": new,
            "style": style,
            "bbox_before_nm": box,
            "bbox_after_nm": [box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy],
            "drawn_bbox_before_nm": list(box),
            "drawn_bbox_after_nm": [box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy],
        }
        checks.append(
            {
                **deepcopy(common),
                "inside_edge": True,
                "clear": True,
                "manufacturing_conflicts": [],
                "owner_body_within_3mm": True,
            }
        )
        if old != new:
            moves.append(
                {
                    **deepcopy(common),
                    "from_mm": row["from_mm"],
                    "to_mm": row["to_mm"],
                    "relative_nm": [0, -3_000_000],
                }
            )
    return {
        "schema": "targeted-silkscreen/v1",
        "complete": True,
        "selection_mode": "explicit_targets",
        "targets": rows,
        "moves": moves,
        "checks": checks,
        "mask_observations": [],
        "stuck": [],
        "issues": [],
        "references": len(rows),
        "clearance_mm": 0.15,
    }


def test_targets_schema_and_actual_normalized_fields_match_exactly():
    declaration = json.loads(
        (Path(__file__).resolve().parents[1] / "kicad_cli/driver_contract.json").read_bytes()
    )
    command = next(
        row
        for row in declaration["reference"]["commands"]
        if row["path"] == "board silkscreen place"
    )
    assert next(p for p in command["params"] if p["name"] == "targets")["required"] is False
    schema = declaration["reference"]["json_schemas"][command["driver"]["request_schema"]]["$defs"][
        "targets_file"
    ]
    fields = schema["properties"]["targets"]["items"]
    actual = silk.targets_document(
        json.dumps({"schema": silk.TARGET_SCHEMA, "targets": [target()]})
    )[0]
    assert set(fields["properties"]) == set(fields["required"]) == set(actual)
    assert fields["additionalProperties"] is False and schema["additionalProperties"] is False


def mask_observation(source="board_drawing", *, tented=False):
    row = {
        "object_uuid": "00000000-0000-4000-8000-000000000009",
        "source": source,
        "layer": "F.Mask",
        "box_nm": [20_700_000, 20_700_000, 21_300_000, 21_300_000],
    }
    if source == "via":
        row.update(
            effective_tented=tented,
            on_mask_layer=not tented,
            front_copper_diameter_nm=600_000,
            mask_expansion_nm=None if tented else 0,
            geometry="effective_front_tenting_no_opening"
            if tented
            else "conservative_rectangle_around_front_circular_opening",
        )
        if tented:
            row["box_nm"] = None
    elif source == "pad":
        row.update(
            kind="mask_opening_conservative_rectangle", footprint_uuid=FP2, mask_expansion_nm=0
        )
    elif source == "track":
        row["kind"] = "track_mask_opening_conservative_rectangle"
    else:
        row["kind"] = "mask_graphic_conservative_rectangle"
    return row


@pytest.mark.parametrize(
    "source,tented",
    [
        ("pad", False),
        ("board_drawing", False),
        ("footprint_graphic", False),
        ("footprint_field", False),
        ("track", False),
        ("via", False),
        ("via", True),
    ],
)
def test_actual_mask_observation_branches_match_public_schema_exact_fields(source, tented):
    from jsonschema import Draft202012Validator

    declaration = json.loads(
        (Path(__file__).resolve().parents[1] / "kicad_cli/driver_contract.json").read_bytes()
    )
    schema = declaration["reference"]["json_schemas"][silk.TARGET_SCHEMA_REF.split("#")[0]][
        "$defs"
    ]["mask_observations"]
    observed = mask_observation(source, tented=tented)
    validator = Draft202012Validator(schema)
    assert not list(validator.iter_errors([observed]))
    matched = [
        branch
        for branch in schema["items"]["oneOf"]
        if not list(Draft202012Validator(branch).iter_errors(observed))
    ]
    assert len(matched) == 1
    assert set(matched[0]["properties"]) == set(matched[0]["required"]) == set(observed)
    for field in observed:
        missing = {key: value for key, value in observed.items() if key != field}
        assert list(validator.iter_errors([missing])), field
    value = report()
    value["mask_observations"] = [observed]
    silk.validate_result(value, value["targets"], clearance_mm=0.15)


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "wrong-layer",
        "unknown-source",
        "unknown-tenting",
        "contradictory-tenting",
        "missing-expansion",
        "exposed-wrong-diameter",
        "tented-has-opening",
        "duplicate",
        "overlap",
        "clearance-gap",
        "clearance-drift",
    ],
)
def test_host_refuses_incomplete_or_conflicting_mask_observations(case):
    value = report()
    row = mask_observation(
        "via" if "tenting" in case or "exposed" in case or "tented" in case else "board_drawing"
    )
    value["mask_observations"] = [row]
    if case == "missing":
        del value["mask_observations"]
    elif case == "wrong-layer":
        row["layer"] = "B.Mask"
    elif case == "unknown-source":
        row["source"] = "unread"
    elif case == "unknown-tenting":
        row["effective_tented"] = None
    elif case == "contradictory-tenting":
        row["effective_tented"] = True
    elif case == "missing-expansion":
        row.pop("mask_expansion_nm", None)
    elif case == "exposed-wrong-diameter":
        row["front_copper_diameter_nm"] = 800_000
    elif case == "tented-has-opening":
        row.update(
            effective_tented=True,
            on_mask_layer=False,
            geometry="effective_front_tenting_no_opening",
            mask_expansion_nm=None,
        )
    elif case == "duplicate":
        value["mask_observations"].append(deepcopy(row))
    elif case == "overlap":
        row["box_nm"] = [9_500_000, 6_500_000, 10_500_000, 7_500_000]
    elif case == "clearance-gap":
        row["box_nm"] = [11_100_000, 6_500_000, 12_100_000, 7_500_000]
    elif case == "clearance-drift":
        value["clearance_mm"] = 0
    if case == "missing-expansion":
        value["mask_observations"] = [mask_observation("pad")]
        del value["mask_observations"][0]["mask_expansion_nm"]
    with pytest.raises(ValueError):
        silk.validate_result(value, value["targets"], clearance_mm=0.15)


@pytest.mark.parametrize(
    "case",
    [
        "wrong-root",
        "missing-schema",
        "wrong-schema",
        "empty",
        "missing-field",
        "extra-field",
        "wrong-uuid",
        "wrong-layer",
        "boolean",
        "string-coordinate",
        "nan",
        "infinite",
        "sub-nm",
        "coordinate-overflow",
        "finite-huge-exponent",
        "repeat-reference",
        "repeat-footprint",
        "repeat-field",
        "duplicate-json-key",
    ],
    ids=str,
)
def test_invalid_target_documents_are_structured_validation_errors(case):
    value = {"schema": silk.TARGET_SCHEMA, "targets": [target()]}
    row = value["targets"][0]
    if case == "wrong-root":
        value = []
    elif case == "missing-schema":
        del value["schema"]
    elif case == "wrong-schema":
        value["schema"] = "v2"
    elif case == "empty":
        value["targets"] = []
    elif case == "missing-field":
        del row["reference_uuid"]
    elif case == "extra-field":
        row["force"] = True
    elif case == "wrong-uuid":
        row["reference_uuid"] = "not-a-uuid"
    elif case == "wrong-layer":
        row["layer"] = "B.SilkS"
    elif case == "boolean":
        row["to_mm"][0] = True
    elif case == "string-coordinate":
        row["to_mm"][0] = "10"
    elif case == "nan":
        row["to_mm"][0] = float("nan")
    elif case == "infinite":
        row["to_mm"][0] = float("inf")
    elif case == "sub-nm":
        row["to_mm"][0] = 10.0000001
    elif case == "coordinate-overflow":
        row["to_mm"][0] = 2147.483648
    elif case.startswith("repeat"):
        second = {**target(), "reference": "C11", "footprint_uuid": FP2, "reference_uuid": REF2}
        field = {
            "repeat-reference": "reference",
            "repeat-footprint": "footprint_uuid",
            "repeat-field": "reference_uuid",
        }[case]
        second[field] = row[field]
        value["targets"].append(second)
    raw = json.dumps(value)
    if case == "finite-huge-exponent":
        raw = raw.replace('"to_mm": [10, 7]', '"to_mm": [1e999999, 7]')
    if case == "duplicate-json-key":
        raw = raw.replace('"schema":', '"schema":"extra", "schema":', 1)
    with pytest.raises(InputError) as caught:
        silk.targets_document(raw)
    assert caught.value.details["parameter"] == "--targets"
    assert caught.value.details["schema_ref"] == silk.TARGET_SCHEMA_REF


@pytest.mark.parametrize(
    "case",
    [
        "absent",
        "duplicate-ref",
        "duplicate-fp-uuid",
        "duplicate-field-uuid",
        "two-reference-properties",
        "wrong-fp-uuid",
        "wrong-field-uuid",
        "wrong-from",
        "locked-modern",
        "locked-legacy",
        "locked-field",
        "wrong-layer",
    ],
    ids=str,
)
def test_selected_fields_reject_bad_actual_identity_or_lock(case):
    text, row = board_text(), target()
    if case == "absent":
        row["reference"] = "C99"
    elif case == "duplicate-ref":
        text = text.replace('"C11"', '"C10"')
    elif case == "duplicate-fp-uuid":
        text = text.replace(FP2, FP1)
    elif case == "duplicate-field-uuid":
        text = text.replace(REF2, REF1)
    elif case == "two-reference-properties":
        text = text.replace('(property "Value" "100n"', '(property "Reference" "C10"')
    elif case == "wrong-fp-uuid":
        row["footprint_uuid"] = FP2
    elif case == "wrong-field-uuid":
        row["reference_uuid"] = REF2
    elif case == "wrong-from":
        row["from_mm"] = [10, 8.000001]
    elif case == "locked-modern":
        text = text.replace('(footprint "Test:C"', '(footprint "Test:C" (locked yes)', 1)
    elif case == "locked-legacy":
        text = text.replace('(footprint "Test:C"', '(footprint "Test:C" locked', 1)
    elif case == "locked-field":
        text = text.replace(
            '(property "Reference" "C10"', '(property "Reference" "C10" (locked yes)', 1
        )
    elif case == "wrong-layer":
        text = text.replace('(layer "F.SilkS")', '(layer "B.SilkS")', 1)
    with pytest.raises(InputError):
        silk.selected_fields(Board(Document.parse(text)), [row])


def test_explicit_unlocked_noop_and_exact_decimal_nm_are_legal():
    row = target()
    row["to_mm"] = row["from_mm"]
    clean = silk.targets_document(json.dumps({"schema": silk.TARGET_SCHEMA, "targets": [row]}))
    silk.selected_fields(
        Board(Document.parse(board_text().replace("(at 10 10)", "(locked no) (at 10 10)", 1))),
        clean,
    )
    silk.validate_result(report(clean), clean)
    assert silk.point_nm([0.000001, -0.000001]) == [1, -1]


@pytest.mark.parametrize(
    "case",
    [
        "incomplete",
        "wrong-target",
        "partial",
        "extra-move",
        "duplicate-move",
        "wrong-position",
        "missing-style",
        "missing-box",
        "missing-check",
        "duplicate-check",
        "conflict",
    ],
    ids=str,
)
def test_partial_or_mismatched_native_report_cannot_be_applied(case):
    value = report()
    if case == "incomplete":
        value["complete"] = False
    elif case == "wrong-target":
        value["targets"] = [{**target(), "reference": "C11"}]
    elif case == "partial":
        value["moves"] = []
    elif case == "extra-move":
        value["moves"].append({**value["moves"][0], "reference_uuid": REF2})
    elif case == "duplicate-move":
        value["moves"].append(deepcopy(value["moves"][0]))
    elif case == "wrong-position":
        value["moves"][0]["to_nm"][0] += 1
    elif case == "missing-style":
        del value["moves"][0]["style"]
    elif case == "missing-box":
        del value["moves"][0]["bbox_after_nm"]
    elif case == "missing-check":
        value["checks"] = []
    elif case == "duplicate-check":
        value["checks"].append(deepcopy(value["checks"][0]))
    elif case == "conflict":
        value["checks"][0]["clear"] = False
    with pytest.raises(InputError):
        silk.validate_result(value, [target()])


def test_host_applies_one_exact_field_translation_after_complete_controlled_report(
    tmp_path, monkeypatch
):
    board = tmp_path / "test.kicad_pcb"
    board.write_text(board_text(), encoding="utf-8")
    calls = []
    monkeypatch.setattr("kicad_cli.kicad_env.find_python", lambda: "controlled-no-process")

    def controlled(argv, **kwargs):
        calls.append(json.loads(Path(argv[-1]).read_bytes()))
        return SimpleNamespace(returncode=0, stdout=json.dumps(report()).encode(), stderr=b"")

    monkeypatch.setattr(silk.subprocess, "run", controlled)
    value = silk.prepare(board, {"clearance": 0.15}, [target()])
    assert len(calls) == 1 and calls[0]["targets"] == [target()]
    silk.selected_fields(Board.load(board), [{**target(), "from_mm": target()["to_mm"]}])
    assert value == report()


def test_locked_member_blocks_entire_batch_before_controlled_native_or_candidate_write(
    tmp_path, monkeypatch
):
    board = tmp_path / "test.kicad_pcb"
    raw = board_text().replace("(at 15 10)", "(locked yes) (at 15 10)", 1).encode()
    board.write_bytes(raw)
    second = {
        **target(),
        "reference": "C11",
        "footprint_uuid": FP2,
        "reference_uuid": REF2,
        "from_mm": [15, 8],
        "to_mm": [15, 7],
    }
    monkeypatch.setattr("kicad_cli.kicad_env.find_python", lambda: "controlled-no-process")
    monkeypatch.setattr(
        silk.subprocess, "run", lambda *args, **kwargs: pytest.fail("locked batch reached native")
    )
    with pytest.raises(InputError):
        silk.prepare(board, {"clearance": 0.15}, [target(), second])
    assert board.read_bytes() == raw


def test_targeted_feedback_exposes_bounds_and_reading_limits_and_refuses_partial():
    clean = feedback("board silkscreen place", report())
    assert can_apply(clean) and clean["target_geometry"] == report()
    assert "copper projection" in clean["requires_visual_review"][0]
    bad = report()
    bad["complete"] = False
    assert not can_apply(feedback("board silkscreen place", bad))


@pytest.mark.parametrize(
    "case",
    [
        "move-null-style",
        "move-null-box",
        "move-bad-box-delta",
        "move-bad-drawn-delta",
        "check-missing-layer",
        "check-missing-from",
        "check-missing-to",
        "check-missing-style",
        "check-missing-box",
        "check-missing-drawn-box",
        "check-wrong-to",
        "check-wrong-style-text",
        "move-check-style-disagreement",
        "check-boolean-coordinate",
    ],
    ids=str,
)
def test_native_measurements_require_complete_matching_style_positions_and_dual_bounds(case):
    value = report()
    move, check = value["moves"][0], value["checks"][0]
    if case == "move-null-style":
        move["style"] = None
    elif case == "move-null-box":
        move["bbox_after_nm"] = None
    elif case == "move-bad-box-delta":
        move["bbox_after_nm"][0] += 1
    elif case == "move-bad-drawn-delta":
        move["drawn_bbox_after_nm"][0] += 1
    elif case.startswith("check-missing"):
        key = {
            "check-missing-layer": "layer",
            "check-missing-from": "from_nm",
            "check-missing-to": "to_nm",
            "check-missing-style": "style",
            "check-missing-box": "bbox_after_nm",
            "check-missing-drawn-box": "drawn_bbox_after_nm",
        }[case]
        del check[key]
    elif case == "check-wrong-to":
        check["to_nm"][0] += 1
    elif case == "check-wrong-style-text":
        check["style"]["text"] = "wrong"
    elif case == "move-check-style-disagreement":
        check["style"]["bold"] = True
    elif case == "check-boolean-coordinate":
        check["from_nm"][0] = True
    with pytest.raises(InputError):
        silk.validate_result(value, [target()])
    assert not can_apply(feedback("board silkscreen place", value))


def test_target_font_face_is_rejected_before_native_font_loading(tmp_path, monkeypatch):
    board = tmp_path / "test.kicad_pcb"
    raw = (
        board_text()
        .replace("(font (size 1 1)", '(font (face "Unqualified System Font") (size 1 1)', 1)
        .encode()
    )
    board.write_bytes(raw)
    monkeypatch.setattr("kicad_cli.kicad_env.find_python", lambda: "controlled-no-process")
    monkeypatch.setattr(
        silk.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("unsupported raw face reached native"),
    )
    with pytest.raises(InputError, match="font face"):
        silk.prepare(board, {"clearance": 0.15}, [target()])
    assert board.read_bytes() == raw


def test_dotdot_targets_path_has_the_same_public_and_captured_mapping_identity(
    tmp_path, monkeypatch
):
    from kicad_cli import drc_baseline, driver_runtime, engineering_prepare
    from kicad_cli.payload import write_txn
    from kicad_cli.sync_inputs import _absolute

    sub = tmp_path / "sub"
    sub.mkdir()
    selection = tmp_path / "targets.json"
    selection.write_text(
        json.dumps({"schema": silk.TARGET_SCHEMA, "targets": [target()]}), encoding="utf-8"
    )
    dotted = str(sub / ".." / selection.name)
    with driver_runtime.create_cli() as cli:
        command = next(
            c
            for c in driver_runtime.declarations()["reference"]["commands"]
            if c["path"] == "board silkscreen place"
        )
        params = cli._params(
            command, {"board": [str(tmp_path / "board.kicad_pcb")], "targets": [dotted]}, None
        )
    assert params["targets"] == str(_absolute(selection))
    primary = tmp_path / "candidate.kicad_pcb"
    primary.write_text(board_text(), encoding="utf-8")
    captured = tmp_path / "captured-selection.json"
    captured.write_bytes(selection.read_bytes())
    seen = []

    def prepared(_primary, _params, targets):
        seen.append(targets)
        return report(targets)

    monkeypatch.setattr(silk, "prepare", prepared)
    monkeypatch.setattr(drc_baseline, "capture", lambda _path: (None, {}))
    monkeypatch.setattr(drc_baseline, "record", lambda _value: {})
    monkeypatch.setattr(drc_baseline, "compare_records", lambda *_values: {"status": "passed"})
    monkeypatch.setattr(write_txn, "commit", lambda: None)
    # Deliberately pass the legal dotdot spelling to the private mapping path as
    # well: both boundaries must share capture's lexical normalization.
    value = engineering_prepare.prepare(
        {
            "command": "board silkscreen place",
            "params": {**params, "targets": dotted},
            "mapping": {str(_absolute(selection)): str(captured)},
            "primary": str(primary),
            "original_primary": str(tmp_path / "board.kicad_pcb"),
        }
    )
    assert value["ok"] and seen == [[target()]]
