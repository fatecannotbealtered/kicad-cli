"""A board's copper as DRC measures it: each item's copper layer by layer,
its hole, the clearance it asks for itself, and the net it is checked on.

A pad's copper is its shape (`shapes.pad_copper`); a via's a disc; a track's
a segment, or an arc's chords, grown by half its width; a zone's the fill
the file holds, as KiCad's DRC checks it without filling again; a copper
graphic its stroke, and its inside when filled. A plated pad or via that
removes its unused layers has copper on a layer only where something of its
net meets it there, where a zone connects to it (`zone_layer_connections`,
as the file records it) or, at the ends of its span, when it keeps them.

The net an item is checked on is its own, except for copper that reaches no
pad of its net (`tests/fixtures/drc/drc2`, row 4): KiCad gives such copper
the net of the pad it touches -- a track across a pad of another net, or a
via on another net's track, is not a short to KiCad -- or, where it touches
pads of several nets, the last of them by name; copper touching no pad at
all shares one net, the first by name. Copper that does reach a pad of its
own net keeps it.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from .. import connectivity, geometry
from ..board import Board, Footprint, Pad, nm, shape_points, stroke_width
from ..connectivity import Polygon
from . import items as describe
from .shapes import Shape, hole, pad_copper, via_hole

GRAPHICS = ("line", "arc", "circle", "rect", "poly")
ARC_ERROR = 50  # nm: how far an arc's chords may stray from it; KiCad reports to 0.1 um


@dataclass(eq=False)
class Thing:
    kind: str  # pad, track, via, fill, shape
    own_net: str
    item: describe.Item
    owner: object
    fp: Footprint | None = None
    # per copper layer: convex pieces, or (for a zone) its fill's polygons
    pieces: dict[str, list[Shape]] = field(default_factory=dict)
    fills: dict[str, list[Polygon]] = field(default_factory=dict)
    # per copper layer: the inside of a filled drawing that is not convex
    areas: dict[str, list[Polygon]] = field(default_factory=dict)
    hole: Shape | None = None
    plated: bool = False
    override: tuple[int, str] | None = None  # a clearance of its own, and its name
    zone_clearance: int | None = None
    npth: list[Shape] | None = None  # an unplated pad's shape, which holds no copper
    net: str = ""  # the net it is checked on
    tied: frozenset[str] = frozenset()  # the nets a net-tie footprint's copper joins
    bridges: str = ""  # a footprint's drawing across two of its pads: "short" or "clearance"
    ties: tuple[frozenset[str], ...] = ()  # its footprint's net-tie groups of pad numbers
    filled: bool = False  # a drawing with its inside filled
    boxes: dict[str, tuple[float, float, float, float]] = field(default_factory=dict)

    @property
    def layers(self):
        return set(self.pieces) | set(self.fills) | set(self.areas)

    def box(self, layer: str) -> tuple[float, float, float, float]:
        found = self.boxes.get(layer)
        if found is None:
            boxes = [s.box for s in self.pieces.get(layer, ())]
            boxes += [p.bbox for p in self.fills.get(layer, ())]
            boxes += [p.bbox for p in self.areas.get(layer, ())]
            found = self.boxes[layer] = (
                min(b[0] for b in boxes),
                min(b[1] for b in boxes),
                max(b[2] for b in boxes),
                max(b[3] for b in boxes),
            )
        return found


def _clearance_of(node) -> int | None:
    found = node.find("clearance") if node is not None else None
    return nm(found.atom(1)) if found is not None and found.atom(1) is not None else None


def _flag(node, head: str) -> bool:
    found = node.find(head) if node is not None else None
    return found is not None and found.value(1) != "no"


def arc_chords(start, mid, end, width: float) -> list[Shape]:
    points = [(round(x), round(y)) for x, y in geometry.arc_through(start, mid, end, ARC_ERROR)]
    half = width / 2
    return [Shape((points[i], points[i + 1]), half) for i in range(len(points) - 1)] or [
        Shape((start,), half)
    ]


def _convex(points) -> bool:
    sign = 0
    for i in range(len(points)):
        (ax, ay), (bx, by), (cx, cy) = points[i - 2], points[i - 1], points[i]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if cross:
            if sign and (cross > 0) != (sign > 0):
                return False
            sign = cross
    return True


def is_filled(node) -> bool:
    fill = node.find("fill")
    return fill is not None and fill.value(1) in ("yes", "solid")


def graphic_copper(node, place=None) -> tuple[list[Shape], list[Polygon]]:
    """A drawing's copper: its stroke, and its inside when it is filled --
    a filled circle as the disc it is, a convex outline grown by its stroke,
    anything else as the polygon it is."""
    points = shape_points(node, ARC_ERROR)
    if not points:
        return [], []
    if place is not None:
        points = place(points)
    half = stroke_width(node) / 2
    filled = is_filled(node)
    kind = node.head.split("_", 1)[1]
    if filled and kind == "circle":
        centre, end = node.find("center"), node.find("end")
        ends = [(nm(centre.atom(1)), nm(centre.atom(2))), (nm(end.atom(1)), nm(end.atom(2)))]
        if place is not None:
            ends = place(ends)
        return [Shape((ends[0],), math.dist(*ends) + half)], []
    if filled and len(points) >= 3 and _convex(points):
        return [Shape(tuple(points), half)], []
    closed = kind in ("circle", "rect", "poly")
    path = list(points) + ([points[0]] if closed and len(points) > 2 else [])
    pieces = [Shape((path[i], path[i + 1]), half) for i in range(len(path) - 1)]
    if len(path) == 1:
        pieces.append(Shape((path[0],), half))
    inside = filled and len(points) >= 3
    areas = [Polygon([(round(x), round(y)) for x, y in points])] if inside else []
    return pieces, areas


class Copper:
    """Every piece of copper on a board, and the nets it is checked on."""

    def __init__(self, board: Board, joined=None) -> None:
        self.board = board
        self.copper = set(board.copper_layers)
        layers = board.copper_layers
        self.ends = {layers[0], layers[-1]} if layers else set()
        self.things: list[Thing] = []
        self.joined = joined if joined is not None else connectivity.connect(board)
        self._build()
        self._assign_nets()

    # -- building -----------------------------------------------------------------------------

    def _used(self, node, layers: list[str], shape: Shape, net: str) -> set[str]:
        """The layers a plated pad or via has copper on: all of them, unless
        it removes the unused ones."""
        if not _flag(node, "remove_unused_layers"):
            return set(layers)
        kept = set(self.ends) & set(layers) if _flag(node, "keep_end_layers") else set()
        zones = node.find("zone_layer_connections")
        if zones is not None:
            kept |= set(zones.values()) & set(layers)
        for layer in layers:
            if layer not in kept and self._meets(shape, net, layer):
                kept.add(layer)
        return kept

    def _meets(self, shape: Shape, net: str, layer: str) -> bool:
        """Whether a track of the net ends on or crosses the shape on a layer."""
        if not net:
            return False
        x1, y1, x2, y2 = shape.bbox()
        for track in self._tracks_by_layer.get((layer, net), ()):
            tx1, ty1, tx2, ty2 = track[0].bbox()
            if tx2 < x1 or tx1 > x2 or ty2 < y1 or ty1 > y2:
                continue
            from .shapes import distance  # noqa: PLC0415

            if any(distance(piece, shape) == 0 for piece in track):
                return True
        return False

    def _build(self) -> None:
        board = self.board
        self._tracks_by_layer: dict[tuple[str, str], list[list[Shape]]] = defaultdict(list)
        tracks = []
        for track in board.tracks:
            if track.kind == "arc" and track.mid is not None:
                pieces = arc_chords(track.start, track.mid, track.end, track.width)
            else:
                pieces = [Shape((track.start, track.end), track.width / 2)]
            tracks.append((track, pieces))
            self._tracks_by_layer[(track.layer, track.net)].append(pieces)
        for fp in board.footprints:
            own = _clearance_of(fp.node)
            start = len(self.things)
            for pad in fp.pads:
                self._pad(fp, pad, own)
            self._footprint_graphics(fp)
            groups = fp.node.find("net_tie_pad_groups")
            if groups is not None:
                ties = tuple(frozenset(g.replace(" ", "").split(",")) for g in groups.values())
                for thing in self.things[start:]:
                    thing.ties = ties
        for track, pieces in tracks:
            if track.layer in self.copper:
                self.things.append(
                    Thing(
                        track.kind,
                        track.net,
                        describe.track(board, track),
                        track,
                        pieces={track.layer: pieces},
                    )  # fmt: skip
                )
        for via in board.vias:
            disc = Shape((via.position,), via.diameter / 2)
            layers = [layer for layer in via.layers if layer in self.copper]
            used = self._used(via.node, layers, disc, via.net)
            self.things.append(
                Thing(
                    "via",
                    via.net,
                    describe.via(board, via),
                    via,
                    pieces={layer: [disc] for layer in layers if layer in used},
                    hole=via_hole(via),
                    plated=True,
                )  # fmt: skip
            )
        for zone in board.zones:
            if zone.rule_area:
                continue
            fills = {
                layer: [Polygon(p) for p in polygons if len(p) >= 3]
                for layer, polygons in zone.filled.items()
                if layer in self.copper
            }
            fills = {layer: polygons for layer, polygons in fills.items() if polygons}
            if not fills:
                continue
            connect = zone.node.find("connect_pads")
            clearance = _clearance_of(connect)
            self.things.append(
                Thing(
                    "fill",
                    zone.net,
                    describe.zone(board, zone),
                    zone,
                    fills=fills,
                    zone_clearance=clearance,
                )  # fmt: skip
            )
        for node in board.root.lists():
            head = node.head
            if not head.startswith("gr_") or head[3:] not in GRAPHICS:
                continue
            layer = node.find("layer")
            layer = layer.value(1) if layer is not None else None
            if layer not in self.copper:
                continue
            pieces, areas = graphic_copper(node)
            if pieces or areas:
                position = pieces[0].core[0] if pieces else areas[0].points[0]
                self.things.append(
                    Thing(
                        "shape",
                        board.net_of(node),
                        describe.shape(board, node, _rounded(position)),
                        node,
                        pieces={layer: pieces} if pieces else {},
                        areas={layer: areas} if areas else {},
                        filled=is_filled(node),
                    )  # fmt: skip
                )

    def _pad(self, fp: Footprint, pad: Pad, footprint_clearance: int | None) -> None:
        board = self.board
        layers = [layer for layer in pad.layers if layer in self.copper]
        own = _clearance_of(pad.node)
        override = (
            (own, "pad") if own is not None
            else (footprint_clearance, f"footprint {describe.reference(fp)}")
            if footprint_clearance is not None else None
        )  # fmt: skip
        drilled = hole(pad) if pad.kind in ("thru_hole", "np_thru_hole") else None
        thing = Thing("pad", pad.net, describe.pad(board, fp, pad), pad, fp=fp, hole=drilled,
                      plated=pad.kind == "thru_hole", override=override)  # fmt: skip
        if pad.kind == "np_thru_hole":
            # No copper; its shape stands against other items' holes.
            thing.npth = pad_copper(pad)
        elif layers:
            pieces = pad_copper(pad)
            used = set(layers)
            if pad.kind == "thru_hole" and len(pieces) == 1:
                used = self._used(pad.node, layers, pieces[0], pad.net)
            thing.pieces = {layer: pieces for layer in layers if layer in used}
        self.things.append(thing)

    def _footprint_graphics(self, fp: Footprint) -> None:
        board = self.board
        source = board.document.source
        span = board.document.span(fp.node)
        if span is not None and source.find(".Cu", *span) < 0:
            return
        for node in fp.node.lists():
            head = node.head
            if not head.startswith("fp_") or head[3:] not in GRAPHICS:
                continue
            inner = board.document.span(node)
            if inner is not None and source.find(".Cu", *inner) < 0:
                continue
            layer = node.find("layer")
            layer = layer.value(1) if layer is not None else None
            if layer not in self.copper:
                continue

            def place(points, fp=fp):
                return [tuple(p) for p in geometry.place(points, *fp.position, fp.angle)]

            pieces, areas = graphic_copper(node, place)
            if pieces or areas:
                position = pieces[0].core[0] if pieces else areas[0].points[0]
                self.things.append(
                    Thing(
                        "shape",
                        board.net_of(node),
                        describe.shape(board, node, _rounded(position), fp),
                        node,
                        fp=fp,
                        pieces={layer: pieces} if pieces else {},
                        areas={layer: areas} if areas else {},
                        filled=is_filled(node),
                    )  # fmt: skip
                )

    # -- nets -------------------------------------------------------------------------------

    def _assign_nets(self) -> None:
        """The net each item is checked on: its own, unless no pad of its net
        is joined to it -- then the net of the pads its copper touches."""
        for thing in self.things:
            thing.net = thing.own_net
        self._footprint_copper()
        anchored = self._anchored()
        loose = [
            t for t in self.things
            if t.kind in ("segment", "arc", "via") and t.own_net and id(t.owner) not in anchored
        ]  # fmt: skip
        if not loose:
            return
        # Which copper touches which, whatever its net: tracks, vias and pads of a net.
        members = [t for t in self.things if t.kind in ("segment", "arc", "via") or (
            t.kind == "pad" and t.own_net and t.pieces)]  # fmt: skip
        grid: dict[tuple[str, int, int], list[int]] = defaultdict(list)
        cell = connectivity.CELL
        for index, thing in enumerate(members):
            for layer in thing.pieces:
                x1, y1, x2, y2 = thing.box(layer)
                for cx in range(int(x1) // cell, int(x2) // cell + 1):
                    for cy in range(int(y1) // cell, int(y2) // cell + 1):
                        grid[(layer, cx, cy)].append(index)
        position = {id(t): i for i, t in enumerate(members)}
        done: set[int] = set()
        for start in loose:
            first = position.get(id(start))
            if first is None or first in done:
                continue
            cluster = self._cluster(members, grid, first)
            done |= cluster
            things = [members[i] for i in cluster]
            pads = sorted({t.own_net for t in things if t.kind == "pad"})
            nets = sorted({t.own_net for t in things if t.own_net})
            net = pads[-1] if pads else nets[0]
            for thing in things:
                if thing.kind != "pad" and id(thing.owner) not in anchored:
                    thing.net = net

    def _footprint_copper(self) -> None:
        """A footprint's copper drawing has no net. Across pads of two nets
        of a net-tie footprint it joins them, as the footprint means it to
        (`tied`). Across two pads of any other footprint it bridges them
        (`tests/fixtures/drc/drc2`, and KiCad's microwave demo): a filled
        drawing touching them is a short whatever their nets, a stroke a
        clearance of nothing."""
        from .clearance import thing_distance  # noqa: PLC0415

        pads: dict[int, list[Thing]] = defaultdict(list)
        for thing in self.things:
            if thing.kind == "pad" and thing.fp is not None:
                pads[id(thing.fp)].append(thing)
        for thing in self.things:
            if thing.kind != "shape" or thing.fp is None or thing.own_net:
                continue
            touched = [
                pad for pad in pads.get(id(thing.fp), ())
                if any(thing_distance(thing, pad, layer, 0) == 0
                       for layer in thing.layers & set(pad.pieces))
            ]  # fmt: skip
            if thing.ties:
                thing.tied = frozenset(pad.own_net for pad in touched if pad.own_net)
            elif len(touched) > 1:
                thing.bridges = "short" if thing.filled else "clearance"

    def _anchored(self) -> set[int]:
        """The tracks and vias whose copper reaches a pad of their own net."""
        joined = self.joined
        with_pad = {
            joined.cluster_of[i] for i, item in enumerate(joined.items)
            if item.kind == "pad" and item.net
        }  # fmt: skip
        return {
            id(item.owner) for i, item in enumerate(joined.items)
            if item.kind in ("segment", "arc", "via") and joined.cluster_of[i] in with_pad
        }  # fmt: skip

    def _cluster(self, members, grid, first: int) -> set[int]:
        from .shapes import distance  # noqa: PLC0415

        cell = connectivity.CELL
        found = {first}
        todo = [first]
        while todo:
            index = todo.pop()
            thing = members[index]
            for layer, pieces in thing.pieces.items():
                x1, y1, x2, y2 = thing.box(layer)
                near = set()
                for cx in range(int(x1) // cell, int(x2) // cell + 1):
                    for cy in range(int(y1) // cell, int(y2) // cell + 1):
                        near.update(grid.get((layer, cx, cy), ()))
                for other in near - found:
                    target = members[other].pieces.get(layer)
                    if target and any(distance(a, b) == 0 for a in pieces for b in target):
                        found.add(other)
                        todo.append(other)
        return found


def _rounded(point) -> tuple[int, int]:
    return round(point[0]), round(point[1])
