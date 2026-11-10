"""netlist import, footprint move, track create, track delete: edits of a saved board.

Every edit rewrites only what it changes in the board's text (``fileformat``). A footprint
move and a new track are first checked with KiCad's own DRC on staged copies
(``pcb_edit_drc``); a change that adds a DRC error or an unconnected item is refused.
"""
from __future__ import annotations

import fnmatch
import json
import math
import os
from pathlib import Path
from typing import Any

from .. import base, kicad_tools, pcb_edit_drc, pcb_edit_links
from ..fileformat import lib_tables
from ..fileformat.board import Board
from ..fileformat.sexpr import Document, List, copy, number, quote
from ..pcb_edit_board import (
    NM,
    board_nets,
    derived_uuid,
    footprint,
    fp_key,
    fresh_uuid,
    identity,
    insert_before_tail,
    load,
    load_for_write,
    net_key,
    pad_key,
    sha,
    to_nm,
    track_key,
    via_key,
)
from ..pcb_edit_sync import check_annotation, copper_counts, plan, read_netlist
from .board_read import courtyard_box


def _fragment(key: str) -> str:
    return key.split("#", 1)[1] if "#" in key else ""


def _no_change(result: Any) -> dict[str, Any]:
    return {"dispatch_state": "dispatched", "change": "not_applied", "delivery": "not_applicable",
            "persistence": "unchanged", "result": result}


def _locked(node: List) -> bool:
    marker = node.find("locked")
    return (marker is not None and marker.value(1) != "no") or "locked" in [i for i in node.items[1:] if isinstance(i, str)]


# ------------------------------------------------------------------------------- netlist import


