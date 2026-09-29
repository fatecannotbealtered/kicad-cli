"""Which pins a schematic joins, and what each net is called, held to KiCad.

`kicad_cli/fileformat/circuit.py` works a design's nets out of its files,
and KiCad's own netlist is the reference: the same nets, the same pins in
each, the same names, in the same order.

Two kinds of evidence:

- `tests/fixtures/schematic/` is a design drawn for the purpose -- its own
  symbols, nothing from KiCad's libraries -- where each net asks one
  question: does a wire ending on another wire's middle join it (only with a
  junction), does a pin in a wire's middle (no), a label in one (yes); how is
  a net named after a pin with no name, one whose name is its number, one
  whose name the part uses twice, one on the second unit of a part. KiCad's
  answers are recorded below as KICAD_SAID, so the rules are checked on every
  run, with or without KiCad; with KiCad, it is asked again.
- Every project KiCad ships as a demo -- 35 of them, from two resistors to
  the Jetson carrier board's 1,355 nets across buses, bus aliases and nested
  sheets -- compared net by net whenever KiCad is installed.
"""

from __future__ import annotations

import functools
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from kicad_demos import DEMOS, SKIP_REASON, copy_demo

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from kicad_cli import kicad_env, sexpr  # noqa: E402
from kicad_cli.fileformat.circuit import Design, bus_members, connect, natural_key  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "schematic" / "rules.kicad_sch"

# KiCad 10.0's netlist of the fixture: every net, in KiCad's order.
KICAD_SAID = [
    ("/ALONG", ["R12.2"]),
    ("/CROSS_V", ["R1.2"]),
    ("/HIDDEN_A", ["U3.1"]),
    ("/IO_A", ["R101.1"]),
    ("/JUNCTION_H", ["R3.2", "R4.2"]),
    ("/ORIENT1_P1", ["Q1.1"]),
    ("/ORIENT1_P2", ["Q1.2"]),
    ("/ORIENT1_P3", ["Q1.3"]),
    ("/ORIENT2_P1", ["Q2.1"]),
    ("/ORIENT2_P2", ["Q2.2"]),
    ("/ORIENT2_P3", ["Q2.3"]),
    ("/ORIENT3_P1", ["Q3.1"]),
    ("/ORIENT3_P2", ["Q3.2"]),
    ("/ORIENT3_P3", ["Q3.3"]),
    ("/ORIENT4_P1", ["Q4.1"]),
    ("/ORIENT4_P2", ["Q4.2"]),
    ("/ORIENT4_P3", ["Q4.3"]),
    ("/ORIENT5_P1", ["Q5.1"]),
    ("/ORIENT5_P2", ["Q5.2"]),
    ("/ORIENT5_P3", ["Q5.3"]),
    ("/ORIENT6_P1", ["Q6.1"]),
    ("/ORIENT6_P2", ["Q6.2"]),
    ("/ORIENT6_P3", ["Q6.3"]),
    ("/ORIENT7_P1", ["Q7.1"]),
    ("/ORIENT7_P2", ["Q7.2"]),
    ("/ORIENT7_P3", ["Q7.3"]),
    ("/ORIENT8_P1", ["Q8.1"]),
    ("/ORIENT8_P2", ["Q8.2"]),
    ("/ORIENT8_P3", ["Q8.3"]),
    ("/PIN_JUNCTION", ["R13.2"]),
    ("/R1_TOP", ["R1.1"]),
    ("/R2_TOP", ["R2.1"]),
    ("/R3_TOP", ["R3.1"]),
    ("/R4_TOP", ["R4.1"]),
    ("/R5_TOP", ["R5.1"]),
    ("/R7_BOTTOM", ["R7.2"]),
    ("/R12_TOP", ["R12.1"]),
    ("/R13_TOP", ["R13.1"]),
    ("/U1B_INV", ["U1.6"]),
    ("/amp_a/LOCAL", ["R101.2"]),
    ("/amp_b/LOCAL", ["R201.2"]),
    ("IO_B", ["R201.1"]),
    ("Net-(J1-Pad1)", ["J1.1", "R9.1"]),
    ("Net-(R10-~-Pad2)", ["R10.2", "U4.1"]),
    ("Net-(R40-~-Pad1)", ["R40.1", "R40.2"]),
    ("Net-(U1A-OUT)", ["R11.1", "U1.1"]),
    ("Net-(U2A-OUT)", ["U2.1", "U2.6"]),
    ("Net-(U5-GND-Pad1)", ["U5.1", "U5.2"]),
    ("SHARED", ["R102.1", "R202.1", "R7.1"]),
    ("VBUS", ["R102.2", "R202.2", "R6.1", "R6.2"]),
    ("VDDX", ["R8.1", "U3.2"]),
    ("unconnected-(J1-Pad2)", ["J1.2"]),
    ("unconnected-(J1-Pad3)", ["J1.3"]),
    ("unconnected-(R2-~-Pad2)", ["R2.2"]),
    ("unconnected-(R5-~-Pad2)", ["R5.2"]),
    ("unconnected-(R8-~-Pad2)", ["R8.2"]),
    ("unconnected-(R9-~-Pad2)", ["R9.2"]),
    ("unconnected-(R10-~-Pad1)", ["R10.1"]),
    ("unconnected-(R11-~-Pad2)", ["R11.2"]),
    ("unconnected-(R30-~-Pad1)", ["R30.1"]),
    ("unconnected-(R30-~-Pad2)", ["R30.2"]),
    ("unconnected-(R31-~-Pad1)", ["R31.1"]),
    ("unconnected-(R31-~-Pad2)", ["R31.2"]),
    ("unconnected-(R32-~-Pad1)", ["R32.1"]),
    ("unconnected-(R32-~-Pad2)", ["R32.2"]),
    ("unconnected-(U1A-IN+-Pad3)", ["U1.3"]),
    ("unconnected-(U1A-IN--Pad2)", ["U1.2"]),
    ("unconnected-(U1A-V+-Pad8)", ["U1.8"]),
    ("unconnected-(U1A-V--Pad4)", ["U1.4"]),
    ("unconnected-(U1B-IN+-Pad5)", ["U1.5"]),
    ("unconnected-(U1B-OUT-Pad7)", ["U1.7"]),
    ("unconnected-(U1B-V+-Pad8)", ["U1.8"]),
    ("unconnected-(U1B-V--Pad4)", ["U1.4"]),
    ("unconnected-(U2A-IN+-Pad3)", ["U2.3"]),
    ("unconnected-(U2A-IN--Pad2)", ["U2.2"]),
    ("unconnected-(U2A-V+-Pad8)", ["U2.8"]),
    ("unconnected-(U2A-V--Pad4)", ["U2.4"]),
    ("unconnected-(U2B-IN+-Pad5)", ["U2.5"]),
    ("unconnected-(U2B-OUT-Pad7)", ["U2.7"]),
    ("unconnected-(U2B-V+-Pad8)", ["U2.8"]),
    ("unconnected-(U2B-V--Pad4)", ["U2.4"]),
    ("unconnected-(U4-GND-Pad2)", ["U4.2"]),
    ("unconnected-(U4-SIG-Pad3)", ["U4.3"]),
    ("unconnected-(U5-SIG-Pad3)", ["U5.3"]),
]

