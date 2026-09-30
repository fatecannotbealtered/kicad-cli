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
- The libraries are judged once per sheet file too. A sheet's copy of a
  symbol is still its library's while the library's pins are where the copy
  has them, its fields have the copy's values, and the drawing, the units,
  being a power symbol and the pin-name offset are the same; nothing else
  counts (`_Drawn`). No footprint at all is one a symbol's filters do not
  allow.
- A violation names at most two items, as KiCad's do.

Which of several equivalent items KiCad names -- two of a junction's four
wires, one of two equally near pins -- follows the order of its spatial
index, and is not reproduced: `Violation.alternatives` lists every item that
could stand in their place.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import annotation, lib_tables
from .circuit import Design, Graph, PlacedPin, _on_segment, bus_members, graph, natural_key
from .schematic import EMPTY_TILDE_BEFORE, Label, Wire, _lib_symbol, _unit_style, uuid_of

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
    "unannotated", "duplicate_reference", "extra_units", "unit_value_mismatch",
    "missing_unit", "missing_input_pin", "missing_bidi_pin", "missing_power_pin",
    "different_unit_footprint", "different_unit_net", "hier_label_mismatch",
    "duplicate_sheet_names", "bus_to_net_conflict", "net_not_bus_member",
    "unresolved_variable", "undefined_netclass",
    "lib_symbol_issues", "lib_symbol_mismatch", "footprint_link_issues", "footprint_filter",
)  # fmt: skip

# Checked only when KiCad's installation is known: its library tables name
# their libraries by paths relative to it.
LIBRARY = ("lib_symbol_issues", "lib_symbol_mismatch", "footprint_link_issues", "footprint_filter")

# Checked here, and not by KiCad's command-line ERC, which does not run the
# annotation check its editor runs first. What counts as an annotation error
# was measured on the warning KiCad's netlist export gives instead
# (`fileformat/annotation.py`).
ANNOTATION = ("unannotated", "duplicate_reference", "extra_units", "unit_value_mismatch")

# The rules KiCad has and this does not check, and why.
NOT_CHECKED = {
    "simulation_model_issue": "simulation",
    # Not reported by KiCad 10's own ERC on any drawing tried for them: a wire
    # on a bus with and without a junction, buses of different members
    # joined directly and through a sheet, one alias defined two ways, global
    # labels floating, on stubs and on buses.
    "bus_entry_needed": "never seen reported", "bus_to_bus_conflict": "never seen reported",
    "bus_definition_conflict": "never seen reported",
    "global_label_dangling": "never seen reported",
}  # fmt: skip


