"""``board plane``, in this process: is there copper under each track?

A port of `payload/plane.py`, which ran on pcbnew; its output is identical on
every board KiCad ships (`tests/test_native_plane.py`), mistakes included --
see DEVELOPMENT_STATUS.md for the ones found while porting.

The defect this looks for is one DRC never reports. A track can be legal in
width, spacing and connection, and the layer under it still have a gap: an
antipad row run together, a track cut through the plane. The return current
goes around the gap, and the loop it encloses is what radiates.

Layers are taken in physical order, F.Cu, In1.Cu ... B.Cu. KiCad numbers
them otherwise (F.Cu 0, B.Cu 2, In1.Cu 4), and "the layer next to this one"
by number is wrong on every board with inner layers.
"""

from __future__ import annotations

from typing import Any

from .. import envelope
from ..fileformat import geometry
from ..fileformat.board import Board, Zone
from ..fileformat.connectivity import Polygon
from . import load_board

# Sample spacing along a track. Denser gains little; sparser misses narrow
# slots -- 0.5 mm is about the width of an 0402 pad.
STEP_MM = 0.5
# How much of the board a layer's pours must cover to call it a plane rather
# than a signal layer with some copper poured on it.
PLANE_COVERAGE = 0.40
# The grid gaps are gathered on: one hole in a plane hits every track across
# it, and should be reported as one thing.
CLUSTER_MM = 5.0
NM = 1_000_000


def mm(value: float) -> float:
    return value / NM


class _Fill:
    """One zone's fill on one layer, and the net it carries."""

    __slots__ = ("net", "polygons", "area")

    def __init__(self, zone: Zone, layer: str) -> None:
        self.net = zone.net
        outlines = zone.filled.get(layer, [])
        self.polygons = [Polygon(points) for points in outlines if len(points) >= 3]
        self.area = sum(geometry.area(points) for points in outlines)

    def contains(self, x: int, y: int) -> bool:
        return any(polygon.contains(x, y) for polygon in self.polygons)


def zone_fills(board: Board, layer: str) -> list[_Fill]:
    """Every pour's fill on the layer, with its net. A rule area is not copper."""
    out = []
    for zone in board.zones:
        if zone.rule_area or layer not in zone.layers:
            continue
        if zone.filled.get(layer):
            out.append(_Fill(zone, layer))
    return out


def covered_by(fills: list[_Fill], x: int, y: int) -> str | None:
    """The net of the pour the point lies in. A hole -- an antipad, a channel
    cut through the plane -- is outside it."""
    for fill in fills:
        if fill.contains(x, y):
            return fill.net
    return None


def stitch_caps(board: Board, plane_nets: set[str]) -> list[dict[str, Any]]:
    """Capacitors that let the return current change planes at a split.

    The planes either side of a split (+3V3 and VSYS, say) are each tied to
    ground by their decoupling, so a capacitor near the crossing lets the
    return current move across close by instead of going round the end of
    the split. That distance is what decides whether a crossing matters.
    """
    out = []
    for fp in board.footprints:
        # Only a capacitor conducts at the frequencies that matter. Without
        # this, anything with one pin on a plane and one on ground counted --
        # and the nearest "stitch" on one board was a push button, open
        # whenever it is not pressed.
        if not fp.reference.upper().startswith("C"):
            continue
        nets = {pad.net for pad in fp.pads}
        touching = nets & plane_nets
        if not touching:
            continue
        # Best is a capacitor across two planes; next, a plane's decoupling to ground.
        if len(touching) >= 2:
            kind = "bridges_planes"
        elif nets & {"GND", "GNDA", "AGND", "DGND"}:
            kind = "plane_to_ground"
        else:
            continue
        out.append(
            {
                "ref": fp.reference,
                "value": fp.value,
                "kind": kind,
                "x": mm(fp.position[0]),
                "y": mm(fp.position[1]),
            }
        )
    return out


def nearest_stitch(caps: list[dict[str, Any]], x: float, y: float) -> dict[str, Any] | None:
    best = None
    for c in caps:
        d = ((c["x"] - x) ** 2 + (c["y"] - y) ** 2) ** 0.5
        if best is None or d < best[0]:
            best = (d, c)
    if best is None:
        return None
    d, c = best
    return {"ref": c["ref"], "value": c["value"], "kind": c["kind"], "distance_mm": round(d, 1)}


