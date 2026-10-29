"""Occurrence-preserving connection witnesses for rigid saved-SCH geometry edits.

Do not use Net.pins: its physical-pad deduplication is useful for PCB export but
would merge repeated unit/library pin occurrences during schematic editing.
Only affected logical sheet instances are measured. Unchanged external sheets
are represented by their unchanged named/hierarchical interface attachments.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

from .fileformat.circuit import Design, PlacedPin, _on_segment, bus_members, graph
from .fileformat.schematic import Label, Wire, uuid_of
from .sch_geometry_contacts import ContactLimit
from .sch_geometry_contacts import capture as capture_contacts

MAX_INSTANCE_SEGMENTS = 3000
MAX_INSTANCE_PINS = 20000
MAX_PARTICIPANTS = 100000
MAX_DIFFERENCES = 100
MAX_BUS_BOUNDARIES = 10000
MAX_BUS_CONTACT_TESTS = 1000000


class GeometryGraphError(ValueError):
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = {"reason": message, **details}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


class _Scope:
    def __init__(self, design, paths):
        self.root_path = design.root_path
        self.instances = [i for i in design.instances if i.path in paths]
        self.bus_aliases = design.bus_aliases
        self.placed_pins = design.placed_pins


def _segment_contact(a, b, c, d):
    """Exact, conservative geometric contact with an unsupported bus boundary."""
    rx, ry = b[0] - a[0], b[1] - a[1]
    sx, sy = d[0] - c[0], d[1] - c[1]
    cross = rx * sy - ry * sx
    if cross:
        qx, qy = c[0] - a[0], c[1] - a[1]
        t = Fraction(qx * sy - qy * sx, cross)
        u = Fraction(qx * ry - qy * rx, cross)
        points = [(a[0] + t * rx, a[1] + t * ry)] if 0 <= t <= 1 and 0 <= u <= 1 else []
    else:
        points = sorted({p for p in (a, b, c, d) if _on_segment(p, a, b) and _on_segment(p, c, d)})
    return [
        [[Fraction(value).numerator, Fraction(value).denominator] for value in point]
        for point in points
    ]


def _bus_contacts(scope, physical, junction_points, node_info):
    """Retain fixed bus/entry/carrier contacts without claiming member routing."""
    boundaries = defaultdict(list)
    for instance in scope.instances:
        path, sch = instance.path, instance.schematic
        segment_ordinals = defaultdict(int)
        for wire in sch.wires:
            if wire.kind != "bus":
                continue
            info = node_info[(path, id(wire.node))]
            ordinal = segment_ordinals[id(wire.node)]
            segment_ordinals[id(wire.node)] += 1
            identity = _canonical(["bus", path, info["file"], info["source_ordinal"], ordinal])
            boundaries[path].append((identity, info, wire.start, wire.end))
        for entry in sch.bus_entries:
            info = node_info[(path, id(entry.node))]
            identity = _canonical(["bus_entry", path, info["file"], info["source_ordinal"]])
            boundaries[path].append((identity, info, entry.position, entry.end))
        for sheet in sch.sheets:
            for ordinal, pin in enumerate(sheet.pins):
                if bus_members(pin.name, scope.bus_aliases) is None:
                    continue
                info = dict(node_info[(path, id(sheet.node))])
                info.update(sheet_uuid=sheet.uuid, pin_name=pin.name, sheet_pin_ordinal=ordinal)
                identity = _canonical(["bus_sheet_pin", path, info["file"], sheet.uuid, ordinal])
                boundaries[path].append((identity, info, pin.position, pin.position))
    # These labels cannot be selected for transformation. Their physical
    # anchors still delimit where a selected scalar object may make contact.
    for token, row in physical.items():
        if row.get("bus_carrier"):
            item = row["item"]
            info = node_info[(row["instance_path"], id(item.node))]
            boundaries[row["instance_path"]].append((token, info, item.position, item.position))
    boundary_count = sum(len(rows) for rows in boundaries.values())
    if boundary_count > MAX_BUS_BOUNDARIES:
        raise GeometryGraphError(
            "Affected graph exceeds fixed bus boundary limit", maximum=MAX_BUS_BOUNDARIES
        )
    targets = defaultdict(list)
    for token, row in physical.items():
        item = row["item"]
        if isinstance(item, Wire):
            if item.kind != "wire":
                continue
            a, b = item.start, item.end
        else:
            a = b = row["position"]
        targets[row["instance_path"]].append((token, a, b))
    for path, token, point in junction_points:
        targets[path].append((token, point, point))
    tests = sum(len(rows) * len(targets[path]) for path, rows in boundaries.items())
    if tests > MAX_BUS_CONTACT_TESTS:
        raise GeometryGraphError(
            "Affected graph exceeds fixed bus contact comparison limit",
            maximum=MAX_BUS_CONTACT_TESTS,
            comparisons=tests,
        )
    result = {}
    for path, rows in boundaries.items():
        for identity, info, a, b in rows:
            contacts = {}
            for token, c, d in targets[path]:
                if token == identity:
                    continue
                contact = _segment_contact(a, b, c, d)
                if contact:
                    contacts[token] = contact
            result[identity] = {"identity": info, "contacts": contacts}
    return result


def witness(design: Design, *, affected_instances=None):
    """Describe identity partitions, all naming carriers and physical attachments."""
    paths = set(affected_instances or [i.path for i in design.instances])
    available = {i.path for i in design.instances}
    if not paths or paths - available:
        raise GeometryGraphError(
            "Unknown affected sheet-instance path", paths=sorted(paths - available)
        )
    scope = _Scope(design, paths)
    root_folder = Path(design.root_path).resolve().parent
    node_info, pins, all_instances = {}, {}, {}
    for instance in scope.instances:
        sch = instance.schematic
        relative = Path(os.path.relpath(Path(sch.path).resolve(), root_folder)).as_posix()
        all_instances[instance.path] = {"file": relative, "name": instance.name}
        nodes = sch.root.lists()
        for ordinal, node in enumerate(nodes):
            node_info[(instance.path, id(node))] = {
                "file": relative,
                "source_ordinal": ordinal,
                "source_kind": node.head,
                "uuid": uuid_of(node) or None,
                "instance_path": instance.path,
            }
        wires = sch.wires
        if len(wires) > MAX_INSTANCE_SEGMENTS:
            raise GeometryGraphError(
                "Affected page exceeds connection segment limit",
                instance_path=instance.path,
                maximum=MAX_INSTANCE_SEGMENTS,
            )
        for symbol in sch.symbols:
            lib = sch.library.get(symbol.library_name)
            if lib is None:
                raise GeometryGraphError(
                    "Pin geometry is unavailable on an affected page",
                    instance_path=instance.path,
                    symbol_uuid=symbol.uuid,
                    lib_id=symbol.library_name,
                )
            mirrors = symbol.node.find_all("mirror")
            if (
                len(mirrors) > 1
                or any(len(node.items) != 2 for node in mirrors)
                or symbol.mirror not in (None, "x", "y")
                or symbol.angle % 90
            ):
                raise GeometryGraphError(
                    "Pin orientation is unproved on an affected page",
                    instance_path=instance.path,
                    symbol_uuid=symbol.uuid,
                    angle=symbol.angle,
                    mirror=symbol.mirror,
                )
        try:
            placed = design.placed_pins(instance)
        except (ValueError, TypeError, KeyError) as exc:
            raise GeometryGraphError(
                "Affected pin geometry could not be measured", instance_path=instance.path
            ) from exc
        if len(placed) > MAX_INSTANCE_PINS:
            raise GeometryGraphError(
                "Affected page exceeds pin occurrence limit",
                instance_path=instance.path,
                maximum=MAX_INSTANCE_PINS,
            )
        for ordinal, pp in enumerate(placed):
            lib = sch.library[pp.symbol.library_name]
            # Object identity, not dataclass equality: identical duplicate pins
            # still occupy different original library-node ordinals.
            index = next(i for i, pin in enumerate(lib.pins) if pin is pp.pin)
            inst = pp.symbol.instance(instance.path)
            effective_unit = inst.unit if inst else pp.symbol.unit
            identity = [
                "pin",
                instance.path,
                relative,
                pp.symbol.uuid,
                effective_unit,
                pp.symbol.body_style,
                index,
            ]
            pins[("p", instance.path, ordinal)] = (identity, pp)
    try:
        found = graph(scope)
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise GeometryGraphError(
            "Affected connection graph is incomplete",
            affected_instances=sorted(paths),
            diagnostic=str(exc)[:300],
        ) from exc
    if len(found.items) > MAX_PARTICIPANTS:
        raise GeometryGraphError(
            "Affected graph exceeds participant limit", maximum=MAX_PARTICIPANTS
        )

    participants, keys, physical, node_segment = {}, {}, {}, defaultdict(int)
    for key, item in found.items.items():
        path = key[1]
        if isinstance(item, PlacedPin):
            identity, original = pins[key]
            info = dict(node_info[(path, id(item.symbol.node))])
            info.update(
                ref=item.reference,
                unit=identity[4],
                body_style=identity[5],
                pin_number=item.pin.number,
                library_pin_ordinal=identity[6],
                placed_pin_uuid=item.symbol.pin_uuids.get(item.pin.number),
                power_symbol=item.power_symbol,
            )
            position = item.position
        elif isinstance(item, (Wire, Label)):
            info = dict(node_info[(path, id(item.node))])
            segment = node_segment[(path, id(item.node))]
            node_segment[(path, id(item.node))] += 1
            identity = [
                item.kind,
                path,
                info["file"],
                info["source_ordinal"],
                segment if isinstance(item, Wire) else 0,
            ]
            position = item.position if isinstance(item, Label) else None
            if isinstance(item, Label):
                info.update(text=item.text, label_kind=item.kind)
        elif key[0] == "nc":
            instance = next(i for i in scope.instances if i.path == path)
            node = instance.schematic.root.find_all("no_connect")[key[2]]
            info = dict(node_info[(path, id(node))])
            identity = ["no_connect", path, info["file"], info["source_ordinal"]]
            position = item[0]
        elif key[0] == "s":
            sheet, pin = item
            info = dict(node_info[(path, id(sheet.node))])
            info.update(sheet_uuid=sheet.uuid, pin_name=pin.name, sheet_pin_ordinal=key[3])
            identity = ["sheet_pin", path, info["file"], sheet.uuid, key[3]]
            position = pin.position
        else:
            raise GeometryGraphError("Unsupported graph participant", key=repr(key))
        token = _canonical(identity)
        if token in participants:
            raise GeometryGraphError("Duplicate connection occurrence identity", identity=identity)
        info["identity"] = identity
        participants[token] = info
        keys[key] = token
        physical[token] = {
            "position": position,
            "item": item,
            "instance_path": path,
            "bus_carrier": isinstance(item, Label) and key not in found.net.parent,
        }

    def partition(uf):
        groups = defaultdict(list)
        for key, token in keys.items():
            groups[uf.find(key)].append(token)
        return sorted(sorted(members) for members in groups.values())

    net_groups = defaultdict(list)
    for key, token in keys.items():
        net_groups[found.net.find(key)].append(token)
    names = {}
    for root, members in net_groups.items():
        carried = sorted(
            {
                tuple(name)
                for key in found.net.parent
                if found.net.find(key) == root
                for name in found.names.get(key, [])
            }
        )
        names[_canonical(sorted(members))] = carried
    nc = {keys[key]: pp.flagged for key, pp in found.pins_at.items()}
    junctions, junction_points = {}, []
    for instance in scope.instances:
        for node in instance.schematic.root.find_all("junction"):
            info = node_info[(instance.path, id(node))]
            from .fileformat.schematic import nm

            at = node.find("at")
            point = (nm(at.atom(1)), nm(at.atom(2)))
            attached = []
            for token, row in physical.items():
                if row["instance_path"] != instance.path:
                    continue
                item = row["item"]
                if (isinstance(item, Wire) and _on_segment(point, item.start, item.end)) or row[
                    "position"
                ] == point:
                    attached.append(token)
            identity = _canonical(["junction", instance.path, info["file"], info["source_ordinal"]])
            junctions[identity] = {"identity": info, "attachments": sorted(attached)}
            junction_points.append((instance.path, identity, point))
    try:
        bus_contacts = _bus_contacts(scope, physical, junction_points, node_info)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        if isinstance(exc, GeometryGraphError):
            raise
        raise GeometryGraphError("Fixed bus boundary geometry is incomplete") from exc
    try:
        drawing_contacts = capture_contacts(physical, junction_points)
    except ContactLimit as exc:
        raise GeometryGraphError(str(exc)) from exc
    return {
        "schema": "sch-geometry-occurrence-connections/v1",
        "scope": all_instances,
        "participants": participants,
        "relations": {
            "net": partition(found.net),
            "drawn": partition(found.drawn),
            "sheet": partition(found.sheet),
        },
        "explicit_names": names,
        "nc_associations": nc,
        "junctions": junctions,
        "fixed_bus_contacts": bus_contacts,
        "drawing_contacts": drawing_contacts,
        "not_checked": [
            "Native netlist/ERC and rendered glyph/artwork readability are separate consumers.",
            "Unchanged external source pages retain bytes; interface carriers are compared "
            "on the affected pages.",
            "Bus/member routing is unsupported; fixed bus, entry and bus-label geometric "
            "contacts must remain exactly unchanged, including crossings.",
        ],
    }


def compare(before, after, catalog=None):
    """Return whole-batch differences, with bounded but explicitly counted diagnostics."""
    differences = []

    def add(kind, **detail):
        differences.append({"kind": kind, **detail})

    if before["scope"] != after["scope"]:
        add("instance_identity_changed", before=before["scope"], after=after["scope"])
    old, new = before["participants"], after["participants"]
    if set(old) != set(new):
        add(
            "pin_or_object_occurrences_changed",
            removed=[old[k] for k in old.keys() - new.keys()][:30],
            added=[new[k] for k in new.keys() - old.keys()][:30],
        )
    lookup = {}
    if catalog is not None:
        for obj in catalog.objects:
            ordinal = next(i for i, n in enumerate(obj.schematic.root.lists()) if n is obj.node)
            lookup[(obj.source_relative, ordinal)] = obj.object_id

    def report_members(tokens):
        rows = []
        for token in tokens[:30]:
            info = dict(old.get(token) or new[token])
            info["object_id"] = lookup.get((info["file"], info["source_ordinal"]))
            rows.append(info)
        return rows

    for relation in ("net", "drawn", "sheet"):
        a = {tuple(group) for group in before["relations"][relation]}
        b = {tuple(group) for group in after["relations"][relation]}
        for group in sorted(a - b):
            add(
                "connection_group_removed_or_split",
                relation=relation,
                members=report_members(group),
                member_count=len(group),
            )
        for group in sorted(b - a):
            add(
                "connection_group_added_or_joined",
                relation=relation,
                members=report_members(group),
                member_count=len(group),
            )
    if before["explicit_names"] != after["explicit_names"]:
        add(
            "explicit_network_names_or_attachment_changed",
            before_sha256=_hash(before["explicit_names"]),
            after_sha256=_hash(after["explicit_names"]),
        )
    for key in sorted(set(before["nc_associations"]) | set(after["nc_associations"])):
        a, b = before["nc_associations"].get(key), after["nc_associations"].get(key)
        if a != b:
            add("no_connect_association_changed", pin=report_members([key]), before=a, after=b)
    for key in sorted(set(before["junctions"]) | set(after["junctions"])):
        a, b = before["junctions"].get(key), after["junctions"].get(key)
        if a != b:
            add(
                "junction_attachment_changed",
                junction=(a or b)["identity"],
                before=None if a is None else report_members(a["attachments"]),
                after=None if b is None else report_members(b["attachments"]),
            )
    old_contacts = before["drawing_contacts"]["contacts"]
    new_contacts = after["drawing_contacts"]["contacts"]
    for key in sorted(set(old_contacts) | set(new_contacts)):
        a, b = old_contacts.get(key), new_contacts.get(key)
        if a != b:
            tokens = json.loads(key)
            members = []
            for token in tokens:
                if token in old or token in new:
                    members.extend(report_members([token]))
                else:
                    junction = before["junctions"].get(token) or after["junctions"][token]
                    members.append(junction["identity"])
            add(
                "drawing_contact_added"
                if a is None
                else "drawing_contact_removed"
                if b is None
                else "drawing_contact_attachment_changed",
                members=members,
                before_local_parameters=a,
                after_local_parameters=b,
            )
    old_bus, new_bus = before.get("fixed_bus_contacts", {}), after.get("fixed_bus_contacts", {})
    for key in sorted(set(old_bus) | set(new_bus)):
        a, b = old_bus.get(key), new_bus.get(key)
        if a != b:
            old_contacts, new_contacts = (
                (a or {}).get("contacts", {}),
                (b or {}).get("contacts", {}),
            )
            add(
                "unsupported_bus_contact_changed",
                boundary=(a or b)["identity"],
                before=None if a is None else dict(list(old_contacts.items())[:30]),
                after=None if b is None else dict(list(new_contacts.items())[:30]),
                before_contact_count=len(old_contacts),
                after_contact_count=len(new_contacts),
                contacts_truncated=max(len(old_contacts), len(new_contacts)) > 30,
            )
    return {
        "ok": not differences,
        "differences": differences[:MAX_DIFFERENCES],
        "difference_count": len(differences),
        "differences_truncated": len(differences) > MAX_DIFFERENCES,
        "scope": before["scope"],
        "participant_count": len(old),
        "pin_occurrence_count": len(before["nc_associations"]),
        "not_checked": before["not_checked"],
    }
