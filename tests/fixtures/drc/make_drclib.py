"""Write drclib/: a project with a footprint library of its own, and a board
of that library's footprints, each placed as KiCad places one and then
changed in one way -- asking KiCad's DRC which changes make a footprint no
longer match its library copy, and what it says of a library it cannot
find, has switched off, or that lacks the footprint.

The answers are recorded in `drclib/drclib.kicad.json`; to change the
board, edit this, run it, and record KiCad's answers again. The project's
own library table names the libraries, so the answers hold wherever the
fixture is.
"""

import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from boardgen import add, cell, start, write  # noqa: E402

sys.path.insert(0, str(HERE.parents[2]))
from kicad_cli.fileformat.sexpr import Document, List, copy, number, quote, render  # noqa: E402

NS = uuid.UUID("7a1c5d2e-0000-4000-8000-0000000d41b0")
start("drclib", NS)

BASE = """(footprint "Base"
	(version 20260206)
	(generator "pcbnew")
	(generator_version "10.0")
	(layer "F.Cu")
	(descr "A footprint the library checks are asked about")
	(tags "drc test")
	(property "Reference" "REF**"
		(at 0 -4 0)
		(layer "F.SilkS")
		(effects
			(font
				(size 1 1)
				(thickness 0.15)
			)
		)
	)
	(property "Value" "Base"
		(at 0 4 0)
		(layer "F.Fab")
		(effects
			(font
				(size 1 1)
				(thickness 0.15)
			)
		)
	)
	(property "Datasheet" ""
		(at 0 0 0)
		(layer "F.Fab")
		(hide yes)
		(effects
			(font
				(size 1.27 1.27)
				(thickness 0.15)
			)
		)
	)
	(property "Description" ""
		(at 0 0 0)
		(layer "F.Fab")
		(hide yes)
		(effects
			(font
				(size 1.27 1.27)
				(thickness 0.15)
			)
		)
	)
	(property "Extra" "lib"
		(at 0 0 0)
		(layer "F.Fab")
		(hide yes)
		(effects
			(font
				(size 1 1)
				(thickness 0.15)
			)
		)
	)
	(attr smd)
	(fp_line
		(start -3 -3)
		(end 3 -3)
		(stroke
			(width 0.12)
			(type solid)
		)
		(layer "F.SilkS")
	)
	(fp_arc
		(start -3 3)
		(mid 0 3.5)
		(end 3 3)
		(stroke
			(width 0.12)
			(type solid)
		)
		(layer "F.SilkS")
	)
	(fp_rect
		(start -3.5 -3.5)
		(end 3.5 3.5)
		(stroke
			(width 0.05)
			(type solid)
		)
		(fill no)
		(layer "F.CrtYd")
	)
	(fp_circle
		(center 0 0)
		(end 0.5 0)
		(stroke
			(width 0.1)
			(type solid)
		)
		(fill no)
		(layer "F.Fab")
	)
	(fp_poly
		(pts
			(xy -1 -1) (xy 1 -1) (xy 0 1)
		)
		(stroke
			(width 0.1)
			(type solid)
		)
		(fill yes)
		(layer "F.Fab")
	)
	(fp_text user "${REFERENCE}"
		(at 0 1.5 0)
		(layer "F.Fab")
		(effects
			(font
				(size 0.5 0.5)
				(thickness 0.08)
			)
		)
	)
	(pad "1" smd rect
		(at -2 0)
		(size 1 1.2)
		(layers "F.Cu" "F.Mask" "F.Paste")
	)
	(pad "2" smd roundrect
		(at 2 0 90)
		(size 1 1.2)
		(layers "F.Cu" "F.Mask" "F.Paste")
		(roundrect_rratio 0.25)
	)
	(pad "3" thru_hole circle
		(at 0 2)
		(size 1.6 1.6)
		(drill 0.8)
		(layers "*.Cu" "*.Mask")
	)
	(pad "4" np_thru_hole circle
		(at 0 -2)
		(size 1 1)
		(drill 1)
		(layers "*.Cu" "*.Mask")
	)
	(pad "5" smd custom
		(at 2 2 30)
		(size 0.5 0.5)
		(layers "F.Cu" "F.Mask" "F.Paste")
		(options
			(clearance outline)
			(anchor rect)
		)
		(primitives
			(gr_poly
				(pts
					(xy 0 0) (xy 1 0) (xy 0 1)
				)
				(width 0)
				(fill yes)
			)
		)
	)
	(embedded_fonts no)
	(model "${KIPRJMOD}/base.step"
		(offset
			(xyz 0 0 0)
		)
		(scale
			(xyz 1 1 1)
		)
		(rotate
			(xyz 0 0 0)
		)
	)
)
"""

