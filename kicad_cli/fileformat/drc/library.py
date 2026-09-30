"""Footprint libraries: whether a board's footprints are still in their
libraries, and still as the libraries have them.

A footprint names its library by nickname -- "Resistor_SMD:R_0805_2012Metric"
-- and the project's library tables say where that library is
(`lib_tables.py`). KiCad reports, measured on `tests/fixtures/drc/drclib`:

- a nickname no table names: "The current configuration does not include
  the footprint library 'X'";
- one a table switches off, or whose folder is not there: "The footprint
  library 'X' is not enabled in the current configuration";
- a library without the footprint: "Footprint 'Y' not found in library 'X'";
- a footprint no longer as the library has it: "Footprint 'Y' does not
  match copy in library 'X'".

A footprint that names no library is not asked about.

A copy matches in the footprint's own frame: how it is turned, and on which
side, does not count. Its pads count by number, kind, shape, place, size,
angle, layers, hole, rounding and chamfer, custom shape, the unused layers
it keeps, fabrication property and die length -- not by net, pin, clearance,
margins, zone connection or thermal spokes, nor a custom pad's anchor. Its
drawings count by kind, layer, fill and points -- not by line width or
style, nor which way a line or an arc is drawn, nor where a polygon starts,
though a circle counts by the point its radius is drawn to. Its rule areas
count, and its type (SMD, through hole), whether it may bridge its pads, and
its net ties. Its text, fields, description, 3D models, other attributes and
its own margins do not count; nor does the order of anything. Points agree
within 10 nm on each axis; sizes, holes and angles only exactly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from .. import geometry, lib_tables
from ..board import Footprint, nm
from ..sexpr import Document, List, SexprError, render
from . import items as describe

TOLERANCE = 10  # nm, on each axis
GRAPHICS = ("fp_line", "fp_arc", "fp_rect", "fp_circle", "fp_poly", "fp_curve")
POINTS = ("start", "mid", "end", "center")


def check(run) -> None:
    if not (run.on("lib_footprint_issues") or run.on("lib_footprint_mismatch")):
        return
    board = run.board
    if run.kicad_root is None:
        return
    tables = lib_tables.Tables.of(Path(run.path).resolve().parent, Path(run.kicad_root))
    copper = frozenset(board.copper_layers)
    forms: dict[Path, _Form | None] = {}
    for fp in board.footprints:
        nickname, colon, name = fp.library_id.partition(":")
        if not colon or not nickname:
            continue
        library = tables.footprints.get(nickname)
        off = f"The footprint library '{nickname}' is not enabled in the current configuration"
        if library is None:
            if nickname in tables.disabled_footprints:
                message = off
            else:
                message = (
                    f"The current configuration does not include the footprint library '{nickname}'"
                )
            run.report("lib_footprint_issues", message, [describe.footprint(fp)])
            continue
        if library.kind != "KiCad":
            continue
        if not library.path.is_dir():
            run.report("lib_footprint_issues", off, [describe.footprint(fp)])
            continue
        file = library.path / f"{name}.kicad_mod"
        if not file.is_file():
            run.report(
                "lib_footprint_issues",
                f"Footprint '{name}' not found in library '{nickname}'",
                [describe.footprint(fp)],
            )
            continue
        if file not in forms:
            forms[file] = _library_form(file, copper)
        theirs = forms[file]
        if theirs is None:
            continue
        if not _form(fp.node, fp, copper).same(theirs):
            run.report(
                "lib_footprint_mismatch",
                f"Footprint '{name}' does not match copy in library '{nickname}'",
                [describe.footprint(fp)],
            )


# -- a footprint's form, in its own frame ----------------------------------------------------


@dataclass
class _Form:
    kind: str
    bridges: bool
    ties: frozenset
    pads: list = field(default_factory=list)
    graphics: list = field(default_factory=list)
    areas: list = field(default_factory=list)

    def same(self, other: _Form) -> bool:
        return (
            (self.kind, self.bridges, self.ties) == (other.kind, other.bridges, other.ties)
            and _matched(self.pads, other.pads, _same_pad)
            and _matched(self.graphics, other.graphics, _same_graphic)
            and _matched(self.areas, other.areas, _same_area)
        )


def _library_form(file: Path, copper: frozenset[str]) -> _Form | None:
    try:
        return _form(Document.load(file).root, None, copper)
    except (OSError, SexprError, UnicodeDecodeError, ValueError):
        return None


class _Frame:
    """From how a footprint is placed back to its library's frame."""

    def __init__(self, fp: Footprint | None, copper: frozenset[str]) -> None:
        self.copper = copper  # the board's copper layers
        self.flipped = fp is not None and fp.layer == "B.Cu"
        self.angle = fp.angle if fp is not None else 0.0
        self.origin = fp.position if fp is not None else (0, 0)

    def point(self, x: int, y: int) -> tuple[int, int]:
        """A child's own point: kept as the library keeps it, but for the side."""
        return (x, -y) if self.flipped else (x, y)

    def absolute(self, x: int, y: int) -> tuple[int, int]:
        """A point the file holds on the board -- a rule area's -- in the footprint's frame."""
        if self.origin == (0, 0) and not self.angle and not self.flipped:
            return x, y
        dx, dy = geometry.rotate(x - self.origin[0], y - self.origin[1], -self.angle)
        return self.point(round(dx), round(dy))

    def pad_angle(self, stored: float) -> float:
        own = stored - self.angle
        return _degrees(-own if self.flipped else own)

    def layer(self, name: str) -> str:
        if self.flipped and name[:2] in ("F.", "B."):
            return ("B." if name[0] == "F" else "F.") + name[2:]
        return name


