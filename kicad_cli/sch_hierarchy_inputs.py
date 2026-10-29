"""Finite, predeclared source inventory for incremental hierarchy batches.

This module captures paths and directory identities. It does not create pages,
choose electrical intent, or write the formal project.
"""

from __future__ import annotations

import re
import stat
from pathlib import Path

from .fileformat.circuit import MAX_HIERARCHY_DEPTH, MAX_HIERARCHY_SOURCES
from .fileformat.schematic import Schematic
from .fileformat.sexpr import Document
from .sync_inputs import Drift, InputError, _absolute

HIERARCHY_ACTIONS = {
    "create_sheet",
    "reference_sheet",
    "update_sheet",
    "update_interface",
    "add_sheet_pin",
}
_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)


def hierarchy_actions(actions):
    return any(action.get("op") in HIERARCHY_ACTIONS for action in actions)


def source_path(root, value):
    """Resolve an explicitly project-root-relative ordinary source pathname."""
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or ":" in value
        or value.startswith("/")
        or any(c in value for c in '\0<>"|?*')
    ):
        raise InputError("Sheet file must be an ordinary project-root-relative path", file=value)
    parts = value.split("/")
    if (
        any(
            part in {"", ".", ".."} or part.endswith((".", " ")) or _RESERVED.match(part)
            for part in parts
        )
        or Path(value).suffix != ".kicad_sch"
    ):
        raise InputError("Sheet file has an unsupported component or suffix", file=value)
    directory = _absolute(root).parent
    path = directory.joinpath(*parts)
    if path.resolve() != path or not path.is_relative_to(directory):
        raise InputError("Sheet file aliases or escapes the project directory", file=value)
    directory_rows(path.parent)
    return path


def directory_rows(directory):
    """Identity only: normal file/sidecar creation may change directory times."""
    rows = []
    for path in reversed((Path(directory), *Path(directory).parents)):
        info = path.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
        ):
            raise InputError(
                "Hierarchy output ancestry must be existing regular directories", path=str(path)
            )
        rows.append({"path": str(path), "device": str(info.st_dev), "inode": str(info.st_ino)})
    return rows


def validate_directories(rows):
    for old in rows:
        try:
            observed = {row["path"]: row for row in directory_rows(Path(old["path"]))}
        except (OSError, InputError) as exc:
            raise Drift("Hierarchy output directory changed", changed_files=[old["path"]]) from exc
        if observed.get(old["path"]) != old:
            raise Drift("Hierarchy output directory identity changed", changed_files=[old["path"]])


def capture(inputs, primary, actions):
    """Add every new absence and referenced source before candidate generation."""
    declarations, references = {}, []
    seen_sources = {path for path in inputs.files if path.suffix == ".kicad_sch"}
    for index, action in enumerate(actions):
        if action.get("op") not in {"create_sheet", "reference_sheet"}:
            continue
        path = source_path(primary, action.get("file"))
        key = str(path).casefold()
        if action["op"] == "create_sheet":
            if key in declarations:
                raise InputError("New sheet source is declared more than once", path=str(path))
            declarations[key] = (index, path)
            seen_sources.add(path)
            if len(seen_sources) > MAX_HIERARCHY_SOURCES:
                raise InputError("Hierarchy source inventory exceeds128 source files")
            inputs.add(path, capture_identity=True)
            if inputs.files[path] is not None:
                raise FileExistsError("New sheet source already exists: " + str(path))
        else:
            references.append((index, path))
    for index, path in references:
        declaration = declarations.get(str(path).casefold())
        if declaration is not None:
            if declaration[0] >= index:
                raise InputError(
                    "Sheet reference must follow its new source declaration", path=str(path)
                )
            continue
        visited = set()

        def walk(source, ancestors, visited=visited):
            source = _absolute(source)
            if len(ancestors) > MAX_HIERARCHY_DEPTH:
                raise InputError("Referenced hierarchy exceeds32 nested sheet levels")
            relative = source.relative_to(_absolute(primary).parent).as_posix()
            source = source_path(primary, relative)
            if source in ancestors:
                raise InputError("Referenced sheet source contains a cycle", path=str(source))
            if source in visited:
                return
            seen_sources.add(source)
            if len(seen_sources) > MAX_HIERARCHY_SOURCES:
                raise InputError("Hierarchy source inventory exceeds128 source files")
            inputs.add(source, required=True, capture_identity=True)
            sheet = Schematic(Document.parse(inputs.files[source].decode("utf-8")), source)
            for child in sheet.sheets:
                child_path = _absolute(source.parent / child.file)
                if not child_path.is_relative_to(_absolute(primary).parent):
                    raise InputError(
                        "Referenced sheet escapes the project directory", path=str(child_path)
                    )
                walk(child_path, {*ancestors, source})
            visited.add(source)

        walk(path, set())
    directories = {}
    for path in [path for path in inputs.files if path.suffix == ".kicad_sch"]:
        inputs.add(path, capture_identity=True)
        # Public standalone selectors bind their root's optional project file,
        # including absence. Imported descendant catalogs must bind it too.
        inputs.add(path.with_suffix(".kicad_pro"), capture_identity=True)
        for row in directory_rows(path.parent):
            directories[row["path"]] = row
    inputs.directories = [directories[name] for name in sorted(directories)]
    inputs.hierarchy = True


def capture_symbol_resources(primary, places, actions, inputs, design):
    """Resolve selected libraries against the exact source page, including new pages."""
    from .sch_symbols import Resources

    resources = Resources(primary)
    aliases = {
        a.get("id"): a for a in actions if a.get("op") in {"create_sheet", "reference_sheet"}
    }
    existing = {instance.path: instance.schematic for instance in design.instances}
    for catalog in reference_catalogs(inputs, primary, actions).values():
        for instance in catalog.design.instances:
            previous = existing.get(instance.path)
            if previous is not None and previous.path != instance.schematic.path:
                raise InputError(
                    "Referenced source instance identity is ambiguous", instance_path=instance.path
                )
            existing[instance.path] = instance.schematic

    def embedded(page):
        if isinstance(page, str):
            if page not in existing:
                raise InputError("Selected symbol source page does not exist", instance_path=page)
            return existing[page]
        action = aliases.get(page.get("new")) if isinstance(page, dict) else None
        if action is None:
            raise InputError("Selected symbol page has no declared new sheet", page=page)
        source_instance = page.get("source_instance")
        if source_instance:
            if source_instance not in existing:
                raise InputError(
                    "Referenced subtree source instance is unknown", instance_path=source_instance
                )
            return existing[source_instance]
        if action["op"] == "create_sheet":
            return None
        path = source_path(primary, action["file"])
        raw = inputs.files.get(path)
        if raw is None:
            # A reference to a source created earlier in this batch is also empty
            # until the batch's explicit place actions populate it.
            return None
        return Schematic(Document.parse(raw.decode("utf-8")), path)

    for name, page in places:
        resources.resolve(name, embedded=embedded(page))
    resources.validate()
    return resources


def reference_catalogs(inputs, primary, actions):
    """Only explicitly imported existing subtrees may supply standalone selectors."""
    from .fileformat.circuit import Design
    from .sch_objects import Catalog

    roots = {}
    for action in actions:
        if action.get("op") != "reference_sheet":
            continue
        path = source_path(primary, action["file"])
        if inputs.files.get(path) is None:
            continue
        design = Design(path, snapshot=dict(inputs.files))
        for source in design.schematics:
            roots[source] = None
    for source in roots:
        roots[source] = Catalog(Design(source, snapshot=dict(inputs.files)), identity_root=source)
    return {catalog.root: catalog for catalog in roots.values()}
