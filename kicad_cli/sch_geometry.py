"""Exact, bounded schematic geometry edits, prepared entirely in memory.

Selectors name source objects from one original catalog.  Numeric atoms in
their geometry are the only source spans replaced; neither the parser's
renderer nor KiCad is asked to save the drawing.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from . import envelope
from .fileformat.circuit import Design
from .fileformat.schematic import SchematicError
from .fileformat.sexpr import List, SexprError
from .sch_objects import MAX_ABS_MM

QUANTUM_NM = 100
TICKS_PER_MM = 10_000
MAX_TICKS = MAX_ABS_MM * TICKS_PER_MM
MAX_OPERATIONS = 128
MAX_SELECTED_OBJECTS = 4096
LABELS = frozenset(
    ("label", "global_label", "hierarchical_label", "directive_label", "netclass_flag")
)
SUPPORTED = LABELS | {"symbol", "wire", "junction", "no_connect"}
_TOKENS = re.compile(r'\s+|"(?:[^"\\]|\\.)*"|[()]|[^\s()"]+|"', re.S)


class GeometryError(ValueError):
    """An unsupported or inexact edit, with a changes-file location."""

    def __init__(self, message: str, path: str, **details: Any) -> None:
        super().__init__(message)
        self.details = {"path": path, "problem": message, **details}


def _decimal(value: Any, path: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise GeometryError("a coordinate is a finite decimal number", path)
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise GeometryError("a coordinate is a finite decimal number", path) from exc
    if not parsed.is_finite():
        raise GeometryError("a coordinate is a finite decimal number", path)
    return parsed


def _ticks(value: Any, path: str) -> int:
    parsed = _decimal(value, path)
    if parsed.copy_abs() > MAX_ABS_MM:
        raise GeometryError(
            "a coordinate must be within the supported absolute millimetre bound",
            path,
            maximum_absolute_mm=MAX_ABS_MM,
        )
    if not parsed:
        return 0
    sign, digits, exponent = parsed.as_tuple()
    word = "".join(str(digit) for digit in digits)
    significant = word.rstrip("0")
    exponent += len(word) - len(significant)
    if exponent < -4:
        raise GeometryError("a coordinate must be on KiCad's 0.0001 mm grid", path)
    ticks = int(significant) * 10 ** (exponent + 4)
    return -ticks if sign else ticks


def _mm(ticks: int) -> str:
    magnitude = abs(ticks)
    whole, fraction = divmod(magnitude, TICKS_PER_MM)
    tail = f".{fraction:04d}".rstrip("0") if fraction else ""
    return ("-" if ticks < 0 else "") + str(whole) + tail


def _pair(value: Any, path: str) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2:
        raise GeometryError("coordinates are a two-item [x, y] array in millimetres", path)
    return _ticks(value[0], path + "/0"), _ticks(value[1], path + "/1")


def _angle(value: Any, path: str) -> int:
    parsed = _decimal(value, path)
    numerator, denominator = parsed.as_integer_ratio()
    if denominator != 1 or numerator % 90:
        raise GeometryError("an existing angle must be a multiple of 90 degrees", path)
    return numerator % 360


@dataclass(frozen=True)
class Transform:
    translation: tuple[int, int]
    rotation: int
    pivot: tuple[int, int]

    @classmethod
    def of(cls, change: dict[str, Any], path: str) -> Transform:
        rotation = change.get("rotate_deg", 0)
        if isinstance(rotation, bool) or rotation not in (0, 90, 180, 270):
            raise GeometryError("rotate_deg is 0, 90, 180, or 270", path + "/rotate_deg")
        if rotation and "pivot_mm" not in change:
            raise GeometryError("a rotation requires pivot_mm", path + "/pivot_mm")
        return cls(
            _pair(change.get("translate_mm", [0, 0]), path + "/translate_mm"),
            int(rotation),
            _pair(change.get("pivot_mm", [0, 0]), path + "/pivot_mm"),
        )

    def point(self, point: tuple[int, int]) -> tuple[int, int]:
        x, y = point[0] - self.pivot[0], point[1] - self.pivot[1]
        x, y = {0: (x, y), 90: (y, -x), 180: (-x, -y), 270: (-y, x)}[self.rotation]
        return (
            self.pivot[0] + x + self.translation[0],
            self.pivot[1] + y + self.translation[1],
        )


@dataclass(frozen=True)
class Patch:
    start: int
    end: int
    before: str
    after: str
    object_id: str


def _one(node: List, head: str, path: str) -> List:
    found = node.find_all(head)
    if len(found) != 1:
        raise GeometryError(f"the selected object requires exactly one ({head} ...)", path)
    return found[0]


def _atoms(source: str, node: List, path: str) -> list[tuple[int, int, str]]:
    """Direct atom spans of a geometry leaf, without changing its whitespace."""
    if node.start < 0 or any(isinstance(item, List) for item in node.items):
        raise GeometryError("geometry must be an original coordinate leaf", path)
    found = []
    for match in _TOKENS.finditer(source, node.start, node.end):
        token = match.group()
        if token.isspace() or token in ("(", ")"):
            continue
        found.append((match.start(), match.end(), token))
    if [token for _, _, token in found] != node.items:
        raise GeometryError("the selected geometry cannot be located exactly in its source", path)
    return found


def _coordinate_patches(
    source: str,
    node: List,
    transform: Transform,
    object_id: str,
    path: str,
    *,
    has_angle: bool,
    mirrored: bool = False,
    upright_angle: bool = False,
) -> list[Patch]:
    atoms = _atoms(source, node, path)
    if len(atoms) != (4 if has_angle else 3):
        raise GeometryError("the selected geometry has an unsupported coordinate shape", path)
    point = (_ticks(atoms[1][2], path + "/x"), _ticks(atoms[2][2], path + "/y"))
    transformed = transform.point(point)
    if any(abs(value) > MAX_TICKS for value in transformed):
        raise GeometryError(
            "the transformed coordinate exceeds the supported absolute millimetre bound",
            path,
            maximum_absolute_mm=MAX_ABS_MM,
        )
    values = {1: _mm(transformed[0]), 2: _mm(transformed[1])}
    if has_angle:
        old_angle = _angle(atoms[3][2], path + "/angle")
        if transform.rotation:
            turn = -transform.rotation if mirrored else transform.rotation
            values[3] = str((old_angle + turn) % (180 if upright_angle else 360))
    patches = []
    for index, after in values.items():
        start, end, before = atoms[index]
        # Keep the existing lexical representation when its value did not move.
        same = (
            _angle(before, path) == int(after)
            if index == 3
            else _ticks(before, path) == _ticks(after, path)
        )
        if not same:
            patches.append(Patch(start, end, before, after, object_id))
    return patches


def _object_patches(obj: Any, transform: Transform, path: str) -> list[Patch]:
    node = obj.node
    kind = node.head
    source = obj.schematic.document.source
    if kind not in SUPPORTED:
        raise GeometryError("this object kind is outside the geometry edit set", path, kind=kind)
    if obj.record.get("editable") is False:
        raise GeometryError(
            "the selected object has geometry that cannot be edited exactly",
            path,
            reasons=obj.record.get("uneditable_reasons", []),
        )
    patches = []
    if kind == "wire":
        pts = _one(node, "pts", path)
        points = pts.lists()
        if len(points) < 2 or len(points) != len(pts.items) - 1:
            raise GeometryError("a wire requires at least two explicit xy points", path)
        for index, point in enumerate(points):
            if point.head != "xy":
                raise GeometryError("a wire has an unsupported point kind", path)
            patches.extend(
                _coordinate_patches(
                    source, point, transform, obj.object_id, path + f"/pts/{index}", has_angle=False
                )
            )
        return patches
    mirrored = False
    if kind == "symbol":
        mirrors = node.find_all("mirror")
        if len(mirrors) > 1 or (mirrors and mirrors[0].values() not in (["x"], ["y"])):
            raise GeometryError("a symbol mirror must be x or y", path)
        mirrored = bool(mirrors)
    patches.extend(
        _coordinate_patches(
            source,
            _one(node, "at", path),
            transform,
            obj.object_id,
            path + "/at",
            has_angle=kind == "symbol" or kind in LABELS,
            mirrored=mirrored,
        )
    )
    if kind == "symbol" or kind in LABELS:
        for index, prop in enumerate(node.find_all("property")):
            patches.extend(
                _coordinate_patches(
                    source,
                    _one(prop, "at", path + f"/property/{index}"),
                    transform,
                    obj.object_id,
                    path + f"/property/{index}/at",
                    has_angle=True,
                    upright_angle=kind == "symbol",
                )
            )
    return patches


def _splice(source: str, patches: list[Patch]) -> str:
    ordered = sorted(patches, key=lambda patch: (patch.start, patch.end))
    previous = 0
    for patch in ordered:
        if patch.start < previous or source[patch.start : patch.end] != patch.before:
            raise GeometryError("selected geometry source spans overlap or changed", "/changes")
        previous = patch.end
    for patch in reversed(ordered):
        source = source[: patch.start] + patch.after + source[patch.end :]
    return source


def _read(root: Path, sources: dict[Path, str] | None = None) -> Design:
    try:
        return Design(root, sources)
    except (SchematicError, SexprError, UnicodeDecodeError, OSError, ValueError) as exc:
        envelope.fail(
            "E_VALIDATION",
            "the schematic could not be read for a geometry edit",
            {"schematic": str(root), "reason": str(exc)[:300]},
        )
        raise AssertionError("unreachable") from exc


def _source_locations(objects: list[Any]) -> dict[str, tuple[str, int]]:
    ordinals: dict[int, dict[int, int]] = {}
    locations = {}
    for obj in objects:
        key = id(obj.schematic)
        if key not in ordinals:
            ordinals[key] = {
                id(node): index for index, node in enumerate(obj.schematic.root.lists())
            }
        locations[obj.object_id] = obj.source_relative, ordinals[key][id(obj.node)]
    return locations


def _object_preview(obj: Any) -> dict[str, Any]:
    """Geometry and fields, without manufacturing an unsaved future selector."""
    return {
        "geometry": deepcopy(obj.record["geometry"]),
        "fields": [
            {key: deepcopy(value) for key, value in field.items() if key != "source_span"}
            for field in obj.record["fields"]
        ],
        "affected_instances": sorted(instance.path for instance in obj.instances),
        "instances": [
            {
                key: deepcopy(row[key])
                for key in ("instance_path", "sheet", "page", "ref", "unit")
                if key in row
            }
            for row in obj.record["instances"]
        ],
    }


def run_geometry(
    schematic: Path,
    changes: list[dict[str, Any]],
    *,
    identity_root: Path | None = None,
) -> tuple[dict[str, Any], dict[Path, str]]:
    """Resolve one original snapshot, prepare all transforms, then guard once.

    Returned sources are candidates only.  A caller must refuse the write
    whenever any boolean in ``verified`` is false.
    """
    from .sch_geometry_graph import GeometryGraphError, compare, witness
    from .sch_objects import Catalog, CatalogError

    prefix = getattr(changes, "path_prefix", "/changes")
    try:
        if not isinstance(changes, list) or not changes:
            raise GeometryError("a geometry batch contains at least one transform", prefix)
        if len(changes) > MAX_OPERATIONS:
            raise GeometryError(
                "the geometry batch exceeds the operation limit", prefix, maximum=MAX_OPERATIONS
            )
    except GeometryError as exc:
        envelope.fail("E_VALIDATION", "the geometry batch cannot be applied", exc.details)
        raise AssertionError("unreachable") from exc
    schematic = Path(schematic).resolve()
    design = _read(schematic)
    try:
        catalog = Catalog(design, identity_root=identity_root)
    except CatalogError as exc:
        envelope.fail(exc.code, str(exc), exc.details)
        raise AssertionError("unreachable") from exc
    report_changes = []
    resolved_changes: list[list[Any]] = []
    patches: dict[Path, list[Patch]] = defaultdict(list)
    seen: set[str] = set()
    selected_count = 0
    try:
        for index, change in enumerate(changes):
            path = f"{prefix}/{index}"
            if not isinstance(change, dict) or change.get("op") != "transform":
                raise GeometryError("transform cannot be mixed with other edit operations", path)
            extra = set(change) - {
                "op",
                "objects",
                "translate_mm",
                "rotate_deg",
                "pivot_mm",
                "affected_instances",
            }
            if extra:
                name = sorted(extra)[0]
                raise GeometryError("transform does not take this field", path + "/" + name)
            transform = Transform.of(change, path)
            selectors = change.get("objects")
            if not isinstance(selectors, list) or not selectors:
                raise GeometryError(
                    "objects contains at least one query selector", path + "/objects"
                )
            selected_count += len(selectors)
            if selected_count > MAX_SELECTED_OBJECTS:
                raise GeometryError(
                    "the geometry batch exceeds the selected source-object limit",
                    path + "/objects",
                    maximum=MAX_SELECTED_OBJECTS,
                )
            resolved = []
            for selector_index, selector in enumerate(selectors):
                obj = catalog.resolve(selector)
                if obj.object_id in seen:
                    raise GeometryError(
                        "one source object may be transformed only once in a batch",
                        path + f"/objects/{selector_index}",
                        object_id=obj.object_id,
                    )
                seen.add(obj.object_id)
                resolved.append(obj)
            affected = sorted({instance.path for obj in resolved for instance in obj.instances})
            acknowledged = change.get("affected_instances")
            if (
                not isinstance(acknowledged, list)
                or not all(isinstance(item, str) for item in acknowledged)
                or len(acknowledged) != len(set(acknowledged))
                or sorted(acknowledged) != affected
            ):
                raise GeometryError(
                    "affected_instances must contain every full source-object instance path",
                    path + "/affected_instances",
                    expected=affected,
                    provided=acknowledged,
                )
            for obj in resolved:
                patches[obj.schematic.path.resolve()].extend(_object_patches(obj, transform, path))
            resolved_changes.append(resolved)
            report_changes.append(
                {
                    "op": "transform",
                    "objects": [obj.object_id for obj in resolved],
                    "translate_mm": [_mm(value) for value in transform.translation],
                    "rotate_deg": transform.rotation,
                    "pivot_mm": [_mm(value) for value in transform.pivot],
                    "affected_instances": affected,
                }
            )
    except CatalogError as exc:
        envelope.fail(exc.code, str(exc), exc.details)
        raise AssertionError("unreachable") from exc
    except GeometryError as exc:
        envelope.fail("E_VALIDATION", "the geometry batch cannot be applied", exc.details)
        raise AssertionError("unreachable") from exc

    originals = {path.resolve(): sch.document.source for path, sch in design.schematics.items()}
    try:
        sources = {
            path: _splice(originals[path], edits) for path, edits in patches.items() if edits
        }
    except GeometryError as exc:
        envelope.fail("E_VALIDATION", "the geometry batch cannot be applied", exc.details)
        raise AssertionError("unreachable") from exc
    after = _read(schematic, sources)
    try:
        after_catalog = Catalog(after, identity_root=identity_root)
    except CatalogError as exc:
        envelope.fail(exc.code, str(exc), exc.details)
        raise AssertionError("unreachable") from exc
    before_locations = _source_locations(catalog.objects)
    after_locations = _source_locations(after_catalog.objects)
    after_objects = {after_locations[obj.object_id]: obj for obj in after_catalog.objects}
    for change, selected in zip(report_changes, resolved_changes, strict=True):
        change["object_changes"] = [
            {
                "object_id": obj.object_id,
                "kind": obj.kind,
                "uuid": obj.record.get("uuid"),
                "source_relative": obj.source_relative,
                "source_ordinal": before_locations[obj.object_id][1],
                "before": _object_preview(obj),
                "after": _object_preview(after_objects[before_locations[obj.object_id]]),
            }
            for obj in selected
        ]
    affected = sorted({path for change in report_changes for path in change["affected_instances"]})
    try:
        before_witness = witness(design, affected_instances=affected)
        after_witness = witness(after, affected_instances=affected)
    except GeometryGraphError as exc:
        envelope.fail(
            "E_VALIDATION", "schematic connectivity could not be measured completely", exc.details
        )
        raise AssertionError("unreachable") from exc
    connections = compare(before_witness, after_witness, catalog)
    preserved = bool(connections["ok"])
    atom_count = sum(len(edits) for edits in patches.values())
    protection = {
        "numeric_atoms_only": True,
        "outside_geometry_spans_unchanged": True,
        "changed_numeric_atoms": atom_count,
        "selected_source_objects": len(seen),
        "changed_files": len(sources),
    }
    return {
        "ok": preserved,
        "schematic": str(schematic),
        "changes": report_changes,
        "files": sorted(str(path) for path in sources),
        "inputs": {
            str(path): hashlib.sha256(text.encode("utf-8")).hexdigest()
            for path, text in originals.items()
        },
        "verified": {
            "nets_join_the_pins_asked": preserved,
            "nets_named_as_asked": preserved,
            "no_other_net_renamed": preserved,
            "fields_read_back": True,
            "every_part_still_there": True,
            "geometry_valid": True,
            "connections_preserved": preserved,
            "protected_content_preserved": True,
            "problems": connections.get("differences", []),
        },
        "geometry": {
            "root": catalog.root,
            "snapshot": catalog.snapshot,
            "coordinate_quantum_nm": QUANTUM_NM,
            "policy": {
                "symbol_fields_normalized_upright_on_rotation": True,
                "symbol_field_translation_only_keeps_original_angle_atom": True,
                "symbol_mirrors_preserved": True,
                "label_angles_keep_full_orientation": True,
                "maximum_absolute_coordinate_mm": MAX_ABS_MM,
                "maximum_operations": MAX_OPERATIONS,
                "maximum_selected_source_objects": MAX_SELECTED_OBJECTS,
            },
            "connections": connections,
            "protection": protection,
        },
        "erc": {
            "checked": False,
            "before": None,
            "after": None,
            "new": [],
            "new_count": None,
            "cleared": [],
            "cleared_count": None,
        },
        "unmeasured": [
            "visual text or symbol overlap and page bounds",
            "rendered label glyph readability and native label layout",
            "native KiCad ERC, netlist, and save-roundtrip behavior",
            "PCB geometry and routing",
        ],
    }, sources
