"""Write drcrules/: a board with custom rules of its own (`.kicad_dru`),
asking KiCad's DRC how they decide track widths, vias, holes and the
clearances -- which rule wins, against the board's minimums, the net classes,
a pad's own clearance and a zone's; what a condition matches; what the
message says. One question per case, each ten millimetres from the next.

The answers are recorded in `drcrules/drcrules.kicad.json`; to change the
board, edit this, run it, and record KiCad's answers again.
"""

import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from boardgen import (  # noqa: E402
    add,
    arc,
    cell,
    footprint,
    npth,
    rect,
    segment,
    smd,
    start,
    tht,
    uid,
    via,
    write,
    xy,
    zone,
)

start("drcrules", uuid.UUID("7a1c5d2e-0000-4000-8000-0000000d4c1e"))

CLASSES = {
    "classes": [
        {"name": "Default", "clearance": 0.2, "track_width": 0.2, "via_diameter": 0.6,
         "via_drill": 0.3, "microvia_diameter": 0.3, "microvia_drill": 0.1,
         "diff_pair_width": 0.2, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
         "priority": 2147483647, "bus_width": 12, "line_style": 0, "wire_width": 6,
         "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)"},
        {"name": "Wide", "clearance": 0.4, "priority": 0},
        {"name": "Hot", "clearance": 0.3, "priority": 1},
        {"name": "Thin", "clearance": 0.1, "priority": 2},
    ],
    "netclass_patterns": [
        {"pattern": "W*", "netclass": "Wide"},
        {"pattern": "*H*", "netclass": "Hot"},
        {"pattern": "T*", "netclass": "Thin"},
    ],
}  # fmt: skip


def track(net, col, row, width, layer="F.Cu"):
    x, y = cell(col, row)
    segment(net, (x - 2, y), (x + 2, y), width, layer, net)


def pair(key, col, row, gap, a_net, b_net, width=0.2):
    """Two parallel tracks `gap` apart."""
    x, y = cell(col, row)
    segment(key + "a", (x - 2, y), (x + 2, y), width, "F.Cu", a_net)
    segment(key + "b", (x - 2, y + width + gap), (x + 2, y + width + gap), width, "F.Cu", b_net)


# -- row 0: track widths ---------------------------------------------------------------------

track("WA", 0, 0, 0.25)  # Wide, a rule's min 0.3
track("WB", 1, 0, 0.35)  # Wide, above it
track("WC", 2, 0, 0.5)  # a rule's max 0.4
track("TA", 3, 0, 0.15)  # Thin: a rule's min 0.1 under the board's 0.2
track("TB", 4, 0, 0.05)  # Thin, under the rule too
track("SA", 5, 0, 0.25)  # two rules, min 0.3 then min 0.2: the later wins
track("IA", 6, 0, 0.25, "In1.Cu")  # a rule on inner layers only, on one
track("IB", 7, 0, 0.25)  # the same rule, on an outer layer
track("DA", 8, 0, 0.15)  # no rule: the board's minimum
track("MX", 9, 0, 0.1)  # a rule with only a max: the board's minimum still holds

# -- row 1: clearances --------------------------------------------------------------------------

for col, (key, a, b, gap) in enumerate([
    ("K0", "WK0", "DK0", 0.45),  # Wide to Default: a rule's 0.5 over the class's 0.4
    ("K1", "WK1", "DK1", 0.55),
    ("K2", "DK2", "WK2", 0.45),  # the same, A and B the other way round
    ("K3", "DK3a", "DK3b", 0.15),  # in the area 'loose': a rule's 0.1 under the class's 0.2
    ("K4", "DK4a", "DK4b", 0.15),  # outside it
    ("K5", "DK5a", "DK5b", 0.05),  # in the area, under the rule too
    ("K6", "USBK6", "DK6", 0.3),  # a rule for all but '*USB*' nets
    ("K7", "XK7", "DK7", 0.3),
    ("K8", "LK8a", "LK8b", 0.15),  # a rule's 0.12 under the board's minimum 0.18
]):  # fmt: skip
    pair(key, col, 1, gap, a, b)
for col in (3, 5):  # the area 'loose'
    x, y = cell(col, 1)
    pts = " ".join(f"(xy {xy(p)})" for p in rect(x - 3, y - 1, 6, 2))
    add(f'(zone (layer "F.Cu") (uuid "{uid("area", col)}") (name "loose") (hatch edge 0.5)'
        f" (connect_pads (clearance 0)) (min_thickness 0.25) (filled_areas_thickness no)"
        f" (keepout (tracks allowed) (vias allowed) (pads allowed) (copperpour allowed)"
        f" (footprints allowed)) (fill (thermal_gap 0.5) (thermal_bridge_width 0.5))"
        f" (polygon (pts {pts})))")  # fmt: skip

# -- row 2: a pad's own clearance, zones, pairs, types --------------------------------------

x, y = cell(0, 2)  # a pad's own 0.3 against a Wide track 0.4 away: the pad's stands
footprint("KP", (x, y), [smd("KP", "1", (0, 0), (1, 1), "DKP", extra=" (clearance 0.3)")],
          courtyard="none")  # fmt: skip
