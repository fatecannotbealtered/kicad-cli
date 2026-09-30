"""Helpers the DRC fixture boards are written with: one footprint, track,
via, text or zone per call, each with uuids of its own derived from a key, so
a board written twice is the same board.

`start(name, ns)` begins a board; `write(out, ...)` writes it and its
project. Every item is KiCad 10's syntax.
"""

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from kicad_cli.fileformat import new_board, new_project  # noqa: E402
from kicad_cli.fileformat.sexpr import Document  # noqa: E402

PITCH = 10


class _State:
    name = ""
    ns = uuid.UUID(int=0)
    items: list[str] = []


S = _State()


def start(name: str, ns: uuid.UUID) -> None:
    S.name, S.ns, S.items = name, ns, []


def add(text: str) -> None:
    S.items.append(text)


def uid(*parts) -> str:
    return str(uuid.uuid5(S.ns, "/".join(str(p) for p in parts)))


def num(v: float) -> str:
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def xy(p) -> str:
    return f"{num(p[0])} {num(p[1])}"


def font(h=1.0, w=None, t=0.15, mirror=False, extra=""):
    w = h if w is None else w
    justify = " (justify mirror)" if mirror else ""
    return f"(effects (font (size {num(h)} {num(w)}) (thickness {num(t)}){extra}){justify})"


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
    add(
        f'(footprint "{S.name}:{ref}" (layer "{layer}") (uuid "{uid(ref, "footprint")}")'
        f" (at {xy(at)}) {ref_text} {value_text} {attr_text} {courtyard} {body}"
        f" {body_text} (embedded_fonts no))"
    )


def segment(key, a, b, width, layer, net):
    add(
        f'(segment (start {xy(a)}) (end {xy(b)}) (width {num(width)}) (layer "{layer}")'
        f' (net "{net}") (uuid "{uid(key, "segment", a, b, layer)}"))'
    )


def arc(key, a, m, b, width, layer, net):
    add(
        f"(arc (start {xy(a)}) (mid {xy(m)}) (end {xy(b)}) (width {num(width)})"
        f' (layer "{layer}") (net "{net}") (uuid "{uid(key, "arc", a, b)}"))'
    )


def via(key, at, size, drill, net, layers=("F.Cu", "B.Cu"), kind="", n=0):
    kind_text = f" {kind}" if kind else ""
    layers_text = " ".join(f'"{layer}"' for layer in layers)
    add(
        f"(via{kind_text} (at {xy(at)}) (size {num(size)}) (drill {num(drill)})"
        f' (layers {layers_text}) (net "{net}") (uuid "{uid(key, "via", at, n)}"))'
    )


def text(key, value, at, layer, effects, knockout=False):
    ko = " knockout" if knockout else ""
    add(
        f'(gr_text "{value}" (at {xy(at)} 0) (layer "{layer}"{ko}) (uuid "{uid(key, "text")}")'
        f" {effects})"
    )


def zone(key, net, layers, points, priority=0, keepout=False, clearance=0.5, fill=None):
    """A zone: its outline, and, if `fill` is given, those polygons as its
    fill on its first layer -- KiCad's DRC checks a fill as the file has it."""
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
    filled = ""
    fill_text = "(fill (thermal_gap 0.5) (thermal_bridge_width 0.5))"
    if fill is not None:
        fill_text = "(fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))"
        for polygon in fill:
            fpts = " ".join(f"(xy {xy(p)})" for p in polygon)
            filled += f' (filled_polygon (layer "{layers[0]}") (pts {fpts}))'
    add(
        f'(zone {net_text} {layer_text} (uuid "{uid(key, "zone")}") (hatch edge 0.5)'
        f" {priority_text} (connect_pads (clearance {num(clearance)})) (min_thickness 0.25)"
        f" (filled_areas_thickness no) {rules}"
        f" {fill_text} (polygon (pts {pts})){filled})"
    )


def rect(x, y, w, h):
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def write(out: Path, cols: int, rows: int, severities=None, rules=None, net_settings=None,
          inner=True):  # fmt: skip
    """The board, outlined around its cells, and its project: a new
    project's settings but for what is given."""
    x1, y1 = 10, 10
    x2, y2 = 20 + PITCH * cols, 20 + PITCH * rows
    add(
        f"(gr_rect (start {xy((x1, y1))}) (end {xy((x2, y2))}) (stroke (width 0.1) (type solid))"
        f' (fill no) (layer "Edge.Cuts") (uuid "{uid("outline")}"))'
    )
    head = new_board.EMPTY
    if inner:
        head = head.replace(
            '\t\t(0 "F.Cu" signal)\n',
            '\t\t(0 "F.Cu" signal)\n\t\t(4 "In1.Cu" signal)\n\t\t(6 "In2.Cu" signal)\n',
        )
    tail = "\t(embedded_fonts no)\n)\n"
    assert head.endswith(tail)
    text_out = head[: -len(tail)] + "".join(f"\t{item}\n" for item in S.items) + tail
    document = Document.parse(text_out)
    out.mkdir(exist_ok=True)
    (out / f"{S.name}.kicad_pcb").write_bytes(document.dumps().encode("utf-8"))
    project = json.loads(new_project.project(S.name))
    design = project["board"]["design_settings"]
    design["rule_severities"].update(severities or {})
    design["rules"].update(rules or {})
    if net_settings:
        project["net_settings"].update(net_settings)
    (out / f"{S.name}.kicad_pro").write_bytes((json.dumps(project, indent=2) + "\n").encode())
    print("written", len(S.items), "items")
