"""Independent saved polygon/pad/track contacts, using installed native SDK only."""

import contextlib
import hashlib
import io
import json
import runpy
from pathlib import Path

buffer = io.StringIO()
with contextlib.redirect_stdout(buffer):
    namespace = runpy.run_path(
        str(Path(__file__).with_name("native_zone_reader.py")), run_name="__main__"
    )
record = json.loads(buffer.getvalue())
pcbnew, board = namespace["pcbnew"], namespace["board"]
board.BuildConnectivity()
contacts = []
for zone in board.Zones():
    for layer in zone.GetLayerSet().Seq():
        fill = zone.GetFilledPolysList(layer)
        for index in range(fill.OutlineCount()):
            poly = pcbnew.SHAPE_POLY_SET()
            poly.AddOutline(fill.Outline(index))
            for j in range(fill.HoleCount(index)):
                poly.AddHole(fill.Hole(index, j))
            pads = []
            for fp in board.GetFootprints():
                for pad in fp.Pads():
                    if pad.GetNetCode() != zone.GetNetCode() or not pad.IsOnLayer(layer):
                        continue
                    if poly.Collide(pad.GetEffectiveShape(layer), 0):
                        pads.append(
                            {
                                "ref": fp.GetReference(),
                                "pad": pad.GetNumber(),
                                "uuid": pad.m_Uuid.AsString(),
                            }
                        )
            tracks = []
            for track in board.GetTracks():
                if track.GetNetCode() != zone.GetNetCode():
                    continue
                on_layer = bool(track.IsOnLayer(layer))
                tracks.append(
                    {
                        "uuid": track.m_Uuid.AsString(),
                        "layer": board.GetLayerName(track.GetLayer()),
                        "on_zone_layer": on_layer,
                        "start_xy_in_fill": bool(poly.Collide(track.GetStart())),
                        "end_xy_in_fill": bool(poly.Collide(track.GetEnd())),
                        "direct_copper_contact": bool(
                            on_layer and poly.Collide(track.GetEffectiveShape(layer), 0)
                        ),
                    }
                )
            contacts.append(
                {
                    "zone_uuid": zone.m_Uuid.AsString(),
                    "layer": board.GetLayerName(layer),
                    "polygon_index": index,
                    "pad_contacts": pads,
                    "tracks": tracks,
                }
            )
record["thermal_polygon_contacts"] = contacts
record["thermal_reader_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
record["contact_scope"] = (
    "Per saved polygon, same-net native shape intersection, zero added clearance; "
    "not whole-net or native spoke inference"
)
print(json.dumps(record))
