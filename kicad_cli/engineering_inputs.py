"""Captured engineering inputs and output existence, including consulted libraries."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from . import kicad_env, read_inputs
from .fileformat import lib_tables
from .move_transaction import regular
from .sync_inputs import MAX_FILES, MAX_TOTAL_BYTES, Drift, InputError, _absolute, _read


def _creation_spec(raw):
    from .schematic import SHAPE, SPEC_SCHEMA, spec_problems

    try:
        spec = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise InputError(
            "Circuit specification is not valid JSON",
            parameter="--spec",
            schema_ref=SPEC_SCHEMA,
        ) from exc
    problems = spec_problems(spec)
    if problems:
        raise InputError(
            "Circuit specification structure is invalid",
            parameter="--spec",
            schema_ref=SPEC_SCHEMA,
            problems=problems[:40],
            problem_count=len(problems),
            shape=SHAPE,
            next_action=(
                "Read reference --command 'sch create'; its request schema "
                "$defs.circuit_spec describes the file contents. "
                "Layout coordinates/rotation/manual groups are unsupported."
            ),
        )
    return spec


def _parent_regular(directory):
    """Reject an output directory or ancestor redirected through a reparse point."""
    for parent in (directory, *directory.parents):
        info = parent.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
        ):
            raise InputError("Output directory ancestry must be regular", path=str(parent))


class Inputs:
    def __init__(self):
        self.files, self.manifest = {}, []
        self.context = self.environment()
        self.directories = []
        self.hierarchy = False

    @staticmethod
    def environment():
        from .engineering_readback import sha

        return {
            key: sha(value.encode())
            for key, value in sorted(os.environ.items())
            if key.startswith("KICAD") and key not in {"KICAD_CLI_TRACE", "KICAD_CLI_ERROR_TRACE"}
        }

    def add(self, path, required=False, *, capture_identity=False):
        path = _absolute(path)
        if path not in self.files:
            row, raw = _read(path, capture_identity=capture_identity)
            if raw is not None:
                regular(path)
            self.files[path] = raw
            self.manifest.append(row)
            if (
                len(self.files) > MAX_FILES
                or sum(len(v or b"") for v in self.files.values()) > MAX_TOTAL_BYTES
            ):
                raise InputError("Engineering input bounds exceeded")
        elif capture_identity:
            row, raw = _read(path, capture_identity=True)
            old_index = next(i for i, old in enumerate(self.manifest) if old["path"] == str(path))
            old = self.manifest[old_index]
            if (
                raw != self.files[path]
                or old["resolved"] != row["resolved"]
                or ("identity" in old and old["identity"] != row["identity"])
            ):
                raise Drift(
                    "Engineering dependency changed during capture", changed_files=[str(path)]
                )
            self.manifest[old_index] = row
        if required and self.files[path] is None:
            raise FileNotFoundError(path)
        return path

    def documents(self, **kwargs):
        captured = read_inputs.capture(**kwargs)
        for row in captured.manifest:
            self.add(row["path"])
        return captured

    def validate(self, replacements=None):
        from .sch_hierarchy_inputs import validate_directories

        validate_directories(self.directories)
        replacements = replacements or {}
        if self.context != self.environment():
            raise Drift("Engineering dependency environment changed")
        for old in self.manifest:
            found, _ = _read(old["path"], capture_identity="identity" in old)
            expected = dict(old)
            if old["path"] in replacements:
                # Formal output identity changes are owned by DocumentTransaction.
                expected.pop("identity", None)
                found.pop("identity", None)
                expected.update(
                    present=replacements[old["path"]] != "absent",
                    sha256=None
                    if replacements[old["path"]] == "absent"
                    else replacements[old["path"]],
                )
            if expected != found:
                raise Drift("Engineering input/output drift", changed_files=[old["path"]])


def capture(command, params):
    if command == "sch export":
        from .sch_export import capture as export_capture

        return export_capture(params)
    from .module_contract import WRITE_COMMANDS

    if command in WRITE_COMMANDS:
        from .module_project import capture as module_capture

        return module_capture(command, params)
    result = Inputs()
    board = params.get("board")
    schematic = params.get("schematic")
    design = None
    if board:
        captured = result.documents(
            board=board,
            schematic=Path(board).with_suffix(".kicad_sch")
            if command == "project links repair"
            else None,
        )
        design = captured.design
        primary = _absolute(board)
    elif schematic:
        captured = result.documents(schematic=schematic)
        design = captured.design
        primary = _absolute(schematic)
    elif command == "sch create":
        spec = result.add(params["spec"], required=True)
        primary = _absolute(params.get("out") or spec.parent) / (spec.stem + ".kicad_sch")
        result.add(primary)
        result.add(primary.with_suffix(".net"))
        result.add(primary.with_suffix(".kicad_pro"))
    else:
        result.add(params["netlist"], required=True)
        primary = _absolute(params["out"])
        if primary.suffix != ".kicad_pcb":
            raise InputError("PCB output must have .kicad_pcb suffix")
        result.add(primary)
        result.add(primary.with_suffix(".kicad_pro"))
    if command in {"sch create", "board from-netlist"} and result.files[primary] is not None:
        raise FileExistsError(
            "Creation requires an absent primary; use edit or sync for existing designs"
        )
    if command == "board from-netlist" and params.get("initialize-empty-project"):
        project_path = primary.with_suffix(".kicad_pro")
        raw = result.files[project_path]
        if raw is not None and json.loads(raw) != {}:
            raise InputError(
                "Explicit project initialization only accepts an absent or exactly empty "
                "project; configured project is preserved"
            )
    if not primary.parent.is_dir() or primary.parent.is_symlink():
        raise InputError(
            "An existing regular output directory is required",
            parameter="--out" if params.get("out") else "--spec",
            path=str(primary.parent),
            next_action="Create the owned output directory, then request a new preview.",
        )
    if command == "sch create":
        from .sch_hierarchy_contract import hierarchy_plan, hierarchy_problems

        _parent_regular(primary.parent)
        spec = _creation_spec(result.files[_absolute(params["spec"])])
        invalid = hierarchy_problems(spec, primary.stem)
        if invalid:
            raise InputError("Hierarchy output names or identities are invalid", problems=invalid)
        planned = hierarchy_plan(spec, primary.stem)
        if planned:
            for name in planned["output_names"]:
                result.add(primary.parent / name)
        creation_outputs = [
            primary,
            primary.with_suffix(".net"),
            primary.with_suffix(".kicad_pro"),
            *([primary.parent / child["file"] for child in planned["children"]] if planned else []),
        ]
        if any(result.files[path] is not None for path in creation_outputs):
            raise FileExistsError(
                "Creation requires every root, child, netlist and consulted project target absent"
            )
    if command == "sch edit":
        result.add(params["changes"], required=True)
        from . import sch_edit_contract

        try:
            edit_document = json.loads(result.files[_absolute(params["changes"])])
        except (ValueError, UnicodeDecodeError) as exc:
            raise InputError(
                "Schematic changes file is not valid JSON",
                **sch_edit_contract.details([{"path": "/", "problem": str(exc)[:200]}]),
            ) from exc
        invalid = sch_edit_contract.problems(edit_document)
        if invalid:
            raise InputError(
                "Schematic changes file structure is invalid", **sch_edit_contract.details(invalid)
            )
        changes = (
            edit_document.get("changes", []) if isinstance(edit_document, dict) else edit_document
        )
        if any(change.get("op") == "transform" for change in changes):
            from ._driver_sdk.models import DriverError
            from .sch_objects import Catalog, CatalogError

            catalog = Catalog(design)
            seen = set()
            prefix = ["changes"] if isinstance(edit_document, dict) else []
            for index, change in enumerate(changes):
                for selection_index, selector in enumerate(change["objects"]):
                    location = [*prefix, index, "objects", selection_index]
                    try:
                        catalog.resolve(selector)
                    except CatalogError as exc:
                        details = {
                            **sch_edit_contract.details(
                                [
                                    {
                                        "index": index,
                                        "path": sch_edit_contract.pointer(location),
                                        "problem": str(exc),
                                        "op": "transform",
                                    }
                                ]
                            ),
                            **exc.details,
                        }
                        if exc.code == "E_CONFLICT":
                            details.setdefault("conflict", "state_drift")
                        raise DriverError(
                            exc.code, str(exc), details=details, dispatch_state="not_started"
                        ) from exc
                    identity = selector["object_id"]
                    if identity in seen:
                        raise InputError(
                            "Transform selections must be disjoint across the batch",
                            **sch_edit_contract.details(
                                [
                                    {
                                        "index": index,
                                        "path": sch_edit_contract.pointer(location),
                                        "problem": "This source object was already selected "
                                        "in this batch",
                                        "op": "transform",
                                    }
                                ]
                            ),
                        )
                    seen.add(identity)
        if any(change.get("op") == "circuit" for change in changes):
            from ._driver_sdk.models import DriverError
            from .sch_objects import Catalog, CatalogError

            catalog = Catalog(design)
            entry = changes[0]
            expected_snapshot = {"root": catalog.root, "snapshot": catalog.snapshot}
            if entry["snapshot"] != expected_snapshot:
                raise DriverError(
                    "E_CONFLICT",
                    "Circuit snapshot belongs to another root or revision",
                    details={"conflict": "state_drift", "reason_code": "stale_circuit_snapshot"},
                    dispatch_state="not_started",
                )
            from .sch_hierarchy_inputs import capture as capture_hierarchy
            from .sch_hierarchy_inputs import hierarchy_actions, reference_catalogs

            catalogs = {catalog.root: catalog}
            if hierarchy_actions(entry["actions"]):
                capture_hierarchy(result, primary, entry["actions"])
                catalogs.update(reference_catalogs(result, primary, entry["actions"]))

            def selections(value):
                if isinstance(value, dict):
                    if {"root", "snapshot", "object_id"}.issubset(value):
                        yield value
                    else:
                        for child in value.values():
                            yield from selections(child)
                elif isinstance(value, list):
                    for child in value:
                        yield from selections(child)

            for selector in selections(entry):
                try:
                    catalogs.get(selector["root"], catalog).resolve(selector)
                except CatalogError as exc:
                    raise DriverError(
                        exc.code,
                        str(exc),
                        details={
                            **exc.details,
                            "parameter": "--changes",
                            **({"problem": "invalid_value"} if exc.code == "E_VALIDATION" else {}),
                        },
                        dispatch_state="not_started",
                    ) from exc
    if command == "board silkscreen place" and params.get("targets"):
        from .engineering_silk import targets_document

        selection = result.add(params["targets"], required=True)
        # Validate captured bytes, not a second unbound read of the caller path.
        targets_document(result.files[selection])

    if command == "sch create" and spec.get("mode") == "blank":
        # An empty root has no symbols/resources. Do not turn installed library
        # tables or a KiCad discovery path into an initialization prerequisite.
        result.validate()
        return result, primary

    # Bind table variables and the actual selected library files, not a library label.
    config = lib_tables.default_config_dir()
    root = kicad_env.find_kicad_root()
    for directory in filter(None, (primary.parent, config)):
        for name in ("sym-lib-table", "fp-lib-table", "kicad_common.json"):
            result.add(Path(directory) / name)
    symbols = set()
    if design:
        symbols.update(
            part.lib_id for instance in design.instances for part in instance.schematic.symbols
        )
    if command == "sch create":
        symbols.update(p["symbol"] for p in spec["parts"])
        symbols.update({"power:GND", "power:PWR_FLAG"})
        symbols.update(
            "power:" + n["name"] for n in spec.get("nets", []) if n["name"].startswith("+")
        )
    if command == "sch edit":
        raw = edit_document
        changes = raw.get("changes", []) if isinstance(raw, dict) else raw
        symbols.update(c["symbol"] for c in changes if c.get("op") == "add" and "symbol" in c)
    circuit_actions = [
        action
        for change in (changes if command == "sch edit" else [])
        if change.get("op") == "circuit"
        for action in change["actions"]
    ]
    if circuit_actions:
        from .sch_symbols import capture_resources

        places = [(a["symbol"], a["page"]) for a in circuit_actions if a["op"] == "place_symbol"]
        if places:
            try:
                if result.hierarchy:
                    from .sch_hierarchy_inputs import capture_symbol_resources

                    resources = capture_symbol_resources(
                        primary, places, circuit_actions, result, design
                    )
                else:
                    resources = capture_resources(primary, places)
            except InputError as exc:
                described = exc.details.get("symbol")
                indices = [
                    i
                    for i, a in enumerate(circuit_actions)
                    if a["op"] == "place_symbol" and (described is None or a["symbol"] == described)
                ]
                prefix = ["changes"] if isinstance(edit_document, dict) else []
                location = (
                    [*prefix, 0, "actions", indices[0], "symbol"]
                    if indices
                    else [*prefix, 0, "actions"]
                )
                raise InputError(
                    "A precise symbol resource cannot be resolved",
                    **sch_edit_contract.details(
                        [
                            {
                                "path": sch_edit_contract.pointer(location),
                                "problem": str(exc),
                                "op": "circuit",
                            }
                        ]
                    ),
                    resource_details=exc.details,
                ) from exc
            for name in resources.files:
                result.add(name, capture_identity=True)
            resources.validate()
        result.add(params["changes"], capture_identity=True)
        symbols.clear()
        # Scalar-only edits use embedded definitions and consult no library table.
        # Existing tables already captured above remain drift-bound, not interpreted.
        tables = lib_tables.Tables()
    else:
        tables = lib_tables.Tables.of(primary.parent, Path(root) if root else None)
    for symbol in symbols:
        nickname = symbol.split(":", 1)[0]
        if nickname in tables.symbols:
            result.add(tables.symbols[nickname].path)
        if root:
            result.add(kicad_env.resources(root) / "symbols" / (nickname + ".kicad_sym"))
    if command == "board from-netlist":
        from . import boardgen

        plan = boardgen.plan(params["netlist"], params["pitch"], params["margin"], primary.parent)
        for item in plan["components"]:
            result.add(item["file"], required=True)
    elif command == "board footprint place" and root:
        from .fileformat.board import Board

        b = Board.load(primary)
        for fp in b.footprints:
            fpid = fp.library_id
            nickname, separator, name = fpid.partition(":")
            if separator:
                library = tables.footprints.get(nickname)
                result.add(
                    (
                        library.path
                        if library
                        else kicad_env.resources(root) / "footprints" / (nickname + ".pretty")
                    )
                    / (name + ".kicad_mod")
                )
    result.validate()
    return result, primary
