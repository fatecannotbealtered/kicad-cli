"""Write drc1/: a board asking KiCad's DRC one question per case.

The questions are about single items and their holes: how wide a track or a
via must be, when a pad's padstack is questioned, which text is too small or
reads the wrong way, when two zones overlap, when a track or a via is left
dangling, which footprints' courtyards collide. Every case is its own
footprint or group of items, on nets of its own, ten millimetres from the
next, so the cases do not interfere; every footprint has a courtyard unless
the case is about not having one.

The footprints are the board's own: no library is asked about, and the
library checks are off. The answers are recorded in `drc1/drc1.kicad.json`.
To change the board: edit this, run it, run KiCad's DRC on a copy of the
result (`kicad-cli pcb drc --severity-all --format json drc1.kicad_pcb`, in
English) and record the answers again.
"""

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from kicad_cli.fileformat import new_board, new_project  # noqa: E402
from kicad_cli.fileformat.sexpr import Document  # noqa: E402

OUT = Path(__file__).resolve().parent / "drc1"
NS = uuid.UUID("7a1c5d2e-0000-4000-8000-0000000d4c01")
NAME = "drc1"
PITCH = 10


def uid(*parts) -> str:
    return str(uuid.uuid5(NS, "/".join(str(p) for p in parts)))


def num(v: float) -> str:
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def xy(p) -> str:
    return f"{num(p[0])} {num(p[1])}"


FONT = "(effects (font (size 1 1) (thickness 0.15)))"


def font(h=1.0, w=None, t=0.15, mirror=False, extra=""):
    w = h if w is None else w
    justify = " (justify mirror)" if mirror else ""
    return f"(effects (font (size {num(h)} {num(w)}) (thickness {num(t)}){extra}){justify})"


class Board:
    def __init__(self) -> None:
        self.items: list[str] = []
        self.footprint_extents: list[tuple[float, float, float, float]] = []

    def add(self, text: str) -> None:
        self.items.append(text)


board = Board()


def cell(col: int, row: int) -> tuple[float, float]:
    return (20 + PITCH * col, 20 + PITCH * row)


def pad(key, number, kind, shape, at, size, layers, net=None, drill=None, extra=""):
    net_text = f' (net "{net}")' if net else ""
    drill_text = f" (drill {drill})" if drill is not None else ""
    layers_text = " ".join(f'"{layer}"' for layer in layers)
    angle = f" {num(at[2])}" if len(at) > 2 else ""
    return (
        f'(pad "{number}" {kind} {shape} (at {xy(at)}{angle}) (size {xy(size)}){drill_text}'
        f' (layers {layers_text}){extra}{net_text} (uuid "{uid(key, "pad", number, at)}"))'
    )


def smd(key, number, at, size, net=None, layers=("F.Cu", "F.Mask", "F.Paste"), shape="rect",
        extra=""):  # fmt: skip
    return pad(key, number, "smd", shape, at, size, layers, net, extra=extra)


def tht(key, number, at, size, drill, net=None, shape="circle", extra="",
        layers=("*.Cu", "*.Mask")):  # fmt: skip
    return pad(key, number, "thru_hole", shape, at, size, layers, net, drill,
               " (remove_unused_layers no)" + extra)  # fmt: skip


def npth(key, number, at, drill, extra=""):
    return pad(key, number, "np_thru_hole", "circle", at, (drill, drill), ("*.Cu", "*.Mask"),
               None, drill, extra)  # fmt: skip


def footprint(ref, at, pads, *, attr="smd", layer="F.Cu", courtyard="auto", body="",
              reference=None, value=None):  # fmt: skip
    """A footprint of its own: a rectangle of courtyard around its pads
    unless `courtyard` says otherwise ("none", or the text of one)."""
    side = "B" if layer == "B.Cu" else "F"
    ref_text = reference or (
        f'(property "Reference" "{ref}" (at 0 -3 0) (layer "{side}.SilkS")'
        f' (uuid "{uid(ref, "reference")}") {font(1, mirror=side == "B")})'
    )
    value_text = value or (
        f'(property "Value" "{ref}" (at 0 3 0) (layer "{side}.Fab")'
        f' (uuid "{uid(ref, "value")}") {font(1, mirror=side == "B")})'
    )
    if courtyard == "auto":
        extents = []
        for text in pads:
            doc = Document.parse(text, lazy=False).root
            p = doc.find("at")
            s = doc.find("size")
            px, py = float(p.atom(1)), float(p.atom(2))
            half = max(float(s.atom(1)), float(s.atom(2))) / 2
            extents.append((px - half, py - half, px + half, py + half))
        if extents:
            x1 = min(e[0] for e in extents) - 0.25
            y1 = min(e[1] for e in extents) - 0.25
            x2 = max(e[2] for e in extents) + 0.25
            y2 = max(e[3] for e in extents) + 0.25
        else:
            x1, y1, x2, y2 = -1, -1, 1, 1
        courtyard = (
            f"(fp_rect (start {xy((x1, y1))}) (end {xy((x2, y2))})"
            f' (stroke (width 0.05) (type solid)) (fill no) (layer "{side}.CrtYd")'
            f' (uuid "{uid(ref, "courtyard")}"))'
        )
    elif courtyard == "none":
        courtyard = ""
    attr_text = f"(attr {attr})" if attr else ""
    body_text = " ".join(pads)
    board.add(
        f'(footprint "{NAME}:{ref}" (layer "{layer}") (uuid "{uid(ref, "footprint")}")'
        f" (at {xy(at)}) {ref_text} {value_text} {attr_text} {courtyard} {body}"
        f" {body_text} (embedded_fonts no))"
    )


