"""The polygon operations a zone's fill is made of (`polygons.py`): each held
to what it means, point by point -- a point is in a union when it is in
either, in a difference when in the first and not the second -- on shapes
drawn to be awkward and on random ones, crossing themselves, sharing edges,
touching at corners."""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kicad_cli.fileformat import polygons as P  # noqa: E402

OPS = {
    "union": (P.unite, lambda a, b: a or b),
    "intersection": (P.intersection, lambda a, b: a and b),
    "difference": (P.difference, lambda a, b: a and not b),
    "xor": (P.xor, lambda a, b: a != b),
}


def square(x, y, s):
    return [(x, y), (x + s, y), (x + s, y + s), (x, y + s)]


def winding(rings, px, py, scale) -> int | None:
    """The winding of rings round (px, py) / scale; None on an edge."""
    w = 0
    for ring in rings:
        n = len(ring)
        for i in range(n):
            (ax, ay), (bx, by) = ring[i - 1], ring[i]
            ax, ay, bx, by = ax * scale, ay * scale, bx * scale, by * scale
            c = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
            if c == 0 and min(ax, bx) <= px <= max(ax, bx) and min(ay, by) <= py <= max(ay, by):
                return None
            if ay <= py < by and c > 0:
                w += 1
            elif by <= py < ay and c < 0:
                w -= 1
    return w


def covered(rings, px, py, scale, rule=P.NONZERO):
    w = winding(rings, px, py, scale)
    if w is None:
        return None
    return w > 0 if rule == P.POSITIVE else (w % 2 == 1 if rule == P.EVENODD else w != 0)


def near_edge(rings, px, py, scale, gap) -> bool:
    for ring in rings:
        for i in range(len(ring)):
            (ax, ay), (bx, by) = ring[i - 1], ring[i]
            ax, ay, bx, by = ax * scale, ay * scale, bx * scale, by * scale
            dx, dy = bx - ax, by - ay
            length2 = dx * dx + dy * dy
            t = 0 if length2 == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / length2))
            if math.hypot(ax + t * dx - px, ay + t * dy - py) < gap * scale:
                return True
    return False


