"""Fresh native load: saved fill contours and ground contacts, without mutation."""

import faulthandler
import hashlib
import json
import sys
from pathlib import Path

faulthandler.enable()
faulthandler.dump_traceback_later(20, repeat=False)
import pcbnew  # noqa: E402

board_path, output = map(Path, sys.argv[1:3])
if output.exists():
    raise SystemExit("Observation output must be new")
before = hashlib.sha256(board_path.read_bytes()).hexdigest()
board = pcbnew.LoadBoard(str(board_path))
rows = []
for zone in board.Zones():
    layer = zone.GetLayer()
    polygons = zone.GetFilledPolysList(layer)
    contacts, isolated = [], []
    for footprint in board.GetFootprints():
        for pad in footprint.Pads():
            if not pad.IsOnLayer(layer):
                continue
            value = {
                "ref": footprint.GetReference(),
                "pad": pad.GetNumber(),
                "touches_saved_fill": bool(polygons.Collide(pad.GetEffectiveShape(layer), 0)),
            }
            if pad.GetNetname() == "GND":
                contacts.append(value)
            elif footprint.GetReference() == "TP1":
                isolated.append(value)
    for track in board.GetTracks():
        if (
            isinstance(track, pcbnew.PCB_VIA)
            and track.GetNetname() == "GND"
            and track.IsOnLayer(layer)
        ):
            contacts.append(
                {
                    "via": track.m_Uuid.AsString(),
                    "touches_saved_fill": bool(polygons.Collide(track.GetEffectiveShape(layer), 0)),
                }
            )
    rows.append(
        {
            "uuid": zone.m_Uuid.AsString(),
            "layer": board.GetLayerName(layer),
            "net": zone.GetNetname(),
            "filled": zone.IsFilled(),
            "saved_contours": polygons.OutlineCount(),
            "ground_contacts": contacts,
            "isolated_testpoint": isolated,
        }
    )
if hashlib.sha256(board_path.read_bytes()).hexdigest() != before:
    raise RuntimeError("Read-only native fill observation changed board bytes")
result = {"board_sha256": before, "native_version": pcbnew.GetBuildVersion(), "zones": rows}
with output.open("x", encoding="utf-8") as stream:
    json.dump(result, stream, indent=2)
faulthandler.cancel_dump_traceback_later()
print(json.dumps(result))