def segment(key, a, b, width, layer, net):
    board.add(
        f'(segment (start {xy(a)}) (end {xy(b)}) (width {num(width)}) (layer "{layer}")'
        f' (net "{net}") (uuid "{uid(key, "segment", a, b, layer)}"))'
    )


def arc(key, a, m, b, width, layer, net):
    board.add(
        f"(arc (start {xy(a)}) (mid {xy(m)}) (end {xy(b)}) (width {num(width)})"
        f' (layer "{layer}") (net "{net}") (uuid "{uid(key, "arc", a, b)}"))'
    )


def via(key, at, size, drill, net, layers=("F.Cu", "B.Cu"), kind="", n=0):
    kind_text = f" {kind}" if kind else ""
    layers_text = " ".join(f'"{layer}"' for layer in layers)
    board.add(
        f"(via{kind_text} (at {xy(at)}) (size {num(size)}) (drill {num(drill)})"
        f' (layers {layers_text}) (net "{net}") (uuid "{uid(key, "via", at, n)}"))'
    )


def text(key, value, at, layer, effects, knockout=False):
    ko = " knockout" if knockout else ""
    board.add(
        f'(gr_text "{value}" (at {xy(at)} 0) (layer "{layer}"{ko}) (uuid "{uid(key, "text")}")'
        f" {effects})"
    )


def zone(key, net, layers, points, priority=0, keepout=False):
    net_text = f'(net "{net}")' if net else ""
    layer_text = (
        f'(layer "{layers[0]}")' if len(layers) == 1
        else "(layers " + " ".join(f'"{layer}"' for layer in layers) + ")"
    )  # fmt: skip
    pts = " ".join(f"(xy {xy(p)})" for p in points)
    rules = (
        "(keepout (tracks not_allowed) (vias not_allowed) (pads allowed)"
        " (copperpour not_allowed) (footprints allowed))"
        if keepout
        else ""
    )
    priority_text = f"(priority {priority})" if priority else ""
    board.add(
        f'(zone {net_text} {layer_text} (uuid "{uid(key, "zone")}") (hatch edge 0.5)'
        f" {priority_text} (connect_pads (clearance 0.5)) (min_thickness 0.25)"
        f" (filled_areas_thickness no) {rules}"
        f" (fill (thermal_gap 0.5) (thermal_bridge_width 0.5)) (polygon (pts {pts})))"
    )


def rect(x, y, w, h):
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


# -- row 0: tracks and vias between two pads of their own net ---------------------------


def between(ref, col, row, span=6.0, drill=0.8, size=1.6):
    """A footprint with a pad at each end, for copper to run between."""
    x, y = cell(col, row)
    footprint(ref, (x, y), [
        tht(ref, "1", (-span / 2, 0), (size, size), drill, ref),
        tht(ref, "2", (span / 2, 0), (size, size), drill, ref),
    ], attr="through_hole")  # fmt: skip
    return x, y


x, y = between("TW1", 0, 0)
segment("TW1", (x - 3, y), (x + 3, y), 0.1, "F.Cu", "TW1")  # below 0.2
x, y = between("TW2", 1, 0)
segment("TW2", (x - 3, y), (x + 3, y), 0.2, "F.Cu", "TW2")  # at 0.2
x, y = between("TW3", 2, 0)
arc("TW3", (x - 3, y), (x, y - 2), (x + 3, y), 0.15, "F.Cu", "TW3")  # an arc below 0.2
for ref, col, size, drill, kind, layers in (
    ("VD1", 3, 0.4, 0.2, "", ("F.Cu", "B.Cu")),  # diameter and drill below
    ("VD2", 4, 0.6, 0.45, "", ("F.Cu", "B.Cu")),  # annular ring 0.075
    ("VD3", 5, 0.15, 0.05, "micro", ("F.Cu", "In1.Cu")),  # a micro via, both below
    ("VD4", 6, 0.4, 0.2, "blind", ("F.Cu", "In1.Cu")),  # a blind via, both below
    ("VD5", 7, 0.6, 0.45, "buried", ("In1.Cu", "In2.Cu")),  # annular ring 0.075
):
    x, y = between(ref, col, 0)
    via(ref, (x, y), size, drill, ref, layers, kind)
    segment(ref, (x - 3, y), (x, y), 0.25, layers[0], ref)
    segment(ref, (x, y), (x + 3, y), 0.25, layers[1], ref)

# -- row 1: holes --------------------------------------------------------------------------

