"""What pcbnew says a board contains, as JSON: the ruler the board model is
measured against while pcbnew still exists.

Runs under KiCad's bundled interpreter, never under the test runner's:

    <kicad python> tests/pcbnew_oracle.py board.kicad_pcb > oracle.json

Test scaffolding for moving off SWIG, and deleted with it. Nothing here is
product code, and nothing in `kicad_cli` may import it.

Units are integer nanometres and degrees, KiCad's own frame (y down). Each
list is sorted so two dumps of one board compare equal item by item.
"""

from __future__ import annotations

import json
import sys

# Before pcbnew: a board naming a font this machine lacks makes pcbnew log
# "Font not found", and wx shows a log message as a modal box that blocks
# until someone clicks OK. Three of KiCad's demos do it on a stock Windows.
try:
    import wx

    wx.DisableAsserts()
    wx.Log.SetActiveTarget(wx.LogStderr())
except Exception:  # noqa: BLE001
    pass

import pcbnew  # noqa: E402

SHAPES = {
    pcbnew.PAD_SHAPE_CIRCLE: "circle",
    pcbnew.PAD_SHAPE_RECTANGLE: "rect",
    pcbnew.PAD_SHAPE_OVAL: "oval",
    pcbnew.PAD_SHAPE_TRAPEZOID: "trapezoid",
    pcbnew.PAD_SHAPE_ROUNDRECT: "roundrect",
    pcbnew.PAD_SHAPE_CHAMFERED_RECT: "chamfered_rect",
    pcbnew.PAD_SHAPE_CUSTOM: "custom",
}
ATTRIBUTES = {
    pcbnew.PAD_ATTRIB_PTH: "thru_hole",
    pcbnew.PAD_ATTRIB_SMD: "smd",
    pcbnew.PAD_ATTRIB_CONN: "connect",
    pcbnew.PAD_ATTRIB_NPTH: "np_thru_hole",
}


def point(p) -> list[int]:
    return [int(p.x), int(p.y)]


def name(layer) -> str:
    """The standard name -- F.Cu -- which is what the file refers to. The
    board's own name for a layer can be anything a user typed ("top_copper")."""
    return pcbnew.BOARD.GetStandardLayerName(layer)


def layer_names(board, layer_set) -> list[str]:
    # "*.Cu" is every copper layer KiCad knows, 32 of them; only the ones this
    # board has mean anything.
    return sorted(name(layer) for layer in layer_set.Seq() if board.IsLayerEnabled(layer))


def outline_stats(poly_set) -> dict:
    """Area and bounding box of a SHAPE_POLY_SET, holes subtracted.

    Small polygons -- rectangles, trapezoids, chamfered corners -- also carry
    their vertices: area and bounding box cannot tell which of two parallel
    sides a trapezoid widened, and the vertices can.
    """
    if poly_set.OutlineCount() == 0:
        return {"area": 0, "bbox": None, "outlines": 0}
    box = poly_set.BBox()
    stats = {
        "area": float(poly_set.Area()),
        "bbox": [int(box.GetLeft()), int(box.GetTop()), int(box.GetRight()), int(box.GetBottom())],
        "outlines": poly_set.OutlineCount(),
    }
    outline = poly_set.Outline(0)
    if poly_set.OutlineCount() == 1 and outline.PointCount() <= 16:
        stats["vertices"] = sorted(
            [int(outline.CPoint(i).x), int(outline.CPoint(i).y)]
            for i in range(outline.PointCount())
        )
    return stats


def pad_record(board, pad) -> dict:
    copper = [layer for layer in pad.GetLayerSet().CuStack()]
    shape_layer = copper[0] if copper else pcbnew.F_Cu
    polygon = pad.GetEffectivePolygon(shape_layer, pcbnew.ERROR_INSIDE)
    drill = pad.GetDrillSize()
    return {
        "number": pad.GetNumber(),
        "type": ATTRIBUTES.get(pad.GetAttribute(), str(pad.GetAttribute())),
        "shape": SHAPES.get(pad.GetShape(shape_layer), str(pad.GetShape(shape_layer))),
        "position": point(pad.GetPosition()),
        "orientation": round(pad.GetOrientationDegrees(), 6),
        "size": point(pad.GetSize(shape_layer)),
        "drill": point(drill) if drill.x or drill.y else None,
        "layers": layer_names(board, pad.GetLayerSet()),
        "net": pad.GetNetname(),
        "polygon": outline_stats(polygon),
    }


