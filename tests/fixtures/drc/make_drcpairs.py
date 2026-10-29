"""Write drcpairs/: a board asking KiCad's DRC about differential pairs with
no custom rule -- which nets make a pair, which of their tracks are coupled,
and how near coupled tracks may run -- one question per case, each ten
millimetres from the next. The board's minimum clearance is 0.18 mm; the
net classes' pair gaps are 0.25 and 0.3.

The answers are recorded in `drcpairs/drcpairs.kicad.json`; to change the
board, edit this, run it, and record KiCad's answers again.
"""

import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from boardgen import cell, segment, start, write  # noqa: E402

start("drcpairs", uuid.UUID("7a1c5d2e-0000-4000-8000-0000000d4ca1"))

CLASSES = {
    "classes": [
        {"name": "Default", "clearance": 0.2, "track_width": 0.2, "via_diameter": 0.6,
         "via_drill": 0.3, "microvia_diameter": 0.3, "microvia_drill": 0.1,
         "diff_pair_width": 0.2, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
         "priority": 2147483647, "bus_width": 12, "line_style": 0, "wire_width": 6,
         "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)"},
        {"name": "Fast", "clearance": 0.2, "diff_pair_gap": 0.3, "priority": 0},
    ],
    "netclass_patterns": [{"pattern": "F_*", "netclass": "Fast"}],
}  # fmt: skip


def pair(col, row, p_net, n_net, gap, layers=("F.Cu", "F.Cu")):
    """Two parallel 0.2 mm tracks, `gap` apart."""
    x, y = cell(col, row)
    segment(f"{p_net}a", (x - 2, y), (x + 2, y), 0.2, layers[0], p_net)
    segment(f"{n_net}b", (x - 2, y + 0.2 + gap), (x + 2, y + 0.2 + gap), 0.2, layers[1], n_net)


# -- row 0: how near -------------------------------------------------------------------------

for col, gap in enumerate((0.1, 0.17, 0.18, 0.25, 0.5, 2.0)):
    pair(col, 0, f"G{col}_P", f"G{col}_N", gap)

# -- row 1: which tracks are coupled ---------------------------------------------------------

x, y = cell(0, 1)  # a V, 0.1 apart at its narrow end
segment("V_Pa", (x - 2, y), (x + 2, y), 0.2, "F.Cu", "V_P")
segment("V_Nb", (x - 2, y + 0.3), (x + 2, y + 1.5), 0.2, "F.Cu", "V_N")
x, y = cell(1, 1)  # parallel 0.1 apart, side by side for half their length
segment("H_Pa", (x - 2, y), (x + 2, y), 0.2, "F.Cu", "H_P")
segment("H_Nb", (x, y + 0.3), (x + 4, y + 0.3), 0.2, "F.Cu", "H_N")
x, y = cell(2, 1)  # parallel 0.1 apart, end to end
segment("E_Pa", (x - 2, y), (x, y), 0.2, "F.Cu", "E_P")
segment("E_Nb", (x + 0.5, y + 0.3), (x + 2.5, y + 0.3), 0.2, "F.Cu", "E_N")
pair(3, 1, "L_P", "L_N", 0.1, layers=("F.Cu", "B.Cu"))  # on two layers
x, y = cell(4, 1)  # diagonal, 0.1 apart
segment("D_Pa", (x - 2, y - 2), (x + 2, y + 2), 0.2, "F.Cu", "D_P")
segment("D_Nb", (x - 2 + 0.4243, y - 2), (x + 2 + 0.4243, y + 2), 0.2, "F.Cu", "D_N")
pair(5, 1, "F_P", "F_N", 0.1)  # class Fast: its pair gap of 0.3 is not the bound

# -- row 2: which nets make a pair -----------------------------------------------------------

for col, (p_net, n_net) in enumerate([("AP", "AN"), ("B+", "B-"), ("C_p", "C_n"),
                                       ("K_P1", "K_N1"), ("MP_1", "MN_1")]):  # fmt: skip
    pair(col, 2, p_net, n_net, 0.1)

write(
    HERE / "drcpairs", 6, 3,
    severities={
        "unconnected_items": "ignore", "track_dangling": "ignore", "clearance": "ignore",
        "tracks_crossing": "ignore", "shorting_items": "ignore",
    },
    rules={"min_clearance": 0.18},
    net_settings=CLASSES,
)  # fmt: skip
