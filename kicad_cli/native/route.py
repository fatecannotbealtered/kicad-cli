"""``board route`` and ``board rewidth``, in this process: the grid router.

A port of `payload/pcb_route.py`, which ran on pcbnew. The router is
`router.py`; this is what it is asked to do, in three modes:

- ``full`` clears every track, fans power pads out to their planes, leads
  each pin of a fine-pitch part out of its row, then routes net by net,
  short nets first;
- ``repair`` keeps what is there and routes only the missing connections
  the DRC lists -- run it again until it stops improving; with ``--ripup``
  it may take up another net's copper near a blocked end and route that net
  again, all or nothing;
- ``rewidth`` tears up the nets of the chosen net classes and routes them
  again at their class's width, necking down only at the pads. Widening a
  path drawn at 0.2 mm does not work: it was squeezed between obstacles at
  that width. The net has to be planned again.

Every mode ends as the payload's did: the zones are filled again, the board
saved, and non-excluded error finding identities compared with the baseline;
any new identity or extra instance puts the board back as it was. The DRC and the
zone filler are this tool's own now (`fileformat/drc/`,
`fileformat/zonefill.py`), and `verify.oracle` says so.

pcbnew held a board's tracks in a list a new track joins at the front; the
order tracks are painted onto the grid decides which of two nets' copper
holds a cell both touch, so `Layout` keeps that order.
"""

from __future__ import annotations

import math
import os
import re
import uuid as uuidlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .. import drc_baseline, envelope, kicad_env
from ..fileformat import connectivity, drc, zonefill
from ..fileformat.board import Board, Pad, nm
from ..fileformat.drc.settings import Settings
from ..fileformat.geometry import rotate
from ..fileformat.sexpr import List, number, quote, symbol
from . import load_board, write
from .move import courtyard, courtyard_box
from .project import NetClasses, load_project
from .router import GRID, LAYERS, MM, TIGHT_MAX_PITCH, B, Copper, F, Router, mm
from .smd_drill_guard import DrillConstraintError, SmdDrillGuard
from .via_hole_guard import ViaHoleConstraintError, ViaHoleGuard

NECK_DEFAULT = 0.20  # mm: a wide track cannot leave a 0.5-0.65 mm pitch row
REWIDTH_NECK_R = 2.5  # mm round a pad where a re-routed net may neck. It
# must cover the whole fan-out: at 0.65 mm pitch four lanes need 1.5 mm of
# spread to hold 1.2 mm tracks. At 1.0 all four OUT nets fell to 0.20 mm; at
# 4.0 they held 1.20 mm but brought 14 DRC errors -- the looser check let
# paths into gaps the width could not fill afterwards.
ESCAPE_CLEAR = 0.9  # mm: how far past the row of pins an escape ends
ESCAPE_STAGGER = 0.5  # mm: neighbouring escapes on a side end staggered
RIPUP_R = 5.0  # mm: how far round a blocked end copper may be taken up
ZONE_TOUCH_MM = 0.35  # mm: "in the copper" -- about half a via's ring
FRONT, BACK = "F.Cu", "B.Cu"
NM = 1_000_000

# board route's three modes fill different fields, but the contract is
# exact: no undeclared key, no declared key missing -- a mode that does not
# set one gives None.
ROUTE_FIELDS = (
    "engine",
    "mode",
    "targets",
    "routed",
    "failed",
    "unresolved",
    "tracks",
    "existing_tracks",
    "cleared_tracks",
    "escape",
    "fanout",
    "plane_nets",
    "plane_served",
    "ripup",
    "vias",
    "track_len_mm",
    "unconnected_before",
    "unconnected_after",
    "improved",
    "rewidth",
    "rewidth_total",
    "rewidth_ok",
    "rewidth_reverted",
    "rewidth_drc_cause",
    "stripped_segments",
    "skipped_nets",
    "verify",
    "note",
    "route_search",
)


def route_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    """Exactly the declared keys: absent-for-this-mode is None, not missing."""
    extra = sorted(set(payload) - set(ROUTE_FIELDS))
    if extra:  # a new field must be declared before it can be emitted
        raise AssertionError("undeclared board route fields: " + ", ".join(extra))
    return {name: payload.get(name) for name in ROUTE_FIELDS}


# -- the board as the router sees it --------------------------------------------


@dataclass(eq=False)
class Piece:
    """A track, arc or via, as pcbnew held one."""

    kind: str  # segment, arc or via
    start: tuple[int, int]  # a via's position
    end: tuple[int, int]
    width: int  # a via's diameter
    layer: str  # a via's top layer
    code: int  # its net's number
    drill: int = 0
    node: List | None = None  # the file's, for a piece read from it

    @property
    def length(self) -> float:
        return math.hypot(mm(self.end[0]) - mm(self.start[0]), mm(self.end[1]) - mm(self.start[1]))


def pad_box(pad: Pad) -> tuple[int, int, int, int]:
    """The box pcbnew gives a pad: round the exact shape, not a polygon
    drawn of it -- measured to the nanometre on every pad of KiCad's demos."""
    w, h = pad.size
    ox, oy = pad.drill_offset
    px, py = pad.position

    def placed(x: float, y: float) -> tuple[float, float]:
        rx, ry = rotate(x + ox, y + oy, pad.angle)
        return px + rx, py + ry

    if pad.shape == "circle":
        cx, cy = placed(0, 0)
        r = w // 2
        return (round(cx - r), round(cy - r), round(cx + r), round(cy + r))
    if pad.shape == "oval":
        if w >= h:
            half, r = (w - h) / 2, h // 2
            ends = [placed(-half, 0), placed(half, 0)]
        else:
            half, r = (h - w) / 2, w // 2
            ends = [placed(0, -half), placed(0, half)]
        xs = [round(p[0]) for p in ends]
        ys = [round(p[1]) for p in ends]
        return (min(xs) - r, min(ys) - r, max(xs) + r, max(ys) + r)
    if pad.shape in ("rect", "roundrect") and not (pad.chamfer_ratio > 0 and pad.chamfer_corners):
        r = pad.roundrect_ratio * min(w, h) if pad.shape == "roundrect" else 0
        hw, hh = w / 2 - r, h / 2 - r
        corners = [placed(-hw, -hh), placed(hw, -hh), placed(hw, hh), placed(-hw, hh)]
        xs = [p[0] for p in corners]
        ys = [p[1] for p in corners]
        return (round(min(xs) - r), round(min(ys) - r), round(max(xs) + r), round(max(ys) + r))
    return pad.bbox()