def run(path: str, step: float = STEP_MM) -> dict[str, Any]:
    board = load_board(path)
    name = board.layer_name
    order = board.copper_layers
    if len(order) < 2:
        envelope.fail("E_VALIDATION", "单层板没有参考平面可查", {"copper_layers": len(order)})

    edge = board.edge_bbox() or (0, 0, 0, 0)
    board_area = mm(edge[2] - edge[0]) * mm(edge[3] - edge[1])

    fills_by_layer = {layer: zone_fills(board, layer) for layer in order}
    coverage = {}
    for layer in order:
        area = sum(fill.area for fill in fills_by_layer[layer])
        coverage[layer] = (mm(1) * mm(1) * area / board_area) if board_area else 0.0
    planes = [layer for layer in order if coverage[layer] >= PLANE_COVERAGE]

    # A track's reference is the layer physically next below it; the bottom
    # layer's is the one above. An earlier version sorted layers into planes
    # and signals by coverage and analysed only signals: on a board whose four
    # layers were all 79-93% poured, it analysed nothing. The return current
    # takes the adjacent layer whatever it is called; coverage stays as context.
    reference_of = {}
    for i, layer in enumerate(order):
        pick = order[i + 1] if i + 1 < len(order) else (order[i - 1] if i else None)
        if pick is not None:
            reference_of[layer] = pick

    # The nets the pours themselves carry: their tracks crossing a split is no
    # return-path problem -- a GND track passing over +3V3 then VSYS breaks no
    # loop. Reporting them alongside signals was noise.
    plane_nets = {fill.net for fills in fills_by_layer.values() for fill in fills}
    caps = stitch_caps(board, plane_nets)

    gaps, splits = [], []
    per_net: dict[str, dict[str, Any]] = {}
    total_samples = ok_samples = 0

    for track in board.tracks:
        if track.kind != "segment":
            continue
        ref = reference_of.get(track.layer)
        if ref is None:
            continue
        net = track.net
        (ax, ay), (bx, by) = track.start, track.end
        length = mm(track.length())
        if length <= 0:
            continue
        n = max(2, int(length / step) + 1)
        fills = fills_by_layer[ref]
        seen_nets = set()
        bad = 0
        for k in range(n):
            f = k / (n - 1)
            under = covered_by(fills, int(ax + (bx - ax) * f), int(ay + (by - ay) * f))
            total_samples += 1
            if under is None:
                bad += 1
            else:
                ok_samples += 1
                seen_nets.add(under)

        stat = per_net.setdefault(net, {"net": net, "samples": 0, "unbacked": 0, "length_mm": 0.0})
        stat["samples"] += n
        stat["unbacked"] += bad
        stat["length_mm"] += length

        at = [round(mm(ax), 3), round(mm(ay), 3)]
        if bad:
            gaps.append(
                {
                    "net": net,
                    "layer": name(track.layer),
                    "reference": name(ref),
                    "at_mm": at,
                    "length_mm": round(length, 3),
                    "unbacked_fraction": round(bad / n, 3),
                }
            )
        if len(seen_nets) > 1 and net not in plane_nets:
            splits.append(
                {
                    "net": net,
                    "layer": name(track.layer),
                    "reference": name(ref),
                    "at_mm": list(at),
                    "crosses": sorted(seen_nets),
                    # How far to the nearest place the return can change
                    # planes: that decides whether this is a problem.
                    "nearest_stitch": nearest_stitch(caps, at[0], at[1]),
                }
            )

    worst = sorted(
        (v for v in per_net.values() if v["unbacked"]),
        key=lambda v: -v["unbacked"] / max(1, v["samples"]),
    )
    for v in worst:
        v["unbacked_fraction"] = round(v["unbacked"] / max(1, v["samples"]), 3)
        v["length_mm"] = round(v["length_mm"], 2)

    gaps.sort(key=lambda g: -g["unbacked_fraction"] * g["length_mm"])

    # One hole in a plane hits every track across it. Four hundred findings of
    # one hole is four hundred checks of one thing; gathered by position, the
    # answer is "the plane has holes here".
    clusters: dict[tuple, dict[str, Any]] = {}
    for g in gaps:
        key = (
            g["layer"],
            g["reference"],
            int(g["at_mm"][0] // CLUSTER_MM),
            int(g["at_mm"][1] // CLUSTER_MM),
        )
        c = clusters.setdefault(
            key,
            {
                "layer": g["layer"],
                "reference": g["reference"],
                "near_mm": [
                    round(key[2] * CLUSTER_MM + CLUSTER_MM / 2, 1),
                    round(key[3] * CLUSTER_MM + CLUSTER_MM / 2, 1),
                ],
                "segments": 0,
                "nets": set(),
            },
        )
        c["segments"] += 1
        c["nets"].add(g["net"])
    hot = sorted(clusters.values(), key=lambda c: -c["segments"])
    for c in hot:
        c["nets"] = sorted(c["nets"])[:12]

    findings = []
    if gaps:
        findings.append(
            {
                "id": "P1-no-reference",
                "severity": "warn",
                "title": "走线下方的参考平面有缺口",
                "detail": "这些线段下方那一层没有铜。回流电流必须绕行，绕出的环路面积"
                "就是辐射源。DRC 不检查这件事，间距和连通性都合规。",
                "evidence": {
                    "segments": len(gaps),
                    "hotspots": hot[:8],
                    "worst_nets": worst[:10],
                },
                "confidence": "measured",
                "fix": "要么把走线挪到有平面支撑的区域，要么补平面上的缺口；高速或大电流网络优先",
            }
        )
    if splits:
        findings.append(
            {
                "id": "P2-crosses-split",
                "severity": "warn",
                "title": "走线跨越了参考平面上的分割",
                "detail": "线段下方的参考铜从一个网络变成了另一个。回流要换层只能借道"
                "跨接电容，绕出去的距离就是额外环路。铺铜自身的网络已排除——一条 GND "
                "走线经过分割上方不构成回流问题。`nearest_stitch` 给出每处最近的换层点"
                "有多远：几毫米以内影响有限，十几毫米以上对快沿信号才真正要紧。",
                "evidence": {
                    "segments": len(splits),
                    "stitch_candidates": len(caps),
                    "bridging_caps": sum(1 for c in caps if c["kind"] == "bridges_planes"),
                    "sample": splits[:10],
                },
                "confidence": "measured",
                "fix": "按信号的边沿速率排序处理：时钟、快沿总线优先改走参考完整的层，"
                "或在跨越点就近补一个跨接电容；静态信号（strap、使能、静音）可以接受",
            }
        )

    return {
        "board": path,
        "stackup": [name(x) for x in order],
        "plane_layers": [name(x) for x in planes],
        "coverage": {name(x): round(coverage[x], 3) for x in order},
        "reference_map": {name(k): name(v) for k, v in reference_of.items()},
        "samples": total_samples,
        "backed_fraction": round(ok_samples / total_samples, 4) if total_samples else None,
        "segments_without_reference": len(gaps),
        "segments_crossing_split": len(splits),
        "worst_nets": worst[:20],
        "stitch_candidates": len(caps),
        "hotspots": hot[:20],
        "gaps": gaps[:40],
        "splits": splits[:40],
        "findings": findings,
        "status": "PASS" if not (gaps or splits) else "FAIL",
        "not_checked": [
            "只看物理上最近的那个平面层。三层以上叠层里，真正的回流层可能是另一层",
            "不计算环路面积，也不看边沿速率。跨分割要不要紧取决于信号有多快，"
            "工具只给出「跨了」和「最近换层点多远」两个事实，快慢要由人判断",
            "跨接电容只按位置找，没有核算它在关心的频段上阻抗够不够低",
            "过孔的换层不在此列——那需要看伴地孔，属于 board via 的范围",
            "铺铜必须已经填充过。未填充的区在这里等同于没有铜，"
            "这是保守的方向，但会把「忘了填充」报成「平面有缺口」",
        ],
    }
