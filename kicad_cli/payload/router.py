"""栅格 A* 布线器。kicad-layout skill 的执行核心，被 pcb_route.py 使用。

只用 F.Cu 与 B.Cu 两个信号层；内层保持完整的地/电源平面，不参与信号布线。

三条硬经验固化在本文件里，改动前先读 reference/pcbnew-api.md：

  1  起止层必须是该网络在该点已有铜的层。允许从任意层起步时，大量走线会
     起止在没有焊盘的那一层，既不报错也接不通——这是最容易误判为
     「布通率上不去」的坑。调用方负责传对 starts/goals（见 pcb_route.py 的
     layers_at）。

  2  「本网络自身铜可通行」的放宽只用于让引脚从密集阵列出脚，必须限制在
     端点附近，且绝不能用于过孔校验。一旦全窗口放宽，过孔就会被允许落在
     紧邻的异网焊盘上。

  3  膨胀必须用圆形。方形膨胀在对角线方向多封 1.41 倍，0.65 mm 引脚间距下
     会把合法的出脚通道判成不可通行。
"""

from __future__ import annotations

import heapq
import math

import numpy as np
import pcbnew
from pcbnew import VECTOR2I

GRID = 0.10  # mm，栅格步长（0.15 时方形膨胀会封死 0.65 mm 间距的出脚通道）
EDGE_KEEP = 0.6  # mm，铜到板边的保留
VIA_COST = 14.0  # 换层代价，折合栅格数
TURN_COST = 0.8  # 转向代价
CL_MAX = 0.30  # mm，全板最大网络类间距。膨胀统一按它算，
# 否则后布的窄间距网络会侵入先布的宽间距网络
# 量化余量不再进入间距计算。曾经为了补偿 0.05 mm 量级的 DRC 欠估，在 cl 上
# 统一加 0.10 mm，结果把合法的出脚车道判死了：0.65 mm 间距下邻脚焊盘边缘距
# 本脚中心线 0.450 mm，0.2 mm 线真实只需 0.400 mm，加了余量变成 0.500 mm，
# 装不下。欠估的根源是焊盘涂色向内取整，已改为向外取整（见 _paint_pad），
# 从源头消掉，不必再加全局余量。
QUANT = 0.10  # 仅保留给涂色之外的调用方，不再进入 cl
QUANT_DIAG = 0.08  # mm，只加在「全宽」校验上的量化余量。
# 路径走的是格心连线，斜向一步真实位置与被校验
# 的格子最多差半个格对角线（0.0707 mm），于是
# DRC 报出 0.2842/0.3000 这种差十几微米的欠量。
# 关键是**不加在缩颈与放宽通道上**：那里 0.1 mm
# 的余量就会把 0.65 mm 间距的出脚车道判死（第 13 条）。
# 粗线本来就需要空地，多这 0.08 mm 代价很小。
CL_TIGHT = 0.15  # mm，细间距封装庭院内部的间距，与 .kicad_dru 中的定向规则一致
QUANT_TIGHT = 0.04  # 放宽区余量。取 0.04 而非 0.05：0.05 时
# (0.10+0.15+0.05)/0.10 的浮点结果略大于 3，ceil 得 4，等于没改
RELAX_MARGIN = 0.0  # mm，放宽区在庭院框外的外扩量。必须为 0。
# 放宽区只能与 .kicad_dru 里那条规则的作用域
# 严格一致（条件是 insideCourtyard）。曾经外扩
# 0.8 mm 去「覆盖出脚走廊」，等于在没有规则撑腰
# 的那圈环里按 0.15 mm 布线，DRC 原样报回来：
# 「间距 0.3000 mm；实际 0.1500 mm」。
# 栅格模型放宽什么，必须由设计规则说了算。
TIGHT_MAX_PITCH = 0.80  # mm，引脚间距低于此值视为细间距封装，自动纳入放宽区
DIAG = math.sqrt(2.0)

F, B = 0, 1  # 本地层编号
KLAYER = {F: pcbnew.F_Cu, B: pcbnew.B_Cu}


def MM(v):
    return pcbnew.FromMM(v)


mm = pcbnew.ToMM


