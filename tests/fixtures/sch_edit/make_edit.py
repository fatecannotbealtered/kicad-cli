"""Write edit.kicad_sch and its child: a small design for `sch edit` to change.

Every way a net is named is here, once: local labels, a global label on both
sheets, power symbols, a hierarchical label with the sheet pin that meets it,
and a sheet placed twice -- so one drawing carries two references and two
nets. And a part of two units. Nothing else, so what an edit changes is easy
to see.

    /SIG             R1.1 R2.1 R10.1   local labels; CHILD_A's pin IN on it
    /OUT             R1.2 U1.1         local labels
    EN               R2.2 C10.2 C20.2  global labels, both sheets
    +3V3, GND        R3.1, R3.2        power symbols
    /CHILD_A/LOCAL   R10.2 C10.1       a local label in the child
    /CHILD_B/LOCAL   R20.2 C20.1       the same label, the other placement
    /CHILD_B/IN      R20.1             named by the child's hierarchical label
    Net-(R4-Pad2)    R4.2 R5.2         a wire, and nothing naming it

and pins free to connect, flagged: R4.1, R5.1, and R11.1 and R11.2 (R21 in
the other placement) in the child.

To change the design: edit this and run it.
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from kicad_cli.fileformat.schematic import LibPin, Symbol  # noqa: E402
from kicad_cli.fileformat.sexpr import Document, copy, render  # noqa: E402

OUT = Path(__file__).resolve().parent
NS = uuid.UUID("7a1c5d2e-0000-4000-8000-00000000ed17")
PROJECT = "edit"


def uid(*parts) -> str:
    return str(uuid.uuid5(NS, "/".join(str(p) for p in parts)))


def num(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


FONT = "(effects (font (size 1.27 1.27)))"
HIDDEN = "(hide yes) (effects (font (size 1.27 1.27)))"


def pin(etype, number, name, x, y, angle, hidden=False):
    h = " (hide yes)" if hidden else ""
    return (
        f"(pin {etype} line (at {num(x)} {num(y)} {angle}) (length 2.54){h}"
        f" (name {q(name)} {FONT}) (number {q(number)} {FONT}))"
    )


LIB: dict[str, str] = {}
PINS: dict[tuple[str, int], list] = {}


def define(name, units, power=False, ref="U"):
    """units: {unit: [(type, number, pin name, x, y, angle, hidden)]}"""
    body = f"(symbol {q(name)}" + (" (power)" if power else "")
    body += " (exclude_from_sim no) (in_bom yes) (on_board yes)"
    body += f' (property "Reference" {q(ref)} (at 0 3.81 0) {FONT})'
    body += f' (property "Value" {q(name.split(":")[1])} (at 0 -3.81 0) {FONT})'
    for field in ("Footprint", "Datasheet", "Description"):
        body += f" (property {q(field)} {q('')} (at 0 0 0) {HIDDEN})"
    short = name.split(":")[1]
    for unit, pins in units.items():
        body += f" (symbol {q(f'{short}_{unit}_1')} " + " ".join(pin(*p) for p in pins) + ")"
    LIB[name] = body + ")"
    for unit, pins in units.items():
        PINS[(name, unit)] = [(p[1], p[3], p[4], p[5]) for p in pins]


define("fixture:R", {1: [("passive", "1", "~", 0, 3.81, 270), ("passive", "2", "~", 0, -3.81, 90)]},
       ref="R")  # fmt: skip
define("fixture:DUAL", {
    1: [("output", "1", "OUT", 7.62, 0, 180), ("input", "2", "IN-", -7.62, 2.54, 0),
        ("input", "3", "IN+", -7.62, -2.54, 0)],
    2: [("output", "7", "OUT", 7.62, 0, 180), ("input", "6", "IN-", -7.62, 2.54, 0),
        ("input", "5", "IN+", -7.62, -2.54, 0)],
})  # fmt: skip
define("fixture:PWR", {1: [("power_in", "1", "PWR", 0, 0, 90, True)]}, power=True, ref="#PWR")


class Sheet:
    def __init__(self, name, file):
        self.name, self.file = name, file
        self.uuid = uid("sheet", name)
        self.items: list[str] = []
        self.libs: set[str] = set()

    def wire(self, a, b):
        self.items.append(
            f"(wire (pts (xy {num(a[0])} {num(a[1])}) (xy {num(b[0])} {num(b[1])}))"
            f" (stroke (width 0) (type default)) (uuid {q(uid(self.name, 'wire', a, b))}))"
        )

    def label(self, kind, text, p):
        shape = " (shape bidirectional)" if kind != "label" else ""
        self.items.append(
            f"({kind} {q(text)}{shape} (at {num(p[0])} {num(p[1])} 0) (fields_autoplaced yes)"
            f" (effects (font (size 1.27 1.27)) (justify left bottom))"
            f" (uuid {q(uid(self.name, kind, text, p))}))"
        )

    def no_connect(self, p):
        self.items.append(
            f"(no_connect (at {num(p[0])} {num(p[1])}) (uuid {q(uid(self.name, 'nc', p))}))"
        )

    def symbol(self, lib_id, refs, at, unit=1, value=None, footprint=""):
        """refs: {sheet instance path: reference}; the pins, where they land."""
        self.libs.add(lib_id)
        sid = uid(self.name, "sym", lib_id, at, unit)
        first = next(iter(refs.values()))
        value = value or lib_id.split(":")[1]
        text = (
            f"(symbol (lib_id {q(lib_id)}) (at {num(at[0])} {num(at[1])} 0) (unit {unit})"
            f" (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no) (uuid {q(sid)})"
            f' (property "Reference" {q(first)} (at {num(at[0] + 2.54)} {num(at[1] - 1.27)} 0)'
            f" {HIDDEN if first.startswith('#') else FONT})"
            f' (property "Value" {q(value)} (at {num(at[0] + 2.54)} {num(at[1] + 1.27)} 0) {FONT})'
        )
        fields = {"Footprint": footprint, "Datasheet": "", "Description": ""}
        for field, content in fields.items():
            text += f" (property {q(field)} {q(content)} (at {num(at[0])} {num(at[1])} 0) {HIDDEN})"
        defs = PINS[(lib_id, unit)]
        for number, *_ in defs:
            text += f" (pin {q(number)} (uuid {q(uid(sid, 'pin', number))}))"
        text += f" (instances (project {q(PROJECT)}"
        for path, reference in refs.items():
            text += f" (path {q(path)} (reference {q(reference)}) (unit {unit}))"
        self.items.append(text + ")))")
        placed = Symbol(lib_id, None, (round(at[0] * 1e6), round(at[1] * 1e6)), 0, None, unit, 1,
                        sid, {}, True, True, False, False, [], None)  # fmt: skip
        out = {}
        for number, x, y, angle in defs:
            lib_pin = LibPin(number, "", "", "", (round(x * 1e6), round(y * 1e6)), angle, 0, 0, 0)
            px, py = placed.transform(lib_pin.position)
            out[number] = (round(px / 1e6, 4), round(py / 1e6, 4))
        return out

    def power(self, net, at, path):
        self.symbol("fixture:PWR", {path: f"#PWR{len(self.items):02d}"}, at, value=net)

    def sheet(self, name, file, at, size, pins, pages):
        """pins: [(name, (x, y))]; pages: [(parent path, page)]"""
        sid = uid(self.name, "sheet", name)
        text = (
            f"(sheet (at {num(at[0])} {num(at[1])}) (size {num(size[0])} {num(size[1])})"
            f" (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no) (fields_autoplaced yes)"
            f" (stroke (width 0.1524) (type solid)) (fill (color 0 0 0 0.0000)) (uuid {q(sid)})"
            f' (property "Sheetname" {q(name)} (at {num(at[0])} {num(at[1] - 0.7)} 0) {FONT})'
            f' (property "Sheetfile" {q(file)} (at {num(at[0])} {num(at[1] + size[1] + 0.6)} 0)'
            f" {FONT})"
        )
        for pin_name, (px, py) in pins:
            text += (
                f" (pin {q(pin_name)} input (at {num(px)} {num(py)} 180)"
                f" (uuid {q(uid(sid, 'pin', pin_name))})"
                f" (effects (font (size 1.27 1.27)) (justify left)))"
            )
        text += f" (instances (project {q(PROJECT)}"
        for parent, page in pages:
            text += f" (path {q(parent)} (page {q(page)}))"
        self.items.append(text + ")))")
        return sid

    def write(self, root=False):
        libs = " ".join(LIB[name] for name in sorted(self.libs))
        text = (
            f'(kicad_sch (version 20250114) (generator "eeschema") (generator_version "9.0")'
            f' (uuid {q(self.uuid)}) (paper "A4") (lib_symbols {libs}) ' + " ".join(self.items)
        )
        if root:
            text += ' (sheet_instances (path "/" (page "1")))'
        # Laid out as KiCad lays out what it saves, so an edit's diff is the
        # few lines it changes.
        document = Document.parse(text + " (embedded_fonts no))", lazy=False)
        (OUT / self.file).write_bytes((render(copy(document.root)) + "\n").encode("utf-8"))


def stub(sheet, at, dy, kind, text):
    end = (at[0], round(at[1] + dy, 4))
    sheet.wire(at, end)
    sheet.label(kind, text, end)


R0805 = "fixture:R_0805"  # every part's footprint is the fixture's own library's
root = Sheet("root", "edit.kicad_sch")
ROOT = "/" + root.uuid
r1 = root.symbol("fixture:R", {ROOT: "R1"}, (50.8, 50.8), value="10k", footprint=R0805)
stub(root, r1["1"], -2.54, "label", "SIG")
stub(root, r1["2"], 2.54, "label", "OUT")
r2 = root.symbol("fixture:R", {ROOT: "R2"}, (63.5, 50.8), value="10k", footprint=R0805)
stub(root, r2["1"], -2.54, "label", "SIG")
stub(root, r2["2"], 2.54, "global_label", "EN")
r3 = root.symbol("fixture:R", {ROOT: "R3"}, (76.2, 50.8), value="1k", footprint=R0805)
root.power("+3V3", r3["1"], ROOT)
root.power("GND", r3["2"], ROOT)
u1 = root.symbol(
    "fixture:DUAL", {ROOT: "U1"}, (101.6, 50.8), unit=1, value="LM358", footprint="fixture:SO8"
)
root.wire(u1["1"], (111.76, 50.8))
root.label("label", "OUT", (111.76, 50.8))
root.no_connect(u1["2"])
root.no_connect(u1["3"])
r4 = root.symbol("fixture:R", {ROOT: "R4"}, (50.8, 76.2), value="1k", footprint=R0805)
r5 = root.symbol("fixture:R", {ROOT: "R5"}, (63.5, 76.2), value="1k", footprint=R0805)
root.no_connect(r4["1"])
root.no_connect(r5["1"])
root.wire(r4["2"], (50.8, 82.55))
root.wire((50.8, 82.55), (63.5, 82.55))
root.wire((63.5, 82.55), r5["2"])
u1b = root.symbol(
    "fixture:DUAL", {ROOT: "U1"}, (101.6, 76.2), unit=2, value="LM358", footprint="fixture:SO8"
)
for at in u1b.values():
    root.no_connect(at)
child_a = root.sheet("CHILD_A", "edit_child.kicad_sch", (127, 38.1), (15.24, 10.16),
                     [("IN", (127, 40.64))], [(ROOT, "2")])  # fmt: skip
root.wire((121.92, 40.64), (127, 40.64))
root.label("label", "SIG", (121.92, 40.64))
child_b = root.sheet("CHILD_B", "edit_child.kicad_sch", (127, 63.5), (15.24, 10.16),
                     [("IN", (127, 66.04))], [(ROOT, "3")])  # fmt: skip

child = Sheet("child", "edit_child.kicad_sch")
A, B = f"{ROOT}/{child_a}", f"{ROOT}/{child_b}"
r10 = child.symbol("fixture:R", {A: "R10", B: "R20"}, (50.8, 50.8), value="4k7", footprint=R0805)
stub(child, r10["1"], -2.54, "hierarchical_label", "IN")
stub(child, r10["2"], 2.54, "label", "LOCAL")
c10 = child.symbol("fixture:R", {A: "C10", B: "C20"}, (63.5, 50.8), value="100n", footprint=R0805)
stub(child, c10["1"], -2.54, "label", "LOCAL")
stub(child, c10["2"], 2.54, "global_label", "EN")
r11 = child.symbol("fixture:R", {A: "R11", B: "R21"}, (88.9, 50.8), value="10k", footprint=R0805)
child.no_connect(r11["1"])
child.no_connect(r11["2"])

root.write(root=True)
child.write()
(OUT / "edit.kicad_pro").write_bytes(
    b'{\n  "meta": {\n    "filename": "edit.kicad_pro",\n    "version": 3\n  }\n}\n'
)
print("written")
