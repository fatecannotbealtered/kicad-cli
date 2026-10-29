"""Public field previews disclose per-unit measured bounds and their limits."""

import re

import pytest
from test_sch_edit_contract import edit_scene as edit_scene

from kicad_cli.fileformat.sexpr import Document, copy, quote

# KiCad property encodings already accepted by the file reader. Explicit no
# and absence are positive controls; this suite does not rewrite visibility.
VISIBILITY = [
    ("bare-direct", "hide", "", True),
    ("list-direct", "(hide)", "", True),
    ("yes-direct", "(hide yes)", "", True),
    ("bare-effects", "", "hide", True),
    ("list-effects", "", "(hide)", True),
    ("yes-effects", "", "(hide yes)", True),
    ("no-direct", "(hide no)", "", False),
    ("no-effects", "", "(hide no)", False),
    ("absent", "", "", False),
]


def prepare_field(project, direct, effects, *, neighbor=False, style=""):
    """Prepare owned inputs, then freeze all bytes before the public preview."""
    path = project / "edit_child.kicad_sch"
    doc = Document.load(path)
    sym = next(
        s
        for s in doc.root.find_all("symbol")
        if any(p.value(1) == "Reference" and p.value(2) == "R10" for p in s.find_all("property"))
    )
    for name in ["Value", "Reference"] if neighbor else ["Value"]:
        prop = next(p for p in sym.find_all("property") if p.value(1) == name)
        selected = name == ("Reference" if neighbor else "Value")
        hidden = direct if selected else ""
        nested = effects if selected else ""
        angle, size = ("90", "1.27") if style == "rotation" else ("0", "2.54" if style else "1.27")
        replacement = Document.parse(
            f"(property {quote(name)} {quote(prop.value(2))} (at 10 10 {angle}) "
            f"{hidden} (effects (font (size {size} {size})) {nested}))"
        ).root
        sym.replace(prop, copy(replacement))
    doc.save(path)
    return {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}


@pytest.mark.parametrize("case,direct,effects,hidden", VISIBILITY, ids=[r[0] for r in VISIBILITY])
@pytest.mark.parametrize("neighbor", [False, True], ids=["edited-field", "neighbor-field"])
def test_visibility_formats_are_read_only_in_public_preview(
    edit_scene, case, direct, effects, hidden, neighbor
):
    project, _, _, preview = edit_scene
    original = prepare_field(project, direct, effects, neighbor=neighbor)
    code, planned = preview([{"op": "set", "ref": "R10", "value": "47k"}])
    assert code == 0 and planned["data"]["preview"]["can_apply"], planned
    feedback, rows = fields(planned)
    row = rows[0]
    assert row["before"]["text"] == "4k7" and row["after"]["text"] == "47k"
    for report in (row["before"], row["after"]):
        assert report["at_mm"] == [10, 10]
        conflicts = [c for c in report["conflicts"] if c["kind"] == "visible_field"]
        if neighbor:
            assert bool(conflicts) is not hidden
            assert all(c["field"] == "Reference" for c in conflicts)
            assert not any(
                c.get("field") == "Reference" and c["symbol_uuid"] == row["symbol_uuid"]
                for c in report["unmeasured_objects"]
            )
        else:
            assert report["status"] == (
                "not_visible" if hidden else "no_overlap_in_measured_bounds"
            )
            assert not conflicts
    assert feedback["requires_visual_review"] and feedback["definite_violations"] == []
    assert {p: p.read_bytes() for p in project.rglob("*") if p.is_file()} == original


@pytest.mark.parametrize("style", ["font", "rotation"])
@pytest.mark.parametrize("hidden", [False, True], ids=["visible", "hidden"])
def test_unsupported_style_does_not_override_visibility(edit_scene, style, hidden):
    project, _, _, preview = edit_scene
    original = prepare_field(project, "", "hide" if hidden else "", style=style)
    code, planned = preview([{"op": "set", "ref": "R10", "value": "47k"}])
    assert code == 0, planned
    feedback, rows = fields(planned)
    for report in (rows[0]["before"], rows[0]["after"]):
        assert report["status"] == ("not_visible" if hidden else "requires_visual_review")
        assert report["geometry_check"] == "not_performed"
    assert feedback["requires_visual_review"]
    assert {p: p.read_bytes() for p in project.rglob("*") if p.is_file()} == original


