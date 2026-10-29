"""Bounded independent geometry checks. Native objects are only loaded/read."""

import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path

import pcbnew
from sexpr_reader import child, children, parse

board_path, intent_path, plan_path, initial_report_path, output = map(Path, sys.argv[1:6])
if output.exists():
    raise SystemExit("Refuse existing report")
intent = json.loads(intent_path.read_text(encoding="utf-8-sig"))
plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
initial = json.loads(initial_report_path.read_text(encoding="utf-8-sig"))
board = pcbnew.LoadBoard(str(board_path.resolve()))
raw = parse(board_path.read_text(encoding="utf-8-sig"))
project = json.loads(board_path.with_suffix(".kicad_pro").read_text(encoding="utf-8-sig"))
failures = []


def require(ok, kind, **facts):
    if not ok:
        failures.append({"check": kind, **facts})


def xy(point):
    return (point.x, point.y)


def bbox(item):
    box = item.GetBoundingBox()
    return [box.GetLeft(), box.GetTop(), box.GetRight(), box.GetBottom()]


edges = [g for g in board.GetDrawings() if g.GetLayer() == pcbnew.Edge_Cuts]
endpoints = [xy(p) for g in edges for p in (g.GetStart(), g.GetEnd())]
counts = Counter(endpoints)
require(
    len(edges) == 4 and len(counts) == 4 and all(n == 2 for n in counts.values()),
    "closed_four_edge_contour",
    endpoints=endpoints,
)
require(
    all(
        int(g.GetShape()) == 0
        and (g.GetStart().x == g.GetEnd().x or g.GetStart().y == g.GetEnd().y)
        for g in edges
    ),
    "axis_aligned_straight_edges",
)
bounds = [
    min(x for x, y in endpoints),
    min(y for x, y in endpoints),
    max(x for x, y in endpoints),
    max(y for x, y in endpoints),
]
dimensions = [(bounds[2] - bounds[0]) / 1e6, (bounds[3] - bounds[1]) / 1e6]
require(
    intent["board"]["width_mm"][0] <= dimensions[0] <= intent["board"]["width_mm"][1]
    and intent["board"]["height_mm"][0] <= dimensions[1] <= intent["board"]["height_mm"][1],
    "mechanical_range",
    actual_mm=dimensions,
)
require(board.GetCopperLayerCount() == 2, "two_copper_layers")
poses = []
silk = []
pad_boxes = []
for footprint in board.GetFootprints():
    expected = plan["final_pcb_poses"][footprint.GetReference()]
    wanted = [round(v * 1e6) for v in expected["xy_mm"]]
    pose = {
        "ref": footprint.GetReference(),
        "position_nm": list(xy(footprint.GetPosition())),
        "rotation_deg": footprint.GetOrientationDegrees(),
        "layer": board.GetLayerName(footprint.GetLayer()),
    }
    poses.append(pose)
    require(
        pose["position_nm"] == wanted
        and pose["rotation_deg"] == expected["rotation_deg"]
        and pose["layer"] == expected["layer"],
        "planned_pose",
        actual=pose,
        expected=expected,
    )
    for pad in footprint.Pads():
        box = bbox(pad)
        gap = min(box[0] - bounds[0], box[1] - bounds[1], bounds[2] - box[2], bounds[3] - box[3])
        require(
            gap >= round(intent["board"]["footprint_copper_inside_edge_mm"] * 1e6),
            "pad_edge_clearance",
            ref=footprint.GetReference(),
            pin=pad.GetNumber(),
            actual_nm=gap,
        )
        margin = pad.GetSolderMaskExpansion(pcbnew.F_Cu)
        pad_boxes.append(
            {
                "ref": footprint.GetReference(),
                "pin": pad.GetNumber(),
                "box": [box[0] - margin, box[1] - margin, box[2] + margin, box[3] + margin],
                "layer": footprint.GetLayer(),
                "pad": pad,
            }
        )


def box_distance(a, b):
    return math.hypot(max(a[0] - b[2], b[0] - a[2], 0), max(a[1] - b[3], b[1] - a[3], 0))


for footprint in board.GetFootprints():
    reference = footprint.Reference()
    require(reference.IsVisible(), "reference_visible", ref=footprint.GetReference())
    box = bbox(reference)
    edge_gap = min(box[0] - bounds[0], box[1] - bounds[1], bounds[2] - box[2], bounds[3] - box[3])
    pad_gap = min(
        box_distance(box, p["box"]) for p in pad_boxes if p["pad"].IsOnLayer(pcbnew.F_Mask)
    )
    silk.append(
        {
            "ref": footprint.GetReference(),
            "box_nm": box,
            "min_edge_gap_nm": edge_gap,
            "min_pad_mask_bbox_gap_nm": pad_gap,
        }
    )
    require(
        min(edge_gap, pad_gap) >= round(intent["visual"]["silk_clearance_mm"] * 1e6),
        "reference_silk_clearance",
        ref=footprint.GetReference(),
        edge_gap_nm=edge_gap,
        pad_gap_nm=pad_gap,
    )
