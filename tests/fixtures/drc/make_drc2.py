"""Write drc2/: a board asking KiCad's DRC about clearance, one question per
case: which clearance two items are held to -- their net classes', the board
minimum, a pad's or a footprint's own, a zone's -- and how it is measured;
when copper touching is a short, and when it is not asked about at all; how
far copper must keep from a hole and from the board's edge. Every case has
nets of its own, ten millimetres from the next.

Net classes: Default 0.2 mm; Wide 0.4 mm (nets W*); Tight 0.1 mm (nets T*).
Board minimum clearance 0.12 mm, hole clearance 0.25 mm, edge clearance
0.5 mm. Dangling copper, connections and courtyards are not what is asked,
so those checks are off. The answers are recorded in `drc2/drc2.kicad.json`;
to change the board, edit this, run it, and record KiCad's answers again.
"""

import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from boardgen import (  # noqa: E402
    add,
    arc,
    cell,
    footprint,
    npth,
    rect,
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

start("drc2", uuid.UUID("7a1c5d2e-0000-4000-8000-0000000d4c02"))


def pair(key, col, row, gap, net_a, net_b, width=0.25, layer="F.Cu"):
    """Two parallel tracks `gap` apart, edge to edge."""
    x, y = cell(col, row)
    pitch = gap + width
    segment(key, (x - 3, y), (x + 3, y), width, layer, net_a)
    segment(key, (x - 3, y + pitch), (x + 3, y + pitch), width, layer, net_b)


def anchor(key, x, y, net, layers=("F.Cu", "F.Mask")):
    """A pad of the net: copper that reaches a pad is on its net for good."""
    footprint(key, (x, y), [smd(key, "1", (0, 0), (0.6, 0.6), net, layers=layers)],
              courtyard="none")  # fmt: skip


# -- row 0: which clearance two tracks are held to, and how closely ------------------------

pair("CA1", 0, 0, 0.15, "CA1a", "CA1b")  # Default and Default: 0.2
pair("CA2", 1, 0, 0.2, "CA2a", "CA2b")  # exactly 0.2
pair("CA3", 2, 0, 0.1995, "CA3a", "CA3b")  # 0.5 um short: within KiCad's tolerance
pair("CA4", 3, 0, 0.3, "Wa4", "CA4b")  # Wide and Default: 0.4
pair("CA5", 4, 0, 0.11, "Ta5", "Tb5")  # Tight and Tight: the board's 0.12
pair("CA6", 5, 0, 0.15, "Ta6", "CA6b")  # Tight and Default: 0.2
pair("CA7", 6, 0, 0.125, "Ta7", "Tb7")  # Tight and Tight, over the board's minimum
pair("CA8", 7, 0, 0.15, "CA8a", "CA8b", layer="In1.Cu")  # on an inner layer
x, y = cell(8, 0)  # an arc bending towards a track
arc("CA9", (x - 3, y), (x, y + 1.0), (x + 3, y), 0.25, "F.Cu", "CA9a")
segment("CA9", (x - 3, y + 1.4), (x + 3, y + 1.4), 0.25, "F.Cu", "CA9b")  # 0.15 at the arc's mid
pair("CA10", 9, 0, 0.1994, "CA10a", "CA10b")  # 0.6 um short: reported

# -- row 1: pads, and their own clearances -------------------------------------------------

x, y = cell(0, 1)  # a pad and a track
footprint("PA1", (x, y), [smd("PA1", "1", (0, 0), (1, 1), "PA1a")])
segment("PA1", (x - 3, y + 0.775), (x + 3, y + 0.775), 0.25, "F.Cu", "PA1b")
x, y = cell(1, 1)  # a pad asking 0.5 of its own
footprint("PA2", (x, y), [smd("PA2", "1", (0, 0), (1, 1), "PA2a", extra=" (clearance 0.5)")])
segment("PA2", (x - 3, y + 0.925), (x + 3, y + 0.925), 0.25, "F.Cu", "PA2b")
x, y = cell(2, 1)  # a pad asking less than its class
footprint("PA3", (x, y), [smd("PA3", "1", (0, 0), (1, 1), "PA3a", extra=" (clearance 0.05)")])
segment("PA3", (x - 3, y + 0.775), (x + 3, y + 0.775), 0.25, "F.Cu", "PA3b")
x, y = cell(3, 1)  # a footprint asking 0.5 of its own
footprint("PA4", (x, y), [smd("PA4", "1", (0, 0), (1, 1), "PA4a")], body="(clearance 0.5)")
segment("PA4", (x - 3, y + 0.925), (x + 3, y + 0.925), 0.25, "F.Cu", "PA4b")
x, y = cell(4, 1)  # two pads of one footprint
footprint("PA5", (x, y), [smd("PA5", "1", (-0.55, 0), (1, 1), "PA5a"),
                          smd("PA5", "2", (0.55, 0), (1, 1), "PA5b")])  # fmt: skip
x, y = cell(5, 1)  # a pad on no net
footprint("PA6", (x, y), [smd("PA6", "1", (0, 0), (1, 1))])
segment("PA6", (x - 3, y + 0.725), (x + 3, y + 0.725), 0.25, "F.Cu", "PA6b")
x, y = cell(6, 1)  # a plated pad and an inner track
footprint("PA7", (x, y), [tht("PA7", "1", (0, 0), (1.6, 1.6), 0.8, "PA7a")],
          attr="through_hole")  # fmt: skip
segment("PA7", (x - 3, y + 1.075), (x + 3, y + 1.075), 0.25, "In1.Cu", "PA7b")
x, y = cell(7, 1)  # the same pad without its unused inner copper
footprint("PA8", (x, y), [
    tht("PA8", "1", (0, 0), (1.6, 1.6), 0.8, "PA8a").replace(
        "(remove_unused_layers no)", "(remove_unused_layers yes)")
], attr="through_hole")  # fmt: skip
segment("PA8", (x - 3, y + 1.075), (x + 3, y + 1.075), 0.25, "In1.Cu", "PA8b")
x, y = cell(8, 1)  # an arc passing a round pad
footprint("PA9", (x, y), [smd("PA9", "1", (0, 0), (1, 1), "PA9a", shape="circle")])
arc("PA9", (x - 3, y + 2), (x, y + 0.775), (x + 3, y + 2), 0.25, "F.Cu", "PA9b")

# -- row 2: vias ---------------------------------------------------------------------------

x, y = cell(0, 2)  # a via and a track
via("VA1", (x, y), 0.6, 0.3, "VA1a")
segment("VA1", (x - 3, y + 0.575), (x + 3, y + 0.575), 0.25, "F.Cu", "VA1b")
x, y = cell(1, 2)  # two vias
via("VA2", (x - 0.375, y), 0.6, 0.3, "VA2a")
via("VA2", (x + 0.375, y), 0.6, 0.3, "VA2b")
x, y = cell(2, 2)  # a via without its unused inner copper, and an inner track
add(
    f'(via (at {xy((x, y))}) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu")'
    f' (remove_unused_layers yes) (net "VA3a") (uuid "{uid("VA3", "via")}"))'
)
segment("VA3", (x - 3, y + 0.525), (x + 3, y + 0.525), 0.25, "In1.Cu", "VA3b")
x, y = cell(3, 2)  # a via of one net touching another's track from the side
anchor("VB1b", x - 3, y, "VB1b")
segment("VB1", (x - 3, y), (x + 3, y), 0.25, "F.Cu", "VB1b")
anchor("VB1a", x, y + 3, "VB1a", ("B.Cu", "B.Mask"))
segment("VB1", (x, y + 3), (x, y + 0.3), 0.25, "B.Cu", "VB1a")
via("VB1", (x, y + 0.3), 0.6, 0.3, "VB1a")
x, y = cell(4, 2)  # a via at the end of another net's track
anchor("VB2b", x - 3, y, "VB2b")
segment("VB2", (x - 3, y), (x, y), 0.25, "F.Cu", "VB2b")
anchor("VB2a", x, y + 3, "VB2a", ("B.Cu", "B.Mask"))
segment("VB2", (x, y + 3), (x, y), 0.25, "B.Cu", "VB2a")
via("VB2", (x, y), 0.6, 0.3, "VB2a")
x, y = cell(5, 2)  # a via on another net's pad
anchor("VB3b", x, y, "VB3b")
anchor("VB3a", x, y + 3, "VB3a", ("B.Cu", "B.Mask"))
segment("VB3", (x, y + 3), (x, y), 0.25, "B.Cu", "VB3a")
via("VB3", (x, y), 0.6, 0.3, "VB3a")
x, y = cell(6, 2)  # a via crossing another net's track on its other side
anchor("VB5b", x - 3, y, "VB5b", ("B.Cu", "B.Mask"))
segment("VB5", (x - 3, y), (x + 3, y), 0.25, "B.Cu", "VB5b")
anchor("VB5a", x, y - 3, "VB5a")
segment("VB5", (x, y - 3), (x, y), 0.25, "F.Cu", "VB5a")
via("VB5", (x, y), 0.6, 0.3, "VB5a")

# -- row 3: copper that touches, on nets pads have ------------------------------------------

x, y = cell(0, 3)  # two tracks crossing
anchor("SH1a", x - 2, y - 2, "SH1a")
anchor("SH1b", x - 2, y + 2, "SH1b")
segment("SH1", (x - 2, y - 2), (x + 2, y + 2), 0.25, "F.Cu", "SH1a")
segment("SH1", (x - 2, y + 2), (x + 2, y - 2), 0.25, "F.Cu", "SH1b")
x, y = cell(1, 3)  # a track ending on another net's pad
anchor("SH2a", x, y, "SH2a")
anchor("SH2b", x - 3, y, "SH2b")
segment("SH2", (x - 3, y), (x, y), 0.25, "F.Cu", "SH2b")
x, y = cell(2, 3)  # two tracks overlapping along their length
anchor("SH3a", x - 3, y, "SH3a")
anchor("SH3b", x + 3, y, "SH3b")
segment("SH3", (x - 3, y), (x + 1, y), 0.25, "F.Cu", "SH3a")
segment("SH3", (x - 1, y + 0.1), (x + 3, y), 0.25, "F.Cu", "SH3b")
x, y = cell(3, 3)  # a footprint's copper line on its own pad of no net
footprint("SH4", (x, y), [smd("SH4", "1", (0, 0), (1, 1))],
          body=f'(fp_line (start 0 0) (end 2 0) (stroke (width 0.2) (type solid)) (layer "F.Cu")'
               f' (uuid "{uid("SH4", "line")}"))')  # fmt: skip
x, y = cell(4, 3)  # a footprint's copper line on its own pad of a net
footprint("SH5", (x, y), [smd("SH5", "1", (0, 0), (1, 1), "SH5a")],
          body=f'(fp_line (start 0 0) (end 2 0) (stroke (width 0.2) (type solid)) (layer "F.Cu")'
               f' (uuid "{uid("SH5", "line")}"))')  # fmt: skip
x, y = cell(5, 3)  # two pads of two nets overlapping
footprint("SH6", (x, y), [smd("SH6", "1", (-0.3, 0), (1, 1), "SH6a"),
                          smd("SH6", "2", (0.3, 0), (1, 1), "SH6b")])  # fmt: skip
x, y = cell(6, 3)  # two tracks of no net crossing
add(f'(segment (start {xy((x - 2, y - 2))}) (end {xy((x + 2, y + 2))}) (width 0.25)'
    f' (layer "F.Cu") (uuid "{uid("SH7", "a")}"))')  # fmt: skip
add(f'(segment (start {xy((x - 2, y + 2))}) (end {xy((x + 2, y - 2))}) (width 0.25)'
    f' (layer "F.Cu") (uuid "{uid("SH7", "b")}"))')  # fmt: skip
x, y = cell(7, 3)  # a track through another net's plated pad
footprint("SH8", (x, y), [tht("SH8", "1", (0, 0), (1.6, 1.6), 0.8, "SH8a")],
          attr="through_hole", courtyard="none")  # fmt: skip
anchor("SH8b", x - 3, y, "SH8b")
segment("SH8", (x - 3, y), (x + 3, y), 0.25, "F.Cu", "SH8b")
x, y = cell(8, 3)  # a track touching two pads of two nets
anchor("SH9a", x - 2, y, "SH9a")
anchor("SH9b", x + 2, y, "SH9b")
segment("SH9", (x - 2, y), (x + 2, y), 0.25, "F.Cu", "SH9c")

# -- row 4: copper no pad holds, touching other nets --------------------------------------

x, y = cell(0, 4)  # two padless tracks crossing
segment("PL1", (x - 2, y - 2), (x + 2, y + 2), 0.25, "F.Cu", "PL1a")
segment("PL1", (x - 2, y + 2), (x + 2, y - 2), 0.25, "F.Cu", "PL1b")
x, y = cell(1, 4)  # a padless track crossing a track with a pad
anchor("PL2a", x - 2, y - 2, "PL2a")
segment("PL2", (x - 2, y - 2), (x + 2, y + 2), 0.25, "F.Cu", "PL2a")
segment("PL2", (x - 2, y + 2), (x + 2, y - 2), 0.25, "F.Cu", "PL2b")
x, y = cell(2, 4)  # a padless track over a pad, and 0.15 from that pad's track
anchor("PL3a", x - 3, y, "PL3a")
segment("PL3", (x - 3, y), (x + 3, y), 0.25, "F.Cu", "PL3a")
segment("PL3", (x - 3, y + 0.4), (x + 3, y + 0.4), 0.25, "F.Cu", "PL3b")
x, y = cell(3, 4)  # padless via and track of two nets, the via's copper touching the track
segment("PL4", (x - 3, y), (x, y), 0.25, "F.Cu", "PL4a")
via("PL4", (x + 0.2, y), 0.6, 0.3, "PL4b")
segment("PL4", (x + 0.725, y - 2), (x + 0.725, y + 2), 0.25, "F.Cu", "PL4c")
x, y = cell(4, 4)  # a padless track on a pad of no net
anchor("PL5", x, y, None)
segment("PL5", (x, y), (x + 3, y), 0.25, "F.Cu", "PL5a")
x, y = cell(5, 4)  # a padless via on a track that has a pad
anchor("PL6a", x - 3, y, "PL6a")
segment("PL6", (x - 3, y), (x + 3, y), 0.25, "F.Cu", "PL6a")
segment("PL6", (x + 3, y + 3), (x, y), 0.25, "B.Cu", "PL6b")
via("PL6", (x, y), 0.6, 0.3, "PL6b")

# -- row 5: zones, filled as the file says -------------------------------------------------

x, y = cell(0, 5)  # a zone asking 0.5, a track 0.3 from its fill
fill = rect(x - 3, y - 3, 3, 6)
zone("ZA1", "ZA1a", ["F.Cu"], fill, clearance=0.5, fill=[fill])
segment("ZA1", (x + 0.425, y - 3), (x + 0.425, y + 3), 0.25, "F.Cu", "ZA1b")
x, y = cell(1, 5)  # a zone asking 0.1, a Wide track 0.3 from it
fill = rect(x - 3, y - 3, 3, 6)
zone("ZA2", "ZA2a", ["F.Cu"], fill, clearance=0.1, fill=[fill])
segment("ZA2", (x + 0.425, y - 3), (x + 0.425, y + 3), 0.25, "F.Cu", "Wa2b")
x, y = cell(2, 5)  # a zone asking 0.1, a track 0.15 from it
fill = rect(x - 3, y - 3, 3, 6)
zone("ZA3", "ZA3a", ["F.Cu"], fill, clearance=0.1, fill=[fill])
segment("ZA3", (x + 0.275, y - 3), (x + 0.275, y + 3), 0.25, "F.Cu", "ZA3b")
x, y = cell(3, 5)  # a track across another net's fill
fill = rect(x - 3, y - 3, 3, 6)
zone("ZA4", "ZA4a", ["F.Cu"], fill, clearance=0.1, fill=[fill])
segment("ZA4", (x - 4, y), (x + 1, y), 0.25, "F.Cu", "ZA4b")
x, y = cell(4, 5)  # two nets' fills overlapping
fill = rect(x - 3, y - 3, 3, 6)
zone("ZA5a", "ZA5a", ["F.Cu"], fill, clearance=0.1, fill=[fill])
fill = rect(x - 1, y - 3, 3, 6)
zone("ZA5b", "ZA5b", ["F.Cu"], fill, clearance=0.1, fill=[fill], priority=1)
x, y = cell(5, 5)  # a pad of a net inside another net's fill
fill = rect(x - 3, y - 3, 6, 6)
zone("ZA6", "ZA6a", ["F.Cu"], fill, clearance=0.3, fill=[fill])
anchor("ZA6b", x, y, "ZA6b")

# -- row 6: holes --------------------------------------------------------------------------

x, y = cell(0, 6)  # an unplated hole and a track 0.2 from it
footprint("HO1", (x, y), [npth("HO1", "", (0, 0), 1)], attr="through_hole")
segment("HO1", (x - 3, y + 0.825), (x + 3, y + 0.825), 0.25, "F.Cu", "HO1b")
x, y = cell(1, 6)  # the same, 0.3 from it
footprint("HO2", (x, y), [npth("HO2", "", (0, 0), 1)], attr="through_hole")
segment("HO2", (x - 3, y + 0.925), (x + 3, y + 0.925), 0.25, "F.Cu", "HO2b")
x, y = cell(2, 6)  # a plated pad, thin ring, a track clear of its copper but not its hole
footprint("HO3", (x, y), [tht("HO3", "1", (0, 0), (1.2, 1.2), 1.0, "HO3a")],
          attr="through_hole")  # fmt: skip
segment("HO3", (x - 3, y + 0.935), (x + 3, y + 0.935), 0.25, "F.Cu", "HO3b")
x, y = cell(3, 6)  # an unplated hole 0.2 from a pad of another footprint
footprint("HO4", (x, y), [npth("HO4", "", (0, 0), 1)], attr="through_hole")
footprint("HO4b", (x + 1.2, y), [smd("HO4b", "1", (0, 0), (1, 1), "HO4b")], courtyard="none")
for n, d in enumerate((0.7, 0.8, 0.85, 0.9, 0.95)):  # an unplated hole and a via ever further
    x, y = cell(4 + n, 6)
    footprint(f"HN{n}", (x, y), [npth(f"HN{n}", "", (0, 0), 1)], attr="through_hole",
              courtyard="none")  # fmt: skip
    via(f"HN{n}", (x + d, y), 0.6, 0.3, f"HN{n}v")
x, y = cell(9, 6)  # a via's hole and another net's track, where the via has no copper
add(
    f'(via (at {xy((x, y))}) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu")'
    f' (remove_unused_layers yes) (net "HO6a") (uuid "{uid("HO6", "via")}"))'
)
segment("HO6", (x - 3, y + 0.475), (x + 3, y + 0.475), 0.25, "In1.Cu", "HO6b")
x, y = cell(0, 7)  # a plated pad's hole and another net's track, where the pad has no copper
footprint("HO7", (x, y), [tht("HO7", "1", (0, 0), (1.0, 1.0), 0.8, "HO7a").replace(
    "(remove_unused_layers no)", "(remove_unused_layers yes)")], attr="through_hole",
    courtyard="none")  # fmt: skip
segment("HO7", (x - 3, y + 0.725), (x + 3, y + 0.725), 0.25, "In1.Cu", "HO7b")
x, y = cell(1, 7)  # a plated pad's hole and a pad on the far side of the board
footprint("HO8", (x, y), [tht("HO8", "1", (0, 0), (1.0, 1.0), 0.8, "HO8a")],
          attr="through_hole", courtyard="none")  # fmt: skip
footprint("HO8b", (x, y + 0.9), [smd("HO8b", "1", (0, 0), (0.6, 0.6), "HO8b",
                                     layers=("B.Cu", "B.Mask"))], courtyard="none")  # fmt: skip

# -- rows 8 and 9: the board's edge --------------------------------------------------------

EDGE = 10  # the outline's left side
for n, (gap, kind) in enumerate(((0.3, "track"), (0.6, "track"), (0.4, "pad"))):
    y = 20 + 10 * 8 + 3 * n
    if kind == "track":
        segment(f"ED{n}", (EDGE + gap + 0.125, y), (EDGE + 5, y), 0.25, "F.Cu", f"ED{n}")
    else:
        footprint(f"ED{n}", (EDGE + gap + 0.5, y), [smd(f"ED{n}", "1", (0, 0), (1, 1), f"ED{n}")],
                  courtyard="none")  # fmt: skip
x, y = cell(1, 8)  # an Edge.Cuts slot inside the board, drawn 0.5 wide
add(f'(gr_line (start {xy((x, y - 2))}) (end {xy((x, y + 2))}) (stroke (width 0.5) (type solid))'
    f' (layer "Edge.Cuts") (uuid "{uid("ED4", "slot")}"))')  # fmt: skip
add(
    f"(gr_line (start {xy((x + 1, y - 2))}) (end {xy((x + 1, y + 2))})"
    f' (stroke (width 0.5) (type solid)) (layer "Edge.Cuts") (uuid "{uid("ED4", "slot2")}"))'
)
add(
    f"(gr_arc (start {xy((x, y - 2))}) (mid {xy((x + 0.5, y - 2.5))}) (end {xy((x + 1, y - 2))})"
    f' (stroke (width 0.5) (type solid)) (layer "Edge.Cuts") (uuid "{uid("ED4", "slot3")}"))'
)
add(
    f"(gr_arc (start {xy((x, y + 2))}) (mid {xy((x + 0.5, y + 2.5))}) (end {xy((x + 1, y + 2))})"
    f' (stroke (width 0.5) (type solid)) (layer "Edge.Cuts") (uuid "{uid("ED4", "slot4")}"))'
)
segment("ED4", (x - 0.6, y - 1), (x - 0.6, y + 1), 0.25, "F.Cu", "ED4")  # 0.6 from the centre
x, y = cell(2, 8)  # a footprint's cut-out on Edge.Cuts, a track 0.2 from it
footprint("ED5", (x, y), [], courtyard="none",
          body=f'(fp_rect (start -1 -1) (end 1 1) (stroke (width 0.05) (type solid)) (fill no)'
               f' (layer "Edge.Cuts") (uuid "{uid("ED5", "cut")}"))')  # fmt: skip
segment("ED5", (x + 1.325, y - 2), (x + 1.325, y + 2), 0.25, "F.Cu", "ED5")
y = 20 + 10 * 9
fill = [(EDGE - 1, y - 2), (EDGE + 2, y - 2), (EDGE + 2, y + 2), (EDGE - 1, y + 2)]
zone("ED6", "ED6", ["F.Cu"], fill, fill=[fill])  # a zone's fill over the board's edge
x, y = cell(3, 8)  # a via 0.3 from the edge of a footprint's cut-out
footprint("ED7", (x, y), [], courtyard="none",
          body=f'(fp_circle (center 0 0) (end 1 0) (stroke (width 0.05) (type solid)) (fill no)'
               f' (layer "Edge.Cuts") (uuid "{uid("ED7", "cut")}"))')  # fmt: skip
via("ED7", (x + 1.6, y), 0.6, 0.3, "ED7")

write(
    HERE / "drc2", 10, 9,
    severities={
        "track_dangling": "ignore", "via_dangling": "ignore", "unconnected_items": "ignore",
        "courtyards_overlap": "ignore", "missing_courtyard": "ignore",
        "lib_footprint_issues": "ignore", "lib_footprint_mismatch": "ignore",
        "silk_overlap": "ignore", "silk_over_copper": "ignore", "silk_edge_clearance": "ignore",
        "isolated_copper": "ignore", "starved_thermal": "ignore", "solder_mask_bridge": "ignore",
        "npth_inside_courtyard": "ignore", "pth_inside_courtyard": "ignore",
        "padstack": "ignore", "annular_width": "ignore", "hole_to_hole": "ignore",
        "holes_co_located": "ignore",
    },
    rules={"min_clearance": 0.12, "min_hole_clearance": 0.25, "min_copper_edge_clearance": 0.5},
    net_settings={
        "classes": [
            {"name": "Default", "clearance": 0.2, "track_width": 0.2, "via_diameter": 0.6,
             "via_drill": 0.3, "microvia_diameter": 0.3, "microvia_drill": 0.1,
             "diff_pair_width": 0.2, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
             "priority": 2147483647, "bus_width": 12, "line_style": 0, "wire_width": 6,
             "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)"},
            {"name": "Wide", "clearance": 0.4, "priority": 0},
            {"name": "Tight", "clearance": 0.1, "priority": 1},
        ],
        "netclass_patterns": [
            {"pattern": "W*", "netclass": "Wide"},
            {"pattern": "T*", "netclass": "Tight"},
        ],
    },
)  # fmt: skip
