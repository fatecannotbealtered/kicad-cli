"""netlist import: carry the schematic onto its board, as KiCad's "Update PCB from Schematic".

The schematic is read through a fresh netlist that KiCad's own program exports. Then the
board is updated with the options KiCad's dialog opens with:

- A part is matched to its footprint by the uuid path the footprint carries, as KiCad
  matches them. A part no footprint matches is added beside the board, right of the
  outline, for placing. A footprint no part matches stays where it is and is listed: it is
  never deleted.
- A matched footprint takes its part's reference and value, the fields the part has, its
  "do not populate" and "exclude from BOM" marks, and, when the part names another
  footprint, that footprint from the project's libraries, put where the old one was, at its
  angle, with the old reference and value text where they were (front side only).
- Every pad takes its pin's net. A net whose pads all moved to one other name is renamed on
  the copper too (tracks, vias and zones), so a net renamed in the schematic keeps its
  routing. A net whose pads went different ways leaves its copper on the old name, listed.

Ported from the previous implementation (``native/board_update.py``, ``commands/sync.py``,
``native/from_netlist.py``), which matched KiCad's updater on its demo projects.
"""
from __future__ import annotations

import uuid as uuidlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import base
from .fileformat import lib_tables
from .fileformat.board import Board, Footprint
from .fileformat.new_board import EMPTY
from .fileformat.sexpr import Document, List, SexprError, copy, number, quote, symbol, text
from .pcb_edit_board import insert_before_tail

NM = 1_000_000
ASIDE_MM = 10.0  # how far right of the outline new footprints start
GAP_MM = 3.0  # and the gap between them
FIELD_EXEMPT = {"Reference", "Value", "Footprint"}  # handled by replacement, not as fields
WITH_UUID = {
    "property", "fp_text", "fp_text_box", "fp_line", "fp_arc", "fp_circle", "fp_rect",
    "fp_poly", "fp_curve", "pad", "zone", "dimension", "group", "image",
}
DROPPED = {"version", "generator", "generator_version"}
COPPER = ("segment", "arc", "via", "zone")


# -- the schematic, through KiCad's netlist -------------------------------------------------


@dataclass
class Schematic:
    components: list[dict[str, Any]]
    pin_net: dict[tuple[str, str], str]


def join_path(sheet: str, uuid: str) -> str:
    """The footprint ``path`` KiCad builds: the sheet path plus the symbol uuid."""
    return "/" + "/".join(x for x in f"{sheet}/{uuid}".split("/") if x)


def normalise(path: str) -> str:
    return "/" + "/".join(x for x in str(path).split("/") if x) if path else ""


def _value(node: List | None, name: str, default: str = "") -> str:
    found = node.find(name) if node is not None else None
    return (found.value(1) or default) if found is not None else default


def read_netlist(source: str) -> Schematic:
    """The parts and pin nets of a KiCad netlist (S-expression export)."""
    try:
        root = Document.parse(source, lazy=False).root
    except SexprError as exc:
        raise base.AdapterError("E_NATIVE_FAILURE", f"KiCad's netlist cannot be read: {exc}.",
                                dispatch_state="not_started") from None
    comps = []
    for comp in root.walk("comp"):
        ref = _value(comp, "ref")
        if not ref:
            continue
        stamps = comp.find("tstamps")
        uuids = [u for u in (stamps.values() if stamps is not None else []) if u]
        sheetpath = comp.find("sheetpath")
        sheet = _value(sheetpath, "tstamps", "/") if sheetpath is not None else "/"
        fields: dict[str, str] = {}
        fnode = comp.find("fields")
        for f in fnode.find_all("field") if fnode is not None else []:
            name = _value(f, "name")
            if name:
                rest = [v for v in f.items[2:] if isinstance(v, str)]
                fields[name] = text(rest[0]) if rest else ""
        props: dict[str, str] = {}
        for p in comp.find_all("property"):
            name = _value(p, "name")
            if name:
                props[name] = _value(p, "value")
        comps.append({
            "ref": ref, "value": _value(comp, "value"), "fpid": _value(comp, "footprint"), "sheet": sheet,
            "uuids": uuids, "paths": [join_path(sheet, u) for u in uuids], "fields": fields, "properties": props,
        })
    pin_net: dict[tuple[str, str], str] = {}
    for net in root.walk("net"):
        name = _value(net, "name")
        for node in net.find_all("node"):
            pin_net[(_value(node, "ref"), _value(node, "pin"))] = name
    return Schematic(comps, pin_net)