x, y = between("HC1", 0, 1)  # two vias on one spot
via("HC1", (x, y), 0.6, 0.3, "HC1", n=1)
via("HC1", (x, y), 0.6, 0.3, "HC1", n=2)
segment("HC1", (x - 3, y), (x, y), 0.25, "F.Cu", "HC1")
segment("HC1", (x, y), (x + 3, y), 0.25, "B.Cu", "HC1")
x, y = between("HH1", 1, 1)  # two vias' holes 0.1 apart
via("HH1", (x - 0.2, y), 0.6, 0.3, "HH1", n=1)
via("HH1", (x + 0.2, y), 0.6, 0.3, "HH1", n=2)
segment("HH1", (x - 3, y), (x - 0.2, y), 0.25, "F.Cu", "HH1")
segment("HH1", (x + 0.2, y), (x + 3, y), 0.25, "B.Cu", "HH1")
x, y = cell(2, 1)  # two pads of one footprint, holes 0.1 apart
footprint("HH2", (x, y), [tht("HH2", "1", (-0.45, 0), (1, 1), 0.8, "HH2"),
                          tht("HH2", "2", (0.45, 0), (1, 1), 0.8, "HH2")],
          attr="through_hole")  # fmt: skip
x, y = cell(3, 1)  # pads of two footprints, holes 0.1 apart
footprint("HH3", (x - 0.45, y), [tht("HH3", "1", (0, 0), (1, 1), 0.8, "HH3")],
          attr="through_hole", courtyard="none")  # fmt: skip
footprint("HH4", (x + 0.45, y), [tht("HH4", "1", (0, 0), (1, 1), 0.8, "HH3")],
          attr="through_hole", courtyard="none")  # fmt: skip
x, y = cell(4, 1)  # one pad twice over, as a footprint may draw one
footprint("HC2", (x, y), [tht("HC2", "1", (0, 0), (1.7, 1.7), 1, "HC2"),
                          tht("HC2b", "1", (0, 0), (1.7, 1.7), 1, "HC2")],
          attr="through_hole")  # fmt: skip
x, y = cell(5, 1)  # a via in a pad's hole
footprint("HC3", (x, y), [tht("HC3", "1", (0, 0), (1.7, 1.7), 1, "HC3")], attr="through_hole")
via("HC3", (x, y), 0.6, 0.3, "HC3")
x, y = cell(6, 1)  # two footprints' NPTH holes on one spot
footprint("HC4", (x, y), [npth("HC4", "", (0, 0), 1.2)], attr="through_hole", courtyard="none")
footprint("HC5", (x, y), [npth("HC5", "", (0, 0), 1.2)], attr="through_hole", courtyard="none")
x, y = cell(7, 1)  # a via's hole 0.1 from an NPTH's
footprint("HH5", (x, y), [npth("HH5", "", (0, 0), 1.0)], attr="through_hole")
x2 = x + 0.5 + 0.1 + 0.15
via("HH5", (x2, y), 0.6, 0.3, "HH5")

# -- row 2: through-hole padstacks ---------------------------------------------------------

for col, (ref, spec) in enumerate(
    (
        ("PT1", dict(size=(1, 1), drill="1")),  # hole as big as the pad
        ("PT2", dict(size=(1, 1), drill="1.2")),  # hole bigger than the pad
        ("PT3", dict(size=(1.6, 1.6), drill="1 (offset 0.5 0)")),  # hole out of the copper
        ("PT4", dict(size=(1.2, 1.2), drill="1.1")),  # ring 0.05
        ("PT5", dict(size=(1, 1), drill="0.9", shape="rect")),  # ring 0.05, square
        ("PT6", dict(size=(0.8, 0.8), drill="0.2")),  # hole below 0.3
        ("PT7", dict(size=(1, 2), drill="oval 0.25 1.2", shape="oval")),  # slot 0.25 wide
        ("PT8", dict(size=(2, 1.2), drill="oval 1.4 0.6", shape="oval")),  # slot, ring 0.3
        ("PT9", dict(size=(1.6, 1.6), drill="0.8", layers=("In1.Cu", "F.Mask"))),  # no outer copper
        ("PT10", dict(size=(1.6, 1.6), drill=None)),  # no hole
    )
):
    x, y = cell(col, 2)
    footprint(ref, (x, y), [tht(ref, "1", (0, 0), spec["size"], spec["drill"], ref,
                                shape=spec.get("shape", "circle"),
                                layers=spec.get("layers", ("*.Cu", "*.Mask")))],
              attr="through_hole")  # fmt: skip
x, y = cell(10, 2)  # an NPTH hole below 0.3
footprint("PT11", (x, y), [npth("PT11", "", (0, 0), 0.2)], attr="through_hole")

# -- row 3: surface padstacks and pad properties -------------------------------------------

