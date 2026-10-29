"""Fresh copper/project consumption, independent of the writer's file model.

The separate Decimal lexer also used by pose readback supplies measurements;
this module imports neither the router, board writer nor its geometry engine.
Native SDK/connectivity/DRC and full Gerber geometry remain separate evidence.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

from . import move_readback as reader
from ._driver_sdk import tokens
from .move_transaction import regular

COPPER = {"segment", "arc", "via"}
FILL = {"filled_polygon", "fill_segments"}


def nm(value):
    number = reader._decimal(value) * Decimal(1_000_000)
    return int(number.to_integral_value())


def canonical(node, *, omit_fill=False):
    if isinstance(node, reader._Atom):
        return node.value
    return [
        canonical(item, omit_fill=omit_fill)
        for item in node.items
        if not (omit_fill and isinstance(item, reader._List) and item.head in FILL)
    ]


def position(node, name):
    value = node.one(name)
    return [nm(value.atom(1)), nm(value.atom(2))]


def net_of(node, table):
    entry = node.one("net", optional=True)
    if entry is None:
        return ""
    if len(entry.items) >= 3:
        return entry.atom(2)
    return table.get(entry.atom(1), entry.atom(1))


def reference(node):
    values = [part.atom(2) for part in node.children("property") if part.atom(1) == "Reference"]
    values += [part.atom(2) for part in node.children("fp_text") if part.atom(1) == "reference"]
    values = list(dict.fromkeys(values))
    if len(values) != 1:
        raise reader.ReadbackError("Footprint reference is absent or ambiguous")
    return values[0]


def inspect(raw, project_raw, selected):
    root = reader._parse(raw.decode("utf-8"))
    table = {node.atom(1): node.atom(2) for node in root.children("net")}
    tracks, pads, identities, nets = [], [], set(), set(table.values()) - {""}
    static, other_copper = [], []
    for node in root.items:
        if not isinstance(node, reader._List) or node.head not in COPPER:
            static.append(canonical(node, omit_fill=True))
            if isinstance(node, reader._List) and node.head in {"footprint", "module"}:
                ref = reference(node)
                for pad in node.children("pad"):
                    net = net_of(pad, table)
                    if net:
                        nets.add(net)
                    identity = pad.one("uuid", optional=True)
                    pads.append(
                        {
                            "ref": ref,
                            "pin": pad.atom(1),
                            "net": net,
                            "id": identity.atom(1) if identity else tokens.digest(canonical(pad)),
                        }
                    )
            continue
        net = net_of(node, table)
        if net:
            nets.add(net)
        identity = node.one("uuid", optional=True)
        uid = identity.atom(1) if identity else "content-" + tokens.digest(canonical(node))
        if uid in identities:
            raise reader.ReadbackError("Ambiguous copper identity")
        identities.add(uid)
        row = {
            "id": uid,
            "kind": node.head,
            "net": net,
            "semantic_sha256": tokens.digest(canonical(node)),
        }
        if node.head == "via":
            layers = node.one("layers")
            row.update(
                position_nm=position(node, "at"),
                diameter_nm=nm(node.one("size").atom(1)),
                drill_nm=nm(node.one("drill").atom(1)),
                layers=[item.value for item in layers.items[1:] if isinstance(item, reader._Atom)],
            )
        else:
            row.update(
                start_nm=position(node, "start"),
                end_nm=position(node, "end"),
                width_nm=nm(node.one("width").atom(1)),
                layer=node.one("layer").atom(1),
            )
            if node.head == "arc":
                row["mid_nm"] = position(node, "mid")
        tracks.append(row)
        if net not in selected:
            other_copper.append(row)
    project = json.loads(project_raw.decode("utf-8")) if project_raw else None
    if project is not None and not isinstance(project, dict):
        raise reader.ReadbackError("Project is not a JSON object")
    return {
        "objects": sorted(tracks, key=lambda x: x["id"]),
        "pads": sorted(pads, key=lambda x: (x["ref"], x["pin"], x["id"])),
        "nets": sorted(nets),
        "protected_sha256": tokens.digest(
            {"static": static, "other_copper": sorted(other_copper, key=lambda x: x["id"])}
        ),
        "project": project,
        "board_sha256": hashlib.sha256(raw).hexdigest(),
        "project_sha256": hashlib.sha256(project_raw).hexdigest() if project_raw else None,
        "copper_layers": [
            row.atom(1)
            for row in root.one("layers").items[1:]
            if isinstance(row, reader._List) and row.head.isdigit() and row.atom(1).endswith(".Cu")
        ],
    }


def observe(board, selected):
    board = Path(board)
    regular(board)
    project = board.with_suffix(".kicad_pro")
    raw = board.read_bytes()
    try:
        regular(project)
        project_raw = project.read_bytes()
    except FileNotFoundError:
        project_raw = None
    result = inspect(raw, project_raw, selected)
    if (
        board.read_bytes() != raw
        or (project.read_bytes() if project.exists() else None) != project_raw
    ):
        raise reader.ReadbackError("Saved copper/project changed during observation")
    return result