def _disown(o):
    """交给板子后放弃所有权，消除 SWIG 析构时打到 stdout 的噪声。"""
    try:
        o.thisown = False
    except Exception:  # noqa: BLE001
        pass
    return o


_DISK = {}


def _disk(r):
    """半径 r 个格子的圆形结构元偏移表。"""
    if r not in _DISK:
        _DISK[r] = [
            (dx, dy)
            for dx in range(-r, r + 1)
            for dy in range(-r, r + 1)
            if dx * dx + dy * dy <= r * r
        ]
    return _DISK[r]


def dilate(mask: np.ndarray, r: int) -> np.ndarray:
    """圆形膨胀，半径 r 个格子。

    方形膨胀在对角线方向会多封 1.41 倍，细间距封装的出脚通道本就只有
    零点几毫米，方形膨胀会把合法通道判成不可通行。
    """
    if r <= 0:
        return mask
    h, w = mask.shape
    p = np.pad(mask, ((r, r), (r, r)), mode="constant", constant_values=False)
    acc = np.zeros_like(mask)
    for dx, dy in _disk(r):
        acc |= p[r + dy : r + dy + h, r + dx : r + dx + w]
    return acc


_EXTENT = None  # 板框缓存
_STATIC = None  # 静态占用缓存：板边 + 禁布区 + 焊盘
_RELAX = None  # 细间距封装庭院缓存

# 这三项在布线期间都不变。缓存的动机不只是省时间：撕线的整板回滚会大量增删
# 对象，之后再遍历 GetFootprints() / GetBoardEdgesBoundingBox() 会拿到未解包的
# SwigPyObject。把静态部分算一次存成 numpy 模板，重建时直接复制，
# 就彻底不用再碰这些对象。


def reset_cache():
    """换板子时调用。同一进程里处理不同板必须先清缓存。"""
    global _EXTENT, _STATIC, _RELAX
    _EXTENT = _STATIC = _RELAX = None


