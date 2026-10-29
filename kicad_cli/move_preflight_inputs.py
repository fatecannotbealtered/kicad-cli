"""Bound local inputs and isolated copies for the official move DRC preflight.

This is a finite dependency collector, not a filesystem sandbox or a geometric
checker. Only local KiCad footprint libraries are admitted. Native consumers
receive copies; no original project, library or configuration path is writable
through a staged table. Version probing and report validation belong to the
controller, which must freeze its prepared configuration before formal DRC.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

from . import kicad_env
from .fileformat import lib_tables
from .fileformat.board import Board
from .fileformat.sexpr import Document, quote
from .payload import drc_runner
from .sync_inputs import MAX_FILES, MAX_TOTAL_BYTES, Drift, InputError, _absolute, _read

VERSION = 1
MAX_TOOL_FILES = 4096
MAX_TOOL_BYTES = 512 * 1024 * 1024
RESOURCE_DIRECTORIES = ("resources", "schemas", "internat")
EXECUTION_ENVIRONMENT = {
    "PATH",
    "PATHEXT",
    "SystemRoot",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "APPDATA",
    "LOCALAPPDATA",
    "XDG_CONFIG_HOME",
    "HOME",
    "USERPROFILE",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "DYLD_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES",
    "QT_PLUGIN_PATH",
}


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json_object(raw, path):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError("duplicate object key")
            result[key] = value
        return result

    def invalid(_value):
        raise ValueError("nonfinite JSON value")

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
        if not isinstance(value, dict):
            raise ValueError("object required")
    except (ValueError, UnicodeError) as exc:
        raise InputError("Invalid project or configuration JSON", path=str(path)) from exc


def _identity(info):
    return {
        "device": info.st_dev,
        "inode": info.st_ino,
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
    }


def _safe(path):
    """Reject reparse points, links and unexpected node kinds before following."""
    path = _absolute(path)
    if str(path).startswith(("\\\\", "//")):
        raise InputError("Network paths are unsupported by the move preflight", path=str(path))
    for item in [*reversed(path.parents), path]:
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise InputError("Move preflight inputs must not contain links", path=str(item))
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise InputError("Move preflight inputs must be regular local files", path=str(item))
        if item != path and not stat.S_ISDIR(info.st_mode):
            raise InputError("An input parent is not a directory", path=str(item))
    return path


def _file(path):
    path = _safe(path)
    try:
        before = path.lstat()
    except FileNotFoundError:
        before = None
    if before is not None and (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1):
        raise InputError("Move preflight requires single regular input files", path=str(path))
    row, raw = _read(path)
    try:
        after = path.lstat()
    except FileNotFoundError:
        after = None
    if (before is None) != (after is None) or (
        before is not None and _identity(before) != _identity(after)
    ):
        raise Drift("An input changed while captured", changed_files=[str(path)])
    row["identity"] = _identity(after) if after is not None else None
    return row, raw


class _Collector:
    def __init__(self, max_files=MAX_FILES, max_bytes=MAX_TOTAL_BYTES):
        self.rows, self.raw = {}, {}
        self.max_files, self.max_bytes = max_files, max_bytes
        self.total = 0

    def read(self, path, required=False):
        path = _absolute(path)
        if path not in self.rows:
            row, raw = _file(path)
            self.rows[path], self.raw[path] = row, raw
            self.total += len(raw or b"")
            if len(self.rows) > self.max_files or self.total > self.max_bytes:
                raise InputError("Move preflight dependency bounds exceeded")
        if required and self.raw[path] is None:
            raise InputError("Required move preflight input is missing", path=str(path))
        return self.raw[path]

    def manifest(self):
        return [self.rows[path] for path in sorted(self.rows)]


def _tree(path, collector):
    """A whole bounded configuration/resource tree, including empty directories."""
    path = _safe(path)
    if not path.exists():
        return {"path": str(path), "present": False, "directories": [], "files": []}
    if not path.is_dir():
        raise InputError("Expected a move preflight directory", path=str(path))
    directories, files = [], []

    def walk(directory):
        _safe(directory)
        entries = sorted(directory.iterdir(), key=lambda item: item.name)
        directories.append(
            {
                "relative": directory.relative_to(path).as_posix(),
                "identity": _identity(directory.lstat()),
                "members": [p.name for p in entries],
            }
        )
        if len(directories) + len(files) > collector.max_files:
            raise InputError("Move preflight directory bounds exceeded")
        for item in entries:
            _safe(item)
            if item.is_dir():
                walk(item)
            else:
                collector.read(item, required=True)
                files.append(item.relative_to(path).as_posix())

    walk(path)
    return {"path": str(path), "present": True, "directories": directories, "files": files}


def _selection():
    installed = kicad_env.find_kicad_root()
    official = kicad_env.find_official_cli()
    if not installed or not official:
        raise InputError(
            "Official KiCad installation and CLI are required for move preflight",
            dependency_unavailable=True,
        )
    root, executable = _safe(installed), _safe(official)
    from .installation import official_cli

    if executable.parent != official_cli(root).resolve().parent:
        raise InputError(
            "Move preflight requires the official CLI inside the selected native binary directory",
            dependency_unavailable=True,
        )
    return {
        "installation": str(root),
        "official_executable": str(executable),
        "config_root": str(_safe(drc_runner.config_root())),
        "expected_version_series": "10.0",
        "environment_sha256": {
            name: _sha(value.encode())
            for name, value in sorted(os.environ.items())
            if (name.startswith("KICAD") or name == "KIPRJMOD" or name in EXECUTION_ENVIRONMENT)
            and name not in {"KICAD_CLI_TRACE", "KICAD_CLI_ERROR_TRACE"}
        },
    }


def _tools(selection):
    collector = _Collector(MAX_TOOL_FILES, MAX_TOOL_BYTES)
    executable = Path(selection["official_executable"])
    collector.read(executable, required=True)
    directory = executable.parent
    members = sorted(p.name for p in directory.iterdir() if p.suffix.lower() == ".dll")
    for name in members:
        collector.read(directory / name, required=True)
    from .installation import resources as resource_directory

    resources = [
        _tree(resource_directory(selection["installation"]) / name, collector)
        for name in RESOURCE_DIRECTORIES
    ]
    return {
        "files": collector.manifest(),
        "bin_DLL_members": members,
        "resource_trees": resources,
        "scope": "Official executable, direct bin DLLs, KiCad resources/schemas/translations; "
        "not all operating-system dependencies or arbitrary plugins",
    }


def _location(value, variables, project):
    expanded = value
    for _ in range(16):
        next_value = lib_tables.expand(expanded, variables)
        if next_value == expanded:
            break
        expanded = next_value
    if lib_tables._VARIABLE.search(expanded) or "$" in expanded or "://" in expanded:
        raise InputError("Unresolved or nonlocal footprint library URI", uri=value)
    if not expanded or str(expanded).startswith(("\\\\", "//")):
        raise InputError("A local footprint library path is required", uri=value)
    path = Path(expanded).expanduser()
    return _safe(path if path.is_absolute() else project / path)


def _libraries(board, project, config, installed, collector):
    version_config = config / lib_tables.VERSION
    variables = lib_tables._variables(project, installed, version_config, reader=collector.read)
    tables, disabled, table_paths, visiting = {}, set(), [], set()
    variable_names = set()

    def read_table(path):
        path = _absolute(path)
        raw = collector.read(path)
        table_paths.append(str(path))
        if raw is None:
            return {}
        if path in visiting:
            raise InputError("Cyclic footprint library tables are unsupported", path=str(path))
        visiting.add(path)
        document = Document.parse(raw.decode("utf-8"))
        if document.root.head != "fp_lib_table":
            raise InputError("A footprint library table has an invalid root", path=str(path))
        found = {}
        for entry in document.root.find_all("lib"):
            name, kind, uri = (entry.find(key) for key in ("name", "type", "uri"))
            if any(value is None or not value.value(1) for value in (name, kind, uri)):
                raise InputError("A footprint library table entry is incomplete", path=str(path))
            nickname, kind_name, uri_value = name.value(1), kind.value(1), uri.value(1)
            variable_names.update(
                m.group(1) or m.group(2) for m in lib_tables._VARIABLE.finditer(uri_value)
            )
            if entry.find("disabled") is not None:
                if kind_name != "Table":
                    disabled.add(nickname)
                continue
            if kind_name not in {"KiCad", "Table"}:
                raise InputError(
                    "Only local KiCad footprint libraries are supported",
                    library=nickname,
                    kind=kind_name,
                )
            options = entry.find("options")
            if options is not None and options.value(1):
                raise InputError(
                    "Footprint library options require a separately supported preflight",
                    library=nickname,
                )
            location = _location(uri_value, variables, project)
            if kind_name == "Table":
                if collector.read(location) is None:
                    raise InputError(
                        "An included footprint library table is missing", path=str(location)
                    )
                found.update(read_table(location))
            else:
                found[nickname] = str(location)
        visiting.remove(path)
        return found

    for path in (version_config / "fp-lib-table", project / "fp-lib-table"):
        tables.update(read_table(path))
    resolved = []
    for fpid in sorted({fp.library_id for fp in board.footprints}):
        nickname, separator, name = fpid.partition(":")
        if (
            not separator
            or not nickname
            or not name
            or any(c in name for c in "/\\")
            or name in {".", ".."}
        ):
            raise InputError(
                "Every footprint requires an unambiguous library identity", footprint=fpid
            )
        if nickname in disabled or nickname not in tables:
            raise InputError("A board footprint library is missing or disabled", footprint=fpid)
        directory = _safe(tables[nickname])
        if not directory.is_dir():
            raise InputError("A footprint library directory is missing", path=str(directory))
        path = directory / (name + ".kicad_mod")
        raw = collector.read(path, required=True)
        if Document.parse(raw.decode("utf-8")).root.head not in {"footprint", "module"}:
            raise InputError("An actual footprint library file is invalid", path=str(path))
        resolved.append(
            {
                "fpid": fpid,
                "nickname": nickname,
                "name": name,
                "directory": str(directory),
                "path": str(path),
                "sha256": _sha(raw),
            }
        )
    return {
        "resolved": resolved,
        "consulted_tables": sorted(set(table_paths)),
        "directories": [
            _library_directory(Path(path))
            for path in sorted({item["directory"] for item in resolved})
        ],
        "variables_sha256": {
            name: _sha(variables[name].encode()) if name in variables else None
            for name in sorted(variable_names)
        },
    }


def _library_directory(path):
    path = _safe(path)
    if not path.is_dir():
        raise InputError("A footprint library directory is missing", path=str(path))
    members = sorted(path.iterdir(), key=lambda item: item.name)
    if len(members) > MAX_FILES:
        raise InputError("Footprint library directory membership bounds exceeded")
    for item in members:
        _safe(item)
    return {
        "path": str(path),
        "identity": _identity(path.lstat()),
        "members": [item.name for item in members],
    }


def _workspace(path, protected):
    path = _safe(path)
    for source in protected:
        source = _absolute(source)
        if path == source or path.is_relative_to(source) or source.is_relative_to(path):
            raise InputError(
                "Preflight workspace must be separate from original inputs", path=str(path)
            )
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise InputError("Preflight workspace must be new and empty", path=str(path))
    return path


def capture(board_path: Path, board: Board, workspace: Path) -> dict:
    """Capture source inputs without dispatching KiCad or writing any directory."""
    path = _safe(board_path)
    selection = _selection()
    collector = _Collector()
    raw = collector.read(path, required=True)
    if board.document.dumps().encode("utf-8") != raw:
        raise Drift("Parsed board is not the exact captured source bytes")
    project = path.parent
    for suffix in (".kicad_pro", ".kicad_dru"):
        collector.read(path.with_suffix(suffix))
    project_raw = collector.read(path.with_suffix(".kicad_pro"))
    if project_raw is not None:
        _json_object(project_raw, path.with_suffix(".kicad_pro"))
    config = Path(selection["config_root"])
    config_tree = _tree(config, collector)
    for name in config_tree["files"]:
        if name.endswith(".json"):
            _json_object(collector.read(config / name), config / name)
    libraries = _libraries(board, project, config, Path(selection["installation"]), collector)
    workspace = _workspace(
        workspace,
        [
            project,
            config,
            selection["installation"],
            *{item["directory"] for item in libraries["resolved"]},
        ],
    )
    binding = {
        "version": VERSION,
        "board": str(path),
        "workspace": str(workspace),
        "selection": selection,
        "files": collector.manifest(),
        "configuration_tree": config_tree,
        "libraries": libraries,
        "tools": _tools(selection),
        "policy": {
            "footprint_libraries": "local KiCad only; required board footprints cloned",
            "rules": "project pro and custom dru copied byte for byte, including absence",
            "configuration": (
                "entire seed tree cloned; initialization and formal freeze are separate"
            ),
            "source_writes": False,
            "schematic_parity_or_XML_checked": False,
        },
    }
    validate(binding)
    return binding


def validate(binding: dict) -> None:
    """Reject input, file identity, configuration membership or selection drift."""
    if binding.get("version") != VERSION:
        raise Drift("Unsupported move preflight binding version")
    if _selection() != binding["selection"]:
        raise Drift("Move preflight installation or execution environment changed")
    for old in [*binding["files"], *binding["tools"]["files"]]:
        found, _ = _file(old["path"])
        if found != old:
            raise Drift("Move preflight dependency changed", changed_files=[old["path"]])
    for old in binding["libraries"]["directories"]:
        if _library_directory(Path(old["path"])) != old:
            raise Drift("Move preflight footprint library directory changed")
    config = _tree(Path(binding["selection"]["config_root"]), _Collector())
    if config != binding["configuration_tree"]:
        raise Drift("Move preflight configuration directory membership changed")
    tools = _tools(binding["selection"])
    if tools != binding["tools"]:
        raise Drift("Move preflight official tool dependencies changed")


def _put(path, raw, relocations, source, role):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
    relocations.append(
        {"source": source, "destination": str(path), "sha256": _sha(raw), "role": role}
    )


def stage(binding: dict, candidate_bytes: bytes) -> dict:
    """Create isolated exact source/candidate/preparation copies and local libraries.

    The controller must prove that candidate bytes contain only its requested
    translations. This collector independently binds their bytes, but does not
    substitute a new geometric checker for KiCad.
    """
    validate(binding)
    Board(Document.parse(candidate_bytes.decode("utf-8")))
    workspace = _workspace(
        binding["workspace"],
        [
            Path(binding["board"]).parent,
            binding["selection"]["config_root"],
            binding["selection"]["installation"],
            *{row["directory"] for row in binding["libraries"]["resolved"]},
        ],
    )
    workspace.mkdir(parents=True, exist_ok=True)
    files = {row["path"]: _file(row["path"])[1] for row in binding["files"]}
    relocations, stages = [], {}
    original = Path(binding["board"])
    library_ids = sorted({row["nickname"] for row in binding["libraries"]["resolved"]})
    for label in ("baseline", "candidate", "preparation"):
        project = workspace / label / "project"
        config = workspace / label / "configuration"
        project.mkdir(parents=True)
        config.mkdir(parents=True)
        # KiCad10.0.6/wx on Windows also creates an empty default-location
        # skeleton even with KICAD_DOCUMENTS_HOME. Declare that exact empty
        # tree before freezing; the project guard still refuses any addition.
        if os.name == "nt":
            (project / "KiCad" / lib_tables.VERSION).mkdir(parents=True)
        for suffix in (".kicad_pcb", ".kicad_pro", ".kicad_dru"):
            source = original if suffix == ".kicad_pcb" else original.with_suffix(suffix)
            raw = files[str(source)]
            if raw is not None:
                payload = (
                    candidate_bytes if label == "candidate" and suffix == ".kicad_pcb" else raw
                )
                _put(project / source.name, payload, relocations, str(source), label + suffix)
        seed = Path(binding["selection"]["config_root"])
        for directory in binding["configuration_tree"]["directories"]:
            (config / directory["relative"]).mkdir(parents=True, exist_ok=True)
        for relative in binding["configuration_tree"]["files"]:
            if relative == lib_tables.VERSION + "/fp-lib-table":
                continue  # Its exact bytes go to table evidence, its active copy is relocated.
            _put(
                config / relative,
                files[str(seed / relative)],
                relocations,
                str(seed / relative),
                "configuration_seed",
            )
        destinations = {}
        for index, nickname in enumerate(library_ids):
            destination = workspace / label / "libraries" / f"library-{index + 1:04d}.pretty"
            destination.mkdir(parents=True)
            destinations[nickname] = destination
        for row in binding["libraries"]["resolved"]:
            _put(
                destinations[row["nickname"]] / (row["name"] + ".kicad_mod"),
                files[row["path"]],
                relocations,
                row["path"],
                "required_footprint_library",
            )
        table = (
            "(fp_lib_table\n  (version 7)\n"
            + "".join(
                f"  (lib (name {quote(name)})(type KiCad)"
                f'(uri {quote(str(destinations[name]))})(options "")(descr ""))\n'
                for name in library_ids
            )
            + ")\n"
        )
        generated = table.encode("utf-8")
        _put(
            project / "fp-lib-table",
            generated,
            relocations,
            "generated_required_local_library_projection",
            "relocated_footprint_table",
        )
        version_config = config / lib_tables.VERSION
        version_config.mkdir(parents=True, exist_ok=True)
        # KiCad 10.0.6 creates this empty preference directory even for
        # --version. Declare it in the owned staging before any native call;
        # subsequent directory membership remains strict, including empties.
        colors = version_config / "colors"
        colors.mkdir(exist_ok=True)
        # A private Windows profile may have no registered Documents folder.
        # Bind native user data explicitly; never let its relative fallback
        # create KiCad/10.0 directories inside the staged engineering project.
        documents = config / "documents" / "KiCad" / lib_tables.VERSION
        for name in (
            "3dmodels",
            "3rdparty",
            "footprints",
            "plugins",
            "projects",
            "scripting",
            "symbols",
            "template",
        ):
            (documents / name).mkdir(parents=True, exist_ok=True)
        # Remove only this newly-created cloned table; never alter its original.
        cloned_table = version_config / "fp-lib-table"
        if cloned_table.exists():
            cloned_table.unlink()
        _put(
            cloned_table,
            generated,
            relocations,
            "generated_required_local_library_projection",
            "relocated_config_footprint_table",
        )
        stages[label] = {
            "project": str(project),
            "board": str(project / original.name),
            "config_root": str(config),
            "footprint_table_sha256": _sha(generated),
            "declared_preparation_directory": str(colors),
        }
    for index, table_path in enumerate(binding["libraries"]["consulted_tables"]):
        if files[table_path] is not None:
            _put(
                workspace / "evidence/tables" / f"table-{index + 1:04d}",
                files[table_path],
                relocations,
                table_path,
                "original_table_exact_evidence",
            )
    validate(binding)
    staged_files = []
    for path in sorted(workspace.rglob("*")):
        if path.is_file():
            staged_files.append(
                {
                    "relative": path.relative_to(workspace).as_posix(),
                    "sha256": _sha(path.read_bytes()),
                }
            )
    result = {
        "version": VERSION,
        "workspace": str(workspace),
        "stages": stages,
        "candidate_sha256": _sha(candidate_bytes),
        "baseline_sha256": _sha(files[str(original)]),
        "relocations": relocations,
        "files": staged_files,
        "table_policy": (
            "Only required resolved local libraries; original nested tables retained as evidence"
        ),
        "configuration_prepared": False,
        "formal_configuration_frozen": False,
    }
    (workspace / "STAGING.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result
