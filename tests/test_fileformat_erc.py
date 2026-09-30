"""This tool's electrical rules check, held to KiCad's own.

Two designs are drawn to ask KiCad's ERC one question per case, with every
rule switched on. `tests/fixtures/erc/erc.kicad_sch` asks 169: when a pin
counts as connected, which pin stands for a net nothing drives, which pair of
conflicting pins is named, when a label is dangling. `erc2.kicad_sch` and its
sheets ask 40 more, about parts of several units, the hierarchy, buses, text
variables and net classes. `erc.kicad.json` and `erc2.kicad.json` are KiCad
10's answers, violation by violation, so the comparison runs anywhere; with
KiCad installed it is asked again, and every demo project KiCad ships is
checked both ways too.

A violation matches when the rule is the same and so are its items. Where
KiCad names one of several equivalent items -- two of a junction's four
wires, one of two equally near pins -- it goes by the order of its spatial
index, which is not reproduced; there, KiCad's items must be among the ones
this tool says could stand in their place.

Two kinds of rule are left out of the comparison. The library rules, because
whether a symbol's library is found depends on the machine's library tables,
and this tool does not check them yet (`erc.NOT_CHECKED`). And the
annotation rules, because KiCad's command-line ERC does not run the
annotation check at all; they are held here to the cases drawn for them,
which are what makes KiCad's netlist export warn.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

import pytest
from kicad_demos import DEMOS, SKIP_REASON, copy_demo
from test_fileformat_circuit import demo_roots, needs_kicad, official_cli

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from kicad_cli.fileformat import erc  # noqa: E402

FIXTURES = REPO / "tests" / "fixtures" / "erc"
DESIGNS = {
    # design: (root schematic, KiCad's recorded answer, the cases drawn)
    "erc": (FIXTURES / "erc.kicad_sch", FIXTURES / "erc.kicad.json", FIXTURES / "cases.json"),
    "erc2": (FIXTURES / "erc2.kicad_sch", FIXTURES / "erc2.kicad.json", FIXTURES / "cases2.json"),
}
# What KiCad's command-line ERC reports and this is held to.
COMPARED = set(erc.CHECKED) - set(erc.ANNOTATION)

# Where this tool and KiCad still differ, by demo: the items KiCad names that
# this tool does not. On vme-wren two labels on stubs off an aliased bus,
# {WR_CLK}, are dangling to KiCad; here the bus's members reach pins on
# another sheet.
KNOWN = {
    "vme-wren": {"c6c2fa91-2ce3-4db2-8643-8fab26bab4f9", "f353caa5-d07c-4f00-8349-77869e8450d8"},
}


def compare(theirs: list[dict], ours: list[erc.Violation]) -> tuple[list, list]:
    """KiCad's violations this tool did not find, and the reverse."""
    exact = defaultdict(list)
    for index, violation in enumerate(ours):
        exact[(violation.rule, frozenset(i.uuid for i in violation.items))].append(index)
    used: set[int] = set()
    pending = []
    for v in theirs:
        pool = [i for i in exact.get((v["type"], frozenset(v["items"])), []) if i not in used]
        if pool:
            used.add(pool[0])
        else:
            pending.append(v)
    missing = []
    for v in pending:
        pool = [
            i for i, o in enumerate(ours)
            if i not in used and o.rule == v["type"] and len(o.items) == len(v["items"])
            and set(v["items"]) <= o.alternatives
        ]  # fmt: skip
        if pool:
            used.add(pool[0])
        else:
            missing.append(v)
    return missing, [o for i, o in enumerate(ours) if i not in used]


def cases(design: str) -> dict:
    return json.loads(DESIGNS[design][2].read_text(encoding="utf-8"))


def case_of(design: str, uuids) -> str:
    drawn = cases(design)["items"]
    return next((name for name, items in drawn.items() if set(uuids) & set(items)), "?")


def everything_on(design: str = "erc") -> erc.Settings:
    settings = erc.Settings.of(DESIGNS[design][0].with_suffix(".kicad_pro"))
    assert all(v != "ignore" for v in settings.severities.values())
    return settings


def checked(design: str) -> list[erc.Violation]:
    return [v for v in erc.check(DESIGNS[design][0], everything_on(design)) if v.rule in COMPARED]


@pytest.mark.parametrize("design", DESIGNS)
def test_the_fixture_is_checked_as_kicad_checks_it(design):
    recorded = json.loads(DESIGNS[design][1].read_text(encoding="utf-8"))["violations"]
    missing, extra = compare(recorded, checked(design))
    assert [(v["type"], case_of(design, v["items"])) for v in missing] == []
    assert [(v.rule, case_of(design, [i.uuid for i in v.items])) for v in extra] == []