POINTS = ("at", "start", "end", "mid", "center", "xy")
TURNED = ("pad", "property", "fp_text")


def _flip(node: List) -> None:
    """Mirror top to bottom in the footprint's own frame, onto the other side."""
    for index, item in enumerate(node.items):
        if not isinstance(item, List):
            if item.startswith('"F.') or item.startswith('"B.'):
                side = "B" if item[1] == "F" else "F"
                node.set(index, f'"{side}.' + item[3:])
            continue
        if item.head == "model":
            continue
        if item.head in POINTS or (item.head == "offset" and item.atom(2) is not None):
            y = item.atom(2)
            if y is not None:
                item.set(2, number(-float(y)))
        _flip(item)


def _turn(node: List, angle: float, flipped: bool) -> None:
    """Each pad's and text's angle as KiCad writes it: the footprint's plus its own."""
    for child in node.lists():
        if child.head not in TURNED:
            continue
        at = child.find("at")
        own = float(at.atom(3)) if at.atom(3) is not None else 0.0
        own = -own if flipped else own
        total = (angle + own) % 360
        if at.atom(3) is not None:
            at.set(3, number(total))
        elif total:
            at.append(number(total))


def _mirror_text(node: List) -> None:
    for child in node.lists():
        if child.head in ("property", "fp_text"):
            effects = child.find("effects")
            if effects is not None and effects.find("justify") is None:
                effects.append(List.new("justify", "mirror"))


def _uuids(node: List, ref: str) -> None:
    for index, child in enumerate(node.lists()):
        if child.head in ("property", "fp_text", "fp_line", "fp_arc", "fp_rect", "fp_circle",
                          "fp_poly", "pad"):  # fmt: skip
            value = List.new("uuid", quote(str(uuid.uuid5(NS, f"{ref}/{index}"))))
            effects = child.find("effects") if child.head in ("property", "fp_text") else None
            if effects is not None:
                child.insert(child.items.index(effects), value)
            else:
                child.append(value)


def place(ref: str, col: int, row: int, angle: float = 0, side: str = "F",
          lib_id: str = "drclib:Base", source: str = BASE, edit=None) -> None:  # fmt: skip
    """A footprint of the library, placed as KiCad places one, then edited."""
    root = Document.parse(source, lazy=False).root
    node = List.new("footprint", quote(lib_id))
    x, y = cell(col, row)
    for child in root.items[2:]:
        if not isinstance(child, List) or child.head in ("version", "generator",
                                                          "generator_version"):  # fmt: skip
            continue
        placed = copy(child)
        if placed.head == "property" and placed.value(1) == "Reference":
            placed.set(2, quote(ref))
        node.append(placed)
        if placed.head == "layer":
            node.append(List.new("uuid", quote(str(uuid.uuid5(NS, ref)))))
    flipped = side == "B"
    if flipped:
        _flip(node)
        _mirror_text(node)
    where = List.new("at", number(x), number(y), *([number(angle)] if angle else []))
    insert_after(node, node.find("uuid"), where)
    _turn(node, angle, flipped)
    _uuids(node, ref)
    if edit is not None:
        edit(node)
    add(render(node).strip())


# -- editing a placed footprint -------------------------------------------------------------


def child(node: List, head: str, name: str | None = None, nth: int = 0) -> List:
    found = [c for c in node.lists() if c.head == head and (name is None or c.value(1) == name)]
    return found[nth]


def set_point(item: List, head: str, x: float, y: float) -> None:
    at = item.find(head)
    at.set(1, number(x))
    at.set(2, number(y))


def replace_child(item: List, head: str, *atoms) -> None:
    old = item.find(head)
    new = List.new(head, *atoms)
    if old is not None:
        item.replace(old, new)
    else:
        item.append(new)


def remove(node: List, head: str, name: str | None = None, nth: int = 0) -> None:
    node.remove(child(node, head, name, nth))


def parsed(text: str) -> List:
    return copy(Document.parse(text, lazy=False).root)


def insert_after(node: List, anchor: List, new: List) -> None:
    node.insert(node.items.index(anchor) + 1, new)


# -- the cases ------------------------------------------------------------------------------

CASES = []


def case(ref, note, **kwargs):
    CASES.append((ref, note, kwargs))


