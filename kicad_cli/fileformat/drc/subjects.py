"""Items as the conditions of a board's custom rules see them: `A.Type`,
`A.NetClass`, `A.intersectsArea('name')` (`rules.py`, `expression.py`).

A subject answers for one item on one layer -- an item on several, a pad
or a via, is asked about the layer a check is on, as `A.Layer == B.Layer`
asks about the layer two items meet on.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..connectivity import Polygon
from .expression import Unknown, matches
from .shapes import Shape

TYPES = {"segment": "Track", "arc": "Track", "via": "Via", "pad": "Pad", "fill": "Zone",
         "shape": "Graphic"}  # fmt: skip
PAD_TYPES = {"smd": "SMD", "thru_hole": "Through-hole", "np_thru_hole": "NPTH, mechanical",
             "connect": "Edge connector"}  # fmt: skip


@dataclass
class Subject:
    """One item: what it is, its net, its layers, its copper by layer."""

    run: object
    kind: str
    net: str
    layers: frozenset[str]
    pieces: dict[str, list[Shape]] = field(default_factory=dict)
    areas: dict[str, list[Polygon]] = field(default_factory=dict)
    width: int | None = None
    pad_kind: str = ""
    via_kind: str = ""
    footprint: str | None = None  # the reference of the footprint it belongs to

    def on(self, layer: str | None) -> _Bound:
        return _Bound(self, layer)


@dataclass
class _Bound:
    subject: Subject
    layer: str | None

    def get(self, name: str):
        s = self.subject
        if name == "Type":
            return TYPES.get(s.kind, s.kind)
        if name == "NetName":
            return s.net
        if name == "NetClass":
            # Each of a net's classes: 'Wide' and 'Hot' both match a net in
            # Wide and Hot; 'Wide,Hot' does not.
            return _class_names(s.run, s.net)
        if name == "Layer":
            layer = self.layer if self.layer is not None else next(iter(sorted(s.layers)), "")
            return [layer, s.run.board.layer_name(layer)]
        if name == "Width":
            if s.width is None:
                raise Unknown("Width")
            return s.width
        if name == "Pad_Type":
            return PAD_TYPES.get(s.pad_kind, "")
        if name == "Reference":
            return s.footprint or ""
        raise Unknown(name)

    def call(self, name: str, args: list):
        s = self.subject
        first = str(args[0]) if args else ""
        if name in ("intersectsArea", "insideArea"):
            return any(_meets(s, zone) for zone in _areas(s.run, first))
        if name == "enclosedByArea":
            return any(_within(s, zone) for zone in _areas(s.run, first))
        if name == "inDiffPair":
            return _in_pair(s.run, s.net, first)
        if name == "isPlated":
            return s.kind == "via" or s.pad_kind == "thru_hole"
        if name == "existsOnLayer":
            return any(matches(first, layer) for layer in s.layers)
        if name == "memberOfFootprint":
            return s.footprint is not None and matches(first, s.footprint)
        if name == "hasNetclass":
            return first in _class_names(s.run, s.net)
        if name == "isMicroVia":
            return s.via_kind == "micro"
        if name == "isBlindBuriedVia":
            return s.via_kind in ("blind", "buried")
        raise Unknown(name)


def _class_names(run, net: str) -> list[str]:
    parts = run.settings.netclasses.of(net).name.split(",")
    explicit = [p for p in parts if p != "Default"]
    return explicit or ["Default"]


def _areas(run, name: str) -> list:
    cache = getattr(run, "_named_areas", None)
    if cache is None:
        cache = run._named_areas = {}
        for zone in run.board.zones:
            if zone.name:
                cache.setdefault(zone.name, []).append(zone)
    return [zone for key, zones in cache.items() if matches(name, key) for zone in zones]


def _outline(zone) -> list[Polygon]:
    return [Polygon(points) for points in zone.outlines if len(points) >= 3]


def _meets(subject: Subject, zone) -> bool:
    from .clearance import fill_distance, fills_distance  # noqa: PLC0415

    for layer in subject.layers & set(zone.layers):
        for outline in _outline(zone):
            pieces = subject.pieces.get(layer, [])
            if pieces and fill_distance(pieces, outline, 0) <= 0:
                return True
            if any(fills_distance(area, outline, 0) <= 0 for area in subject.areas.get(layer, [])):
                return True
    return False


def _within(subject: Subject, zone) -> bool:
    for layer in subject.layers & set(zone.layers):
        for outline in _outline(zone):
            pieces = subject.pieces.get(layer, [])
            points = [p for piece in pieces for p in piece.core]
            points += [p for area in subject.areas.get(layer, []) for p in area.points]
            if points and all(outline.contains(*p) for p in points):
                return True
    return False


def _in_pair(run, net: str, pattern: str) -> bool:
    if len(net) < 2 or net[-1] not in "PN+-":
        return False
    partner = net[:-1] + {"P": "N", "N": "P", "+": "-", "-": "+"}[net[-1]]
    nets = getattr(run, "_net_names", None)
    if nets is None:
        board = run.board
        nets = run._net_names = (
            {t.net for t in board.tracks}
            | {v.net for v in board.vias}
            | {p.net for fp in board.footprints for p in fp.pads}
        )
    if partner not in nets:
        return False
    base = net[:-1]
    return matches(pattern, base) or matches(pattern, base.rstrip("_"))


# -- the subjects of a board's items ---------------------------------------------------------


def of_thing(run, thing) -> Subject:
    """A piece of copper (`copper.Thing`) as a rule sees it."""
    pad = thing.owner if thing.kind == "pad" else None
    via = thing.owner if thing.kind == "via" else None
    return Subject(
        run,
        thing.kind,
        thing.net,
        frozenset(thing.layers),
        pieces=thing.pieces,
        areas={k: thing.fills.get(k, []) + thing.areas.get(k, []) for k in thing.layers},
        width=getattr(thing.owner, "width", None) if thing.kind in ("segment", "arc") else None,
        pad_kind=pad.kind if pad is not None else "",
        via_kind=via.kind if via is not None else "",
        footprint=thing.fp.reference if thing.fp is not None else None,
    )


def of_track(run, track, pieces=None) -> Subject:
    return Subject(run, track.kind, track.net, frozenset({track.layer}),
                   pieces={track.layer: pieces or []}, width=track.width)  # fmt: skip


def of_via(run, via) -> Subject:
    disc = [Shape((via.position,), via.diameter / 2)]
    layers = frozenset(via.layers)
    return Subject(run, "via", via.net, layers, pieces={layer: disc for layer in layers},
                   via_kind=via.kind)  # fmt: skip


def of_pad(run, fp, pad) -> Subject:
    from .shapes import pad_copper  # noqa: PLC0415

    copper = [layer for layer in pad.layers if layer.endswith(".Cu")]
    shapes = pad_copper(pad) if pad.kind != "np_thru_hole" else []
    return Subject(run, "pad", pad.net, frozenset(pad.layers),
                   pieces={layer: shapes for layer in copper}, pad_kind=pad.kind,
                   footprint=fp.reference)  # fmt: skip
