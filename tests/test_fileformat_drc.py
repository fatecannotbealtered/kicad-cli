"""This tool's design rules check, held to KiCad's own.

Two boards are drawn to ask KiCad's DRC one question per case.
`tests/fixtures/drc/drc1` asks about single items and their holes: how wide
a track or a via must be, when a padstack is questioned, which text is too
small or reads the wrong way, when holes crowd each other, when zones
overlap, which track ends dangle, which courtyards collide. `drc2` asks
about clearance: which clearance two items are held to, when touching is a
short, how far copper keeps from holes and from the board's edge. `drc3`
asks about rule areas, text variables, a plated pad with no hole and copper
on a layer the board has not got. `drc4` and `drc5` ask about the solder
mask: which openings bridge nets, under a margin, a minimum web and a
clearance to copper, and with all of them 0. `drclib` asks about footprint
libraries, from a library of its own: which changes to a footprint make it
unlike its library's copy, and what KiCad says of a library it cannot find.
`drcrules` asks about custom rules, from a `.kicad_dru` of its own: which
rule decides a track's width, a via, a hole or a clearance, against the
board's minimums, the net classes and a pad's own clearance, and what a
condition matches; `drcrules2` about what only a rule asks for -- items it
disallows, courtyards held apart, text sizes, segment lengths, vias counted,
the angle two segments make. `drcislands` and `drcthermal` ask about zones'
fills -- which islands are isolated copper, how many thermal spokes reach a
pad; KiCad filled `drcthermal` itself. `drcpairs` asks which nets make a
differential pair, which of their tracks are coupled, how near they run.
The `*.kicad.json` beside each is KiCad 10's answer, violation by
violation, so the comparison runs anywhere; with
KiCad installed it is asked again, and every demo board KiCad ships is
checked both ways too. The board's outline, of which a board has one, is
asked about on boards of its own (`outline/`).

A violation matches when its rule and its items are the same, with these
allowances for what KiCad does its own way:

- KiCad names a few twice, from two of its checks; this names each once, so
  answers are compared as sets.
- Copper touching copper is a clearance, a short or a crossing; which of
  the three KiCad calls a few odd touches -- a footprint's own copper over
  its pads -- follows its order of checking, so the three are compared as
  one.
- KiCad reports one clearance violation for a track, the first it finds; a
  violation of this tool's is not an extra when its track is one KiCad
  flags too.
- Of edges equally near, which one KiCad names for copper too near the
  board's edge is its own; that rule is compared by the copper.
- An opening drawn on a solder mask layer is named beside one item it lays
  bare, which KiCad picks by chance, run to run; its bridges are compared
  by the opening.
- Missing connections are compared net by net: of several equally near
  items, which one KiCad names for a connection follows an order of its
  own, which is not reproduced.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

import pytest
from kicad_demos import DEMOS, SKIP_REASON, copy_demo
from test_fileformat_circuit import needs_kicad, official_cli

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from kicad_cli import kicad_env  # noqa: E402
from kicad_cli.fileformat import drc  # noqa: E402
from kicad_cli.fileformat.board import Board  # noqa: E402
from kicad_cli.fileformat.drc import settings as drc_settings  # noqa: E402
from kicad_cli.fileformat.drc.items import uuid_of  # noqa: E402

FIXTURES = REPO / "tests" / "fixtures" / "drc"
FIXTURE = FIXTURES / "drc1"
BOARD = FIXTURE / "drc1.kicad_pcb"
RECORDED = FIXTURE / "drc1.kicad.json"
BOARDS = {
    name: FIXTURES / name / f"{name}.kicad_pcb"
    for name in (
        "drc1",
        "drc2",
        "drc3",
        "drc4",
        "drc5",
        "drclib",
        "drcrules",
        "drcrules2",
        "drcislands",
        "drcthermal",
        "drcpairs",
        "drcwords",
        "drcwords2",
    )
}
# KiCad's installation, for the libraries the tables of a demo board name;
# the fixtures name only their own, so any folder stands in without KiCad.
KICAD_ROOT = kicad_env.find_kicad_root() or str(REPO)
OUTLINES = FIXTURES / "outline"
# The rules whose findings are copper touching or too near copper: which of
# them KiCad names for a touch is compared as one.
COPPER = ("clearance", "shorting_items", "tracks_crossing")
# The rules KiCad reports once per track it tests.
ONCE_PER_TRACK = (*COPPER, "hole_clearance")
# KiCad lists at most this many violations of one kind; past it, its list is
# a sample, and this tool's must include it rather than equal it.
KICADS_LIMIT = 199


def nets_of(board: Path) -> dict[str, str]:
    """Every copper item's net, by uuid."""
    loaded = Board.load(board)
    out = {}
    for fp in loaded.footprints:
        for pad in fp.pads:
            out[uuid_of(pad.node)] = pad.net
    for item in loaded.tracks + loaded.vias + loaded.zones:
        out[uuid_of(item.node)] = item.net
    return out