for col, (ref, pads) in enumerate(
    (
        ("PS1", [smd("PS1", "1", (0, 0), (0.8, 0.8), "PS1", extra=" (solder_paste_margin -0.5)")]),
        ("PS2", [smd("PS2", "1", (0, 0), (0.8, 0.8), "PS2", layers=("B.Cu", "F.Mask"))]),
        ("PS3", [smd("PS3", "1", (0, 0), (0.8, 0.8), "PS3", layers=("F.Cu", "B.Cu", "F.Mask"))]),
        ("PS4", [smd("PS4", "1", (0, 0), (0.8, 0.8), "PS4", layers=("In1.Cu",))]),
        ("PS5", [smd("PS5", "1", (0, 0), (0.8, 0.8), "PS5", layers=("F.Cu", "F.Mask", "B.Paste"))]),
        (
            "PS6",
            [
                pad(
                    "PS6",
                    "1",
                    "connect",
                    "rect",
                    (0, 0),
                    (0.8, 0.8),
                    ("F.Cu", "F.Mask", "F.Paste"),
                    "PS6",
                )
            ],
        ),
        (
            "PS7",
            [
                pad(
                    "PS7",
                    "1",
                    "smd",
                    "circle",
                    (0, 0),
                    (1.2, 1.2),
                    ("F.Cu", "F.Mask"),
                    "PS7",
                    drill="0.5",
                )
            ],
        ),
        (
            "PS8",
            [smd("PS8", "1", (0, 0), (0.8, 0.8), "PS8", extra=" (property pad_prop_castellated)")],
        ),
        (
            "PS9",
            [tht("PS9", "1", (0, 0), (1.6, 1.6), 0.8, "PS9", extra=" (property pad_prop_bga)")],
        ),
        ("PS10", [npth("PS10", "", (0, 0), 1, extra=" (property pad_prop_heatsink)")]),
        ("PS11", [npth("PS11", "", (0, 0), 1, extra=" (property pad_prop_testpoint)")]),
        ("PS12", [npth("PS12", "", (0, 0), 1, extra=" (property pad_prop_fiducial_glob)")]),
        (
            "PS13",
            [
                pad(
                    "PS13",
                    "1",
                    "smd",
                    "custom",
                    (0, 0),
                    (0.5, 0.5),
                    ("F.Cu", "F.Mask"),
                    "PS13",
                    extra=" (options (clearance outline) (anchor rect)) (primitives (gr_poly"
                    " (pts (xy 1 -0.25) (xy 2 -0.25) (xy 2 0.25) (xy 1 0.25)) (width 0)"
                    " (fill yes)))",
                )
            ],
        ),  # a primitive apart from the anchor
        (
            "PS14",
            [
                pad(
                    "PS14",
                    "1",
                    "smd",
                    "custom",
                    (0, 0),
                    (0.5, 0.5),
                    ("F.Cu", "F.Mask"),
                    "PS14",
                    extra=" (options (clearance outline) (anchor rect)) (primitives (gr_poly"
                    " (pts (xy 0.2 -0.25) (xy 2 -0.25) (xy 2 0.25) (xy 0.2 0.25)) (width 0)"
                    " (fill yes)))",
                )
            ],
        ),  # a primitive over the anchor
        (
            "PS15",
            [
                pad(
                    "PS15",
                    "1",
                    "smd",
                    "custom",
                    (0, 0),
                    (0.5, 0.5),
                    ("F.Cu", "F.Mask"),
                    "PS15",
                    extra=" (options (clearance outline) (anchor rect)) (primitives (gr_poly"
                    " (pts (xy 0.35 -0.25) (xy 2 -0.25) (xy 2 0.25) (xy 0.35 0.25))"
                    " (width 0.2) (fill yes)))",
                )
            ],
        ),  # apart, but for its outline's width
    )
):
    x, y = cell(col, 3)
    attr = "through_hole" if ref in ("PS9", "PS10", "PS11", "PS12") else "smd"
    footprint(ref, (x, y), pads, attr=attr)

# -- row 4: footprint types ----------------------------------------------------------------

for col, (ref, attr, pads) in enumerate(
    (
        ("FT1", "through_hole", [smd("FT1", "1", (0, 0), (0.8, 0.8), "FT1")]),
        ("FT2", "smd", [tht("FT2", "1", (0, 0), (1.6, 1.6), 0.8, "FT2")]),
        ("FT3", "", [tht("FT3", "1", (0, 0), (1.6, 1.6), 0.8, "FT3")]),
        ("FT4", "smd", [smd("FT4", "1", (-1, 0), (0.8, 0.8), "FT4"), npth("FT4", "", (1, 0), 1)]),
        (
            "FT5",
            "through_hole",
            [
                smd("FT5", "1", (-1, 0), (0.8, 0.8), "FT5"),
                tht("FT5", "2", (1.5, 0), (1.6, 1.6), 0.8, "FT5b"),
            ],
        ),
        ("FT6", "through_hole", [npth("FT6", "", (0, 0), 1)]),
        (
            "FT7",
            "smd",
            [
                tht(
                    "FT7",
                    "1",
                    (0, 0),
                    (1.6, 1.6),
                    0.8,
                    "FT7",
                    extra=" (property pad_prop_heatsink)",
                )
            ],
        ),
        ("FT8", "smd", [smd("FT8", "1", (0, 0), (0.8, 0.8), "FT8", layers=("F.Mask",))]),
    )
):
    x, y = cell(col, 4)
    footprint(ref, (x, y), pads, attr=attr)

# -- row 5: texts --------------------------------------------------------------------------

