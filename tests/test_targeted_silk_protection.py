"""Selected Reference x/y is the sole permitted saved-board difference."""

from __future__ import annotations

import json

import pytest
from test_targeted_silk_contract import REF1, board_text, target

from kicad_cli import engineering_inputs
from kicad_cli.engineering_readback import protection
from kicad_cli.sync_inputs import Drift

COMMAND = "board silkscreen place"


def protected(text):
    return protection(text.encode(), COMMAND, silk_targets=[target()])


def test_only_the_selected_reference_xy_translation_is_ignored():
    before = board_text()
    after = before.replace("(at 0 -2 0)", "(at 0 -3 0)", 1)
    assert before != after and protected(before) == protected(after)


@pytest.mark.parametrize(
    "old,new",
    [
        ('"C11" (at 0 -2 0)', '"C11" (at 0 -3 0)'),
        ('"C10" (at 0 -2 0)', '"C10" (at 0 -2 90)'),
        ("(font (size 1 1)", '(font (face "Arial") (size 1 1)'),
        ("(size 1 1) (thickness 0.15)", "(size 0.8 1) (thickness 0.15)"),
        ("(thickness 0.15)", "(thickness 0.1)"),
        ('(layer "F.SilkS")', '(layer "B.SilkS")'),
        ('"Reference" "C10"', '"Reference" "C99"'),
        ("(effects (font", "(effects hide (font"),
        (REF1, "00000000-0000-4000-8000-000000000099"),
        ("(at 10 10)", "(at 11 10)"),
        ("(at -1 0)", "(at -2 0)"),
        ('(net "GND")', '(net "OTHER")'),
        ("(end 14 10)", "(end 13 10)"),
        ("(width 0.2)", "(width 0.3)"),
        ("(drill 0.3)", "(drill 0.4)"),
        ("(pad_to_mask_clearance 0)", "(pad_to_mask_clearance 0.1)"),
        ('"Value" "100n"', '"Value" "10n"'),
    ],
    ids=[
        "non-target-translation",
        "target-angle",
        "font",
        "size",
        "stroke",
        "layer",
        "content",
        "visibility",
        "identity",
        "footprint-position",
        "pad-position",
        "net",
        "track",
        "track-width",
        "via-hole",
        "rules",
        "other-field",
    ],
)
def test_non_target_or_non_translation_changes_are_protected(old, new):
    before = board_text()
    after = before.replace(old, new, 1)
    assert before != after and protected(before) != protected(after)


def test_empty_explicit_allowlist_does_not_allow_any_reference_translation():
    before = board_text().encode()
    after = board_text().replace("(at 0 -2 0)", "(at 0 -3 0)", 1).encode()
    assert protection(before, COMMAND, silk_targets=[]) != protection(
        after, COMMAND, silk_targets=[]
    )
    # Omitted selector deliberately retains the historic all-reference policy.
    assert protection(before, COMMAND) == protection(after, COMMAND)


def test_explicit_text_translation_does_not_exempt_saved_zone_fill_geometry():
    before = (
        board_text()[:-1]
        + '(zone (filled_polygon (layer "F.Cu") (pts (xy 1 1) (xy 2 1) (xy 2 2)))) )'
    )
    after = before.replace("(xy 2 2)", "(xy 3 2)")
    assert protected(before) != protected(after)


@pytest.mark.parametrize(
    "field", ["selected-numeric-text", "non-target-numeric-value", "quoted-versus-bare"], ids=str
)
def test_explicit_protection_preserves_numeric_text_and_original_atom_tokens(field):
    before = board_text()
    if field == "selected-numeric-text":
        before = before.replace('"Reference" "C10"', '"Reference" "10"')
        after = before.replace('"Reference" "10"', '"Reference" "10.0"')
    else:
        before = before.replace('"Value" "100n"', '"Value" "100"')
        new = '"Value" "1e2"' if field == "non-target-numeric-value" else '"Value" 100'
        after = before.replace('"Value" "100"', new)
    assert protected(before) != protected(after)


@pytest.mark.parametrize(
    "changed", ["targets", "board", "project", "rules", "config", "environment"], ids=str
)
def test_captured_selection_and_consulted_input_drift_reject_before_save(
    tmp_path, monkeypatch, changed
):
    board = tmp_path / "board.kicad_pcb"
    board.write_text(board_text(), encoding="utf-8")
    targets = tmp_path / "targets.json"
    targets.write_text(
        json.dumps({"schema": "silkscreen-targets/v1", "targets": [target()]}), encoding="utf-8"
    )
    project, rules = board.with_suffix(".kicad_pro"), board.with_suffix(".kicad_dru")
    project.write_text("{}", encoding="utf-8")
    rules.write_text("(version 1)", encoding="utf-8")
    config = tmp_path / "preferences"
    config.mkdir()
    common = config / "kicad_common.json"
    common.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(engineering_inputs.lib_tables, "default_config_dir", lambda: config)
    monkeypatch.setattr(engineering_inputs.kicad_env, "find_kicad_root", lambda: None)
    captured, _primary = engineering_inputs.capture(
        COMMAND, {"board": str(board), "targets": str(targets), "clearance": 0.15}
    )
    assert captured.files[targets] == targets.read_bytes()
    old_board = board.read_bytes()
    if changed == "environment":
        monkeypatch.setenv("KICAD_FINITE_SILK_TEST", "changed")
    else:
        name = {
            "targets": targets,
            "board": board,
            "project": project,
            "rules": rules,
            "config": common,
        }[changed]
        name.write_bytes(name.read_bytes() + b"\n")
    with pytest.raises(Drift):
        captured.validate()
    if changed != "board":
        assert board.read_bytes() == old_board


@pytest.mark.parametrize("action", ["delete", "replace"], ids=str)
def test_selection_deletion_or_replacement_cannot_reuse_captured_bytes(tmp_path, action):
    selection = tmp_path / "targets.json"
    selection.write_text(
        json.dumps({"schema": "silkscreen-targets/v1", "targets": [target()]}), encoding="utf-8"
    )
    captured = engineering_inputs.Inputs()
    captured.add(selection, required=True)
    selection.unlink()
    if action == "replace":
        selection.write_text("{}", encoding="utf-8")
    with pytest.raises(Drift):
        captured.validate()
