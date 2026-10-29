"""Snapshot-bound schematic source objects and their hierarchy occurrences.

The object is a source list, rather than a reference, a wire segment, or an
electrical pin. A shared file consequently has one object and several logical
occurrences. Selectors bind both that source list and the complete input revision.
This module imports neither a native application nor a command handler.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from copy import copy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .fileformat.circuit import (
    MAX_HIERARCHY_DEPTH,
    MAX_HIERARCHY_INSTANCES,
    MAX_HIERARCHY_SOURCES,
    Design,
    SheetInstance,
    grouped,
)
from .fileformat.schematic import Schematic, uuid_of
from .fileformat.sexpr import List

MAX_OBJECTS = 50_000
MAX_INSTANCE_OBJECTS = 100_000
MAX_LIMIT = 500
QUANTUM_NM = 100
# Conservative editable domain below KiCad10.0.6's SCH file-coordinate clamp.
MAX_ABS_MM = 10_000
EDITABLE_KINDS = frozenset(
    {"symbol", "wire", "label", "global_label", "hierarchical_label", "junction", "no_connect"}
)
OTHER_KINDS = frozenset(
    {
        "bus",
        "sheet",
        "bus_entry",
        "text",
        "text_box",
        "polyline",
        "rectangle",
        "circle",
        "arc",
        "image",
        "directive_label",
        "netclass_flag",
    }
)
KINDS = EDITABLE_KINDS | OTHER_KINDS
_CHILDREN = {
    "symbol": {
        "lib_id",
        "lib_name",
        "at",
        "mirror",
        "unit",
        "body_style",
        "convert",
        "in_bom",
        "on_board",
        "dnp",
        "exclude_from_sim",
        "uuid",
        "property",
        "pin",
        "instances",
        "fields_autoplaced",
    },
    "wire": {"pts", "stroke", "uuid"},
    "label": {"at", "effects", "uuid", "property", "fields_autoplaced"},
    "global_label": {"at", "shape", "effects", "uuid", "property", "fields_autoplaced"},
    "hierarchical_label": {"at", "shape", "effects", "uuid", "property", "fields_autoplaced"},
    "junction": {"at", "diameter", "color", "uuid"},
    "no_connect": {"at", "uuid"},
}


class CatalogError(ValueError):
    def __init__(self, message: str, *, code: str = "E_VALIDATION", **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _absolute(path: str | Path) -> Path:
    return Path(os.path.abspath(Path(path).expanduser()))


def _relative(path: Path, folder: Path) -> str:
    return Path(os.path.relpath(_absolute(path), folder)).as_posix()


def _mm(point: list[int] | tuple[int, int]) -> list[float]:
    return [value / 1_000_000 for value in point]


def _coordinate(raw: str | None) -> int:
    try:
        number = Decimal(raw or "") * 1_000_000
    except InvalidOperation as exc:
        raise ValueError("coordinate is not a finite number") from exc
    if not number.is_finite() or number != number.to_integral_value():
        raise ValueError("coordinate must be a finite whole number of nanometres")
    return int(number)


def _point(node: List) -> list[int]:
    return [_coordinate(node.atom(1)), _coordinate(node.atom(2))]


def _position(node: List, problems: list[str]) -> dict[str, Any]:
    try:
        point = _point(node)
        if any(value % QUANTUM_NM for value in point):
            problems.append("coordinates are outside the 100 nm schematic quantum")
        if any(abs(value) > MAX_ABS_MM * 1_000_000 for value in point):
            problems.append("coordinates exceed the conservative absolute10000mm edit domain")
        result: dict[str, Any] = {"coordinate_nm": point, "coordinate_mm": _mm(point)}
        if node.head == "at":
            angle = Decimal(node.atom(3) or "0")
            if not angle.is_finite():
                raise ValueError("angle is not finite")
            result["angle_deg"] = float(angle)
            if angle % 90:
                problems.append("angle is not a multiple of 90 degrees")
        return result
    except (ValueError, InvalidOperation) as exc:
        problems.append(str(exc))
        return {"coordinate_nm": None, "coordinate_mm": None}


def _span(node: List) -> list[int] | None:
    return [node.start, node.end] if node.start >= 0 and node.end > node.start else None


def _geometry(node: List, problems: list[str]) -> dict[str, Any]:
    at = node.find("at")
    geometry = _position(at, problems) if at is not None else {}
    if node.head in EDITABLE_KINDS - {"wire"} and at is None:
        problems.append("object has no position")
    if node.head in {"wire", "bus", "polyline"}:
        pts = node.find("pts")
        points = [_position(point, problems) for point in pts.lists()] if pts is not None else []
        if len(points) < 2 or (pts is not None and any(p.head != "xy" for p in pts.lists())):
            problems.append("wire must contain at least two xy points")
        geometry.update(
            points_nm=[p["coordinate_nm"] for p in points],
            points_mm=[p["coordinate_mm"] for p in points],
        )
    mirror = node.find("mirror")
    if mirror is not None:
        geometry["mirror"] = mirror.atom(1)
        if (
            len(node.find_all("mirror")) != 1
            or len(mirror.items) != 2
            or mirror.atom(1) not in {"x", "y"}
        ):
            problems.append("unsupported symbol mirror")
    return geometry


def _fields(node: List, problems: list[str]) -> list[dict[str, Any]]:
    fields = []
    for field in node.find_all("property"):
        at = field.find("at")
        geometry = _position(at, problems) if at is not None else {}
        fields.append(
            {
                "name": field.value(1),
                "value": field.value(2),
                "source_span": _span(field),
                "ownership": "moves_with_object",
                "geometry": geometry,
            }
        )
    return fields


@dataclass
class Object:
    schematic: Schematic
    node: List
    kind: str
    source_relative: str
    object_id: str
    instances: list[SheetInstance]
    record: dict[str, Any]

    @property
    def sch(self) -> Schematic:
        return self.schematic


class Catalog:
    def __init__(self, design: Design, *, identity_root: Path | None = None) -> None:
        self.design = design
        self.identity_root = _absolute(identity_root or design.root_path).resolve()
        self.root = self.identity_root.as_uri()
        self.objects: list[Object] = []
        self.by_id: dict[str, Object] = {}
        self.connections: list[dict[str, Any]] = []
        self.connection_status = "complete"
        self.connection_problem: str | None = None
        folder = _absolute(design.root_path).parent
        sources: dict[str, tuple[Schematic, bytes]] = {}
        source_of: dict[int, str] = {}
        # Resolved aliases describe one source object, even when reached by
        # different lexical paths in the hierarchy.
        canonical: dict[Path, str] = {}
        for path, schematic in design.schematics.items():
            resolved = _absolute(path).resolve()
            relative = canonical.setdefault(resolved, _relative(path, folder))
            source_of[id(schematic)] = relative
            raw = schematic.document.dumps().encode("utf-8")
            if relative in sources and sources[relative][1] != raw:
                raise CatalogError("Aliased schematic sources have inconsistent bytes.")
            sources.setdefault(relative, (schematic, raw))
        manifest = [
            {"path": relative, "sha256": hashlib.sha256(raw).hexdigest()}
            for relative, (_, raw) in sorted(sources.items())
        ]
        project = _absolute(design.root_path.with_suffix(".kicad_pro"))
        if design._snapshot is not None:
            if project not in design._snapshot:
                raise CatalogError("Captured schematic snapshot omits the root project file.")
            project_raw = design._snapshot[project]
        else:
            try:
                project_raw = project.read_bytes()
            except FileNotFoundError:
                project_raw = None
        manifest.append(
            {
                "path": _relative(project, folder),
                "present": project_raw is not None,
                "sha256": hashlib.sha256(project_raw).hexdigest()
                if project_raw is not None
                else None,
            }
        )
        self.snapshot_inputs = manifest
        self.snapshot = _digest(manifest)
        placements: dict[str, list[SheetInstance]] = defaultdict(list)
        for instance in design.instances:
            placements[source_of[id(instance.schematic)]].append(instance)
        pages = design.pages()
        self.pages = [
            {
                "instance_path": instance.path,
                "sheet": instance.name,
                "page": pages[instance.path],
                "source_relative": source_of[id(instance.schematic)],
                "source_file": str(self.identity_root.parent / source_of[id(instance.schematic)]),
            }
            for instance in sorted(design.instances, key=lambda row: row.path)
        ]
        instance_objects = 0
        self._node_objects: dict[tuple[int, int], Object] = {}
        aliases: dict[str, dict[tuple, list[tuple[int, int]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for other in design.schematics.values():
            for candidate in other.root.lists():
                aliases[source_of[id(other)]][
                    (candidate.head, candidate.start, candidate.end)
                ].append((id(other), id(candidate)))
        for relative, (schematic, raw) in sorted(sources.items()):
            file_hash = hashlib.sha256(raw).hexdigest()
            occurrences = sorted(placements[relative], key=lambda instance: instance.path)
            for node in schematic.root.lists():
                if node.head not in KINDS:
                    continue
                problems: list[str] = []
                if node.head not in EDITABLE_KINDS:
                    problems.append("object kind is outside the geometry edit contract")
                else:
                    unknown = sorted({child.head for child in node.lists()} - _CHILDREN[node.head])
                    if unknown:
                        problems.append("unsupported object children: " + ", ".join(unknown))
                geometry = _geometry(node, problems)
                fields = _fields(node, problems)
                span = schematic.document.span(node)
                if span is None:
                    problems.append("source object has no untouched source span")
                object_id = "schobj:" + _digest([relative, node.head, span, file_hash])
                rows = []
                symbol = node.head == "symbol"
                source_unit = node.find("unit")
                try:
                    unit = int(source_unit.atom(1)) if source_unit is not None else 1
                except (TypeError, ValueError):
                    unit = None
                    problems.append("symbol unit is not an integer")
                properties = {field["name"]: field["value"] for field in fields}
                placed_paths = {}
                symbol_instances = node.find("instances")
                for project_node in (
                    symbol_instances.find_all("project") if symbol_instances is not None else []
                ):
                    for placed_path in project_node.find_all("path"):
                        placed_paths[placed_path.value(1)] = placed_path
                for instance in occurrences:
                    row: dict[str, Any] = {
                        "instance_path": instance.path,
                        "path": instance.path,
                        "sheet": instance.name,
                        "page": pages[instance.path],
                        "nets": [],
                        "connection_ids": [],
                    }
                    if symbol:
                        placed = placed_paths.get(instance.path)
                        placed_ref = placed.find("reference") if placed is not None else None
                        placed_unit = placed.find("unit") if placed is not None else None
                        try:
                            effective_unit = (
                                int(placed_unit.atom(1)) if placed_unit is not None else unit
                            )
                        except (TypeError, ValueError):
                            effective_unit = None
                            problems.append("instance symbol unit is not an integer")
                        row.update(
                            ref=placed_ref.value(1)
                            if placed_ref is not None
                            else properties.get("Reference", ""),
                            unit=effective_unit,
                            pins=[],
                        )
                    rows.append(row)
                record = {
                    "object_id": object_id,
                    "selector": {
                        "root": self.root,
                        "snapshot": self.snapshot,
                        "object_id": object_id,
                    },
                    "kind": node.head,
                    "uuid": uuid_of(node) or None,
                    "source_relative": relative,
                    "source_file": str(_absolute(self.identity_root.parent / relative)),
                    "source_span": list(span) if span is not None else None,
                    "span_unit": "unicode_codepoint",
                    "source_sha256": file_hash,
                    "geometry": geometry,
                    "fields": fields,
                    "editable": not problems,
                    "uneditable_reasons": list(dict.fromkeys(problems)),
                    "instances": rows,
                    "nets": [],
                    "connection_ids": [],
                }
                if node.head == "symbol":
                    lib_id = node.find("lib_id")
                    record["lib_id"] = lib_id.value(1) if lib_id is not None else None
                    record["source_unit"] = unit
                elif node.head in {
                    "label",
                    "global_label",
                    "hierarchical_label",
                    "text",
                    "text_box",
                }:
                    record["text"] = node.value(1)
                obj = Object(schematic, node, node.head, relative, object_id, occurrences, record)
                self.objects.append(obj)
                self.by_id[object_id] = obj
                # Map each alias model's node to the canonical object by its
                # span. Graph may have been built with another lexical alias.
                for key in aliases[relative][(node.head, node.start, node.end)]:
                    self._node_objects[key] = obj
                instance_objects += len(occurrences)
                if len(self.objects) > MAX_OBJECTS or instance_objects > MAX_INSTANCE_OBJECTS:
                    raise CatalogError(
                        "Schematic object catalog bounds exceeded.",
                        max_objects=MAX_OBJECTS,
                        max_instance_objects=MAX_INSTANCE_OBJECTS,
                    )
        self.objects.sort(key=lambda obj: (obj.source_relative, obj.node.start, obj.kind))
        self.instance_object_count = instance_objects
        self._attach_connections()
        self.hierarchy = self._hierarchy()

    def resolve(self, selector: dict[str, Any]) -> Object:
        if not isinstance(selector, dict) or set(selector) != {"root", "snapshot", "object_id"}:
            raise CatalogError("Object selector requires exactly root, snapshot and object_id.")
        if selector["root"] != self.root or selector["snapshot"] != self.snapshot:
            raise CatalogError(
                "Object selector belongs to another root or input revision.",
                code="E_CONFLICT",
                conflict="state_drift",
                reason_code="stale_object_selector",
                root=self.root,
                snapshot=self.snapshot,
            )
        object_id = selector["object_id"]
        if not isinstance(object_id, str) or object_id not in self.by_id:
            raise CatalogError(
                "Object selector is absent from this input revision.",
                code="E_CONFLICT",
                conflict="state_drift",
                reason_code="unknown_object_selector",
                object_id=object_id,
            )
        return self.by_id[object_id]

    def records(self, **params: Any) -> list[dict[str, Any]]:
        return self.query(params)["items"]

    def query(self, params: dict[str, Any] | None = None, **filters: Any) -> dict[str, Any]:
        params = {**(params or {}), **filters}
        offset, limit = params.get("offset", 0), params.get("limit", 100)
        if type(offset) is not int or offset < 0:
            raise CatalogError("Offset must be a non-negative integer.", parameter="--offset")
        if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
            raise CatalogError("Limit must be an integer from 1 through 500.", parameter="--limit")
        supported = {"page", "instance", "instance_path", "kind", "uuid", "ref", "net"}
        selected = {key: params[key] for key in supported if params.get(key) is not None}
        for key, value in selected.items():
            if not isinstance(value, str) or not value:
                raise CatalogError("Query filters must be non-empty strings.", parameter="--" + key)
        if selected.get("kind") not in KINDS and "kind" in selected:
            raise CatalogError(
                "Unknown schematic object kind.", parameter="--kind", kinds=sorted(KINDS)
            )
        if (
            selected.get("instance")
            and selected.get("instance_path")
            and (selected["instance"] != selected["instance_path"])
        ):
            raise CatalogError("Conflicting instance filters.", parameter="--instance")
        items = []
        for obj in self.objects:
            row = obj.record
            if any(row[key] != selected[key] for key in ("kind", "uuid") if key in selected):
                continue
            matches = []
            for instance in row["instances"]:
                if selected.get("page") not in {None, instance["page"], instance["sheet"]}:
                    continue
                wanted = selected.get("instance", selected.get("instance_path"))
                if wanted is not None and wanted != instance["instance_path"]:
                    continue
                if "ref" in selected and selected["ref"] != instance.get("ref"):
                    continue
                if "net" in selected and instance.get("connection_status") != "complete":
                    raise CatalogError(
                        "Net filtering requires complete connections on selected sheet instances.",
                        instance_path=instance["instance_path"],
                        reason=instance.get("connection_problem"),
                    )
                if "net" in selected and selected["net"] not in instance["nets"]:
                    continue
                matches.append(instance["instance_path"])
            if matches:
                items.append({**row, "matching_instances": matches})
        total = len(items)
        page = items[offset : offset + limit]
        more = offset + len(page) < total
        return {
            "items": page,
            "count": len(page),
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": more,
            "next_offset": offset + len(page) if more else None,
            "root": self.root,
            "snapshot": self.snapshot,
            "pages": self.pages,
            "hierarchy": self.hierarchy,
            "bounds": {
                "max_objects": MAX_OBJECTS,
                "max_instance_objects": MAX_INSTANCE_OBJECTS,
                "max_limit": MAX_LIMIT,
                "objects": len(self.objects),
                "instance_objects": self.instance_object_count,
            },
            "connection_status": self.connection_status,
            "connection_problem": self.connection_problem,
            "connections_count": len(self.connections),
        }

    def _hierarchy(self) -> dict[str, Any]:
        """Describe source definitions and their distinct saved logical placements."""
        by_path = {instance.path: instance for instance in self.design.instances}
        by_source: dict[str, list[str]] = defaultdict(list)
        page_rows = {row["instance_path"]: row for row in self.pages}
        for row in self.pages:
            by_source[row["source_relative"]].append(row["instance_path"])
        instances = []
        interface_problems = []
        for instance in sorted(self.design.instances, key=lambda item: item.path):
            row = page_rows[instance.path]
            parent = instance.parent
            source_instances = sorted(by_source[row["source_relative"]])
            entry = {
                **row,
                "parent_instance": parent.path if parent is not None else None,
                "children": sorted(
                    child.path for child in self.design.instances if child.parent is instance
                ),
                "depth": instance.path.count("/") - 1,
                "source_instances": source_instances,
                "reused_source": len(source_instances) > 1,
                "sheet_selector": None,
                "sheet_uuid": None,
                "display_name": instance.sheet.name if instance.sheet is not None else "/",
                "source_reference": instance.sheet.file if instance.sheet is not None else None,
                "interfaces": [],
            }
            if parent is not None and instance.sheet is not None:
                sheet = instance.sheet
                obj = self._node_objects.get((id(parent.schematic), id(sheet.node)))
                if obj is None:
                    raise CatalogError("Hierarchy sheet object is not publicly discoverable")
                entry.update(
                    sheet_selector=obj.record["selector"],
                    sheet_uuid=sheet.uuid,
                    frame={
                        "at_nm": list(sheet.position),
                        "at_mm": _mm(sheet.position),
                        "size_nm": list(sheet.size),
                        "size_mm": _mm(sheet.size),
                    },
                )
                labels = [
                    item
                    for item in self.objects
                    if item.schematic is instance.schematic and item.kind == "hierarchical_label"
                ]
                occurrence = next(
                    item for item in obj.record["instances"] if item["instance_path"] == parent.path
                )
                pin_connections = {
                    pin["sheet_pin_ordinal"]: pin for pin in occurrence.get("sheet_pins", [])
                }
                names = [pin.name for pin in sheet.pins]
                for ordinal, pin in enumerate(sheet.pins):
                    matches = [label for label in labels if label.record.get("text") == pin.name]
                    shape = matches[0].node.find("shape") if len(matches) == 1 else None
                    label_type = shape.atom(1) if shape is not None else None
                    status = (
                        "duplicate_sheet_pin"
                        if names.count(pin.name) != 1
                        else "missing_hierarchical_label"
                        if not matches
                        else "ambiguous_hierarchical_label"
                        if len(matches) != 1
                        else "type_mismatch"
                        if label_type != pin.electrical
                        else "paired"
                    )
                    pin_at = pin.node.find("at") if pin.node is not None else None
                    geometry_problems = []
                    pin_geometry = (
                        _position(pin_at, geometry_problems) if pin_at is not None else {}
                    )
                    if pin_at is None:
                        geometry_problems.append("sheet pin has no readable position")
                    x, y = pin.position
                    left, top = sheet.position
                    width, height = sheet.size
                    edges = [
                        edge
                        for edge, matches_edge in (
                            ("left", x == left and top < y < top + height),
                            ("right", x == left + width and top < y < top + height),
                            ("top", y == top and left < x < left + width),
                            ("bottom", y == top + height and left < x < left + width),
                        )
                        if matches_edge
                    ]
                    if len(edges) != 1:
                        geometry_problems.append("sheet pin is not on exactly one open frame edge")
                    if geometry_problems:
                        status = "unsupported_pin_geometry"
                    interface = {
                        "name": pin.name,
                        "direction": pin.electrical,
                        "sheet_pin_ordinal": ordinal,
                        "sheet_pin_uuid": uuid_of(pin.node) or None,
                        "parent_anchor": {
                            "selector": obj.record["selector"],
                            "instance_path": parent.path,
                            "sheet_pin_ordinal": ordinal,
                        },
                        "coordinate_nm": list(pin.position),
                        "coordinate_mm": _mm(pin.position),
                        "angle_deg": pin_geometry.get("angle_deg"),
                        "edge": edges[0] if len(edges) == 1 else None,
                        "geometry_problems": geometry_problems,
                        "child_labels": [
                            {
                                "selector": label.record["selector"],
                                "instance_path": instance.path,
                                "direction": label.node.find("shape").atom(1)
                                if label.node.find("shape") is not None
                                else None,
                                "geometry": label.record["geometry"],
                            }
                            for label in matches
                        ],
                        "pairing_status": status,
                        "connection": pin_connections.get(ordinal),
                    }
                    entry["interfaces"].append(interface)
                    if status != "paired":
                        interface_problems.append(
                            {"instance_path": instance.path, "name": pin.name, "problem": status}
                        )
                for label in labels:
                    if label.record.get("text") not in names:
                        interface_problems.append(
                            {
                                "instance_path": instance.path,
                                "name": label.record.get("text"),
                                "problem": "missing_sheet_pin",
                                "label_selector": label.record["selector"],
                            }
                        )
            instances.append(entry)
        return {
            "instances": instances,
            "sources": [
                {
                    "source_relative": source,
                    "source_file": str(_absolute(self.identity_root.parent / source)),
                    "instances": sorted(paths),
                }
                for source, paths in sorted(by_source.items())
            ],
            "root_instance": next(path for path, item in by_path.items() if item.parent is None),
            "sort": "full instance_path ascending; source_relative ascending",
            "truncated": False,
            "interface_status": "complete"
            if not interface_problems and not self.design.missing
            else "incomplete",
            "interface_problems": interface_problems,
            "missing_sources": self.design.missing,
            "bounds": {
                "max_depth": MAX_HIERARCHY_DEPTH,
                "max_instances": MAX_HIERARCHY_INSTANCES,
                "max_sources": MAX_HIERARCHY_SOURCES,
            },
            "selector_lifetime": "Selectors bind this complete input snapshot; "
            "any source/project change invalidates them.",
        }

    def _attach_connections(self) -> None:
        usable = []
        unavailable = {}
        # The graph's parser is intentionally stricter than source discovery.
        # Establish its coverage per occurrence before building cross-sheet edges.
        for instance in self.design.instances:
            try:
                schematic = instance.schematic
                for symbol in schematic.symbols:
                    if symbol.library_name not in schematic.library:
                        raise ValueError("missing embedded library symbol: " + symbol.library_name)
                self.design.placed_pins(instance)
                _ = (
                    schematic.wires,
                    schematic.labels,
                    schematic.junctions,
                    schematic.no_connects,
                    schematic.sheets,
                )
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
                unavailable[instance.path] = str(exc)
            else:
                usable.append(instance)
        for obj in self.objects:
            for row in obj.record["instances"]:
                problem = unavailable.get(row["instance_path"])
                row["connection_status"] = "unavailable" if problem else "complete"
                row["connection_problem"] = problem
                row["editable"] = obj.record["editable"] and problem is None
                if problem:
                    obj.record["editable"] = False
                    obj.record["uneditable_reasons"].append(
                        "connections unavailable for instance " + row["instance_path"]
                    )
        if unavailable:
            self.connection_status = "partial" if usable else "unavailable"
            self.connection_problem = "Connections unavailable for instances: " + ", ".join(
                sorted(unavailable)
            )
        view = copy(self.design)
        view.instances = usable
        try:
            found, nets = grouped(view, keep_empty=True)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
            # A readable but unsupported object must not hide unrelated objects.
            self.connection_status = "unavailable"
            self.connection_problem = str(exc)
            for obj in self.objects:
                obj.record["editable"] = False
                obj.record["uneditable_reasons"].append("connection observation failed")
                for row in obj.record["instances"]:
                    row.update(
                        connection_status="unavailable", connection_problem=str(exc), editable=False
                    )
            return
        rows = {
            (obj.object_id, row["instance_path"]): row
            for obj in self.objects
            for row in obj.record["instances"]
        }
        # All graph groups are retained, including an unnamed wire to nowhere.
        keys_by_group: dict[Any, list[Any]] = defaultdict(list)
        for key in found.net.parent:
            keys_by_group[found.net.find(key)].append(key)
        names = {found.net.find(keys[0]): net.name for net, keys in nets if keys}
        connection_of = {}
        for root, keys in keys_by_group.items():
            connection_id = "schconn:" + _digest([self.snapshot, sorted(keys, key=repr)])
            connection_of[root] = connection_id
            self.connections.append(
                {
                    "connection_id": connection_id,
                    "net": names.get(root),
                    "pin_occurrences": sum(key in found.pins_at for key in keys),
                    "no_connect_flags": sum(key[0] == "nc" for key in keys),
                }
            )
        self.connections.sort(key=lambda row: row["connection_id"])
        object_keys: dict[tuple[str, str], set[Any]] = defaultdict(set)
        instances = {instance.path: instance for instance in self.design.instances}
        nc_nodes = {
            instance.path: instance.schematic.root.find_all("no_connect")
            for instance in self.design.instances
        }
        point_keys: dict[tuple[str, tuple[int, int]], set[Any]] = defaultdict(set)
        wire_keys: dict[str, list[tuple[Any, Any]]] = defaultdict(list)
        for key, item in found.items.items():
            if len(key) < 2 or key[1] not in instances:
                continue
            instance = instances[key[1]]
            node = None
            if key[0] == "p":
                node = item.symbol.node
                point_keys[(instance.path, item.position)].add(key)
            elif key[0] in {"w", "b", "l"}:
                node = item.node
                if key[0] in {"w", "b"}:
                    wire_keys[instance.path].append((key, item))
                elif key in found.net.parent:
                    point_keys[(instance.path, item.position)].add(key)
            elif key[0] == "nc":
                node = nc_nodes[instance.path][key[2]]
                point_keys[(instance.path, item[0])].add(key)
            elif key[0] == "s":
                node = item[0].node
                point_keys[(instance.path, item[1].position)].add(key)
            obj = self._node_objects.get((id(instance.schematic), id(node)))
            if obj is None:
                continue
            if key not in found.net.parent:
                # Bus-expression labels are graph carriers, rather than scalar
                # net nodes. Keep their source objects discoverable without
                # inventing a scalar connection or hiding unrelated objects.
                problem = "bus-expression label connectivity is outside the geometry edit contract"
                obj.record["editable"] = False
                if problem not in obj.record["uneditable_reasons"]:
                    obj.record["uneditable_reasons"].append(problem)
                rows[(obj.object_id, instance.path)].update(
                    connection_status="unavailable", connection_problem=problem, editable=False
                )
                if self.connection_status == "complete":
                    self.connection_status = "partial"
                    self.connection_problem = problem
                continue
            object_keys[(obj.object_id, instance.path)].add(key)
            if key[0] == "s":
                root = found.net.find(key)
                sheet, pin = item
                rows[(obj.object_id, instance.path)].setdefault("sheet_pins", []).append(
                    {
                        "sheet_pin_ordinal": key[3],
                        "uuid": uuid_of(pin.node) or None,
                        "name": pin.name,
                        "direction": pin.electrical,
                        "coordinate_nm": list(pin.position),
                        "coordinate_mm": _mm(pin.position),
                        "net": names.get(root),
                        "connection_id": connection_of[root],
                    }
                )
            if key[0] == "p":
                pin = item.pin
                root = found.net.find(key)
                placed = item.symbol.instance(instance.path)
                lib = instance.schematic.library[item.symbol.library_name]
                ordinal = next(
                    index for index, candidate in enumerate(lib.pins) if candidate is pin
                )
                rows[(obj.object_id, instance.path)]["pins"].append(
                    {
                        "pin_id": "schpin:" + _digest([obj.object_id, instance.path, ordinal]),
                        "number": pin.number,
                        "name": item.function[0],
                        "electrical": item.function[1],
                        "uuid": item.symbol.pin_uuids.get(pin.number),
                        "lib_pin_ordinal": ordinal,
                        "unit": placed.unit if placed else item.symbol.unit,
                        "coordinate_nm": list(item.position),
                        "coordinate_mm": _mm(item.position),
                        "net": names.get(root),
                        "connection_id": connection_of[root],
                        "no_connect": item.flagged,
                    }
                )
        for obj in self.objects:
            if obj.kind == "junction":
                point = obj.record["geometry"].get("coordinate_nm")
                if point is not None:
                    point = tuple(point)
                    for instance in obj.instances:
                        keys = object_keys[(obj.object_id, instance.path)]
                        keys.update(point_keys.get((instance.path, point), ()))
                        for key, wire in wire_keys[instance.path]:
                            (px, py), (ax, ay), (bx, by) = point, wire.start, wire.end
                            if (
                                (bx - ax) * (py - ay) == (by - ay) * (px - ax)
                                and min(ax, bx) <= px <= max(ax, bx)
                                and min(ay, by) <= py <= max(ay, by)
                            ):
                                keys.add(key)
            for row in obj.record["instances"]:
                roots = {
                    found.net.find(key)
                    for key in object_keys[(obj.object_id, row["instance_path"])]
                }
                row["nets"] = sorted({names[root] for root in roots if root in names})
                row["connection_ids"] = sorted(connection_of[root] for root in roots)
            obj.record["nets"] = sorted(
                {net for row in obj.record["instances"] for net in row["nets"]}
            )
            obj.record["connection_ids"] = sorted(
                {key for row in obj.record["instances"] for key in row["connection_ids"]}
            )
