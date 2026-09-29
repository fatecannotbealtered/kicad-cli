"""The circuit a schematic draws: which pins are joined, and what each net is called.

A design is a tree of sheet instances. On each, things are joined by where
they touch, and KiCad is stricter about it than it looks on screen:

- two wires join where their ends meet; a wire ending on the middle of
  another joins it only if a junction dot is there, and so do two crossing;
- a pin joins a wire at the wire's end, not along it; a label joins a wire
  anywhere along it;
- pins, labels and sheet pins at one point join each other;

and across sheets by name: local labels within a sheet instance, global
labels and power symbols across the design, and a hierarchical label with
the pin of the same name on the sheet symbol that places its sheet. A power
input pin a library hides joins the global net of its name, as KiCad has
always done.

A net is named by the strongest name on it: a global label, then a power
symbol, then a local label -- prefixed with its sheet's path, "/amp/OUT" --
then a hierarchical label, then a sheet pin; a net with none is named after
one of its pins, "Net-(U4-FB+)", and a pin joined to nothing is
"unconnected-(J1-Pad1)". Which of several equal names wins, and which pin
names an unnamed net, was measured on KiCad's own netlists of its demo
projects (`tests/test_fileformat_circuit.py`).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .schematic import LibPin, Schematic, Sheet, Symbol

Point = tuple[int, int]

# Stronger names win. Ordered as KiCad resolves a net's name.
PRIORITY = {
    "pin": 1,
    "sheet_pin": 2,
    "hierarchical_label": 3,
    "label": 4,
    "local_power": 5,
    "power": 6,
    "global_label": 7,
}


@dataclass(slots=True)
class SheetInstance:
    path: str  # "/<root uuid>/<sheet uuid>...": what symbols' instances name
    name: str  # "/" or "/amp/": what local net names are prefixed with
    schematic: Schematic
    parent: SheetInstance | None = None
    sheet: Sheet | None = None  # the sheet symbol in the parent that places this one
    page: str = ""

    @property
    def board_path(self) -> str:
        """The path without the root's uuid, as the netlist and the board write it."""
        parts = [p for p in self.path.split("/") if p][1:]
        return "/" + "".join(p + "/" for p in parts)


@dataclass(slots=True)
class PlacedPin:
    instance: SheetInstance
    symbol: Symbol
    pin: LibPin
    reference: str
    position: Point
    # A no-connect flag is on what this pin is wired to on its own sheet.
    flagged: bool = False

    @property
    def function(self) -> tuple[str, str]:
        """The pin's name and electrical type, as the placed symbol chose them:
        a pin given an alternate function is that function."""
        chosen = self.symbol.alternates.get(self.pin.number)
        if chosen and chosen in self.pin.alternates:
            return chosen, self.pin.alternates[chosen][0]
        return self.pin.name, self.pin.electrical

    @property
    def power_symbol(self) -> bool:
        return self.reference.startswith("#")


@dataclass
class Net:
    name: str
    pins: list[PlacedPin] = field(default_factory=list)
    names: list[tuple[int, str]] = field(default_factory=list)  # (priority, name)
    no_connect: bool = False  # a no-connect flag is on it

    @property
    def board_pins(self) -> list[PlacedPin]:
        """Its pins on parts that go on the board: what a netlist lists."""
        return [pp for pp in self.pins if pp.symbol.on_board]


