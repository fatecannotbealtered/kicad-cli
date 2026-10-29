"""Solder mask: openings that let solder run from one net's copper to
another's.

What opens the mask on a side: a pad on the side's mask layer, grown by its
margin -- its own, else its footprint's, else the board's
(`pad_to_mask_clearance`); a via not tented on that side, and a drawing on
the mask layer, each grown by the board's margin. Everything else on the
side's copper lies under the mask: tracks, tented vias, zone fills, copper
drawings, and pads without the mask layer -- which KiCad holds by their
copper grown by their margin all the same.

An opening bridges two items of different nets -- no net counting as a net
of its own -- when it comes closer to another item's opening than the
board's minimum web (`solder_mask_min_width`), or closer to copper under the
mask than the project's `solder_mask_to_copper_clearance`; where either is
0, when they overlap. Touching is not overlapping, and nothing is rounded:
an opening 0.0995 mm from another falls short of a web of 0.1 mm. The
margin and the web are the board's own settings; KiCad does not read the
project's copies of them.

A drawing's opening has no net of its own: it takes the net of the first
item it lays bare, and bridges when it lays bare an item of another net --
reported, as KiCad reports it, with that first item (KiCad reports the pair
again for each further item; here it is once). Which item is first KiCad
leaves to chance: run twice, it names different ones; here it is the first
in the board's order. Items of its footprint the board lets it bare, as
below, give it no net.

A footprint that allows bridges itself (`allow_soldermask_bridges`) answers
for none of its openings, whatever they lay bare -- a solder jumper's, say;
the board's allowance (`allow_soldermask_bridges_in_footprints`) lets only
items of one footprint share its openings. A net-tie footprint's copper may
share them too, and so may the pieces of one pad. An unplated hole lays
bare no copper, tenting a pad changes nothing, and a zone on a mask layer
opens nothing. Nets are those KiCad's connectivity gives: a track laid over
a pad of another net and no pad of its own is of the pad's net
(`copper.py`).

Measured on `tests/fixtures/drc/drc4` and KiCad's demo boards.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from .. import geometry
from ..board import nm
from ..connectivity import Polygon
from . import items as describe
from .clearance import fill_distance, fills_distance, pieces_distance
from .copper import GRAPHICS, Thing, graphic_copper
from .shapes import Shape, pad_copper

CELL = 2_000_000
SIDES = (("F", "Front"), ("B", "Rear"))
SHY = 0.5  # nm: KiCad measures in whole nanometres, so "closer than" is a nanometre closer


@dataclass(eq=False)
class _Region:
    """An opening in the mask, or copper under it: convex pieces, and
    polygons whose growth their edges among the pieces already are."""

    thing: Thing | None  # None: a drawing on the mask layer
    item: describe.Item
    net: str | None  # None: an opening with no net of its own
    fp: object
    order: int  # its place on the board, drawings last
    pieces: list[Shape]
    areas: list[Polygon] = field(default_factory=list)
    box: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    grown: dict[float, tuple[list[Shape], list[Polygon]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        boxes = [p.box for p in self.pieces] + [a.bbox for a in self.areas]
        self.box = (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )

    def by(self, extra: float) -> tuple[list[Shape], list[Polygon]]:
        """This region grown by `extra` more, or shrunk when it is negative."""
        found = self.grown.get(extra)
        if found is None:
            found = self.grown[extra] = _grow(self.pieces, self.areas, extra)
        return found


# -- growing and shrinking ------------------------------------------------------------------


def _grow(pieces: list[Shape], areas: list[Polygon], by: float):
    """Pieces and polygons grown by `by`: a polygon grows by its edges, and
    is not shrunk; a piece shrunk to nothing is gone."""
    grown = [g for g in (_grow_shape(p, by) for p in pieces) if g is not None]
    if by > 0:
        for area in areas:
            points = area.points
            grown += [Shape((points[i - 1], points[i]), by) for i in range(len(points))]
    return grown, list(areas)


def _grow_shape(shape: Shape, by: float) -> Shape | None:
    radius = shape.radius + by
    if radius >= 0:
        return Shape(shape.core, radius)
    if len(shape.core) < 3:
        return None
    core = _inset(shape.core, -radius)
    return Shape(core, 0.0) if core else None


def _inset(core, by: float) -> tuple:
    """A convex polygon with every edge moved `by` inwards; () when nothing
    is left."""
    twice = sum(core[i - 1][0] * core[i][1] - core[i][0] * core[i - 1][1] for i in range(len(core)))
    if twice == 0:
        return ()
    sign = 1 if twice > 0 else -1
    polygon = list(core)
    for i in range(len(core)):
        (ax, ay), (bx, by_) = core[i - 1], core[i]
        length = math.hypot(bx - ax, by_ - ay)
        if length == 0:
            continue
        nx, ny = -(by_ - ay) * sign / length, (bx - ax) * sign / length  # inwards
        polygon = _clip(polygon, ax + nx * by, ay + ny * by, nx, ny)
        if len(polygon) < 3:
            return ()
    return tuple(polygon)


def _clip(polygon, ax: float, ay: float, nx: float, ny: float) -> list:
    """What of a convex polygon lies on the inner side of a line through
    (ax, ay) with inward normal (nx, ny)."""
    out = []
    for i in range(len(polygon)):
        p, q = polygon[i - 1], polygon[i]
        fp = (p[0] - ax) * nx + (p[1] - ay) * ny
        fq = (q[0] - ax) * nx + (q[1] - ay) * ny
        if (fp < 0) != (fq < 0):
            t = fp / (fp - fq)
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
        if fq >= 0:
            out.append(q)
    return out


# -- the board's settings -------------------------------------------------------------------


def _setup(board):
    """The board's mask margin and minimum web, whether its footprints may
    bridge pads, and on which sides its vias are tented."""
    setup = board.root.find("setup")

    def length(key: str) -> int:
        found = setup.find(key) if setup is not None else None
        return nm(found.atom(1)) if found is not None and found.atom(1) is not None else 0

    allow = setup.find("allow_soldermask_bridges_in_footprints") if setup is not None else None
    tented = {"F": True, "B": True}
    tenting = setup.find("tenting") if setup is not None else None
    if tenting is not None:
        words = tenting.values()  # the older form names the tented sides: (tenting front back)
        for side, word in (("F", "front"), ("B", "back")):
            found = tenting.find(word)
            tented[side] = found.value(1) != "no" if found is not None else word in words
    return (
        length("pad_to_mask_clearance"),
        length("solder_mask_min_width"),
        allow is not None and allow.value(1) == "yes",
        tented,
    )


def _margin(board_margin: int, *nodes) -> int:
    """A pad's mask margin: its own, else its footprint's, else the board's."""
    for node in nodes:
        found = node.find("solder_mask_margin") if node is not None else None
        if found is not None and found.atom(1) is not None:
            return nm(found.atom(1))
    return board_margin


