"""``sch edit``, in this process: change an existing schematic, and prove the change.

The changes are described, not drawn. They are made in the files' own text,
so everything else in them stays byte for byte as it was, and each one sees
the design as the ones before it left it: a net named by one change can be
connected to by the next. Before anything is written, the edited design is
read back through this tool's own connectivity and held to what was asked:
the pins each net joins -- the same as before but for the pins a change
moved -- the names nets carry, and every field. An edit that would join or
split anything else is refused, not written.

What each change touches:

- `set` changes a part's fields and flags, on every unit of it. A sheet
  placed more than once is one file, so a part on it is one symbol whatever
  its reference in each placement: the others change with it, and are named.
- `rename` changes what names a net: every local label of that name on its
  sheet; every global label, power symbol and local label joined by that
  name; a hierarchical label with the sheet pins that meet it. A net nothing
  names -- "Net-(R1-Pad1)" -- is given the name by a label on one of its pins.
  A net named by a bus, or by a supply pin a part hides, is not renamed.
- `connect` joins a pin to a net: a short wire out from the pin and, at its
  end, what names the net there -- a label, a global label, a power symbol
  like the net's others. The pin must be free; a no-connect flag on it goes.
- `disconnect` frees a pin: the wire from it, as far as nothing else uses
  it, and a label or power symbol only it had. A flag marks it unused,
  unless asked otherwise.

New wires and labels go where nothing is drawn (`sch_draw.py`). A sheet
placed twice is one drawing: a change to it is a change in every placement,
and the report says what else changed with it.

The design's ERC -- this tool's own, `fileformat/erc.py` -- is run before and
after, and what the edit adds to it or clears is reported.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import envelope, kicad_env
from ..fileformat import erc as rules
from ..fileformat.circuit import (
    PRIORITY,
    Design,
    Graph,
    Net,
    SheetInstance,
    _on_segment,
    bus_members,
    escape,
    grouped,
)
from ..fileformat.schematic import LibPin, LibSymbol, Schematic, SchematicError, Symbol
from ..fileformat.sexpr import List, SexprError, copy, quote, symbol
from . import sch_draw as draw

OPS = ("set", "rename", "connect", "disconnect", "add", "remove")
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
PIN_REF = re.compile(r"\A(?P<ref>[^.\s]+)\.(?P<pin>[^\s]+)\Z")

Pin = tuple[str, str]  # (reference, pin number): how a netlist names a pin


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


# -- what the design has, as the changes so far left it -----------------------------------


@dataclass
class _Part:
    """One reference: every symbol of it, each once, and each unit it is
    given where it is placed."""

    reference: str
    symbols: list[tuple[Schematic, Symbol]] = field(default_factory=list)
    units: list[int] = field(default_factory=list)


def _parts(design: Design) -> dict[str, _Part]:
    out: dict[str, _Part] = {}
    seen: set[tuple[str, str, str]] = set()
    for instance in design.instances:
        sch = instance.schematic
        for sym in sch.symbols:
            inst = sym.instance(instance.path)
            reference = inst.reference if inst else sym.properties.get("Reference", "")
            part = out.setdefault(reference, _Part(reference))
            part.units.append(inst.unit if inst else sym.unit)
            key = (reference, str(sch.path), sym.uuid)
            if key not in seen:
                seen.add(key)
                part.symbols.append((sch, sym))
    return out


def _pins(net: Net) -> frozenset:
    return frozenset((pp.reference, pp.pin.number) for pp in net.pins)


def _nearest(word: str, choices) -> list[str]:
    return difflib.get_close_matches(word, list(choices), n=5, cutoff=0.5)


@dataclass
class Step:
    """The design as the changes so far have left it, read back."""

    design: Design
    found: Graph
    nets: dict[str, tuple[Net, list]]
    parts: dict[str, _Part]
    net_of: dict[Pin, str]
    canvases: dict[int, draw.Canvas] = field(default_factory=dict)
    kicad_root: str | None = None

    @classmethod
    def read(cls, root: Path, sources: dict[Path, str], kicad_root: str | None = None) -> Step:
        design = Design(root, sources)
        found, listed = grouped(design)
        nets = {net.name: (net, keys) for net, keys in listed}
        net_of = {pin: net.name for net, _ in listed for pin in _pins(net)}
        return cls(design, found, nets, _parts(design), net_of, kicad_root=kicad_root)

    def canvas(self, sch: Schematic) -> draw.Canvas:
        if id(sch) not in self.canvases:
            self.canvases[id(sch)] = draw.Canvas.of(sch)
        return self.canvases[id(sch)]

    def placements(self, sch: Schematic) -> list[SheetInstance]:
        """The sheet instances that show this file."""
        return [i for i in self.design.instances if i.schematic is sch]


@dataclass
class Edit:
    root: Path
    ids: draw.Ids
    project: str
    report: list[dict[str, Any]] = field(default_factory=list)
    touched: dict[Path, Schematic] = field(default_factory=dict)
    # What must read back: by reference, each field's value (None: gone) and
    # each flag.
    fields: dict[str, dict[str, str | None]] = field(default_factory=dict)
    flags: dict[str, dict[str, bool]] = field(default_factory=dict)
    # The pins each net must join afterwards, as the design's own grouping
    # moved by each change: a pin to a group, by group number.
    where: dict[Pin, int] = field(default_factory=dict)
    fresh: dict[Any, int] = field(default_factory=dict)
    moved: set[Pin] = field(default_factory=set)
    # A pin, and the name its net must have afterwards.
    expect: dict[Pin, str] = field(default_factory=dict)
    references: set[int] = field(default_factory=set)
    # Parts a change took away on purpose: a power symbol only a pin had.
    removed: set[str] = field(default_factory=set)
    # Numbers given to new parts, by prefix.
    numbers: dict[str, set[int]] = field(default_factory=dict)
    kicad_root: str | None = None

    def touch(self, sch: Schematic) -> None:
        self.touched[sch.path.resolve()] = sch

    def power_reference(self, step: Step) -> str:
        """A power symbol's reference no part of the design has."""
        taken = {int(m.group(1)) for r in step.parts if (m := re.match(r"#PWR0*(\d+)$", r))}
        taken |= self.references
        number = max(taken, default=0) + 1
        self.references.add(number)
        return f"#PWR{number:04d}"

    def _new_group(self, key: Any = None) -> int:
        if key is not None and key in self.fresh:
            return self.fresh[key]
        number = max(self.where.values(), default=0) + 1 + len(self.fresh)
        number = max(number, max(self.fresh.values(), default=0) + 1)
        if key is not None:
            self.fresh[key] = number
        return number

    def vanish(self, pin: Pin) -> None:
        """A pin whose part is gone: on no net."""
        self.where.pop(pin, None)
        self.moved.add(pin)

    def alone(self, pin: Pin) -> None:
        self.where[pin] = self._new_group()
        self.moved.add(pin)

    def join(self, pin: Pin, other: Pin | None, key: Any = None) -> None:
        """`pin` onto the net `other` is on, or a new one known by `key`."""
        if other is not None and other in self.where:
            self.where[pin] = self.where[other]
        else:
            self.where[pin] = self._new_group(key)
            if other is not None:
                self.where[other] = self.where[pin]
        self.moved.add(pin)


def _problem(index: int, text: str, **more: Any) -> dict[str, Any]:
    return {"index": index, "problem": text, **more}


