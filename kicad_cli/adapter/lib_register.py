"""Registering a project library in the project's library table.

A project library is named in the project's ``sym-lib-table`` or ``fp-lib-table`` with a
``${KIPRJMOD}`` path. Global (user) tables are never written.
"""
from __future__ import annotations

from pathlib import Path

from .base import invalid
from .fileformat.sexpr import Document, copy, quote

TABLES = {"symbol": ("sym-lib-table", "sym_lib_table", ".kicad_sym"),
          "footprint": ("fp-lib-table", "fp_lib_table", ".pretty")}


def table_path(project_dir: Path, kind: str) -> Path:
    return project_dir / TABLES[kind][0]


def uri_for(project_dir: Path, library: Path) -> str:
    """The table's path of a library: relative to the project folder when inside it."""
    try:
        rel = library.resolve().relative_to(project_dir.resolve())
        return "${KIPRJMOD}/" + rel.as_posix()
    except ValueError:
        return library.resolve().as_posix()


def registered(project_dir: Path, kind: str, nickname: str) -> str | None:
    """The uri a project table gives a nickname, or None."""
    path = table_path(project_dir, kind)
    if not path.is_file():
        return None
    for node in Document.load(path).root.find_all("lib"):
        name = node.find("name")
        if name is not None and name.value(1) == nickname:
            uri = node.find("uri")
            return uri.value(1) if uri is not None else ""
    return None


def with_library(raw: str | None, kind: str, nickname: str, uri: str) -> tuple[str, bool]:
    """The table text with the library added; ``False`` when it was already there."""
    head = TABLES[kind][1]
    doc = Document.parse(raw) if raw else Document.parse(f"({head} (version 7))")
    if doc.root.head != head:
        raise invalid(f"The project's {TABLES[kind][0]} is not a KiCad library table.")
    same = [n for n in doc.root.find_all("lib") if n.find("name") is not None and n.find("name").value(1) == nickname]
    if len(same) > 1:
        raise invalid(f"The project's {TABLES[kind][0]} names {nickname} twice.")
    if same:
        node = same[0]
        if (node.find("uri").value(1) if node.find("uri") is not None else None) != uri:
            raise invalid(f"The project's {TABLES[kind][0]} already names {nickname} for another path.")
        return doc.dumps(), False
    doc.root.append(copy(Document.parse(
        f'(lib (name {quote(nickname)}) (type "KiCad") (uri {quote(uri)}) (options "") (descr ""))').root))
    return doc.dumps(), True