class Layout:
    """A board loaded for routing: nets numbered, tracks and vias in the
    order pcbnew held them, pads and keep-outs fixed for the run."""

    def __init__(self, path: str, *, smd_drill_clearance_mm=None, smd_drill_guard=None,
                 via_hole_guard=None) -> None:  # fmt: skip
        self.path = path
        self.board = load_board(path)
        board = self.board
        # Numbers for the nets, as pcbnew numbered them: any numbering serves
        # the router, which only asks whether two pieces of copper are one net.
        self.codes: dict[str, int] = {"": 0}
        self.names: dict[int, str] = {0: ""}
        for name in board._net_table().values():
            self.code(name)
        for fp in board.footprints:
            for pad in fp.pads:
                self.code(pad.net)
        self.pieces: list[Piece] = []
        for node in board.root.lists():
            if node.head in ("segment", "arc", "via"):
                self.pieces.append(self._piece(node))
        for zone in board.zones:
            self.code(zone.net)
        self.pads: list[tuple[Any, Pad]] = [(fp, pad) for fp in board.footprints for pad in fp.pads]
        self.smd_drill_clearance_mm = smd_drill_clearance_mm
        self.smd_drill_guard = smd_drill_guard
        self.via_hole_guard = via_hole_guard
        self._copper: Copper | None = None
        self._template: Router | None = None

    def code(self, name: str) -> int:
        if name not in self.codes:
            self.codes[name] = len(self.codes)
            self.names[self.codes[name]] = name
        return self.codes[name]

    def find(self, name: str) -> int | None:
        """pcbnew's FindNet: the net's number, None for a net not on the board."""
        return self.codes.get(name)

    def _piece(self, node: List) -> Piece:
        board = self.board
        net = board.net_of(node)
        if node.head == "via":
            at = node.find("at")
            size = node.find("size")
            drill = node.find("drill")
            position = (_nm(at.atom(1)), _nm(at.atom(2)))
            layers = node.find("layers")
            top = layers.value(1) if layers is not None else FRONT
            return Piece("via", position, position, _nm(size.atom(1)), top, self.code(net),
                         _nm(drill.atom(1)) if drill is not None else 0, node)  # fmt: skip
        start, end = node.find("start"), node.find("end")
        layer = node.find("layer")
        width = node.find("width")
        return Piece(
            node.head,
            (_nm(start.atom(1)), _nm(start.atom(2))),
            (_nm(end.atom(1)), _nm(end.atom(2))),
            _nm(width.atom(1)) if width is not None else 0,
            layer.value(1) if layer is not None else "",
            self.code(net),
            0,
            node,
        )

    # -- pcbnew's Add and Remove ------------------------------------------------

    def add(self, piece: Piece) -> Piece:
        self.pieces.insert(0, piece)  # pcbnew puts a new track first
        return piece

    def remove(self, piece: Piece) -> None:
        for k, held in enumerate(self.pieces):
            if held is piece:
                del self.pieces[k]
                return

    # -- what the router needs ----------------------------------------------------

    @property
    def copper(self) -> Copper:
        if self._copper is None:
            self._copper = self._make_copper()
        return self._copper

    def _make_copper(self) -> Copper:
        board = self.board
        box = board.edge_bbox() or (0, 0, 0, 0)
        edges = (mm(box[0]), mm(box[1]), mm(box[2]), mm(box[3]))
        keepouts = []
        for zone in board.zones:
            if not zone.rule_area:
                continue
            points = [p for ring in zone.outlines for p in ring]
            if points:
                xs, ys = [p[0] for p in points], [p[1] for p in points]
                keepouts.append((mm(min(xs)), mm(min(ys)), mm(max(xs)), mm(max(ys))))
        pads = []
        for _fp, pad in self.pads:
            layers = board.expand_layers(pad.layers)
            pads.append({
                "box": pad_box(pad),
                "code": self.codes.get(pad.net, 0),
                "front": FRONT in layers,
                "back": BACK in layers,
            })  # fmt: skip
        # Fine-pitch footprints, found by their pins rather than named: a
        # router's clearance inside their courtyards is the board's tight one.
        relax = []
        for fp in board.footprints:
            if len(fp.pads) < 6:
                continue
            ps = [(mm(p.position[0]), mm(p.position[1])) for p in fp.pads]
            mp = 9.9
            for i, a in enumerate(ps):
                for c in ps[i + 1 :]:
                    d = math.hypot(a[0] - c[0], a[1] - c[1])
                    if 0.01 < d < mp:
                        mp = d
            if mp > TIGHT_MAX_PITCH:
                continue
            found = courtyard(fp)
            if found is not None:
                relax.append(found)
        guard = SmdDrillGuard.read(self.pads, self.smd_drill_clearance_mm)
        if self.smd_drill_guard is not None:
            # Rewidth reloads the candidate for each selected net.  Share its
            # actual planning witness only after proving the pad material and
            # requested policy have not changed during those reloads.
            expected = self.smd_drill_guard
            fresh = SmdDrillGuard.read(self.pads, expected.gap_nm / NM)
            if fresh.pads != expected.pads or fresh.gap_nm != expected.gap_nm:
                raise DrillConstraintError(
                    "SMD geometry/policy drifted during selected-net re-routing",
                    objects=[{"kind": "pad", "identity": pad.identity} for pad in expected.pads],
                )
            guard = expected
        self.smd_drill_guard = guard
        hole_guard = self.via_hole_guard or ViaHoleGuard.read(self.path)
        hole_guard.require_binding(self.path)
        hole_guard.saved_holes(board)  # reject unread drills/padstacks before searching
        self.via_hole_guard = hole_guard
        return Copper(edges, keepouts, pads, relax, smd_drill_guard=guard,
                      via_hole_guard=hole_guard)  # fmt: skip

    def router(self) -> Router:
        """A router with the board's fixed copper painted -- painted once,
        copied after (pcbnew's own lists of a board worn by thousands of
        adds and removes could not be trusted to be walked again)."""
        if self._template is None:
            self._template = Router(self.copper)
        return Router(self.copper, static=self._template)

    def is_on(self, pad: Pad, layer: str) -> bool:
        return layer in self.board.expand_layers(pad.layers)

    # -- writing it back ------------------------------------------------------------

    def apply(self) -> Board:
        """Put the pieces into the file: those taken up out of it, those
        added into it after the board's own tracks and vias."""
        root = self.board.root
        kept = {id(p.node) for p in self.pieces if p.node is not None}
        for node in list(root.lists()):
            if node.head in ("segment", "arc", "via") and id(node) not in kept:
                root.remove(node)
        fresh = [p for p in reversed(self.pieces) if p.node is None]
        heads = [getattr(item, "head", None) for item in root.items]
        after = max(
            (i for i, h in enumerate(heads) if h in ("segment", "arc", "via")), default=None
        )
        if after is None:
            zones = [i for i, h in enumerate(heads) if h == "zone"]
            after = zones[0] - 1 if zones else len(root.items) - 1
        for k, piece in enumerate(fresh):
            piece.node = self._node(piece)
            root.insert(after + 1 + k, piece.node)
        self.board = Board(self.board.document, self.board.path)
        return self.board

    def _node(self, piece: Piece) -> List:
        net = self.names.get(piece.code, "")
        if piece.kind == "via":
            return List.new(
                "via",
                List.new("at", _mm_atom(piece.start[0]), _mm_atom(piece.start[1])),
                List.new("size", _mm_atom(piece.width)),
                List.new("drill", _mm_atom(piece.drill)),
                List.new("layers", quote(FRONT), quote(BACK)),
                List.new("capping", symbol("no")),
                List.new(
                    "covering", List.new("front", symbol("no")), List.new("back", symbol("no"))
                ),
                List.new(
                    "plugging", List.new("front", symbol("no")), List.new("back", symbol("no"))
                ),
                List.new("filling", symbol("no")),
                List.new("net", quote(net)),
                List.new("uuid", quote(str(uuidlib.uuid4()))),
            )
        return List.new(
            "segment",
            List.new("start", _mm_atom(piece.start[0]), _mm_atom(piece.start[1])),
            List.new("end", _mm_atom(piece.end[0]), _mm_atom(piece.end[1])),
            List.new("width", _mm_atom(piece.width)),
            List.new("layer", quote(piece.layer)),
            List.new("net", quote(net)),
            List.new("uuid", quote(str(uuidlib.uuid4()))),
        )

    def fill_and_save(self) -> Board:
        """As the payload's fill_and_save: zones filled again, then saved."""
        board = self.apply()
        filler = zonefill.Filler(board, Settings.of(board.path.with_suffix(".kicad_pro")))
        for zone in board.zones:
            if not zone.rule_area:
                zonefill.write(filler.fill(zone))
        write.save(board)
        return board


def _nm(atom: str | None) -> int:
    return nm(atom or "0")


def _mm_atom(value: int):
    return number(value / NM)


# -- the board's numbers ----------------------------------------------------------


def unconnected(board: Board) -> int:
    return connectivity.connect(board).unconnected


def errors(
    path: str, *, baseline=None, intermediate_checks=None, library_checks=True
) -> tuple[int, Any]:
    """The custom engine's saved-file report, retaining checker provenance."""
    try:
        return drc_baseline.capture(
            path, kicad_root=kicad_env.find_kicad_root() if library_checks else None
        )
    except (OSError, ValueError) as exc:
        envelope.fail(
            "E_VERIFY_INCOMPLETE",
            "Custom DRC could not produce a comparable saved-file observation",
            {
                "checker": getattr(exc, "provenance", None)
                or {
                    "kind": "custom_checker",
                    "origin": "kicad_cli.fileformat.drc",
                    "completion": "unknown",
                },
                "reason": str(exc),
                "baseline": drc_baseline.record(baseline) if baseline is not None else None,
                "intermediate_checks": intermediate_checks or [],
            },
        )


def verify_baseline(
    path,
    count_before,
    report_before,
    *,
    enabled=True,
    intermediate_checks=None,
    library_checks=True,
):
    """Counts are diagnostic; actual finding identities decide this gate."""
    count_after, report_after = errors(
        path,
        baseline=report_before,
        intermediate_checks=intermediate_checks,
        **({} if library_checks else {"library_checks": False}),
    )
    comparison = drc_baseline.compare(report_before, report_after, enabled=enabled)
    details = {
        "errors_baseline": count_before,
        "errors_final": count_after,
        "finding_comparison": comparison,
        "intermediate_checks": intermediate_checks or [],
    }
    if comparison["status"] == "incomplete":
        envelope.fail(
            "E_VERIFY_INCOMPLETE", "Custom DRC finding identity/provenance is incomplete", details
        )
    if comparison["status"] == "failed":
        envelope.fail(
            "E_INTEGRITY",
            "Custom DRC found new error findings; the write is being rolled back",
            details,
        )
    if enabled:
        try:
            drc_baseline.require_saved_input(path, report_after)
        except (OSError, ValueError) as exc:
            comparison["status"] = "incomplete"
            comparison["incomplete_reasons"].append(str(exc))
            envelope.fail("E_VERIFY_INCOMPLETE", "Saved DRC input changed before commit", details)
    return count_after, comparison


