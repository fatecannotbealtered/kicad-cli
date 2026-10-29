"""Write the board of the `sch edit` design, and the footprint library it uses.

`make_edit.py` first: this reads the schematic it writes. The board is what
KiCad's Update PCB from Schematic would make of it -- every part's footprint
from `fixture.pretty`, linked by its uuid path, each pad on its pin's net --
laid out on a grid, with a little copper: /SIG runs from R1 to R2 through
two vias. `fixture.pretty` also holds R_0603, a footprint no part uses yet,
for a part to change to.

To change the board: edit this and run it, after `make_edit.py`.
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from kicad_cli.fileformat import netlist  # noqa: E402
from kicad_cli.fileformat.new_board import EMPTY  # noqa: E402
from kicad_cli.fileformat.sexpr import Document, List, copy, quote, render  # noqa: E402
from kicad_cli.native import from_netlist  # noqa: E402

OUT = Path(__file__).resolve().parent
LIBRARY = OUT / "fixture.pretty"
NS = uuid.UUID("7a1c5d2e-0000-4000-8000-0000000b0a2d")

FONT = "(effects (font (size 1 1) (thickness 0.15)))"


def footprint(name, pads, courtyard):
    """pads: [(number, x, y, w, h)]"""
    x1, y1, x2, y2 = courtyard
    text = (
        f'(footprint "{name}" (version 20260206) (generator "fixture") (layer "F.Cu")'
        f' (property "Reference" "REF**" (at 0 {y1 - 0.7} 0) (layer "F.SilkS") {FONT})'
        f' (property "Value" "{name}" (at 0 {y2 + 0.7} 0) (layer "F.Fab") {FONT})'
        f" (attr smd)"
        f" (fp_rect (start {x1} {y1}) (end {x2} {y2}) (stroke (width 0.05) (type solid))"
        f' (fill no) (layer "F.CrtYd"))'
    )
    for number, x, y, w, h in pads:
        text += (
            f' (pad "{number}" smd roundrect (at {x} {y}) (size {w} {h})'
            f' (layers "F.Cu" "F.Mask" "F.Paste") (roundrect_rratio 0.25))'
        )
    document = Document.parse(text + " (embedded_fonts no))", lazy=False)
    (LIBRARY / f"{name}.kicad_mod").write_bytes((render(copy(document.root)) + "\n").encode())


LIBRARY.mkdir(exist_ok=True)
footprint("R_0805", [("1", -0.9125, 0, 1.025, 1.4), ("2", 0.9125, 0, 1.025, 1.4)],
          (-1.68, -0.95, 1.68, 0.95))  # fmt: skip
footprint("R_0603", [("1", -0.825, 0, 0.8, 0.95), ("2", 0.825, 0, 0.8, 0.95)],
          (-1.48, -0.73, 1.48, 0.73))  # fmt: skip
footprint(
    "SO8",
    [(str(n), -2.475 if n <= 4 else 2.475, -1.905 + 1.27 * ((n - 1) % 4 if n <= 4 else 8 - n),
      1.95, 0.6) for n in range(1, 9)],
    (-3.7, -2.7, 3.7, 2.7),
)  # fmt: skip
(OUT / "fp-lib-table").write_bytes(
    b'(fp_lib_table\n\t(version 7)\n\t(lib (name "fixture") (type "KiCad")'
    b' (uri "${KIPRJMOD}/fixture.pretty") (options "") (descr ""))\n)\n'
)

built = netlist.build(OUT / "edit.kicad_sch")
net_of = {(n.ref, n.pin): net.name for net in built.nets for n in net.nodes}
ids = from_netlist._Ids("sch_edit fixture board")
document = Document.parse(EMPTY)
root = document.root
spots = {
    "R1": (100, 100), "R2": (105, 100), "R3": (110, 100), "U1": (122, 104),
    "R4": (100, 110), "R5": (105, 110), "R10": (100, 120), "C10": (105, 120),
    "R11": (110, 120), "R20": (100, 130), "C20": (105, 130), "R21": (110, 130),
}  # fmt: skip
for part in built.components:
    library, _, name = part.footprint.partition(":")
    x, y = spots[part.ref]
    item = {"file": str(LIBRARY / f"{name}.kicad_mod"), "ref": part.ref, "value": part.value,
            "x": x, "y": y}  # fmt: skip
    node = from_netlist._footprint(item, ids)
    node.set(1, quote(part.footprint))
    at = node.find("at")
    path = List.new("path", quote(netlist_path := part.sheet_tstamps + part.tstamps[0]))
    node.insert(node.items.index(at) + 1, path)
    for pad in node.find_all("pad"):
        net = net_of.get((part.ref, pad.value(1) or ""))
        if net:
            from_netlist._set_net(pad, net)
    from_netlist._insert_before_tail(root, node)


def segment(a, b, layer, net, key):
    return List.new(
        "segment",
        List.new("start", str(a[0]), str(a[1])),
        List.new("end", str(b[0]), str(b[1])),
        List.new("width", "0.25"),
        List.new("layer", quote(layer)),
        List.new("net", quote(net)),
        List.new("uuid", ids("segment", key)),
    )


def via(at, net, key):
    return List.new(
        "via",
        List.new("at", str(at[0]), str(at[1])),
        List.new("size", "0.6"),
        List.new("drill", "0.3"),
        List.new("layers", quote("F.Cu"), quote("B.Cu")),
        List.new("net", quote(net)),
        List.new("uuid", ids("via", key)),
    )


SIG = net_of[("R1", "1")]
for node in (
    segment((99.0875, 100), (99.0875, 102), "F.Cu", SIG, 1),
    via((99.0875, 102), SIG, 1),
    segment((99.0875, 102), (104.0875, 102), "B.Cu", SIG, 2),
    via((104.0875, 102), SIG, 2),
    segment((104.0875, 102), (104.0875, 100), "F.Cu", SIG, 3),
):
    from_netlist._insert_before_tail(root, node)
from_netlist._outline(document, ids)
(OUT / "edit.kicad_pcb").write_bytes(document.dumps().encode("utf-8"))
print("written", len(built.components), "footprints")