class Design:
    """A whole design: the root schematic and every sheet instance under it."""

    def __init__(self, root: str | Path) -> None:
        self.root_path = Path(root)
        self._files: dict[Path, Schematic] = {}
        root_schematic = self._schematic(self.root_path)
        self.project = self.root_path.stem
        top = SheetInstance("/" + root_schematic.uuid, "/", root_schematic, page="1")
        self.instances: list[SheetInstance] = []
        # Sheets placed whose file is not there, as "/amp/ (amp.kicad_sch)".
        # KiCad leaves their parts out without a word, and so does this.
        self.missing: list[str] = []
        self._walk(top)

    def _schematic(self, path: Path) -> Schematic:
        key = path.resolve()
        if key not in self._files:
            self._files[key] = Schematic.load(path)
        return self._files[key]

    def _walk(self, instance: SheetInstance) -> None:
        self.instances.append(instance)
        folder = (instance.schematic.path or self.root_path).parent
        for sheet in instance.schematic.sheets:
            path = folder / sheet.file
            if not path.exists():
                self.missing.append(f"{instance.name}{sheet.name}/ ({sheet.file})")
                continue
            child = SheetInstance(
                path=f"{instance.path}/{sheet.uuid}",
                name=f"{instance.name}{sheet.name}/",
                schematic=self._schematic(path),
                parent=instance,
                sheet=sheet,
                page=sheet.pages.get(instance.path, ""),
            )
            self._walk(child)

    def pages(self) -> dict[str, str]:
        """Each sheet instance's page number, by path, as KiCad numbers them.

        A number already taken by an instance met earlier in the hierarchy is
        given the lowest number no sheet has: CM5_MINIMA_3 has two page 7s
        and its PCIe-M2 sheet is page 2 to KiCad; vme-wren's second 26 and 33
        are 71 and 72. The netlist lists parts in this order.
        """
        used = {instance.page for instance in self.instances}
        taken: set[str] = set()
        out: dict[str, str] = {}
        again = []
        for instance in self.instances:
            if instance.page and instance.page in taken:
                again.append(instance)
                continue
            taken.add(instance.page)
            out[instance.path] = instance.page
        number = 1
        for instance in again:
            while str(number) in used or str(number) in taken:
                number += 1
            taken.add(str(number))
            out[instance.path] = str(number)
        return out

    def bus_aliases(self) -> dict[str, list[str]]:
        """Every `(bus_alias ...)` of every sheet: aliases are the design's."""
        out: dict[str, list[str]] = {}
        for schematic in self._files.values():
            for node in schematic.root.find_all("bus_alias"):
                members = node.find("members")
                out[node.value(1) or ""] = members.values() if members is not None else []
        return out

    # -- what is placed -----------------------------------------------------------------

    def placed_pins(self, instance: SheetInstance) -> list[PlacedPin]:
        out = []
        library = instance.schematic.library
        for symbol in instance.schematic.symbols:
            inst = symbol.instance(instance.path)
            reference = inst.reference if inst else symbol.properties.get("Reference", "")
            unit = inst.unit if inst else symbol.unit
            lib = library.get(symbol.library_name)
            if lib is None:
                continue
            for pin in lib.pins_of(unit, symbol.body_style):
                out.append(
                    PlacedPin(instance, symbol, pin, reference, symbol.transform(pin.position))
                )
        return out


# -- joining things on one sheet ---------------------------------------------------------


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict = {}

    def add(self, key) -> None:
        self.parent.setdefault(key, key)

    def find(self, key):
        parent = self.parent
        parent.setdefault(key, key)
        root = key
        while parent[root] != root:
            root = parent[root]
        while parent[key] != root:
            parent[key], key = root, parent[key]
        return root

    def union(self, a, b) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _on_segment(p: Point, a: Point, b: Point) -> bool:
    """Whether p lies on the segment from a to b, ends included."""
    (px, py), (ax, ay), (bx, by) = p, a, b
    if (bx - ax) * (py - ay) != (by - ay) * (px - ax):
        return False
    return min(ax, bx) <= px <= max(ax, bx) and min(ay, by) <= py <= max(ay, by)