class Router:
    def __init__(self, board, log=print):
        self.b = board
        self.log = log
        # 板框在布线期间不会变，但反复增删走线后 GetBoardEdgesBoundingBox()
        # 会返回未解包的 SwigPyObject（撕线的整板回滚必然触发）。
        # 算一次缓存起来，后续 Router 直接复用。
        global _EXTENT
        if _EXTENT is None:
            bb = board.GetBoardEdgesBoundingBox()
            _EXTENT = (mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom()))
        self.x0, self.y0, self.x1, self.y1 = _EXTENT
        self.W = int((self.x1 - self.x0) / GRID) + 1
        self.H = int((self.y1 - self.y0) / GRID) + 1
        # 每层的占用：0 空闲，>0 为网络码，-1 硬禁止
        self.stats = {"routed": 0, "failed": 0, "vias": 0, "tracks": 0}
        global _STATIC, _RELAX
        if _RELAX is None:
            # 自动判定细间距封装，不再硬编码位号；并把放宽区外扩到庭院之外，
            # 覆盖出脚走廊——否则走线刚出庭院就恢复到全板间距，等于没放宽。
            _RELAX = []
            for f in board.GetFootprints():
                pads = list(f.Pads())
                if len(pads) < 6:
                    continue
                ps = [(mm(p.GetPosition().x), mm(p.GetPosition().y)) for p in pads]
                mp = 9.9
                for i, a in enumerate(ps):
                    for c in ps[i + 1 :]:
                        d = math.hypot(a[0] - c[0], a[1] - c[1])
                        if 0.01 < d < mp:
                            mp = d
                if mp > TIGHT_MAX_PITCH:
                    continue
                cy = f.GetCourtyard(pcbnew.F_CrtYd)
                if not cy.OutlineCount():
                    cy = f.GetCourtyard(pcbnew.B_CrtYd)
                if cy.OutlineCount():
                    bb = cy.BBox()
                    m = RELAX_MARGIN
                    _RELAX.append(
                        (
                            mm(bb.GetLeft()) - m,
                            mm(bb.GetTop()) - m,
                            mm(bb.GetRight()) + m,
                            mm(bb.GetBottom()) + m,
                        )
                    )
        self.relax = _RELAX
        if _STATIC is None:
            self.occ = [np.zeros((self.H, self.W), dtype=np.int32) for _ in range(2)]
            self._paint_static()
            _STATIC = [a.copy() for a in self.occ]
        else:
            self.occ = [a.copy() for a in _STATIC]

    # ---- 坐标换算 ------------------------------------------------------------
    def gx(self, x):
        return int(round((x - self.x0) / GRID))

    def gy(self, y):
        return int(round((y - self.y0) / GRID))

    def wx(self, i):
        return self.x0 + i * GRID

    def wy(self, j):
        return self.y0 + j * GRID

    # ---- 静态障碍：板边、禁布区、焊盘 ---------------------------------------
    def _paint_static(self):
        e = int(math.ceil(EDGE_KEEP / GRID))
        for a in self.occ:
            a[:e, :] = -1
            a[-e:, :] = -1
            a[:, :e] = -1
            a[:, -e:] = -1
        nk = 0
        for z in self.b.Zones():
            if not z.GetIsRuleArea():
                continue
            bb = z.Outline().BBox()
            i0, i1 = self.gx(mm(bb.GetLeft())), self.gx(mm(bb.GetRight()))
            j0, j1 = self.gy(mm(bb.GetTop())), self.gy(mm(bb.GetBottom()))
            for a in self.occ:
                a[max(0, j0) : j1 + 1, max(0, i0) : i1 + 1] = -1
            nk += 1
        npad = 0
        for f in self.b.GetFootprints():
            for p in f.Pads():
                self._paint_pad(p)
                npad += 1
        self.log(
            "  栅格 %d x %d（%.2f mm），禁布区 %d 个，焊盘 %d 个" % (self.W, self.H, GRID, nk, npad)
        )

    def _paint_pad(self, pad):
        # 向外取整。gx/gy 用的是 round，最多会少涂半格（0.05 mm），
        # 那正是当初要用 QUANT 去补偿的欠估。向外取整后焊盘只会多占不会少占，
        # 误差消灭在源头，间距计算里就不必再加全局余量。
        nc = pad.GetNetCode() or 0
        bb = pad.GetBoundingBox()
        i0 = int(math.floor((mm(bb.GetLeft()) - self.x0) / GRID))
        i1 = int(math.ceil((mm(bb.GetRight()) - self.x0) / GRID))
        j0 = int(math.floor((mm(bb.GetTop()) - self.y0) / GRID))
        j1 = int(math.ceil((mm(bb.GetBottom()) - self.y0) / GRID))
        i0, j0 = max(0, i0), max(0, j0)
        i1, j1 = min(self.W - 1, i1), min(self.H - 1, j1)
        if i1 < i0 or j1 < j0:
            return
        for L in (F, B):
            if pad.IsOnLayer(KLAYER[L]):
                blk = self.occ[L][j0 : j1 + 1, i0 : i1 + 1]
                blk[blk == 0] = nc if nc else -1

    # ---- 动态障碍 ------------------------------------------------------------
    def paint_seg(self, L, x1, y1, x2, y2, w, nc):
        n = max(2, int(math.hypot(x2 - x1, y2 - y1) / GRID) + 1)
        r = int(math.ceil((w / 2.0) / GRID))  # 不再叠加 QUANT，见文件头说明
        for k in range(n + 1):
            t = k / n
            i, j = self.gx(x1 + (x2 - x1) * t), self.gy(y1 + (y2 - y1) * t)
            a = self.occ[L][max(0, j - r) : j + r + 1, max(0, i - r) : i + r + 1]
            a[a == 0] = nc

    def paint_via(self, x, y, d, nc):
        r = int(math.ceil((d / 2.0) / GRID))
        i, j = self.gx(x), self.gy(y)
        for L in (F, B):
            a = self.occ[L][max(0, j - r) : j + r + 1, max(0, i - r) : i + r + 1]
            a[a == 0] = nc

    # ---- 目标判定 ------------------------------------------------------------
    def blocked_mask(self, nc, halo_cells, win, relax=None):
        """窗口内对本网络不可通行的格子。

        relax 给出「允许踩本网络自身铜」的范围掩膜。这个放宽只用于让起止
        焊盘能从密集引脚阵列里出脚，必须限制在端点附近，且不能用于过孔校验：
        一旦全窗口放宽，过孔就会被允许落在紧邻的异网焊盘上。
        """
        j0, j1, i0, i1 = win
        out = []
        for L in (F, B):
            sub = self.occ[L][j0:j1, i0:i1]
            foreign = (sub != 0) & (sub != nc)
            d = dilate(foreign, halo_cells)
            if relax is not None:
                d &= ~((sub == nc) & relax)
            out.append(d)
        return out

    def route(
        self,
        nc,
        starts,
        goals,
        width,
        clearance,
        via_d,
        via_h,
        allow_via=True,
        margin=18.0,
        neck=None,
        neck_r=2.2,
    ):
        """starts/goals 为世界坐标点列表。

        neck 给出焊盘附近允许的缩颈线宽。粗线（电源、功放输出）无法穿出
        0.65 mm 间距的引脚阵列，必须在焊盘处收细、离开引脚区后再恢复。
        返回 (路径, 每格线宽) 或 None。
        """
        if neck is None:
            neck = width
        pts = starts + goals
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        i0 = max(0, self.gx(min(xs) - margin))
        i1 = min(self.W, self.gx(max(xs) + margin) + 1)
        j0 = max(0, self.gy(min(ys) - margin))
        j1 = min(self.H, self.gy(max(ys) + margin) + 1)
        win = (j0, j1, i0, i1)
        cl = max(clearance, CL_MAX)  # 不再叠加 QUANT：halo 从 5 格降到 4 格
        halo_f = int(math.ceil((width / 2.0 + cl + QUANT_DIAG) / GRID))
        halo_n = int(math.ceil((neck / 2.0 + cl) / GRID))
        halo_v = int(math.ceil((via_d / 2.0 + cl) / GRID))
        halo_t = int(math.ceil((neck / 2.0 + CL_TIGHT + QUANT_TIGHT) / GRID))
        hh = j1 - j0
        wwid = i1 - i0
        relax = np.zeros((hh, wwid), dtype=bool)
        rr_relax = int(math.ceil(1.6 / GRID))  # 端点附近 1.6 mm
        for x, y, _L in pts:
            ci, cj = self.gx(x) - i0, self.gy(y) - j0
            a0, a1 = max(0, cj - rr_relax), min(hh, cj + rr_relax + 1)
            b0, b1 = max(0, ci - rr_relax), min(wwid, ci + rr_relax + 1)
            relax[a0:a1, b0:b1] = True
        blk_f = self.blocked_mask(nc, halo_f, win, relax)
        blk_n = self.blocked_mask(nc, halo_n, win, relax) if halo_n != halo_f else blk_f
        # 换层处要按过孔直径校验，且不套用「踩自身铜」的放宽，
        # 否则过孔会落到紧邻的异网焊盘上。
        # 但细间距封装庭院内部的间距由 .kicad_dru 定向放宽到 CL_TIGHT，
        # 过孔校验也应当跟随，否则这些区域内根本换不了层，
        # 表现为起终点明明连通却怎么都布不通。
        halo_vt = int(math.ceil((via_d / 2.0 + CL_TIGHT + QUANT_TIGHT) / GRID))
        blk_v = self.blocked_mask(nc, halo_v, win, None)
        blk_vt = self.blocked_mask(nc, halo_vt, win, None) if halo_vt != halo_v else blk_v
        # 细间距封装庭院内部按 0.15 mm 间距，否则 0.65 mm 引脚间距下出不了脚
        blk_t = self.blocked_mask(nc, halo_t, win, relax) if halo_t != halo_n else blk_n
        hard = [self.occ[L][j0:j1, i0:i1] == -1 for L in (F, B)]
        h, w = blk_f[0].shape
        # 缩颈区：端点周围 neck_r 内
        nz = np.zeros((h, w), dtype=bool)
        rr = int(math.ceil(neck_r / GRID))
        for x, y, _L in pts:
            ci, cj = self.gx(x) - i0, self.gy(y) - j0
            a0, a1 = max(0, cj - rr), min(h, cj + rr + 1)
            b0, b1 = max(0, ci - rr), min(w, ci + rr + 1)
            nz[a0:a1, b0:b1] = True
        # 放宽区掩膜
        rz = np.zeros((h, w), dtype=bool)
        for L0, T0, R0, B0 in self.relax:
            a0 = max(0, self.gy(T0) - j0)
            a1 = min(h, self.gy(B0) - j0 + 1)
            b0 = max(0, self.gx(L0) - i0)
            b1 = min(w, self.gx(R0) - i0 + 1)
            if a1 > a0 and b1 > b0:
                rz[a0:a1, b0:b1] = True
        blk = [np.where(rz, blk_t[L], np.where(nz, blk_n[L], blk_f[L])) for L in (F, B)]
        blk_v = [np.where(rz, blk_vt[L], blk_v[L]) for L in (F, B)]
        # 缩颈掩膜：只在「全宽走不通、放宽后才走通」的格子上缩颈。
        # 判据必须用**未放宽**的全宽掩膜。端点附近为了出脚放宽了「本网络
        # 自身铜」，自己焊盘那一格在 blk_f 里也是空的，于是恰恰在最该缩颈的
        # 焊盘上判成不必缩颈，1.5 mm 的线直接铺在焊盘上压住邻脚，DRC 报
        # 「短路了两个不同网络的项目」。blk_f ⊆ blk_fh，用 blk_fh 只会更保守。
        blk_fh = self.blocked_mask(nc, halo_f, win, None)
        # 曾经按「落在 nz 或 rz 里就缩颈」提交，后果是一条 1.2 mm 的喇叭线
        # 只要路过某个细间距封装外扩框，整段被压到 0.2 mm——28 mm 里只有
        # 2.2 mm 达标。放宽区表达的是间距，不是线宽，两者必须解耦。
        # 外扩 2 格是因为宽度按段落，段跨两格，端点擦边时取保守值。
        need_neck = [dilate(blk_fh[L] & ~blk[L], 2) for L in (F, B)]

        def ok(L, i, j):
            return 0 <= i < w and 0 <= j < h and not blk[L][j, i] and not hard[L][j, i]

        S = set()
        for x, y, L in starts:
            i, j = self.gx(x) - i0, self.gy(y) - j0
            if 0 <= i < w and 0 <= j < h:
                S.add((L, i, j))
        G = set()
        for x, y, L in goals:
            i, j = self.gx(x) - i0, self.gy(y) - j0
            if 0 <= i < w and 0 <= j < h:
                G.add((L, i, j))
        if not S or not G:
            return None
        gset = G
        gi = sum(g[1] for g in G) / len(G)
        gj = sum(g[2] for g in G) / len(G)

        DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
        INF = float("inf")
        dist = {}
        prev = {}
        pq = []
        for s in S:
            dist[s] = 0.0
            heapq.heappush(pq, (abs(s[1] - gi) + abs(s[2] - gj), 0.0, s, None))
        found = None
        seen = 0
        LIMIT = 900000
        while pq:
            f, g, cur, pdir = heapq.heappop(pq)
            if cur in gset:
                found = cur
                break
            if g > dist.get(cur, INF):
                continue
            seen += 1
            if seen > LIMIT:
                break
            L, i, j = cur
            for d in DIRS:
                ni, nj = i + d[0], j + d[1]
                if not ok(L, ni, nj):
                    continue
                step = DIAG if d[0] and d[1] else 1.0
                c = g + step + (TURN_COST if pdir is not None and d != pdir else 0.0)
                nk = (L, ni, nj)
                if c < dist.get(nk, INF):
                    dist[nk] = c
                    prev[nk] = (cur, d)
                    heapq.heappush(pq, (c + abs(ni - gi) + abs(nj - gj), c, nk, d))
            if allow_via:
                oL = 1 - L
                if (
                    not blk_v[L][j, i]
                    and not blk_v[oL][j, i]
                    and not hard[L][j, i]
                    and not hard[oL][j, i]
                    and ok(oL, i, j)
                ):
                    nk = (oL, i, j)
                    c = g + VIA_COST
                    if c < dist.get(nk, INF):
                        dist[nk] = c
                        prev[nk] = (cur, None)
                        heapq.heappush(pq, (c + abs(i - gi) + abs(j - gj), c, nk, None))
        if found is None:
            return None
        path = [found]
        while path[-1] in prev:
            path.append(prev[path[-1]][0])
        path.reverse()
        out = []
        for L, i, j in path:
            # 落笔宽度必须跟校验用的掩膜一致：nz 是端点缩颈区，rz 是细间距
            # 封装的放宽区——两处都用 blk_n / blk_t（缩颈宽度的膨胀）校验过，
            # 若按全宽落笔就会真的压到邻铜上。早先只看 nz，放宽区内按全宽落笔，
            # DRC 一次冒出 26 条间距与短路。
            out.append((L, i + i0, j + j0, neck if need_neck[L][j, i] else width))
        return out

    # ---- 提交到板 ------------------------------------------------------------
    def commit(self, path, net, width, via_d, via_h, snap_a=None, snap_b=None):
        segs = []
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
        nadd = 0
        made = []
        created = []  # 本次新增的板上对象，供调用方精确撤销
        for kind, data in segs:
            if kind == "V":
                L, i, j, _w = data
                v = pcbnew.PCB_VIA(self.b)
                v.SetPosition(VECTOR2I(MM(self.wx(i)), MM(self.wy(j))))
                v.SetViaType(pcbnew.VIATYPE_THROUGH)
                v.SetDrill(MM(via_h))
                v.SetWidth(MM(via_d))
                v.SetNet(net)
                v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
                self.b.Add(v)
                _disown(v)
                created.append(v)
                self.paint_via(self.wx(i), self.wy(j), via_d, net.GetNetCode())
                self.stats["vias"] += 1
            else:
                if len(data) < 2:
                    continue
                segw = data[0][3]
                pts = self._simplify(data)
                for p, q in zip(pts, pts[1:], strict=False):
                    t = pcbnew.PCB_TRACK(self.b)
                    t.SetStart(VECTOR2I(MM(self.wx(p[1])), MM(self.wy(p[2]))))
                    t.SetEnd(VECTOR2I(MM(self.wx(q[1])), MM(self.wy(q[2]))))
                    t.SetLayer(KLAYER[p[0]])
                    t.SetWidth(MM(segw))
                    t.SetNet(net)
                    self.b.Add(t)
                    _disown(t)
                    created.append(t)
                    made.append((t, p[0], segw))
                    self.paint_seg(
                        p[0],
                        self.wx(p[1]),
                        self.wy(p[2]),
                        self.wx(q[1]),
                        self.wy(q[2]),
                        segw,
                        net.GetNetCode(),
                    )
                    self.stats["tracks"] += 1
                    nadd += 1
        # 栅格取整会让首末端点离焊盘中心差半格，留下悬空走线，所以要拉回来。
        #
        # 但**只能拉半格量级的偏差**。made[0] 是第一条走线；若路径一开始就换层
        # （先打过孔再走），made[0] 在另一层、起点在过孔处，把它硬拉到焊盘中心
        # 就凭空造出一条从焊盘斜穿出去、未经校验、且层都不对的线段——
        # 表现是压着邻脚焊盘短路，以及板上莫名其妙的 1 mm 级悬空走线。
        # 端点离目标超过一格半就别拉：那说明路径不是从这个焊盘直接起步的，
        # 连接由起点处的过孔负责，不需要也不能靠拉伸来补。
        SNAP_MAX = 1.5 * GRID
        if made and snap_a:
            t, L, w_ = made[0]
            if (
                math.hypot(mm(t.GetStart().x) - snap_a[0], mm(t.GetStart().y) - snap_a[1])
                <= SNAP_MAX
            ):
                t.SetStart(VECTOR2I(MM(snap_a[0]), MM(snap_a[1])))
                self.paint_seg(
                    L,
                    snap_a[0],
                    snap_a[1],
                    mm(t.GetEnd().x),
                    mm(t.GetEnd().y),
                    w_,
                    net.GetNetCode(),
                )
        if made and snap_b:
            t, L, w_ = made[-1]
            if math.hypot(mm(t.GetEnd().x) - snap_b[0], mm(t.GetEnd().y) - snap_b[1]) <= SNAP_MAX:
                t.SetEnd(VECTOR2I(MM(snap_b[0]), MM(snap_b[1])))
                self.paint_seg(
                    L,
                    mm(t.GetStart().x),
                    mm(t.GetStart().y),
                    snap_b[0],
                    snap_b[1],
                    w_,
                    net.GetNetCode(),
                )
        return created

    @staticmethod
    def _simplify(cells):
        """合并共线段，保留 45 度折线。"""
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
