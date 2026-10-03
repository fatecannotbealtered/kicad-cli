"""Write drcthermal/: a board asking KiCad's DRC how many thermal spokes
reach each pad a zone joins by thermal relief -- spokes cut by tracks of
other nets, the spokes' angle, a pad that connects solidly -- one question
per pad, each ten millimetres from the next.

The zones are written unfilled; KiCad fills them (`kicad-cli pcb drc
--refill-zones --save-board`), and its fill is what the board keeps, since
DRC checks a fill as the file holds it. The answers are recorded in
`drcthermal/drcthermal.kicad.json`. To change the board: edit this, run it,
let KiCad fill it, and record KiCad's answers again.
"""

import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from boardgen import (  # noqa: E402
    add,
    cell,
    footprint,
    rect,
    segment,
    smd,
    start,
    tht,
    uid,
    write,
    xy,
)

start("drcthermal", uuid.UUID("7a1c5d2e-0000-4000-8000-0000000d4c7e"))
SIDES = {"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1),
         "up-left": (-1, -1), "up-right": (1, -1), "down-left": (-1, 1),
         "down-right": (1, 1)}  # fmt: skip


def zone(key, net, points, connect=""):
    pts = " ".join(f"(xy {xy(p)})" for p in points)
    add(f'(zone (net "{net}") (layer "F.Cu") (uuid "{uid(key, "zone")}") (hatch edge 0.5)'
        f" (connect_pads {connect} (clearance 0.3)) (min_thickness 0.25)"
        f" (filled_areas_thickness no) (fill (thermal_gap 0.5) (thermal_bridge_width 0.5)"
        f" (island_removal_mode 1)) (polygon (pts {pts})))")  # fmt: skip


def case(col, key, pad="smd", block=(), connect="", pad_extra="", partial=False):
    """A zone of a net of its own around a 1.6 mm pad of it; a short track of
    another net across each spoke named in `block`, 1.6 mm out -- `partial`
    lays it half across, leaving part of the spoke."""
    x, y = cell(col, 0)
    net = f"G{key}"
    zone(key, net, rect(x - 4, y - 4, 8, 8), connect)
    if pad == "tht":
        footprint(key, (x, y), [tht(key, "1", (0, 0), (1.6, 1.6), 0.8, net, extra=pad_extra)],
                  attr="through_hole", courtyard="none")  # fmt: skip
    else:
        footprint(key, (x, y), [smd(key, "1", (0, 0), (1.6, 1.6), net, extra=pad_extra)],
                  courtyard="none")  # fmt: skip
    for side in block:
        dx, dy = SIDES[side]
        cx, cy = x + dx * 1.6, y + dy * 1.6
        half = 0.4 if partial else 0.9
        if dx and dy:  # across a diagonal spoke
            a = (cx - dy * half * 0.7, cy + dx * half * 0.7)
            b = (cx + dy * half * 0.7, cy - dx * half * 0.7)
        elif dx:
            a, b = (cx, cy - half), (cx, cy + (half if not partial else 0))
        else:
            a, b = (cx - half, cy), (cx + (half if not partial else 0), cy)
        segment(f"{key}{side}", a, b, 0.4, "F.Cu", f"S{key}{side}")


case(0, "A")  # four spokes free
case(1, "B", block=("left", "right"))  # two left
case(2, "C", block=("left", "right", "up"))  # one left
case(3, "D", block=("left", "right", "up", "down"))  # none left
case(4, "E", pad="tht", block=("left", "right", "up"))  # a round pad's spokes run diagonally
case(5, "F", pad="tht", block=("up-left", "up-right", "down-left"))  # one diagonal left
case(6, "H", block=("left", "right", "up"), connect="yes")  # joined solidly: no spokes asked
case(7, "J", block=("left", "right", "up"), pad_extra=" (thermal_bridge_angle 45)")  # turned
case(8, "K", block=("left", "right", "up"), partial=True)  # cut short, not off

write(
    HERE / "drcthermal", 9, 1,
    severities={
        "unconnected_items": "ignore", "track_dangling": "ignore", "missing_courtyard": "ignore",
        "lib_footprint_issues": "ignore", "lib_footprint_mismatch": "ignore",
        "clearance": "ignore", "isolated_copper": "ignore",
    },
)  # fmt: skip