def connect(design: Design, keep_empty: bool = False) -> list[Net]:
    """Every net of the design, pins and names.

    Parts kept off the board are on their nets like any other: they take part
    in naming them. `Net.board_pins` leaves them out. With ``keep_empty``,
    nets that have a name and no pins -- a label on a wire to nowhere -- are
    listed too: KiCad numbers them.
    """
    uf = _UnionFind()
    names: dict = defaultdict(list)  # item key -> [(priority, name)]
    pins_at: dict = {}  # item key -> PlacedPin
    hier = {}  # (instance path, label text) -> key
    sheet_pins = {}  # (child instance path, pin name) -> key
    bus_hier = {}  # (instance path, text) -> (bus key, members)
    bus_pins = {}  # (child instance path, text) -> (bus key, members)
    aliases = design.bus_aliases()

    def local(path: str, text: str):
        """Everything of one name on one sheet instance is one net."""
        key = ("local", path, text)
        uf.add(key)
        return key

    def global_(text: str):
        key = ("global", text)
        uf.add(key)
        return key

    # What touches what on one sheet, before names join anything: a
    # no-connect flag speaks for that much and no more.
    drawn = _UnionFind()
    both = _Both(uf, drawn)
    strong: list = []  # keys of power symbol pins and global labels

    for instance in design.instances:
        sch = instance.schematic
        path = instance.path
        all_wires = sch.wires
        wires = [w for w in all_wires if w.kind == "wire"]
        buses = [w for w in all_wires if w.kind == "bus"]
        wire_keys = [("w", path, i) for i in range(len(wires))]
        bus_keys = [("b", path, i) for i in range(len(buses))]
        for key in wire_keys + bus_keys:
            uf.add(key)
        points: dict[Point, list] = defaultdict(list)  # things that attach at a point

        def attach(key, point, points=points):
            uf.add(key)
            points[point].append(key)

        for pp_index, pp in enumerate(design.placed_pins(instance)):
            key = ("p", path, pp_index)
            pins_at[key] = pp
            attach(key, pp.position)
            lib = sch.library.get(pp.symbol.library_name)
            if lib is not None and lib.power and pp.pin.electrical == "power_in":
                strong.append(key)
                value = escape(pp.symbol.properties.get("Value", ""))
                if lib.local_power:
                    names[key].append((PRIORITY["local_power"], instance.name + value))
                    uf.union(key, local(path, "\0power:" + value))
                else:
                    names[key].append((PRIORITY["power"], value))
                    uf.union(key, global_(value))
                    uf.union(key, local(path, value))
            elif pp.pin.hidden and pp.pin.electrical == "power_in":
                names[key].append((PRIORITY["power"], escape(pp.pin.name)))
                uf.union(key, global_(escape(pp.pin.name)))

        # Wires and buses join where their ends meet, and anywhere else only
        # at a junction.
        junctions = set(sch.junctions)
        ends = _ends(wire_keys, wires, both)
        through = _through(junctions, wire_keys, wires, both)
        bus_ends = _ends(bus_keys, buses, uf)
        bus_through = _through(junctions, bus_keys, buses, uf)

        label_bus = {}  # label index -> (bus key, members), for labels on a bus
        for li, label in enumerate(sch.labels):
            key = ("l", path, li)
            text = escape(label.text)
            members = bus_members(label.text, aliases)
            if members is not None:
                bus = next(
                    (k for k, w in zip(bus_keys, buses, strict=True)
                     if _on_segment(label.position, w.start, w.end)),
                    None,
                )  # fmt: skip
                if bus is None:
                    bus = ("bl", path, li)  # a bus label on nothing: its own bus
                    uf.add(bus)
                label_bus[li] = (bus, members)
                if label.kind == "hierarchical_label":
                    bus_hier[(path, text)] = (bus, members)
                continue
            attach(key, label.position)
            for wire_key, wire in zip(wire_keys, wires, strict=True):
                if _on_segment(label.position, wire.start, wire.end):
                    both.union(key, wire_key)
            if label.kind == "global_label":
                strong.append(key)
                names[key].append((PRIORITY["global_label"], text))
                uf.union(key, global_(text))
                uf.union(key, local(path, text))
            elif label.kind == "label":
                names[key].append((PRIORITY["label"], instance.name + text))
                uf.union(key, local(path, text))
            elif label.kind == "hierarchical_label":
                names[key].append((PRIORITY["hierarchical_label"], instance.name + text))
                hier[(path, text)] = key
                uf.union(key, local(path, text))

        sheet_bus = []  # (bus key, members) of this sheet's bus sheet pins
        for sheet in sch.sheets:
            child_path = f"{path}/{sheet.uuid}"
            for pi, pin in enumerate(sheet.pins):
                text = escape(pin.name)
                members = bus_members(pin.name, aliases)
                if members is not None:
                    bus = next(iter(bus_ends.get(pin.position, ())), None) or next(
                        iter(bus_through.get(pin.position, ())), None
                    )
                    if bus is None:
                        bus = ("bp", path, sheet.uuid, pi)
                        uf.add(bus)
                    bus_pins[(child_path, text)] = (bus, members)
                    sheet_bus.append((bus, members))
                    continue
                key = ("s", path, sheet.uuid, pi)
                attach(key, pin.position)
                names[key].append((PRIORITY["sheet_pin"], f"{instance.name}{sheet.name}/{text}"))
                sheet_pins[(child_path, text)] = key

        # A no-connect flag joins what it marks, and says so of the whole net.
        for ni, point in enumerate(sch.no_connects):
            attach(("nc", path, ni), point)

        # A pin or a sheet pin joins a wire at the wire's end, or anywhere a
        # junction marks.
        for point, keys in points.items():
            for other in keys[1:]:
                both.union(keys[0], other)
            for wire_key in ends.get(point, ()):
                both.union(keys[0], wire_key)
            for wire_key in through.get(point, ()):
                both.union(keys[0], wire_key)

        # A bus carries its members: on this sheet, each member is the net of
        # the member's own name, "ETH_PI.TRD2_P" or "D3" -- and a bus label
        # names them, as a label of its kind would.
        for li, (bus, members) in label_bus.items():
            label = sch.labels[li]
            for member_key, member_name in members:
                node = local(path, escape(member_name))
                uf.union(_member(uf, bus, member_key), node)
                if label.kind == "global_label":
                    names[node].append((PRIORITY["global_label"], escape(member_name)))
                else:
                    names[node].append((PRIORITY[label.kind], instance.name + escape(member_name)))

        # A bus with no label of its own is named by the sheet pins on it, and
        # its members by theirs: two such buses here carrying USB{VBUS CC1}
        # are one bus. A bus with a label is named by the label.
        labelled = {uf.find(bus) for bus, _ in label_bus.values()}
        for bus, members in sheet_bus:
            if uf.find(bus) in labelled:
                continue
            for member_key, member_name in members:
                uf.union(_member(uf, bus, member_key), local(path, escape(member_name)))

    for (child_path, text), key in hier.items():
        pin_key = sheet_pins.get((child_path, text))
        if pin_key is not None:
            both.union(key, pin_key)
    # Across a sheet boundary, a bus's members join the parent's bus's
    # members of the same name -- the prefix before the braces may differ.
    for (child_path, text), (child_bus, members) in bus_hier.items():
        found = bus_pins.get((child_path, text))
        if found is None:
            continue
        parent_bus, parent_members = found
        for member_key, _ in members:
            uf.union(
                _member(uf, child_bus, member_key), _member(uf, uf.find(parent_bus), member_key)
            )

    # A flag marks the pin wired to it -- across a sheet's edge too -- when
    # that is all the wiring reaches: pins at one point (a stack of them
    # counts as one) and no power symbol or global label. Reaching more, the
    # pin is connected after all and the flag is a mistake ERC reports.
    flagged = {drawn.find(k) for k in list(drawn.parent) if k[0] == "nc"}
    flagged -= {drawn.find(k) for k in strong}
    where: dict = defaultdict(set)
    for key, pp in pins_at.items():
        where[drawn.find(key)].add((pp.instance.path, pp.position))
    for key, pp in pins_at.items():
        root = drawn.find(key)
        pp.flagged = root in flagged and len(where[root]) == 1

    groups: dict = defaultdict(list)
    for key in uf.parent:
        groups[uf.find(key)].append(key)

    nets = []
    for keys in groups.values():
        pins = [pins_at[k] for k in keys if k in pins_at and not pins_at[k].power_symbol]
        found = [n for k in keys for n in names.get(k, [])]
        if not pins and not (keep_empty and found):
            continue
        flagged = any(k[0] == "nc" for k in keys)
        nets.append(Net(name="", pins=pins, names=found, no_connect=flagged))
    for net in nets:
        net.name = _name(net)
        # A pin every unit of a part carries -- its supply -- is placed once
        # per unit, and a net lists it once. (Units not joined to each other
        # put their copies on different nets, and KiCad lists it on each.)
        seen: set[tuple[str, str]] = set()
        unique = []
        for pp in net.pins:
            if (pp.reference, pp.pin.number) not in seen:
                seen.add((pp.reference, pp.pin.number))
                unique.append(pp)
        net.pins = unique
    _deduplicate(nets)
    nets.sort(key=lambda n: natural_key(n.name))
    return nets