def theirs(report: dict) -> tuple[dict[str, set], Counter, Counter]:
    """KiCad's answer: per rule, the item sets it names; per rule, how many
    it listed; and its missing connections."""
    found: dict[str, set] = defaultdict(set)
    listed: Counter = Counter()
    for v in report["violations"]:
        items = v["items"]
        found[v["type"]].add(frozenset(i["uuid"] if isinstance(i, dict) else i for i in items))
        listed[v["type"]] += 1
    unconnected = Counter()
    for v in report["unconnected_items"]:
        first = v["items"][0]
        unconnected[first["uuid"] if isinstance(first, dict) else first] += 1
    return found, listed, unconnected


def _as_one(found: dict[str, set], rules) -> None:
    """The rules' findings, each rule's set holding all of theirs."""
    union = set().union(*(found.get(rule, set()) for rule in rules))
    for rule in rules:
        found[rule] = union


def _by_copper(board: Path, found: set) -> set:
    """Edge clearance findings, by their copper item alone."""
    edges = {uuid_of(node) for node in Board.load(board).edge_shapes()}
    return {frozenset(u for u in key if u not in edges) or key for key in found}


def _by_opening(board: Path, found: set) -> set:
    """Solder mask bridges of a drawn opening, by the opening alone."""
    drawn = {
        uuid_of(node) for node in Board.load(board).root.walk()
        if node.head and node.head[:3] in ("gr_", "fp_") and node.find("layer") is not None
        and (node.find("layer").value(1) or "").endswith(".Mask")
    }  # fmt: skip
    return {frozenset(key & drawn) or key for key in found}


def differences(board: Path, report: dict) -> list[str]:
    """Where this tool and KiCad disagree on a board, rule by rule."""
    ours = drc.check(board, kicad_root=KICAD_ROOT)
    found, listed, unconnected = theirs(report)
    mine: dict[str, set] = defaultdict(set)
    tracks = set()
    for v in ours.violations:
        mine[v.rule].add(v.uuids())
        tracks |= {i.uuid for i in v.items if i.kind == "track"}
    checked = [r for r in COPPER if r not in ours.not_checked]
    _as_one(found, checked)
    _as_one(mine, checked)
    flagged = set().union(*(found.get(rule, set()) for rule in ONCE_PER_TRACK))
    flagged = set().union(*flagged) if flagged else set()
    edge = "copper_edge_clearance"
    if edge not in ours.not_checked:
        found[edge] = _by_copper(board, found.get(edge, set()))
        mine[edge] = _by_copper(board, mine.get(edge, set()))
    mask = "solder_mask_bridge"
    if mask not in ours.not_checked:
        found[mask] = _by_opening(board, found.get(mask, set()))
        mine[mask] = _by_opening(board, mine.get(mask, set()))
    out = []
    for rule in drc.CHECKED:
        if rule == "unconnected_items" or rule in ours.not_checked:
            continue
        missing = found.get(rule, set()) - mine.get(rule, set())
        # KiCad names no item for a footprint field of its own making ("MPN"):
        # such a finding is matched by a finding of ours on a field.
        blank = sum(1 for m in missing if not m)
        missing = {m for m in missing if m}
        extra = mine.get(rule, set()) - found.get(rule, set())
        # A family compared as one is a sample when any of its lists is.
        family = checked if rule in checked else [rule]
        if any(listed[r] >= KICADS_LIMIT for r in family):
            extra = set()
        if rule in ONCE_PER_TRACK:
            extra = {key for key in extra if not key & tracks & flagged}
        fields = [
            v for v in ours.violations
            if v.rule == rule and v.uuids() in extra and v.items and v.items[0].kind == "field"
        ]  # fmt: skip
        for v in fields[:blank]:
            extra.discard(v.uuids())
        out += [f"{rule}: KiCad names {sorted(m)}" for m in missing]
        out += [f"{rule}: this names {sorted(e)}" for e in extra]
    nets = nets_of(board)
    theirs_by_net = Counter()
    for uuid, n in unconnected.items():
        theirs_by_net[nets.get(uuid, uuid)] += n
    ours_by_net = Counter(nets.get(v.items[0].uuid, "?") for v in ours.unconnected)
    if theirs_by_net != ours_by_net:
        out.append(f"unconnected_items: KiCad {dict(theirs_by_net)}, this {dict(ours_by_net)}")
    return out


