"""Write rules.kicad_sch: a design that asks KiCad one question per net.

Every symbol here is drawn for the purpose -- nothing comes from KiCad's
libraries. Each case is wired so that KiCad's netlist of it answers one
question about how a schematic connects or names things; the answers are in
`tests/test_fileformat_circuit.py` as KICAD_SAID.

To change the design: edit this, run it, export KiCad's netlist of the
result (`kicad-cli sch export netlist --format kicadsexpr rules.kicad_sch`)
and record the nets again. Pin positions come from this tool's own
transform, so a wrong transform draws wires to where KiCad has no pin, and
KiCad's netlist says so.
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from kicad_cli.fileformat.schematic import LibPin, Symbol  # noqa: E402

OUT = Path(__file__).resolve().parent
OUT.mkdir(parents=True, exist_ok=True)
NS = uuid.UUID("7a1c5d2e-0000-4000-8000-00000000c1c1")
PROJECT = "rules"


def uid(*parts) -> str:
    return str(uuid.uuid5(NS, "/".join(str(p) for p in parts)))


def num(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


FONT = "(effects (font (size 1.27 1.27)))"
HIDDEN = "(effects (font (size 1.27 1.27)) (hide yes))"


def pin(etype, number, name, x, y, angle, hidden=False, length=1.27):
    h = " (hide yes)" if hidden else ""
    return (
        f"(pin {etype} line (at {num(x)} {num(y)} {angle}) (length {num(length)}){h}"
        f" (name {q(name)} {FONT}) (number {q(number)} {FONT}))"
    )


def lib_symbol(name, units, power=False, ref="U", extra=""):
    """units: {(unit, style): [pin strings]}"""
    body = f"(symbol {q(name)}"
    if power:
        body += " (power)"
    body += extra
    body += " (exclude_from_sim no) (in_bom yes) (on_board yes)"
    body += f' (property "Reference" {q(ref)} (at 0 3.81 0) {FONT})'
    body += f' (property "Value" {q(name.split(":")[1])} (at 0 -3.81 0) {FONT})'
    body += f' (property "Footprint" "" (at 0 0 0) {HIDDEN})'
    body += f' (property "Datasheet" "" (at 0 0 0) {HIDDEN})'
    body += f' (property "Description" "" (at 0 0 0) {HIDDEN})'
    short = name.split(":")[1]
    for (unit, style), pins in units.items():
        body += f" (symbol {q(f'{short}_{unit}_{style}')} " + " ".join(pins) + ")"
    return body + ")"


LIB = {
    "fixture:Q3": lib_symbol(
        "fixture:Q3",
        {
            (1, 1): [
                pin("input", "1", "B", -2.54, 0, 0),
                pin("passive", "2", "C", 2.54, 1.27, 180),
                pin("passive", "3", "E", 2.54, -1.27, 180),
            ]
        },
        ref="Q",
    ),
    "fixture:R": lib_symbol(
        "fixture:R",
        {(1, 1): [pin("passive", "1", "~", 0, 2.54, 270), pin("passive", "2", "~", 0, -2.54, 90)]},
        ref="R",
    ),
    "fixture:R_NONUM": lib_symbol(
        "fixture:R_NONUM",
        {(1, 1): [pin("passive", "1", "~", 0, 2.54, 270), pin("passive", "2", "~", 0, -2.54, 90)]},
        ref="R",
        extra=" (pin_numbers (hide yes))",
    ),
    "fixture:R_OFFSET": lib_symbol(
        "fixture:R_OFFSET",
        {(1, 1): [pin("passive", "1", "~", 0, 2.54, 270), pin("passive", "2", "~", 0, -2.54, 90)]},
        ref="R",
        extra=" (pin_names (offset 0))",
    ),
    "fixture:R_NONAME": lib_symbol(
        "fixture:R_NONAME",
        {(1, 1): [pin("passive", "1", "~", 0, 2.54, 270), pin("passive", "2", "~", 0, -2.54, 90)]},
        ref="R",
        extra=" (pin_names (offset 0) (hide yes))",
    ),
    "fixture:DUAL": lib_symbol(
        "fixture:DUAL",
        {
            (0, 1): [
                pin("power_in", "8", "V+", 0, 5.08, 270),
                pin("power_in", "4", "V-", 0, -5.08, 90),
            ],
            (1, 1): [
                pin("output", "1", "OUT", 5.08, 0, 180),
                pin("input", "2", "IN-", -5.08, 1.27, 0),
                pin("input", "3", "IN+", -5.08, -1.27, 0),
            ],
            (2, 1): [
                pin("output", "7", "OUT", 5.08, 0, 180),
                pin("input", "6", "IN-", -5.08, 1.27, 0),
                pin("input", "5", "IN+", -5.08, -1.27, 0),
            ],
        },
    ),
    "fixture:CONN": lib_symbol(
        "fixture:CONN",
        {
            (1, 1): [
                pin("passive", str(i), str(i), -2.54, 2.54 - 2.54 * (i - 1), 0) for i in (1, 2, 3)
            ]
        },
        ref="J",
    ),
    "fixture:DUP": lib_symbol(
        "fixture:DUP",
        {
            (1, 1): [
                pin("passive", "1", "GND", -2.54, 1.27, 0),
                pin("passive", "2", "GND", -2.54, -1.27, 0),
                pin("passive", "3", "SIG", 2.54, 0, 180),
            ]
        },
    ),
    "fixture:HIDDEN": lib_symbol(
        "fixture:HIDDEN",
        {
            (1, 1): [
                pin("passive", "1", "A", -2.54, 0, 0),
                pin("power_in", "2", "VDDX", 2.54, 0, 180, hidden=True),
            ]
        },
    ),
    "fixture:PWR": lib_symbol(
        "fixture:PWR",
        {(0, 1): [pin("power_in", "1", "PWR", 0, 0, 90, length=0)]},
        power=True,
        ref="#PWR",
    ),
}

PIN_DEFS = {
    "fixture:Q3": [("1", -2.54, 0, 0), ("2", 2.54, 1.27, 180), ("3", 2.54, -1.27, 180)],
    "fixture:R": [("1", 0, 2.54, 270), ("2", 0, -2.54, 90)],
    "fixture:R_NONUM": [("1", 0, 2.54, 270), ("2", 0, -2.54, 90)],
    "fixture:R_OFFSET": [("1", 0, 2.54, 270), ("2", 0, -2.54, 90)],
    "fixture:R_NONAME": [("1", 0, 2.54, 270), ("2", 0, -2.54, 90)],
    "fixture:CONN": [("1", -2.54, 2.54, 0), ("2", -2.54, 0, 0), ("3", -2.54, -2.54, 0)],
    "fixture:DUP": [("1", -2.54, 1.27, 0), ("2", -2.54, -1.27, 0), ("3", 2.54, 0, 180)],
    "fixture:HIDDEN": [("1", -2.54, 0, 0)],
    "fixture:PWR": [("1", 0, 0, 90)],
    ("fixture:DUAL", 1): [
        ("1", 5.08, 0, 180),
        ("2", -5.08, 1.27, 0),
        ("3", -5.08, -1.27, 0),
        ("8", 0, 5.08, 270),
        ("4", 0, -5.08, 90),
    ],
    ("fixture:DUAL", 2): [
        ("7", 5.08, 0, 180),
        ("6", -5.08, 1.27, 0),
        ("5", -5.08, -1.27, 0),
        ("8", 0, 5.08, 270),
        ("4", 0, -5.08, 90),
    ],
}


class Sheet:
    def __init__(self, name, root_uuid):
        self.name = name
        self.uuid = uid("sheet", name)
        self.root_uuid = root_uuid
        self.items = []
        self.symbol_libs = set()

    def wire(self, a, b):
        self.items.append(
            f"(wire (pts (xy {num(a[0])} {num(a[1])}) (xy {num(b[0])} {num(b[1])}))"
            f" (stroke (width 0) (type default)) (uuid {q(uid(self.name, 'w', a, b))}))"
        )

    def junction(self, p):
        self.items.append(
            f"(junction (at {num(p[0])} {num(p[1])}) (diameter 0) (color 0 0 0 0) (uuid {q(uid(self.name, 'j', p))}))"
        )

    def label(self, kind, text, p, angle=0, shape=None):
        if kind in ("global_label", "hierarchical_label") and not shape:
            shape = "bidirectional"
        extra = f" (shape {shape})" if shape else ""
        fields = ""
        if kind == "global_label":
            fields = (
                f' (property "Intersheetrefs" "${{INTERSHEET_REFS}}" (at {num(p[0])} {num(p[1])} 0)'
                f" {HIDDEN})"
            )
        self.items.append(
            f"({kind} {q(text)}{extra} (at {num(p[0])} {num(p[1])} {angle}) (fields_autoplaced yes)"
            f" (effects (font (size 1.27 1.27)) (justify left bottom)) (uuid {q(uid(self.name, kind, text, p))}){fields})"
        )

    def symbol(self, lib_id, refs, at, angle=0, mirror=None, unit=1, value=None, instances=None):
        """refs: {instance path: reference}"""
        self.symbol_libs.add(lib_id)
        sid = uid(self.name, "sym", lib_id, at, unit)
        m = f" (mirror {mirror})" if mirror else ""
        first_ref = next(iter(refs.values()))
        val = value or lib_id.split(":")[1]
        text = (
            f"(symbol (lib_id {q(lib_id)}) (at {num(at[0])} {num(at[1])} {angle}){m} (unit {unit})"
            f" (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no) (uuid {q(sid)})"
            f' (property "Reference" {q(first_ref)} (at {num(at[0])} {num(at[1] - 5)} 0) {FONT})'
            f' (property "Value" {q(val)} (at {num(at[0])} {num(at[1] + 5)} 0) {FONT})'
            f' (property "Footprint" "" (at 0 0 0) {HIDDEN})'
            f' (property "Datasheet" "" (at 0 0 0) {HIDDEN})'
            f' (property "Description" "" (at 0 0 0) {HIDDEN})'
        )
        key = (lib_id, unit) if lib_id == "fixture:DUAL" else lib_id
        for number, *_ in PIN_DEFS[key]:
            text += f" (pin {q(number)} (uuid {q(uid(sid, 'pin', number))}))"
        text += f" (instances (project {q(PROJECT)}"
        for path, ref in refs.items():
            text += f" (path {q(path)} (reference {q(ref)}) (unit {unit}))"
        text += ")))"
        self.items.append(text)
        # Where its pins are, by this tool's own transform: a wrong transform
        # here draws wires to places KiCad has no pin, and KiCad says so.
        s = Symbol(
            lib_id,
            None,
            (round(at[0] * 1e6), round(at[1] * 1e6)),
            angle,
            mirror,
            unit,
            1,
            sid,
            {},
            True,
            True,
            False,
            False,
            [],
            None,
        )
        out = {}
        for number, x, y, pangle in PIN_DEFS[key]:
            lp = LibPin(number, "", "", "", (round(x * 1e6), round(y * 1e6)), pangle, 0, 0, 0)
            px, py = s.transform(lp.position)
            # the direction the pin points away from the body, in sheet terms
            dx, dy = {0: (-1, 0), 90: (0, -1), 180: (1, 0), 270: (0, 1)}[pangle]
            ox, oy = s.transform(
                (lp.position[0] + round(dx * 1e6), lp.position[1] + round(dy * 1e6))
            )
            out[number] = ((px / 1e6, py / 1e6), ((ox - px) / 1e6, (oy - py) / 1e6))
        return out

    def sheet_symbol(self, name, file, at, size, pins, page):
        sid = uid(self.name, "sheet", name)
        text = (
            f"(sheet (at {num(at[0])} {num(at[1])}) (size {num(size[0])} {num(size[1])}) (exclude_from_sim no)"
            f" (in_bom yes) (on_board yes) (dnp no) (fields_autoplaced yes) (stroke (width 0.1524) (type solid))"
            f" (fill (color 0 0 0 0.0000)) (uuid {q(sid)})"
            f' (property "Sheetname" {q(name)} (at {num(at[0])} {num(at[1] - 0.7)} 0) {FONT})'
            f' (property "Sheetfile" {q(file)} (at {num(at[0])} {num(at[1] + size[1] + 0.6)} 0) {FONT})'
        )
        for pname, (px, py) in pins:
            text += (
                f" (pin {q(pname)} bidirectional (at {num(px)} {num(py)} 180)"
                f" (uuid {q(uid(sid, 'pin', pname))}) (effects (font (size 1.27 1.27)) (justify left)))"
            )
        text += (
            f" (instances (project {q(PROJECT)} (path {q('/' + self.root_uuid)} (page {q(page)}))))"
        )
        text += ")"
        self.items.append(text)
        return sid

    def write(self, path, root=False):
        libs = " ".join(LIB[name] for name in sorted(self.symbol_libs))
        text = (
            f'(kicad_sch (version 20260101) (generator "eeschema") (generator_version "10.0")'
            f' (uuid {q(self.uuid)}) (paper "A3") (lib_symbols {libs}) ' + " ".join(self.items)
        )
        if root:
            text += ' (sheet_instances (path "/" (page "1")))'
        text += " (embedded_fonts no))"
        path.write_text(text + "\n", encoding="utf-8")


root = Sheet("root", None)
root.uuid = uid("root")
root.root_uuid = root.uuid
ROOT_PATH = "/" + root.uuid


def lead(sheet, pin_at, label_text, kind="label", length=2.54):
    (px, py), (dx, dy) = pin_at
    end = (px + dx * length / 1.0, py + dy * length / 1.0)
    sheet.wire((px, py), end)
    sheet.label(kind, label_text, end)


# 1. Eight orientations of an asymmetric part, every pin led out to its own label.
orientations = [
    (0, None),
    (90, None),
    (180, None),
    (270, None),
    (0, "x"),
    (0, "y"),
    (90, "x"),
    (270, "x"),
]
for i, (angle, mirror) in enumerate(orientations):
    at = (30 + 25 * i, 30)
    pins = root.symbol("fixture:Q3", {ROOT_PATH: f"Q{i + 1}"}, at, angle, mirror)
    for number, where in pins.items():
        lead(root, where, f"ORIENT{i + 1}_P{number}")

# 2. Joins by geometry. R1..R6, pin 2 of each on one row of cases.
y = 70
# 2a. crossing wires without a junction: not joined; with one: joined.
r1 = root.symbol("fixture:R", {ROOT_PATH: "R1"}, (30, y))
r2 = root.symbol("fixture:R", {ROOT_PATH: "R2"}, (40, y))
root.wire(r1["2"][0], (r1["2"][0][0], y + 12.7))  # vertical from R1 pin 2 down
root.wire(
    (25.4, y + 7.62), (r2["2"][0][0] + 5.08, y + 7.62)
)  # horizontal, crossing it, no junction
root.wire(r2["2"][0], (r2["2"][0][0], y + 7.62))  # R2 pin 2 down to the horizontal: T end on it
root.label("label", "CROSS_H", (25.4, y + 7.62))
root.label("label", "CROSS_V", (r1["2"][0][0], y + 12.7))
r3 = root.symbol("fixture:R", {ROOT_PATH: "R3"}, (60, y))
r4 = root.symbol("fixture:R", {ROOT_PATH: "R4"}, (70, y))
root.wire(r3["2"][0], (r3["2"][0][0], y + 12.7))
root.wire((55.88, y + 7.62), (r4["2"][0][0], y + 7.62))
root.junction((r3["2"][0][0], y + 7.62))
root.wire(r4["2"][0], (r4["2"][0][0], y + 7.62))
root.label("label", "JUNCTION_H", (55.88, y + 7.62))
# 2b. a label on a wire's middle, and a pin on a wire's middle.
r5 = root.symbol("fixture:R", {ROOT_PATH: "R5"}, (90, y))
root.wire(
    (r5["2"][0][0] - 5.08, r5["2"][0][1]), (r5["2"][0][0] + 5.08, r5["2"][0][1])
)  # passes through pin 2
root.label("label", "MIDWIRE", (r5["2"][0][0] + 2.54, r5["2"][0][1]))
lead(root, r5["1"], "R5_TOP")
# 2c. a label in the middle of a wire that runs from a pin's end.
r12 = root.symbol("fixture:R", {ROOT_PATH: "R12"}, (110, y))
end = (r12["2"][0][0], r12["2"][0][1] + 7.62)
root.wire(r12["2"][0], end)
root.label("label", "ALONG", (r12["2"][0][0], r12["2"][0][1] + 3.81))
lead(root, r12["1"], "R12_TOP")
# 2d. a pin on a wire's middle with a junction there.
r13 = root.symbol("fixture:R", {ROOT_PATH: "R13"}, (125, y))
root.wire((r13["2"][0][0] - 5.08, r13["2"][0][1]), (r13["2"][0][0] + 5.08, r13["2"][0][1]))
root.junction(r13["2"][0])
root.label("label", "PIN_JUNCTION", (r13["2"][0][0] + 5.08, r13["2"][0][1]))
lead(root, r13["1"], "R13_TOP")
lead(root, r1["1"], "R1_TOP")
lead(root, r2["1"], "R2_TOP")
lead(root, r3["1"], "R3_TOP")
lead(root, r4["1"], "R4_TOP")

# 3. Names. A power symbol named by its value, a local label of the same
# name on the same sheet, a global label, a hidden power pin.
y = 110
r6 = root.symbol("fixture:R", {ROOT_PATH: "R6"}, (30, y))
p1 = root.symbol("fixture:PWR", {ROOT_PATH: "#PWR01"}, (30, y - 7.62), value="VBUS")
root.wire(p1["1"][0], r6["1"][0])
lead(root, r6["2"], "VBUS")  # local label named like the power net
r7 = root.symbol("fixture:R", {ROOT_PATH: "R7"}, (45, y))
lead(root, r7["1"], "SHARED", kind="global_label")
lead(root, r7["2"], "R7_BOTTOM")
h1 = root.symbol("fixture:HIDDEN", {ROOT_PATH: "U3"}, (60, y))
lead(root, h1["1"], "HIDDEN_A")
r8 = root.symbol("fixture:R", {ROOT_PATH: "R8"}, (75, y))
lead(root, r8["1"], "VDDX", kind="global_label")  # joins the hidden pin's net
# 4. Pins without names: name == number, and names used twice on one part.
j1 = root.symbol("fixture:CONN", {ROOT_PATH: "J1"}, (95, y))
r9 = root.symbol("fixture:R", {ROOT_PATH: "R9"}, (110, y))
root.wire(j1["1"][0], (j1["1"][0][0] - 5.08, j1["1"][0][1]))
root.wire((j1["1"][0][0] - 5.08, j1["1"][0][1]), (j1["1"][0][0] - 5.08, y - 12.7))
root.wire((j1["1"][0][0] - 5.08, y - 12.7), (r9["1"][0][0], y - 12.7))
root.wire((r9["1"][0][0], y - 12.7), r9["1"][0])
d1 = root.symbol("fixture:DUP", {ROOT_PATH: "U4"}, (130, y))
r10 = root.symbol("fixture:R", {ROOT_PATH: "R10"}, (140, y))
root.wire(d1["1"][0], (d1["1"][0][0] - 3.81, d1["1"][0][1]))
root.wire((d1["1"][0][0] - 3.81, d1["1"][0][1]), (d1["1"][0][0] - 3.81, y + 10.16))
root.wire((d1["1"][0][0] - 3.81, y + 10.16), (r10["2"][0][0], y + 10.16))
root.wire((r10["2"][0][0], y + 10.16), r10["2"][0])
# 5. A part of two units, its units on either side; a pin left alone.
u1a = root.symbol("fixture:DUAL", {ROOT_PATH: "U1"}, (170, y), unit=1)
u1b = root.symbol("fixture:DUAL", {ROOT_PATH: "U1"}, (200, y), unit=2)
r11 = root.symbol("fixture:R", {ROOT_PATH: "R11"}, (185, y + 20))
root.wire(u1a["1"][0], (u1a["1"][0][0] + 3.81, u1a["1"][0][1]))
root.wire((u1a["1"][0][0] + 3.81, u1a["1"][0][1]), (u1a["1"][0][0] + 3.81, y + 12.7))
root.wire((u1a["1"][0][0] + 3.81, y + 12.7), (r11["1"][0][0], y + 12.7))
root.wire((r11["1"][0][0], y + 12.7), r11["1"][0])
lead(root, u1b["6"], "U1B_INV")

# 6. The same child sheet placed twice, a hierarchical label per instance.
child = Sheet("child", root.uuid)
child.uuid = uid("child")
p_a = root.sheet_symbol(
    "amp_a", "rules_child.kicad_sch", (40, 160), (20, 10), [("IO", (40, 165))], "2"
)
p_b = root.sheet_symbol(
    "amp_b", "rules_child.kicad_sch", (80, 160), (20, 10), [("IO", (80, 165))], "3"
)
root.wire((40, 165), (33.02, 165))
root.label("label", "IO_A", (33.02, 165))
root.wire((80, 165), (73.02, 165))
root.label("global_label", "IO_B", (73.02, 165))
path_a, path_b = f"{ROOT_PATH}/{p_a}", f"{ROOT_PATH}/{p_b}"
cr = child.symbol("fixture:R", {path_a: "R101", path_b: "R201"}, (50, 50))
child.wire(cr["1"][0], (cr["1"][0][0], cr["1"][0][1] - 5.08))
child.label(
    "hierarchical_label", "IO", (cr["1"][0][0], cr["1"][0][1] - 5.08), shape="bidirectional"
)
lead(child, cr["2"], "LOCAL")
cg = child.symbol("fixture:R", {path_a: "R102", path_b: "R202"}, (70, 50))
lead(child, cg["1"], "SHARED", kind="global_label")  # the root's global label
cp = child.symbol(
    "fixture:PWR", {path_a: "#PWR101", path_b: "#PWR201"}, (70, 50 + 7.62), value="VBUS", angle=180
)
child.wire(cp["1"][0], cg["2"][0])

# 7. "~" as a pin's name, with the symbol's pin numbers or names hidden.
for i, lib in enumerate(("fixture:R_NONUM", "fixture:R_OFFSET", "fixture:R_NONAME")):
    root.symbol(lib, {ROOT_PATH: f"R{30 + i}"}, (150 + 15 * i, 160))

# 8. Two pins of one part joined to each other and to nothing else: by a
# wire, and (a two-unit part) across its units.
d2 = root.symbol("fixture:DUP", {ROOT_PATH: "U5"}, (150, 190))
root.wire(d2["1"][0], (d2["1"][0][0] - 2.54, d2["1"][0][1]))
root.wire((d2["1"][0][0] - 2.54, d2["1"][0][1]), (d2["2"][0][0] - 2.54, d2["2"][0][1]))
root.wire((d2["2"][0][0] - 2.54, d2["2"][0][1]), d2["2"][0])
r40 = root.symbol("fixture:R", {ROOT_PATH: "R40"}, (170, 190))
root.wire(r40["1"][0], (r40["1"][0][0] + 5.08, r40["1"][0][1]))
root.wire((r40["1"][0][0] + 5.08, r40["1"][0][1]), (r40["2"][0][0] + 5.08, r40["2"][0][1]))
root.wire((r40["2"][0][0] + 5.08, r40["2"][0][1]), r40["2"][0])
u2a = root.symbol("fixture:DUAL", {ROOT_PATH: "U2"}, (200, 190), unit=1)
u2b = root.symbol("fixture:DUAL", {ROOT_PATH: "U2"}, (230, 190), unit=2)
root.wire(u2a["1"][0], (u2a["1"][0][0], u2a["1"][0][1] + 15))
root.wire((u2a["1"][0][0], u2a["1"][0][1] + 15), (u2b["6"][0][0], u2a["1"][0][1] + 15))
root.wire((u2b["6"][0][0], u2a["1"][0][1] + 15), u2b["6"][0])

root.write(OUT / "rules.kicad_sch", root=True)
child.write(OUT / "rules_child.kicad_sch")
(OUT / "rules.kicad_pro").write_text(
    '{"meta": {"filename": "rules.kicad_pro", "version": 3}}\n', encoding="utf-8"
)
print("wrote", OUT)