class _Both:
    """Joins in two union-finds at once: the circuit, and what is drawn."""

    def __init__(self, first: _UnionFind, second: _UnionFind) -> None:
        self.first, self.second = first, second

    def union(self, a, b) -> None:
        self.first.union(a, b)
        self.second.union(a, b)


def _ends(keys: list, segments: list, uf) -> dict:
    ends: dict[Point, list] = defaultdict(list)
    for key, segment in zip(keys, segments, strict=True):
        ends[segment.start].append(key)
        ends[segment.end].append(key)
    for group in ends.values():
        for other in group[1:]:
            uf.union(group[0], other)
    return ends


def _through(junctions: set, keys: list, segments: list, uf) -> dict:
    through: dict[Point, list] = {}
    for point in junctions:
        through[point] = [
            k for k, s in zip(keys, segments, strict=True) if _on_segment(point, s.start, s.end)
        ]
        for other in through[point][1:]:
            uf.union(through[point][0], other)
    return through


def _member(uf: _UnionFind, bus, member_key):
    """The node standing for one member of one bus: joined across the
    buses that are one bus, since `bus` is looked up at its root."""
    key = ("member", uf.find(bus), member_key)
    uf.add(key)
    return key


# -- bus names ------------------------------------------------------------------------------

_VECTOR = re.compile(r"^(?P<prefix>.*?)\[(?P<a>\d+)\.\.(?P<b>\d+)\]$")
_MEMBER_SEPARATOR = re.compile(r"[\s,]+")
_ESCAPES = {"slash", "backslash", "lt", "gt", "colon", "dblquote", "quote", "space", "tab",
            "return", "comma", "brace", "lbrace", "rbrace", "at", "dollar", "tilde"}  # fmt: skip


