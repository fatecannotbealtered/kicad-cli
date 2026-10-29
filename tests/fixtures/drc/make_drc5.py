"""Write drc5/: `drc4`'s questions about the solder mask where the minimum
web and the mask-to-copper clearance are 0 -- so openings bridge only where
they overlap, not where they touch -- the board lets footprints bridge their
own pads, and vias are open on the front and tented on the back. The mask
margin is 0.05 mm, as on `drc4`; also asked: the net KiCad's connectivity
gives a track laid over pads.

The answers are recorded in `drc5/drc5.kicad.json`; to change the board,
edit this, run it, and record KiCad's answers again.
"""

import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from boardgen import cell, footprint, segment, start, uid, via, write  # noqa: E402
from maskgen import MASK_RULES, alone, below, mask_rect, pad, pair, tracks, twin  # noqa: E402

start("drc5", uuid.UUID("7a1c5d2e-0000-4000-8000-0000000d4c05"))


def via_over(key, col, row, gap, extra="", layer="F.Cu", net=None):
    """A 0.6 mm via of net {key}a and, `gap` below its copper, a 0.2 mm
    track of another net."""
    x, y = cell(col, row)
    via(key, (x, y), 0.6, 0.3, f"{key}a", extra=extra)
    ty = y + 0.3 + gap + 0.1
    segment(key, (x - 2, ty), (x + 2, ty), 0.2, layer, net or f"{key}b")


RECT = (
    '(fp_rect (start -1.2 -0.8) (end 1.2 0.8) (stroke (width 0) (type solid)) (fill yes)'
    ' (layer "F.Mask") (uuid "{}"))'
)  # fmt: skip


def jumper(key, col, row, opening=True):
    """One footprint of two 0.8 x 1 mm pads 0.4 apart, and an opening of its
    own over both."""
    x, y = cell(col, row)
    body = RECT.format(uid(key, "fprect")) if opening else ""
    footprint(key, (x, y), [pad(key, "1", (-0.6, 0), f"{key}a", size=(0.8, 1)),
                            pad(key, "2", (0.6, 0), f"{key}b", size=(0.8, 1))],
              courtyard="none", body=body)  # fmt: skip
    return x, y


# -- row 0: overlapping, not touching ------------------------------------------------------

pair("E0", 0, 0, 0.1)  # openings touching
pair("E1", 1, 0, 0.0995)  # openings overlapping by 0.5 um
below("E2", 2, 0, 0.05, "track")  # an opening touching a track
below("E3", 3, 0, 0.0495, "track")
via_over("E4", 4, 0, 0.0495)  # open on the front, as the board has vias: grown by the margin
via_over("E5", 5, 0, 0.0495, extra=" (tenting (front yes) (back yes))")  # tented itself
via_over("E6", 6, 0, 0.0495, extra=" (tenting (front none) (back none))")  # as the board has it
below("E7", 7, 0, 0.0995, "maskless")  # a pad without the mask layer, held by its margin
below("E8", 8, 0, 0.1, "maskless")
x, y = cell(9, 0)  # on the back, a pad over a via tented there
alone("E9A", (x, y), "E9a", side="B")
via("E9", (x, y + 0.5 + 0.0495 + 0.3), 0.6, 0.3, "E9b")
via_over("E10", 10, 0, 0.0495, layer="B.Cu")  # on the back, copper under the mask only

# -- row 1: what the board lets footprints bridge, and the nets of tracks over pads --------

twin("F0", 0, 1, 0.05)  # two pads of one footprint
x, y = cell(1, 1)  # a pad of such a footprint over a track of a third net
twin("F1", 1, 1, 0.4)
segment("F1", (x - 2, y + 0.5 + 0.0495 + 0.1), (x - 0.6, y + 0.5 + 0.0495 + 0.1), 0.2, "F.Cu",
        "F1c")  # fmt: skip
x, y = jumper("F2", 2, 1)  # its opening over tracks of its pads' nets
segment("F2a", (x - 0.6, y), (x - 0.6, y + 2), 0.2, "F.Cu", "F2a")
segment("F2b", (x + 0.6, y), (x + 0.6, y + 2), 0.2, "F.Cu", "F2b")
x, y = jumper("F3", 3, 1)  # its opening over a track of a third net
tracks("F3", x, (y + 0.65,), ("F3c",))
x, y = jumper("F4", 4, 1, opening=False)  # the board's opening over the pads of one footprint
mask_rect("F4", x - 1.2, y - 0.8, 2.4, 1.6)
x, y = cell(5, 1)  # a track from a pad of a third net: of that net
below("F5", 5, 1, 0.0495, "track", net="F5c")
alone("F5P", (x - 2, y + 0.5 + 0.0495 + 0.1), "F5d")
x, y = cell(6, 1)  # a track from a pad of the net of the pad it passes: of that net
below("F6", 6, 1, 0.0495, "track", net="F6c")
alone("F6P", (x - 2, y + 0.5 + 0.0495 + 0.1), "F6a")
x, y = cell(7, 1)  # a track over pads of two nets: its own net
below("F7", 7, 1, 0.0495, "track", net="F7c")
alone("F7D", (x - 2, y + 0.5 + 0.0495 + 0.1), "F7d")
alone("F7E", (x - 1.5, y + 3), "F7e")
segment("F7e", (x - 2, y + 0.5 + 0.0495 + 0.1), (x - 1.5, y + 3), 0.2, "F.Cu", "F7c")
x, y = cell(8, 1)  # a track whose end meets the pad's edge: not joined to it
alone("F8A", (x, y), "F8a")
segment("F8", (x, y + 0.6), (x, y + 2), 0.2, "F.Cu", "F8c")

write(
    HERE / "drc5", 11, 2, severities=MASK_RULES,
    setup={
        "pad_to_mask_clearance": "0.05",
        "allow_soldermask_bridges_in_footprints": "yes",
        "tenting": {"front": "no", "back": "yes"},
    },
)  # fmt: skip
