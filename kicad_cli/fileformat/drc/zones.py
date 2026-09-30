"""Zones that overlap with nothing to say which fills first.

Measured on `tests/fixtures/drc/drc1`: two zones of one net -- no net
counting as one -- that share a copper layer and a priority intersect when
their outlines meet at all, an edge in common included. Zones of two nets
do not: their fills keep apart by clearance. A rule area is not a zone here.
"""

from __future__ import annotations

from ..connectivity import Polygon
from . import items as describe


def intersecting(run) -> None:
    if not run.on("zones_intersect"):
        return
    board = run.board
    copper = set(board.copper_layers)
    zones = [
        (zone, set(zone.layers) & copper, [Polygon(o) for o in zone.outlines if len(o) >= 3])
        for zone in board.zones
        if not zone.rule_area
    ]
    for i, (a, layers_a, outline_a) in enumerate(zones):
        for b, layers_b, outline_b in zones[i + 1 :]:
            if a.priority != b.priority or a.net != b.net or not layers_a & layers_b:
                continue
            if any(p.touches(q) for p in outline_a for q in outline_b):
                run.report(
                    "zones_intersect",
                    "Copper zones intersect (intersecting zones must have distinct priorities)",
                    [describe.zone(board, a), describe.zone(board, b)],
                )