# Where this tool and KiCad still name a net differently. The pins agree.
KNOWN_NAMES = {
    # A net joined across sheets through a bus, with local labels of its
    # member's name on the root and on two sheets below. Elsewhere the name
    # nearest the root wins a tie -- video, vme-wren -- and here KiCad takes
    # a sheet's. Not yet understood.
    "RoyalBlue54L-Feather": {("/Debugger/SWD_TRG.~{RESET}", "/SWD_TRG.~{RESET}")},
}


def nets_of(root: Path) -> list[tuple[str, list[str]]]:
    return [
        (net.name, sorted(f"{p.reference}.{p.pin.number}" for p in net.board_pins))
        for net in connect(Design(root))
        if net.board_pins
    ]


def kicad_nets(netlist: str) -> list[tuple[str, list[str]]]:
    node = sexpr.parse(netlist)
    return [
        (
            sexpr.value(sexpr.child(net, "name")),
            sorted(
                f"{sexpr.value(sexpr.child(n, 'ref'))}.{sexpr.value(sexpr.child(n, 'pin'))}"
                for n in sexpr.children(net, "node")
            ),
        )
        for net in sexpr.children(sexpr.child(node, "nets"), "net")
    ]


def test_the_fixture_connects_as_kicad_said():
    assert nets_of(FIXTURE) == [(name, pins) for name, pins in KICAD_SAID]


