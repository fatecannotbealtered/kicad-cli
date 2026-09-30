"""KiCad's library tables: which library a nickname names, and where it is.

A symbol is "Device:R" and a footprint "Resistor_SMD:R_0805_2012Metric": the
part before the colon is a nickname, and the tables say what file it is. There
are two of each, read in order, the later winning a nickname they share:

- the user's, in KiCad's configuration directory (`10.0/sym-lib-table`,
  `10.0/fp-lib-table`), which in KiCad 10 is itself mostly one entry of type
  "Table" pointing at the table KiCad installs;
- the project's, beside the project file.

A path in a table names variables -- `${KICAD10_SYMBOL_DIR}`, `${KIPRJMOD}` --
which KiCad defines from its installation, the project's folder, its own
settings (`kicad_common.json`, `environment.vars`) and the environment, in
that order of precedence from last to first. A library marked disabled is as
good as absent.

Nothing here runs KiCad; the tables and the libraries are read as files.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path

from .sexpr import Document, List, SexprError

VERSION = "10.0"
_VARIABLE = re.compile(r"\$\{([^}]+)\}|\$\(([^)]+)\)")


@dataclass
class Library:
    nickname: str
    kind: str  # KiCad, Legacy, Table, ...
    uri: str  # as the table has it
    path: Path  # with its variables expanded
    disabled: bool = False
    location: str = ""  # the uri expanded, as KiCad names it in a message


@dataclass
class Tables:
    symbols: dict[str, Library] = field(default_factory=dict)
    footprints: dict[str, Library] = field(default_factory=dict)
    _loaded: dict[Path, _SymbolLibrary | None] = field(default_factory=dict, repr=False)

    @classmethod
    def of(
        cls, project_dir: Path, kicad_root: Path | None, config_dir: Path | None = None
    ) -> Tables:
        """The tables a project is opened with: the user's, then its own."""
        config = config_dir if config_dir is not None else default_config_dir()
        variables = _variables(project_dir, kicad_root, config)
        tables = cls()
        for kind, target in (
            ("sym-lib-table", tables.symbols),
            ("fp-lib-table", tables.footprints),
        ):
            if config is not None:
                target.update(_read(config / kind, variables, set()))
            target.update(_read(project_dir / kind, variables, set()))
        return tables

    def _symbol_library(self, library: Library) -> _SymbolLibrary | None:
        """A KiCad symbol library, read once; None when it cannot be read as one."""
        if library.path not in self._loaded:
            loaded = None
            if library.kind == "KiCad" and library.path.is_file():
                try:
                    loaded = _SymbolLibrary.load(library.path)
                except (OSError, SexprError, UnicodeDecodeError):
                    loaded = None
            self._loaded[library.path] = loaded
        return self._loaded[library.path]

    def symbol_names(self, library: Library) -> Collection[str] | None:
        """The symbols a KiCad symbol library holds, or None when it cannot be
        read as one."""
        loaded = self._symbol_library(library)
        return loaded.symbols.keys() if loaded is not None else None

    def symbol(self, library: Library, name: str) -> tuple[List, int] | None:
        """A library's symbol as a schematic embeds it -- a derived symbol
        whole -- and the version of the file it came from; None if absent."""
        from .symbols import _flatten  # noqa: PLC0415

        loaded = self._symbol_library(library)
        raw = loaded.symbols.get(name) if loaded is not None else None
        if loaded is None or raw is None:
            return None
        node = _flatten(loaded.document, raw, f"{library.nickname}:{name}", name, loaded.find)
        return node, loaded.version


@dataclass
class _SymbolLibrary:
    document: Document
    symbols: dict[str, List]  # by name
    version: int

    @classmethod
    def load(cls, path: Path) -> _SymbolLibrary:
        document = Document.load(path)
        # A name as a rescue library stores it, "lib-rescue:R", is found by
        # its part after the colon.
        symbols = {
            (node.value(1) or "").rpartition(":")[2]: node
            for node in document.root.find_all("symbol")
        }
        version = document.root.find("version")
        try:
            number = int(version.atom(1) or 0) if version is not None else 0
        except ValueError:
            number = 0
        return cls(document, symbols, number)

    def find(self, _document: Document, name: str) -> List | None:
        return self.symbols.get(name.rpartition(":")[2])


def default_config_dir() -> Path | None:
    """KiCad's configuration directory for this version, as KiCad finds it."""
    home = os.environ.get("KICAD_CONFIG_HOME")
    if home:
        return Path(home) / VERSION
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return Path(base) / "kicad" / VERSION if base else None
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Preferences" / "kicad" / VERSION
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "kicad" / VERSION


def _variables(project_dir: Path, kicad_root: Path | None, config: Path | None) -> dict[str, str]:
    out: dict[str, str] = {"KIPRJMOD": str(project_dir)}
    if kicad_root is not None:
        # KiCad 10 still defines its earlier versions' names, all pointing at
        # this installation: a project from KiCad 6 names ${KICAD6_...}.
        share = Path(kicad_root) / "share" / "kicad"
        dirs = {
            "SYMBOL": share / "symbols",
            "FOOTPRINT": share / "footprints",
            "3DMODEL": share / "3dmodels",
            "TEMPLATE": share / "template",
        }
        for major in range(6, int(VERSION.split(".")[0]) + 1):
            out.update({f"KICAD{major}_{kind}_DIR": str(path) for kind, path in dirs.items()})
        out.update(
            {
                "KICAD_SYMBOL_DIR": str(dirs["SYMBOL"]),
                "KISYSMOD": str(dirs["FOOTPRINT"]),
                "KISYS3DMOD": str(dirs["3DMODEL"]),
                "KICAD_TEMPLATE_DIR": str(dirs["TEMPLATE"]),
            }
        )
    if config is not None:
        try:
            common = json.loads((config / "kicad_common.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            common = {}
        given = (common.get("environment") or {}).get("vars") or {}
        if isinstance(given, dict):
            out.update({str(k): str(v) for k, v in given.items()})
    out.update({k: v for k, v in os.environ.items() if k.startswith("KICAD") or k == "KIPRJMOD"})
    out["KIPRJMOD"] = str(project_dir)
    return out


def expand(text: str, variables: dict[str, str]) -> str:
    """`${NAME}` and `$(NAME)` replaced; a name nothing defines is left."""
    return _VARIABLE.sub(lambda m: variables.get(m.group(1) or m.group(2), m.group(0)), text)


def _read(path: Path, variables: dict[str, str], seen: set[Path]) -> dict[str, Library]:
    """A table's libraries, a nested table's included, in the table's order."""
    try:
        key = path.resolve()
        if key in seen or not path.is_file():
            return {}
        seen.add(key)
        root = Document.load(path).root
    except (OSError, SexprError, UnicodeDecodeError):
        return {}
    out: dict[str, Library] = {}
    for entry in root.find_all("lib"):
        name = entry.find("name")
        kind = entry.find("type")
        uri = entry.find("uri")
        if name is None or uri is None:
            continue
        location = expand(uri.value(1) or "", variables)
        library = Library(
            nickname=name.value(1) or "",
            kind=(kind.value(1) or "") if kind is not None else "",
            uri=uri.value(1) or "",
            path=Path(location),
            disabled=entry.find("disabled") is not None,
            location=location,
        )
        if library.disabled:
            continue
        if library.kind == "Table":
            out.update(_read(library.path, variables, seen))
        else:
            out[library.nickname] = library
    return out
