"""Finite saved-file placement guard, independent of the footprint writer.

Only front, unrotated footprints with ordinary unrotated SMD rectangular or
rounded-rectangular pads and straight line/rectangle/polygon artwork are
supported. Integer rectangles conservatively contain pads and all nontext
artwork, including courtyards. They are not exact collision polygons: a box
intersection can reject two shapes that do not actually touch.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from . import move_readback as reader

VERSION = "1"
SCHEMA = "kicad-cli://schemas/move-placement-guard/v1"
_METADATA = {
    "layer",
    "uuid",
    "at",
    "descr",
    "tags",
    "property",
    "path",
    "attr",
    "duplicate_pad_numbers_are_jumpers",
    "embedded_fonts",
    "model",
    "fp_text",
    "locked",
    "sheetname",
    "sheetfile",
}
_GRAPHICS = {"fp_line", "fp_rect", "fp_poly"}
LIMITS = [
    "Conservative nontext pad/artwork/courtyard envelopes, not exact polygon collisions.",
    "Only F.Cu zero-degree footprints, zero-degree SMD rect/roundrect pads, "
    "straight fp_line/fp_rect/fp_poly graphics and four straight rectangular Edge.Cuts.",
    "Ordinary front SMD pad layers are F.Cu/F.Mask with optional F.Paste; "
    "paste presence does not change the measured copper-containing pad rectangle.",
    "Footprint fields/text, board-level artwork, copper clearance, 3D bodies and "
    "manufacturing/assembly acceptance are outside this placement guard.",
]


class GuardError(ValueError):
    """A required saved geometry observation is unsupported or incomplete."""


def _children(node):
    return [item for item in node.items if isinstance(item, reader._List)]


def _walk(node):
    yield node
    for child in _children(node):
        yield from _walk(child)


def _nm(value):
    number = reader._decimal(str(value)) * reader.NM_PER_MM
    if number != number.to_integral_value() or not -(2**31) <= number <= 2**31 - 1:
        raise GuardError("Geometry requires exact signed-32-bit integer nanometres")
    return int(number)


def _point(node):
    if len(node.items) != 3:
        raise GuardError("A geometry point must have exactly two coordinates")
    return (_nm(node.atom(1)), _nm(node.atom(2)))


def _pose(node):
    at = node.one("at")
    if len(at.items) not in (3, 4):
        raise GuardError("Unsupported footprint or pad pose")
    if len(at.items) == 4 and reader._decimal(at.atom(3)) != 0:
        raise GuardError("Placement guard supports only zero-degree footprints and pads")
    return (_nm(at.atom(1)), _nm(at.atom(2)))


def _uuid(node):
    value = node.one("uuid").atom(1)
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as exc:
        raise GuardError("Geometry requires unique canonical UUID identities") from exc
    return value


def _envelope(points, grow=0):
    if not points:
        raise GuardError("Geometry has no measurable points")
    return [
        min(p[0] for p in points) - grow,
        min(p[1] for p in points) - grow,
        max(p[0] for p in points) + grow,
        max(p[1] for p in points) + grow,
    ]


def _union(boxes):
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def _shift(box, position):
    x, y = position
    result = [box[0] + x, box[1] + y, box[2] + x, box[3] + y]
    if any(not -(2**31) <= value <= 2**31 - 1 for value in result):
        raise GuardError("World geometry exceeds signed-32-bit integer nanometres")
    return result


def _graphic(node):
    allowed = {"start", "end", "stroke", "layer", "uuid", "fill", "locked"}
    if node.head == "fp_poly":
        allowed = (allowed - {"start", "end"}) | {"pts"}
        pts = node.one("pts")
        points = [_point(item) for item in _children(pts) if item.head == "xy"]
        if len(points) < 3 or len(points) != len(_children(pts)):
            raise GuardError("Only straight polygon vertices are supported")
    else:
        points = [_point(node.one(key)) for key in ("start", "end")]
    if any(child.head not in allowed for child in _children(node)):
        raise GuardError("Unsupported straight footprint graphic option")
    stroke = node.one("stroke")
    width = stroke.one("width")
    grow = _nm(width.atom(1))
    if grow < 0 or stroke.one("type").atom(1) not in {"solid", "default"}:
        raise GuardError("Unsupported graphic stroke")
    # Deliberately use a full stroke width, rather than the mathematical half
    # width, as an explicit conservative guard band around straight artwork.
    return _envelope(points, grow)


def _pad(node):
    if node.atom(2) != "smd" or node.atom(3) not in {"rect", "roundrect"}:
        raise GuardError("Only ordinary SMD rect/roundrect pads are supported")
    allowed = {"at", "size", "layers", "roundrect_rratio", "net", "uuid", "pinfunction", "pintype"}
    if any(child.head not in allowed for child in _children(node)):
        raise GuardError("Unsupported pad geometry or option")
    position, size = _pose(node), _point(node.one("size"))
    layers = node.one("layers")
    declared_layers = [layers.atom(i) for i in range(1, len(layers.items))]
    if len(declared_layers) != len(set(declared_layers)) or set(declared_layers) not in (
        {"F.Cu", "F.Mask"},
        {"F.Cu", "F.Mask", "F.Paste"},
    ):
        raise GuardError(
            "Only ordinary front copper/mask SMD pads with optional paste are supported"
        )
    if any(value <= 0 for value in size):
        raise GuardError("Pad size must be positive")
    if node.atom(3) == "roundrect":
        ratio = reader._decimal(node.one("roundrect_rratio").atom(1))
        if not 0 <= ratio <= Decimal("0.5"):
            raise GuardError("Invalid rounded rectangular pad radius")
    half = [(value + 1) // 2 for value in size]
    return [
        position[0] - half[0],
        position[1] - half[1],
        position[0] + half[0],
        position[1] + half[1],
    ]


def _rectangle(root):
    segments = []
    for node in _children(root):
        layers = node.children("layer")
        if not any(layer.atom(1) == "Edge.Cuts" for layer in layers):
            continue
        if node.head != "gr_line" or len(layers) != 1:
            raise GuardError("Board edge requires exactly four straight board-level lines")
        a, b = (_point(node.one(key)) for key in ("start", "end"))
        if a == b or (a[0] != b[0] and a[1] != b[1]):
            raise GuardError("Board edge must be axis aligned and nondegenerate")
        segments.append(tuple(sorted((a, b))))
    if len(segments) != 4 or len(set(segments)) != 4:
        raise GuardError("Board edge requires exactly four distinct straight lines")
    degree = Counter(point for segment in segments for point in segment)
    xs, ys = sorted({p[0] for p in degree}), sorted({p[1] for p in degree})
    if (
        len(xs) != 2
        or len(ys) != 2
        or len(degree) != 4
        or set(degree.values()) != {2}
        or set(degree) != {(x, y) for x in xs for y in ys}
    ):
        raise GuardError("Board edge must be one closed rectangle without cutouts")
    return [xs[0], ys[0], xs[1], ys[1]]


def _courtyard(nodes):
    """Require a nondegenerate rectangle or one closed connected straight loop.

    This verifies completeness of the bounded straight courtyard input, not
    an exact polygon collision model or assembly clearance rule.
    """
    if len(nodes) == 1 and nodes[0].head == "fp_rect":
        a, b = (_point(nodes[0].one(key)) for key in ("start", "end"))
        if a[0] != b[0] and a[1] != b[1]:
            return
        raise GuardError("Front courtyard rectangle is degenerate")
    edges, neighbors = set(), {}
    for node in nodes:
        if node.head != "fp_line":
            raise GuardError("Front courtyard requires one rectangle or one straight closed loop")
        a, b = (_point(node.one(key)) for key in ("start", "end"))
        edge = tuple(sorted((a, b)))
        if a == b or edge in edges:
            raise GuardError("Front courtyard has a degenerate or repeated segment")
        edges.add(edge)
        neighbors.setdefault(a, []).append(b)
        neighbors.setdefault(b, []).append(a)
    if len(edges) < 3 or any(len(values) != 2 for values in neighbors.values()):
        raise GuardError("Front courtyard is not a closed straight loop")
    visited, pending = set(), [next(iter(neighbors))]
    while pending:
        current = pending.pop()
        if current not in visited:
            visited.add(current)
            pending.extend(neighbors[current])
    if visited != neighbors.keys():
        raise GuardError("Front courtyard is not one connected straight loop")


def observe(path):
    """Measure complete supported footprint envelopes from fresh saved bytes."""
    raw = Path(path).read_bytes()
    root = reader._parse(raw.decode("utf-8"))
    identities = [node.atom(1) for node in _walk(root) if node.head == "uuid"]
    if any(not value or count != 1 for value, count in Counter(identities).items()):
        raise GuardError("Saved board UUIDs are absent or repeated")
    footprints, references = [], set()
    for fp in _children(root):
        if fp.head not in {"footprint", "module"}:
            continue
        if fp.head != "footprint" or fp.one("layer").atom(1) != "F.Cu":
            raise GuardError("Only front copper footprints are supported")
        if any(child.head not in _METADATA | _GRAPHICS | {"pad"} for child in _children(fp)):
            raise GuardError("Unsupported footprint member or geometry")
        props = [p for p in fp.children("property") if p.atom(1) == "Reference"]
        if len(props) != 1 or not props[0].atom(2) or props[0].atom(2) in references:
            raise GuardError("Every footprint requires one unique nonempty Reference")
        reference = props[0].atom(2)
        references.add(reference)
        position, boxes, courtyard, pads = _pose(fp), [], [], []
        for node in _children(fp):
            layers = node.children("layer")
            if any(layer.atom(1) == "Edge.Cuts" for layer in layers):
                raise GuardError("Footprint-level Edge.Cuts are unsupported")
            if node.head in _GRAPHICS:
                box = _graphic(node)
                boxes.append(box)
                if node.one("layer").atom(1) == "F.CrtYd":
                    courtyard.append(node)
            elif node.head == "pad":
                boxes.append(_pad(node))
                pads.append(_uuid(node))
        if not courtyard or not pads:
            raise GuardError("Every footprint requires measurable front courtyard and SMD pads")
        _courtyard(courtyard)
        locked = fp.one("locked", optional=True)
        footprints.append(
            {
                "ref": reference,
                "id": _uuid(fp),
                "position_nm": list(position),
                "bounds_nm": _shift(_union(boxes), position),
                "pad_ids": sorted(pads),
                "locked": (locked is not None and locked.atom(1) != "no")
                or any(
                    isinstance(item, reader._Atom) and item.value == "locked" for item in fp.items
                ),
            }
        )
    if not footprints:
        raise GuardError("Saved board has no supported footprints")
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "outline_nm": _rectangle(root),
        "footprints": sorted(footprints, key=lambda item: item["ref"]),
    }


def check(baseline, candidate, requested):
    """Check the complete selected batch; exact protected content remains mandatory."""
    if not isinstance(requested, dict) or not requested:
        raise GuardError("A nonempty exact footprint translation request is required")
    for xy in requested.values():
        if not isinstance(xy, (list, tuple)) or len(xy) != 2:
            raise GuardError("Every requested target requires two finite millimetre coordinates")
        for value in xy:
            _nm(value)
    before, after = observe(baseline), observe(candidate)
    prior = reader.observe(baseline, sorted(requested))
    actual = reader.observe(candidate, sorted(requested))
    _, failures = reader.compare(prior, actual, requested)
    if (
        failures
        or prior.snapshot["revision"] != before["sha256"]
        or actual.snapshot["revision"] != after["sha256"]
    ):
        raise GuardError("Candidate differs from requested translation or protected saved content")
    if before["outline_nm"] != after["outline_nm"]:
        raise GuardError("Candidate changes the protected board outline")
    targets, conflicts = [], []
    old = {row["ref"]: row for row in before["footprints"]}
    selected = set(requested)
    bounds = after["outline_nm"]
    for row in after["footprints"]:
        if row["ref"] not in selected:
            continue
        if row["locked"] or old[row["ref"]]["locked"]:
            raise GuardError("Locked selected footprint prevents the entire batch")
        box = row["bounds_nm"]
        targets.append(
            {
                "ref": row["ref"],
                "id": row["id"],
                "from_nm": old[row["ref"]]["position_nm"],
                "to_nm": row["position_nm"],
                "before_bounds_nm": old[row["ref"]]["bounds_nm"],
                "after_bounds_nm": box,
            }
        )
        if not (
            bounds[0] < box[0] and bounds[1] < box[1] and box[2] < bounds[2] and box[3] < bounds[3]
        ):
            conflicts.append(
                {"criterion": "strict_rectangular_edge_containment", "refs": [row["ref"]]}
            )
    rows = after["footprints"]
    for index, first in enumerate(rows):
        for second in rows[index + 1 :]:
            if not selected.intersection((first["ref"], second["ref"])):
                continue
            a, b = first["bounds_nm"], second["bounds_nm"]
            if not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1]):
                conflicts.append(
                    {
                        "criterion": "conservative_envelopes_not_strictly_disjoint",
                        "refs": [first["ref"], second["ref"]],
                    }
                )
    return {
        "schema_version": 1,
        "version": VERSION,
        "status": "blocked" if conflicts else "passed",
        "source": {
            "kind": "independent_saved_file_consumer",
            "name": "kicad_cli.move_guard",
            "official_native": False,
        },
        "baseline_sha256": before["sha256"],
        "candidate_sha256": after["sha256"],
        "outline_nm": bounds,
        "envelope_units": "integer_nm",
        "method": "conservative_nontext_envelopes_full_graphic_stroke_guard_band",
        "edge_policy": "strictly_inside_rectangular_Edge.Cuts_centerlines",
        "collision_policy": "selected_vs_every_candidate_footprint_strict_box_separation",
        "targets": targets,
        "candidate_footprints": rows,
        "conflicts": conflicts,
        "protected_content": "All saved bytes except selected footprint-level at expressions; "
        "identity, zero rotation, layer and intended position checked separately "
        "with 1 nm tolerance.",
        "not_checked": LIMITS,
    }