def _unknown(index: int, change: dict, allowed: set[str], op: str) -> list[dict]:
    return [
        _problem(index, f"'{k}' is not something {op} takes", allowed=sorted(allowed))
        for k in sorted(set(change) - allowed)
    ]


def _part(step: Step, index: int, reference: Any) -> tuple[_Part | None, list[dict]]:
    part = step.parts.get(reference) if isinstance(reference, str) else None
    if part is None:
        return None, [
            _problem(
                index,
                "no part has that reference",
                ref=reference,
                nearest=_nearest(str(reference), (r for r in step.parts if not r.startswith("#"))),
            )
        ]
    if len(part.units) != len(set(part.units)):
        return None, [
            _problem(
                index,
                "more than one part is annotated with this reference; annotate the design first",
                ref=reference,
            )
        ]
    return part, []


# -- set -------------------------------------------------------------------------------------


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
    _set_field_node(sym.node, sch.version, name, value)


def _set_field_node(node: List, version: int, name: str, value: str | None) -> None:
    found = [p for p in node.find_all("property") if p.value(1) == name]
    if value is None:
        for prop in found:
            node.remove(prop)
        return
    if found:
        found[0].set(2, quote(value))
        return
    new = copy(_hidden_template(node, version))
    new.set(1, quote(name))
    new.set(2, quote(value))
    at, place = new.find("at"), node.find("at")
    if at is not None and place is not None:
        # At the symbol, level whatever the symbol's angle.
        new.replace(at, List.new("at", place.atom(1) or "0", place.atom(2) or "0", "0"))
    last = node.find_all("property")[-1]
    node.insert(node.items.index(last) + 1, new)


def _set_flag(sym: Symbol, name: str, on: bool) -> None:
    _set_flag_node(sym.node, name, on)


def _set_flag_node(node: List, name: str, on: bool) -> None:
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


def _set(edit: Edit, step: Step, index: int, change: dict) -> list[dict]:
    problems = _unknown(index, change, {"op", "ref", "fields", *FIELDS, *FLAGS}, "set")
    reference = change.get("ref")
    part, missing = _part(step, index, reference)
    if part is None:
        return problems + missing
    wanted: dict[str, str | None] = {FIELDS[k]: change[k] for k in FIELDS if k in change}
    given = change.get("fields", {})
    if not isinstance(given, dict):
        problems.append(_problem(index, "fields is an object: {name: value}"))
        given = {}
    wanted.update(given)
    flags = {k: change[k] for k in FLAGS if k in change}
    for name, value in wanted.items():
        if value is not None and not isinstance(value, str):
            problems.append(_problem(index, "a field's value is text", field=name))
        elif name == "Reference":
            problems.append(_problem(index, "a reference is changed by annotating, not by set"))
        elif value is None and name in MANDATORY:
            problems.append(
                _problem(
                    index, "every part has this field; it can be emptied, not removed", field=name
                )
            )
        elif not name.strip() or name.startswith("ki_") or any(c in name for c in "\r\n\t"):
            problems.append(_problem(index, "not a name a field can have", field=name))
    problems += [
        _problem(index, "a flag is true or false", flag=k)
        for k, v in flags.items()
        if not isinstance(v, bool)
    ]
    if not wanted and not flags:
        problems.append(_problem(index, "set changes nothing: give fields or flags"))
    if any(
        (lib := sch.library.get(sym.library_name)) is not None and lib.power
        for sch, sym in part.symbols
    ):
        problems.append(
            _problem(
                index,
                "a power symbol's value is the name of its net: rename the net instead",
                ref=reference,
            )
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
            "also_changes": _also(part, reference),
            "files": sorted({sch.path.name for sch, _ in part.symbols}),
        }
    )
    return []


def _also(part: _Part, reference: str) -> list[str]:
    """The references another placement of the sheet gives these symbols."""
    return sorted(
        {inst.reference for _, sym in part.symbols for inst in sym.instances} - {reference}
    )


# -- naming nets -----------------------------------------------------------------------------


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


def _node_of(item, key) -> List:
    if key[0] == "p":
        return item.symbol.node
    if key[0] == "s":
        return item[1].node
    return item.node


def _renamed(old: str, text: str, new: str) -> str:
    """A net's new name: the old one with the drawn text replaced -- the path
    of the sheet before a local name stays."""
    escaped = escape(text)
    return old[: len(old) - len(escaped)] + escape(new) if old.endswith(escaped) else escape(new)


@dataclass
class _Namer:
    """What names a net, as a change needs to know it: the kind of thing,
    the text drawn, the keys that carry it, and why it cannot be used."""

    kind: str | None  # label, global_label, hierarchical_label, sheet_pin, power, ...
    text: str | None
    keys: list
    problem: str | None = None


def _namer(step: Step, net: Net, keys: list) -> _Namer:
    found = step.found
    best = max((p for p, n in net.names if n == net.name), default=0)
    namers = [k for k in keys if (best, net.name) in found.names.get(k, [])]
    if namers and all(k[0] in ("local", "global") for k in namers):
        return _Namer(
            "bus", None, namers,
            "the net is named as a member of a bus, and a member's name is the bus's: rename "
            "the bus's label",
        )  # fmt: skip
    if best in (0, PRIORITY["pin"]) or not namers:
        return _Namer(None, None, [])
    item = found.items.get(namers[0])
    kind = _kind(item, namers[0])
    if kind == "hidden_power_pin":
        return _Namer(
            kind, _text_of(item, namers[0]), namers,
            "the net is named by a supply pin a part hides, and a pin's name is its library's",
        )  # fmt: skip
    return _Namer(kind, _text_of(item, namers[0]), namers)


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


def _valid_name(new: Any) -> bool:
    return isinstance(new, str) and bool(new.strip()) and not any(c in new for c in "\r\n\t")


def _rename(edit: Edit, step: Step, index: int, change: dict) -> list[dict]:
    problems = _unknown(index, change, {"op", "net", "to"}, "rename")
    name, new = change.get("net"), change.get("to")
    if not _valid_name(new):
        problems.append(_problem(index, "to is the new name: text on one line"))
    entry = step.nets.get(name) if isinstance(name, str) else None
    if entry is None:
        return problems + [
            _problem(
                index, "no net has that name", net=name, nearest=_nearest(str(name), step.nets)
            )
        ]
    if problems:
        return problems
    net, keys = entry
    namer = _namer(step, net, keys)
    if namer.problem:
        return [_problem(index, namer.problem, net=name)]
    if namer.kind is None:
        return _name_net(edit, step, index, net, new)
    items = step.found.items
    best = max((p for p, n in net.names if n == net.name), default=0)
    targets, refusals = _carriers(step.design, best, keys, items, namer.keys, namer.text)
    if refusals:
        return [_problem(index, sorted(set(refusals))[0], net=name)]

    text = namer.text
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
    for other, (other_net, other_keys) in step.nets.items():
        top = max((p for p, n in other_net.names if n == other), default=0)
        carriers = [k for k in other_keys if (top, other) in step.found.names.get(k, [])]
        if carriers and all(k in items and id(_node_of(items[k], k)) in changed for k in carriers):
            for pin in _pins(other_net):
                edit.expect[pin] = _renamed(other, text, new)
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


