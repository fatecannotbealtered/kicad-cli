"""A new board from a netlist: footprints from the libraries, placed on a grid, wired into nets.

KiCad's "Update PCB from Schematic" has no command-line entry point. What it does for a
new board is load each part's footprint, place it, join its pads into nets and link it to
its symbol; a footprint is a file kicad-cli reads like any other. Ported from the previous
implementation (``native/from_netlist.py`` and ``boardgen.py``):

- Parts go on a grid ordered by reference: somewhere definite, not a layout.
- The outline is a rectangle around the parts' copper, drawings and courtyards, a margin
  out, or a rectangle of the given size centred on them.
- Every UUID follows from the netlist's content, so one netlist gives one board.
- Every physical pad of a pin number gets the pin's net.
- Each footprint keeps the library-qualified footprint ID and gets the link to its symbol
  (``(path ...)``), so KiCad's schematic parity check and later updates match them.
"""
from __future__ import annotations

import hashlib
import json
import uuid as uuidlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .base import invalid
from .fileformat import lib_tables
from .fileformat.board import Board
from .fileformat.new_board import EMPTY
from .fileformat.sexpr import Document, List, SexprError, copy, number, quote, symbol
from .pcb_read_netlist import Netlist

NM = 1_000_000
EDGE_MARGIN_MM = 2.0
OUTLINE_WIDTH = "0.1"
WITH_UUID = {
    "property", "fp_text", "fp_text_box", "fp_line", "fp_arc", "fp_circle", "fp_rect",
    "fp_poly", "fp_curve", "pad", "zone", "dimension", "group", "image",
}  # fmt: skip
DROPPED = {"version", "generator", "generator_version"}


@dataclass
class Plan:
    components: list[dict[str, Any]] = field(default_factory=list)
    nets: list[dict[str, Any]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def mm(value: float) -> float:
    return value / NM


def from_mm(value: float) -> int:
    return int(float(value) * float(NM))


class _Ids:
    """UUIDs that follow from what the board is made of, not from chance."""

    def __init__(self, seed: str) -> None:
        self.namespace = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "kicad-cli/board/" + seed)

    def __call__(self, *parts: object) -> str:
        return quote(str(uuidlib.uuid5(self.namespace, "/".join(str(p) for p in parts))))


def resolve(identifier: str, tables: lib_tables.Tables) -> Path | None:
    """``Library:Name`` to its ``.kicad_mod`` file through the library tables, or None."""
    if ":" not in identifier:
        return None
    library, name = identifier.split(":", 1)
    if any(not v or v in {".", ".."} or any(c in v for c in "/\\:") for v in (library, name)):
        return None
    selected = tables.footprints.get(library)
    if selected is None or selected.kind != "KiCad":
        return None
    candidate = selected.path / (name + ".kicad_mod")
    return candidate if candidate.is_file() else None


