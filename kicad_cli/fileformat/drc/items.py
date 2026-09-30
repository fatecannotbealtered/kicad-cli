"""A board's objects as a DRC report names them.

Every item carries its uuid -- which is how a violation is matched against
KiCad's report and against the project's exclusions -- and a description in
KiCad's own words: "Pad 2 [GND] of R1 on F.Cu", "PTH pad 1 [+5V] of J1",
"Track [SDA] on F.Cu, length 4.2000 mm", "Zone [GND] on F.Cu and B.Cu,
priority 0". Layers are named as the board names them ("Top Layer" where
someone renamed F.Cu).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..board import Board, Footprint, Pad, Track, Via, Zone
from ..sexpr import List

NM = 1_000_000
NO_NET = "<no net>"
SHAPES = {
    "line": "Segment", "arc": "Arc", "circle": "Circle", "rect": "Rectangle",
    "poly": "Polygon", "curve": "Bezier Curve",
}  # fmt: skip


@dataclass(frozen=True)
class Item:
    kind: str  # pad, track, via, zone, footprint, field, text, shape
    uuid: str
    description: str
    position: tuple[int, int]

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "uuid": self.uuid,
            "description": self.description,
            "at_mm": [self.position[0] / NM, self.position[1] / NM],
        }


def uuid_of(node: List | None) -> str:
    found = node.find("uuid") if node is not None else None
    if found is None and node is not None:
        found = node.find("tstamp")  # before KiCad 7
    return (found.value(1) or "") if found is not None else ""


def layer_order(name: str) -> int:
    """Where KiCad sorts a layer: F.Cu, B.Cu, then the inner layers."""
    if name == "F.Cu":
        return 0
    if name == "B.Cu":
        return 1
    if name.startswith("In") and name.endswith(".Cu") and name[2:-3].isdigit():
        return 1 + int(name[2:-3])
    return 100


def _net(net: str) -> str:
    return net or NO_NET


def reference(fp: Footprint | None) -> str:
    return (fp.reference if fp is not None else "") or "<no reference designator>"


def pad(board: Board, fp: Footprint, pad: Pad) -> Item:
    number = f"{pad.number} " if pad.number else ""
    if pad.kind == "np_thru_hole":
        text = f"NPTH pad of {reference(fp)}"
    elif pad.kind == "thru_hole":
        text = f"PTH pad {number}[{_net(pad.net)}] of {reference(fp)}"
    else:
        copper = sorted((la for la in pad.layers if la.endswith(".Cu")), key=layer_order)
        layers = copper or sorted(pad.layers, key=layer_order)
        layer = board.layer_name(layers[0]) if layers else ""
        more = " and others" if len(copper) > 1 else ""
        text = f"Pad {number}[{_net(pad.net)}] of {reference(fp)} on {layer}{more}"
    return Item("pad", uuid_of(pad.node), text, pad.position)


def track(board: Board, track: Track) -> Item:
    kind = "Track (arc)" if track.kind == "arc" else "Track"
    text = (
        f"{kind} [{_net(track.net)}] on {board.layer_name(track.layer)}, "
        f"length {track.length() / NM:.4f} mm"
    )
    return Item("track", uuid_of(track.node), text, track.start)


def via(board: Board, via: Via) -> Item:
    kind = {"blind": "Blind via", "buried": "Buried via", "micro": "Micro via"}.get(via.kind, "Via")
    top, bottom = (via.layers[0], via.layers[-1]) if via.layers else ("", "")
    text = f"{kind} [{_net(via.net)}] on {board.layer_name(top)} - {board.layer_name(bottom)}"
    return Item("via", uuid_of(via.node), text, via.position)


def layer_list(board: Board, layers: list[str]) -> str:
    """ "F.Cu", "F.Cu and B.Cu", "F.Cu, B.Cu and GND", "F.Cu, B.Cu and 3 more"."""
    names = [board.layer_name(n) for n in sorted(dict.fromkeys(layers), key=layer_order)]
    if len(names) <= 1:
        return names[0] if names else ""
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    if len(names) == 3:
        return f"{names[0]}, {names[1]} and {names[2]}"
    return f"{names[0]}, {names[1]} and {len(names) - 2} more"


def zone(board: Board, zone: Zone) -> Item:
    name = f"'{zone.name}' " if zone.name else ""
    layers = layer_list(board, zone.layers)
    if zone.rule_area:
        text = f"Rule area {name}on {layers}"
    else:
        text = f"Zone {name}[{_net(zone.net)}] on {layers}, priority {zone.priority}"
    first = zone.outlines[0][0] if zone.outlines and zone.outlines[0] else (0, 0)
    return Item("zone", uuid_of(zone.node), text, first)


def footprint(fp: Footprint) -> Item:
    return Item("footprint", uuid_of(fp.node), f"Footprint {reference(fp)}", fp.position)


def field(fp: Footprint, name: str, value: str, node: List, position: tuple[int, int]) -> Item:
    """A footprint's field: "Reference field of R1", "Value field of R1 (10k)"."""
    if name == "Reference":
        text = f"Reference field of {reference(fp)}"
    elif name == "Value":
        text = f"Value field of {reference(fp)} ({value})"
    else:
        text = f"{name} field of {reference(fp)}"
    return Item("field", uuid_of(node), text, position)


def text(board: Board, value: str, layer: str, node: List, position, fp=None) -> Item:
    if fp is not None:
        description = f"Footprint text of {reference(fp)}"
    else:
        description = f"PCB text '{value}' on {board.layer_name(layer)}"
    return Item("text", uuid_of(node), description, position)


def shape(board: Board, node: List, position, fp: Footprint | None = None) -> Item:
    kind = SHAPES.get(node.head.split("_", 1)[1], "Shape")
    found = node.find("layer")
    layer = found.value(1) if found is not None else ""
    net = f" [{_net(board.net_of(node))}]" if layer.endswith(".Cu") else ""
    owner = f" of {reference(fp)}" if fp is not None else ""
    text = f"{kind}{net}{owner} on {board.layer_name(layer)}"
    return Item("shape", uuid_of(node), text, position)