class NetlistImport(base.Handler):
    """Update the board from its schematic, as KiCad's "Update PCB from Schematic"."""

    def sources(self) -> list[Path]:
        doc = self.call.doc
        given = self.call.inputs.get("schematic")
        schematic = Path(os.path.abspath(given)) if given else doc.with_suffix(".kicad_sch") if doc is not None else None
        return [p for p in (doc, schematic) if p is not None]

    def _schematic(self, board: Path) -> Path:
        given = self.call.inputs.get("schematic")
        schematic = Path(os.path.abspath(given)) if given else board.with_suffix(".kicad_sch")
        if schematic.suffix != ".kicad_sch":
            raise base.usage(f"--schematic must be a .kicad_sch file, not {schematic.name}.")
        if not schematic.is_file():
            raise base.not_found(f"There is no schematic {schematic}; give it with --schematic.")
        return schematic

    def _compute(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        before = load_for_write(doc)
        before_text = before.document.dumps()
        schematic = self._schematic(doc)
        found = read_netlist(kicad_tools.netlist(base.kicad(), schematic))
        check_annotation(found)
        installed = base._found()
        tables = lib_tables.Tables.of(doc.parent, installed.root if installed else None)
        working = Board(Document.parse(before_text), doc)
        update = plan(working, found, tables, sha(before_text))
        after_text = working.document.dumps()
        return {"doc": doc, "schematic": schematic, "before": before, "before_text": before_text,
                "update": update, "after_text": after_text}

    @staticmethod
    def _result(state: dict[str, Any]) -> dict[str, Any]:
        update = state["update"]
        rows = update.rows
        return {
            "board": str(state["doc"]),
            "schematic": str(state["schematic"]),
            "counts": {k: len(v) for k, v in rows.items()},
            "added": rows.get("add", [])[:20],
            "changed": {k: rows.get(k, [])[:20] for k in ("change_footprint", "change_reference", "change_value",
                                                          "update_fields", "change_marks")},
            "renamed_nets": rows.get("renamed_nets", [])[:20],
            "copper_left_on_old_nets": rows.get("copper_left_on_old_nets", [])[:20],
            "not_removed": rows.get("not_removed", [])[:20],
            "problems": update.problems[:20],
            "note": "Footprints that no part matches are never deleted; they are listed under not_removed. "
                    "New footprints wait to the right of the board outline for placing.",
        }

    def preview(self) -> dict[str, Any]:
        state = self._compute()
        doc, update = state["doc"], state["update"]
        after = Board(Document.parse(state["after_text"]))
        targets: list[dict[str, Any]] = []
        changes: list[dict[str, Any]] = []

        def change(key: str, field: str, before: Any, after_value: Any) -> None:
            changes.append({"target": key, "field": field, "before": before, "after": after_value})

        old_pads = {(fp.reference, pad.number): pad.net for fp in state["before"].footprints for pad in fp.pads}
        for ref, want in update.expect_parts.items():
            key = fp_key(doc, ref)
            count = len(changes)
            if ref in update.added:
                change(key, "exists", False, True)
                change(key, "value", None, want["value"])
                change(key, "footprint", None, want["fpid"])
            else:
                old = update.old_of[ref]
                if old["ref"] != ref:
                    change(key, "reference", old["ref"], ref)
                if old["value"] != want["value"]:
                    change(key, "value", old["value"], want["value"])
                if old["fpid"] != want["fpid"]:
                    change(key, "footprint", old["fpid"], want["fpid"])
                for name, value in want["fields"].items():
                    if old["fields"].get(name) != value:
                        change(key, f"field:{name}", old["fields"].get(name), value)
                for word in ("dnp", "exclude_from_bom"):
                    was = word in old["footprint"].attributes
                    if was != want[word]:
                        change(key, word, was, want[word])
            old_ref = None if ref in update.added else update.old_of[ref]["ref"]
            for (pref, number_), net in sorted(update.expect_pads.items()):
                if pref != ref:
                    continue
                was = old_pads.get((old_ref, number_)) if old_ref is not None else None
                if was != net:
                    change(pad_key(doc, ref, number_), "net", was, net)
            if len(changes) > count:
                targets.append({"key": key, "kind": "footprint", "label": ref})
        before_copper, after_copper = copper_counts(state["before"]), copper_counts(after)
        nets = sorted({name for pair in update.renamed.items() for name in pair})
        for name in nets:
            key = net_key(doc, name)
            change(key, "copper_items", before_copper.get(name, 0), after_copper.get(name, 0))
            targets.append({"key": key, "kind": "net", "label": name})
        counts = {k: len(v) for k, v in update.rows.items() if v}
        action = (f"Update {doc.name} from {state['schematic'].name}: "
                  + (", ".join(f"{k} {v}" for k, v in counts.items()) or "nothing to change")
                  + (f"; {len(update.problems)} problems left as they are" if update.problems else ""))
        if not changes and state["after_text"] == state["before_text"]:
            # Nothing to write: the runtime refuses a blocked preview (E_CONFLICT, blocked) instead of
            # asking to confirm an empty change.
            return {"action": action, "targets": targets, "changes": changes, "can_apply": False,
                    "blocked_by": [f"{doc.name} already matches {state['schematic'].name}; there is nothing to import"]}
        return {"action": action, "targets": targets, "changes": changes, "can_apply": True}

    def apply(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        base.refuse_if_open(doc)
        state = self._compute()
        if state["after_text"] == state["before_text"]:
            return _no_change(self._result(state))
        base.write_text(doc, state["after_text"])
        return base.saved(self._result(state))

    def read_back(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        board = load(doc)
        by_ref: dict[str, list] = {}
        for fp in board.footprints:
            by_ref.setdefault(fp.reference, []).append(fp)
        copper = copper_counts(board)
        observed: dict[str, dict[str, Any]] = {}
        unreadable: list[str] = []
        for key in self.call.targets:
            frag = _fragment(key)
            if frag.startswith("/nets/"):
                observed[key] = {"copper_items": copper.get(frag[len("/nets/"):], 0)}
                continue
            if not frag.startswith("/footprints/"):
                continue
            rest = frag[len("/footprints/"):]
            ref, _, number_ = rest.partition("/pads/")
            found = by_ref.get(ref, [])
            if len(found) > 1:
                unreadable.append(f"reference@{key}")
                continue
            if number_:
                pads = [p for p in found[0].pads if p.number == number_] if found else []
                observed[key] = {"net": pads[0].net} if pads else {}
                continue
            if not found:
                observed[key] = {"exists": False}
                continue
            fp = found[0]
            values: dict[str, Any] = {"exists": True, "reference": fp.reference, "value": fp.value,
                                      "footprint": fp.library_id, "dnp": "dnp" in fp.attributes,
                                      "exclude_from_bom": "exclude_from_bom" in fp.attributes}
            for prop in fp.node.find_all("property"):
                values[f"field:{prop.value(1)}"] = prop.value(2) or ""
            observed[key] = values
        return {"observed": observed, "unreadable": unreadable}


# ------------------------------------------------------------------------------- footprint move


def eligible(fp: Any) -> None:
    """The footprints kicad-cli changes: unlocked ones with pads."""
    if fp.locked:
        raise base.invalid(f"{fp.reference} is locked; unlock it in KiCad first.")
    if not fp.pads:
        raise base.unsupported(f"{fp.reference} has no pads; kicad-cli moves only footprints with pads.")


TURNED = ("pad", "property", "fp_text")
FIXED = ("zone", "fp_text_box", "dimension")  # written where they lie on the board, not in the footprint's frame


def movable(fp: Any) -> None:
    """kicad-cli moves and turns only footprints whose children all follow the footprint's place."""
    for kind in FIXED:
        if fp.node.find(kind) is not None:
            raise base.unsupported(f"{fp.reference} holds a {kind.replace('_', ' ')}, which kicad-cli does not move "
                                   "with it; move this footprint in KiCad.")


def angle_text(value: float) -> str:
    return number(round(value, 6))


def turn(fp: Any, angle: float) -> None:
    """Turn a footprint to ``angle`` about its origin, as KiCad does: its own angle is written in
    (-180, 180], and every pad and text, whose stored angle is already its angle on the board,
    turns by the same amount, written in [0, 360). Children in the footprint's frame stay."""
    delta = angle - fp.angle
    own = board_angle(angle)
    at = fp.node.find("at")
    while len(at.items) > 3:
        at.pop(3)
    if own:
        at.append(angle_text(own))
    for child in fp.node.lists():
        if child.head not in TURNED:
            continue
        place = child.find("at")
        if place is None:
            continue
        turned = (float(place.atom(3) or 0) + delta) % 360
        while len(place.items) > 3:
            place.pop(3)
        if turned or child.head != "pad":
            place.append(angle_text(turned))


def board_angle(value: float) -> float:
    """An angle as KiCad writes a footprint's: in (-180, 180]."""
    out = round((value + 180) % 360 - 180, 6)
    return 180.0 if out == -180 else out + 0.0


def items_of(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    """The items of a --input batch, or the flags as its one item."""
    return inputs["items"] if inputs.get("items") is not None else [inputs]


def uuids(inputs: dict[str, Any]) -> list[str]:
    """--ids, or --id as a list of one."""
    return [str(i) for i in inputs.get("ids") or [inputs["id"]]]


class FootprintMove(base.Handler):
    def _moves(self) -> list[dict[str, Any]]:
        moves = []
        for item in items_of(self.call.inputs):
            rotation = None if item.get("rotation_deg") is None else float(item["rotation_deg"]) % 360
            if item.get("x_mm") is None and item.get("y_mm") is None and rotation is None:
                raise base.usage(f"footprint move needs --x, --y or --rotation for {item['id']}.")
            moves.append({"id": str(item["id"]), "x_mm": item.get("x_mm"), "y_mm": item.get("y_mm"),
                          "rotation_deg": rotation})
        return moves

    @staticmethod
    def _move(board: Board, moves: list[dict[str, Any]]) -> list[dict[str, Any]]:
        done = []
        for move in moves:
            fp = footprint(board, move["id"])
            eligible(fp)
            movable(fp)
            x = to_nm(move["x_mm"]) if move["x_mm"] is not None else fp.position[0]
            y = to_nm(move["y_mm"]) if move["y_mm"] is not None else fp.position[1]
            at = fp.node.find("at")
            at.set(1, number(x / NM))
            at.set(2, number(y / NM))
            angle = fp.angle if move["rotation_deg"] is None else move["rotation_deg"]
            if (angle - fp.angle) % 360:
                turn(fp, angle)
            done.append({"move": move, "before": fp.position, "after": (x, y), "angle_before": board_angle(fp.angle),
                         "angle_after": board_angle(angle)})
        return done

    def preview(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        board = load_for_write(doc)
        before_text = board.document.dumps()
        done = self._move(board, self._moves())
        check = pcb_edit_drc.precheck(doc, before_text, board.document.dumps())
        targets, changes = [], []
        for item in done:
            ref, move = item["move"]["id"], item["move"]
            key = fp_key(doc, ref)
            targets.append({"key": key, "kind": "footprint", "label": ref})
            for axis, index in (("x_mm", 0), ("y_mm", 1)):
                if move[axis] is not None:
                    changes.append({"target": key, "field": axis, "before": base.mm(item["before"][index]),
                                    "after": base.mm(item["after"][index])})
            if move["rotation_deg"] is not None:
                changes.append({"target": key, "field": "rotation_deg", "before": item["angle_before"],
                                "after": item["angle_after"]})
        out = {"action": f"Move {len(done)} footprint(s) on {doc.name}. {check.summary()}.",
               "targets": targets, "changes": changes, "can_apply": not check.blocked}
        if check.blocked:
            out["blocked_by"] = check.blocked_by()
        return out

    def apply(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        base.refuse_if_open(doc)
        board = load_for_write(doc)
        moves = self._moves()
        items = pcb_edit_links.copper(board)
        before = {m["id"]: pcb_edit_links.pad_links(items, m["id"]) for m in moves}
        done = self._move(board, moves)
        base.write_text(doc, board.document.dumps())
        return base.saved({"moved": self._as_stored(load(doc), done, before),
                           "note": "Tracks connected to a moved footprint do not follow it."})

    @staticmethod
    def _as_stored(board: Board, done: list[dict[str, Any]], before: dict[str, dict[str, list[str]]]) -> list[dict[str, Any]]:
        """Each moved footprint as the file now holds it, with the tracks its pads touch and the
        tracks that touched them before the move and no longer do."""
        items = pcb_edit_links.copper(board)
        out = []
        for d in done:
            ref = d["move"]["id"]
            fp = footprint(board, ref)
            links = pcb_edit_links.pad_links(items, ref)
            then = {w for hits in before.get(ref, {}).values() for w in hits}
            now = {w for hits in links.values() for w in hits}
            out.append({"id": ref, "x_mm": base.mm(fp.position[0]), "y_mm": base.mm(fp.position[1]),
                        "rotation_deg": board_angle(fp.angle), "layer": fp.layer,
                        "from_x_mm": base.mm(d["before"][0]), "from_y_mm": base.mm(d["before"][1]),
                        "from_rotation_deg": d["angle_before"], "courtyard": courtyard_box(fp),
                        "pads": [{"number": p.number, "net": p.net, "x_mm": base.mm(p.position[0]),
                                  "y_mm": base.mm(p.position[1]), "connects_to": links.get(str(p.number), [])}
                                 for p in fp.pads],
                        "tracks_left_behind": sorted(then - now)})
        return out

    def read_back(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        board = load(doc)
        observed: dict[str, dict[str, Any]] = {}
        unreadable: list[str] = []
        for key in self.call.targets:
            ref = _fragment(key)[len("/footprints/"):]
            found = [fp for fp in board.footprints if fp.reference == ref]
            if len(found) != 1:
                unreadable += [f"x_mm@{key}", f"y_mm@{key}", f"rotation_deg@{key}"]
                continue
            observed[key] = {"x_mm": base.mm(found[0].position[0]), "y_mm": base.mm(found[0].position[1]),
                             "rotation_deg": board_angle(found[0].angle)}
        return {"observed": observed, "unreadable": unreadable}


# ------------------------------------------------------------------------------- tracks


def net_class(doc: Path, net: str) -> dict[str, Any]:
    """The settings of the net's class in the project file: its track width, via size and so on."""
    project = doc.with_suffix(".kicad_pro")
    if not project.is_file():
        raise base.usage(f"There is no project file {project.name} to take the net class from; give the size.")
    try:
        settings = json.loads(project.read_text(encoding="utf-8")).get("net_settings") or {}
    except (OSError, ValueError) as exc:
        raise base.invalid(f"{project.name} cannot be read: {exc}.") from None
    classes = {c.get("name"): c for c in settings.get("classes") or []}
    name = (settings.get("netclass_assignments") or {}).get(net)
    if isinstance(name, list):
        name = name[0] if name else None
    if not name:
        for pattern in settings.get("netclass_patterns") or []:
            if fnmatch.fnmatch(net, pattern.get("pattern", "")):
                name = pattern.get("netclass")
                break
    return classes.get(name or "Default") or classes.get("Default") or {}


def class_size(doc: Path, net: str, key: str, flag: str) -> int:
    """One size of the net's class, such as track_width, in nm."""
    value = net_class(doc, net).get(key)
    if not isinstance(value, (int, float)) or value <= 0:
        raise base.usage(f"The net class of {net} gives no {key.replace('_', ' ')}; give {flag}.")
    return to_nm(value)


def default_width(doc: Path, net: str) -> int:
    """The track width of the net's class in the project file, in nm."""
    return class_size(doc, net, "track_width", "--width")


def check_net_and_layer(doc: Path, board: Board, net: str, layer: str, command: str) -> None:
    if not net or not layer:
        raise base.usage(f"{command} needs --net and --layer.")
    if layer not in board.copper_layers:
        raise base.invalid(f"{layer} is not a copper layer of {doc.name}; use one of {', '.join(board.copper_layers)}.")
    if net not in board_nets(board):
        raise base.invalid(f"{doc.name} has no net {net}; the board's nets come from the schematic (netlist import).")


def corners(value: Any, least: int, what: str) -> list[tuple[int, int]]:
    """The points of a --points value, in nm."""
    if not isinstance(value, list) or len(value) < least:
        raise base.invalid(f"{what} needs at least {least} points.")
    out = []
    for point in value:
        if not isinstance(point, dict) or not all(isinstance(point.get(k), (int, float)) and math.isfinite(point[k])
                                                  for k in ("x_mm", "y_mm")):
            raise base.invalid(f'Each point of {what} is {{"x_mm": number, "y_mm": number}}.')
        out.append((to_nm(point["x_mm"]), to_nm(point["y_mm"])))
    return out


def as_points(points: list[tuple[int, int]]) -> list[dict[str, float]]:
    return [{"x_mm": base.mm(x), "y_mm": base.mm(y)} for x, y in points]


class TrackCreate(base.Handler):
    """One segment from --x1..--y2, or a path through --points; with --input, several of them.

    A path's first segment has the path's UUID, so a target names the path; segment i has a UUID
    derived from it. UUIDs follow from what is drawn, so a preview and each item's apply agree."""

    FIELDS = ("x1_mm", "y1_mm", "x2_mm", "y2_mm")

    def _path(self, doc: Path, board: Board, item: dict[str, Any]) -> dict[str, Any]:
        net, layer = str(item.get("net") or ""), str(item.get("layer") or "")
        check_net_and_layer(doc, board, net, layer, "track create")
        if item.get("points") is not None:
            if any(item.get(f) is not None for f in self.FIELDS):
                raise base.usage("Give --points or --x1, --y1, --x2 and --y2, not both.")
            points, form = corners(item["points"], 2, "--points"), "points"
        else:
            missing = [f"--{f[:-3]}" for f in self.FIELDS if item.get(f) is None]
            if missing:
                raise base.usage(f"track create needs --points, or {', '.join(missing)}.")
            points = [(to_nm(item["x1_mm"]), to_nm(item["y1_mm"])), (to_nm(item["x2_mm"]), to_nm(item["y2_mm"]))]
            form = "segment"
        if any(a == b for a, b in zip(points, points[1:])):
            raise base.invalid("A segment has no length: two points in a row are the same point.")
        width = to_nm(item["width_mm"]) if item.get("width_mm") is not None else default_width(doc, net)
        if width <= 0:
            raise base.invalid("--width must be more than 0.")
        existing = {(t.net, t.layer, t.width, tuple(sorted((t.start, t.end)))): t for t in board.tracks if t.kind == "segment"}
        for a, b in zip(points, points[1:]):
            same = existing.get((net, layer, width, tuple(sorted((a, b)))))
            if same is not None:
                raise base.invalid(f"The board already has the segment from {base.mm(a[0])}, {base.mm(a[1])} mm to "
                                   f"{base.mm(b[0])}, {base.mm(b[1])} mm ({identity(same.node)}).")
        uid = fresh_uuid(board, "track", net, layer, width, *[c for p in points for c in p])
        ids = [uid] + [derived_uuid("track", uid, i) for i in range(1, len(points) - 1)]
        return {"net": net, "layer": layer, "width": width, "points": points, "id": uid, "ids": ids, "form": form}

    def _paths(self, doc: Path, board: Board) -> list[dict[str, Any]]:
        paths = [self._path(doc, board, item) for item in items_of(self.call.inputs)]
        drawn = set()
        for path in paths:
            for a, b in zip(path["points"], path["points"][1:]):
                segment = (path["net"], path["layer"], path["width"], tuple(sorted((a, b))))
                if segment in drawn:
                    raise base.invalid(f"The segment from {base.mm(a[0])}, {base.mm(a[1])} mm to {base.mm(b[0])}, "
                                       f"{base.mm(b[1])} mm is drawn twice.")
                drawn.add(segment)
        return paths

    @staticmethod
    def _insert(board: Board, path: dict[str, Any]) -> None:
        points = path["points"]
        for uid, s, e in zip(path["ids"], points, points[1:]):
            node = copy(Document.parse(
                f"(segment (start {number(s[0] / NM)} {number(s[1] / NM)}) (end {number(e[0] / NM)} {number(e[1] / NM)})"
                f" (width {number(path['width'] / NM)}) (layer {quote(path['layer'])}) (net {quote(path['net'])})"
                f" (uuid {quote(uid)}))").root)
            insert_before_tail(board.root, node)

    @staticmethod
    def _values(path: dict[str, Any]) -> dict[str, Any]:
        out = {"exists": True, "net": path["net"], "layer": path["layer"], "width_mm": base.mm(path["width"])}
        points = path["points"]
        if path["form"] == "segment":
            (x1, y1), (x2, y2) = points
            return {**out, "x1_mm": base.mm(x1), "y1_mm": base.mm(y1), "x2_mm": base.mm(x2), "y2_mm": base.mm(y2)}
        return {**out, "points": as_points(points)}

    def preview(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        board = load_for_write(doc)
        before_text = board.document.dumps()
        paths = self._paths(doc, board)
        for path in paths:
            self._insert(board, path)
        check = pcb_edit_drc.precheck(doc, before_text, board.document.dumps())
        targets, changes = [], []
        for path in paths:
            key = track_key(doc, path["id"])
            targets.append({"key": key, "kind": "track", "label": path["id"]})
            changes += [{"target": key, "field": f, "before": False if f == "exists" else None, "after": v}
                        for f, v in self._values(path).items()]
        segments = sum(len(p["ids"]) for p in paths)
        what = (f"a {base.mm(paths[0]['width']):g} mm track on {paths[0]['layer']} for net {paths[0]['net']}"
                if len(paths) == 1 else f"{len(paths)} tracks")
        out = {"action": f"Add {what} ({segments} segment(s)) to {doc.name}. {check.summary()}.",
               "targets": targets, "changes": changes, "can_apply": not check.blocked}
        if check.blocked:
            out["blocked_by"] = check.blocked_by()
        return out

    def apply(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        base.refuse_if_open(doc)
        board = load_for_write(doc)
        paths = self._paths(doc, board)
        for path in paths:
            self._insert(board, path)
        base.write_text(doc, board.document.dumps())
        # The runtime applies a batch one item at a time (DCLI-064), so this is one path.
        return base.saved(self._as_stored(load(doc), paths[0]))

    @staticmethod
    def _as_stored(board: Board, path: dict[str, Any]) -> dict[str, Any]:
        """The track as the file now holds it, with what its two ends touch (DCLI-068)."""
        if len(path["ids"]) == 1:
            return pcb_edit_links.track_ends(board, path["id"]) or {"id": path["id"]}
        first = pcb_edit_links.track_ends(board, path["ids"][0], path["ids"][1:]) or {}
        last = pcb_edit_links.track_ends(board, path["ids"][-1], path["ids"][:-1]) or {}
        ends = {"start": first.get("start"), "end": last.get("end")}
        return {"id": path["id"], "net": path["net"], "layer": path["layer"], "width_mm": base.mm(path["width"]),
                "segments": path["ids"], **ends,
                "unconnected_ends": [n for n in ("start", "end") if not (ends[n] or {}).get("connects_to")]}

    def read_back(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        board = load(doc)
        by_id = {identity(t.node): t for t in board.tracks if t.node is not None}
        observed: dict[str, dict[str, Any]] = {}
        for key in self.call.targets:
            uid = _fragment(key)[len("/tracks/"):]
            first = by_id.get(uid)
            if first is None:
                observed[key] = {"exists": False}
                continue
            chain = [first]
            while (nxt := by_id.get(derived_uuid("track", uid, len(chain)))) is not None:
                chain.append(nxt)
            points = [chain[0].start] + [t.end for t in chain]
            (x1, y1), (x2, y2) = first.start, first.end
            observed[key] = {"exists": True, "net": first.net, "layer": first.layer, "width_mm": base.mm(first.width),
                             "x1_mm": base.mm(x1), "y1_mm": base.mm(y1), "x2_mm": base.mm(x2), "y2_mm": base.mm(y2),
                             "points": as_points(points)}
        return {"observed": observed}


class TrackDelete(base.Handler):
    @staticmethod
    def _node(board: Board, uid: str) -> List:
        found = [n for n in board.root.lists() if n.head in ("segment", "arc", "via") and identity(n) == uid]
        if not found:
            raise base.not_found(f"The board has no track or via {uid}.")
        node = found[0]
        if node.head == "arc":
            raise base.unsupported(f"{uid} is an arc; kicad-cli deletes only straight segments and vias.")
        if _locked(node):
            kind = "via" if node.head == "via" else "segment"
            raise base.invalid(f"The {kind} {uid} is locked; unlock it in KiCad first.")
        return node

    def preview(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        board = load_for_write(doc)
        targets, changes = [], []
        vias = 0
        for uid in uuids(self.call.inputs):
            via = self._node(board, uid).head == "via"
            vias += via
            key = via_key(doc, uid) if via else track_key(doc, uid)
            targets.append({"key": key, "kind": "via" if via else "track", "label": uid})
            changes.append({"target": key, "field": "exists", "before": True, "after": False})
        what = f"{len(targets) - vias} track segment(s)" + (f" and {vias} via(s)" if vias else "")
        return {"action": f"Delete {what} from {doc.name}.", "targets": targets, "changes": changes, "can_apply": True}

    def apply(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        base.refuse_if_open(doc)
        board = load_for_write(doc)
        ids = uuids(self.call.inputs)
        nodes = {uid: self._node(board, uid) for uid in ids}
        was = {uid: (pcb_edit_links.via_links(board, uid) if nodes[uid].head == "via"
                     else pcb_edit_links.track_ends(board, uid)) for uid in ids}
        for uid in ids:
            board.root.remove(nodes[uid])
        base.write_text(doc, board.document.dumps())
        return base.saved({"deleted": [self._was(uid, nodes[uid].head, was[uid]) for uid in ids],
                           "note": "connects_to lists what each deleted segment's ends, or each deleted via, touched; "
                                   "those connections are gone."})

    @staticmethod
    def _was(uid: str, kind: str, item: dict[str, Any] | None) -> dict[str, Any]:
        if item is None:
            return {"id": uid, "type": kind}
        return {"type": kind, **{k: v for k, v in item.items() if k != "unconnected_ends"}}

    def read_back(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        board = load(doc)
        present = {identity(n) for n in board.root.lists() if n.head in ("segment", "arc", "via")}
        return {"observed": {key: {"exists": _fragment(key).rsplit("/", 1)[-1] in present} for key in self.call.targets}}


HANDLERS = {
    "netlist import": NetlistImport,
    "footprint move": FootprintMove,
    "track create": TrackCreate,
    "track delete": TrackDelete,
}
