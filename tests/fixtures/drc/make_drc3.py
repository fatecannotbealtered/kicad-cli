"""Write drc3/: a board asking KiCad's DRC about rule areas, text
variables, a plated pad with no hole and copper on a layer the board has
not got -- one question per case, each ten millimetres from the next.

The answers are recorded in `drc3/drc3.kicad.json`; to change the board,
edit this, run it, and record KiCad's answers again. The project defines
one text variable, MYVAR.
"""

import json
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
    tht,
    uid,
    via,
    write,
    xy,
)

start("drc3", uuid.UUID("7a1c5d2e-0000-4000-8000-0000000d4c03"))


def keepout(key, points, rules, layers=("F.Cu",), name=""):
    """A rule area keeping out what `rules` says, on `layers`."""
    pts = " ".join(f"(xy {xy(p)})" for p in points)
    layer_text = (
        f'(layer "{layers[0]}")'
        if len(layers) == 1
        else "(layers " + " ".join(f'"{la}"' for la in layers) + ")"
    )
    name_text = f'(name "{name}")' if name else ""
    allow = {k: "allowed" for k in ("tracks", "vias", "pads", "copperpour", "footprints")}
    allow.update(rules)
    keep = " ".join(f"({k} {v})" for k, v in allow.items())
    add(
        f'(zone {layer_text} (uuid "{uid(key, "keepout")}") {name_text} (hatch edge 0.5)'
        f" (connect_pads (clearance 0)) (min_thickness 0.25) (filled_areas_thickness no)"
        f" (keepout {keep}) (fill (thermal_gap 0.5) (thermal_bridge_width 0.5))"
        f" (polygon (pts {pts})))"
    )


# -- row 0: rule areas ---------------------------------------------------------------------

x, y = cell(0, 0)  # a track through a named no-tracks area
keepout("K1", rect(x - 1, y - 1, 2, 2), {"tracks": "not_allowed"}, name="notracks")
segment("K1", (x - 3, y), (x + 3, y), 0.25, "F.Cu", "K1")
x, y = cell(1, 0)  # a via in a no-vias area
keepout("K2", rect(x - 1, y - 1, 2, 2), {"vias": "not_allowed"})
via("K2", (x, y), 0.6, 0.3, "K2")
x, y = cell(2, 0)  # a pad in a no-pads area
keepout("K3", rect(x - 1, y - 1, 2, 2), {"pads": "not_allowed"})
footprint("K3", (x, y), [smd("K3", "1", (0, 0), (0.8, 0.8), "K3")], courtyard="none")
x, y = cell(3, 0)  # a footprint in a no-footprints area
keepout("K4", rect(x - 1, y - 1, 2, 2), {"footprints": "not_allowed"})
footprint("K4", (x + 1.2, y), [smd("K4", "1", (0, 0), (0.8, 0.8), "K4")])
x, y = cell(4, 0)  # a track under a no-tracks area on the other side
keepout("K5", rect(x - 1, y - 1, 2, 2), {"tracks": "not_allowed"}, layers=("B.Cu",))
segment("K5", (x - 3, y), (x + 3, y), 0.25, "F.Cu", "K5")
x, y = cell(5, 0)  # a track 0.1 short of a no-tracks area
keepout("K6", rect(x - 1, y - 1, 2, 2), {"tracks": "not_allowed"})
segment("K6", (x - 3, y + 1.225), (x + 3, y + 1.225), 0.25, "F.Cu", "K6")
x, y = cell(6, 0)  # a plated pad in an area keeping out only tracks and vias
keepout("K7", rect(x - 1, y - 1, 2, 2), {"tracks": "not_allowed", "vias": "not_allowed"},
        layers=("F.Cu", "B.Cu"))  # fmt: skip
footprint("K7", (x, y), [tht("K7", "1", (0, 0), (1.2, 1.2), 0.6, "K7")], attr="through_hole",
          courtyard="none")  # fmt: skip
