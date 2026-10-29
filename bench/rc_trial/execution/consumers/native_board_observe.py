"""Fresh read-only native board observation and independent physical pin checks."""

import faulthandler
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

faulthandler.enable()
faulthandler.dump_traceback_later(10, repeat=False)
import pcbnew  # noqa: E402 - native logging configured before SDK import
from frozen_intent_view import expand  # noqa: E402
from sexpr_reader import child, children, parse  # noqa: E402

board_path, intent_path, output = map(Path, sys.argv[1:4])
if output.exists():
    raise SystemExit("Observation destination already exists")
source_hash = hashlib.sha256(board_path.read_bytes()).hexdigest()
intent = json.loads(intent_path.read_text(encoding="utf-8-sig"))
intent, isolated = expand(intent)
board = pcbnew.LoadBoard(str(board_path.resolve()))
print("READ_STAGE loaded board", file=sys.stderr, flush=True)


def xy(v):
    return [v.x, v.y]


def uid(v):
    return v.m_Uuid.AsString()


def logical(name):
    return name[1:] if name.startswith("/") and name.count("/") == 1 else name


footprints = []
for footprint in sorted(board.GetFootprints(), key=lambda x: x.GetReference()):
    pads = []
    for pad in footprint.Pads():
        pads.append(
            {
                "uuid": uid(pad),
                "number": pad.GetNumber(),
                "net": pad.GetNetname(),
                "logical_net": logical(pad.GetNetname()),
                "position_nm": xy(pad.GetPosition()),
                "size_nm": xy(pad.GetSize()),
                "drill_nm": xy(pad.GetDrillSize()),
                "shape": int(pad.GetShape()),
                "rotation_deg": pad.GetOrientationDegrees(),
                "layers": [
                    board.GetLayerName(layer)
                    for layer in pad.GetLayerSet().Seq()
                    if board.IsLayerEnabled(layer)
                ],
            }
        )
    footprints.append(
        {
            "ref": footprint.GetReference(),
            "value": footprint.GetValue(),
            "uuid": uid(footprint),
            "source_path": footprint.GetPath().AsString(),
            "footprint": str(footprint.GetFPID().GetLibNickname())
            + ":"
            + str(footprint.GetFPID().GetLibItemName()),
            "position_nm": xy(footprint.GetPosition()),
            "rotation_deg": footprint.GetOrientationDegrees(),
            "layer": board.GetLayerName(footprint.GetLayer()),
            "exclude_from_bom": bool(footprint.GetAttributes() & pcbnew.FP_EXCLUDE_FROM_BOM),
            "locked": bool(footprint.IsLocked()),
            "pads": pads,
        }
    )
edges = []
print("READ_STAGE footprints read", file=sys.stderr, flush=True)
for drawing in board.GetDrawings():
    if drawing.GetLayer() == pcbnew.Edge_Cuts:
        edges.append(
            {
                "uuid": uid(drawing),
                "shape": int(drawing.GetShape()),
                "start_nm": xy(drawing.GetStart()),
                "end_nm": xy(drawing.GetEnd()),
                "width_nm": drawing.GetWidth(),
            }
        )
tracks = []
print("READ_STAGE edges read", file=sys.stderr, flush=True)
for track in board.GetTracks():
    width = track.GetWidth(pcbnew.F_Cu) if isinstance(track, pcbnew.PCB_VIA) else track.GetWidth()
    item = {
        "uuid": uid(track),
        "type": track.GetClass(),
        "net": track.GetNetname(),
        "logical_net": logical(track.GetNetname()),
        "start_nm": xy(track.GetStart()),
        "end_nm": xy(track.GetEnd()),
        "width_nm": width,
        "layer": board.GetLayerName(track.GetLayer()),
        "locked": bool(track.IsLocked()),
    }
    if isinstance(track, pcbnew.PCB_VIA):
        item["drill_nm"] = track.GetDrillValue()
    tracks.append(item)
root = parse(board_path.read_text(encoding="utf-8"))


def owned_uuids(node):
    if not isinstance(node, list):
        return []
    if node and node[0] == "uuid":
        return [node[1]]
    return [identity for part in node for identity in owned_uuids(part)]


footprint_owned_uuids = {
    next(prop[2] for prop in children(fp, "property") if prop[1] == "Reference"): sorted(
        owned_uuids(fp)
    )
    for fp in children(root, "footprint")
}
print("READ_STAGE tracks and raw board parsed", file=sys.stderr, flush=True)
zone_definitions = []
zone_fills = []
for zone in children(root, "zone"):
    zone_definitions.append(
        [
            v
            for v in zone
            if not (isinstance(v, list) and v and v[0] in ("filled_polygon", "fill_segments"))
        ]
    )
    zone_fills.append(
        [
            v
            for v in zone
            if isinstance(v, list) and v and v[0] in ("filled_polygon", "fill_segments")
        ]
    )
failures = []
raw_nets = [net.GetNetname() for net in board.GetNetsByNetcode().values() if net.GetNetname()]
raw_to_logical = {name: logical(name) for name in raw_nets}
if len(set(raw_to_logical.values())) != len(raw_to_logical):
    failures.append({"check": "injective_raw_net_to_logical_mapping", "mapping": raw_to_logical})
