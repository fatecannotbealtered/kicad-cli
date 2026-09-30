"""``sch edit``, in this process: change an existing schematic, and prove the change.

The changes are described, not drawn -- a part's fields, a net's name -- and
made in the files' own text, so everything else in them stays byte for byte
as it was. Before anything is written, the edited design is read back
through this tool's own connectivity and checked: every net joins the same
pins as before, a renamed net is called what was asked and no other net is
renamed, and every field reads back as set. An edit that would join two nets
or split one is refused, not written.

What each change touches:

- `set` changes a part's fields and flags, on every unit of it. A sheet
  placed more than once is one file, so a part on it is one symbol whatever
  its reference in each placement: the others change with it, and are named.
- `rename` changes what names a net: every local label of that name on its
  sheet; every global label, power symbol and local label joined by that
  name; or a local power symbol's value. A net named by a hierarchical label
  or a sheet pin is not renamed -- the name belongs to both sides of the
  sheet -- and one nothing names, "Net-(R1-Pad1)", has no name to change. A
  sheet placed twice is one drawing: renaming a label on it renames the net
  it names in every placement, and each is named.

The design's ERC -- this tool's own, `fileformat/erc.py` -- is run before and
after, and what the edit adds to it or clears is reported.
"""

from __future__ import annotations

import difflib
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import envelope, kicad_env
from ..fileformat import erc as rules
from ..fileformat.circuit import PRIORITY, Design, Net, bus_members, escape, grouped
from ..fileformat.schematic import Schematic, SchematicError, Symbol
from ..fileformat.sexpr import List, SexprError, copy, quote, symbol

OPS = ("set", "rename")
# Shorthand for the fields every part has.
FIELDS = {
    "value": "Value",
    "footprint": "Footprint",
    "datasheet": "Datasheet",
    "description": "Description",
}
MANDATORY = ("Reference", "Value", "Footprint", "Datasheet", "Description")
FLAGS = ("dnp", "in_bom", "on_board", "exclude_from_sim")
# KiCad 9 moved a field's (hide yes) out of its (effects ...).
HIDE_OUTSIDE_EFFECTS = 20241209
LABELS = ("label", "global_label", "hierarchical_label")


# -- reading the changes ---------------------------------------------------------------