for col, (ref, layer, effects, knockout) in enumerate(
    (
        ("TX1", "F.SilkS", font(0.5, t=0.1), False),  # too low
        ("TX2", "F.SilkS", font(1, t=0.05), False),  # too thin
        ("TX3", "F.SilkS", font(0.9, t=0.3), False),  # thick for its size: a quarter of it counts
        ("TX4", "F.SilkS", font(1, 0.3, t=0.1), False),  # narrow: which of the two is height?
        ("TX5", "F.SilkS", font(1, t=0), False),  # no thickness given
        ("TX6", "F.Fab", font(0.5, t=0.05), False),
        ("TX7", "F.Cu", font(0.5, t=0.05), False),
        ("TX8", "B.SilkS", font(1), False),  # on the back, not mirrored
        ("TX9", "F.SilkS", font(1, mirror=True), False),  # on the front, mirrored
        ("TX10", "B.Cu", font(1), False),
        ("TX11", "B.Fab", font(1), False),
        ("TX12", "F.SilkS", font(0.5, t=0.05), True),  # knocked out
        ("TX13", "Edge.Cuts", font(1), False),
        ("TX14", "B.SilkS", font(1, mirror=True), False),
        ("TX15", "F.SilkS", font(0.5, t=0.1, extra=" (bold yes)"), False),
    )
):
    x, y = cell(col, 5)
    text(ref, ref, (x, y), layer, effects, knockout)
x, y = cell(0, 6)  # fields and footprint text
footprint("TF1", (x, y), [smd("TF1", "1", (0, 0), (0.8, 0.8), "TF1")],
          reference=f'(property "Reference" "TF1" (at 0 -2 0) (layer "F.SilkS")'
                    f' (uuid "{uid("TF1", "reference")}") {font(0.5, t=0.05)})',
          value=f'(property "Value" "small" (at 0 2 0) (layer "F.SilkS") (hide yes)'
                f' (uuid "{uid("TF1", "value")}") {font(0.5, t=0.05)})',
          body=f'(property "MPN" "ABC" (at 0 3 0) (layer "F.SilkS")'
               f' (uuid "{uid("TF1", "mpn")}") {font(0.5, t=0.05)})'
               f' (fp_text user "note" (at 0 4 0) (layer "F.SilkS")'
               f' (uuid "{uid("TF1", "user")}") {font(0.5, t=0.05)})')  # fmt: skip
x, y = cell(1, 6)  # a footprint on the back, its reference not mirrored
footprint("TF2", (x, y), [smd("TF2", "1", (0, 0), (0.8, 0.8), "TF2",
                              layers=("B.Cu", "B.Mask", "B.Paste"))],
          layer="B.Cu",
          reference=f'(property "Reference" "TF2" (at 0 -2 0) (layer "B.SilkS")'
                    f' (uuid "{uid("TF2", "reference")}") {font(1)})')  # fmt: skip
x, y = cell(2, 6)  # a text box
board.add(
    f'(gr_text_box "box" (start {xy((x - 2, y - 1))}) (end {xy((x + 2, y + 1))})'
    f' (margins 0.25 0.25 0.25 0.25) (layer "F.SilkS") (uuid "{uid("TB1", "box")}")'
    f" {font(0.5, t=0.05)} (border yes) (stroke (width 0.1) (type solid)))"
)
x, y = cell(3, 6)  # a hidden field on the back, not mirrored
footprint("TF3", (x, y), [smd("TF3", "1", (0, 0), (0.8, 0.8), "TF3",
                              layers=("B.Cu", "B.Mask", "B.Paste"))],
          layer="B.Cu",
          reference=f'(property "Reference" "TF3" (at 0 -2 0) (layer "B.SilkS") (hide yes)'
                    f' (uuid "{uid("TF3", "reference")}") {font(0.5, t=0.05)})')  # fmt: skip

# -- row 7: zones, outlines only -----------------------------------------------------------

for col, (a, b) in enumerate(
    (
        (("ZA", ["F.Cu"], 0), ("ZA", ["F.Cu"], 0)),  # overlapping, one net, one priority
        (("ZB", ["F.Cu"], 0), ("ZC", ["F.Cu"], 0)),  # two nets
        (("ZD", ["F.Cu"], 1), ("ZD", ["F.Cu"], 2)),  # two priorities
        (("ZE", ["F.Cu"], 0), ("ZE", ["B.Cu"], 0)),  # two layers
        (("ZF", ["F.Cu", "B.Cu"], 0), ("ZF", ["B.Cu"], 0)),  # sharing one layer
        (("", ["F.Cu"], 0), ("", ["F.Cu"], 0)),  # no net
    )
):
    x, y = cell(col, 7)
    zone(f"Z{col}a", a[0], a[1], rect(x - 3, y - 3, 4, 4), a[2])
    zone(f"Z{col}b", b[0], b[1], rect(x - 1, y - 1, 4, 4), b[2])
x, y = cell(6, 7)  # sharing an edge only
zone("Z6a", "ZG", ["F.Cu"], rect(x - 3, y - 2, 3, 4))
zone("Z6b", "ZG", ["F.Cu"], rect(x, y - 2, 3, 4))
x, y = cell(7, 7)  # a zone under a keep-out
zone("Z7a", "ZH", ["F.Cu"], rect(x - 3, y - 3, 4, 4))
zone("Z7b", "", ["F.Cu"], rect(x - 1, y - 1, 4, 4), keepout=True)

