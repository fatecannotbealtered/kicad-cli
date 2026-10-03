"""Write drcrules2/: a board whose custom rules ask for what only a rule
asks for -- items disallowed where a condition holds, courtyards held apart,
text sizes, track segment lengths, a net's vias counted, the angle two
segments make -- one question per case, each ten millimetres from the next.

The answers are recorded in `drcrules2/drcrules2.kicad.json`; to change the
board, edit this, run it, and record KiCad's answers again.
"""

import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from boardgen import (  # noqa: E402
    add,
    cell,
    font,
    footprint,
    rect,
    segment,
    smd,
    start,
    text,
    uid,
    via,
    write,
    xy,
)

start("drcrules2", uuid.UUID("7a1c5d2e-0000-4000-8000-0000000d4c2e"))


def note(key, text_):
    """What a case asks (kept beside it, not in the board)."""


def area(key, col, row, name):
    x, y = cell(col, row)
    pts = " ".join(f"(xy {xy(p)})" for p in rect(x - 2, y - 2, 4, 4))
    add(
        f'(zone (layer "F.Cu") (uuid "{uid("area", key)}") (name "{name}") (hatch edge 0.5)'
        f" (connect_pads (clearance 0)) (min_thickness 0.25) (filled_areas_thickness no)"
        f" (keepout (tracks allowed) (vias allowed) (pads allowed) (copperpour allowed)"
        f" (footprints allowed)) (fill (thermal_gap 0.5) (thermal_bridge_width 0.5))"
        f" (polygon (pts {pts})))"
    )


def build():
    # row 0: disallow
    area("D0", 0, 0, "novia")
    x, y = cell(0, 0)
    via("DV", (x, y), 0.6, 0.3, "DV")
    note("DV", "via in 'novia': rule disallow via")
    area("D1", 1, 0, "notrack")
    x, y = cell(1, 0)
    segment("DT", (x - 3, y), (x + 3, y), 0.2, "F.Cu", "DT")
    note("DT", "track across 'notrack': rule disallow track")
    area("D2", 2, 0, "nopad")
    x, y = cell(2, 0)
    footprint("DP", (x, y), [smd("DP", "1", (0, 0), (1, 1), "DP")], courtyard="none")
    note("DP", "pad in 'nopad': rule disallow pad")
    x, y = cell(3, 0)
    via("DW", (x, y), 0.6, 0.3, "DW")
    note("DW", "via of net DW: rule disallow via, condition A.NetName == 'DW'")
    # row 1: courtyards, text
    x, y = cell(0, 1)
    footprint("CA", (x - 1.2, y), [smd("CA", "1", (0, 0), (1, 1), "CA")])
    footprint("CB", (x + 1.2, y), [smd("CB", "1", (0, 0), (1, 1), "CB")])
    note("CA", "courtyards 0.9 apart: rule courtyard_clearance min 1")
    x, y = cell(1, 1)
    text("TXa", "small", (x, y), "F.SilkS", font(0.5, t=0.1))
    note("small", "text 0.5 high on silk: rule text_height min 0.8 (board 0.8 too)")
    x, y = cell(2, 1)
    text("TXb", "thin", (x, y), "F.SilkS", font(1, t=0.05))
    note("thin", "text 0.05 thick: rule text_thickness min 0.1")
    # row 2: segment lengths, via counts, angles
    x, y = cell(0, 2)
    segment("SL", (x - 0.2, y), (x + 0.2, y), 0.2, "F.Cu", "SL")
    note("SL", "segment 0.4 long: rule track_segment_length min 0.5")
    x, y = cell(1, 2)
    for i in range(3):
        via("VC", (x - 1 + i, y), 0.6, 0.3, "VC", n=i)
    segment("VCt", (x - 1, y), (x + 1, y), 0.2, "F.Cu", "VC")
    note("VC", "three vias on net VC: rule via_count max 2")
    x, y = cell(2, 2)
    segment("TA1", (x - 2, y), (x, y), 0.2, "F.Cu", "TA")
    segment("TA2", (x, y), (x + 1, y + 1.732), 0.2, "F.Cu", "TA")
    note("TA", "two segments turning 60 degrees: rule track_angle min 90")


RULES = """(version 1)
(rule "no_via_here"
	(condition "A.intersectsArea('novia')")
	(constraint disallow via))
(rule "no_track_here"
	(condition "A.intersectsArea('notrack')")
	(constraint disallow track))
(rule "no_pad_here"
	(condition "A.intersectsArea('nopad')")
	(constraint disallow pad))
(rule "no_dw_via"
	(condition "A.NetName == 'DW'")
	(constraint disallow via))
(rule "court"
	(constraint courtyard_clearance (min 1mm)))
(rule "text_h"
	(condition "A.Type == 'Text'")
	(constraint text_height (min 0.8mm)))
(rule "text_t"
	(condition "A.Type == 'Text'")
	(constraint text_thickness (min 0.1mm)))
(rule "seg_len"
	(condition "A.NetName == 'SL'")
	(constraint track_segment_length (min 0.5mm)))
(rule "vias_max"
	(condition "A.NetName == 'VC'")
	(constraint via_count (max 2)))
(rule "angle"
	(condition "A.NetName == 'TA'")
	(constraint track_angle (max 30)))
"""

build()
out = HERE / "drcrules2"
write(
    out, 5, 3,
    severities={
        "unconnected_items": "ignore", "track_dangling": "ignore", "via_dangling": "ignore",
        "silk_overlap": "ignore", "isolated_copper": "ignore", "lib_footprint_issues": "ignore",
        "lib_footprint_mismatch": "ignore", "missing_courtyard": "ignore",
        "silk_over_copper": "ignore", "silk_edge_clearance": "ignore",
    },
)  # fmt: skip
(out / "drcrules2.kicad_dru").write_bytes(RULES.encode("utf-8"))