def check_annotation(schematic: Schematic) -> None:
    """Refuse a schematic KiCad would not update a board from."""
    comps = schematic.components
    if not comps:
        raise base.invalid("KiCad's netlist of the schematic has no parts.")
    unannotated = sorted({c["ref"] for c in comps if "?" in c["ref"]})
    if unannotated:
        raise base.invalid("The schematic is not fully annotated: " + ", ".join(unannotated[:10])
                           + ". Annotate it in KiCad first.")
    seen: dict[tuple[str, str], int] = defaultdict(int)
    for c in comps:
        seen[(c["sheet"], c["ref"])] += 1
    dupes = sorted(r for (_, r), n in seen.items() if n > 1)
    if dupes:
        raise base.invalid("The schematic has duplicate references: " + ", ".join(dupes[:10]) + ".")


# -- matching, as KiCad's updater ----------------------------------------------------------


def _records(board: Board) -> list[dict[str, Any]]:
    out = []
    for fp in board.footprints:
        node = fp.node
        path = node.find("path")
        attr = node.find("attr")
        out.append({
            "ref": fp.reference, "value": fp.value, "fpid": fp.library_id,
            "path": normalise(path.value(1) if path is not None else ""),
            "fields": {p.value(1): p.value(2) or "" for p in node.find_all("property")},
            "board_only": attr is not None and "board_only" in attr.values(),
            "locked": fp.locked, "footprint": fp,
        })
    return out


def _fpid_equivalent(board_id: str, sch_id: str) -> bool:
    """A library-less id matches on the bare footprint name, as KiCad treats it."""
    if ":" not in sch_id:
        return board_id.split(":", 1)[-1] == sch_id
    return board_id == sch_id


def _match(comps: list[dict], fps: list[dict]) -> tuple[dict, list, list]:
    """Pair parts with footprints by uuid path, as the updater does by default."""
    pairs: dict[str, tuple[dict, dict]] = {}
    taken: set[int] = set()
    unmatched = []
    by_path = {f["path"]: f for f in fps if f["path"]}
    for c in comps:
        hit = next((by_path[p] for p in c["paths"] if p in by_path), None)
        if hit is None or id(hit) in taken:
            unmatched.append(c)
        else:
            taken.add(id(hit))
            pairs[c["ref"]] = (c, hit)
    orphans = [f for f in fps if id(f) not in taken]
    return pairs, unmatched, orphans


# -- footprints from the libraries ---------------------------------------------------------


class Ids:
    """Uuids that follow from what the board is made of, not from chance."""

    def __init__(self, seed: str) -> None:
        self.namespace = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "kicad-cli/board/" + seed)

    def __call__(self, *parts: object) -> str:
        return quote(str(uuidlib.uuid5(self.namespace, "/".join(str(p) for p in parts))))


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
    """A field the library footprint does not have, as KiCad adds it."""
    return List.new(
        "property", quote(name), quote(""), List.new("at", "0", "0", "0"), List.new("layer", quote("F.Fab")),
        List.new("hide", symbol("yes")), List.new("uuid", uuid),
        List.new("effects", List.new("font", List.new("size", "1.27", "1.27"))),
    )


