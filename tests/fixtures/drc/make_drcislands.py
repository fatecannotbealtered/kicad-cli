"""Write drcislands/: a board asking KiCad's DRC which islands of a zone's
fill are isolated copper -- by the zone's island removal mode, by what an
island touches -- one question per zone, each ten millimetres from the
next. The fills are written as given, not filled by KiCad: DRC checks a fill
as the file holds it.

The answers are recorded in `drcislands/drcislands.kicad.json`; to change
the board, edit this, run it, and record KiCad's answers again.
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
    uid,
    via,
    write,
    xy,
)

start("drcislands", uuid.UUID("7a1c5d2e-0000-4000-8000-0000000d4c15"))


def zone(key, net, fills, mode=None, area_min=None, outline=None):
    extra = f" (island_removal_mode {mode})" if mode is not None else ""
    extra += f" (island_area_min {area_min})" if area_min is not None else ""
    pts = " ".join(f"(xy {xy(p)})" for p in outline)
    filled = "".join(
        f' (filled_polygon (layer "F.Cu") (pts {" ".join(f"(xy {xy(p)})" for p in poly)}))'
        for poly in fills
    )
    add(f'(zone (net "{net}") (layer "F.Cu") (uuid "{uid(key, "zone")}") (hatch edge 0.5)'
        f" (connect_pads yes (clearance 0.2)) (min_thickness 0.25) (filled_areas_thickness no)"
        f" (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5){extra})"
        f" (polygon (pts {pts})){filled})")  # fmt: skip


def case(col, net, mode=None, area_min=None, island=2.0, touch=None, islands=1):
    """A zone of `net`: its main fill under a pad of the net, and islands
    `island` mm square beside it, touching `touch` if anything."""
    x, y = cell(col, 0)
    main = rect(x - 4, y - 4, 8, 3)
    footprint(f"P{net}", (x, y - 2.5), [smd(f"P{net}", "1", (0, 0), (1, 1), net)],
              courtyard="none")  # fmt: skip
    fills = [main] + [rect(x - 4 + n * 4, y + 1, island, island) for n in range(islands)]
    zone(net, net, fills, mode, area_min, outline=rect(x - 4.5, y - 4.5, 9, 9))
    if touch == "track":
        segment(f"T{net}", (x - 3.5, y + 1.5), (x - 3.5, y + 3.5), 0.2, "F.Cu", net)
    elif touch == "via":
        via(f"V{net}", (x - 3, y + 2), 0.6, 0.3, net)
    elif touch == "pad":
        footprint(f"Q{net}", (x - 3, y + 2), [smd(f"Q{net}", "1", (0, 0), (0.6, 0.6), net)],
                  courtyard="none")  # fmt: skip


case(0, "G0")  # the zone says nothing of islands
case(1, "G1", mode=0)  # islands always removed
case(2, "G2", mode=1)  # never removed
case(3, "G3", mode=2, area_min=10, island=2.0)  # removed below 10 mm2: an island of 4
case(4, "G4", mode=2, area_min=10, island=4.0)  # an island of 16
case(5, "G5", mode=1, touch="track")  # touching a track of its net, and nothing else
case(6, "G6", mode=1, touch="via")  # touching a via of its net, and nothing else
case(7, "G7", mode=1, touch="pad")  # touching a pad of its net
case(8, "G8", mode=1, islands=2)  # two islands

write(
    HERE / "drcislands", 9, 1,
    severities={
        "unconnected_items": "ignore", "track_dangling": "ignore", "via_dangling": "ignore",
        "missing_courtyard": "ignore", "lib_footprint_issues": "ignore",
        "lib_footprint_mismatch": "ignore",
    },
)  # fmt: skip
