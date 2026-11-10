"""Reading a KiCad netlist (s-expression format, as KiCad's ``sch export netlist`` writes it).

Each component gives its reference, value, footprint and the link KiCad keeps between a
footprint and its schematic symbol: the sheet path's timestamps followed by the symbol's
UUID. Each net gives its name and the pins on it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .base import invalid
from .fileformat.sexpr import Document, List, SexprError


@dataclass
class Component:
    ref: str
    value: str
    footprint: str
    path: str = ""
    on_board: bool = True


@dataclass
class Netlist:
    components: list[Component] = field(default_factory=list)
    nets: list[dict] = field(default_factory=list)  # {"name": str, "nodes": [{"ref", "pin"}]}


def _child_value(node: List, name: str) -> str:
    found = node.find(name)
    return (found.value(1) or "") if found is not None else ""


def _properties(node: List) -> dict[str, str]:
    out = {}
    for prop in node.find_all("property"):
        name = _child_value(prop, "name")
        if name:
            out[name] = _child_value(prop, "value")
    return out


def _link(comp: List) -> str:
    """The footprint's link to its symbol: ``/<sheet uuids>/<symbol uuid>``."""
    sheet = comp.find("sheetpath")
    sheet_stamps = _child_value(sheet, "tstamps") if sheet is not None else "/"
    stamps = comp.find("tstamps")
    symbol = stamps.value(1) if stamps is not None else None
    if not symbol:
        return ""
    parts = [p for p in sheet_stamps.split("/") if p] + [symbol]
    return "".join("/" + p for p in parts)


def parse(text: str) -> Netlist:
    try:
        root = Document.parse(text, lazy=False).root
    except SexprError as exc:
        raise invalid(f"KiCad's netlist cannot be read: {exc}.") from None
    if root.head != "export":
        raise invalid("KiCad's netlist does not start with (export ...).")
    out = Netlist()
    components = root.find("components")
    for comp in components.find_all("comp") if components is not None else []:
        ref = _child_value(comp, "ref")
        if not ref:
            continue
        props = _properties(comp)
        excluded = "exclude_from_board" in props or "exclude_from_board" in comp.values()
        out.components.append(Component(ref=ref, value=_child_value(comp, "value"),
                                        footprint=_child_value(comp, "footprint"), path=_link(comp),
                                        on_board=not excluded))
    nets = root.find("nets")
    for net in nets.find_all("net") if nets is not None else []:
        name = _child_value(net, "name")
        if not name:
            continue
        nodes = []
        for node in net.find_all("node"):
            ref, pin = _child_value(node, "ref"), _child_value(node, "pin")
            if ref and pin:
                nodes.append({"ref": ref, "pin": pin})
        if nodes:
            out.nets.append({"name": name, "nodes": nodes})
    return out