def width_summary(layout: Layout, nc: NetClasses) -> dict[str, Any]:
    """Track width against each net's net-class target, weighted by length.

    DRC does not check this: a board can be routed entirely at the neck
    width, pass every clearance rule, and carry a third of the current it
    was designed for.
    """
    total = ok = 0.0
    smallest = None
    targets = []
    for piece in layout.pieces:
        if piece.kind != "segment":
            continue
        width = piece.width / 1e6
        length = math.hypot(piece.end[0] - piece.start[0], piece.end[1] - piece.start[1]) / 1e6
        if length <= 0:
            continue
        target = nc.params(layout.names.get(piece.code, ""))[0]
        targets.append(target)
        total += length
        if width + 1e-6 >= target:
            ok += length
        smallest = width if smallest is None else min(smallest, width)
    if total <= 0:
        return {"len_mm": 0.0, "min_mm": None, "target_mm": None, "compliant_pct": None}
    return {
        "len_mm": round(total, 1),
        "min_mm": round(smallest, 3),
        "target_mm": round(max(targets), 3),
        "compliant_pct": round(100.0 * ok / total, 1),
    }


def progress_note(mode: str, before_un: int, after_un: int) -> str:
    """What to do next -- above all when repair can get no further.

    repair finds paths only between the copper already there, and that is
    what stops it; once it is stuck, running it again changes nothing. The
    next step is --mode full: clear every track and route in another order.
    On a 15-part ATmega328P board repair stuck at one open connection
    through three runs; full connected all 35 in one. This only suggests it:
    full deletes every track on the board, hand-drawn ones too, and that is
    a person's call.
    """
    if mode == "repair" and after_un > 0 and after_un >= before_un:
        return (
            "repair 这一轮一条都没修掉，再跑也不会变（它只能在现有铜的缝隙里找路）。"
            f"还剩 {after_un} 条不通，见 unresolved。下一步：board route --mode full "
            "——它会清掉板上全部现有走线重新布线，换一个顺序常常能全通；"
            "手工画过的走线会一起没掉，所以这一步要人点头。"
        )
    return "线宽此时是缩颈宽度，跑 board widen 加宽；repair 模式可反复跑到不再改善"


# -- painting and committing --------------------------------------------------------


def paint_existing(R: Router, pieces: list[Piece]) -> int:  # noqa: N803
    """Every track and via onto the grid, in pcbnew's order; the tracks on
    the two signal layers counted."""
    nt = 0
    for t in pieces:
        if t.kind == "via":
            # The via's own diameter, not its drill plus a guess.
            R.paint_via(mm(t.start[0]), mm(t.start[1]), mm(t.width), t.code,
                        drill_nm=t.drill, position_nm=t.start)  # fmt: skip
        elif t.layer in (FRONT, BACK):
            layer = F if t.layer == FRONT else B
            R.paint_seg(layer, mm(t.start[0]), mm(t.start[1]), mm(t.end[0]), mm(t.end[1]),
                        mm(t.width), t.code)  # fmt: skip
            nt += 1
    return nt


def rebuild(layout: Layout) -> Router:
    R = layout.router()  # noqa: N806
    paint_existing(R, layout.pieces)
    return R


def layers_at(R: Router, x: float, y: float, code: int) -> list[int]:  # noqa: N803
    """The layers where the net already has copper at the point.

    The one constraint everything rests on: a path allowed to start on any
    layer starts on many a layer with no pad there, connects to nothing, and
    raises no error -- "the completion rate will not go up".
    """
    i, j = R.gx(x), R.gy(y)
    out = [
        layer for layer in (F, B)
        if 0 <= i < R.W and 0 <= j < R.H and R.holds(layer, i, j, code)
    ]  # fmt: skip
    return out or [F, B]


def commit(layout: Layout, R: Router, path: list, code: int, width: float,  # noqa: N803
           via_d: float, via_h: float, snap_a=None, snap_b=None) -> list[Piece]:  # fmt: skip
    """Lay a path on the board: its tracks and vias, painted as they go.
    Returns what was added, for the caller to take up again exactly."""
    hole_guard = R.copper.via_hole_guard
    if hole_guard is not None:
        holes = list(R.holes)
        for hole in R.path_holes(path, MM(via_h)):
            hole_guard.require_clearance(*hole, holes)
            holes.append(hole)
    segs: list[tuple[str, Any]] = []
    cur = [path[0]]
    for a, bn in zip(path, path[1:], strict=False):
        if a[0] != bn[0] or a[3] != bn[3]:
            segs.append(("T", cur))
            if a[0] != bn[0]:
                segs.append(("V", a))
            cur = [bn] if a[0] != bn[0] else [a, bn]
        else:
            cur.append(bn)
    segs.append(("T", cur))
    made = []
    created = []
    for kind, data in segs:
        if kind == "V":
            _layer, i, j, _w = data
            at = (MM(R.wx(i)), MM(R.wy(j)))
            guard = R.copper.smd_drill_guard
            if guard is not None:
                guard.require_clearance(*at, MM(via_h))
            via = layout.add(Piece("via", at, at, MM(via_d), FRONT, code, MM(via_h)))
            created.append(via)
            R.paint_via(R.wx(i), R.wy(j), via_d, code, drill_nm=MM(via_h), position_nm=at)
            R.stats["vias"] += 1
            continue
        if len(data) < 2:
            continue
        segw = data[0][3]
        pts = Router.simplify(data)
        for p, q in zip(pts, pts[1:], strict=False):
            t = layout.add(Piece(
                "segment",
                (MM(R.wx(p[1])), MM(R.wy(p[2]))),
                (MM(R.wx(q[1])), MM(R.wy(q[2]))),
                MM(segw),
                LAYERS[p[0]],
                code,
            ))  # fmt: skip
            created.append(t)
            made.append((t, p[0], segw))
            R.paint_seg(p[0], R.wx(p[1]), R.wy(p[2]), R.wx(q[1]), R.wy(q[2]), segw, code)
            R.stats["tracks"] += 1
    # Rounding to the grid leaves the first and last ends up to half a cell
    # off the pad's centre, so they are pulled onto it -- but only that far.
    # If the path changes layer first, its first track starts at the via on
    # the other layer, and pulling it to the pad would lay an unchecked track
    # across the pins, on the wrong layer.
    snap_max = 1.5 * GRID
    if made and snap_a:
        t, layer, w_ = made[0]
        if math.hypot(mm(t.start[0]) - snap_a[0], mm(t.start[1]) - snap_a[1]) <= snap_max:
            t.start = (MM(snap_a[0]), MM(snap_a[1]))
            R.paint_seg(layer, snap_a[0], snap_a[1], mm(t.end[0]), mm(t.end[1]), w_, code)
    if made and snap_b:
        t, layer, w_ = made[-1]
        if math.hypot(mm(t.end[0]) - snap_b[0], mm(t.end[1]) - snap_b[1]) <= snap_max:
            t.end = (MM(snap_b[0]), MM(snap_b[1]))
            R.paint_seg(layer, mm(t.start[0]), mm(t.start[1]), snap_b[0], snap_b[1], w_, code)
    return created


def _ordinary_via_dimensions(diameter: float, drill: float) -> tuple[int, int]:
    """Use the actual saved drill, with a positive ordinary copper annulus."""
    if any(
        type(value) not in (int, float) or not math.isfinite(value) for value in (diameter, drill)
    ):
        raise ViaHoleConstraintError("Positive ordinary circular through-via dimensions required")
    diameter_nm, drill_nm = MM(diameter), MM(drill)
    if not 0 < drill_nm < diameter_nm:
        raise ViaHoleConstraintError("Positive ordinary circular through-via dimensions required")
    return diameter_nm, drill_nm


