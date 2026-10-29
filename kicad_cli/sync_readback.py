"""Fresh saved-byte sync consumer, with no writer board/design/library imports.

Expected metadata comes from the schematic model; prepared geometry expectations
share this consumer. Neither is an official native or electrical acceptance.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .move_readback import Observation, ReadbackError, _Atom, _decimal, _List, _parse

VERSION = "1"
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


def _sha(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _tree(node):
    if isinstance(node, _Atom):
        # Semantic comparison permits native decimal spelling differences.
        if _NUMBER.fullmatch(node.value):
            return str(_decimal(node.value).normalize())
        return node.value
    return [_tree(item) for item in node.items]


def _identity(node, seen):
    identity = node.one("uuid").atom(1)
    try:
        uuid.UUID(identity)
    except (ValueError, AttributeError) as exc:
        raise ReadbackError("object has no valid native UUID") from exc
    if identity in seen:
        raise ReadbackError("duplicate object UUID")
    seen.add(identity)
    return identity


def _pose(node):
    at = node.one("at")
    coordinates = [_decimal(at.atom(axis)) * Decimal(1_000_000) for axis in (1, 2)]
    if any(value != value.to_integral_value() for value in coordinates):
        raise ReadbackError("position is finer than a nanometre")
    return {
        "position_nm": [int(value) for value in coordinates],
        "rotation_deg": float(_decimal(at.atom(3))) if len(at.items) > 3 else 0.0,
    }


def _net(node, names):
    if node is None:
        return ""
    if names:
        return node.atom(2) if len(node.items) > 2 else names.get(node.atom(1), "")
    return node.atom(1)


def inspect_bytes(raw, *, renamed=None):
    """Full untruncated objects, unrelated/copper semantics and board-format version."""
    root = _parse(raw.decode("utf-8"))
    version = root.one("version").atom(1)
    if not version.isdigit():
        raise ReadbackError("invalid board format version")
    names = {}
    for node in root.children("net"):
        code, name = node.atom(1), node.atom(2)
        if code in names:
            raise ReadbackError("duplicate board net code")
        names[code] = name
    if len(set(names.values())) != len(names):
        raise ReadbackError("duplicate board net name")
    seen, references, paths, objects = set(), set(), set(), []
    for footprint in root.children("footprint"):
        identity = _identity(footprint, seen)
        fields = {}
        for prop in footprint.children("property"):
            key = prop.atom(1)
            if key in fields:
                raise ReadbackError("duplicate footprint property")
            fields[key] = prop.atom(2)
        reference = fields.get("Reference")
        if not reference or reference.casefold() in references:
            raise ReadbackError("missing or duplicate footprint reference")
        references.add(reference.casefold())
        link = footprint.one("path", optional=True)
        link = "/" + "/".join(p for p in link.atom(1).split("/") if p) if link else ""
        if link and link in paths:
            raise ReadbackError("duplicate footprint schematic path")
        paths.add(link)
        attr = footprint.one("attr", optional=True)
        attrs = [item.value for item in attr.items[1:] if isinstance(item, _Atom)] if attr else []
        pads = []
        for pad in footprint.children("pad"):
            pad_id = _identity(pad, seen)
            geometry = [
                _tree(item)
                for item in pad.items
                if not isinstance(item, _List) or item.head not in {"net", "uuid"}
            ]
            pads.append(
                {
                    "id": pad_id,
                    "number": pad.atom(1),
                    "net": _net(pad.one("net", optional=True), names),
                    "kind": pad.atom(2),
                    "shape": pad.atom(3),
                    **_pose(pad),
                    "geometry_sha256": _sha(geometry),
                }
            )
        pads.sort(key=lambda item: (item["number"], item["id"]))
        geometry, property_geometry = [], {}
        for item in footprint.items:
            if isinstance(item, _List) and item.head == "property":
                # Only the value is mutable; field text pose/effects remain protected.
                property_geometry[item.atom(1)] = _sha(
                    [item.head, item.atom(1), *[_tree(part) for part in item.items[3:]]]
                )
            elif isinstance(item, _List) and item.head == "pad":
                geometry.append(
                    [
                        _tree(part)
                        for part in item.items
                        if not isinstance(part, _List) or part.head != "net"
                    ]
                )
            elif isinstance(item, _List) and item.head == "attr":
                geometry.append(
                    [
                        "attr",
                        *sorted(word for word in attrs if word not in {"dnp", "exclude_from_bom"}),
                    ]
                )
            else:
                geometry.append(_tree(item))
        objects.append(
            {
                "id": identity,
                "ref": reference,
                "path": link,
                "value": fields.get("Value", ""),
                "fpid": footprint.atom(1),
                "layer": footprint.one("layer").atom(1),
                **_pose(footprint),
                "fields": fields,
                "attributes": sorted(attrs),
                "pads": pads,
                # Physical artwork/identities, with approved metadata/net values masked.
                "geometry_sha256": _sha(geometry),
                "property_geometry_sha256": property_geometry,
                "saved_semantics_sha256": _sha(_tree(footprint)),
            }
        )
    if root.children("module"):
        raise ReadbackError("legacy module records are outside this sync consumer")
    objects.sort(key=lambda item: item["ref"])
    renamed = renamed or {}
    protected = []
    for node in root.items:
        if isinstance(node, _List) and node.head in {"footprint", "net"}:
            continue
        if isinstance(node, _List) and node.head in {"segment", "arc", "via", "zone"}:
            semantic = []
            for item in node.items:
                if isinstance(item, _List) and item.head in {"net", "net_name"}:
                    if item.head == "net_name":
                        continue
                    name = _net(item, names)
                    semantic.append(["net", renamed.get(name, name)])
                else:
                    semantic.append(_tree(item))
            protected.append(semantic)
        else:
            protected.append(_tree(node))
    return objects, _sha(protected), version


def fulfillment(objects, tasks):
    """Compare requested source metadata/pins against actual saved consumer objects."""
    actual, issues = {item["ref"]: item for item in objects}, []
    for task in tasks:
        found = actual.get(task["ref"])
        if found is None:
            issues.append({"ref": task["ref"], "criterion": "component_present"})
            continue
        fpid = task["fpid"]
        equivalent = found["fpid"] == fpid if ":" in fpid else found["fpid"].split(":")[-1] == fpid
        if not equivalent or found["value"] != task["value"] or found["path"] not in task["paths"]:
            issues.append({"ref": task["ref"], "criterion": "value_fpid_link"})
        for key, value in task["fields"].items():
            if found["fields"].get(key) != value:
                issues.append({"ref": task["ref"], "criterion": "field", "field": key})
        for key, value in task["marks"].items():
            if (key in found["attributes"]) != value:
                issues.append({"ref": task["ref"], "criterion": "mark", "mark": key})
        missing = sorted(set(task["pins"]) - {pad["number"] for pad in found["pads"]})
        wrong = [
            pad["id"] for pad in found["pads"] if pad["net"] != task["pins"].get(pad["number"], "")
        ]
        if missing or wrong:
            issues.append(
                {
                    "ref": task["ref"],
                    "criterion": "physical_pads_on_source_nets",
                    "missing_pins": missing,
                    "wrong_pad_ids": wrong,
                }
            )
    return {
        "status": "failed" if issues else "passed",
        "issues": issues,
        "source": "Actual saved lexical/Decimal consumer compared with captured schematic "
        "task requirements; custom schematic/netlist expectation model, not native parity.",
    }


def observe(path):
    """Read actual bytes now. Actual fields never come from planner arguments."""
    path = Path(path).resolve()
    raw = path.read_bytes()
    objects, protected, version = inspect_bytes(raw)
    revision = hashlib.sha256(raw).hexdigest()
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    code = Path(__file__)
    source = {
        "kind": "independent_consumer",
        "name": "kicad_cli.sync_readback",
        "version": VERSION,
        "completed": True,
        "input_sha256": revision,
        "format_version": version,
        "observed_at": now,
        "code_sha256": hashlib.sha256(code.read_bytes()).hexdigest() if code.is_file() else None,
        "limits": "Independent saved lexical/Decimal metadata, pad/net and semantics consumer. "
        "Geometry expectations share this consumer/prepared serialization; no native DRC, "
        "schematic parity, electrical connectivity or manufacturing acceptance.",
    }
    snapshot = {
        "id": "disk:" + revision,
        "source": "disk",
        "revision": revision,
        "observed_at": now,
        "dependencies": [],
        "extensions": {"path": str(path), "board_format_version": version},
    }
    return Observation(snapshot, objects, protected, source)
