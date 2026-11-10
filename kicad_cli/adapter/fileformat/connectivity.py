"""Which copper touches which: the clusters a board's items form.

A net's pads should all be one cluster; every cluster beyond the first is a
connection still open, and the count of those is what DRC reports as
unconnected and what routing works to bring to zero.

Every item is a shape on each copper layer it occupies: a pad's copper and a
zone's fill are polygons, a track is a centre line with half its width
around it, a via a point with its radius, and a graphic drawn on copper with
a net -- which KiCad counts as copper -- is a polygon, a stroked outline, or
both. Two items of one net on a shared layer are joined when their shapes
meet. That is the whole rule, and every simpler guess failed somewhere:

- a track's end is not what counts, its copper is: on KiCad's ecc83 demo an
  0.8 mm track stops 0.28 mm short of a 3 mm pad and pcbnew connects it;
- nor is where the track ends: one running straight across two pads joins
  them (Jetson baseboard), two tracks crossing mid-way join, and a track
  crossing a zone's fill with both ends outside it joins the fill -- the
  last two found only on a board built to ask, since no demo does either;
- two pads with one number join when their copper overlaps, though neither
  centre lies in the other (tinytapeout, RoyalBlue);
- rectangles drawn on F.Cu with a net tie a connector's pads together
  (One-Air-Max);
- a thermal relief leaves a pad's centre in a hole in the fill and reaches
  its edge with spokes, so touching is the test, not the centre.

What is left unconnected is counted the way pcbnew counts it: per net, the
clusters that hold a pad, a track, a via or a copper graphic, less one. A
cluster of fill alone -- an island of a pour -- is not a connection waiting
to be made; DRC reports it as isolated copper instead.

Only items on the same net are joined: two nets touching is a short, which
DRC reports and connectivity does not paper over.

A zone fill can be one polygon of a hundred thousand edges, so polygons are
indexed -- by horizontal band for "is this point inside", by grid cell for
"which edges are near" -- and a query reads the few edges that can matter.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from . import geometry
from .board import Board, Pad, shape_points, stroke_width

CELL = 2_000_000  # nm: the grid that finds which items are near which
EDGE_CELL = 500_000  # nm: the grid inside a large polygon
BAND = 250_000  # nm: the horizontal bands inside a large polygon
SMALL = 64  # polygons with fewer edges are searched without an index


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


# -- small geometry -----------------------------------------------------------------


def _distance_to_segment(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    if length2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _orient(p, q, r) -> int:
    v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return (v > 0) - (v < 0)


def _segments_cross(a, b, c, d) -> bool:
    return _orient(a, b, c) != _orient(a, b, d) and _orient(c, d, a) != _orient(c, d, b)


def _segment_distance(a, b, c, d) -> float:
    if _segments_cross(a, b, c, d):
        return 0.0
    return min(
        _distance_to_segment(*a, *c, *d),
        _distance_to_segment(*b, *c, *d),
        _distance_to_segment(*c, *a, *b),
        _distance_to_segment(*d, *a, *b),
    )


def _path_segments(path):
    if len(path) == 1:
        return [(path[0], path[0])]
    return [(path[i], path[i + 1]) for i in range(len(path) - 1)]


def _box_of(points, pad=0.0) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = geometry.bbox(points)
    return (math.floor(x1 - pad), math.floor(y1 - pad), math.ceil(x2 + pad), math.ceil(y2 + pad))


def _boxes_meet(a, b) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


class Polygon:
    """A simple polygon that answers point and proximity questions quickly."""

    __slots__ = ("points", "bbox", "_edges", "_bands", "_cells")

    def __init__(self, points: list[tuple[int, int]]) -> None:
        self.points = points
        self.bbox = geometry.bbox(points)
        self._edges = [(points[i - 1], points[i]) for i in range(len(points))]
        self._bands: dict[int, list] | None = None
        self._cells: dict[tuple[int, int], list] | None = None

    def _index(self) -> None:
        bands: dict[int, list] = defaultdict(list)
        cells: dict[tuple[int, int], list] = defaultdict(list)
        for edge in self._edges:
            (ax, ay), (bx, by) = edge
            y1, y2 = min(ay, by), max(ay, by)
            for band in range(y1 // BAND, y2 // BAND + 1):
                bands[band].append(edge)
            x1, x2 = min(ax, bx), max(ax, bx)
            for cx in range(x1 // EDGE_CELL, x2 // EDGE_CELL + 1):
                for cy in range(y1 // EDGE_CELL, y2 // EDGE_CELL + 1):
                    cells[(cx, cy)].append(edge)
        self._bands, self._cells = bands, cells

    def near_edges(self, x1, y1, x2, y2) -> list:
        if len(self._edges) < SMALL:
            return self._edges
        if self._cells is None:
            self._index()
        found, seen = [], set()
        for cx in range(math.floor(x1) // EDGE_CELL, math.floor(x2) // EDGE_CELL + 1):
            for cy in range(math.floor(y1) // EDGE_CELL, math.floor(y2) // EDGE_CELL + 1):
                for edge in self._cells.get((cx, cy), ()):
                    if id(edge) not in seen:
                        seen.add(id(edge))
                        found.append(edge)
        return found

    def contains(self, x, y) -> bool:
        """Whether the point is inside, decided on the boundary as pcbnew
        decides it -- measured, not assumed (`tests/test_fileformat_polygon.py`):
        a point on an edge the polygon lies above or to the right of is
        inside, one on an edge it lies below or to the left of is outside,
        and where an edge crosses the point's row is taken to the nearest
        nanometre, halves away from zero, before the point is compared with
        it. The textbook rule gets the first half the other way round, and on
        KiCad's interf_u demo that alone put a track running along the edge
        of a ground pour on the wrong side of it."""
        box = self.bbox
        if not (box[0] <= x <= box[2] and box[1] <= y <= box[3]):
            return False
        if len(self._edges) < SMALL:
            edges = self._edges
        else:
            if self._bands is None:
                self._index()
            edges = self._bands.get(math.floor(y) // BAND, ())
        exact = isinstance(x, int) and isinstance(y, int)
        inside = False
        for (xi, yi), (xj, yj) in edges:
            if (yi < y) != (yj < y):
                num, den = (xj - xi) * (y - yi), yj - yi
                if exact and isinstance(num, int):
                    if den < 0:
                        num, den = -num, -den
                    half_up = (2 * abs(num) + den) // (2 * den)
                    crossing = half_up if num >= 0 else -half_up
                else:
                    q = num / den
                    crossing = math.floor(q + 0.5) if q >= 0 else -math.floor(0.5 - q)
                if x - xi < crossing:
                    inside = not inside
        return inside

    def path_within(self, path, reach) -> bool:
        """Whether a centre line, thickened by ``reach``, meets this polygon."""
        box = _box_of(path, reach)
        if not _boxes_meet(box, self.bbox):
            return False
        if any(self.contains(x, y) for x, y in path):
            return True
        near = self.near_edges(*box)
        return any(
            _segment_distance(a, b, c, d) <= reach for a, b in _path_segments(path) for c, d in near
        )

    def touches(self, other: Polygon) -> bool:
        """Whether two polygons share any area or boundary."""
        if not _boxes_meet(self.bbox, other.bbox):
            return False
        big, small = (self, other) if len(self._edges) >= len(other._edges) else (other, self)
        if any(big.contains(x, y) for x, y in small.points):
            return True
        near = big.near_edges(*small.bbox)
        if any(small.contains(*p) for edge in near for p in edge):
            return True
        return any(_segments_cross(a, b, c, d) for a, b in small._edges for c, d in near)


@dataclass(slots=True)
class Item:
    kind: str  # pad, segment, arc, via, fill, shape
    net: str
    layers: tuple[str, ...]
    bbox: tuple[int, int, int, int]
    polygon: Polygon | None = None  # the area it covers, if it covers one
    path: list[tuple[int, int]] | None = None  # a centre line, if it has one
    radius: float = 0.0  # half the width around that line, or a via's radius
    label: str = ""
    owner: object = None  # what on the board it is: a (footprint, pad), a track, a via, a zone


def _overlap(a: Item, b: Item) -> bool:
    """Whether two items' copper meets: areas, centre lines with their widths."""
    if a.polygon is not None and b.polygon is not None and a.polygon.touches(b.polygon):
        return True
    if a.polygon is not None and b.path is not None and a.polygon.path_within(b.path, b.radius):
        return True
    if b.polygon is not None and a.path is not None and b.polygon.path_within(a.path, a.radius):
        return True
    if a.path is not None and b.path is not None:
        limit = a.radius + b.radius
        return any(
            _segment_distance(p, q, r, s) <= limit
            for p, q in _path_segments(a.path)
            for r, s in _path_segments(b.path)
        )
    return False


