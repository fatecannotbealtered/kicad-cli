"""Symbols from KiCad's libraries, as a schematic embeds them.

A schematic carries a copy of every symbol it uses in `(lib_symbols ...)`,
named "Library:Name", so the file opens without the library. This reads a
symbol out of a `.kicad_sym` and makes that copy the way KiCad makes it:

- a derived symbol -- `(extends "Parent")`, how a library gives one drawing
  many part numbers (BC337 is Q_NPN_CBE with its own fields) -- is written
  out whole: the parent's flags and drawing, the derived symbol's fields
  over the parent's, and the parent's units renamed for the child
  ("Q_NPN_CBE_1_1" becomes "BC337_1_1");
- nothing else is changed.

The libraries are KiCad's data, read like any other file; nothing here runs
KiCad.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .schematic import LibPin, LibSymbol, _lib_symbol
from .sexpr import Document, List, copy, quote

FLAGS = (
    "power", "pin_numbers", "pin_names", "exclude_from_sim", "in_bom", "on_board",
    "in_pos_files", "duplicate_pin_numbers_are_jumpers", "jumper_pin_groups",
)  # fmt: skip


class SymbolError(LookupError):
    """A symbol the libraries do not have."""


@dataclass
class Resolved:
    lib_id: str
    node: List  # the copy to embed, named lib_id
    symbol: LibSymbol

    def pin(self, wanted: str, unit: int | None = None) -> LibPin | None:
        """A pin by number, or failing that by name -- "1" or "VCC"."""
        pins = self.symbol.pins
        by_number = [p for p in pins if p.number == wanted]
        if by_number:
            return by_number[0]
        by_name = [p for p in pins if p.name == wanted]
        if len({p.number for p in by_name}) == 1:
            return by_name[0]
        folded = [p for p in pins if p.name.lower() == wanted.lower()]
        if len({p.number for p in folded}) == 1:
            return folded[0]
        return None

    def pins_named(self, wanted: str) -> list[LibPin]:
        return [p for p in self.symbol.pins if p.name == wanted or p.number == wanted]


class Libraries:
    """KiCad's installed symbol libraries: `share/kicad/symbols/*.kicad_sym`."""

    def __init__(self, directories: list[Path]) -> None:
        self.directories = directories
        self._files: dict[str, Document | None] = {}
        self._cache: dict[str, Resolved] = {}

    def _library(self, name: str) -> Document | None:
        if name not in self._files:
            found = None
            for directory in self.directories:
                path = directory / f"{name}.kicad_sym"
                if path.is_file():
                    found = Document.load(path)
                    break
            self._files[name] = found
        return self._files[name]

    def _raw(self, library: Document, name: str) -> List | None:
        for node in library.root.find_all("symbol"):
            if node.value(1) == name:
                return node
        return None

    def resolve(self, lib_id: str) -> Resolved:
        """The symbol `lib_id` names, ready to embed. SymbolError if absent."""
        if lib_id in self._cache:
            return self._cache[lib_id]
        library_name, _, name = lib_id.partition(":")
        library = self._library(library_name)
        if library is None:
            raise SymbolError(f"no symbol library named {library_name!r}")
        raw = self._raw(library, name)
        if raw is None:
            raise SymbolError(f"{library_name!r} has no symbol {name!r}")
        node = _flatten(library, raw, lib_id, name, self._raw)
        symbol = _lib_symbol(lib_id, node, {lib_id: node})
        resolved = Resolved(lib_id, node, symbol)
        self._cache[lib_id] = resolved
        return resolved


def _flatten(library: Document, raw: List, lib_id: str, name: str, find) -> List:
    """The symbol whole, as a schematic embeds it."""
    extends = raw.find("extends")
    parent = find(library, extends.value(1)) if extends is not None else None
    if parent is not None and parent.find("extends") is not None:
        parent = find(library, parent.find("extends").value(1)) or parent
    out = List.new("symbol", quote(lib_id))
    source_flags = parent if parent is not None else raw
    own = {c.head for c in raw.lists()}
    for flag in FLAGS:
        node = raw.find(flag) if flag in own else source_flags.find(flag)
        if node is not None:
            out.append(copy(node))
    fields = {p.value(1): p for p in raw.find_all("property")}
    if parent is not None:
        for prop in parent.find_all("property"):
            if prop.value(1) not in fields:
                out.append(copy(prop))
                fields[prop.value(1)] = prop
    for prop in raw.find_all("property"):
        out.append(copy(prop))
    drawing = parent if parent is not None else raw
    parent_name = drawing.value(1) or name
    for unit in drawing.find_all("symbol"):
        placed = copy(unit)
        unit_name = unit.value(1) or ""
        if unit_name.startswith(parent_name):
            placed.set(1, quote(name + unit_name[len(parent_name) :]))
        out.append(placed)
    fonts = raw.find("embedded_fonts") or drawing.find("embedded_fonts")
    if fonts is not None:
        out.append(copy(fonts))
    return out


def installed(kicad_root: str | Path) -> Libraries:
    return Libraries([Path(kicad_root) / "share" / "kicad" / "symbols"])