def plan(netlist: Netlist, tables: lib_tables.Tables, pitch_mm: float = 10.0, margin_mm: float = 10.0) -> Plan:
    """Every footprint resolved and placed on a grid, or the reasons it cannot be."""
    out = Plan(nets=netlist.nets)
    placed = []
    for comp in netlist.components:
        if not comp.on_board:
            out.skipped.append(comp.ref)
            continue
        if not comp.footprint:
            out.problems.append(f"{comp.ref}: the schematic assigns no footprint")
            continue
        found = resolve(comp.footprint, tables)
        if found is None:
            out.problems.append(f"{comp.ref}: footprint {comp.footprint} is not in the project's or the user's libraries")
            continue
        placed.append({"ref": comp.ref, "value": comp.value, "footprint": comp.footprint, "file": str(found),
                       "path": comp.path})
    if not placed and not out.problems:
        out.problems.append("the schematic has no component that goes on the board")
    columns = max(1, int(len(placed) ** 0.5 + 0.999))
    for index, item in enumerate(sorted(placed, key=lambda c: _natural(c["ref"]))):
        item["x"] = round(margin_mm + (index % columns) * pitch_mm, 3)
        item["y"] = round(margin_mm + (index // columns) * pitch_mm, 3)
        out.components.append(item)
    return out


def _natural(ref: str) -> tuple:
    import re

    return tuple(int(p) if p.isdigit() else p for p in re.split(r"(\d+)", ref))


def _insert_before_tail(root: List, node: List) -> None:
    for index, item in enumerate(root.items):
        if isinstance(item, List) and item.head == "embedded_fonts":
            root.insert(index, node)
            return
    root.append(node)


def _footprint(item: dict[str, Any], ids: _Ids, *, source: List | None = None) -> List:
    """A library footprint as a placed one: named, positioned, linked, given UUIDs."""
    if source is None:
        try:
            source = Document.load(item["file"]).root
        except (OSError, SexprError, UnicodeDecodeError) as exc:
            raise invalid(f"{item['ref']}: the footprint file {item['file']} cannot be read: {str(exc)[:200]}") from None
    ref = item["ref"]
    node = List.new("footprint", quote(item["footprint"]))
    children = [c for c in source.items[2:] if not (isinstance(c, List) and c.head in DROPPED)]
    have = {c.value(1) for c in children if isinstance(c, List) and c.head == "property"}
    at = List.new("at", number(mm(from_mm(item["x"]))), number(mm(from_mm(item["y"]))))
    counters: dict[str, int] = {}
    last_property = max((i for i, c in enumerate(children) if isinstance(c, List) and c.head == "property"),
                        default=None)
    for index, child in enumerate(children):
        if not isinstance(child, List):
            node.append(child)
            continue
        placed = copy(child)
        head = placed.head
        if head == "property":
            name = placed.value(1)
            if name == "Reference":
                placed.set(2, quote(ref))
            elif name == "Value" and item.get("value"):
                placed.set(2, quote(str(item["value"])))
        if head in WITH_UUID:
            counters[head] = counters.get(head, 0) + 1
            _set_uuid(placed, ids(ref, head, counters[head]))
        node.append(placed)
        if head == "layer":
            node.append(List.new("uuid", ids(ref)))
            node.append(at)
        if head == "property" and placed.value(1) == "Value":
            for missing in ("Datasheet", "Description"):
                if missing not in have:
                    node.append(_empty_property(missing, ids(ref, "property", missing)))
        if index == last_property and item.get("path"):
            node.append(List.new("path", quote(item["path"])))
    if last_property is None and item.get("path"):
        node.append(List.new("path", quote(item["path"])))
    return node


def _set_uuid(node: List, value: str) -> None:
    for index, item in enumerate(node.items):
        if isinstance(item, List) and item.head == "uuid":
            node.set(index, List.new("uuid", value))
            return
    if node.head == "property":
        for index, item in enumerate(node.items):
            if isinstance(item, List) and item.head == "effects":
                node.insert(index, List.new("uuid", value))
                return
    node.append(List.new("uuid", value))


def _empty_property(name: str, uuid: str) -> List:
    return List.new(
        "property", quote(name), quote(""), List.new("at", "0", "0", "0"), List.new("layer", quote("F.Fab")),
        List.new("hide", symbol("yes")), List.new("uuid", uuid),
        List.new("effects", List.new("font", List.new("size", "1.27", "1.27"))),
    )


def _wire(footprints: dict[str, List], nets: list[dict[str, Any]]) -> tuple[int, list[str]]:
    """Join each net's pads; a node naming a pad the footprint lacks is reported."""
    pads: dict[str, dict[str, list[List]]] = {}
    for ref, node in footprints.items():
        pads[ref] = {}
        for pad in node.find_all("pad"):
            pads[ref].setdefault(pad.value(1) or "", []).append(pad)
    joined, missing = 0, []
    for net in nets:
        for member in net["nodes"]:
            if member["ref"] not in footprints:
                continue  # a part that is not on the board
            physical = pads.get(member["ref"], {}).get(member["pin"], [])
            if not physical:
                missing.append(f"{member['ref']}.{member['pin']} ({net['name']})")
                continue
            for pad in physical:
                _set_net(pad, net["name"])
                joined += 1
    return joined, missing


def _set_net(pad: List, name: str) -> None:
    entry = List.new("net", quote(name))
    for index, item in enumerate(pad.items):
        if isinstance(item, List) and item.head == "net":
            pad.set(index, entry)
            return
    for index, item in enumerate(pad.items):
        if isinstance(item, List) and item.head == "uuid":
            pad.insert(index, entry)
            return
    pad.append(entry)


def _outline(document: Document, ids: _Ids, size_mm: tuple[float, float] | None) -> list[float]:
    """A rectangle on Edge.Cuts: around the parts a margin out, or of the given size centred on them."""
    boxes = [box for fp in Board(document).footprints if (box := fp.bbox()) is not None]
    if not boxes and size_mm is None:
        return [0.0, 0.0]
    if boxes:
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    else:
        x0 = y0 = x1 = y1 = 0
    if size_mm is None:
        margin = from_mm(EDGE_MARGIN_MM)
        left, top, right, bottom = x0 - margin, y0 - margin, x1 + margin, y1 + margin
    else:
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        w, h = from_mm(size_mm[0]), from_mm(size_mm[1])
        left, top = cx - w // 2, cy - h // 2
        right, bottom = left + w, top + h
    corners = [(left, top), (right, top), (right, bottom), (left, bottom)]
    for index in range(4):
        (ax, ay), (bx, by) = corners[index], corners[(index + 1) % 4]
        line = List.new(
            "gr_line",
            List.new("start", number(mm(ax)), number(mm(ay))),
            List.new("end", number(mm(bx)), number(mm(by))),
            List.new("stroke", List.new("width", OUTLINE_WIDTH), List.new("type", "default")),
            List.new("layer", quote("Edge.Cuts")),
            List.new("uuid", ids("outline", index)),
        )
        _insert_before_tail(document.root, line)
    return [round(mm(right - left), 3), round(mm(bottom - top), 3)]


def seed(p: Plan) -> str:
    """What the board is made of, as a stable hash: one netlist, one board."""
    content = {"components": [{k: c[k] for k in ("ref", "value", "footprint", "path")} for c in p.components],
               "nets": p.nets}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode("utf-8")).hexdigest()


def build(p: Plan, size_mm: tuple[float, float] | None = None) -> tuple[str, dict[str, Any]]:
    """The board's text and a summary of what it holds."""
    document = Document.parse(EMPTY)
    ids = _Ids(seed(p))
    footprints: dict[str, List] = {}
    for item in p.components:
        node = _footprint(item, ids)
        _insert_before_tail(document.root, node)
        footprints[item["ref"]] = node
    joined, missing = _wire(footprints, p.nets)
    board_mm = _outline(document, ids, size_mm)
    return document.dumps(), {
        "footprints": len(footprints),
        "nets": len(p.nets),
        "pads_connected": joined,
        "unmatched_pins": missing,
        "width_mm": board_mm[0],
        "height_mm": board_mm[1],
        "placement": "grid",
    }