def recorded(name: str) -> dict:
    return json.loads(BOARDS[name].with_suffix(".kicad.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", BOARDS)
def test_the_fixture_is_checked_as_kicad_checks_it(name):
    assert differences(BOARDS[name], recorded(name)) == []


def test_the_fixtures_ask_every_question_they_say_they_ask():
    rules = Counter(v["type"] for name in BOARDS for v in recorded(name)["violations"])
    for rule in drc.CHECKED:
        if rule not in ("unconnected_items", "invalid_outline"):  # the outline: `outline/`
            assert rules[rule], f"no fixture asks about {rule}"
    assert recorded("drc1")["unconnected_items"]


def _messages(name: str) -> dict[frozenset, str]:
    return {v.uuids(): v.message for v in drc.check(BOARDS[name]).violations}


@pytest.mark.parametrize("name", ["drcwords", "drcwords2"])
def test_a_violation_is_worded_as_kicad_words_it(name):
    """KiCad's own words, recorded (`<name>.words.json`): which class a
    clearance names, or none; net names as items write them and as a short's
    message does; lengths too small for four places; the order of the items
    -- but a solder mask opening's, which KiCad picks its own way."""
    said = json.loads((FIXTURES / name / f"{name}.words.json").read_text(encoding="utf-8"))
    ours = {(v.rule, v.uuids()): v for v in drc.check(BOARDS[name]).violations}
    for entry in said:
        v = ours[(entry["type"], frozenset(i["uuid"] for i in entry["items"]))]
        assert v.message == entry["description"]
        words = [(i.uuid, i.description) for i in v.items]
        theirs = [(i["uuid"], i["description"]) for i in entry["items"]]
        if entry["type"] == "solder_mask_bridge":
            words, theirs = sorted(words), sorted(theirs)
        assert words == theirs
    assert len(ours) == len(said)


def test_a_clearance_names_what_set_it_as_kicad_names_it():
    """drc2: the net classes' larger, the board's minimum, a pad's or a
    footprint's own even when smaller, a zone's."""
    messages = list(_messages("drc2").values())
    for expected in (
        "Clearance violation (netclass 'Default' clearance 0.2000 mm; actual 0.1500 mm)",
        "Clearance violation (netclass 'Wide' clearance 0.4000 mm; actual 0.3000 mm)",
        "Clearance violation (board minimum clearance 0.1200 mm; actual 0.1100 mm)",
        "Clearance violation (pad clearance 0.5000 mm; actual 0.3000 mm)",
        "Clearance violation (footprint PA4 clearance 0.5000 mm; actual 0.3000 mm)",
        "Clearance violation (zone clearance 0.5000 mm; actual 0.3000 mm)",
        "Clearance violation (netclass 'Default' clearance 0.2000 mm; actual 0.1994 mm)",
    ):
        assert expected in messages
    assert not any("actual 0.1995 mm" in m for m in messages)  # within KiCad's 0.5 um


def _outline(name: str) -> list[str]:
    """What this tool says of an outline board: the reasons it gives."""
    report = drc.check(OUTLINES / name / f"{name}.kicad_pcb")
    return sorted(
        v.message.split("(", 1)[1].rstrip(")") for v in report.violations
        if v.rule == "invalid_outline"
    )  # fmt: skip


@pytest.mark.parametrize(
    "name", sorted(json.loads((OUTLINES / "cases.json").read_text(encoding="utf-8")))
)
def test_an_outline_is_judged_as_kicad_judges_it(name):
    said = json.loads((OUTLINES / "cases.json").read_text(encoding="utf-8"))[name]
    assert _outline(name) == ([said] if said else [])


def test_a_drc_marker_in_a_text_is_raised_as_its_own_violation():
    """drc3: KiCad resolves ${DRC_ERROR} to nothing and raises a DRC error
    where it stands -- a marker a person leaves on the board."""
    found = [v for v in drc.check(BOARDS["drc3"]).violations if v.rule.startswith("generic_")]
    assert sorted((v.message, v.severity) for v in found) == [
        ("Error", "error"), ("check this", "warning"),
    ]  # fmt: skip


def test_a_solder_mask_bridge_names_its_side():
    messages = {v.message for v in drc.check(BOARDS["drc4"]).violations}
    assert messages == {
        "Front solder mask aperture bridges items with different nets",
        "Rear solder mask aperture bridges items with different nets",
    }


def test_a_track_from_a_pad_of_another_net_is_named_with_that_net():
    """drc4: KiCad's connectivity gives a track the net of the one pad it
    comes from, and names it so; a track merely touching a pad keeps its
    own."""
    names = {i.description for v in drc.check(BOARDS["drc4"]).violations for i in v.items}
    assert "Track [C10d] on F.Cu, length 4.0000 mm" in names
    assert "Track [C11c] on F.Cu, length 1.4000 mm" in names
    assert not any("[C10c]" in name for name in names)


def test_a_library_it_cannot_use_is_named_as_kicad_names_it():
    """drclib: a library no table names, one switched off, one whose folder
    is gone -- KiCad calls that one switched off too -- and one without the
    footprint."""
    report = drc.check(BOARDS["drclib"], kicad_root=KICAD_ROOT)
    said = sorted(v.message for v in report.violations if v.rule == "lib_footprint_issues")
    assert said == [
        "Footprint 'Missing' not found in library 'drclib'",
        "The current configuration does not include the footprint library 'drcnowhere'",
        "The footprint library 'drcgone' is not enabled in the current configuration",
        "The footprint library 'drcoff' is not enabled in the current configuration",
    ]


def test_without_kicad_the_libraries_are_listed_as_not_checked():
    report = drc.check(BOARDS["drclib"])
    assert not [v for v in report.violations if v.rule.startswith("lib_footprint")]
    assert {"lib_footprint_issues", "lib_footprint_mismatch"} <= set(report.not_checked)


def test_a_custom_rule_is_named_in_its_message():
    """drcrules: the rule that set the limit, as KiCad names it."""
    messages = set(_messages("drcrules").values())
    for expected in (
        "Track width (rule 'wide_min' min width 0.3000 mm; actual 0.2500 mm)",
        "Track width (rule 'wide_max' max width 0.4000 mm; actual 0.5000 mm)",
        "Track width (board setup constraints min width 0.2000 mm; actual 0.1000 mm)",
        "Clearance violation (rule 'z_zone' clearance 1.0000 mm; actual 0.5000 mm)",
        "Via diameter (rule 'via_b' max diameter 0.8000 mm; actual 0.9000 mm)",
        "Annular width (rule 'via_c' max annular width 0.1500 mm; actual 0.2000 mm)",
        "Hole size out of range (rule 'pad_hole' min hole 0.6000 mm; actual 0.5000 mm)",
        "Hole clearance violation (rule 'npth_far' clearance 0.5000 mm; actual 0.3000 mm)",
        "Drilled hole too close to other hole (rule 'holes_far' min 1.0000 mm; actual 0.9000 mm)",
        "Board edge clearance violation (rule 'edge_far' clearance 1.0000 mm; actual 0.7000 mm)",
    ):
        assert expected in messages


@pytest.mark.parametrize(
    "rule",
    [
        '(rule "bad" (constraint track_width (min 1furlong)))',  # a unit KiCad does not read
        '(rule "bad" (constraint track_width (min 0.5)))',  # a length without its unit
        '(rule "bad" (constraint nonsense (min 1mm)))',  # a kind of constraint it does not know
        '(rule "bad" (constraint track_angle (min 90deg)))',  # an angle is a plain number
        '(rule "bad" (condition "A.NetName ==") (constraint track_width (min 1mm)))',
    ],
)
def test_a_rules_file_kicad_does_not_read_is_ignored_whole(tmp_path, rule):
    """KiCad reads a rules file whole or not at all: one rule it cannot read,
    and no rule in the file counts -- here the rule of drcrules2 that holds a
    segment to 0.5 mm, beside it."""
    for f in BOARDS["drcrules2"].parent.glob("drcrules2.kicad_p*"):
        shutil.copy(f, tmp_path)
    good = '(rule "seg_len" (condition "A.NetName == \'SL\'")' + (
        " (constraint track_segment_length (min 0.5mm)))"
    )
    (tmp_path / "drcrules2.kicad_dru").write_bytes(f"(version 1)\n{rule}\n{good}\n".encode())
    report = drc.check(tmp_path / "drcrules2.kicad_pcb")
    assert not [v for v in report.violations if v.rule == "track_segment_length"]
    assert report.custom_rules["read"] is False
    assert "ignores every rule" in report.custom_rules["why"]


def test_a_custom_rule_gives_its_own_severity():
    found = {
        v.items[0].description.split(" ")[1]: v.severity
        for v in drc.check(BOARDS["drcrules"]).violations if v.rule == "track_width"
    }  # fmt: skip
    assert found["[SV]"] == "warning" and "[SI]" not in found


def test_a_rule_this_cannot_read_leaves_its_checks_unmade(tmp_path):
    """A condition asking what this does not know decides nothing here: the
    checks it would decide are said not to be made."""
    for f in BOARDS["drcrules"].parent.glob("drcrules.kicad_p*"):
        shutil.copy(f, tmp_path)
    (tmp_path / "drcrules.kicad_dru").write_bytes(
        b'(version 1)\n(rule "odd" (condition "A.Pad_Count > 2")'
        b" (constraint track_width (min 0.3mm)))\n"
    )
    report = drc.check(tmp_path / "drcrules.kicad_pcb")
    assert "track_width" in report.not_checked
    assert not [v for v in report.violations if v.rule == "track_width"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("A.NetName == 'VCC'", True),
        ("A.NetName == 'vcc'", True),  # letter case aside
        ("A.NetName == 'V*'", True),
        ("A.NetName != '*CC'", False),
        ("A.NetName == 'GND' || A.Width > 0.1mm", True),
        ("A.NetName == 'GND' || A.Width > 0.3mm", False),
        ("!(A.NetName == 'GND') && A.Width >= 7mil", True),  # 7 mil is 0.1778 mm
        ("A.Width == 0.2mm", True),
        ("A.NetClass == 'Power'", True),
    ],
)
def test_an_expression_is_evaluated_as_kicad_evaluates_it(text, expected):
    from kicad_cli.fileformat.drc import expression  # noqa: PLC0415

    class Item:
        def get(self, name):
            return {"NetName": "VCC", "Width": 200_000, "NetClass": ["Power"]}[name]

        def call(self, name, args):
            raise expression.Unknown(name)

    item = Item()
    value = expression.evaluate(expression.parse(text), lambda name: item if name == "A" else None)
    assert bool(value) is expected


