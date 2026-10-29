"""Pure model checks for the declared 20-part / 42-pin dual RC topology.

The pins/nets below precede the new layout. They are not recovered from its
output. The drawings are intentionally tiny owned models, not installed KiCad
glyph validation; complete public EXE/native images are a separate acceptance.
"""

from kicad_cli.fileformat.schematic import LibPin, LibSymbol
from kicad_cli.fileformat.sexpr import List, quote
from kicad_cli.fileformat.symbols import Resolved
from kicad_cli.native import sch_create as create

NETS = {
    "A_BUF": ["U1.1", "U1.2", "R11.1"],
    "A_RC": ["U1.3", "R10.2", "C10.1", "TP1.1"],
    "GND": [
        "U1.4",
        "J1.2",
        "J2.2",
        "J3.2",
        "J4.2",
        "J5.2",
        "C10.2",
        "C11.2",
        "C20.2",
        "C21.2",
        "C1.2",
        "C2.2",
    ],
    "B_RC": ["U1.5", "R20.2", "C20.1", "TP3.1"],
    "B_BUF": ["U1.6", "U1.7", "R21.1"],
    "+3V3": ["U1.8", "J5.1", "C1.1", "C2.1"],
    "A_IN": ["J1.1", "R10.1"],
    "A_OUT": ["J2.1", "R11.2", "C11.1", "TP2.1"],
    "B_IN": ["J3.1", "R20.1"],
    "B_OUT": ["J4.1", "R21.2", "C21.1", "TP4.1"],
}
POWER = {"+3V3", "GND"}


def pin(number, unit, electrical="passive", side="left"):
    x, y, angle = {
        "left": (-5_080_000, 0, 0),
        "right": (5_080_000, 0, 180),
        "top": (0, 2_540_000, 270),
        "bottom": (0, -2_540_000, 90),
    }[side]
    if electrical == "input":
        y = {2: -1_270_000, 3: 1_270_000, 5: 1_270_000, 6: -1_270_000}.get(number, y)
    return LibPin(str(number), str(number), electrical, "line", (x, y), angle, 1_270_000, unit, 1)


def part(ref, pins, units=1, value=""):
    node = List.new("symbol", quote("Owned:" + ref))
    for unit in range(1, units + 1):
        node.append(
            List.new(
                "symbol",
                quote(f"{ref}_{unit}_1"),
                List.new("rectangle", List.new("start", "-1", "-1"), List.new("end", "1", "1")),
            )
        )
    symbol = LibSymbol("Owned:" + ref, {}, pins, False, False, units, node=node)
    return create.Part(ref, Resolved("Owned:" + ref, node, symbol), value or ref, "owned:fixture")


def topology():
    parts = [
        part(
            "U1",
            [
                pin(1, 1, "output", "right"),
                pin(2, 1, "input"),
                pin(3, 1, "input"),
                pin(4, 3, "power_in", "bottom"),
                pin(5, 2, "input"),
                pin(6, 2, "input"),
                pin(7, 2, "output", "right"),
                pin(8, 3, "power_in", "top"),
            ],
            3,
            "TLV9062IDR",
        )
    ]
    for ref in [
        "J1",
        "J2",
        "J3",
        "J4",
        "J5",
        "R10",
        "R11",
        "R20",
        "R21",
        "C1",
        "C2",
        "C10",
        "C11",
        "C20",
        "C21",
    ]:
        parts.append(
            part(
                ref,
                [pin(1, 1, side="top"), pin(2, 1, side="bottom")],
                value={"C1": "100n", "C2": "1u"}.get(ref, "10k"),
            )
        )
    for ref in ["TP1", "TP2", "TP3", "TP4"]:
        parts.append(part(ref, [pin(1, 1, side="bottom")]))
    on = {tuple(p.rsplit(".", 1)): net for net, pins in NETS.items() for p in pins}
    units = [u for p in parts for u in create._units(p, on)]
    for unit in units:
        create._plan(unit, POWER)
    return units, parts, on