# -- row 8: dangling copper and missing connections ---------------------------------------

x, y = cell(0, 8)  # a track to nowhere
footprint("DG1", (x - 2, y), [smd("DG1", "1", (0, 0), (1, 1), "DG1")])
segment("DG1", (x - 2, y), (x + 2, y), 0.25, "F.Cu", "DG1")
x, y = cell(1, 8)  # a via on one track only
footprint("DG2", (x - 2, y), [smd("DG2", "1", (0, 0), (1, 1), "DG2")])
segment("DG2", (x - 2, y), (x + 1, y), 0.25, "F.Cu", "DG2")
via("DG2", (x + 1, y), 0.6, 0.3, "DG2")
x, y = cell(2, 8)  # a via alone
via("DG3", (x, y), 0.6, 0.3, "DG3")
x, y = between("DG4", 3, 8)  # a track ending on another's middle
footprint("DG4b", (x, y + 3), [smd("DG4b", "1", (0, 0), (1, 1), "DG4")])
segment("DG4", (x - 3, y), (x + 3, y), 0.25, "F.Cu", "DG4")
segment("DG4", (x, y + 3), (x, y), 0.25, "F.Cu", "DG4")
x, y = cell(4, 8)  # a track ending inside a pad, off its centre
footprint("DG5", (x - 2, y), [smd("DG5", "1", (0, 0), (2, 2), "DG5")])
footprint("DG5b", (x + 2, y), [smd("DG5b", "1", (0, 0), (1, 1), "DG5")])
segment("DG5", (x - 1.5, y + 0.5), (x + 2, y), 0.25, "F.Cu", "DG5")
x, y = between("DG6", 5, 8)  # two tracks whose ends overlap without meeting
segment("DG6", (x - 3, y), (x - 0.05, y), 0.25, "F.Cu", "DG6")
segment("DG6", (x + 0.05, y), (x + 3, y), 0.25, "F.Cu", "DG6")
x, y = cell(6, 8)  # two pads of one net, nothing between them
footprint("UC1", (x, y), [smd("UC1", "1", (-2, 0), (1, 1), "UC1"),
                          smd("UC1", "2", (2, 0), (1, 1), "UC1")])  # fmt: skip
x, y = cell(7, 8)  # three pads of one net, two joined
footprint("UC2", (x, y), [smd("UC2", "1", (-2.5, 0), (1, 1), "UC2"),
                          smd("UC2", "2", (0, 0), (1, 1), "UC2"),
                          smd("UC2", "3", (2.5, 0), (1, 1), "UC2")])  # fmt: skip
segment("UC2", (x - 2.5, y), (x, y), 0.25, "F.Cu", "UC2")
x, y = cell(8, 8)  # a via reaching a track on one layer and a pad on another
footprint("DG7", (x - 2, y), [smd("DG7", "1", (0, 0), (1, 1), "DG7",
                                  layers=("B.Cu", "B.Mask"))])  # fmt: skip
footprint("DG7b", (x + 2, y), [smd("DG7b", "1", (0, 0), (1, 1), "DG7")])
segment("DG7", (x + 2, y), (x, y), 0.25, "F.Cu", "DG7")
segment("DG7", (x, y), (x - 2, y), 0.25, "B.Cu", "DG7")
via("DG7", (x, y), 0.6, 0.3, "DG7")

# -- row 9: courtyards ---------------------------------------------------------------------


def courtyard_rect(ref, x1, y1, x2, y2, side="F"):
    return (
        f"(fp_rect (start {xy((x1, y1))}) (end {xy((x2, y2))}) (stroke (width 0.05)"
        f' (type solid)) (fill no) (layer "{side}.CrtYd") (uuid "{uid(ref, "cy", x1, y1)}"))'
    )


def courtyard_line(ref, a, b, side="F"):
    return (
        f"(fp_line (start {xy(a)}) (end {xy(b)}) (stroke (width 0.05) (type solid))"
        f' (layer "{side}.CrtYd") (uuid "{uid(ref, "cyl", a, b)}"))'
    )


x, y = cell(0, 9)  # overlapping
footprint("CY1", (x - 1, y), [smd("CY1", "1", (0, 0), (0.8, 0.8), "CY1")],
          courtyard=courtyard_rect("CY1", -1, -1, 1, 1))  # fmt: skip
footprint("CY2", (x + 0.5, y), [smd("CY2", "1", (0, 0), (0.8, 0.8), "CY2")],
          courtyard=courtyard_rect("CY2", -1, -1, 1, 1))  # fmt: skip
x, y = cell(1, 9)  # touching along an edge
footprint("CY3", (x - 1, y), [smd("CY3", "1", (0, 0), (0.8, 0.8), "CY3")],
          courtyard=courtyard_rect("CY3", -1, -1, 1, 1))  # fmt: skip
footprint("CY4", (x + 1, y), [smd("CY4", "1", (0, 0), (0.8, 0.8), "CY4")],
          courtyard=courtyard_rect("CY4", -1, -1, 1, 1))  # fmt: skip