@pytest.mark.parametrize("direct,effects", [("(hide yes)", ""), ("", "hide")], ids=["new", "old"])
def test_confirm_changes_only_requested_text_and_preserves_hidden_field(
    edit_scene, direct, effects
):
    project, changes, call, preview = edit_scene
    original = prepare_field(project, direct, effects)
    code, planned = preview([{"op": "set", "ref": "R10", "value": "47k"}])
    assert code == 0
    assert fields(planned)[1][0]["after"]["status"] == "not_visible"
    args = ["sch", "edit", "--schematic", project / "edit.kicad_sch", "--changes", changes]
    code, saved = call(*args, "--confirm", planned["data"]["confirm_token"])
    assert code == 0 and saved["data"]["receipt"]["outcome"]["persistence"] == "saved"
    child = project / "edit_child.kicad_sch"
    expected = dict(original)
    expected[child] = original[child].replace(b'"Value" "4k7"', b'"Value" "47k"', 1)
    assert {p: p.read_bytes() for p in project.rglob("*") if p.is_file()} == expected
    code, repeated = call(*args, "--confirm", planned["data"]["confirm_token"])
    assert code == 6 and repeated["error"]["code"] == "E_CONFLICT"
    assert {p: p.read_bytes() for p in project.rglob("*") if p.is_file()} == expected


def fields(planned):
    feedback = planned["data"]["preview"]["extensions"]["engineering_feedback"]
    return feedback, feedback["affected_edits"][0]["field_layout"]


def test_existing_input_overlap_is_exposed_before_confirm_for_each_unit(edit_scene):
    project, _, _, preview = edit_scene
    original = {p: p.read_bytes() for p in project.glob("*.kicad_sch")}
    code, planned = preview([{"op": "set", "ref": "U1", "value": "LONG_GAIN_BUFFER_VALUE"}])
    assert code == 0 and planned["data"]["preview"]["can_apply"]
    feedback, rows = fields(planned)
    assert {r["unit"] for r in rows} == {1, 2}
    assert all(r["field"] == "Value" for r in rows)
    assert all(r["before"]["conflicts"] and r["after"]["conflicts"] for r in rows)
    assert all(r["before"]["at_mm"] == r["after"]["at_mm"] for r in rows)
    assert all(
        any(c["kind"] == "symbol_body_or_pin_bounds" for c in r["after"]["conflicts"]) for r in rows
    )
    assert feedback["geometry_check"] == "partial_conservative_schematic_bounds"
    assert feedback["definite_violations"] == []
    assert all(p.read_bytes() == value for p, value in original.items())


def test_shared_field_feedback_names_all_instance_paths(edit_scene):
    project, _, _, preview = edit_scene
    original = (project / "edit_child.kicad_sch").read_bytes()
    code, planned = preview([{"op": "set", "ref": "R10", "value": "47k"}])
    assert code == 0
    feedback, rows = fields(planned)
    assert feedback["affected_edits"][0]["also_changes"] == ["R20"]
    assert len(rows) == 1
    instances = rows[0]["instances"]
    assert sorted(p["ref"] for p in instances) == ["R10", "R20"]
    assert len({p["instance_path"] for p in instances}) == 2
    assert all(p["full_path"].endswith("/" + rows[0]["symbol_uuid"]) for p in instances)
    assert (project / "edit_child.kicad_sch").read_bytes() == original


def test_nondefault_field_font_is_explicitly_unmeasured(edit_scene):
    project, _, _, preview = edit_scene
    path = project / "edit_child.kicad_sch"
    text = path.read_text(encoding="utf-8")
    text, changed = re.subn(
        r'(\(property "Value" "4k7".*?\(size )1\.27 1\.27',
        r"\g<1>2.54 2.54",
        text,
        count=1,
        flags=re.S,
    )
    assert changed == 1
    path.write_text(text, encoding="utf-8")
    original = path.read_bytes()
    code, planned = preview([{"op": "set", "ref": "R10", "value": "47k"}])
    assert code == 0
    feedback, rows = fields(planned)
    assert rows[0]["after"]["status"] == "requires_visual_review"
    assert rows[0]["after"]["geometry_check"] == "not_performed"
    assert "font" in rows[0]["after"]["limit"]
    assert feedback["requires_visual_review"]
    assert path.read_bytes() == original
