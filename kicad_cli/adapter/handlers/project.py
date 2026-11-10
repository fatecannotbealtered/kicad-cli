"""project get, library list, library get: what a project is made of, read from its files."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .. import base
from ..fileformat import lib_tables
from ..fileformat.sexpr import Document, SexprError

RULE_FIELDS = (
    "min_clearance", "min_track_width", "min_via_diameter", "min_via_annular_width", "min_through_hole_diameter",
    "min_hole_clearance", "min_hole_to_hole", "min_copper_edge_clearance", "min_silk_clearance",
)


def load_project(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise base.invalid(f"{path.name} cannot be read as a KiCad project file: {exc}.") from None
    if not isinstance(data, dict):
        raise base.invalid(f"{path.name} is not a KiCad project file.")
    return data


def project_of(doc: Path) -> Path:
    """The project file of a project, schematic or board document."""
    project = doc if doc.suffix == ".kicad_pro" else doc.with_suffix(".kicad_pro")
    if not project.is_file():
        raise base.not_found(f"There is no project file {project.name} beside {doc.name}.")
    return project


def tables(project: Path) -> lib_tables.Tables:
    found = base._found()
    return lib_tables.Tables.of(project.parent, found.root if found else None)


def net_classes(data: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for c in (data.get("net_settings") or {}).get("classes") or []:
        out.append({"name": c.get("name"), "track_width_mm": c.get("track_width"), "clearance_mm": c.get("clearance"),
                    "via_diameter_mm": c.get("via_diameter"), "via_drill_mm": c.get("via_drill")})
    return out


def libraries(project: Path) -> list[dict[str, Any]]:
    """Every enabled library the project sees: the user's tables, then its own."""
    found = base._found()
    config = lib_tables.default_config_dir()
    variables = lib_tables._variables(project.parent, found.root if found else None, config)
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for kind, table in (("symbol", "sym-lib-table"), ("footprint", "fp-lib-table")):
        for scope, folder in (("global", config), ("project", project.parent)):
            if folder is None:
                continue
            for lib in lib_tables._read(folder / table, variables, set()).values():
                writable = scope == "project" and lib.kind == "KiCad" and (
                    os.access(lib.path, os.W_OK) if lib.path.exists() else os.access(lib.path.parent, os.W_OK))
                out[(kind, lib.nickname)] = {"id": lib.nickname, "type": kind, "scope": scope, "format": lib.kind,
                                             "path": str(lib.path), "exists": lib.path.exists(), "writable": writable}
    return sorted(out.values(), key=lambda x: (x["type"], x["id"].casefold()))


class ProjectGet(base.Handler):
    def read(self) -> Any:
        project = project_of(self.call.require_doc(".kicad_pro", ".kicad_sch", ".kicad_pcb"))
        data = load_project(project)
        stem = project.with_suffix("")
        files = {name: str(p) for name, p in (
            ("schematic", stem.with_suffix(".kicad_sch")), ("board", stem.with_suffix(".kicad_pcb")),
            ("symbol_table", project.parent / "sym-lib-table"), ("footprint_table", project.parent / "fp-lib-table"),
            ("custom_rules", stem.with_suffix(".kicad_dru"))) if p.exists()}
        rules = ((data.get("board") or {}).get("design_settings") or {}).get("rules") or {}
        patterns = (data.get("net_settings") or {}).get("netclass_patterns") or []
        return {
            "name": project.stem,
            "path": str(project),
            "files": files,
            "rules": {f"{name}_mm": rules[name] for name in RULE_FIELDS if isinstance(rules.get(name), (int, float))},
            "net_classes": net_classes(data),
            "netclass_patterns": [{"pattern": p.get("pattern"), "netclass": p.get("netclass")} for p in patterns],
        }


class LibraryList(base.Handler):
    def read(self) -> Any:
        project = project_of(self.call.require_doc(".kicad_pro", ".kicad_sch", ".kicad_pcb"))
        kind = self.call.inputs.get("type")
        items = [lib for lib in libraries(project) if kind in (None, "all", lib["type"])]
        query = (self.call.inputs.get("query") or "").casefold()
        if query:
            items = [lib for lib in items if query in lib["id"].casefold()]
        return {"items": items}


class LibraryGet(base.Handler):
    """One library and the names of the symbols or footprints it holds."""

    def read(self) -> Any:
        project = project_of(self.call.require_doc(".kicad_pro", ".kicad_sch", ".kicad_pcb"))
        nickname = self.call.inputs["id"]
        kind = self.call.inputs.get("type")
        matches = [lib for lib in libraries(project) if lib["id"] == nickname and kind in (None, "all", lib["type"])]
        if not matches:
            raise base.not_found(f"The project sees no library named {nickname}.")
        if len(matches) > 1:
            raise base.ambiguous(f"Both a symbol and a footprint library are named {nickname}; add --type.",
                                 [{"id": m["id"], "type": m["type"], "path": m["path"]} for m in matches])
        lib = dict(matches[0])
        path = Path(lib["path"])
        if lib["type"] == "symbol":
            names = symbol_names(path)
        else:
            names = sorted(p.stem for p in path.glob("*.kicad_mod")) if path.is_dir() else None
        if names is None:
            raise base.AdapterError("E_NATIVE_FAILURE", f"The library {nickname} cannot be read at {path}.",
                                    dispatch_state="not_started")
        query = (self.call.inputs.get("query") or "").casefold()
        lib["item_count"] = len(names)
        lib["items"] = [{"name": n} for n in names if not query or query in n.casefold()]
        return lib


def symbol_names(path: Path) -> list[str] | None:
    if not path.is_file():
        return None
    try:
        root = Document.load(path).root
    except (OSError, SexprError, UnicodeDecodeError):
        return None
    return sorted((node.value(1) or "").rpartition(":")[2] for node in root.find_all("symbol"))


HANDLERS = {
    "project get": ProjectGet,
    "library list": LibraryList,
    "library get": LibraryGet,
}