def test_the_two_nets_of_a_differential_pair_are_held_to_its_gap():
    from kicad_cli.fileformat.drc.clearance import coupled  # noqa: PLC0415

    assert coupled("/PCIE.TX_P", "/PCIE.TX_N") and coupled("usb_d+", "usb_d-")
    assert not coupled("/PCIE.TX_P", "/PCIE.RX_N") and not coupled("CLK", "CLKN")
    assert not coupled("/usb_dp", "/usb_dn")  # lower case is not a pair


def test_a_rule_answers_each_item_made_afresh_for_itself(tmp_path):
    """An item made for one question and dropped is not taken for the next
    one made in its place: on vme-wren, 2608 tracks once took the rule of a
    pair they are not in."""
    from kicad_cli.fileformat.drc import rules as rules_module  # noqa: PLC0415
    from kicad_cli.fileformat.drc import subjects  # noqa: PLC0415

    (tmp_path / "p.kicad_dru").write_bytes(
        b'(version 1)\n(rule "pair" (condition "A.NetName == \'D_P\'")\n'
        b"  (constraint track_width (max 0.1mm)))\n"
    )
    rules = rules_module.load(tmp_path / "p.kicad_pro")
    for k in range(40):
        net = "D_P" if k % 2 == 0 else "OTHER"
        item = subjects.Subject(None, "segment", net, frozenset({"F.Cu"}), width=200_000)
        found = rules.find("track_width", item, layer="F.Cu", bound="max")
        assert (found is not None) == (net == "D_P")
        del item  # its place free for the next


