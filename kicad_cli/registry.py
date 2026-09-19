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

GLOBAL_OPTIONS: list[dict[str, Any]] = [
    {"name": "compact", "type": "boolean", "default": False, "required": False, "multiple": False},
    {"name": "quiet", "type": "boolean", "default": False, "required": False, "multiple": False},
    {
        "name": "dry-run",
        "type": "boolean",
        "default": False,
        "description": "Preview a write; never combine with confirm.",
        "required": False,
        "multiple": False,
    },
    {
        "name": "json",
        "type": "boolean",
        "default": False,
        "description": "Compatibility alias for JSON output.",
        "required": False,
        "multiple": False,
    },
    {
        "name": "format",
        "type": "string",
        "default": "json",
        "enum": ["json", "text", "raw"],
        "required": False,
        "multiple": False,
    },
    {
        "name": "fields",
        "type": "string",
        "default": None,
        "separator": ",",
        "description": "Top-level data keys; output projection, not a compute filter.",
        "required": False,
        "multiple": False,
    },
    {"name": "confirm", "type": "string", "default": None, "required": False, "multiple": False},
]


SCHEMAS: dict[str, dict[str, Any]] = {
    "reference": {
        "shape": "object",
        "fields": [
            "tool",
            "version",
            "risk_tier",
            "release_readiness",
            "commands",
            "schemas",
            "exit_codes",
            "error_codes",
            "global_options",
        ],
        "untrusted_fields": [],
    },
    "context": {
        "shape": "object",
        "fields": ["version", "env", "account", "config", "credentials"],
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
            "copper_mm",
            "track_count",
            "via_count",
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
    "sch_create": {
        "shape": "object",
        # `drawing.status` is "drawn" or "failed"; on "failed" written.schematic
        # is null and the netlist is still complete. They are separate outputs
        # of the same generator and only the netlist is load-bearing.
        "fields": ["title", "parts", "nets", "unconnected_parts", "written", "drawing", "note"],
        # Everything here is the caller's own specification coming back, but it
        # has been through the KiCad libraries: a symbol name that resolved is
        # a name KiCad has, and a value is whatever the spec said.
        "untrusted_fields": ["title", "parts", "nets", "unconnected_parts"],
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
    "board_from_netlist": {
        "shape": "object",
        "fields": [
            "board",
            "footprints",
            "nets",
            "pads_connected",
            "unmatched_nodes",
            "board_mm",
            "placement",
            "note",
        ],
        # Reference designators, net names and pin numbers all come from the
        # netlist, which came from a schematic, which came from somewhere.
        "untrusted_fields": ["unmatched_nodes"],
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
            "cleared_tracks",
            "escape",
            "fanout",
            "plane_served",
            "ripup",
            "vias",
            "track_len_mm",
            "unconnected_before",
            "unconnected_after",
            "improved",
            "rewidth",
            "rewidth_total",
            "rewidth_ok",
            "rewidth_reverted",
            "rewidth_drc_cause",
            "stripped_segments",
            "skipped_nets",
            "verify",
            "note",
        ],
        "untrusted_fields": [
            "failed",
            "unresolved",
            "rewidth",
            "rewidth_reverted",
            "rewidth_drc_cause",
            "skipped_nets",
        ],
    },
    "board_rewidth": {
        "shape": "object",
        "fields": [
            "mode",
            "rewidth",
            "rewidth_ok",
            "rewidth_total",
            # These four are produced by the same rewidth run as the three
            # above and were simply never declared, so a caller reading
            # `reference` could not know a reverted net would be reported.
            "rewidth_reverted",
            "rewidth_drc_cause",
            "stripped_segments",
            "skipped_nets",
            "verify",
            "tracks",
            "vias",
            "track_len_mm",
            "unconnected_before",
            "unconnected_after",
            "improved",
            "note",
        ],
        # Reports the width actually achieved per net, which is not always the
        # width asked for. The revert reasons quote DRC, which quotes the board.
        "untrusted_fields": [
            "rewidth",
            "rewidth_reverted",
            "rewidth_drc_cause",
            "skipped_nets",
        ],
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
    "board_drc": {
        "shape": "object",
        # `ok_to_fabricate` is the one-field answer; `counts` is the whole
        # report even when `violations` is truncated to --limit.
        "fields": [
            "board",
            "oracle",
            "counts",
            "unconnected_count",
            "ok_to_fabricate",
            "violations",
            "violations_shown",
            "violations_total",
            "unconnected",
            "note",
        ],
        # Violation text quotes reference designators, net names and rule names
        # out of the board file. Data, never instructions.
        "untrusted_fields": ["violations", "unconnected"],
    },
    "board_move": {
        "shape": "object",
        "fields": ["plan", "moved", "courtyard_clash", "note"],
        "untrusted_fields": ["plan", "moved", "courtyard_clash"],
    },
    "board_place": {
        "shape": "object",
        # `improved` is the field to read first: false means the board was left
        # exactly as it was, and every other field describes that same board.
        "fields": [
            "board",
            "improved",
            "moved",
            "moves",
            "hpwl_before_mm",
            "hpwl_after_mm",
            "hpwl_reduction_pct",
            "courtyard_clash",
            "min_courtyard_gap_mm",
            "outside_outline",
            "existing_tracks",
            "verify",
            "note",
        ],
        "untrusted_fields": ["moves", "courtyard_clash", "outside_outline", "verify"],
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


for _name, _values in {
    "sch_link": ["PASS", "FAIL"],
    "sch_audit": ["PASS", "FAIL"],
    "board_plane": ["PASS", "FAIL"],
    "board_parity": ["PASS", "FAIL"],
    "sch_relink": ["PASS", "PARTIAL", "NOOP"],
    "sch_sync_preview": ["CLEAN", "DESTRUCTIVE"],
}.items():
    SCHEMAS[_name]["field_values"] = {"status": _values}


def _cmd(
    path: str,
    kind: str,
    description: str,
    params: list[dict[str, Any]],
    output_schema: str,
    examples: list[str],
    handler: Callable[..., Any],
) -> dict[str, Any]:
    if path == "board route":
        conditions = {
            "nets": {"when": {"mode": ["rewidth"]}, "conflicts_with": ["classes"]},
            "classes": {"when": {"mode": ["rewidth"]}},
            "ripup": {"when": {"mode": ["repair"]}},
            "no-verify": {"when": {"mode": ["rewidth"]}},
            "no-restore": {"when": {"mode": ["rewidth"]}, "requires": ["nets"]},
        }
        for param in params:
            param.update(conditions.get(param["name"], {}))
    return {
        "required_any": [["board", "schematic"]] if path == "sch audit" else [],
        "path": path,
        "type": kind,
        "description": description,
        "params": params,
        "output_schema": output_schema,
        "examples": examples,
        "handler": handler,
    }


def _board_param() -> dict[str, Any]:
    return {"name": "board", "type": "string", "required": True, "multiple": False, "default": None}


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
            [
                {
                    "name": "command",
                    "type": "string",
                    "required": False,
                    "multiple": False,
                    "default": None,
                    "description": "Return just this exact command and its output schema.",
                }
            ],
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
            [
                {
                    "name": "since",
                    "type": "string",
                    "required": False,
                    "multiple": False,
                    "default": None,
                }
            ],
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
                {
                    "name": "oz",
                    "type": "number",
                    "required": False,
                    "multiple": False,
                    "default": 1.0,
                    "unit": "oz",
                    "exclusive_minimum": 0,
                },
                {
                    "name": "dt",
                    "type": "number",
                    "required": False,
                    "multiple": False,
                    "default": 10.0,
                    "unit": "K",
                    "exclusive_minimum": 0,
                },
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
            "board drc",
            "read",
            "Run KiCad's design rule check and report the violations. Exit is 0 whatever "
            "it finds: read ok_to_fabricate and counts, not the exit code.",
            [
                _board_param(),
                {
                    "name": "severity",
                    "type": "string",
                    "required": False,
                    "multiple": False,
                    "default": "all",
                    "enum": ["all", "error", "warning"],
                },
                {
                    "name": "limit",
                    "type": "integer",
                    "required": False,
                    "multiple": False,
                    "default": 50,
                },
            ],
            "board_drc",
            [
                "kicad-cli board drc --board board.kicad_pcb --compact",
                "kicad-cli board drc --board board.kicad_pcb --severity error --compact",
            ],
            board.drc,
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
                {
                    "name": "step",
                    "type": "number",
                    "required": False,
                    "multiple": False,
                    "default": 0.5,
                    "unit": "mm",
                    "exclusive_minimum": 0,
                },
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
            "sch create",
            "write",
            "Build a schematic and its netlist from a JSON circuit specification: parts "
            "named by KiCad library symbol, nets named by the pins they join. Every "
            "symbol and pin is resolved against the installed libraries during the dry "
            "run, so a wrong pin name fails before anything is written.",
            [
                {
                    "name": "spec",
                    "type": "string",
                    "required": True,
                    "multiple": False,
                    "default": None,
                    "description": "Path to the JSON circuit specification.",
                },
                {
                    "name": "out",
                    "type": "string",
                    "required": False,
                    "multiple": False,
                    "default": None,
                    "default_from": "the specification's own directory",
                },
            ],
            "sch_create",
            [
                "kicad-cli sch create --spec circuit.json --dry-run --compact",
                "kicad-cli sch create --spec circuit.json --out build --confirm ct_xxx --compact",
            ],
            sch.create,
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
                {
                    "name": "schematic",
                    "type": "string",
                    "required": False,
                    "multiple": False,
                    "default_from": "<board>.kicad_sch",
                    "default": None,
                },
                {
                    "name": "board",
                    "type": "string",
                    "required": False,
                    "multiple": False,
                    "default": None,
                },
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
                {
                    "name": "schematic",
                    "type": "string",
                    "required": False,
                    "multiple": False,
                    "default_from": "<board>.kicad_sch",
                    "default": None,
                },
            ],
            "sch_sync_preview",
            ["kicad-cli sch sync-preview --board board.kicad_pcb --compact"],
            sync.preview,
        ),
        _cmd(
            "board from-netlist",
            "write",
            "Create a board from a netlist: load each component's footprint, place it, and "
            "join the pads into nets. This is what 'Update PCB from Schematic' does and does "
            "not expose headlessly. Placement is a grid ordered by reference, not a layout.",
            [
                {
                    "name": "netlist",
                    "type": "string",
                    "required": True,
                    "multiple": False,
                    "default": None,
                    "description": "Path to a KiCad netlist, from `sch create` or KiCad itself.",
                },
                {
                    "name": "out",
                    "type": "string",
                    "required": True,
                    "multiple": False,
                    "default": None,
                    "description": "Path of the board to create.",
                },
                {
                    "name": "pitch",
                    "type": "number",
                    "required": False,
                    "multiple": False,
                    "default": 10.0,
                    "unit": "mm",
                    "exclusive_minimum": 0,
                },
                {
                    "name": "margin",
                    "type": "number",
                    "required": False,
                    "multiple": False,
                    "default": 10.0,
                    "unit": "mm",
                    "minimum": 0,
                },
            ],
            "board_from_netlist",
            [
                "kicad-cli board from-netlist --netlist c.net --out c.kicad_pcb --dry-run",
                "kicad-cli board from-netlist --netlist c.net --out c.kicad_pcb --confirm ct_xxx",
            ],
            board.from_netlist,
        ),
        _cmd(
            "board route",
            "write",
            "Grid autorouter: repair what is unconnected, or clear and route the whole board. "
            "Routes each net against copper it treats as immovable -- it does not push and shove, "
            "so a connection needing an existing track to move aside will not be found.",
            [
                _board_param(),
                {
                    "name": "mode",
                    "type": "string",
                    "required": False,
                    "multiple": False,
                    "default": "repair",
                    "enum": ["repair", "full", "rewidth"],
                },
                {
                    "name": "nets",
                    "type": "string",
                    "required": False,
                    "multiple": False,
                    "separator": ",",
                    "default": None,
                },
                {
                    "name": "classes",
                    "type": "string",
                    "required": False,
                    "multiple": False,
                    "default": "PWR_MAIN,BTL_OUT,SWITCH",
                    "separator": ",",
                    "description": "Legacy profile; pass the board's real netclasses explicitly.",
                },
                {
                    "name": "neck",
                    "type": "number",
                    "required": False,
                    "multiple": False,
                    "default": 0.2,
                    "unit": "mm",
                    "exclusive_minimum": 0,
                },
                {
                    "name": "ripup",
                    "type": "boolean",
                    "required": False,
                    "multiple": False,
                    "default": False,
                },
                {
                    "name": "no-verify",
                    "type": "boolean",
                    "required": False,
                    "multiple": False,
                    "default": False,
                },
                {
                    "name": "no-restore",
                    "type": "boolean",
                    "required": False,
                    "multiple": False,
                    "default": False,
                },
                {
                    "name": "ignore-lock",
                    "type": "boolean",
                    "required": False,
                    "multiple": False,
                    "default": False,
                },
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
                {
                    "name": "net",
                    "type": "string",
                    "required": False,
                    "multiple": False,
                    "default": "GND",
                },
                {
                    "name": "min-area",
                    "type": "number",
                    "required": False,
                    "multiple": False,
                    "default": 0.5,
                    "unit": "mm^2",
                    "minimum": 0,
                },
                {
                    "name": "bridge",
                    "type": "boolean",
                    "required": False,
                    "multiple": False,
                    "default": False,
                },
                {
                    "name": "no-verify",
                    "type": "boolean",
                    "required": False,
                    "multiple": False,
                    "default": False,
                },
                {
                    "name": "ignore-lock",
                    "type": "boolean",
                    "required": False,
                    "multiple": False,
                    "default": False,
                },
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
                {
                    "name": "classes",
                    "type": "string",
                    "required": False,
                    "multiple": False,
                    "default": "PWR_MAIN,BTL_OUT,SWITCH",
                    "separator": ",",
                    "description": "Legacy profile; pass the board's real netclasses explicitly.",
                },
                {
                    "name": "neck",
                    "type": "number",
                    "required": False,
                    "multiple": False,
                    "default": 0.2,
                    "unit": "mm",
                    "exclusive_minimum": 0,
                },
                {
                    "name": "ignore-lock",
                    "type": "boolean",
                    "required": False,
                    "multiple": False,
                    "default": False,
                },
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
                {
                    "name": "oz",
                    "type": "number",
                    "required": False,
                    "multiple": False,
                    "default": 1.0,
                    "unit": "oz",
                    "exclusive_minimum": 0,
                },
                {
                    "name": "no-verify",
                    "type": "boolean",
                    "required": False,
                    "multiple": False,
                    "default": False,
                },
                {
                    "name": "ignore-lock",
                    "type": "boolean",
                    "required": False,
                    "multiple": False,
                    "default": False,
                },
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
                {
                    "name": "moves",
                    "type": "string",
                    "required": True,
                    "multiple": False,
                    "default": None,
                },
                {
                    "name": "ignore-lock",
                    "type": "boolean",
                    "required": False,
                    "multiple": False,
                    "default": False,
                },
            ],
            "board_move",
            [
                "kicad-cli board move --board board.kicad_pcb --dry-run --compact",
                "kicad-cli board move --board board.kicad_pcb --confirm ct_xxx --compact",
            ],
            layout.move,
        ),
        _cmd(
            "board place",
            "write",
            "Rearrange parts by connectivity to shorten total wirelength, "
            "reporting half-perimeter wirelength before and after.",
            [
                _board_param(),
                {
                    "name": "iterations",
                    "type": "integer",
                    "required": False,
                    "multiple": False,
                    "default": 200,
                },
                {
                    "name": "clearance",
                    "type": "number",
                    "required": False,
                    "multiple": False,
                    "default": 0.5,
                },
                {
                    "name": "keep",
                    "type": "string",
                    "required": False,
                    "multiple": True,
                    "separator": ",",
                    "default": None,
                },
                {
                    "name": "ignore-lock",
                    "type": "boolean",
                    "required": False,
                    "multiple": False,
                    "default": False,
                },
            ],
            "board_place",
            [
                "kicad-cli board place --board board.kicad_pcb --dry-run --compact",
                "kicad-cli board place --board board.kicad_pcb --confirm ct_xxx --compact",
            ],
            layout.place,
        ),
        _cmd(
            "fab gerber",
            "write",
            "Plot manufacturing layers to Gerber in process, using the project's "
            "existing plot settings unchanged.",
            [
                _board_param(),
                {
                    "name": "layers",
                    "type": "string",
                    "required": False,
                    "multiple": True,
                    "separator": ",",
                    "default_from": "project plot settings",
                    "default": None,
                },
                {
                    "name": "out",
                    "type": "string",
                    "required": False,
                    "multiple": False,
                    "default_from": "<board directory>/fab",
                    "default": None,
                },
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
                {
                    "name": "layers",
                    "type": "string",
                    "required": False,
                    "multiple": True,
                    "separator": ",",
                    "default_from": "project plot settings",
                    "default": None,
                },
                {
                    "name": "out",
                    "type": "string",
                    "required": False,
                    "multiple": False,
                    "default_from": "<board directory>/fab",
                    "default": None,
                },
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
                {
                    "name": "layers",
                    "type": "string",
                    "required": False,
                    "multiple": True,
                    "separator": ",",
                    "default_from": "project plot settings",
                    "default": None,
                },
                {
                    "name": "out",
                    "type": "string",
                    "required": False,
                    "multiple": False,
                    "default_from": "<board directory>/fab",
                    "default": None,
                },
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
                {
                    "name": "layers",
                    "type": "string",
                    "required": False,
                    "multiple": True,
                    "separator": ",",
                    "default_from": "project plot settings",
                    "default": None,
                },
                {
                    "name": "out",
                    "type": "string",
                    "required": False,
                    "multiple": False,
                    "default_from": "<board directory>/fab",
                    "default": None,
                },
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
                {
                    "name": "out",
                    "type": "string",
                    "required": False,
                    "multiple": False,
                    "default_from": "<board directory>/fab",
                    "default": None,
                },
                {
                    "name": "map",
                    "type": "string",
                    "required": False,
                    "multiple": False,
                    "default": "pdf",
                    "enum": ["pdf", "gerber", "svg", "none"],
                },
                {
                    "name": "merge",
                    "type": "boolean",
                    "required": False,
                    "multiple": False,
                    "default": False,
                },
                {
                    "name": "inch",
                    "type": "boolean",
                    "required": False,
                    "multiple": False,
                    "default": False,
                },
                {
                    "name": "aux-origin",
                    "type": "boolean",
                    "required": False,
                    "multiple": False,
                    "default": False,
                },
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