@pytest.mark.parametrize(("design", "count"), [("erc", 169), ("erc2", 40)])
def test_the_fixture_asks_every_question_it_says_it_asks(design, count):
    """Each case drew something."""
    drawn = cases(design)
    assert len(drawn["questions"]) == len(drawn["items"]) == count
    assert all(drawn["items"].values())


def test_every_rule_kicad_has_is_either_checked_or_listed_as_not():
    assert set(erc.CHECKED) | set(erc.NOT_CHECKED) == set(erc.SEVERITIES)
    assert not set(erc.CHECKED) & set(erc.NOT_CHECKED)
    assert set(erc.ANNOTATION) <= set(erc.CHECKED)


def test_a_violation_names_at_most_two_items_as_kicads_do():
    for design, (root, _, _) in DESIGNS.items():
        assert all(1 <= len(v.items) <= 2 for v in erc.check(root, everything_on(design)))


def test_a_rule_switched_off_reports_nothing():
    settings = everything_on()
    settings.severities["pin_not_connected"] = "ignore"
    rules = {v.rule for v in erc.check(DESIGNS["erc"][0], settings)}
    assert "pin_not_connected" not in rules and "pin_to_pin" in rules


def test_annotation_errors_are_found_where_they_were_drawn():
    """KiCad's command-line ERC does not look; its editor and its netlist
    export do. Each case is one KiCad's export warns of."""
    found = Counter(
        (v.rule, case_of("erc2", [i.uuid for i in v.items]))
        for v in erc.check(DESIGNS["erc2"][0], everything_on("erc2"))
        if v.rule in erc.ANNOTATION
    )
    assert found == {
        ("unannotated", "unannotated"): 1,
        ("duplicate_reference", "duplicate_on_sheet"): 1,
        ("duplicate_reference", "duplicate_power"): 1,
        ("duplicate_reference", "twice_placed"): 1,
        ("unit_value_mismatch", "unit_values_differ"): 1,
        ("extra_units", "unit_beyond_count"): 1,
    }


def kicad_erc(schematic: Path) -> list[dict]:
    """KiCad's ERC of a design, every severity, from a copy of it."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = copy_demo(schematic.parent, Path(tmp) / "design", whole=False)
        out = Path(tmp) / "erc.json"
        for _ in range(3):  # Windows fails roughly one launch in six
            subprocess.run(
                [official_cli(), "sch", "erc", "--severity-all", "--format", "json",
                 "-o", str(out), str(copy / schematic.name)],
                capture_output=True,
                timeout=1800,
            )  # fmt: skip
            if out.exists():
                report = json.loads(out.read_text(encoding="utf-8"))
                return [
                    {"type": v["type"], "items": [i["uuid"] for i in v["items"]]}
                    for sheet in report["sheets"]
                    for v in sheet["violations"]
                ]
    raise AssertionError(f"KiCad would not check {schematic}")


@needs_kicad
@pytest.mark.parametrize("design", DESIGNS)
def test_kicad_still_says_what_was_recorded(design):
    """Rule for rule as many findings, and each one this tool's. Which of
    several equivalent items KiCad names changes from run to run -- the
    order of its spatial index is not a thing it keeps -- so the findings
    are held to this tool's, as the recorded ones are, not to the record."""
    root, answer, _ = DESIGNS[design]
    recorded = json.loads(answer.read_text(encoding="utf-8"))["violations"]
    asked = [v for v in kicad_erc(root) if v["type"] in COMPARED]
    assert Counter(v["type"] for v in asked) == Counter(v["type"] for v in recorded)
    missing, extra = compare(asked, checked(design))
    assert [(v["type"], case_of(design, v["items"])) for v in missing] == []
    assert extra == []


@needs_kicad
@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
@pytest.mark.parametrize("root", demo_roots(), ids=lambda p: p.stem)
def test_every_demo_is_checked_as_kicad_checks_it(root):
    """As the project has its rules set: a rule it switches off is off here."""
    theirs = [v for v in kicad_erc(root) if v["type"] in COMPARED]
    missing, extra = compare(theirs, [v for v in erc.check(root) if v.rule in COMPARED])
    known = KNOWN.get(root.stem, set())
    assert [v for v in missing if not set(v["items"]) <= known] == []
    assert [(v.rule, [i.description for i in v.items]) for v in extra] == []