def check_points(a, b, result, keep, rng, points=400, size=100):
    """Every sample point away from the edges is covered by the result just
    when `keep` holds of it -- away by more than the nanometre a crossing is
    rounded by."""
    scale = 1
    checked = 0
    for _ in range(points):
        px = rng.randrange(-size // 20, size + size // 20) + 0.5
        py = rng.randrange(-size // 20, size + size // 20) + 0.5
        if near_edge(a + b, px, py, scale, 2):
            continue
        ca, cb = covered(a, px, py, scale), covered(b, px, py, scale)
        cr = covered(result, px, py, scale)
        if ca is None or cb is None or cr is None:
            continue
        assert cr == keep(ca, cb), (px / scale, py / scale, ca, cb, cr)
        checked += 1
    return checked


def assert_simple(rings):
    """No two edges of the result cross, and none is repeated."""
    edges = []
    for ring in rings:
        assert len(ring) >= 3
        for i in range(len(ring)):
            edges.append((ring[i - 1], ring[i]))
    seen = set()
    for e in edges:
        key = tuple(sorted(e))
        assert key not in seen, f"edge {e} twice"
        seen.add(key)
    for i in range(len(edges)):
        for j in range(i + 1, len(edges)):
            (a, b), (c, d) = edges[i], edges[j]
            d1, d2 = P._cross(c, d, a), P._cross(c, d, b)
            d3, d4 = P._cross(a, b, c), P._cross(a, b, d)
            if ((d1 > 0 > d2) or (d1 < 0 < d2)) and ((d3 > 0 > d4) or (d3 < 0 < d4)):
                raise AssertionError(f"{edges[i]} crosses {edges[j]}")


@pytest.mark.parametrize("name", sorted(OPS))
def test_two_overlapping_squares(name):
    op, keep = OPS[name]
    a, b = [square(0, 0, 10)], [square(5, 5, 10)]
    result = op(a, b)
    expected = {"union": 175, "intersection": 25, "difference": 75, "xor": 150}[name]
    assert P.total_area(result) == expected
    assert_simple(result)
    big = (
        [[(x * 1000, y * 1000) for x, y in r] for r in a],
        [[(x * 1000, y * 1000) for x, y in r] for r in b],
    )
    assert check_points(*big, op(*big), keep, random.Random(1), size=20_000) > 50


def test_a_hole_is_a_ring_wound_the_other_way():
    result = P.difference([square(0, 0, 30)], [square(10, 10, 10)])
    assert sorted(P.area(r) for r in result) == [-100, 900]


def test_two_pieces_touching_at_a_corner_are_two_rings():
    result = P.unite([square(0, 0, 10)], [square(10, 10, 10)])
    assert len(result) == 2 and all(P.area(r) == 100 for r in result)


def test_a_shared_edge_disappears_from_a_union():
    result = P.unite([square(0, 0, 10)], [square(10, 0, 10)])
    assert result == [[(0, 10), (0, 0), (20, 0), (20, 10)]] or (
        len(result) == 1 and P.area(result[0]) == 200 and len(result[0]) == 4
    )


def test_a_ring_crossing_itself_covers_by_its_winding():
    bow = [(0, 0), (10, 10), (10, 0), (0, 10)]  # a bow tie: two triangles wound opposite ways
    assert P.total_area(P.union([bow])) == 50
    assert P.total_area(P.union([bow], P.POSITIVE)) == 25


def test_rings_wound_twice_count_once():
    assert P.total_area(P.union([square(0, 0, 10), square(0, 0, 10)])) == 100
    assert P.union([square(0, 0, 10), square(0, 0, 10)[::-1]]) == []


def _random_ring(rng, size=100, count=None):
    count = count or rng.randrange(3, 9)
    return [(rng.randrange(0, size), rng.randrange(0, size)) for _ in range(count)]


@pytest.mark.parametrize("seed", range(40))
def test_random_shapes_combine_point_by_point(seed):
    rng = random.Random(seed)
    size = 100_000
    a = [_random_ring(rng, size) for _ in range(rng.randrange(1, 3))]
    b = [_random_ring(rng, size) for _ in range(rng.randrange(1, 3))]
    if seed % 4 == 0:  # an edge of b on an edge of a
        b.append([a[0][0], a[0][1], (rng.randrange(0, size), rng.randrange(0, size))])
    if seed % 4 == 1:  # a corner of b on a corner of a, and on an edge of it
        mid = ((a[0][0][0] + a[0][1][0]) // 2, (a[0][0][1] + a[0][1][1]) // 2)
        b.append([a[0][0], mid, (rng.randrange(0, size), rng.randrange(0, size))])
    for name, (op, keep) in OPS.items():
        result = op(a, b)
        assert_simple(result)
        checked = check_points(a, b, result, keep, random.Random(seed * 7 + len(name)), size=size)
        assert checked > 100, name


@pytest.mark.parametrize("seed", range(20))
def test_areas_add_up(seed):
    rng = random.Random(1000 + seed)
    size = 1_000_000
    a = [_random_ring(rng, size)]
    b = [_random_ring(rng, size)]
    u = P.total_area(P.unite(a, b))
    i = P.total_area(P.intersection(a, b))
    d = P.total_area(P.difference(a, b))
    ua = P.total_area(P.union(a))
    ub = P.total_area(P.union(b))
    # Cutting rounds each crossing to the nanometre, moving the area by
    # less than half a nanometre along the edges at it.
    slack = sum(len(r) for r in a + b) ** 2 * size
    assert abs(u - (ua + ub - i)) <= slack
    assert abs(d - (ua - i)) <= slack


def test_growing_a_square_rounds_its_corners():
    grown = P.offset([square(0, 0, 1_000_000)], 100_000)
    assert len(grown) == 1
    exact = 1_000_000**2 + 4 * 100_000 * 1_000_000 + math.pi * 100_000**2
    # The corners' chords stray at most max_error inside the arcs.
    assert exact - 2 * math.pi * 100_000 * P.geometry.MAX_ERROR < P.area(grown[0]) <= exact


def test_shrinking_a_square_keeps_its_corners_sharp():
    shrunk = P.offset([square(0, 0, 1_000_000)], -100_000)
    assert shrunk == [[(100_000, 900_000), (100_000, 100_000), (900_000, 100_000),
                       (900_000, 900_000)]] or P.area(shrunk[0]) == 800_000**2  # fmt: skip


def test_shrinking_away_leaves_nothing():
    assert P.offset([square(0, 0, 100)], -60) == []


def test_growing_closes_a_narrow_gap_and_a_hole():
    two = [square(0, 0, 1000), square(1100, 0, 1000)]
    assert len(P.offset(two, 60)) == 1
    holed = P.difference([square(0, 0, 3000)], [square(1000, 1000, 100)])
    assert len(P.offset(holed, 60)) == 1


@pytest.mark.parametrize("seed", range(10))
def test_grown_shapes_hold_what_they_should(seed):
    rng = random.Random(500 + seed)
    ring = _random_ring(rng, 1_000_000, rng.randrange(3, 7))
    base = P.union([ring])
    delta = rng.randrange(10_000, 80_000)
    grown = P.offset(base, delta)
    assert_simple(grown)
    scale = 1
    for _ in range(300):
        px, py = rng.randrange(-100_000, 1_100_000) + 0.5, rng.randrange(-100_000, 1_100_000) + 0.5
        inside = covered(base, px, py, scale)
        if inside is None:
            continue
        distance = 0.0 if inside else _distance(base, px, py)
        got = covered(grown, px, py, scale)
        if got is None:
            continue
        if distance < delta - P.geometry.MAX_ERROR - 2:
            assert got, (px, py, distance)
        elif distance > delta + 2:
            assert not got, (px, py, distance)


def _distance(rings, px, py) -> float:
    best = math.inf
    for ring in rings:
        for i in range(len(ring)):
            (ax, ay), (bx, by) = ring[i - 1], ring[i]
            dx, dy = bx - ax, by - ay
            length2 = dx * dx + dy * dy
            t = 0 if length2 == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / length2))
            best = min(best, math.hypot(ax + t * dx - px, ay + t * dy - py))
    return best


def test_holes_are_joined_to_their_outline_by_cuts_of_no_width():
    rng = random.Random(77)
    holes = []
    for _ in range(60):
        x, y = rng.randrange(1_000_000, 99_000_000), rng.randrange(1_000_000, 99_000_000)
        holes.append(square(x, y, 300_000))
    shape = P.difference([square(0, 0, 100_000_000)], holes)
    joined = P.fracture(shape)
    assert all(P.area(r) > 0 for r in joined)
    assert len(joined) == sum(1 for r in shape if P.area(r) > 0)
    assert P.total_area(joined) == P.total_area(shape)
    again = P.union(joined)  # the cuts close up: what is covered is unchanged
    assert P.total_area(again) == P.total_area(shape) and len(again) == len(shape)