@dataclass
class Item:
    # pin, wire, bus, bus_entry, label, global_label, hierarchical_label,
    # directive_label, no_connect, sheet, sheet_pin, symbol, text
    kind: str
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
        # A directive label sets a net's class and connects nothing.
        self.directive = {
            k for k, v in g.items.items() if k[0] == "l" and v.kind == "directive_label"
        }
        for key in g.items:
            if key in self.bus_labels:
                continue
            self.net_members[g.net.find(key)].append(key)
            self.sub_members[g.sheet.find(key)].append(key)
        # A bus's label on a wire is a label there, if a wrong one.
        self.bus_label_wires = set()
        for key in self.bus_labels:
            position = g.items[key].position
            self.bus_label_wires.update(
                k for k, w in g.items.items()
                if k[0] == "w" and k[1] == key[1] and _on_segment(position, w.start, w.end)
            )  # fmt: skip
        # Bus labels a junction puts on a net that has a label of its own:
        # KiCad judges them as the net's labels (`buses`).
        self.joined_bus_labels: dict = defaultdict(list)
        # Subgraphs a junction joins to a bus: connected, if wrongly.
        self.on_bus: set = set()
        # What only the drawing decides is reported once per sheet file, however
        # many times the sheet is placed; KiCad does.
        self.screens: set = set()
        # Symbols as `_Drawn` has them: a library's, by its file and name; a
        # sheet file's copies, by the file and the copy's name.
        self.drawn_libraries: dict = {}
        self.drawn_copies: dict = {}
        try:
            self.project = json.loads(
                design.root_path.with_suffix(".kicad_pro").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            self.project = {}
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
        powered: dict = defaultdict(list)  # subgraph -> power symbols with only each other
        for key, pp in g.pins_at.items():
            if self.pin_type(key) in ("no_connect", "free"):
                continue
            # A supply pin a part hides is on the net of its name wherever
            # that net is drawn; a power symbol's pin is where it is drawn.
            power_symbol = self._is_power(pp)
            hidden_supply = pp.pin.hidden and pp.function[1] == "power_in" and not power_symbol
            members = self.net_members[g.net.find(key)] if hidden_supply else None
            company = [k for k in (members or self.sub_members[g.sheet.find(key)]) if k != key]
            if g.sheet.find(key) in self.on_bus or any(
                (k[0] == "l" and k not in self.directive)
                or k[0] in ("s", "nc")
                or (k[0] == "w" and k in self.bus_label_wires)
                for k in company
            ):
                continue
            others = [k for k in company if k[0] == "p" and self.pin_type(k) != "no_connect"]
            if power_symbol:
                # Power symbols with no part between them, on a net that has no
                # part or label anywhere, are one finding, named by one of them.
                if not any(not self._is_power(g.pins_at[k]) for k in others) and not any(
                    (k[0] == "p" and not self._is_power(g.pins_at[k])
                     and self.pin_type(k) != "no_connect")
                    or (k[0] == "l" and k not in self.directive)
                    for k in self.net_members[g.net.find(key)]
                ):  # fmt: skip
                    powered[g.sheet.find(key)].append(key)
                continue
            if others:
                continue
            self.report("pin_not_connected", "Pin not connected", key[1], _pin_item(pp))
        for keys in powered.values():
            self.report(
                "pin_not_connected",
                "Pin not connected",
                keys[0][1],
                _pin_item(g.pins_at[keys[0]]),
                alternatives={self.uuid(k) for k in keys},
            )

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
                    alternatives=self._same_pin(first, power_in),
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
                    alternatives=self._same_pin(first, inputs),
                )

    def _same_pin(self, first, keys) -> set[str]:
        """The pins that sort with `first`: one pin of a part placed once per
        unit, which KiCad names by any of its copies."""
        g = self.graph
        want = (g.pins_at[first].reference, g.pins_at[first].pin.number)
        return {
            self.uuid(k) for k in keys
            if (g.pins_at[k].reference, g.pins_at[k].pin.number) == want
        }  # fmt: skip

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
                    # KiCad names any pin of the net, from run to run.
                    alternatives={
                        self.uuid(k)
                        for k in self.net_members[g.net.find(key)]
                        if k[0] == "p" and self.pin_type(k) != "no_connect"
                    },
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
        for root, keys in self.net_members.items():
            labels = [k for k in keys if k[0] == "l" and k not in self.directive]
            labels += self.joined_bus_labels.get(root, [])
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
        subgraphs = sorted(
            (keys for keys in self.sub_members.values() if keys),
            key=lambda keys: rank.get(keys[0][1], 0),
        )
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
                    (m[0] == "l" and m not in self.directive)
                    or m[0] == "s"
                    or (m[0] == "p" and self.pin_type(m) != "no_connect")
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
            if key[0] != "l" or key in self.bus_labels or key in self.directive:
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

    # -- references ---------------------------------------------------------------------

    def _placements(self) -> dict[str, list]:
        """Every placed unit of every part, by reference."""
        placed: dict[str, list] = defaultdict(list)
        for instance in self.design.instances:
            for symbol in instance.schematic.symbols:
                inst = symbol.instance(instance.path)
                reference = inst.reference if inst else symbol.properties.get("Reference", "")
                unit = inst.unit if inst else symbol.unit
                placed[reference].append((instance, symbol, unit))
        return placed

    def annotation(self) -> None:
        by_name = {i.name: i.path for i in self.design.instances}
        for problem in annotation.problems(self.design):
            items = [
                Item("symbol", symbol, f"Symbol {problem.reference}", (0, 0))
                for _, symbol in problem.symbols
            ]
            path = by_name.get(problem.symbols[0][0], "") if problem.symbols else ""
            self.report(
                problem.rule,
                problem.detail,
                path,
                *items[:2],
                alternatives={i.uuid for i in items},
            )

    def units(self) -> None:
        """Parts of several units: every unit placed, one footprint for all,
        and a pin every unit has on one net in all of them."""
        g = self.graph
        pins_of: dict[str, list] = defaultdict(list)
        for key, pp in g.pins_at.items():
            pins_of[pp.reference].append(key)
        for reference, entries in self._placements().items():
            if not reference or reference.endswith("?") or reference.startswith("#"):
                continue
            instance, symbol, _ = entries[0]
            library = instance.schematic.library.get(symbol.library_name)
            if library is None or library.unit_count < 2:
                continue  # one reference on parts of one unit is an annotation error
            items = [_symbol_item(s, reference, i.path) for i, s, _ in entries]
            either = {i.uuid for i in items}
            have = {unit for _, _, unit in entries}
            missing = [u for u in range(1, library.unit_count + 1) if u not in have]
            if missing:
                kinds = {
                    p.electrical for u in missing for p in library.pins_of(u, symbol.body_style)
                }
                where = instance.path
                self.report("missing_unit", f"Symbol {reference} has units not placed", where,
                            items[0], alternatives=either)  # fmt: skip
                for kind, rule, what in (
                    ("input", "missing_input_pin", "input"),
                    ("bidirectional", "missing_bidi_pin", "bidirectional"),
                    ("power_in", "missing_power_pin", "power input"),
                ):
                    if kind in kinds:
                        self.report(
                            rule,
                            f"Symbol {reference} has a unit not placed with {what} pins",
                            where,
                            items[0],
                            alternatives=either,
                        )
            footprints = [s.properties.get("Footprint", "") for _, s, _ in entries]
            if len(set(footprints)) > 1:
                other = next(n for n, f in enumerate(footprints) if f != footprints[0])
                self.report(
                    "different_unit_footprint",
                    f"Units of {reference} have different footprints",
                    instance.path,
                    items[0],
                    items[other],
                )
            by_number: dict[str, list] = defaultdict(list)
            for key in pins_of.get(reference, []):
                by_number[g.pins_at[key].pin.number].append(key)
            for number, keys in by_number.items():
                if len({g.pins_at[k].symbol.uuid for k in keys}) < 2:
                    continue
                nets = [g.net.find(k) for k in keys]
                if len(set(nets)) < 2:
                    continue
                other = next(k for k in keys if g.net.find(k) != nets[0])
                self.report(
                    "different_unit_net",
                    f"Pin {number} of {reference} is on different nets in different units",
                    keys[0][1],
                    _pin_item(g.pins_at[keys[0]]),
                    _pin_item(g.pins_at[other]),
                )

    # -- the hierarchy --------------------------------------------------------------------

    def sheets(self) -> None:
        paths = {i.path: i for i in self.design.instances}
        for instance in self.design.instances:
            seen: dict[str, Item] = {}
            for sheet in instance.schematic.sheets:
                item = Item("sheet", sheet.uuid, f"Sheet '{sheet.name}'", sheet.position)
                if sheet.name in seen:
                    self.report("duplicate_sheet_names", "Two sheets of one name on one sheet",
                                instance.path, seen[sheet.name], item)  # fmt: skip
                else:
                    seen[sheet.name] = item
                child = paths.get(f"{instance.path}/{sheet.uuid}")
                if child is None:
                    continue
                inside = {
                    label.text for label in child.schematic.labels
                    if label.kind == "hierarchical_label"
                }  # fmt: skip
                outside = {pin.name for pin in sheet.pins}
                for pin in sheet.pins:
                    if pin.name not in inside:
                        self.report(
                            "hier_label_mismatch",
                            f"Sheet pin {pin.name} has no hierarchical label inside the sheet",
                            instance.path,
                            Item(
                                "sheet_pin",
                                uuid_of(pin.node),
                                f"Sheet pin '{pin.name}'",
                                pin.position,
                            ),  # fmt: skip
                        )
                for label in child.schematic.labels:
                    if label.kind == "hierarchical_label" and label.text not in outside:
                        self.report(
                            "hier_label_mismatch",
                            f"Hierarchical label {label.text} has no sheet pin outside the sheet",
                            child.path,
                            _label_item(label),
                        )

    # -- buses ------------------------------------------------------------------------------

    def buses(self) -> None:
        """Nets and buses meeting where they should not, members that are not,
        and buses of two names."""
        from .circuit import PRIORITY, _UnionFind  # noqa: PLC0415

        g = self.graph
        aliases = self.design.bus_aliases()
        rank = {i.path: n for n, i in enumerate(self.page_order)}
        for instance in sorted(self.design.instances, key=lambda i: rank.get(i.path, 0)):
            path = instance.path
            sch = instance.schematic
            buses = [w for w in sch.wires if w.kind == "bus"]
            if not buses and not any(k[1] == path for k in self.bus_labels):
                continue
            junctions = set(sch.junctions)
            groups = _UnionFind()
            for i, a in enumerate(buses):
                groups.add(i)
                for j in range(i):
                    b = buses[j]
                    if {a.start, a.end} & {b.start, b.end} or any(
                        _on_segment(p, a.start, a.end) and _on_segment(p, b.start, b.end)
                        for p in junctions
                    ):
                        groups.union(i, j)
            on_bus: dict = defaultdict(list)  # bus group -> label keys on it
            for key, label in g.items.items():
                if key[0] != "l" or key[1] != path or key in self.directive:
                    continue
                hit = next(
                    (i for i, b in enumerate(buses) if _on_segment(label.position, b.start, b.end)),
                    None,
                )
                is_bus = key in self.bus_labels
                if hit is not None and not is_bus:
                    self.report("bus_to_net_conflict", "A net label on a bus", path,
                                _label_item(label), _wire_item(buses[hit]))  # fmt: skip
                elif hit is None and is_bus:
                    wire = next(
                        (k for k in self.bus_label_wires if k[1] == path
                         and _on_segment(label.position, g.items[k].start, g.items[k].end)),
                        None,
                    )  # fmt: skip
                    if wire is not None:
                        self.report("bus_to_net_conflict", "A bus label on a wire", path,
                                    _wire_item(g.items[wire]), _label_item(label))  # fmt: skip
                if hit is not None and is_bus:
                    on_bus[groups.find(hit)].append(key)
            # Two names on one bus.
            for keys in on_bus.values():
                names = sorted(
                    {(-PRIORITY.get(g.items[k].kind, 0), g.items[k].text, k) for k in keys}
                )
                texts = {text for _, text, _ in names}
                if len(texts) < 2:
                    continue
                winner = names[0]
                other = next(n for n in names if n[1] != winner[1])
                first, second = _label_item(g.items[winner[2]]), _label_item(g.items[other[2]])
                if self.once_per_screen("multiple_net_names", path, first, second):
                    self.report(
                        "multiple_net_names",
                        f"Both {winner[1]} and {other[1]} name one bus; {winner[1]} is used",
                        path,
                        first,
                        second,
                    )
            # A junction putting a wire on a bus.
            wire_keys = [k for k in g.items if k[0] == "w" and k[1] == path]
            for point in junctions:
                hit = next(
                    (i for i, b in enumerate(buses) if _on_segment(point, b.start, b.end)), None
                )
                if hit is None:
                    continue
                wire = next(
                    (k for k in wire_keys
                     if _on_segment(point, g.items[k].start, g.items[k].end)),
                    None,
                )  # fmt: skip
                if wire is None:
                    continue
                net_labels = [
                    k for k in self.sub_members.get(g.sheet.find(wire), [])
                    if k[0] == "l" and k not in self.directive
                ]  # fmt: skip
                bus_labels = on_bus.get(groups.find(hit), [])
                first = (
                    _label_item(g.items[net_labels[0]]) if net_labels else _wire_item(g.items[wire])
                )
                second = (
                    _label_item(g.items[bus_labels[0]]) if bus_labels else _wire_item(buses[hit])
                )
                self.report("bus_to_net_conflict", "A wire joined to a bus", path, first, second)
                self.on_bus.add(g.sheet.find(wire))
                if net_labels and bus_labels:
                    self.joined_bus_labels[g.net.find(wire)].extend(bus_labels)
                    candidates = sorted(
                        (-PRIORITY.get(g.items[k].kind, 0), g.items[k].text, k)
                        for k in net_labels + bus_labels
                    )
                    winner = candidates[0]
                    other = next((c for c in candidates if c[1] != winner[1]), None)
                    if other is not None:
                        self.report(
                            "multiple_net_names",
                            f"Both {winner[1]} and {other[1]} are attached to the same items",
                            path,
                            _label_item(g.items[winner[2]]),
                            _label_item(g.items[other[2]]),
                        )
            # A net off a bus through an entry must be one of the bus's members.
            for entry in sch.bus_entries:
                ends = (entry.position, entry.end)
                hit = next(
                    ((i, e) for e in ends for i, b in enumerate(buses)
                     if _on_segment(e, b.start, b.end)),
                    None,
                )  # fmt: skip
                if hit is None:
                    continue
                bus_index, bus_end = hit
                wire_end = ends[1] if bus_end == ends[0] else ends[0]
                wire = next(
                    (k for k in wire_keys if wire_end in (g.items[k].start, g.items[k].end)), None
                )
                if wire is None:
                    continue
                names = [
                    g.items[k].text for k in self.sub_members.get(g.sheet.find(wire), [])
                    if k[0] == "l" and k not in self.directive
                ]  # fmt: skip
                members = {
                    name
                    for k in on_bus.get(groups.find(bus_index), [])
                    for _, name in (bus_members(g.items[k].text, aliases) or [])
                }
                if names and members and not any(name in members for name in names):
                    self.report(
                        "net_not_bus_member",
                        f"Net {names[0]} is not a member of the bus it leaves",
                        path,
                        Item(
                            "bus_entry", uuid_of(entry.node), "Bus to wire entry", entry.position
                        ),  # fmt: skip
                        _wire_item(buses[bus_index]),
                    )

    # -- text and net classes -------------------------------------------------------------

    def text_variables(self) -> None:
        """A ${NAME} nothing defines, in free text or in a part's fields."""
        variables = {name.upper() for name in self.project.get("text_variables") or {}}
        for instance in self.design.instances:
            sch = instance.schematic
            for text in sch.texts:
                if _unresolved(text.text, set(), variables):
                    item = Item("text", uuid_of(text.node), f"Text '{text.text}'", text.position)
                    if self.once_per_screen("unresolved_variable", instance.path, item):
                        self.report("unresolved_variable", "Unresolved text variable",
                                    instance.path, item)  # fmt: skip
            for symbol in sch.symbols:
                fields = {name.upper() for name in symbol.properties}
                if any(_unresolved(v, fields, variables) for v in symbol.properties.values()):
                    inst = symbol.instance(instance.path)
                    reference = inst.reference if inst else symbol.properties.get("Reference", "")
                    item = _symbol_item(symbol, reference, instance.path)
                    if self.once_per_screen("unresolved_variable", instance.path, item):
                        self.report("unresolved_variable", "Unresolved text variable",
                                    instance.path, item)  # fmt: skip

    # -- libraries ------------------------------------------------------------------------

    def libraries(self, tables: lib_tables.Tables) -> None:
        """Each part's symbol and footprint found where its nickname says, and
        its footprint one its symbol's filters allow. Once a part, however
        many times its sheet is placed."""
        for instance in self.design.instances:
            for symbol in instance.schematic.symbols:
                inst = symbol.instance(instance.path)
                reference = inst.reference if inst else symbol.properties.get("Reference", "")
                item = _symbol_item(symbol, reference, instance.path)
                if not self.once_per_screen("libraries", instance.path, item):
                    continue
                self._symbol_link(tables, symbol, item, instance)
                self._footprint_link(tables, symbol, item, instance.path)
                library = instance.schematic.library.get(symbol.library_name)
                if library is not None:
                    self._footprint_filter(library, symbol, item, instance.path)

    def _symbol_link(self, tables, symbol, item: Item, instance) -> None:
        nickname, _, name = symbol.lib_id.partition(":")
        if not name:
            return
        library = tables.symbols.get(nickname)
        if library is None:
            message = f"The current configuration does not include the symbol library '{nickname}'"
        elif library.kind == "KiCad" and not library.path.is_file():
            message = f"The symbol library '{nickname}' was not found at '{library.location}'"
        else:
            names = tables.symbol_names(library)
            if names is None:
                return
            if name in names:
                if self._differs(tables, library, name, instance.schematic, symbol.library_name):
                    self.report(
                        "lib_symbol_mismatch",
                        f"Symbol '{name}' doesn't match copy in library '{nickname}'",
                        instance.path,
                        item,
                    )
                return
            message = f"Symbol '{name}' not found in symbol library '{nickname}'"
        self.report("lib_symbol_issues", message, instance.path, item)

    def _differs(self, tables, library, name: str, schematic, copy_name: str) -> bool:
        """Whether a sheet's copy of a symbol is not its library's."""
        copy = schematic.library.get(copy_name)
        if copy is None or copy.node is None:
            return False
        if (library.path, name) not in self.drawn_libraries:
            found = tables.symbol(library, name)
            self.drawn_libraries[library.path, name] = _drawn(*found) if found else None
        theirs = self.drawn_libraries[library.path, name]
        if theirs is None:
            return False
        if (schematic.path, copy_name) not in self.drawn_copies:
            self.drawn_copies[schematic.path, copy_name] = _drawn(copy.node, schematic.version)
        return not self.drawn_copies[schematic.path, copy_name].matches(theirs)

    def _footprint_link(self, tables, symbol, item: Item, path: str) -> None:
        nickname, _, name = symbol.properties.get("Footprint", "").partition(":")
        if not name:
            return
        library = tables.footprints.get(nickname)
        # A library whose folder is not there is not loaded, and KiCad says
        # so as of one it does not know.
        if library is None or (library.kind == "KiCad" and not library.path.is_dir()):
            message = (
                f"The current configuration does not include the footprint library '{nickname}'"
            )
        elif library.kind == "KiCad" and not (library.path / f"{name}.kicad_mod").is_file():
            message = f"Footprint '{name}' not found in library '{nickname}'"
        else:
            return
        self.report("footprint_link_issues", message, path, item)

    def _footprint_filter(self, library, symbol, item: Item, path: str) -> None:
        filters = library.properties.get("ki_fp_filters", "").split()
        assigned = symbol.properties.get("Footprint", "")
        # No footprint at all is one the filters do not allow, too.
        if not filters:
            return
        name = assigned.partition(":")[2] or assigned
        # As KiCad matches: without case, a filter with a colon against the
        # whole name, one without against the footprint's own.
        if any(
            fnmatch.fnmatchcase((assigned if ":" in f else name).lower(), f.lower())
            for f in filters
        ):
            return
        self.report(
            "footprint_filter",
            f"Assigned footprint ({name.lower()}) doesn't match footprint filters "
            f"({' '.join(filters)})",
            path,
            item,
        )

    def net_classes(self) -> None:
        """A directive label naming a net class the project does not have."""
        settings = self.project.get("net_settings") or {}
        classes = {c.get("name") for c in settings.get("classes") or []} | {"Default"}
        for instance in self.design.instances:
            for label in instance.schematic.labels:
                if label.kind != "directive_label" or label.node is None:
                    continue
                for prop in label.node.find_all("property"):
                    wanted = prop.value(2) or ""
                    if prop.value(1) == "Netclass" and wanted and wanted not in classes:
                        item = _label_item(label)
                        if self.once_per_screen("undefined_netclass", instance.path, item):
                            self.report("undefined_netclass", f"Net class {wanted} is not defined",
                                        instance.path, item)  # fmt: skip


