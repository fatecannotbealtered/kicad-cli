"""Zone preparation in memory; no CLI envelope, save, or transaction dispatch."""

from __future__ import annotations

import json
import math
import uuid

from .fileformat import zonefill
from .fileformat.board import Board
from .fileformat.sexpr import List, symbol
from .native import pour


class ZonePlanError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.details = code, details


def identities(board):
    found = {}
    for zone in board.zones:
        node = zone.node.find("uuid")
        identity = str(node.value(1)) if node is not None else ""
        if not identity or identity in found:
            raise ZonePlanError("E_VALIDATION", "Zone UUIDs must be present and unique.")
        found[identity] = zone
    return found


def create(board: Board, params: dict, baseline: str):
    net, name = str(params["net"]).strip(), str(params["layer"]).strip()
    layer = pour.layer_named(board, name)
    if layer is None or not layer.name.endswith(".Cu"):
        raise ZonePlanError("E_VALIDATION", "Select an existing copper layer.", layer=name)
    if net not in pour.nets_of(board):
        raise ZonePlanError("E_NOT_FOUND", "Net not present in board.", net=net)
    zones = identities(board)
    if any(z.net == net and layer.name in z.layers and not z.rule_area for z in zones.values()):
        raise ZonePlanError(
            "E_CONFLICT", "Zone already exists; use zone refill.", conflict="state_drift"
        )
    options = {
        "margin": float(params.get("margin", pour.DEFAULT_MARGIN_MM)),
        "clearance": float(params.get("clearance", pour.DEFAULT_CLEARANCE_MM)),
        "min_width": float(params.get("min-width", pour.DEFAULT_MIN_WIDTH_MM)),
        "connect": str(params.get("connect", "thermal")),
    }
    if any(
        not math.isfinite(v) or not math.isfinite(v * pour.NM) for v in list(options.values())[:3]
    ):
        raise ZonePlanError("E_VALIDATION", "Zone dimensions must be finite.")
    if options["margin"] < 0 or options["clearance"] <= 0 or options["min_width"] <= 0:
        raise ZonePlanError(
            "E_VALIDATION", "Margin must be nonnegative; clearance and width must be positive."
        )
    if options["connect"] not in {"thermal", "solid"}:
        raise ZonePlanError("E_VALIDATION", "Connection must be thermal or solid.")
    box = board.edge_bbox()
    if box is None or box[2] <= box[0] or box[3] <= box[1]:
        raise ZonePlanError("E_VALIDATION", "A nonempty Edge.Cuts outline is required.")
    margin = pour.from_mm(options["margin"])
    rect = (box[0] + margin, box[1] + margin, box[2] - margin, box[3] - margin)
    if rect[2] <= rect[0] or rect[3] <= rect[1]:
        raise ZonePlanError("E_VALIDATION", "Margin leaves no zone area.")
    seed = json.dumps([baseline, net, layer.name, rect, options], sort_keys=True)
    identity = str(uuid.uuid5(uuid.NAMESPACE_URL, seed))
    if identity in zones:
        raise ZonePlanError(
            "E_CONFLICT", "Planned zone UUID already exists.", conflict="state_drift"
        )
    node = pour.zone_node(net, layer.name, rect, options)
    node.find("uuid").set(1, '"' + identity + '"')
    node.find("fill").pop(1)  # KiCad represents unfilled by omitting the optional 'yes' atom.
    # KiCad 6+ needs this marker to avoid legacy stroked-fill inflation on load.
    node.append(List.new("filled_areas_thickness", symbol("no")))
    pour._insert_zone(board, node)
    return (
        Board(board.document, board.path),
        [identity],
        {
            "net": net,
            "layer": layer.name,
            "outline_nm": list(rect),
            "options": options,
        },
    )


def refill(board: Board, params: dict, settings):
    available = {key: z for key, z in identities(board).items() if not z.rule_area}
    text = str(params.get("zones") or "")
    all_zones = bool(params.get("all"))
    if bool(text) == all_zones:
        raise ZonePlanError("E_USAGE", "Choose exactly one of --zones or --all.")
    parts = [part.strip() for part in text.split(",")]
    if not all_zones and (not all(parts) or len(parts) != len(set(parts))):
        raise ZonePlanError("E_VALIDATION", "Selected zone UUIDs must be nonempty and unique.")
    selected = sorted(available) if all_zones else sorted(parts)
    missing = sorted(set(selected) - available.keys())
    if missing:
        raise ZonePlanError(
            "E_NOT_FOUND", "Selected copper zone UUID not present.", missing=missing
        )
    legacy = [
        identity
        for identity in selected
        if available[identity].node.find("filled_areas_thickness") is None
        or available[identity].node.find("filled_areas_thickness").value(1) != "no"
    ]
    if legacy:
        raise ZonePlanError(
            "E_VALIDATION",
            "Selected zones use legacy/stroked fill; modern polygon refill "
            "would change native interpretation.",
            zones=legacy,
            next_action="Open an owned copy in KiCad, inspect and save modern zone "
            "settings, then re-plan.",
        )
    filler = zonefill.Filler(board, settings)
    for identity in selected:
        zonefill.write(filler.fill(available[identity]))
    return board, selected, {"selection": "all" if all_zones else "explicit"}
