"""The lossless S-expression layer every file this tool touches goes through.

Two promises, and each test holds one of them. An untouched list comes back
byte for byte, however it was formatted; a changed list comes back the way
KiCad itself would have written it. The layout expectations below are not
invented: the `pins` and `pts` blocks are copied from a board KiCad 10 saved
(`pic_programmer`), so the renderer is held to KiCad's output rather than to
this file's author's idea of it.

None of this needs KiCad. `test_fileformat_conformance.py` holds the same
promises against every file KiCad ships.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from kicad_cli.fileformat import sexpr  # noqa: E402
from kicad_cli.fileformat.sexpr import Document, List, SexprError  # noqa: E402

BOARD = """(kicad_pcb
\t(version 20260206)
\t(generator "pcbnew")
\t(general
\t\t(thickness 1.6)
\t\t(legacy_teardrops no)
\t)
\t(net 1 "GND")
\t(footprint "Resistor_SMD:R_0603_1608Metric"
\t\t(layer "F.Cu")
\t\t(uuid "0f8c7b56-8d0e-4f55-9b4e-2d9a9c2b6f11")
\t\t(at 10 20 90)
\t\t(property "Reference" "R1"
\t\t\t(at 0 -1.43 90)
\t\t\t(layer "F.SilkS")
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1 1)
\t\t\t\t\t(thickness 0.15)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(pad "1" smd roundrect
\t\t\t(at -0.825 0 90)
\t\t\t(size 0.8 0.95)
\t\t\t(layers "F.Cu" "F.Mask" "F.Paste")
\t\t\t(net "GND")
\t\t)
\t)
\t(zone
\t\t(net "GND")
\t\t(layer "B.Cu")
\t\t(polygon
\t\t\t(pts
\t\t\t\t(xy 223.52 138.43) (xy 232.41 128.905) (xy 232.41 53.975) (xy 219.71 41.91) (xy 81.28 41.91) (xy 74.295 48.895)
\t\t\t\t(xy 74.295 127.635) (xy 86.36 138.43)
\t\t\t)
\t\t)
\t)
)
"""


@pytest.fixture
def defer_everything(monkeypatch):
    """Take the deferred path for lists of any size, not only large ones."""
    monkeypatch.setattr(sexpr, "DEFER_ABOVE", 0)


def same_tree(a: List, b: List) -> bool:
    stack = [(a, b)]
    while stack:
        x, y = stack.pop()
        if (x.start, x.end) != (y.start, y.end) or len(x.items) != len(y.items):
            return False
        for i, j in zip(x.items, y.items, strict=True):
            if isinstance(i, str) or isinstance(j, str):
                if i != j:
                    return False
            else:
                stack.append((i, j))
    return True


# -- untouched means identical ---------------------------------------------------


@pytest.mark.parametrize("lazy", [True, False])
@pytest.mark.parametrize(
    "variant",
    [
        pytest.param(lambda s: s, id="as-kicad-writes-it"),
        pytest.param(lambda s: s.replace("\n", "\r\n"), id="crlf"),
        pytest.param(lambda s: "\ufeff" + s, id="byte-order-mark"),
        pytest.param(lambda s: s.rstrip("\n"), id="no-final-newline"),
        pytest.param(lambda s: s.replace("\t", "  "), id="older-two-space-layout"),
        pytest.param(lambda s: " ".join(s.split()), id="all-on-one-line"),
    ],
)
def test_an_untouched_document_comes_back_byte_for_byte(variant, lazy, defer_everything):
    source = variant(BOARD)
    assert Document.parse(source, lazy=lazy).dumps() == source


def test_deferred_parsing_builds_the_same_tree_as_parsing_whole(defer_everything):
    lazy = Document.parse(BOARD).root
    whole = Document.parse(BOARD, lazy=False).root
    assert same_tree(lazy, whole)


def test_asking_what_an_item_is_does_not_parse_it(defer_everything):
    doc = Document.parse(BOARD)
    heads = [item.head for item in doc.root.items[1:]]
    assert heads == ["version", "generator", "general", "net", "footprint", "zone"]
    footprint = doc.root.find("footprint")
    assert footprint._items is None, "reading the head parsed the footprint"
    assert footprint.find("pad").value(1) == "1"
    assert doc.root.find("zone")._items is None, "reading one item parsed another"


# -- changed means laid out like KiCad -----------------------------------------------


def test_a_changed_atom_rewrites_its_own_line_and_nothing_else(defer_everything):
    doc = Document.parse(BOARD)
    at = doc.root.find("footprint").find("at")
    at.set(1, sexpr.number(11.5))
    after = doc.dumps()
    changed = [(a, b) for a, b in zip(BOARD.split("\n"), after.split("\n"), strict=True) if a != b]
    assert changed == [("\t\t(at 10 20 90)", "\t\t(at 11.5 20 90)")]


def test_an_inserted_list_is_laid_out_the_way_kicad_lays_it_out(defer_everything):
    doc = Document.parse(BOARD)
    footprint = doc.root.find("footprint")
    pad = List.new(
        "pad",
        sexpr.quote("2"),
        sexpr.symbol("smd"),
        sexpr.symbol("roundrect"),
        List.new("at", "0.825", "0", "90"),
        List.new("size", "0.8", "0.95"),
        List.new("layers", *(sexpr.quote(n) for n in ("F.Cu", "F.Mask", "F.Paste"))),
        List.new("net", sexpr.quote("GND")),
    )
    footprint.append(pad)
    expected_pad = (
        '\t\t(pad "2" smd roundrect\n'
        "\t\t\t(at 0.825 0 90)\n"
        "\t\t\t(size 0.8 0.95)\n"
        '\t\t\t(layers "F.Cu" "F.Mask" "F.Paste")\n'
        '\t\t\t(net "GND")\n'
        "\t\t)\n"
    )
    after = doc.dumps()
    assert expected_pad in after
    # The footprint was re-rendered, and it was already in KiCad's layout, so
    # removing the new pad again gives back the original text exactly.
    assert after.replace(expected_pad, "") == BOARD


def test_a_crlf_file_gets_crlf_in_what_was_written_new(defer_everything):
    source = BOARD.replace("\n", "\r\n")
    doc = Document.parse(source)
    doc.root.find("footprint").append(List.new("attr", "smd"))
    after = doc.dumps()
    assert "\t\t(attr smd)\r\n" in after
    assert "\n" not in after.replace("\r\n", ""), "a bare LF crept into a CRLF file"


def test_the_same_edit_gives_the_same_bytes_deferred_or_whole(defer_everything):
    outputs = []
    for lazy in (True, False):
        doc = Document.parse(BOARD, lazy=lazy)
        pad = doc.root.find("footprint").find("pad")
        pad.find("size").set(1, "1.2")
        pad.remove(pad.find("net"))
        doc.root.find("zone").find("polygon").find("pts").append(List.new("xy", "1", "2"))
        outputs.append(doc.dumps())
    assert outputs[0] == outputs[1]


def test_atoms_wrap_where_kicad_wraps_them():
    # Copied from pic_programmer.kicad_pcb, saved by KiCad 10.0.
    kicad = (
        '(pins "1" "2" "3" "4" "5" "6" "7" "8" "9" "10" "11" "12" "13" "14" "15"\n'
        '\t\t\t\t\t"16" "17" "18" "19" "20" "40" "39" "38" "37" "36" "35" "34" "33" "32"\n'
        '\t\t\t\t\t"31" "30" "29" "28" "27" "26" "25" "24" "23" "22" "21"\n'
        "\t\t\t\t)"
    )
    order = [*range(1, 21), *range(40, 20, -1)]
    pins = List.new("pins", *(sexpr.quote(str(n)) for n in order))
    assert sexpr._render(pins, 4, "") == kicad


def test_xy_runs_pack_where_kicad_packs_them():
    # Copied from pic_programmer.kicad_pcb, saved by KiCad 10.0.
    kicad = (
        "(pts\n"
        "\t\t\t\t(xy 223.52 138.43) (xy 232.41 128.905) (xy 232.41 53.975) (xy 219.71 41.91)"
        " (xy 81.28 41.91) (xy 74.295 48.895)\n"
        "\t\t\t\t(xy 74.295 127.635) (xy 86.36 138.43)\n"
        "\t\t\t)"
    )
    points = [
        ("223.52", "138.43"), ("232.41", "128.905"), ("232.41", "53.975"),
        ("219.71", "41.91"), ("81.28", "41.91"), ("74.295", "48.895"),
        ("74.295", "127.635"), ("86.36", "138.43"),
    ]  # fmt: skip
    pts = List.new("pts", *(List.new("xy", x, y) for x, y in points))
    assert sexpr._render(pts, 3, "") == kicad


def test_the_atom_threshold_is_exactly_72():
    """The samples above sit a few characters from the edge; this sits on it.

    Measured over every file KiCad 9 and 10 saved in its demos: the longest
    line that still took another atom was 71 characters, the shortest that
    wrapped was 72.
    """
    head = "(h"  # at depth 0 the line so far is 2 characters
    at_71 = List.new("h", "a" * 68, "b", "c")  # 2 + 1 + 68 = 71: 'b' still fits
    assert sexpr._render(at_71, 0, "") == f"{head} {'a' * 68} b\n\tc\n)"
    at_72 = List.new("h", "a" * 69, "b")  # 2 + 1 + 69 = 72: 'b' wraps
    assert sexpr._render(at_72, 0, "") == f"{head} {'a' * 69}\n\tb\n)"


def test_the_xy_threshold_is_exactly_99():
    """Longest line that took another xy: 98; shortest that wrapped: 99."""
    # At depth 0 an xy line starts with one tab. Four 18-character points and
    # one of 21 make 1 + 4*18 + 21 + 4 spaces = 98: the next point still fits.
    plain = ["(xy 12.345 67.891)"] * 4

    def pts(fifth: str) -> List:
        points = [*plain, fifth, "(xy 6.5 7.5)"]
        return List.new("pts", *(List.new("xy", *p[4:-1].split()) for p in points))

    fits = sexpr._render(pts("(xy 12.345678 67.891)"), 0, "")
    line = " ".join([*plain, "(xy 12.345678 67.891)", "(xy 6.5 7.5)"])
    assert fits == f"(pts\n\t{line}\n)"
    # One character more on the fifth point makes 99, and the last one wraps.
    wraps = sexpr._render(pts("(xy 12.3456789 67.891)"), 0, "")
    line = " ".join([*plain, "(xy 12.3456789 67.891)"])
    assert wraps == f"(pts\n\t{line}\n\t(xy 6.5 7.5)\n)"


@pytest.mark.parametrize(
    ("node", "depth", "expected"),
    [
        (List.new("size", "1", "1"), 3, "(size 1 1)"),
        (List(), 0, "()"),
        (List.new("net", "1", sexpr.quote("GND")), 1, '(net 1 "GND")'),
        # An atom after a sub-list stays on the line that sub-list ended on;
        # KiCad 9 writes stackup sub-layers this way.
        (
            List.new("layer", sexpr.quote("dielectric 3"), List.new("loss_tangent", "0.02"),
                     "addsublayer", List.new("thickness", "0.07")),
            3,
            '(layer "dielectric 3"\n\t\t\t\t(loss_tangent 0.02) addsublayer\n'
            "\t\t\t\t(thickness 0.07)\n\t\t\t)",
        ),
    ],
)  # fmt: skip
def test_small_layout_rules(node, depth, expected):
    assert sexpr._render(node, depth, "") == expected


# -- atoms ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "atom"),
    [
        ("GND", '"GND"'),
        ('0.020" (0.50mm)', '"0.020\\" (0.50mm)"'),
        ("\\\\cern.ch\\dfs", '"\\\\\\\\cern.ch\\\\dfs"'),
        ("Complex hierarchy\nDemo", '"Complex hierarchy\\nDemo"'),
        ("\t10uF", '"\t10uF"'),  # a tab stays a tab, as KiCad writes it
        ("100µF", '"100µF"'),
    ],
)
def test_strings_are_escaped_exactly_as_kicad_escapes_them(value, atom):
    assert sexpr.quote(value) == atom
    assert sexpr.text(atom) == value


@pytest.mark.parametrize(
    ("value", "places", "text"),
    [
        (0, 6, "0"),
        (-0.0, 6, "0"),
        (-1e-9, 6, "0"),
        (1.5, 6, "1.5"),
        (126.0475, 6, "126.0475"),
        (123.4567891, 6, "123.456789"),
        (2.54, 4, "2.54"),
        (-10.16, 4, "-10.16"),
        (90, 6, "90"),
    ],
)
def test_numbers_are_written_the_way_kicad_writes_them(value, places, text):
    assert sexpr.number(value, places) == text


@pytest.mark.parametrize("bad", ["two words", 'quo"te', "(paren", ""])
def test_user_text_cannot_become_a_bare_atom(bad):
    with pytest.raises(ValueError):
        sexpr.symbol(bad)


# -- refusals -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "(kicad_pcb (version 1)",
        "(kicad_pcb (version 1)))",
        '(kicad_pcb (title "unterminated))',
        "(kicad_pcb) (kicad_pcb)",
        "stray (kicad_pcb)",
        "",
    ],
)
@pytest.mark.parametrize("lazy", [True, False])
def test_malformed_text_is_refused(text, lazy):
    with pytest.raises(SexprError):
        list(Document.parse(text, lazy=lazy).root.walk())


def test_a_broken_item_fails_when_read_and_still_writes_back_untouched(defer_everything):
    broken = BOARD.replace("\t\t\t(size 0.8 0.95)\n", '\t\t\t(size 0.8 "0.95)\n')
    doc = Document.parse(broken)
    assert doc.dumps() == broken
    with pytest.raises(SexprError):
        list(doc.root.walk())


def test_a_list_cannot_belong_to_two_parents():
    child = List.new("at", "1", "2")
    List.new("pad", child)
    with pytest.raises(ValueError):
        List.new("via", child)


def test_a_refused_set_leaves_both_lists_as_they_were():
    owned = List.new("at", "1", "2")
    List.new("pad", owned)
    keeper = List.new("size", "1", "1")
    target = List.new("via", keeper)
    with pytest.raises(ValueError):
        target.set(1, owned)
    assert target.items[1] is keeper and keeper.parent is target


def test_a_sub_list_is_removed_by_identity_not_by_equality():
    first, second = List.new("xy", "1", "2"), List.new("xy", "1", "2")
    pts = List.new("pts", first, second)
    pts.remove(second)
    assert pts.items[1] is first and len(pts.items) == 2
    assert second.parent is None


def test_an_atom_is_removed_by_position_because_identity_cannot_name_it():
    # CPython keeps one object per one-character string: both "0"s below are
    # the same object, so removing "this one" by identity would be a guess.
    at = List.new("at", "0", "0", "90")
    assert at.items[1] is at.items[2]
    with pytest.raises(TypeError):
        at.remove(at.items[2])
    assert at.pop(2) == "0"
    assert at.items == ["at", "0", "90"]
