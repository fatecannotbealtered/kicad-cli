"""Write erc.kicad_sch: a design that asks KiCad's ERC one question per case.

Every symbol is drawn for the purpose -- nothing comes from KiCad's
libraries. Each case sits in its own cell of a grid, with net names of its
own, so no case reaches another; what KiCad's ERC reports about each is the
answer to one question about when a rule fires and what it names. The
answers are recorded in `tests/test_fileformat_erc.py`.

To change the design: edit this, run it, run KiCad's ERC on the result
(`kicad-cli sch erc --severity-all --format json erc.kicad_sch`) and record
the answers again. The project turns every rule on, the four a new KiCad
project ships silenced included.
"""

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from kicad_cli.fileformat.schematic import LibPin, Symbol  # noqa: E402

OUT = Path(__file__).resolve().parent
NS = uuid.UUID("7a1c5d2e-0000-4000-8000-0000000e4c00")
PROJECT = "erc"

# Every rule KiCad 10's ERC has, as a project stores their severities.
RULES = {
    "bus_definition_conflict": "error", "bus_entry_needed": "error",
    "bus_to_bus_conflict": "error", "bus_to_net_conflict": "error",
    "different_unit_footprint": "error", "different_unit_net": "error",
    "duplicate_reference": "error", "duplicate_sheet_names": "error",
    "endpoint_off_grid": "warning", "extra_units": "error",
    "footprint_filter": "warning", "footprint_link_issues": "warning",
    "four_way_junction": "warning", "global_label_dangling": "warning",
    "hier_label_mismatch": "error", "isolated_pin_label": "warning", "label_dangling": "error",
    "label_multiple_wires": "warning", "lib_symbol_issues": "warning",
    "lib_symbol_mismatch": "warning", "missing_bidi_pin": "warning",
    "missing_input_pin": "warning", "missing_power_pin": "error",
    "missing_unit": "warning", "multiple_net_names": "warning",
    "net_not_bus_member": "warning", "no_connect_connected": "warning",
    "no_connect_dangling": "warning", "pin_not_connected": "error",
    "pin_not_driven": "error", "pin_to_pin": "warning",
    "power_pin_not_driven": "error", "same_local_global_label": "warning",
    "similar_label_and_power": "warning", "similar_labels": "warning",
    "similar_power": "warning", "simulation_model_issue": "warning",
    "single_global_label": "warning", "unannotated": "error",
    "unconnected_wire_endpoint": "warning", "undefined_netclass": "error",
    "unit_value_mismatch": "error", "unresolved_variable": "error",
    "wire_dangling": "error",
}  # fmt: skip


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


TYPES = [
    "input", "output", "bidirectional", "tri_state", "passive", "free",
    "unspecified", "power_in", "power_out", "open_collector", "open_emitter", "no_connect",
]  # fmt: skip

LIB: dict[str, str] = {}
PIN_DEFS: dict[str, list] = {}


def define(name, pins, **kw):
    """pins: [(etype, number, pin name, x, y, angle, hidden)]"""
    LIB[name] = lib_symbol(
        name, {(1, 1): [pin(t, n, nm, x, y, a, h) for t, n, nm, x, y, a, h in pins]}, **kw
    )
    PIN_DEFS[name] = [(n, x, y, a) for t, n, nm, x, y, a, h in pins]


# A two-pin passive part, pins up and down.
define("fixture:R", [("passive", "1", "~", 0, 3.81, 270, False),
                     ("passive", "2", "~", 0, -3.81, 90, False)], ref="R")  # fmt: skip
# One pin of each electrical type, pointing left.
for etype in TYPES:
    define(f"fixture:T_{etype}", [(etype, "1", etype.upper(), -5.08, 0, 0, False)])
# A part with a supply pin the library hides, and a signal pin.
define("fixture:HIDDENPWR", [("input", "1", "IN", -5.08, 0, 0, False),
                             ("power_in", "2", "VHID", 5.08, 0, 180, True)])  # fmt: skip
# A power symbol, as KiCad's are: one hidden power input of length 0.
LIB["fixture:PWR"] = lib_symbol(
    "fixture:PWR", {(0, 1): [pin("power_in", "1", "PWR", 0, 0, 90, hidden=True, length=0)]},
    power=True, ref="#PWR",
)  # fmt: skip
PIN_DEFS["fixture:PWR"] = [("1", 0, 0, 90)]
# KiCad's PWR_FLAG: a power symbol whose one pin is a power output.
LIB["fixture:FLAG"] = lib_symbol(
    "fixture:FLAG", {(0, 1): [pin("power_out", "1", "pwr", 0, 0, 90, hidden=True, length=0)]},
    power=True, ref="#FLG",
)  # fmt: skip
PIN_DEFS["fixture:FLAG"] = [("1", 0, 0, 90)]


ITEMS: dict[str, list[str]] = {}  # case -> the uuids of what it drew
_current = [""]


def track(item_uuid: str) -> str:
    ITEMS.setdefault(_current[0], []).append(item_uuid)
    return item_uuid