def _require_via_clearance(layout: Layout, R: Router, at, drill_nm: int) -> None:  # noqa: N803
    """Keep final placement independent of the earlier candidate search."""
    hole_guard = R.copper.via_hole_guard
    if hole_guard is not None:
        if getattr(layout, "path", None) is not None:
            hole_guard.require_binding(layout.path)
        hole_guard.require_clearance(*at, drill_nm, R.holes + R.planned_holes)
    guard = R.copper.smd_drill_guard
    if guard is not None:
        guard.require_clearance(*at, drill_nm)


def place_via(layout: Layout, R: Router, x: float, y: float, code: int,  # noqa: N803
              vd: float, vh: float) -> None:  # fmt: skip
    diameter_nm, drill_nm = _ordinary_via_dimensions(vd, vh)
    at = (MM(x), MM(y))
    _require_via_clearance(layout, R, at, drill_nm)
    layout.add(Piece("via", at, at, diameter_nm, FRONT, code, drill_nm))
    R.paint_via(x, y, vd, code, drill_nm=drill_nm, position_nm=at)


# -- footprints ----------------------------------------------------------------------


def min_pitch(fp) -> float:
    ps = [(mm(p.position[0]), mm(p.position[1])) for p in fp.pads]
    if len(ps) < 3:
        return 9.9
    best = 9.9
    for i in range(len(ps)):
        for j in range(i + 1, len(ps)):
            d = math.hypot(ps[i][0] - ps[j][0], ps[i][1] - ps[j][1])
            if 0.01 < d < best:
                best = d
    return best


def pad_bbox(fp) -> tuple:
    """The box round a footprint's pads: which side a pin is on is judged
    by it, not by the courtyard."""
    left = top = right = bottom = None
    for p in fp.pads:
        x1, y1, x2, y2 = pad_box(p)
        left = mm(x1) if left is None else min(left, mm(x1))
        top = mm(y1) if top is None else min(top, mm(y1))
        right = mm(x2) if right is None else max(right, mm(x2))
        bottom = mm(y2) if bottom is None else max(bottom, mm(y2))
    return left, top, right, bottom


def pad_side(box, px: float, py: float):
    """Which side of its footprint a pin is nearest: (normal, side, distance).

    A direction from the courtyard's centre was 30 to 53 degrees off the
    pins' true normal -- an uneven courtyard pushes its centre aside -- and
    the escapes crossed each other. By side, the pins of a side are parallel.
    """
    left, top, right, bottom = box
    d = {"L": px - left, "R": right - px, "T": py - top, "B": bottom - py}
    side = min(d, key=d.get)
    nrm = {"L": (-1.0, 0.0), "R": (1.0, 0.0), "T": (0.0, -1.0), "B": (0.0, 1.0)}[side]
    return nrm, side, d[side]


def pad_layers(layout: Layout, pad: Pad) -> list[int]:
    """The layers a pad is really on. With the net's own copper taken up,
    layers_at falls back to both, and a path may end on a layer with no pad."""
    out = []
    if layout.is_on(pad, FRONT):
        out.append(F)
    if layout.is_on(pad, BACK):
        out.append(B)
    return out or [F]


def mst_edges(pts: list) -> list[tuple[int, int]]:
    n = len(pts)
    if n < 2:
        return []
    used, rest, edges = [0], list(range(1, n)), []
    while rest:
        best = None
        for a in used:
            for c in rest:
                d = math.hypot(pts[a][0] - pts[c][0], pts[a][1] - pts[c][1])
                if best is None or d < best[0]:
                    best = (d, a, c)
        edges.append((best[1], best[2]))
        used.append(best[2])
        rest.remove(best[2])
    return edges


# -- full: escapes and plane fan-out ---------------------------------------------------


def escape_pins(layout: Layout, R: Router, nc: NetClasses, neck: float,  # noqa: N803
                plane_nets: set[str], log: dict) -> dict:  # fmt: skip
    """Lead each pin of a fine-pitch part straight out of its row.

    Two rules; break either and the pins done last are walled in:

    1. Out along the normal only, within 35 degrees -- never along the
       footprint's side. Escapes once tried 60 to 90 degrees, which runs
       along the side and builds a wall there: one signal on a TSSOP laid
       259 cells of copper in 3.6 x 3.6 mm round a single pin.
    2. No via: 32 vias do not fit round a 32-pin part. An escape only has to
       leave the row of pins; the router decides the layer after.

    Pins on one side are a pitch apart and do not cross, so the order does
    not matter. The ends are staggered a little, so that they do not line up
    into a wall across the routes that come after.
    """
    npads: dict[int, int] = {}
    for _fp, p in layout.pads:
        code = layout.codes.get(p.net, 0)
        npads[code] = npads.get(code, 0) + 1
    targets = []
    for fp in layout.board.footprints:
        if len(fp.pads) >= 6:
            pitch = min_pitch(fp)
            if pitch <= 1.30:
                targets.append((pitch, fp))
    targets.sort(key=lambda t: t[0])

    access: dict[tuple[str, str], tuple[float, float]] = {}
    ok = fail = skip = 0
    per_fp = {}
    for _pitch, fp in targets:
        box = pad_bbox(fp)
        left, top, right, bottom = courtyard_box(fp)
        groups: dict[str, list] = {}
        for p in fp.pads:
            px, py = mm(p.position[0]), mm(p.position[1])
            nrm, side, _ = pad_side(box, px, py)
            groups.setdefault(side, []).append((p, px, py, nrm))
        fok = ffail = 0
        for side, items in groups.items():
            items.sort(key=lambda t: t[2] if side in ("L", "R") else t[1])
            for idx, (p, px, py, (nx, ny)) in enumerate(items):
                nn = p.net
                code = layout.codes.get(nn, 0)
                if (
                    nn in plane_nets
                    or nn.startswith("unconnected-")
                    or not nn
                    or npads.get(code, 0) < 2
                ):
                    skip += 1
                    continue
                w, cl, vd, vh = nc.params(nn)
                ww = min(w, neck)
                base = (
                    abs((right if nx > 0 else left) - px)
                    if nx
                    else abs((bottom if ny > 0 else top) - py)
                )
                got = None
                for extra in (
                    ESCAPE_CLEAR,
                    ESCAPE_CLEAR + ESCAPE_STAGGER * (idx % 3),
                    ESCAPE_CLEAR + 1.6,
                    ESCAPE_CLEAR + 2.6,
                ):
                    for adeg in (0, 12, -12, 24, -24, 35, -35):
                        ca = math.cos(math.radians(adeg))
                        sa = math.sin(math.radians(adeg))
                        ex, ey = nx * ca - ny * sa, nx * sa + ny * ca
                        d = base + extra
                        x = float(px + ex * d)
                        y = float(py + ey * d)
                        path = R.route(
                            code, [(px, py, F)], [(x, y, F)], ww, cl, vd, vh,
                            allow_via=False, margin=5.0, neck=ww, neck_r=99.0,
                        )  # fmt: skip
                        if path is not None:
                            commit(layout, R, path, code, ww, vd, vh, snap_a=(px, py))
                            access[(fp.reference, p.number)] = (x, y)
                            got = (x, y)
                            break
                    if got:
                        break
                if got:
                    ok += 1
                    fok += 1
                else:
                    fail += 1
                    ffail += 1
        per_fp[fp.reference] = "%d/%d" % (fok, fok + ffail)  # noqa: UP031
    log["escape"] = {"per_footprint": per_fp, "ok": ok, "failed": fail, "skipped": skip}
    return access


def zone_covers(zones: list, x: float, y: float) -> bool:
    """Whether a zone's fill covers (x, y), within ZONE_TOUCH_MM.

    The fill, not the outline: an outline covers the board, its fill is cut
    to pieces by tracks, pads and clearances. Judged by the outline, a place
    with no copper would pass for joined.
    """
    for zone, layer in zones:
        rings = zone.filled.get(layer, [])
        if not rings:
            continue
        px, py = MM(x), MM(y)
        reach = MM(ZONE_TOUCH_MM)
        for ring in rings:
            if _collides(ring, px, py, reach):
                return True
    return False


def _collides(ring: list[tuple[int, int]], x: int, y: int, reach: int) -> bool:
    """A point in a filled ring, or within reach of its edge."""
    n = len(ring)
    inside = False
    j = n - 1
    near = reach * reach
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        dx, dy = xi - xj, yi - yj
        length2 = dx * dx + dy * dy
        t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((x - xj) * dx + (y - yj) * dy) / length2))
        ex, ey = xj + t * dx - x, yj + t * dy - y
        if ex * ex + ey * ey <= near:
            return True
        j = i
    return inside


