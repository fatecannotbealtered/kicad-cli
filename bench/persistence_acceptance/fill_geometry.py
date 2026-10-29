"""Independent saved polygon measurements; never imports the product writer."""

import hashlib
import json
import re
import sys
from collections import Counter
from decimal import Decimal

from .support import ASSETS

sys.path.insert(0, str(ASSETS["readers"]))
from sexpr_reader import child, children
from sexpr_reader import parse as parse

ZONE = "c112dd21-021c-5816-a3ee-d433e8cd259f"
OLD = (55.0, 32.0)
NEW = (55.0, 34.0)


def normalized(value):
    if isinstance(value, list):
        return [normalized(x) for x in value]
    if isinstance(value, str) and re.fullmatch(r"-?\d+(?:\.\d+)?", value):
        return str(Decimal(value).normalize())
    return value


def digest(value):
    return hashlib.sha256(json.dumps(normalized(value), sort_keys=True).encode()).hexdigest()


def uid(node):
    return child(node, "uuid")[1]


def definition(zone):
    return [
        v
        for v in zone
        if not (isinstance(v, list) and v and v[0] in {"filled_polygon", "fill_segments"})
    ]


def protected(root, *, move=False):
    output = []
    for item in root:
        if isinstance(item, list) and item:
            if move and item[0] == "footprint":
                if any(p[1:3] == ["Reference", "TP1"] for p in children(item, "property")):
                    item = [v for v in item if not (isinstance(v, list) and v and v[0] == "at")]
            elif not move and item[0] == "zone" and uid(item) == ZONE:
                item = definition(item)
        output.append(item)
    return normalized(output)


def polygons(zone):
    return [
        [(Decimal(p[1]), Decimal(p[2])) for p in children(child(poly, "pts"), "xy")]
        for poly in children(zone, "filled_polygon")
    ]


def inside(polygon, point):
    # Even/odd crossings also cancel the paired bridge edges used for polygon holes.
    x, y = map(Decimal, map(str, point))
    result = False
    for a, b in zip(polygon, polygon[1:] + polygon[:1], strict=True):
        if (a[1] > y) != (b[1] > y):
            if x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]:
                result = not result
    return result


def contains(zone, point):
    return any(inside(p, point) for p in polygons(zone))


def boundary(zone):
    counts = Counter()
    for polygon in polygons(zone):
        for a, b in zip(polygon, polygon[1:] + polygon[:1], strict=True):
            counts[tuple(sorted((a, b)))] += 1
    return {edge for edge, count in counts.items() if count % 2}


def metrics(root):
    result = []
    for zone in children(root, "zone"):
        polys = polygons(zone)
        areas = [
            abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(p, p[1:] + p[:1], strict=True))) / 2
            for p in polys
        ]
        result.append(
            {
                "uuid": uid(zone),
                "layer": child(zone, "layer")[1],
                "definition_sha256": digest(definition(zone)),
                "fill_sha256": digest(children(zone, "filled_polygon")),
                "polygons": len(polys),
                "area_mm2": str(sum(areas)),
                "old_center_in_fill": contains(zone, OLD),
                "new_center_in_fill": contains(zone, NEW),
            }
        )
    return result


def compare(before, after):
    left = next(z for z in children(before, "zone") if uid(z) == ZONE)
    right = next(z for z in children(after, "zone") if uid(z) == ZONE)
    changed = boundary(left) ^ boundary(right)
    # Frozen region is the old/new 0.75 mm pad radius + 0.30 mm clearance,
    # with 0.15 mm tolerance for polygon approximation. No whole-board allowance.
    bounded = all(
        any(abs(float(x) - cx) <= 1.2 and abs(float(y) - cy) <= 1.2 for cx, cy in (OLD, NEW))
        for edge in changed
        for x, y in edge
    )
    result = {
        "non_target_structure_equal": protected(before) == protected(after),
        "changed_boundary_edges": len(changed),
        "all_changed_boundary_vertices_in_frozen_region": bounded,
        "before_old_center_in_fill": contains(left, OLD),
        "before_new_center_in_fill": contains(left, NEW),
        "after_old_center_in_fill": contains(right, OLD),
        "after_new_center_in_fill": contains(right, NEW),
        "before": metrics(before),
        "after": metrics(after),
    }
    result["passed"] = bool(
        result["non_target_structure_equal"]
        and changed
        and bounded
        and not result["before_old_center_in_fill"]
        and result["before_new_center_in_fill"]
        and result["after_old_center_in_fill"]
        and not result["after_new_center_in_fill"]
    )
    return result