class Sheet:
    def __init__(self, name):
        self.name = name
        self.uuid = uid("sheet", name)
        self.items = []
        self.libs = set()
        self.path = "/" + self.uuid

    def wire(self, a, b):
        self.items.append(
            f"(wire (pts (xy {num(a[0])} {num(a[1])}) (xy {num(b[0])} {num(b[1])}))"
            f" (stroke (width 0) (type default)) (uuid {q(uid(self.name, 'w', a, b))}))"
        )
        return track(uid(self.name, "w", a, b))

    def junction(self, p):
        self.items.append(
            f"(junction (at {num(p[0])} {num(p[1])}) (diameter 0) (color 0 0 0 0)"
            f" (uuid {q(uid(self.name, 'j', p))}))"
        )
        track(uid(self.name, "j", p))

    def no_connect(self, p):
        self.items.append(
            f"(no_connect (at {num(p[0])} {num(p[1])}) (uuid {q(uid(self.name, 'nc', p))}))"
        )
        return track(uid(self.name, "nc", p))

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
        lid = track(uid(self.name, kind, text, p))
        self.items.append(
            f"({kind} {q(text)}{extra} (at {num(p[0])} {num(p[1])} {angle}) (fields_autoplaced yes)"
            f" (effects (font (size 1.27 1.27)) (justify left bottom)) (uuid {q(lid)}){fields})"
        )
        return lid

    def symbol(self, lib_id, ref, at, angle=0, value=None, unit=1):
        self.libs.add(lib_id)
        sid = uid(self.name, "sym", lib_id, at, unit)
        val = value or lib_id.split(":")[1]
        text = (
            f"(symbol (lib_id {q(lib_id)}) (at {num(at[0])} {num(at[1])} {angle}) (unit {unit})"
            f" (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no) (uuid {q(sid)})"
            f' (property "Reference" {q(ref)} (at {num(at[0])} {num(at[1] - 5.08)} 0) {HIDDEN})'
            f' (property "Value" {q(val)} (at {num(at[0])} {num(at[1] + 5.08)} 0) {HIDDEN})'
            f' (property "Footprint" "" (at 0 0 0) {HIDDEN})'
            f' (property "Datasheet" "" (at 0 0 0) {HIDDEN})'
            f' (property "Description" "" (at 0 0 0) {HIDDEN})'
        )
        track(sid)
        for number, *_ in PIN_DEFS[lib_id]:
            text += f" (pin {q(number)} (uuid {q(track(uid(sid, 'pin', number)))}))"
        text += f" (instances (project {q(PROJECT)} (path {q(self.path)} (reference {q(ref)}) (unit {unit}))))"
        self.items.append(text + ")")
        s = Symbol(lib_id, None, (round(at[0] * 1e6), round(at[1] * 1e6)), angle, None, unit, 1,
                   sid, {}, True, True, False, False, [], None)  # fmt: skip
        out = {}
        for number, x, y, pangle in PIN_DEFS[lib_id]:
            lp = LibPin(number, "", "", "", (round(x * 1e6), round(y * 1e6)), pangle, 0, 0, 0)
            px, py = s.transform(lp.position)
            out[number] = (round(px / 1e6, 4), round(py / 1e6, 4))
        return out

    def write(self, path):
        libs = " ".join(LIB[name] for name in sorted(self.libs))
        text = (
            f'(kicad_sch (version 20260101) (generator "eeschema") (generator_version "10.0")'
            f' (uuid {q(self.uuid)}) (paper "A1") (lib_symbols {libs}) '
            + " ".join(self.items)
            + ' (sheet_instances (path "/" (page "1"))) (embedded_fonts no))'
        )
        path.write_bytes((text + "\n").encode("utf-8"))


root = Sheet("root")
CASES: dict[str, str] = {}  # case -> question
_cell = [0]
REFS = {"R": 0, "U": 0, "#PWR": 0, "#FLG": 0}


def ref(prefix, number=None):
    if number is not None:
        return f"{prefix}{number}"
    REFS[prefix] += 1
    return f"{prefix}{REFS[prefix]}"