segment("KPt", (x - 2, y + 1), (x + 2, y + 1), 0.2, "F.Cu", "WKP")
x, y = cell(1, 2)  # a track 0.5 from a zone: a rule's 1 for tracks against zones
fill = rect(x - 3, y + 0.6, 6, 2)
zone("KZ", "DKZz", ("F.Cu",), fill, clearance=0.2, fill=[fill])
segment("KZt", (x - 2, y), (x + 2, y), 0.2, "F.Cu", "ZKZ")
x, y = cell(2, 2)  # 0.4 from a zone asking 0.8: a rule's 0.3 stands
fill = rect(x - 3, y + 0.5, 6, 2)
zone("ZA", "ZAz", ("F.Cu",), fill, clearance=0.8, fill=[fill])
segment("ZA", (x - 2, y), (x + 2, y), 0.2, "F.Cu", "ZAt")
x, y = cell(3, 2)  # 0.4 from a zone asking 0.2: a rule's 0.6
fill = rect(x - 3, y + 0.5, 6, 2)
zone("ZB", "ZBz", ("F.Cu",), fill, clearance=0.2, fill=[fill])
segment("ZB", (x - 2, y), (x + 2, y), 0.2, "F.Cu", "ZBt")
x, y = cell(4, 2)  # a pair's P and N 0.15 apart: a rule for the pair's 0.1
segment("DPP", (x - 2, y), (x + 2, y), 0.2, "F.Cu", "DP_P")
segment("DPN", (x - 2, y + 0.35), (x + 2, y + 0.35), 0.2, "F.Cu", "DP_N")
x, y = cell(5, 2)  # a rule for vias only, two tracks
segment("Gtr", (x - 2, y), (x + 2, y), 0.2, "F.Cu", "GT")
segment("Gvi", (x - 2, y + 0.45), (x + 2, y + 0.45), 0.2, "F.Cu", "GV")
x, y = cell(6, 2)  # an arc is a Track
arc("AR", (x - 2, y), (x, y - 1), (x + 2, y), 0.15, "F.Cu", "AR")
track("case", 7, 2, 0.15)  # 'CASE' is case
track("WH", 8, 2, 0.15)  # a net in Wide and Hot: NetClass == 'Wide'
track("WHb", 9, 2, 0.15)  # NetClass == 'Wide,Hot'

# -- row 3: vias and holes --------------------------------------------------------------------

for col, (net, size, drill) in enumerate([
    ("VA", 0.45, 0.25),  # a rule's via_diameter min 0.5, hole_size min 0.3
    ("VB", 0.9, 0.3),  # a rule's via_diameter max 0.8
    ("VC", 0.6, 0.2),  # a rule's annular_width max 0.15
    ("VD", 0.5, 0.3),  # a rule on inner layers: the via passes them
    ("VE", 0.5, 0.3),  # a rule on outer layers
]):  # fmt: skip
    x, y = cell(col, 3)
    via(net, (x, y), size, drill, net)
x, y = cell(5, 3)  # a pad's hole: a rule for pads' holes
footprint("PH", (x, y), [tht("PH", "1", (0, 0), (1.2, 1.2), 0.5, "PH")], attr="through_hole",
          courtyard="none")  # fmt: skip
x, y = cell(6, 3)  # a track 0.3 from an unplated hole: a rule's hole clearance 0.5
footprint("HA", (x, y), [npth("HA", "", (0, 0), 1.0)], courtyard="none")
segment("HAt", (x - 2, y + 0.9), (x + 2, y + 0.9), 0.2, "F.Cu", "HAt")
x, y = cell(7, 3)  # via holes 0.9 apart: a rule's hole to hole 1
via("VV1", (x - 0.6, y), 0.6, 0.3, "VV1")
via("VV2", (x + 0.6, y), 0.6, 0.3, "VV2")
track("WH1", 8, 3, 0.15)  # a net in Wide and Hot: NetClass == 'Hot'
track("WHAS", 9, 3, 0.15)  # hasNetclass('Hot')

# -- row 4: the board's edge, severities --------------------------------------------------------

x, y = cell(0, 4)  # the outline runs at x = 10
segment("EA", (10.8, y), (10.8, y + 3), 0.2, "F.Cu", "EA")  # a rule's edge clearance 1
segment("EB", (10.5, y + 5), (10.5, y + 8), 0.2, "F.Cu", "EB")  # a rule's 0.3, the board's 0.5
track("SV", 2, 4, 0.15)  # a rule's own severity: warning
track("SI", 3, 4, 0.15)  # a rule's own severity: ignore

