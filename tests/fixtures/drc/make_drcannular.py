"""Write drcannular/: which other holes narrow a plated pad's ring.

Each case is a plated pad, 1.7 mm round with a 1.0 mm hole (a ring of
0.35 mm), and another hole near it or on it: a via's, another footprint's
pad's, a pad of the same footprint with another number, a pad of the same
footprint with the same number. KiCad 10 narrows the ring only for the
last; the rest are holes too near each other, not rings too thin. The
answers are recorded in `drcannular/drcannular.kicad.json` (English).
"""

import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from boardgen import cell, footprint, smd, start, tht, via, write  # noqa: E402

start("drcannular", uuid.UUID("7a1c5d2e-0000-4000-8000-0000000d4ca1"))


def pad_at(ref, col, row):
    x, y = cell(col, row)
    footprint(ref, (x, y), [tht(ref, "1", (0, 0), (1.7, 1.7), 1.0, ref)], attr="through_hole",
              courtyard="none")  # fmt: skip
    return x, y


# V1: a via of the net, its hole overlapping the pad's hole
x, y = pad_at("V1", 0, 0)
via("V1", (x + 0.55, y), 0.6, 0.3, "V1")
# V2: a via of the net, its hole in the ring and out past the copper
x, y = pad_at("V2", 1, 0)
via("V2", (x + 0.75, y), 0.6, 0.3, "V2")
# V3: a via of the net, its hole inside the ring, touching neither edge
x, y = pad_at("V3", 2, 0)
via("V3", (x + 0.67, y), 0.4, 0.1, "V3")
# V4: a via of another net overlapping the pad's hole
x, y = pad_at("V4", 3, 0)
via("V4b", (x + 0.55, y), 0.6, 0.3, "V4b")
# P1: another footprint's pad, its hole overlapping
x, y = pad_at("P1", 4, 0)
footprint("P1b", (x + 0.55, y), [tht("P1b", "1", (0, 0), (0.6, 0.6), 0.3, "P1")],
          attr="through_hole", courtyard="none")  # fmt: skip
# P2: a second pad of the same footprint, its hole overlapping
x, y = cell(5, 0)
footprint("P2", (x, y), [tht("P2", "1", (0, 0), (1.7, 1.7), 1.0, "P2"),
                         tht("P2", "2", (0.55, 0), (0.6, 0.6), 0.3, "P2")],
          attr="through_hole", courtyard="none")  # fmt: skip
# P3: a second pad of the same footprint, its hole in the ring, touching neither edge
x, y = cell(6, 0)
footprint("P3", (x, y), [tht("P3", "1", (0, 0), (1.7, 1.7), 1.0, "P3"),
                         tht("P3", "2", (0.67, 0), (0.4, 0.4), 0.1, "P3")],
          attr="through_hole", courtyard="none")  # fmt: skip
# P4: another footprint's pad, its hole in the ring touching neither edge
x, y = pad_at("P4", 7, 0)
footprint("P4b", (x + 0.67, y), [tht("P4b", "1", (0, 0), (0.4, 0.4), 0.1, "P4")],
          attr="through_hole", courtyard="none")  # fmt: skip
# D1: the same pad twice in one footprint (as measured before: no ring)
x, y = cell(8, 0)
footprint("D1", (x, y), [tht("D1", "1", (0, 0), (1.7, 1.7), 1.0, "D1"),
                         tht("D1b", "1", (0, 0), (1.7, 1.7), 1.0, "D1")],
          attr="through_hole", courtyard="none")  # fmt: skip
# D2: a smaller-hole pad of the same footprint on the same spot
x, y = cell(9, 0)
footprint("D2", (x, y), [tht("D2", "1", (0, 0), (1.7, 1.7), 1.0, "D2"),
                         tht("D2b", "1", (0, 0), (1.7, 1.7), 0.6, "D2")],
          attr="through_hole", courtyard="none")  # fmt: skip

# D3: pads of two footprints on one spot, alike
x, y = pad_at("D3", 0, 1)
footprint("D3b", (x, y), [tht("D3b", "1", (0, 0), (1.7, 1.7), 1.0, "D3")],
          attr="through_hole", courtyard="none")  # fmt: skip
# D4: a via on the pad's spot, its hole as big as the pad's
x, y = pad_at("D4", 1, 1)
via("D4", (x, y), 1.2, 1.0, "D4")
# D5: a second pad of the footprint on the spot, smaller copper, same hole
x, y = cell(2, 1)
footprint("D5", (x, y), [tht("D5", "1", (0, 0), (1.7, 1.7), 1.0, "D5"),
                         tht("D5", "2", (0, 0), (1.3, 1.3), 1.0, "D5")],
          attr="through_hole", courtyard="none")  # fmt: skip
# D6: a second pad of the footprint 0.01 mm off the spot
x, y = cell(3, 1)
footprint("D6", (x, y), [tht("D6", "1", (0, 0), (1.7, 1.7), 1.0, "D6"),
                         tht("D6", "2", (0.01, 0), (1.7, 1.7), 1.0, "D6")],
          attr="through_hole", courtyard="none")  # fmt: skip
# D7: a second pad of the footprint on the spot, a surface pad (no hole)
x, y = cell(4, 1)
footprint("D7", (x, y), [tht("D7", "1", (0, 0), (1.7, 1.7), 1.0, "D7"),
                         smd("D7", "2", (0, 0), (1.7, 1.7), "D7")],
          attr="through_hole", courtyard="none")  # fmt: skip

# D8: a second pad of the same number, its hole overlapping
x, y = cell(5, 1)
footprint("D8", (x, y), [tht("D8", "1", (0, 0), (1.7, 1.7), 1.0, "D8"),
                         tht("D8b", "1", (0.55, 0), (0.6, 0.6), 0.3, "D8")],
          attr="through_hole", courtyard="none")  # fmt: skip
# D9: a second pad of the same number, apart
x, y = cell(6, 1)
footprint("D9", (x, y), [tht("D9", "1", (0, 0), (1.7, 1.7), 1.0, "D9"),
                         tht("D9b", "1", (3, 0), (1.7, 1.7), 1.0, "D9")],
          attr="through_hole", courtyard="none")  # fmt: skip
# D10: a second pad of the same number, its hole in the first's ring
x, y = cell(7, 1)
footprint("D10", (x, y), [tht("D10", "1", (0, 0), (1.7, 1.7), 1.0, "D10"),
                          tht("D10b", "1", (0.67, 0), (0.4, 0.4), 0.1, "D10")],
          attr="through_hole", courtyard="none")  # fmt: skip
# D11: a second pad of the same number, 0.01 mm off, alike
x, y = cell(8, 1)
footprint("D11", (x, y), [tht("D11", "1", (0, 0), (1.7, 1.7), 1.0, "D11"),
                          tht("D11b", "1", (0.01, 0), (1.7, 1.7), 1.0, "D11")],
          attr="through_hole", courtyard="none")  # fmt: skip
# D12: a second pad of the same number on the spot, a surface pad
x, y = cell(9, 1)
footprint("D12", (x, y), [tht("D12", "1", (0, 0), (1.7, 1.7), 1.0, "D12"),
                          smd("D12b", "1", (0, 0), (1.7, 1.7), "D12")],
          attr="through_hole", courtyard="none")  # fmt: skip

write(HERE / "drcannular", 10, 2)
