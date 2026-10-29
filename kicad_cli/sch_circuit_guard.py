"""Explicit occurrence-level electrical expectations for saved-SCH construction.

This is separate from the unchanged rigid-transform guard.  It compares the
whole readable design, including remote scalar name/hierarchy edges, without
using Net.pins' physical-pin deduplication.  No writer is imported or invoked.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from copy import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .fileformat.circuit import PRIORITY, PlacedPin, bus_members, graph
from .fileformat.schematic import Label, Wire
from .fileformat.sexpr import Document, List, quote
from .sch_geometry_contacts import ContactLimit, capture
from .sch_geometry_graph import _segment_contact
from .sch_objects import EDITABLE_KINDS, KINDS

MAX_PARTICIPANTS = 100_000
MAX_SEGMENTS_PER_INSTANCE = 3_000
MAX_PINS_PER_INSTANCE = 20_000
MAX_QUERY_LIMIT = 500
MAX_DECLARED_COMPONENTS = 5_000
MAX_DIAGNOSTICS = 100

_SELECTOR = {
    "type": "object",
    "required": ["root", "snapshot", "object_id"],
    "properties": {
        "root": {"type": "string", "minLength": 1},
        "snapshot": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "object_id": {"type": "string", "pattern": "^schobj:[0-9a-f]{64}$"},
    },
    "additionalProperties": False,
}
_ANCHOR_PROPERTIES = {
    "instance_path": {"type": "string", "minLength": 1},
    "pin_ordinal": {"type": "integer", "minimum": 0},
    "sheet_pin_ordinal": {"type": "integer", "minimum": 0},
}
ANCHOR_SCHEMA = {
    "oneOf": [
        {
            "type": "object",
            "required": ["selector", "instance_path"],
            "properties": {"selector": _SELECTOR, **_ANCHOR_PROPERTIES},
            "additionalProperties": False,
        },
        {
            "type": "object",
            "required": ["new", "instance_path"],
            "properties": {
                "new": {"type": "string", "minLength": 1, "maxLength": 100},
                **_ANCHOR_PROPERTIES,
            },
            "additionalProperties": False,
        },
    ]
}
NAME_SCHEMA = {
    "type": "object",
    "required": ["kind", "name"],
    "properties": {
        "kind": {
            "type": "string",
            "enum": [
                "label",
                "global_label",
                "hierarchical_label",
                "sheet_pin",
                "power",
                "local_power",
            ],
        },
        "name": {"type": "string", "minLength": 1},
    },
    "additionalProperties": False,
}
COMPONENT_SCHEMA = {
    "type": "object",
    "required": ["members", "explicit_names", "nc_members"],
    "properties": {
        "members": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_PARTICIPANTS,
            "items": ANCHOR_SCHEMA,
            "uniqueItems": True,
        },
        "explicit_names": {
            "type": "array",
            "maxItems": MAX_PARTICIPANTS,
            "items": NAME_SCHEMA,
            "uniqueItems": True,
        },
        "nc_members": {
            "type": "array",
            "maxItems": MAX_PARTICIPANTS,
            "items": ANCHOR_SCHEMA,
            "uniqueItems": True,
        },
    },
    "additionalProperties": False,
}
EXPECTED_SCHEMA = {
    "type": "object",
    "required": ["components"],
    "properties": {
        "components": {
            "type": "array",
            "maxItems": MAX_DECLARED_COMPONENTS,
            "items": COMPONENT_SCHEMA,
            "uniqueItems": True,
        }
    },
    "additionalProperties": False,
}


class CircuitGuardError(ValueError):
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = {"reason": message, **details}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _raw(obj):
    return obj.schematic.document.source[obj.node.start : obj.node.end]


def _names(values):
    kinds = {priority: kind for kind, priority in PRIORITY.items()}
    return sorted(
        ({"kind": kinds[p], "name": name} for p, name in set(values) if p != PRIORITY["pin"]),
        key=lambda row: (row["kind"], row["name"]),
    )


@dataclass
class _Projection:
    catalog: Any

    def __post_init__(self):
        self.design = self.catalog.design
        self.objects = {}
        self.base_of = {}
        self.object_problems = defaultdict(list)
        repetitions = defaultdict(int)
        uuid_counts = defaultdict(int)
        for obj in self.catalog.objects:
            if obj.record.get("uuid"):
                uuid_counts[(obj.source_relative, obj.record["uuid"])] += 1
        for obj in self.catalog.objects:
            uid = obj.record.get("uuid")
            if uid and uuid_counts[(obj.source_relative, uid)] == 1:
                identity = [obj.source_relative, obj.kind, uid]
            else:
                fingerprint = hashlib.sha256(_raw(obj).encode()).hexdigest()
                key = (obj.source_relative, obj.kind, fingerprint)
                ordinal = repetitions[key]
                repetitions[key] += 1
                identity = [*key, ordinal]
                if uid:
                    self.object_problems[obj.object_id].append("duplicate source UUID")
            base = _canonical(identity)
            self.objects[base] = obj
            self.base_of[obj.object_id] = base
        self.unavailable = {}
        available = []
        for instance in self.design.instances:
            try:
                sch = instance.schematic
                if len(sch.wires) > MAX_SEGMENTS_PER_INSTANCE:
                    raise ValueError("page segment resource limit exceeded")
                for sym in sch.symbols:
                    if sym.library_name not in sch.library:
                        raise ValueError("missing embedded symbol geometry")
                    if sym.angle % 90 or sym.mirror not in (None, "x", "y"):
                        raise ValueError(
                            "symbol orientation is outside the proved orthogonal model"
                        )
                pins = self.design.placed_pins(instance)
                if len(pins) > MAX_PINS_PER_INSTANCE:
                    raise ValueError("page pin occurrence resource limit exceeded")
                _ = sch.labels, sch.junctions, sch.no_connects, sch.sheets, sch.bus_entries
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
                self.unavailable[instance.path] = str(exc)
            else:
                available.append(instance)
        for missing in self.design.missing:
            self.unavailable["missing:" + missing] = "missing child source"
        view = copy(self.design)
        view.instances = available
        try:
            self.found = found = graph(view)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, IndexError) as exc:
            raise CircuitGuardError(
                "Saved-SCH scalar connectivity could not be measured", diagnostic=str(exc)[:400]
            ) from exc
        if len(found.items) > MAX_PARTICIPANTS:
            raise CircuitGuardError(
                "Circuit graph participant resource limit exceeded", maximum=MAX_PARTICIPANTS
            )
        self.tokens = {}
        self.physical = {}
        self.token_by_key = {}
        self.tokens_by_object = defaultdict(list)
        self.junction_points = []
        self.bus_boundaries = []
        segments = defaultdict(int)
        instances = {i.path: i for i in available}
        for key, item in found.items.items():
            instance = instances[key[1]]
            path = instance.path
            ordinal = None
            if isinstance(item, PlacedPin):
                obj = self._object(instance, item.symbol.node)
                lib = instance.schematic.library[item.symbol.library_name]
                ordinal = next(i for i, pin in enumerate(lib.pins) if pin is item.pin)
                placed = item.symbol.instance(path)
                unit = placed.unit if placed else item.symbol.unit
                identity = [
                    "pin",
                    self.base_of[obj.object_id],
                    path,
                    unit,
                    item.symbol.body_style,
                    ordinal,
                ]
                ref = {
                    "selector": obj.record["selector"],
                    "instance_path": path,
                    "pin_ordinal": ordinal,
                }
                position = item.position
                detail = {
                    "kind": "pin",
                    "ref": item.reference,
                    "unit": unit,
                    "pin_number": item.pin.number,
                    "pin_name": item.function[0],
                    "electrical": item.function[1],
                    "lib_pin_ordinal": ordinal,
                    "body_style": item.symbol.body_style,
                    "hidden": item.pin.hidden,
                }
            elif isinstance(item, (Wire, Label)):
                obj = self._object(instance, item.node)
                segment = segments[(path, obj.object_id)]
                segments[(path, obj.object_id)] += 1
                identity = [
                    obj.kind,
                    self.base_of[obj.object_id],
                    path,
                    segment if isinstance(item, Wire) else 0,
                ]
                ref = {"selector": obj.record["selector"], "instance_path": path}
                position = None if isinstance(item, Wire) else item.position
                detail = {"kind": obj.kind}
                if isinstance(item, Wire):
                    detail.update(
                        segment_ordinal=segment, points_nm=[list(item.start), list(item.end)]
                    )
                else:
                    detail["text"] = item.text
            elif key[0] == "nc":
                node = instance.schematic.root.find_all("no_connect")[key[2]]
                obj = self._object(instance, node)
                identity = ["no_connect", self.base_of[obj.object_id], path]
                ref = {"selector": obj.record["selector"], "instance_path": path}
                position = item[0]
                detail = {"kind": "no_connect"}
            elif key[0] == "s":
                sheet, pin = item
                obj = self._object(instance, sheet.node)
                identity = ["sheet_pin", self.base_of[obj.object_id], path, key[3]]
                ref = {
                    "selector": obj.record["selector"],
                    "instance_path": path,
                    "sheet_pin_ordinal": key[3],
                }
                position = pin.position
                detail = {
                    "kind": "sheet_pin",
                    "text": pin.name,
                    "sheet_uuid": sheet.uuid,
                    "sheet_pin_ordinal": key[3],
                }
            else:
                raise CircuitGuardError("Unknown scalar graph participant", graph_key=repr(key))
            token = _canonical(identity)
            if token in self.tokens:
                raise CircuitGuardError("Duplicate raw occurrence identity", identity=identity)
            base = self.base_of[obj.object_id]
            self.tokens[token] = {
                "anchor": ref,
                "base": base,
                "instance_path": path,
                "source_relative": obj.source_relative,
                "uuid": obj.record.get("uuid"),
                **detail,
            }
            if position is not None:
                self.tokens[token]["coordinate_nm"] = list(position)
            self.token_by_key[key] = token
            self.tokens_by_object[(base, path)].append(token)
            self.physical[token] = {
                "position": position,
                "item": item,
                "instance_path": path,
                "bus_carrier": key not in found.net.parent,
            }
        for obj in self.catalog.objects:
            base = self.base_of[obj.object_id]
            for instance in obj.instances:
                if instance.path not in instances:
                    continue
                if obj.kind == "junction":
                    at = obj.record["geometry"].get("coordinate_nm")
                    if at is None:
                        raise CircuitGuardError(
                            "Junction geometry is unavailable", object_id=obj.object_id
                        )
                    token = _canonical(["junction", base, instance.path])
                    self.junction_points.append((instance.path, token, tuple(at)))
                    self.tokens[token] = {
                        "anchor": {
                            "selector": obj.record["selector"],
                            "instance_path": instance.path,
                        },
                        "base": base,
                        "instance_path": instance.path,
                        "kind": "junction",
                        "coordinate_nm": list(at),
                        "source_relative": obj.source_relative,
                        "uuid": obj.record.get("uuid"),
                    }
                    self.tokens_by_object[(base, instance.path)].append(token)
        self.groups = {}
        self.group_by_token = {}
        roots = defaultdict(list)
        for key in found.net.parent:
            roots[found.net.find(key)].append(key)
        flagged_by_root = defaultdict(list)
        for key, pp in found.pins_at.items():
            if pp.flagged:
                flagged_by_root[found.net.find(key)].append(self.token_by_key[key])
        for keys in roots.values():
            members = sorted(self.token_by_key[k] for k in keys if k in self.token_by_key)
            if not members:
                continue
            names = _names([name for k in keys for name in found.names.get(k, [])])
            nc = sorted(flagged_by_root[found.net.find(keys[0])])
            problems = []
            if any(k[0] in {"member", "b", "bl", "bp"} for k in keys):
                problems.append("component crosses unsupported bus/member topology")
            for token in members:
                obj = self.objects[self.tokens[token]["base"]]
                problems.extend(self.object_problems[obj.object_id])
                if obj.kind in EDITABLE_KINDS and not obj.record["editable"]:
                    problems.extend(obj.record["uneditable_reasons"])
            remote = any(
                n["kind"] in {"global_label", "power", "hierarchical_label", "sheet_pin"}
                for n in names
            )
            if self.unavailable and remote:
                problems.append(
                    "remote scalar effect cannot be excluded on unreadable or missing pages"
                )
            signature = _canonical([members, names, nc])
            self.groups[signature] = {
                "members": members,
                "explicit_names": names,
                "nc_members": nc,
                "supported": not problems,
                "problems": sorted(set(problems)),
            }
            for token in members:
                self.group_by_token[token] = signature
        try:
            self.contacts = capture(self.physical, self.junction_points)["contacts"]
        except ContactLimit as exc:
            raise CircuitGuardError(str(exc)) from exc
        self.fixed_bus_contacts = self._fixed_bus_contacts(available)

    def _object(self, instance, node):
        obj = self.catalog._node_objects.get((id(instance.schematic), id(node)))
        if obj is None:
            raise CircuitGuardError(
                "Graph object is not publicly discoverable",
                instance_path=instance.path,
                kind=node.head,
            )
        return obj

    def _fixed_bus_contacts(self, instances):
        boundaries = []
        aliases = self.design.bus_aliases()
        for instance in instances:
            for wire in instance.schematic.wires:
                if wire.kind == "bus":
                    obj = self._object(instance, wire.node)
                    identity = [
                        "bus",
                        self.base_of[obj.object_id],
                        instance.path,
                        list(wire.start),
                        list(wire.end),
                    ]
                    boundaries.append((_canonical(identity), instance.path, wire.start, wire.end))
            for entry in instance.schematic.bus_entries:
                obj = self._object(instance, entry.node)
                identity = ["bus_entry", self.base_of[obj.object_id], instance.path]
                boundaries.append((_canonical(identity), instance.path, entry.position, entry.end))
            for label in instance.schematic.labels:
                if bus_members(label.text, aliases) is not None:
                    obj = self._object(instance, label.node)
                    identity = ["bus_label", self.base_of[obj.object_id], instance.path]
                    boundaries.append(
                        (_canonical(identity), instance.path, label.position, label.position)
                    )
            for sheet in instance.schematic.sheets:
                obj = self._object(instance, sheet.node)
                for ordinal, pin in enumerate(sheet.pins):
                    if bus_members(pin.name, aliases) is not None:
                        identity = [
                            "bus_sheet_pin",
                            self.base_of[obj.object_id],
                            instance.path,
                            ordinal,
                        ]
                        boundaries.append(
                            (_canonical(identity), instance.path, pin.position, pin.position)
                        )
        if len(boundaries) > 10_000:
            raise CircuitGuardError("Fixed bus boundary resource limit exceeded")
        targets = []
        for token, row in self.physical.items():
            item = row["item"]
            if row["bus_carrier"] or (isinstance(item, Wire) and item.kind != "wire"):
                continue
            a, b = (
                (item.start, item.end)
                if isinstance(item, Wire)
                else (row["position"], row["position"])
            )
            targets.append((token, row["instance_path"], a, b))
        targets.extend((token, path, point, point) for path, token, point in self.junction_points)
        by_page = defaultdict(list)
        for token, path, a, b in targets:
            by_page[path].append((token, a, b))
        comparisons = sum(len(by_page[path]) for _, path, _, _ in boundaries)
        if comparisons > 1_000_000:
            raise CircuitGuardError("Fixed bus contact resource limit exceeded")
        result = {}
        for token, path, a, b in boundaries:
            contacts = {}
            for other, c, d in by_page[path]:
                if points := _segment_contact(a, b, c, d):
                    contacts[other] = points
            result[token] = contacts
        return result

    def public_component(self, group):
        # One source wire may have several graph segments; its public anchor
        # deliberately expands the complete source object, once per occurrence.
        refs = {
            _canonical(self.tokens[t]["anchor"]): self.tokens[t]["anchor"] for t in group["members"]
        }
        nc = [self.tokens[t]["anchor"] for t in group["nc_members"]]
        return {
            "members": [refs[k] for k in sorted(refs)],
            "explicit_names": group["explicit_names"],
            "nc_members": nc,
        }

    def detail(self, signature):
        group = self.groups[signature]
        return {
            "component": self.public_component(group),
            "occurrences": [self.tokens[token] for token in group["members"]],
            "supported": group["supported"],
            "problems": group["problems"],
        }


def query_connections(catalog, offset=0, limit=50):
    """Publicly copyable complete raw component expectations, never pin-deduped."""
    if (
        type(offset) is not int
        or offset < 0
        or type(limit) is not int
        or not 1 <= limit <= MAX_QUERY_LIMIT
    ):
        raise CircuitGuardError("Connection pagination requires offset>=0 and limit1..500")
    projection = _Projection(catalog)
    signatures = sorted(
        projection.groups, key=lambda signature: _digest([catalog.snapshot, signature])
    )
    rows = []
    for signature in signatures[offset : offset + limit]:
        rows.append(
            {
                "connection_id": "schcircuit:" + _digest([catalog.snapshot, signature]),
                **projection.detail(signature),
            }
        )
    more = offset + len(rows) < len(signatures)
    return {
        "root": catalog.root,
        "snapshot": catalog.snapshot,
        "items": rows,
        "offset": offset,
        "limit": limit,
        "total": len(signatures),
        "has_more": more,
        "next_offset": offset + len(rows) if more else None,
        "truncated": False,
        "connection_status": "partial"
        if projection.unavailable or any(not g["supported"] for g in projection.groups.values())
        else "complete",
        "unavailable_instances": projection.unavailable,
        "identity_policy": "Snapshot selectors identify source objects; raw pins remain separate "
        "by library ordinal, unit and logical instance.",
        "not_checked": [
            "Independent native XML/ERC and rendered page readability.",
            "Bus editing/member routing are unsupported; unchanged bus boundaries are retained.",
        ],
    }


def _alias_objects(projection, alias):
    if not isinstance(alias, dict):
        raise CircuitGuardError("A batch reference has no object identity mapping")
    uuids = alias.get("uuids")
    if not isinstance(uuids, list) or not uuids or not all(isinstance(u, str) and u for u in uuids):
        raise CircuitGuardError("A batch reference has no complete UUID list")
    result = []
    for uid in uuids:
        matches = [
            obj
            for obj in projection.objects.values()
            if obj.source_relative == alias.get("source_relative")
            and obj.kind == alias.get("kind")
            and obj.record.get("uuid") == uid
        ]
        if len(matches) != 1:
            raise CircuitGuardError("A batch object mapping is absent or ambiguous", uuid=uid)
        result.extend(matches)
    return result


def _tokens_of(projection, obj, ref, *, contacts=False):
    path = ref.get("instance_path")
    if not isinstance(path, str) or path not in {i.path for i in obj.instances}:
        raise CircuitGuardError(
            "Anchor does not name a full occurrence of this source object", anchor=ref
        )
    if path in projection.unavailable:
        raise CircuitGuardError(
            "Anchor belongs to an unreadable page",
            instance_path=path,
            diagnostic=projection.unavailable[path],
        )
    base = projection.base_of[obj.object_id]
    tokens = projection.tokens_by_object[(base, path)]
    pin, sheet_pin = ref.get("pin_ordinal"), ref.get("sheet_pin_ordinal")
    if obj.kind == "symbol":
        if type(pin) is not int or sheet_pin is not None:
            raise CircuitGuardError("A symbol anchor requires its library pin ordinal", anchor=ref)
        tokens = [t for t in tokens if projection.tokens[t].get("lib_pin_ordinal") == pin]
    elif obj.kind == "sheet":
        if type(sheet_pin) is not int or pin is not None:
            raise CircuitGuardError("A sheet anchor requires its sheet pin ordinal", anchor=ref)
        tokens = [t for t in tokens if projection.tokens[t].get("sheet_pin_ordinal") == sheet_pin]
    elif pin is not None or sheet_pin is not None:
        raise CircuitGuardError("Pin ordinals are not valid on this object kind", anchor=ref)
    if obj.kind == "junction" and not contacts:
        raise CircuitGuardError(
            "A junction is a contact target, not an electrical component member", anchor=ref
        )
    if not tokens:
        raise CircuitGuardError("Anchor has no measured occurrence", anchor=ref)
    for token in tokens:
        signature = projection.group_by_token.get(token)
        if contacts and signature is not None and not projection.groups[signature]["supported"]:
            raise CircuitGuardError(
                "Anchor belongs to an unproved component",
                anchor=ref,
                problems=projection.groups[signature]["problems"],
            )
    return tokens


def _resolve(ref, before, after, aliases, *, contacts=False, old_allowed=False):
    if not isinstance(ref, dict):
        raise CircuitGuardError("Connection anchor must be an object")
    allowed = {"selector", "new", "instance_path", "pin_ordinal", "sheet_pin_ordinal"}
    if set(ref) - allowed or ("selector" in ref) == ("new" in ref):
        raise CircuitGuardError(
            "Connection anchor requires exactly one selector or batch reference", anchor=ref
        )
    if "new" in ref:
        alias = aliases.get(ref["new"]) if isinstance(ref["new"], str) else None
        if alias is None:
            raise CircuitGuardError("Unknown batch object reference", anchor=ref)
        objects = _alias_objects(after, alias)
        return sorted(t for obj in objects for t in _tokens_of(after, obj, ref, contacts=contacts))
    obj = before.catalog.resolve(ref["selector"])
    base = before.base_of[obj.object_id]
    if base in after.objects:
        return _tokens_of(after, after.objects[base], ref, contacts=contacts)
    if old_allowed:
        return _tokens_of(before, obj, ref, contacts=contacts)
    raise CircuitGuardError("Expected final anchor was removed by this batch", anchor=ref)


def _expected(request, before, after, aliases):
    value = request.get("expected")
    if not isinstance(value, dict) or set(value) != {"components"}:
        raise CircuitGuardError(
            "Circuit edits require explicit expected.components", path="/expected"
        )
    components = value["components"]
    if not isinstance(components, list) or len(components) > MAX_DECLARED_COMPONENTS:
        raise CircuitGuardError(
            "Expected components must be a bounded list", path="/expected/components"
        )
    result = {}
    all_members = set()
    valid_kinds = set(NAME_SCHEMA["properties"]["kind"]["enum"])
    for index, component in enumerate(components):
        path = f"/expected/components/{index}"
        if not isinstance(component, dict) or set(component) != {
            "members",
            "explicit_names",
            "nc_members",
        }:
            raise CircuitGuardError(
                "An expected component requires members, explicit_names and nc_members", path=path
            )
        if not isinstance(component["members"], list) or not component["members"]:
            raise CircuitGuardError(
                "A component has at least one raw member", path=path + "/members"
            )
        members = []
        for ordinal, ref in enumerate(component["members"]):
            tokens = _resolve(ref, before, after, aliases)
            if set(tokens) & set(members):
                raise CircuitGuardError(
                    "Component anchors overlap the same raw occurrence",
                    path=path + f"/members/{ordinal}",
                )
            members.extend(tokens)
        if len(members) > MAX_PARTICIPANTS:
            raise CircuitGuardError(
                "Expected component exceeds participant resource limit", path=path
            )
        if set(members) & all_members:
            raise CircuitGuardError(
                "One raw occurrence is declared in several expected components", path=path
            )
        all_members.update(members)
        names = component["explicit_names"]
        if not isinstance(names, list) or any(
            not isinstance(n, dict)
            or set(n) != {"kind", "name"}
            or not isinstance(n["kind"], str)
            or n["kind"] not in valid_kinds
            or not isinstance(n["name"], str)
            or not n["name"]
            for n in names
        ):
            raise CircuitGuardError(
                "Explicit names require exact carrier kind and full name",
                path=path + "/explicit_names",
            )
        if len({_canonical(n) for n in names}) != len(names):
            raise CircuitGuardError(
                "Explicit name carriers are duplicated", path=path + "/explicit_names"
            )
        names = sorted(names, key=lambda n: (n["kind"], n["name"]))
        flags = component["nc_members"]
        if not isinstance(flags, list):
            raise CircuitGuardError(
                "nc_members must list exact flagged pin occurrences", path=path + "/nc_members"
            )
        nc = []
        for ref in flags:
            tokens = _resolve(ref, before, after, aliases)
            if any(after.tokens[t]["kind"] != "pin" for t in tokens):
                raise CircuitGuardError(
                    "Only pin occurrences may be declared no-connect", path=path + "/nc_members"
                )
            if set(tokens) & set(nc):
                raise CircuitGuardError(
                    "NC pin occurrences are duplicated", path=path + "/nc_members"
                )
            nc.extend(tokens)
        if not set(nc) <= set(members):
            raise CircuitGuardError(
                "NC pins must also belong to their declared component", path=path + "/nc_members"
            )
        signature = _canonical([sorted(members), names, sorted(nc)])
        if signature in result:
            raise CircuitGuardError("Expected component is duplicated", path=path)
        result[signature] = index
    return result


def _target_bases(info, before, after):
    if not isinstance(info, dict):
        return set()
    if "objects" in info:
        if set(info) != {"objects"} or not isinstance(info["objects"], list):
            raise CircuitGuardError("Multi-object action targets require an explicit objects list")
        return set().union(*(_target_bases(item, before, after) for item in info["objects"]))
    uuids = info.get("uuids", [])
    if (
        not isinstance(uuids, list)
        or not all(isinstance(u, str) for u in uuids)
        or len(uuids) != len(set(uuids))
    ):
        raise CircuitGuardError("Action target UUIDs must be a unique identity list")
    for projection in (before, after):
        for uid in uuids:
            matches = [
                obj
                for obj in projection.objects.values()
                if obj.source_relative == info.get("source_relative")
                and obj.kind == info.get("kind")
                and obj.record.get("uuid") == uid
            ]
            if len(matches) > 1:
                raise CircuitGuardError("Action target source UUID is ambiguous", uuid=uid)
    return {
        base
        for projection in (before, after)
        for base, obj in projection.objects.items()
        if obj.source_relative == info.get("source_relative")
        and obj.kind == info.get("kind")
        and obj.record.get("uuid") in info.get("uuids", [])
    }


def _point_refs(value):
    """Only endpoint-bound references carry an implicit exact-point contact grant."""
    if isinstance(value, dict):
        if "pin" in value and isinstance(value["pin"], dict):
            yield "pin", value["pin"], None
        elif "sheet_pin" in value and isinstance(value["sheet_pin"], dict):
            yield "sheet_pin", value["sheet_pin"], None
        elif "object" in value:
            yield "object", value, value.get("endpoint")
        else:
            for child in value.values():
                yield from _point_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _point_refs(child)


def _point_contact_refs(kind, value, before, after, aliases):
    if kind in {"pin", "sheet_pin"}:
        tokens = _resolve(value, before, after, aliases, contacts=True)
        return [(token, tuple(after.tokens[token]["coordinate_nm"])) for token in tokens]
    target = value.get("object")
    ref = {"instance_path": value.get("instance_path")}
    ref.update(target if isinstance(target, dict) and "new" in target else {"selector": target})
    if "new" in ref:
        alias = aliases.get(ref["new"])
        if alias is None:
            raise CircuitGuardError("Unknown endpoint batch reference", anchor=ref)
        objects = _alias_objects(after, alias)
    else:
        old = before.catalog.resolve(ref["selector"])
        base = before.base_of[old.object_id]
        if base not in after.objects:
            raise CircuitGuardError(
                "An endpoint refers to an object deleted by the batch", anchor=ref
            )
        objects = [after.objects[base]]
    endpoint = value.get("endpoint")
    if type(endpoint) is not int or endpoint not in (0, 1):
        raise CircuitGuardError("Object endpoint must be0 or1")
    obj = objects[0] if endpoint == 0 else objects[-1]
    geometry = obj.record["geometry"]
    if obj.kind == "wire":
        points = geometry.get("points_nm") or []
        if len(points) < 2:
            raise CircuitGuardError("Endpoint wire geometry is unavailable")
        point = tuple(points[0] if endpoint == 0 else points[-1])
    else:
        if endpoint != 0 or geometry.get("coordinate_nm") is None:
            raise CircuitGuardError("A point object has only endpoint0")
        point = tuple(geometry["coordinate_nm"])
    path = ref.get("instance_path")
    if path not in {i.path for i in obj.instances}:
        raise CircuitGuardError("Object endpoint requires a full valid instance path", anchor=ref)
    tokens = []
    for selected in objects:
        base = after.base_of[selected.object_id]
        for token in after.tokens_by_object[(base, path)]:
            row = after.physical.get(token)
            if row is not None and isinstance(row["item"], Wire):
                if point in (row["item"].start, row["item"].end):
                    tokens.append(token)
            elif after.tokens[token].get("coordinate_nm") == list(point):
                tokens.append(token)
    return [(token, point) for token in tokens]


def _contact_points(projection, key, values):
    """Recover exact world points solely to bind an explicit endpoint grant."""
    from fractions import Fraction

    first, _ = json.loads(key)
    if first in projection.physical:
        row = projection.physical[first]
        item = row["item"]
        a, b = (
            (item.start, item.end) if isinstance(item, Wire) else (row["position"], row["position"])
        )
    else:
        a = b = tuple(projection.tokens[first]["coordinate_nm"])
    result = []
    for parameters in values:
        p = Fraction(*parameters[0])
        result.append((a[0] + p * (b[0] - a[0]), a[1] + p * (b[1] - a[1])))
    return result


def _library_rows(design):
    result = {}
    folder = Path(design.root_path).resolve().parent
    for path, sch in design.schematics.items():
        table = sch.root.find("lib_symbols")
        if table is None:
            continue
        relative = (
            Path(path).resolve().relative_to(folder).as_posix()
            if Path(path).resolve().is_relative_to(folder)
            else str(Path(path).resolve())
        )
        counts = defaultdict(int)
        for node in table.find_all("symbol"):
            name = node.value(1)
            ordinal = counts[name]
            counts[name] += 1
            result[(relative, name, ordinal)] = sch.document.source[node.start : node.end]
    return result


def _source_headers(design):
    """Protect metadata/opaque top-level nodes, separately from editable objects."""
    return {
        str(Path(path).resolve()): [
            sch.document.source[node.start : node.end]
            for node in sch.root.lists()
            if node.head not in KINDS | {"lib_symbols"}
        ]
        for path, sch in design.schematics.items()
    }


def _structure(node):
    return [_structure(item) if isinstance(item, List) else item for item in node.items]


def _without_instances(obj):
    """Keep every non-annotation child byte-for-byte, including opaque fields."""
    return [
        obj.schematic.document.source[item.start : item.end] if isinstance(item, List) else item
        for item in obj.node.items
        if not isinstance(item, List) or item.head != "instances"
    ]


def _instance_records(obj):
    blocks = obj.node.find_all("instances")
    if len(blocks) > 1:
        raise CircuitGuardError("Duplicate source instances annotation blocks")
    if not blocks:
        return {}, []
    records = {}
    metadata = []
    project_names = set()
    for project in blocks[0].lists():
        if project.head != "project":
            raise CircuitGuardError("Unsupported source instances annotation")
        name = project.value(1)
        if name in project_names:
            raise CircuitGuardError("Duplicate source project annotation wrapper", project=name)
        project_names.add(name)
        extra = [
            _structure(item) if isinstance(item, List) else item
            for item in project.items
            if not isinstance(item, List) or item.head != "path"
        ]
        metadata.append(extra)
        for path in project.find_all("path"):
            key = (name, path.value(1))
            if key in records:
                raise CircuitGuardError("Duplicate source instance path annotation", path=key[1])
            records[key] = _structure(path)
    return records, metadata


def _annotation_preserved(old, current, hierarchy, new_instances, project):
    if old.kind not in {"symbol", "sheet"} or old.kind != current.kind:
        return False
    if _without_instances(old) != _without_instances(current):
        return False
    old_records, old_metadata = _instance_records(old)
    new_records, new_metadata = _instance_records(current)
    if any(new_records.get(key) != record for key, record in old_records.items()):
        return False
    # A new project wrapper may be needed on an unannotated imported file.
    for record in old_metadata:
        if record not in new_metadata:
            return False
    additions = {key: value for key, value in new_records.items() if key not in old_records}
    expected = {}
    rows = hierarchy.get("symbol_instances" if old.kind == "symbol" else "sheet_instances", [])
    for row in rows:
        if row.get("source_relative") != old.source_relative or row.get("uuid") != old.record.get(
            "uuid"
        ):
            continue
        path = row.get("instance_path" if old.kind == "symbol" else "parent_instance")
        # Sheet annotations name the parent; its child is the new occurrence.
        target_path = (
            path if old.kind == "symbol" else str(path) + "/" + str(old.record.get("uuid"))
        )
        if target_path not in new_instances:
            return False
        name = row.get("project", project)
        if old.kind == "symbol":
            expected[(name, path)] = [
                "path",
                quote(path),
                ["reference", quote(str(row.get("reference")))],
                ["unit", str(row.get("unit"))],
            ]
        else:
            expected[(name, path)] = ["path", quote(path), ["page", quote(str(row.get("page")))]]
    allowed_wrappers = [["project", quote(name)] for name, _ in expected]
    if any(
        record not in old_metadata and record not in allowed_wrappers for record in new_metadata
    ):
        return False
    return bool(additions) and additions == expected


def _hierarchy_permissions(before, after, metadata):
    """Authorize only the declared tree additions and exact instance annotation appends."""
    if metadata is None:
        return set(), set(), set(), set()
    if not isinstance(metadata, dict):
        raise CircuitGuardError("Hierarchy authorization metadata must be an object")
    old_paths = {item.path for item in before.design.instances}
    new_paths = {item.path for item in after.design.instances}
    added = metadata.get("new_instances", [])
    if (
        not isinstance(added, list)
        or len(added) != len(set(added))
        or set(added) != new_paths - old_paths
        or old_paths - new_paths
    ):
        raise CircuitGuardError(
            "Hierarchy new instance inventory is incomplete or changes existing identities"
        )
    added = set(added)
    old_sources = {obj.source_relative for obj in before.catalog.objects}
    # Empty pages still participate in the source inventory.
    old_sources.update(row["source_relative"] for row in before.catalog.pages)
    new_sources = {row["source_relative"] for row in after.catalog.pages} - old_sources
    declared = metadata.get("new_sources", [])
    if (
        not isinstance(declared, list)
        or len(declared) != len(set(declared))
        or set(declared) != new_sources
    ):
        raise CircuitGuardError("Hierarchy new source inventory is incomplete or inaccurate")
    annotations = set()
    for base, obj in before.objects.items():
        current = after.objects.get(base)
        if (
            current is not None
            and _raw(obj) != _raw(current)
            and _annotation_preserved(obj, current, metadata, added, after.design.project)
        ):
            annotations.add(base)
    imported = set()
    imports = metadata.get("imported_sources", {})
    if not isinstance(imports, dict) or not set(imports) <= new_sources:
        raise CircuitGuardError(
            "Imported hierarchy sources must be a declared new source inventory"
        )
    for relative, text in imports.items():
        if not isinstance(text, str):
            raise CircuitGuardError(
                "Imported hierarchy source must retain its original source bytes"
            )
        document = Document.parse(text)
        raw_objects = {}
        for node in document.root.lists():
            uid = node.find("uuid")
            if node.head in KINDS and uid is not None:
                key = (node.head, uid.value(1))
                if key in raw_objects:
                    raise CircuitGuardError(
                        "Imported hierarchy source has duplicate object identities"
                    )
                raw_objects[key] = node
        for base, obj in after.objects.items():
            if obj.source_relative != relative:
                continue
            original = raw_objects.pop((obj.kind, obj.record.get("uuid")), None)
            if original is None:
                raise CircuitGuardError(
                    "Imported hierarchy source object was not in the captured original"
                )
            old = copy(obj)
            old.node = original
            old.schematic = copy(obj.schematic)
            old.schematic.document = document
            if _raw(old) != _raw(obj) and not _annotation_preserved(
                old, obj, metadata, added, after.design.project
            ):
                raise CircuitGuardError(
                    "Imported hierarchy source changed outside exact new instance annotations"
                )
            imported.add(base)
        if raw_objects:
            raise CircuitGuardError("Imported hierarchy source lost captured objects")
        before_headers = [
            text[node.start : node.end]
            for node in document.root.lists()
            if node.head not in KINDS | {"lib_symbols"}
        ]
        absolute = (Path(after.design.root_path).resolve().parent / relative).resolve()
        source_path = next(
            path for path in after.design.schematics if Path(path).resolve() == absolute
        )
        sch = after.design.schematics[source_path]
        after_headers = [
            sch.document.source[node.start : node.end]
            for node in sch.root.lists()
            if node.head not in KINDS | {"lib_symbols"}
        ]
        if before_headers != after_headers:
            raise CircuitGuardError("Imported hierarchy source metadata changed")
        old_table = document.root.find("lib_symbols")
        new_table = sch.root.find("lib_symbols")
        for library in old_table.find_all("symbol") if old_table is not None else []:
            matches = (
                [node for node in new_table.find_all("symbol") if node.value(1) == library.value(1)]
                if new_table is not None
                else []
            )
            if (
                len(matches) != 1
                or text[library.start : library.end]
                != sch.document.source[matches[0].start : matches[0].end]
            ):
                raise CircuitGuardError("Imported embedded symbol library changed")
    return added, new_sources, annotations, imported


def verify(
    before_design,
    after_design,
    before_catalog,
    after_catalog,
    request,
    new_aliases,
    touched,
    action_targets=None,
    *,
    hierarchy=None,
):
    """Validate the entire prepared batch; return facts, never save any input."""
    if before_design is not before_catalog.design or after_design is not after_catalog.design:
        raise CircuitGuardError("Guard catalogs must describe the supplied exact designs")
    before, after = _Projection(before_catalog), _Projection(after_catalog)
    new_instances, new_sources, annotations, imported = _hierarchy_permissions(
        before, after, hierarchy
    )
    actions = request.get("actions")
    if (
        not isinstance(actions, list)
        or not actions
        or not all(isinstance(a, dict) for a in actions)
    ):
        raise CircuitGuardError("Circuit batch has at least one explicit action", path="/actions")
    aliases = new_aliases or {}
    targets = {int(index): info for index, info in (action_targets or {}).items()}
    for alias in aliases.values():
        if isinstance(alias, dict) and type(alias.get("action_index")) is int:
            targets.setdefault(alias["action_index"], alias)
    for index, action in enumerate(actions):
        if index not in targets and isinstance(action, dict) and "target" in action:
            obj = before_catalog.resolve(action["target"])
            targets[index] = {
                "kind": obj.kind,
                "source_relative": obj.source_relative,
                "uuids": [obj.record.get("uuid")],
            }
    owned = {index: _target_bases(info, before, after) for index, info in targets.items()}
    if any(index not in owned or not owned[index] for index in range(len(actions))):
        raise CircuitGuardError(
            "Every action requires a resolved target identity mapping", path="/actions"
        )
    owners = defaultdict(set)
    for index, bases in owned.items():
        for base in bases:
            owners[base].add(index)
    touched_bases = {
        before.base_of[obj.object_id if hasattr(obj, "object_id") else obj] for obj in touched
    }
    if not touched_bases <= set(owners):
        raise CircuitGuardError("An edited source object is outside the action target inventory")
    expected = _expected(request, before, after, aliases)
    differences = []

    def difference(kind, **detail):
        differences.append({"kind": kind, **detail})

    old_groups, new_groups = set(before.groups), set(after.groups)
    removed, added = sorted(old_groups - new_groups), sorted(new_groups - old_groups)
    for signature, index in expected.items():
        if signature not in after.groups:
            difference(
                "expected_component_mismatch",
                path=f"/expected/components/{index}",
                expected=request["expected"]["components"][index],
            )
        elif not after.groups[signature]["supported"]:
            difference(
                "unsupported_expected_component",
                path=f"/expected/components/{index}",
                problems=after.groups[signature]["problems"],
            )
    for signature in added:
        if signature not in expected:
            difference("undeclared_new_or_changed_component", **after.detail(signature))
    for projection, signatures, stage in [(before, removed, "before"), (after, added, "after")]:
        for signature in signatures:
            if not projection.groups[signature]["supported"]:
                difference(
                    "unproved_affected_component", stage=stage, **projection.detail(signature)
                )
    for base, obj in before.objects.items():
        if base not in owners:
            current = after.objects.get(base)
            if current is None or (_raw(obj) != _raw(current) and base not in annotations):
                difference(
                    "non_target_source_object_changed",
                    source_relative=obj.source_relative,
                    uuid=obj.record.get("uuid"),
                    object_kind=obj.kind,
                )
    for base, obj in after.objects.items():
        if base not in before.objects and base not in owners and base not in imported:
            difference(
                "unauthorized_source_object_added",
                source_relative=obj.source_relative,
                uuid=obj.record.get("uuid"),
                object_kind=obj.kind,
            )
    for token in set(before.tokens) ^ set(after.tokens):
        row = before.tokens.get(token) or after.tokens[token]
        if row["base"] not in owners and not (
            row["instance_path"] in new_instances
            and (row["base"] in before.objects or row["base"] in imported)
        ):
            difference("unauthorized_occurrence_added_or_removed", occurrence=row)
    for token in set(before.tokens) & set(after.tokens):
        if before.tokens[token]["base"] not in owners:
            a = {k: v for k, v in before.tokens[token].items() if k != "anchor"}
            b = {k: v for k, v in after.tokens[token].items() if k != "anchor"}
            if a != b:
                difference(
                    "non_target_occurrence_geometry_or_pin_definition_changed", before=a, after=b
                )
    old_libraries, new_libraries = _library_rows(before_design), _library_rows(after_design)
    for key, raw in old_libraries.items():
        if new_libraries.get(key) != raw:
            difference("existing_embedded_library_changed", source_relative=key[0], lib_id=key[1])
    old_headers, new_headers = _source_headers(before_design), _source_headers(after_design)
    if any(new_headers.get(path) != value for path, value in old_headers.items()) or (
        hierarchy is None and old_headers != new_headers
    ):
        difference("non_target_source_metadata_changed")
    if before.fixed_bus_contacts != after.fixed_bus_contacts:
        difference("unsupported_fixed_bus_contact_changed")
    before_paths = {i.path for i in before_design.instances}
    after_paths = {i.path for i in after_design.instances}
    if before_paths != after_paths and (
        hierarchy is None
        or after_paths - before_paths != new_instances
        or before_paths - after_paths
    ):
        difference("sheet_instance_identity_changed")
    required_instances = {
        path
        for base in owners
        for projection in (before, after)
        if base in projection.objects
        for path in (i.path for i in projection.objects[base].instances)
    }
    required_instances.update(new_instances)
    required_instances.update(
        instance.path
        for base in annotations
        for projection in (before, after)
        if base in projection.objects
        for instance in projection.objects[base].instances
    )
    for projection, signatures in [(before, removed), (after, added)]:
        required_instances.update(
            projection.tokens[token]["instance_path"]
            for signature in signatures
            for token in projection.groups[signature]["members"]
        )
    acknowledged = request.get("affected_instances")
    if (
        not isinstance(acknowledged, list)
        or not all(isinstance(p, str) for p in acknowledged)
        or len(acknowledged) != len(set(acknowledged))
        or set(acknowledged) != required_instances
    ):
        difference(
            "affected_instances_incomplete_or_inaccurate",
            expected_instances=sorted(required_instances),
            provided=acknowledged,
        )
    for path in required_instances:
        if path in before.unavailable or path in after.unavailable:
            difference("affected_instance_unreadable", instance_path=path)
    grants = defaultdict(list)
    for index, action in enumerate(actions):
        explicit = action.get("contact_targets", [])
        if not isinstance(explicit, list):
            raise CircuitGuardError(
                "contact_targets must be an explicit anchor list",
                path=f"/actions/{index}/contact_targets",
            )
        for ref in explicit:
            grants[index].extend(
                (token, None)
                for token in _resolve(ref, before, after, aliases, contacts=True, old_allowed=True)
            )
        for field in ("at", "points"):
            for kind, ref, _ in _point_refs(action.get(field)):
                grants[index].extend(_point_contact_refs(kind, ref, before, after, aliases))
    contact_changes = []
    for key in sorted(set(before.contacts) | set(after.contacts)):
        old, new = before.contacts.get(key), after.contacts.get(key)
        if old == new:
            continue
        left, right = json.loads(key)
        rows = [before.tokens.get(t) or after.tokens[t] for t in (left, right)]
        bases = [row["base"] for row in rows]
        allowed = bool(set(bases) & set(owners)) if new is None else False
        if (
            new is not None
            and rows[0]["instance_path"] == rows[1]["instance_path"]
            and rows[0]["instance_path"] in new_instances
            and all(
                base not in owners and (base in before.objects or base in imported)
                for base in bases
            )
        ):
            # A reused source's unchanged physical contacts gain a new logical
            # occurrence. New/edited source objects still require normal grants.
            allowed = True
        # Source-internal pin stacks and a single explicit polyline's own
        # segments are intrinsic geometry, not a blanket selected-object waiver.
        if (
            new is not None
            and bases[0] == bases[1]
            and bases[0] in owners
            and rows[0]["kind"] == rows[1]["kind"] == "pin"
        ):
            allowed = True
        if (
            new is not None
            and rows[0]["kind"] == rows[1]["kind"] == "wire"
            and owners.get(bases[0], set()) & owners.get(bases[1], set())
        ):
            # Only ordered adjacent segments at their one shared endpoint are
            # intrinsic to a polyline. A non-adjacent crossing is not a waiver.
            uuids = [after.objects[base].record.get("uuid") for base in bases]
            for index in owners[bases[0]] & owners[bases[1]]:
                ordered = targets[index].get("uuids", [])
                if not all(uid in ordered for uid in uuids):
                    continue
                positions = [ordered.index(uid) for uid in uuids]
                if abs(positions[0] - positions[1]) != 1:
                    continue
                first, second = (left, right) if positions[0] < positions[1] else (right, left)
                a, b = after.physical[first]["item"], after.physical[second]["item"]
                if a.end == b.start and _contact_points(after, key, new) == [a.end]:
                    allowed = True
        if new is not None and not allowed:
            points = _contact_points(after, key, new)
            for owner_side, other_side in ((0, 1), (1, 0)):
                for index in owners.get(bases[owner_side], set()):
                    for target, at in grants[index]:
                        if target == (right if other_side else left) and (
                            at is None or all(p == at for p in points)
                        ):
                            allowed = True
        change = {
            "before": old,
            "after": new,
            "members": [row["anchor"] for row in rows],
            "declared": allowed,
        }
        contact_changes.append(change)
        if not allowed:
            difference("undeclared_geometric_contact_change", **change)
    merges = []
    for signature in added:
        members = set(after.groups[signature]["members"])
        prior = [s for s in removed if members & set(before.groups[s]["members"])]
        if len(prior) > 1:
            merges.append(
                {
                    "before": [before.public_component(before.groups[s]) for s in prior],
                    "after": after.public_component(after.groups[signature]),
                }
            )
    splits = []
    for signature in removed:
        members = set(before.groups[signature]["members"])
        following = [s for s in added if members & set(after.groups[s]["members"])]
        if len(following) > 1:
            splits.append(
                {
                    "before": before.public_component(before.groups[signature]),
                    "after": [after.public_component(after.groups[s]) for s in following],
                }
            )
    nc_changes = []
    for token in sorted(set(before.tokens) | set(after.tokens)):
        row = before.tokens.get(token) or after.tokens[token]
        if row["kind"] != "pin":
            continue
        old = (
            token in before.tokens
            and token in before.groups[before.group_by_token[token]]["nc_members"]
        )
        new = (
            token in after.tokens
            and token in after.groups[after.group_by_token[token]]["nc_members"]
        )
        if old != new:
            nc_changes.append({"pin": row["anchor"], "before": old, "after": new})
    kinds = {row["kind"] for row in differences}
    return {
        "ok": not differences,
        "verified": {
            "expected_connections_match": not kinds
            & {
                "expected_component_mismatch",
                "undeclared_new_or_changed_component",
                "unsupported_expected_component",
                "unproved_affected_component",
            },
            "undeclared_components_preserved": "undeclared_new_or_changed_component" not in kinds,
            "protected_content_preserved": not kinds
            & {
                "non_target_source_object_changed",
                "non_target_occurrence_geometry_or_pin_definition_changed",
                "existing_embedded_library_changed",
                "non_target_source_metadata_changed",
                "unauthorized_source_object_added",
            },
            "participant_identities_authorized": "unauthorized_occurrence_added_or_removed"
            not in kinds,
            "fixed_bus_boundaries_preserved": "unsupported_fixed_bus_contact_changed" not in kinds,
            "new_contacts_declared": "undeclared_geometric_contact_change" not in kinds,
            "affected_instances_acknowledged": not kinds
            & {
                "affected_instances_incomplete_or_inaccurate",
                "affected_instance_unreadable",
                "sheet_instance_identity_changed",
            },
        },
        "differences": differences[:MAX_DIAGNOSTICS],
        "difference_count": len(differences),
        "differences_truncated": len(differences) > MAX_DIAGNOSTICS,
        "affected_instances": sorted(required_instances),
        "connections": {
            "before": [before.detail(s) for s in removed][:MAX_DIAGNOSTICS],
            "after": [after.detail(s) for s in added][:MAX_DIAGNOSTICS],
            "removed_count": len(removed),
            "added_count": len(added),
            "merges": merges[:MAX_DIAGNOSTICS],
            "splits": splits[:MAX_DIAGNOSTICS],
            "nc_changes": nc_changes[:MAX_DIAGNOSTICS],
            "changes_truncated": max(len(removed), len(added), len(nc_changes)) > MAX_DIAGNOSTICS,
            "pin_occurrences_before": sum(row["kind"] == "pin" for row in before.tokens.values()),
            "pin_occurrences_after": sum(row["kind"] == "pin" for row in after.tokens.values()),
        },
        "contacts": {
            "changes": contact_changes[:MAX_DIAGNOSTICS],
            "change_count": len(contact_changes),
            "changes_truncated": len(contact_changes) > MAX_DIAGNOSTICS,
        },
        "unavailable_instances": {"before": before.unavailable, "after": after.unavailable},
        "not_checked": [
            "Independent official KiCad XML/ERC and actual complete-page readability.",
            "Bus editing/member routing, automatic module refactoring and native glyph approval.",
            "Multiple source files still save by the existing per-file transaction contract.",
        ],
    }
