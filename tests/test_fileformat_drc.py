"""This tool's design rules check, held to KiCad's own.

`tests/fixtures/drc/drc1` is a board drawn to ask KiCad's DRC one question
per case: how wide a track or a via must be, when a padstack is questioned,
which text is too small or reads the wrong way, when holes crowd each other,
when zones overlap, which track ends dangle, which courtyards collide.
`drc1.kicad.json` is KiCad 10's answer, violation by violation, so the
comparison runs anywhere; with KiCad installed it is asked again, and every
demo board KiCad ships is checked both ways too.

A violation matches when its rule and its items are the same. KiCad names a
few twice, from two of its checks; this names each once, so answers are
compared as sets. Missing connections are compared net by net: of several
equally near items, which one KiCad names for a connection follows an order
of its own, which is not reproduced.
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

from kicad_cli.fileformat import drc  # noqa: E402
from kicad_cli.fileformat.board import Board  # noqa: E402
from kicad_cli.fileformat.drc import settings as drc_settings  # noqa: E402
from kicad_cli.fileformat.drc.items import uuid_of  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "drc" / "drc1"
BOARD = FIXTURE / "drc1.kicad_pcb"
RECORDED = FIXTURE / "drc1.kicad.json"
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


def differences(board: Path, report: dict) -> list[str]:
    """Where this tool and KiCad disagree on a board, rule by rule."""
    ours = drc.check(board)
    found, listed, unconnected = theirs(report)
    mine: dict[str, set] = defaultdict(set)
    for v in ours.violations:
        mine[v.rule].add(v.uuids())
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
        if listed[rule] >= KICADS_LIMIT:
            extra = set()
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


def test_the_fixture_is_checked_as_kicad_checks_it():
    recorded = json.loads(RECORDED.read_text(encoding="utf-8"))
    assert differences(BOARD, recorded) == []


def test_the_fixture_asks_every_question_it_says_it_asks():
    recorded = json.loads(RECORDED.read_text(encoding="utf-8"))
    rules = Counter(v["type"] for v in recorded["violations"])
    for rule in drc.CHECKED:
        if rule != "unconnected_items":
            assert rules[rule], f"the fixture asks nothing about {rule}"
    assert recorded["unconnected_items"]


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
    for f in FIXTURE.glob("drc1.kicad_p*"):
        shutil.copy(f, tmp_path)
    (tmp_path / "drc1.kicad_dru").write_bytes(
        b"(version 1)\n# a comment (with brackets\n"
        b'(rule "under the FPGA"\n\t(constraint hole_size (min 0.2mm))\n'
        b"\t(condition \"A.intersectsArea('FPGA')\"))\n"
    )
    report = drc.check(tmp_path / "drc1.kicad_pcb")
    rules = {v.rule for v in report.violations}
    assert "drill_out_of_range" not in rules and "microvia_drill_out_of_range" not in rules
    assert "under the FPGA" in report.not_checked["drill_out_of_range"]
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
            folder.mkdir()
            for f in board.parent.glob(f"{board.stem}.kicad_*"):
                shutil.copy(f, folder)
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
def test_kicad_still_says_what_was_recorded():
    recorded = json.loads(RECORDED.read_text(encoding="utf-8"))
    asked = kicad_drc(BOARD)
    rules = set(drc.CHECKED)
    assert {v["type"] for v in asked["violations"] if v["type"] in rules} == {
        v["type"] for v in recorded["violations"] if v["type"] in rules
    }
    assert differences(BOARD, asked) == []


@needs_kicad
@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
@pytest.mark.parametrize(
    "board", sorted(DEMOS.rglob("*.kicad_pcb")) if DEMOS.exists() else [], ids=lambda p: p.stem
)
def test_every_demo_board_is_checked_as_kicad_checks_it(board):
    """As the project has its rules set: a rule it switches off is off here,
    and a rule its custom rules decide is not made."""
    assert differences(board, kicad_drc(board)) == []