x, y = cell(2, 9)  # an open courtyard
footprint("CY5", (x, y), [smd("CY5", "1", (0, 0), (0.8, 0.8), "CY5")],
          courtyard=courtyard_line("CY5", (-1, -1), (1, -1))
          + courtyard_line("CY5", (1, -1), (1, 1))
          + courtyard_line("CY5", (1, 1), (-1, 1)))  # fmt: skip
x, y = cell(3, 9)  # front and back, overlapping
footprint("CY6", (x, y), [smd("CY6", "1", (0, 0), (0.8, 0.8), "CY6")],
          courtyard=courtyard_rect("CY6", -1, -1, 1, 1))  # fmt: skip
footprint("CY7", (x + 0.5, y), [smd("CY7", "1", (0, 0), (0.8, 0.8), "CY7",
                                    layers=("B.Cu", "B.Mask", "B.Paste"))],
          layer="B.Cu", courtyard=courtyard_rect("CY7", -1, -1, 1, 1, "B"))  # fmt: skip
x, y = cell(4, 9)  # a plated hole inside another footprint's courtyard
footprint("CY8", (x, y), [smd("CY8", "1", (0, 0), (0.8, 0.8), "CY8")],
          courtyard=courtyard_rect("CY8", -2, -2, 2, 2))  # fmt: skip
footprint("CY9", (x + 1.5, y), [tht("CY9", "1", (0, 0), (1, 1), 0.5, "CY9")],
          attr="through_hole", courtyard="none")  # fmt: skip
x, y = cell(5, 9)  # an unplated hole inside another footprint's courtyard
footprint("CY10", (x, y), [smd("CY10", "1", (0, 0), (0.8, 0.8), "CY10")],
          courtyard=courtyard_rect("CY10", -2, -2, 2, 2))  # fmt: skip
footprint("CY11", (x + 1.5, y), [npth("CY11", "", (0, 0), 0.8)],
          attr="through_hole", courtyard="none")  # fmt: skip
x, y = cell(6, 9)  # no courtyard at all
footprint("CY12", (x, y), [smd("CY12", "1", (0, 0), (0.8, 0.8), "CY12")], courtyard="none")
x, y = cell(7, 9)  # a round courtyard over a square one
footprint("CY13", (x - 1, y), [smd("CY13", "1", (0, 0), (0.8, 0.8), "CY13")],
          courtyard=f"(fp_circle (center 0 0) (end 1 0) (stroke (width 0.05) (type solid))"
                    f' (fill no) (layer "F.CrtYd") (uuid "{uid("CY13", "circle")}"))')  # fmt: skip
footprint("CY14", (x + 0.7, y), [smd("CY14", "1", (0, 0), (0.8, 0.8), "CY14")],
          courtyard=courtyard_rect("CY14", -1, -1, 1, 1))  # fmt: skip

# -- row 10: solder paste shrunk to nothing, or not quite ----------------------------------

for col, (ref, extra) in enumerate(
    (
        ("PM1", " (solder_paste_margin -0.39)"),
        ("PM2", " (solder_paste_margin -0.41)"),
        ("PM3", " (solder_paste_margin -0.79)"),
        ("PM4", " (solder_paste_margin -0.8)"),
        ("PM5", " (solder_paste_margin -0.81)"),
        ("PM6", " (solder_paste_margin_ratio -0.6)"),
        ("PM7", " (solder_paste_margin_ratio -1.1)"),
    )
):
    x, y = cell(col, 10)
    footprint(ref, (x, y), [smd(ref, "1", (0, 0), (0.8, 0.8), ref, extra=extra)])
x, y = cell(7, 10)  # the footprint's margin, not the pad's
footprint("PM8", (x, y), [smd("PM8", "1", (0, 0), (0.8, 0.8), "PM8")],
          body="(solder_paste_margin -0.9)")  # fmt: skip

# -- row 11: which track ends are held, and by what -----------------------------------------


def filled_zone(key, net, points, layer="F.Cu"):
    pts = " ".join(f"(xy {xy(p)})" for p in points)
    board.add(
        f'(zone (net "{net}") (layer "{layer}") (uuid "{uid(key, "zone")}") (hatch edge 0.5)'
        f" (connect_pads (clearance 0.5)) (min_thickness 0.25) (filled_areas_thickness no)"
        f" (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))"
        f' (polygon (pts {pts})) (filled_polygon (layer "{layer}") (pts {pts})))'
    )