def first_layer(board: Board, layers: list[str]) -> str | None:
    """The first of a zone's layers in pcbnew's numbering: F.Cu, B.Cu, then
    the inner layers in order."""
    copper = [name for name in layers if name.endswith(".Cu")]

    def key(name: str) -> int:
        if name == FRONT:
            return 0
        if name == BACK:
            return 2
        if name.startswith("In") and name[2:-3].isdigit():
            return 2 + 2 * int(name[2:-3])
        return 10_000

    return min(copper, key=key) if copper else (layers[0] if layers else None)


def via_spot_ok(R: Router, x: float, y: float, d: float, code: int,  # noqa: N803
                *, drill: float) -> bool:  # fmt: skip
    """Check the hole independently of copper ownership before selecting a spot.

    A local collision is another position to search. Unread dimensions or
    unsupported policies still raise; they are not a blocked grid cell.
    """
    _diameter_nm, drill_nm = _ordinary_via_dimensions(d, drill)
    at = (MM(x), MM(y))
    guard = R.copper.smd_drill_guard
    if guard is not None and not guard.allows(*at, drill_nm):
        return False
    hole_guard = R.copper.via_hole_guard
    if hole_guard is not None and not hole_guard.allows(*at, drill_nm, R.holes + R.planned_holes):
        return False
    r = int(math.ceil(((d / 2.0) + 0.35) / GRID))
    i, j = R.gx(x), R.gy(y)
    if not (r <= i < R.W - r and r <= j < R.H - r):
        return False
    rows, cols = (j - r, j + r + 1), (i - r, i + r + 1)
    return not any(R.foreign(layer, code, rows, cols) for layer in (F, B))


def fanout_planes(layout: Layout, R: Router, nc: NetClasses, plane_nets: set[str],  # noqa: N803
                  log: dict) -> set[str]:  # fmt: skip
    """Finite two-layer fan-out from front SMD pads to their saved zone fill.

    Only ordinary F.Cu/B.Cu vias and single-outer-layer zones are supported.
    Coverage remains the existing finite fill-contact heuristic; it does not
    prove Native connectivity, refill, arbitrary zone geometry or plane quality.
    """
    board = layout.board
    if plane_nets:
        if board.copper_layers != [FRONT, BACK] or any(
            z.layers not in ([FRONT], [BACK])
            for z in board.zones
            if not z.rule_area and z.net in plane_nets
        ):
            raise ViaHoleConstraintError(
                "Plane fan-out supports only two-layer F.Cu/B.Cu boards "
                "and single-outer-layer zones"
            )
        hole_guard = R.copper.via_hole_guard
        if hole_guard is not None and getattr(layout, "path", None) is not None:
            hole_guard.require_binding(layout.path)
    parameters = {}
    for netname in sorted(plane_nets):
        code = layout.find(netname)
        if code is not None:
            w, cl, vd, vh = nc.params(netname)
            _diameter_nm, drill_nm = _ordinary_via_dimensions(vd, vh)
            parameters[netname] = (code, w, cl, vd, vh, drill_nm)
    res = {}
    served = set()
    for netname, (code, w, cl, vd, vh, drill_nm) in parameters.items():
        # Whether a pad needs a via is asked of the board: is it covered by
        # its net's copper on its own layer? GND used to be served by name.
        zones = [
            (z, first_layer(board, z.layers))
            for z in board.zones if not z.rule_area and z.net == netname
        ]  # fmt: skip
        top_zones = [(z, layer) for z, layer in zones if FRONT in z.layers]
        ok = fail = 0
        for _fp, p in layout.pads:
            if layout.codes.get(p.net, 0) != code or not layout.is_on(p, FRONT):
                continue
            if p.kind != "smd":
                continue  # a through-hole pad already passes every layer
            px, py = mm(p.position[0]), mm(p.position[1])
            if zone_covers(top_zones, px, py):
                ok += 1
                continue  # its net's copper covers it on its own layer
            spot = None
            path = None
            for step in range(0, 40):
                rad = step * 0.15
                for ang in range(0, 360, 15) if step else (0,):
                    x = float(px + rad * math.cos(math.radians(ang)))
                    y = float(py + rad * math.sin(math.radians(ang)))
                    if not zone_covers(zones, x, y) or not via_spot_ok(R, x, y, vd, code, drill=vh):
                        continue
                    path = None
                    if abs(x - px) > 1e-6 or abs(y - py) > 1e-6:
                        path = R.route(
                            code, [(px, py, F)], [(x, y, F)], min(w, 0.5), cl, vd, vh,
                            allow_via=False, margin=6.0, neck=min(w, 0.5), neck_r=99.0,
                        )  # fmt: skip
                        if path is None:
                            continue
                    spot = (x, y)
                    break
                if spot:
                    break
            if not spot:
                fail += 1
                continue
            _require_via_clearance(layout, R, (MM(spot[0]), MM(spot[1])), drill_nm)
            if path is not None:
                commit(layout, R, path, code, min(w, 0.5), vd, vh, snap_a=(px, py))
            place_via(layout, R, spot[0], spot[1], code, vd, vh)
            ok += 1
        res[netname] = "%d/%d" % (ok, ok + fail)  # noqa: UP031
        if fail == 0:
            served.add(netname)
    log["fanout"] = res
    return served


def mode_full(layout: Layout, R: Router, nc: NetClasses, neck: float, log: dict,  # noqa: N803
              use_planes: bool = False) -> None:  # fmt: skip
    """--use-planes lets each pour carry its own net; off by default.

    On the 15-part board this chain makes, planes on took unconnected items
    34 -> 0 and copper 257.6 -> 178.1 mm. But on KiCad's interf_u, with a
    real GND pour, DRC errors went 3 -> 5 (starved thermals: that pour's
    spokes cannot carry those joins) and the run was rolled back -- so it is
    a switch, and a worse DRC rolls back either way.
    """
    board = layout.board
    plane_nets: set[str] = set()
    if use_planes:
        plane_nets = {z.net for z in board.zones if not z.rule_area}
    log["plane_nets"] = sorted(plane_nets)
    # Only a net whose fan-out is complete is served by its plane: a plane
    # covering part of the board leaves the pads it cannot reach to tracks.
    served = fanout_planes(layout, R, nc, plane_nets, log)
    log["plane_served"] = sorted(served)
    access = escape_pins(layout, R, nc, neck, served, log)
    plane_nets = served
    pads_by_net: dict[int, list] = {}
    for fp, p in layout.pads:
        code = layout.codes.get(p.net, 0)
        if code:
            pads_by_net.setdefault(code, []).append((fp, p))
    todo = []
    for code, ps in pads_by_net.items():
        if len(ps) < 2:
            continue
        nn = layout.names[code]
        if nn.startswith("unconnected-"):
            continue
        if nn in plane_nets:
            continue  # its plane joins it
        pts = []
        for fp, p in ps:
            key = (fp.reference, p.number)
            pts.append(access.get(key, (mm(p.position[0]), mm(p.position[1]))))
        todo.append((code, pts))
    # Short nets first: local decoupling, bootstrap and charge-pump links
    # take their places before the long runs.
    todo.sort(key=lambda t: max(math.hypot(a[0] - c[0], a[1] - c[1]) for a in t[1] for c in t[1]))
    ok = fail = 0
    per_net: dict[str, list[int]] = {}
    for code, pts in todo:
        nn = layout.names[code]
        w, cl, vd, vh = nc.params(nn)
        ww = min(w, neck)
        st = per_net.setdefault(nn, [0, 0])
        for a, c in mst_edges(pts):
            path = R.route(
                code,
                [(*pts[a], layer) for layer in layers_at(R, pts[a][0], pts[a][1], code)],
                [(*pts[c], layer) for layer in layers_at(R, pts[c][0], pts[c][1], code)],
                ww, cl, vd, vh, allow_via=True,
                margin=max(14.0, 0.5 * math.hypot(pts[a][0] - pts[c][0], pts[a][1] - pts[c][1])),
                neck=ww, neck_r=99.0,
            )  # fmt: skip
            log.setdefault("route_search", []).append(
                {"net": nn, **getattr(R, "last_search", {"status": "unreported"})}
            )
            if path is None:
                fail += 1
                st[1] += 1
            else:
                commit(layout, R, path, code, ww, vd, vh, snap_a=pts[a], snap_b=pts[c])
                ok += 1
                st[0] += 1
    log["routed"] = ok
    log["failed"] = fail
    log["unresolved"] = {k: v for k, v in sorted(per_net.items()) if v[1]}


# -- repair ------------------------------------------------------------------------------