# -- items --------------------------------------------------------------------------


def _pad_item(pad: Pad, reference: str, copper: set[str], owner=None) -> Item:
    layers = tuple(layer for layer in pad.layers if layer in copper)
    points = pad.polygon()
    if points is None:  # a custom pad: its outline box stands in for its shape
        x1, y1, x2, y2 = pad.bbox()
        points = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    polygon = Polygon(points)
    label = f"{reference}.{pad.number}@{pad.position[0]},{pad.position[1]}"
    return Item("pad", pad.net, layers, polygon.bbox, polygon=polygon, label=label, owner=owner)


def _copper_shape(board: Board, node, copper: set[str]) -> Item | None:
    """A graphic drawn on a copper layer with a net: copper like any other."""
    layer = node.find("layer")
    if layer is None or layer.value(1) not in copper:
        return None
    net = board.net_of(node)
    points = shape_points(node)
    if not net or len(points) < 1:
        return None
    radius = stroke_width(node) / 2
    fill = node.find("fill")
    filled = fill is not None and fill.value(1) in ("yes", "solid")
    head = node.head
    closed = head in ("gr_rect", "gr_circle", "gr_poly")
    path = points + [points[0]] if closed and len(points) > 2 else points
    polygon = Polygon(points) if filled and len(points) >= 3 else None
    return Item("shape", net, (layer.value(1),), _box_of(points, radius), polygon=polygon,
                path=path, radius=radius, owner=node)  # fmt: skip


