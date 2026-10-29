"""One explicitly identified, disconnected schematic footprint; never delete-all."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .fileformat.drc.clearance import thing_distance
from .fileformat.drc.copper import Copper
from .fileformat.geometry import MAX_ERROR
from .sch_edit_contract import pointer

REQUEST = "urn:kicad-cli:sync-request:1"
NEXT = (
    "Read reference --command 'project sync' and $defs.remove_footprint_file. "
    "Observe the exact retained footprint, disconnect its schematic pin and use "
    "explicitly selected routing where needed; refill/recheck before a new preview. "
    "Never remove all unmatched objects or edit the PCB to bypass this refusal."
)


def parse(raw):
    from jsonschema import Draft202012Validator

    from .sync_inputs import InputError

    contract = json.loads(Path(__file__).with_name("driver_contract.json").read_bytes())
    schema = contract["reference"]["json_schemas"][REQUEST]["$defs"]["remove_footprint_file"]
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise InputError(
            "Removal selection is not valid UTF-8 JSON",
            parameter="--remove-footprint",
            path="/",
            next_action=NEXT,
        ) from exc
    problems = []
    for error in Draft202012Validator(schema).iter_errors(value):
        problems.append({"path": pointer(error.absolute_path), "problem": error.message[:250]})
    if problems:
        raise InputError(
            "Removal selection does not match the public schema",
            parameter="--remove-footprint",
            fields=problems,
            next_action=NEXT,
        )
    return value


def prepare(board, before, found, selected, schematic_identity):
    """Return a removal preview. Geometry is read, not modified here."""
    from .sync_plan import PlanError

    def refuse(message, **details):
        details.setdefault("next_action", NEXT)
        raise PlanError(message, parameter="--remove-footprint", **details)

    hits = [item for item in before if item["id"] == selected["pcb_uuid"]]
    if len(hits) != 1:
        refuse("Selected PCB UUID does not identify exactly one footprint")
    item = hits[0]
    expected = {
        "schematic_root_uuid": schematic_identity["root_uuid"],
        "schematic_sha256": schematic_identity["sha256"],
        "reference": item["ref"],
        "pcb_uuid": item["id"],
        "schematic_path": item["path"],
        "footprint": item["fpid"],
        "saved_semantics_sha256": item["saved_semantics_sha256"],
        "pads": [{"uuid": p["id"], "number": p["number"], "net": p["net"]} for p in item["pads"]],
        "expect_schematic_absent": True,
    }
    normalized = dict(
        selected, pads=sorted(selected["pads"], key=lambda p: (p["number"], p["uuid"]))
    )
    if normalized != expected:
        refuse(
            "Selected footprint identity/pad snapshot differs from the saved PCB",
            expected_selection=expected,
        )
    if not item["path"] or "board_only" in item["attributes"]:
        refuse("Board-only or unlinked objects are outside explicit schematic deletion")
    source_paths = [path for part in found.components for path in part["paths"]]
    if (
        any(part["ref"].casefold() == item["ref"].casefold() for part in found.components)
        or item["path"] in source_paths
    ):
        refuse("The selected schematic identity/reference is still present")
    if len(item["path"].strip("/").split("/")) != 1:
        refuse(
            "Explicit deletion currently supports a root-sheet footprint only; "
            "hierarchical removal is unsupported"
        )
    fp = next(fp for fp in board.footprints if fp.node.find("uuid").value(1) == item["id"])
    descendants = []

    def walk(node):
        if "locked" in [part for part in node.items[1:] if isinstance(part, str)]:
            refuse("Selected footprint or attached object has a legacy lock flag")
        for child in node.lists():
            if child.head == "locked" and child.value(1) != "no":
                refuse("Selected footprint or attached object is locked")
            if child.head == "uuid":
                descendants.append(child.value(1))
            walk(child)

    walk(fp.node)
    if fp.locked:
        refuse("Selected footprint is locked")
    if any(
        pad.shape not in {"circle", "rect", "oval", "roundrect"}
        for part in board.footprints
        for pad in part.pads
    ):
        refuse("Deletion contact check does not support every pad shape on this board")
    approximated_pads = [
        {
            "reference": part.reference,
            "pad": pad.number,
            "uuid": pad.node.find("uuid").value(1),
            "roundrect_ratio": pad.roundrect_ratio,
            "chamfer_ratio": pad.chamfer_ratio,
            "chamfer_corners": sorted(pad.chamfer_corners),
        }
        for part in board.footprints
        for pad in part.pads
        if pad.shape == "roundrect"
        and pad.roundrect_ratio > 0
        and pad.chamfer_ratio > 0
        and pad.chamfer_corners
    ]
    if approximated_pads:
        # pad_copper represents mixed rounded/chamfered corners by inscribed
        # chords. Their MAX_ERROR bound is not covered by the 1 nm numerical
        # guard below. Refuse this representation on either side of a contact
        # query; do not alter shared DRC geometry or inflate every tolerance.
        refuse(
            "Chamfered roundrect pads are unsupported by deletion contact checks",
            pads=approximated_pads,
            inscribed_curve_error_bound_nm=MAX_ERROR,
            contact_numerical_guard_nm=1,
            next_action="Keep this footprint: run project sync without --remove-footprint for "
            "default retention. Read reference --command 'project sync' for supported geometry. "
            "Native inspection is separate; do not alter pad shapes or tolerances "
            "to bypass this refusal.",
        )
    if not fp.pads:
        refuse("Padless mechanical objects are outside this removal policy")
    if board.footprint_zones or any(track.kind != "segment" for track in board.tracks):
        refuse("Footprint-local zones or curved tracks are outside this deletion contact check")
    for pad in item["pads"]:
        if pad["net"] not in {"", f"unconnected-({item['ref']}-Pad{pad['number']})"}:
            refuse(
                "Selected pad still has a connected net; disconnect/sync it first",
                pad_uuid=pad["id"],
                net=pad["net"],
            )
    # Parent groups/dimensions may refer to attached UUIDs. They are not owned by
    # the footprint and cannot be silently edited as part of its removal.
    for node in board.root.lists():
        if node is fp.node:
            continue
        if any(identity in child.values() for child in node.walk() for identity in descendants):
            refuse(
                "A board-level object refers to the selected footprint/attachment",
                object_kind=node.head,
            )
    if any(
        flag.value(1) != "no"
        for zone in board.zones
        for flag in zone.node.find_all("filled_areas_thickness")
    ):
        refuse("Legacy zone geometry is outside this deletion contact check")
    for zone in board.zones:
        if zone.rule_area:
            continue
        if not zone.filled:
            refuse("Copper zones require saved native refill before deletion contact checks")
        for polygon in zone.node.find_all("filled_polygon"):
            points = polygon.find("pts")
            if points is None or any(part.head != "xy" for part in points.lists()):
                refuse("Curved/unknown zone fill is outside this deletion contact check")
    for node in board.root.walk():
        if node.head.startswith(("gr_", "fp_")) or node.head == "property":
            layer = node.find("layer")
            if layer is not None and layer.value(1) in board.copper_layers:
                refuse("Copper graphics/text are outside the deletion contact check")
    copper = Copper(board)
    owned = [thing for thing in copper.things if thing.fp is fp]
    contacts = []
    for a in owned:
        for b in copper.things:
            if b.fp is fp:
                continue
            for layer in sorted(a.layers & b.layers):
                # Numerical guard for the supported analytic pad/straight saved
                # geometry only. It does not cover curved polygon approximation
                # error and is not a native clearance verdict.
                if thing_distance(a, b, layer, reach=1) <= 1:
                    node = getattr(b.owner, "node", None)
                    identity = node.find("uuid") if node is not None else None
                    contacts.append(
                        {
                            "kind": b.kind,
                            "uuid": identity.value(1) if identity else None,
                            "net": b.own_net,
                            "layer": layer,
                        }
                    )
    if contacts:
        refuse(
            "Selected footprint still contacts saved copper; nothing was removed", contacts=contacts
        )
    return {
        "selection": expected,
        "before": item,
        "attached_objects": {
            "uuids": sorted(descendants),
            "direct_kinds": dict(Counter(child.head for child in fp.node.lists())),
        },
        "copper_impact": {
            "tracks_removed": [],
            "vias_removed": [],
            "zones_removed": [],
            "contacts": contacts,
            "geometry_policy": "Analytic circle/oval/unchamfered roundrect pads and straight "
            "polygons/tracks; mixed rounded/chamfered pads refused on either side. "
            "1 nm numerical contact guard; no acceptance of inscribed curved approximations.",
            "policy": "Preserve every board-level object; require isolated pads and no saved "
            "copper contact. Native refill/DRC after removal remains required.",
        },
    }
