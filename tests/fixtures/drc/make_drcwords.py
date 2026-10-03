"""Write drcwords/ and drcwords2/: two boards asking KiCad's DRC how it words
what it finds, one question per case, each ten millimetres from the next.

drcwords -- with net classes named in clearance messages, as a class whose
clearance (Wide, 0.4 mm) is wider than its pairs' gap makes them:

- row 0, two tracks 0.1 mm apart, of two classes asking the same 0.2 mm:
  which class the message names;
- row 1, the same of a pad and a track;
- row 2, nets whose names KiCad's file escapes ("{slash}"), as a report
  writes them, in a clearance and in a short;
- row 3, vias whose holes are too small to show in four places.

drcwords2 -- with no class named: only Default, whose clearance is narrower
than its pairs' gap. The board's minimum, a pad's own clearance and a
zone's are named still.

The answers are recorded in `drcwords/drcwords.kicad.json` and
`drcwords2/drcwords2.kicad.json`; to change a board, edit this, run it, and
record KiCad's answers again.
"""

import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from boardgen import cell, footprint, segment, smd, start, via, write, zone  # noqa: E402

DEFAULT = {
    "name": "Default", "clearance": 0.2, "track_width": 0.2, "via_diameter": 0.6,
    "via_drill": 0.3, "microvia_diameter": 0.3, "microvia_drill": 0.1, "diff_pair_width": 0.2,
    "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25, "priority": 2147483647, "bus_width": 12,
    "line_style": 0, "wire_width": 6, "pcb_color": "rgba(0, 0, 0, 0.000)",
    "schematic_color": "rgba(0, 0, 0, 0.000)",
}  # fmt: skip
SEVERITIES = {
    "unconnected_items": "ignore", "track_dangling": "ignore", "via_dangling": "ignore",
    "annular_width": "ignore", "lib_footprint_issues": "ignore", "isolated_copper": "ignore",
}  # fmt: skip


def tracks(col, row, first, second, gap=0.1):
    """Two 0.2 mm tracks, `gap` apart."""
    x, y = cell(col, row)
    segment(f"{first}-{col}-{row}", (x - 2, y), (x + 2, y), 0.2, "F.Cu", first)
    segment(f"{second}-{col}-{row}b", (x - 2, y + 0.2 + gap), (x + 2, y + 0.2 + gap), 0.2,
            "F.Cu", second)  # fmt: skip


def pad_and_track(col, row, pad_net, track_net, gap=0.1, own=""):
    """A 1 mm pad and a 0.2 mm track `gap` from it."""
    x, y = cell(col, row)
    footprint(
        f"U{col}{row}", (x, y), [smd(f"p{col}{row}", "1", (0, 0), (1, 1), pad_net, extra=own)]
    )
    segment(f"t{col}{row}", (x - 2, y + 0.6 + gap), (x + 2, y + 0.6 + gap), 0.2, "F.Cu", track_net)


# -- drcwords ---------------------------------------------------------------------------------

start("drcwords", uuid.UUID("7a1c5d2e-0000-4000-8000-0000000d0c01"))
# In the project: not by name, and not by priority.
CLASSES = {
    "classes": [
        DEFAULT,
        {"name": "Gamma", "clearance": 0.2, "priority": 2},
        {"name": "Alpha", "clearance": 0.2, "priority": 1},
        {"name": "Beta", "clearance": 0.2, "priority": 0},
        {"name": "alpha", "clearance": 0.2, "priority": 4},
        {"name": "Wide", "clearance": 0.4, "priority": 3},
    ],
    "netclass_patterns": [
        {"pattern": "A*", "netclass": "Alpha"}, {"pattern": "B*", "netclass": "Beta"},
        {"pattern": "G*", "netclass": "Gamma"}, {"pattern": "L*", "netclass": "alpha"},
        {"pattern": "W*", "netclass": "Wide"},
    ],
}  # fmt: skip
for col, (first, second) in enumerate(
    [
        ("D0", "A0"),
        ("A1", "D1"),
        ("A2", "B2"),
        ("B3", "A3"),
        ("B4", "G4"),
        ("G5", "B5"),
        ("L6", "B6"),
        ("B7", "L7"),
    ]
):
    tracks(col, 0, first, second)
for col, (pad_net, track_net) in enumerate([("D10", "A10"), ("A11", "D11"), ("B12", "G12"),
                                            ("G13", "B13")]):  # fmt: skip
    pad_and_track(col, 1, pad_net, track_net)
x, y = cell(7, 1)
segment("wide", (x - 2, y), (x + 2, y), 0.2, "F.Cu", "W1")
for col, name in enumerate(["E{slash}1", "E{backslash}2", "E{lt}3{gt}", "E{colon}4",
                            "E{dblquote}5", "E{quote}6", "E{space}7", "E{bar}8"]):  # fmt: skip
    tracks(col, 2, name, f"P{col}")
x, y = cell(8, 2)  # a track from a pad of its net over a pad of another: a short
footprint("U82", (x, y), [smd("p82", "1", (0, 0), (1, 1), "S{colon}2"),
                          smd("q82", "2", (3, 0), (1, 1), "S{slash}1")])  # fmt: skip
segment("s82", (x - 2, y), (x + 3, y), 0.2, "F.Cu", "S{slash}1")
for col, drill in enumerate([0.00001, 0.00004, 0.00005, 0.0001]):
    x, y = cell(col, 3)
    via(f"v{col}", (x, y), 0.6, drill, f"V{col}")
write(HERE / "drcwords", 9, 4, severities=SEVERITIES, net_settings=CLASSES)

# -- drcwords2 --------------------------------------------------------------------------------

start("drcwords2", uuid.UUID("7a1c5d2e-0000-4000-8000-0000000d0c02"))
tracks(0, 0, "D0", "D0x")  # the class's clearance: no name
pad_and_track(1, 0, "P1", "T1", gap=0.25, own=" (clearance 0.3)")  # the pad's: named
pad_and_track(2, 0, "P2", "T2", gap=0.12, own=" (clearance 0.1)")  # the board's minimum: named
x, y = cell(3, 0)  # a zone's clearance, wider than the class's: named
zone("z3", "Z3", ["F.Cu"], [(x - 2, y - 2), (x + 2, y - 2), (x + 2, y + 2), (x - 2, y + 2)],
     clearance=0.3, fill=[[(x - 2, y - 2), (x + 2, y - 2), (x + 2, y), (x - 2, y)]])  # fmt: skip
segment("t3", (x - 2, y + 0.35), (x + 2, y + 0.35), 0.2, "F.Cu", "T3")
write(HERE / "drcwords2", 4, 1, severities=SEVERITIES, rules={"min_clearance": 0.15},
      net_settings={"classes": [DEFAULT], "netclass_patterns": []})  # fmt: skip
