"""Write zonefill/: a board of small zones, one question about filling each,
for KiCad to fill -- the fills this tool's are held to.

To change it: edit this, run it, then have KiCad fill the zones
(`kicad-cli pcb drc --refill-zones --save-board zonefill/zonefill.kicad_pcb`)
and keep what it saves.
"""

import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "drc"))
from boardgen import (  # noqa: E402
    add,
    arc,
    cell,
    footprint,
    npth,
    segment,
    smd,
    start,
    tht,
    uid,
    via,
    write,
    xy,
    zone,
)

start("zonefill", uuid.UUID("7a1c5d2e-0000-4000-8000-0000000f1001"))


def square(x, y, half=3.0):
    return [(x - half, y - half), (x + half, y - half), (x + half, y + half), (x - half, y + half)]


def boxed(col, row, key, add_items, net="GND"):
    """A zone of `net` 6 mm square round a cell, and what `add_items` puts in it."""
    x, y = cell(col, row)
    zone(f"z{key}", net, ["F.Cu"], square(x, y))
    add_items(x, y)


# -- row 0: copper of another net -------------------------------------------------------------

boxed(0, 0, "rect", lambda x, y: footprint("U1", (x, y), [smd("p1", "1", (0, 0), (1, 1), "SIG")]))
boxed(1, 0, "round", lambda x, y: footprint(
    "U2", (x, y), [smd("p2", "1", (0, 0), (1, 1), "SIG", shape="circle")]))  # fmt: skip
boxed(2, 0, "rrect", lambda x, y: footprint(
    "U3", (x, y), [smd("p3", "1", (0, 0), (1.2, 0.8), "SIG", shape="roundrect",
                       extra=" (roundrect_rratio 0.25)")]))  # fmt: skip
boxed(3, 0, "oval", lambda x, y: footprint(
    "U4", (x, y), [smd("p4", "1", (0, 0, 30), (1.6, 0.8), "SIG", shape="oval")]))  # fmt: skip
boxed(
    4, 0, "track", lambda x, y: segment("t5", (x - 1, y - 1), (x + 1, y + 0.5), 0.25, "F.Cu", "SIG")
)
boxed(
    5,
    0,
    "arc",
    lambda x, y: arc("a6", (x - 1.5, y), (x, y - 1.2), (x + 1.5, y), 0.2, "F.Cu", "SIG"),
)
boxed(6, 0, "via", lambda x, y: via("v7", (x, y), 0.6, 0.3, "SIG"))
boxed(7, 0, "npth", lambda x, y: footprint("H8", (x, y), [npth("h8", "", (0, 0), 1.0)],
                                           attr="through_hole"))  # fmt: skip

# -- row 1: the zone's own net ----------------------------------------------------------------

boxed(
    0, 1, "thermal", lambda x, y: footprint("U11", (x, y), [smd("p11", "1", (0, 0), (1, 1), "GND")])
)


def own_pad(ref, connect=""):
    """A pad of the zone's net, joined as `connect` says, or as the zone does."""
    extra = f" (zone_connect {connect})" if connect else ""
    return lambda x, y: footprint(
        ref, (x, y), [smd(f"p{ref}", "1", (0, 0), (1, 1), "GND", extra=extra)]
    )


def own_tht(x, y):
    footprint("U12", (x, y), [tht("p12", "1", (0, 0), (1.5, 1.5), 0.8, "GND")], attr="through_hole")


boxed(1, 1, "tht", own_tht)
boxed(2, 1, "solid", own_pad("U13", "2"))
boxed(3, 1, "none", own_pad("U14", "0"))


def pair(x, y):
    """Two through-hole pads of the net side by side: their reliefs meet."""
    footprint("U15", (x, y), [tht("p15a", "1", (-1.27, 0), (1.6, 1.6), 0.8, "GND"),
                              tht("p15b", "2", (1.27, 0), (1.6, 1.6), 0.8, "GND")],
              attr="through_hole")  # fmt: skip


boxed(4, 1, "pair", pair)