def test_a_round_oval_pad_is_ringed_all_round():
    """interf_u's U5 pad 16, an oval as long as it is wide: its spokes are
    counted all round it, not along one half twice."""
    from kicad_cli.fileformat.drc import fills  # noqa: PLC0415
    from kicad_cli.fileformat.drc.shapes import Shape  # noqa: PLC0415

    ring = fills._ring([Shape(((0, 0), (0, 0)), 800_000)], 254_000)
    assert ring is not None
    assert min(y for _, y in ring) < -1_000_000 < 1_000_000 < max(y for _, y in ring)
    assert min(x for x, _ in ring) < -1_000_000 < 1_000_000 < max(x for x, _ in ring)


def test_every_rule_kicad_has_is_either_checked_or_listed_as_not():
    known = set(drc.CHECKED) | set(drc.NOT_CHECKED)
    assert set(drc_settings.SEVERITIES) - known == set()
    assert set(drc.CHECKED) & set(drc.NOT_CHECKED) == set()


def test_a_violation_names_at_most_two_items_as_kicads_do():
    report = drc.check(BOARD)
    assert all(1 <= len(v.items) <= 2 for v in report.violations + report.unconnected)


def test_a_rule_switched_off_reports_nothing_and_is_listed_as_off():
    settings = drc.Settings.of(BOARD.with_suffix(".kicad_pro"))
    settings.severities["track_width"] = "ignore"
    report = drc.check(BOARD, settings)
    assert not [v for v in report.violations if v.rule == "track_width"]
    assert "track_width" in report.ignored
    assert "track_width" not in report.not_checked