track_counts = Counter()
via_dimensions = []
widths = set()
for track in board.GetTracks():
    if isinstance(track, pcbnew.PCB_VIA):
        dimensions_via = [track.GetWidth(pcbnew.F_Cu), track.GetDrillValue()]
        via_dimensions.append(dimensions_via)
        require(
            dimensions_via
            == [
                round(intent["routing"]["via_diameter_mm"] * 1e6),
                round(intent["routing"]["via_drill_mm"] * 1e6),
            ],
            "actual_via_geometry",
            actual_nm=dimensions_via,
        )
    else:
        widths.add(track.GetWidth())
        track_counts[track.GetNetname()] += 1
        require(
            abs(track.GetWidth() - intent["routing"]["actual_trace_width_mm"] * 1e6)
            <= intent["routing"]["width_tolerance_mm"] * 1e6,
            "actual_segment_width",
            uuid=track.m_Uuid.AsString(),
            width_nm=track.GetWidth(),
        )
rules = project["board"]["design_settings"]
original_rules = initial["project"]["board"]["design_settings"]
require(rules == original_rules, "design_rules_severities_defaults_exclusions_unchanged")
require(
    rules["rules"]["min_resolved_spokes"] >= intent["zones"]["minimum_spokes"],
    "minimum_thermal_spokes",
)
zone_report = []
for zone in board.Zones():
    layer = zone.GetLayer()
    layer_name = board.GetLayerName(layer)
    polygons = zone.GetFilledPolysList(layer)
    zone_id = zone.m_Uuid.AsString()
    node = next(z for z in children(raw, "zone") if child(z, "uuid")[1] == zone_id)
    connection = child(node, "connect_pads")
    fill = child(node, "fill")
    parameters = {
        "clearance_mm": float(child(connection, "clearance")[1]),
        "min_thickness_mm": float(child(node, "min_thickness")[1]),
        "thermal_gap_mm": float(child(fill, "thermal_gap")[1]),
        "thermal_spoke_width_mm": float(child(fill, "thermal_bridge_width")[1]),
    }
    thermal = not any(
        isinstance(v, str) and v in ("yes", "solid", "no", "thru_hole_only") for v in connection[1:]
    )
    require(
        zone.GetNetname() == "GND" and layer_name in intent["zones"]["layers"],
        "zone_net_and_layer",
        actual_net=zone.GetNetname(),
        layer=layer_name,
    )
    require(thermal, "thermal_connection_mode", raw=connection)
    for key, actual in parameters.items():
        require(
            abs(actual - intent["zones"][key]) <= 0.000001,
            "zone_parameter",
            name=key,
            actual=actual,
            expected=intent["zones"][key],
        )
    require(
        zone.IsFilled() and polygons.OutlineCount() == 1,
        "one_saved_connected_fill_contour",
        layer=layer_name,
        filled=zone.IsFilled(),
        contours=polygons.OutlineCount(),
    )
    contacts = []
    for footprint in board.GetFootprints():
        for pad in footprint.Pads():
            if pad.GetNetname() == "GND" and pad.IsOnLayer(layer):
                touching = bool(polygons.Collide(pad.GetEffectiveShape(layer), 0))
                contacts.append(
                    {
                        "kind": "pad",
                        "ref": footprint.GetReference(),
                        "pin": pad.GetNumber(),
                        "touches_saved_fill_on_layer": touching,
                    }
                )
                require(
                    touching,
                    "same_layer_ground_pad_fill_contact",
                    layer=layer_name,
                    ref=footprint.GetReference(),
                    pin=pad.GetNumber(),
                )
    for track in board.GetTracks():
        if (
            isinstance(track, pcbnew.PCB_VIA)
            and track.GetNetname() == "GND"
            and track.IsOnLayer(layer)
        ):
            touching = bool(polygons.Collide(track.GetEffectiveShape(layer), 0))
            contacts.append(
                {
                    "kind": "via",
                    "uuid": track.m_Uuid.AsString(),
                    "touches_saved_fill_on_layer": touching,
                }
            )
            require(
                touching,
                "same_layer_ground_via_fill_contact",
                layer=layer_name,
                uuid=track.m_Uuid.AsString(),
            )
    zone_report.append(
        {
            "uuid": zone_id,
            "layer": layer_name,
            "parameters": parameters,
            "thermal": thermal,
            "native_saved_contour_count": polygons.OutlineCount(),
            "contacts": contacts,
        }
    )
require(
    Counter(z["layer"] for z in zone_report) == Counter(intent["zones"]["layers"]),
    "exact_two_ground_zone_layers",
)
report = {
    "passed": not failures,
    "failures": failures,
    "board_sha256": hashlib.sha256(board_path.read_bytes()).hexdigest(),
    "units": "nm unless explicitly mm",
    "outline_bounds_nm": bounds,
    "dimensions_mm": dimensions,
    "poses": poses,
    "silkscreen": silk,
    "actual_segment_widths_nm": sorted(widths),
    "track_counts": dict(track_counts),
    "vias": via_dimensions,
    "zones": zone_report,
    "design_settings_unchanged": rules == original_rules,
    "coverage": (
        "Axis aligned rectangular outline, zero-degree selected standard "
        "footprints, conservative reference-font and mask bounding boxes, "
        "all actual segment/via dimensions, native saved filled-polygon "
        "intersection on each physical copper layer. Native DRC is a "
        "separate result; no manufacturing artwork or load rating."
    ),
}
output.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(
    json.dumps(
        {
            "report": str(output),
            "passed": report["passed"],
            "failures": failures,
            "dimensions_mm": dimensions,
            "segment_widths_nm": sorted(widths),
            "segment_count": sum(track_counts.values()),
            "via_count": len(via_dimensions),
            "zone_count": len(zone_report),
            "fill_contacts": sum(len(z["contacts"]) for z in zone_report),
        },
        indent=2,
    )
)
raise SystemExit(0 if not failures else 1)