def footprint_record(board, fp) -> dict:
    pads = [pad_record(board, pad) for pad in fp.Pads()]
    pads.sort(key=lambda p: (p["number"], p["position"]))
    return {
        "reference": fp.GetReference(),
        "value": fp.GetValue(),
        "fpid": fp.GetFPIDAsString(),
        "layer": name(fp.GetLayer()),
        "position": point(fp.GetPosition()),
        "orientation": round(fp.GetOrientationDegrees(), 6),
        "pads": pads,
    }


def track_record(board, track) -> dict:
    if track.Type() == pcbnew.PCB_VIA_T:
        # A via's diameter may differ per layer in KiCad 10; asking without a
        # layer trips an assertion that hangs a headless process.
        return {
            "kind": "via",
            "net": track.GetNetname(),
            "position": point(track.GetPosition()),
            "width": int(track.GetFrontWidth()),
            "drill": int(track.GetDrillValue()),
            "layers": layer_names(board, track.GetLayerSet()),
        }
    record = {
        "kind": "arc" if track.Type() == pcbnew.PCB_ARC_T else "segment",
        "layer": name(track.GetLayer()),
        "net": track.GetNetname(),
        "width": int(track.GetWidth()),
        "start": point(track.GetStart()),
        "end": point(track.GetEnd()),
    }
    if record["kind"] == "arc":
        record["mid"] = point(track.GetMid())
    return record


def zone_record(board, zone) -> dict:
    filled = {}
    for layer in zone.GetLayerSet().Seq():
        if zone.HasFilledPolysForLayer(layer):
            filled[name(layer)] = outline_stats(zone.GetFilledPolysList(layer))
    return {
        "net": zone.GetNetname(),
        "name": zone.GetZoneName(),
        "layers": layer_names(board, zone.GetLayerSet()),
        "rule_area": bool(zone.GetIsRuleArea()),
        "priority": int(zone.GetAssignedPriority()),
        "outline": outline_stats(zone.Outline()),
        "filled": filled,
    }


# The pad shapes the demo boards barely use, from KiCad's own libraries: both
# trapezoid axes, a chamfer on one corner, a custom pad, an oval drill with
# an offset, beside the everyday ones.
SHAPES_BOARD = (
    ("Diode_SMD", "D_SMA-SMB_Universal_Handsoldering"),  # trapezoid, dx
    ("Package_LGA", "AMS_LGA-10-1EP_2.7x4mm_P0.6mm"),  # trapezoid
    ("RF_Antenna", "Texas_SWRA416_868MHz_915MHz"),  # trapezoid
    ("Converter_DCDC", "Converter_DCDC_Murata_MYRxP"),  # chamfered corner
    ("Battery", "BatteryHolder_Keystone_1057_1x2032"),  # custom pads
    ("Connector_USB", "USB_C_Receptacle_CNCTech_C-ARA1-AK51X"),  # oval drill, offset
    ("Package_QFP", "TQFP-32_7x7mm_P0.8mm"),  # roundrect
    ("Connector_PinHeader_2.54mm", "PinHeader_1x02_P2.54mm_Vertical"),  # rect and oval THT
)
SHAPES_ANGLES = (0, 30, 90, 180, -45)