def test_a_violation_takes_the_severity_the_project_gives_its_rule():
    settings = drc.Settings.of(BOARD.with_suffix(".kicad_pro"))
    settings.severities["hole_to_hole"] = "error"
    report = drc.check(BOARD, settings)
    assert {v.severity for v in report.violations if v.rule == "hole_to_hole"} == {"error"}


def test_an_excluded_violation_is_still_reported_marked_and_with_its_comment():
    settings = drc.Settings.of(BOARD.with_suffix(".kicad_pro"))
    first = next(v for v in drc.check(BOARD, settings).violations if v.rule == "track_width")
    settings.exclusions[("track_width", first.uuids())] = "checked by hand"
    again = next(
        v for v in drc.check(BOARD, settings).violations
        if v.rule == "track_width" and v.uuids() == first.uuids()
    )  # fmt: skip
    assert again.excluded and again.comment == "checked by hand"
    assert again.to_dict()["excluded"] is True


def test_exclusions_are_read_as_kicad_writes_them(tmp_path):
    project = json.loads(BOARD.with_suffix(".kicad_pro").read_text(encoding="utf-8"))
    project["board"]["design_settings"]["drc_exclusions"] = [
        # KiCad 9 and later: [what, comment]; the second item may be null.
        ["courtyards_overlap|100221000|45955000|aaaa|bbbb", "Intentional overlap"],
        ["via_dangling|1|2|cccc|00000000-0000-0000-0000-000000000000", ""],
        # Before KiCad 9: the text alone.
        "track_dangling|3|4|dddd|00000000-0000-0000-0000-000000000000",
    ]
    path = tmp_path / "x.kicad_pro"
    path.write_bytes(json.dumps(project).encode())
    settings = drc.Settings.of(path)
    assert settings.exclusions == {
        ("courtyards_overlap", frozenset({"aaaa", "bbbb"})): "Intentional overlap",
        ("via_dangling", frozenset({"cccc"})): "",
        ("track_dangling", frozenset({"dddd"})): "",
    }


def test_what_a_project_leaves_out_is_a_new_projects(tmp_path):
    (tmp_path / "x.kicad_pro").write_bytes(b"{}")
    settings = drc.Settings.of(tmp_path / "x.kicad_pro")
    assert settings.severities == drc_settings.SEVERITIES
    assert settings.nm("min_track_width") == 200_000
    assert drc.Settings.of(None).nm("min_hole_to_hole") == 250_000