def _name_net(edit: Edit, step: Step, index: int, net: Net, text: str) -> list[dict]:
    """Give a net nothing names a name: a local label on one of its pins,
    lying along the wire that leaves it."""
    choices = sorted(net.pins, key=lambda pp: (pp.instance.path.count("/"), pp.reference))
    for pp in choices:
        sch = pp.instance.schematic
        canvas = step.canvas(sch)
        wires = [(a, b) for a, b in canvas.wires if pp.position in (a, b)]
        if len(wires) > 1:
            continue
        if wires:
            a, b = wires[0]
            far = b if a == pp.position else a
            out = _unit((far[0] - pp.position[0], far[1] - pp.position[1]))
        else:
            lib = sch.library.get(pp.symbol.library_name)
            placed = next(p for p in canvas.pins(pp.symbol, lib) if p.pin.number == pp.pin.number)
            out = placed.out
        if out not in draw.ANGLE:
            continue
        uid = edit.ids("name", index, pp.reference, pp.pin.number)
        _append(sch, draw.label("label", text, pp.position, out, uid))
        canvas.take(pp.position, "label", draw.label_box("label", text, pp.position, out))
        edit.touch(sch)
        # The drawing's net in every placement of the sheet takes the name.
        renamed = []
        for inst in step.placements(sch):
            ref = _reference(pp.symbol, inst)
            other = step.net_of.get((ref, pp.pin.number))
            if other is None:
                continue
            for pin in _pins(step.nets[other][0]):
                edit.expect[pin] = inst.name + escape(text)
            renamed.append({"net": other, "to": inst.name + escape(text)})
        edit.report.append(
            {
                "index": index,
                "op": "rename",
                "net": net.name,
                "to": renamed[0]["to"] if renamed else escape(text),
                "items": 1,
                "files": [sch.path.name],
                "also_renames": [r for r in renamed if r["net"] != net.name],
                "drawn": {"label": text, "at_pin": f"{pp.reference}.{pp.pin.number}"},
            }
        )
        return []
    return [
        _problem(
            index,
            "no pin of this net has a place for a label: each is where several wires meet",
            net=net.name,
        )
    ]


def _unit(vector: tuple[int, int]) -> tuple[int, int]:
    x, y = vector
    return ((x > 0) - (x < 0), (y > 0) - (y < 0))


def _reference(sym: Symbol, instance: SheetInstance) -> str:
    inst = sym.instance(instance.path)
    return inst.reference if inst else sym.properties.get("Reference", "")


def _append(sch: Schematic, node: List) -> None:
    """A new item, where KiCad keeps items of its kind: before the sheet's
    instance list at the end of the file."""
    root = sch.root
    tail = next(
        (n for n in root.lists() if n.head in ("sheet_instances", "symbol_instances",
                                                "embedded_fonts")),
        None,
    )  # fmt: skip
    if tail is None:
        root.append(node)
    else:
        root.insert(root.items.index(tail), node)


# -- connect ---------------------------------------------------------------------------------


@dataclass
class _Way:
    """How a net is drawn where a pin joins it."""

    kind: str  # label, global_label, power, local_power
    text: str
    net: str | None  # the net as the design has it, None for a new one
    template: List | None = None  # a power symbol of the net, to copy
    template_lib: LibSymbol | None = None
    shape: str | None = None  # a global label's, as the net's others have it

    def name(self, instance: SheetInstance) -> str:
        if self.kind in ("label", "local_power"):
            return instance.name + escape(self.text)
        return escape(self.text)


def _pins_named(part: _Part, wanted: str) -> list[tuple[Schematic, Symbol, LibSymbol, LibPin]]:
    """A pin by number; else every pin of that name."""
    out = []
    for sch, sym in part.symbols:
        lib = sch.library.get(sym.library_name)
        if lib is None:
            continue
        for pin in lib.pins_of(sym.unit, sym.body_style):
            if pin.number == wanted:
                out.append((sch, sym, lib, pin))
    if out:
        return out[:1]
    for sch, sym in part.symbols:
        lib = sch.library.get(sym.library_name)
        if lib is None:
            continue
        out += [
            (sch, sym, lib, p) for p in lib.pins_of(sym.unit, sym.body_style) if p.name == wanted
        ]
    return out


def _way(step: Step, index: int, wanted: str, sch: Schematic) -> tuple[_Way | None, list[dict]]:
    """How the net the change names is drawn on the sheet `sch`."""
    name = wanted
    match = PIN_REF.match(wanted)
    if wanted not in step.nets and match and match["ref"] in step.parts:
        part = step.parts[match["ref"]]
        pins = _pins_named(part, match["pin"])
        name = step.net_of.get((match["ref"], pins[0][3].number)) if pins else None
        if name is None:
            return None, [_problem(index, "that part has no such pin", net=wanted)]
    entry = step.nets.get(name)
    if entry is None:
        text = wanted
        if text.startswith("/"):
            return None, [
                _problem(
                    index,
                    "no net has that name; a new net is named by its label's text, without the "
                    "sheet's path",
                    net=wanted,
                    nearest=_nearest(wanted, step.nets),
                )
            ]
        # A new supply is drawn as supplies are: a power symbol, the
        # design's own of that name if it has one, else KiCad's.
        if draw.POWER_NAME.match(text):
            found = _power_template(step, text)
            if found is not None:
                return _Way("power", text, None, *found), []
        return _Way("label", text, None), []
    net, keys = entry
    namer = _namer(step, net, keys)
    if namer.kind == "hidden_power_pin":
        # A supply a hidden pin names is a global name: a power symbol of it
        # joins it, as it joins every hidden pin of the name.
        found = _power_template(step, namer.text)
        if found is not None:
            return _Way("power", namer.text, name, *found), []
    if namer.problem:
        return None, [_problem(index, namer.problem, net=name)]
    if namer.kind is None:
        return None, [
            _problem(
                index,
                "nothing names this net yet: rename it first -- a label is drawn on one of its "
                "pins -- and connect to the new name",
                net=name,
            )
        ]
    item = step.found.items[namer.keys[0]]
    if namer.kind in ("power", "local_power"):
        lib = item.instance.schematic.library.get(item.symbol.library_name)
        if namer.kind == "local_power" and item.instance.schematic is not sch:
            return None, [_problem(index, "the net's power symbols are another sheet's", net=name)]
        return _Way(namer.kind, namer.text, name, item.symbol.node, lib), []
    if namer.kind == "global_label":
        return _Way("global_label", namer.text, name, shape=item.shape), []
    if namer.kind in ("label", "hierarchical_label"):
        files = {id(_file_of(step.design, k[1])) for k in namer.keys}
        if id(sch) not in files:
            return None, [
                _problem(
                    index,
                    "the net is local to another sheet; connect to it there, or give it a global "
                    "label",
                    net=name,
                )
            ]
        return _Way("label", namer.text, name), []
    return None, [
        _problem(
            index,
            "the net is named by a sheet pin; connect to it on the sheet the pin leads into",
            net=name,
        )
    ]