# Text variables KiCad defines itself: of the sheet and its title block, of
# a part, of a label's net.
BUILT_IN_VARIABLES = {
    "#", "##", "SHEETNAME", "SHEETPATH", "SHEETFILE", "FILENAME", "FILEPATH", "PROJECTNAME",
    "PROJECTPATH", "CURRENT_DATE", "ISSUE_DATE", "REVISION", "TITLE", "COMPANY", "PAPER",
    "KICAD_VERSION", "VARIANT", "VARIANTNAME", "REFERENCE", "VALUE", "FOOTPRINT", "DATASHEET",
    "DESCRIPTION", "FOOTPRINT_LIBRARY", "FOOTPRINT_NAME", "UNIT", "SHORT_REFERENCE",
    "SYMBOL_LIBRARY", "SYMBOL_NAME", "SYMBOL_DESCRIPTION", "SYMBOL_KEYWORDS",
    "EXCLUDE_FROM_BOM", "EXCLUDE_FROM_BOARD", "EXCLUDE_FROM_SIM", "DNP", "NET_NAME",
    "SHORT_NET_NAME", "NET_CLASS", "PIN_NAME", "INTERSHEET_REFS", "CONNECTION_TYPE", "OP",
    "ERC_ERROR", "ERC_WARNING", "KIPRJMOD",
} | {f"COMMENT{n}" for n in range(1, 10)}  # fmt: skip
_VARIABLE = re.compile(r"\$\{([^}]*)\}")