def library_footprint(item: dict[str, Any], ids: Ids) -> List:
    """A library footprint as a placed one: named, positioned, given uuids."""
    try:
        source = Document.load(item["file"]).root
    except (OSError, ValueError) as exc:
        raise base.AdapterError("E_NATIVE_FAILURE", f"The footprint file {item['file']} cannot be read: {exc}.",
                                dispatch_state="not_started") from None
    ref = item["ref"]
    node = List.new("footprint", quote(item["footprint"]))
    children = [c for c in source.items[2:] if not (isinstance(c, List) and c.head in DROPPED)]
    have = {c.value(1) for c in children if isinstance(c, List) and c.head == "property"}
    at = List.new("at", number(int(round(item["x"] * NM)) / NM), number(int(round(item["y"] * NM)) / NM))
    counters: dict[str, int] = {}
    for child in children:
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
    return node


def _library(tables: lib_tables.Tables, fpid: str) -> Path | None:
    nickname, _, name = fpid.partition(":")
    library = tables.footprints.get(nickname)
    if library is None or library.kind != "KiCad":
        return None
    candidate = library.path / f"{name}.kicad_mod"
    return candidate if candidate.is_file() else None


# -- the update ----------------------------------------------------------------------------


@dataclass
class Update:
    board: Board
    rows: dict[str, list] = field(default_factory=lambda: defaultdict(list))
    problems: list[dict[str, Any]] = field(default_factory=list)
    expect_pads: dict[tuple[str, str], str] = field(default_factory=dict)  # (ref, pad) -> net
    expect_parts: dict[str, dict[str, Any]] = field(default_factory=dict)
    added: list[str] = field(default_factory=list)
    old_of: dict[str, dict[str, Any]] = field(default_factory=dict)  # new ref -> board record before
    renamed: dict[str, str] = field(default_factory=dict)  # copper: old net -> new


def _set_net(pad: List, name: str, board: Board) -> None:
    """A pad's net, as KiCad 10 writes it: (net "NAME") before its uuid; none for no net."""
    if board.net_of(pad) == name:
        return
    found = pad.find("net")
    if not name:
        if found is not None:
            pad.remove(found)
        return
    entry = List.new("net", quote(name))
    if found is not None:
        pad.replace(found, entry)
        return
    uuid = pad.find("uuid")
    if uuid is not None:
        pad.insert(pad.items.index(uuid), entry)
    else:
        pad.append(entry)


def _rename_copper(node: List, name: str) -> None:
    found = node.find("net")
    if found is not None:
        node.replace(found, List.new("net", quote(name)))


def _property(node: List, name: str) -> List | None:
    return next((p for p in node.find_all("property") if p.value(1) == name), None)


def _set_property(node: List, name: str, value: str, uuid: str) -> str:
    prop = _property(node, name)
    if prop is not None:
        prop.set(2, quote(value))
        return "changed"
    new = _empty_property(name, uuid)
    new.set(2, quote(value))
    last = node.find_all("property")[-1]
    node.insert(node.items.index(last) + 1, new)
    return "added"


def _set_attr(node: List, word: str, on: bool) -> bool:
    attr = node.find("attr")
    have = attr.values() if attr is not None else []
    if (word in have) == on:
        return False
    if attr is None:
        attr = List.new("attr")
        at = node.find("at")
        node.insert(node.items.index(at) + 1 if at is not None else len(node.items), attr)
    if on:
        attr.append(symbol(word))
    else:
        for index, item in enumerate(attr.items):
            if item == word:
                attr.pop(index)
                break
    return True


def _turned(node: List, angle: float) -> None:
    """A footprint placed at angle 0, turned: KiCad writes each pad's and text's final angle."""
    if not angle:
        return
    at = node.find("at")
    node.replace(at, List.new("at", at.atom(1), at.atom(2), number(angle)))
    for child in node.lists():
        if child.head not in ("pad", "property", "fp_text"):
            continue
        place = child.find("at")
        if place is None:
            continue
        old = float(place.atom(3)) if place.atom(3) is not None else 0.0
        child.replace(place, List.new("at", place.atom(1), place.atom(2), number((old + angle) % 360)))


