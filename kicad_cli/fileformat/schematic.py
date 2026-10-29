"""A `.kicad_sch`, read into the objects the rest of the tool reasons about.

Built on `sexpr` like the board model: every object keeps the list it came
from (``node``), so an edit changes that list and the document writes back
only what changed. Nothing here imports KiCad.

Units are integer nanometres; a schematic stores millimetres to four places,
so every coordinate is a whole multiple of 100 nm. Angles are degrees.

A symbol's library drawing is in its own frame with y pointing up; the sheet
has y pointing down. `Symbol.transform` takes a point from one to the other:
the library point with its y turned over, rotated by the symbol's angle, then
mirrored, then moved to the symbol's position. Rotating first matters: a
symbol at 90 degrees mirrored about x otherwise lands with its pins swapped.
That was settled against KiCad's own netlists of its demo projects, and is
held by a fixture that places one part in all eight orientations
(`tests/test_fileformat_circuit.py`).

A design is a tree of sheets. One file can be used by several sheet symbols,
so a sheet *instance* is a path -- the root's uuid, then each sheet symbol's
uuid on the way down -- and what differs per instance, a symbol's reference
and unit, is stored per path in the symbol's `(instances ...)`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .sexpr import Document, List, text

NM = 1_000_000
# Up to KiCad 9 a pin with no name was written as "~"; from the first file
# format of 10 on, as "" -- and "~" became a name like any other. The demos
# have "~" in files of versions up to 20250114 and "" from 20250610.
EMPTY_TILDE_BEFORE = 20250610


def nm(atom: str) -> int:
    return round(float(atom) * NM)


Point = tuple[int, int]


# -- the library ------------------------------------------------------------------


@dataclass(slots=True)
class LibPin:
    number: str
    name: str
    electrical: str  # input, output, bidirectional, tri_state, passive, free,
    # unspecified, power_in, power_out, open_collector, open_emitter, no_connect
    shape: str
    position: Point  # the connection point, library frame (y up)
    angle: int  # which way the pin points from the connection point to the body
    length: int
    unit: int  # 0: on every unit
    body_style: int  # 0: on every body style
    hidden: bool = False
    # Other functions the pin can be given on a placed symbol, by name:
    # (electrical type, shape). A connector's pin H37 can be CAN1_DOUT.
    alternates: dict[str, tuple[str, str]] = field(default_factory=dict)


@dataclass(slots=True)
class LibSymbol:
    name: str  # as the schematic names it: "Device:R", or a lib_name
    properties: dict[str, str]
    pins: list[LibPin]
    power: bool  # a power symbol: its pin names a global net
    local_power: bool  # KiCad 10: a power symbol whose net is the sheet's own
    unit_count: int
    pin_names_hidden: bool = False
    pin_numbers_hidden: bool = False
    node: List | None = field(default=None, repr=False)

    def pins_of(self, unit: int, body_style: int) -> list[LibPin]:
        """The pins a placed unit has: its own and those common to every unit."""
        return [p for p in self.pins if p.unit in (0, unit) and p.body_style in (0, body_style)]


# -- what is drawn on a sheet --------------------------------------------------


@dataclass(slots=True)
class Instance:
    """What one sheet instance calls a symbol."""

    path: str  # the sheet instance's path, without the symbol's own uuid
    reference: str
    unit: int


@dataclass(slots=True)
class Symbol:
    lib_id: str
    lib_name: str | None
    position: Point
    angle: int
    mirror: str | None  # "x", "y" or None
    unit: int
    body_style: int
    uuid: str
    properties: dict[str, str]
    in_bom: bool
    on_board: bool
    dnp: bool
    exclude_from_sim: bool
    instances: list[Instance]
    node: List | None = field(default=None, repr=False)
    # The alternate function chosen for a pin, by pin number.
    alternates: dict[str, str] = field(default_factory=dict)
    # Each pin's own uuid, by number: what ERC names a pin by.
    pin_uuids: dict[str, str] = field(default_factory=dict)

    @property
    def library_name(self) -> str:
        """The key of its drawing in the sheet's own `lib_symbols`."""
        return self.lib_name or self.lib_id

    def transform(self, point: Point) -> Point:
        """A point of the library drawing (y up), where it lands on the sheet."""
        x, y = _turn(point[0], -point[1], self.angle)
        if self.mirror == "y":
            x = -x
        elif self.mirror == "x":
            y = -y
        return (self.position[0] + x, self.position[1] + y)

    def instance(self, path: str) -> Instance | None:
        for inst in self.instances:
            if inst.path == path:
                return inst
        return None


