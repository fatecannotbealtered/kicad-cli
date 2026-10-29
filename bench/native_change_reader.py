"""Independent saved change observations: native project classes and pin components.

Only stdlib and the installed SDK are imported. No product parser or writer.
"""

import contextlib
import hashlib
import io
import json
import runpy
import sys
from pathlib import Path

buffer = io.StringIO()
with contextlib.redirect_stdout(buffer):
    runpy.run_path(
        str(Path(__file__).with_name("native_engineering_reader.py")), run_name="__main__"
    )
record = json.loads(buffer.getvalue())
import pcbnew  # noqa: E402

board_path = Path(sys.argv[1])
project_path = board_path.with_suffix(".kicad_pro")
project_before = hashlib.sha256(project_path.read_bytes()).hexdigest()
manager = pcbnew.SETTINGS_MANAGER()
assert manager.LoadProject(str(project_path), False)
native_project = manager.GetProject(str(project_path))
board = pcbnew.LoadBoard(str(board_path))
board.SetProject(native_project)
board.BuildConnectivity()
conn = board.GetConnectivity()
record["locked_refs"] = sorted(fp.GetReference() for fp in board.GetFootprints() if fp.IsLocked())
record["pad_components"] = []
for fp in board.GetFootprints():
    for pad in fp.Pads():
        record["pad_components"].append(
            {
                "ref": fp.GetReference(),
                "number": pad.GetNumber(),
                "net": pad.GetNetname(),
                "uuid": pad.m_Uuid.AsString(),
                "connected_pads": sorted(
                    [
                        p.GetParentFootprint().GetReference() + "." + p.GetNumber()
                        for p in conn.GetConnectedItems(pad)
                        if p.GetClass() == "PAD"
                    ]
                ),
            }
        )
record["effective_netclasses"] = []
for name in board.GetNetsByName():
    name = str(name)
    if not name:
        continue
    cls = board.GetDesignSettings().m_NetSettings.GetEffectiveNetClass(name)
    record["effective_netclasses"].append(
        {
            "net": name,
            "name": str(cls.GetName()),
            "width_nm": int(cls.GetTrackWidth()),
            "clearance_nm": int(cls.GetClearance()),
            "via_diameter_nm": int(cls.GetViaDiameter()),
            "via_drill_nm": int(cls.GetViaDrill()),
        }
    )
record["project_sha256"] = project_before
assert manager.UnloadProject(native_project, False)
record["project_unchanged"] = (
    project_before == hashlib.sha256(project_path.read_bytes()).hexdigest()
)
record["change_reader_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
assert record["project_unchanged"] and record["input_unchanged"]
print(json.dumps(record))