def _replace(update: Update, record: dict, part: dict, file: Path, ids: Ids) -> List | None:
    """The part's footprint from the library, where the old one was."""
    fp: Footprint = record["footprint"]
    if fp.layer != "F.Cu":
        update.problems.append({"ref": part["ref"], "problem": "the footprint is on the back side, where kicad-cli "
                                "does not replace footprints; replace it in KiCad", "from": fp.library_id,
                                "to": part["fpid"]})
        return None
    item = {"file": str(file), "footprint": part["fpid"], "ref": part["ref"], "value": part["value"],
            "x": fp.position[0] / NM, "y": fp.position[1] / NM}
    new = library_footprint(item, ids)
    _turned(new, fp.angle)
    old = fp.node
    for head in ("uuid", "path", "sheetname", "sheetfile", "locked"):
        kept = old.find(head)
        if kept is None:
            continue
        mine = new.find(head)
        if mine is not None:
            new.replace(mine, copy(kept))
        else:
            at = new.find("at")
            new.insert(new.items.index(at) + 1, copy(kept))
    for name in ("Reference", "Value"):
        before, after = _property(old, name), _property(new, name)
        if before is None or after is None:
            continue
        for head in ("at", "layer", "hide", "effects", "unlocked"):
            was, now = before.find(head), after.find(head)
            if now is not None:
                after.remove(now)
            if was is not None:
                uuid = after.find("uuid")
                index = after.items.index(uuid) if uuid is not None else len(after.items)
                after.insert(index, copy(was))
    for prop in old.find_all("property"):
        if _property(new, prop.value(1)) is None:
            last = new.find_all("property")[-1]
            new.insert(new.items.index(last) + 1, copy(prop))
    update.board.root.replace(old, new)
    return new


def _aside(board: Board) -> tuple[float, float]:
    box = board.edge_bbox()
    if box is None:
        boxes = [b for fp in board.footprints if (b := fp.bbox()) is not None]
        box = ((min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes),
                max(b[3] for b in boxes)) if boxes else (0, 0, 0, 0))
    return (box[2] / NM + ASIDE_MM, box[1] / NM)