def _unresolved(text: str, fields: set[str], project: set[str]) -> bool:
    """Whether a ${NAME} in `text` is one nothing defines. Cross-references
    (${U1:VALUE}), functions (${NET_NAME(3)}) and the environment's paths
    (${KICAD9_SYMBOL_DIR}) are taken as defined."""
    for name in _VARIABLE.findall(text):
        upper = name.upper()
        if (
            upper in BUILT_IN_VARIABLES
            or upper in fields
            or upper in project
            or ":" in name
            or "(" in name
            or upper.startswith("KICAD")
            or name in os.environ
        ):
            continue
        return True
    return False


@dataclass(frozen=True)
class _Drawn:
    """A symbol as KiCad compares a sheet's copy of it with its library's.

    Asked of KiCad one difference at a time (`tests/fixtures/erc/erc3`) and
    checked against its verdict on the 3200 parts of its demo projects whose
    library is installed. What counts, the library's side against the copy:
    each of the library's pins -- by unit, body style and number -- where the
    copy has it; each of its fields' values, a field the copy lacks being
    empty; and, both ways, the drawing -- every line, shape and fill, an arc
    either way round the same -- the units, their names, being a power
    symbol, and how far in the pin names sit. What does not: a pin's length,
    direction, type, shape, name or being hidden; pins or fields only the
    copy has; text; line colour; where fields sit; the BOM and board flags.
    """

    pins: frozenset
    drawing: tuple
    units: tuple
    power: tuple
    pin_name_offset: int
    fields: dict

    def matches(self, library: _Drawn) -> bool:
        return (
            library.pins <= self.pins
            and (self.drawing, self.units, self.power, self.pin_name_offset)
            == (library.drawing, library.units, library.power, library.pin_name_offset)
            and all(self.fields.get(name, "") == value for name, value in library.fields.items())
        )