# Placement: the library's footprint as it is, however it is placed.
case("L0", "as the library has it")
case("L1", "turned 90", angle=90)
case("L2", "turned 37.5", angle=37.5)
case("L3", "on the back", side="B")
case("L4", "on the back, turned 90", side="B", angle=90)
case("L5", "on the back, turned 215", side="B", angle=215)

# Its description, and its fields.
case("L6", "another description",
     edit=lambda n: replace_child(n, "descr", quote("changed")))  # fmt: skip
case("L7", "other tags", edit=lambda n: replace_child(n, "tags", quote("other")))
case("L8", "its reference elsewhere",
     edit=lambda n: set_point(child(n, "property", "Reference"), "at", 1, -4))  # fmt: skip
case("L9", "its reference larger", edit=lambda n: child(n, "property", "Reference").find(
    "effects").find("font").replace(child(n, "property", "Reference").find("effects").find(
    "font").find("size"), List.new("size", "1.5", "1.5")))  # fmt: skip
case("L10", "another value", edit=lambda n: child(n, "property", "Value").set(2, quote("10k")))
case("L11", "its value elsewhere",
     edit=lambda n: set_point(child(n, "property", "Value"), "at", 1, 4))  # fmt: skip
case("L12", "a field of the library's another value",
     edit=lambda n: child(n, "property", "Extra").set(2, quote("board")))  # fmt: skip
case("L13", "a field of the library's gone", edit=lambda n: remove(n, "property", "Extra"))
case("L14", "a field of its own",
     edit=lambda n: insert_after(n, child(n, "property", "Extra"), parsed(
         '(property "LCSC" "C123" (at 0 0 0) (layer "F.Fab") (hide yes)'
         ' (effects (font (size 1 1) (thickness 0.15))))')))  # fmt: skip
case(
    "L15",
    "a datasheet",
    edit=lambda n: child(n, "property", "Datasheet").set(2, quote("https://example.com")),
)
case("L16", "through hole, not SMD", edit=lambda n: replace_child(n, "attr", "through_hole"))
case("L17", "left off the BOM", edit=lambda n: replace_child(n, "attr", "smd", "exclude_from_bom"))
case("L18", "not populated", edit=lambda n: replace_child(n, "attr", "smd", "dnp"))
case("L19", "a clearance of its own",
     edit=lambda n: insert_after(n, n.find("attr"), List.new("clearance", "0.3")))  # fmt: skip
case(
    "L20",
    "a mask margin of its own",
    edit=lambda n: insert_after(n, n.find("attr"), List.new("solder_mask_margin", "0.1")),
)
case("L21", "a net tie",
     edit=lambda n: insert_after(n, n.find("attr"),
                                 List.new("net_tie_pad_groups", quote("1,2"))))  # fmt: skip
case("L22", "another 3D model",
     edit=lambda n: n.find("model").set(1, quote("${KIPRJMOD}/other.step")))  # fmt: skip
case("L23", "its 3D model moved", edit=lambda n: n.find("model").replace(
    n.find("model").find("offset"), parsed("(offset (xyz 0 0 1))")))  # fmt: skip
case("L24", "no 3D model", edit=lambda n: n.remove(n.find("model")))
case("L25", "locked", edit=lambda n: insert_after(n, n.find("layer"), List.new("locked", "yes")))
case(
    "L26",
    "linked to a symbol",
    edit=lambda n: insert_after(
        n,
        child(n, "property", "Extra"),
        List.new("path", quote("/12345678-1234-1234-1234-123456789abc")),
    ),
)

# Its drawings.
case("L27", "a line 1 um longer",
     edit=lambda n: set_point(child(n, "fp_line"), "end", 3.001, -3))  # fmt: skip
case("L28", "a line 1 nm longer",
     edit=lambda n: set_point(child(n, "fp_line"), "end", 3.000001, -3))  # fmt: skip
case("L29", "a line wider", edit=lambda n: child(n, "fp_line").find("stroke").replace(
    child(n, "fp_line").find("stroke").find("width"), List.new("width", "0.15")))  # fmt: skip
case("L30", "a line on another layer",
     edit=lambda n: replace_child(child(n, "fp_line"), "layer", quote("F.Fab")))  # fmt: skip
case("L31", "a line dashed", edit=lambda n: child(n, "fp_line").find("stroke").replace(
    child(n, "fp_line").find("stroke").find("type"), List.new("type", "dash")))  # fmt: skip


