"""Fresh saved-byte zone consumer; independent of writer model and filler.

Exact fill representation is observed, not certified as geometrically correct.
Definition/preservation expectations share this consumer before and after.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .move_readback import Observation, ReadbackError, _Atom, _decimal, _List, _parse

VERSION = "1"


def _tree(node):
    return [_tree(item) for item in node.items] if isinstance(node, _List) else node.value


def _hash(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _nm(text):
    value = _decimal(text) * 1_000_000
    if value != value.to_integral_value():
        raise ReadbackError("zone coordinate finer than the nanometre grid")
    return int(value)


def inspect_bytes(raw: bytes, selected: list[str], *, allow_missing=False):
    text = raw.decode("utf-8")
    root = _parse(text)
    version = root.one("version").atom(1)
    if not version.isdigit():
        raise ReadbackError("invalid board format version")
    nets = {node.atom(1): node.atom(2) for node in root.children("net")}
    zones, objects = {}, []
    for node in root.children("zone"):
        identity = node.one("uuid").atom(1)
        if not identity or identity in zones:
            raise ReadbackError("zone UUIDs must be present and unique")
        zones[identity] = node
    for identity in sorted(selected):
        node = zones.get(identity)
        if node is None:
            if allow_missing:
                continue
            raise ReadbackError("selected zone is absent: " + identity)
        net_node = node.one("net")
        net = net_node.atom(1)
        if not re.match(r'^\(net\s+"', text[net_node.start : net_node.end]) and net.isdigit():
            name = node.one("net_name", optional=True)
            net = name.atom(1) if name is not None else nets.get(net)
            if net is None:
                raise ReadbackError("zone net code has no name")
        layer, layers = node.one("layer", optional=True), node.one("layers", optional=True)
        if (layer is None) == (layers is None):
            raise ReadbackError("expected exactly one zone layer declaration")
        names = [item.value for item in (layer or layers).items[1:] if isinstance(item, _Atom)]
        fill = node.one("fill", optional=True)
        states = [item.value for item in fill.items[1:] if isinstance(item, _Atom)] if fill else []
        if states not in ([], ["yes"]):
            raise ReadbackError("invalid zone fill state; optional atom must be 'yes'")
        filled = states == ["yes"]
        polygons = []
        outlines = []
        for polygon in node.children("polygon"):
            outlines.append(
                [[_nm(xy.atom(1)), _nm(xy.atom(2))] for xy in polygon.one("pts").children("xy")]
            )
        for polygon in node.children("filled_polygon"):
            polygons.append(
                {
                    "layer": polygon.one("layer").atom(1),
                    "vertices_nm": [
                        [_nm(xy.atom(1)), _nm(xy.atom(2))]
                        for xy in polygon.one("pts").children("xy")
                    ],
                    "representation": _tree(polygon),
                }
            )
        definition = []
        for item in node.items:
            if isinstance(item, _List) and item.head == "filled_polygon":
                continue
            if isinstance(item, _List) and item.head == "fill":
                definition.append(
                    ["fill", *[_tree(x) for x in item.items[1:] if isinstance(x, _List)]]
                )
            else:
                definition.append(_tree(item))
        objects.append(
            {
                "id": identity,
                "identity_kind": "uuid",
                "net": net,
                "layers": names,
                "outlines_nm": outlines,
                "filled": filled,
                "filled_polygons": len(polygons),
                "filled_representation_sha256": _hash(polygons),
                "definition_sha256": _hash(definition),
            }
        )
    skipped = {id(zones[key]) for key in selected if key in zones}
    protected = [_tree(item) for item in root.items if id(item) not in skipped]
    return objects, _hash(protected), version


def observe(path, selected, *, allow_missing=False):
    target = Path(path).resolve()
    raw = target.read_bytes()
    objects, protected, version = inspect_bytes(raw, selected, allow_missing=allow_missing)
    digest = hashlib.sha256(raw).hexdigest()
    observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    source = {
        "kind": "independent_consumer",
        "name": "kicad_cli.zone_readback",
        "version": VERSION,
        "completed": True,
        "input_sha256": digest,
        "format_version": version,
        "observed_at": observed_at,
        "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        if Path(__file__).is_file()
        else None,
        "lexer_code_sha256": hashlib.sha256(
            Path(__file__).with_name("move_readback.py").read_bytes()
        ).hexdigest()
        if Path(__file__).with_name("move_readback.py").is_file()
        else None,
        "limits": "Saved lexical/Decimal consumer; shared consumer expectations "
        "for definition/preservation; no native DRC or geometric fill acceptance.",
    }
    snapshot = {
        "id": "disk:" + digest,
        "source": "disk",
        "revision": digest,
        "observed_at": observed_at,
        "dependencies": [],
        "extensions": {"path": str(target), "board_format_version": version},
    }
    return Observation(snapshot, objects, protected, source)