def _turn(x: int, y: int, angle: int) -> Point:
    """Turn a sheet vector by a multiple of 90 degrees, counter-clockwise as
    seen on screen -- where y points down."""
    quarter = angle % 360
    if quarter == 0:
        return x, y
    if quarter == 90:
        return y, -x
    if quarter == 180:
        return -x, -y
    if quarter == 270:
        return -y, x
    raise ValueError(f"a symbol is placed at a multiple of 90 degrees, not {angle}")


@dataclass(slots=True)
class Wire:
    start: Point
    end: Point
    kind: str = "wire"  # wire or bus
    node: List | None = field(default=None, repr=False)


@dataclass(slots=True)
class Label:
    text: str
    position: Point
    angle: float
    kind: str  # label, global_label, hierarchical_label, directive_label
    shape: str | None = None  # input, output, bidirectional, tri_state, passive
    node: List | None = field(default=None, repr=False)


@dataclass(slots=True)
class SheetPin:
    name: str
    electrical: str
    position: Point
    node: List | None = field(default=None, repr=False)


@dataclass(slots=True)
class Sheet:
    """A sheet symbol: a child sheet placed on this one."""

    name: str
    file: str
    position: Point
    size: Point
    uuid: str
    pins: list[SheetPin]
    node: List | None = field(default=None, repr=False)
    pages: dict[str, str] = field(default_factory=dict)  # parent path -> page number


@dataclass(slots=True)
class Text:
    """Free text on a sheet: a text or a text box."""

    text: str
    position: Point
    kind: str  # text or text_box
    node: List | None = field(default=None, repr=False)


@dataclass(slots=True)
class BusEntry:
    position: Point
    size: Point
    node: List | None = field(default=None, repr=False)

    @property
    def end(self) -> Point:
        return (self.position[0] + self.size[0], self.position[1] + self.size[1])


class SchematicError(ValueError):
    """The file parses, but it is not a schematic KiCad would open."""