x, y = between("DE1", 0, 11)  # a sliver of track at the joint of two others
segment("DE1", (x - 3, y), (x, y), 0.3, "F.Cu", "DE1")
segment("DE1", (x, y), (x + 3, y), 0.3, "F.Cu", "DE1")
segment("DE1", (x, y), (x + 0.01, y), 0.3, "F.Cu", "DE1")
x, y = cell(1, 11)  # a short track inside one big pad
footprint("DE2", (x, y), [smd("DE2", "1", (0, 0), (2, 2), "DE2")])
segment("DE2", (x - 0.3, y), (x + 0.3, y), 0.2, "F.Cu", "DE2")
x, y = between("DE3", 2, 11)  # a short track lying on another's middle
segment("DE3", (x - 3, y), (x + 3, y), 0.25, "F.Cu", "DE3")
segment("DE3", (x - 0.05, y), (x + 0.05, y), 0.25, "F.Cu", "DE3")
x, y = between("DE4", 3, 11)  # a T whose foot is off the other's centre line
footprint("DE4b", (x, y + 3), [smd("DE4b", "1", (0, 0), (1, 1), "DE4")])
segment("DE4", (x - 3, y), (x + 3, y), 0.25, "F.Cu", "DE4")
segment("DE4", (x, y + 3), (x, y + 0.1), 0.25, "F.Cu", "DE4")
x, y = cell(4, 11)  # ends short of a pad's edge: within half the width, and not
footprint("DE5", (x, y), [smd("DE5", "1", (0, 0), (1, 1), "DE5")])
segment("DE5", (x + 0.55, y), (x + 3, y), 0.25, "F.Cu", "DE5")
segment("DE5", (x - 0.7, y), (x - 3, y), 0.25, "F.Cu", "DE5")
footprint("DE5b", (x + 3, y), [smd("DE5b", "1", (0, 0), (1, 1), "DE5")])
footprint("DE5c", (x - 3, y), [smd("DE5c", "1", (0, 0), (1, 1), "DE5")])
x, y = cell(5, 11)  # a short track between two vias, each over both its ends
via("DE6", (x - 0.05, y), 0.6, 0.3, "DE6", n=1)
via("DE6", (x + 0.05, y), 0.6, 0.3, "DE6", n=2)
segment("DE6", (x - 0.05, y), (x + 0.05, y), 0.2, "F.Cu", "DE6")
x, y = cell(6, 11)  # ends short of a zone's fill by less than half the width, and by more
filled_zone("DE7", "DE7", rect(x - 1, y - 2, 2, 4))
footprint("DE7b", (x + 3.5, y - 1), [smd("DE7b", "1", (0, 0), (1, 1), "DE7")])
footprint("DE7c", (x + 3.5, y + 1), [smd("DE7c", "1", (0, 0), (1, 1), "DE7")])
footprint("DE7d", (x - 3.5, y - 1), [smd("DE7d", "1", (0, 0), (1, 1), "DE7")])
footprint("DE7e", (x - 3.5, y + 1), [smd("DE7e", "1", (0, 0), (1, 1), "DE7")])
segment("DE7", (x + 1.12, y - 1), (x + 3.5, y - 1), 0.25, "F.Cu", "DE7")  # 0.005 short of half
segment("DE7", (x + 1.128, y + 1), (x + 3.5, y + 1), 0.25, "F.Cu", "DE7")  # 0.003 over
segment("DE7", (x - 1.135, y - 1), (x - 3.5, y - 1), 0.25, "F.Cu", "DE7")  # 0.010 over
segment("DE7", (x - 1.2, y + 1), (x - 3.5, y + 1), 0.25, "F.Cu", "DE7")  # 0.075 over
x, y = between("DE8", 7, 11)  # a T whose other end is nearer the long track's end
footprint("DE8b", (x + 2.9, y + 0.8), [smd("DE8b", "1", (0, 0), (0.6, 0.6), "DE8")])
segment("DE8", (x - 3, y), (x + 3, y), 0.25, "F.Cu", "DE8")
segment("DE8", (x, y), (x + 2.9, y + 0.8), 0.25, "F.Cu", "DE8")
x, y = between("DE9", 8, 11)  # a short jog between two tracks, each over both its ends
segment("DE9", (x - 3, y), (x - 0.1, y), 0.3, "F.Cu", "DE9")
segment("DE9", (x - 0.1, y), (x + 0.1, y + 0.1), 0.3, "F.Cu", "DE9")
segment("DE9", (x + 0.1, y + 0.1), (x + 3, y), 0.3, "F.Cu", "DE9")

# -- the board -----------------------------------------------------------------------------

COLS, ROWS = 16, 12
x1, y1 = 10, 10
x2, y2 = 20 + PITCH * COLS, 20 + PITCH * ROWS
board.add(
    f"(gr_rect (start {xy((x1, y1))}) (end {xy((x2, y2))}) (stroke (width 0.1) (type solid))"
    f' (fill no) (layer "Edge.Cuts") (uuid "{uid("outline")}"))'
)
head = new_board.EMPTY.replace(
    '\t\t(0 "F.Cu" signal)\n',
    '\t\t(0 "F.Cu" signal)\n\t\t(4 "In1.Cu" signal)\n\t\t(6 "In2.Cu" signal)\n',
)
tail = "\t(embedded_fonts no)\n)\n"
assert head.endswith(tail)
text_out = head[: -len(tail)] + "".join(f"\t{item}\n" for item in board.items) + tail
document = Document.parse(text_out)
OUT.mkdir(exist_ok=True)
(OUT / f"{NAME}.kicad_pcb").write_bytes(document.dumps().encode("utf-8"))

project = json.loads(new_project.project(NAME))
severities = project["board"]["design_settings"]["rule_severities"]
severities["footprint_type_mismatch"] = "warning"
severities["missing_courtyard"] = "warning"
severities["lib_footprint_issues"] = "ignore"
severities["lib_footprint_mismatch"] = "ignore"
(OUT / f"{NAME}.kicad_pro").write_bytes((json.dumps(project, indent=2) + "\n").encode("utf-8"))
print("written", len(board.items), "items")