# KiCad's pin-name offset where a symbol does not give one: 20 mil.
PIN_NAME_OFFSET = 508_000


def _drawn(node, version: int) -> _Drawn:
    symbol = _lib_symbol(node.value(1) or "", node, {node.value(1): node},
                         version < EMPTY_TILDE_BEFORE)  # fmt: skip
    own_name = node.value(1) or ""
    drawing = []
    names = []
    for unit in node.find_all("symbol"):
        which = _unit_style(unit.value(1) or "", own_name)
        for item in unit.lists():
            if item.head == "unit_name":
                names.append((which, item.value(1) or ""))
            if item.head in ("pin", "unit_name", "text", "text_box"):
                continue
            geometry = [_xy(item.find(part)) for part in ("start", "end", "center", "mid")
                        if item.find(part) is not None]  # fmt: skip
            if item.head == "arc":
                geometry = sorted(geometry[:2]) + geometry[2:]
            radius = item.find("radius")
            if radius is not None:
                geometry.append(_nm(radius.atom(1)))
            points = item.find("pts")
            if points is not None:
                geometry.append(tuple(_xy(p) for p in points.lists()))
            stroke = item.find("stroke")
            width = stroke.find("width") if stroke is not None else None
            line = stroke.find("type") if stroke is not None else None
            fill = item.find("fill")
            filled = fill.find("type") if fill is not None else None
            drawing.append(
                (
                    which,
                    item.head,
                    tuple(geometry),
                    _nm(width.atom(1)) if width is not None else 0,
                    line.atom(1) if line is not None else "default",
                    filled.atom(1) if filled is not None else "none",
                )
            )
    pin_names = node.find("pin_names")
    offset = pin_names.find("offset") if pin_names is not None else None
    return _Drawn(
        pins=frozenset((p.unit, p.body_style, p.number, p.position) for p in symbol.pins),
        drawing=tuple(sorted(drawing, key=repr)),
        units=(symbol.unit_count, tuple(sorted(names))),
        power=(symbol.power, symbol.local_power),
        pin_name_offset=_nm(offset.atom(1)) if offset is not None else PIN_NAME_OFFSET,
        fields=symbol.properties,
    )