def test_checks_the_projects_custom_rules_decide_are_held_back_and_said_to_be(tmp_path):
    """A kind of constraint not read yet -- a physical clearance -- holds
    its check back; one that is read -- a hole's size -- does not."""
    for f in FIXTURE.glob("drc1.kicad_p*"):
        shutil.copy(f, tmp_path)
    (tmp_path / "drc1.kicad_dru").write_bytes(
        b"(version 1)\n# a comment (with brackets\n"
        b'(rule "under the FPGA"\n\t(constraint physical_clearance (min 0.2mm))\n'
        b"\t(constraint hole_size (min 0.2mm))\n"
        b"\t(condition \"A.intersectsArea('FPGA')\"))\n"
    )
    report = drc.check(tmp_path / "drc1.kicad_pcb")
    rules = {v.rule for v in report.violations}
    assert "'under the FPGA'" in report.not_checked["clearance"]
    assert "drill_out_of_range" in rules and "drill_out_of_range" not in report.not_checked
    assert "track_width" in rules  # what the rules do not touch is still checked


def test_net_classes_are_resolved_as_kicad_resolves_them():
    classes = drc_settings.NetClasses(
        {
            "classes": [
                {"name": "Default", "clearance": 0.2, "track_width": 0.25, "priority": 2147483647},
                {"name": "Power", "clearance": 0.3, "priority": 1},
                {"name": "Wide", "clearance": 0.5, "track_width": 1.0, "priority": 0},
                {"name": "Colour", "clearance": None, "priority": 3},
            ],
            "netclass_patterns": [
                {"pattern": "+*V*", "netclass": "Power"},
                {"pattern": "+5V", "netclass": "Wide"},
                {"pattern": "uio\\d+", "netclass": "Colour"},
            ],
            "netclass_assignments": {"GND": ["Power"]},
        }
    )
    assert classes.of("SIG").name == "Default"
    assert classes.of("+3V3").name == "Power,Default"  # Power leaves track width to Default
    assert classes.of("+3V3").nm("clearance") == 300_000
    assert classes.of("+3V3").nm("track_width") == 250_000
    assert classes.of("+5V").name == "Wide,Power,Default"  # the lower number first
    assert classes.of("+5V").nm("clearance") == 500_000
    assert classes.of("uio7").name == "Colour,Default"  # a regular expression
    assert classes.of("uio7").nm("clearance") == 200_000
    assert classes.of("GND").name == "Power,Default"  # assigned by name


# -- KiCad, asked again -----------------------------------------------------------------


def kicad_drc(board: Path) -> dict:
    """KiCad's own DRC of a board, run on a copy of its project."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "board"
        if DEMOS.exists() and DEMOS.resolve() in board.resolve().parents:
            copy_demo(board.parent, folder, whole=False)
        else:
            # The fixture's folder whole: a project's own library table and
            # libraries are beside it.
            shutil.copytree(board.parent, folder)
        out = Path(tmp) / "report.json"
        for _ in range(3):
            subprocess.run(
                [official_cli(), "pcb", "drc", "--severity-all", "--format", "json",
                 "-o", str(out), str(folder / board.name)],
                capture_output=True, timeout=3600, check=False,
            )  # fmt: skip
            if out.exists():
                return json.loads(out.read_text(encoding="utf-8"))
    raise AssertionError(f"KiCad would not check {board}")


@needs_kicad
@pytest.mark.parametrize(
    "name", sorted(json.loads((OUTLINES / "cases.json").read_text(encoding="utf-8")))
)
def test_kicad_still_judges_each_outline_as_recorded(name):
    """KiCad's words are in its own language here; that it finds a fault,
    and how many kinds, is what is asked again."""
    said = json.loads((OUTLINES / "cases.json").read_text(encoding="utf-8"))[name]
    asked = kicad_drc(OUTLINES / name / f"{name}.kicad_pcb")
    faults = [v for v in asked["violations"] if v["type"] == "invalid_outline"]
    assert bool(faults) == bool(said)


@needs_kicad
@pytest.mark.parametrize("name", BOARDS)
def test_kicad_still_says_what_was_recorded(name):
    asked = kicad_drc(BOARDS[name])
    rules = set(drc.CHECKED)
    assert {v["type"] for v in asked["violations"] if v["type"] in rules} == {
        v["type"] for v in recorded(name)["violations"] if v["type"] in rules
    }
    assert differences(BOARDS[name], asked) == []


@needs_kicad
@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
@pytest.mark.parametrize(
    "board", sorted(DEMOS.rglob("*.kicad_pcb")) if DEMOS.exists() else [], ids=lambda p: p.stem
)
def test_every_demo_board_is_checked_as_kicad_checks_it(board):
    """As the project has its rules set: a rule it switches off is off here,
    and a rule its custom rules decide is not made."""
    assert differences(board, kicad_drc(board)) == []