def _power_template(step: Step, name: str) -> tuple[List, LibSymbol] | None:
    """A power symbol for a supply the design does not have yet: one placed
    elsewhere of that name, else the power library's."""
    from ..fileformat import lib_tables  # noqa: PLC0415
    from ..fileformat.schematic import EMPTY_TILDE_BEFORE, _lib_symbol  # noqa: PLC0415
    from ..fileformat.symbols import Resolved  # noqa: PLC0415
    from . import sch_create as create  # noqa: PLC0415

    for sch in step.design.schematics.values():
        for sym in sch.symbols:
            lib = sch.library.get(sym.library_name)
            if lib is not None and lib.power and not lib.local_power:
                if sym.lib_id.partition(":")[2] == name:
                    return sym.node, lib
    if step.kicad_root is None:
        return None
    tables = lib_tables.Tables.of(step.design.root_path.resolve().parent, Path(step.kicad_root))
    library = tables.symbols.get("power")
    found = tables.symbol(library, name) if library is not None else None
    if found is None:
        return None
    node, version = found
    lib_id = f"power:{name}"
    lib = _lib_symbol(lib_id, node, {lib_id: node}, version < EMPTY_TILDE_BEFORE)
    placed = create._symbol_node(
        Resolved(lib_id, node, lib), "#PWR", name, "", (0, 0), 0, 1, draw.Ids("power"), "",
        step.design.project, (0, 0), (0, 0), None, hide_ref=True,
    )  # fmt: skip
    return placed, lib


def _connect(edit: Edit, step: Step, index: int, change: dict) -> list[dict]:
    problems = _unknown(index, change, {"op", "ref", "pin", "net"}, "connect")
    reference, wanted, net = change.get("ref"), change.get("pin"), change.get("net")
    if not isinstance(wanted, str) or not wanted:
        problems.append(_problem(index, "pin is the pin's number or name"))
    if not _valid_name(net):
        problems.append(_problem(index, "net is a net's name, a new name, or REF.PIN"))
    part, missing = _part(step, index, reference)
    if part is None or problems:
        return problems + missing
    pins = _pins_named(part, wanted)
    if not pins:
        return [
            _problem(
                index,
                "the part has no such pin",
                pin=f"{reference}.{wanted}",
                available=sorted(
                    {
                        p.name or p.number
                        for sch, sym in part.symbols
                        for p in (
                            sch.library.get(sym.library_name).pins
                            if sch.library.get(sym.library_name)
                            else []
                        )
                    }
                )[:40],
            )  # fmt: skip
        ]
    drawn, joined = [], []
    for sch, sym, lib, pin in pins:
        way, trouble = _way(step, index, net, sch)
        if trouble:
            return trouble
        # A local name is each placement's own: the pin named must join the
        # net named, not the same name in another placement of its sheet.
        own = next(i for i in step.placements(sch) if _reference(sym, i) == reference)
        if way.net is not None and way.kind == "label" and way.name(own) != way.net:
            return [
                _problem(
                    index,
                    f"{reference} is on {own.name}; a label there joins it to "
                    f"{way.name(own)}, not to {way.net}",
                    net=way.net,
                )
            ]
        canvas = step.canvas(sch)
        placed = next(p for p in canvas.pins(sym, lib) if p.pin.number == pin.number)
        there = canvas.touching(placed.at, besides="pin")
        if [t for t in there if t != "no_connect"]:
            current = step.net_of.get((_reference(sym, step.placements(sch)[0]), pin.number))
            return [
                _problem(
                    index,
                    "the pin is already connected; disconnect it first",
                    pin=f"{reference}.{pin.number}",
                    net=current,
                )
            ]
        for flag in [n for n in sch.root.find_all("no_connect") if _point(n) == placed.at]:
            sch.root.remove(flag)
            canvas.forget(placed.at, "no_connect")
        result = _draw_out(edit, step, canvas, sch, placed, way, (index, reference, pin.number))
        if "problem" in result:
            return [result | {"index": index}]
        edit.touch(sch)
        # Every placement of the sheet: that placement's pin, onto that
        # placement's net of the name.
        for inst in step.placements(sch):
            here = (_reference(sym, inst), pin.number)
            onto = None
            if way.net is not None:
                target = way.name(inst) if way.kind == "label" else way.net
                found = step.nets.get(target)
                onto = next(iter(_pins(found[0])), None) if found else None
            edit.join(here, onto, key=("net", way.name(inst)))
            edit.expect[here] = way.name(inst)
            joined.append({"pin": f"{here[0]}.{here[1]}", "net": way.name(inst)})
        drawn.append({"pin": f"{reference}.{pin.number}", **result})
    edit.report.append(
        {
            "index": index,
            "op": "connect",
            "ref": reference,
            "pin": wanted,
            "joined": joined,
            "drawn": drawn,
            "also_connects": sorted(
                {
                    f"{_reference(sym, i)}.{pin.number}"
                    for sch, sym, _, pin in pins
                    for i in step.placements(sch)
                }
                - {f"{reference}.{p[3].number}" for p in pins}
            ),  # fmt: skip
            "files": sorted({sch.path.name for sch, *_ in pins}),
        }
    )
    return []


def _point(node: List) -> tuple[int, int]:
    at = node.find("at")
    return (draw._nm(at.atom(1)), draw._nm(at.atom(2)))


