"""The grid router: A* on a 0.1 mm grid, on F.Cu and B.Cu.

Moved out of `payload/router.py`, which kept its grid in numpy arrays and
read the board through pcbnew. The router is the same; its grid is now rows
of bits, one Python integer per row of cells, and the board comes from the
file. Where a numpy slice would have reached past an edge of the grid -- a
negative stop counts from the far end -- `_span` reaches exactly as far.

Only F.Cu and B.Cu carry signals; inner layers are left whole for planes.
Three rules learned the hard way are kept here as they were:

1. A path starts and ends on a layer where its net already has copper at
   that point. Allowed to start anywhere, many paths start on a layer with
   no pad and connect to nothing, without any error to say so. The caller
   passes the right layers (`route.layers_at`).
2. A net may cross its own copper only near its end points, to get out of
   a dense row of pins -- and never when placing a via, or a via may land
   right next to another net's pad.
3. Obstacles grow by a disc, not a square: a square closes the diagonals
   1.41 times too far, and at a 0.65 mm pitch that shuts escape channels
   that are really open.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Callable
from typing import Any

GRID = 0.10  # mm: one cell. At 0.15 a square halo shut 0.65 mm pitch escapes.
EDGE_KEEP = 0.6  # mm: copper keeps this far inside the board's box
VIA_COST = 14.0  # a change of layer, in cells
TURN_COST = 0.8  # a turn
CL_MAX = 0.30  # mm: the largest net-class clearance on the board; every
# obstacle grows by it, or a narrow-clearance net routed later creeps into
# the clearance of a wide one routed earlier.
QUANT_DIAG = 0.08  # mm: added only where the full width is checked. A
# diagonal step's true position is up to half a cell's diagonal (0.0707 mm)
# from the cell checked; necks and relaxed channels do not get it, or a
# 0.65 mm pitch escape lane is shut.
CL_TIGHT = 0.15  # mm: the clearance inside a fine-pitch footprint's
# courtyard, as the board's custom rule allows there.
QUANT_TIGHT = 0.04  # 0.05 made (0.10 + 0.15 + 0.05) / 0.10 a hair over 3
RELAX_MARGIN = 0.0  # mm: the relaxed area is the courtyard and no more --
# the custom rule's condition is insideCourtyard, and a relaxed ring around
# it with no rule behind it came back from DRC as clearance errors.
TIGHT_MAX_PITCH = 0.80  # mm: a footprint finer than this is relaxed
DIAG = math.sqrt(2.0)
LIMIT = 900_000  # cells looked at before giving up on a path

F, B = 0, 1  # the two signal layers
LAYERS = {F: "F.Cu", B: "B.Cu"}
HARD = -1  # what no net may cross: the edge, a keep-out, a pad of no net


def MM(value: float) -> int:  # noqa: N802 - pcbnew's name for it
    """Millimetres to nanometres as pcbnew's FromMM: truncated."""
    return int(float(value) * 1_000_000.0)


def mm(value: int | float) -> float:
    """Nanometres to millimetres as pcbnew's ToMM."""
    return float(value) / 1_000_000.0


def _span(start: int, stop: int, size: int) -> tuple[int, int]:
    """The cells a numpy slice [start:stop] reaches on an axis of `size`."""
    first, last, _step = slice(start, stop).indices(size)
    return first, max(first, last)


_DISKS: dict[int, list[int]] = {}


def _disk(r: int) -> list[int]:
    """For each row offset -r..r, how far a disc of radius r reaches sideways."""
    if r not in _DISKS:
        _DISKS[r] = [math.isqrt(r * r - dy * dy) for dy in range(-r, r + 1)]
    return _DISKS[r]


def dilate(rows: list[int], r: int, width: int) -> list[int]:
    """Grow the set cells of a window by a disc of radius r cells.

    What lies outside the window does not grow into it: the window is padded
    with empty cells, as the payload padded its array.
    """
    if r <= 0:
        return rows
    full = (1 << width) - 1
    reach = _disk(r)
    spread: dict[int, list[int]] = {0: rows}
    wider = rows
    for k in range(1, max(reach) + 1):
        wider = [(w | (row >> k) | (row << k)) & full for w, row in zip(wider, rows, strict=True)]
        spread[k] = wider
    h = len(rows)
    out = []
    for j in range(h):
        acc = 0
        for dy, k in enumerate(reach, -r):
            jj = j + dy
            if 0 <= jj < h:
                acc |= spread[k][jj]
        out.append(acc)
    return out


