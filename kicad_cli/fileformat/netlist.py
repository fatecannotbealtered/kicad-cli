"""A design's netlist, as KiCad's own S-expression export writes it.

The netlist is what a board is built from and kept in step with: every part
with its value, footprint and fields and the uuids that link it to its
footprint, and every net with the pins on it. This builds it from the
schematics themselves (`circuit.py` for the nets) and writes it in the
format `kicad-cli sch export netlist --format kicadsexpr` writes, so anything
that reads KiCad's netlist reads this one.

What goes in and how it is spelled was measured on KiCad's netlists of its
demo projects (`tests/test_fileformat_netlist.py`):

- a part marked "exclude from board" is left out altogether, and so are
  power symbols;
- a pin's function is its name and number, "OUT_7"; a pin with no name has
  none;
- a pin alone on its net is typed with "+no_connect" after its type.
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .circuit import Design, Net, SheetInstance, connect, natural_key
from .schematic import Symbol
from .sexpr import List, quote, render

MANDATORY = ("Reference", "Value", "Footprint", "Datasheet", "Description")
FIELDS_LAST = ("Footprint", "Datasheet", "Description")


@dataclass
class Component:
    ref: str
    value: str
    footprint: str
    datasheet: str
    fields: list[tuple[str, str]]
    lib: str
    part: str
    lib_description: str
    properties: list[tuple[str, str | None]]
    sheet_names: str
    sheet_tstamps: str
    tstamps: list[str]
    units: list[tuple[str, list[str]]] = field(default_factory=list)


@dataclass
class Node:
    ref: str
    pin: str
    function: str | None
    type: str


@dataclass
class NetEntry:
    code: int
    name: str
    net_class: str
    nodes: list[Node]


@dataclass
class Netlist:
    source: str
    components: list[Component]
    nets: list[NetEntry]


def build(root: str | Path | Design) -> Netlist:
    design = root if isinstance(root, Design) else Design(root)
    return Netlist(
        source=str(design.root_path.resolve()),
        components=components(design),
        nets=nets(design, connect(design, keep_empty=True)),
    )


# -- parts --------------------------------------------------------------------------


def components(design: Design) -> list[Component]:
    """One entry per reference. A part's units on one sheet are one component
    listing each unit's uuid; the sheet is the first, in page order, that has
    one of its units -- units on other sheets are not listed. Parts are listed
    sheet by sheet in page order, by reference within a sheet."""
    pages = design.pages()
    order = sorted(design.instances, key=lambda i: natural_key(pages.get(i.path, "")))
    rank = {instance.path: index for index, instance in enumerate(order)}
    found: dict[str, tuple[SheetInstance, list[tuple[Symbol, int]]]] = {}
    for instance in order:
        for symbol in instance.schematic.symbols:
            inst = symbol.instance(instance.path)
            reference = inst.reference if inst else symbol.properties.get("Reference", "")
            if not reference or reference.startswith("#") or not symbol.on_board:
                continue
            lib = instance.schematic.library.get(symbol.library_name)
            if lib is not None and lib.power:
                continue
            unit = inst.unit if inst else symbol.unit
            if reference not in found:
                found[reference] = (instance, [])
            if found[reference][0] is instance:
                found[reference][1].append((symbol, unit))
    out = []
    for reference in sorted(found, key=lambda r: (rank[found[r][0].path], natural_key(r))):
        instance, units = found[reference]
        placed = [(instance, symbol, unit) for symbol, unit in units]
        out.append(_component(reference, instance, units[0][0], placed))
    return out


def _component(reference, instance, symbol, placed) -> Component:
    props = _fields(symbol.properties)
    library = instance.schematic.library.get(symbol.library_name)
    lib_props = library.properties if library is not None else {}
    custom = [(k, v) for k, v in props.items() if k not in MANDATORY and not k.startswith("ki_")]
    fields = custom + [(k, props.get(k, "")) for k in FIELDS_LAST]
    properties: list[tuple[str, str | None]] = [(k, v) for k, v in custom]
    sheet = instance.sheet
    properties.append(("Sheetname", sheet.name if sheet else design_name(instance)))
    # As the sheet symbol names it -- "sch/Debugger.kicad_sch" -- not the file's own name.
    properties.append(("Sheetfile", sheet.file if sheet else Path(instance.schematic.path).name))
    # The fields someone added to the sheet symbol, carried to every part on
    # the sheet -- a BOM groups by them.
    if sheet is not None and sheet.node is not None:
        for prop in sheet.node.find_all("property"):
            name = prop.value(1)
            if name not in ("Sheetname", "Sheetfile", "Sheet name", "Sheet file"):
                properties.append((name, prop.value(2) or ""))
    for flag, on in (
        ("exclude_from_bom", not symbol.in_bom),
        ("exclude_from_board", not symbol.on_board),
        ("dnp", symbol.dnp),
    ):
        if on:
            properties.append((flag, None))
    for key in ("ki_keywords", "ki_fp_filters"):
        if key in lib_props:
            properties.append((key, lib_props[key]))
    # A symbol changed from its library carries its own copy, named for it:
    # no library, just the name.
    if symbol.lib_name:
        lib, part = "", symbol.lib_name
    else:
        lib, _, part = symbol.lib_id.rpartition(":")
    # Every unit the part has, placed or not, in order, with its pins: left
    # to right, top to bottom in the library's drawing. Pins stacked at one
    # point KiCad lists in an order its sort leaves to chance; here they keep
    # the file's.
    units = []
    if library is not None:
        for unit in range(1, library.unit_count + 1):
            pins = sorted(
                library.pins_of(unit, symbol.body_style),
                key=lambda p: (p.position[0], -p.position[1]),
            )
            units.append((_unit_name(unit), [p.number for p in pins]))
    return Component(
        ref=reference,
        value=expand(props.get("Value", ""), props, instance),
        footprint=props.get("Footprint", ""),
        datasheet=props.get("Datasheet", ""),
        fields=fields,
        lib=lib,
        part=part,
        lib_description=lib_props.get("Description", "").strip(),
        properties=properties,
        sheet_names=instance.name,
        sheet_tstamps=instance.board_path,
        tstamps=[sym.uuid for _, sym, _ in placed],
        units=units,
    )


_VARIABLE = re.compile(r"\$\{([^}]+)\}")


def expand(text: str, props: dict[str, str], instance: SheetInstance) -> str:
    """Text variables in a field, as a netlist shows them: ${NAME} is the
    symbol's own field of that name, whatever its case."""
    if "${" not in text:
        return text
    fields = {k.upper(): v for k, v in props.items()}

    def value(match: re.Match) -> str:
        name = match.group(1).upper()
        if name in fields:
            return fields[name]
        if name == "SHEETNAME":
            return instance.sheet.name if instance.sheet else ""
        return match.group(0)

    return _VARIABLE.sub(value, text)


