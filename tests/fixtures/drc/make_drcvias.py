"""Write drcvias/: when KiCad calls a via dangling.

Each case is a via with what it meets: a through-hole pad, a surface pad on
either side, another via, a zone's fill, tracks on the same layer or on
two. KiCad counts each thing a via meets on one layer only, the topmost --
so a via on a through-hole pad with a track on the top dangles, and with one
on the bottom does not. The answers are recorded in
`drcvias/drcvias.kicad.json` (English).
"""

import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from boardgen import cell, footprint, rect, segment, smd, start, tht, via, write, zone  # noqa: E402

start("drcvias", uuid.UUID("7a1c5d2e-0000-4000-8000-0000000d4cb1"))


def two(ref, col, row, kind="tht"):
    """A footprint of two pads of one net, 4 mm apart."""
    x, y = cell(col, row)
    if kind == "tht":
        pads = [
            tht(ref, "1", (-2, 0), (1.7, 1.7), 1.0, ref),
            tht(ref, "2", (2, 0), (1.7, 1.7), 1.0, ref),
        ]
        attr = "through_hole"
    else:
        pads = [smd(ref, "1", (-2, 0), (1.7, 1.7), ref), smd(ref, "2", (2, 0), (1.7, 1.7), ref)]
        attr = "smd"
    footprint(ref, (x, y), pads, attr=attr, courtyard="none")
    return x, y


# W1: via on a through-hole pad, a track on B.Cu from it to the other pad
x, y = two("W1", 0, 0)
via("W1", (x - 1.45, y), 0.6, 0.3, "W1")
segment("W1", (x - 1.45, y), (x + 2, y), 0.25, "B.Cu", "W1")
# W2: a via with a track on F.Cu to a pad, nothing else
x, y = two("W2", 1, 0)
via("W2", (x, y + 2), 0.6, 0.3, "W2")
segment("W2", (x, y + 2), (x - 2, y), 0.25, "F.Cu", "W2")
# W3: a via with two tracks on F.Cu, one to each pad
x, y = two("W3", 2, 0)
via("W3", (x, y + 2), 0.6, 0.3, "W3")
segment("W3a", (x, y + 2), (x - 2, y), 0.25, "F.Cu", "W3")
segment("W3b", (x, y + 2), (x + 2, y), 0.25, "F.Cu", "W3")
# W4: a via inside a surface pad, nothing else
x, y = two("W4", 3, 0, kind="smd")
via("W4", (x - 2, y), 0.6, 0.3, "W4")
# W5: a via inside a surface pad, a track on B.Cu to a via at the other pad
x, y = two("W5", 4, 0, kind="smd")
via("W5a", (x - 2, y), 0.6, 0.3, "W5")
via("W5b", (x + 2, y), 0.6, 0.3, "W5")
segment("W5", (x - 2, y), (x + 2, y), 0.25, "B.Cu", "W5")
# W6: a via on a through-hole pad, a track on F.Cu to the other pad
x, y = two("W6", 5, 0)
via("W6", (x - 1.45, y), 0.6, 0.3, "W6")
segment("W6", (x - 1.45, y), (x + 2, y), 0.25, "F.Cu", "W6")
# W7: a via with a track on F.Cu and one on B.Cu, both to the same pad
x, y = two("W7", 6, 0)
via("W7", (x - 2, y + 2), 0.6, 0.3, "W7")
segment("W7a", (x - 2, y + 2), (x - 2, y), 0.25, "F.Cu", "W7")
segment("W7b", (x - 2, y + 2), (x - 2, y), 0.25, "B.Cu", "W7")
# W8: a via inside a surface pad, a track on F.Cu from the pad
x, y = two("W8", 7, 0, kind="smd")
via("W8", (x - 2, y), 0.6, 0.3, "W8")
segment("W8", (x - 2, y), (x + 2, y), 0.25, "F.Cu", "W8")
# W9: two vias touching each other, each with nothing else
x, y = two("W9", 8, 0)
via("W9a", (x, y + 2), 0.6, 0.3, "W9")
via("W9b", (x + 0.5, y + 2), 0.6, 0.3, "W9")
# W10: a via on a through-hole pad's centre (in its hole)
x, y = two("W10", 9, 0)
via("W10", (x - 2, y), 0.6, 0.3, "W10")

# W11: a via touching another via, and a track on B.Cu from it to a pad
x, y = two("W11", 0, 1)
via("W11a", (x - 2, y + 2), 0.6, 0.3, "W11")
via("W11b", (x - 1.5, y + 2), 0.6, 0.3, "W11")
segment("W11", (x - 2, y + 2), (x + 2, y), 0.25, "B.Cu", "W11")
# W12: a via on a through-hole pad, a track on In1.Cu to the other pad
x, y = two("W12", 1, 1)
via("W12", (x - 1.45, y), 0.6, 0.3, "W12")
segment("W12", (x - 1.45, y), (x + 2, y), 0.25, "In1.Cu", "W12")
# W13: a via on a surface pad on B.Cu, a track on F.Cu from it to the other pad
x, y = cell(2, 1)
back = ("B.Cu", "B.Mask", "B.Paste")
footprint("W13", (x, y), [smd("W13", "1", (-2, 0), (1.7, 1.7), "W13", layers=back),
                          tht("W13", "2", (2, 0), (1.7, 1.7), 1.0, "W13")],
          attr="smd", courtyard="none")  # fmt: skip
via("W13", (x - 2, y), 0.6, 0.3, "W13")
segment("W13", (x - 2, y), (x + 2, y), 0.25, "F.Cu", "W13")
# W14: a via on a through-hole pad, a track on B.Cu that ends at the via only
x, y = two("W14", 3, 1)
via("W14", (x - 1.45, y), 0.6, 0.3, "W14")
segment("W14", (x - 1.45, y), (x - 1.45, y + 3), 0.25, "B.Cu", "W14")

# W15: a via in a zone of its net on F.Cu, nothing else
x, y = two("W15", 4, 1)
zone("W15", "W15", ["F.Cu"], rect(x - 3, y + 1, 6, 3), fill=[rect(x - 3, y + 1, 6, 3)])
via("W15", (x, y + 2.5), 0.6, 0.3, "W15")
# W16: a via in a zone of its net on F.Cu, a track on F.Cu from it to a pad
x, y = two("W16", 5, 1)
zone("W16", "W16", ["F.Cu"], rect(x - 3, y + 1, 6, 3), fill=[rect(x - 3, y + 1, 6, 3)])
via("W16", (x, y + 2.5), 0.6, 0.3, "W16")
segment("W16", (x, y + 2.5), (x - 2, y), 0.25, "F.Cu", "W16")

write(HERE / "drcvias", 10, 2)
