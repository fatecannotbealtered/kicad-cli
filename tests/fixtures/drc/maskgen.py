"""Helpers the solder mask fixture boards (`make_drc4.py`, `make_drc5.py`)
are written with, on top of `boardgen`: pads of their own footprints side by
side, a pad with something under its opening, openings drawn on the mask.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from boardgen import add, cell, footprint, num, rect, segment, smd, uid, via, xy  # noqa: E402

MASK_RULES = {
    "unconnected_items": "ignore", "missing_courtyard": "ignore", "clearance": "ignore",
    "lib_footprint_issues": "ignore", "lib_footprint_mismatch": "ignore",
    "silk_overlap": "ignore", "silk_over_copper": "ignore", "silk_edge_clearance": "ignore",
    "copper_edge_clearance": "ignore", "track_dangling": "ignore", "via_dangling": "ignore",
    "isolated_copper": "ignore", "starved_thermal": "ignore", "hole_clearance": "ignore",
    "shorting_items": "ignore", "tracks_crossing": "ignore", "track_width": "ignore",
    "text_height": "ignore", "text_thickness": "ignore", "courtyards_overlap": "ignore",
}  # fmt: skip


def pad(key, number, at, net, size=(1, 1), side="F", extra="", layers=None):
    layers = layers or (f"{side}.Cu", f"{side}.Mask", f"{side}.Paste")
    return smd(key, number, at, size, net, layers=layers, extra=extra)


def alone(key, at, net, side="F", extra="", body="", attr="smd", layers=None):
    """A footprint of one 1 mm pad."""
    footprint(key, at, [pad(key, "1", (0, 0), net, side=side, extra=extra, layers=layers)],
              courtyard="none", body=body, attr=attr, layer=f"{side}.Cu")  # fmt: skip


def pair(key, col, row, gap, nets=None, side="F", b_extra="", b_body="", b_layers=None):
    """Two 1 mm pads of footprints of their own, `gap` apart side by side."""
    x, y = cell(col, row)
    a, b = nets if nets is not None else (f"{key}a", f"{key}b")
    off = 0.5 + gap / 2
    alone(f"{key}A", (x - off, y), a, side=side)
    alone(f"{key}B", (x + off, y), b, side=side, extra=b_extra, body=b_body, layers=b_layers)


def twin(key, col, row, gap, numbers=("1", "2"), attr="smd", body=""):
    """One footprint of two 1 mm pads of two nets, `gap` apart."""
    x, y = cell(col, row)
    off = 0.5 + gap / 2
    pads = [pad(key, numbers[0], (-off, 0), f"{key}a"), pad(key, numbers[1], (off, 0), f"{key}b")]
    footprint(key, (x, y), pads, courtyard="none", attr=attr, body=body)


def below(key, col, row, gap, what, net=None, extra=""):
    """A 1 mm pad of net {key}a, and `gap` below its copper: a 0.2 mm track,
    a 0.6 mm via, a zone's fill, or a pad without the mask layer."""
    x, y = cell(col, row)
    alone(f"{key}A", (x, y), f"{key}a")
    edge = y + 0.5 + gap
    net = net or f"{key}b"
    if what == "track":
        segment(key, (x - 2, edge + 0.1), (x + 2, edge + 0.1), 0.2, "F.Cu", net)
    elif what == "via":
        via(key, (x, edge + 0.3), 0.6, 0.3, net, extra=extra)
    elif what == "zone":
        fill = rect(x - 2, edge, 4, 1)
        pts = " ".join(f"(xy {xy(p)})" for p in fill)
        add(f'(zone (net "{net}") (layer "F.Cu") (uuid "{uid(key, "zone")}") (hatch edge 0.5)'
            f" (connect_pads (clearance 0)) (min_thickness 0.25) (filled_areas_thickness no)"
            f" (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5)) (polygon (pts {pts}))"
            f' (filled_polygon (layer "F.Cu") (pts {pts})))')  # fmt: skip
    elif what == "maskless":
        alone(f"{key}B", (x, edge + 0.5), net, layers=("F.Cu",))


def opening(key, points, layer="F.Mask", fill="yes", width=0):
    """A polygon drawn on a mask layer."""
    pts = " ".join(f"(xy {xy(p)})" for p in points)
    add(f"(gr_poly (pts {pts}) (stroke (width {num(width)}) (type solid)) (fill {fill})"
        f' (layer "{layer}") (uuid "{uid(key, "opening")}"))')  # fmt: skip


def mask_rect(key, x, y, w, h, layer="F.Mask", fill="yes", width=0):
    add(f"(gr_rect (start {xy((x, y))}) (end {xy((x + w, y + h))})"
        f" (stroke (width {num(width)}) (type solid)) (fill {fill}) (layer \"{layer}\")"
        f' (uuid "{uid(key, "rect")}"))')  # fmt: skip


def tracks(key, x, ys, nets, layer="F.Cu", x1=-2, x2=2):
    for y, net in zip(ys, nets, strict=True):
        segment(key, (x + x1, y), (x + x2, y), 0.2, layer, net)
