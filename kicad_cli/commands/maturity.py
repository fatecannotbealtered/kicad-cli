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
    'board check': ('stable', ['live:docs/evidence/2026-11-12-121848-board-check.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'board create': ('stable', ['live:docs/evidence/2026-11-12-121828-board-create.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'board export': ('stable', ['live:docs/evidence/2026-11-12-121901-board-export.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'board get': ('stable', ['live:docs/evidence/2026-11-12-121954-board-get.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'board render': ('stable', ['live:docs/evidence/2026-11-12-121911-board-render.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'board update': ('stable', ['live:docs/evidence/2026-11-12-122215-board-update.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'doc get': ('stable', ['live:docs/evidence/2026-11-12-121514-doc-get.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'doc list': ('stable', ['live:docs/evidence/2026-11-12-121518-doc-list.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'footprint create': ('stable', ['live:docs/evidence/2026-11-12-121921-footprint-create.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'footprint list': ('stable', ['live:docs/evidence/2026-11-12-121852-footprint-list.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'footprint move': ('stable', ['live:docs/evidence/2026-11-12-122012-footprint-move.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'footprint update': ('stable', ['live:docs/evidence/2026-11-12-122303-footprint-update.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'library get': ('stable', ['live:docs/evidence/2026-11-12-121511-library-get.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'library list': ('stable', ['live:docs/evidence/2026-11-12-121504-library-list.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'net list': ('stable', ['live:docs/evidence/2026-11-12-121538-net-list.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'net update': ('stable', ['live:docs/evidence/2026-11-12-121754-net-update.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'netlist import': ('stable', ['live:docs/evidence/2026-11-12-121933-netlist-import.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'pin update': ('stable', ['live:docs/evidence/2026-11-12-121734-pin-update.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'project get': ('stable', ['live:docs/evidence/2026-11-12-121459-project-get.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'schematic check': ('stable', ['live:docs/evidence/2026-11-12-121529-schematic-check.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'schematic create': ('stable', ['live:docs/evidence/2026-11-12-121608-schematic-create.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'schematic export': ('stable', ['live:docs/evidence/2026-11-12-121548-schematic-export.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'schematic render': ('stable', ['live:docs/evidence/2026-11-12-121558-schematic-render.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'symbol create': ('stable', ['live:docs/evidence/2026-11-12-121618-symbol-create.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'symbol delete': ('stable', ['live:docs/evidence/2026-11-12-121715-symbol-delete.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'symbol get': ('stable', ['live:docs/evidence/2026-11-12-121542-symbol-get.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'symbol list': ('stable', ['live:docs/evidence/2026-11-12-121534-symbol-list.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'symbol move': ('stable', ['live:docs/evidence/2026-11-12-121656-symbol-move.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'symbol update': ('stable', ['live:docs/evidence/2026-11-12-121636-symbol-update.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'track create': ('stable', ['live:docs/evidence/2026-11-12-122101-track-create.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'track delete': ('stable', ['live:docs/evidence/2026-11-12-122146-track-delete.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'track list': ('stable', ['live:docs/evidence/2026-11-12-121857-track-list.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'via create': ('stable', ['live:docs/evidence/2026-11-12-122343-via-create.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'zone create': ('stable', ['live:docs/evidence/2026-11-12-122515-zone-create.json#normal', 'eval:docs/evidence/eval-2026-11-12.json']),
    'zone delete': ('verified', ['live:docs/evidence/2026-11-12-122612-zone-delete.json#normal']),
    'zone list': ('verified', ['live:docs/evidence/2026-11-12-122501-zone-list.json#normal']),
}


def apply(commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for command in commands:
        level, refs = MATURITY.get(command["path"], ("draft", []))
        command["maturity"] = level
        command["evidence"] = list(refs)
    return commands
