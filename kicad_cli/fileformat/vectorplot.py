"""A layer's artwork (`artwork.py`) drawn on a page: SVG and PDF.

The board's coordinates are the page's, in millimetres with y down, as
KiCad's plots place them: a board drawn at (150, 100) is 150 mm across and
100 mm down the sheet. The sheet is the board's paper -- A4 unless it says
otherwise -- at KiCad's sizes, which are whole mils. Everything is black:
pads and fills solid, lines with round ends and joins.

A page marks the holes as KiCad's pages do (measured on interf_u): a 0.35 mm
dot, or the hole where it is smaller, at every pad's hole -- white on copper
and mask, where the pad is drawn and the hole shows through it; black on
any other layer -- and at every via's hole on copper.
"""

from __future__ import annotations

import math
from xml.sax.saxutils import escape

from . import geometry
from .artwork import SMALL_DRILL, Fill, Flash, Stroke
from .board import Board

# Paper sizes in mils, landscape, as KiCad keeps them.
PAPERS = {
    "A5": (8268, 5846), "A4": (11693, 8268), "A3": (16535, 11693), "A2": (23386, 16535),
    "A1": (33110, 23386), "A0": (46811, 33110), "A": (11000, 8500), "B": (17000, 11000),
    "C": (22000, 17000), "D": (34000, 22000), "E": (44000, 34000), "GERBER": (32000, 32000),
    "USLetter": (11000, 8500), "USLegal": (14000, 8500), "USLedger": (17000, 11000),
}  # fmt: skip
MAX_ERROR = 1_000  # nm: arcs drawn as chords, at most this far inside
PT = 72 / 25.4  # points per millimetre


def paper(board: Board) -> tuple[float, float]:
    """The sheet's width and height in millimetres."""
    node = board.root.find("paper")
    name = node.value(1) if node is not None else "A4"
    if name == "User" and node is not None:
        return float(node.atom(2)), float(node.atom(3))
    w, h = PAPERS.get(name or "A4", PAPERS["A4"])
    if node is not None and "portrait" in node.values():
        w, h = h, w
    return w * 0.0254, h * 0.0254


def drill_marks(board: Board, layer: str) -> list[tuple[tuple[int, int], int, bool]]:
    """The marks a page puts at holes: (where, how wide, white)."""
    out = []
    copper = layer.endswith(".Cu")
    mask = layer.endswith(".Mask")
    for fp in board.footprints:
        for pad in fp.pads:
            if not pad.drill or min(pad.drill) <= 0:
                continue
            if (copper or mask) and layer not in board.expand_layers(pad.layers):
                continue
            out.append((pad.position, min(SMALL_DRILL, min(pad.drill)), copper or mask))
    if copper:
        for via in board.vias:
            if layer in board.expand_layers(via.layers):
                out.append((via.position, min(SMALL_DRILL, via.drill), True))
    return out


def _drawn(shapes):
    """The artwork without the Gerber's own hole marks: a page marks holes
    its own way."""
    return [s for s in shapes if not (isinstance(s, Flash) and s.attrs.get("mark"))]


def _polygons(flash: Flash) -> list[list[tuple[float, float]]]:
    from .gerber import _flash_rings  # noqa: PLC0415

    return _flash_rings(flash)


def _arc_chords(start, mid, end) -> list[tuple[float, float]]:
    return geometry.arc_through(start, mid, end, MAX_ERROR)


def _mm(v: float) -> str:
    return f"{v / 1e6:.4f}"


# -- SVG ----------------------------------------------------------------------------------


def svg(board: Board, shapes, title: str, layer: str = "") -> str:
    w, h = paper(board)
    out = [
        '<?xml version="1.0" standalone="no"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" version="1.1"',
        f'  width="{w:.4f}mm" height="{h:.4f}mm" viewBox="0 0 {w:.4f} {h:.4f}">',
        f"<title>{escape(title)}</title>",
        '<g style="fill:#000000; stroke:#000000; stroke-linecap:round; stroke-linejoin:round;">',
    ]
    for shape in _drawn(shapes):
        if isinstance(shape, Flash):
            if shape.shape == "circle":
                out.append(_svg_dot(shape.at, shape.size[0], "#000000"))
            else:
                out.append(_svg_fill(_polygons(shape)))
        elif isinstance(shape, Fill):
            out.append(_svg_fill(shape.rings))
        elif isinstance(shape, Stroke):
            out.append(_svg_stroke(shape))
    for at, d, white in drill_marks(board, layer) if layer else []:
        out.append(_svg_dot(at, d, "#FFFFFF" if white else "#000000"))
    out += ["</g>", "</svg>"]
    return "\n".join(out) + "\n"


def _svg_dot(at, d: float, colour: str) -> str:
    x, y, r = _mm(at[0]), _mm(at[1]), _mm(d / 2)
    return f'<circle cx="{x}" cy="{y}" r="{r}" stroke="none" fill="{colour}"/>'


def _svg_fill(rings) -> str:
    d = " ".join(
        "M " + " L ".join(f"{_mm(x)},{_mm(y)}" for x, y in ring) + " Z" for ring in rings if ring
    )
    return f'<path stroke="none" fill-rule="evenodd" d="{d}"/>'