def cell(name, question):
    """The origin of the next case's cell: 10 per row, 25.4 mm apart."""
    index = _cell[0]
    _cell[0] += 1
    CASES[name] = question
    _current[0] = name
    return (25.4 + 25.4 * (index % 10), 31.75 + 25.4 * (index // 10))


def two(etype_a, etype_b, x, y, how="wire"):
    """Two one-pin parts, their pins joined by a wire or by two labels."""
    a = root.symbol(f"fixture:T_{etype_a}", ref("U"), (x + 7.62, y))
    b = root.symbol(f"fixture:T_{etype_b}", ref("U"), (x + 7.62, y + 7.62))
    if how == "wire":
        root.wire(a["1"], b["1"])
    return a, b


# -- pin_not_connected ----------------------------------------------------------------
x, y = cell("pnc_alone", "a part with nothing attached")
root.symbol("fixture:R", ref("R"), (x, y + 5.08))

x, y = cell("pnc_stub", "a pin with a wire to nowhere, the other flagged")
r = root.symbol("fixture:R", ref("R"), (x, y + 5.08))
root.wire(r["1"], (r["1"][0], r["1"][1] - 5.08))
root.no_connect(r["2"])

x, y = cell("pnc_label_on_pin", "a pin with a label of its own at its end, the other flagged")
r = root.symbol("fixture:R", ref("R"), (x, y + 5.08))
root.label("label", "PNC_LABEL_ONLY", r["1"])
root.no_connect(r["2"])

x, y = cell("pnc_label_wire", "a pin with a wire to a label no other place has")
r = root.symbol("fixture:R", ref("R"), (x, y + 5.08))
root.wire(r["1"], (r["1"][0], r["1"][1] - 5.08))
root.label("label", "PNC_LABEL_WIRE", (r["1"][0], r["1"][1] - 5.08))
root.no_connect(r["2"])

x, y = cell("pnc_joined_by_label", "two pins joined by one label name")
r1 = root.symbol("fixture:R", ref("R"), (x, y + 5.08))
r2 = root.symbol("fixture:R", ref("R"), (x + 10.16, y + 5.08))
for r in (r1, r2):
    root.wire(r["1"], (r["1"][0], r["1"][1] - 5.08))
    root.label("label", "PNC_JOINED", (r["1"][0], r["1"][1] - 5.08))
    root.no_connect(r["2"])

x, y = cell("pnc_pin_on_pin", "two pins touching, end on end")
r1 = root.symbol("fixture:R", ref("R"), (x, y + 5.08))
r2 = root.symbol("fixture:R", ref("R"), (x, y + 5.08 + 7.62))
root.no_connect(r1["1"])
root.no_connect(r2["2"])

x, y = cell("pnc_nc_type", "a no-connect pin with nothing attached")
root.symbol("fixture:T_no_connect", ref("U"), (x + 7.62, y + 5.08))

x, y = cell("pnc_free_type", "a free pin with nothing attached")
root.symbol("fixture:T_free", ref("U"), (x + 7.62, y + 5.08))

# -- power_pin_not_driven -------------------------------------------------------------
x, y = cell("ppnd_symbol_and_pin", "a power symbol and a power input, nothing driving")
p = root.symbol("fixture:PWR", ref("#PWR"), (x, y), value="PPND_A")
t = root.symbol("fixture:T_power_in", ref("U"), (x + 7.62, y + 7.62))
root.wire(p["1"], (p["1"][0], t["1"][1]))
root.wire((p["1"][0], t["1"][1]), t["1"])

x, y = cell("ppnd_flagged", "the same, with a PWR_FLAG")
p = root.symbol("fixture:PWR", ref("#PWR"), (x, y), value="PPND_B")
t = root.symbol("fixture:T_power_in", ref("U"), (x + 7.62, y + 7.62))
f = root.symbol("fixture:FLAG", ref("#FLG"), (t["1"][0], y))
root.wire(p["1"], (p["1"][0], t["1"][1]))
root.wire((p["1"][0], t["1"][1]), t["1"])
root.wire(f["1"], t["1"])

x, y = cell("ppnd_output", "a power input and an output")
two("power_in", "output", x, y)

x, y = cell("ppnd_passive", "a power input and a passive pin")
two("power_in", "passive", x, y)

x, y = cell("ppnd_power_out", "a power input and a power output")
two("power_in", "power_out", x, y)

x, y = cell("ppnd_two_inputs", "two power inputs, nothing else")
two("power_in", "power_in", x, y)

x, y = cell("ppnd_symbol_alone", "a power symbol on its own")
root.symbol("fixture:PWR", ref("#PWR"), (x, y), value="PPND_ALONE")

x, y = cell("ppnd_hidden", "a part's hidden supply pin, its input wired to a label")
h = root.symbol("fixture:HIDDENPWR", ref("U"), (x + 7.62, y + 5.08))
root.wire(h["1"], (h["1"][0] - 2.54, h["1"][1]))
root.label("label", "PPND_HIDDEN_IN", (h["1"][0] - 2.54, h["1"][1]))

# -- pin_not_driven -------------------------------------------------------------------
for other in ("input", "passive", "output", "bidirectional", "tri_state", "unspecified",
              "open_collector", "open_emitter", "power_out", "free"):  # fmt: skip
    x, y = cell(f"pnd_{other}", f"an input and a pin of type {other}")
    two("input", other, x, y)

x, y = cell("pnd_label_only", "an input wired to a label no other place has")
t = root.symbol("fixture:T_input", ref("U"), (x + 7.62, y + 5.08))
root.wire(t["1"], (t["1"][0] - 2.54, t["1"][1]))
root.label("label", "PND_LABEL", (t["1"][0] - 2.54, t["1"][1]))

# -- pin_to_pin: every pair of types -------------------------------------------------
PAIRS = []
for i, a in enumerate(TYPES):
    for b in TYPES[i:]:
        PAIRS.append((a, b))
for a, b in PAIRS:
    x, y = cell(f"p2p_{a}_{b}", f"a {a} pin and a {b} pin")
    two(a, b, x, y)

x, y = cell("p2p_three_outputs", "three outputs on one wire")
ts = [root.symbol("fixture:T_output", ref("U"), (x + 7.62, y + 5.08 * i)) for i in range(3)]
for t, t_next in zip(ts, ts[1:], strict=False):
    root.wire(t["1"], t_next["1"])

x, y = cell("p2p_outputs_by_label", "two outputs, joined only by a label name")
for i in range(2):
    t = root.symbol("fixture:T_output", ref("U"), (x + 7.62, y + 7.62 * i))
    root.wire(t["1"], (t["1"][0] - 2.54, t["1"][1]))
    root.label("label", "P2P_BY_LABEL", (t["1"][0] - 2.54, t["1"][1]))

x, y = cell("p2p_bidi_flag", "three bidirectional pins and a PWR_FLAG")
f = root.symbol("fixture:FLAG", ref("#FLG"), (x, y))
ts = [root.symbol("fixture:T_bidirectional", ref("U"), (x + 10.16, y + 5.08 * i)) for i in range(3)]
root.wire(f["1"], (f["1"][0], ts[-1]["1"][1]))
for t in ts:
    root.wire((f["1"][0], t["1"][1]), t["1"])
for t in ts[:-1]:
    root.junction((f["1"][0], t["1"][1]))

# -- no-connect flags -----------------------------------------------------------------
x, y = cell("nc_on_joined_pin", "a no-connect flag on a pin wired to another pin")
r1 = root.symbol("fixture:R", ref("R"), (x, y + 5.08))
r2 = root.symbol("fixture:R", ref("R"), (x + 10.16, y + 5.08))
root.wire(r1["1"], (r1["1"][0], r1["1"][1] - 2.54))
root.wire((r1["1"][0], r1["1"][1] - 2.54), (r2["1"][0], r2["1"][1] - 2.54))
root.wire((r2["1"][0], r2["1"][1] - 2.54), r2["1"])
root.no_connect(r1["1"])
root.no_connect(r1["2"])
root.no_connect(r2["2"])

x, y = cell("nc_alone", "a no-connect flag on nothing")
root.no_connect((x, y))

x, y = cell("nc_on_wire_end", "a no-connect flag on the far end of a pin's wire")
r = root.symbol("fixture:R", ref("R"), (x, y + 5.08))
root.wire(r["1"], (r["1"][0], r["1"][1] - 5.08))
root.no_connect((r["1"][0], r["1"][1] - 5.08))
root.no_connect(r["2"])

x, y = cell("nc_with_label", "a flagged pin that also has a label no other place has")
r = root.symbol("fixture:R", ref("R"), (x, y + 5.08))
root.wire(r["1"], (r["1"][0], r["1"][1] - 5.08))
root.label("label", "NC_LABELLED", (r["1"][0], r["1"][1] - 5.08))
root.no_connect(r["1"])
root.no_connect(r["2"])

# -- labels and wires ----------------------------------------------------------------
x, y = cell("label_alone", "a local label on nothing")
root.label("label", "LBL_ALONE", (x, y))

x, y = cell("global_alone", "a global label on nothing")
root.label("global_label", "GLB_ALONE", (x, y))

x, y = cell("global_on_pin", "a global label no other place has, wired to a pin")
r = root.symbol("fixture:R", ref("R"), (x, y + 5.08))
root.wire(r["1"], (r["1"][0], r["1"][1] - 5.08))
root.label("global_label", "GLB_ONCE", (r["1"][0], r["1"][1] - 5.08))
root.no_connect(r["2"])

x, y = cell("label_on_stub", "a label on a wire whose other end is free")
root.wire((x, y), (x + 7.62, y))
root.label("label", "LBL_STUB", (x, y))

x, y = cell("wire_alone", "a wire touching nothing")
root.wire((x, y), (x + 7.62, y))

x, y = cell("wire_t_no_junction", "a wire ending on another's middle, no junction")
r1 = root.symbol("fixture:R", ref("R"), (x, y + 5.08))
r2 = root.symbol("fixture:R", ref("R"), (x + 10.16, y + 5.08))
root.wire(r1["1"], (r1["1"][0], r1["1"][1] - 5.08))
root.wire((r1["1"][0], r1["1"][1] - 5.08), (r2["1"][0], r1["1"][1] - 5.08))
root.wire((r1["1"][0] + 5.08, r1["1"][1] - 5.08), (r1["1"][0] + 5.08, r1["1"][1] - 10.16))
root.label("label", "T_STEM", (r1["1"][0] + 5.08, r1["1"][1] - 10.16))
root.no_connect(r1["2"])
root.no_connect(r2["1"])
root.no_connect(r2["2"])

x, y = cell("two_names", "two local labels of different names on one wire")
r1 = root.symbol("fixture:R", ref("R"), (x, y + 5.08))
root.wire(r1["1"], (r1["1"][0], r1["1"][1] - 7.62))
root.label("label", "NAME_A", (r1["1"][0], r1["1"][1] - 2.54))
root.label("label", "NAME_B", (r1["1"][0], r1["1"][1] - 7.62))
root.no_connect(r1["2"])

x, y = cell("three_names", "three local labels of different names on one wire")
r1 = root.symbol("fixture:R", ref("R"), (x, y + 10.16))
root.wire(r1["1"], (r1["1"][0], r1["1"][1] - 7.62))
for i, name in enumerate(("TRI_A", "TRI_B", "TRI_C")):
    root.label("label", name, (r1["1"][0], r1["1"][1] - 2.54 * (i + 1)))
root.no_connect(r1["2"])

x, y = cell("power_and_label", "a power symbol and a local label of another name on one wire")
p = root.symbol("fixture:PWR", ref("#PWR"), (x, y), value="PWR_NAMED")
f = root.symbol("fixture:FLAG", ref("#FLG"), (x + 5.08, y))
r1 = root.symbol("fixture:R", ref("R"), (x, y + 12.7))
root.wire(p["1"], r1["1"])
root.wire(f["1"], (f["1"][0], y + 5.08))
root.wire((f["1"][0], y + 5.08), (p["1"][0], y + 5.08))
root.junction((p["1"][0], y + 5.08))
root.label("label", "LBL_NAMED", (p["1"][0], y + 2.54))
root.no_connect(r1["2"])

x, y = cell("similar_labels", "two local labels differing in case, on separate pins")
for i, name in enumerate(("SIMILAR", "similar")):
    r = root.symbol("fixture:R", ref("R"), (x + 10.16 * i, y + 5.08))
    root.wire(r["1"], (r["1"][0], r["1"][1] - 5.08))
    root.label("label", name, (r["1"][0], r["1"][1] - 5.08))
    root.no_connect(r["2"])

x, y = cell("off_grid", "a wire end off the 1.27 mm grid")
root.wire((x + 0.5, y + 0.5), (x + 7.62, y + 0.5))

x, y = cell("four_way", "four wires meeting at a junction")
c = (x + 5.08, y + 5.08)
for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
    end = (c[0] + 5.08 * dx, c[1] + 5.08 * dy)
    root.wire(c, end)
    root.label("label", "FOUR_WAY", end)
root.junction(c)

x, y = cell("label_two_wires", "a label where two wires meet")
root.wire((x, y), (x + 5.08, y))
root.wire((x + 5.08, y), (x + 10.16, y))
root.label("label", "LBL_JOINT", (x + 5.08, y))
r1 = root.symbol("fixture:R", ref("R"), (x, y + 5.08))
r2 = root.symbol("fixture:R", ref("R"), (x + 10.16, y + 5.08))
root.no_connect(r1["2"])
root.no_connect(r2["2"])
root.wire((x, y), r1["1"])
root.wire((x + 10.16, y), r2["1"])

x, y = cell("label_stub_named_elsewhere", "a label on a stub, its name on a pin's wire too")
root.wire((x, y), (x + 7.62, y))
root.label("label", "LD_ELSEWHERE", (x, y))
r = root.symbol("fixture:R", ref("R"), (x, y + 10.16))
root.wire(r["1"], (r["1"][0], r["1"][1] - 2.54))
root.label("label", "LD_ELSEWHERE", (r["1"][0], r["1"][1] - 2.54))
root.no_connect(r["2"])

x, y = cell("label_on_crossing", "a label where two wires cross, no junction")
r1 = root.symbol("fixture:R", ref("R"), (x, y + 10.16))
r2 = root.symbol("fixture:R", ref("R"), (x + 10.16, y + 10.16))
root.wire((x - 2.54, y + 2.54), (x + 12.7, y + 2.54))
root.wire(r1["1"], (r1["1"][0], y))
root.label("label", "CROSSING", (x, y + 2.54))
root.no_connect(r1["2"])
root.no_connect(r2["2"])
root.no_connect(r2["1"])

x, y = cell("label_on_wire_middle_and_end", "a label on one wire's end and another's middle")
root.wire((x, y + 2.54), (x + 10.16, y + 2.54))
root.wire((x + 5.08, y + 2.54), (x + 5.08, y + 7.62))
root.label("label", "MID_END", (x + 5.08, y + 2.54))
r1 = root.symbol("fixture:R", ref("R"), (x, y + 10.16))
root.wire((x, y + 2.54), r1["1"])
root.no_connect(r1["2"])

x, y = cell("global_twice", "a global label on two pins' wires")
for i in range(2):
    r = root.symbol("fixture:R", ref("R"), (x + 10.16 * i, y + 10.16))
    root.wire(r["1"], (r["1"][0], r["1"][1] - 5.08))
    root.label("global_label", "GLB_TWICE", (r["1"][0], r["1"][1] - 5.08))
    root.no_connect(r["2"])

x, y = cell("global_stub_named_elsewhere", "a global label on a stub, its name on a pin too")
root.wire((x, y), (x + 7.62, y))
root.label("global_label", "GLB_ELSEWHERE", (x, y))
r = root.symbol("fixture:R", ref("R"), (x, y + 10.16))
root.wire(r["1"], (r["1"][0], r["1"][1] - 2.54))
root.label("global_label", "GLB_ELSEWHERE", (r["1"][0], r["1"][1] - 2.54))
root.no_connect(r["2"])

x, y = cell("nc_on_pin_with_label_net", "a flagged pin joined by a label to another pin")
for i in range(2):
    r = root.symbol("fixture:R", ref("R"), (x + 10.16 * i, y + 10.16))
    root.wire(r["1"], (r["1"][0], r["1"][1] - 5.08))
    root.label("label", "NC_BY_LABEL", (r["1"][0], r["1"][1] - 5.08))
    root.no_connect(r["2"])
    if i == 0:
        root.no_connect(r["1"])

x, y = cell("nc_on_wire_middle", "a no-connect flag on the middle of a wire between pins")
r1 = root.symbol("fixture:R", ref("R"), (x, y + 10.16))
r2 = root.symbol("fixture:R", ref("R"), (x + 10.16, y + 10.16))
root.wire(r1["1"], (r1["1"][0], y + 2.54))
root.wire((r1["1"][0], y + 2.54), (r2["1"][0], y + 2.54))
root.wire((r2["1"][0], y + 2.54), r2["1"])
root.no_connect((x + 5.08, y + 2.54))
root.no_connect(r1["2"])
root.no_connect(r2["2"])

x, y = cell("two_power_symbols", "two power symbols of one name, nothing else")
for i in range(2):
    root.symbol("fixture:PWR", ref("#PWR"), (x + 10.16 * i, y), value="TWO_PWR")

x, y = cell("power_symbol_on_pin", "a power symbol on a passive pin, nothing driving")
p = root.symbol("fixture:PWR", ref("#PWR"), (x, y), value="PWR_ON_PIN")
r = root.symbol("fixture:R", ref("R"), (x, y + 3.81))
root.no_connect(r["2"])

x, y = cell("pins_two_inputs_label", "two inputs joined by one label name, nothing driving")
for i in range(2):
    t = root.symbol("fixture:T_input", ref("U"), (x + 7.62, y + 7.62 * i))
    root.wire(t["1"], (t["1"][0] - 2.54, t["1"][1]))
    root.label("label", "TWO_INPUTS", (t["1"][0] - 2.54, t["1"][1]))

x, y = cell("three_power_in", "three power inputs on one wire, nothing driving")
ts = [root.symbol("fixture:T_power_in", ref("U"), (x + 7.62, y + 5.08 * i)) for i in range(3)]
for t, t_next in zip(ts, ts[1:], strict=False):
    root.wire(t["1"], t_next["1"])

x, y = cell("pin_to_nc_pin_and_wire", "a passive pin wired to a no-connect pin and a label")
a, b = two("passive", "no_connect", x, y)
root.wire(a["1"], (a["1"][0] - 2.54, a["1"][1]))
root.label("label", "P_NC_LABEL", (a["1"][0] - 2.54, a["1"][1]))

x, y = cell("wire_between_labels", "a wire with a label at each end, the names used nowhere else")
root.wire((x, y), (x + 10.16, y))
root.label("label", "WB_A", (x, y))
root.label("label", "WB_A", (x + 10.16, y))

x, y = cell("junction_alone", "a junction on nothing")
root.junction((x, y))

x, y = cell("junction_on_wire_end", "a junction at the free end of a pin's wire")
r = root.symbol("fixture:R", ref("R"), (x, y + 10.16))
root.wire(r["1"], (r["1"][0], y))
root.junction((r["1"][0], y))
root.no_connect(r["2"])

x, y = cell("floating_label_named_elsewhere", "a label on nothing, its name on a pin's wire")
root.label("label", "FLOAT_ELSEWHERE", (x, y))
r = root.symbol("fixture:R", ref("R"), (x + 7.62, y + 10.16))
root.wire(r["1"], (r["1"][0], r["1"][1] - 2.54))
root.label("label", "FLOAT_ELSEWHERE", (r["1"][0], r["1"][1] - 2.54))
root.no_connect(r["2"])

x, y = cell("floating_global_named_elsewhere", "a global label on nothing, its name on a pin")
root.label("global_label", "GFLOAT_ELSEWHERE", (x, y))
r = root.symbol("fixture:R", ref("R"), (x + 7.62, y + 10.16))
root.wire(r["1"], (r["1"][0], r["1"][1] - 2.54))
root.label("global_label", "GFLOAT_ELSEWHERE", (r["1"][0], r["1"][1] - 2.54))
root.no_connect(r["2"])

x, y = cell("two_wires_alone", "two wires joined end to end, touching nothing else")
root.wire((x, y), (x + 5.08, y))
root.wire((x + 5.08, y), (x + 5.08, y + 5.08))

x, y = cell("power_in_before_symbol", "a power input drawn before the power symbol it meets")
t = root.symbol("fixture:T_power_in", ref("U"), (x + 7.62, y + 7.62))
p = root.symbol("fixture:PWR", ref("#PWR"), (x, y), value="PIN_FIRST")
root.wire(p["1"], (p["1"][0], t["1"][1]))
root.wire((p["1"][0], t["1"][1]), t["1"])

x, y = cell("power_in_by_label", "two power inputs joined by a label, the lower drawn first")
lower = root.symbol("fixture:T_power_in", ref("U"), (x + 7.62, y + 10.16))
upper = root.symbol("fixture:T_power_in", ref("U"), (x + 7.62, y + 2.54))
for t in (lower, upper):
    root.wire(t["1"], (t["1"][0] - 2.54, t["1"][1]))
    root.label("label", "PIN_BY_LABEL", (t["1"][0] - 2.54, t["1"][1]))

x, y = cell("nc_on_free_wire_end", "a no-connect flag on a wire end, no pin anywhere")
root.wire((x, y), (x + 7.62, y))
root.no_connect((x, y))

x, y = cell("local_and_global_names", "a local and a global label of other names on one wire")
r = root.symbol("fixture:R", ref("R"), (x, y + 10.16))
root.wire(r["1"], (r["1"][0], r["1"][1] - 7.62))
root.label("label", "AAA_LOCAL", (r["1"][0], r["1"][1] - 2.54))
root.label("global_label", "ZZZ_GLOBAL", (r["1"][0], r["1"][1] - 7.62))
root.no_connect(r["2"])

x, y = cell("output_input_power_in", "an output, an input and a power input on one wire")
ts = [root.symbol(f"fixture:T_{t}", ref("U"), (x + 7.62, y + 5.08 * i))
      for i, t in enumerate(("output", "input", "power_in"))]  # fmt: skip
for t, t_next in zip(ts, ts[1:], strict=False):
    root.wire(t["1"], t_next["1"])

x, y = cell("similar_power", "two power symbols whose values differ in case, each on a pin")
for i, value in enumerate(("SIMPWR", "simpwr")):
    p = root.symbol("fixture:PWR", ref("#PWR"), (x + 10.16 * i, y), value=value)
    r = root.symbol("fixture:R", ref("R"), (x + 10.16 * i, y + 3.81))
    root.no_connect(r["2"])

x, y = cell("similar_label_and_power", "a label and a power symbol whose names differ in case")
p = root.symbol("fixture:PWR", ref("#PWR"), (x, y), value="LBLPWR")
r = root.symbol("fixture:R", ref("R"), (x, y + 3.81))
root.no_connect(r["2"])
r2 = root.symbol("fixture:R", ref("R"), (x + 10.16, y + 10.16))
root.wire(r2["1"], (r2["1"][0], r2["1"][1] - 5.08))
root.label("label", "lblpwr", (r2["1"][0], r2["1"][1] - 5.08))
root.no_connect(r2["2"])

x, y = cell("same_local_global", "a local and a global label of one name, on different pins")
r1 = root.symbol("fixture:R", ref("R"), (x, y + 10.16))
root.wire(r1["1"], (r1["1"][0], r1["1"][1] - 5.08))
root.label("label", "SAME_LG", (r1["1"][0], r1["1"][1] - 5.08))
root.no_connect(r1["2"])
r2 = root.symbol("fixture:R", ref("R"), (x + 10.16, y + 10.16))
root.wire(r2["1"], (r2["1"][0], r2["1"][1] - 5.08))
root.label("global_label", "SAME_LG", (r2["1"][0], r2["1"][1] - 5.08))
root.no_connect(r2["2"])

x, y = cell("four_wires_no_junction", "four wires meeting at a point, no junction dot")
c = (x + 5.08, y + 5.08)
for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
    end = (c[0] + 5.08 * dx, c[1] + 5.08 * dy)
    root.wire(c, end)
    root.label("label", "FOUR_NOJ", end)

x, y = cell("three_wires_and_pin", "three wires and a pin meeting at a junction")
r = root.symbol("fixture:R", ref("R"), (x + 5.08, y + 8.89))
c = r["1"]
for dx, dy in ((-1, 0), (1, 0), (0, -1)):
    end = (c[0] + 5.08 * dx, c[1] + 5.08 * dy)
    root.wire(c, end)
    root.label("label", "THREE_PIN", end)
root.junction(c)
root.no_connect(r["2"])

x, y = cell("symbol_off_grid", "a part placed off the grid, its pins wired to labels")
r = root.symbol("fixture:R", ref("R"), (x + 0.635, y + 10.16))
root.wire(r["1"], (r["1"][0], r["1"][1] - 2.54))
root.label("label", "OFFGRID_TOP", (r["1"][0], r["1"][1] - 2.54))
root.no_connect(r["2"])

x, y = cell("label_on_power_only", "a label on a power symbol's wire, no other pin")
p = root.symbol("fixture:PWR", ref("#PWR"), (x, y), value="LBL_ON_PWR")
root.wire(p["1"], (p["1"][0], p["1"][1] + 5.08))
root.label("label", "LBL_ON_PWR", (p["1"][0], p["1"][1] + 5.08))

x, y = cell("junction_mid_wire_alone", "a junction on the middle of a wire touching nothing")
root.wire((x, y), (x + 10.16, y))
root.junction((x + 5.08, y))

x, y = cell("two_floating_labels", "two labels of one name on nothing")
root.label("label", "TWO_FLOAT", (x, y))
root.label("label", "TWO_FLOAT", (x + 7.62, y))

x, y = cell("pin_label_two_floating", "a pin's label, and two floating labels of its name")
r = root.symbol("fixture:R", ref("R"), (x + 7.62, y + 10.16))
root.wire(r["1"], (r["1"][0], r["1"][1] - 2.54))
root.label("label", "PIN_TWO_FLOAT", (r["1"][0], r["1"][1] - 2.54))
root.label("label", "PIN_TWO_FLOAT", (x, y))
root.label("label", "PIN_TWO_FLOAT", (x, y + 5.08))
root.no_connect(r["2"])

x, y = cell("label_on_pin_and_floating", "a label on a pin's end, and a floating label of its name")
r = root.symbol("fixture:R", ref("R"), (x + 7.62, y + 10.16))
root.label("label", "ON_PIN_FLOAT", r["1"])
root.label("label", "ON_PIN_FLOAT", (x, y))
root.no_connect(r["2"])

x, y = cell("stub_pin_and_floating", "a pin's label, a stub's label and a floating label, one name")
r = root.symbol("fixture:R", ref("R"), (x + 7.62, y + 10.16))
root.wire(r["1"], (r["1"][0], r["1"][1] - 2.54))
root.label("label", "STUB_PIN_FLOAT", (r["1"][0], r["1"][1] - 2.54))
root.wire((x, y + 2.54), (x + 2.54, y + 2.54))
root.label("label", "STUB_PIN_FLOAT", (x, y + 2.54))
root.label("label", "STUB_PIN_FLOAT", (x, y + 7.62))
root.no_connect(r["2"])

x, y = cell(
    "order_power_in_refs", "two power inputs on a wire: U900 drawn first and lower, U100 upper"
)
lower = root.symbol("fixture:T_power_in", ref("U", 900), (x + 7.62, y + 7.62))
upper = root.symbol("fixture:T_power_in", ref("U", 100), (x + 7.62, y))
root.wire(upper["1"], lower["1"])

x, y = cell(
    "order_power_in_refs_2", "two power inputs on a wire: U101 drawn first and upper, U901 lower"
)
upper = root.symbol("fixture:T_power_in", ref("U", 101), (x + 7.62, y))
lower = root.symbol("fixture:T_power_in", ref("U", 901), (x + 7.62, y + 7.62))
root.wire(upper["1"], lower["1"])

x, y = cell(
    "order_power_in_refs_3", "two power inputs on a wire: U902 drawn first and upper, U102 lower"
)
upper = root.symbol("fixture:T_power_in", ref("U", 902), (x + 7.62, y))
lower = root.symbol("fixture:T_power_in", ref("U", 102), (x + 7.62, y + 7.62))
root.wire(upper["1"], lower["1"])

x, y = cell("order_inputs_refs", "two inputs on a wire: U903 drawn first and upper, U103 lower")
upper = root.symbol("fixture:T_input", ref("U", 903), (x + 7.62, y))
lower = root.symbol("fixture:T_input", ref("U", 103), (x + 7.62, y + 7.62))
root.wire(upper["1"], lower["1"])

x, y = cell("three_wires_alone", "three wires in a chain, touching nothing else")
root.wire((x, y), (x + 5.08, y))
root.wire((x + 5.08, y), (x + 5.08, y + 5.08))
root.wire((x + 5.08, y + 5.08), (x + 10.16, y + 5.08))

x, y = cell("pin_off_grid_unwired", "a part off the grid with nothing attached")
root.symbol("fixture:R", ref("R"), (x + 0.635, y + 10.16))

x, y = cell("nc_off_grid", "a no-connect flag off the grid, on nothing")
root.no_connect((x + 0.635, y + 0.635))

x, y = cell("label_off_grid", "a label off the grid, on a pin's wire")
r = root.symbol("fixture:R", ref("R"), (x, y + 10.16))
root.wire(r["1"], (r["1"][0], r["1"][1] - 5.08))
root.label("label", "LBL_OFF_GRID", (r["1"][0], r["1"][1] - 3.175))
root.no_connect(r["2"])

x, y = cell("wire_mid_off_grid", "a wire from a pin whose far end is off the grid, with a label")
r = root.symbol("fixture:R", ref("R"), (x, y + 10.16))
root.wire(r["1"], (r["1"][0] + 0.635, r["1"][1] - 5.08))
root.label("label", "WIRE_OFF_GRID", (r["1"][0] + 0.635, r["1"][1] - 5.08))
root.no_connect(r["2"])

root.write(OUT / "erc.kicad_sch")
project = {
    "erc": {"rule_severities": RULES},
    "meta": {"filename": "erc.kicad_pro", "version": 3},
}
(OUT / "erc.kicad_pro").write_bytes((json.dumps(project, indent=2) + "\n").encode("utf-8"))
(OUT / "cases.json").write_bytes(
    (json.dumps({"questions": CASES, "items": ITEMS}, indent=1) + "\n").encode("utf-8")
)
print(f"{len(CASES)} cases")