def _rect_as_lines(n: List) -> None:
    rect = child(n, "fp_rect")
    corners = [(-3.5, -3.5), (3.5, -3.5), (3.5, 3.5), (-3.5, 3.5)]
    for i in range(4):
        a, b = corners[i], corners[(i + 1) % 4]
        line = parsed(
            f"(fp_line (start {number(a[0])} {number(a[1])}) (end {number(b[0])} {number(b[1])})"
            f' (stroke (width 0.05) (type solid)) (layer "F.CrtYd")'
            f' (uuid "{uuid.uuid5(NS, f"rect-line-{i}")}"))'
        )
        n.insert(n.items.index(rect), line)
    n.remove(rect)


case("L32", "a rectangle drawn as four lines", edit=_rect_as_lines)
case("L33", "a circle gone", edit=lambda n: remove(n, "fp_circle"))
case("L34", "a line more", edit=lambda n: insert_after(n, child(n, "fp_line"), parsed(
    '(fp_line (start -3 -2.5) (end 3 -2.5) (stroke (width 0.12) (type solid))'
    ' (layer "F.SilkS"))')))  # fmt: skip


def _swap_first_two(n: List) -> None:
    line, arc = child(n, "fp_line"), child(n, "fp_arc")
    i, j = n.items.index(line), n.items.index(arc)
    n.set(i, copy(arc))
    n.set(j, copy(line))


case("L35", "its line and arc in the other order", edit=_swap_first_two)
case("L36", "its text another text", edit=lambda n: child(n, "fp_text").set(2, quote("X")))
case("L37", "its text elsewhere", edit=lambda n: set_point(child(n, "fp_text"), "at", 0, 1.6))
case("L38", "its arc bent further",
     edit=lambda n: set_point(child(n, "fp_arc"), "mid", 0, 3.51))  # fmt: skip
case("L39", "its circle filled", edit=lambda n: replace_child(child(n, "fp_circle"), "fill", "yes"))


def _poly(points):
    def edit(n: List) -> None:
        pts = child(n, "fp_poly").find("pts")
        child(n, "fp_poly").replace(pts, List.new("pts", *(
            List.new("xy", number(x), number(y)) for x, y in points)))  # fmt: skip

    return edit


case("L40", "its polygon from another corner", edit=_poly([(1, -1), (0, 1), (-1, -1)]))
case("L41", "its polygon the other way round", edit=_poly([(0, 1), (1, -1), (-1, -1)]))

# Its pads.
case(
    "L42", "a pad larger", edit=lambda n: replace_child(child(n, "pad", "1"), "size", "1.1", "1.2")
)
case("L43", "a pad moved", edit=lambda n: set_point(child(n, "pad", "1"), "at", -2.01, 0))
case("L44", "a pad rounded", edit=lambda n: (child(n, "pad", "1").set(3, "roundrect"),
     insert_after(child(n, "pad", "1"), child(n, "pad", "1").find("layers"),
                  List.new("roundrect_rratio", "0.25"))))  # fmt: skip
case("L45", "a pad rounded more",
     edit=lambda n: replace_child(child(n, "pad", "2"), "roundrect_rratio", "0.2"))  # fmt: skip
case("L46", "a pad without paste", edit=lambda n: replace_child(
    child(n, "pad", "1"), "layers", quote("F.Cu"), quote("F.Mask")))  # fmt: skip
case("L47", "a hole larger", edit=lambda n: replace_child(child(n, "pad", "3"), "drill", "0.9"))
case("L48", "a pad renumbered", edit=lambda n: child(n, "pad", "1").set(1, quote("A")))


def _swap_pads(n: List) -> None:
    one, two = child(n, "pad", "1"), child(n, "pad", "2")
    i, j = n.items.index(one), n.items.index(two)
    n.set(i, copy(two))
    n.set(j, copy(one))


case("L49", "its pads in another order", edit=_swap_pads)


def _nets(n: List) -> None:
    for number_, net in (("1", "LN1"), ("2", "LN2"), ("3", "LN1")):
        pad = child(n, "pad", number_)
        uid = pad.find("uuid")
        pad.insert(pad.items.index(uid), List.new("net", quote(net)))


case("L50", "its pads on nets", edit=_nets)
case("L51", "its pads with pin names", edit=lambda n: (
    child(n, "pad", "1").insert(child(n, "pad", "1").items.index(child(n, "pad", "1").find("uuid")),
                                List.new("pinfunction", quote("IN"))),
    child(n, "pad", "1").insert(child(n, "pad", "1").items.index(child(n, "pad", "1").find("uuid")),
                                List.new("pintype", quote("input")))))  # fmt: skip
case("L52", "a pad with a clearance of its own", edit=lambda n: child(n, "pad", "1").insert(
    child(n, "pad", "1").items.index(child(n, "pad", "1").find("uuid")),
    List.new("clearance", "0.3")))  # fmt: skip