def snapshot(pieces: list[Piece]) -> list[dict]:
    """The geometry of what is taken up, to route again or to put back."""
    out = []
    for t in pieces:
        if t.kind == "via":
            out.append({
                "kind": "via",
                "net": t.code,
                "x": mm(t.start[0]),
                "y": mm(t.start[1]),
                "drill": mm(t.drill),
                # The diameter too: a via put back with only its drill takes
                # the default diameter, and DRC reports via_diameter for it.
                "d": mm(t.width),
            })  # fmt: skip
        else:
            out.append({
                "kind": "track",
                "net": t.code,
                "layer": t.layer,
                "w": mm(t.width),
                "x1": mm(t.start[0]),
                "y1": mm(t.start[1]),
                "x2": mm(t.end[0]),
                "y2": mm(t.end[1]),
            })  # fmt: skip
    return out


def restore(layout: Layout, snaps: list[dict]) -> list[Piece]:
    out = []
    for s in snaps:
        if s["kind"] == "via":
            at = (MM(s["x"]), MM(s["y"]))
            width = MM(s.get("d") or (s["drill"] + 0.3))
            out.append(layout.add(Piece("via", at, at, width, FRONT, s["net"], MM(s["drill"]))))
        else:
            if abs(s["x1"] - s["x2"]) < 1e-6 and abs(s["y1"] - s["y2"]) < 1e-6:
                continue  # a track of no length put back broke pcbnew's zone filler
            out.append(layout.add(Piece(
                "segment",
                (MM(s["x1"]), MM(s["y1"])),
                (MM(s["x2"]), MM(s["y2"])),
                MM(s["w"]),
                s["layer"],
                s["net"],
            )))  # fmt: skip
    return out


def undo(layout: Layout, added: list[Piece], snaps: list[dict]) -> list[Piece]:
    """Take back a rip-up: what it added out, what it took up back in.

    What it added includes the target's own new tracks: putting back only
    what was taken up once left 16 overlapping pairs in 20 attempts, and DRC
    a wall of crossings and shorts.
    """
    for t in added:
        layout.remove(t)
    return restore(layout, snaps)


def near_items(layout: Layout, x: float, y: float, r: float, code: int, limit: int = 16):
    """Other nets' copper near a point, nearest first, at most `limit`.

    A dense corner easily has more than a dozen; giving up past a limit gave
    up exactly where a rip-up was needed. The nearest few are cheap to route
    again and all put back if that fails.
    """
    out = []
    for t in list(layout.pieces):
        if t.code == code:
            continue
        if t.kind == "via":
            dv = math.hypot(mm(t.start[0]) - x, mm(t.start[1]) - y)
            if dv < r:
                out.append((dv, t))
        else:
            ax, ay = mm(t.start[0]), mm(t.start[1])
            bx, by = mm(t.end[0]), mm(t.end[1])
            vx, vy = bx - ax, by - ay
            l2 = vx * vx + vy * vy
            if l2 > 1e-9:
                u = max(0.0, min(1.0, ((x - ax) * vx + (y - ay) * vy) / l2))
                d = math.hypot(ax + u * vx - x, ay + u * vy - y)
            else:
                d = math.hypot(ax - x, ay - y)
            if d < r:
                out.append((d, t))
    out.sort(key=lambda z: z[0])
    return [t for _, t in out[:limit]]


def try_ripup(layout: Layout, R: Router, nc: NetClasses, neck: float, code: int,  # noqa: N803
              ax, ay, bx, by, ww, cl, vd, vh, log):  # fmt: skip
    """Take up other nets' copper near a blocked end, route the target, and
    route what was taken up again. All or nothing: if the target fails, or
    what was taken up cannot be routed back, everything goes back as it was."""
    for px, py in ((ax, ay), (bx, by)):
        victims = near_items(layout, px, py, RIPUP_R, code)
        if not victims:
            continue
        snaps = snapshot(victims)
        for t in victims:
            layout.remove(t)
        R2 = rebuild(layout)  # noqa: N806
        p2 = R2.route(
            code,
            [(ax, ay, layer) for layer in layers_at(R2, ax, ay, code)],
            [(bx, by, layer) for layer in layers_at(R2, bx, by, code)],
            ww, cl, vd, vh, allow_via=True,
            margin=max(25.0, 0.9 * math.hypot(bx - ax, by - ay)),
            neck=ww, neck_r=99.0,
        )  # fmt: skip
        if p2 is None:
            undo(layout, [], snaps)
            R2 = rebuild(layout)  # noqa: N806
            continue
        added = list(commit(layout, R2, p2, code, ww, vd, vh, snap_a=(ax, ay), snap_b=(bx, by)))
        back_ok = True
        for s in snaps:
            if s["kind"] == "via":
                continue  # vias come back with their tracks, where A* puts them
            name = layout.names.get(s["net"])
            if name is None:
                continue
            w2, cl2, vd2, vh2 = nc.params(name)
            p3 = R2.route(
                s["net"],
                [(s["x1"], s["y1"], layer) for layer in layers_at(R2, s["x1"], s["y1"], s["net"])],
                [(s["x2"], s["y2"], layer) for layer in layers_at(R2, s["x2"], s["y2"], s["net"])],
                min(w2, neck), cl2, vd2, vh2, allow_via=True, margin=18.0,
                neck=min(w2, neck), neck_r=99.0,
            )  # fmt: skip
            if p3 is None:
                back_ok = False
                break
            added += list(commit(layout, R2, p3, s["net"], min(w2, neck), vd2, vh2,
                                 snap_a=(s["x1"], s["y1"]), snap_b=(s["x2"], s["y2"])))  # fmt: skip
        if back_ok:
            log.setdefault("ripup", {"attempts": 0, "success": 0, "removed": 0})
            log["ripup"]["attempts"] += 1
            log["ripup"]["success"] += 1
            log["ripup"]["removed"] += len(snaps)
            return True, R2
        undo(layout, added, snaps)
        R2 = rebuild(layout)  # noqa: N806
        log.setdefault("ripup", {"attempts": 0, "success": 0, "removed": 0})
        log["ripup"]["attempts"] += 1
        return None, R2
    return None, R


def missing_connections(path: str, *, library_checks=True) -> list:
    """The DRC's missing connections on the board as saved: each two items,
    the first one's net named in its description."""
    return drc.check(
        path, kicad_root=kicad_env.find_kicad_root() if library_checks else None
    ).unconnected


def mode_repair(layout: Layout, R: Router, nc: NetClasses, neck: float, log: dict,  # noqa: N803
                ripup: bool = False, selected: set[str] | None = None,
                library_checks: bool = True) -> None:  # fmt: skip
    un = missing_connections(layout.path, **({} if library_checks else {"library_checks": False}))
    log["targets"] = len(un)
    ok = fail = 0
    per_net: dict[str, list[int]] = {}
    for u in un:
        items = u.items
        if len(items) < 2:
            continue
        nn = None
        for it in items:
            m = re.search(r"\[([^\]]+)\]", it.description)
            if m:
                nn = m.group(1)
                break
        if not nn:
            continue
        if selected is not None and nn not in selected:
            continue
        code = layout.find(nn)
        if code is None:
            continue
        w, cl, vd, vh = nc.params(nn)
        ww = min(w, neck)
        ax, ay = mm(items[0].position[0]), mm(items[0].position[1])
        bx, by = mm(items[1].position[0]), mm(items[1].position[1])
        dist = math.hypot(bx - ax, by - ay)
        path = R.route(
            code,
            [(ax, ay, layer) for layer in layers_at(R, ax, ay, code)],
            [(bx, by, layer) for layer in layers_at(R, bx, by, code)],
            ww, cl, vd, vh, allow_via=True, margin=max(25.0, 0.9 * dist), neck=ww, neck_r=99.0,
        )  # fmt: skip
        st = per_net.setdefault(nn, [0, 0])
        if path is None and ripup:
            path, R = try_ripup(layout, R, nc, neck, code, ax, ay, bx, by, ww, cl, vd, vh, log)  # noqa: N806
        if path is None:
            fail += 1
            st[1] += 1
        else:
            if path is not True:
                commit(layout, R, path, code, ww, vd, vh, snap_a=(ax, ay), snap_b=(bx, by))
            ok += 1
            st[0] += 1
        log.setdefault("route_search", []).append(
            {"net": nn, **getattr(R, "last_search", {"status": "unreported"})}
        )
    log["routed"] = ok
    log["failed"] = fail
    log["unresolved"] = {k: v for k, v in sorted(per_net.items()) if v[1]}


# -- rewidth ------------------------------------------------------------------------------


