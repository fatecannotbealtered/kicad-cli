"""One bounded immutable input set for saved-document observations, without libraries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from .fileformat.circuit import MAX_HIERARCHY_DEPTH, MAX_HIERARCHY_SOURCES, Design
from .fileformat.schematic import Schematic
from .fileformat.sexpr import Document
from .sync_inputs import MAX_FILES, MAX_TOTAL_BYTES, Drift, InputError, _absolute, _read


@dataclass(frozen=True)
class Inputs:
    board: Path | None
    schematic: Path | None
    files: object
    manifest: list
    design: Design | None

    def raw(self, path):
        return self.files[_absolute(path)]

    def validate(self):
        changed = [old["path"] for old in self.manifest if _read(old["path"])[0] != old]
        if changed:
            raise Drift(
                "Saved observation inputs changed during computation.", changed_files=changed
            )


def capture(*, board=None, schematic=None):
    board = _absolute(board) if board is not None else None
    schematic = _absolute(schematic) if schematic is not None else None
    files, records, total = {}, {}, 0

    def read(path, required=False):
        nonlocal total
        path = _absolute(path)
        if path not in records:
            row, raw = _read(path)
            records[path] = row
            files[path] = files[Path(row["resolved"])] = raw
            total += len(raw or b"")
            if len(records) > MAX_FILES or total > MAX_TOTAL_BYTES:
                raise InputError("Observation input bounds exceeded.")
        if required and files[path] is None:
            raise FileNotFoundError(path)
        return files[path]

    def project(path):
        raw = read(path)
        if raw is None:
            return
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise InputError("Present project settings must be a JSON object.", path=str(path))
        # Native optional null objects are equivalent to absent; other types are errors.
        branches = [
            (data, key) for key in ("board", "erc", "schematic", "net_settings", "text_variables")
        ]
        branches.append((data.get("board") or {}, "design_settings"))
        for parent, key in branches:
            if not isinstance(parent, dict) or (
                parent.get(key) is not None and not isinstance(parent[key], dict)
            ):
                raise InputError("Malformed project settings object.", path=str(path), field=key)
        for parent in [
            (data.get("board") or {}).get("design_settings") or {},
            data.get("erc") or {},
        ]:
            for key in ("rules", "rule_severities"):
                if parent.get(key) is not None and not isinstance(parent[key], dict):
                    raise InputError("Malformed rule configuration.", path=str(path), field=key)
            if any(
                value not in {"error", "warning", "ignore"}
                for value in (parent.get("rule_severities") or {}).values()
            ):
                raise InputError("Unknown configured rule severity.", path=str(path))
        pin_map = (data.get("erc") or {}).get("pin_map")
        if pin_map is not None and (
            not isinstance(pin_map, list)
            or len(pin_map) != 12
            or any(
                not isinstance(row, list)
                or len(row) != 12
                or any(type(value) is not int or value not in {0, 1, 2} for value in row)
                for row in pin_map
            )
        ):
            raise InputError(
                "Malformed ERC pin conflict matrix; defaults cannot conceal it.", path=str(path)
            )

    if board is not None:
        read(board, required=True)
        project(board.with_suffix(".kicad_pro"))
        read(board.with_suffix(".kicad_dru"))
    if schematic is not None:
        project(schematic.with_suffix(".kicad_pro"))
        missing, visited, discovered = [], set(), set()

        def walk(path, ancestors):
            if len(ancestors) > MAX_HIERARCHY_DEPTH:
                raise InputError("Schematic hierarchy exceeds32 nested sheet levels.")
            raw = read(path)
            if raw is None:
                missing.append(str(path))
                return
            key = Path(records[_absolute(path)]["resolved"])
            if key in ancestors:
                raise InputError("Cyclic schematic hierarchy.", path=str(path))
            if key in visited:
                return
            discovered.add(key)
            if len(discovered) > MAX_HIERARCHY_SOURCES:
                raise InputError("Schematic hierarchy exceeds128 distinct source files.")
            sheet = Schematic(Document.parse(raw.decode("utf-8")), path)
            for child in sheet.sheets:
                walk(path.parent / child.file, {*ancestors, key})
            visited.add(key)

        read(schematic, required=True)
        walk(schematic, set())
        if missing:
            raise InputError(
                "Every referenced sheet is required for a complete observation.",
                missing_sheets=missing,
            )
        design = Design(schematic, snapshot=MappingProxyType(dict(files)))
    else:
        design = None
    result = Inputs(
        board,
        schematic,
        MappingProxyType(dict(files)),
        [records[p] for p in sorted(records)],
        design,
    )
    result.validate()
    return result
