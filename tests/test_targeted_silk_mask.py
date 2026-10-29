"""Finite synthetic KiCad10 mask API contracts, not actual Native evidence."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_targeted_silk_geometry import PCB, Board, Box, Item, Segment, T, Text, target


class MaskGraphic(Segment):
    def __init__(
        self,
        uid="mask-graphic",
        layer=PCB.F_Mask,
        box=(5_800_000, 4_800_000, 6_200_000, 5_200_000),
    ):
        super().__init__(uid, (box[0], box[1]), (box[2], box[3]), layer)
        self.effective_box = box
        self.shape = PCB.SHAPE_T_SEGMENT
        self.effective_layers = []

    def GetShape(self):
        return self.shape

    def GetEffectiveShape(self, layer):
        self.effective_layers.append(layer)
        assert layer == PCB.F_Mask
        return SimpleNamespace(BBox=lambda: Box(self.effective_box))

    def IsOnLayer(self, layer):
        return layer == self.layer


class Via(Item):
    def __init__(self, tented=False, position=(6_000_000, 5_000_000)):
        super().__init__("via-mask", PCB.F_Cu)
        self.tented = tented
        self.position = position
        self.expansion = 50_000
        self.width = 600_000
        self.size = (600_000, 600_000)
        self.shape = PCB.PAD_SHAPE_CIRCLE
        self.via_type = PCB.VIATYPE_THROUGH
        self.top, self.bottom = PCB.F_Cu, PCB.B_Cu
        self.post_machined = False
        self.mask_membership_override = "not_overridden"
        self.front_copper = True
        self.tenting_layers, self.width_layers, self.shape_layers, self.size_layers = [], [], [], []

    def Type(self):
        return PCB.PCB_VIA_T

    def IsOnLayer(self, layer):
        if layer == PCB.F_Mask:
            if self.mask_membership_override != "not_overridden":
                return self.mask_membership_override
            return not self.tented
        return self.front_copper if layer == PCB.F_Cu else layer == PCB.B_Cu

    def IsTented(self, layer):
        self.tenting_layers.append(layer)
        assert layer == PCB.F_Mask
        return self.tented

    def GetViaType(self):
        return self.via_type

    def TopLayer(self):
        return self.top

    def BottomLayer(self):
        return self.bottom

    def IsBackdrilledOrPostMachined(self, layer):
        assert layer == PCB.F_Cu
        return self.post_machined

    def Padstack(self):
        def shape(layer):
            self.shape_layers.append(layer)
            assert layer == PCB.F_Cu
            return self.shape

        def size(layer):
            self.size_layers.append(layer)
            assert layer == PCB.F_Cu
            return SimpleNamespace(x=self.size[0], y=self.size[1])

        return SimpleNamespace(Shape=shape, Size=size)

    def GetWidth(self, layer):
        self.width_layers.append(layer)
        assert layer == PCB.F_Cu
        return self.width

    def GetSolderMaskExpansion(self):
        return self.expansion

    def GetPosition(self):
        return SimpleNamespace(x=self.position[0], y=self.position[1])

    def GetBoundingBox(self):
        x, y = self.position
        r = (self.width + 1) // 2
        return Box((x - r, y - r, x + r, y + r))

    def GetEffectiveShape(self, layer):
        raise AssertionError("KiCad10 via shape alone omits mask expansion and must not be used")


def measure(board):
    return T.measure_targets(board, PCB, [target()], 0.15)


@pytest.mark.parametrize("position", [(6_000_000, 5_000_000), (15_000_000, 15_000_000)])
def test_any_front_mask_zone_is_incomplete_without_guessing_its_geometry(position):
    board = Board()
    zone = Item("mask-zone", PCB.F_Mask)
    zone.IsOnLayer = lambda layer: layer == PCB.F_Mask
    zone.GetBoundingBox = lambda: Box((*position, position[0] + 500_000, position[1] + 500_000))
    board.zones.append(zone)
    before = board.fps[0].text.position
    result = measure(board)
    assert result["complete"] is False and result["moves"] == [] and result["checks"] == []
    assert result["issues"][0] == {
        "reason": "unsupported_mask_zone",
        "object_uuid": "mask-zone",
        "layer": "F.Mask",
    }
    assert board.fps[0].text.position == before


@pytest.mark.parametrize("layer", [PCB.F_Cu, PCB.B_Cu, PCB.B_Mask])
def test_non_front_mask_zone_does_not_become_a_front_opening(layer):
    board = Board()
    zone = Item("non-mask-zone", layer)
    zone.IsOnLayer = lambda requested: requested == layer
    board.zones.append(zone)
    result = measure(board)
    assert result["complete"] is True
    assert not any(row["object_uuid"] == zone.uid for row in result["mask_observations"])


@pytest.mark.parametrize("unproven", [None, 0, 1, "false", "true"])
def test_zone_mask_membership_requires_actual_native_boolean(unproven):
    board = Board()
    zone = Item("unknown-mask-zone")
    zone.IsOnLayer = lambda layer: unproven
    board.zones.append(zone)
    before = board.fps[0].text.position
    result = measure(board)
    assert result["complete"] is False and result["moves"] == [] and result["checks"] == []
    assert result["issues"][0]["reason"] == "zone_mask_membership_unproven"
    assert board.fps[0].text.position == before


def test_missing_zone_enumeration_api_is_incomplete_instead_of_an_empty_collection():
    board = Board()
    board.Zones = None
    result = measure(board)
    assert result["complete"] is False and result["moves"] == [] and result["checks"] == []
    assert result["issues"][0]["reason"] == "board_mask_zones_cannot_be_enumerated"


@pytest.mark.parametrize("layer", [PCB.F_Mask, PCB.F_Cu])
@pytest.mark.parametrize("unproven", [None, 0, 1, "false", "true"])
def test_pad_layer_membership_requires_native_boolean_before_target_translation(layer, unproven):
    board = Board()
    pad = board.fps[0].pad
    pad.IsOnLayer = lambda requested: unproven if requested == layer else True
    before = board.fps[0].text.position
    result = measure(board)
    assert result["complete"] is False and result["moves"] == [] and result["checks"] == []
    expected = (
        "pad_mask_membership_unproven"
        if layer == PCB.F_Mask
        else "pad_front_copper_membership_unproven"
    )
    assert result["issues"][0]["reason"] == expected
    assert result["issues"][0]["object_uuid"] == pad.uid
    assert board.fps[0].text.position == before


@pytest.mark.parametrize("location", ["board", "footprint"])
def test_front_mask_graphic_overlap_blocks_entire_target_batch(location):
    board = Board()
    graphic = MaskGraphic()
    (board.drawings if location == "board" else board.fps[0].graphics).append(graphic)
    result = measure(board)
    assert result["complete"] is False and result["moves"] == []
    assert any(
        row["kind"] == "mask_graphic_conservative_rectangle" and row["object_uuid"] == graphic.uid
        for row in result["checks"][0]["manufacturing_conflicts"]
    )
    assert graphic.effective_layers == [PCB.F_Mask]


def test_nonoverlapping_mask_graphic_is_measured_without_inventing_collision():
    board = Board()
    graphic = MaskGraphic(box=(12_000_000, 12_000_000, 13_000_000, 13_000_000))
    board.drawings.append(graphic)
    result = measure(board)
    assert result["complete"] is True
    observed = next(row for row in result["mask_observations"] if row["object_uuid"] == graphic.uid)
    assert observed["box_nm"] == list(graphic.effective_box)
    assert observed["layer"] == "F.Mask"


def test_mask_graphic_uses_effective_geometry_instead_of_display_bbox():
    board = Board()
    graphic = MaskGraphic()
    graphic.GetBoundingBox = lambda: Box((12_000_000, 12_000_000, 13_000_000, 13_000_000))
    board.drawings.append(graphic)
    assert measure(board)["complete"] is False


def test_back_mask_graphic_does_not_become_a_front_opening():
    board = Board()
    graphic = MaskGraphic(layer=PCB.B_Mask)
    board.drawings.append(graphic)
    result = measure(board)
    assert result["complete"] is True and graphic.effective_layers == []
    assert not any(row["object_uuid"] == graphic.uid for row in result["mask_observations"])


@pytest.mark.parametrize(
    "shape", [PCB.SHAPE_T_SEGMENT, PCB.SHAPE_T_RECTANGLE, PCB.SHAPE_T_CIRCLE, PCB.SHAPE_T_ARC]
)
def test_supported_mask_graphics_all_use_their_actual_native_layer_envelopes(shape):
    board = Board()
    graphic = MaskGraphic()
    graphic.shape = shape
    board.drawings.append(graphic)
    assert measure(board)["complete"] is False
    assert graphic.effective_layers == [PCB.F_Mask]


@pytest.mark.parametrize(
    "failure", ["unsupported_shape", "missing_shape", "missing_envelope", "empty_envelope"]
)
def test_unproven_mask_graphic_is_incomplete_before_any_target_translation(failure):
    board = Board()
    graphic = MaskGraphic()
    if failure == "unsupported_shape":
        graphic.shape = 999
    elif failure == "missing_shape":
        graphic.GetShape = None
    elif failure == "missing_envelope":
        graphic.GetEffectiveShape = lambda layer: None
    else:
        graphic.effective_box = (6_000_000, 5_000_000, 6_000_000, 5_000_000)
    board.drawings.append(graphic)
    before = board.fps[0].text.position
    result = measure(board)
    assert result["complete"] is False and result["moves"] == []
    assert result["checks"] == [] and result["issues"]
    assert board.fps[0].text.position == before


def test_front_mask_text_is_an_opening_and_requires_qualified_font():
    board = Board()
    board.drawings.append(Text("mask-text", "OPEN", (6_000_000, 5_000_000), PCB.F_Mask))
    result = measure(board)
    assert result["complete"] is False
    assert result["checks"][0]["manufacturing_conflicts"][0]["layer"] == "F.Mask"


def test_mask_knockout_text_is_explicitly_incomplete_instead_of_using_positive_strokes():
    board = Board()
    text = Text("mask-text", "OPEN", (6_000_000, 5_000_000), PCB.F_Mask)
    text.IsKnockout = lambda: True
    board.drawings.append(text)
    result = measure(board)
    assert result["complete"] is False and result["checks"] == []
    assert result["issues"][0]["reason"] == "unsupported_mask_knockout_text"


def test_untented_via_opening_blocks_text_and_is_not_only_a_copper_advisory():
    board = Board()
    via = Via()
    board.tracks.append(via)
    result = measure(board)
    assert result["complete"] is False and result["moves"] == []
    conflicts = result["checks"][0]["manufacturing_conflicts"]
    assert any(row["kind"] == "via_mask_opening_conservative_rectangle" for row in conflicts)
    observation = next(row for row in result["mask_observations"] if row["source"] == "via")
    assert observation["effective_tented"] is False and observation["on_mask_layer"] is True
    assert observation["box_nm"] == [5_650_000, 4_650_000, 6_350_000, 5_350_000]
    assert via.width_layers == via.shape_layers == via.size_layers == [PCB.F_Cu]
    assert via.tenting_layers == [PCB.F_Mask]


def test_effectively_tented_via_has_no_front_opening_but_copper_advisory_remains():
    board = Board()
    via = Via(tented=True)
    board.tracks.append(via)
    result = measure(board)
    assert result["complete"] is True
    observation = next(row for row in result["mask_observations"] if row["source"] == "via")
    assert observation["effective_tented"] is True and observation["on_mask_layer"] is False
    assert observation["box_nm"] is None and observation["mask_expansion_nm"] is None
    assert observation["geometry"] == "effective_front_tenting_no_opening"
    assert result["checks"][0]["manufacturing_conflicts"] == []
    assert result["checks"][0]["reading_copper_projection_advisories"]


def test_nonoverlapping_untented_via_is_measured_and_accepts_clear_target():
    board = Board()
    via = Via(position=(12_000_000, 12_000_000))
    board.tracks.append(via)
    result = measure(board)
    assert result["complete"] is True
    assert next(row for row in result["mask_observations"] if row["source"] == "via")["box_nm"] == [
        11_650_000,
        11_650_000,
        12_350_000,
        12_350_000,
    ]


def test_via_expansion_reaches_target_even_when_bare_copper_and_clearance_do_not():
    board = Board()
    via = Via(position=(7_000_000, 5_000_000))
    via.expansion = 200_000
    board.tracks.append(via)
    result = measure(board)
    assert result["complete"] is False
    assert result["checks"][0]["reading_copper_projection_advisories"] == []
    assert result["checks"][0]["manufacturing_conflicts"][0]["mask_expansion_nm"] == 200_000


def test_odd_nanometre_via_diameter_is_conservatively_enclosed_without_rounding_in():
    via = Via()
    via.width, via.size, via.expansion = 600_001, (600_001, 600_001), 0
    result = T._via_mask_observation(via, PCB)
    assert result["box_nm"] == [5_699_999, 4_699_999, 6_300_001, 5_300_001]


@pytest.mark.parametrize(
    "failure",
    [
        "unknown_tenting",
        "unknown_membership",
        "contradictory_tented",
        "contradictory_open",
        "wrong_top",
        "wrong_bottom",
        "wrong_copper_membership",
        "unsupported_via_type",
        "post_machined",
        "unknown_post_machined",
        "unknown_shape",
        "oval_size",
        "width_mismatch",
        "zero_size",
        "collapsed_opening",
        "missing_tenting_api",
        "missing_padstack_api",
        "missing_expansion_api",
        "missing_classification",
        "unknown_classification",
    ],
)
def test_unproven_via_or_wrong_layer_is_incomplete_before_any_target_translation(failure):
    board = Board()
    via = Via()
    if failure == "unknown_tenting":
        via.tented = None
    elif failure == "unknown_membership":
        via.mask_membership_override = None
    elif failure == "contradictory_tented":
        via.tented, via.mask_membership_override = True, True
    elif failure == "contradictory_open":
        via.mask_membership_override = False
    elif failure == "wrong_top":
        via.top = PCB.B_Cu
    elif failure == "wrong_bottom":
        via.bottom = PCB.F_Cu
    elif failure == "wrong_copper_membership":
        via.front_copper = False
    elif failure == "unsupported_via_type":
        via.via_type = 999
    elif failure == "post_machined":
        via.post_machined = True
    elif failure == "unknown_post_machined":
        via.post_machined = None
    elif failure == "unknown_shape":
        via.shape = 999
    elif failure == "oval_size":
        via.size = (600_000, 700_000)
    elif failure == "width_mismatch":
        via.width = 700_000
    elif failure == "zero_size":
        via.width, via.size = 0, (0, 0)
    elif failure == "collapsed_opening":
        via.expansion = -300_000
    elif failure == "missing_tenting_api":
        via.IsTented = None
    elif failure == "missing_padstack_api":
        via.Padstack = None
    elif failure == "missing_expansion_api":
        via.GetSolderMaskExpansion = None
    elif failure == "missing_classification":
        via.Type = None
    else:
        via.Type = lambda: 999
    board.tracks.append(via)
    before = board.fps[0].text.position
    result = measure(board)
    assert result["complete"] is False and result["moves"] == []
    assert result["checks"] == [] and result["issues"]
    assert board.fps[0].text.position == before


def test_opening_on_front_track_uses_native_effective_mask_shape():
    board = Board()
    track = Segment("exposed-track", (6_000_000, 4_000_000), (6_000_000, 6_000_000), PCB.F_Cu)
    track.IsOnLayer = lambda layer: layer in (PCB.F_Cu, PCB.F_Mask)
    layers = []

    def effective(layer):
        layers.append(layer)
        assert layer == PCB.F_Mask
        return SimpleNamespace(BBox=lambda: Box((5_800_000, 3_800_000, 6_200_000, 6_200_000)))

    track.GetEffectiveShape = effective
    board.tracks.append(track)
    result = measure(board)
    assert result["complete"] is False and layers == [PCB.F_Mask]
    assert (
        result["checks"][0]["manufacturing_conflicts"][0]["kind"]
        == "track_mask_opening_conservative_rectangle"
    )


def test_copper_graphic_with_automatic_mask_opening_is_explicitly_incomplete():
    board = Board()
    graphic = MaskGraphic(layer=PCB.F_Cu)
    graphic.IsOnLayer = lambda layer: layer in (PCB.F_Cu, PCB.F_Mask)
    board.drawings.append(graphic)
    result = measure(board)
    assert result["complete"] is False and result["checks"] == []
    assert result["issues"][0]["reason"] == "unsupported_copper_graphic_mask_opening"