case("L53", "a pad with a mask margin of its own", edit=lambda n: child(n, "pad", "1").insert(
    child(n, "pad", "1").items.index(child(n, "pad", "1").find("uuid")),
    List.new("solder_mask_margin", "0.1")))  # fmt: skip
case("L54", "a pad not connected to zones", edit=lambda n: child(n, "pad", "1").insert(
    child(n, "pad", "1").items.index(child(n, "pad", "1").find("uuid")),
    List.new("zone_connect", "0")))  # fmt: skip
case("L55", "a hole's pad without unused layers", edit=lambda n: child(n, "pad", "3").insert(
    child(n, "pad", "3").items.index(child(n, "pad", "3").find("uuid")),
    List.new("remove_unused_layers", "yes")))  # fmt: skip
case("L56", "a pad a test point", edit=lambda n: child(n, "pad", "1").insert(
    child(n, "pad", "1").items.index(child(n, "pad", "1").find("uuid")),
    List.new("property", "pad_prop_testpoint")))  # fmt: skip
case(
    "L57",
    "a custom pad drawn otherwise",
    edit=lambda n: child(n, "pad", "5").replace(
        child(n, "pad", "5").find("primitives"),
        parsed("(primitives (gr_poly (pts (xy 0 0) (xy 1.2 0) (xy 0 1)) (width 0) (fill yes)))"),
    ),
)
case("L58", "a pad gone", edit=lambda n: remove(n, "pad", "1"))
case(
    "L59",
    "a pad more",
    edit=lambda n: insert_after(
        n,
        child(n, "pad", "5"),
        parsed('(pad "6" smd rect (at -2 -2) (size 0.5 0.5) (layers "F.Cu" "F.Mask" "F.Paste"))'),
    ),
)
case("L60", "a pad turned", edit=lambda n: child(n, "pad", "2").find("at").set(3, "0"))
case("L61", "a pad turned with its size swapped: the same copper", edit=lambda n: (
    child(n, "pad", "2").find("at").set(3, "0"),
    replace_child(child(n, "pad", "2"), "size", "1.2", "1")))  # fmt: skip

# Round two: how near is the same, and what else counts.
for n, delta in enumerate((0.000002, 0.00001, 0.0001, 0.0005, 0.000999)):
    case(
        f"M{n}",
        f"a line {delta * 1e6:g} nm longer",
        edit=lambda node, d=delta: set_point(child(node, "fp_line"), "end", 3 + d, -3),
    )
for n, delta in enumerate((0.000001, 0.00001, 0.0001, 0.001)):
    case(
        f"N{n}",
        f"a pad moved {delta * 1e6:g} nm",
        edit=lambda node, d=delta: set_point(child(node, "pad", "1"), "at", -2 + d, 0),
    )
case("P0", "a courtyard line wider", edit=lambda n: child(n, "fp_rect").find("stroke").replace(
    child(n, "fp_rect").find("stroke").find("width"), List.new("width", "0.1")))  # fmt: skip
case("P1", "a fab circle wider", edit=lambda n: child(n, "fp_circle").find("stroke").replace(
    child(n, "fp_circle").find("stroke").find("width"), List.new("width", "0.3")))  # fmt: skip
case("P2", "a line drawn the other way",
     edit=lambda n: (set_point(child(n, "fp_line"), "start", 3, -3),
                     set_point(child(n, "fp_line"), "end", -3, -3)))  # fmt: skip
case("P3", "an arc drawn the other way",
     edit=lambda n: (set_point(child(n, "fp_arc"), "start", 3, 3),
                     set_point(child(n, "fp_arc"), "end", -3, 3)))  # fmt: skip
case("P4", "a circle's end elsewhere on it",
     edit=lambda n: set_point(child(n, "fp_circle"), "end", 0, 0.5))  # fmt: skip
case("P5", "its text gone", edit=lambda n: remove(n, "fp_text"))
case("P6", "a text more", edit=lambda n: insert_after(n, child(n, "fp_text"), parsed(
    '(fp_text user "more" (at 0 -1.5 0) (layer "F.Fab")'
    ' (effects (font (size 0.5 0.5) (thickness 0.08))))')))  # fmt: skip
case("P7", "its text on another layer",
     edit=lambda n: replace_child(child(n, "fp_text"), "layer", quote("F.SilkS")))  # fmt: skip
case("P8", "left off position files",
     edit=lambda n: replace_child(n, "attr", "smd", "exclude_from_pos_files"))  # fmt: skip
