"""Fresh saved-document consumer; board structure uses the independent lexer.

Schematic connectivity still shares the custom design model. Native intent,
DRC, rendering and full manufacturing geometry remain separate acceptance gates.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .move_readback import _TOKENS, _Atom, _parse


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(node, command, parent="", *, silk_targets=None, footprint_uuid=None, raw_atoms=None):
    if isinstance(node, _Atom):
        if raw_atoms is not None:
            return raw_atoms[id(node)]
        # Numeric spelling is insignificant, but strings such as UUIDs are not.
        from decimal import Decimal, InvalidOperation

        try:
            return str(Decimal(node.value).normalize())
        except InvalidOperation:
            return node.value
    if node.head in {"filled_polygon", "fill_segments"} and not (
        command == "board silkscreen place" and silk_targets is not None
    ):
        return None
    if command == "board zone stitch" and parent == "zone" and node.head == "fill":
        # Actual refill changes status; thermal/clearance/island settings remain protected.
        node.items = [
            item for item in node.items if not isinstance(item, _Atom) or item.value != "yes"
        ]
    if parent == "footprint":
        if command == "board footprint place" and node.head == "at":
            # Only translation is allowed. Rotation is protected.
            return ["at", "translation", node.atom(3) if len(node.items) > 3 else "0"]
        if command == "project links repair" and node.head == "path":
            return None
    if node.head == "footprint" and silk_targets is not None:
        identity = node.one("uuid")
        footprint_uuid = identity.atom(1)
    if command == "board silkscreen place" and parent == "reference" and node.head == "at":
        if silk_targets is None:
            return ["at", "translation", node.atom(3) if len(node.items) > 3 else "0"]
        # A selected field permits only its x/y translation. Every angle and
        # any extra saved at attributes remain part of protection.
        return ["at", "translation"] + [
            canonical(item, "", raw_atoms=raw_atoms) for item in node.items[3:]
        ]
    nested = (
        "reference"
        if node.head == "property"
        and len(node.items) > 1
        and isinstance(node.items[1], _Atom)
        and node.atom(1) == "Reference"
        else node.head
    )
    if nested == "reference" and silk_targets is not None:
        identities = node.children("uuid")
        field_uuid = identities[0].atom(1) if len(identities) == 1 else None
        if (footprint_uuid, field_uuid) not in silk_targets:
            nested = "protected_reference"
    items = [
        canonical(
            item,
            command,
            nested,
            silk_targets=silk_targets,
            footprint_uuid=footprint_uuid,
            raw_atoms=raw_atoms,
        )
        for item in node.items
    ]
    # Child ordering is irrelevant for protection; preserve repeated children.
    atoms = [v for item, v in zip(node.items, items, strict=True) if isinstance(item, _Atom)]
    children = [
        v
        for item, v in zip(node.items, items, strict=True)
        if not isinstance(item, _Atom) and v is not None
    ]
    return atoms + sorted(children, key=lambda v: json.dumps(v, sort_keys=True))


def protection(raw, command, *, silk_targets=None):
    node = _parse(raw.decode("utf-8"))
    if command == "board zone stitch":
        # New copper is checked separately; existing copper must be preserved.
        node.items = [
            item
            for item in node.items
            if isinstance(item, _Atom) or item.head not in {"via", "segment", "arc"}
        ]
    selected = (
        {(row["footprint_uuid"], row["reference_uuid"]) for row in silk_targets}
        if silk_targets is not None
        else None
    )
    raw_atoms = None
    if command == "board silkscreen place" and selected is not None:
        # The independent lexer decodes quoted atoms. Preserve their original
        # tokens here: "100", "1e2", 100 and escaped text are distinct saved
        # content/style under an explicitly bounded translation authorization.
        def atoms(current):
            if isinstance(current, _Atom):
                yield current
            else:
                for item in current.items:
                    yield from atoms(item)

        tokens = [m[0] for m in _TOKENS.finditer(raw.decode("utf-8")) if m[0] not in {"(", ")"}]
        raw_atoms = {id(atom): token for atom, token in zip(atoms(node), tokens, strict=True)}
    return sha(
        json.dumps(
            canonical(node, command, silk_targets=selected, raw_atoms=raw_atoms),
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    )


def observe(files):
    from . import sync_readback
    from .fileformat import netlist

    result = []
    for row in files:
        path = Path(row["path"])
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            result.append({"path": str(path), "sha256": "absent", "objects": []})
            continue
        data = {"path": str(path), "sha256": sha(raw)}
        if path.suffix == ".kicad_pcb":
            objects, protected, version = sync_readback.inspect_bytes(raw)
            data.update(objects=objects, protected_sha256=protected, format_version=version)
        elif path.suffix == ".kicad_sch":
            built = netlist.build(path)
            saved = _parse(raw.decode("utf-8-sig"), expected_head="kicad_sch")
            data["objects"] = {
                "components": sorted(
                    [
                        {"ref": c.ref, "value": c.value, "footprint": c.footprint}
                        for c in built.components
                    ],
                    key=lambda c: c["ref"],
                ),
                "nets": sorted(
                    [
                        {"name": n.name, "nodes": sorted([f"{p.ref}.{p.pin}" for p in n.nodes])}
                        for n in built.nets
                    ],
                    key=lambda n: n["name"],
                ),
                "geometry": [
                    canonical(node, "")
                    for node in saved.items
                    if not isinstance(node, _Atom)
                    and node.head
                    in {
                        "symbol",
                        "wire",
                        "label",
                        "global_label",
                        "hierarchical_label",
                        "directive_label",
                        "junction",
                        "no_connect",
                        "sheet",
                        "bus",
                        "bus_entry",
                        "text",
                        "text_box",
                        "polyline",
                        "rectangle",
                        "circle",
                        "arc",
                    }
                ],
            }
        elif path.suffix == ".kicad_pro":
            data["objects"] = json.loads(raw)
        elif path.suffix == ".net":
            data["objects"] = canonical(_parse(raw.decode("utf-8"), expected_head="export"), "")
        elif path.suffix in {".kicad_sym", ".kicad_mod"} or path.name in {
            "sym-lib-table",
            "fp-lib-table",
        }:
            head = {".kicad_sym": "kicad_symbol_lib", ".kicad_mod": "footprint"}.get(
                path.suffix, "sym_lib_table" if path.name == "sym-lib-table" else "fp_lib_table"
            )
            data["objects"] = canonical(_parse(raw.decode("utf-8"), expected_head=head), "")
        elif path.name.endswith(".provenance.json"):
            data["objects"] = json.loads(raw)
        elif path.suffix in {".svg", ".pdf"}:
            from .export_readback import consume

            data["objects"] = consume(path)
        else:
            raise ValueError("Unsupported engineering output")
        result.append(data)
    return result