class Schematic:
    """One `.kicad_sch` file: a sheet's drawing, whichever instances use it."""

    def __init__(self, document: Document, path: Path | None = None) -> None:
        self.document = document
        self.path = path
        self.root = document.root
        if self.root.head != "kicad_sch":
            raise SchematicError(f"not a schematic: the file starts with ({self.root.head} ...)")
        version = self.root.find("version")
        if version is None or not (version.atom(1) or "").isdigit():
            raise SchematicError("the file has no (version ...), and KiCad will not open it")
        self.version = int(version.atom(1))
        uuid = self.root.find("uuid")
        self.uuid = uuid.value(1) if uuid is not None else ""
        self._library: dict[str, LibSymbol] | None = None
        self._symbols: list[Symbol] | None = None

    @classmethod
    def load(cls, path: str | Path) -> Schematic:
        path = Path(path)
        return cls(Document.load(path), path)

    # -- the embedded library ---------------------------------------------------------

    @property
    def library(self) -> dict[str, LibSymbol]:
        if self._library is None:
            table = self.root.find("lib_symbols")
            raw = {n.value(1): n for n in table.find_all("symbol")} if table is not None else {}
            legacy = self.version < EMPTY_TILDE_BEFORE
            self._library = {
                name: _lib_symbol(name, node, raw, legacy) for name, node in raw.items()
            }
        return self._library

    # -- what is drawn ------------------------------------------------------------------

    @property
    def symbols(self) -> list[Symbol]:
        if self._symbols is None:
            legacy = self.version < EMPTY_TILDE_BEFORE
            self._symbols = [_symbol(n, legacy) for n in self.root.find_all("symbol")]
        return self._symbols

    @property
    def wires(self) -> list[Wire]:
        out = []
        for node in self.root.lists():
            if node.head in ("wire", "bus"):
                pts = [_xy(p) for p in node.find("pts").lists() if p.head == "xy"]
                for a, b in zip(pts, pts[1:], strict=False):
                    out.append(Wire(a, b, node.head, node))
        return out

    @property
    def junctions(self) -> list[Point]:
        return [_at(n) for n in self.root.find_all("junction")]

    @property
    def no_connects(self) -> list[Point]:
        return [_at(n) for n in self.root.find_all("no_connect")]

    @property
    def no_connect_uuids(self) -> list[str]:
        """The uuid of each no-connect flag, in the order `no_connects` lists them."""
        return [uuid_of(n) for n in self.root.find_all("no_connect")]

    @property
    def labels(self) -> list[Label]:
        out = []
        for node in self.root.lists():
            # A net class flag is KiCad's older word for a directive label.
            if node.head in (
                "label",
                "global_label",
                "hierarchical_label",
                "directive_label",
                "netclass_flag",
            ):
                at = node.find("at")
                shape = node.find("shape")
                out.append(
                    Label(
                        text=node.value(1) or "",
                        position=(nm(at.atom(1)), nm(at.atom(2))),
                        angle=float(at.atom(3) or 0),
                        kind="directive_label" if node.head == "netclass_flag" else node.head,
                        shape=shape.atom(1) if shape is not None else None,
                        node=node,
                    )
                )
        return out

    @property
    def sheets(self) -> list[Sheet]:
        out = []
        for node in self.root.find_all("sheet"):
            props = {p.value(1): p.value(2) for p in node.find_all("property")}
            size = node.find("size")
            uuid = node.find("uuid")
            out.append(
                Sheet(
                    name=props.get("Sheetname") or props.get("Sheet name") or "",
                    file=props.get("Sheetfile") or props.get("Sheet file") or "",
                    position=_at(node),
                    size=(nm(size.atom(1)), nm(size.atom(2))),
                    uuid=uuid.value(1) if uuid is not None else "",
                    pins=[
                        SheetPin(p.value(1) or "", p.atom(2) or "", _at(p), p)
                        for p in node.find_all("pin")
                    ],
                    node=node,
                    pages=_pages(node),
                )
            )
        return out

    @property
    def texts(self) -> list[Text]:
        out = []
        for node in self.root.lists():
            if node.head in ("text", "text_box"):
                at = node.find("at")
                position = (nm(at.atom(1)), nm(at.atom(2))) if at is not None else (0, 0)
                out.append(Text(node.value(1) or "", position, node.head, node))
        return out

    @property
    def bus_entries(self) -> list[BusEntry]:
        out = []
        for node in self.root.find_all("bus_entry"):
            size = node.find("size")
            out.append(BusEntry(_at(node), (nm(size.atom(1)), nm(size.atom(2))), node))
        return out


# -- reading --------------------------------------------------------------------------


def uuid_of(node: List | None) -> str:
    """An item's own uuid, or nothing."""
    found = node.find("uuid") if node is not None else None
    return (found.value(1) or "") if found is not None else ""


def _at(node: List) -> Point:
    at = node.find("at")
    return (nm(at.atom(1)), nm(at.atom(2)))


def _xy(node: List) -> Point:
    return (nm(node.atom(1)), nm(node.atom(2)))


def _flag(node: List, name: str, default: bool) -> bool:
    found = node.find(name)
    if found is None:
        return default
    value = found.atom(1)
    return value != "no" if value is not None else True


def _hidden(node: List) -> bool:
    """`(hide yes)` since KiCad 9; a bare `hide` atom before."""
    found = node.find("hide")
    if found is not None:
        return found.atom(1) != "no"
    return any(item == "hide" for item in node.items[1:] if isinstance(item, str))


def _unit_style(sub_name: str, parent: str) -> tuple[int, int]:
    """The unit and body style in a sub-symbol's name: ``R_1_1`` is unit 1,
    body style 1; ``_0`` in either place means every one."""
    tail = sub_name[len(parent.split(":")[-1]) :] if sub_name else ""
    parts = tail.rsplit("_", 2)
    try:
        return int(parts[-2]), int(parts[-1])
    except (ValueError, IndexError):
        return 0, 0


