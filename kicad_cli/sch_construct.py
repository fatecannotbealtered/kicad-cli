"""Exact scalar schematic construction, prepared entirely in memory.

This is the domain primitive behind public ``sch edit`` circuit batches. It does
not save files, auto-place symbols, snap points or remove attached drawing.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from copy import deepcopy
from pathlib import Path

from . import envelope
from .fileformat.circuit import Design
from .fileformat.sexpr import List, copy, quote, render, symbol
from .sch_geometry import GeometryError, _angle
from .sch_geometry import _mm as _ticks_mm
from .sch_geometry import _pair as _ticks_pair
from .sch_geometry_graph import _segment_contact
from .sch_objects import Catalog, CatalogError
from .sch_symbols import Resources
from .sync_inputs import InputError

KINDS = {"wire", "label", "global_label", "hierarchical_label", "junction", "no_connect"}
MAX_ACTIONS = 256
MAX_POINTS = 128
MAX_SCALAR_LENGTH = 4096
REFERENCE = re.compile(r"\A#?[A-Za-z_]+[A-Za-z_-]*\d+\Z")


def _pair(value, path):
    """Library/Design/writer coordinates are nm; the public quantum is100nm."""
    return tuple(ticks * 100 for ticks in _ticks_pair(value, path))


def _mm(value):
    if value % 100:
        raise ConstructError("A generated coordinate is not on the exact100nm grid")
    return _ticks_mm(value // 100)


class ConstructError(ValueError):
    def __init__(self, message, path="/changes/0", **details):
        super().__init__(message)
        self.details = {"path": path, "problem": message, **details}


def _known(row, keys, path):
    if not isinstance(row, dict):
        raise ConstructError("The action must be an object", path)
    extra = set(row) - set(keys)
    if extra:
        key = sorted(extra)[0]
        raise ConstructError(
            "This field is outside the precise operation contract", path + "/" + key
        )


def _at(point):
    return List.new("at", _mm(point[0]), _mm(point[1]))


def _raw(document, node):
    span = document.span(node)
    return document.source[slice(*span)] if span is not None else render(node)


class Construction:
    def __init__(self, schematic, request, *, identity_root=None, resource_files=None):
        self.root = Path(schematic).resolve()
        self.identity_root = Path(identity_root or self.root).resolve()
        self.request = request
        self.before = Design(self.root)
        if self.before.missing:
            raise ConstructError(
                "The complete existing hierarchy is required", missing=self.before.missing
            )
        self.catalog = Catalog(self.before, identity_root=self.identity_root)
        wanted = {"root": self.catalog.root, "snapshot": self.catalog.snapshot}
        if request.get("snapshot") != wanted:
            raise CatalogError(
                "Circuit batch snapshot is stale or names a different root",
                code="E_CONFLICT",
                conflict="state_drift",
                expected=wanted,
            )
        self.originals = {
            path.resolve(): sch.document.source for path, sch in self.before.schematics.items()
        }
        self.working = Design(self.root, dict(self.originals))
        self.documents = {p.resolve(): sch for p, sch in self.working.schematics.items()}
        self.work_nodes = {}
        for obj in self.catalog.objects:
            original_children = obj.schematic.root.lists()
            index = next(i for i, node in enumerate(original_children) if node is obj.node)
            self.work_nodes[obj.object_id] = self.documents[
                obj.schematic.path.resolve()
            ].root.lists()[index]
        self.resource_files = resource_files
        self.resources = None
        self.aliases = {}
        self.alias_nodes = {}
        self.action_targets = {}
        self.touched = []
        self.used_targets = set()
        self.changed_files = set()
        self.report = []
        seed = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        self.namespace = uuid.uuid5(uuid.NAMESPACE_URL, "kicad-cli/sch-circuit/" + seed)
        self.used_uuids = {
            node.value(1)
            for sch in self.before.schematics.values()
            for node in sch.root.walk("uuid")
        }
        self.instances = {i.path: i for i in self.before.instances}
        from .native.sch_edit import _project

        self.project = _project(self.before)
        from .sch_hierarchy_build import Hierarchy

        self.hierarchy = Hierarchy(self)

    def uid(self, action, what):
        value = str(uuid.uuid5(self.namespace, f"{action}/{what}"))
        if value in self.used_uuids:
            raise ConstructError("Generated object identity collides with an existing identity")
        self.used_uuids.add(value)
        return value

    def page(self, path, location):
        path = self.hierarchy.instance(path, location)
        if not isinstance(path, str) or path not in self.instances:
            raise ConstructError(
                "page must name one full public instance_path",
                location,
                available=sorted(self.instances),
            )
        original = self.instances[path].schematic
        return self.documents[original.path.resolve()]

    def _resources(self):
        if self.resources is None:
            if self.identity_root != self.root and self.resource_files is None:
                raise ConstructError(
                    "Candidate symbol placement requires the closed captured resource inventory"
                )
            self.resources = Resources(self.identity_root, captured_files=self.resource_files)
        return self.resources

    def _instance_paths(self, sch):
        return sorted(
            i.path
            for i in self.instances.values()
            if i.schematic.path.resolve() == sch.path.resolve()
        )

    def _relative(self, sch):
        try:
            return Path(os.path.relpath(sch.path.resolve(), self.root.parent)).as_posix()
        except ValueError as exc:
            raise ConstructError(
                "A source on a different filesystem drive cannot be represented in this batch",
                source_file=str(sch.path),
            ) from exc

    def _target(self, selector, path):
        obj = self.catalog.resolve(selector)
        if not obj.record.get("uuid"):
            raise ConstructError(
                "Precise object update/delete currently requires a saved UUID; "
                "snapshot query remains available",
                path,
            )
        if obj.object_id in self.used_targets:
            raise ConstructError(
                "One original object may be modified or deleted only once in a circuit batch", path
            )
        if obj.kind not in KINDS | {"symbol"}:
            raise ConstructError(
                "This object kind is outside scalar circuit editing", path, kind=obj.kind
            )
        if not obj.record["editable"]:
            raise ConstructError(
                "The selected object's geometry or connectivity is incomplete",
                path,
                reasons=obj.record["uneditable_reasons"],
            )
        self.used_targets.add(obj.object_id)
        self.touched.append(obj)
        return obj, self.documents[obj.schematic.path.resolve()], self.work_nodes[obj.object_id]

    def _alias(self, action, sch, kind, nodes, index):
        name = action.get("id")
        target = {
            "kind": kind,
            "source_relative": self._relative(sch),
            "uuids": [node.find("uuid").value(1) for node in nodes],
            "action_index": index,
        }
        self.action_targets[index] = target
        if name is not None:
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", name):
                raise ConstructError(
                    "Batch id must be a short stable ASCII identifier",
                    f"/changes/0/actions/{index}/id",
                )
            if name in self.aliases:
                raise ConstructError(
                    "Batch object id is duplicated", f"/changes/0/actions/{index}/id", id=name
                )
            self.aliases[name] = target
            self.alias_nodes[name] = (sch, nodes)
        return target

    def _point(self, value, sch, path):
        if isinstance(value, list):
            return _pair(value, path)
        if not isinstance(value, dict):
            raise ConstructError(
                "A point is an exact mm pair or an explicit public pin/object reference", path
            )
        if set(value) == {"sheet_pin"}:
            anchor = value["sheet_pin"]
            _known(anchor, {"selector", "new", "instance_path", "sheet_pin_ordinal"}, path)
            if ("selector" in anchor) == ("new" in anchor):
                raise ConstructError("sheet_pin requires one selector or earlier sheet id", path)
            source, nodes = self._anchor_nodes(anchor, path)
            instance = self.hierarchy.instance(anchor.get("instance_path"), path)
            ordinal = anchor.get("sheet_pin_ordinal")
            if source.path.resolve() != sch.path.resolve() or instance not in self._instance_paths(
                source
            ):
                raise ConstructError(
                    "Sheet pin must occur on this source page and parent instance", path
                )
            if (
                len(nodes) != 1
                or nodes[0].head != "sheet"
                or type(ordinal) is not int
                or not 0 <= ordinal < len(nodes[0].find_all("pin"))
            ):
                raise ConstructError("Sheet pin ordinal does not exist", path)
            at = nodes[0].find_all("pin")[ordinal].find("at")
            return _pair([at.atom(1), at.atom(2)], path)
        if set(value) == {"pin"}:
            anchor = value["pin"]
            if not isinstance(anchor, dict):
                raise ConstructError("pin must be an occurrence reference object", path)
            _known(anchor, {"selector", "new", "instance_path", "pin_ordinal"}, path + "/pin")
            if ("selector" in anchor) == ("new" in anchor):
                raise ConstructError("pin uses exactly one selector or earlier batch id", path)
            instance = self.hierarchy.instance(anchor.get("instance_path"), path)
            ordinal = anchor.get("pin_ordinal")
            if type(ordinal) is not int or ordinal < 0:
                raise ConstructError("pin_ordinal must be the public raw library ordinal", path)
            source, nodes = self._anchor_nodes(anchor, path)
            if source.path.resolve() != sch.path.resolve() or instance not in self._instance_paths(
                source
            ):
                raise ConstructError(
                    "A point reference must lie on the selected source page and its real instance",
                    path,
                )
            if len(nodes) != 1 or nodes[0].head != "symbol":
                raise ConstructError(
                    "The pin occurrence must identify exactly one symbol unit", path
                )
            placed = self._symbol(source, nodes[0])
            occurrence = placed.instance(instance)
            effective_unit = occurrence.unit if occurrence is not None else placed.unit
            lib = source.library.get(placed.library_name)
            if lib is None or ordinal >= len(lib.pins):
                raise ConstructError("The referenced raw pin ordinal does not exist", path)
            pin = lib.pins[ordinal]
            if pin.unit not in (0, effective_unit) or pin.body_style not in (0, placed.body_style):
                raise ConstructError("The raw pin belongs to another unit or body style", path)
            position = placed.transform(pin.position)
            if any(value % 100 for value in position):
                raise ConstructError(
                    "The referenced pin is outside the supported exact100nm coordinate grid", path
                )
            return position
        if "object" in value:
            _known(value, {"object", "endpoint", "instance_path"}, path)
            target = value["object"]
            anchor = (
                {"new": target["new"]}
                if isinstance(target, dict) and set(target) == {"new"}
                else {"selector": target}
            )
            source, nodes = self._anchor_nodes(anchor, path)
            if source.path.resolve() != sch.path.resolve() or self.hierarchy.instance(
                value.get("instance_path"), path
            ) not in self._instance_paths(source):
                raise ConstructError(
                    "Object endpoint must lie on the selected source page and instance", path
                )
            endpoint = value.get("endpoint")
            if type(endpoint) is not int or endpoint not in (0, 1):
                raise ConstructError("endpoint is0 or1 for a wire,0 for a point object", path)
            if all(node.head == "wire" for node in nodes):
                node = nodes[0] if endpoint == 0 else nodes[-1]
                xy = node.find("pts").find_all("xy")[endpoint]
                return _pair([xy.atom(1), xy.atom(2)], path)
            if len(nodes) != 1 or nodes[0].head not in KINDS - {"wire"} or endpoint != 0:
                raise ConstructError("The object has no supported endpoint", path)
            at = nodes[0].find("at")
            return _pair([at.atom(1), at.atom(2)], path)
        raise ConstructError("Unknown explicit point-reference shape", path)

    def _anchor_nodes(self, anchor, path):
        if "new" in anchor:
            if not isinstance(anchor["new"], str) or anchor["new"] not in self.alias_nodes:
                raise ConstructError(
                    "Batch reference must name an earlier successful action",
                    path,
                    new=anchor["new"],
                )
            return self.alias_nodes[anchor["new"]]
        try:
            obj = self.catalog.resolve(anchor.get("selector"))
        except CatalogError:
            return self.hierarchy.imported_target(anchor.get("selector"), path)
        if not obj.record["editable"] and obj.kind != "sheet":
            raise ConstructError(
                "The referenced source object's geometry or connectivity is incomplete",
                path,
                reasons=obj.record["uneditable_reasons"],
            )
        node = self.work_nodes[obj.object_id]
        if node.parent is None:
            raise ConstructError("A reference names an object deleted earlier in the batch", path)
        return self.documents[obj.schematic.path.resolve()], [node]

    @staticmethod
    def _symbol(sch, node):
        # Schematic properties are deliberately reparsed after each in-memory edit.
        return next(s for s in sch.symbols if s.node is node)

    def _scalar(self, text, path):
        from .fileformat.circuit import _VECTOR, _group

        if (
            not isinstance(text, str)
            or not text
            or len(text) > MAX_SCALAR_LENGTH
            or any(c in text for c in "\0\r\n")
            # Recognize syntax only: calling bus_members here would expand a
            # vector such as D[0..999999999999] before it could be rejected.
            or _VECTOR.fullmatch(text) is not None
            or _group(text) is not None
            or text in self.before.bus_aliases()
        ):
            raise ConstructError(
                "A label requires one nonempty scalar name, not a bus expression", path
            )
        return text

    def _wire_nodes(self, action, sch, index, old=None):
        from .native.sch_draw import wire

        path = f"/changes/0/actions/{index}/points"
        points = action.get("points")
        if not isinstance(points, list) or not 2 <= len(points) <= MAX_POINTS:
            raise ConstructError("wire points requires2..128 explicit points", path)
        points = [self._point(p, sch, path + f"/{i}") for i, p in enumerate(points)]
        existing = []
        for item in sch.root.find_all("wire"):
            if item is old:
                continue
            old_points = item.find("pts").find_all("xy")
            existing.extend(
                (_pair([a.atom(1), a.atom(2)], path), _pair([b.atom(1), b.atom(2)], path))
                for a, b in zip(old_points, old_points[1:], strict=False)
            )
        if len(existing) + len(points) - 1 > 3000:
            raise ConstructError(
                "The affected page exceeds the3000-segment construction bound", path
            )
        segments = set()
        earlier_segments = []
        nodes = []
        for i, (a, b) in enumerate(zip(points, points[1:], strict=False)):
            if a == b:
                raise ConstructError(
                    "A wire segment must have nonzero exact length", path + f"/{i + 1}"
                )
            key = tuple(sorted((a, b)))
            if key in segments:
                raise ConstructError(
                    "A polyline must not contain duplicate or reversed-overlap segments", path
                )
            segments.add(key)
            for j, (c, d) in enumerate(earlier_segments[:-1]):
                if _segment_contact(a, b, c, d):
                    raise ConstructError(
                        "Non-adjacent polyline segments must not touch; use separate explicit "
                        "wire actions and earlier contact targets for intentional contacts",
                        path + f"/{i + 1}",
                        segment_indices=[j, i],
                    )
            for c, d in existing:
                dx, dy = b[0] - a[0], b[1] - a[1]
                if dx * (c[1] - a[1]) != dy * (c[0] - a[0]) or dx * (d[1] - a[1]) != dy * (
                    d[0] - a[0]
                ):
                    continue
                axis = 0 if dx else 1
                if min(max(a[axis], b[axis]), max(c[axis], d[axis])) > max(
                    min(a[axis], b[axis]), min(c[axis], d[axis])
                ):
                    raise ConstructError(
                        "Positive-length collinear overlap is unsupported; "
                        "split or replace explicitly",
                        path,
                    )
            existing.append((a, b))
            earlier_segments.append((a, b))
            uid = (
                old.find("uuid").value(1)
                if old is not None and i == 0
                else self.uid(index, f"wire/{i}")
            )
            node = wire(a, b, uid)
            if old is not None and i == 0:
                for part in old.lists():
                    if part.head not in {"pts", "uuid"}:
                        fresh = node.find(part.head)
                        if fresh is not None:
                            node.replace(fresh, copy(part))
                        else:
                            node.append(copy(part))
            nodes.append(node)
        return nodes

    def place(self, action, index):
        from .native.sch_create import _property, _symbol_node

        path = f"/changes/0/actions/{index}"
        _known(
            action,
            {
                "op",
                "id",
                "page",
                "symbol",
                "unit",
                "ref",
                "references",
                "value",
                "at_mm",
                "angle_deg",
                "fields",
                "contact_targets",
            },
            path,
        )
        sch = self.page(action.get("page"), path + "/page")
        action = deepcopy(action)
        action["page"] = self.hierarchy.instance(action["page"], path + "/page")
        if isinstance(action.get("references"), list):
            normalized = {}
            for row in action["references"]:
                _known(row, {"instance", "ref"}, path + "/references")
                instance = self.hierarchy.instance(row.get("instance"), path + "/references")
                if instance in normalized:
                    raise ConstructError(
                        "Duplicate source occurrence in references", path + "/references"
                    )
                normalized[instance] = row.get("ref")
            action["references"] = normalized
        try:
            resolved, resource = self._resources().resolve(action.get("symbol"), embedded=sch)
        except InputError as exc:
            raise ConstructError(str(exc), path + "/symbol", resource_details=exc.details) from exc
        unit = action.get("unit")
        if (
            type(unit) is not int
            or unit < 1
            or unit > resolved.symbol.unit_count
            or not resolved.symbol.pins_of(unit, 1)
        ):
            raise ConstructError(
                "unit must name a pin-bearing body-style1 unit of this symbol", path + "/unit"
            )
        if any(value % 100 for pin in resolved.symbol.pins_of(unit, 1) for value in pin.position):
            raise ConstructError(
                "The selected unit has pin geometry outside the exact100nm coordinate grid",
                path + "/symbol",
            )
        at = _pair(action.get("at_mm"), path + "/at_mm")
        angle = _angle(action.get("angle_deg", 0), path + "/angle_deg")
        instances = self._instance_paths(sch)
        references = action.get("references")
        if references is None:
            if len(instances) != 1:
                raise ConstructError(
                    "Shared source placement requires an exact references map for every instance",
                    path + "/references",
                    expected_instances=instances,
                )
            references = {instances[0]: action.get("ref")}
        elif "ref" in action:
            raise ConstructError("Use ref or references, not both", path + "/ref")
        if (
            not isinstance(references, dict)
            or sorted(references) != instances
            or any(
                not isinstance(v, str) or not REFERENCE.fullmatch(v) for v in references.values()
            )
        ):
            raise ConstructError(
                "references must map every source instance to an explicit annotated reference",
                path + "/references",
                expected_instances=instances,
            )
        if len(set(references.values())) != len(references):
            raise ConstructError(
                "Different shared instances require distinct physical references",
                path + "/references",
            )
        value = action.get("value", resolved.symbol.properties.get("Value", ""))
        if not isinstance(value, str):
            raise ConstructError("value must be a string", path + "/value")
        for current in self.working.instances:
            for placed in current.schematic.symbols:
                inst = placed.instance(current.path)
                ref = inst.reference if inst else placed.properties.get("Reference")
                effective_unit = inst.unit if inst else placed.unit
                if ref in references.values():
                    if effective_unit == unit:
                        raise ConstructError(
                            "This reference/unit is already placed",
                            path + "/ref",
                            ref=ref,
                            unit=unit,
                        )
                    if placed.lib_id != resolved.lib_id or placed.properties.get("Value") != value:
                        raise ConstructError(
                            "Units sharing a physical reference require the same library and value",
                            path + "/ref",
                            ref=ref,
                        )
        uid = self.uid(index, "symbol")

        # Template pin IDs are discarded below. A repeated physical number
        # must not trigger a collision before its raw ordinal is assigned.
        def ids(*parts):
            return (
                uid
                if parts[0] == "symbol"
                else str(uuid.uuid5(self.namespace, "template/" + "/".join(map(str, parts))))
            )

        ref = references[action["page"]]
        node = _symbol_node(
            resolved,
            ref,
            value,
            resolved.symbol.properties.get("Footprint", ""),
            at,
            angle,
            unit,
            ids,
            "",
            self.project,
            (at[0], at[1] - 3_810_000),
            (at[0], at[1] + 3_810_000),
        )
        for pin in node.find_all("pin"):
            node.remove(pin)
        for ordinal, pin in enumerate(resolved.symbol.pins):
            if pin.unit in (0, unit) and pin.body_style in (0, 1):
                node.append(
                    List.new(
                        "pin",
                        quote(pin.number),
                        List.new("uuid", quote(self.uid(index, f"pin-ordinal/{ordinal}"))),
                    )
                )
        node.replace(
            node.find("instances"),
            List.new(
                "instances",
                List.new(
                    "project",
                    quote(self.project),
                    *[
                        List.new(
                            "path",
                            quote(p),
                            List.new("reference", quote(references[p])),
                            List.new("unit", str(unit)),
                        )
                        for p in instances
                    ],
                ),
            ),
        )
        fields = action.get("fields", {})
        _known(fields, {"Reference", "Value"}, path + "/fields")
        for name, field in fields.items():
            _known(field, {"at_mm", "angle_deg"}, path + "/fields/" + name)
            position = _pair(field.get("at_mm"), path + "/fields/" + name + "/at_mm")
            field_angle = field.get("angle_deg", 0)
            if type(field_angle) is not int or field_angle not in (0, 90):
                raise ConstructError(
                    "Visible field angle must be0 or90 degrees", path + "/fields/" + name
                )
            old = next(p for p in node.find_all("property") if p.value(1) == name)
            fresh = _property(name, ref if name == "Reference" else value, position)
            fresh.find("at").set(3, str(field_angle))
            node.replace(old, fresh)
        # Defaults are page-local horizontal text, independent of symbol angle.
        for prop in node.find_all("property"):
            if prop.value(1) in {"Reference", "Value"} and prop.value(1) not in fields:
                prop.find("at").set(3, "0")
        table = sch.root.find("lib_symbols")
        if table is None:
            table = List.new("lib_symbols")
            first_drawing = next(
                (n for n in sch.root.lists() if n.head in KINDS | {"symbol", "sheet"}), None
            )
            sch.root.insert(
                sch.root.items.index(first_drawing)
                if first_drawing is not None
                else len(sch.root.items),
                table,
            )
        if resolved.lib_id not in sch.library:
            table.append(copy(resolved.node))
        sch.root.append(node)
        sch._library = None
        sch._symbols = None
        self.changed_files.add(sch.path.resolve())
        target = self._alias(action, sch, "symbol", [node], index)
        self.report.append(
            {
                "index": index,
                "op": "place_symbol",
                **target,
                "references": references,
                "symbol": resolved.lib_id,
                "unit": unit,
                "at_mm": [_mm(x) for x in at],
                "angle_deg": angle,
                "resource": resource,
                "fields": [
                    {
                        "name": p.value(1),
                        "text": p.value(2),
                        "at_mm": [p.find("at").atom(1), p.find("at").atom(2)],
                        "angle_deg": int(p.find("at").atom(3) or 0),
                    }
                    for p in node.find_all("property")
                ],
                "layout": "Explicit geometry; no automatic layout, glyph overlap "
                "or page fit approval.",
            }
        )

    def scalar(self, action, index):
        from .native.sch_draw import label, no_connect

        path = f"/changes/0/actions/{index}"
        update = action["op"] == "update"
        if update:
            _known(
                action,
                {
                    "op",
                    "id",
                    "target",
                    "points",
                    "at",
                    "text",
                    "angle_deg",
                    "shape",
                    "contact_targets",
                },
                path,
            )
            obj, sch, old = self._target(action.get("target"), path + "/target")
            kind = obj.kind
            if kind == "symbol":
                raise ConstructError(
                    "Use transform for existing symbol geometry; circuit update "
                    "edits scalar connection objects",
                    path,
                )
        else:
            _known(
                action,
                {
                    "op",
                    "id",
                    "page",
                    "kind",
                    "points",
                    "at",
                    "text",
                    "angle_deg",
                    "shape",
                    "contact_targets",
                },
                path,
            )
            sch = self.page(action.get("page"), path + "/page")
            old = None
            kind = action.get("kind")
            if kind not in KINDS:
                raise ConstructError(
                    "Only scalar wire/label/junction/no_connect objects may be created",
                    path + "/kind",
                )
        if kind == "hierarchical_label":
            self.hierarchy.interface_sources.add(sch.path.resolve())
        if kind == "wire":
            if any(k in action for k in ("at", "text", "angle_deg", "shape")):
                raise ConstructError("wire uses points, not point/label fields", path)
            nodes = self._wire_nodes(action, sch, index, old)
        else:
            if "points" in action:
                raise ConstructError("A point object uses at, not points", path + "/points")
            if "at" in action:
                point = self._point(action["at"], sch, path + "/at")
            elif old is not None:
                at = old.find("at")
                point = _pair([at.atom(1), at.atom(2)], path)
            else:
                raise ConstructError("The point object requires an exact at position", path + "/at")
            uid = old.find("uuid").value(1) if old is not None else self.uid(index, kind)
            if kind in {"label", "global_label", "hierarchical_label"}:
                text = self._scalar(
                    action.get("text", old.value(1) if old is not None else None), path + "/text"
                )
                angle = _angle(
                    action.get(
                        "angle_deg",
                        int(float(old.find("at").atom(3) or 0)) if old is not None else 0,
                    ),
                    path + "/angle_deg",
                )
                shape = action.get(
                    "shape",
                    old.find("shape").atom(1)
                    if old is not None and old.find("shape") is not None
                    else "bidirectional",
                )
                if shape not in {"input", "output", "bidirectional", "tri_state", "passive"}:
                    raise ConstructError("Unsupported scalar label shape", path + "/shape")
                node = label(
                    kind,
                    text,
                    point,
                    {0: (1, 0), 90: (0, -1), 180: (-1, 0), 270: (0, 1)}[angle],
                    uid,
                    shape,
                )
                if old is not None:
                    # Preserve field/font/visibility/electrical attributes of an existing label.
                    node = copy(old)
                    node.set(1, quote(text))
                    replacement = List.new("at", _mm(point[0]), _mm(point[1]), str(angle))
                    node.replace(node.find("at"), replacement)
                    if "shape" in action:
                        if kind == "label":
                            raise ConstructError(
                                "Local label has no electrical shape", path + "/shape"
                            )
                        node.replace(node.find("shape"), List.new("shape", symbol(shape)))
            else:
                if any(k in action for k in ("text", "angle_deg", "shape")):
                    raise ConstructError("junction/NC accepts only at geometry", path)
                node = (
                    no_connect(point, uid)
                    if kind == "no_connect"
                    else List.new(
                        "junction",
                        _at(point),
                        List.new("diameter", "0"),
                        List.new("color", "0", "0", "0", "0"),
                        List.new("uuid", quote(uid)),
                    )
                )
                if old is not None:
                    node = copy(old)
                    node.replace(node.find("at"), _at(point))
            nodes = [node]
        if old is not None:
            sch.root.replace(old, nodes[0])
            # Point references to an updated original object see the new actual geometry.
            self.work_nodes[obj.object_id] = nodes[0]
            for node in nodes[1:]:
                sch.root.append(node)
        else:
            for node in nodes:
                sch.root.append(node)
        self.changed_files.add(sch.path.resolve())
        target = self._alias(action, sch, kind, nodes, index)
        self.report.append(
            {
                "index": index,
                "op": action["op"],
                **target,
                "geometry": [_raw(sch.document, node) for node in nodes],
                "wire_identity": "First updated segment retains UUID and attributes; "
                "additional segments receive new UUIDs and default stroke."
                if kind == "wire"
                else None,
            }
        )

    def delete(self, action, index):
        path = f"/changes/0/actions/{index}"
        _known(action, {"op", "target", "contact_targets"}, path)
        obj, sch, node = self._target(action.get("target"), path + "/target")
        target = {
            "kind": obj.kind,
            "source_relative": obj.source_relative,
            "uuids": [obj.record["uuid"]],
            "action_index": index,
        }
        self.action_targets[index] = target
        sch.root.remove(node)
        if obj.kind == "hierarchical_label":
            self.hierarchy.interface_sources.add(sch.path.resolve())
        sch._symbols = None
        self.changed_files.add(sch.path.resolve())
        self.report.append(
            {"index": index, "op": "delete", **target, "attached_objects_implicitly_deleted": []}
        )

    def build(self):
        actions = self.request.get("actions")
        if not isinstance(actions, list) or not 1 <= len(actions) <= MAX_ACTIONS:
            raise ConstructError(
                "Circuit batch requires1..256 explicit actions", "/changes/0/actions"
            )
        acknowledged = self.request.get("affected_instances")
        if not isinstance(acknowledged, list) or not all(
            isinstance(p, (str, dict)) for p in acknowledged
        ):
            raise ConstructError(
                "affected_instances must contain distinct paths "
                "or explicit batch instance references",
                "/changes/0/affected_instances",
            )
        for index, action in enumerate(actions):
            if not isinstance(action, dict):
                raise ConstructError(
                    "Each circuit action must be an object", f"/changes/0/actions/{index}"
                )
            op = action.get("op")
            contacts = action.get("contact_targets", [])
            if not isinstance(contacts, list):
                raise ConstructError(
                    "contact_targets must be an explicit anchor list",
                    f"/changes/0/actions/{index}/contact_targets",
                )
            for ordinal, anchor in enumerate(contacts):
                if isinstance(anchor, dict) and "new" in anchor:
                    name = anchor["new"]
                    if not isinstance(name, str) or name not in self.aliases:
                        raise ConstructError(
                            "Contact targets may reference only earlier batch objects",
                            f"/changes/0/actions/{index}/contact_targets/{ordinal}/new",
                        )
            if (
                op in {"place_symbol", "create", "create_sheet", "reference_sheet"}
                and "id" not in action
            ):
                raise ConstructError(
                    "New objects require a stable batch id", f"/changes/0/actions/{index}/id"
                )
            if op == "place_symbol":
                self.place(action, index)
            elif op in {"create", "update"}:
                self.scalar(action, index)
            elif op == "delete":
                self.delete(action, index)
            elif op in {"create_sheet", "reference_sheet"}:
                self.hierarchy.add(action, index)
            elif op == "update_sheet":
                self.hierarchy.frame(action, index)
            elif op == "update_interface":
                self.hierarchy.interface(action, index)
            elif op == "add_sheet_pin":
                self.hierarchy.add_pin(action, index)
            else:
                raise ConstructError(
                    "Unknown circuit action operation", f"/changes/0/actions/{index}/op"
                )
        has_hierarchy = any(
            a.get("op")
            in {
                "create_sheet",
                "reference_sheet",
                "update_sheet",
                "update_interface",
                "add_sheet_pin",
            }
            for a in actions
        )
        if has_hierarchy or self.hierarchy.interface_sources:
            self.hierarchy.validate()
        normalized_request = self.hierarchy.normalize(self.request)
        acknowledged = normalized_request["affected_instances"]
        if len(set(acknowledged)) != len(acknowledged):
            raise ConstructError(
                "affected_instances must not contain duplicate logical occurrences"
            )
        written_instances = sorted(
            {
                i.path
                for i in self.instances.values()
                if i.schematic.path.resolve() in self.changed_files
            }
        )
        if not set(written_instances) <= set(acknowledged):
            raise ConstructError(
                "The batch must disclose every occurrence of each written source",
                "/changes/0/affected_instances",
                expected_instances=written_instances,
            )
        sources = {p: self.documents[p].document.dumps() for p in self.changed_files}
        hierarchy_geometry = []
        if has_hierarchy:
            from .native.sch_create_feedback import inspect_created

            for path, source in sorted(sources.items()):
                try:
                    feedback = inspect_created(path, source=source)
                except (ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
                    feedback = {
                        "geometry_check": "incomplete",
                        "status": "requires_visual_review",
                        "reason": str(exc),
                        "limit": "Candidate geometry could not be measured; "
                        "do not claim layout approval.",
                    }
                hierarchy_geometry.append(
                    {"source_relative": self._relative(self.documents[path]), **feedback}
                )
        after = Design(self.root, sources)
        after_catalog = Catalog(after, identity_root=self.identity_root)
        from .sch_circuit_guard import verify

        guard = verify(
            self.before,
            after,
            self.catalog,
            after_catalog,
            normalized_request,
            self.aliases,
            self.touched,
            action_targets=self.action_targets,
            hierarchy=self.hierarchy.metadata if has_hierarchy else None,
        )
        if self.resources is not None:
            self.resources.validate()
        before_objects = {
            (o.source_relative, o.record.get("uuid")): o.record for o in self.catalog.objects
        }
        after_objects = {
            (o.source_relative, o.record.get("uuid")): o.record for o in after_catalog.objects
        }
        object_changes = [
            {
                "action_index": index,
                "kind": target["kind"],
                "source_relative": target["source_relative"],
                "uuid": uid,
                "before": deepcopy(before_objects.get((target["source_relative"], uid))),
                "after": deepcopy(after_objects.get((target["source_relative"], uid))),
            }
            for index, group in self.action_targets.items()
            for target in group.get("objects", [group])
            for uid in target["uuids"]
        ]
        verified = {
            "circuit_expected": True,
            "expected_connections": guard["ok"],
            "protected_content": guard["verified"]["protected_content_preserved"],
            "geometry": True,
            "batch_complete": True,
            "problems": guard.get("differences", []),
        }
        report = {
            "schematic": str(self.root),
            "changes": [
                {
                    "op": "circuit",
                    "actions": self.report,
                    "new_objects": deepcopy(self.aliases),
                    "affected_instances": acknowledged,
                    "connection_changes": guard.get("connections"),
                }
            ],
            "files": sorted(map(str, sources)),
            "inputs": {
                str(p): hashlib.sha256(s.encode()).hexdigest() for p, s in self.originals.items()
            },
            "verified": verified,
            "erc": {
                "source": "Not official native ERC; run independent KiCad ERC on "
                "the exact candidate and saved files."
            },
            "circuit": {
                "new_objects": deepcopy(self.aliases),
                "action_targets": deepcopy(self.action_targets),
                "resource_inputs": deepcopy(self.resources.manifest) if self.resources else [],
                "hierarchy": deepcopy(self.hierarchy.metadata),
                "hierarchy_geometry": hierarchy_geometry,
                "instance_tree": after_catalog.extensions.get("hierarchy")
                if hasattr(after_catalog, "extensions")
                else [
                    {
                        "instance_path": i.path,
                        "parent": i.parent.path if i.parent else None,
                        "source_relative": self._relative(i.schematic),
                        "sheet": i.name,
                    }
                    for i in after.instances
                ],
                "guard": guard,
                "object_changes": object_changes,
                "not_measured": [
                    "Rendered glyph extents, text/symbol overlaps and full-page aesthetics.",
                    "No automatic routing, snapping, library authoring, "
                    "buses or automatic module refactoring.",
                    "Multi-file saving remains per-file, not an atomic set transaction.",
                ],
            },
        }
        return report, sources


def run_construct(schematic, changes, *, identity_root=None, resource_files=None):
    """Prepare one circuit request; runtime remains the sole formal writer."""
    from .sch_circuit_guard import CircuitGuardError

    try:
        if not isinstance(changes, list) or len(changes) != 1 or changes[0].get("op") != "circuit":
            raise ConstructError("A circuit batch contains exactly one op=circuit entry")
        return Construction(
            schematic, changes[0], identity_root=identity_root, resource_files=resource_files
        ).build()
    except CatalogError as exc:
        envelope.fail(exc.code, str(exc), exc.details)
    except (ConstructError, GeometryError, CircuitGuardError, InputError) as exc:
        from .sch_edit_contract import details

        problem = {"path": "/changes/0", "problem": str(exc), **exc.details}
        envelope.fail(
            "E_VALIDATION",
            "The precise circuit batch cannot be prepared; no formal input was written",
            {
                **details([problem]),
                **{k: v for k, v in problem.items() if k not in {"problem", "path"}},
            },
        )
    raise AssertionError("unreachable")