x, y = cell(7, 0)  # a footprint whose courtyard, not its pad, reaches into the area
keepout("K8", rect(x - 1, y - 1, 2, 2), {"footprints": "not_allowed"})
footprint("K8", (x + 1.6, y), [smd("K8", "1", (0, 0), (0.8, 0.8), "K8")],
          courtyard=f"(fp_rect (start -1 -1) (end 1 1) (stroke (width 0.05) (type solid))"
                    f' (fill no) (layer "F.CrtYd") (uuid "{uid("K8", "cy")}"))')  # fmt: skip

# -- row 1: a plated pad with no hole, copper on a layer the board has not got ----------

x, y = cell(0, 1)
footprint("H0", (x, y), [tht("H0", "1", (0, 0), (1.2, 1.2), 0, "H0")], attr="through_hole",
          courtyard="none")  # fmt: skip
x, y = cell(1, 1)
segment("L1", (x - 3, y), (x + 3, y), 0.25, "In5.Cu", "L1")
x, y = cell(2, 1)  # a drawing on a user layer the board has not got: not asked about
add(f'(gr_line (start {xy((x - 1, y))}) (end {xy((x + 1, y))}) (stroke (width 0.1) (type solid))'
    f' (layer "User.9") (uuid "{uid("L2", "line")}"))')  # fmt: skip

# -- rows 2 to 5: text variables -----------------------------------------------------------

BOARD = [
    "TITLE", "REVISION", "COMPANY", "COMMENT1", "COMMENT9", "ISSUE_DATE", "FILENAME",
    "FILEPATH", "PROJECTNAME", "CURRENT_DATE", "LAYER", "VCSHASH", "VCSSHORTHASH",
    "BOARD_NAME", "MYVAR", "R1:VALUE", "R1:MPN", "R1:NOPE", "R9:VALUE", "R1:REFERENCE",
    "R1:FOOTPRINT_NAME", "SHEETNAME", "SHEETPATH", "PAPER", "KICAD_VERSION", "DRC_ERROR",
    "REFERENCE", "VALUE", "NOSUCH",
]  # fmt: skip
FOOTPRINT = [
    "REFERENCE", "VALUE", "MPN", "NOPE", "FOOTPRINT_NAME", "FOOTPRINT_LIBRARY", "LAYER",
    "NET_NAME", "SHORT_NET_NAME", "NET_CLASS", "PIN_NAME", "TITLE", "MYVAR", "DNP", "ref",
    "Reference", "ATTRIBUTES",
]  # fmt: skip
for n, var in enumerate(BOARD):
    x, y = 20 + 12 * (n % 10), 40 + 3 * (n // 10)
    add(f'(gr_text "${{{var}}}" (at {xy((x, y))} 0) (layer "F.Fab") (uuid "{uid("B", var)}")'
        f" {font(1)})")  # fmt: skip
x, y = cell(9, 5)  # a marker left for DRC to raise, with its own words
add(f'(gr_text "${{DRC_WARNING check this}}" (at {xy((x, y))} 0) (layer "F.Fab")'
    f' (uuid "{uid("M", "warning")}") {font(1)})')  # fmt: skip
texts = " ".join(
    f'(fp_text user "${{{var}}}" (at 0 {3 + 1.5 * i} 0) (layer "F.Fab") (uuid "{uid("F", var)}")'
    f" {font(1)})"
    for i, var in enumerate(FOOTPRINT)
)
footprint("R1", (60, 60), [smd("R1", "1", (0, 0), (1, 1), "N1")], courtyard="none",
          body=f'(property "MPN" "ABC-1" (at 0 -5 0) (layer "F.Fab") (hide yes)'
               f' (uuid "{uid("R1", "mpn")}") {font(1)}) ' + texts)  # fmt: skip

write(
    HERE / "drc3", 12, 8,
    severities={
        "track_dangling": "ignore", "via_dangling": "ignore", "unconnected_items": "ignore",
        "missing_courtyard": "ignore", "lib_footprint_issues": "ignore",
        "lib_footprint_mismatch": "ignore", "silk_overlap": "ignore",
        "solder_mask_bridge": "ignore", "copper_edge_clearance": "ignore",
    },
)  # fmt: skip
project = HERE / "drc3" / "drc3.kicad_pro"
data = json.loads(project.read_text(encoding="utf-8"))
data["text_variables"] = {"MYVAR": "hello"}
project.write_bytes((json.dumps(data, indent=2) + "\n").encode("utf-8"))
