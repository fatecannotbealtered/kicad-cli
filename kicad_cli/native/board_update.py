"""``board update``, in this process: carry the schematic onto its board.

What KiCad's "Update PCB from Schematic" does with the options its dialog
opens with (`commands/sync.py`, `DEFAULTS`), without opening it -- the
changes `sch sync-preview` says it would make, made:

- A part is matched to its footprint by the uuid path the footprint carries,
  as KiCad matches them; a part no footprint matches is added, a footprint
  no part matches is left where it is and said to be.
- A matched footprint takes its part's reference and value, the fields the
  part has, its "do not populate" and "exclude from BOM" marks, and -- when
  the part names another footprint -- that footprint from the libraries, put
  where the old one was, at its angle, with the old reference and value text
  where they were.
- Every pad takes its pin's net. A net whose pads all moved to one other
  name is renamed on the copper too -- its tracks, vias and zones -- so a
  net renamed in the schematic keeps its routing. A net whose pads went
  different ways leaves its copper on the old name, and is listed.
- A new footprint goes beside the board, right of the outline, for placing.

The footprints come from the project's library tables, as KiCad finds them
(`fileformat/lib_tables.py`). Nothing here runs KiCad. Before anything is
written the board is read back and held to the schematic: every part's
footprint with its reference, value and footprint, every pad on its pin's
net, and the copper as it was but for the renames.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import envelope, kicad_env, netlist
from ..fileformat import lib_tables
from ..fileformat import netlist as built_netlist
from ..fileformat.board import Board, BoardError, Footprint
from ..fileformat.circuit import Design
from ..fileformat.schematic import SchematicError
from ..fileformat.sexpr import Document, List, SexprError, copy, number, quote, symbol
from . import from_netlist

NM = 1_000_000
# Beside the board: how far right of the outline new footprints start, and
# the gap between them.
ASIDE_MM = 10.0
GAP_MM = 3.0


# -- what the board has --------------------------------------------------------------------


def _records(board: Board) -> list[dict[str, Any]]:
    """Each footprint as `sch sync-preview` reads it, with its node."""
    out = []
    for fp in board.footprints:
        node = fp.node
        path = node.find("path")
        attr = node.find("attr")
        out.append(
            {
                "ref": fp.reference,
                "value": fp.value,
                "fpid": fp.library_id,
                "path": netlist.normalise(path.value(1) if path is not None else ""),
                "fields": {p.value(1): p.value(2) or "" for p in node.find_all("property")},
                "board_only": attr is not None and "board_only" in attr.values(),
                "locked": fp.locked,
                "footprint": fp,
            }
        )
    return out


class Nets:
    """A board's nets as its version writes them: KiCad 9 numbers them in a
    table at the top and on each item; KiCad 10 names them on each item."""

    def __init__(self, board: Board) -> None:
        self.root = board.root
        self.codes: dict[str, str] = {name: code for code, name in board._net_names.items()}
        self.numbered = bool(self.codes)

    def code(self, name: str) -> str:
        if name not in self.codes:
            number_ = max((int(c) for c in self.codes.values()), default=0) + 1
            entry = List.new("net", str(number_), quote(name))
            last = [n for n in self.root.lists() if n.head == "net"][-1]
            self.root.insert(self.root.items.index(last) + 1, entry)
            self.codes[name] = str(number_)
        return self.codes[name]

    def on_pad(self, pad: List, name: str, board: Board) -> None:
        if board.net_of(pad) == name:
            return  # already so: the pad is left as it was written
        found = pad.find("net")
        if not name:
            if found is not None:
                pad.remove(found)
            return
        entry = (
            List.new("net", self.code(name), quote(name))
            if self.numbered
            else List.new("net", quote(name))
        )
        if found is not None:
            pad.replace(found, entry)
            return
        uuid = pad.find("uuid")
        if uuid is not None:
            pad.insert(pad.items.index(uuid), entry)
        else:
            pad.append(entry)

    def on_copper(self, node: List, name: str) -> None:
        """A track's, via's or zone's net, renamed."""
        found = node.find("net")
        if found is None:
            return
        if self.numbered:
            node.replace(found, List.new("net", self.code(name)))
            net_name = node.find("net_name")
            if net_name is not None:
                node.replace(net_name, List.new("net_name", quote(name)))
        else:
            node.replace(found, List.new("net", quote(name)))


# -- the update ------------------------------------------------------------------------------


