"""The electrical rules check, as KiCad's ERC checks a schematic.

Each rule was measured, not assumed: `tests/fixtures/erc/` is a design drawn
to ask KiCad's own ERC one question per case -- when a pin counts as
connected, which pin stands for a net nothing drives, which pair of
conflicting pins is named, when a label is dangling -- and its answers are
recorded in `tests/test_fileformat_erc.py`. On KiCad's 35 demo projects this
finds what KiCad finds, but for two labels on one of them.

What was found, in short:

- A pin is not connected when nothing on its own sheet touches it but wires:
  no other pin, no label, no no-connect flag. A pin of type no-connect
  counts for nothing, and wiring one is a finding of its own; a free pin is
  never unconnected. A supply pin a part hides is connected wherever the
  net of its name is.
- A net nothing drives is reported once, by the pin whose reference sorts
  first -- #PWR2 before #PWR11: power inputs first, then inputs. Output,
  bidirectional, tri-state, passive and power output pins drive; the others
  do not. A net with a no-connect flag is left alone.
- Conflicting pins: each pin in turn is paired with the nearest pin it
  conflicts with and has not been paired with yet, however mild the
  conflict; a pin on another sheet is as far as can be.
- Labels are judged by the net they are on: with no pin it is dangling, with
  one it is isolated -- unless a label of the net floats on nothing, which
  alone is then reported.
- Two names on one subgraph are reported once per sheet file, first
  placement in page order, naming the item that names the whole net.
- What only the drawing decides -- a wire's end joined to nothing -- is
  reported once per sheet file, however many times the sheet is placed; an
  end off the grid is reported for every placement.
- A violation names at most two items, as KiCad's do.

Which of several equivalent items KiCad names -- two of a junction's four
wires, one of two equally near pins -- follows the order of its spatial
index, and is not reproduced: `Violation.alternatives` lists every item that
could stand in their place.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .circuit import Design, Graph, PlacedPin, bus_members, graph, natural_key
from .schematic import Label, Wire, uuid_of

Point = tuple[int, int]
NM = 1_000_000

# The rules a KiCad 10 project knows, at the severities a new project gives
# them ("ignore" is off).
SEVERITIES = {
    "bus_definition_conflict": "error", "bus_entry_needed": "error",
    "bus_to_bus_conflict": "error", "bus_to_net_conflict": "error",
    "different_unit_footprint": "error", "different_unit_net": "error",
    "duplicate_reference": "error", "duplicate_sheet_names": "error",
    "endpoint_off_grid": "warning", "extra_units": "error",
    "footprint_filter": "ignore", "footprint_link_issues": "warning",
    "four_way_junction": "ignore", "global_label_dangling": "warning",
    "hier_label_mismatch": "error", "isolated_pin_label": "warning", "label_dangling": "error",
    "label_multiple_wires": "warning", "lib_symbol_issues": "warning",
    "lib_symbol_mismatch": "warning", "missing_bidi_pin": "warning",
    "missing_input_pin": "warning", "missing_power_pin": "error",
    "missing_unit": "warning", "multiple_net_names": "warning",
    "net_not_bus_member": "warning", "no_connect_connected": "warning",
    "no_connect_dangling": "warning", "pin_not_connected": "error",
    "pin_not_driven": "error", "pin_to_pin": "warning",
    "power_pin_not_driven": "error", "same_local_global_label": "warning",
    "similar_label_and_power": "warning", "similar_labels": "warning",
    "similar_power": "warning", "simulation_model_issue": "ignore",
    "single_global_label": "ignore", "unannotated": "error",
    "unconnected_wire_endpoint": "warning", "undefined_netclass": "error",
    "unit_value_mismatch": "error", "unresolved_variable": "error",
    "wire_dangling": "error",
}  # fmt: skip

# The electrical types, in the order KiCad's pin conflict matrix lists them.
TYPES = (
    "input", "output", "bidirectional", "tri_state", "passive", "free",
    "unspecified", "power_in", "power_out", "open_collector", "open_emitter", "no_connect",
)  # fmt: skip
OK, WARNING, ERROR = 0, 1, 2
# The matrix a new KiCad project has: rows and columns in TYPES' order.
PIN_MAP = [
    [0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 2],
    [0, 2, 0, 1, 0, 0, 1, 0, 2, 2, 2, 2],
    [0, 0, 0, 0, 0, 0, 1, 0, 1, 0, 1, 2],
    [0, 1, 0, 0, 0, 0, 1, 1, 2, 1, 1, 2],
    [0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 2],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2],
    [1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 2],
    [0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 2],
    [0, 2, 1, 2, 0, 0, 1, 0, 2, 2, 2, 2],
    [0, 2, 0, 1, 0, 0, 1, 0, 2, 0, 0, 2],
    [0, 2, 1, 1, 0, 0, 1, 0, 2, 0, 0, 2],
    [2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2],
]
DRIVERS = {"output", "bidirectional", "tri_state", "passive", "power_out"}
# Which pin of a conflicting pair KiCad names first: by type, in this order.
PAIR_ORDER = {
    t: i for i, t in enumerate((
        "unspecified", "open_collector", "open_emitter", "tri_state", "bidirectional",
        "output", "input", "passive", "free", "power_in", "power_out", "no_connect",
    ))
}  # fmt: skip
GRID = 50 * 25_400  # 50 mil, a new project's connection grid

# The rules checked here.
CHECKED = (
    "pin_not_connected", "power_pin_not_driven", "pin_not_driven", "pin_to_pin",
    "no_connect_connected", "no_connect_dangling", "label_dangling", "isolated_pin_label",
    "single_global_label", "same_local_global_label", "similar_labels", "similar_power",
    "similar_label_and_power", "multiple_net_names", "unconnected_wire_endpoint",
    "wire_dangling", "endpoint_off_grid", "four_way_junction", "label_multiple_wires",
)  # fmt: skip

# The rules KiCad has and this does not check yet, and what they are about.
NOT_CHECKED = {
    "unannotated": "annotation", "duplicate_reference": "annotation",
    "extra_units": "annotation", "unit_value_mismatch": "annotation",
    "missing_unit": "parts of several units", "missing_input_pin": "parts of several units",
    "missing_bidi_pin": "parts of several units", "missing_power_pin": "parts of several units",
    "different_unit_footprint": "parts of several units",
    "different_unit_net": "parts of several units",
    "hier_label_mismatch": "the hierarchy", "duplicate_sheet_names": "the hierarchy",
    "bus_definition_conflict": "buses", "bus_entry_needed": "buses",
    "bus_to_bus_conflict": "buses", "bus_to_net_conflict": "buses",
    "net_not_bus_member": "buses", "global_label_dangling": "labels",
    "unresolved_variable": "text variables", "undefined_netclass": "net classes",
    "lib_symbol_issues": "the symbol libraries", "lib_symbol_mismatch": "the symbol libraries",
    "footprint_link_issues": "the footprint libraries",
    "footprint_filter": "the footprint libraries", "simulation_model_issue": "simulation",
}  # fmt: skip


@dataclass
class Item:
    kind: str  # pin, wire, label, global_label, hierarchical_label, no_connect, sheet_pin
    uuid: str
    description: str
    position: Point

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "uuid": self.uuid,
            "description": self.description,
            "at_mm": [self.position[0] / NM, self.position[1] / NM],
        }


@dataclass
class Violation:
    rule: str
    severity: str
    message: str
    sheet: str  # the sheet instance's name, "/" or "/amp/"
    items: list[Item] = field(default_factory=list)
    # Every item that could stand where `items` stand: KiCad names one of
    # several equivalent ones -- two of a junction's four wires -- in the
    # order of its spatial index, which is not reproduced.
    alternatives: frozenset[str] = frozenset()

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "sheet": self.sheet,
            "items": [i.to_dict() for i in self.items],
        }


@dataclass
class Settings:
    severities: dict[str, str]
    pin_map: list[list[int]]
    grid: int = GRID

    @classmethod
    def of(cls, project: Path | None) -> Settings:
        """The project's rule severities and pin map, KiCad's defaults where
        it says nothing."""
        severities, pin_map, grid = dict(SEVERITIES), [row[:] for row in PIN_MAP], GRID
        if project is not None and project.exists():
            try:
                data = json.loads(project.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            erc = data.get("erc") or {}
            severities.update(erc.get("rule_severities") or {})
            given = erc.get("pin_map")
            if isinstance(given, list) and len(given) == len(TYPES):
                pin_map = given
            size = (data.get("schematic") or {}).get("connection_grid_size")
            if isinstance(size, (int, float)) and size > 0:
                grid = round(size * 25_400)
        return cls(severities, pin_map, grid)


# -- items as KiCad describes them ------------------------------------------------------


def _pin_item(pp: PlacedPin) -> Item:
    name, kind = pp.function
    hidden = "hidden pin" if pp.pin.hidden else "pin"
    return Item(
        "pin",
        pp.symbol.pin_uuids.get(pp.pin.number, ""),
        f"Symbol {pp.reference} {hidden} {pp.pin.number} [{name}, {kind}]",
        pp.position,
    )


def _wire_item(wire: Wire) -> Item:
    return Item("wire", uuid_of(wire.node), "Wire", wire.start)


def _label_item(label: Label) -> Item:
    return Item(label.kind, uuid_of(label.node), f"{label.kind} '{label.text}'", label.position)


def _flag_item(flag: tuple[Point, str]) -> Item:
    return Item("no_connect", flag[1], "No connect", flag[0])


# -- the check ------------------------------------------------------------------------


class _Check:
    def __init__(self, design: Design, settings: Settings) -> None:
        self.design = design
        self.settings = settings
        self.graph: Graph = graph(design)
        self.found: list[Violation] = []
        self.names = {i.path: i.name for i in design.instances}
        g = self.graph
        self.net_members: dict = defaultdict(list)
        self.sub_members: dict = defaultdict(list)
        aliases = design.bus_aliases()
        # A label naming a bus is on the bus, not on a net of its own.
        self.bus_labels = {
            k for k, v in g.items.items()
            if k[0] == "l" and bus_members(v.text, aliases) is not None
        }  # fmt: skip
        for key in g.items:
            if key in self.bus_labels:
                continue
            self.net_members[g.net.find(key)].append(key)
            self.sub_members[g.sheet.find(key)].append(key)
        # What only the drawing decides is reported once per sheet file, however
        # many times the sheet is placed; KiCad does.
        self.screens: set = set()
        pages = design.pages()
        self.page_order = sorted(design.instances, key=lambda i: natural_key(pages.get(i.path, "")))

    def once_per_screen(self, rule: str, path: str, *items: Item) -> bool:
        """Whether this drawing-only finding is new for its sheet file."""
        screen = next((i.schematic.path for i in self.design.instances if i.path == path), path)
        key = (rule, screen, tuple(i.uuid for i in items))
        if key in self.screens:
            return False
        self.screens.add(key)
        return True

    def report(
        self,
        rule: str,
        message: str,
        path: str,
        *items: Item,
        severity: str | None = None,
        alternatives: set[str] | None = None,
    ) -> None:
        configured = self.settings.severities.get(rule, SEVERITIES.get(rule, "error"))
        if configured == "ignore":
            return
        either = frozenset((alternatives or set()) | {i.uuid for i in items})
        self.found.append(
            Violation(
                rule,
                severity or configured,
                message,
                self.names.get(path, path),
                list(items),
                either,
            )
        )

    def uuid(self, key) -> str:
        g = self.graph
        if key[0] == "p":
            pp = g.pins_at[key]
            return pp.symbol.pin_uuids.get(pp.pin.number, "")
        if key[0] == "nc":
            return g.items[key][1]
        if key[0] == "s":
            return uuid_of(g.items[key][1].node)
        return uuid_of(g.items[key].node)

    def pin_type(self, key) -> str:
        return self.graph.pins_at[key].function[1]

    # -- pins ---------------------------------------------------------------------------

    def pins_not_connected(self) -> None:
        g = self.graph
        for key, pp in g.pins_at.items():
            if self.pin_type(key) in ("no_connect", "free"):
                continue
            # A supply pin a part hides is on the net of its name wherever
            # that net is drawn; a power symbol's pin is where it is drawn.
            hidden_supply = (
                pp.pin.hidden and pp.function[1] == "power_in" and not self._is_power(pp)
            )
            members = self.net_members[g.net.find(key)] if hidden_supply else None
            company = [k for k in (members or self.sub_members[g.sheet.find(key)]) if k != key]
            if any(
                k[0] in ("l", "s", "nc") or (k[0] == "p" and self.pin_type(k) != "no_connect")
                for k in company
            ):
                continue
            self.report("pin_not_connected", "Pin not connected", key[1], _pin_item(pp))

    def nets_not_driven(self) -> None:
        g = self.graph
        for keys in self.net_members.values():
            if any(k[0] == "nc" for k in keys):
                continue  # meant to be unconnected: nothing need drive it
            pins = sorted(
                (k for k in keys if k[0] == "p"),
                key=lambda k: (
                    natural_key(g.pins_at[k].reference),
                    natural_key(g.pins_at[k].pin.number),
                ),
            )
            types = {k: self.pin_type(k) for k in pins}
            power_in = [k for k in pins if types[k] == "power_in"]
            if power_in and not any(t == "power_out" for t in types.values()):
                first = power_in[0]
                self.report(
                    "power_pin_not_driven",
                    "Input Power pin not driven by any Output Power pins",
                    first[1],
                    _pin_item(g.pins_at[first]),
                )
                continue
            inputs = [k for k in pins if types[k] == "input"]
            if inputs and not any(t in DRIVERS for t in types.values()):
                first = inputs[0]
                self.report(
                    "pin_not_driven",
                    "Input pin not driven by any Output pins",
                    first[1],
                    _pin_item(g.pins_at[first]),
                )

    def pin_conflicts(self) -> None:
        g = self.graph
        for keys in self.net_members.values():
            pins = [k for k in keys if k[0] == "p" and self.pin_type(k) != "no_connect"]
            if len(pins) < 2:
                continue
            pins.sort(
                key=lambda k: (
                    PAIR_ORDER[self.pin_type(k)],
                    natural_key(g.pins_at[k].reference),
                    natural_key(g.pins_at[k].pin.number),
                )
            )
            tested: set = set()
            for ref in pins:
                # Of the pins this one conflicts with and has not yet been
                # paired with, the nearest -- however mild its conflict: KiCad
                # names the pair a person would look at. A pin on another sheet
                # is as far as can be; of equals, the one met last is named.
                worst, partner, nearest = OK, None, None
                ties: list = []
                here = g.pins_at[ref]
                for test in pins:
                    if test == ref or frozenset((ref, test)) in tested:
                        continue
                    tested.add(frozenset((ref, test)))
                    a = TYPES.index(self.pin_type(ref))
                    b = TYPES.index(self.pin_type(test))
                    value = self.settings.pin_map[a][b]
                    if value == OK:
                        continue
                    there = g.pins_at[test]
                    distance = (
                        (here.position[0] - there.position[0]) ** 2
                        + (here.position[1] - there.position[1]) ** 2
                        if test[1] == ref[1]
                        else float("inf")
                    )
                    if nearest is None or distance < nearest:
                        worst, partner, nearest, ties = value, test, distance, [test]
                    elif distance == nearest:
                        worst, partner = value, test
                        ties.append(test)
                if partner is None:
                    continue
                first, second = g.pins_at[ref], g.pins_at[partner]
                # The matrix says how bad, not the rule's severity.
                self.report(
                    "pin_to_pin",
                    f"Pins of type {first.function[1]} and {second.function[1]} are connected",
                    ref[1],
                    _pin_item(first),
                    _pin_item(second),
                    severity="error" if worst == ERROR else "warning",
                    # Pins as near as the one named are named by KiCad in the
                    # order of its spatial index.
                    alternatives={self.uuid(k) for k in ties},
                )

    # -- no-connect flags ---------------------------------------------------------------

    def no_connects(self) -> None:
        g = self.graph
        for key, flag in [(k, v) for k, v in g.items.items() if k[0] == "nc"]:
            company = [k for k in self.sub_members[g.sheet.find(key)] if k != key]
            pins = [k for k in company if k[0] == "p" and self.pin_type(k) != "no_connect"]
            attached = any(
                k[0] == "p" and g.pins_at[k].position == flag[0] for k in company
            ) or any(k[0] == "w" and flag[0] in (g.items[k].start, g.items[k].end) for k in company)
            on_sheet_pin = any(k[0] == "s" for k in company)
            if on_sheet_pin:
                continue
            if not attached or not pins:
                self.report(
                    "no_connect_dangling",
                    'Unconnected "no connection" flag',
                    key[1],
                    _flag_item(flag),
                )
                continue
            # A flag on a pin, where the pin's net reaches a pin elsewhere --
            # a stack of pins at one point is one. A flag on a wire's end
            # marks the wire, and KiCad lets it be.
            on_pin = [k for k in pins if g.pins_at[k].position == flag[0]]
            where = {
                (k[1], g.pins_at[k].position)
                for k in self.net_members[g.net.find(key)]
                if k[0] == "p" and self.pin_type(k) != "no_connect"
            }
            if on_pin and len(where) > 1:
                at_flag = on_pin
                self.report(
                    "no_connect_connected",
                    'A pin with a "no connection" flag is connected',
                    key[1],
                    _pin_item(g.pins_at[at_flag[0]]),
                    _flag_item(flag),
                    alternatives={self.uuid(k) for k in pins},
                )
        for key, pp in g.pins_at.items():
            if self.pin_type(key) != "no_connect":
                continue
            company = [k for k in self.sub_members[g.sheet.find(key)] if k != key and k[0] != "nc"]
            if company:
                other = company[0]
                item = (
                    _wire_item(g.items[other]) if other[0] == "w"
                    else _pin_item(g.pins_at[other]) if other[0] == "p"
                    else _label_item(g.items[other]) if other[0] == "l"
                    else None
                )  # fmt: skip
                self.report(
                    "no_connect_connected",
                    'A pin with a "no connection" flag is connected',
                    key[1],
                    *[i for i in (_pin_item(pp), item) if i is not None],
                )

    # -- labels -------------------------------------------------------------------------

    def labels(self) -> None:
        g = self.graph
        for keys in self.net_members.values():
            labels = [k for k in keys if k[0] == "l"]
            if not labels:
                continue
            floating = [k for k in labels if self.sub_members[g.sheet.find(k)] == [k]]
            if floating:
                first = floating[0]
                self.report("label_dangling", "Label not connected", first[1],
                            _label_item(g.items[first]),
                            alternatives={self.uuid(k) for k in floating})  # fmt: skip
                continue
            if any(k[0] == "nc" for k in keys):
                continue
            pins = [k for k in keys if k[0] == "p" and self.pin_type(k) != "no_connect"]
            if len(pins) > 1:
                continue
            rule, message = (
                ("label_dangling", "Label not connected") if not pins
                else ("isolated_pin_label", "Label connected to only one pin")
            )  # fmt: skip
            for k in labels:
                self.report(rule, message, k[1], _label_item(g.items[k]))

    def global_labels(self) -> None:
        g = self.graph
        by_text: dict[str, list] = defaultdict(list)
        for key, item in g.items.items():
            if key[0] == "l" and item.kind == "global_label" and key not in self.bus_labels:
                by_text[item.text].append(key)
        for keys in by_text.values():
            if len(keys) == 1:
                self.report(
                    "single_global_label",
                    "Global label only appears once in the schematic",
                    keys[0][1],
                    _label_item(g.items[keys[0]]),
                )
        for key, item in g.items.items():
            if (
                key[0] == "l"
                and item.kind == "label"
                and item.text in by_text
                and key not in self.bus_labels
            ):
                other = by_text[item.text][0]
                self.report(
                    "same_local_global_label",
                    "Local and global labels have same name",
                    other[1],
                    _label_item(g.items[other]),
                    _label_item(item),
                )

    def similar_names(self) -> None:
        g = self.graph
        seen: dict[str, list] = defaultdict(list)  # lower-case text -> [(text, key)]
        for key, item in g.items.items():
            if key[0] != "l" or item.kind == "directive_label" or key in self.bus_labels:
                continue
            folded = item.text.lower()
            for text, earlier in seen[folded]:
                if text != item.text:
                    self.report(
                        "similar_labels",
                        "Labels are similar (lower/upper case difference only)",
                        key[1],
                        _label_item(item),
                        _label_item(g.items[earlier]),
                    )
            seen[folded].append((item.text, key))
        powers: dict[str, list] = defaultdict(list)
        for key, pp in g.pins_at.items():
            if pp.power_symbol and pp.function[1] == "power_in" and self._is_power(pp):
                value = pp.symbol.properties.get("Value", "")
                for text, earlier in powers[value.lower()]:
                    if text != value:
                        self.report(
                            "similar_power",
                            "Power pins are similar (lower/upper case difference only)",
                            key[1],
                            _pin_item(pp),
                            _pin_item(g.pins_at[earlier]),
                        )
                powers[value.lower()].append((value, key))
        for folded, entries in powers.items():
            for text, power_key in entries:
                for label_text, label_key in seen.get(folded, []):
                    if label_text != text:
                        self.report(
                            "similar_label_and_power",
                            "Power pin and label are similar (lower/upper case difference only)",
                            power_key[1],
                            _pin_item(g.pins_at[power_key]),
                            _label_item(g.items[label_key]),
                        )

    def _is_power(self, pp: PlacedPin) -> bool:
        library = pp.instance.schematic.library.get(pp.symbol.library_name)
        return library is not None and library.power

    def multiple_names(self) -> None:
        g = self.graph
        # Once per sheet file, on the first placement of it in page order.
        rank = {i.path: n for n, i in enumerate(self.page_order)}
        subgraphs = sorted(self.sub_members.values(), key=lambda keys: rank.get(keys[0][1], 0))
        for keys in subgraphs:
            drivers = []
            for k in keys:
                for priority, name in g.names.get(k, []):
                    if k[0] in ("l", "p"):
                        drivers.append((priority, _bare(name), k))
            if len({name for _, name, _ in drivers}) < 2:
                continue
            drivers.sort(key=lambda d: (-d[0], d[1]))
            winner = drivers[0]
            others = [d for d in drivers if d[1] != winner[1]]
            other = others[0]
            if not self.once_per_screen(
                "multiple_net_names",
                winner[2][1],
                *(Item("", self.uuid(d[2]), "", (0, 0)) for d in (winner, other)),
            ):
                continue

            def item(key):
                return _pin_item(g.pins_at[key]) if key[0] == "p" else _label_item(g.items[key])

            self.report(
                "multiple_net_names",
                f"Both {_bare(winner[1])} and {_bare(other[1])} are attached to the same items; "
                f"{_bare(winner[1])} will be used in the netlist",
                winner[2][1],
                item(winner[2]),
                item(other[2]),
                # KiCad names the item that names the whole net, which may be
                # on another subgraph: any item of the net with that name.
                alternatives={self.uuid(d[2]) for d in drivers}
                | {
                    self.uuid(k)
                    for k in self.net_members[g.net.find(winner[2])]
                    if any(_bare(n) == winner[1] for _, n in g.names.get(k, []))
                },
            )

    # -- wires --------------------------------------------------------------------------

    def wires(self) -> None:
        g = self.graph
        for instance in self.design.instances:
            path = instance.path
            sch = instance.schematic
            wire_keys = [k for k in g.items if k[0] == "w" and k[1] == path]
            if not wire_keys:
                continue
            touching: dict[Point, int] = defaultdict(int)
            for k in wire_keys:
                touching[g.items[k].start] += 1
                touching[g.items[k].end] += 1
            anchors: set[Point] = set(sch.junctions) | set(sch.no_connects)
            for k, pp in g.pins_at.items():
                if k[1] == path:
                    anchors.add(pp.position)
            for label in sch.labels:
                anchors.add(label.position)
            for sheet in sch.sheets:
                anchors.update(pin.position for pin in sheet.pins)
            for entry in sch.bus_entries:
                anchors.update((entry.position, entry.end))
            for k in wire_keys:
                wire = g.items[k]
                for end in (wire.start, wire.end):
                    if touching[end] < 2 and end not in anchors:
                        item = _wire_item(wire)
                        if self.once_per_screen(f"end{end}", path, item):
                            self.report(
                                "unconnected_wire_endpoint", "Unconnected wire endpoint", path, item
                            )
            seen_subgraphs = set()
            for k in wire_keys:
                root = g.sheet.find(k)
                if root in seen_subgraphs:
                    continue
                seen_subgraphs.add(root)
                members = self.sub_members[root]
                if any(
                    m[0] in ("l", "s") or (m[0] == "p" and self.pin_type(m) != "no_connect")
                    for m in members
                ):
                    continue
                wires = [m for m in members if m[0] == "w"]
                if not self.once_per_screen("wire_dangling", path, _wire_item(g.items[wires[0]])):
                    continue
                self.report("wire_dangling", "Wires not connected to anything", path,
                            *[_wire_item(g.items[w]) for w in wires[:2]],
                            alternatives={self.uuid(w) for w in wires})  # fmt: skip

    def grid(self) -> None:
        g = self.graph
        step = self.settings.grid
        # Reported for every placement of a sheet, unlike an unconnected end.
        for key, wire in g.items.items():
            if key[0] in ("w", "b") and any(
                p[0] % step or p[1] % step for p in (wire.start, wire.end)
            ):
                self.report(
                    "endpoint_off_grid",
                    "Symbol pin or wire end off connection grid",
                    key[1],
                    _wire_item(wire),
                )
        # A bus entry, once for each of its ends that is off.
        for instance in self.design.instances:
            for entry in instance.schematic.bus_entries:
                for end in (entry.position, entry.end):
                    if end[0] % step or end[1] % step:
                        item = Item(
                            "bus_entry", uuid_of(entry.node), "Bus to wire entry", entry.position
                        )
                        self.report(
                            "endpoint_off_grid",
                            "Symbol pin or wire end off connection grid",
                            instance.path,
                            item,
                        )
        done = set()
        for key, pp in g.pins_at.items():
            owner = (key[1], pp.symbol.uuid)
            if owner in done or pp.function[1] == "no_connect":
                continue
            if pp.position[0] % step or pp.position[1] % step:
                done.add(owner)
                self.report(
                    "endpoint_off_grid",
                    "Symbol pin or wire end off connection grid",
                    key[1],
                    _pin_item(pp),
                )

    def junctions(self) -> None:
        g = self.graph
        for instance in self.design.instances:
            path = instance.path
            at: dict[Point, list] = defaultdict(list)
            for k, pp in g.pins_at.items():
                if k[1] == path:
                    at[pp.position].append(k)
            for k, wire in g.items.items():
                if k[0] == "w" and k[1] == path:
                    at[wire.start].append(k)
                    at[wire.end].append(k)
            for keys in at.values():
                if len(keys) < 4:
                    continue
                items = [
                    _pin_item(g.pins_at[k]) if k[0] == "p" else _wire_item(g.items[k])
                    for k in sorted(keys, key=lambda k: k[0] != "p")[:2]
                ]
                if self.once_per_screen("four_way_junction", path, *items):
                    self.report(
                        "four_way_junction",
                        "Four items connected at a single point",
                        path,
                        *items,
                        alternatives={self.uuid(k) for k in keys},
                    )

    def label_wires(self) -> None:
        g = self.graph
        for key, label in g.items.items():
            if key[0] != "l" or key in self.bus_labels:
                continue
            inside = [
                k for k, w in g.items.items()
                if k[0] == "w" and k[1] == key[1] and _inside(label.position, w)
            ]  # fmt: skip
            if len(inside) > 1 and self.once_per_screen(
                "label_multiple_wires", key[1], _label_item(label)
            ):
                self.report(
                    "label_multiple_wires",
                    "Label connects more than one wire",
                    key[1],
                    _label_item(label),
                    _wire_item(g.items[inside[1]]),
                    alternatives={self.uuid(k) for k in inside},
                )


def _inside(p: Point, wire: Wire) -> bool:
    """On the wire, and not at either end."""
    from .circuit import _on_segment  # noqa: PLC0415

    return p not in (wire.start, wire.end) and _on_segment(p, wire.start, wire.end)


def _bare(name: str) -> str:
    """A net name without its sheet path: "/amp/OUT" is OUT."""
    return name.rsplit("/", 1)[-1] if name.startswith("/") else name


def check(root: str | Path, settings: Settings | None = None) -> list[Violation]:
    """Every violation of the design whose root schematic is `root`."""
    design = Design(root)
    if settings is None:
        settings = Settings.of(Path(root).with_suffix(".kicad_pro"))
    run = _Check(design, settings)
    run.pins_not_connected()
    run.nets_not_driven()
    run.pin_conflicts()
    run.no_connects()
    run.labels()
    run.global_labels()
    run.similar_names()
    run.multiple_names()
    run.wires()
    run.grid()
    run.junctions()
    run.label_wires()
    return run.found