def pins_and_drawings(units):
    return [
        (
            id(u),
            u.part.ref,
            u.unit,
            [(s.pin.number, s.net, s.at, s.out) for s in u.spots],
            repr(u.drawn),
            u.body,
            u.box,
            u.ref_at,
            u.value_at,
        )
        for u in units
    ]


def test_twenty_part_topology_separates_channels_and_power_without_changing_pins():
    units, parts, on = topology()
    before = pins_and_drawings(units)
    groups = create._groups(units, parts, on, POWER)
    assert len(parts) == 20 and sum(len(u.spots) for u in units) == 42 and len(NETS) == 10
    assert pins_and_drawings(units) == before
    assert len(groups) == 3
    assert [{(u.part.ref, u.unit) for u in group.units} for group in groups] == [
        {("U1", 1), *[(r, 1) for r in ["J1", "J2", "R10", "R11", "C10", "C11", "TP1", "TP2"]]},
        {("U1", 2), *[(r, 1) for r in ["J3", "J4", "R20", "R21", "C20", "C21", "TP3", "TP4"]]},
        {("U1", 3), ("J5", 1), ("C1", 1), ("C2", 1)},
    ]
    assert [g.layout for g in groups] == ["separable_multiunit_signal_component"] * 2 + [
        "separate_multiunit_power"
    ]


def test_recursive_series_branches_follow_input_and_output_faces_consistently():
    units, parts, on = topology()
    groups = create._groups(units, parts, on, POWER)
    for group, refs in zip(
        groups[:2], [("J1", "R10", "R11", "J2"), ("J3", "R20", "R21", "J4")], strict=True
    ):
        by_ref = {u.part.ref: u for u in group.units}
        chain = [by_ref[r] for r in [refs[0], refs[1], "U1", refs[2], refs[3]]]
        assert all(
            create._placed_box(a)[2] < create._placed_box(b)[0]
            for a, b in zip(chain, chain[1:], strict=False)
        )
    a = {u.part.ref: u.origin for u in groups[0].units}
    b = {u.part.ref: u.origin for u in groups[1].units}
    for first, second in [
        ("U1", "U1"),
        ("J1", "J3"),
        ("J2", "J4"),
        ("R10", "R20"),
        ("R11", "R21"),
        ("C10", "C20"),
        ("C11", "C21"),
        ("TP1", "TP3"),
        ("TP2", "TP4"),
    ]:
        assert a[first] == b[second]


def test_coupled_signal_units_keep_existing_grouping_and_disclose_fallback():
    units, parts, on = topology()
    # A shared non-power input net makes the two units coupled, regardless of names.
    for unit in units:
        if unit.part.ref == "U1" and unit.unit == 2:
            next(s for s in unit.spots if s.pin.number == "5").net = "A_RC"
    on[("U1", "5")] = "A_RC"
    before = pins_and_drawings(units)
    groups = create._groups(units, parts, on, POWER)
    assert all(g.layout == "physical_reference" for g in groups)
    assert all("share a non-power component" in g.layout_reason for g in groups)
    assert pins_and_drawings(units) == before


def test_ambiguous_feedback_branch_does_not_guess_input_output_membership():
    units, parts, on = topology()
    for unit in units:
        if unit.part.ref == "C10":
            next(s for s in unit.spots if s.pin.number == "2").net = "A_BUF"
    on[("C10", "2")] = "A_BUF"
    groups = create._groups(units, parts, on, POWER)
    assert all(g.layout == "physical_reference" for g in groups)
    assert all("direction is ambiguous" in g.layout_reason for g in groups)


def test_single_unit_centre_retains_legacy_layout_path():
    units, parts, on = topology()
    selected = [u for u in units if u.part.ref != "U1" or u.unit == 1]
    original = pins_and_drawings(selected)
    groups = create._groups(selected, parts, on, POWER)
    assert groups and all(g.layout == "physical_reference" for g in groups)
    assert pins_and_drawings(selected) == original