def _nm(atom) -> int:
    try:
        return round(float(atom) * NM)
    except (TypeError, ValueError):
        return 0


def _xy(node) -> tuple[int, ...]:
    return tuple(_nm(a) for a in node.values()[:2])


def _symbol_item(symbol, reference: str, _path: str) -> Item:
    value = symbol.properties.get("Value", "")
    return Item("symbol", symbol.uuid, f"Symbol {reference} [{value}]", symbol.position)


def _inside(p: Point, wire: Wire) -> bool:
    """On the wire, and not at either end."""
    return p not in (wire.start, wire.end) and _on_segment(p, wire.start, wire.end)


def _bare(name: str) -> str:
    """A net name without its sheet path: "/amp/OUT" is OUT."""
    return name.rsplit("/", 1)[-1] if name.startswith("/") else name


def check(
    root: str | Path,
    settings: Settings | None = None,
    kicad_root: str | Path | None = None,
    design: Design | None = None,
) -> list[Violation]:
    """Every violation of the design whose root schematic is `root`. The
    library rules are checked when `kicad_root`, KiCad's installation, is
    given: the library tables name their libraries relative to it. A design
    already read -- an edit not yet written -- is checked as it is."""
    design = design if design is not None else Design(root)
    if settings is None:
        settings = Settings.of(Path(root).with_suffix(".kicad_pro"))
    run = _Check(design, settings)
    run.buses()
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
    run.annotation()
    run.units()
    run.sheets()
    run.text_variables()
    run.net_classes()
    if kicad_root is not None:
        run.libraries(lib_tables.Tables.of(Path(root).resolve().parent, Path(kicad_root)))
    return run.found