@dataclass
class Update:
    board: Board
    nets: Nets
    rows: dict[str, list] = field(default_factory=lambda: defaultdict(list))
    # What KiCad's dialog would report as an error and pass over: a footprint
    # no library has. The rest of the update is made, as KiCad makes it.
    problems: list[dict[str, Any]] = field(default_factory=list)
    # What must read back.
    expect_pads: dict[tuple[str, str], str] = field(default_factory=dict)  # (ref, pad) -> net
    expect_parts: dict[str, dict[str, str]] = field(default_factory=dict)
    renamed: dict[str, str] = field(default_factory=dict)  # copper: old net -> new


def _property(node: List, name: str) -> List | None:
    return next((p for p in node.find_all("property") if p.value(1) == name), None)


def _set_property(node: List, name: str, value: str, ids) -> str:
    """A footprint's field, set; a new one hidden on the fab layer, as KiCad
    adds the fields of a part."""
    prop = _property(node, name)
    if prop is not None:
        prop.set(2, quote(value))
        return "changed"
    new = from_netlist._empty_property(name, ids("field", name))
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


def _library(tables: lib_tables.Tables, fpid: str) -> Path | None:
    nickname, _, name = fpid.partition(":")
    library = tables.footprints.get(nickname)
    if library is None or library.kind != "KiCad":
        return None
    candidate = library.path / f"{name}.kicad_mod"
    return candidate if candidate.is_file() else None


def _turned(node: List, angle: float) -> None:
    """A footprint just placed at angle 0, turned: KiCad writes each pad's and
    text's angle as it ends up on the board."""
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
        child.replace(
            place, List.new("at", place.atom(1), place.atom(2), number((old + angle) % 360))
        )


def _replace(update: Update, record: dict, part: dict, file: Path, ids) -> List | None:
    """The part's footprint from the library, where the old one was."""
    fp: Footprint = record["footprint"]
    if fp.layer != "F.Cu":
        update.problems.append(
            {
                "ref": part["ref"],
                "problem": "the footprint is on the bottom, and a footprint is not yet "
                "replaced on the bottom: replace it in KiCad",
                "from": fp.library_id,
                "to": part["fpid"],
            }
        )
        return None
    item = {
        "file": str(file),
        "ref": part["ref"],
        "value": part["value"],
        "x": fp.position[0] / NM,
        "y": fp.position[1] / NM,
    }
    new = from_netlist._footprint(item, ids)
    new.set(1, quote(part["fpid"]))
    _turned(new, fp.angle)
    old = fp.node
    # The old one's identity and its link, and where its text was.
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
    # Fields the old one had and the library's does not: kept.
    for prop in old.find_all("property"):
        name = prop.value(1)
        if _property(new, name) is None:
            last = new.find_all("property")[-1]
            new.insert(new.items.index(last) + 1, copy(prop))
    update.board.root.replace(old, new)
    return new


def _aside(board: Board) -> tuple[float, float]:
    box = board.edge_bbox()
    if box is None:
        boxes = [b for fp in board.footprints if (b := fp.bbox()) is not None]
        box = (
            (min(b[0] for b in boxes), min(b[1] for b in boxes),
             max(b[2] for b in boxes), max(b[3] for b in boxes))
            if boxes else (0, 0, 0, 0)
        )  # fmt: skip
    return (box[2] / NM + ASIDE_MM, box[1] / NM)


def _net_of(nets: list[built_netlist.NetEntry]) -> dict[tuple[str, str], str]:
    return {(node.ref, node.pin): net.name for net in nets for node in net.nodes}


