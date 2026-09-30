"""Write drc4/: a board asking KiCad's DRC about the solder mask -- which
openings bridge nets, with a mask margin of 0.05 mm, a minimum web of
0.1 mm and a mask-to-copper clearance of 0.1 mm -- one question per case,
each ten millimetres from the next.

The answers are recorded in `drc4/drc4.kicad.json`; to change the board,
edit this, run it, and record KiCad's answers again. `drc5` asks the same
questions where everything is 0, and the board lets footprints bridge.
"""

import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from boardgen import add, cell, footprint, segment, start, uid, write, xy  # noqa: E402
from maskgen import (  # noqa: E402
    MASK_RULES,
    alone,
    below,
    mask_rect,
    opening,
    pad,
    pair,
    tracks,
    twin,
)

start("drc4", uuid.UUID("7a1c5d2e-0000-4000-8000-0000000d4c04"))


# -- row 0: openings of pads ---------------------------------------------------------------

pair("P0", 0, 0, 0.1995)  # openings 0.0995 apart, under the 0.1 web
pair("P1", 1, 0, 0.2)  # a web of 0.1 exactly
pair("P2", 2, 0, 0.15, b_extra=" (solder_mask_margin 0)")  # its own margin, 0
pair("P3", 3, 0, 0.1, b_extra=" (solder_mask_margin -0.05)")  # its own margin, shrinking it
pair("P4", 4, 0, 0.2, b_body="(solder_mask_margin 0.1)")  # its footprint's margin
pair("P5", 5, 0, 0.05, nets=("P5a", "P5a"))  # one net
pair("P6", 6, 0, 0.05, nets=(None, None))  # no net, twice
twin("P7", 7, 0, 0.1)  # two pads of one footprint
twin("P8", 8, 0, 0.1, attr="smd allow_soldermask_bridges")  # a footprint allowing it
pair("P9", 9, 0, 0.1, side="B")  # on the back
twin("P10", 10, 0, 0.1, body='(net_tie_pad_groups "1,2")')  # a net tie
twin("P11", 11, 0, 0.1, numbers=("1", "1"))  # one pad in two pieces
twin("P12", 12, 0, 0.1, body='(net_tie_pad_groups "1,2")'  # a net tie's copper between them
     ' (fp_poly (pts (xy -0.6 -0.2) (xy 0.6 -0.2) (xy 0.6 0.2) (xy -0.6 0.2))'
     ' (stroke (width 0) (type solid)) (fill yes) (layer "F.Cu")'
     f' (uuid "{uid("P12", "tie")}"))')  # fmt: skip

# -- row 1: openings and copper under the mask ---------------------------------------------

below("C0", 0, 1, 0.1495, "track")  # 0.0995 from the opening, under the 0.1 clearance
below("C1", 1, 1, 0.15, "track")
below("C2", 2, 1, 0.1995, "via", extra=" (tenting (front no) (back no))")  # an open via
below("C3", 3, 1, 0.2, "via", extra=" (tenting (front no) (back no))")
below("C4", 4, 1, 0.1495, "via")  # a tented via: copper under the mask
below("C5", 5, 1, 0.15, "via")
below("C6", 6, 1, 0.1495, "zone")
below("C7", 7, 1, 0.1995, "maskless")  # a pad without the mask layer, held by its margin
below("C8", 8, 1, 0.2, "maskless")
x, y = cell(9, 1)  # a track from a pad of the net of the pad it passes: of that net
below("C9", 9, 1, 0.1495, "track", net="C9c")
alone("C9P", (x - 2, y + 0.5 + 0.1495 + 0.1), "C9a")
x, y = cell(10, 1)  # the same from a pad of a third net: of that net
below("C10", 10, 1, 0.1495, "track", net="C10c")
alone("C10P", (x - 2, y + 0.5 + 0.1495 + 0.1), "C10d")
x, y = cell(11, 1)  # a track whose end meets the pad's edge: not joined to it
alone("C11A", (x, y), "C11a")
segment("C11", (x, y + 0.6), (x, y + 2), 0.2, "F.Cu", "C11c")

# -- row 2: openings drawn on the mask -----------------------------------------------------