def _degrees(value: float) -> float:
    return round(value % 360.0, 6) % 360.0


def _form(node: List, fp: Footprint | None, copper: frozenset[str]) -> _Form:
    frame = _Frame(fp, copper)
    attr = node.find("attr")
    words = set(attr.values()) if attr is not None else set()
    kind = "smd" if "smd" in words else "through_hole" if "through_hole" in words else ""
    ties = node.find("net_tie_pad_groups")
    groups = frozenset(
        frozenset(n.strip() for n in group.split(","))
        for group in (ties.values() if ties is not None else ())
    )
    form = _Form(kind, "allow_soldermask_bridges" in words, groups)
    for child in node.lists():
        if child.head == "pad":
            form.pads.append(_pad(child, frame))
        elif child.head in GRAPHICS:
            form.graphics.append(_graphic(child, frame))
        elif child.head == "zone":
            form.areas.append(_area(child, frame))
    return form


def _xy(node: List | None, frame: _Frame) -> tuple[int, int] | None:
    if node is None or node.atom(2) is None:
        return None
    return frame.point(nm(node.atom(1)), nm(node.atom(2)))


def _layers(node: List, frame: _Frame) -> frozenset[str]:
    found = node.find("layers")
    names = found.values() if found is not None else []
    single = node.find("layer")
    if single is not None and single.value(1):
        names = [*names, single.value(1)]
    out = set()
    for name in names:
        if name == "*.Cu":
            out |= frame.copper
        elif name == "F&B.Cu":
            out |= {"F.Cu", "B.Cu"}
        elif name.startswith("*."):
            out |= {"F." + name[2:], "B." + name[2:]}
        else:
            out.add(frame.layer(name))
    return frozenset(out)


def _pad_layers(node: List, frame: _Frame, kind: str) -> frozenset[str]:
    """A pad's layers as KiCad compares them: a plated hole's copper is every
    layer, whatever the file says; a surface pad's the outer layer it names,
    inner ones dropped; an unplated hole's what the board has of it."""
    layers = _layers(node, frame)
    other = frozenset(name for name in layers if not name.endswith(".Cu"))
    copper = layers - other
    if kind == "thru_hole":
        copper = frozenset({"*.Cu"})
    elif kind in ("smd", "connect"):
        copper &= {"F.Cu", "B.Cu"}
    else:
        copper &= frame.copper
    return other | copper


def _graphic(node: List, frame: _Frame) -> tuple:
    fill = node.find("fill")
    filled = fill is not None and fill.value(1) in ("yes", "solid")
    if node.head == "fp_poly":
        pts = node.find("pts")
        points = [_xy(p, frame) for p in pts.lists() if p.head == "xy"] if pts is not None else []
    elif node.head == "fp_curve":
        pts = node.find("pts")
        points = [_xy(p, frame) for p in pts.lists() if p.head == "xy"] if pts is not None else []
    else:
        points = [_xy(node.find(head), frame) for head in POINTS]
    return (node.head, _layers(node, frame), filled, [p for p in points if p is not None])


def _pad(node: List, frame: _Frame) -> tuple:
    at = node.find("at")
    x, y = frame.point(nm(at.atom(1)), nm(at.atom(2)))
    angle = frame.pad_angle(float(at.atom(3)) if at.atom(3) is not None else 0.0)
    size = node.find("size")
    shape = node.atom(3) or ""
    drill = node.find("drill")
    hole = None
    if drill is not None:
        values = [v for v in drill.values()]
        oval = "oval" in values
        numbers = [nm(v) for v in values if v != "oval"]
        offset = _xy(drill.find("offset"), frame)
        hole = (oval, tuple(numbers), offset)
    extras = []
    if shape in ("roundrect",):
        extras.append(("rratio", _text(node, "roundrect_rratio")))
    for head in ("chamfer_ratio", "chamfer", "remove_unused_layers", "keep_end_layers",
                 "property", "die_length", "padstack"):  # fmt: skip
        found = node.find(head)
        if found is None:
            continue
        if head == "padstack":
            extras.append((head, render(found)))
            continue
        words = found.values()
        if head == "chamfer":
            if frame.flipped:  # the corners mirrored top to bottom
                flip = {"top": "bottom", "bottom": "top"}
                words = ["_".join([flip.get(w.split("_")[0], w.split("_")[0]), *w.split("_")[1:]])
                         for w in words]  # fmt: skip
            words = sorted(words)
        value = " ".join(words)
        if value in ("", "no", "0", "pad_prop_none"):
            continue  # as good as not written
        extras.append((head, value))
    primitives = node.find("primitives")
    shapes = []
    if primitives is not None:
        for primitive in primitives.lists():
            pts = primitive.find("pts")
            points = (
                [_xy(p, frame) for p in pts.lists() if p.head == "xy"] if pts is not None
                else [_xy(primitive.find(head), frame) for head in POINTS]
            )  # fmt: skip
            width = primitive.find("width")
            fill = primitive.find("fill")
            shapes.append((
                primitive.head, [p for p in points if p is not None],
                nm(width.atom(1)) if width is not None else 0,
                fill is not None and fill.value(1) in ("yes", "solid"),
            ))  # fmt: skip
    return {
        "number": node.value(1) or "",
        "kind": node.atom(2) or "",
        "shape": shape,
        "at": (x, y),
        "angle": angle,
        "size": (nm(size.atom(1)), nm(size.atom(2))) if size is not None else None,
        "layers": _pad_layers(node, frame, node.atom(2) or ""),
        "hole": hole,
        "extras": sorted(extras),
        "primitives": shapes,
    }