def rewidth_targets(layout: Layout, nc: NetClasses, classes) -> list[str]:
    """The nets to route again at their class's width, longest first: the
    short ones find their way through the gaps more easily, so they go last."""
    plane_nets = {z.net for z in layout.board.zones if not z.rule_area}
    pads: dict[int, list[Pad]] = {}
    for _fp, p in layout.pads:
        code = layout.codes.get(p.net, 0)
        if code:
            pads.setdefault(code, []).append(p)
    out = []
    for code, ps in pads.items():
        if len(ps) < 2:
            continue
        nn = layout.names[code]
        if nn in plane_nets:
            continue  # a plane carries its current; its tracks are via stubs
        if nc.name_for(nn) not in classes:
            continue
        span = max(
            math.hypot(mm(a.position[0]) - mm(c.position[0]), mm(a.position[1]) - mm(c.position[1]))
            for a in ps
            for c in ps
        )
        out.append((span, nn))
    out.sort(reverse=True)
    return [nn for _span, nn in out]


def rewidth_one(path: str, nc: NetClasses, neck: float, nn: str, *, smd_drill_guard=None,
                via_hole_guard=None, diagnostics=None):  # fmt: skip
    """Route one net again, on the board as saved; (what became of it, the
    net's geometry before).

    The width steps down when the class's will not fit, rather than giving
    up: 0.8 mm for a 2 A track drawn at 0.2 mm is 0.9 A raised to 2.2 A. The
    last step is always the neck, so a net is never left worse than it was.
    """
    layout = Layout(
        path,
        **({"smd_drill_guard": smd_drill_guard} if smd_drill_guard is not None else {}),
        **({"via_hole_guard": via_hole_guard} if via_hole_guard is not None else {}),
    )
    code = layout.find(nn)
    if code is None:
        return "跳过（板上找不到该网络）", None
    ps = [p for _fp, p in layout.pads if layout.codes.get(p.net, 0) == code]
    if len(ps) < 2:
        return "跳过（焊盘少于两个）", None
    w, cl, vd, vh = nc.params(nn)
    victims = [t for t in layout.pieces if t.code == code]
    snaps = snapshot(victims)
    for t in victims:
        layout.remove(t)
    R = rebuild(layout)  # noqa: N806
    pts = [(mm(p.position[0]), mm(p.position[1]), pad_layers(layout, p)) for p in ps]
    ladder = [x for x in (w, round(w * 0.75, 2), round(w * 0.5, 2)) if x > neck + 1e-9] + [neck]
    for w_try in ladder:
        if getattr(getattr(R, "copper", None), "via_hole_guard", None) is not None:
            R.planned_holes.clear()
        plans, ok = [], True
        for a, c in mst_edges([(x, y) for x, y, _ in pts]):
            p = R.route(
                code,
                [(pts[a][0], pts[a][1], layer) for layer in pts[a][2]],
                [(pts[c][0], pts[c][1], layer) for layer in pts[c][2]],
                w_try, cl, vd, vh, allow_via=True,
                margin=max(18.0, 0.7 * math.hypot(pts[a][0] - pts[c][0], pts[a][1] - pts[c][1])),
                # Better too small than too large: once widths were decided
                # cell by cell, a larger neck radius was expected to cost
                # nothing. Measured on one board, 1.0 kept 6 nets and 2.5
                # kept 1 -- the DRC revert took the rest.
                neck=min(w_try, neck), neck_r=REWIDTH_NECK_R,
            )  # fmt: skip
            if diagnostics is not None:
                diagnostics.append(
                    {"net": nn, **getattr(R, "last_search", {"status": "unreported"})}
                )
            if p is None:
                ok = False
                break
            if getattr(getattr(R, "copper", None), "via_hole_guard", None) is not None:
                R.reserve_path_holes(p, MM(vh))
            plans.append((p, (pts[a][0], pts[a][1]), (pts[c][0], pts[c][1])))
        if not ok:
            continue  # this width does not fit; try the next, nothing laid yet
        added: list[Piece] = []
        if getattr(getattr(R, "copper", None), "via_hole_guard", None) is not None:
            R.planned_holes.clear()  # commits now register the actual saved holes
        for p, sa, sb in plans:
            added += commit(layout, R, p, code, w_try, vd, vh, snap_a=sa, snap_b=sb)
        tot = full = 0.0
        for t in added:
            if t.kind != "segment":
                continue
            tot += t.length
            if mm(t.width) >= w_try - 1e-6:
                full += t.length
        layout.fill_and_save()
        return (
            "重布 目标%.2f 实落%.2f 达标 %.0f%%（%.1f/%.1f mm）"  # noqa: UP031
            % (w, w_try, 100.0 * full / max(tot, 1e-9), full, tot)
        ), snaps
    # Not even the neck fits: nothing was saved, the board is as it was.
    return "保持原状（降到 %.2f mm 仍布不通）" % ladder[-1], None  # noqa: UP031


def revert_net(path: str, nn: str, snaps: list[dict]) -> None:
    """Put a net back as it was before it was routed again."""
    layout = Layout(path)
    code = layout.find(nn)
    if code is None:
        return
    for t in [t for t in layout.pieces if t.code == code]:
        layout.remove(t)
    restore(layout, snaps)
    layout.fill_and_save()


def strip(path: str, nets: str) -> tuple[dict[str, list[dict]], int]:
    """Take up every track of the named nets at once, so that the order they
    are routed again in really is their priority: taken up one by one, the
    first still meets the old tracks of the rest."""
    layout = Layout(path)
    want = {x.strip() for x in (nets or "").split(",") if x.strip()}
    snaps: dict[str, list[dict]] = {}
    victims = [t for t in layout.pieces if layout.names.get(t.code, "") in want]
    for t in victims:
        snaps.setdefault(layout.names.get(t.code, ""), []).extend(snapshot([t]))
    for t in victims:
        layout.remove(t)
    layout.fill_and_save()
    return snaps, len(victims)


def stats(path: str) -> dict[str, Any]:
    layout = Layout(path)
    tr = [t for t in layout.pieces if t.kind == "segment"]
    return {
        "unconnected": unconnected(layout.board),
        "tracks": len(tr),
        "vias": len(layout.pieces) - len(tr),
        "len_mm": round(sum(t.length for t in tr), 1),
    }


def rewidth_classes(args: dict, path: str) -> tuple[str, ...]:
    """Which net classes to route again: those named, or every class the
    project has but Default -- a class exists because someone wanted other
    geometry for it, and putting that geometry into copper is this mode's job.
    A name not in the project is a typo and says so."""
    pro, _ = load_project(path)
    defined = [c.get("name") for c in pro.get("net_settings", {}).get("classes", [])]
    asked = [c.strip() for c in (args.get("classes") or "").split(",") if c.strip()]
    if not asked:
        chosen = tuple(n for n in defined if n and n != "Default")
        if not chosen:
            envelope.fail(
                "E_VALIDATION",
                "工程里除 Default 外没有别的网络类，rewidth 无从下手",
                {
                    "defined": defined,
                    "hint": "先用 board netclass 建一个类并把网络指派进去，再跑 rewidth",
                },
            )
        return chosen
    missing = [name for name in asked if name not in defined]
    if missing:
        envelope.fail(
            "E_NOT_FOUND",
            "工程里没有这些网络类",
            {
                "missing": missing,
                "defined": defined,
                "hint": "用 board netclass 先建出来，或改用工程里已有的类名",
            },
        )
    return tuple(asked)


def _names_in(report_items) -> set[str]:
    out = set()
    for u in report_items:
        for it in u.items:
            m = re.search(r"\[([^\]]+)\]", it.description)
            if m:
                out.add(m.group(1))
    return out


# -- the command ------------------------------------------------------------------------------


