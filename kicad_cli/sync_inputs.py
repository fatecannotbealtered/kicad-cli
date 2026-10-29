"""Immutable saved-input bytes and the paths actually consulted by project sync.

Hashing after a planner read is insufficient. This collector supplies the same
captured bytes to the design, tables and footprint loader, then rejects drift.
It writes neither source documents nor libraries and records no environment secrets.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from . import kicad_env, netlist
from .fileformat import annotation, lib_tables
from .fileformat import netlist as built_netlist
from .fileformat.board import Board
from .fileformat.circuit import Design
from .fileformat.schematic import Schematic
from .fileformat.sexpr import Document
from .native import board_update

MAX_FILES = 2048
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
VERSION = "3"


class InputError(ValueError):
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details


class Drift(InputError):
    """The bytes, existence or resolution context no longer match the plan."""


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _absolute(path):
    return Path(os.path.abspath(Path(path).expanduser()))


def _read(path, *, capture_identity=False):
    """Legacy byte/existence row by default; sync explicitly binds path identity."""
    requested = _absolute(path)
    if str(requested).startswith(("\\\\", "//")):
        raise InputError("Network dependencies are outside this saved-file sync adapter.")
    resolved = requested.resolve()
    if str(resolved).startswith(("\\\\", "//")):
        raise InputError("A dependency resolves to a network path.")
    opened = False
    try:
        with requested.open("rb") as handle:
            opened = True
            before = os.fstat(handle.fileno())
            path_before = requested.stat()
            raw = handle.read(MAX_FILE_BYTES + 1)
            after = os.fstat(handle.fileno())
    except FileNotFoundError as exc:
        if opened:
            raise Drift(
                "A dependency disappeared while being captured.", path=str(requested)
            ) from exc
        raw = None
        before = after = path_before = None
    if raw is not None and len(raw) > MAX_FILE_BYTES:
        raise InputError("A sync input exceeds the supported size.", path=str(requested))
    if requested.resolve() != resolved:
        raise Drift("A dependency changed identity while being captured.", path=str(requested))

    def identity(info):
        return (
            {
                "device": str(info.st_dev),
                "inode": str(info.st_ino),
                "size": info.st_size,
                "mtime_ns": str(info.st_mtime_ns),
                "ctime_ns": str(info.st_ctime_ns),
            }
            if info is not None
            else None
        )

    observed = None
    if raw is not None:
        try:
            path_after = requested.stat()
        except FileNotFoundError as exc:
            raise Drift(
                "A dependency disappeared while being captured.", path=str(requested)
            ) from exc
        handle_before, handle_after = identity(before), identity(after)
        observed = identity(path_after)
        # CPython 3.12 on Windows can report creation time for path stat's
        # st_ctime_ns and change time for fstat's st_ctime_ns. Each API must
        # remain fully stable against itself; cross-API identity uses the
        # shared device/inode/size/mtime fields. Persist path-stat identity so
        # later same-byte replacements are still compared through the same API.
        shared = ("device", "inode", "size", "mtime_ns")
        if (
            handle_before != handle_after
            or identity(path_before) != observed
            or any(handle_after[key] != observed[key] for key in shared)
            or (os.name != "nt" and handle_after["ctime_ns"] != observed["ctime_ns"])
        ):
            raise Drift(
                "A dependency was replaced or changed while being captured.", path=str(requested)
            )
    row = {
        "path": str(requested),
        "resolved": str(resolved),
        "present": raw is not None,
        "sha256": _sha(raw) if raw is not None else None,
    }
    if capture_identity:
        row["identity"] = observed
    return row, raw


def _context(project, config, installed, variables, names):
    return {
        "collector_version": VERSION,
        "project_directory": str(project),
        "config_directory": str(config) if config is not None else None,
        "installation": str(installed) if installed is not None else None,
        "variables": [
            {
                "name": name,
                "value_sha256": _sha(variables[name].encode()) if name in variables else None,
            }
            for name in sorted(names)
        ],
        "variable_policy": "Variables referenced by consulted footprint tables; values hashed.",
    }


@dataclass(frozen=True)
class Inputs:
    board_path: Path
    schematic_path: Path
    files: object
    manifest: dict
    design: Design
    found: netlist.Read
    built: object
    tables: lib_tables.Tables
    libraries: object

    def raw(self, path):
        return self.files[_absolute(path)]

    def library(self, fpid):
        path = self.libraries.get(fpid)
        return path if path is not None and self.raw(path) is not None else None

    def footprint_source(self, path):
        return Document.parse(self.raw(path).decode("utf-8")).root


def capture(board_path, schematic_path, removal_path=None):
    """Capture each consulted path once; any unknown/malformed input fails closed."""
    from .commands import sync  # noqa: PLC0415

    board_path, schematic_path = _absolute(board_path), _absolute(schematic_path)
    files, records = {}, {}
    total = 0

    def read(path):
        nonlocal total
        path = _absolute(path)
        if path not in records:
            row, raw = _read(path, capture_identity=True)
            records[path] = row
            files[path] = raw
            # Design's frozen hierarchy uses canonical paths without disk fallback.
            files[Path(row["resolved"])] = raw
            total += len(raw or b"")
            if len(records) > MAX_FILES or total > MAX_TOTAL_BYTES:
                raise InputError("The sync dependency set exceeds the supported size.")
        return files[path]

    raw_board = read(board_path)
    if raw_board is None:
        raise FileNotFoundError(board_path)
    read(board_path.with_suffix(".kicad_pro"))
    read(board_path.with_suffix(".kicad_dru"))
    read(schematic_path.with_suffix(".kicad_pro"))

    def walk(path, ancestors):
        raw = read(path)
        if raw is None:
            return
        key = Path(records[_absolute(path)]["resolved"])
        if key in ancestors:
            raise InputError("Cyclic schematic hierarchy.", path=str(path))
        schematic = Schematic(Document.parse(raw.decode("utf-8")), path)
        for sheet in schematic.sheets:
            walk(path.parent / sheet.file, {*ancestors, key})

    if read(schematic_path) is None:
        raise FileNotFoundError(schematic_path)
    walk(schematic_path, set())
    design = Design(schematic_path, snapshot=MappingProxyType(dict(files)))
    built = built_netlist.build(design)
    found = netlist.Read(
        [netlist.record(component) for component in built.components],
        annotation.problems(design),
        list(design.missing),
    )
    project = board_path.parent
    config = lib_tables.default_config_dir()
    config = _absolute(config) if config is not None else None
    installed = kicad_env.find_kicad_root()
    installed = _absolute(installed) if installed else None
    variables = lib_tables._variables(project, installed, config, reader=read)
    tables = lib_tables.Tables()
    table_paths = []

    def read_table(path):
        path = _absolute(path)
        raw = read(path)
        table_paths.append(path)
        if raw is not None and Document.parse(raw.decode("utf-8")).root.head != "fp_lib_table":
            raise InputError("A consulted footprint table has an invalid root.", path=str(path))
        return raw

    if config is not None:
        tables.footprints.update(
            lib_tables._read(
                config / "fp-lib-table",
                variables,
                set(),
                tables.disabled_footprints,
                reader=read_table,
            )
        )
    tables.footprints.update(
        lib_tables._read(
            project / "fp-lib-table",
            variables,
            set(),
            tables.disabled_footprints,
            reader=read_table,
        )
    )
    if removal_path is not None:
        from .sync_removal import parse

        raw_selection = read(removal_path)
        if raw_selection is None:
            raise FileNotFoundError(removal_path)
        parse(raw_selection)
    board = Board(Document.parse(raw_board.decode("utf-8")), board_path)
    rows = sync._rows(found.components, board_update._records(board), sync.DEFAULTS)
    needed = {item["footprint"] for item in rows["add"]} | {
        item["to"] for item in rows["change_footprint"]
    }
    libraries = {}
    for fpid in sorted(needed):
        nickname, separator, name = fpid.partition(":")
        entry = tables.footprints.get(nickname)
        if not separator or not name or entry is None or entry.kind != "KiCad":
            libraries[fpid] = None
            continue
        if "/" in name or "\\" in name or name in {".", ".."}:
            raise InputError("Footprint names must not contain path traversal.", footprint=fpid)
        path = entry.path / (name + ".kicad_mod")
        path = _absolute(path if path.is_absolute() else project / path)
        raw = read(path)
        if raw is not None and Document.parse(raw.decode("utf-8")).root.head != "footprint":
            raise InputError("A consulted library footprint has an invalid root.", path=str(path))
        libraries[fpid] = path
    names = {
        match.group(1) or match.group(2)
        for path in table_paths
        for match in lib_tables._VARIABLE.finditer((read(path) or b"").decode("utf-8"))
    }
    context = _context(project, config, installed, variables, names)
    manifest = {
        "board": str(board_path),
        "schematic": str(schematic_path),
        "files": [records[path] for path in sorted(records)],
        "context": context,
        "resolution": [
            {"fpid": key, "path": str(path) if path else None}
            for key, path in sorted(libraries.items())
        ],
        "source": "One captured byte set for board, complete hierarchy, project rules, consulted "
        "footprint tables/config and required replacement/addition footprints.",
    }
    validate(manifest)
    return Inputs(
        board_path,
        schematic_path,
        MappingProxyType(dict(files)),
        manifest,
        design,
        found,
        built,
        tables,
        MappingProxyType(libraries),
    )


def validate(manifest, *, ignore_board=False):
    """Observe current dependency bytes; this never retries or performs a write."""
    changed, diagnostics, current = [], [], {}
    for old in manifest["files"]:
        if ignore_board and old["path"] == manifest["board"]:
            continue
        row, raw = _read(old["path"], capture_identity=True)
        if row != old:
            changed.append(old["path"])
            diagnostics.append(
                {
                    "path": old["path"],
                    "changed_fields": [key for key in row if row[key] != old.get(key)],
                    "bytes_unchanged": row["sha256"] == old["sha256"],
                }
            )
        current[_absolute(old["path"])] = raw
    if changed:
        raise Drift(
            "Approved sync dependencies drifted.",
            changed_files=changed,
            changed_file_details=diagnostics,
        )
    old = manifest["context"]
    config = lib_tables.default_config_dir()
    config = _absolute(config) if config is not None else None
    installed = kicad_env.find_kicad_root()
    installed = _absolute(installed) if installed else None
    project = Path(old["project_directory"])
    if old["config_directory"] != (str(config) if config is not None else None) or old[
        "installation"
    ] != (str(installed) if installed else None):
        raise Drift("Sync configuration/installation selection drifted.")
    variables = lib_tables._variables(
        project,
        installed,
        config,
        reader=lambda path: current[_absolute(path)],
    )
    context = _context(project, config, installed, variables, [v["name"] for v in old["variables"]])
    if context != old:
        raise Drift("Effective footprint-table variables drifted.")
