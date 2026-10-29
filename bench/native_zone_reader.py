"""Fresh installed SDK zone geometry observation; no product/expected imports."""

import contextlib
import hashlib
import io
import json
import pathlib
import runpy
import sys
from collections import Counter

path = pathlib.Path(sys.argv[1]).resolve()
collector = pathlib.Path(__file__).with_name("native_sdk_reader.py")
buffer = io.StringIO()
print("zone-reader: before base native load", file=sys.stderr, flush=True)
with contextlib.redirect_stdout(buffer):
    base_namespace = runpy.run_path(str(collector), run_name="__main__")
print("zone-reader: after base native load", file=sys.stderr, flush=True)
record = json.loads(buffer.getvalue())
assert record["completed"] and record["input_unchanged"]
pcbnew = base_namespace["pcbnew"]


def sha(file):
    return hashlib.sha256(pathlib.Path(file).read_bytes()).hexdigest()


def chain(line):
    return {
        "vertices_nm": [
            [int(line.CPoint(i).x), int(line.CPoint(i).y)] for i in range(line.PointCount())
        ],
        "closed": bool(line.IsClosed()),
        "arcs": int(line.ArcCount()),
    }


def polygons(poly):
    return [
        {
            "outline": chain(poly.Outline(i)),
            "holes": [chain(poly.Hole(i, j)) for j in range(poly.HoleCount(i))],
        }
        for i in range(poly.OutlineCount())
    ]


before = sha(path)
print("zone-reader: before second native load", file=sys.stderr, flush=True)
board = pcbnew.LoadBoard(str(path))
print("zone-reader: after second native load", file=sys.stderr, flush=True)
assert board is not None
zones = []
for zone in board.Zones():
    layers = list(zone.GetLayerSet().Seq())
    zones.append(
        {
            "uuid": zone.m_Uuid.AsString(),
            "net": zone.GetNetname(),
            "rule_area": bool(zone.GetIsRuleArea()),
            "filled": bool(zone.IsFilled()),
            "layers": [pcbnew.BOARD.GetStandardLayerName(layer) for layer in layers],
            "outlines": polygons(zone.Outline()),
            "filled_polygons": {
                pcbnew.BOARD.GetStandardLayerName(layer): polygons(zone.GetFilledPolysList(layer))
                for layer in layers
            },
            "min_thickness_nm": int(zone.GetMinThickness()),
            "clearance_nm": int(zone.GetOwnClearance(layers[0])),
            "thermal_gap_nm": int(zone.GetThermalReliefGap()),
            "thermal_spoke_nm": int(zone.GetThermalReliefSpokeWidth()),
            "pad_connection_code": int(zone.GetPadConnection()),
            "pad_connection_name": {
                pcbnew.ZONE_CONNECTION_THERMAL: "thermal",
                pcbnew.ZONE_CONNECTION_FULL: "solid",
                pcbnew.ZONE_CONNECTION_NONE: "none",
                pcbnew.ZONE_CONNECTION_INHERITED: "inherited",
                pcbnew.ZONE_CONNECTION_THT_THERMAL: "tht_thermal",
            }.get(zone.GetPadConnection(), "unknown"),
        }
    )
print("zone-reader: zones observed", file=sys.stderr, flush=True)
tracks = []
for track in board.GetTracks():
    item = {
        "uuid": track.m_Uuid.AsString(),
        "kind": track.GetClass(),
        "net": track.GetNetname(),
        "layer": pcbnew.BOARD.GetStandardLayerName(track.GetLayer()),
        "width_nm": int(track.GetWidth(track.GetLayer()))
        if track.GetClass() == "PCB_VIA"
        else int(track.GetWidth()),
        "start_nm": [int(track.GetStart().x), int(track.GetStart().y)],
        "end_nm": [int(track.GetEnd().x), int(track.GetEnd().y)],
    }
    if track.GetClass() == "PCB_VIA":
        item["drill_nm"] = int(track.GetDrillValue())
        item["width_by_layer_nm"] = {
            pcbnew.BOARD.GetStandardLayerName(layer): int(track.GetWidth(layer))
            for layer in track.GetLayerSet().Seq()
        }
    tracks.append(item)
print("zone-reader: tracks observed", file=sys.stderr, flush=True)
box = board.GetBoardEdgesBoundingBox()
edges, unsupported = [], []
for owner, item in [("board", g) for g in board.GetDrawings()] + [
    ("footprint", g) for fp in board.GetFootprints() for g in fp.GraphicalItems()
]:
    if item.GetLayer() != pcbnew.Edge_Cuts:
        continue
    if (
        owner != "board"
        or item.GetClass() != "PCB_SHAPE"
        or item.GetShape() not in (pcbnew.S_SEGMENT, pcbnew.S_RECT)
    ):
        unsupported.append(
            {"owner": owner, "class": item.GetClass(), "uuid": item.m_Uuid.AsString()}
        )
        continue
    start, end = item.GetStart(), item.GetEnd()
    points = [(int(start.x), int(start.y)), (int(end.x), int(end.y))]
    if item.GetShape() == pcbnew.S_RECT:
        points = [(int(p.x), int(p.y)) for p in item.GetRectCorners()]
        if len(points) != 4:
            unsupported.append({"class": "rectangle", "reason": "unexpected native corner count"})
            continue
        points.append(points[0])
    for a, b in zip(points, points[1:], strict=False):
        a, b = sorted((a, b))
        edges.append({"start_nm": list(a), "end_nm": list(b), "width_nm": int(item.GetWidth())})
degree = Counter(tuple(p) for e in edges for p in (e["start_nm"], e["end_nm"]))
closed = (
    bool(edges)
    and all(n == 2 for n in degree.values())
    and all(e["start_nm"] != e["end_nm"] for e in edges)
)
record.update(
    zone_geometry=sorted(zones, key=lambda item: item["uuid"]),
    track_geometry=sorted(tracks, key=lambda item: item["uuid"]),
    edge_bbox_nm=[int(box.GetX()), int(box.GetY()), int(box.GetRight()), int(box.GetBottom())],
    edge_geometry={
        "status": "supported" if closed and not unsupported else "unsupported",
        "segments": sorted(edges, key=lambda e: (e["start_nm"], e["end_nm"], e["width_nm"])),
        "unsupported_items": unsupported,
        "closed_degree_two": closed,
        "scope": (
            "Native board-owned segment/rectangle centerlines and widths, nm; "
            "order/direction canonicalized. Other shapes/open contours refused. "
            "This is preservation checking, not full outline DRC."
        ),
    },
    zone_reader_sha256=sha(__file__),
    base_reader_sha256=sha(collector),
    second_input_sha256=before,
    input_unchanged=before == sha(path) == record["input_sha256"],
    coverage={
        "native_zone_identity_definition_fill_geometry": "observed; compare to the frozen task",
        "native_other_footprints_pads_tracks_origin": "observed",
        "DRC": "not_checked",
        "fill_geometric_correctness": "not_certified",
        "electrical_manufacturing_visual": "not_checked",
    },
)
assert record["input_unchanged"]
print(json.dumps(record))