def run(path: str, args: dict, shape: Callable[[Any], Any] = lambda d: d) -> None:
    write.start()
    mode = args.get("mode") or "repair"
    neck = float(args.get("neck") if args.get("neck") not in (None, "") else NECK_DEFAULT)
    if mode not in ("full", "repair", "rewidth"):
        envelope.fail("E_USAGE", "--mode 只能是 full / repair / rewidth", {"got": mode})
    classes = rewidth_classes(args, path) if mode == "rewidth" else ()

    layout = Layout(path)
    pro, _ = load_project(path)
    nc = NetClasses(pro)
    before_un = unconnected(layout.board)
    before_tr = len([t for t in layout.pieces if t.kind == "segment"])
    preview = {
        "board": path,
        "mode": mode,
        "neck_mm": neck,
        "unconnected_before": before_un,
        "tracks_before": before_tr,
        "will": {
            "full": "清空全部既有布线后重布",
            "repair": "保留现状，只补未连接项",
            "rewidth": (
                "按指定顺序重布 %s" % args["nets"]  # noqa: UP031
                if args.get("nets")
                else "拆掉 %s 类网络并按目标线宽重布" % ",".join(classes)  # noqa: UP031
            ),
        }[mode],
    }
    envelope.check_confirm(args.get("confirm"), "pcb_route:%s:%s" % (mode, os.path.basename(path)),  # noqa: UP031
                           preview, path)  # fmt: skip

    log: dict[str, Any] = {"mode": mode, "engine": "grid"}
    # The baseline the DRC is judged against, taken before anything changes.
    errors_baseline, baseline_report = errors(path)
    # Width is what DRC cannot see: full routes at the neck by design and
    # expects `board widen` after.
    width_before = width_summary(layout, nc)

    if mode == "rewidth":
        result = _rewidth(
            path,
            args,
            layout,
            nc,
            neck,
            classes,
            log,
            errors_baseline,
            baseline_report,
            before_un,
            shape,
        )
        write.done(result)
        return
    if mode == "repair":
        R = layout.router()  # noqa: N806
        log["existing_tracks"] = paint_existing(R, layout.pieces)
        mode_repair(layout, R, nc, neck, log, ripup=bool(args.get("ripup")))
    else:
        # full is "clear every track, then route" -- a refactor once lost the
        # clearing, and the new tracks lay on the old: twice the segments,
        # 548 DRC errors, what looked like a router out of control.
        old = list(layout.pieces)
        for t in old:
            layout.remove(t)
        log["cleared_tracks"] = len(old)
        R = layout.router()  # noqa: N806
        mode_full(layout, R, nc, neck, log, use_planes=bool(args.get("use-planes")))

    board = layout.fill_and_save()
    after_un = unconnected(board)
    # An equal/lower total can still hide a newly introduced error.
    errors_final, comparison = verify_baseline(path, errors_baseline, baseline_report)
    width_after = width_summary(layout, nc)
    log["verify"] = {
        "ran": True,
        "oracle": "engine",
        "errors_baseline": errors_baseline,
        "errors_final": errors_final,
        "finding_comparison": comparison,
        "width_before": width_before,
        "width_after": width_after,
        "width_regressed": bool(
            width_before
            and width_after
            and width_before.get("compliant_pct") is not None
            and width_after.get("compliant_pct") is not None
            and width_after["compliant_pct"] < width_before["compliant_pct"]
        ),
        "note": "自家 fileformat DRC 的非排除 error finding 无新增；其未检查/部分检查规则、"
        "warnings 和未连通不在此门禁内，不能据此宣布官方 DRC 或全板验收通过。"
        "线宽是 DRC 看不见的那部分：width_regressed 为真时走线已按缩颈宽度重布，"
        "载流能力随之下降，必须再跑 board widen 或 board rewidth 才算完成",
    }
    tr = [t for t in layout.pieces if t.kind == "segment"]
    write.done(shape(route_envelope({
        **log,
        "unconnected_before": before_un,
        "unconnected_after": after_un,
        "improved": before_un - after_un,
        "tracks": len(tr),
        "track_len_mm": round(sum(t.length for t in tr), 1),
        "vias": len(layout.pieces) - len(tr),
        "note": progress_note(mode, before_un, after_un),
    })))  # fmt: skip


def _rewidth(
    path, args, layout, nc, neck, classes, log, errors_baseline, baseline_report, before_un, shape
):
    # The board as it stands is saved first; each net is then routed on the
    # board as saved, read again.
    layout.fill_and_save()
    if args.get("nets"):
        # Named nets, in the order named: the hardest or most important first,
        # to take its lanes before the rest route round it.
        names = [x.strip() for x in args["nets"].split(",") if x.strip()]
    else:
        names = rewidth_targets(Layout(path), nc, classes)
    res: dict[str, str] = {}
    keep: dict[str, list] = {}
    pre: dict[str, list] = {}
    need_restore = []
    unconn_before_names: set[str] = set()
    if args.get("nets"):
        unconn_before_names = _names_in(missing_connections(path))
        pre, stripped = strip(path, args["nets"])
        log["stripped_segments"] = stripped
    for nn in names:
        guard = getattr(layout, "smd_drill_guard", None)
        hole_guard = getattr(layout, "via_hole_guard", None)
        txt, snaps = rewidth_one(
            path,
            nc,
            neck,
            nn,
            **({"smd_drill_guard": guard} if guard is not None else {}),
            **({"via_hole_guard": hole_guard} if hole_guard is not None else {}),
            diagnostics=log.setdefault("route_search", []),
        )
        if txt.startswith("跳过"):
            log.setdefault("skipped_nets", []).append(nn)
        res[nn] = txt
        if snaps:
            keep[nn] = snaps
        if nn in pre:
            if args.get("no-restore"):
                # After parts have moved: the old geometry joins pads where
                # they used to be, and putting it back lays tracks to nowhere.
                if not txt.startswith("重布"):
                    res[nn] = "拆后布不通，留空待补布（器件已挪位，不还原旧走线）"
                keep[nn] = []  # a DRC revert then only takes the new tracks out
                continue
            if not txt.startswith("重布"):
                # Put back only once the whole group is routed: the rest were
                # routed on a board without this net, and its old copper back
                # now would lie across them.
                need_restore.append(nn)
            keep[nn] = pre[nn]  # a DRC revert goes back to before the strip
    for nn in need_restore:
        # Only a net that was connected before: one already broken (BSNL,
        # say) put back is half a track of rubbish on the board.
        if nn in unconn_before_names:
            res[nn] = "拆后布不通，原本也未连通，未还原（留空待处理）"
            keep.pop(nn, None)
            continue
        revert_net(path, nn, pre[nn])
        res[nn] = "已还原（整组重布后仍布不通，恢复原走线）"
    log["rewidth"] = res
    log["rewidth_total"] = len(res)
    # The grid's clearance is an approximation and DRC the judge: a net that
    # brought an error goes back as it was. Again and again: putting A back
    # may meet B's new wide track, so one round may not settle it.
    intermediate_checks = []
    if not args.get("no-verify") and keep:
        alive, reverted = dict(keep), []
        for _round in range(5):
            _count, report = errors(
                path, **({} if args.get("library-checks", True) else {"library_checks": False})
            )
            intermediate_checks.append(drc_baseline.record(report))
            bad = set()
            for v in report.violations:
                if v.severity != "error" or v.excluded:
                    continue
                for it in v.items:
                    m2 = re.search(r"\[([^\]]+)\]", it.description)
                    if m2 and m2.group(1) in alive:
                        bad.add(m2.group(1))
                        log.setdefault("rewidth_drc_cause", []).append(
                            {"why": v.message, "items": [x.description for x in v.items]}
                        )
            if not bad:
                break
            for nn2 in sorted(bad):
                revert_net(path, nn2, alive.pop(nn2))
                res[nn2] = "已还原（重布引入 DRC error）"
                reverted.append(nn2)
        if reverted:
            log["rewidth_reverted"] = sorted(reverted)
    log["rewidth_ok"] = sum(1 for v in res.values() if v.startswith("重布"))
    # After per-net reverts compare identities; totals cannot hide replacement errors.
    errors_final, comparison = verify_baseline(
        path,
        errors_baseline,
        baseline_report,
        enabled=not args.get("no-verify"),
        intermediate_checks=intermediate_checks,
        library_checks=args.get("library-checks", True),
    )
    st = stats(path)
    return shape(route_envelope({
        **log,
        "unconnected_before": before_un,
        "unconnected_after": st["unconnected"],
        "tracks": st["tracks"],
        "improved": before_un - st["unconnected"],
        "track_len_mm": st["len_mm"],
        "vias": st["vias"],
        # rewidth is judged net by net and then as a whole; this says which.
        "verify": {
            "ran": not args.get("no-verify"),
            "oracle": "engine",
            "scope": "per-net revert loop, then a whole-board baseline",
            "reverted": log.get("rewidth_reverted") or [],
            "errors_baseline": errors_baseline,
            "errors_final": errors_final,
            "finding_comparison": comparison,
            "intermediate_checks": intermediate_checks,
            "note": "逐网络回退后按整盘 finding 身份门禁检查自家 engine 的非排除 errors；"
            "跳过时 status=not_run，不能声称通过。部分/未检查规则和官方原生 DRC 不在本门禁内。",
        },
        "note": "线宽按目标值重布；未达标需检查规则、几何与搜索预算，不能据此断言无路",
    }))  # fmt: skip