def _lib_symbol(name: str, node: List, raw: dict[str, List], legacy: bool = False) -> LibSymbol:
    extends = node.find("extends")
    base = raw.get(extends.value(1)) if extends is not None else None
    source = base if base is not None else node
    properties = _properties(node, legacy)
    pins: list[LibPin] = []
    units = set()
    for sub in source.find_all("symbol"):
        unit, style = _unit_style(sub.value(1) or "", source.value(1) or name)
        if unit:
            units.add(unit)
        for pin in sub.find_all("pin"):
            at = pin.find("at")
            number = pin.find("number")
            pin_name = pin.find("name")
            length = pin.find("length")
            pins.append(
                LibPin(
                    number=number.value(1) if number is not None else "",
                    name=_pin_name(pin_name, legacy),
                    electrical=pin.atom(1) or "unspecified",
                    shape=pin.atom(2) or "line",
                    position=(nm(at.atom(1)), nm(at.atom(2))),
                    angle=int(float(at.atom(3) or 0)),
                    length=nm(length.atom(1)) if length is not None else 0,
                    unit=unit,
                    body_style=style,
                    hidden=_hidden(pin),
                    alternates={
                        (alt.value(1) or ""): (alt.atom(2) or "unspecified", alt.atom(3) or "line")
                        for alt in pin.find_all("alternate")
                    },
                )
            )
    power = source.find("power")
    pin_names = source.find("pin_names")
    pin_numbers = source.find("pin_numbers")
    return LibSymbol(
        name=name,
        properties=properties,
        pins=pins,
        power=power is not None,
        local_power=power is not None and power.atom(1) == "local",
        unit_count=max(units) if units else 1,
        pin_names_hidden=pin_names is not None and _hidden(pin_names),
        pin_numbers_hidden=pin_numbers is not None and _hidden(pin_numbers),
        node=node,
    )


def _pages(node: List) -> dict[str, str]:
    out = {}
    found = node.find("instances")
    for project in found.find_all("project") if found is not None else []:
        for path in project.find_all("path"):
            page = path.find("page")
            out[path.value(1) or ""] = page.value(1) if page is not None else ""
    return out


def _pin_name(node: List | None, legacy: bool) -> str:
    name = node.value(1) if node is not None else ""
    return "" if legacy and name == "~" else (name or "")


def _properties(node: List, legacy: bool) -> dict[str, str]:
    """A symbol's fields, by name. Before KiCad 10's format an empty field was
    written "~" -- a datasheet of "~" is no datasheet. A value of "~" stays
    one: KiCad's netlist of such a part says "~"."""
    out = {}
    for prop in node.find_all("property"):
        name, value = prop.value(1), prop.value(2) or ""
        empty = legacy and value == "~" and name not in ("Value", "Reference")
        out[name] = "" if empty else value
    return out


def _symbol(node: List, legacy: bool = False) -> Symbol:
    at = node.find("at")
    mirror = node.find("mirror")
    unit = node.find("unit")
    style = node.find("body_style") or node.find("convert")
    uuid = node.find("uuid")
    lib_name = node.find("lib_name")
    instances = []
    found = node.find("instances")
    for project in found.find_all("project") if found is not None else []:
        for path in project.find_all("path"):
            reference = path.find("reference")
            inst_unit = path.find("unit")
            instances.append(
                Instance(
                    path=path.value(1) or "",
                    reference=reference.value(1) if reference is not None else "",
                    unit=int(inst_unit.atom(1)) if inst_unit is not None else 1,
                )
            )
    return Symbol(
        lib_id=node.find("lib_id").value(1) or "",
        lib_name=lib_name.value(1) if lib_name is not None else None,
        position=(nm(at.atom(1)), nm(at.atom(2))),
        angle=int(float(at.atom(3) or 0)),
        mirror=mirror.atom(1) if mirror is not None else None,
        unit=int(unit.atom(1)) if unit is not None else 1,
        body_style=int(style.atom(1)) if style is not None else 1,
        uuid=uuid.value(1) if uuid is not None else "",
        properties=_properties(node, legacy),
        in_bom=_flag(node, "in_bom", True),
        on_board=_flag(node, "on_board", True),
        dnp=_flag(node, "dnp", False),
        exclude_from_sim=_flag(node, "exclude_from_sim", False),
        instances=instances,
        node=node,
        alternates={
            (pin.value(1) or ""): pin.find("alternate").value(1)
            for pin in node.find_all("pin")
            if pin.find("alternate") is not None
        },
        pin_uuids={
            (pin.value(1) or ""): pin.find("uuid").value(1) or ""
            for pin in node.find_all("pin")
            if pin.find("uuid") is not None
        },
    )


__all__ = [
    "BusEntry",
    "Instance",
    "Label",
    "LibPin",
    "LibSymbol",
    "Schematic",
    "SchematicError",
    "Sheet",
    "SheetPin",
    "Symbol",
    "Text",
    "Wire",
    "text",
    "uuid_of",
]