def narrow(x, y):
    """Two pads of another net leaving a neck of fill narrower than the
    zone's minimum thickness between them."""
    footprint("U16", (x, y), [smd("p16a", "1", (-0.9, 0), (0.6, 2), "SIG"),
                              smd("p16b", "2", (0.9, 0), (0.6, 2), "SIG")])  # fmt: skip


boxed(5, 1, "neck", narrow)


def own_clearance(x, y):
    """A footprint of its own clearance, smaller than the zone's (written in
    below)."""
    footprint("U17", (x, y), [smd("p17", "1", (0, 0), (1, 1), "SIG")])


boxed(6, 1, "own", own_clearance)


def drawn(x, y):
    """A line drawn on the copper layer, of no net."""
    add(f'(gr_line (start {xy((x - 1.5, y - 1))}) (end {xy((x + 1.5, y + 1))}) '
        f'(stroke (width 0.3) (type solid)) (layer "F.Cu") (uuid "{uid("line18")}"))')  # fmt: skip


boxed(7, 1, "drawn", drawn)

# -- row 2: zones among themselves, islands, the board's edge --------------------------------

x, y = cell(0, 2)  # a zone of another net, of a higher priority, over part of this one
zone("z21a", "GND", ["F.Cu"], square(x, y))
zone("z21b", "VCC", ["F.Cu"], [(x, y - 1.5), (x + 3, y - 1.5), (x + 3, y + 1.5), (x, y + 1.5)],
     priority=1)  # fmt: skip
footprint("U21", (x + 2, y), [smd("p21", "1", (0, 0), (0.6, 0.6), "VCC")])
x, y = cell(1, 2)  # a keep-out across the zone
zone("z22", "GND", ["F.Cu"], square(x, y))
zone("k22", None, ["F.Cu"], [(x - 0.5, y - 3.5), (x + 0.5, y - 3.5), (x + 0.5, y + 3.5),
                             (x - 0.5, y + 3.5)], keepout=True)  # fmt: skip
x, y = cell(2, 2)  # a ring of track of another net leaves an island inside it
zone("z23", "GND", ["F.Cu"], square(x, y))
ring = [(x - 1.5, y - 1.5), (x + 1.5, y - 1.5), (x + 1.5, y + 1.5), (x - 1.5, y + 1.5)]
for k in range(4):
    segment(f"r23{k}", ring[k], ring[(k + 1) % 4], 0.25, "F.Cu", "SIG")
footprint("U23", (x - 2.5, y - 2.5), [smd("p23", "1", (0, 0), (0.5, 0.5), "GND")])
x, y = cell(3, 2)  # the same, the zone keeping its islands
zone("z24", "GND", ["F.Cu"], square(x, y))  # keeping its islands: written in below
ring = [(x - 1.5, y - 1.5), (x + 1.5, y - 1.5), (x + 1.5, y + 1.5), (x - 1.5, y + 1.5)]
for k in range(4):
    segment(f"r24{k}", ring[k], ring[(k + 1) % 4], 0.25, "F.Cu", "SIG")
footprint("U24", (x - 2.5, y - 2.5), [smd("p24", "1", (0, 0), (0.5, 0.5), "GND")])

out = HERE / "zonefill"
QUIET = {"unconnected_items": "ignore", "track_dangling": "ignore",
         "lib_footprint_issues": "ignore", "via_dangling": "ignore"}  # fmt: skip
write(out, 8, 3, severities=QUIET)
# A footprint's own clearance: written into its node after the fact.
board = out / "zonefill.kicad_pcb"
text = board.read_text(encoding="utf-8")
marker = '(property "Reference" "U17"'
at = text.index(marker)
text = text[:at] + "(clearance 0.2) " + text[at:]
# The zone keeping its islands.
at = text.index(uid("z24", "zone"))
fill = text.index("(fill (thermal_gap 0.5) (thermal_bridge_width 0.5))", at)
text = (
    text[:fill]
    + "(fill (thermal_gap 0.5) (thermal_bridge_width 0.5) (island_removal_mode 1))"
    + text[fill + len("(fill (thermal_gap 0.5) (thermal_bridge_width 0.5))") :]
)
board.write_bytes(text.encode("utf-8"))
