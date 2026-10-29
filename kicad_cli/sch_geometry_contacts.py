"""Exact source-local contact witnesses, beyond electrical component counts."""

from __future__ import annotations

import json
from collections import defaultdict
from fractions import Fraction

from .fileformat.schematic import Wire

MAX_CONTACT_COMPARISONS = 1_000_000


class ContactLimit(ValueError):
    pass


def _on(point, a, b):
    return (
        (b[0] - a[0]) * (point[1] - a[1]) == (b[1] - a[1]) * (point[0] - a[0])
        and min(a[0], b[0]) <= point[0] <= max(a[0], b[0])
        and min(a[1], b[1]) <= point[1] <= max(a[1], b[1])
    )


def _points(a, b, c, d):
    rx, ry = b[0] - a[0], b[1] - a[1]
    sx, sy = d[0] - c[0], d[1] - c[1]
    cross = rx * sy - ry * sx
    if cross:
        qx, qy = c[0] - a[0], c[1] - a[1]
        t = Fraction(qx * sy - qy * sx, cross)
        u = Fraction(qx * ry - qy * rx, cross)
        return [(a[0] + t * rx, a[1] + t * ry)] if 0 <= t <= 1 and 0 <= u <= 1 else []
    return sorted({p for p in (a, b, c, d) if _on(p, a, b) and _on(p, c, d)})


def _parameter(point, a, b):
    index = 0 if b[0] != a[0] else 1
    value = Fraction(point[index] - a[index], b[index] - a[index]) if b != a else Fraction(0)
    return [value.numerator, value.denominator]


def capture(physical, junction_points):
    """Conserve contacts at local segment parameters, not moving world points."""
    pages = defaultdict(list)
    for token, row in physical.items():
        item = row["item"]
        if row.get("bus_carrier"):
            continue  # A separate fixed-boundary guard covers unsupported carriers.
        if isinstance(item, Wire):
            if item.kind != "wire":
                continue
            a, b = item.start, item.end
        else:
            a = b = row["position"]
        pages[row["instance_path"]].append((token, a, b))
    for path, token, point in junction_points:
        pages[path].append((token, point, point))
    contacts, comparisons = {}, 0
    for rows in pages.values():
        bounded = [
            (min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1]), token, a, b)
            for token, a, b in rows
        ]
        active = []
        for current in sorted(bounded):
            low_x, high_x, low_y, high_y, token, a, b = current
            active = [row for row in active if row[1] >= low_x]
            for previous in active:
                comparisons += 1
                if comparisons > MAX_CONTACT_COMPARISONS:
                    raise ContactLimit("Affected geometry exceeds exact contact comparison limit")
                if previous[3] < low_y or previous[2] > high_y:
                    continue
                _, _, _, _, other, c, d = previous
                points = _points(a, b, c, d)
                if not points:
                    continue
                pair = sorted([token, other])
                values = [
                    [_parameter(p, a, b), _parameter(p, c, d)]
                    if pair[0] == token
                    else [_parameter(p, c, d), _parameter(p, a, b)]
                    for p in points
                ]
                key = json.dumps(pair, ensure_ascii=False, separators=(",", ":"))
                contacts[key] = sorted(values)
            active.append(current)
    return {
        "contacts": contacts,
        "comparisons": comparisons,
        "maximum_comparisons": MAX_CONTACT_COMPARISONS,
        "basis": "Exact geometric contact including unjunctioned crossings; conserve "
        "local source-segment parameters, not world coordinates or net counts.",
    }
