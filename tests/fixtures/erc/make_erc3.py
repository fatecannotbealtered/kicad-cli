"""Write erc3/: the third design asking KiCad's ERC one question per case,
about the library rules -- whether a part's symbol and footprint are where
its nicknames say, whether the sheet's copy of a symbol is still its
library's, and whether its footprint is one its symbol's filters allow.

The libraries are the project's own, named in its `sym-lib-table` and
`fp-lib-table` by `${KIPRJMOD}`, and no nickname here is one KiCad's tables
use, so the answers do not depend on the machine. Only the library rules are
on. Each case is one part; where it asks whether a difference between a
sheet's copy and the library counts, the library has a symbol of its own for
the case, drawn as `R` is, and the sheet's copy is that drawing changed one
way. The answers are recorded in `erc3/erc3.kicad.json`. To change the
design: edit this, run it, run KiCad's ERC on a copy of the result
(`kicad-cli sch erc --severity-all --format json erc3.kicad_sch`) and record
the answers again.
"""

import json
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from kicad_cli.fileformat import erc  # noqa: E402

OUT = Path(__file__).resolve().parent / "erc3"
NS = uuid.UUID("7a1c5d2e-0000-4000-8000-0000000e4c03")
PROJECT = "erc3"
# The library rules on, every other off.
RULES = {rule: ("warning" if rule in erc.LIBRARY else "ignore") for rule in sorted(erc.SEVERITIES)}


def uid(*parts) -> str:
    return str(uuid.uuid5(NS, "/".join(str(p) for p in parts)))