x, y = cell(0, 2)  # over two tracks of two nets
mask_rect("D0", x - 1, y - 0.5, 2, 1)
tracks("D0", x, (y - 0.2, y + 0.2), ("D0a", "D0b"))
x, y = cell(1, 2)  # over one track
mask_rect("D1", x - 1, y - 0.5, 2, 1)
tracks("D1", x, (y,), ("D1a",))
x, y = cell(2, 2)  # over nothing, two tracks 0.1495 from its edges: grown by the margin
mask_rect("D2", x - 1, y - 0.5, 2, 1)
tracks("D2", x, (y - 0.5 - 0.1495 - 0.1, y + 0.5 + 0.1495 + 0.1), ("D2a", "D2b"))
x, y = cell(3, 2)
mask_rect("D3", x - 1, y - 0.5, 2, 1)
tracks("D3", x, (y - 0.5 - 0.15 - 0.1, y + 0.5 + 0.15 + 0.1), ("D3a", "D3b"))
x, y = cell(4, 2)  # a footprint allowing bridges: its opening over its pads and a third net
footprint("D4", (x, y), [pad("D4", "1", (-0.6, 0), "D4a", size=(0.8, 1)),
                         pad("D4", "2", (0.6, 0), "D4b", size=(0.8, 1))],
          courtyard="none", attr="smd allow_soldermask_bridges",
          body=f'(fp_rect (start -1.2 -0.8) (end 1.2 0.8) (stroke (width 0) (type solid))'
               f' (fill yes) (layer "F.Mask") (uuid "{uid("D4", "fprect")}"))')  # fmt: skip
tracks("D4", x, (y + 0.65,), ("D4c",))
x, y = cell(5, 2)  # a footprint's opening over its two pads
footprint("D5", (x, y), [pad("D5", "1", (-0.6, 0), "D5a", size=(0.8, 1)),
                         pad("D5", "2", (0.6, 0), "D5b", size=(0.8, 1))],
          courtyard="none",
          body=f'(fp_rect (start -1.2 -0.8) (end 1.2 0.8) (stroke (width 0) (type solid))'
               f' (fill yes) (layer "F.Mask") (uuid "{uid("D5", "fprect")}"))')  # fmt: skip
x, y = cell(6, 2)  # a circle over two tracks
add(f"(gr_circle (center {xy((x, y))}) (end {xy((x + 1, y))}) (stroke (width 0) (type solid))"
    f' (fill yes) (layer "F.Mask") (uuid "{uid("D6", "circle")}"))')  # fmt: skip
tracks("D6", x, (y - 0.3, y + 0.3), ("D6a", "D6b"))
x, y = cell(7, 2)  # an L of a polygon over two tracks
opening("D7", [(x - 1, y - 1), (x + 1, y - 1), (x + 1, y - 0.5), (x - 0.5, y - 0.5),
               (x - 0.5, y + 1), (x - 1, y + 1)])  # fmt: skip
tracks("D7", x, (y - 0.75, y + 0.5), ("D7a", "D7b"), x1=-2, x2=0.5)
x, y = cell(8, 2)  # on the back
mask_rect("D8", x - 1, y - 0.5, 2, 1, layer="B.Mask")
tracks("D8", x, (y - 0.2, y + 0.2), ("D8a", "D8b"), layer="B.Cu")
x, y = cell(9, 2)  # 0.15 from a pad's copper, over a track: the openings closer than the web
alone("D9A", (x, y), "D9a")
mask_rect("D9", x - 1, y + 0.5 + 0.15, 2, 0.6)
tracks("D9", x, (y + 0.5 + 0.15 + 0.3,), ("D9b",))
x, y = cell(10, 2)  # an outline, not filled, its sides across two tracks
mask_rect("D10", x - 1, y - 0.5, 2, 1, fill="no", width=0.1)
tracks("D10", x, (y - 0.2, y + 0.2), ("D10a", "D10b"))
x, y = cell(11, 2)  # an outline, not filled, two tracks inside it
mask_rect("D11", x - 1.5, y - 1, 3, 2, fill="no", width=0.1)
tracks("D11", x, (y - 0.2, y + 0.2), ("D11a", "D11b"), x1=-0.8, x2=0.8)

write(
    HERE / "drc4", 13, 3, severities=MASK_RULES,
    rules={"solder_mask_to_copper_clearance": 0.1},
    setup={"pad_to_mask_clearance": "0.05", "solder_mask_min_width": "0.1"},
)  # fmt: skip