def plan(board_path: Path, schematic: Path, kicad_root: str | None) -> tuple[Update, str]:
    """The board updated in memory, and the report of what changed."""
    from ..commands import sync  # noqa: PLC0415

    try:
        board = Board.load(board_path)
    except (BoardError, SexprError, UnicodeDecodeError, OSError) as exc:
        envelope.fail("E_VALIDATION", "the board could not be read", {"reason": str(exc)[:300]})
        raise AssertionError("unreachable") from exc
    found = netlist.read(schematic)
    try:
        built = built_netlist.build(Design(schematic))
    except (SchematicError, SexprError, UnicodeDecodeError, OSError) as exc:
        envelope.fail("E_VALIDATION", "the schematic could not be read", {"reason": str(exc)})
        raise AssertionError("unreachable") from exc
    comps = found.components
    records = _records(board)
    checks = sync._preflight(board_path, comps, records, found)
    blocking = [
        c for c in checks
        if not c["pass"]
        and c["id"] in {"fully_annotated", "netlist_exported_cleanly", "no_duplicate_references"}
    ]  # fmt: skip
    if blocking:
        envelope.fail(
            "E_VALIDATION",
            "the schematic is not in a state KiCad would update a board from",
            {"failed": blocking},
        )
    pairs, unmatched, orphans = sync._match(comps, records, sync.DEFAULTS["lookup_by_timestamp"])
    rows = sync._rows(comps, records, sync.DEFAULTS)
    tables = lib_tables.Tables.of(
        board_path.resolve().parent, Path(kicad_root) if kicad_root else None
    )
    ids = from_netlist._Ids(hashlib.sha256(board_path.read_bytes()).hexdigest())
    update = Update(board, Nets(board))
    pin_net = _net_of(built.nets)

    # Matched footprints: identity, fields, marks, and the footprint itself.
    placed: dict[str, List] = {}
    for ref, (part, record) in pairs.items():
        node = record["footprint"].node
        replacement = next((r for r in rows["change_footprint"] if r["ref"] == ref), None)
        if replacement is not None:
            file = _library(tables, part["fpid"])
            if file is None:
                update.problems.append(
                    {"ref": ref, "problem": "the part's footprint is in no library of the "
                     "project's tables", "footprint": part["fpid"]}
                )  # fmt: skip
            else:
                new = _replace(update, record, part, file, ids)
                if new is not None:
                    node = new
                    update.rows["change_footprint"].append(replacement)
        if record["ref"] != ref:
            _property(node, "Reference").set(2, quote(ref))
            update.rows["change_reference"].append({"from": record["ref"], "to": ref})
        if (
            _property(node, "Value") is not None
            and _property(node, "Value").value(2) != part["value"]
        ):
            update.rows["change_value"].append(
                {"ref": ref, "from": _property(node, "Value").value(2), "to": part["value"]}
            )
            _property(node, "Value").set(2, quote(part["value"]))
        # A field the part has with something in it, or one the footprint has.
        fields = {
            k: v for k, v in part["fields"].items()
            if k not in sync.FIELD_EXEMPT and (v != "" or k in record["fields"])
        }  # fmt: skip
        touched = {}
        for name, value in fields.items():
            prop = _property(node, name)
            if prop is None or (prop.value(2) or "") != value:
                touched[name] = _set_property(node, name, value, lambda *p, r=ref: ids(r, *p))
        if touched:
            update.rows["update_fields"].append({"ref": ref, "fields": touched})
        marks = {
            "dnp": "dnp" in part["properties"],
            "exclude_from_bom": "exclude_from_bom" in part["properties"],
        }
        changed = [w for w, on in marks.items() if _set_attr(node, w, on)]
        if changed:
            update.rows["change_marks"].append({"ref": ref, "marks": changed})
        placed[ref] = node
        # A footprint that could not be replaced stays what it was.
        update.expect_parts[ref] = {"value": part["value"], "fpid": node.value(1) or ""}

    # Parts no footprint matches: added beside the board.
    x, y = _aside(board)
    for part in unmatched:
        file = _library(tables, part["fpid"]) if part["fpid"] else None
        if file is None:
            update.problems.append(
                {"ref": part["ref"], "problem": "the part has no footprint the project's "
                 "tables find, so there is nothing to add", "footprint": part["fpid"]}
            )  # fmt: skip
            continue
        item = {"file": str(file), "ref": part["ref"], "value": part["value"], "x": x, "y": y}
        node = from_netlist._footprint(item, ids)
        node.set(1, quote(part["fpid"]))
        path = List.new("path", quote(part["paths"][0]))
        at = node.find("at")
        node.insert(node.items.index(at) + 1, path)
        for name, value in part["fields"].items():
            if name not in sync.FIELD_EXEMPT and value != "":
                _set_property(node, name, value, lambda *p, r=part["ref"]: ids(r, *p))
        if "dnp" in part["properties"]:
            _set_attr(node, "dnp", True)
        if "exclude_from_bom" in part["properties"]:
            _set_attr(node, "exclude_from_bom", True)
        from_netlist._insert_before_tail(board.root, node)
        placed[part["ref"]] = node
        update.expect_parts[part["ref"]] = {"value": part["value"], "fpid": part["fpid"]}
        size = _reader()._footprint(node).bbox()
        height = (size[3] - size[1]) / NM if size else 5.0
        update.rows["add"].append({"ref": part["ref"], "footprint": part["fpid"], "at_mm": [x, y]})
        y += height + GAP_MM

    # Every pad of a part onto its pin's net; and the copper of a net whose
    # pads all went one way, with them.
    before: dict[str, set[str]] = defaultdict(set)  # old net -> new nets of its pads
    kept_old: set[str] = set()  # old nets some pad keeps
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
            update.nets.on_pad(pad, name, update.board)
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
        if node.head in ("segment", "arc", "via", "zone"):
            name = board.net_of(node)
            if name in update.renamed:
                update.nets.on_copper(node, update.renamed[name])
    update.rows["renamed_nets"] = [{"from": a, "to": b} for a, b in sorted(update.renamed.items())]
    update.rows["copper_left_on_old_nets"] = split
    update.rows["not_removed"] = rows["not_removed"]
    return update, board.document.dumps()


