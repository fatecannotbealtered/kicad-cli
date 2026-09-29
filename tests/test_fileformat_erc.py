"""This tool's electrical rules check, held to KiCad's own.

`tests/fixtures/erc/erc.kicad_sch` is a design drawn to ask KiCad's ERC one
question per case -- 169 of them: when a pin counts as connected, which pin
stands for a net nothing drives, which pair of conflicting pins is named,
when a label is dangling -- with every rule switched on.
`erc.kicad.json` is KiCad 10's answer, violation by violation, so the
comparison runs anywhere; with KiCad installed it is asked again, and every
demo project KiCad ships is checked both ways too.

A violation matches when the rule is the same and so are its items. Where
KiCad names one of several equivalent items -- two of a junction's four
wires, one of two equally near pins -- it goes by the order of its spatial
index, which is not reproduced; there, KiCad's items must be among the ones
this tool says could stand in their place.

The library rules are not compared: whether a symbol's library is found
depends on the machine's library tables, and this tool does not check them
yet (`erc.NOT_CHECKED`).
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

FIXTURE = REPO / "tests" / "fixtures" / "erc" / "erc.kicad_sch"
RECORDED = FIXTURE.with_name("erc.kicad.json")
CASES = json.loads(FIXTURE.with_name("cases.json").read_text(encoding="utf-8"))

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


def case_of(uuids) -> str:
    return next((name for name, items in CASES["items"].items() if set(uuids) & set(items)), "?")


def everything_on() -> erc.Settings:
    settings = erc.Settings.of(FIXTURE.with_suffix(".kicad_pro"))
    assert all(v != "ignore" for v in settings.severities.values())
    return settings


def test_the_fixture_is_checked_as_kicad_checks_it():
    recorded = json.loads(RECORDED.read_text(encoding="utf-8"))["violations"]
    missing, extra = compare(recorded, erc.check(FIXTURE, everything_on()))
    assert [(v["type"], case_of(v["items"])) for v in missing] == []
    assert [(v.rule, case_of([i.uuid for i in v.items])) for v in extra] == []


def test_the_fixture_asks_every_question_it_says_it_asks():
    """Each case drew something, and the design reads as a design."""
    assert len(CASES["questions"]) == len(CASES["items"]) == 169
    assert all(CASES["items"].values())


def test_every_rule_kicad_has_is_either_checked_or_listed_as_not():
    assert set(erc.CHECKED) | set(erc.NOT_CHECKED) == set(erc.SEVERITIES)
    assert not set(erc.CHECKED) & set(erc.NOT_CHECKED)


def test_a_violation_names_at_most_two_items_as_kicads_do():
    assert all(1 <= len(v.items) <= 2 for v in erc.check(FIXTURE, everything_on()))


def test_a_rule_switched_off_reports_nothing():
    settings = everything_on()
    settings.severities["pin_not_connected"] = "ignore"
    rules = {v.rule for v in erc.check(FIXTURE, settings)}
    assert "pin_not_connected" not in rules and "pin_to_pin" in rules


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
def test_kicad_still_says_what_was_recorded():
    """Rule for rule as many findings, and each one this tool's. Which of
    several equivalent items KiCad names changes from run to run -- the
    order of its spatial index is not a thing it keeps -- so the findings
    are held to this tool's, as the recorded ones are, not to the record."""
    recorded = json.loads(RECORDED.read_text(encoding="utf-8"))["violations"]
    asked = [v for v in kicad_erc(FIXTURE) if v["type"] != "lib_symbol_issues"]
    assert Counter(v["type"] for v in asked) == Counter(v["type"] for v in recorded)
    missing, extra = compare(asked, erc.check(FIXTURE, everything_on()))
    assert [(v["type"], case_of(v["items"])) for v in missing] == []
    assert extra == []


@needs_kicad
@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
@pytest.mark.parametrize("root", demo_roots(), ids=lambda p: p.stem)
def test_every_demo_is_checked_as_kicad_checks_it(root):
    """As the project has its rules set: a rule it switches off is off here."""
    theirs = [v for v in kicad_erc(root) if v["type"] in erc.CHECKED]
    missing, extra = compare(theirs, erc.check(root))
    known = KNOWN.get(root.stem, set())
    assert [v for v in missing if not set(v["items"]) <= known] == []
    assert [(v.rule, [i.description for i in v.items]) for v in extra] == []