RULES = """(version 1)
(rule "wide_min"
	(condition "A.NetClass == 'Wide'")
	(constraint track_width (min 0.3mm)))
(rule "wide_max"
	(condition "A.NetName == 'WC'")
	(constraint track_width (max 0.4mm)))
(rule "thin"
	(condition "A.NetClass == 'Thin'")
	(constraint track_width (min 0.1mm)))
(rule "sig_first"
	(condition "A.NetName == 'S*'")
	(constraint track_width (min 0.3mm)))
(rule "sig_second"
	(condition "A.NetName == 'S*'")
	(constraint track_width (min 0.2mm)))
(rule "inner_only"
	(layer inner)
	(condition "A.NetName == 'I*'")
	(constraint track_width (min 0.3mm)))
(rule "mx_max"
	(condition "A.NetName == 'MX'")
	(constraint track_width (max 0.4mm)))
(rule "wide_default"
	(condition "A.NetClass == 'Wide' && B.NetClass == 'Default'")
	(constraint clearance (min 0.5mm)))
(rule "loose"
	(condition "A.intersectsArea('loose')")
	(constraint clearance (min 0.1mm)))
(rule "not_usb"
	(condition "(A.NetName == 'USBK6' || A.NetName == 'XK7') && A.NetName != '*USB*'")
	(constraint clearance (min 0.5mm)))
(rule "low"
	(condition "A.NetName == 'L*'")
	(constraint clearance (min 0.12mm)))
(rule "z_zone"
	(condition "A.NetName == 'Z*' && B.Type == 'Zone' && A.Layer == B.Layer")
	(constraint clearance (min 1mm)))
(rule "zone_a"
	(condition "A.NetName == 'ZAt' && B.Type == 'Zone'")
	(constraint clearance (min 0.3mm)))
(rule "zone_b"
	(condition "A.NetName == 'ZBt' && B.Type == 'Zone'")
	(constraint clearance (min 0.6mm)))
(rule "pair"
	(condition "A.inDiffPair('DP_')")
	(constraint clearance (min 0.1mm)))
(rule "vias_only"
	(condition "A.Type == 'Via' && A.NetName == 'G*'")
	(constraint clearance (min 0.5mm)))
(rule "arcs"
	(condition "A.Type == 'Track' && A.NetName == 'AR'")
	(constraint track_width (min 0.3mm)))
(rule "upper"
	(condition "A.NetName == 'CASE'")
	(constraint track_width (min 0.3mm)))
(rule "wide_class"
	(condition "A.NetName == 'WH' && A.NetClass == 'Wide'")
	(constraint track_width (min 0.3mm)))
(rule "composite_class"
	(condition "A.NetName == 'WHb' && A.NetClass == 'Wide,Hot'")
	(constraint track_width (min 0.3mm)))
(rule "hot_class"
	(condition "A.NetName == 'WH1' && A.NetClass == 'Hot'")
	(constraint track_width (min 0.3mm)))
(rule "has_hot"
	(condition "A.NetName == 'WHAS' && A.hasNetclass('Hot')")
	(constraint track_width (min 0.3mm)))
(rule "via_a"
	(condition "A.NetName == 'VA'")
	(constraint via_diameter (min 0.5mm))
	(constraint hole_size (min 0.3mm)))
(rule "via_b"
	(condition "A.NetName == 'VB'")
	(constraint via_diameter (max 0.8mm)))
(rule "via_c"
	(condition "A.NetName == 'VC'")
	(constraint annular_width (max 0.15mm)))
(rule "via_d"
	(layer inner)
	(condition "A.NetName == 'VD'")
	(constraint via_diameter (min 0.6mm)))
(rule "via_e"
	(layer outer)
	(condition "A.NetName == 'VE'")
	(constraint via_diameter (min 0.6mm)))
(rule "pad_hole"
	(condition "A.Type == 'Pad' && A.NetName == 'PH'")
	(constraint hole_size (min 0.6mm)))
(rule "npth_far"
	(condition "A.NetName == 'HAt' || B.NetName == 'HAt'")
	(constraint hole_clearance (min 0.5mm)))
(rule "holes_far"
	(condition "A.NetName == 'VV*'")
	(constraint hole_to_hole (min 1mm)))
(rule "edge_far"
	(condition "A.NetName == 'EA'")
	(constraint edge_clearance (min 1mm)))
(rule "edge_near"
	(condition "A.NetName == 'EB'")
	(constraint edge_clearance (min 0.3mm)))
(rule "as_warning"
	(condition "A.NetName == 'SV'")
	(constraint track_width (min 0.3mm))
	(severity warning))
(rule "as_ignore"
	(condition "A.NetName == 'SI'")
	(constraint track_width (min 0.3mm))
	(severity ignore))
"""

out = HERE / "drcrules"
write(
    out, 10, 5,
    severities={
        "unconnected_items": "ignore", "track_dangling": "ignore", "via_dangling": "ignore",
        "silk_overlap": "ignore", "isolated_copper": "ignore", "lib_footprint_issues": "ignore",
        "lib_footprint_mismatch": "ignore", "missing_courtyard": "ignore",
    },
    rules={"min_track_width": 0.2, "min_clearance": 0.18, "min_via_diameter": 0.4,
           "min_through_hole_diameter": 0.2},
    net_settings=CLASSES,
)  # fmt: skip
(out / "drcrules.kicad_dru").write_bytes(RULES.encode("utf-8"))
