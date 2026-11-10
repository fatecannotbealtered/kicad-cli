"""via create, zone list, zone create, zone delete: copper beside the tracks.

A new via and a new zone are first checked with KiCad's own DRC on staged copies
(``pcb_edit_drc``), as a new track is; a change that adds a DRC error or an unconnected item is
refused. A zone is written as an outline only: KiCad fills it in memory for checks and exports,
and the file holds no fill until KiCad saves the board after a refill.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import base, pcb_edit_drc, pcb_edit_links
from ..fileformat.board import Board, Via, Zone
from ..fileformat.sexpr import Document, copy, number, quote
from ..pcb_edit_board import NM, fresh_uuid, identity, insert_before_tail, load, load_for_write, to_nm, via_key
from .board_edit import _fragment, _locked, as_points, check_net_and_layer, class_size, corners, items_of, uuids


def zone_key(path: Path, uid: str) -> str:
    return base.key(path, f"/zones/{uid}")


# ------------------------------------------------------------------------------- vias


class ViaCreate(base.Handler):
    """Through vias, from the first copper layer to the last; with --input, several of them."""

    def _via(self, doc: Path, board: Board, item: dict[str, Any]) -> dict[str, Any]:
        net = str(item.get("net") or "")
        check_net_and_layer(doc, board, net, board.copper_layers[0], "via create")
        if item.get("x_mm") is None or item.get("y_mm") is None:
            raise base.usage("via create needs --x and --y.")
        at = (to_nm(item["x_mm"]), to_nm(item["y_mm"]))
        diameter = to_nm(item["diameter_mm"]) if item.get("diameter_mm") is not None else \
            class_size(doc, net, "via_diameter", "--diameter")
        drill = to_nm(item["drill_mm"]) if item.get("drill_mm") is not None else class_size(doc, net, "via_drill", "--drill")
        if drill <= 0 or diameter <= drill:
            raise base.invalid("A via needs a drill more than 0 and a diameter larger than its drill.")
        for via in board.vias:
            if via.position == at:
                raise base.invalid(f"The board already has a via at {base.mm(at[0])}, {base.mm(at[1])} mm "
                                   f"({identity(via.node)}).")
        layers = [board.copper_layers[0], board.copper_layers[-1]]
        return {"net": net, "at": at, "diameter": diameter, "drill": drill, "layers": layers,
                "id": fresh_uuid(board, "via", net, *at)}

    def _vias(self, doc: Path, board: Board) -> list[dict[str, Any]]:
        vias = [self._via(doc, board, item) for item in items_of(self.call.inputs)]
        if len({v["at"] for v in vias}) != len(vias):
            raise base.invalid("Two items put a via at the same point.")
        return vias

    @staticmethod
    def _insert(board: Board, via: dict[str, Any]) -> None:
        (x, y), layers = via["at"], " ".join(quote(name) for name in via["layers"])
        node = copy(Document.parse(
            f"(via (at {number(x / NM)} {number(y / NM)}) (size {number(via['diameter'] / NM)})"
            f" (drill {number(via['drill'] / NM)}) (layers {layers}) (net {quote(via['net'])})"
            f" (uuid {quote(via['id'])}))").root)
        insert_before_tail(board.root, node)

    @staticmethod
    def _values(via: dict[str, Any]) -> dict[str, Any]:
        return {"exists": True, "net": via["net"], "x_mm": base.mm(via["at"][0]), "y_mm": base.mm(via["at"][1]),
                "diameter_mm": base.mm(via["diameter"]), "drill_mm": base.mm(via["drill"])}

    def preview(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        board = load_for_write(doc)
        before_text = board.document.dumps()
        vias = self._vias(doc, board)
        for via in vias:
            self._insert(board, via)
        check = pcb_edit_drc.precheck(doc, before_text, board.document.dumps())
        targets, changes = [], []
        for via in vias:
            key = via_key(doc, via["id"])
            targets.append({"key": key, "kind": "via", "label": via["id"]})
            changes += [{"target": key, "field": f, "before": False if f == "exists" else None, "after": v}
                        for f, v in self._values(via).items()]
        what = f"a via for net {vias[0]['net']}" if len(vias) == 1 else f"{len(vias)} vias"
        out = {"action": f"Add {what} to {doc.name}. {check.summary()}.", "targets": targets, "changes": changes,
               "can_apply": not check.blocked}
        if check.blocked:
            out["blocked_by"] = check.blocked_by()
        return out

    def apply(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        base.refuse_if_open(doc)
        board = load_for_write(doc)
        vias = self._vias(doc, board)
        for via in vias:
            self._insert(board, via)
        base.write_text(doc, board.document.dumps())
        # The runtime applies a batch one item at a time (DCLI-064), so this is one via.
        uid = vias[0]["id"]
        return base.saved(pcb_edit_links.via_links(load(doc), uid) or {"id": uid})

    def read_back(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        by_id: dict[str, Via] = {identity(v.node): v for v in load(doc).vias}
        observed: dict[str, dict[str, Any]] = {}
        for key in self.call.targets:
            via = by_id.get(_fragment(key)[len("/vias/"):])
            observed[key] = {"exists": False} if via is None else self._values(
                {"net": via.net, "at": via.position, "diameter": via.diameter, "drill": via.drill})
        return {"observed": observed}


# ------------------------------------------------------------------------------- zones


def zone_row(zone: Zone) -> dict[str, Any]:
    """A zone: its outline, and the holes cut out of it, which KiCad writes as further polygons."""
    outline, *holes = zone.outlines or [[]]
    return {"id": identity(zone.node), "net": zone.net, "layers": zone.layers, "name": zone.name,
            "priority": zone.priority, "keepout": zone.rule_area, "points": as_points(outline),
            "holes": [as_points(h) for h in holes], "filled_in_file": bool(zone.filled), "locked": _locked(zone.node)}


class ZoneList(base.Handler):
    def read(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        inputs = self.call.inputs
        rows = [zone_row(z) for z in load(doc).zones]
        if inputs.get("net"):
            rows = [r for r in rows if r["net"] == inputs["net"]]
        if inputs.get("layer"):
            rows = [r for r in rows if inputs["layer"] in r["layers"]]
        return {"items": rows}


class ZoneCreate(base.Handler):
    """A copper zone outline on one layer, with KiCad's default zone settings."""

    def _zone(self, doc: Path, board: Board) -> dict[str, Any]:
        inputs = self.call.inputs
        net, layer = str(inputs.get("net") or ""), str(inputs.get("layer") or "")
        check_net_and_layer(doc, board, net, layer, "zone create")
        points = corners(inputs.get("points"), 3, "--points")
        if len(set(points)) != len(points):
            raise base.invalid("The outline repeats a point.")
        priority = inputs.get("priority") or 0
        if priority < 0:
            raise base.invalid("--priority is a whole number, 0 or more.")
        for zone in board.zones:
            if zone.net == net and layer in zone.layers and zone.outlines and zone.outlines[0] == points:
                raise base.invalid(f"The board already has this zone ({identity(zone.node)}).")
        uid = fresh_uuid(board, "zone", net, layer, *[c for p in points for c in p])
        return {"net": net, "layer": layer, "points": points, "priority": priority, "id": uid}

    @staticmethod
    def _insert(board: Board, zone: dict[str, Any]) -> None:
        pts = " ".join(f"(xy {number(x / NM)} {number(y / NM)})" for x, y in zone["points"])
        priority = f" (priority {zone['priority']})" if zone["priority"] else ""
        node = copy(Document.parse(
            f"(zone (net {quote(zone['net'])}) (layer {quote(zone['layer'])}) (uuid {quote(zone['id'])}){priority}"
            " (hatch edge 0.5) (connect_pads (clearance 0.5)) (min_thickness 0.25)"
            " (fill (thermal_gap 0.5) (thermal_bridge_width 0.5) (island_removal_mode 0))"
            f" (polygon (pts {pts})))").root)
        insert_before_tail(board.root, node)

    @staticmethod
    def _values(zone: dict[str, Any]) -> dict[str, Any]:
        return {"exists": True, "net": zone["net"], "layer": zone["layer"], "priority": zone["priority"],
                "points": as_points(zone["points"])}

    def preview(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        board = load_for_write(doc)
        before_text = board.document.dumps()
        zone = self._zone(doc, board)
        self._insert(board, zone)
        check = pcb_edit_drc.precheck(doc, before_text, board.document.dumps())
        key = zone_key(doc, zone["id"])
        changes = [{"target": key, "field": f, "before": False if f == "exists" else None, "after": v}
                   for f, v in self._values(zone).items()]
        out = {"action": f"Add a {zone['net']} zone on {zone['layer']} to {doc.name}. {check.summary()}.",
               "targets": [{"key": key, "kind": "zone", "label": zone["id"]}], "changes": changes,
               "can_apply": not check.blocked}
        if check.blocked:
            out["blocked_by"] = check.blocked_by()
        return out

    def apply(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        base.refuse_if_open(doc)
        board = load_for_write(doc)
        zone = self._zone(doc, board)
        self._insert(board, zone)
        base.write_text(doc, board.document.dumps())
        row = zone_row(next(z for z in load(doc).zones if identity(z.node) == zone["id"]))
        return base.saved({"id": row["id"], "net": row["net"], "layer": zone["layer"], "priority": row["priority"],
                           "points": row["points"]})

    def read_back(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        by_id = {identity(z.node): z for z in load(doc).zones}
        observed: dict[str, dict[str, Any]] = {}
        for key in self.call.targets:
            zone = by_id.get(_fragment(key)[len("/zones/"):])
            observed[key] = {"exists": False} if zone is None else self._values(
                {"net": zone.net, "layer": zone.layers[0] if zone.layers else None, "priority": zone.priority,
                 "points": zone.outlines[0] if zone.outlines else []})
        return {"observed": observed}


class ZoneDelete(base.Handler):
    @staticmethod
    def _zone(board: Board, uid: str) -> Zone:
        found = [z for z in board.zones if identity(z.node) == uid]
        if not found:
            raise base.not_found(f"The board has no zone {uid}.")
        zone = found[0]
        if zone.rule_area:
            raise base.unsupported(f"{uid} is a rule area (keepout); kicad-cli deletes only copper zones.")
        if _locked(zone.node):
            raise base.invalid(f"The zone {uid} is locked; unlock it in KiCad first.")
        return zone

    def preview(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        board = load_for_write(doc)
        targets, changes = [], []
        for uid in uuids(self.call.inputs):
            self._zone(board, uid)
            key = zone_key(doc, uid)
            targets.append({"key": key, "kind": "zone", "label": uid})
            changes.append({"target": key, "field": "exists", "before": True, "after": False})
        return {"action": f"Delete {len(targets)} zone(s) from {doc.name}.", "targets": targets, "changes": changes,
                "can_apply": True}

    def apply(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        base.refuse_if_open(doc)
        board = load_for_write(doc)
        zones = [self._zone(board, uid) for uid in uuids(self.call.inputs)]
        rows = [zone_row(z) for z in zones]
        for zone in zones:
            board.root.remove(zone.node)
        base.write_text(doc, board.document.dumps())
        return base.saved({"deleted": [{k: r[k] for k in ("id", "net", "layers", "points", "holes")} for r in rows]})

    def read_back(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        present = {identity(z.node) for z in load(doc).zones}
        return {"observed": {key: {"exists": _fragment(key)[len("/zones/"):] in present} for key in self.call.targets}}


HANDLERS = {
    "via create": ViaCreate,
    "zone list": ZoneList,
    "zone create": ZoneCreate,
    "zone delete": ZoneDelete,
}