def _draw_out(
    edit: Edit, step: Step, canvas: draw.Canvas, sch: Schematic, placed: draw.Placed,
    way: _Way, key: tuple,
) -> dict:  # fmt: skip
    """A wire out from the pin, and what names the net at its end, where it
    touches nothing else and, if it can, overlaps nothing."""
    start, out = placed.at, placed.out
    best = None
    for steps in draw.STUBS:
        end = (start[0] + out[0] * steps * draw.GRID, start[1] + out[1] * steps * draw.GRID)
        if not canvas.clear(start, end) or canvas.touching(end):
            continue
        if way.kind in ("power", "local_power"):
            box = draw.power_box(way.text, end, out)
        else:
            box = draw.label_box(way.kind, way.text, end, out)
        # The wire's own end at the pin is the pin's: measured from half a
        # grid out, it meets nothing of its own symbol.
        near = (start[0] + out[0] * draw.GRID // 2, start[1] + out[1] * draw.GRID // 2)
        stub = draw._pad(draw._span(near, end), draw.GRID // 4)
        crowded = canvas.overlaps(box) + canvas.overlaps(stub)
        if best is None or crowded < best[0]:
            best = (crowded, end, box)
        if crowded == 0:
            break
    if best is None:
        return {
            "problem": "there is no clear way out of the pin: every short wire from it would "
            "touch something else"
        }
    crowded, end, box = best
    _append(sch, draw.wire(start, end, edit.ids(*key, "wire")))
    canvas.take_wire(start, end)
    if way.kind in ("power", "local_power"):
        references = {inst.path: edit.power_reference(step) for inst in step.placements(sch)}
        node = draw.power_symbol(
            way.template, way.template_lib, way.text, end, out, edit.ids, key, references,
            edit.project,
        )  # fmt: skip
        _append(sch, node)
        _embed(sch, way.template_lib, step)
        canvas.take(end, "pin", box)
    else:
        node = draw.label(way.kind, way.text, end, out, edit.ids(*key, "label"), way.shape)
        _append(sch, node)
        canvas.take(end, "label", box)
    return {"wire_mm": [_mm(start), _mm(end)], "marker": way.kind, "overlaps": crowded}


def _mm(point: tuple[int, int]) -> list[float]:
    return [point[0] / draw.NM, point[1] / draw.NM]


def _embed(sch: Schematic, lib: LibSymbol | None, step: Step) -> None:
    """The power symbol's drawing in this sheet's own library, if it is not."""
    if lib is None or lib.node is None:
        return
    table = sch.root.find("lib_symbols")
    if table is None:
        return
    if any(n.value(1) == lib.name for n in table.find_all("symbol")):
        return
    table.append(copy(lib.node))


# -- disconnect ------------------------------------------------------------------------------


def _disconnect(edit: Edit, step: Step, index: int, change: dict) -> list[dict]:
    problems = _unknown(index, change, {"op", "ref", "pin", "no_connect"}, "disconnect")
    reference, wanted = change.get("ref"), change.get("pin")
    flag = change.get("no_connect", True)
    if not isinstance(wanted, str) or not wanted:
        problems.append(_problem(index, "pin is the pin's number or name"))
    if not isinstance(flag, bool):
        problems.append(_problem(index, "no_connect is true or false"))
    part, missing = _part(step, index, reference)
    if part is None or problems:
        return problems + missing
    pins = _pins_named(part, wanted)
    if not pins:
        return [_problem(index, "the part has no such pin", pin=f"{reference}.{wanted}")]
    removed_total: dict[str, int] = defaultdict(int)
    for sch, sym, lib, pin in pins:
        canvas = step.canvas(sch)
        placed = next(p for p in canvas.pins(sym, lib) if p.pin.number == pin.number)
        removed = _cut(sch, step, sym, pin, placed.at, flag, edit.removed)
        if isinstance(removed, str):
            return [_problem(index, removed, pin=f"{reference}.{pin.number}")]
        for kind, count in removed.items():
            removed_total[kind] += count
        if flag and not [n for n in sch.root.find_all("no_connect") if _point(n) == placed.at]:
            _append(sch, draw.no_connect(placed.at, edit.ids(index, reference, pin.number, "nc")))
            removed_total["no_connect_added"] += 1
        edit.touch(sch)
        for inst in step.placements(sch):
            here = (_reference(sym, inst), pin.number)
            name = step.net_of.get(here)
            left = step.nets.get(name) if name else None
            edit.alone(here)
            # The net it leaves keeps its name, if something drawn named it.
            if left is not None and _namer(step, *left).kind is not None:
                rest = [p for p in _pins(left[0]) if p != here]
                if rest:
                    edit.expect[rest[0]] = name
    edit.report.append(
        {
            "index": index,
            "op": "disconnect",
            "ref": reference,
            "pin": wanted,
            "removed": dict(removed_total),
            "also_disconnects": sorted(
                {
                    f"{_reference(sym, i)}.{pin.number}"
                    for sch, sym, _, pin in pins
                    for i in step.placements(sch)
                }
                - {f"{reference}.{p[3].number}" for p in pins}
            ),  # fmt: skip
            "files": sorted({sch.path.name for sch, *_ in pins}),
        }
    )
    return []


def _cut(
    sch: Schematic,
    step: Step,
    sym: Symbol,
    pin: LibPin,
    at: tuple[int, int],
    keep_flag: bool,
    lost: set[str],
    removing: bool = False,
) -> dict[str, int] | str:
    """Take away what joins the pin at `at` to anything, and nothing more.

    The wires from the pin are followed to where they end. If they end at
    nothing -- a label, a flag, or nothing at all -- they served the pin
    alone, and they go with what names them. If they reach something else --
    a pin, a branch, a sheet pin -- they serve that too: only the wire
    touching the pin goes, and what named the net on it moves to where that
    wire met the rest, so the rest keeps its name. A reason instead, if what
    is at the pin cannot be told apart from what others use."""
    wires = [w for w in sch.wires if w.kind == "wire"]
    labels = list(sch.labels)
    symbols = list(sch.symbols)

    def pins_at(point):
        found = []
        for other in symbols:
            lib = sch.library.get(other.library_name)
            if lib is None:
                continue
            for p in lib.pins_of(other.unit, other.body_style):
                if other.transform(p.position) == point and not (
                    other is sym and p.number == pin.number
                ):
                    found.append((other, lib))
        return found

    def junctions_at(point):
        return [n for n in sch.root.find_all("junction") if _point(n) == point]

    def flags_at(point):
        return [n for n in sch.root.find_all("no_connect") if _point(n) == point]

    sheet_pins = [p.position for s in sch.sheets for p in s.pins]
    entries = [e.position for e in sch.bus_entries] + [e.end for e in sch.bus_entries]
    others = pins_at(at)
    ends = [w for w in wires if at in (w.start, w.end)]
    shared = [o for o in others if not o[1].power] or at in sheet_pins or at in entries
    if removing and (shared or len(ends) > 1 or junctions_at(at)):
        # The part goes; what meets at its pin stays joined without it.
        return {}
    if shared:
        return "the pin touches another pin, a sheet pin or a bus entry directly; move one of them"
    if len(ends) > 1 or junctions_at(at):
        return "other connections meet at this pin; rewire it in KiCad"
    removed: dict[str, int] = defaultdict(int)
    gone: set[int] = set()

    def drop(node: List, kind: str) -> None:
        if id(node) not in gone:
            gone.add(id(node))
            sch.root.remove(node)
            removed[kind] += 1

    def move(node: List, to: tuple[int, int], kind: str) -> None:
        place = node.find("at")
        angle = place.atom(3) if place is not None and place.atom(3) is not None else "0"
        node.replace(place, List.new("at", *draw._xy(to), angle))
        removed[kind] += 1

    if not ends:
        # Nothing but what sits on the pin: a label, a power symbol.
        for other, _lib in others:
            drop(other.node, "power_symbols")
            lost.update(inst.reference for inst in other.instances)
        for lb in labels:
            if lb.position == at:
                drop(lb.node, "labels")
        if not keep_flag:
            for flag in flags_at(at):
                drop(flag, "no_connects")
        return dict(removed)

    # Follow the wires from the pin to where they stop.
    chain, point, wire = [], at, ends[0]
    while True:
        chain.append(wire)
        far = wire.end if wire.start == point else wire.start
        rest = [w for w in wires if w not in chain and far in (w.start, w.end)]
        through = [w for w in wires if w not in chain and far not in (w.start, w.end)
                   and _on_segment(far, w.start, w.end)]  # fmt: skip
        stops = pins_at(far) or junctions_at(far) or far in sheet_pins or far in entries
        if len(rest) == 1 and not through and not stops:
            point, wire = far, rest[0]
            continue
        dead = not rest and not through and not stops
        break

    def on(lb, segment) -> bool:
        return _on_segment(lb.position, segment.start, segment.end)

    if dead:
        for segment in chain:
            drop(segment.node, "wires")
        for lb in labels:
            if any(on(lb, segment) for segment in chain):
                # A sheet's own name for a net is gone with it; a name it
                # shares with the rest of the design -- its interface to the
                # sheet above, a global label -- stays for what comes next.
                if lb.kind == "label":
                    drop(lb.node, "labels")
                else:
                    removed[f"{lb.kind}s_kept"] += 1
        for other, _lib in others + pins_at(far):
            if _lib.power:
                drop(other.node, "power_symbols")
                lost.update(inst.reference for inst in other.instances)
        for flag in flags_at(far):
            drop(flag, "no_connects")
    elif (
        not rest
        and not through
        and len(pins_at(far)) == 1
        and not junctions_at(far)
        and far not in sheet_pins
        and far not in entries
        and not any(on(lb, segment) for lb in labels for segment in chain)
    ):
        # A bare wire to one other pin: it joined the two and nothing else,
        # and left behind it would hang from that pin to nowhere.
        for segment in chain:
            drop(segment.node, "wires")
        for other, _lib in others:
            drop(other.node, "power_symbols")
            lost.update(inst.reference for inst in other.instances)
    else:
        first = chain[0]
        meet = first.end if first.start == at else first.start
        drop(first.node, "wires")
        for lb in labels:
            if on(lb, first) and lb.position != meet:
                move(lb.node, meet, "labels_moved")
        for other, _lib in others:
            move(other.node, meet, "power_symbols_moved")
        if meet == far:
            joined = len(rest) + 2 * len(through) + len(pins_at(far))
            if joined < 3:
                for junction in junctions_at(far):
                    drop(junction, "junctions")
    if not keep_flag:
        for flag in flags_at(at):
            drop(flag, "no_connects")
    return dict(removed)


# -- add and remove --------------------------------------------------------------------------

REFERENCE = re.compile(r"\A#?[A-Za-z_]+[A-Za-z_-]*(\d+|\?)\Z")


def _free_reference(step: Step, edit: Edit, prefix: str) -> str:
    """The prefix and the lowest number no part of the design has."""
    taken = set()
    for reference in step.parts:
        match = re.match(rf"\A{re.escape(prefix)}(\d+)\Z", reference)
        if match:
            taken.add(int(match.group(1)))
    taken |= edit.numbers.get(prefix, set())
    number = 1
    while number in taken:
        number += 1
    edit.numbers.setdefault(prefix, set()).add(number)
    return f"{prefix}{number}"


def _resolve_symbol(step: Step, sch: Schematic, lib_id: str, kicad_root: str | None):
    """The symbol to place: the sheet's own copy of it if it has one, so the
    part matches its kin; else the library's, found through the tables."""
    from ..fileformat import lib_tables  # noqa: PLC0415
    from ..fileformat.schematic import EMPTY_TILDE_BEFORE, _lib_symbol  # noqa: PLC0415
    from ..fileformat.symbols import Resolved  # noqa: PLC0415

    own = sch.library.get(lib_id)
    if own is not None and own.node is not None:
        return Resolved(lib_id, own.node, own), None
    nickname, _, name = lib_id.partition(":")
    if not name:
        return None, "a symbol is named LIBRARY:NAME, as KiCad's libraries name them"
    tables = lib_tables.Tables.of(
        step.design.root_path.resolve().parent, Path(kicad_root) if kicad_root else None
    )
    library = tables.symbols.get(nickname)
    if library is None:
        return None, f"no symbol library is named {nickname} in this project's tables"
    found = tables.symbol(library, name)
    if found is None:
        return None, f"the library {nickname} has no symbol {name}"
    node, version = found
    return Resolved(
        lib_id, node, _lib_symbol(lib_id, node, {lib_id: node}, version < EMPTY_TILDE_BEFORE)
    ), None


def _add(edit: Edit, step: Step, index: int, change: dict) -> list[dict]:
    from . import sch_create as create  # noqa: PLC0415

    allowed = {
        "op",
        "ref",
        "symbol",
        "value",
        "footprint",
        "fields",
        "pins",
        "sheet",
        "near",
        *FLAGS,
    }
    problems = _unknown(index, change, allowed, "add")
    reference, lib_id = change.get("ref"), change.get("symbol")
    pins_wanted = change.get("pins") or {}
    if not isinstance(reference, str) or not REFERENCE.match(reference):
        problems.append(
            _problem(
                index,
                "ref is a reference: letters and a number, or letters "
                "and ? for the next free number",
            )
        )
    elif not reference.endswith("?") and reference in step.parts:
        problems.append(_problem(index, "a part already has that reference", ref=reference))
    if not isinstance(lib_id, str):
        problems.append(_problem(index, "symbol is LIBRARY:NAME"))
    if not isinstance(pins_wanted, dict) or not all(
        isinstance(k, str) and _valid_name(v) for k, v in pins_wanted.items()
    ):
        problems.append(_problem(index, "pins is an object: {pin number or name: net}"))
    # Which sheet: the one asked for, or the one the part it goes near is on.
    sheet_name, near = change.get("sheet"), change.get("near")
    instance = step.design.instances[0]
    if sheet_name is not None:
        found = [i for i in step.design.instances if i.name == sheet_name]
        if not found:
            problems.append(
                _problem(
                    index,
                    "no sheet has that path",
                    sheet=sheet_name,
                    sheets=[i.name for i in step.design.instances][:20],
                )
            )
        else:
            instance = found[0]
    near_part = None
    if near is not None:
        near_part = step.parts.get(near)
        if near_part is None:
            problems.append(_problem(index, "no part has the reference near names", near=near))
        elif sheet_name is None:
            placed_on = next(
                (
                    i
                    for i in step.design.instances
                    if near_part.symbols[0][1].instance(i.path) is not None
                    and _reference(near_part.symbols[0][1], i) == near
                ),
                None,
            )
            instance = placed_on or instance
    if problems:
        return problems
    sch = instance.schematic
    resolved, trouble = _resolve_symbol(step, sch, lib_id, edit.kicad_root)
    if resolved is None:
        return [_problem(index, trouble, symbol=lib_id)]
    if resolved.symbol.power:
        return [
            _problem(
                index, "a power symbol names a net: connect a pin to the net instead", symbol=lib_id
            )
        ]

    # The references, one per placement of the sheet: the one asked for
    # where it was asked for, the next free ones elsewhere.
    prefix = re.sub(r"(\d+|\?)\Z", "", reference)
    refs: dict[str, str] = {}
    for inst in step.placements(sch):
        if inst is instance and not reference.endswith("?"):
            refs[inst.path] = reference
        else:
            refs[inst.path] = _free_reference(step, edit, prefix)
    first_ref = refs[instance.path]

    # Each pin: the way its net is drawn on this sheet.
    ways: dict[str, _Way] = {}
    on: dict[tuple[str, str], str] = {}
    for wanted, net in pins_wanted.items():
        matched = [p for p in resolved.symbol.pins if p.number == wanted] or [
            p for p in resolved.symbol.pins if p.name == wanted
        ]
        if not matched:
            return [
                _problem(
                    index,
                    "the symbol has no such pin",
                    pin=wanted,
                    available=sorted({p.name or p.number for p in resolved.symbol.pins})[:40],
                )
            ]
        way, trouble = _way(step, index, net, sch)
        if trouble:
            return trouble
        key = f"{way.kind}:{way.text}"
        ways[key] = way
        for pin in matched:
            on[(first_ref, pin.number)] = key
    power = {k for k, w in ways.items() if w.kind in ("power", "local_power")}
    value = change.get("value") or resolved.symbol.properties.get("Value", "")
    footprint = change.get("footprint") or resolved.symbol.properties.get("Footprint", "")
    part = create.Part(first_ref, resolved, str(value), str(footprint))
    units = create._units(part, on)
    for unit in units:
        unit.spots = [spot for spot in unit.spots if not spot.pin.hidden]
        create._plan(unit, power)

    given = change.get("fields") or {}
    extra = (
        {k: v for k, v in given.items() if isinstance(v, str)} if isinstance(given, dict) else {}
    )
    flags = {k: change[k] for k in FLAGS if isinstance(change.get(k), bool)}
    canvas = step.canvas(sch)
    anchor = _anchor(step, canvas, sch, near_part, near, on)
    placed_report = []
    for unit in units:
        origin, crowded = _find_room(canvas, unit, anchor, create)
        if origin is None:
            return [
                _problem(
                    index,
                    "there is no room on the sheet for the part: every free "
                    "place would touch a wire, pin or label",
                    ref=first_ref,
                )
            ]
        unit.origin = origin
        _write_unit(edit, step, sch, unit, ways, refs, create, extra, flags)
        anchor = (create._placed_box(unit)[2] + 2 * draw.GRID, create._placed_box(unit)[1])
        placed_report.append({"unit": unit.unit, "at_mm": _mm(origin), "overlaps": crowded})
    table = sch.root.find("lib_symbols")
    if table is not None and not any(n.value(1) == lib_id for n in table.find_all("symbol")):
        table.append(copy(resolved.node))
    edit.touch(sch)

    # What must read back: the fields, and every pin on the net it was put on.
    wanted_fields: dict[str, str | None] = {"Value": str(value), "Footprint": str(footprint)}
    wanted_fields.update(extra)
    joined = []
    for inst in step.placements(sch):
        ref = refs[inst.path]
        edit.fields[ref] = dict(wanted_fields)
        edit.flags[ref] = dict(flags)
        for pin in resolved.symbol.pins:
            if pin.hidden and pin.electrical == "power_in":
                target = step.nets.get(escape(pin.name))
                onto = next(iter(_pins(target[0])), None) if target else None
                edit.join((ref, pin.number), onto, key=("global", pin.name))
                continue
            key = on.get((first_ref, pin.number))
            if key is None:
                edit.alone((ref, pin.number))
                continue
            way = ways[key]
            name = way.name(inst)
            target = step.nets.get(
                name if way.kind in ("label", "local_power") else way.net or name
            )
            onto = next(iter(_pins(target[0])), None) if target else None
            edit.join((ref, pin.number), onto, key=("net", name))
            edit.expect[(ref, pin.number)] = name
            joined.append({"pin": f"{ref}.{pin.number}", "net": name})
    edit.report.append(
        {
            "index": index,
            "op": "add",
            "ref": first_ref,
            "symbol": lib_id,
            "sheet": instance.name,
            "placed": placed_report,
            "joined": joined,
            "also_added": sorted(set(refs.values()) - {first_ref}),
            "files": [sch.path.name],
        }
    )
    return []


def _anchor(step: Step, canvas: draw.Canvas, sch: Schematic, near_part, near, on) -> tuple:
    """Where to start looking for room: beside the part named, else beside the
    part on this sheet sharing most nets with the new one, else at the top left."""
    if near_part is not None:
        for other_sch, sym in near_part.symbols:
            if other_sch is sch:
                lib = sch.library.get(sym.library_name)
                box = draw._body(sym, lib) if lib is not None else None
                if box is not None:
                    return (box[2] + 4 * draw.GRID, box[1])
    return (canvas.area[0] + 10 * draw.GRID, canvas.area[1] + 10 * draw.GRID)


def _find_room(canvas: draw.Canvas, unit, anchor: tuple[int, int], create):
    """The origin nearest `anchor`, on the grid, where the unit and all it
    draws take nothing's room and touch nothing: its pins, its wires, its
    labels. Nearer is better; a little lower or to the right, better still."""
    grid = draw.GRID
    box = unit.box
    pad = grid
    step_ = 2 * grid
    area = canvas.area
    candidates = []
    for ox in range(area[0] - box[0], area[2] - box[2] + 1, step_):
        for oy in range(area[1] - box[1], area[3] - box[3] + 1, step_):
            gx, gy = (ox // grid) * grid, (oy // grid) * grid
            dx, dy = gx - anchor[0], gy - anchor[1]
            candidates.append((dx * dx + dy * dy + (abs(dx) if dx < 0 else 0), gx, gy))
    candidates.sort()
    for _, ox, oy in candidates:
        placed = (box[0] + ox - pad, box[1] + oy - pad, box[2] + ox + pad, box[3] + oy + pad)
        if not canvas.free(placed):
            continue
        if not _clear_unit(canvas, unit, (ox, oy), create):
            continue
        return (ox, oy), 0
    return None, 0


def _clear_unit(canvas: draw.Canvas, unit, origin, create) -> bool:
    ox, oy = origin

    def at(p):
        return (p[0] + ox, p[1] + oy)

    for spot in unit.spots:
        if canvas.touching(at(spot.at)):
            return False
    for item in unit.drawn:
        if isinstance(item, create.Wire):
            a, b = at(item.a), at(item.b)
            if not canvas.clear(a, b) or canvas.touching(b):
                return False
        elif isinstance(item, (create.Label, create.Power, create.NoConnect)):
            if canvas.touching(at(item.at)):
                return False
    return True


def _write_unit(
    edit: Edit,
    step: Step,
    sch: Schematic,
    unit,
    ways: dict,
    refs: dict,
    create,
    extra: dict[str, str],
    flags: dict[str, bool],
) -> None:
    """The unit and what it draws, into the sheet and onto the canvas."""
    ox, oy = unit.origin
    canvas = step.canvas(sch)
    part = unit.part

    def at(p):
        return (p[0] + ox, p[1] + oy)

    node = create._symbol_node(
        part.lib,
        part.ref,
        part.value,
        part.footprint,
        (ox, oy),
        0,
        unit.unit,
        edit.ids,
        "",
        edit.project,
        at(unit.ref_at),
        at(unit.value_at),
        unit.fields_justify,
    )
    paths = [
        List.new(
            "path", quote(path), List.new("reference", quote(ref)), List.new("unit", str(unit.unit))
        )
        for path, ref in refs.items()
    ]
    node.replace(
        node.find("instances"),
        List.new("instances", List.new("project", quote(edit.project), *paths)),
    )
    for name, value in extra.items():
        _set_field_node(node, sch.version, name, value)
    for name, on in flags.items():
        _set_flag_node(node, name, on)
    _append(sch, node)
    canvas.take_box(create._placed_box(unit))
    for spot in unit.spots:
        canvas.take(at(spot.at), "pin")
    for number, item in enumerate(unit.drawn):
        key = (part.ref, unit.unit, number)
        if isinstance(item, create.Wire):
            _append(sch, draw.wire(at(item.a), at(item.b), edit.ids(*key, "wire")))
            canvas.take_wire(at(item.a), at(item.b))
        elif isinstance(item, create.NoConnect):
            _append(sch, draw.no_connect(at(item.at), edit.ids(*key, "nc")))
            canvas.take(at(item.at), "no_connect")
        elif isinstance(item, create.Label):
            way = ways[item.text]
            out = {0: (1, 0), 90: (0, -1), 180: (-1, 0), 270: (0, 1)}[item.angle]
            _append(
                sch,
                draw.label(
                    way.kind, way.text, at(item.at), out, edit.ids(*key, "label"), way.shape
                ),
            )
            canvas.take(at(item.at), "label")
        elif isinstance(item, create.Power):
            way = ways[item.net]
            references = {path: edit.power_reference(step) for path in refs}
            _append(
                sch,
                draw.power_symbol(
                    way.template,
                    way.template_lib,
                    way.text,
                    at(item.at),
                    item.out,
                    edit.ids,
                    key,
                    references,
                    edit.project,
                ),
            )
            _embed(sch, way.template_lib, step)
            canvas.take(at(item.at), "pin")


def _remove(edit: Edit, step: Step, index: int, change: dict) -> list[dict]:
    problems = _unknown(index, change, {"op", "ref"}, "remove")
    reference = change.get("ref")
    part, missing = _part(step, index, reference)
    if part is None or problems:
        return problems + missing
    if any(
        (lib := sch.library.get(sym.library_name)) is not None and lib.power
        for sch, sym in part.symbols
    ):
        return [
            _problem(
                index, "a power symbol names a net: disconnect the pin it is on", ref=reference
            )
        ]
    removed: dict[str, int] = defaultdict(int)
    refs = set()
    for sch, sym in part.symbols:
        lib = sch.library.get(sym.library_name)
        canvas = step.canvas(sch)
        for placed in canvas.pins(sym, lib) if lib is not None else []:
            cut = _cut(sch, step, sym, placed.pin, placed.at, False, edit.removed, removing=True)
            if isinstance(cut, str):
                return [_problem(index, cut, pin=f"{reference}.{placed.pin.number}")]
            for kind, count in cut.items():
                removed[kind] += count
        sch.root.remove(sym.node)
        removed["symbols"] += 1
        # Its drawing in the sheet's library, if nothing else there uses it.
        if not any(
            other is not sym and other.library_name == sym.library_name for other in sch.symbols
        ):
            table = sch.root.find("lib_symbols")
            for node in table.find_all("symbol") if table is not None else []:
                if node.value(1) == sym.library_name:
                    table.remove(node)
        edit.touch(sch)
        for inst in step.placements(sch):
            ref = _reference(sym, inst)
            refs.add(ref)
            lib_pins = lib.pins_of(sym.unit, sym.body_style) if lib is not None else []
            for pin in lib_pins:
                here = (ref, pin.number)
                name = step.net_of.get(here)
                left = step.nets.get(name) if name else None
                edit.vanish(here)
                if left is not None and _namer(step, *left).kind is not None:
                    rest = [p for p in _pins(left[0]) if p[0] != ref]
                    if rest:
                        edit.expect[rest[0]] = name
    edit.removed |= refs
    edit.report.append(
        {
            "index": index,
            "op": "remove",
            "ref": reference,
            "units": len(part.symbols),
            "removed": dict(removed),
            "also_removes": sorted(refs - {reference}),
            "files": sorted({sch.path.name for sch, _ in part.symbols}),
        }
    )
    return []


# -- checking the edit, before it is written -------------------------------------------


def _verify(
    edit: Edit,
    before: list[tuple[Net, list]],
    parts_before: dict[str, _Part],
    after: Design,
) -> dict[str, Any]:
    """What the edited design says, against what it had to say."""
    then = {_pins(n): n.name for n, _ in before if n.pins}
    groups: dict[int, set[Pin]] = defaultdict(set)
    for pin, number in edit.where.items():
        groups[number].add(pin)
    expected = {frozenset(g) for g in groups.values() if g}
    _, found = grouped(after)
    now = {_pins(n): n.name for n, _ in found if n.pins}
    moved = [
        {
            "pins": sorted(f"{r}.{p}" for r, p in pins)[:8],
            "expected": pins in expected,
            "before": then.get(pins),
            "after": now.get(pins),
        }
        for pins in expected ^ set(now)
    ]
    wrong_name, other_name = [], []
    by_pin = {pin: (pins, name) for pins, name in now.items() for pin in pins}
    for pin, name in edit.expect.items():
        pins, got = by_pin.get(pin, (None, None))
        if got != name:
            wrong_name.append({"pin": f"{pin[0]}.{pin[1]}", "expected": name, "after": got})
    looked = set(edit.expect) | edit.moved
    for pins, name in now.items():
        if pins in then and not pins & looked and name != then[pins]:
            other_name.append({"before": then[pins], "after": name})
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
    lost = sorted(set(parts_before) - set(parts) - edit.removed)
    return {
        "nets_join_the_pins_asked": not moved,
        "nets_named_as_asked": not wrong_name,
        "no_other_net_renamed": not other_name,
        "fields_read_back": not wrong_fields,
        "every_part_still_there": not lost,
        "nets": len(now),
        "problems": {
            "joined_or_split": moved[:10],
            "named_wrong": wrong_name[:10],
            "renamed_unasked": other_name[:10],
            "fields": wrong_fields[:10],
            "lost_parts": lost[:10],
        },
    }


def passed(verified: dict[str, Any]) -> bool:
    return all(v for v in verified.values() if isinstance(v, bool))


NET_CHECKS = ("nets_join_the_pins_asked", "nets_named_as_asked", "no_other_net_renamed")


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


def _project(design: Design) -> str:
    """The project name a symbol's instances are filed under."""
    for sch in design.schematics.values():
        for node in sch.root.find_all("symbol"):
            instances = node.find("instances")
            project = instances.find("project") if instances is not None else None
            if project is not None:
                return project.value(1) or design.project
    return design.project


OPERATIONS = {
    "set": _set,
    "rename": _rename,
    "connect": _connect,
    "disconnect": _disconnect,
    "add": _add,
    "remove": _remove,
}


def run(schematic: Path, changes: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[Path, str]]:
    """Make the changes in memory, one after another, and check them: the
    report, and each changed file's new text. Nothing is written here."""
    kicad_root = kicad_env.find_kicad_root()
    try:
        step = Step.read(schematic, {}, kicad_root)
    except (SchematicError, SexprError, UnicodeDecodeError, OSError) as exc:
        envelope.fail(
            "E_VALIDATION",
            "the schematic could not be read",
            {"schematic": str(schematic), "reason": str(exc)[:300]},
        )
        raise AssertionError("unreachable") from exc
    inputs = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in step.design.schematics
    }
    erc_before = _erc(schematic, step.design, kicad_root)
    before = [(n, k) for n, k in step.nets.values()]
    parts_before = step.parts
    edit = Edit(schematic, draw.Ids(next(iter(inputs.values()), "")), _project(step.design))
    edit.kicad_root = kicad_root
    for number, (net, _) in enumerate(before, start=1):
        for pin in _pins(net):
            edit.where[pin] = number

    sources: dict[Path, str] = {}
    problems: list[dict] = []
    for index, change in enumerate(changes):
        found = OPERATIONS[change["op"]](edit, step, index, change)
        if found:
            problems += found
            continue
        if edit.touched:
            sources.update({p: s.document.dumps() for p, s in edit.touched.items()})
            edit.touched = {}
            step = Step.read(schematic, sources, kicad_root)
    if problems:
        envelope.fail(
            "E_VALIDATION",
            "the changes cannot be made as described; nothing was written",
            {"problems": problems[:40], "problem_count": len(problems)},
        )
    report = {
        "schematic": str(schematic),
        "changes": edit.report,
        "files": sorted(str(p) for p in sources),
        "inputs": inputs,
        "verified": _verify(edit, before, parts_before, step.design),
        "erc": _erc_delta(erc_before, _erc(schematic, step.design, kicad_root)),
    }
    return report, sources


def not_checked(kicad_root: str | None) -> list[str]:
    out = [
        "the drawing is not laid out again: a longer value or a new field is placed where the "
        "old one was, and may now overlap what is beside it; a new wire and its label go "
        "where nothing is if there is room, and `drawn.overlaps` counts what they cross if "
        "there is not",
        "a board made from this schematic is not changed by this command",
    ]
    if kicad_root is None:
        out.append(
            "ERC's library rules were not checked, before or after: KiCad's installation was "
            "not found"
        )
    return out