def build_shapes_board(footprint_dir: str, out: str) -> None:
    """Every footprint above at every angle above, on the front and the back."""
    import os

    board = pcbnew.CreateEmptyBoard()
    for row, (library, name) in enumerate(SHAPES_BOARD):
        for column, angle in enumerate(SHAPES_ANGLES):
            for back in (False, True):
                fp = pcbnew.FootprintLoad(os.path.join(footprint_dir, library + ".pretty"), name)
                fp.SetReference(f"R{row}C{column}{'B' if back else 'F'}")
                where = pcbnew.VECTOR2I(
                    pcbnew.FromMM(40 + 70 * column), pcbnew.FromMM(40 + 70 * (2 * row + back))
                )
                # Onto the board first: flipping a footprint that belongs to
                # no board dereferences the missing board and segfaults.
                board.Add(fp)
                # The board owns it now; left to Python it would be freed twice.
                fp.thisown = False
                fp.SetPosition(where)
                if back:
                    fp.Flip(where, pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
                fp.SetOrientationDegrees(angle)
    board.Save(out)


def connectivity_record(board) -> dict:
    """Which pads copper joins, per net, and how many connections are open.

    A cluster is every item copper joins to a pad, found once per cluster:
    a pad already placed in one is not asked again.
    """
    board.BuildConnectivity()
    conn = board.GetConnectivity()
    placed = set()
    clusters: dict[str, list[list[str]]] = {}
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetCode() <= 0:
                continue
            where = pad.GetPosition()
            key = (fp.GetReference(), pad.GetNumber(), int(where.x), int(where.y))
            if key in placed:
                continue
            members = [key]
            for item in conn.GetConnectedItems(pad):
                if item.Type() == pcbnew.PCB_PAD_T:
                    parent = item.GetParentFootprint()
                    ref = parent.GetReference() if parent else ""
                    p = item.GetPosition()
                    members.append((ref, item.GetNumber(), int(p.x), int(p.y)))
            for member in members:
                placed.add(member)
            clusters.setdefault(pad.GetNetname(), []).append(
                sorted({f"{ref}.{number}@{x},{y}" for ref, number, x, y in members})
            )
    for net in clusters:
        clusters[net].sort()
    return {"unconnected": int(conn.GetUnconnectedCount(False)), "clusters": clusters}


def contains_record(cases: list) -> list[int]:
    """Whether each point is inside its polygon, by SHAPE_POLY_SET.Contains:
    ``cases`` is a list of [polygon, [x, y]], a polygon a list of [x, y]."""
    out = []
    for polygon, (x, y) in cases:
        chain = pcbnew.SHAPE_LINE_CHAIN()
        for px, py in polygon:
            chain.Append(int(px), int(py))
        chain.SetClosed(True)
        shape = pcbnew.SHAPE_POLY_SET()
        shape.AddOutline(chain)
        out.append(int(shape.Contains(pcbnew.VECTOR2I(int(x), int(y)))))
    return out


def main(path: str) -> None:
    board = pcbnew.LoadBoard(path)
    footprints = [footprint_record(board, fp) for fp in board.GetFootprints()]
    footprints.sort(key=lambda f: (f["reference"], f["position"]))
    tracks = [track_record(board, t) for t in board.GetTracks()]
    tracks.sort(key=lambda t: json.dumps(t, sort_keys=True))
    zones = [zone_record(board, z) for z in board.Zones()]
    zones.sort(key=lambda z: json.dumps(z, sort_keys=True))
    edge = board.GetBoardEdgesBoundingBox()
    json.dump(
        {
            "board": path,
            "kicad": pcbnew.GetBuildVersion(),
            "copper_layers": [name(layer) for layer in board.GetEnabledLayers().CuStack()],
            "edge_bbox": [
                int(edge.GetLeft()),
                int(edge.GetTop()),
                int(edge.GetRight()),
                int(edge.GetBottom()),
            ],
            "footprints": footprints,
            "tracks": tracks,
            "zones": zones,
        },
        sys.stdout,
    )


if __name__ == "__main__":
    if sys.argv[1] == "--build-shapes-board":
        build_shapes_board(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == "--connectivity":
        json.dump(connectivity_record(pcbnew.LoadBoard(sys.argv[2])), sys.stdout)
    elif sys.argv[1] == "--contains":
        json.dump(contains_record(json.load(sys.stdin)), sys.stdout)
    else:
        main(sys.argv[1])