_BITS = [bytes((b >> k) & 1 for k in range(8)) for b in range(256)]


def _cells(rows: list[int], width: int) -> bytes:
    """Rows of bits as one byte per cell, row after row: fast to index."""
    n = (width + 7) // 8
    return b"".join(b"".join(_BITS[c] for c in row.to_bytes(n, "little"))[:width] for row in rows)


class Copper:
    """What the router needs of the board's copper: pads and keep-outs fixed,
    tracks and vias in the order pcbnew holds them."""

    def __init__(
        self,
        edges: tuple[float, float, float, float],
        keepouts: list[tuple[float, float, float, float]],
        pads: list[dict[str, Any]],
        relax: list[tuple[float, float, float, float]],
        smd_drill_guard=None,
        via_hole_guard=None,
    ) -> None:
        self.edges = edges  # mm: left, top, right, bottom
        self.keepouts = keepouts  # mm boxes of every rule area
        self.pads = pads  # {"box": nm box, "code": net code, "front", "back"}
        self.relax = relax  # mm boxes of fine-pitch courtyards
        self.smd_drill_guard = smd_drill_guard
        self.via_hole_guard = via_hole_guard


class Router:
    def __init__(self, copper: Copper, log: Callable[[str], None] | None = None,
                 static: Router | None = None) -> None:  # fmt: skip
        self.copper = copper
        self.log = log or (lambda _s: None)
        self.x0, self.y0, self.x1, self.y1 = copper.edges
        self.W = int((self.x1 - self.x0) / GRID) + 1  # noqa: N806
        self.H = int((self.y1 - self.y0) / GRID) + 1  # noqa: N806
        self.stats = {"routed": 0, "failed": 0, "vias": 0, "tracks": 0}
        self.relax = copper.relax
        # Drilled material has no net-ownership exemption. Existing holes
        # are painted separately, and not yet committed MST paths reserve
        # their proposed holes before another path is searched.
        self.holes: list[tuple[int, int, int]] = []
        self.planned_holes: list[tuple[int, int, int]] = []
        self.last_search: dict = {}
        if static is not None:
            self.full = [list(rows) for rows in static.full]
            self.owned = [{c: dict(r) for c, r in layer.items()} for layer in static.owned]
        else:
            # Per layer: which cells anything holds, and which each net code holds.
            self.full = [[0] * self.H for _ in range(2)]
            self.owned: list[dict[int, dict[int, int]]] = [{}, {}]
            self._paint_static()

    # -- coordinates -----------------------------------------------------------

    def gx(self, x: float) -> int:
        return int(round((x - self.x0) / GRID))

    def gy(self, y: float) -> int:
        return int(round((y - self.y0) / GRID))

    def wx(self, i: int) -> float:
        return self.x0 + i * GRID

    def wy(self, j: int) -> float:
        return self.y0 + j * GRID

    # -- painting -----------------------------------------------------------------

    def _fill(self, layer: int, rows: tuple[int, int], cols: tuple[int, int], code: int) -> None:
        """Give the empty cells of a block to a net code; held cells stay."""
        (r0, r1), (c0, c1) = rows, cols
        if not code or r1 <= r0 or c1 <= c0:
            return
        mask = ((1 << (c1 - c0)) - 1) << c0
        full = self.full[layer]
        held = self.owned[layer].setdefault(code, {})
        for j in range(r0, r1):
            empty = mask & ~full[j]
            if empty:
                full[j] |= empty
                held[j] = held.get(j, 0) | empty

    def _set(self, layer: int, rows: tuple[int, int], cols: tuple[int, int], code: int) -> None:
        """Give every cell of a block to a code, whoever held it."""
        (r0, r1), (c0, c1) = rows, cols
        if r1 <= r0 or c1 <= c0:
            return
        mask = ((1 << (c1 - c0)) - 1) << c0
        for other, held in self.owned[layer].items():
            if other == code:
                continue
            for j in range(r0, r1):
                if held.get(j, 0) & mask:
                    held[j] &= ~mask
        mine = self.owned[layer].setdefault(code, {})
        full = self.full[layer]
        for j in range(r0, r1):
            if code:
                full[j] |= mask
                mine[j] = mine.get(j, 0) | mask
            else:
                full[j] &= ~mask

    def _paint_static(self) -> None:
        e = int(math.ceil(EDGE_KEEP / GRID))
        everything = (0, self.W)
        for layer in (F, B):
            self._set(layer, _span(0, e, self.H), everything, HARD)
            self._set(layer, _span(-e, self.H, self.H), everything, HARD)
            self._set(layer, (0, self.H), _span(0, e, self.W), HARD)
            self._set(layer, (0, self.H), _span(-e, self.W, self.W), HARD)
        for left, top, right, bottom in self.copper.keepouts:
            i0, i1 = self.gx(left), self.gx(right)
            j0, j1 = self.gy(top), self.gy(bottom)
            rows = _span(max(0, j0), j1 + 1, self.H)
            cols = _span(max(0, i0), i1 + 1, self.W)
            for layer in (F, B):
                self._set(layer, rows, cols, HARD)
        for pad in self.copper.pads:
            self._paint_pad(pad)
        self.log(
            "  栅格 %d x %d（%.2f mm），禁布区 %d 个，焊盘 %d 个"  # noqa: UP031
            % (self.W, self.H, GRID, len(self.copper.keepouts), len(self.copper.pads))
        )

    def _paint_pad(self, pad: dict[str, Any]) -> None:
        # Outward: a pad covers every cell it touches, so that no clearance
        # is lost to rounding and none has to be added back elsewhere.
        left, top, right, bottom = pad["box"]
        i0 = int(math.floor((mm(left) - self.x0) / GRID))
        i1 = int(math.ceil((mm(right) - self.x0) / GRID))
        j0 = int(math.floor((mm(top) - self.y0) / GRID))
        j1 = int(math.ceil((mm(bottom) - self.y0) / GRID))
        i0, j0 = max(0, i0), max(0, j0)
        i1, j1 = min(self.W - 1, i1), min(self.H - 1, j1)
        if i1 < i0 or j1 < j0:
            return
        code = pad["code"] or HARD
        for layer, on in ((F, pad["front"]), (B, pad["back"])):
            if on:
                self._fill(layer, (j0, j1 + 1), (i0, i1 + 1), code)

    def paint_seg(self, layer: int, x1: float, y1: float, x2: float, y2: float,
                  w: float, code: int) -> None:  # fmt: skip
        if not code:
            return
        n = max(2, int(math.hypot(x2 - x1, y2 - y1) / GRID) + 1)
        r = int(math.ceil((w / 2.0) / GRID))
        last = None
        for k in range(n + 1):
            t = k / n
            i, j = self.gx(x1 + (x2 - x1) * t), self.gy(y1 + (y2 - y1) * t)
            if (i, j) == last:
                continue
            last = (i, j)
            rows = _span(max(0, j - r), j + r + 1, self.H)
            cols = _span(max(0, i - r), i + r + 1, self.W)
            self._fill(layer, rows, cols, code)

    def paint_via(
        self, x: float, y: float, d: float, code: int, *, drill_nm=None, position_nm=None
    ) -> None:
        if self.copper.via_hole_guard is not None:
            from .via_hole_guard import _hole  # noqa: PLC0415

            self.holes.append(_hole(*(position_nm or (MM(x), MM(y))), drill_nm))
        if not code:
            return
        r = int(math.ceil((d / 2.0) / GRID))
        i, j = self.gx(x), self.gy(y)
        rows = _span(max(0, j - r), j + r + 1, self.H)
        cols = _span(max(0, i - r), i + r + 1, self.W)
        for layer in (F, B):
            self._fill(layer, rows, cols, code)

    def path_holes(self, path, drill_nm):
        return [
            (MM(self.wx(a[1])), MM(self.wy(a[2])), drill_nm)
            for a, b in zip(path, path[1:], strict=False)
            if a[0] != b[0]
        ]

    def reserve_path_holes(self, path, drill_nm):
        guard = self.copper.via_hole_guard
        if guard is not None:
            reserved = list(self.holes) + self.planned_holes
            for hole in self.path_holes(path, drill_nm):
                guard.require_clearance(*hole, reserved)
                reserved.append(hole)
            self.planned_holes = reserved[len(self.holes) :]

    # -- reading the grid -------------------------------------------------------

    def holds(self, layer: int, i: int, j: int, code: int) -> bool:
        """Whether a net code holds a cell."""
        return bool((self.owned[layer].get(code, {}).get(j, 0) >> i) & 1)

    def foreign(self, layer: int, code: int, rows: tuple[int, int], cols: tuple[int, int]) -> bool:
        """Whether anything but the code holds a cell of the block."""
        (r0, r1), (c0, c1) = rows, cols
        mask = ((1 << (c1 - c0)) - 1) << c0
        held = self.owned[layer].get(code, {})
        full = self.full[layer]
        return any(full[j] & ~held.get(j, 0) & mask for j in range(r0, r1))

    def _window(self, rows: list[int], win: tuple[int, int, int, int]) -> list[int]:
        j0, j1, i0, i1 = win
        mask = (1 << (i1 - i0)) - 1
        return [(rows[j] >> i0) & mask for j in range(j0, j1)]

    def _held(self, layer: int, code: int, win: tuple[int, int, int, int]) -> list[int]:
        j0, j1, i0, i1 = win
        held = self.owned[layer].get(code, {})
        mask = (1 << (i1 - i0)) - 1
        return [(held.get(j, 0) >> i0) & mask for j in range(j0, j1)]

    def blocked_mask(self, code: int, halo: int, win: tuple[int, int, int, int],
                     relax: list[int] | None = None) -> list[list[int]]:  # fmt: skip
        """The cells of the window this net may not enter, per layer.

        `relax` is where the net may cross its own copper: near its end
        points only, to get out of a dense row of pins -- never for a via,
        or a via may land beside another net's pad.
        """
        width = win[3] - win[2]
        out = []
        for layer in (F, B):
            full = self._window(self.full[layer], win)
            own = self._held(layer, code, win)
            grown = dilate([f & ~o for f, o in zip(full, own, strict=True)], halo, width)
            if relax is not None:
                grown = [g & ~(o & r) for g, o, r in zip(grown, own, relax, strict=True)]
            out.append(grown)
        return out

    # -- one path ------------------------------------------------------------------

    def route(self, code: int, starts: list[tuple[float, float, int]],
              goals: list[tuple[float, float, int]], width: float, clearance: float,
              via_d: float, via_h: float, allow_via: bool = True, margin: float = 18.0,
              neck: float | None = None, neck_r: float = 2.2):  # fmt: skip
        """A path from any start to any goal, (layer, i, j, width) per cell,
        or None.

        `neck` is the narrower width allowed near the pads: a wide track
        cannot leave a 0.65 mm pitch row of pins at its full width, so it
        narrows at the pad and widens again once clear.
        """
        if neck is None:
            neck = width
        self.last_search = {
            "status": "started",
            "net_code": code,
            "starts": starts,
            "goals": goals,
            "layers": ["F.Cu", "B.Cu"],
            "grid_mm": GRID,
            "budget_cells": LIMIT,
            "expanded_cells": 0,
            "width_mm": width,
            "clearance_mm": clearance,
            "allow_via": allow_via,
            "margin_mm": margin,
            "scope": "bounded heuristic grid; failure is not physical impossibility",
        }
        if (
            not starts
            or not goals
            or any(
                p[2] not in (F, B) or not all(math.isfinite(v) for v in p[:2])
                for p in starts + goals
            )
        ):
            self.last_search["status"] = "invalid_endpoint"
            return None
        pts = starts + goals
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        i0 = max(0, self.gx(min(xs) - margin))
        i1 = min(self.W, self.gx(max(xs) + margin) + 1)
        j0 = max(0, self.gy(min(ys) - margin))
        j1 = min(self.H, self.gy(max(ys) + margin) + 1)
        h, w = j1 - j0, i1 - i0
        self.last_search["window_cells"] = [i0, j0, i1, j1]
        if h <= 0 or w <= 0:
            self.last_search["status"] = "invalid_endpoint"
            return None
        win = (j0, j1, i0, i1)
        everything = (1 << w) - 1
        # Separate from ownership masks: even same-net SMD copper is not a
        # location into which an ordinary through-via may drill.  These are
        # actual integer-nm positions used by commit(), not display decimals.
        drill_forbidden = [0] * h
        if allow_via and self.copper.smd_drill_guard is not None:
            rows = self.copper.smd_drill_guard.rows(
                tuple(MM(self.wx(i)) for i in range(self.W)),
                tuple(MM(self.wy(j)) for j in range(self.H)),
                MM(via_h),
            )
            drill_forbidden = self._window(rows, win)
        hole_guard = self.copper.via_hole_guard
        if allow_via and hole_guard is not None:
            rows = hole_guard.rows(
                tuple(MM(self.wx(i)) for i in range(self.W)),
                tuple(MM(self.wy(j)) for j in range(self.H)),
                MM(via_h),
                self.holes + self.planned_holes,
            )
            drill_forbidden = [
                a | b for a, b in zip(drill_forbidden, self._window(rows, win), strict=True)
            ]
        cl = max(clearance, CL_MAX)
        halo_f = int(math.ceil((width / 2.0 + cl + QUANT_DIAG) / GRID))
        halo_n = int(math.ceil((neck / 2.0 + cl) / GRID))
        halo_v = int(math.ceil((via_d / 2.0 + cl) / GRID))
        halo_t = int(math.ceil((neck / 2.0 + CL_TIGHT + QUANT_TIGHT) / GRID))

        def block(mask: list[int], rows: tuple[int, int], cols: tuple[int, int]) -> None:
            (a0, a1), (b0, b1) = rows, cols
            if a1 <= a0 or b1 <= b0:
                return
            bits = ((1 << (b1 - b0)) - 1) << b0
            for a in range(a0, a1):
                mask[a] |= bits

        relax = [0] * h
        near = int(math.ceil(1.6 / GRID))  # 1.6 mm round each end point
        for x, y, _layer in pts:
            ci, cj = self.gx(x) - i0, self.gy(y) - j0
            block(relax, _span(max(0, cj - near), min(h, cj + near + 1), h),
                  _span(max(0, ci - near), min(w, ci + near + 1), w))  # fmt: skip
        blk_f = self.blocked_mask(code, halo_f, win, relax)
        blk_n = self.blocked_mask(code, halo_n, win, relax) if halo_n != halo_f else blk_f
        # A via is checked at its own diameter and never crosses the net's
        # own copper -- but inside a fine-pitch courtyard at the tight
        # clearance, or no via would fit there at all.
        halo_vt = int(math.ceil((via_d / 2.0 + CL_TIGHT + QUANT_TIGHT) / GRID))
        blk_v = self.blocked_mask(code, halo_v, win, None)
        blk_vt = self.blocked_mask(code, halo_vt, win, None) if halo_vt != halo_v else blk_v
        blk_t = self.blocked_mask(code, halo_t, win, relax) if halo_t != halo_n else blk_n
        hard = [self._held(layer, HARD, win) for layer in (F, B)]
        # Necks: within neck_r of an end point.
        nz = [0] * h
        rr = int(math.ceil(neck_r / GRID))
        for x, y, _layer in pts:
            ci, cj = self.gx(x) - i0, self.gy(y) - j0
            block(nz, _span(max(0, cj - rr), min(h, cj + rr + 1), h),
                  _span(max(0, ci - rr), min(w, ci + rr + 1), w))  # fmt: skip
        # The relaxed courtyards.
        rz = [0] * h
        for left, top, right, bottom in self.relax:
            a0 = max(0, self.gy(top) - j0)
            a1 = min(h, self.gy(bottom) - j0 + 1)
            b0 = max(0, self.gx(left) - i0)
            b1 = min(w, self.gx(right) - i0 + 1)
            if a1 > a0 and b1 > b0:
                block(rz, (a0, a1), (b0, b1))
        blk = []
        for layer in (F, B):
            rows = []
            for j in range(h):
                z, n = rz[j], nz[j]
                rest = everything & ~z
                rows.append(
                    (z & blk_t[layer][j])
                    | (rest & ((n & blk_n[layer][j]) | (everything & ~n & blk_f[layer][j])))
                )
            blk.append(rows)
        blk_v = [
            [(rz[j] & blk_vt[layer][j]) | (everything & ~rz[j] & blk_v[layer][j]) for j in range(h)]
            for layer in (F, B)
        ]
        # Where to neck: only where the full width does not fit and the
        # narrower check lets the path through -- judged against the full
        # width's mask without the relaxation near the end points, or the
        # pad itself (the net's own copper, so free in the relaxed mask)
        # reads as needing no neck, and a 1.5 mm track is laid across it.
        # Grown by two cells: a width is decided per segment and a segment
        # spans two cells.
        blk_fh = self.blocked_mask(code, halo_f, win, None)
        need_neck = []
        for layer in (F, B):
            wider = [a & ~b for a, b in zip(blk_fh[layer], blk[layer], strict=True)]
            need_neck.append(_cells(dilate(wider, 2, w), w))
        free = [
            _cells([everything & ~(blk[layer][j] | hard[layer][j]) for j in range(h)], w)
            for layer in (F, B)
        ]
        via_ok = []
        for layer in (F, B):
            other = 1 - layer
            via_ok.append(_cells([
                everything & ~(blk_v[layer][j] | blk_v[other][j] | hard[layer][j] | hard[other][j]
                               | blk[other][j] | drill_forbidden[j])
                for j in range(h)
            ], w))  # fmt: skip

        def ok(layer: int, i: int, j: int) -> bool:
            return 0 <= i < w and 0 <= j < h and free[layer][j * w + i] == 1

        S = set()  # noqa: N806
        for x, y, layer in starts:
            i, j = self.gx(x) - i0, self.gy(y) - j0
            if 0 <= i < w and 0 <= j < h:
                S.add((layer, i, j))
        G = set()  # noqa: N806
        for x, y, layer in goals:
            i, j = self.gx(x) - i0, self.gy(y) - j0
            if 0 <= i < w and 0 <= j < h:
                G.add((layer, i, j))
        if not S or not G:
            self.last_search["status"] = "invalid_endpoint"
            return None
        gi = sum(g[1] for g in G) / len(G)
        gj = sum(g[2] for g in G) / len(G)

        dirs = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
        inf = float("inf")
        dist: dict[tuple[int, int, int], float] = {}
        prev: dict[tuple[int, int, int], tuple] = {}
        via_history: dict[tuple[int, int, int], tuple] = {}
        pq: list = []
        for s in S:
            dist[s] = 0.0
            via_history[s] = ()
            heapq.heappush(pq, (abs(s[1] - gi) + abs(s[2] - gj), 0.0, s, None))
        found = None
        seen = 0
        while pq:
            _f, g, cur, pdir = heapq.heappop(pq)
            if cur in G:
                found = cur
                break
            if g > dist.get(cur, inf):
                continue
            seen += 1
            if seen > LIMIT:
                break
            layer, i, j = cur
            for d in dirs:
                ni, nj = i + d[0], j + d[1]
                if not ok(layer, ni, nj):
                    continue
                step = DIAG if d[0] and d[1] else 1.0
                c = g + step + (TURN_COST if pdir is not None and d != pdir else 0.0)
                nk = (layer, ni, nj)
                if c < dist.get(nk, inf):
                    dist[nk] = c
                    prev[nk] = (cur, d)
                    via_history[nk] = via_history[cur]
                    heapq.heappush(pq, (c + abs(ni - gi) + abs(nj - gj), c, nk, d))
            if allow_via and via_ok[layer][j * w + i] == 1:
                hole = (MM(self.wx(i + i0)), MM(self.wy(j + j0)), MM(via_h))
                if hole_guard is not None and not hole_guard.allows(*hole, via_history[cur]):
                    continue
                nk = (1 - layer, i, j)
                c = g + VIA_COST
                if c < dist.get(nk, inf):
                    dist[nk] = c
                    prev[nk] = (cur, None)
                    via_history[nk] = (*via_history[cur], hole)
                    heapq.heappush(pq, (c + abs(i - gi) + abs(j - gj), c, nk, None))
        if found is None:
            self.last_search.update(
                status="budget_exhausted" if seen > LIMIT else "search_window_exhausted",
                expanded_cells=seen,
            )
            return None
        path = [found]
        while path[-1] in prev:
            path.append(prev[path[-1]][0])
        path.reverse()
        # The width laid is the width checked: near the ends and in relaxed
        # courtyards the path was checked at the neck, so it is laid at it.
        result = [
            (layer, i + i0, j + j0, neck if need_neck[layer][j * w + i] == 1 else width)
            for layer, i, j in path
        ]
        # Search keeps one heuristic predecessor per grid state. Recheck
        # the reconstructed path itself, so predecessor improvements can
        # never turn a history hint into an unchecked pair of drill disks.
        if hole_guard is not None:
            holes = self.holes + self.planned_holes
            for hole in self.path_holes(result, MM(via_h)):
                if not hole_guard.allows(*hole, holes):
                    self.last_search.update(
                        status="rule_constraint",
                        expanded_cells=seen,
                        rule="reconstructed via-hole spacing",
                    )
                    return None
                holes = [*holes, hole]
        self.last_search.update(status="path_found", expanded_cells=seen)
        return result

    @staticmethod
    def simplify(cells: list) -> list:
        """Merge collinear steps; 45 degree bends stay."""
        if len(cells) < 2:
            return cells
        out = [cells[0]]
        pd = None
        for a, bn in zip(cells, cells[1:], strict=False):
            d = (bn[1] - a[1], bn[2] - a[2])
            if pd is not None and d != pd:
                out.append(a)
            pd = d
        out.append(cells[-1])
        return out