def _fields(properties: dict[str, str]) -> dict[str, str]:
    """A symbol's fields as KiCad reads them. A field named like a mandatory
    one in another case -- "DESCRIPTION" -- is that field, and the
    description is trimmed; other fields keep their spaces and line breaks."""
    out = dict(properties)
    out["Description"] = out.get("Description", "").strip()
    for name in MANDATORY:
        for key in [k for k in out if k != name and k.lower() == name.lower()]:
            value = out.pop(key)
            if not out.get(name):
                out[name] = value
    return out


def design_name(instance: SheetInstance) -> str:
    return Path(instance.schematic.path).stem


def _unit_name(unit: int) -> str:
    out = ""
    while unit > 0:
        unit, rest = divmod(unit - 1, 26)
        out = chr(ord("A") + rest) + out
    return out


# -- nets ---------------------------------------------------------------------------


def nets(design: Design, found: list[Net]) -> list[NetEntry]:
    """The nets that reach the board, numbered as KiCad numbers them: in
    order over every net of the design, a label's net with no pins and a net
    of parts kept off the board included."""
    classes = _NetClasses(design.root_path.with_suffix(".kicad_pro"))
    out = []
    for code, net in enumerate(found, start=1):
        pins = net.board_pins
        if not pins:
            continue
        nodes = []
        for pp in sorted(pins, key=lambda p: (p.reference, p.pin.number)):
            name, kind = pp.function
            # A pin marked as meant to be unconnected: wired to a no-connect
            # flag, on its sheet or through its sheet's pin, and to no power
            # symbol or global label. A flag elsewhere on the net -- reached
            # through a local label -- marks nothing here.
            if kind != "no_connect" and pp.flagged:
                kind += "+no_connect"
            function = f"{name}_{pp.pin.number}" if name else None
            nodes.append(Node(pp.reference, pp.pin.number, function, kind))
        out.append(NetEntry(code, net.name, classes.name_for(net.name), nodes))
    return out


