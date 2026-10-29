"""SDK parameter copies must preserve the origin or refuse the export."""

from __future__ import annotations

import pytest

from kicad_cli.payload.plot_settings import copy_saved


class Params:
    def __init__(self, origin=False):
        self.origin = origin
        self.svg_fit = False

    def GetUseAuxOrigin(self):
        return self.origin

    def SetUseAuxOrigin(self, value):
        self.origin = value

    def GetSvgFitPagetoBoard(self):
        return self.svg_fit

    def SetSvgFitPageToBoard(self, value):
        self.svg_fit = value

    def IsSameAs(self, other):
        return self.origin == other.origin and self.svg_fit == other.svg_fit


def test_origin_and_sdk_spelling_variant_survive_default_controller_params():
    saved, controller = Params(True), Params(False)
    saved.svg_fit = True
    copy_saved(saved, controller)
    assert controller.origin and controller.svg_fit
    assert saved.origin and saved.svg_fit


def test_native_settings_mismatch_refuses_the_copy():
    class IgnoredOrigin(Params):
        def SetUseAuxOrigin(self, value):
            pass

    with pytest.raises(ValueError, match="comparison failed"):
        copy_saved(Params(True), IgnoredOrigin())


def test_opaque_legacy_optional_is_left_to_native_board_loading():
    class NativeParams(Params):
        def GetLegacyPlotViaOnMaskLayer(self):
            raise AssertionError("an opaque optional is not Python None or bool")

    source, target = NativeParams(True), NativeParams()
    copy_saved(source, target)
    assert target.origin