def bus_members(text: str, aliases: dict[str, list[str]]) -> list[tuple[str, str]] | None:
    """The members of a bus named ``text`` -- (key, net name) pairs -- or
    None when it names a single net.

    ``D[0..7]`` is eight nets, D0 to D7. ``USB{DP DM}`` is two, USB.DP and
    USB.DM; a member may be an alias defined in the design, or a vector
    itself. Braces are also text markup -- ``+V_{ADJ}``, ``~{RESET}``,
    ``{slash}`` -- and those name a single net. Members are matched across a
    sheet boundary by key: a vector's by position, a group's by the name
    after the prefix.
    """
    vector = _VECTOR.match(text)
    if vector and "{" not in text:
        prefix, a, b = vector["prefix"], int(vector["a"]), int(vector["b"])
        step = 1 if b >= a else -1
        return [(f"#{i}", f"{prefix}{n}") for i, n in enumerate(range(a, b + step, step))]
    group = _group(text)
    if group is None:
        return None
    prefix, members = group
    out = []
    for token in members:
        for name in _expand(token, aliases, set()):
            out.append((name, f"{prefix}.{name}" if prefix else name))
    return out


def _group(text: str) -> tuple[str, list[str]] | None:
    """``PREFIX{A, B C}`` as ("PREFIX", ["A", "B", "C"]), or None.

    The group's brace is the first one that is not markup -- "_{" is a
    subscript, "^{" a superscript, "~{" an overbar -- and it must close at
    the end of the text. Inside, members are split on commas and spaces, but
    not within their own markup: SWD_TRG{~{RESET}, SWDIO} is two members.
    """
    if not text.endswith("}"):
        return None
    open_at = next(
        (i for i, ch in enumerate(text) if ch == "{" and (i == 0 or text[i - 1] not in "_^~")),
        None,
    )
    if open_at is None:
        return None
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                if i != len(text) - 1:
                    return None
                break
    inside = text[open_at + 1 : -1]
    if inside.strip() in _ESCAPES:
        return None
    members, current, depth = [], "", 0
    for ch in inside:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if depth == 0 and (ch == "," or ch.isspace()):
            if current:
                members.append(current)
            current = ""
            continue
        current += ch
    if current:
        members.append(current)
    return text[:open_at], members


