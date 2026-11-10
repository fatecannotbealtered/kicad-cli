"""Registrations of the project group: project get, library list, library get (DCLI-029)."""
from __future__ import annotations

from typing import Any

PAGE = [
    {"name": "--limit", "type": "integer", "summary": "Items per page; default 50."},
    {"name": "--offset", "type": "integer", "summary": "Items to skip, from next_offset of the previous page."},
]
LIBRARY_TYPE = {"name": "--type", "type": "string", "summary": "symbol, footprint or all (default)."}
DOC_NOTE = "--doc is the project file (.kicad_pro), or the project's schematic or board."

COMMANDS: list[dict[str, Any]] = [
    {
        "path": "project get",
        "summary": "Read a project: its files, design rules and net classes.",
        "effects": ["observe"],
        "modes": ["file"],
        "target_kind": "project",
        "flags": [],
        "untrusted_fields": ["name", "net_classes", "netclass_patterns"],
        "irreversible": False,
        "halt": False,
        "tolerances": {},
        "default_fields": [],
        "related_knowledge": ["design-rules-files"],
        "output_schema": {"type": "object", "properties": {
            "name": {"type": "string"}, "path": {"type": "string"}, "files": {"type": "object"},
            "rules": {"type": "object"}, "net_classes": {"type": "array"}, "netclass_patterns": {"type": "array"}}},
        "examples": ["kicad-cli project get --doc D:/work/demo/demo.kicad_pro"],
        "maturity": "draft",
        "evidence": [],
    },
    {
        "path": "library list",
        "summary": "List the symbol and footprint libraries a project sees.",
        "effects": ["observe"],
        "modes": ["file"],
        "target_kind": "library",
        "flags": [LIBRARY_TYPE, {"name": "--query", "type": "string", "summary": "Part of a library nickname."}, *PAGE],
        "untrusted_fields": ["id", "path"],
        "irreversible": False,
        "halt": False,
        "tolerances": {},
        "default_fields": ["id", "type", "scope", "writable"],
        "related_knowledge": ["library-changes-do-not-refresh-instances"],
        "output_schema": {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "type": {"type": "string"}, "scope": {"type": "string"}, "format": {"type": "string"},
            "path": {"type": "string"}, "exists": {"type": "boolean"}, "writable": {"type": "boolean"}}}}}},
        "examples": ["kicad-cli library list --doc D:/work/demo/demo.kicad_pro --type symbol --query Device"],
        "maturity": "draft",
        "evidence": [],
    },
    {
        "path": "library get",
        "summary": "Read one library and the names of its symbols or footprints.",
        "effects": ["observe"],
        "modes": ["file"],
        "target_kind": "library",
        "flags": [
            {"name": "--id", "type": "string", "required": True, "summary": "The library nickname, such as Device."},
            LIBRARY_TYPE,
            {"name": "--query", "type": "string", "summary": "Part of a symbol or footprint name."},
            *PAGE,
        ],
        "untrusted_fields": ["id", "path", "items"],
        "irreversible": False,
        "halt": False,
        "tolerances": {},
        "default_fields": [],
        "related_knowledge": ["library-changes-do-not-refresh-instances"],
        "output_schema": {"type": "object", "properties": {
            "id": {"type": "string"}, "type": {"type": "string"}, "scope": {"type": "string"}, "path": {"type": "string"},
            "item_count": {"type": "integer"}, "items": {"type": "array"}}},
        "examples": ["kicad-cli library get --doc D:/work/demo/demo.kicad_pro --id Device --query LED"],
        "maturity": "draft",
        "evidence": [],
    },
]