if len(raw_nets) != len(set(raw_nets)):
    failures.append({"check": "unique_raw_net_declarations", "raw_nets": raw_nets})
if Counter(raw_to_logical.values()) != Counter(intent["nets"].keys()):
    failures.append(
        {
            "check": "exact_declared_logical_net_multiset",
            "actual": list(raw_to_logical.values()),
            "expected": list(intent["nets"]),
        }
    )
identity_checks = []
schematic_symbols = None
if len(sys.argv) > 4:
    xml_path = Path(sys.argv[4])
    document = ET.parse(xml_path).getroot()
    root_only = all(
        part.find("sheetpath") is not None and part.find("sheetpath").get("names") == "/"
        for part in document.findall("./components/comp")
    )
    if not root_only:
        failures.append({"check": "single_root_hierarchy_required_for_qualification"})
    schematic_root = parse(board_path.with_suffix(".kicad_sch").read_text(encoding="utf-8-sig"))
    schematic_symbols = children(schematic_root, "symbol")
    root_uuid = child(schematic_root, "uuid")[1]
    identity_by_ref = {
        part.attrib["ref"]: (part.find("sheetpath").get("tstamps"), part.findtext("tstamps"))
        for part in document.findall("./components/comp")
    }
    for footprint in footprints:
        source_sheet, source_symbol = identity_by_ref.get(footprint["ref"], ("MISSING", "MISSING"))
        expected_path = source_sheet.rstrip("/") + "/" + source_symbol
        ok = footprint["source_path"] == expected_path
        identity_checks.append(
            {
                "ref": footprint["ref"],
                "observed_path": footprint["source_path"],
                "expected_path": expected_path,
                "official_xml_sheetpath": source_sheet,
                "official_xml_symbol_uuid": source_symbol,
                "schematic_root_uuid_record_only": root_uuid,
                "passed": ok,
            }
        )
        if not ok:
            failures.append(
                {
                    "check": "source_path_symbol_uuid",
                    "ref": footprint["ref"],
                    "expected": expected_path,
                    "actual": footprint["source_path"],
                }
            )
else:
    identity_checks = "not_requested_initial_unlinked_observation"
if Counter(f["ref"] for f in footprints) != Counter(p["ref"] for p in intent["components"]):
    failures.append({"check": "reference_occurrences"})
for expected in intent["components"]:
    matches = [f for f in footprints if f["ref"] == expected["ref"]]
    if len(matches) != 1:
        continue
    actual = matches[0]
    if "in_bom" in expected and actual["exclude_from_bom"] == expected["in_bom"]:
        failures.append({"check": "bom_flag", "ref": expected["ref"]})
    for key in ("value", "footprint"):
        if actual[key] != expected[key]:
            failures.append(
                {
                    "check": key,
                    "ref": expected["ref"],
                    "actual": actual[key],
                    "expected": expected[key],
                }
            )
    actual_pins = Counter((p["number"], p["logical_net"]) for p in actual["pads"])
    if actual_pins != Counter(expected["pins"].items()):
        failures.append(
            {
                "check": "physical_pad_occurrences",
                "ref": expected["ref"],
                "actual": list(actual_pins.elements()),
                "expected": list(expected["pins"].items()),
            }
        )
project = board_path.with_suffix(".kicad_pro")
report = {
    "footprint_owned_uuids": footprint_owned_uuids,
    "all_uuid_mentions": sorted(
        set(
            re.findall(
                r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",
                board_path.read_text(encoding="utf-8"),
            )
        )
    ),
    "source": (
        "fresh official pcbnew LoadBoard + independent raw s-expression zone "
        "reader; no setters/save/fill/route"
    ),
    "board": str(board_path),
    "board_sha256": source_hash,
    "native_version": pcbnew.GetBuildVersion(),
    "physical_intent_passed": not failures,
    "failures": failures,
    "raw_net_to_logical": raw_to_logical,
    "identity_checks": identity_checks,
    "schematic_symbols": schematic_symbols,
    "footprints": footprints,
    "copper_layer_count": board.GetCopperLayerCount(),
    "edges": edges,
    "tracks": tracks,
    "zone_definitions": zone_definitions,
    "saved_filled_polygons": zone_fills,
    "project": json.loads(project.read_text(encoding="utf-8-sig")) if project.exists() else None,
}
if hashlib.sha256(board_path.read_bytes()).hexdigest() != source_hash:
    raise RuntimeError("Board bytes changed during observation")
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(report, indent=2), encoding="utf-8")
faulthandler.cancel_dump_traceback_later()
print(
    json.dumps(
        {
            "report": str(output),
            "report_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "physical_intent_passed": not failures,
            "failures": failures,
            "footprint_count": len(footprints),
            "pad_count": sum(len(f["pads"]) for f in footprints),
            "copper_layer_count": report["copper_layer_count"],
            "edge_count": len(edges),
            "track_count": len(tracks),
            "zone_count": len(zone_definitions),
        },
        indent=2,
    )
)
raise SystemExit(0 if not failures else 1)