class _NetClasses:
    """A net's class as the project assigns it: by name, then by pattern.

    A pattern matches the whole net name as a wildcard ("osc*") or as a
    regular expression ("uio\\d+"). A net of several classes is named for all
    of them, highest priority first; and when they leave a parameter of a
    board's rules undefined, Default supplies it and is named last: a class
    giving only colours is "85Ohm-diff_PCIE,Default".
    """

    ESSENTIAL = (
        "clearance", "track_width", "via_diameter", "via_drill",
        "microvia_diameter", "microvia_drill", "diff_pair_width", "diff_pair_gap",
    )  # fmt: skip

    def __init__(self, project: Path) -> None:
        self.patterns: list[tuple[str, str]] = []
        try:
            settings = json.loads(project.read_text(encoding="utf-8")).get("net_settings", {})
        except (OSError, ValueError):
            settings = {}
        self.classes = {c.get("name"): c for c in settings.get("classes") or []}
        self.assigned: dict[str, list[str]] = settings.get("netclass_assignments") or {}
        for entry in settings.get("netclass_patterns") or []:
            self.patterns.append((entry.get("pattern", ""), entry.get("netclass", "Default")))

    def name_for(self, net: str) -> str:
        if net in self.assigned:
            given = self.assigned[net]
            matched = list(given if isinstance(given, list) else [given])
        else:
            matched = [cls for pattern, cls in self.patterns if _matches(pattern, net)]
        matched = [c for c in dict.fromkeys(matched) if c != "Default"]
        if not matched:
            return "Default"
        matched.sort(key=lambda c: self.classes.get(c, {}).get("priority", 0))
        defined = set()
        for name in matched:
            defined |= set(self.classes.get(name, {}))
        if any(key not in defined for key in self.ESSENTIAL):
            matched.append("Default")
        return ",".join(matched)


def _matches(pattern: str, net: str) -> bool:
    if fnmatch.fnmatchcase(net, pattern):
        return True
    try:
        return re.fullmatch(pattern, net) is not None
    except re.error:
        return False


# -- writing ------------------------------------------------------------------------


def dumps(netlist: Netlist, date: str = "", tool: str = "kicad-cli") -> str:
    """The netlist as KiCad's S-expression export writes it."""
    root = List.new("export")
    root.append(List.new("version", quote("E")))
    design = List.new("design")
    design.append(List.new("source", quote(netlist.source)))
    design.append(List.new("date", quote(date)))
    design.append(List.new("tool", quote(tool)))
    root.append(design)
    comps = List.new("components")
    for c in netlist.components:
        comp = List.new("comp")
        comp.append(List.new("ref", quote(c.ref)))
        comp.append(List.new("value", quote(c.value)))
        if c.footprint:
            comp.append(List.new("footprint", quote(c.footprint)))
        if c.datasheet:
            comp.append(List.new("datasheet", quote(c.datasheet)))
        fields = List.new("fields")
        for name, value in c.fields:
            entry = List.new("field", List.new("name", quote(name)))
            if value:
                entry.append(quote(value))
            fields.append(entry)
        comp.append(fields)
        comp.append(
            List.new(
                "libsource",
                List.new("lib", quote(c.lib)),
                List.new("part", quote(c.part)),
                List.new("description", quote(c.lib_description)),
            )
        )
        for name, value in c.properties:
            prop = List.new("property", List.new("name", quote(name)))
            if value is not None:
                prop.append(List.new("value", quote(value)))
            comp.append(prop)
        comp.append(
            List.new(
                "sheetpath",
                List.new("names", quote(c.sheet_names)),
                List.new("tstamps", quote(c.sheet_tstamps)),
            )
        )
        comp.append(List.new("tstamps", *[quote(t) for t in c.tstamps]))
        units = List.new("units")
        for unit_name, pins in c.units:
            listed = [List.new("pin", List.new("num", quote(n))) for n in pins]
            unit_pins = List.new("pins", *listed)
            units.append(List.new("unit", List.new("name", quote(unit_name)), unit_pins))
        comp.append(units)
        comps.append(comp)
    root.append(comps)
    nets_node = List.new("nets")
    for n in netlist.nets:
        net = List.new("net")
        net.append(List.new("code", quote(str(n.code))))
        net.append(List.new("name", quote(n.name)))
        net.append(List.new("class", quote(n.net_class)))
        for node in n.nodes:
            entry = List.new(
                "node", List.new("ref", quote(node.ref)), List.new("pin", quote(node.pin))
            )
            if node.function is not None:
                entry.append(List.new("pinfunction", quote(node.function)))
            entry.append(List.new("pintype", quote(node.type)))
            net.append(entry)
        nets_node.append(net)
    root.append(nets_node)
    return render(root)