def _tented(via, side: str, board_default: bool) -> bool:
    tenting = via.node.find("tenting")
    found = tenting.find("front" if side == "F" else "back") if tenting is not None else None
    value = found.value(1) if found is not None else "none"
    return board_default if value == "none" else value != "no"


# -- the check ------------------------------------------------------------------------------


def check(run) -> None:
    if not run.on("solder_mask_bridge"):
        return
    board = run.board
    margin, web, allow_all, tented = _setup(board)
    to_copper = run.settings.nm("solder_mask_to_copper_clearance")
    things = run.copper().things
    for side, name in SIDES:
        openings, covered = _regions(board, things, side, margin, tented[side])
        message = f"{name} solder mask aperture bridges items with different nets"
        _bridges(run, openings, covered, web, to_copper, allow_all, message)


def _regions(board, things: list[Thing], side: str, margin: int, tented: bool):
    """A side's openings in the mask, and the copper under it."""
    layer, mask = f"{side}.Cu", f"{side}.Mask"
    openings: list[_Region] = []
    covered: list[_Region] = []
    for order, thing in enumerate(things):
        if thing.kind == "pad":
            pad = thing.owner
            if pad.kind == "np_thru_hole":
                continue  # a hole: no copper to lay bare
            grow = _margin(margin, pad.node, thing.fp.node if thing.fp is not None else None)
            if mask in pad.layers:
                pieces, _ = _grow(pad_copper(pad), [], grow)
                if pieces:
                    openings.append(_Region(thing, thing.item, thing.net, thing.fp, order,
                                            pieces))  # fmt: skip
            elif layer in thing.pieces:
                pieces, _ = _grow(thing.pieces[layer], [], grow)
                if pieces:
                    covered.append(_Region(thing, thing.item, thing.net, thing.fp, order,
                                           pieces))  # fmt: skip
        elif thing.kind == "via":
            via = thing.owner
            if layer not in via.layers:
                continue
            if not _tented(via, side, tented):
                disc = [Shape((via.position,), via.diameter / 2)]
                pieces, _ = _grow(disc, [], margin)
                if pieces:
                    openings.append(_Region(thing, thing.item, thing.net, None, order, pieces))
            elif layer in thing.pieces:
                covered.append(_Region(thing, thing.item, thing.net, None, order,
                                       list(thing.pieces[layer])))  # fmt: skip
        else:
            pieces = thing.pieces.get(layer, [])
            areas = thing.fills.get(layer, []) + thing.areas.get(layer, [])
            if pieces or areas:
                covered.append(_Region(thing, thing.item, thing.net, thing.fp, order,
                                       list(pieces), areas))  # fmt: skip
    order = len(things)
    for node, fp in _drawings(board, mask):
        place = None
        if fp is not None:

            def place(points, fp=fp):
                return [tuple(p) for p in geometry.place(points, *fp.position, fp.angle)]

        pieces, areas = graphic_copper(node, place)
        if not pieces and not areas:
            continue
        position = pieces[0].core[0] if pieces else areas[0].points[0]
        item = describe.shape(board, node, (round(position[0]), round(position[1])), fp)
        grown, areas = _grow(pieces, areas, margin)
        if grown or areas:
            openings.append(_Region(None, item, None, fp, order, grown, areas))
            order += 1
    return openings, covered