case("P9", "on the board only", edit=lambda n: replace_child(n, "attr", "smd", "board_only"))
case("P10", "allowed to bridge its pads",
     edit=lambda n: replace_child(n, "attr", "smd", "allow_soldermask_bridges"))  # fmt: skip
case("P11", "allowed no courtyard",
     edit=lambda n: replace_child(n, "attr", "smd", "allow_missing_courtyard"))  # fmt: skip
case("P12", "no attributes", edit=lambda n: replace_child(n, "attr"))
case(
    "P13",
    "a paste margin of its own",
    edit=lambda n: insert_after(n, n.find("attr"), List.new("solder_paste_margin", "-0.05")),
)
case("P14", "zones connected its own way",
     edit=lambda n: insert_after(n, n.find("attr"), List.new("zone_connect", "2")))  # fmt: skip
case("P15", "a pad's paste margin", edit=lambda n: child(n, "pad", "1").insert(
    child(n, "pad", "1").items.index(child(n, "pad", "1").find("uuid")),
    List.new("solder_paste_margin", "-0.05")))  # fmt: skip
case("P16", "a pad's thermal gap", edit=lambda n: child(n, "pad", "1").insert(
    child(n, "pad", "1").items.index(child(n, "pad", "1").find("uuid")),
    List.new("thermal_gap", "0.3")))  # fmt: skip
case("P17", "a pad's die length", edit=lambda n: child(n, "pad", "1").insert(
    child(n, "pad", "1").items.index(child(n, "pad", "1").find("uuid")),
    List.new("die_length", "0.5")))  # fmt: skip
case("P18", "a hole's pad keeping its end layers", edit=lambda n: child(n, "pad", "3").insert(
    child(n, "pad", "3").items.index(child(n, "pad", "3").find("uuid")),
    List.new("keep_end_layers", "yes")))  # fmt: skip
case("P19", "a hole's pad square", edit=lambda n: child(n, "pad", "3").set(3, "rect"))
case("P20", "an oval hole", edit=lambda n: replace_child(child(n, "pad", "3"), "drill", "oval",
                                                          "0.8", "1"))  # fmt: skip
case("P21", "a hole off its pad's centre", edit=lambda n: child(n, "pad", "3").replace(
    child(n, "pad", "3").find("drill"), parsed("(drill 0.8 (offset 0.1 0))")))  # fmt: skip
case("P22", "a pad on another layer set", edit=lambda n: replace_child(
    child(n, "pad", "1"), "layers", quote("F.Cu"), quote("F.Mask"), quote("F.Paste"),
    quote("F.SilkS")))  # fmt: skip
case("P23", "a hole's pad plated no more",
     edit=lambda n: child(n, "pad", "3").set(2, "np_thru_hole"))  # fmt: skip
case("P24", "a duplicate pad numbers jumper flag", edit=lambda n: insert_after(
    n, n.find("attr"), List.new("duplicate_pad_numbers_are_jumpers", "yes")))  # fmt: skip
case(
    "P25",
    "a jumper group",
    edit=lambda n: insert_after(n, n.find("attr"), parsed('(jumper_pad_groups ("1" "2"))')),
)
case("P26", "the polygon unfilled", edit=lambda n: replace_child(child(n, "fp_poly"), "fill", "no"))
case("P27", "a pad chamfered", edit=lambda n: (
    child(n, "pad", "2").set(3, "roundrect"),
    replace_child(child(n, "pad", "2"), "chamfer_ratio", "0.2"),
    replace_child(child(n, "pad", "2"), "chamfer", "top_left")))  # fmt: skip

# Round three: exactly how near, and the rest of a pad.
for n, nm_ in enumerate((20, 40, 49, 50, 51, 99)):
    case(
        f"Q{n}",
        f"a line {nm_} nm longer",
        edit=lambda node, d=nm_ / 1e6: set_point(child(node, "fp_line"), "end", 3 + d, -3),
    )
for n, nm_ in enumerate((20, 49, 50, 51, 99)):
    case(
        f"R{n}",
        f"a pad moved {nm_} nm",
        edit=lambda node, d=nm_ / 1e6: set_point(child(node, "pad", "1"), "at", -2 + d, 0),
    )
for n, nm_ in enumerate((49, 50, 51)):
    case(f"S{n}", f"a pad {nm_} nm larger",
         edit=lambda node, d=nm_ / 1e6: replace_child(child(node, "pad", "1"), "size",
                                                        number(1 + d), "1.2"))  # fmt: skip