def load_changes(path: str) -> list[dict[str, Any]]:
    """The changes file: {"changes": [...]}, or the list alone."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        envelope.fail("E_NOT_FOUND", "the changes file does not exist", {"changes": path})
        raise AssertionError("unreachable") from None
    except (OSError, UnicodeDecodeError) as exc:
        envelope.fail("E_IO", "the changes file cannot be read", {"changes": path})
        raise AssertionError("unreachable") from exc
    except ValueError as exc:
        envelope.fail(
            "E_VALIDATION", "the changes file is not JSON", {"changes": path, "reason": str(exc)}
        )
        raise AssertionError("unreachable") from exc
    changes = data.get("changes") if isinstance(data, dict) else data
    if not isinstance(changes, list) or not changes:
        envelope.fail(
            "E_VALIDATION",
            'the changes file must hold {"changes": [...]}, a list of at least one change',
            {"changes": path},
        )
    bad = [
        {"index": i, "problem": f"a change is an object whose op is one of: {', '.join(OPS)}"}
        for i, c in enumerate(changes)
        if not isinstance(c, dict) or c.get("op") not in OPS
    ]
    if bad:
        envelope.fail("E_VALIDATION", "the changes cannot be read", {"problems": bad[:40]})
    return changes


# -- what the design has -----------------------------------------------------------------


@dataclass
class _Part:
    """One reference: every symbol of it, each once, and each unit it is
    given where it is placed."""

    reference: str
    symbols: list[tuple[Schematic, Symbol]] = field(default_factory=list)
    units: list[int] = field(default_factory=list)


def _parts(design: Design) -> dict[str, _Part]:
    out: dict[str, _Part] = {}
    seen: set[tuple[str, str]] = set()
    for instance in design.instances:
        sch = instance.schematic
        for sym in sch.symbols:
            inst = sym.instance(instance.path)
            reference = inst.reference if inst else sym.properties.get("Reference", "")
            part = out.setdefault(reference, _Part(reference))
            part.units.append(inst.unit if inst else sym.unit)
            key = (str(sch.path), sym.uuid)
            if key not in seen:
                seen.add(key)
                part.symbols.append((sch, sym))
    return out


def _pins(net: Net) -> frozenset:
    return frozenset((pp.reference, pp.pin.number) for pp in net.pins)


def _nearest(word: str, choices) -> list[str]:
    return difflib.get_close_matches(word, list(choices), n=5, cutoff=0.5)


# -- set -------------------------------------------------------------------------------------


@dataclass
class Edit:
    design: Design
    report: list[dict[str, Any]] = field(default_factory=list)
    touched: dict[Path, Schematic] = field(default_factory=dict)
    # What must read back: by reference, each field's value (None: gone) and
    # each flag; by the pins of a net, the name it must have.
    fields: dict[str, dict[str, str | None]] = field(default_factory=dict)
    flags: dict[str, dict[str, bool]] = field(default_factory=dict)
    names: dict[frozenset, str] = field(default_factory=dict)

    def touch(self, sch: Schematic) -> None:
        self.touched[sch.path.resolve()] = sch


def _hidden_template(node: List, version: int) -> List:
    """A hidden field of this symbol to copy, so a new one is written as the
    file writes its own; failing that, one in the style of its version."""
    for prop in node.find_all("property"):
        effects = prop.find("effects")
        if prop.find("hide") is not None or (
            effects is not None
            # (hide yes) since KiCad 8, a bare `hide` before
            and (effects.find("hide") is not None or "hide" in effects.values())
        ):
            return prop
    made = List.new("property", quote(""), quote(""), List.new("at", "0", "0", "0"))
    effects = List.new("effects", List.new("font", List.new("size", "1.27", "1.27")))
    if version >= HIDE_OUTSIDE_EFFECTS:
        made.append(List.new("hide", symbol("yes")))
    else:
        effects.append(List.new("hide", symbol("yes")))
    made.append(effects)
    return made


def _set_field(sch: Schematic, sym: Symbol, name: str, value: str | None) -> None:
    node = sym.node
    found = [p for p in node.find_all("property") if p.value(1) == name]
    if value is None:
        for prop in found:
            node.remove(prop)
        return
    if found:
        found[0].set(2, quote(value))
        return
    new = copy(_hidden_template(node, sch.version))
    new.set(1, quote(name))
    new.set(2, quote(value))
    at, place = new.find("at"), node.find("at")
    if at is not None and place is not None:
        # At the symbol, level whatever the symbol's angle.
        new.replace(at, List.new("at", place.atom(1) or "0", place.atom(2) or "0", "0"))
    last = node.find_all("property")[-1]
    node.insert(node.items.index(last) + 1, new)


def _set_flag(sym: Symbol, name: str, on: bool) -> None:
    node = sym.node
    word = symbol("yes" if on else "no")
    found = node.find(name)
    if found is not None:
        found.set(1, word)
        return
    # Where KiCad writes it: with the other flags, after the unit.
    anchor = next(
        (node.find(f) for f in ("dnp", "on_board", "in_bom", "exclude_from_sim")
         if node.find(f) is not None),
        node.find("unit"),
    )  # fmt: skip
    index = node.items.index(anchor) + 1 if anchor is not None else len(node.items)
    node.insert(index, List.new(name, word))


def _set(edit: Edit, index: int, change: dict, parts: dict[str, _Part]) -> list[dict]:
    allowed = {"op", "ref", "fields", *FIELDS, *FLAGS}
    problems = [
        {
            "index": index,
            "problem": f"'{k}' is not something set changes",
            "allowed": sorted(allowed),
        }
        for k in sorted(set(change) - allowed)
    ]
    reference = change.get("ref")
    part = parts.get(reference) if isinstance(reference, str) else None
    if part is None:
        return problems + [
            {
                "index": index,
                "problem": "no part has that reference",
                "ref": reference,
                "nearest": _nearest(str(reference), (r for r in parts if not r.startswith("#"))),
            }
        ]
    if len(part.units) != len(set(part.units)):
        return problems + [
            {
                "index": index,
                "problem": "more than one part is annotated with this reference; annotate the "
                "design first",
                "ref": reference,
            }
        ]
    wanted: dict[str, str | None] = {FIELDS[k]: change[k] for k in FIELDS if k in change}
    given = change.get("fields", {})
    if not isinstance(given, dict):
        problems.append({"index": index, "problem": "fields is an object: {name: value}"})
        given = {}
    wanted.update(given)
    flags = {k: change[k] for k in FLAGS if k in change}
    for name, value in wanted.items():
        if value is not None and not isinstance(value, str):
            problems.append({"index": index, "problem": "a field's value is text", "field": name})
        elif name == "Reference":
            problems.append(
                {"index": index, "problem": "a reference is changed by annotating, not by set"}
            )
        elif value is None and name in MANDATORY:
            problems.append(
                {
                    "index": index,
                    "problem": "every part has this field; it can be emptied, not removed",
                    "field": name,
                }
            )
        elif not name.strip() or name.startswith("ki_") or any(c in name for c in "\r\n\t"):
            problems.append(
                {"index": index, "problem": "not a name a field can have", "field": name}
            )
    problems += [
        {"index": index, "problem": "a flag is true or false", "flag": k}
        for k, v in flags.items()
        if not isinstance(v, bool)
    ]
    if not wanted and not flags:
        problems.append({"index": index, "problem": "set changes nothing: give fields or flags"})
    if any(
        (lib := sch.library.get(sym.library_name)) is not None and lib.power
        for sch, sym in part.symbols
    ):
        problems.append(
            {
                "index": index,
                "problem": "a power symbol's value is the name of its net: rename the net instead",
                "ref": reference,
            }
        )
    if problems:
        return problems

    first = part.symbols[0][1]
    changed = {
        name: {"from": first.properties.get(name), "to": value}
        for name, value in wanted.items()
        if first.properties.get(name) != value
    }
    flag_changes = {
        name: {"from": getattr(first, name), "to": on}
        for name, on in flags.items()
        if getattr(first, name) != on
    }
    for sch, sym in part.symbols:
        for name, value in wanted.items():
            if sym.properties.get(name) != value:
                _set_field(sch, sym, name, value)
                edit.touch(sch)
        for name, on in flags.items():
            if getattr(sym, name) != on:
                _set_flag(sym, name, on)
                edit.touch(sch)
    edit.fields.setdefault(reference, {}).update(wanted)
    edit.flags.setdefault(reference, {}).update(flags)
    edit.report.append(
        {
            "index": index,
            "op": "set",
            "ref": reference,
            "units": len(part.symbols),
            "fields": changed,
            "flags": flag_changes,
            # The references another placement of the sheet gives these symbols.
            "also_changes": sorted(
                {
                    inst.reference
                    for _, sym in part.symbols
                    for inst in sym.instances
                    if inst.reference != reference
                }
            ),
            "files": sorted({sch.path.name for sch, _ in part.symbols}),
        }
    )
    return []


# -- rename ----------------------------------------------------------------------------------


def _kind(item, key) -> str | None:
    """What an item of the connectivity graph is, as naming a net goes."""
    if item is None:
        return None
    if key[0] == "l":
        return item.kind
    if key[0] == "s":
        return "sheet_pin"
    if key[0] == "p":
        lib = item.instance.schematic.library.get(item.symbol.library_name)
        if lib is not None and lib.power and item.pin.electrical == "power_in":
            return "local_power" if lib.local_power else "power"
        if item.pin.hidden and item.pin.electrical == "power_in":
            return "hidden_power_pin"
    return None


def _text_of(item, key) -> str | None:
    kind = _kind(item, key)
    if kind in LABELS:
        return item.text
    if kind == "sheet_pin":
        return item[1].name
    if kind in ("power", "local_power"):
        return item.symbol.properties.get("Value", "")
    if kind == "hidden_power_pin":
        return item.pin.name
    return None


def _renamed(old: str, text: str, new: str) -> str:
    """A net's new name: the old one with the drawn text replaced -- the path
    of the sheet before a local name stays."""
    escaped = escape(text)
    return old[: len(old) - len(escaped)] + escape(new) if old.endswith(escaped) else escape(new)


@dataclass
class _Target:
    """One drawn thing that carries the name: its file, its node, and whether
    the name is its text or its value (a power symbol's)."""

    schematic: Schematic
    node: List
    value: bool = False


def _file_of(design: Design, path: str) -> Schematic:
    return next(i.schematic for i in design.instances if i.path == path)


def _on_file(sch: Schematic, text: str, kinds: tuple[str, ...]) -> list[_Target]:
    return [_Target(sch, lb.node) for lb in sch.labels if lb.kind in kinds and lb.text == text]


def _placements(design: Design, sch: Schematic) -> list[_Target]:
    """The sheet symbols that place this file, each once."""
    out, seen = [], set()
    for instance in design.instances:
        if instance.schematic is sch and instance.sheet is not None and instance.parent is not None:
            if id(instance.sheet.node) not in seen:
                seen.add(id(instance.sheet.node))
                out.append(_Target(instance.parent.schematic, instance.sheet.node))
    return out


def _carriers(
    design: Design, best: int, keys: list, items: dict, namers: list, text: str
) -> tuple[list[_Target], list[str]]:
    """Everything that carries the net's name, and would part from it if left
    with the old one; and why it cannot be renamed, if it cannot. A name a bus
    on the same sheet has as a member joins the bus by that name: renamed, it
    would leave it."""
    targets, refusals = _named_by(design, best, keys, items, namers, text)
    aliases = design.bus_aliases()
    for sch in {id(t.schematic): t.schematic for t in targets}.values():
        buses = [lb.text for lb in sch.labels] + [p.name for s in sch.sheets for p in s.pins]
        if any(name == text for bus in buses for _, name in (bus_members(bus, aliases) or [])):
            refusals.append(
                "a bus on the same sheet has the name as a member, and is joined to the net by "
                "it: renamed, the net would leave the bus"
            )
    return targets, refusals


def _named_by(
    design: Design, best: int, keys: list, items: dict, namers: list, text: str
) -> tuple[list[_Target], list[str]]:
    targets: list[_Target] = []
    refusals: list[str] = []
    if best in (PRIORITY["global_label"], PRIORITY["power"]):
        # A global name joins the design: its global labels and power
        # symbols everywhere, and on their sheets the local labels too.
        files = []
        for key in keys:
            item = items.get(key)
            kind = _kind(item, key)
            if _text_of(item, key) != text:
                continue
            if kind == "global_label":
                targets.append(_Target(_file_of(design, key[1]), item.node))
            elif kind == "power":
                targets.append(_Target(_file_of(design, key[1]), item.symbol.node, value=True))
            elif kind == "hidden_power_pin":
                refusals.append(
                    "a part's hidden supply pin is named so, and a pin's name is its library's"
                )
                continue
            else:
                continue
            files.append(_file_of(design, key[1]))
        for sch in {id(f): f for f in files}.values():
            targets += _on_file(sch, text, ("label",))
            if _on_file(sch, text, ("hierarchical_label",)):
                refusals.append(
                    "a hierarchical label of the same name is joined to it by the name, and its "
                    "sheet pins would part from the global net"
                )
        return targets, refusals
    namer_files = {id(s): s for s in (_file_of(design, k[1]) for k in namers)}
    if best == PRIORITY["local_power"]:
        for key in keys:
            item = items.get(key)
            if _kind(item, key) == "local_power" and _text_of(item, key) == text:
                if id(_file_of(design, key[1])) in namer_files:
                    targets.append(_Target(_file_of(design, key[1]), item.symbol.node, value=True))
        return targets, refusals
    # A local name is its sheet's: the local labels of it there. A
    # hierarchical label of it there is the same net, and the sheet pins that
    # meet that label -- on every sheet symbol placing the file -- must take
    # the new name with it.
    if best == PRIORITY["sheet_pin"]:
        files = {}
        for key in namers:
            sheet, _pin = items[key]
            child = next(
                (i.schematic for i in design.instances
                 if i.sheet is not None and i.sheet.node is sheet.node),
                None,
            )  # fmt: skip
            if child is not None:
                files[id(child)] = child
        if not files:
            targets += [_Target(_file_of(design, k[1]), items[k][1].node) for k in namers]
    else:
        files = namer_files
    for sch in files.values():
        targets += _on_file(sch, text, ("label",))
        hierarchical = _on_file(sch, text, ("hierarchical_label",))
        if hierarchical:
            targets += hierarchical
            for placement in _placements(design, sch):
                targets += [
                    _Target(placement.schematic, pin)
                    for pin in placement.node.find_all("pin")
                    if pin.value(1) == text
                ]
        if _on_file(sch, text, ("global_label",)):
            refusals.append("a global label of the same name is on the sheet")
    return targets, refusals


def _rename(edit: Edit, index: int, change: dict, nets: dict, found) -> list[dict]:
    allowed = {"op", "net", "to"}
    problems = [
        {
            "index": index,
            "problem": f"'{k}' is not something rename takes",
            "allowed": sorted(allowed),
        }
        for k in sorted(set(change) - allowed)
    ]
    name, new = change.get("net"), change.get("to")
    if not isinstance(new, str) or not new.strip() or any(c in new for c in "\r\n\t"):
        problems.append({"index": index, "problem": "to is the new name: text on one line"})
    entry = nets.get(name) if isinstance(name, str) else None
    if entry is None:
        return problems + [
            {
                "index": index,
                "problem": "no net has that name",
                "net": name,
                "nearest": _nearest(str(name), nets),
            }
        ]
    if problems:
        return problems
    net, keys = entry
    items = found.items
    best = max((p for p, n in net.names if n == net.name), default=0)
    namers = [k for k in keys if (best, net.name) in found.names.get(k, [])]
    if namers and all(k[0] in ("local", "global") for k in namers):
        return [
            {
                "index": index,
                "problem": "the net is named as a member of a bus, and a member's name is the "
                "bus's: rename the bus's label",
                "net": name,
            }
        ]
    text = _text_of(items.get(namers[0]), namers[0]) if namers else None
    if best in (0, PRIORITY["pin"]) or text is None:
        return [
            {
                "index": index,
                "problem": "nothing drawn names this net -- the name is made from a pin -- so "
                "there is no name to change",
                "net": name,
            }
        ]
    if _kind(items[namers[0]], namers[0]) == "hidden_power_pin":
        return [
            {
                "index": index,
                "problem": "the net is named by a supply pin a part hides, and a pin's name is "
                "its library's",
                "net": name,
            }
        ]
    targets, refusals = _carriers(edit.design, best, keys, items, namers, text)
    if refusals:
        return [{"index": index, "problem": sorted(set(refusals))[0], "net": name}]

    changed: dict[int, _Target] = {}
    for target in targets:
        changed.setdefault(id(target.node), target)
    if text != new:
        for target in changed.values():
            if target.value:
                value = next(p for p in target.node.find_all("property") if p.value(1) == "Value")
                value.set(2, quote(new))
            else:
                target.node.set(1, quote(new))
            edit.touch(target.schematic)
    # Every net named by what was renamed takes the new name: this one, and
    # the same drawing's nets in other placements of its sheet.
    also = []
    for other, (other_net, other_keys) in nets.items():
        top = max((p for p, n in other_net.names if n == other), default=0)
        carriers = [k for k in other_keys if (top, other) in found.names.get(k, [])]
        if carriers and all(k in items and id(_node_of(items[k], k)) in changed for k in carriers):
            edit.names[_pins(other_net)] = _renamed(other, text, new)
            if other != name:
                also.append({"net": other, "to": _renamed(other, text, new)})
    edit.report.append(
        {
            "index": index,
            "op": "rename",
            "net": name,
            "to": _renamed(name, text, new),
            "items": len(changed) if text != new else 0,
            "files": sorted({t.schematic.path.name for t in changed.values()}),
            "also_renames": also,
        }
    )
    return []


def _node_of(item, key) -> List:
    if key[0] == "p":
        return item.symbol.node
    if key[0] == "s":
        return item[1].node
    return item.node


# -- checking the edit, before it is written -------------------------------------------


def _verify(
    edit: Edit, before: list[tuple[Net, list]], parts_before: dict[str, _Part], after: Design
) -> dict[str, Any]:
    """What the edited design says, against what it had to say."""
    then = {_pins(n): n.name for n, _ in before if n.pins}
    _, found = grouped(after)
    now = {_pins(n): n.name for n, _ in found if n.pins}
    moved = [
        {
            "pins": sorted(f"{r}.{p}" for r, p in pins)[:8],
            "before": then.get(pins),
            "after": now.get(pins),
        }
        for pins in set(then) ^ set(now)
    ]
    wrong_name, other_name = [], []
    for pins, name in now.items():
        if pins not in then:
            continue
        expected = edit.names.get(pins, then[pins])
        if name != expected:
            (wrong_name if pins in edit.names else other_name).append(
                {"before": then[pins], "expected": expected, "after": name}
            )
    parts = _parts(after)
    empty = _Part("")
    wrong_fields = []
    for reference, wanted in edit.fields.items():
        for sch, sym in parts.get(reference, empty).symbols:
            wrong_fields += [
                {"ref": reference, "field": name, "wanted": value,
                 "read": sym.properties.get(name), "file": sch.path.name}
                for name, value in wanted.items()
                if sym.properties.get(name) != value
            ]  # fmt: skip
    for reference, wanted in edit.flags.items():
        for sch, sym in parts.get(reference, empty).symbols:
            wrong_fields += [
                {"ref": reference, "flag": name, "wanted": on, "read": getattr(sym, name),
                 "file": sch.path.name}
                for name, on in wanted.items()
                if getattr(sym, name) != on
            ]  # fmt: skip
    lost = sorted(set(parts_before) - set(parts))
    return {
        "nets_join_the_same_pins": not moved,
        "nets_renamed_as_asked": not wrong_name,
        "no_other_net_renamed": not other_name,
        "fields_read_back": not wrong_fields,
        "every_part_still_there": not lost,
        "nets": len(now),
        "problems": {
            "joined_or_split": moved[:10],
            "renamed_wrong": wrong_name[:10],
            "renamed_unasked": other_name[:10],
            "fields": wrong_fields[:10],
            "lost_parts": lost[:10],
        },
    }


def passed(verified: dict[str, Any]) -> bool:
    return all(v for v in verified.values() if isinstance(v, bool))


def _erc(root: Path, design: Design, kicad_root: str | None) -> list[rules.Violation]:
    settings = rules.Settings.of(root.with_suffix(".kicad_pro"))
    return rules.check(root, settings, kicad_root, design=design)


def _erc_delta(before: list[rules.Violation], after: list[rules.Violation]) -> dict[str, Any]:
    def key(v: rules.Violation) -> tuple:
        return (v.rule, frozenset(i.uuid for i in v.items), v.message)

    then = {key(v): v for v in before}
    now = {key(v): v for v in after}
    new = [now[k].to_dict() for k in now if k not in then]
    cleared = [then[k].to_dict() for k in then if k not in now]
    return {
        "before": len(before),
        "after": len(after),
        "new": new[:20],
        "new_count": len(new),
        "cleared": cleared[:20],
        "cleared_count": len(cleared),
    }


def run(schematic: Path, changes: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[Path, str]]:
    """Make the changes in memory and check them: the report, and each
    changed file's new text. Nothing is written here."""
    try:
        design = Design(schematic)
    except (SchematicError, SexprError, UnicodeDecodeError, OSError) as exc:
        envelope.fail(
            "E_VALIDATION",
            "the schematic could not be read",
            {"schematic": str(schematic), "reason": str(exc)[:300]},
        )
        raise AssertionError("unreachable") from exc
    kicad_root = kicad_env.find_kicad_root()
    inputs = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in design.schematics
    }
    erc_before = _erc(schematic, design, kicad_root)
    found, before = grouped(design)
    nets = {net.name: (net, keys) for net, keys in before}
    parts = _parts(design)

    edit = Edit(design)
    problems: list[dict] = []
    for index, change in enumerate(changes):
        if change["op"] == "set":
            problems += _set(edit, index, change, parts)
        else:
            problems += _rename(edit, index, change, nets, found)
    if problems:
        envelope.fail(
            "E_VALIDATION",
            "the changes cannot be made as described; nothing was written",
            {"problems": problems[:40], "problem_count": len(problems)},
        )

    sources = {path: sch.document.dumps() for path, sch in edit.touched.items()}
    after = Design(schematic, sources)
    report = {
        "schematic": str(schematic),
        "changes": edit.report,
        "files": sorted(str(p) for p in sources),
        "inputs": inputs,
        "verified": _verify(edit, before, parts, after),
        "erc": _erc_delta(erc_before, _erc(schematic, after, kicad_root)),
    }
    return report, sources


def not_checked(kicad_root: str | None) -> list[str]:
    out = [
        "the drawing is not laid out again: a longer value or a new field is placed where the "
        "old one was, and may now overlap what is beside it",
        "a board made from this schematic is not changed by this command",
    ]
    if kicad_root is None:
        out.append(
            "ERC's library rules were not checked, before or after: KiCad's installation was "
            "not found"
        )
    return out
