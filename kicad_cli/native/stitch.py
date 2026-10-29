"""``board stitch``, in this process: a via for every island of a pour that
has none, so that its ground no longer hangs on a track.

A port of `payload/pcb_stitch.py`, which ran on pcbnew. Where it puts each via
is the payload's (`tests/test_native_stitch.py`); the zones it fills again
afterwards are filled by this tool's own filler (`fileformat/zonefill.py`),
and the DRC that judges each new via is this tool's own (`fileformat/drc/`).

Tracks cut a pour into islands. What is dangerous is not that it is cut but
an island with a pad on it and no via down to the plane: that pad's ground
is then a small patch of copper, joined to the rest by a long track across
the board. This finds, in each island lacking one, a safe place for a via,
and puts it there; after that the long tracks are redundant.
"""

from __future__ import annotations

import bisect
import math
import os
import uuid as uuidlib

from .. import envelope, kicad_env
from ..fileformat import connectivity, drc, zonefill
from ..fileformat.board import Board
from ..fileformat.drc.settings import Settings
from ..fileformat.sexpr import List, number, quote, symbol
from . import load_board, write
from .project import NetClasses, load_project

NM = 1_000_000
HOLE2HOLE = 0.25  # mm: KiCad's default hole-to-hole clearance
EDGE_MARGIN = 0.05  # mm: from the via's pad to the island's edge
STEP = 0.4  # mm: how far apart the candidate places are
FRONT, BACK = "F.Cu", "B.Cu"


def mm(value: float) -> float:
    return value / NM


def from_mm(value: float) -> int:
    """As pcbnew's FromMM: rounded, halves away from nought."""
    scaled = value * NM
    return int(math.floor(scaled + 0.5)) if scaled >= 0 else -int(math.floor(-scaled + 0.5))


def poly_area(pts):
    n = len(pts)
    return abs(sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1]
                   for i in range(n))) / 2.0  # fmt: skip


def point_in_poly(pts, x, y):
    n = len(pts)
    c = False
    j = n - 1
    for i in range(n):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            c = not c
        j = i
    return c


def dist_to_poly(pts, x, y):
    best = 1e9
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        length2 = dx * dx + dy * dy
        t = (
            0.0 if length2 < 1e-12
            else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / length2))
        )  # fmt: skip
        best = min(best, math.hypot(x - (x1 + t * dx), y - (y1 + t * dy)))
    return best


class Outline:
    """An island's outline, indexed: thousands of candidate places are each
    tested against a few of its edges rather than all of them.

    The answers are `point_in_poly`'s and `dist_to_poly`'s, bit for bit --
    the same arithmetic on the same edges -- only fewer edges are looked at.
    A large pour has tens of thousands of edges; testing every candidate
    against every one took minutes on a demo board.
    """

    CELL = 1.0  # mm: the side of a square of the edge index

    def __init__(self, pts) -> None:
        self.pts = pts
        n = len(pts)
        self.rows: dict[float, list[float]] = {}
        self.edges = [(*pts[i], *pts[(i + 1) % n]) for i in range(n)]
        self.cells: dict[tuple[int, int], list[int]] = {}
        self.x0 = min(p[0] for p in pts)
        self.y0 = min(p[1] for p in pts)
        for k, (x1, y1, x2, y2) in enumerate(self.edges):
            left, right = self._cell(min(x1, x2), self.x0), self._cell(max(x1, x2), self.x0)
            top, bottom = self._cell(min(y1, y2), self.y0), self._cell(max(y1, y2), self.y0)
            for cx in range(left, right + 1):
                for cy in range(top, bottom + 1):
                    self.cells.setdefault((cx, cy), []).append(k)
        self.reach = max((max(abs(cx), abs(cy)) for cx, cy in self.cells), default=0) + 1

    def _cell(self, v: float, origin: float) -> int:
        return int(math.floor((v - origin) / self.CELL))

    def inside(self, x: float, y: float) -> bool:
        """`point_in_poly`: a ray to +x crosses the outline an odd number of
        times. The crossings of a row are found once for all its places."""
        xs = self.rows.get(y)
        if xs is None:
            xs = []
            pts = self.pts
            j = len(pts) - 1
            for i in range(len(pts)):
                xi, yi = pts[i]
                xj, yj = pts[j]
                if (yi > y) != (yj > y):
                    xs.append((xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi)
                j = i
            xs.sort()
            self.rows[y] = xs
        return (len(xs) - bisect.bisect_right(xs, x)) % 2 == 1

    def distance(self, x: float, y: float) -> float:
        """`dist_to_poly`: to the nearest edge. Squares of the index are
        searched outward until none further can hold a nearer edge."""
        cx, cy = self._cell(x, self.x0), self._cell(y, self.y0)
        best = 1e9
        k = 0
        while True:
            for i in range(cx - k, cx + k + 1):
                for j in range(cy - k, cy + k + 1) if i in (cx - k, cx + k) else (cy - k, cy + k):
                    for e in self.cells.get((i, j), ()):
                        x1, y1, x2, y2 = self.edges[e]
                        best = min(best, seg_dist(x, y, x1, y1, x2, y2))
            # Whatever is not yet searched lies outside the square, at least
            # k cells from (x, y), which is inside its middle cell; one cell
            # more is kept in hand against rounding at the cells' borders.
            if best <= (k - 1) * self.CELL or k > self.reach + max(abs(cx), abs(cy)):
                return best
            k += 1


def seg_dist(x, y, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 < 1e-12 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / length2))
    return math.hypot(x - (x1 + t * dx), y - (y1 + t * dy))