def _drawings(board, mask: str):
    """The drawings on a mask layer: the board's, then its footprints'."""
    for node in board.root.lists():
        if node.head.startswith("gr_") and node.head[3:] in GRAPHICS and _on(node, mask):
            yield node, None
    for fp in board.footprints:
        for node in fp.node.lists():
            if node.head.startswith("fp_") and node.head[3:] in GRAPHICS and _on(node, mask):
                yield node, fp


def _on(node, layer: str) -> bool:
    found = node.find("layer")
    return found is not None and found.value(1) == layer


def _apart(a, b, reach: float) -> bool:
    return a[2] + reach < b[0] or b[2] + reach < a[0] or a[3] + reach < b[1] or b[3] + reach < a[1]


def _touch(pieces: list[Shape], areas: list[Polygon], b: _Region) -> bool:
    if pieces and b.pieces and pieces_distance(pieces, b.pieces, 0) <= 0:
        return True
    for area in b.areas:
        if pieces and fill_distance(pieces, area, 0) <= 0:
            return True
    for area in areas:
        if b.pieces and fill_distance(b.pieces, area, 0) <= 0:
            return True
        if any(fills_distance(area, other, 0) <= 0 for other in b.areas):
            return True
    return False


def _same_net(a: _Region, b: _Region) -> bool:
    return bool(a.net) and a.net == b.net


def _exempt(a: _Region, b: _Region, allow_all: bool) -> bool:
    """Whether two items of one footprint may share an opening: any, where
    the board allows it in footprints; a net tie's copper; one pad drawn in
    pieces."""
    if a.fp is None or a.fp is not b.fp:
        return False
    things = [r.thing for r in (a, b) if r.thing is not None]
    if any(t.ties for t in things):
        return _net_tie(things)
    if len(things) == 2 and all(t.kind == "pad" for t in things):
        if things[0].owner.number == things[1].owner.number:
            return True
    return allow_all


def _net_tie(things: list[Thing]) -> bool:
    """Whether a net-tie footprint joins these on purpose: its drawings, and
    pads of one of its groups."""
    ties = things[0].ties
    pads = [t.owner.number for t in things if t.kind == "pad"]
    return all(any(number in group for group in ties) for number in pads) and (
        len(pads) < 2 or any(pads[0] in group and pads[1] in group for group in ties)
    )


def _bridges(run, openings, covered, web: int, to_copper: int, allow_all: bool,
             message: str) -> None:  # fmt: skip
    regions = openings + covered
    count = len(openings)
    reach = max(web, to_copper)
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for index, region in enumerate(regions):
        x1, y1, x2, y2 = region.box
        for cx in range(int(x1) // CELL, int(x2) // CELL + 1):
            for cy in range(int(y1) // CELL, int(y2) // CELL + 1):
                grid[(cx, cy)].append(index)
    seen: set = set()

    def report(a: _Region, b: _Region) -> None:
        key = frozenset((id(a.thing or a.item), id(b.thing or b.item)))
        if key not in seen:
            seen.add(key)
            run.report("solder_mask_bridge", message, [a.item, b.item])

    for index, a in enumerate(openings):
        if a.fp is not None and "allow_soldermask_bridges" in a.fp.attributes:
            continue  # a footprint that allows bridges answers for none of its openings
        x1, y1, x2, y2 = a.box
        near: set[int] = set()
        for cx in range(int(x1 - reach) // CELL, int(x2 + reach) // CELL + 1):
            for cy in range(int(y1 - reach) // CELL, int(y2 + reach) // CELL + 1):
                near.update(grid.get((cx, cy), ()))
        bared = []
        for other in sorted(near):
            b = regions[other]
            opening = other < count
            if other == index or opening and b.net is None:
                continue  # a drawing's opening answers for itself
            if a.net is not None and (_same_net(a, b) or _exempt(a, b, allow_all)):
                continue
            limit = web if opening else to_copper
            if _apart(a.box, b.box, limit):
                continue
            pieces, areas = a.by(limit - SHY)
            if _touch(pieces, areas, b):
                bared.append(b)
        if a.net is not None:
            for b in bared:
                report(a, b)
        else:
            # What its footprint may bare gives the opening no net.
            bared = sorted((b for b in bared if not _exempt(a, b, allow_all)),
                           key=lambda r: r.order)  # fmt: skip
            if any(not _same_net(bared[0], b) for b in bared[1:]):
                report(a, bared[0])
