"""Write erc2.kicad_sch and its sheets: the second design asking KiCad's ERC
one question per case -- the rules about references, parts of several units,
the hierarchy, buses, text variables and net classes.

Like `make_erc.py`, every symbol is drawn for the purpose, each case keeps to
itself, and the project turns every rule on. The answers are recorded in
`erc2.kicad.json`. To change the design: edit this, run it, run KiCad's ERC
on the result (`kicad-cli sch erc --severity-all --format json erc2.kicad_sch`)
and record the answers again.
"""

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from kicad_cli.fileformat.schematic import LibPin, Symbol  # noqa: E402

OUT = Path(__file__).resolve().parent
NS = uuid.UUID("7a1c5d2e-0000-4000-8000-0000000e4c02")
PROJECT = "erc2"
# The same rules as the first design's, all on.
RULES = json.loads((OUT / "erc.kicad_pro").read_text(encoding="utf-8"))["erc"]["rule_severities"]


def uid(*parts) -> str:
    return str(uuid.uuid5(NS, "/".join(str(p) for p in parts)))


def num(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


FONT = "(effects (font (size 1.27 1.27)))"
HIDDEN = "(effects (font (size 1.27 1.27)) (hide yes))"


def pin(etype, number, name, x, y, angle, hidden=False, length=2.54):
    h = " (hide yes)" if hidden else ""
    return (
        f"(pin {etype} line (at {num(x)} {num(y)} {angle}) (length {num(length)}){h}"
        f" (name {q(name)} {FONT}) (number {q(number)} {FONT}))"
    )


LIB: dict[str, str] = {}
PIN_DEFS: dict[tuple[str, int], list] = {}


def define(name, units, power=False, ref="U"):
    """units: {unit: [(etype, number, pin name, x, y, angle, hidden)]}; unit 0
    is on every unit."""
    body = f"(symbol {q(name)}" + (" (power)" if power else "")
    body += " (exclude_from_sim no) (in_bom yes) (on_board yes)"
    body += f' (property "Reference" {q(ref)} (at 0 3.81 0) {FONT})'
    body += f' (property "Value" {q(name.split(":")[1])} (at 0 -3.81 0) {FONT})'
    body += f' (property "Footprint" "" (at 0 0 0) {HIDDEN})'
    body += f' (property "Datasheet" "" (at 0 0 0) {HIDDEN})'
    body += f' (property "Description" "" (at 0 0 0) {HIDDEN})'
    short = name.split(":")[1]
    for unit, pins in units.items():
        body += (
            f" (symbol {q(f'{short}_{unit}_1')} "
            + " ".join(pin(t, n, nm, x, y, a, h) for t, n, nm, x, y, a, h in pins)
            + ")"
        )
    LIB[name] = body + ")"
    common = units.get(0, [])
    for unit in [u for u in units if u != 0] or [1]:
        PIN_DEFS[(name, unit)] = [
            (n, x, y, a) for _, n, _, x, y, a, _ in units.get(unit, []) + common
        ]


define("fixture:R", {1: [("passive", "1", "~", 0, 3.81, 270, False),
                         ("passive", "2", "~", 0, -3.81, 90, False)]}, ref="R")  # fmt: skip
define("fixture:DUAL", {
    0: [("power_in", "8", "V+", 0, 7.62, 270, False), ("power_in", "4", "V-", 0, -7.62, 90, False)],
    1: [("output", "1", "OUT", 7.62, 0, 180, False), ("input", "2", "IN-", -7.62, 2.54, 0, False),
        ("input", "3", "IN+", -7.62, -2.54, 0, False)],
    2: [("output", "7", "OUT", 7.62, 0, 180, False), ("input", "6", "IN-", -7.62, 2.54, 0, False),
        ("input", "5", "IN+", -7.62, -2.54, 0, False)],
})  # fmt: skip
define("fixture:BIDI2", {1: [("bidirectional", "1", "IO1", -7.62, 0, 0, False)],
                         2: [("bidirectional", "2", "IO2", -7.62, 0, 0, False)]})  # fmt: skip
define("fixture:PWRUNIT", {1: [("passive", "1", "A", -7.62, 0, 0, False)],
                           2: [("power_in", "2", "VCC", -7.62, 0, 0, False)]})  # fmt: skip
define("fixture:PASS2", {1: [("passive", "1", "A", -7.62, 0, 0, False)],
                         2: [("passive", "2", "B", -7.62, 0, 0, False)]})  # fmt: skip
define("fixture:PWR", {0: [("power_in", "1", "PWR", 0, 0, 90, True)]}, power=True, ref="#PWR")
PIN_DEFS[("fixture:PWR", 1)] = [("1", 0, 0, 90)]
define("fixture:FLAG", {0: [("power_out", "1", "pwr", 0, 0, 90, True)]}, power=True, ref="#FLG")
PIN_DEFS[("fixture:FLAG", 1)] = [("1", 0, 0, 90)]

ITEMS: dict[str, list[str]] = {}
CASES: dict[str, str] = {}
_current = [""]


def track(item_uuid: str) -> str:
    ITEMS.setdefault(_current[0], []).append(item_uuid)
    return item_uuid


class Sheet:
    def __init__(self, name, file):
        self.name = name
        self.file = file
        self.uuid = uid("sheet", name)
        self.items: list[str] = []
        self.libs: set[str] = set()
        self.aliases: list[str] = []

    def add(self, text):
        self.items.append(text)

    def wire(self, a, b, kind="wire"):
        u = track(uid(self.name, kind, a, b))
        self.add(f"({kind} (pts (xy {num(a[0])} {num(a[1])}) (xy {num(b[0])} {num(b[1])}))"
                 f" (stroke (width 0) (type default)) (uuid {q(u)}))")  # fmt: skip
        return u

    def bus(self, a, b):
        return self.wire(a, b, "bus")

    def bus_entry(self, at, size=(2.54, 2.54)):
        u = track(uid(self.name, "entry", at))
        self.add(f"(bus_entry (at {num(at[0])} {num(at[1])}) (size {num(size[0])} {num(size[1])})"
                 f" (stroke (width 0) (type default)) (uuid {q(u)}))")  # fmt: skip
        return (at[0] + size[0], at[1] + size[1])

    def junction(self, p):
        u = track(uid(self.name, "j", p))
        self.add(
            f"(junction (at {num(p[0])} {num(p[1])}) (diameter 0) (color 0 0 0 0) (uuid {q(u)}))"
        )

    def no_connect(self, p):
        u = track(uid(self.name, "nc", p))
        self.add(f"(no_connect (at {num(p[0])} {num(p[1])}) (uuid {q(u)}))")

    def label(self, kind, text, p, shape=None, netclass=None):
        if kind in ("global_label", "hierarchical_label") and not shape:
            shape = "bidirectional"
        u = track(uid(self.name, kind, text, p))
        extra = f" (shape {shape})" if shape else ""
        if kind == "netclass_flag":
            extra = " (length 2.54) (shape round)"
        fields = ""
        if kind == "global_label":
            fields = (f' (property "Intersheetrefs" "${{INTERSHEET_REFS}}"'
                      f" (at {num(p[0])} {num(p[1])} 0) {HIDDEN})")  # fmt: skip
        if netclass is not None:
            fields = (
                f' (property "Netclass" {q(netclass)} (at {num(p[0])} {num(p[1] - 2.54)} 0) {FONT})'
            )
        self.add(f"({kind} {q(text)}{extra} (at {num(p[0])} {num(p[1])} 0) (fields_autoplaced yes)"
                 f" (effects (font (size 1.27 1.27)) (justify left bottom)) (uuid {q(u)}){fields})")  # fmt: skip
        return u

    def text(self, body, p):
        u = track(uid(self.name, "text", body, p))
        self.add(f"(text {q(body)} (exclude_from_sim no) (at {num(p[0])} {num(p[1])} 0)"
                 f" {FONT} (uuid {q(u)}))")  # fmt: skip

    def symbol(self, lib_id, refs, at, unit=1, value=None, footprint="", fields=None):
        """refs: {instance path: reference}"""
        self.libs.add(lib_id)
        sid = track(uid(self.name, "sym", lib_id, at, unit))
        first = next(iter(refs.values()))
        val = value or lib_id.split(":")[1]
        text = (
            f"(symbol (lib_id {q(lib_id)}) (at {num(at[0])} {num(at[1])} 0) (unit {unit})"
            f" (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no) (uuid {q(sid)})"
            f' (property "Reference" {q(first)} (at {num(at[0])} {num(at[1] - 10.16)} 0) {HIDDEN})'
            f' (property "Value" {q(val)} (at {num(at[0])} {num(at[1] + 10.16)} 0) {HIDDEN})'
            f' (property "Footprint" {q(footprint)} (at 0 0 0) {HIDDEN})'
            f' (property "Datasheet" "" (at 0 0 0) {HIDDEN})'
            f' (property "Description" "" (at 0 0 0) {HIDDEN})'
        )
        for name, value in (fields or {}).items():
            text += f" (property {q(name)} {q(value)} (at 0 0 0) {HIDDEN})"
        defs = PIN_DEFS[(lib_id, unit)] if (lib_id, unit) in PIN_DEFS else PIN_DEFS[(lib_id, 1)]
        for number, *_ in defs:
            text += f" (pin {q(number)} (uuid {q(track(uid(sid, 'pin', number)))}))"
        text += f" (instances (project {q(PROJECT)}"
        for path, ref in refs.items():
            text += f" (path {q(path)} (reference {q(ref)}) (unit {unit}))"
        self.add(text + ")))")
        s = Symbol(lib_id, None, (round(at[0] * 1e6), round(at[1] * 1e6)), 0, None, unit, 1,
                   sid, {}, True, True, False, False, [], None)  # fmt: skip
        out = {}
        for number, x, y, pangle in defs:
            lp = LibPin(number, "", "", "", (round(x * 1e6), round(y * 1e6)), pangle, 0, 0, 0)
            px, py = s.transform(lp.position)
            out[number] = (round(px / 1e6, 4), round(py / 1e6, 4))
        return out

    def sheet(self, name, file, at, size, pins, instances):
        """pins: [(name, (x, y))]; instances: [(parent path, page)]"""
        sid = track(uid(self.name, "sheet", name, at))
        text = (
            f"(sheet (at {num(at[0])} {num(at[1])}) (size {num(size[0])} {num(size[1])})"
            f" (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no) (fields_autoplaced yes)"
            f" (stroke (width 0.1524) (type solid)) (fill (color 0 0 0 0.0000)) (uuid {q(sid)})"
            f' (property "Sheetname" {q(name)} (at {num(at[0])} {num(at[1] - 0.7)} 0) {FONT})'
            f' (property "Sheetfile" {q(file)} (at {num(at[0])} {num(at[1] + size[1] + 0.6)} 0) {FONT})'
        )
        for pname, (px, py) in pins:
            u = track(uid(sid, "pin", pname))
            text += (f" (pin {q(pname)} bidirectional (at {num(px)} {num(py)} 180) (uuid {q(u)})"
                     f" (effects (font (size 1.27 1.27)) (justify left)))")  # fmt: skip
        text += f" (instances (project {q(PROJECT)}"
        for parent, page in instances:
            text += f" (path {q(parent)} (page {q(page)}))"
        self.add(text + ")))")
        return sid

    def write(self, root=False):
        libs = " ".join(LIB[name] for name in sorted(self.libs))
        aliases = " ".join(self.aliases)
        text = (
            f'(kicad_sch (version 20260101) (generator "eeschema") (generator_version "10.0")'
            f' (uuid {q(self.uuid)}) (paper "A2") (lib_symbols {libs}) {aliases} '
            + " ".join(self.items)
        )
        if root:
            text += ' (sheet_instances (path "/" (page "1")))'
        (OUT / self.file).write_bytes((text + " (embedded_fonts no))\n").encode("utf-8"))


root = Sheet("root", "erc2.kicad_sch")
ROOT = "/" + root.uuid
_cell = [0]
REFS: dict[str, int] = {}


def ref(prefix, number=None):
    if number is not None:
        return f"{prefix}{number}"
    REFS[prefix] = REFS.get(prefix, 0) + 1
    return f"{prefix}{REFS[prefix]}"


def cell(name, question):
    index = _cell[0]
    _cell[0] += 1
    CASES[name] = question
    _current[0] = name
    return (25.4 + 38.1 * (index % 8), 25.4 + 38.1 * (index // 8))


def resistor_to_label(sheet, path, x, y, label_text, reference=None):
    """A resistor, pin 1 to a label, pin 2 flagged: nothing to report of it."""
    r = sheet.symbol("fixture:R", {path: reference or ref("R")}, (x, y + 7.62))
    sheet.wire(r["1"], (r["1"][0], r["1"][1] - 2.54))
    sheet.label("label", label_text, (r["1"][0], r["1"][1] - 2.54))
    sheet.no_connect(r["2"])
    return r


# -- references -----------------------------------------------------------------------
x, y = cell("unannotated", "a part not yet numbered")
resistor_to_label(root, ROOT, x, y, "UNANN", reference="R?")
resistor_to_label(root, ROOT, x + 10.16, y, "UNANN")

x, y = cell("duplicate_on_sheet", "two parts numbered alike on one sheet")
resistor_to_label(root, ROOT, x, y, "DUPREF", reference="R901")
resistor_to_label(root, ROOT, x + 10.16, y, "DUPREF", reference="R901")

x, y = cell("duplicate_power", "two power symbols numbered alike")
f = root.symbol("fixture:FLAG", {ROOT: ref("#FLG")}, (x + 10.16, y))
for i in range(2):
    p = root.symbol("fixture:PWR", {ROOT: "#PWR901"}, (x + 5.08 * i, y), value="DUPPWR")
    root.wire(p["1"], (p["1"][0], y + 5.08))
root.wire((x, y + 5.08), (x + 10.16, y + 5.08))
root.wire(f["1"], (x + 10.16, y + 5.08))
root.junction((x + 5.08, y + 5.08))

x, y = cell("unit_values_differ", "the two units of one part, different values")
for unit in (1, 2):
    u = root.symbol("fixture:DUAL", {ROOT: "U901"}, (x + 20.32 * (unit - 1), y + 10.16), unit=unit,
                    value=f"DUAL{unit}")  # fmt: skip
    for at in u.values():
        root.no_connect(at)

x, y = cell("unit_beyond_count", "unit 3 of a part that has two")
u = root.symbol("fixture:DUAL", {ROOT: "U902"}, (x, y + 10.16), unit=3)
for at in u.values():
    root.no_connect(at)

# -- parts of several units ---------------------------------------------------------------
x, y = cell("missing_unit_inputs", "one unit of two placed; the other has inputs")
u = root.symbol("fixture:DUAL", {ROOT: "U903"}, (x, y + 10.16), unit=1)
for at in u.values():
    root.no_connect(at)

x, y = cell("missing_unit_bidi", "one unit of two placed; the other a bidirectional pin")
u = root.symbol("fixture:BIDI2", {ROOT: "U904"}, (x + 7.62, y + 10.16), unit=1)
root.no_connect(u["1"])

x, y = cell("missing_unit_power", "one unit of two placed; the other a power input")
u = root.symbol("fixture:PWRUNIT", {ROOT: "U905"}, (x + 7.62, y + 10.16), unit=1)
root.no_connect(u["1"])

x, y = cell("missing_unit_passive", "one unit of two placed; the other a passive pin")
u = root.symbol("fixture:PASS2", {ROOT: "U906"}, (x + 7.62, y + 10.16), unit=1)
root.no_connect(u["1"])

x, y = cell("unit_footprints_differ", "the two units of one part, different footprints")
for unit in (1, 2):
    u = root.symbol("fixture:DUAL", {ROOT: "U907"}, (x + 20.32 * (unit - 1), y + 10.16), unit=unit,
                    footprint=f"Package:FP{unit}")  # fmt: skip
    for at in u.values():
        root.no_connect(at)

x, y = cell("shared_pin_two_nets", "a pin every unit has, on different nets in each")
for unit in (1, 2):
    u = root.symbol("fixture:DUAL", {ROOT: "U908"}, (x + 20.32 * (unit - 1), y + 10.16), unit=unit)
    for number, at in u.items():
        if number == "8":
            root.wire(at, (at[0], at[1] - 2.54))
            root.label("label", f"SHARED_{unit}", (at[0], at[1] - 2.54))
            r = root.symbol("fixture:R", {ROOT: ref("R")}, (at[0] + 5.08, at[1] - 7.62))
            root.wire(r["1"], (r["1"][0], r["1"][1] - 2.54))
            root.label("label", f"SHARED_{unit}", (r["1"][0], r["1"][1] - 2.54))
            root.no_connect(r["2"])
        else:
            root.no_connect(at)

x, y = cell("shared_pin_one_net", "a pin every unit has, on one net in both")
for unit in (1, 2):
    u = root.symbol("fixture:DUAL", {ROOT: "U909"}, (x + 20.32 * (unit - 1), y + 10.16), unit=unit)
    for number, at in u.items():
        if number == "8":
            root.wire(at, (at[0], at[1] - 2.54))
            root.label("label", "SHARED_ONE", (at[0], at[1] - 2.54))
        else:
            root.no_connect(at)

# -- the hierarchy ------------------------------------------------------------------------
x, y = cell("hier_mismatch", "a sheet pin with no label inside, a label with no sheet pin")
child = Sheet("child", "erc2_child.kicad_sch")
sheet_id = root.sheet("CHILD", "erc2_child.kicad_sch", (x, y), (15.24, 10.16),
                      [("HL_MATCH", (x, y + 2.54)), ("SP_ONLY", (x, y + 5.08))], [(ROOT, "2")])  # fmt: skip
CHILD = f"{ROOT}/{sheet_id}"
for i in range(2):
    r = root.symbol("fixture:R", {ROOT: ref("R")}, (x - 7.62 - 5.08 * i, y + 17.78))
    root.no_connect(r["2"])
    root.wire(r["1"], (r["1"][0], y + 2.54 * (i + 1)))
    root.wire((r["1"][0], y + 2.54 * (i + 1)), (x, y + 2.54 * (i + 1)))
_current[0] = "hier_mismatch"
for i, name in enumerate(("HL_MATCH", "HL_ONLY")):
    r = child.symbol("fixture:R", {CHILD: ref("R")}, (50.8 + 20.32 * i, 50.8))
    child.wire(r["1"], (r["1"][0], r["1"][1] - 5.08))
    child.label("hierarchical_label", name, (r["1"][0], r["1"][1] - 5.08))
    child.no_connect(r["2"])

x, y = cell("duplicate_sheet_names", "two sheets of one name")
for i in range(2):
    empty = Sheet(f"empty{i}", f"erc2_empty{i}.kicad_sch")
    root.sheet("TWIN", f"erc2_empty{i}.kicad_sch", (x + 17.78 * i, y), (12.7, 7.62), [],
               [(ROOT, str(3 + i))])  # fmt: skip
    empty.write()

x, y = cell("twice_placed", "one sheet placed twice, one part numbered alike in both")
twice = Sheet("twice", "erc2_twice.kicad_sch")
paths = []
for i in range(2):
    sid = root.sheet(f"TWICE{i}", "erc2_twice.kicad_sch", (x + 17.78 * i, y), (12.7, 7.62), [],
                     [(ROOT, str(5 + i))])  # fmt: skip
    paths.append(f"{ROOT}/{sid}")
_current[0] = "twice_placed"
r = twice.symbol("fixture:R", {paths[0]: "R950", paths[1]: "R950"}, (50.8, 50.8))
twice.no_connect(r["1"])
twice.no_connect(r["2"])

# -- buses ----------------------------------------------------------------------------------
x, y = cell("bus_member", "a bus, a member off it through an entry, and a non-member")
root.bus((x, y), (x + 25.4, y))
root.label("label", "DATA[0..3]", (x + 2.54, y))
for i, name in enumerate(("DATA0", "NOTMEMBER")):
    end = root.bus_entry((x + 7.62 + 10.16 * i, y))
    r = root.symbol("fixture:R", {ROOT: ref("R")}, (end[0], end[1] + 7.62))
    root.wire(end, r["1"])
    root.label("label", name, (end[0], end[1] + 1.27))
    root.no_connect(r["2"])

x, y = cell("wire_on_bus", "a wire ending on a bus, no entry")
root.bus((x, y), (x + 25.4, y))
root.label("label", "WB[0..1]", (x + 2.54, y))
r = root.symbol("fixture:R", {ROOT: ref("R")}, (x + 12.7, y + 10.16))
root.wire((x + 12.7, y), r["1"])
root.label("label", "WB0", (x + 12.7, y + 2.54))
root.no_connect(r["2"])

x, y = cell("bus_label_on_wire", "a bus's label on a wire")
r = root.symbol("fixture:R", {ROOT: ref("R")}, (x, y + 10.16))
root.wire(r["1"], (x, y))
root.label("label", "BL[0..1]", (x, y + 2.54))
root.no_connect(r["2"])

x, y = cell("net_label_on_bus", "a net's label on a bus")
root.bus((x, y), (x + 25.4, y))
root.label("label", "NETONBUS", (x + 5.08, y))

x, y = cell("two_buses_joined", "two buses of different members, joined")
root.bus((x, y), (x + 12.7, y))
root.bus((x + 12.7, y), (x + 25.4, y))
root.label("label", "PB[0..1]", (x + 2.54, y))
root.label("label", "QB[0..1]", (x + 15.24, y))

x, y = cell("alias_twice", "one bus alias defined differently on two sheets")
root.aliases.append('(bus_alias "ALIASX" (members "AX1" "AX2"))')
child.aliases.append('(bus_alias "ALIASX" (members "BX1"))')
root.bus((x, y), (x + 25.4, y))
root.label("label", "{ALIASX}", (x + 2.54, y))

# -- text and net classes ----------------------------------------------------------------
x, y = cell("text_variable", "a text naming a variable nothing defines")
root.text("${NO_SUCH_VARIABLE}", (x, y))

x, y = cell("field_variable", "a part's field naming a variable nothing defines")
r = root.symbol("fixture:R", {ROOT: ref("R")}, (x, y + 10.16), fields={"Note": "${ALSO_UNDEFINED}"})
root.wire(r["1"], (x, y))
root.label("label", "FIELDVAR", (x, y))
root.no_connect(r["2"])

x, y = cell("undefined_netclass", "a directive label naming a net class nothing defines")
r = root.symbol("fixture:R", {ROOT: ref("R")}, (x, y + 10.16))
root.wire(r["1"], (x, y))
root.label("label", "NC_CLASS", (x, y))
root.label("netclass_flag", "", (x, y + 3.81), netclass="NoSuchClass")
root.no_connect(r["2"])

x, y = cell("defined_netclass", "a directive label naming the Default class")
r = root.symbol("fixture:R", {ROOT: ref("R")}, (x, y + 10.16))
root.wire(r["1"], (x, y))
root.label("label", "DEF_CLASS", (x, y))
root.label("netclass_flag", "", (x, y + 3.81), netclass="Default")
root.no_connect(r["2"])

x, y = cell("directive_only", "a pin whose wire carries only a directive label")
r = root.symbol("fixture:R", {ROOT: ref("R")}, (x, y + 10.16))
root.wire(r["1"], (x, y))
root.label("netclass_flag", "", (x, y + 3.81), netclass="Default")
root.no_connect(r["2"])

# -- global labels -----------------------------------------------------------------------
x, y = cell(
    "global_on_stub_elsewhere_pins", "a global label on a stub; its name on two pins elsewhere"
)
root.wire((x, y), (x + 7.62, y))
root.label("global_label", "GL_STUB", (x, y))
for i in range(2):
    r = root.symbol("fixture:R", {ROOT: ref("R")}, (x + 10.16 * i, y + 17.78))
    root.wire(r["1"], (r["1"][0], r["1"][1] - 2.54))
    root.label("global_label", "GL_STUB", (r["1"][0], r["1"][1] - 2.54))
    root.no_connect(r["2"])

x, y = cell("global_in_child_only", "a global label in the child sheet, on a pin, once")
_current[0] = "global_in_child_only"
r = child.symbol("fixture:R", {CHILD: ref("R")}, (101.6, 50.8))
child.wire(r["1"], (r["1"][0], r["1"][1] - 5.08))
child.label("global_label", "GL_CHILD", (r["1"][0], r["1"][1] - 5.08))
child.no_connect(r["2"])

# -- power symbols with nothing else ---------------------------------------------------
x, y = cell("flag_two_power", "a PWR_FLAG and two power symbols wired, no other part")
f = root.symbol("fixture:FLAG", {ROOT: ref("#FLG")}, (x + 10.16, y))
for i in range(2):
    p = root.symbol("fixture:PWR", {ROOT: ref("#PWR")}, (x + 5.08 * i, y), value="FLAG_TWO")
    root.wire(p["1"], (p["1"][0], y + 5.08))
root.wire((x, y + 5.08), (x + 10.16, y + 5.08))
root.wire(f["1"], (x + 10.16, y + 5.08))
root.junction((x + 5.08, y + 5.08))

x, y = cell("flag_one_power", "a PWR_FLAG and one power symbol wired, no other part")
f = root.symbol("fixture:FLAG", {ROOT: ref("#FLG")}, (x + 5.08, y))
p = root.symbol("fixture:PWR", {ROOT: ref("#PWR")}, (x, y), value="FLAG_ONE")
root.wire(p["1"], (x, y + 5.08))
root.wire((x, y + 5.08), (x + 5.08, y + 5.08))
root.wire(f["1"], (x + 5.08, y + 5.08))

x, y = cell("two_power_wired", "two power symbols wired together, nothing else")
for i in range(2):
    p = root.symbol("fixture:PWR", {ROOT: ref("#PWR")}, (x + 5.08 * i, y), value="TWO_WIRED")
    root.wire(p["1"], (p["1"][0], y + 5.08))
root.wire((x, y + 5.08), (x + 5.08, y + 5.08))

# -- the bus rules not yet seen --------------------------------------------------------
x, y = cell("wire_on_bus_end", "a wire ending on a bus's end, no entry")
root.bus((x, y), (x + 12.7, y))
root.label("label", "WE[0..1]", (x + 2.54, y))
r = root.symbol("fixture:R", {ROOT: ref("R")}, (x + 12.7, y + 10.16))
root.wire((x + 12.7, y), r["1"])
root.label("label", "WE0", (x + 12.7, y + 2.54))
root.no_connect(r["2"])

x, y = cell("wire_crossing_bus_junction", "a wire crossing a bus with a junction on the crossing")
root.bus((x, y + 5.08), (x + 25.4, y + 5.08))
root.label("label", "WJ[0..1]", (x + 2.54, y + 5.08))
r = root.symbol("fixture:R", {ROOT: ref("R")}, (x + 12.7, y + 17.78))
root.wire((x + 12.7, y), r["1"])
root.junction((x + 12.7, y + 5.08))
root.label("label", "WJ0", (x + 12.7, y + 10.16))
root.no_connect(r["2"])

x, y = cell("bus_sheet_pin_other_members", "a bus into a sheet whose own bus has other members")
bus_child = Sheet("buschild", "erc2_buschild.kicad_sch")
sid = root.sheet("BUSCHILD", "erc2_buschild.kicad_sch", (x + 12.7, y), (12.7, 7.62),
                 [("CB[0..1]", (x + 12.7, y + 2.54))], [(ROOT, "7")])  # fmt: skip
root.bus((x, y + 2.54), (x + 12.7, y + 2.54))
root.label("label", "PARENT[0..1]", (x + 2.54, y + 2.54))
_current[0] = "bus_sheet_pin_other_members"
bus_child.bus((50.8, 50.8), (76.2, 50.8))
bus_child.label("hierarchical_label", "CB[0..1]", (50.8, 50.8))
bus_child.label("label", "OTHER[0..1]", (60.96, 50.8))

x, y = cell("bus_group_and_vector", "a group bus and a vector bus joined")
root.bus((x, y), (x + 12.7, y))
root.bus((x + 12.7, y), (x + 25.4, y))
root.label("label", "GRP{A B}", (x + 2.54, y))
root.label("label", "VEC[0..1]", (x + 15.24, y))

x, y = cell("global_on_bus", "a global label naming a net, on a bus")
root.bus((x, y), (x + 25.4, y))
root.label("global_label", "GLOB_ON_BUS", (x + 5.08, y))

x, y = cell("global_floating_twice", "a global label floating, its name floating elsewhere too")
root.label("global_label", "GL_FLOAT2", (x, y))
root.label("global_label", "GL_FLOAT2", (x + 12.7, y))

x, y = cell("global_stub_only", "a global label on a stub, its name on another stub")
for i in range(2):
    root.wire((x + 12.7 * i, y), (x + 12.7 * i + 7.62, y))
    root.label("global_label", "GL_STUBS", (x + 12.7 * i, y))

x, y = cell("wire_bus_junction_bare", "a wire joined to a bus by a junction, no label on it")
root.bus((x, y + 5.08), (x + 25.4, y + 5.08))
root.label("label", "WK[0..1]", (x + 2.54, y + 5.08))
r = root.symbol("fixture:R", {ROOT: ref("R")}, (x + 12.7, y + 17.78))
root.wire((x + 12.7, y + 5.08), r["1"])
root.junction((x + 12.7, y + 5.08))
root.no_connect(r["2"])

x, y = cell("group_pin_vector_label", "a group-bus sheet pin on a vector bus")
group_child = Sheet("groupchild", "erc2_groupchild.kicad_sch")
root.sheet("GROUPCHILD", "erc2_groupchild.kicad_sch", (x + 12.7, y), (12.7, 7.62),
           [("GC{X Y}", (x + 12.7, y + 2.54))], [(ROOT, "8")])  # fmt: skip
root.bus((x, y + 2.54), (x + 12.7, y + 2.54))
root.label("label", "VP[0..1]", (x + 2.54, y + 2.54))
_current[0] = "group_pin_vector_label"
group_child.bus((50.8, 50.8), (76.2, 50.8))
group_child.label("hierarchical_label", "GC{X Y}", (50.8, 50.8))

root.write(root=True)
child.write()
twice.write()
bus_child.write()
group_child.write()
project = {
    "erc": {"rule_severities": RULES},
    "meta": {"filename": "erc2.kicad_pro", "version": 3},
    "net_settings": {"classes": [{"name": "Default"}], "meta": {"version": 5}},
}
(OUT / "erc2.kicad_pro").write_bytes((json.dumps(project, indent=2) + "\n").encode("utf-8"))
(OUT / "cases2.json").write_bytes(
    (json.dumps({"questions": CASES, "items": ITEMS}, indent=1) + "\n").encode("utf-8")
)
print(f"{len(CASES)} cases")