@pytest.mark.parametrize(
    ("text", "members"),
    [
        ("D[0..3]", ["D0", "D1", "D2", "D3"]),
        ("A[3..1]", ["A3", "A2", "A1"]),
        ("USB{DP DM}", ["USB.DP", "USB.DM"]),
        ("USB{VBUS, CC1, CC2}", ["USB.VBUS", "USB.CC1", "USB.CC2"]),
        ("DIG{D5 D[9..10]}", ["DIG.D5", "DIG.D9", "DIG.D10"]),
        ("SWD{~{RESET}, SWDIO}", ["SWD.~{RESET}", "SWD.SWDIO"]),
        ("{A B}", ["A", "B"]),
        ("ETH_C{ETH}", ["ETH_C.TX", "ETH_C.RX"]),
    ],
)
def test_a_bus_name_lists_its_members(text, members):
    assert [name for _, name in bus_members(text, {"ETH": ["TX", "RX"]})] == members


@pytest.mark.parametrize(
    "text",
    ["+V_{ADJ}", "~{RESET}", "FB{slash}VSET", "A^{2}", "GND", "X{slash}", "~{crst}{slash}out2"],
)
def test_markup_is_not_a_bus(text):
    assert bus_members(text, {}) is None


def test_nets_sort_as_kicad_sorts_them():
    names = [
        "0",
        "+5V",
        "/outb",
        "-5V",
        "Net-(D10-A)",
        "Net-(D9-A)",
        "VCC",
        "unconnected-(J1-Pad1)",
    ]
    assert sorted(names, key=natural_key) == [
        "+5V", "-5V", "/outb", "0", "Net-(D9-A)", "Net-(D10-A)", "VCC", "unconnected-(J1-Pad1)",
    ]  # fmt: skip


# -- against KiCad -----------------------------------------------------------------


def official_cli() -> str | None:
    try:
        return kicad_env.find_official_cli()
    except Exception:  # noqa: BLE001 - resolution must never break collection
        return None


needs_kicad = pytest.mark.skipif(official_cli() is None, reason="needs KiCad's own binary")


@functools.cache
def export(schematic: Path) -> str:
    """KiCad's netlist of a design. Cached: the netlist tests ask for every
    demo's too, and one export of each per run is enough. A demo is exported
    from a copy: KiCad's binary, when it fails, leaves a lock file beside the
    project it had open, and the installed demos are not the suite's to leave
    things in."""
    with tempfile.TemporaryDirectory() as tmp:
        if DEMOS.exists() and DEMOS.resolve() in schematic.resolve().parents:
            copy = copy_demo(schematic.parent, Path(tmp) / "design", whole=False)
            schematic = copy / schematic.name
        out = Path(tmp) / "out.net"
        for _ in range(3):  # Windows fails roughly one launch in six
            subprocess.run(
                [official_cli(), "sch", "export", "netlist", "--format", "kicadsexpr",
                 "-o", str(out), str(schematic)],
                capture_output=True,
                timeout=600,
            )  # fmt: skip
            if out.exists():
                return out.read_text(encoding="utf-8")
    raise AssertionError(f"KiCad would not export {schematic}")


@needs_kicad
def test_kicad_still_says_what_was_recorded(tmp_path):
    for source in FIXTURE.parent.glob("*.kicad_*"):
        (tmp_path / source.name).write_bytes(source.read_bytes())
    assert kicad_nets(export(tmp_path / FIXTURE.name)) == [(n, p) for n, p in KICAD_SAID]


def demo_roots() -> list[Path]:
    if not DEMOS.exists():
        return []
    return sorted(
        pro.with_suffix(".kicad_sch")
        for pro in DEMOS.rglob("*.kicad_pro")
        if pro.with_suffix(".kicad_sch").exists()
    )


@needs_kicad
@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
@pytest.mark.parametrize("root", demo_roots(), ids=lambda p: p.stem)
def test_every_demo_project_connects_as_kicad_connects_it(root):
    theirs = kicad_nets(export(root))
    ours = nets_of(root)
    assert sorted(p for _, p in ours) == sorted(p for _, p in theirs), "the nets' pins differ"
    known = KNOWN_NAMES.get(root.stem, set())
    their_name = {tuple(pins): name for name, pins in theirs}
    renamed = {(their_name[tuple(pins)], name) for name, pins in ours} - {
        (name, name) for name, _ in ours
    }
    assert renamed == known
    if not known:
        assert [n for n, _ in ours] == [n for n, _ in theirs], "the nets are in another order"
