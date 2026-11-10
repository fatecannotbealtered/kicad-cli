"""What copper touches what on a saved board, for the result of a board edit (DCLI-068).

A track end or a pad is reported with the pads, tracks, vias and zone fills its copper meets on
the same layer, as KiCad's file holds them. Zone fills are as last filled: a fill is not redone
here, so a change that a refill would connect may show no zone yet.
"""
from __future__ import annotations

import math
from typing import Any

from .fileformat.board import Board
from .fileformat.connectivity import Item, _boxes_meet, _path_segments, _segment_distance, items_of
from .pcb_edit_board import identity


def copper(board: Board) -> list[Item]:
    return items_of(board)


def label(item: Item) -> str:
    if item.kind == "pad":
        fp, pad = item.owner
        return f"pad {fp.reference}.{pad.number}"
    if item.kind in ("segment", "arc"):
        return f"track {identity(item.owner.node) if item.owner.node is not None else '?'}"
    if item.kind == "via":
        return f"via {identity(item.owner.node) if item.owner.node is not None else '?'}"
    if item.kind == "fill":
        return f"zone {item.net}"
    return f"{item.kind} {item.net}"


def at_point(items: list[Item], layer: str, point: tuple[int, int], reach: float, skip: set[str] = frozenset()) -> list[str]:
    """What a round end of radius ``reach`` at ``point`` on ``layer`` touches; ``skip`` holds the
    labels of the track the end belongs to."""
    x, y = point
    found = []
    for item in items:
        if layer not in item.layers or item.kind == "shape":
            continue
        if not _boxes_meet(item.bbox, (x - reach, y - reach, x + reach, y + reach)):
            continue
        if item.kind in ("pad", "fill"):
            hit = item.polygon is not None and item.polygon.path_within([point], reach)
        elif item.kind == "via":
            vx, vy = item.path[0]
            hit = math.hypot(x - vx, y - vy) <= item.radius + reach
        else:
            hit = any(_segment_distance(point, point, a, b) <= item.radius + reach for a, b in _path_segments(item.path))
        if hit:
            text = label(item)
            if text not in skip and text not in found:
                found.append(text)
    return found


def pad_links(items: list[Item], reference: str) -> dict[str, list[str]]:
    """For each pad of a footprint, the tracks and vias whose copper meets it."""
    pads = [i for i in items if i.kind == "pad" and i.owner[0].reference == reference]
    wires = [i for i in items if i.kind in ("segment", "arc", "via")]
    out: dict[str, list[str]] = {}
    for pad in pads:
        hits = []
        for wire in wires:
            if not set(pad.layers) & set(wire.layers) or not _boxes_meet(pad.bbox, wire.bbox):
                continue
            if pad.polygon is not None and pad.polygon.path_within(wire.path, wire.radius):
                hits.append(label(wire))
        out[str(pad.owner[1].number)] = sorted(set(hits))
    return out


def track_ends(board: Board, uid: str, path: list[str] = ()) -> dict[str, Any] | None:
    """A track as the board now holds it, with what each end touches; ``path`` names the other
    segments of the same path, which an end does not count."""
    track = next((t for t in board.tracks if t.node is not None and identity(t.node) == uid), None)
    if track is None:
        return None
    items = copper(board)
    me = {f"track {i}" for i in (uid, *path)}
    ends = {}
    for name, point in (("start", track.start), ("end", track.end)):
        ends[name] = {"x_mm": round(point[0] / 1e6, 6), "y_mm": round(point[1] / 1e6, 6),
                      "connects_to": at_point(items, track.layer, point, track.width / 2, skip=me)}
    return {"id": uid, "net": track.net, "layer": track.layer, "width_mm": round(track.width / 1e6, 6), **ends,
            "unconnected_ends": [n for n in ("start", "end") if not ends[n]["connects_to"]]}


def via_links(board: Board, uid: str) -> dict[str, Any] | None:
    """A via as the board now holds it, with what its copper meets on each layer it passes."""
    via = next((v for v in board.vias if v.node is not None and identity(v.node) == uid), None)
    if via is None:
        return None
    items = copper(board)
    me = {f"via {uid}"}
    return {"id": uid, "net": via.net, "x_mm": round(via.position[0] / 1e6, 6), "y_mm": round(via.position[1] / 1e6, 6),
            "diameter_mm": round(via.diameter / 1e6, 6), "drill_mm": round(via.drill / 1e6, 6), "layers": via.layers,
            "connects_to": {layer: at_point(items, layer, via.position, via.diameter / 2, skip=me) for layer in via.layers}}