case("T0", "a pad a heatsink", edit=lambda n: child(n, "pad", "1").insert(
    child(n, "pad", "1").items.index(child(n, "pad", "1").find("uuid")),
    List.new("property", "pad_prop_heatsink")))  # fmt: skip
case("T1", "a pad's spokes wider", edit=lambda n: child(n, "pad", "1").insert(
    child(n, "pad", "1").items.index(child(n, "pad", "1").find("uuid")),
    List.new("thermal_bridge_width", "0.3")))  # fmt: skip
case("T2", "a pad's spokes turned", edit=lambda n: child(n, "pad", "1").insert(
    child(n, "pad", "1").items.index(child(n, "pad", "1").find("uuid")),
    List.new("thermal_bridge_angle", "45")))  # fmt: skip
case("T3", "a rectangular pad with a rounding ratio", edit=lambda n: insert_after(
    child(n, "pad", "1"), child(n, "pad", "1").find("layers"),
    List.new("roundrect_rratio", "0.25")))  # fmt: skip
case("T4", "a custom pad's anchor round", edit=lambda n: child(n, "pad", "5").find(
    "options").replace(child(n, "pad", "5").find("options").find("anchor"),
    List.new("anchor", "circle")))  # fmt: skip
case("T5", "a custom pad's clearance by its hull", edit=lambda n: child(n, "pad", "5").find(
    "options").replace(child(n, "pad", "5").find("options").find("clearance"),
    List.new("clearance", "convexhull")))  # fmt: skip
case("T6", "a mounting hole larger", edit=lambda n: (
    replace_child(child(n, "pad", "4"), "drill", "1.1"),
    replace_child(child(n, "pad", "4"), "size", "1.1", "1.1")))  # fmt: skip
case("T7", "a mounting hole's pad larger, its hole not",
     edit=lambda n: replace_child(child(n, "pad", "4"), "size", "1.2", "1.2"))  # fmt: skip
case("T8", "the rectangle filled", edit=lambda n: replace_child(child(n, "fp_rect"), "fill", "yes"))
case("T9", "a polygon corner 1 um off", edit=_poly([(-1, -1), (1.001, -1), (0, 1)]))
case("T10", "a private layer", edit=lambda n: insert_after(
    n, n.find("attr"), List.new("private_layers", quote("User.1"))))  # fmt: skip
case("T11", "a keepout of its own", edit=lambda n: insert_after(n, child(n, "pad", "5"), parsed(
    '(zone (net 0) (net_name "") (layer "F.Cu") (hatch edge 0.5) (connect_pads (clearance 0))'
    ' (min_thickness 0.25) (filled_areas_thickness no) (keepout (tracks not_allowed)'
    ' (vias not_allowed) (pads allowed) (copperpour not_allowed) (footprints allowed))'
    ' (fill (thermal_gap 0.5) (thermal_bridge_width 0.5))'
    ' (polygon (pts (xy -1 -3) (xy 1 -3) (xy 1 -2.5) (xy -1 -2.5))))')))  # fmt: skip
case("T12", "a text box more", edit=lambda n: insert_after(n, child(n, "fp_text"), parsed(
    '(fp_text_box "box" (start -1 -3) (end 1 -2) (margins 0.1 0.1 0.1 0.1) (layer "F.Fab")'
    ' (effects (font (size 0.5 0.5) (thickness 0.08))) (border yes)'
    ' (stroke (width 0.1) (type solid)))')))  # fmt: skip
case("T13", "a line on the courtyard more", edit=lambda n: insert_after(
    n, child(n, "fp_line"), parsed(
        '(fp_line (start -3 -2.5) (end 3 -2.5) (stroke (width 0.05) (type solid))'
        ' (layer "F.CrtYd"))')))  # fmt: skip
case("T14", "a line on a user layer more", edit=lambda n: insert_after(
    n, child(n, "fp_line"), parsed(
        '(fp_line (start -3 -2.5) (end 3 -2.5) (stroke (width 0.1) (type solid))'
        ' (layer "User.1"))')))  # fmt: skip
case("T15", "a line on copper more", edit=lambda n: insert_after(
    n, child(n, "fp_line"), parsed(
        '(fp_line (start -3 -2.5) (end 3 -2.5) (stroke (width 0.1) (type solid))'
        ' (layer "F.Cu"))')))  # fmt: skip
case("T16", "an arc centred elsewhere, same ends",
     edit=lambda n: set_point(child(n, "fp_arc"), "mid", 0, 4))  # fmt: skip
case("T17", "a library whose folder is gone", lib_id="drcgone:Base")

