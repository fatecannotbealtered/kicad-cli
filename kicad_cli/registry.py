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
            # Per-layer track length. On a two-layer board every bottom-layer
            # signal cuts the ground plane, which the total cannot show.
            "copper_by_layer_mm",
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
        "fields": [
            "title",
            "parts",
            "nets",
            "unconnected_parts",
            "written",
            "drawing",
            "note",
            "creation_geometry",
        ],
        # Everything here is the caller's own specification coming back, but it
        # has been through the KiCad libraries: a symbol name that resolved is
        # a name KiCad has, and a value is whatever the spec said.
        "untrusted_fields": ["title", "parts", "nets", "unconnected_parts", "creation_geometry"],
    },
    "sch_edit": {
        "shape": "object",
        "fields": ["schematic", "changes", "files", "verified", "erc", "status", "not_checked"],
        # Field values, net names and ERC's item descriptions are the design's text.
        "untrusted_fields": ["changes", "verified", "erc"],
    },
    "board_update": {
        "shape": "object",
        "fields": [
            "board",
            "schematic",
            "counts",
            "changes",
            "skipped",
            "verified",
            "status",
            "not_checked",
        ],
        # References, values, fields and net names are the design's own text.
        "untrusted_fields": ["changes", "skipped", "verified"],
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
        # Label names, references and footprints are the design's own text.
        "untrusted_fields": [
            "findings",
            "as_configured",
            "with_rules_enabled",
            "cross_sheet_connectivity",
            "footprint_filters",
        ],
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
            "project_files",
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
            "route_search",
            # engine=grid 的那套字段与 engine=freerouting 的那套互不适用，
            # 不适用的一律为 null——和三个 mode 的处理方式一致。
            "engine",
            "passes",
            "layer_policy",
            "layer_policy_applied",
            "layer_policy_fallback",
            "copper_by_layer_mm",
            "freerouting_unrouted",
            "freerouting_violations",
            "necked_tracks_widened",
            "min_track_width_mm",
            "board",
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
            # plane_nets: nets with a pour, empty unless --use-planes.
            # plane_served: those whose fanout actually reaches it, so the
            # router left them to the plane.
            "plane_nets",
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
            # Lists of net names read out of the board file.
            "plane_nets",
            "plane_served",
            "verify",
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
            "verify",
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
            "engine",
            "counts",
            "excluded_count",
            "unconnected_count",
            "ok_to_fabricate",
            "violations",
            "violations_shown",
            "violations_total",
            "unconnected",
            "not_checked",
            "partial",
            "custom_rules",
            "note",
        ],
        # Violation text quotes reference designators, net names and rule names
        # out of the board file, and custom_rules the rules file's own words.
        # Data, never instructions.
        "untrusted_fields": ["violations", "unconnected", "custom_rules"],
    },
    "board_move": {
        "shape": "object",
        "fields": ["plan", "moved", "courtyard_clash", "receipt", "note"],
        "untrusted_fields": ["plan", "moved", "courtyard_clash", "receipt"],
    },
    "board_pour": {
        "shape": "object",
        # `filled_mm2` is measured after the fill, not before: asking a zone for
        # its area before filling reports zero and reads as an empty pour.
        "fields": [
            "board",
            "net",
            "layer",
            "area_mm",
            "filled_mm2",
            "outlines",
            "connect",
            "clearance_mm",
            "tracks",
            "note",
        ],
        "untrusted_fields": ["net", "layer"],
    },
    "board_netclass": {
        "shape": "object",
        # `action` is "created" or "updated". The copper is unchanged either
        # way: a netclass is a target, and `board rewidth` is what applies it.
        "fields": [
            "project",
            "netclass",
            "action",
            "params",
            "ampacity_a",
            "assigned",
            "already_assigned",
            "note",
        ],
        "untrusted_fields": ["assigned", "already_assigned"],
    },
    "board_silkscreen": {
        "shape": "object",
        # `crowded` is how many were in the way; `moved` how many were fixed;
        # `stuck` names the ones with nowhere free to go.
        "fields": [
            "board",
            "references",
            "crowded",
            "moved",
            "moves",
            "stuck",
            "clearance_mm",
            # How far the text had to go. Legible is the floor; still obviously
            # belonging to its own part is the bar, and distance is what tells
            # the two apart.
            "max_move_mm",
            "mean_move_mm",
            "verify",
            "note",
        ],
        "untrusted_fields": ["moves", "stuck"],
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
        "fields": ["board", "format", "output_dir", "files", "settings", "note"],
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
    "board_update": ["PASS", "PARTIAL", "NOOP"],
    "sch_edit": ["PASS", "NOOP"],
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
            # Only `full` plans from scratch, which is when leaving a net to
            # its pour is a decision rather than a retrofit.
            "use-planes": {"when": {"mode": ["full"]}},
            # freerouting 自己决定怎么布，--mode 对它没有意义；--passes 只对它有。
            "passes": {"when": {"engine": ["freerouting"]}},
            "layer-policy": {"when": {"engine": ["freerouting"]}},
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


def _base_commands() -> list[dict[str, Any]]:
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
            "Check the board against its design rules, with this tool's own DRC (no KiCad "
            "runs), and report the violations and the rules not checked yet. Exit is 0 "
            "whatever it finds: read ok_to_fabricate and counts, not the exit code.",
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
            "Read-only observation of an existing enabled KiCad session through its IPC API. "
            "Requires an existing client; does not edit documents or change API preferences. "
            "Saved-file tasks do not require this session.",
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
            "Create an empty A4 root SCH/default project/empty netlist with an explicit "
            "mode:blank spec (no libraries or circuit objects), or a single-page "
            "schematic, or one root and exactly two distinct direct "
            "child pages declared by the optional direct-two-child/v1 circuit intent, "
            "and its source-stamped netlist. Resolve actual library symbols and physical "
            "pins during preparation. Hierarchy uses real sheet pins and child labels; "
            "nested/shared sheets and buses are unsupported. Review all candidate pages "
            "and output identities before confirming; the file set is not atomically saved.",
            [
                {
                    "name": "spec",
                    "type": "string",
                    "required": True,
                    "multiple": False,
                    "default": None,
                    "description": "Path to circuit JSON, or explicit {mode:blank,title:...} "
                    "with no parts/nets/hierarchy. Reference --command 'sch create' "
                    "exposes request $defs.circuit_spec; normal circuits remain nonempty.",
                },
                {
                    "name": "out",
                    "type": "string",
                    "required": False,
                    "multiple": False,
                    "default": None,
                    "default_from": "the specification's own directory",
                    "description": "Existing regular output directory; root/netlist names use "
                    "the spec stem and hierarchy child basenames come from the captured spec. "
                    "All declared outputs and consulted project targets must be absent.",
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
            "sch edit",
            "write",
            "Change an existing schematic from a JSON list of changes: set a part's fields "
            "and flags, rename or name a net, connect a free pin to a net, disconnect a pin, "
            "add a part from the libraries, remove a part. "
            "The edit is made in memory and read back through this tool's own connectivity "
            "before anything is written -- every net joining the pins asked, each change as "
            "asked -- and this tool's structural ERC is run before and after. "
            "Inspect label_layout for local rename text turns at retained anchors and "
            "remaining visual limits; native ERC and full-image review are separate checks.",
            [
                {
                    "name": "schematic",
                    "type": "string",
                    "required": True,
                    "multiple": False,
                    "default": None,
                    "description": "The root schematic of the design.",
                },
                {
                    "name": "changes",
                    "type": "string",
                    "required": True,
                    "multiple": False,
                    "default": None,
                    "description": (
                        "UTF-8/BOM ordered set/add/remove/connect/disconnect/rename JSON; "
                        "read selected schema "
                        "$defs.changes_file and examples."
                    ),
                },
                {
                    "name": "ignore-lock",
                    "type": "boolean",
                    "required": False,
                    "multiple": False,
                    "default": False,
                },
            ],
            "sch_edit",
            [
                "kicad-cli sch edit --schematic design.kicad_sch --changes changes.json --dry-run "
                "--compact",
                "kicad-cli sch edit --schematic design.kicad_sch --changes changes.json "
                "--confirm ct_xxx --compact",
            ],
            sch.edit,
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
            "they would report if they were on. The design is checked once with every rule "
            "on, by this tool's own ERC, so the answer is measured rather than inferred, and "
            "no KiCad runs for it.",
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
                    "description": "Absent .kicad_pcb output path in an existing directory.",
                },
                {
                    "name": "pitch",
                    "type": "number",
                    "required": False,
                    "multiple": False,
                    "default": 10.0,
                    "unit": "mm",
                    "exclusive_minimum": 0,
                    "description": "Initial grid centre spacing; not board width.",
                },
                {
                    "name": "margin",
                    "type": "number",
                    "required": False,
                    "multiple": False,
                    "default": 10.0,
                    "unit": "mm",
                    "minimum": 0,
                    "description": "Grid X/Y offset; outline uses footprint bounds plus 2 mm.",
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
            "so a connection needing an existing track to move aside will not be found. "
            "Grid verification compares non-excluded custom-DRC error finding identities and "
            "multiplicity, retaining source/input/rules/coverage; this is not official native DRC.",
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
                    "default": None,
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
                    "name": "use-planes",
                    "type": "boolean",
                    "required": False,
                    "multiple": False,
                    "default": False,
                },
                {
                    "name": "engine",
                    "type": "string",
                    "required": False,
                    "multiple": False,
                    "default": "grid",
                    "enum": ["grid", "freerouting"],
                },
                {
                    "name": "passes",
                    "type": "integer",
                    "required": False,
                    "multiple": False,
                    "default": 10,
                },
                {
                    "name": "layer-policy",
                    "type": "string",
                    "required": False,
                    "multiple": False,
                    "default": "balanced",
                    "enum": ["balanced", "plane-first"],
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
            "achieved rather than the width requested. Verification gates on custom-DRC error "
            "finding identities, with incomplete sources reported explicitly; not native DRC.",
            [
                _board_param(),
                {
                    "name": "classes",
                    "type": "string",
                    "required": False,
                    "multiple": False,
                    "default": None,
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
            "board update",
            "write",
            "Carry the schematic onto its board, as KiCad's Update PCB from Schematic does with "
            "the options its dialog opens with, in this process: references, values, fields, "
            "DNP and BOM marks, footprints the parts name, every pad's net, and the copper of a "
            "renamed net. New parts are placed beside the board; nothing is deleted. The board "
            "is read back against the schematic before anything is written.",
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
                {
                    "name": "ignore-lock",
                    "type": "boolean",
                    "required": False,
                    "multiple": False,
                    "default": False,
                },
            ],
            "board_update",
            [
                "kicad-cli board update --board board.kicad_pcb --dry-run --compact",
                "kicad-cli board update --board board.kicad_pcb --confirm ct_xxx --compact",
            ],
            board.update,
        ),
        _cmd(
            "board move",
            "write",
            "Move unique references in board coordinates (mm), then independently read "
            "saved identity, position (1 nm tolerance), rotation, layer and protected content. "
            "receipt distinguishes intended and observed values; "
            "this is not DRC or native KiCad loading.",
            [
                _board_param(),
                {
                    "name": "moves",
                    "type": "string",
                    "required": True,
                    "multiple": False,
                    "default": None,
                    "unit": "mm",
                    "description": "Unique REF:x,y entries separated by ';'; finite millimetres, "
                    "KiCad board x right/y down. Legacy binary-float truncation to nm, "
                    "verified within 1 nm.",
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
                "kicad-cli board move --board board.kicad_pcb "
                "--moves R1:101.25,100.5 --dry-run --compact",
                "kicad-cli board move --board board.kicad_pcb "
                "--moves R1:101.25,100.5 --confirm ct_xxx --compact",
            ],
            layout.move,
        ),
        _cmd(
            "board pour",
            "write",
            "Fill a copper layer with a zone for one net, usually ground, and fill it. "
            "DRC has no opinion about reference planes; board plane does.",
            [
                _board_param(),
                {
                    "name": "net",
                    "type": "string",
                    "required": True,
                    "multiple": False,
                    "default": None,
                },
                {
                    "name": "layer",
                    "type": "string",
                    "required": True,
                    "multiple": False,
                    "default": None,
                },
                {
                    "name": "margin",
                    "type": "number",
                    "required": False,
                    "multiple": False,
                    "default": 0.5,
                },
                {
                    "name": "clearance",
                    "type": "number",
                    "required": False,
                    "multiple": False,
                    "default": 0.3,
                },
                {
                    "name": "min-width",
                    "type": "number",
                    "required": False,
                    "multiple": False,
                    "default": 0.25,
                },
                {
                    "name": "connect",
                    "type": "string",
                    "required": False,
                    "multiple": False,
                    "default": "thermal",
                    "enum": ["thermal", "solid"],
                },
                {
                    "name": "ignore-lock",
                    "type": "boolean",
                    "required": False,
                    "multiple": False,
                    "default": False,
                },
            ],
            "board_pour",
            [
                "kicad-cli board pour --board b.kicad_pcb --net GND --layer B.Cu "
                "--dry-run --compact",
                "kicad-cli board pour --board b.kicad_pcb --net GND --layer B.Cu "
                "--confirm ct_xxx --compact",
            ],
            layout.pour,
        ),
        _cmd(
            "board netclass",
            "write",
            "Create or update a netclass in the project and assign nets to it. Sets the "
            "target only; run board rewidth or board widen to change the copper.",
            [
                _board_param(),
                {
                    "name": "name",
                    "type": "string",
                    "required": True,
                    "multiple": False,
                    "default": None,
                },
                {
                    "name": "nets",
                    "type": "string",
                    "required": True,
                    "multiple": True,
                    "separator": ",",
                    "default": None,
                },
                {
                    "name": "width",
                    "type": "number",
                    "required": True,
                    "multiple": False,
                    "default": None,
                },
                {
                    "name": "clearance",
                    "type": "number",
                    "required": False,
                    "multiple": False,
                    "default": None,
                },
                {
                    "name": "via-diameter",
                    "type": "number",
                    "required": False,
                    "multiple": False,
                    "default": None,
                },
                {
                    "name": "via-drill",
                    "type": "number",
                    "required": False,
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
            "board_netclass",
            [
                "kicad-cli board netclass --board b.kicad_pcb --name Power --width 0.5 "
                "--nets +3V3,VIN --dry-run --compact",
                "kicad-cli board netclass --board b.kicad_pcb --name Power --width 0.5 "
                "--nets +3V3,VIN --confirm ct_xxx --compact",
            ],
            layout.netclass,
        ),
        _cmd(
            "board silkscreen",
            "write",
            "Translate reference text using native KiCad bounds. The public targeted "
            "mode checks supported same-side mask openings, graphics and fixed text; "
            "unproved mask membership, tenting or geometry makes the check incomplete. "
            "Copper projection is separate reading advice. Omitted targets retain the "
            "legacy all-visible-reference placement pass; discover the selected public "
            "reference for its exact target schema and preservation limits.",
            [
                _board_param(),
                {
                    "name": "clearance",
                    "type": "number",
                    "required": False,
                    "multiple": False,
                    "default": 0.15,
                },
                {
                    "name": "ignore-lock",
                    "type": "boolean",
                    "required": False,
                    "multiple": False,
                    "default": False,
                },
            ],
            "board_silkscreen",
            [
                "kicad-cli board silkscreen --board b.kicad_pcb --dry-run --compact",
                "kicad-cli board silkscreen --board b.kicad_pcb --confirm ct_xxx --compact",
            ],
            layout.silkscreen,
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


COMMAND_MIGRATIONS = {
    "board audit": "board analyze ampacity",
    "board plane": "board analyze reference-plane",
    "board parity": "project parity",
    "board drc": "board check",
    "board live": "session inspect",
    "sch link": "project links inspect",
    "sch relink": "project links repair",
    "sch audit": "sch check",
    "board update": "project sync",
    "board netclass": "project netclass set",
    "board move": "board footprint move",
    "board place": "board footprint place",
    "board stitch": "board zone stitch",
    "board widen": "board track widen",
    "board silkscreen": "board silkscreen place",
    "board rewidth": "board route rewidth",
    "board pour": "board zone create + board zone refill",
    "sch sync-preview": "project sync --dry-run",
    "board route": "board route repair/rebuild/rewidth",
    "fab pdf": "board plot --file-format pdf",
    "fab svg": "board plot --file-format svg",
    "fab dxf": "board plot --file-format dxf",
}

COMPATIBILITY = {
    "command_tree_version": 2,
    "driver_spec_design_baseline": {
        "version": "1.0.2",
        "commit": "9607a35fe0ea33fe822e1f2ad2704eae260d4018",
    },
    "runtime_contract": {
        "envelope": "1.0",
        "confirmation": "driver-2.0 for runtime_migration.migrated; legacy for remaining writers",
        "scope_hmac_durable_operation": "listed-migrated-paths",
        "driver_2_0_conformance": "not_claimed",
    },
    "legacy_aliases": False,
    "removed_commands": COMMAND_MIGRATIONS,
}
SCHEMAS["reference"]["fields"].extend(["compatibility", "runtime_migration"])
SCHEMAS["context"]["fields"].append("runtime_migration")
for _zone_schema in ("zone_create", "zone_refill"):
    SCHEMAS[_zone_schema] = {
        "shape": "object",
        "fields": ["board", "status", "zones", "verification", "input_sha256", "output_sha256"],
        "field_values": {"status": ["CREATED", "REFILLED", "NOOP"]},
        "untrusted_fields": ["zones"],
    }


def build() -> list[dict[str, Any]]:
    """Expose canonical tasks only. Base adapter declarations are not aliases.

    Internal result schema identifiers stay stable; command paths and accepted
    inputs are breaking version-2 changes. The old dialog prediction is no
    longer a public task: sync dry-run and execution use board_update.run.
    """
    from copy import deepcopy  # noqa: PLC0415

    from .commands import fab, layout  # noqa: PLC0415

    result = []
    for original in _base_commands():
        old = original["path"]
        if old in ("sch sync-preview", "board rewidth", "fab svg", "fab dxf"):
            continue
        command = deepcopy(original)
        if old == "board route":
            variants = (
                (
                    "repair",
                    layout.route_repair,
                    {"board", "neck", "ripup", "engine", "ignore-lock"},
                ),
                (
                    "rebuild",
                    layout.route_rebuild,
                    {
                        "board",
                        "neck",
                        "use-planes",
                        "engine",
                        "passes",
                        "layer-policy",
                        "ignore-lock",
                    },
                ),
                (
                    "rewidth",
                    layout.route_rewidth,
                    {"board", "nets", "classes", "neck", "no-verify", "no-restore", "ignore-lock"},
                ),
            )
            for task, handler, accepted in variants:
                variant = deepcopy(command)
                variant.update(path="board route " + task, handler=handler)
                variant["params"] = [p for p in variant["params"] if p["name"] in accepted]
                for param in variant["params"]:
                    param.get("when", {}).pop("mode", None)
                    if not param.get("when"):
                        param.pop("when", None)
                    if task == "repair" and param["name"] == "engine":
                        param["enum"] = ["grid"]
                if task == "rewidth":
                    variant["output_schema"] = "board_rewidth"
                    variant["required_any"] = [["nets", "classes"]]
                variant["description"] = {
                    "repair": (
                        "Keep existing copper and attempt missing connections with the grid engine."
                    ),
                    "rebuild": (
                        "Replace tracks/vias and rebuild routes; grid or configured Freerouting. "
                        "Existing routes are not preserved."
                    ),
                    "rewidth": (
                        "Re-route explicitly selected nets/classes at their configured width; "
                        "report achieved width and bounded custom finding verification."
                    ),
                }[task]
                selector = " --classes Default" if task == "rewidth" else ""
                variant["examples"] = [
                    f"kicad-cli {variant['path']} --board board.kicad_pcb{selector} "
                    "--dry-run --compact"
                ]
                result.append(variant)
            continue
        if old == "fab pdf":
            command.update(path="board plot", handler=fab.board_plot)
            command["params"].insert(
                1,
                {
                    "name": "file-format",
                    "type": "string",
                    "required": True,
                    "multiple": False,
                    "default": None,
                    "enum": ["pdf", "svg", "dxf"],
                },
            )
            command["description"] = (
                "Plot selected PCB layers as review drawings, using project settings."
            )
            next(p for p in command["params"] if p["name"] == "out")["default_from"] = (
                "<board directory>/plots"
            )
            command["examples"] = [
                "kicad-cli board plot --file-format pdf --board board.kicad_pcb --dry-run"
            ]
        elif old == "board pour":
            command.update(
                path="board zone create", handler=layout.zone_create, output_schema="zone_create"
            )
            command["description"] = (
                "Create one unfilled rectangular copper zone. Existing fills are unchanged."
            )
            for param in command["params"]:
                if param["name"] in ("margin", "clearance"):
                    param.update(unit="mm", minimum=0)
                elif param["name"] == "min-width":
                    param.update(unit="mm", exclusive_minimum=0)
            command["examples"] = [
                "kicad-cli board zone create --board b.kicad_pcb --net GND --layer B.Cu --dry-run"
            ]
        else:
            command["path"] = COMMAND_MIGRATIONS.get(old, old)
            command["examples"] = [s.replace(old, command["path"]) for s in command["examples"]]
        if old == "sch audit":
            command["params"] = [p for p in command["params"] if p["name"] == "schematic"]
            command["params"][0].update(required=True)
            command["params"][0].pop("default_from", None)
            command["required_any"] = []
            command["examples"] = ["kicad-cli sch check --schematic board.kicad_sch --compact"]
        if old == "board update":
            schematic_param = next(p for p in command["params"] if p["name"] == "schematic")
            schematic_param.update(required=True)
            schematic_param.pop("default_from", None)
            command["params"].append(
                {
                    "name": "remove-footprint",
                    "type": "string",
                    "required": False,
                    "multiple": False,
                    "description": "Optional exact single-footprint identity/pad selection JSON; "
                    "default retains unmatched. See reference request schema.",
                }
            )
            command["examples"] = [
                "kicad-cli project sync --board board.kicad_pcb "
                "--schematic board.kicad_sch --dry-run --compact"
            ]
            command["description"] = (
                "Plan/apply schematic changes to PCB with the same planner; "
                "keep geometry and report missing footprints/partial updates."
            )
        if old == "board parity":
            command["params"].append(
                {
                    "name": "schematic",
                    "type": "string",
                    "required": False,
                    "multiple": False,
                    "default": None,
                    "default_from": "<board>.kicad_sch",
                }
            )
        if old == "board netclass":
            command["description"] = (
                "Set the board's sibling .kicad_pro netclass and assignments; copper is unchanged."
            )
        if old == "board audit":
            command["description"] = (
                "Analyze track width/ampacity with explicit copper thickness "
                "and temperature-rise assumptions."
            )
        result.append(command)
    refill = _cmd(
        "board zone refill",
        "write",
        "Refill explicitly selected existing copper zones; preserve zone definitions.",
        [
            _board_param(),
            {
                "name": "zones",
                "type": "string",
                "required": False,
                "multiple": False,
                "default": None,
                "separator": ",",
                "conflicts_with": ["all"],
            },
            {
                "name": "all",
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
        "zone_refill",
        ["kicad-cli board zone refill --board b.kicad_pcb --all --dry-run"],
        layout.zone_refill,
    )
    refill["required_any"] = [["zones", "all"]]
    result.append(refill)
    from . import driver_runtime  # noqa: PLC0415

    pilot = driver_runtime.declarations()["reference"]
    SCHEMAS.update({k: v for k, v in pilot["schemas"].items() if k not in SCHEMAS})
    by_path = {c["path"]: c for c in result}
    for declaration in pilot["commands"]:
        if declaration["path"] not in driver_runtime.MIGRATED:
            continue
        command = deepcopy(declaration)
        command["handler"] = (
            layout.move if command["path"] == "board footprint move" else _driver_only
        )
        by_path[command["path"]] = command
    result = list(by_path.values())
    for command in result:
        command["runtime_protocol"] = (
            "driver-2.0" if command["path"] in driver_runtime.MIGRATED else "legacy-1.0"
        )
    return result


def _driver_only(_options):
    raise RuntimeError("driver command must dispatch through main's driver runtime")


def lookup(
    commands: list[dict[str, Any]], argv: list[str]
) -> tuple[dict[str, Any] | None, list[str]]:
    """Longest-prefix match so ``board audit`` wins over a hypothetical ``board``."""
    for width in range(max(len(c["path"].split()) for c in commands), 0, -1):
        if len(argv) >= width:
            path = " ".join(argv[:width])
            for c in commands:
                if c["path"] == path:
                    return c, argv[width:]
    return None, argv