def items_of(board: Board) -> list[Item]:
    copper = set(board.copper_layers)
    items: list[Item] = []
    for fp in board.footprints:
        for pad in fp.pads:
            item = _pad_item(pad, fp.reference, copper, (fp, pad))
            if item.layers:
                items.append(item)
    for track in board.tracks:
        if track.kind == "arc":
            chords = geometry.arc_through(track.start, track.mid, track.end)
            path = [(round(x), round(y)) for x, y in chords]
        else:
            path = [track.start, track.end]
        r = track.width / 2
        items.append(
            Item(
                track.kind,
                track.net,
                (track.layer,),
                _box_of(path, r),
                path=path,
                radius=r,
                owner=track,
            )
        )
    for via in board.vias:
        r = via.diameter / 2
        items.append(
            Item("via", via.net, tuple(via.layers), _box_of([via.position], r),
                 path=[via.position], radius=r, owner=via)
        )  # fmt: skip
    for zone in board.zones:
        if zone.rule_area:
            continue
        for layer, polygons in zone.filled.items():
            if layer not in copper:
                continue
            for points in polygons:
                if len(points) >= 3:
                    polygon = Polygon(points)
                    items.append(
                        Item("fill", zone.net, (layer,), polygon.bbox, polygon=polygon, owner=zone)
                    )
    for node in board.root.lists():
        if node.head in ("gr_line", "gr_arc", "gr_circle", "gr_rect", "gr_poly"):
            item = _copper_shape(board, node, copper)
            if item is not None:
                items.append(item)
    return items


@dataclass(slots=True)
class Connectivity:
    items: list[Item]
    cluster_of: list[int]

    def pad_clusters(self) -> dict[str, list[list[str]]]:
        """Per net, the pads copper joins: each cluster sorted, the list sorted."""
        groups: dict[tuple[str, int], set[str]] = defaultdict(set)
        for index, item in enumerate(self.items):
            if item.kind == "pad" and item.net:
                groups[(item.net, self.cluster_of[index])].add(item.label)
        out: dict[str, list[list[str]]] = defaultdict(list)
        for (net, _), labels in groups.items():
            out[net].append(sorted(labels))
        return {net: sorted(clusters) for net, clusters in out.items()}

    @property
    def unconnected(self) -> int:
        """Connections still open: per net, the clusters holding a pad, a
        track, a via or a copper graphic, less one. Fill alone does not count."""
        counted = {"pad", "segment", "arc", "via", "shape"}
        clusters: dict[str, set[int]] = defaultdict(set)
        for index, item in enumerate(self.items):
            if item.net and item.kind in counted:
                clusters[item.net].add(self.cluster_of[index])
        return sum(len(found) - 1 for found in clusters.values())


def connect(board: Board) -> Connectivity:
    items = items_of(board)
    grid: dict[tuple[str, int, int], list[int]] = defaultdict(list)
    for index, item in enumerate(items):
        x1, y1, x2, y2 = item.bbox
        for layer in item.layers:
            for cx in range(x1 // CELL, x2 // CELL + 1):
                for cy in range(y1 // CELL, y2 // CELL + 1):
                    grid[(layer, cx, cy)].append(index)

    joined = _UnionFind(len(items))
    for index, item in enumerate(items):
        if not item.net:
            continue
        x1, y1, x2, y2 = item.bbox
        tested = set()
        for layer in item.layers:
            for cx in range(x1 // CELL, x2 // CELL + 1):
                for cy in range(y1 // CELL, y2 // CELL + 1):
                    for other in grid.get((layer, cx, cy), ()):
                        # Each pair once: the lower index asks.
                        if other <= index or other in tested:
                            continue
                        tested.add(other)
                        target = items[other]
                        if target.net != item.net or not _boxes_meet(item.bbox, target.bbox):
                            continue
                        if joined.find(index) == joined.find(other):
                            continue
                        if _overlap(item, target):
                            joined.union(index, other)
    return Connectivity(items, [joined.find(i) for i in range(len(items))])