def num(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def xy(p) -> str:
    return f"{num(p[0])} {num(p[1])}"


FONT = "(effects (font (size 1.27 1.27)))"
HIDDEN = "(effects (font (size 1.27 1.27)) (hide yes))"

# -- symbols ------------------------------------------------------------------------------

# name, value, at, hidden
FIELDS = [
    ("Reference", "R", (2.032, 0), False),
    ("Value", "R", (0, 0), False),
    ("Footprint", "", (-1.778, 0), True),
    ("Datasheet", "~", (0, 0), True),
    ("Description", "Resistor", (0, 0), True),
    ("ki_fp_filters", "R_*", (0, 0), True),
]
# unit, type, shape, number, name, at, angle, length, hidden
PINS = [
    (1, "passive", "line", "1", "~", (0, 3.81), 270, 1.27, False),
    (1, "passive", "line", "2", "~", (0, -3.81), 90, 1.27, False),
]
RECTANGLE = {"start": (-1.016, -2.54), "end": (1.016, 2.54), "width": 0.254, "type": "default",
             "fill": "none"}  # fmt: skip
ARC = {"start": (-1.016, 2.54), "mid": (0, 3.556), "end": (1.016, 2.54)}
TEXT = {"text": "R", "at": (0, -1.27), "size": 1.27}
BOX = {"text": "T", "at": (-5.08, 2.54)}
FLAGS = {"exclude_from_sim": "no", "in_bom": "yes", "on_board": "yes"}


def drawn(name, *, value=None, fields=None, pins=None, rectangle=None, arc=None, flags=None,
          pin_numbers_hidden=True, pin_names="(pin_names (offset 0))", power=False,
          with_arc=True, extra_shape="", unit_name=None, text=None, box=None) -> str:  # fmt: skip
    """A symbol drawn as R is, but for what is given."""
    short = name.rpartition(":")[2]
    fields = fields if fields is not None else [
        (n, value if (n == "Value" and value) else v, at, h) for n, v, at, h in FIELDS
    ]  # fmt: skip
    pins = pins if pins is not None else PINS
    rect = dict(RECTANGLE, **(rectangle or {}))
    words = dict(TEXT, **(text or {}))
    boxed = dict(BOX, **(box or {}))
    arc = arc or ARC
    flags = dict(FLAGS, **(flags or {}))
    text = f"(symbol {q(name)}" + (" (power)" if power else "")
    if pin_numbers_hidden:
        text += " (pin_numbers (hide yes))"
    text += f" {pin_names}"
    text += "".join(f" ({k} {v})" for k, v in flags.items())
    for field_name, field_value, at, hidden in fields:
        text += f" (property {q(field_name)} {q(field_value)} (at {xy(at)} 0) {HIDDEN if hidden else FONT})"
    text += (
        f" (symbol {q(short + '_0_1')}"
        f" (rectangle (start {xy(rect['start'])}) (end {xy(rect['end'])})"
        f" (stroke (width {num(rect['width'])}) (type {rect['type']}){rect.get('color', '')})"
        f" (fill (type {rect['fill']})))"
        f" (text {q(words['text'])} (at {xy(words['at'])} 0)"
        f" (effects (font (size {num(words['size'])} {num(words['size'])}))))"
        f" (text_box {q(boxed['text'])} (at {xy(boxed['at'])} 0) (size 2.54 -1.27)"
        f" (margins 0.254 0.254 0.254 0.254) (stroke (width 0) (type default)) (fill (type none))"
        f" (effects (font (size 0.508 0.508))))"
    )
    if with_arc:
        text += (
            f" (arc (start {xy(arc['start'])}) (mid {xy(arc['mid'])}) (end {xy(arc['end'])})"
            f" (stroke (width 0) (type default)) (fill (type none)))"
        )
    text += f"{extra_shape})"
    for unit in sorted({p[0] for p in pins}):
        text += f" (symbol {q(f'{short}_{unit}_1')}"
        if unit_name:
            text += f" (unit_name {q(unit_name)})"
        for _, etype, shape, number, pin_name, at, angle, length, hidden in pins:
            if _ != unit:
                continue
            text += (
                f" (pin {etype} {shape} (at {xy(at)} {angle}) (length {num(length)})"
                + (" (hide yes)" if hidden else "")
                + f" (name {q(pin_name)} {FONT}) (number {q(number)} {FONT}))"
            )
        text += ")"
    return text + ")"


def pin(which, **changes):
    """R's pin `which`, changed."""
    names = ("unit", "etype", "shape", "number", "name", "at", "angle", "length", "hidden")
    base = dict(zip(names, next(p for p in PINS if p[3] == which), strict=True))
    base.update(changes)
    return tuple(base[n] for n in names)


def field(name, **changes):
    names = ("name", "value", "at", "hidden")
    base = dict(zip(names, next(f for f in FIELDS if f[0] == name), strict=True))
    base.update(changes)
    return tuple(base[n] for n in names)


def fields_without(*names):
    return [f for f in FIELDS if f[0] not in names]


# Library symbols by name, as the library holds them.
LIBRARY: dict[str, str] = {
    "R": drawn("R"),
    "C": drawn("C", fields=[
        ("Reference", "C", (2.032, 0), False), ("Value", "C", (0, 0), False),
        ("Footprint", "", (-1.778, 0), True), ("Datasheet", "~", (0, 0), True),
        ("Description", "Capacitor", (0, 0), True),
        ("ki_fp_filters", "erc3_fp:C_*", (0, 0), True),
    ]),
    "NOFILTER": drawn("NOFILTER", fields=fields_without("ki_fp_filters")),
    # Derived: R's drawing, its own fields.
    "R_derived": "(symbol \"R_derived\" (extends \"R\")"
    + "".join(
        f" (property {q(n)} {q(v)} (at {xy(at)} 0) {HIDDEN if h else FONT})"
        for n, v, at, h in [field("Value", value="R_derived"), field("Description", value="Derived")]
        + [f for f in FIELDS if f[0] not in ("Value", "Description")]
    )
    + ")",
}  # fmt: skip
DERIVED_FIELDS = [field("Value", value="R_derived"), field("Description", value="Derived")] + [
    f for f in FIELDS if f[0] not in ("Value", "Description")
]

ITEMS: dict[str, list[str]] = {}
CASES: dict[str, str] = {}
EMBEDDED: dict[str, str] = {}  # the sheet's copies by name
PLACED: list[str] = []
REFS: dict[str, int] = {}


def ref(prefix):
    REFS[prefix] = REFS.get(prefix, 0) + 1
    return f"{prefix}{REFS[prefix]}"


def place(case, question, lib_id, *, lib_name=None, footprint="", numbers=("1", "2")):
    """A part: one case."""
    index = len(CASES)
    CASES[case] = question
    x, y = 25.4 + 30.48 * (index % 8), 25.4 + 30.48 * (index // 8)
    sid = uid("sym", case)
    ITEMS[case] = [sid]
    short = lib_id.rpartition(":")[2]
    reference = ref("C" if short == "C" else "R")
    text = "(symbol" + (f" (lib_name {q(lib_name)})" if lib_name else "")
    text += (
        f" (lib_id {q(lib_id)}) (at {xy((x, y))} 0) (unit 1)"
        f" (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no) (uuid {q(sid)})"
        f' (property "Reference" {q(reference)} (at {xy((x + 2.54, y))} 0) {FONT})'
        f' (property "Value" {q(short)} (at {xy((x + 5.08, y))} 0) {FONT})'
        f' (property "Footprint" {q(footprint)} (at {xy((x, y))} 0) {HIDDEN})'
        f' (property "Datasheet" "~" (at {xy((x, y))} 0) {HIDDEN})'
        f' (property "Description" "" (at {xy((x, y))} 0) {HIDDEN})'
    )
    for number in numbers:
        text += f" (pin {q(number)} (uuid {q(uid(sid, 'pin', number))}))"
    text += f" (instances (project {q(PROJECT)} (path {q('/' + ROOT)} (reference {q(reference)}) (unit 1))))"
    PLACED.append(text + ")")


ROOT = uid("sheet", "root")


def copy_of(case, question, **changes):
    """A part whose library symbol is R's drawing under the case's name, and
    whose sheet copy is that drawing changed."""
    name = f"R_{case}"
    LIBRARY[name] = drawn(name, value=name)
    numbers = changes.pop("numbers", ("1", "2"))
    if "fields" in changes:
        changes["fields"] = [
            (n, name if n == "Value" else v, at, h) for n, v, at, h in changes["fields"]
        ]
    EMBEDDED[f"erc3:{name}"] = drawn(f"erc3:{name}", value=name, **changes)
    place(case, question, f"erc3:{name}", numbers=numbers)


# -- the cases ----------------------------------------------------------------------------

EMBEDDED["erc3:R"] = drawn("erc3:R")
place("same", "a copy as the library has it", "erc3:R")

copy_of("pin_moved", "a pin moved", pins=[PINS[0], pin("2", at=(0, -5.08))])
copy_of("pin_longer", "a pin longer", pins=[PINS[0], pin("2", length=2.54)])
copy_of("pin_turned", "a pin pointing another way", pins=[PINS[0], pin("2", angle=270)])
copy_of("pin_renumbered", "a pin numbered otherwise", pins=[PINS[0], pin("2", number="3")],
        numbers=("1", "3"))  # fmt: skip
copy_of("pin_renamed", "a pin named otherwise", pins=[pin("1", name="A"), PINS[1]])
copy_of("pin_hidden", "a pin hidden", pins=[pin("1", hidden=True), PINS[1]])
copy_of("pin_type", "a pin of another electrical type", pins=[pin("1", etype="input"), PINS[1]])
copy_of("pin_shape", "a pin of another shape", pins=[pin("1", shape="inverted"), PINS[1]])
copy_of("pin_numbers_shown", "pin numbers shown", pin_numbers_hidden=False)
copy_of("arc_reversed", "an arc drawn the other way round",
        arc={"start": ARC["end"], "mid": ARC["mid"], "end": ARC["start"]})  # fmt: skip
copy_of("line_width", "a line wider", rectangle={"width": 0.3048})
copy_of("line_solid", "a line solid, not default", rectangle={"type": "solid"})
copy_of("line_dashed", "a line dashed", rectangle={"type": "dash"})
copy_of("fill", "a shape filled", rectangle={"fill": "background"})
copy_of("line_color", "a line coloured", rectangle={"color": " (color 255 0 0 1)"})
copy_of("text_moved", "a text elsewhere", text={"at": (0, 1.27)})
copy_of("text_changed", "a text saying otherwise", text={"text": "Q"})
copy_of("text_bigger", "a text bigger", text={"size": 1.524})
copy_of("text_added", "a text more",
        extra_shape=' (text "X" (at 0 1.27 0) (effects (font (size 1.27 1.27))))')  # fmt: skip
copy_of("text_box_moved", "a text box elsewhere", box={"at": (-5.08, 5.08)})
copy_of("text_box_changed", "a text box saying otherwise", box={"text": "U"})
copy_of("shape_moved", "a shape bigger", rectangle={"end": (1.016, 2.794)})
copy_of("field_moved", "a field elsewhere and shown",
        fields=[field("Datasheet", at=(2.54, 2.54), hidden=False)] + fields_without("Datasheet"))  # fmt: skip
copy_of("field_value", "a field's value otherwise",
        fields=[field("Description", value="Other")] + fields_without("Description"))  # fmt: skip
copy_of("field_tilde", "an empty field as \"\", the library's as \"~\"",
        fields=[field("Datasheet", value="")] + fields_without("Datasheet"))  # fmt: skip
copy_of("field_missing_empty", "a field the library has empty missing",
        fields=fields_without("Footprint"))  # fmt: skip
copy_of("field_missing", "a field the library has a value for missing",
        fields=fields_without("Description"))  # fmt: skip
copy_of("field_added_empty", "an empty field the library lacks",
        fields=[*FIELDS, ("MPN", "", (0, 0), True)])  # fmt: skip
copy_of("field_added", "a field the library lacks",
        fields=[*FIELDS, ("MPN", "RC0805", (0, 0), True)])  # fmt: skip
copy_of("flags", "left out of the BOM and the board", flags={"in_bom": "no", "on_board": "no"})
copy_of("unit_added", "a unit more",
        pins=[*PINS, (2, "passive", "line", "3", "~", (0, 3.81), 270, 1.27, False)])  # fmt: skip
copy_of("pin_added", "a pin more", pins=[*PINS, pin("2", number="3", at=(2.54, 0), angle=180)],
        numbers=("1", "2", "3"))  # fmt: skip
copy_of("pin_removed", "a pin fewer", pins=[PINS[0]], numbers=("1",))
copy_of("shape_added", "a line more",
        extra_shape=" (polyline (pts (xy -1.016 0) (xy 1.016 0)) (stroke (width 0) (type default))"
        " (fill (type none)))")  # fmt: skip
copy_of("shape_removed", "a shape fewer", with_arc=False)
copy_of("power", "a power symbol", power=True)
copy_of("pin_names_offset", "pin names further in", pin_names="(pin_names (offset 0.508))")
copy_of("pin_names_hidden", "pin names hidden", pin_names="(pin_names (offset 0) (hide yes))")
copy_of("unit_named", "a unit named", unit_name="A")

EMBEDDED["R_1"] = drawn("R_1")
place(
    "lib_name_same", "a second copy, by lib_name, as the library has it", "erc3:R", lib_name="R_1"
)
EMBEDDED["R_2"] = drawn("R_2", pins=[PINS[0], pin("2", at=(0, -5.08))])
place("lib_name_changed", "a second copy, by lib_name, a pin moved", "erc3:R", lib_name="R_2")

EMBEDDED["erc3:R_derived"] = drawn("erc3:R_derived", fields=DERIVED_FIELDS)
place("derived_same", "a derived symbol as the library has it", "erc3:R_derived")
EMBEDDED["R_3"] = drawn("R_3", fields=[field("Description", value="Other")]
                        + [f for f in DERIVED_FIELDS if f[0] != "Description"])  # fmt: skip
place("derived_changed", "a derived symbol, a field's value otherwise", "erc3:R_derived",
      lib_name="R_3")  # fmt: skip

EMBEDDED["erc3:NOPE"] = drawn("erc3:NOPE")
place("symbol_not_in_library", "a symbol its library does not have", "erc3:NOPE")
EMBEDDED["erc3_nowhere:R"] = drawn("erc3_nowhere:R")
place("library_unknown", "a symbol library no table names", "erc3_nowhere:R")
EMBEDDED["erc3_gone:R"] = drawn("erc3_gone:R")
place("library_file_gone", "a symbol library whose file is not there", "erc3_gone:R")
EMBEDDED["erc3_off:R"] = drawn("erc3_off:R")
place("library_disabled", "a symbol library the table disables", "erc3_off:R")

place("footprint_found", "a footprint found", "erc3:R", footprint="erc3_fp:R_0805")
place("footprint_not_in_library", "a footprint its library does not have", "erc3:R",
      footprint="erc3_fp:R_1206")  # fmt: skip
place("footprint_library_unknown", "a footprint library no table names", "erc3:R",
      footprint="erc3_fp_nowhere:R_0805")  # fmt: skip
place("footprint_library_gone", "a footprint library whose folder is not there", "erc3:R",
      footprint="erc3_fp_gone:R_0805")  # fmt: skip
place("filter_mismatch", "a footprint the symbol's filters do not allow", "erc3:R",
      footprint="erc3_fp:C_0805")  # fmt: skip
EMBEDDED["erc3:C"] = drawn("erc3:C", fields=[
    ("Reference", "C", (2.032, 0), False), ("Value", "C", (0, 0), False),
    ("Footprint", "", (-1.778, 0), True), ("Datasheet", "~", (0, 0), True),
    ("Description", "Capacitor", (0, 0), True), ("ki_fp_filters", "erc3_fp:C_*", (0, 0), True),
])  # fmt: skip
place("filter_with_library", "a filter naming the library, matched", "erc3:C",
      footprint="erc3_fp:C_0805")  # fmt: skip
place("filter_with_library_other", "a filter naming the library, another library's footprint",
      "erc3:C", footprint="erc3_fp_nowhere:C_0805")  # fmt: skip
EMBEDDED["erc3:NOFILTER"] = drawn("erc3:NOFILTER", fields=fields_without("ki_fp_filters"))
place("no_filters", "a symbol with no filters", "erc3:NOFILTER", footprint="erc3_fp:C_0805")
place("no_footprint", "no footprint", "erc3:R")

# -- the files ----------------------------------------------------------------------------

if OUT.exists():
    shutil.rmtree(OUT)
(OUT / "erc3.pretty").mkdir(parents=True)


def write(name: str, text: str) -> None:
    (OUT / name).write_bytes(text.encode("utf-8"))


write(
    "erc3.kicad_sch",
    f'(kicad_sch (version 20260101) (generator "eeschema") (generator_version "10.0")'
    f' (uuid {q(ROOT)}) (paper "A3") (lib_symbols {" ".join(EMBEDDED[n] for n in sorted(EMBEDDED))}) '
    + " ".join(PLACED)
    + ' (sheet_instances (path "/" (page "1"))) (embedded_fonts no))\n',
)
write(
    "erc3.kicad_sym",
    '(kicad_symbol_lib (version 20251024) (generator "kicad_symbol_editor")'
    ' (generator_version "10.0") ' + " ".join(LIBRARY[n] for n in sorted(LIBRARY)) + ")\n",
)
for footprint in ("R_0805", "C_0805"):
    write(
        f"erc3.pretty/{footprint}.kicad_mod",
        f'(footprint {q(footprint)} (version 20260206) (generator "pcbnew")'
        f' (generator_version "10.0") (layer "F.Cu") (attr smd)'
        f' (pad "1" smd rect (at -0.9 0) (size 1 1.4) (layers "F.Cu" "F.Mask" "F.Paste"))'
        f' (pad "2" smd rect (at 0.9 0) (size 1 1.4) (layers "F.Cu" "F.Mask" "F.Paste"))'
        f" (embedded_fonts no))\n",
    )
write(
    "sym-lib-table",
    "(sym_lib_table (version 7)\n"
    '  (lib (name "erc3") (type "KiCad") (uri "${KIPRJMOD}/erc3.kicad_sym") (options "") (descr ""))\n'
    '  (lib (name "erc3_gone") (type "KiCad") (uri "${KIPRJMOD}/gone.kicad_sym") (options "") (descr ""))\n'
    '  (lib (name "erc3_off") (type "KiCad") (uri "${KIPRJMOD}/erc3.kicad_sym") (options "") (descr "") (disabled))\n'
    ")\n",
)
write(
    "fp-lib-table",
    "(fp_lib_table (version 7)\n"
    '  (lib (name "erc3_fp") (type "KiCad") (uri "${KIPRJMOD}/erc3.pretty") (options "") (descr ""))\n'
    '  (lib (name "erc3_fp_gone") (type "KiCad") (uri "${KIPRJMOD}/gone.pretty") (options "") (descr ""))\n'
    ")\n",
)
project = {
    "erc": {"rule_severities": RULES},
    "meta": {"filename": "erc3.kicad_pro", "version": 3},
    "net_settings": {"classes": [{"name": "Default"}], "meta": {"version": 5}},
}
write("erc3.kicad_pro", json.dumps(project, indent=2) + "\n")
write("cases.json", json.dumps({"questions": CASES, "items": ITEMS}, indent=1) + "\n")
print(f"{len(CASES)} cases")
