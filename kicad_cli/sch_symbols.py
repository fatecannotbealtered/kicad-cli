"""Captured symbol discovery and exact resources used by schematic construction.

Tables and their included tables are read through one bounded byte inventory.
A supplied capture is closed: an undeclared path never falls back to disk.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from . import kicad_env, read_inputs
from .fileformat import lib_tables
from .fileformat.schematic import EMPTY_TILDE_BEFORE, Schematic, _lib_symbol
from .fileformat.sexpr import Document
from .fileformat.symbols import Resolved, _flatten
from .sync_inputs import MAX_FILES, MAX_TOTAL_BYTES, Drift, InputError, _absolute, _read

MAX_LIMIT = 200


def _identity(value: str) -> tuple[str, str]:
    if not isinstance(value, str) or value.count(":") != 1:
        raise InputError("Symbol identity must be exactly LIBRARY:NAME", symbol=value)
    library, name = value.split(":")
    if not library or not name or any(c in value for c in "/\\\r\n\0"):
        raise InputError("Symbol identity has an empty or unsupported path component", symbol=value)
    return library, name


def _environment() -> dict[str, str]:
    return {
        k: hashlib.sha256(v.encode()).hexdigest()
        for k, v in sorted(os.environ.items())
        if k.startswith("KICAD") and k not in {"KICAD_CLI_TRACE", "KICAD_CLI_ERROR_TRACE"}
    }


class Resources:
    """One exact symbol-table/library observation, or a closed captured view."""

    def __init__(
        self,
        root: str | Path,
        *,
        captured_files: dict | None = None,
        kicad_root: str | Path | None = None,
        config_dir: str | Path | None = None,
    ) -> None:
        self.root = _absolute(root)
        self.files: dict[Path, bytes | None] = {}
        self.manifest: list[dict] = []
        self._captured = (
            {_absolute(k): v for k, v in captured_files.items()}
            if captured_files is not None
            else None
        )
        self.context = _environment()
        installed = kicad_root if kicad_root is not None else kicad_env.find_kicad_root()
        self.installed = _absolute(installed) if installed else None
        config = config_dir if config_dir is not None else lib_tables.default_config_dir()
        self.config = _absolute(config) if config else None
        self.variables = lib_tables._variables(
            self.root.parent, self.installed, self.config, reader=self.read
        )
        self.tables = lib_tables.Tables()
        self.table_paths: list[str] = []
        self._libraries: dict[Path, tuple[Document, dict, int]] = {}
        for directory in filter(None, (self.config, self.root.parent)):
            self.tables.symbols.update(
                lib_tables._read(
                    directory / "sym-lib-table", self.variables, set(), reader=self._table
                )
            )

    def read(self, path: str | Path) -> bytes | None:
        path = _absolute(path)
        if path not in self.files:
            if self._captured is not None:
                if path not in self._captured:
                    raise InputError(
                        "Symbol resolution requires an undeclared captured input",
                        path=str(path),
                        next_action="Capture complete symbol tables and selected libraries "
                        "before preview.",
                    )
                raw = self._captured[path]
                row = {
                    "path": str(path),
                    "present": raw is not None,
                    "sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
                }
            else:
                row, raw = _read(path, capture_identity=True)
            self.files[path] = raw
            self.manifest.append(row)
            if (
                len(self.files) > MAX_FILES
                or sum(len(v or b"") for v in self.files.values()) > MAX_TOTAL_BYTES
            ):
                raise InputError("Symbol resource capture exceeds the public input bounds")
        return self.files[path]

    def _table(self, path: str | Path) -> bytes | None:
        raw = self.read(path)
        self.table_paths.append(str(_absolute(path)))
        if raw is not None and Document.parse(raw.decode("utf-8-sig")).root.head != "sym_lib_table":
            raise InputError("A consulted symbol table has an invalid root", path=str(path))
        return raw

    def _library(self, nickname: str) -> tuple[Document, dict, int, Path]:
        library = self.tables.symbols.get(nickname)
        if library is None or library.kind != "KiCad" or library.disabled:
            raise InputError("No enabled KiCad symbol library has this nickname", library=nickname)
        path = _absolute(
            library.path if library.path.is_absolute() else self.root.parent / library.path
        )
        if path not in self._libraries:
            raw = self.read(path)
            if raw is None:
                raise FileNotFoundError(path)
            document = Document.parse(raw.decode("utf-8-sig"))
            if document.root.head != "kicad_symbol_lib":
                raise InputError(
                    "The selected library is not a KiCad symbol library", path=str(path)
                )
            nodes: dict[str, Any] = {}
            for node in document.root.find_all("symbol"):
                name = (node.value(1) or "").rpartition(":")[2]
                if not name or name in nodes:
                    raise InputError(
                        "Symbol library contains an empty or duplicate identity", path=str(path)
                    )
                nodes[name] = node
            version = document.root.find("version")
            self._libraries[path] = (document, nodes, int(version.atom(1) if version else 0))
        document, nodes, version = self._libraries[path]
        return document, nodes, version, path

    def resolve(self, lib_id: str, *, embedded: Schematic | None = None) -> tuple[Resolved, dict]:
        nickname, name = _identity(lib_id)
        if embedded is not None and lib_id in embedded.library:
            lib = embedded.library[lib_id]
            if lib.node is None:
                raise InputError("Embedded symbol has no readable source definition", symbol=lib_id)
            if lib.node.find("extends") is not None:
                raise InputError(
                    "An unflattened embedded inheritance chain cannot be proved exact",
                    symbol=lib_id,
                )
            resolved = Resolved(lib_id, lib.node, lib)
            return resolved, descriptor(
                resolved,
                str(embedded.path),
                "embedded",
                hashlib.sha256(embedded.document.source.encode("utf-8")).hexdigest(),
            )
        document, nodes, version, path = self._library(nickname)
        node = nodes.get(name)
        if node is None:
            raise InputError("Selected library has no such symbol", symbol=lib_id, path=str(path))
        extends = node.find("extends")
        if extends is not None:
            parent = nodes.get(extends.value(1))
            if parent is None:
                raise InputError("The symbol inheritance parent is missing", symbol=lib_id)
            if parent is node or parent.find("extends") is not None:
                raise InputError(
                    "Multi-level or cyclic symbol inheritance is not proved "
                    "by this finite resolver",
                    symbol=lib_id,
                )
        flattened = _flatten(document, node, lib_id, name, lambda _doc, key: nodes.get(key))
        lib = _lib_symbol(lib_id, flattened, {lib_id: flattened}, version < EMPTY_TILDE_BEFORE)
        resolved = Resolved(lib_id, flattened, lib)
        return resolved, descriptor(
            resolved, str(path), "library", hashlib.sha256(self.read(path)).hexdigest()
        )

    def validate(self) -> None:
        if self.context != _environment():
            raise Drift("Symbol resource environment changed during observation")
        if self._captured is None:
            for old in self.manifest:
                if _read(old["path"], capture_identity=True)[0] != old:
                    raise Drift(
                        "Symbol resource bytes, existence or identity changed",
                        changed_files=[old["path"]],
                    )


def descriptor(resolved: Resolved, source: str, origin: str, digest: str | None) -> dict:
    library, _ = _identity(resolved.lib_id)
    lib = resolved.symbol
    return {
        "kind": "symbol",
        "detail_level": "pin_geometry",
        "library_id": library,
        "symbol_id": resolved.lib_id,
        "source_file": source,
        "source_sha256": digest,
        "origin": origin,
        "units": sorted({p.unit for p in lib.pins if p.unit} or {1}),
        "body_styles": sorted({p.body_style for p in lib.pins if p.body_style} or {1}),
        "power": lib.power,
        "local_power": lib.local_power,
        "properties": dict(lib.properties),
        "pins": [
            {
                "pin_ordinal": index,
                "number": pin.number,
                "name": pin.name,
                "electrical": pin.electrical,
                "shape": pin.shape,
                "hidden": pin.hidden,
                "unit": pin.unit,
                "body_style": pin.body_style,
                "position_nm": list(pin.position),
                "position_mm": [value / 1_000_000 for value in pin.position],
                "coordinate_frame": "library_y_up",
                "angle_deg": pin.angle,
                "length_nm": pin.length,
            }
            for index, pin in enumerate(lib.pins)
        ],
    }


def capture_resources(root: str | Path, symbols=()) -> Resources:
    resources = Resources(root)
    design = read_inputs.capture(schematic=root).design
    for entry in symbols:
        name, path = entry if isinstance(entry, (tuple, list)) else (entry, None)
        instances = [i for i in design.instances if i.path == path] if path else []
        if path and not instances:
            raise InputError("Selected symbol source page does not exist", instance_path=path)
        resources.resolve(name, embedded=instances[0].schematic if instances else None)
    resources.validate()
    return resources


def query(schematic, library=None, symbol=None, page=None, offset=0, limit=50) -> dict:
    if (
        type(offset) is not int
        or offset < 0
        or type(limit) is not int
        or not 1 <= limit <= MAX_LIMIT
    ):
        raise InputError("Symbol query requires nonnegative offset and limit1..200")
    inputs = read_inputs.capture(schematic=schematic)
    resources = Resources(schematic)
    matches = (
        [i for i in inputs.design.instances if i.path == page]
        if page is not None
        else [inputs.design.instances[0]]
    )
    if len(matches) != 1:
        raise InputError(
            "Symbol target page must name exactly one full instance_path",
            instance_path=page,
            available=[i.path for i in inputs.design.instances],
        )
    instance = matches[0]
    embedded = instance.schematic
    cached = {}
    for identity in embedded.library:
        try:
            nickname, _ = _identity(identity)
        except InputError:
            continue
        cached[identity] = nickname
    source_digest = hashlib.sha256(embedded.document.source.encode("utf-8")).hexdigest()
    if symbol is not None:
        nickname, _ = _identity(symbol)
        if library is not None and nickname != library:
            raise InputError("Symbol and library filters disagree", symbol=symbol, library=library)
        rows = [resources.resolve(symbol, embedded=embedded)[1]]
        mode = "symbol_geometry"
    elif library is not None:
        cache_names = {name for name, nickname in cached.items() if nickname == library}
        entry = resources.tables.symbols.get(library)
        if entry is None and not cache_names:
            raise InputError("No available symbol library has this nickname", library=library)
        if entry is not None:
            _, nodes, _, path = resources._library(library)
            external_names = {library + ":" + name for name in nodes}
        else:
            external_names = set()
            path = None
        rows = [
            {
                "kind": "symbol",
                "detail_level": "identity",
                "library_id": library,
                "symbol_id": name,
                "source_file": str(embedded.path) if name in cache_names else str(path),
                "source_sha256": source_digest
                if name in cache_names
                else hashlib.sha256(resources.read(path)).hexdigest(),
                "origin": "embedded" if name in cache_names else "library",
                "units": [],
                "body_styles": [],
                "pins": [],
                "properties": {},
                "power": None,
                "local_power": None,
            }
            for name in sorted(external_names | cache_names)
        ]
        mode = "symbol_identities"
    else:
        library_names = set(resources.tables.symbols) | set(cached.values())
        rows = [
            {
                "kind": "library",
                "detail_level": "identity",
                "library_id": name,
                "symbol_id": None,
                "source_file": str(embedded.path)
                if name in cached.values()
                else str(resources.tables.symbols[name].path),
                "source_sha256": source_digest if name in cached.values() else None,
                "origin": "embedded" if name in cached.values() else "table",
                "units": [],
                "body_styles": [],
                "pins": [],
                "properties": {},
                "power": None,
                "local_power": None,
                "library_type": resources.tables.symbols[name].kind
                if name in resources.tables.symbols
                else "Embedded",
            }
            for name in sorted(library_names)
            if name in cached.values() or not resources.tables.symbols[name].disabled
        ]
        mode = "library_identities"
    selected = rows[offset : offset + limit]
    resources.validate()
    inputs.validate()
    manifest = [*inputs.manifest, *resources.manifest]
    snapshot = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    return {
        "items": selected,
        "count": len(selected),
        "total": len(rows),
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(selected) < len(rows),
        "next_offset": offset + len(selected) if offset + len(selected) < len(rows) else None,
        "mode": mode,
        "resolution": {
            "target_instance_path": instance.path,
            "target_source_file": str(embedded.path),
            "priority": "embedded_then_tables",
            "embedded_symbol_ids": sorted(cached),
            "source_digest_basis": "Complete target SCH for embedded; "
            "complete library file for external symbols.",
        },
        "snapshot": snapshot,
        "inputs": manifest,
        "source": "Captured table/library files; no Native process or library-authoring operation.",
        "limits": {
            "max_limit": MAX_LIMIT,
            "max_files": MAX_FILES,
            "max_total_bytes": MAX_TOTAL_BYTES,
        },
    }
