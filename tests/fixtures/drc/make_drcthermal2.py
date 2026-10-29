"""Write drcthermal2/: whether a track of the pad's own net counts as a
thermal spoke. Each pad keeps one spoke of four -- the other three cut by
tracks of other nets, as in `drcthermal` -- and has a track of its own net
leaving it: into the fill, along the spoke, ending inside the relief,
ending past the middle of the relief but short of the fill.

The zones are written unfilled; KiCad fills them (`kicad-cli pcb drc
--refill-zones --save-board`), and its fill is what the board keeps. The
answers are recorded in `drcthermal2/drcthermal2.kicad.json`. To change the
board: edit this, run it, let KiCad fill it, and record KiCad's answers again.
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

start("drcthermal2", uuid.UUID("7a1c5d2e-0000-4000-8000-0000000d4c7f"))
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


case(0, "C", block=("left", "right", "up"))  # one spoke left
case(1, "C2", block=("left", "right", "up"))  # and a track of its own net into the fill
x, y = cell(1, 0)
segment("C2own", (x, y), (x - 1.0, y - 2.5), 0.25, "F.Cu", "GC2")
case(2, "C3", block=("left", "right", "up"))  # and one along the spoke
x, y = cell(2, 0)
segment("C3own", (x, y), (x, y + 2.5), 0.25, "F.Cu", "GC3")
case(3, "C4", block=("left", "right", "up"))  # and one ending inside the relief
x, y = cell(3, 0)
segment("C4own", (x, y), (x - 0.95, y - 0.95), 0.25, "F.Cu", "GC4")
case(4, "C5", block=("left", "right", "up"))  # and one past the relief's middle, short of the fill
x, y = cell(4, 0)
segment("C5own", (x, y), (x - 1.15, y - 1.15), 0.25, "F.Cu", "GC5")
case(5, "D2", block=("left", "right", "up", "down"))  # no spoke, a track into the fill
x, y = cell(5, 0)
segment("D2own", (x, y), (x - 2.5, y - 2.5), 0.25, "F.Cu", "GD2")
case(6, "E2", pad="tht", block=("up-left", "up-right", "down-left"))  # round: one diagonal left
x, y = cell(6, 0)
segment("E2own", (x, y), (x + 2.5, y), 0.25, "F.Cu", "GE2")  # and a track of its own net

write(
    HERE / "drcthermal2", 7, 1,
    severities={
        "unconnected_items": "ignore", "track_dangling": "ignore", "missing_courtyard": "ignore",
        "lib_footprint_issues": "ignore", "lib_footprint_mismatch": "ignore",
        "clearance": "ignore", "isolated_copper": "ignore",
    },
)  # fmt: skip
