"""Fresh native saved identity/text reader; no product imports."""

import contextlib
import hashlib
import io
import json
import runpy
import sys
from pathlib import Path

buffer = io.StringIO()
with contextlib.redirect_stdout(buffer):
    runpy.run_path(str(Path(__file__).with_name("native_zone_reader.py")), run_name="__main__")
record = json.loads(buffer.getvalue())
import pcbnew  # noqa: E402 - imported after the standalone base reader

path = Path(sys.argv[1])
before = hashlib.sha256(path.read_bytes()).hexdigest()
board = pcbnew.LoadBoard(str(path))
board.BuildConnectivity()
connectivity = board.GetConnectivity()


def component(item):
    members = list(connectivity.GetConnectedItems(item))
    return {
        "component_pads": [
            {
                "ref": p.GetParentFootprint().GetReference(),
                "number": p.GetNumber(),
                "uuid": p.m_Uuid.AsString(),
                "net": p.GetNetname(),
            }
            for p in members
            if p.GetClass() == "PAD"
        ],
        "component_zones": [
            {
                "uuid": z.m_Uuid.AsString(),
                "net": z.GetNetname(),
                "layers": [board.GetLayerName(layer) for layer in z.GetLayerSet().Seq()],
                "filled": z.IsFilled(),
            }
            for z in members
            if z.GetClass() == "ZONE"
        ],
    }


def direct_zone_contacts(via):
    """Saved fill intersection on each layer, independent of global component.

    Uses native effective via copper shapes and saved filled poly sets at zero
    added clearance. This is a contact test for the declared through-via case,
    not an impedance, plating, stackup or manufacturing-quality verdict.
    """
    result = []
    for zone in board.Zones():
        if zone.GetIsRuleArea() or zone.GetNetCode() != via.GetNetCode():
            continue
        for layer in zone.GetLayerSet().Seq():
            on_layer = via.IsOnLayer(layer)
            hit = (
                on_layer
                and zone.IsFilled()
                and zone.GetFilledPolysList(layer).Collide(via.GetEffectiveShape(layer), 0)
            )
            result.append(
                {
                    "zone_uuid": zone.m_Uuid.AsString(),
                    "net": zone.GetNetname(),
                    "layer": board.GetLayerName(layer),
                    "via_on_layer": on_layer,
                    "filled": zone.IsFilled(),
                    "direct_copper_intersection": bool(hit),
                    "added_clearance_nm": 0,
                }
            )
    return result


record["native_connectivity"] = {
    "method": "Fresh BuildConnectivity / GetConnectedItems on saved fill; pcbnew 10.0.6",
    "vias": [
        dict(
            uuid=v.m_Uuid.AsString(),
            net=v.GetNetname(),
            direct_zone_contacts=direct_zone_contacts(v),
            **component(v),
        )
        for v in board.GetTracks()
        if v.GetClass() == "PCB_VIA"
    ],
    "zones": [
        dict(uuid=z.m_Uuid.AsString(), net=z.GetNetname(), **component(z)) for z in board.Zones()
    ],
}
metadata = []
for fp in board.GetFootprints():
    text = fp.Reference()
    box = text.GetBoundingBox()
    metadata.append(
        {
            "ref": fp.GetReference(),
            "uuid": fp.m_Uuid.AsString(),
            "schematic_path": fp.GetPath().AsString(),
            "reference_text": {
                "text": text.GetText(),
                "position_nm": [text.GetPosition().x, text.GetPosition().y],
                "angle_deg": text.GetTextAngle().AsDegrees(),
                "layer": board.GetLayerName(text.GetLayer()),
                "visible": text.IsVisible(),
                "size_nm": [text.GetTextSize().x, text.GetTextSize().y],
                "stroke_nm": text.GetTextThickness(),
                "box_nm": [box.GetLeft(), box.GetTop(), box.GetRight(), box.GetBottom()],
            },
        }
    )
record["identity_text"] = sorted(metadata, key=lambda row: row["ref"])
record["engineering_reader_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
record["input_unchanged"] = (
    before == hashlib.sha256(path.read_bytes()).hexdigest() == record["input_sha256"]
)
assert record["input_unchanged"]
print(json.dumps(record))