def _expand(token: str, aliases: dict[str, list[str]], seen: set) -> list[str]:
    if token in aliases and token not in seen:
        out = []
        for member in aliases[token]:
            out += _expand(member, aliases, seen | {token})
        return out
    vector = _VECTOR.match(token)
    if vector:
        prefix, a, b = vector["prefix"], int(vector["a"]), int(vector["b"])
        step = 1 if b >= a else -1
        return [f"{prefix}{n}" for n in range(a, b + step, step)]
    return [token]


def _name(net: Net) -> str:
    if net.names:
        # The strongest kind of name; among those, the one on the sheet
        # nearest the root; among those, the least as plain text.
        best = max(p for p, _ in net.names)
        return min((n for p, n in net.names if p == best), key=lambda n: (n.count("/"), n))
    if not net.pins:
        return ""
    if len(net.pins) == 1 or net.no_connect:
        # A pin on its own, or a net someone marked as meant to be unconnected.
        labels = [_pin_label(pp, always_pad=True) for pp in net.pins]
        return f"unconnected-({min(labels)})"
    # A name that falls back on a pad number is a poor one: any pin with a
    # name of its own is preferred. Among equals, the least as plain text --
    # not in natural order: Q11 before Q9.
    labels = [_pin_label(pp) for pp in net.pins]
    return f"Net-({min(labels, key=lambda label: ('-Pad' in label, label))})"


def _pin_label(pp: PlacedPin, always_pad: bool = False) -> str:
    """How a net named after this pin names it: "U1B-E", "IC29-GL-Pad27" for
    a name the part gives several pins, "J18-PadB01" for a pin with none."""
    if not _named(pp):
        return f"{pp.reference}-Pad{pp.pin.number}"
    label = f"{_unit_reference(pp)}-{escape(pp.function[0])}"
    if always_pad or _shared_name(pp):
        label += f"-Pad{pp.pin.number}"
    return label


def _shared_name(pp: PlacedPin) -> bool:
    """Whether the unit has another pin of this name. Units of one part do
    not count: both halves of a dual op-amp have an OUT."""
    lib = pp.instance.schematic.library.get(pp.symbol.library_name)
    if lib is None:
        return False
    inst = pp.symbol.instance(pp.instance.path)
    unit = inst.unit if inst else pp.symbol.unit
    numbers = {p.number for p in lib.pins_of(unit, pp.symbol.body_style) if p.name == pp.pin.name}
    return len(numbers) > 1


def _named(pp: PlacedPin) -> bool:
    """A pin has a name of its own: not empty, not just its number. "~" is a
    name like any other here -- KiCad writes "Net-(R10-~-Pad2)"."""
    name = pp.function[0]
    return bool(name) and name != pp.pin.number


def _unit_reference(pp: PlacedPin) -> str:
    """The reference with the unit's letter, on a part of several units: U1B."""
    lib = pp.instance.schematic.library.get(pp.symbol.library_name)
    if lib is None or lib.unit_count < 2:
        return pp.reference
    inst = pp.symbol.instance(pp.instance.path)
    unit = inst.unit if inst else pp.symbol.unit
    return pp.reference + _unit_letter(unit)


def _unit_letter(unit: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA."""
    out = ""
    while unit > 0:
        unit, rest = divmod(unit - 1, 26)
        out = chr(ord("A") + rest) + out
    return out


def _deduplicate(nets: list[Net]) -> None:
    seen: dict[str, int] = {}
    for net in nets:
        if net.name in seen:
            seen[net.name] += 1
            net.name = f"{net.name}_{seen[net.name]}"
        else:
            seen[net.name] = 0


def escape(text: str) -> str:
    """A label as KiCad writes it into a net name: a slash in it is {slash},
    since slashes separate the sheets of the path."""
    return text.replace("/", "{slash}")


_DIGITS = re.compile(r"(\d+)")


def natural_key(text: str) -> tuple:
    """Order strings as KiCad orders net names: character by character, a
    run of digits against another as a number -- D9 before D10 -- and against
    anything else as the digit it starts with, so "0" sorts after "/outb"."""
    key = []
    for part in _DIGITS.split(text):
        if not part:
            continue
        if part.isdigit():
            key.append((ord("0"), int(part)))
        else:
            key.extend((ord(c), -1) for c in part)
    return tuple(key)
