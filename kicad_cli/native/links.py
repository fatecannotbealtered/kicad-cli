"""A board read back after `sch relink` wrote to it: each footprint's link, and
a count of what is on the board.

A port of `payload/verify_links.py`, which had pcbnew load the board and
report the same; on every board KiCad ships the two agree
(`tests/test_native_links.py`). `sch relink` reads the board this way before
and after its edit: the file must still read as a board, every link must be
the one it meant to write, and nothing but links may have changed in number.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..fileformat.board import Board
from ..fileformat.sexpr import List

TRACKS = {"segment", "arc", "via"}
# What pcbnew keeps among a board's drawings: shapes, text, dimensions and
# the like -- not footprints, tracks, zones, groups or tuning patterns.
DRAWINGS = {
    "gr_line", "gr_arc", "gr_circle", "gr_rect", "gr_poly", "gr_curve", "gr_bbox",
    "gr_text", "gr_text_box", "dimension", "target", "table", "image", "barcode",
}  # fmt: skip


def path_string(path: str) -> str:
    """A footprint's link as pcbnew spells it: "/a/b", nothing for none."""
    parts = [p for p in path.split("/") if p]
    return "".join("/" + p for p in parts)


def read(path: str | Path) -> dict[str, Any]:
    """Every footprint's link by reference, and the board's census."""
    board = Board.load(path)
    root = board.root
    links: dict[str, str] = {}
    duplicates: list[str] = []
    census = {"footprints": 0, "tracks": 0, "zones": 0, "drawings": 0}
    for item in root.items[1:]:
        if not isinstance(item, List):
            continue
        head = item.head
        if head == "footprint":
            census["footprints"] += 1
            ref = _reference(item)
            node = item.find("path")
            value = path_string(node.value(1) or "") if node is not None else ""
            if ref in links and links[ref] != value:
                duplicates.append(ref)
            links[ref] = value
        elif head in TRACKS:
            census["tracks"] += 1
        elif head == "zone":
            census["zones"] += 1
        elif head in DRAWINGS:
            census["drawings"] += 1
    census["nets"] = _net_count(board)
    return {
        "board": str(path),
        "links": links,
        "duplicate_references": sorted(set(duplicates)),
        "census": census,
    }


def _reference(footprint: List) -> str:
    for prop in footprint.find_all("property"):
        if prop.value(1) == "Reference":
            return prop.value(2) or ""
    text = next((t for t in footprint.find_all("fp_text") if t.value(1) == "reference"), None)
    return (text.value(2) or "") if text is not None else ""


def _net_count(board: Board) -> int:
    """As pcbnew counts a board's nets: the unconnected net and every named
    one -- from the net table where the board has one, as KiCad 9 wrote them,
    and from its items where it has none, as KiCad 10 writes them."""
    names = set(board._net_names.values())
    names.discard("")
    if not names:
        names = {pad.net for fp in board.footprints for pad in fp.pads}
        names |= {t.net for t in board.tracks} | {v.net for v in board.vias}
        names |= {z.net for z in board.zones}
        names.discard("")
    return len(names) + 1