def _svg_stroke(stroke: Stroke) -> str:
    width = f'stroke-width="{_mm(max(stroke.width, 0))}" fill="none"'
    if stroke.circle:
        (cx, cy), (ex, ey) = stroke.points
        r = math.hypot(ex - cx, ey - cy)
        return f'<circle cx="{_mm(cx)}" cy="{_mm(cy)}" r="{_mm(r)}" {width}/>'
    pts = _arc_chords(*stroke.points) if stroke.arc else list(stroke.points)
    d = "M " + " L ".join(f"{_mm(x)},{_mm(y)}" for x, y in pts)
    if stroke.closed:
        d += " Z"
    return f'<path {width} d="{d}"/>'


# -- PDF ----------------------------------------------------------------------------------


def pdf(board: Board, shapes, title: str, layer: str = "") -> bytes:
    w, h = paper(board)
    ops = ["0 g 0 G 1 J 1 j"]

    def at(x, y):
        return f"{x / 1e6 * PT:.4f} {(h - y / 1e6) * PT:.4f}"

    def path(rings, close=True):
        out = []
        for ring in rings:
            if not ring:
                continue
            out.append(f"{at(*ring[0])} m")
            out += [f"{at(*p)} l" for p in ring[1:]]
            if close:
                out.append("h")
        return out

    def dot(centre, d):
        ring = geometry.circle(d / 2, MAX_ERROR)
        return path([[(centre[0] + x, centre[1] + y) for x, y in ring]])

    for shape in _drawn(shapes):
        if isinstance(shape, Flash):
            if shape.shape == "circle":
                ops += dot(shape.at, shape.size[0]) + ["f"]
            else:
                ops += path(_polygons(shape)) + ["f*"]
        elif isinstance(shape, Fill):
            ops += path(shape.rings) + ["f*"]
        elif isinstance(shape, Stroke):
            ops.append(f"{max(shape.width, 0) / 1e6 * PT:.4f} w")
            if shape.circle:
                (cx, cy), (ex, ey) = shape.points
                ops += dot((cx, cy), 2 * math.hypot(ex - cx, ey - cy)) + ["S"]
            else:
                pts = _arc_chords(*shape.points) if shape.arc else list(shape.points)
                ops += path([pts], close=shape.closed) + ["S"]
    for centre, d, white in drill_marks(board, layer) if layer else []:
        ops += ["1 g" if white else "0 g"] + dot(centre, d) + ["f", "0 g"]
    content = "\n".join(ops).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w * PT:.3f} {h * PT:.3f}] "
            f"/Contents 4 0 R /Resources << >> >>"
        ).encode("ascii"),  # fmt: skip
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        f"<< /Title ({_pdf_text(title)}) /Producer (kicad-cli) >>".encode("ascii", "replace"),
    ]
    out = bytearray(b"%PDF-1.5\n")
    offsets = []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, len(objects), xref)  # fmt: skip
    return bytes(out)


def _pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


# -- DXF ----------------------------------------------------------------------------------


def dxf(board: Board, shapes, layer: str = "", inches: bool | None = None) -> str:
    """A layer as an AutoCAD R12 DXF: filled shapes as closed outlines, lines
    as polylines as wide as they are drawn; y up, in inches where the
    board's plot settings ask for them (KiCad's default) or millimetres."""
    if inches is None:
        params = board.root.find("setup")
        params = params.find("pcbplotparams") if params is not None else None
        found = params.find("dxfimperialunits") if params is not None else None
        inches = found is None or found.value(1) != "no"
    unit = 25_400_000 if inches else 1_000_000
    name = (layer or "0").replace(".", "_")
    out = ["0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", "AC1009",
           "9", "$INSUNITS", "70", "1" if inches else "4", "0", "ENDSEC",
           "0", "SECTION", "2", "ENTITIES"]  # fmt: skip

    def polyline(points, closed: bool, width: float = 0.0):
        out.extend(["0", "POLYLINE", "8", name, "66", "1", "70", "1" if closed else "0",
                    "40", f"{width / unit:.6f}", "41", f"{width / unit:.6f}"])  # fmt: skip
        for x, y in points:
            out.extend(["0", "VERTEX", "8", name,
                        "10", f"{x / unit:.6f}", "20", f"{-y / unit:.6f}"])  # fmt: skip
        out.extend(["0", "SEQEND"])

    for shape in _drawn(shapes):
        if isinstance(shape, Flash):
            if shape.shape == "circle":
                ring = [(shape.at[0] + x, shape.at[1] + y)
                        for x, y in geometry.circle(shape.size[0] / 2, MAX_ERROR)]  # fmt: skip
                polyline(ring, True)
            else:
                for ring in _polygons(shape):
                    polyline(ring, True)
        elif isinstance(shape, Fill):
            for ring in shape.rings:
                polyline(ring, True)
        elif isinstance(shape, Stroke):
            if shape.circle:
                (cx, cy), (ex, ey) = shape.points
                r = math.hypot(ex - cx, ey - cy)
                ring = [(cx + x, cy + y) for x, y in geometry.circle(r, MAX_ERROR)]
                polyline(ring, True, shape.width)
            else:
                pts = _arc_chords(*shape.points) if shape.arc else list(shape.points)
                polyline(pts, shape.closed, shape.width)
    out.extend(["0", "ENDSEC", "0", "EOF"])
    return "\n".join(out) + "\n"