def plan(board: Board, schematic: Schematic, tables: lib_tables.Tables, seed: str) -> Update:
    """The board updated in memory, with what changed and what must read back."""
    comps = schematic.components
    records = _records(board)
    pairs, unmatched, orphans = _match(comps, records)
    ids = Ids(seed)
    update = Update(board)
    pin_net = schematic.pin_net

    placed: dict[str, List] = {}
    for ref, (part, record) in pairs.items():
        update.old_of[ref] = record
        node = record["footprint"].node
        if not _fpid_equivalent(str(record["fpid"]), part["fpid"]) and part["fpid"]:
            file = _library(tables, part["fpid"])
            if file is None:
                update.problems.append({"ref": ref, "problem": "the part's footprint is in no library of the "
                                        "project's tables", "footprint": part["fpid"]})
            else:
                new = _replace(update, record, part, file, ids)
                if new is not None:
                    node = new
                    update.rows["change_footprint"].append({"ref": ref, "from": record["fpid"], "to": part["fpid"]})
        if record["ref"] != ref:
            _property(node, "Reference").set(2, quote(ref))
            update.rows["change_reference"].append({"from": record["ref"], "to": ref})
        value = _property(node, "Value")
        if value is not None and value.value(2) != part["value"]:
            update.rows["change_value"].append({"ref": ref, "from": value.value(2), "to": part["value"]})
            value.set(2, quote(part["value"]))
        fields = {k: v for k, v in part["fields"].items()
                  if k not in FIELD_EXEMPT and (v != "" or k in record["fields"])}
        touched = {}
        for name, wanted in fields.items():
            prop = _property(node, name)
            if prop is None or (prop.value(2) or "") != wanted:
                touched[name] = _set_property(node, name, wanted, ids(ref, "field", name))
        if touched:
            update.rows["update_fields"].append({"ref": ref, "fields": touched})
        marks = {"dnp": "dnp" in part["properties"], "exclude_from_bom": "exclude_from_bom" in part["properties"]}
        changed = [w for w, on in marks.items() if _set_attr(node, w, on)]
        if changed:
            update.rows["change_marks"].append({"ref": ref, "marks": changed})
        placed[ref] = node
        update.expect_parts[ref] = {"value": part["value"], "fpid": node.value(1) or "", "fields": fields,
                                    "dnp": marks["dnp"], "exclude_from_bom": marks["exclude_from_bom"]}

    x, y = _aside(board)
    reader = Board(Document.parse(EMPTY))
    for part in unmatched:
        file = _library(tables, part["fpid"]) if part["fpid"] else None
        if file is None:
            update.problems.append({"ref": part["ref"], "problem": "the part has no footprint the project's tables "
                                    "find, so there is nothing to add", "footprint": part["fpid"]})
            continue
        item = {"file": str(file), "footprint": part["fpid"], "ref": part["ref"], "value": part["value"], "x": x, "y": y}
        node = library_footprint(item, ids)
        if part["paths"]:
            at = node.find("at")
            node.insert(node.items.index(at) + 1, List.new("path", quote(part["paths"][0])))
        fields = {k: v for k, v in part["fields"].items() if k not in FIELD_EXEMPT and v != ""}
        for name, wanted in fields.items():
            _set_property(node, name, wanted, ids(part["ref"], "field", name))
        marks = {"dnp": "dnp" in part["properties"], "exclude_from_bom": "exclude_from_bom" in part["properties"]}
        for word, on in marks.items():
            if on:
                _set_attr(node, word, True)
        insert_before_tail(board.root, node)
        placed[part["ref"]] = node
        update.added.append(part["ref"])
        update.expect_parts[part["ref"]] = {"value": part["value"], "fpid": part["fpid"], "fields": fields, **marks}
        size = reader._footprint(node).bbox()
        height = (size[3] - size[1]) / NM if size else 5.0
        update.rows["add"].append({"ref": part["ref"], "footprint": part["fpid"], "x_mm": round(x, 6),
                                   "y_mm": round(y, 6)})
        y += height + GAP_MM

    before: dict[str, set[str]] = defaultdict(set)  # old net -> new nets of its pads
    kept_old: set[str] = set()
    matched_to = {id(rec): ref for ref, (_, rec) in pairs.items()}
    for record in records:
        matched = matched_to.get(id(record))
        for pad in record["footprint"].pads:
            if not pad.net:
                continue
            if matched is None:
                kept_old.add(pad.net)
                continue
            before[pad.net].add(pin_net.get((matched, pad.number), ""))
    for ref, node in placed.items():
        for pad in node.find_all("pad"):
            name = pin_net.get((ref, pad.value(1) or ""), "")
            _set_net(pad, name, board)
            if pad.value(1):
                update.expect_pads[(ref, pad.value(1))] = name
    split = []
    for old, new in before.items():
        if len(new) == 1 and old not in kept_old:
            target = next(iter(new))
            if target and target != old:
                update.renamed[old] = target
        elif len(new) > 1:
            split.append({"net": old, "now": sorted(n for n in new if n)[:6]})
    for node in board.root.lists():
        if node.head in COPPER:
            name = board.net_of(node)
            if name in update.renamed:
                _rename_copper(node, update.renamed[name])
    update.rows["renamed_nets"] = [{"from": a, "to": b} for a, b in sorted(update.renamed.items())]
    update.rows["copper_left_on_old_nets"] = split
    update.rows["not_removed"] = [{"ref": f["ref"], "footprint": f["fpid"]} for f in orphans]
    return update


def copper_counts(board: Board) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for node in board.root.lists():
        if node.head in COPPER:
            counts[board.net_of(node)] += 1
    return counts
