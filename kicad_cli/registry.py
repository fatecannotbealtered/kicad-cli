"""The command tree, and the machine-readable description of it.

This module is the single source for both dispatch and ``reference``. Keeping
them apart is how a tool's advertised contract silently drifts from what it
actually does, so they are the same table here.

Only commands that actually run are listed. A roadmap entry is not a
capability, and ``reference`` is read by agents deciding what to call.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

SCHEMAS: dict[str, dict[str, Any]] = {
    "reference": {
        "shape": "object",
        "fields": ["tool", "version", "release_readiness", "commands", "schemas", "exit_codes"],
        "untrusted_fields": [],
    },
    "context": {
        "shape": "object",
        "fields": ["env", "account", "config", "credentials"],
        "untrusted_fields": [],
    },
    "doctor": {
        "shape": "object",
        "fields": ["checks"],
        "untrusted_fields": [],
    },
    "changelog": {
        "shape": "object",
        "fields": ["current_version", "since", "entries"],
        "untrusted_fields": [],
    },
    "board_audit": {
        "shape": "object",
        # Corrected once a strict-mode check was added: the declaration listed
        # stackup/zones/netclasses, which this command has never emitted, and
        # omitted four fields it always has. Nothing was verifying it.
        "fields": [
            "board",
            "board_mm",
            "copper_layers",
            "copper_oz",
            "delta_t_c",
            "summary",
            "findings",
            "width_compliance",
            "width_by_net",
        ],
        # Reference designators, net names, footprint names and silkscreen text
        # all come from the board file. Treat them as data, never as instructions.
        "untrusted_fields": ["findings", "width_by_net"],
    },
    "sch_link": {
        "shape": "object",
        "fields": [
            "board",
            "schematic_files",
            "footprints",
            "root_schematic",
            "footprints_from_schematic",
            "board_only",
            "unannotated",
            "symbols",
            "linked",
            "unlinked",
            "dangling",
            "mismatched",
            "orphan_symbols",
            "virtual_symbols",
            "multi_unit_parts",
            "at_risk",
            "status",
            "findings",
            "details",
            "not_checked",
        ],
        # Reference designators and library ids come from the design files.
        "untrusted_fields": ["findings", "details"],
    },
    "sch_relink": {
        "shape": "object",
        "fields": [
            "board",
            "backup",
            "written",
            "counts",
            "multi_unit_parts",
            "verified",
            "status",
            "not_checked",
        ],
        "untrusted_fields": ["written"],
    },
    "sch_audit": {
        "shape": "object",
        "fields": [
            "schematic",
            "sheets",
            "as_configured",
            "silenced_rules",
            "with_rules_enabled",
            "cross_sheet_connectivity",
            "footprint_filters",
            "findings",
            "status",
            "not_checked",
        ],
        "untrusted_fields": ["findings", "as_configured", "with_rules_enabled"],
    },
    "sch_sync_preview": {
        "shape": "object",
        "fields": [
            "board",
            "schematic",
            "components",
            "footprints",
            "preflight",
            "assumptions",
            "counts",
            "matrix",
            "dialog_preview",
            "findings",
            "status",
            "not_checked",
        ],
        "untrusted_fields": ["findings", "dialog_preview", "preflight"],
    },
    # One schema per write command, because they genuinely return different
    # things. A single shared shape would have to be the union of all of them,
    # which promises fields that half the commands never emit -- and the
    # contract is enforced exactly, so a promise not kept is a broken contract.
    "board_plane": {
        "shape": "object",
        "fields": [
            "board",
            "stackup",
            "plane_layers",
            "coverage",
            "reference_map",
            "samples",
            "backed_fraction",
            "segments_without_reference",
            "segments_crossing_split",
            "worst_nets",
            "stitch_candidates",
            "hotspots",
            "gaps",
            "splits",
            "findings",
            "status",
            "not_checked",
        ],
        "untrusted_fields": ["findings", "gaps", "splits", "worst_nets", "hotspots"],
    },
    "board_live": {
        "shape": "object",
        "fields": [
            "connected",
            "kicad_version",
            "open_documents",
            "board",
            "capabilities",
            "not_checked",
        ],
        "untrusted_fields": ["open_documents", "board"],
    },
    "board_route": {
        "shape": "object",
        "fields": [
            "mode",
            "targets",
            "routed",
            "failed",
            "unresolved",
            "tracks",
            "existing_tracks",
            "vias",
            "track_len_mm",
            "unconnected_before",
            "unconnected_after",
            "improved",
            "note",
        ],
        "untrusted_fields": ["failed", "unresolved"],
    },
    "board_rewidth": {
        "shape": "object",
        "fields": [
            "mode",
            "rewidth",
            "rewidth_ok",
            "rewidth_total",
            "tracks",
            "vias",
            "track_len_mm",
            "unconnected_before",
            "unconnected_after",
            "improved",
        ],
        # Reports the width actually achieved per net, which is not always the
        # width asked for.
        "untrusted_fields": ["rewidth"],
    },
    "board_stitch": {
        "shape": "object",
        "fields": [
            "net",
            "vias_added",
            "plan",
            "bridges",
            "reverted",
            "skipped",
            "unconnected_before",
            "unconnected_after",
            "improved",
            "note",
        ],
        "untrusted_fields": ["plan", "reverted", "skipped", "bridges"],
    },
    "board_widen": {
        "shape": "object",
        "fields": ["board", "classes", "unconnected", "verify", "note"],
        "untrusted_fields": ["classes", "verify"],
    },
    "board_move": {
        "shape": "object",
        "fields": ["plan", "moved", "courtyard_clash", "note"],
        "untrusted_fields": ["plan", "moved", "courtyard_clash"],
    },
    "fab_plot": {
        "shape": "object",
        "fields": ["board", "format", "output_dir", "files", "note"],
        "untrusted_fields": ["files"],
    },
    "fab_drill": {
        "shape": "object",
        "fields": ["board", "output_dir", "files", "reconciled", "settings", "holes", "note"],
        "untrusted_fields": ["files"],
    },
    "board_parity": {
        "shape": "object",
        "fields": [
            "status",
            "board",
            "netlist_source",
            "schematic_components",
            "pcb_components",
            "schematic_nets",
            "pcb_nets",
            "unconnected",
            "diffs",
            "limits",
        ],
        "untrusted_fields": ["diffs"],
    },
}


def _cmd(
    path: str,
    kind: str,
    description: str,
    params: list[dict[str, Any]],
    output_schema: str,
    examples: list[str],
    handler: Callable[..., Any],
) -> dict[str, Any]:
    return {
        "path": path,
        "type": kind,
        "description": description,
        "params": params,
        "output_schema": output_schema,
        "examples": examples,
        "handler": handler,
    }


def _board_param() -> dict[str, Any]:
    return {"name": "board", "type": "string", "required": True, "multiple": False}


def build() -> list[dict[str, Any]]:
    from .commands import (
        board,
        changelog,
        context,
        doctor,
        erc,
        fab,
        layout,
        live,
        reference,
        sch,
        sync,
    )

    return [
        _cmd(
            "reference",
            "read",
            "Describe every command, parameter, output schema and exit code this tool exposes.",
            [],
            "reference",
            ["kicad-cli reference --compact"],
            reference.run,
        ),
        _cmd(
            "context",
            "read",
            "Report the resolved KiCad install, interpreter and configuration this run would use.",
            [],
            "context",
            ["kicad-cli context --compact"],
            context.run,
        ),
        _cmd(
            "doctor",
            "read",
            "Check environment and release readiness; "
            "every non-pass check carries an actionable fix.",
            [],
            "doctor",
            ["kicad-cli doctor --compact"],
            doctor.run,
        ),
        _cmd(
            "changelog",
            "read",
            "Report what changed between versions, so an agent can refresh stale assumptions.",
            [{"name": "since", "type": "string", "required": False, "multiple": False}],
            "changelog",
            ["kicad-cli changelog --compact", "kicad-cli changelog --since 0.1.0 --compact"],
            changelog.run,
        ),
        _cmd(
            "board audit",
            "read",
            "Audit design quality: stackup, copper pours, netclass sizing by carrying current, "
            "RF keepouts and fine-pitch clearance.",
            [
                _board_param(),
                {"name": "oz", "type": "number", "required": False, "multiple": False},
                {"name": "dt", "type": "number", "required": False, "multiple": False},
            ],
            "board_audit",
            ["kicad-cli board audit --board board.kicad_pcb --compact"],
            board.audit,
        ),
        _cmd(
            "board parity",
            "read",
            "Verify the .kicad_pcb still matches its .kicad_sch: "
            "components, nets and pad membership.",
            [_board_param()],
            "board_parity",
            ["kicad-cli board parity --board board.kicad_pcb --compact"],
            board.parity,
        ),
        _cmd(
            "board live",
            "read",
            "Report what the running KiCad has open, over its IPC API. Unlike every other "
            "command here, this works on the board in the editor rather than on the file, "
            "so changes made this way appear on screen and can be undone with Ctrl+Z.",
            [],
            "board_live",
            ["kicad-cli board live --compact"],
            live.status,
        ),
        _cmd(
            "board plane",
            "read",
            "Check the reference plane beneath every track: gaps where the layer under a "
            "track has no copper, and points where a track crosses a split in the plane. "
            "Both force the return current to detour, and DRC reports neither.",
            [
                _board_param(),
                {"name": "step", "type": "number", "required": False, "multiple": False},
            ],
            "board_plane",
            ["kicad-cli board plane --board board.kicad_pcb --compact"],
            board.plane,
        ),
        _cmd(
            "sch link",
            "read",
            "Check that every footprint still carries the uuid of its schematic symbol. "
            "Without that link, updating the PCB from the schematic deletes and recreates "
            "every part, taking placement and routing with it.",
            [_board_param()],
            "sch_link",
            ["kicad-cli sch link --board board.kicad_pcb --compact"],
            sch.link,
        ),
        _cmd(
            "sch relink",
            "write",
            "Write each footprint's schematic symbol uuid back into its path field, "
            "restoring the link that 'Update PCB from Schematic' matches on. Paths come "
            "from KiCad's own netlist export, not from a reconstruction.",
            [_board_param()],
            "sch_relink",
            [
                "kicad-cli sch relink --board board.kicad_pcb --dry-run --compact",
                "kicad-cli sch relink --board board.kicad_pcb --confirm ct_xxx --compact",
            ],
            sch.relink,
        ),
        _cmd(
            "sch audit",
            "read",
            "Report what ERC is not telling you: which rules are switched off, and what "
            "they would report if they were on. The silenced rules are re-run in a copy "
            "of the project, so the answer is measured rather than inferred.",
            [
                {"name": "schematic", "type": "string", "required": False, "multiple": False},
                {"name": "board", "type": "string", "required": False, "multiple": False},
            ],
            "sch_audit",
            ["kicad-cli sch audit --schematic design.kicad_sch --compact"],
            erc.audit,
        ),
        _cmd(
            "sch sync-preview",
            "read",
            "Report what KiCad's 'Update PCB from Schematic' would do, without opening it. "
            "Uses the updater's own uuid-path matching rule, so it sees broken links that "
            "the reference-based schematic-parity check misses entirely.",
            [
                _board_param(),
                {"name": "schematic", "type": "string", "required": False, "multiple": False},
            ],
            "sch_sync_preview",
            ["kicad-cli sch sync-preview --board board.kicad_pcb --compact"],
            sync.preview,
        ),
        _cmd(
            "board route",
            "write",
            "Grid autorouter: repair what is unconnected, or clear and route the whole board. "
            "Routes each net against copper it treats as immovable -- it does not push and shove, "
            "so a connection needing an existing track to move aside will not be found.",
            [
                _board_param(),
                {"name": "mode", "type": "string", "required": False, "multiple": False},
                {"name": "nets", "type": "string", "required": False, "multiple": False},
                {"name": "classes", "type": "string", "required": False, "multiple": False},
                {"name": "neck", "type": "number", "required": False, "multiple": False},
                {"name": "ripup", "type": "boolean", "required": False, "multiple": False},
                {"name": "no-verify", "type": "boolean", "required": False, "multiple": False},
                {"name": "no-restore", "type": "boolean", "required": False, "multiple": False},
                {"name": "ignore-lock", "type": "boolean", "required": False, "multiple": False},
            ],
            "board_route",
            [
                "kicad-cli board route --board board.kicad_pcb --dry-run --compact",
                "kicad-cli board route --board board.kicad_pcb --confirm ct_xxx --compact",
            ],
            layout.route,
        ),
        _cmd(
            "board stitch",
            "write",
            "Join copper-pour islands of the same net with vias. An island with pads on it and "
            "no via is electrically separate, and DRC says nothing about it.",
            [
                _board_param(),
                {"name": "net", "type": "string", "required": False, "multiple": False},
                {"name": "min-area", "type": "number", "required": False, "multiple": False},
                {"name": "bridge", "type": "boolean", "required": False, "multiple": False},
                {"name": "no-verify", "type": "boolean", "required": False, "multiple": False},
                {"name": "ignore-lock", "type": "boolean", "required": False, "multiple": False},
            ],
            "board_stitch",
            [
                "kicad-cli board stitch --board board.kicad_pcb --dry-run --compact",
                "kicad-cli board stitch --board board.kicad_pcb --confirm ct_xxx --compact",
            ],
            layout.stitch,
        ),
        _cmd(
            "board rewidth",
            "write",
            "Re-route nets end to end at their netclass width, and report the width actually "
            "achieved rather than the width requested.",
            [
                _board_param(),
                {"name": "classes", "type": "string", "required": False, "multiple": False},
                {"name": "neck", "type": "number", "required": False, "multiple": False},
                {"name": "ignore-lock", "type": "boolean", "required": False, "multiple": False},
            ],
            "board_rewidth",
            [
                "kicad-cli board rewidth --board board.kicad_pcb --dry-run --compact",
                "kicad-cli board rewidth --board board.kicad_pcb --confirm ct_xxx --compact",
            ],
            layout.rewidth,
        ),
        _cmd(
            "board widen",
            "write",
            "Widen existing tracks in place as far as each will go without conflicting. Cheaper "
            "than re-routing and often not enough.",
            [
                _board_param(),
                {"name": "oz", "type": "number", "required": False, "multiple": False},
                {"name": "no-verify", "type": "boolean", "required": False, "multiple": False},
                {"name": "ignore-lock", "type": "boolean", "required": False, "multiple": False},
            ],
            "board_widen",
            [
                "kicad-cli board widen --board board.kicad_pcb --dry-run --compact",
                "kicad-cli board widen --board board.kicad_pcb --confirm ct_xxx --compact",
            ],
            layout.widen,
        ),
        _cmd(
            "board move",
            "write",
            "Move parts to given coordinates, with a courtyard collision check.",
            [
                _board_param(),
                {"name": "moves", "type": "string", "required": False, "multiple": False},
                {"name": "ignore-lock", "type": "boolean", "required": False, "multiple": False},
            ],
            "board_move",
            [
                "kicad-cli board move --board board.kicad_pcb --dry-run --compact",
                "kicad-cli board move --board board.kicad_pcb --confirm ct_xxx --compact",
            ],
            layout.move,
        ),
        _cmd(
            "fab gerber",
            "write",
            "Plot manufacturing layers to Gerber in process, using the project's "
            "existing plot settings unchanged.",
            [
                _board_param(),
                {"name": "layers", "type": "string", "required": False, "multiple": True},
                {"name": "out", "type": "string", "required": False, "multiple": False},
            ],
            "fab_plot",
            [
                "kicad-cli fab gerber --board board.kicad_pcb --dry-run --compact",
                "kicad-cli fab gerber --board board.kicad_pcb --confirm ct_xxx --compact",
            ],
            fab.gerber,
        ),
        _cmd(
            "fab pdf",
            "write",
            "Plot manufacturing layers to PDF in process, using the project's "
            "existing plot settings unchanged.",
            [
                _board_param(),
                {"name": "layers", "type": "string", "required": False, "multiple": True},
                {"name": "out", "type": "string", "required": False, "multiple": False},
            ],
            "fab_plot",
            [
                "kicad-cli fab pdf --board board.kicad_pcb --dry-run --compact",
                "kicad-cli fab pdf --board board.kicad_pcb --confirm ct_xxx --compact",
            ],
            fab.pdf,
        ),
        _cmd(
            "fab svg",
            "write",
            "Plot manufacturing layers to SVG in process, using the project's "
            "existing plot settings unchanged.",
            [
                _board_param(),
                {"name": "layers", "type": "string", "required": False, "multiple": True},
                {"name": "out", "type": "string", "required": False, "multiple": False},
            ],
            "fab_plot",
            [
                "kicad-cli fab svg --board board.kicad_pcb --dry-run --compact",
                "kicad-cli fab svg --board board.kicad_pcb --confirm ct_xxx --compact",
            ],
            fab.svg,
        ),
        _cmd(
            "fab dxf",
            "write",
            "Plot manufacturing layers to DXF in process, using the project's "
            "existing plot settings unchanged.",
            [
                _board_param(),
                {"name": "layers", "type": "string", "required": False, "multiple": True},
                {"name": "out", "type": "string", "required": False, "multiple": False},
            ],
            "fab_plot",
            [
                "kicad-cli fab dxf --board board.kicad_pcb --dry-run --compact",
                "kicad-cli fab dxf --board board.kicad_pcb --confirm ct_xxx --compact",
            ],
            fab.dxf,
        ),
        _cmd(
            "fab drill",
            "write",
            "Write Excellon drill files, the drill map and the drill report. Without "
            "these the Gerber set is not a manufacturable package.",
            [
                _board_param(),
                {"name": "out", "type": "string", "required": False, "multiple": False},
                {"name": "map", "type": "string", "required": False, "multiple": False},
                {"name": "merge", "type": "boolean", "required": False, "multiple": False},
                {"name": "inch", "type": "boolean", "required": False, "multiple": False},
                {"name": "aux-origin", "type": "boolean", "required": False, "multiple": False},
            ],
            "fab_drill",
            [
                "kicad-cli fab drill --board board.kicad_pcb --dry-run --compact",
                "kicad-cli fab drill --board board.kicad_pcb --confirm ct_xxx --compact",
            ],
            fab.drill,
        ),
    ]


def lookup(
    commands: list[dict[str, Any]], argv: list[str]
) -> tuple[dict[str, Any] | None, list[str]]:
    """Longest-prefix match so ``board audit`` wins over a hypothetical ``board``."""
    for width in (2, 1):
        if len(argv) >= width:
            path = " ".join(argv[:width])
            for c in commands:
                if c["path"] == path:
                    return c, argv[width:]
    return None, argv
