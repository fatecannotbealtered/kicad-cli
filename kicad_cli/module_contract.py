"""Single-source public module commands, schemas and capability scopes."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from .internal_release import SUPPORTED, profile


def obj(properties, required=None):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


TEXT = {"type": "string", "minLength": 1, "maxLength": 2048}
NUMBER = {"type": "number", "minimum": -10000, "maximum": 10000}
POSITIVE = {"type": "number", "exclusiveMinimum": 0, "maximum": 1000}
HASH = {"type": "string", "pattern": "^(absent|[0-9a-f]{64})$"}
XY = {"type": "array", "items": NUMBER, "minItems": 2, "maxItems": 2}
NXY = {
    "type": "array",
    "items": {"type": "integer", "minimum": -10000000000, "maximum": 10000000000},
    "minItems": 2,
    "maxItems": 2,
}


def array(item, minimum=1, maximum=256):
    return {"type": "array", "items": item, "minItems": minimum, "maxItems": maximum}


SOURCE = obj(
    {
        "url": {**TEXT, "pattern": "^https://"},
        "revision": TEXT,
        "locator": TEXT,
        "file": {**TEXT, "description": "Evidence file; relative to the request JSON directory"},
        "sha256": {**HASH, "pattern": "^[0-9a-f]{64}$"},
    }
)
LIBRARY_SPEC = obj(
    {
        "library": TEXT,
        "revision": TEXT,
        "baseline": {"type": "object", "additionalProperties": HASH},
        "sources": array(SOURCE, maximum=16),
        "mapping": {"type": "object", "additionalProperties": TEXT, "minProperties": 1},
        "symbol": obj(
            {
                "name": TEXT,
                "reference": TEXT,
                "body_mm": XY,
                "pins": array(
                    obj(
                        {
                            "number": TEXT,
                            "name": TEXT,
                            "type": TEXT,
                            "at_mm": XY,
                            "angle_deg": {"enum": [0, 90, 180, 270]},
                            "length_mm": POSITIVE,
                        }
                    )
                ),
            }
        ),
        "footprint": obj(
            {
                "name": TEXT,
                "body_mm": {
                    **XY,
                    "description": "Nominal centred mechanical body width/height in mm; "
                    "Fab pen/text are not body dimensions; integer-nm input grid",
                },
                "courtyard_mm": {
                    **POSITIVE,
                    "description": "Minimum free space from body/pad union to INNER edge "
                    "of 0.05mm courtyard stroke; integer-nm input grid, "
                    "bounds rounded outward to nm; sub-nm values rejected",
                },
                "pads": array(
                    obj(
                        {
                            "number": TEXT,
                            "at_mm": {**XY, "description": "Pad centre on integer-nm grid"},
                            "size_mm": {
                                "type": "array",
                                "items": POSITIVE,
                                "minItems": 2,
                                "maxItems": 2,
                                "description": "Pad dimensions on integer-nm grid",
                            },
                        }
                    )
                ),
            }
        ),
    }
)
PATCH_SPEC = obj(
    {
        "baseline": obj({k: HASH for k in (".kicad_pcb", ".kicad_pro", ".kicad_dru")}),
        "net": TEXT,
        "remove_ids": array(TEXT, minimum=0, maximum=4096),
        "allow_locked_ids": array(TEXT, minimum=0, maximum=4096),
        "allowed_layers": array(TEXT, maximum=32),
        "bounds_nm": {
            "type": "array",
            "items": {"type": "integer"},
            "minItems": 4,
            "maxItems": 4,
            "description": "Inclusive copper-envelope rectangle [minX,minY,maxX,maxY] in nm; "
            "includes halfwidth rounded outward to whole nm. Boundary contact allowed. "
            "Arc/via removal unsupported.",
        },
        "endpoints": array(
            obj(
                {
                    "object_uuid": TEXT,
                    "terminal": {"enum": ["start", "end", "center"]},
                    "at_nm": NXY,
                    "layer": TEXT,
                }
            ),
            minimum=2,
            maximum=2,
        ),
        "segments": array(
            obj(
                {
                    "start_nm": NXY,
                    "end_nm": NXY,
                    "layer": TEXT,
                    "width_nm": {"type": "integer", "minimum": 1, "maximum": 100000000},
                }
            ),
            minimum=0,
            maximum=4096,
        ),
        "minimum_improvement_mm": {"type": "number", "minimum": 0, "maximum": 100000},
    }
)

WRITE_COMMANDS = {"library build", "board route patch"}
READ_COMMANDS = {"modules", "project context", "library inspect", "board routing inspect"}

MODULES = {
    "kicad-cli": {
        "commands": [
            "modules",
            "context",
            "doctor",
            "reference",
            "changelog",
            "project context",
            "operation show",
            "operation list",
            "operation resolve",
        ],
        "scope": "Environment, actual capability/binding discovery, routing and common workflow",
    },
    "kicad-library": {
        "commands": ["library build", "library inspect"],
        "scope": "Project-local single-unit rectangular symbols, explicit pins, "
        "rectangular front SMD pads, bijective same-number mapping, source/version records",
        "unsupported": [
            "multi-unit authoring",
            "THT/custom pads",
            "automatic datasheet interpretation",
            "global library editing",
            "automatic placed-instance refresh",
        ],
    },
    "kicad-sch": {
        "commands": [
            "sch create",
            "sch edit",
            "sch symbols",
            "sch objects",
            "sch connections",
            "sch export",
        ],
        "scope": "Preserved blank/exact construction, scalar hierarchy/reuse, fields, "
        "connection-preserving transforms; single-root native netlist/SVG/PDF artifacts",
        "unsupported": [
            "bus design",
            "automatic wiring",
            "general library cache refresh",
            "hierarchical SCH export",
        ],
    },
    "kicad-pcb": {
        "commands": [
            "board from-netlist",
            "project sync",
            "project links repair",
            "board footprint move",
            "board footprint place",
            "board route patch",
            "board route repair",
            "board route rebuild",
            "board route rewidth",
            "board track widen",
            "board zone create",
            "board zone refill",
            "board zone stitch",
            "project netclass set",
            "board silkscreen place",
        ],
        "scope": "New explicit identity/baseline/region/layer-scoped segment patch "
        "with native candidate DRC; historical commands retain their own bounded qualification",
        "unsupported": [
            "arc/via removal in explicit route patch",
            "new vias in patch",
            "automatic dense escape",
            "push/shove",
            "controlled impedance acceptance",
            "arbitrary stackup editing",
            "route search in patch",
        ],
    },
    "kicad-review": {
        "commands": [
            "board routing inspect",
            "board check",
            "sch check",
            "project parity",
            "project links inspect",
            "board analyze ampacity",
            "board analyze reference-plane",
            "session inspect",
        ],
        "scope": "Read-only diagnostics; duplicate straight segments, separated length metrics, "
        "open-end candidates; native report comparison in routing preview",
        "unsupported": [
            "hardware signoff",
            "SI validation",
            "automatic repair",
            "schematic-review tool equivalence",
        ],
    },
    "kicad-fabrication": {
        "commands": ["fab gerber", "fab drill", "board plot"],
        "scope": "Existing scoped manufacturing/plot output; artifact hashes "
        "and original bounded coverage retained",
        "unsupported": [
            "manufacturing release approval",
            "assembly process qualification",
            "board power-on validation",
        ],
    },
}

MODULE_REGRESSIONS = {
    "kicad-cli": ["test_module_operations.py", "test_skill.py", "test_build_sdk_boundary.py"],
    "kicad-library": [
        "test_module_operations.py",
        "test_module_geometry.py",
        "native wired functional library placement/reopen",
    ],
    "kicad-sch": [
        "test_sheet_pin_text.py",
        "test_symbol_cache_fidelity.py",
        "test_driver_engineering_runtime.py",
        "test_sch_hierarchy_build.py",
        "test_sch_export.py",
    ],
    "kicad-pcb": [
        "test_module_operations.py",
        "test_driver_power_runtime.py",
        "test_native_route.py",
        "test_move_guard.py",
        "test_move_preflight_inputs.py",
        "test_module_geometry.py",
        "native phone scoped routing/reopen",
    ],
    "kicad-review": [
        "test_module_operations.py",
        "test_driver_read_runtime.py",
        "test_copper_classification.py",
    ],
    "kicad-fabrication": ["test_driver_exports.py"],
}
for _name, _module in MODULES.items():
    _module["internal_1_0_supported_commands"] = [c for c in _module["commands"] if c in SUPPORTED]
    _module["state"] = "implemented_bounded_experimental"
    _module["regression_group"] = MODULE_REGRESSIONS[_name]
    _module["qualification"] = (
        "Requires evidence bound to this exact artifact; "
        "historical qualification does not transfer automatically"
    )


def authorization_scope_schema():
    return json.loads(
        (Path(__file__).parent / "driver_spec/schemas/scope.schema.json").read_bytes()
    )


def extend(contract, protocol):
    ref = contract["reference"]
    from_netlist = next(c for c in ref["commands"] if c["path"] == "board from-netlist")
    from_netlist["params"].append(
        {
            "name": "initialize-empty-project",
            "type": "boolean",
            "required": False,
            "multiple": False,
            "default": False,
            "description": "Explicitly initialize native board/project defaults only if "
            "matching PRO is absent or exactly {}; never merges/replaces configured settings. "
            "PCB must still be absent.",
        }
    )
    ref["protocol_revision"] = protocol
    # The fixed SDK owns whole-product release readiness. A bounded internal
    # delivery never upgrades missing FCC evidence to product-level beta.
    ref.setdefault("extensions", {})["internal_delivery"] = profile()
    for command in ref["commands"]:
        if command["path"] == "operation list":
            for param in command["params"]:
                if param["name"] == "limit":
                    param.update(description="Page size, 1 through 200; enforced before dispatch")
    ref["authorization"] = {
        "scope_schema": "urn:tool-driver-cli-spec:scope:2.0",
        "example_command": "modules",
        "example_path": "observation.operation_scope.example",
        "file": "profile_dir/scope.json or explicit KICAD_CLI_SCOPE_FILE",
        "policy": "Examples do not grant permission or write a scope; "
        "use only the user's already-authorized task range",
    }
    template_read = next(c for c in ref["commands"] if c["path"] == "board check")
    template_write = next(c for c in ref["commands"] if c["path"] == "sch edit")
    definitions = [
        (
            "modules",
            [],
            None,
            "Discover engineering modules, supported limits and exact source/executable binding",
        ),
        (
            "project context",
            [("board", True)],
            None,
            "Require matching project and expose explicit custom-rule presence "
            "and captured library context",
        ),
        (
            "library inspect",
            [("project", True), ("library", True)],
            None,
            "Read project-local assets, mapping, provenance and output baselines",
        ),
        (
            "board routing inspect",
            [("board", True), ("net", False)],
            None,
            "Read exact straight-segment duplicates and bounded centreline network diagnostics",
        ),
        (
            "library build",
            [("project", True), ("spec", True)],
            LIBRARY_SPEC,
            "Create/update source-bound project-local symbols/footprints "
            "and register project tables",
        ),
        (
            "board route patch",
            [("board", True), ("spec", True)],
            PATCH_SPEC,
            "Apply an explicit target-net segment patch with native DRC, "
            "precise baseline and protected non-target data",
        ),
    ]
    for path, params, spec, description in definitions:
        command = copy.deepcopy(template_write if spec else template_read)
        command.update(
            path=path,
            description=description,
            params=[
                {"name": name, "type": "string", "required": required, "multiple": False}
                for name, required in params
            ],
            examples=[
                "kicad-cli "
                + path
                + " "
                + " ".join("--" + k + " <" + k + ">" for k, required in params if required)
            ],
        )
        command["driver"]["capability_id"] = path.replace(" ", "-")
        command["driver"]["limits"] = [
            description,
            "Only the capability scope listed by modules applies; "
            "version equality does not establish compatibility.",
            "Existing per-file transactions; document sets are not atomically replaced.",
        ]
        command["driver"]["environments"][0]["limitations"] = command["driver"]["limits"]
        if not spec:
            command["output_schema"] = "module_observation"
        else:
            example = command["examples"][0]
            command["examples"] = [example + " --dry-run", example + " --confirm <confirm_token>"]
            uri = "urn:kicad-cli:" + path.replace(" ", "-") + ":1"
            request = obj({k: TEXT for k, _ in params})
            request.update(
                {
                    "$id": uri,
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "$defs": {"spec_file": spec},
                }
            )
            ref["json_schemas"][uri] = request
            command["driver"]["request_schema"] = uri
        ref["commands"].append(command)
    export = copy.deepcopy(template_write)
    export.update(
        path="sch export",
        description="Export current single-page SCH to native netlist, SVG or PDF "
        "with captured sources and artifact-scoped publication; source CAD unchanged.",
        params=[
            {
                "name": name,
                "type": "string",
                "required": True,
                "multiple": False,
                **({"enum": ["netlist", "svg", "pdf"]} if name == "file-format" else {}),
            }
            for name in ("schematic", "file-format", "out")
        ],
        examples=[
            "kicad-cli sch export --schematic circuit.kicad_sch --file-format netlist "
            "--out owned-export --dry-run",
            "kicad-cli sch export --schematic circuit.kicad_sch --file-format netlist "
            "--out owned-export --confirm <confirm_token>",
        ],
    )
    export["driver"].update(
        capability_id="sch-export",
        effects=["artifact"],
        limits=[
            "Single source/root page only; hierarchy export unsupported.",
            "Owned output directory must exist; output basename follows root; "
            "existing output refuses.",
            "Native format structure and saved bytes checked; "
            "ERC, visual readability and manufacturing acceptance separate.",
        ],
        request_schema="urn:kicad-cli:sch-export:1",
    )
    export["driver"]["environments"][0]["limitations"] = export["driver"]["limits"]
    ref["json_schemas"]["urn:kicad-cli:sch-export:1"] = {
        "$id": "urn:kicad-cli:sch-export:1",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **obj({"schematic": TEXT, "file-format": {"enum": ["netlist", "svg", "pdf"]}, "out": TEXT}),
    }
    ref["commands"].append(export)
    ref["schemas"]["module_observation"] = {
        "shape": "object",
        "fields": ["module", "observation"],
        "untrusted_fields": ["observation"],
        "json_schema": {"type": "object"},
    }
    contract["build_id"] += "+engineering-modules-v1"
    ref["runtime_migration"].update(
        protocol_revision=protocol,
        migrated=sorted(c["path"] for c in ref["commands"]),
        total=len(ref["commands"]),
    )
    return contract