def islands_of(board: Board, net: str, layer: str) -> list[dict]:
    """The net's islands of fill on the layer, as the file holds them, the
    largest first."""
    out = []
    for zone in board.zones:
        if zone.rule_area or layer not in zone.layers or zone.net != net:
            continue
        for ring in zone.filled.get(layer, []):
            pts = [(mm(x), mm(y)) for x, y in ring]
            if len(pts) >= 3:
                out.append({"pts": pts, "area": poly_area(pts)})
    out.sort(key=lambda d: -d["area"])
    return out


def unconnected(board: Board) -> int:
    return connectivity.connect(board).unconnected


def nets_of(board: Board) -> set[str]:
    names = {pad.net for fp in board.footprints for pad in fp.pads}
    names |= {t.net for t in board.tracks} | {v.net for v in board.vias}
    names |= {z.net for z in board.zones}
    names |= set(board._net_table().values())
    names.discard("")
    return names


def via_node(x: float, y: float, size: float, drill: float, net: str) -> List:
    """A through via as KiCad 10 writes one."""
    return List.new(
        "via",
        List.new("at", number(from_mm(x) / NM), number(from_mm(y) / NM)),
        List.new("size", number(from_mm(size) / NM)),
        List.new("drill", number(from_mm(drill) / NM)),
        List.new("layers", quote(FRONT), quote(BACK)),
        List.new("capping", symbol("no")),
        List.new("covering", List.new("front", symbol("no")), List.new("back", symbol("no"))),
        List.new("plugging", List.new("front", symbol("no")), List.new("back", symbol("no"))),
        List.new("filling", symbol("no")),
        List.new("net", quote(net)),
        List.new("uuid", quote(str(uuidlib.uuid4()))),
    )


def segment_node(a, b, width: float, layer: str, net: str) -> List:
    return List.new(
        "segment",
        List.new("start", number(from_mm(a[0]) / NM), number(from_mm(a[1]) / NM)),
        List.new("end", number(from_mm(b[0]) / NM), number(from_mm(b[1]) / NM)),
        List.new("width", number(from_mm(width) / NM)),
        List.new("layer", quote(layer)),
        List.new("net", quote(net)),
        List.new("uuid", quote(str(uuidlib.uuid4()))),
    )


def add_items(board: Board, nodes: list[List]) -> None:
    """Tracks and vias put where pcbnew puts new ones: after the board's
    own tracks and vias."""
    root = board.root
    heads = [getattr(item, "head", None) for item in root.items]
    after = max((i for i, h in enumerate(heads) if h in ("segment", "arc", "via")), default=None)
    if after is None:
        zones = [i for i, h in enumerate(heads) if h == "zone"]
        after = zones[0] - 1 if zones else len(root.items) - 1
    for k, node in enumerate(nodes):
        root.insert(after + 1 + k, node)


def _refill(board: Board) -> None:
    filler = zonefill.Filler(board, Settings.of(board.path.with_suffix(".kicad_pro")))
    for zone in board.zones:
        if not zone.rule_area:
            zonefill.write(filler.fill(zone))


