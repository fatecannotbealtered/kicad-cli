"""The maturity of each domain command and the evidence behind it (DVER-040).

A command is `verified` when every row of its behavior matrix that applies passed on the real
KiCad, with a live record in docs/evidence/; `stable` when, besides, the newest evaluation
report from the real KiCad passed every question and one of them ran the command. A command
not listed here is `draft`. `check_project` checks every level against its evidence.
"""
from __future__ import annotations

from typing import Any

# path: (level, evidence references)
MATURITY: dict[str, tuple[str, list[str]]] = {
    'board check': ('stable', ['live:docs/evidence/2026-11-09-182136-board-check.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'board create': ('stable', ['live:docs/evidence/2026-11-09-182117-board-create.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'board export': ('stable', ['live:docs/evidence/2026-11-09-182144-board-export.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'board get': ('stable', ['live:docs/evidence/2026-11-09-182231-board-get.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'board render': ('stable', ['live:docs/evidence/2026-11-09-182152-board-render.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'board update': ('stable', ['live:docs/evidence/2026-11-09-182522-board-update.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'doc get': ('verified', ['live:docs/evidence/2026-11-09-181718-doc-get.json#normal']),
    'doc list': ('stable', ['live:docs/evidence/2026-11-09-181721-doc-list.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'footprint create': ('stable', ['live:docs/evidence/2026-11-09-182200-footprint-create.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'footprint list': ('stable', ['live:docs/evidence/2026-11-09-182139-footprint-list.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'footprint move': ('stable', ['live:docs/evidence/2026-11-09-182254-footprint-move.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'footprint update': ('stable', ['live:docs/evidence/2026-11-09-182619-footprint-update.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'library get': ('stable', ['live:docs/evidence/2026-11-09-181714-library-get.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'library list': ('stable', ['live:docs/evidence/2026-11-09-181710-library-list.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'net list': ('stable', ['live:docs/evidence/2026-11-09-181750-net-list.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'net update': ('stable', ['live:docs/evidence/2026-11-09-181958-net-update.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'netlist import': ('stable', ['live:docs/evidence/2026-11-09-182210-netlist-import.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'pin update': ('stable', ['live:docs/evidence/2026-11-09-181942-pin-update.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'project get': ('stable', ['live:docs/evidence/2026-11-09-181706-project-get.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'schematic check': ('stable', ['live:docs/evidence/2026-11-09-181741-schematic-check.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'schematic create': ('stable', ['live:docs/evidence/2026-11-09-181827-schematic-create.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'schematic export': ('stable', ['live:docs/evidence/2026-11-09-181810-schematic-export.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'schematic render': ('stable', ['live:docs/evidence/2026-11-09-181819-schematic-render.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'symbol create': ('stable', ['live:docs/evidence/2026-11-09-181835-symbol-create.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'symbol delete': ('stable', ['live:docs/evidence/2026-11-09-181928-symbol-delete.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'symbol get': ('stable', ['live:docs/evidence/2026-11-09-181753-symbol-get.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'symbol list': ('stable', ['live:docs/evidence/2026-11-09-181746-symbol-list.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'symbol move': ('stable', ['live:docs/evidence/2026-11-09-181910-symbol-move.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'symbol update': ('stable', ['live:docs/evidence/2026-11-09-181853-symbol-update.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'track create': ('stable', ['live:docs/evidence/2026-11-09-182353-track-create.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'track delete': ('stable', ['live:docs/evidence/2026-11-09-182449-track-delete.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'track list': ('stable', ['live:docs/evidence/2026-11-09-182141-track-list.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'via create': ('stable', ['live:docs/evidence/2026-11-09-183108-via-create.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'zone create': ('stable', ['live:docs/evidence/2026-11-09-182741-zone-create.json#normal', 'eval:docs/evidence/eval-2026-11-09.json']),
    'zone delete': ('verified', ['live:docs/evidence/2026-11-09-182845-zone-delete.json#normal']),
    'zone list': ('verified', ['live:docs/evidence/2026-11-09-182725-zone-list.json#normal']),
}


def apply(commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for command in commands:
        level, refs = MATURITY.get(command["path"], ("draft", []))
        command["maturity"] = level
        command["evidence"] = list(refs)
    return commands