# Round four: the nanometres between the same and not.
for n, nm_ in enumerate((11, 12, 13, 15, 17, 19)):
    case(
        f"U{n}",
        f"a line {nm_} nm longer",
        edit=lambda node, d=nm_ / 1e6: set_point(child(node, "fp_line"), "end", 3 + d, -3),
    )
for n, (dx, dy) in enumerate(((8, 8), (7, 7), (10, 10), (0, 11))):
    case(f"V{n}", f"a pad moved {dx} nm across and {dy} nm down",
         edit=lambda node, d=(dx / 1e6, dy / 1e6): set_point(child(node, "pad", "1"), "at",
                                                               -2 + d[0], d[1]))  # fmt: skip
for n, nm_ in enumerate((10, 11, 15)):
    case(f"W{n}", f"a pad {nm_} nm larger",
         edit=lambda node, d=nm_ / 1e6: replace_child(child(node, "pad", "1"), "size",
                                                        number(1 + d), "1.2"))  # fmt: skip
case("W3", "a hole 10 nm larger", edit=lambda n: replace_child(child(n, "pad", "3"), "drill",
                                                                "0.80001"))  # fmt: skip
case("W4", "a hole 11 nm larger", edit=lambda n: replace_child(child(n, "pad", "3"), "drill",
                                                                "0.800011"))  # fmt: skip
case("W5", "a pad turned 0.01 degree", edit=lambda n: child(n, "pad", "2").find("at").set(
    3, "90.01"))  # fmt: skip
case("W6", "a pad turned 0.1 degree", edit=lambda n: child(n, "pad", "2").find("at").set(
    3, "90.1"))  # fmt: skip

# Round five: copper layers named another way.
case("X0", "a plated hole's pad on F&B.Cu for *.Cu", edit=lambda n: replace_child(
    child(n, "pad", "3"), "layers", quote("F&B.Cu"), quote("*.Mask")))  # fmt: skip
case("X1", "an unplated hole on F&B.Cu for *.Cu", edit=lambda n: replace_child(
    child(n, "pad", "4"), "layers", quote("F&B.Cu"), quote("*.Mask")))  # fmt: skip
case("X2", "a pad on an inner layer too", edit=lambda n: replace_child(
    child(n, "pad", "1"), "layers", quote("F.Cu"), quote("In1.Cu"), quote("F.Mask"),
    quote("F.Paste")))  # fmt: skip
case("X3", "a default written out", edit=lambda n: child(n, "pad", "3").insert(
    child(n, "pad", "3").items.index(child(n, "pad", "3").find("uuid")),
    List.new("remove_unused_layers", "no")))  # fmt: skip

# Libraries it cannot be found in.
case("L62", "a footprint the library has not got", lib_id="drclib:Missing")
case("L63", "a library switched off", lib_id="drcoff:Base")
case("L64", "a library no table names", lib_id="drcnowhere:Base")
case("L65", "no library at all", lib_id="Base")

COLUMNS = 11
for index, (ref, _note, kwargs) in enumerate(CASES):
    place(ref, index % COLUMNS, index // COLUMNS, **kwargs)

out = HERE / "drclib"
write(
    out, COLUMNS, (len(CASES) + COLUMNS - 1) // COLUMNS,
    severities={
        "unconnected_items": "ignore", "silk_overlap": "ignore", "silk_over_copper": "ignore",
        "text_height": "ignore", "text_thickness": "ignore", "courtyards_overlap": "ignore",
        "solder_mask_bridge": "ignore", "clearance": "ignore", "hole_clearance": "ignore",
        "copper_edge_clearance": "ignore", "silk_edge_clearance": "ignore",
    },
    inner=True,
)  # fmt: skip
library = out / "drclib.pretty"
library.mkdir(exist_ok=True)
(library / "Base.kicad_mod").write_bytes(BASE.encode("utf-8"))
(out / "fp-lib-table").write_bytes(
    b"(fp_lib_table\n\t(version 7)\n"
    b'\t(lib (name "drclib") (type "KiCad") (uri "${KIPRJMOD}/drclib.pretty") (options "")'
    b' (descr ""))\n'
    b'\t(lib (name "drcoff") (type "KiCad") (uri "${KIPRJMOD}/drclib.pretty") (options "")'
    b' (descr "") (disabled))\n'
    b'\t(lib (name "drcgone") (type "KiCad") (uri "${KIPRJMOD}/gone.pretty") (options "")'
    b' (descr ""))\n'
    b")\n"
)
(out / "cases.txt").write_bytes(
    "".join(f"{ref}\t{note}\n" for ref, note, _ in CASES).encode("utf-8")
)