def _prepare(path: str, args: dict, *, legacy_gate=False):
    write.start()
    net = args.get("net") or "GND"
    min_area = float(args.get("min-area") if args.get("min-area") is not None else 0.5)

    board = load_board(path)
    pro, _ = load_project(path)
    from ..power_geometry import EffectiveClasses

    classes = NetClasses(pro) if legacy_gate else EffectiveClasses(pro)
    _w, clearance, via_d, via_h = classes.params(net)
    if net not in nets_of(board):
        envelope.fail("E_NOT_FOUND", f"板上没有网络 {net}", {"net": net})

    # Every obstacle keeps the clearance the larger of its class and the
    # net's asks: KiCad's DRC takes the larger of the two.
    cl_of: dict[str, float] = {}

    def need_cl(name: str) -> float:
        if name not in cl_of:
            try:
                cl_of[name] = max(clearance, classes.params(name)[1])
            except Exception:  # noqa: BLE001
                cl_of[name] = clearance
        return cl_of[name]

    foreign_pads = []  # (on front, on back, x, y, r, clearance)
    holes = []  # (x, y, r)
    for fp in board.footprints:
        for pad in fp.pads:
            x1, y1, x2, y2 = pad.bbox()
            x, y = mm(pad.position[0]), mm(pad.position[1])
            r = math.hypot(mm(x2 - x1), mm(y2 - y1)) / 2.0
            if pad.drill and pad.drill[0] > 0:
                holes.append((x, y, mm(pad.drill[0]) / 2.0))
            if pad.net != net:
                foreign_pads.append((FRONT in pad.layers, BACK in pad.layers, x, y, r,
                                     need_cl(pad.net)))  # fmt: skip
    foreign_trk: dict[str, list] = {FRONT: [], BACK: []}
    for via in board.vias:
        x, y = mm(via.position[0]), mm(via.position[1])
        holes.append((x, y, mm(via.drill) / 2.0))
        if via.net != net:
            for layer in (FRONT, BACK):
                foreign_trk[layer].append((x, y, x, y, mm(via.drill) / 2.0 + 0.2, need_cl(via.net)))
    for track in board.tracks:
        if track.kind != "segment" or track.net == net or track.layer not in foreign_trk:
            continue
        foreign_trk[track.layer].append(
            (mm(track.start[0]), mm(track.start[1]), mm(track.end[0]), mm(track.end[1]),
             mm(track.width) / 2.0, need_cl(track.net))
        )  # fmt: skip
    # Rule areas: a via never goes in one (an antenna's keep-out says so).
    keepouts = []
    for zone in board.zones:
        if not zone.rule_area:
            continue
        points = [p for ring in zone.outlines for p in ring]
        if not points:
            continue
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        keepouts.append(
            (zone.name or "keepout", mm(min(xs)), mm(min(ys)), mm(max(xs)), mm(max(ys)))
        )
    own_vias = [(mm(v.position[0]), mm(v.position[1])) for v in board.vias if v.net == net]

    def ok_here(x, y, other_layer, vd, vh, both=False):
        for on_front, on_back, px, py, pr, ocl in foreign_pads:
            if (other_layer == FRONT and not on_front) or (other_layer == BACK and not on_back):
                continue
            if math.hypot(x - px, y - py) < vd / 2.0 + ocl + pr:
                return False
        for x1, y1, x2, y2, hw, ocl in foreign_trk[other_layer]:
            if seg_dist(x, y, x1, y1, x2, y2) < vd / 2.0 + ocl + hw:
                return False
        for hx, hy, hr in holes:
            d = math.hypot(x - hx, y - hy)
            if 1e-6 < d < vh / 2.0 + hr + HOLE2HOLE:
                return False
        for _name, left, top, right, bottom in keepouts:
            m = vd / 2.0 + clearance
            if left - m <= x <= right + m and top - m <= y <= bottom + m:
                return False
        return True

    # The via steps down from the class's size to the smallest the project
    # allows -- never past it.
    rules = pro.get("board", {}).get("design_settings", {}).get("rules", {})
    min_d = float(rules.get("min_via_diameter", 0.5))
    min_h = float(rules.get("min_through_hole_diameter", 0.3))
    min_ann = float(rules.get("min_via_annular_width", 0.1))
    small_h = max(min_h, round(min_d - 2 * min_ann, 3))
    ladder = [(via_d, via_h)]
    if min_d < via_d - 1e-9:
        ladder.append((min_d, min(via_h, small_h)))

    plan, skipped = [], []
    for layer, other in ((FRONT, BACK), (BACK, FRONT)):
        name = board.layer_name(layer)
        for d in islands_of(board, net, layer):
            pts = d["pts"]
            outline = Outline(pts)
            if any(outline.inside(vx, vy) for vx, vy in own_vias):
                continue  # a via down already
            if d["area"] < min_area:
                skipped.append({
                    "layer": name,
                    "area": round(d["area"], 2),
                    "why": "面积过小，判为死铜，建议在铺铜属性里开启孤岛移除",
                })  # fmt: skip
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            x_min, y_min = min(xs), min(ys)
            # The step matched to the island: a sliver half a millimetre
            # wide is missed by a 0.4 mm one.
            step = STEP if d["area"] > 20.0 else 0.08
            n_x = max(1, int((max(xs) - x_min) / step) + 1)
            n_y = max(1, int((max(ys) - y_min) / step) + 1)
            best = None
            tries = [(vd, vh, False) for (vd, vh) in ladder] + [
                (ladder[-1][0], ladder[-1][1], True)
            ]
            for vd, vh, loose in tries:
                need_in = 0.0 if loose else vd / 2.0 + EDGE_MARGIN
                for i in range(n_x):
                    for j in range(n_y):
                        x = x_min + i * step
                        y = y_min + j * step
                        if not outline.inside(x, y):
                            continue
                        m = outline.distance(x, y)
                        if m < need_in:
                            continue
                        if best is not None and m <= best[2]:
                            continue  # no better than the place found
                        if not ok_here(x, y, other, vd, vh, both=loose):
                            continue
                        if not legacy_gate and not any(
                            Outline(island["pts"]).inside(x, y)
                            for island in islands_of(board, net, other)
                        ):
                            # A via in one outline is not proof of a plane stitch.
                            # Require existing actual filled copper on both faces.
                            continue
                        best = (x, y, m, vd, vh)
                if best is not None:
                    break
            if best is None:
                skipped.append({
                    "layer": name,
                    "area": round(d["area"], 2),
                    "center": [round(sum(xs) / len(xs), 1), round(sum(ys) / len(ys), 1)],
                    "why": "岛内放不下过孔（降到 %.2f/%.2f mm 仍不行）" % ladder[-1],  # noqa: UP031
                })  # fmt: skip
                continue
            plan.append({
                "layer": name,
                "area": round(d["area"], 2),
                "x": round(best[0], 3),
                "y": round(best[1], 3),
                "margin_mm": round(best[2], 3),
                "via_mm": best[3],
                "drill_mm": best[4],
            })  # fmt: skip
            if not legacy_gate:
                own_vias.append((round(best[0], 3), round(best[1], 3)))
                holes.append((round(best[0], 3), round(best[1], 3), best[4] / 2.0))

    before = unconnected(board)
    bridge = bool(args.get("bridge"))
    preview = {
        "board": path,
        "net": net,
        "via_mm": via_d,
        "drill_mm": via_h,
        "islands_to_stitch": len(plan),
        "skipped": len(skipped),
        "plan": plan,
        "skipped_detail": skipped,
        "unconnected_before": before,
        "bridge_enabled": bridge,
        "will": "为 %d 座缺过孔的 %s 铺铜孤岛各补一个过孔%s"  # noqa: UP031
        % (len(plan), net, "；放不下过孔的细条岛尝试用短走线并入旁边已接通的铜" if bridge else ""),
    }
    if legacy_gate:
        envelope.check_confirm(args.get("confirm"), f"pcb_stitch:{net}:{os.path.basename(path)}",
                               preview, path)  # fmt: skip

    # Bridges: an island too thin for a via, joined by a short track to copper
    # already joined beside it.
    bridges = []
    if bridge and skipped:
        span = float(args.get("bridge-max") or 2.0)
        for layer, _other in ((FRONT, BACK), (BACK, FRONT)):
            name = board.layer_name(layer)
            isl = islands_of(board, net, layer)
            if not isl:
                continue
            planned = [(p["x"], p["y"]) for p in plan if p["layer"] == name]
            joined = [
                d for k, d in enumerate(isl)
                if k == 0
                or any(point_in_poly(d["pts"], vx, vy) for vx, vy in own_vias)
                or any(point_in_poly(d["pts"], vx, vy) for vx, vy in planned)
            ]  # fmt: skip
            for s in skipped:
                if s["layer"] != name or "center" not in s:
                    continue
                src = next((d for d in isl if abs(d["area"] - s["area"]) < 0.05), None)
                if src is None or src in joined:
                    continue
                best = None
                for d in joined:
                    for px, py in src["pts"]:
                        for qx, qy in d["pts"]:
                            g = math.hypot(px - qx, py - qy)
                            if g <= span and (best is None or g < best[0]):
                                best = (g, px, py, qx, qy)
                if best is None:
                    continue
                _g, px, py, qx, qy = best
                half = _w / 2.0
                clear = True
                n = max(2, int(_g / 0.1) + 1)
                for i in range(n):
                    t = i / (n - 1.0)
                    x = px + (qx - px) * t
                    y = py + (qy - py) * t
                    for on_front, on_back, ax, ay, ar, ocl in foreign_pads:
                        if (layer == FRONT and not on_front) or (layer == BACK and not on_back):
                            continue
                        if math.hypot(x - ax, y - ay) < half + ocl + ar:
                            clear = False
                    for x1, y1, x2, y2, hw, ocl in foreign_trk[layer]:
                        if seg_dist(x, y, x1, y1, x2, y2) < half + ocl + hw:
                            clear = False
                    if not clear:
                        break
                if not clear:
                    s["why"] += "；搭桥通道也被异网铜占住"
                    continue
                bridges.append({
                    "layer": name,
                    "width_mm": _w,
                    "from": [round(px, 3), round(py, 3)],
                    "to": [round(qx, 3), round(qy, 3)],
                    "gap_mm": round(_g, 3),
                    "island_area": s["area"],
                })  # fmt: skip
        for br in bridges:
            skipped[:] = [s for s in skipped if abs(s["area"] - br["island_area"]) > 0.05]

    nodes = []
    for br in bridges:
        layer = FRONT if br["layer"] == board.layer_name(FRONT) else BACK
        nodes.append(segment_node(br["from"], br["to"], br["width_mm"], layer, net))
    made: dict[tuple[float, float], List] = {}
    for p in plan:
        node = via_node(p["x"], p["y"], p["via_mm"], p["drill_mm"], net)
        nodes.append(node)
        made[(p["x"], p["y"])] = node
    add_items(board, nodes)
    board = Board(board.document, board.path)  # read again, with what was added
    _refill(board)
    write.save(board)

    # The DRC is the judge: a new via that brought an error is taken out
    # again -- an island left unstitched rather than an error more.
    removed = []
    if not args.get("no-verify") and made:
        root = kicad_env.find_kicad_root() if legacy_gate else None
        size = {(p["x"], p["y"]): p["via_mm"] for p in plan}
        text_cl = max(clearance, classes.classes["Default"].get("clearance", 0.2))
        texts = [
            [(mm(x), mm(y)) for x, y in ring]
            for layer in board.copper_layers
            for ring in zonefill.text_boxes(board, layer)
        ]
        for _round in range(4):
            report = drc.check(path, kicad_root=root)
            bad = set()
            for violation in report.violations:
                if violation.severity != "error" or violation.excluded:
                    continue
                for item in violation.items:
                    if "via" not in item.description.lower():
                        continue
                    x, y = mm(item.position[0]), mm(item.position[1])
                    for key in made:
                        if abs(key[0] - x) < 0.02 and abs(key[1] - y) < 0.02:
                            bad.add(key)
            # Text on copper: this DRC cannot check it until the stroke font
            # is in, and KiCad's finds a via on a letter a short. So a via
            # comes out wherever it is nearer a text's box than the clearance
            # -- surely, if sometimes where KiCad would have let it stand.
            for key in made:
                reach = size[key] / 2.0 + text_cl
                for box in texts:
                    if point_in_poly(box, *key) or dist_to_poly(box, *key) < reach:
                        bad.add(key)
                        break
            if not bad:
                break
            board = load_board(path)
            uuids = {made.pop(key).find("uuid").value(1) for key in bad}
            for node in list(board.root.lists()):
                if node.head == "via" and node.find("uuid").value(1) in uuids:
                    board.root.remove(node)
            removed += [{"x": key[0], "y": key[1]} for key in bad]
            board = Board(board.document, board.path)
            _refill(board)
            write.save(board)

    after = unconnected(load_board(path))
    kept = [
        p for p in plan
        if not any(abs(r["x"] - p["x"]) < 1e-6 and abs(r["y"] - p["y"]) < 1e-6 for r in removed)
    ]  # fmt: skip
    return ({
        "net": net,
        "vias_added": len(kept),
        "plan": kept,
        "bridges": bridges,
        "reverted": removed,
        "skipped": skipped,
        "unconnected_before": before,
        "unconnected_after": after,
        "improved": before - after,
        "note": "补完过孔后，原本用来串联孤岛的长走线就成了冗余，"
        "可以再确认一次连通性后删掉那些横穿整板的走线",
    })  # fmt: skip


def prepare(path: str, args: dict):
    return _prepare(path, args, legacy_gate=False)


def run(path: str, args: dict) -> None:
    write.done(_prepare(path, args, legacy_gate=True))