def _reader() -> Board:
    """A board with nothing on it, to read a footprint not on one yet."""
    from ..fileformat.new_board import EMPTY  # noqa: PLC0415

    return Board(Document.parse(EMPTY))


def verify(update: Update, text: str, before: Board) -> dict[str, Any]:
    """The updated board, read back, against what it had to say."""
    after = Board(Document.parse(text))
    by_ref: dict[str, list[Footprint]] = defaultdict(list)
    for fp in after.footprints:
        by_ref[fp.reference].append(fp)
    parts = []
    for ref, want in update.expect_parts.items():
        found = by_ref.get(ref, [])
        if len(found) != 1:
            parts.append({"ref": ref, "footprints": len(found)})
            continue
        fp = found[0]
        if fp.value != want["value"] or fp.library_id != want["fpid"]:
            parts.append(
                {"ref": ref, "value": fp.value, "footprint": fp.library_id, "wanted": want}
            )
    pads = []
    for fp in after.footprints:
        for pad in fp.pads:
            want = update.expect_pads.get((fp.reference, pad.number))
            if want is not None and pad.net != want:
                pads.append({"pad": f"{fp.reference}.{pad.number}", "net": pad.net, "wanted": want})
    copper = []
    old_tracks = [(t.kind, t.start, t.end, t.layer, t.net) for t in before.tracks]
    new_tracks = [(t.kind, t.start, t.end, t.layer, t.net) for t in after.tracks]
    expected_tracks = [(k, s, e, lay, update.renamed.get(n, n)) for k, s, e, lay, n in old_tracks]
    if sorted(map(repr, expected_tracks)) != sorted(map(repr, new_tracks)):
        copper.append("tracks")
    if len(before.vias) != len(after.vias) or sorted(
        update.renamed.get(v.net, v.net) for v in before.vias
    ) != sorted(v.net for v in after.vias):
        copper.append("vias")
    if len(before.zones) != len(after.zones):
        copper.append("zones")
    return {
        "parts_read_back": not parts,
        "pads_on_their_nets": not pads,
        "copper_as_it_was_but_renamed": not copper,
        "problems": {"parts": parts[:10], "pads": pads[:10], "copper": copper},
    }


def passed(verified: dict[str, Any]) -> bool:
    return all(v for v in verified.values() if isinstance(v, bool))


def run(board_path: Path, schematic: Path) -> tuple[dict[str, Any], str | None]:
    kicad_root = kicad_env.find_kicad_root()
    before = Board.load(board_path)
    update, text = plan(board_path, schematic, kicad_root)
    report = {
        "board": str(board_path),
        "schematic": str(schematic),
        "counts": {k: len(v) for k, v in update.rows.items()},
        "changes": {k: v[:20] for k, v in update.rows.items()},
        "problems": update.problems[:20],
        "verified": verify(update, text, before),
        "inputs": {
            str(board_path): hashlib.sha256(board_path.read_bytes()).hexdigest(),
            **{
                str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in Design(schematic).schematics
            },
        },
    }
    changed = text.encode("utf-8") != board_path.read_bytes()
    return report, text if changed else None


def not_checked() -> list[str]:
    return [
        "DRC was not run: a replaced footprint's pads may now overlap copper laid for the old "
        "one, and new footprints wait beside the board, not on it",
        "footprints no part matches are left on the board, as KiCad's dialog leaves them "
        "with 'delete extra footprints' unticked; they are listed under not_removed",
        "parts are matched to footprints by uuid path, as the dialog opens: a footprint whose "
        "link is broken is not matched by its reference -- run `sch relink` first",
        "a footprint on the bottom of the board is not yet replaced",
    ]