def _text(node: List, head: str) -> str:
    found = node.find(head)
    return (found.atom(1) or "") if found is not None else ""


def _area(node: List, frame: _Frame) -> tuple:
    polygon = node.find("polygon")
    pts = polygon.find("pts") if polygon is not None else None
    points = []
    if pts is not None:
        for p in pts.lists():
            if p.head == "xy":
                points.append(frame.absolute(nm(p.atom(1)), nm(p.atom(2))))
    keepout = node.find("keepout")
    rules = tuple(sorted(" ".join(r.values()) + r.head for r in keepout.lists())) if keepout else ()
    return (_layers(node, frame), rules, points)


# -- comparing -------------------------------------------------------------------------------


def _near(p, q) -> bool:
    return abs(p[0] - q[0]) <= TOLERANCE and abs(p[1] - q[1]) <= TOLERANCE


def _along(ps, qs) -> bool:
    return len(ps) == len(qs) and all(_near(p, q) for p, q in zip(ps, qs, strict=True))


def _cyclic(ps, qs) -> bool:
    if len(ps) != len(qs):
        return False
    if not ps:
        return True
    for candidate in (qs, qs[::-1]):
        for shift in range(len(candidate)):
            if _along(ps, candidate[shift:] + candidate[:shift]):
                return True
    return False


def _same_graphic(a: tuple, b: tuple) -> bool:
    head, layers, filled, ps = a
    if (head, layers, filled) != b[:3]:
        return False
    qs = b[3]
    if head == "fp_line" or head == "fp_curve":
        return _along(ps, qs) or _along(ps, qs[::-1])
    if head == "fp_arc":
        # start, mid, end
        return (
            len(ps) == len(qs) == 3
            and _near(ps[1], qs[1])
            and (_along([ps[0], ps[2]], [qs[0], qs[2]]) or _along([ps[0], ps[2]], [qs[2], qs[0]]))
        )
    if head == "fp_rect":
        return len(ps) == len(qs) == 2 and _along(_corners(ps), _corners(qs))
    if head == "fp_poly":
        return _cyclic(ps, qs)
    return _along(ps, qs)


def _corners(ps):
    (x1, y1), (x2, y2) = ps
    return [(min(x1, x2), min(y1, y2)), (max(x1, x2), max(y1, y2))]


def _same_pad(a: dict, b: dict) -> bool:
    if any(a[k] != b[k] for k in ("number", "kind", "shape", "size", "layers", "extras")):
        return False
    if not math.isclose(a["angle"], b["angle"], abs_tol=1e-6) and not math.isclose(
        abs(a["angle"] - b["angle"]), 360.0, abs_tol=1e-6
    ):
        return False
    if not _near(a["at"], b["at"]):
        return False
    ha, hb = a["hole"], b["hole"]
    if (ha is None) != (hb is None):
        return False
    if ha is not None:
        if ha[:2] != hb[:2] or (ha[2] is None) != (hb[2] is None):
            return False
        if ha[2] is not None and not _near(ha[2], hb[2]):
            return False
    pa, pb = a["primitives"], b["primitives"]
    if len(pa) != len(pb):
        return False
    for x, y in zip(pa, pb, strict=True):
        if (x[0], x[2], x[3]) != (y[0], y[2], y[3]) or not _along(x[1], y[1]):
            return False
    return True


def _same_area(a: tuple, b: tuple) -> bool:
    return a[:2] == b[:2] and _cyclic(a[2], b[2])


def _matched(ours: list, theirs: list, same) -> bool:
    """Whether each of ours is one of theirs, one to one, in any order."""
    if len(ours) != len(theirs):
        return False
    left = list(theirs)
    for item in ours:
        for index, candidate in enumerate(left):
            if same(item, candidate):
                del left[index]
                break
        else:
            return False
    return True
