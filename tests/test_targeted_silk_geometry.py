"""Finite targeted-silk arithmetic and fake-native contracts; no processes."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kicad_cli" / "payload"))
import targeted_silk as T  # noqa: E402


def target(ref="C10", fp="fp-c10", field="field-c10", before=(5, 5), after=(6, 5)):
    return {
        "reference": ref,
        "footprint_uuid": fp,
        "reference_uuid": field,
        "layer": "F.SilkS",
        "from_mm": list(before),
        "to_mm": list(after),
    }


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "0.0000001", 2001])
def test_coordinate_requires_finite_exact_supported_integer_nanometres(value):
    with pytest.raises(T.IncompleteGeometry):
        T.nm(value)


def test_exact_one_nanometre_is_not_rounded_away():
    assert T.nm("0.000001") == 1


@pytest.mark.parametrize(
    "alteration",
    ["duplicate_reference", "duplicate_identity", "extra_field", "wrong_layer", "too_far"],
)
def test_target_array_rejects_duplicate_inexact_or_unsupported_requests(alteration):
    first, second = target(), target("C11", "fp-c11", "field-c11")
    if alteration == "duplicate_reference":
        second["reference"] = first["reference"]
    elif alteration == "duplicate_identity":
        second["footprint_uuid"], second["reference_uuid"] = (
            first["footprint_uuid"],
            first["reference_uuid"],
        )
    elif alteration == "extra_field":
        second["force"] = True
    elif alteration == "wrong_layer":
        second["layer"] = "B.SilkS"
    else:
        second["to_mm"] = [11, 5]
    with pytest.raises(T.IncompleteGeometry):
        T.normalize_targets([first, second])


def test_translation_limit_uses_euclidean_distance_and_accepts_exact_boundary():
    assert T.normalize_targets([target(after=(8, 9))])
    with pytest.raises(T.IncompleteGeometry):
        T.normalize_targets([target(after=(8, 9.000001))])


RECTANGLE = [
    ((0, 0), (20_000_000, 0)),
    ((20_000_000, 0), (20_000_000, 20_000_000)),
    ((20_000_000, 20_000_000), (0, 20_000_000)),
    ((0, 20_000_000), (0, 0)),
]


def test_outline_requires_one_closed_axis_aligned_rectangle():
    assert T.rectangle_outline(RECTANGLE) == (0, 0, 20_000_000, 20_000_000)


@pytest.mark.parametrize(
    "segments",
    [
        RECTANGLE[:3],
        RECTANGLE + RECTANGLE[:1],
        [*RECTANGLE[:3], ((0, 20_000_000), (1, 0))],
        [*RECTANGLE[:3], RECTANGLE[0]],
    ],
)
def test_unsupported_or_nonclosed_outline_is_incomplete(segments):
    with pytest.raises(T.IncompleteGeometry):
        T.rectangle_outline(segments)


class Box:
    def __init__(self, values):
        self.values = values

    def GetLeft(self):
        return self.values[0]

    def GetTop(self):
        return self.values[1]

    def GetRight(self):
        return self.values[2]

    def GetBottom(self):
        return self.values[3]


class Item:
    def __init__(self, uid, layer=1):
        self.uid, self.layer = uid, layer
        self.this = id(self)
        self.m_Uuid = SimpleNamespace(AsString=lambda: uid)

    def GetLayer(self):
        return self.layer


class Text(Item):
    def __init__(self, uid="field-c10", value="C10", position=(5_000_000, 5_000_000), layer=1):
        super().__init__(uid, layer)
        self.value, self.position = value, position
        self.locked, self.visible, self.mirrored = False, True, False
        self.angle, self.font, self.corrupt_bbox, self.change_style = 0, None, False, False

    def GetText(self):
        return self.value

    def IsLocked(self):
        return self.locked

    def IsVisible(self):
        return self.visible

    def GetPosition(self):
        return SimpleNamespace(x=self.position[0], y=self.position[1])

    def GetFPRelativePosition(self):
        return self.GetPosition()

    def SetPosition(self, position):
        self.position = (position.x, position.y)
        if self.change_style:
            self.value = "CHANGED"

    def GetTextAngleDegrees(self):
        return self.angle

    def IsMirrored(self):
        return self.mirrored

    def GetFont(self):
        return self.font

    def GetFontName(self):
        return ""

    def GetFontProp(self):
        return "KiCad Font"

    def GetTextSize(self):
        return SimpleNamespace(x=1_000_000, y=1_000_000)

    def GetTextThickness(self):
        return 150_000

    def GetEffectiveTextPenWidth(self):
        return 150_000

    def GetAutoThickness(self):
        return False

    def IsKeepUpright(self):
        return True

    def IsMultilineAllowed(self):
        return True

    def GetLineSpacing(self):
        return 1.0

    def IsKnockout(self):
        return False

    def IsBold(self):
        return False

    def IsItalic(self):
        return False

    def GetHorizJustify(self):
        return 0

    def GetVertJustify(self):
        return 0

    def GetBoundingBox(self):
        x, y = self.position
        extra = 1 if self.corrupt_bbox and x != 5_000_000 else 0
        return Box((x - 500_000, y - 500_000, x + 500_000 + extra, y + 500_000))

    def GetEffectiveTextShape(self):
        return SimpleNamespace(BBox=self.GetBoundingBox)


class Pad(Item):
    def __init__(self, uid="pad-c10", box=(4_500_000, 6_500_000, 5_500_000, 7_500_000)):
        super().__init__(uid)
        self.box, self.shape, self.angle, self.expansion = box, 8, 0, 0

    def IsOnLayer(self, layer):
        return layer in (2, 3)

    def GetBoundingBox(self):
        return Box(self.box)

    def GetAttribute(self):
        return 7

    def GetShape(self):
        return self.shape

    def GetOrientationDegrees(self):
        return self.angle

    def GetSolderMaskExpansion(self, layer):
        assert layer == 2
        return self.expansion


class Footprint(Item):
    def __init__(self, uid="fp-c10", text=None, pad=None):
        super().__init__(uid)
        self.text, self.pad, self.locked = text or Text(), pad or Pad(), False
        self.value = Text("value-" + uid, "10k", layer=9)
        self.extra_fields, self.graphics = [], []

    def GetReference(self):
        return self.text.value

    def Reference(self):
        return self.text

    def Value(self):
        return self.value

    def GetFields(self):
        return [self.text, self.value, *self.extra_fields]

    def Pads(self):
        return [self.pad]

    def IsLocked(self):
        return self.locked

    def GraphicalItems(self):
        return self.graphics


class Segment(Item):
    def __init__(self, uid, start, end, layer=4):
        super().__init__(uid, layer)
        self.start, self.end = start, end

    def GetShape(self):
        return 6

    def Type(self):
        return 20

    def GetStart(self):
        return SimpleNamespace(x=self.start[0], y=self.start[1])

    def GetEnd(self):
        return SimpleNamespace(x=self.end[0], y=self.end[1])

    def GetBoundingBox(self):
        return Box(
            (
                min(self.start[0], self.end[0]) - 1,
                min(self.start[1], self.end[1]) - 1,
                max(self.start[0], self.end[0]) + 1,
                max(self.start[1], self.end[1]) + 1,
            )
        )


class Board:
    def __init__(self):
        self.fps = [Footprint()]
        self.drawings = [Segment(str(i), *edge) for i, edge in enumerate(RECTANGLE)]
        self.tracks = []
        self.zones = []

    def GetFootprints(self):
        return self.fps

    def GetDrawings(self):
        return self.drawings

    def GetTracks(self):
        return self.tracks

    def Zones(self):
        return self.zones


PCB = SimpleNamespace(
    F_SilkS=1,
    F_Mask=2,
    F_Cu=3,
    B_Cu=5,
    B_Mask=99,
    Edge_Cuts=4,
    SHAPE_T_SEGMENT=6,
    SHAPE_T_RECTANGLE=11,
    SHAPE_T_CIRCLE=12,
    SHAPE_T_ARC=13,
    PAD_ATTRIB_SMD=7,
    PAD_SHAPE_RECT=8,
    PAD_SHAPE_ROUNDRECT=10,
    PAD_SHAPE_CIRCLE=9,
    PCB_TRACE_T=20,
    PCB_VIA_T=21,
    PCB_ARC_T=22,
    VIATYPE_THROUGH=30,
    VECTOR2I=lambda x, y: SimpleNamespace(x=x, y=y),
    GetBuildVersion=lambda: "fake-native-contract-only",
)


def test_selected_translation_preserves_field_style_and_records_both_envelopes():
    result = T.measure_targets(Board(), PCB, [target()], 0.15)
    assert result["complete"] is True
    assert result["references"] == 1
    move = result["moves"][0]
    assert move["from_nm"] == [5_000_000, 5_000_000]
    assert move["to_nm"] == [6_000_000, 5_000_000]
    assert move["bbox_after_nm"] == list(T.shift(move["bbox_before_nm"], 1_000_000, 0))
    assert move["drawn_bbox_after_nm"] == list(T.shift(move["drawn_bbox_before_nm"], 1_000_000, 0))
    assert move["style"]["text"] == "C10"


def test_noop_target_is_checked_without_creating_a_move():
    result = T.measure_targets(Board(), PCB, [target(after=(5, 5))], 0.15)
    assert result["complete"] is True and result["moves"] == []
    assert len(result["checks"]) == 1


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "duplicate_reference",
        "duplicate_fp_uuid",
        "duplicate_field_uuid",
        "wrong_fp",
        "wrong_field",
        "locked_fp",
        "locked_field",
        "wrong_original_position",
        "wrong_layer",
        "hidden",
    ],
)
def test_identity_and_lock_failures_never_return_partial_moves(failure):
    board, request = Board(), target()
    fp = board.fps[0]
    if failure == "missing":
        request["reference"] = "MISSING"
    elif failure == "duplicate_reference":
        board.fps.append(Footprint("second-fp", Text("second-field")))
    elif failure == "duplicate_fp_uuid":
        board.fps.append(Footprint("fp-c10", Text("second-field", "C11")))
    elif failure == "duplicate_field_uuid":
        fp.extra_fields.append(Text("field-c10", "EXTRA"))
        fp.extra_fields[-1].this = id(fp.extra_fields[-1])
    elif failure == "wrong_fp":
        request["footprint_uuid"] = "missing-uuid"
    elif failure == "wrong_field":
        request["reference_uuid"] = "value-fp-c10"
    elif failure == "locked_fp":
        fp.locked = True
    elif failure == "locked_field":
        fp.text.locked = True
    elif failure == "wrong_original_position":
        request["from_mm"] = [5.000001, 5]
    elif failure == "wrong_layer":
        fp.text.layer = 9
    else:
        fp.text.visible = False
    result = T.measure_targets(board, PCB, [request], 0.15)
    assert result["complete"] is False
    assert result["moves"] == [] and result["issues"]


@pytest.mark.parametrize(
    "failure",
    [
        "font",
        "rotation",
        "mirrored",
        "multiline",
        "missing_envelope",
        "changed_style",
        "changed_envelope",
        "pad_shape",
        "pad_rotation",
    ],
)
def test_unread_or_unsupported_geometry_is_incomplete(failure):
    board = Board()
    text, pad = board.fps[0].text, board.fps[0].pad
    if failure == "font":
        text.font = SimpleNamespace(GetName=lambda: "Unqualified font")
    elif failure == "rotation":
        text.angle = 90
    elif failure == "mirrored":
        text.mirrored = True
    elif failure == "multiline":
        text.value = "C10\nextra"
    elif failure == "missing_envelope":
        text.GetEffectiveTextShape = lambda: None
    elif failure == "changed_style":
        text.change_style = True
    elif failure == "changed_envelope":
        text.corrupt_bbox = True
    elif failure == "pad_shape":
        pad.shape = 999
    else:
        pad.angle = 90
    result = T.measure_targets(board, PCB, [target()], 0.15)
    assert result["complete"] is False and result["moves"] == []


@pytest.mark.parametrize(
    "obstacle_kind", ["reference", "value", "other_field", "board_text", "graphic", "mask"]
)
def test_non_target_same_side_obstacles_are_fixed_and_block_destination(obstacle_kind):
    board = Board()
    if obstacle_kind == "mask":
        request = target(after=(5, 7))
    else:
        request = target()
        text = Text("obstacle", "FIXED", (6_000_000, 5_000_000))
        if obstacle_kind == "reference":
            board.fps.append(
                Footprint(
                    "other-fp",
                    text,
                    Pad("other-pad", (14_000_000, 14_000_000, 15_000_000, 15_000_000)),
                )
            )
        elif obstacle_kind == "value":
            board.fps[0].value = text
        elif obstacle_kind == "other_field":
            board.fps[0].extra_fields.append(text)
        elif obstacle_kind == "board_text":
            board.drawings.append(text)
        else:
            board.drawings.append(
                Segment("graphic", (6_000_000, 4_500_000), (6_000_000, 5_500_000), 1)
            )
    result = T.measure_targets(board, PCB, [request], 0.15)
    assert result["complete"] is False and result["moves"] == []
    assert result["checks"][0]["manufacturing_conflicts"]


def test_other_side_text_does_not_become_a_front_obstacle():
    board = Board()
    board.drawings.append(Text("back-text", "BACK", (6_000_000, 5_000_000), layer=99))
    assert T.measure_targets(board, PCB, [target()], 0.15)["complete"] is True


def test_effective_mask_expansion_is_used_instead_of_bare_pad_bounds():
    board = Board()
    board.fps[0].pad.expansion = 600_000
    result = T.measure_targets(board, PCB, [target(after=(6.1, 6))], 0.15)
    assert result["complete"] is False
    assert any(
        x["mask_expansion_nm"] == 600_000 for x in result["checks"][0]["manufacturing_conflicts"]
    )


def test_two_selected_destinations_are_checked_as_one_batch():
    board = Board()
    second = Footprint(
        "fp-c11",
        Text("field-c11", "C11", (9_000_000, 5_000_000)),
        Pad("pad-c11", (8_500_000, 6_500_000, 9_500_000, 7_500_000)),
    )
    board.fps.append(second)
    result = T.measure_targets(
        board,
        PCB,
        [target(after=(7, 5)), target("C11", "fp-c11", "field-c11", (9, 5), (7, 5))],
        0.15,
    )
    assert result["complete"] is False and result["moves"] == []
    assert all(
        any(
            x["kind"] == "other_selected_reference_destination"
            for x in check["manufacturing_conflicts"]
        )
        for check in result["checks"]
    )


def test_owner_gap_limit_is_not_replaced_by_distance_from_old_text():
    board = Board()
    result = T.measure_targets(board, PCB, [target(after=(9.5, 5))], 0.15)
    assert result["complete"] is False
    assert result["checks"][0]["owner_body_within_3mm"] is False


def test_board_edge_clearance_is_part_of_the_checked_destination():
    board = Board()
    result = T.measure_targets(board, PCB, [target(after=(0.5, 5))], 0.15)
    assert result["complete"] is False and result["checks"][0]["inside_edge"] is False


def test_copper_projection_overlap_is_reported_as_reading_advisory_not_collision():
    board = Board()
    copper = Segment("copper", (6_000_000, 4_500_000), (6_000_000, 5_500_000), 3)
    copper.IsOnLayer = lambda layer: layer == 3
    board.tracks.append(copper)
    result = T.measure_targets(board, PCB, [target()], 0.15)
    assert result["complete"] is True
    check = result["checks"][0]
    assert check["reading_copper_projection_advisories"][0]["object_uuid"] == "copper"
    assert check["copper_projection_is_manufacturing_violation"] is False
