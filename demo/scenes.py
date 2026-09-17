"""把成品板切成一幕一幕，供演示按顺序灌回去。

切片在 s-expr 层做，因为 `ParseAndCreateItemsFromString` 吃的就是这个格式，
而且板上 113 个封装的定义是**完整内联**的——焊盘、丝印、图形全在块里，
不依赖任何封装库。这是整个演示能成立的原因：IPC 没有任何 Library 命令，
但我们根本不需要库。

一处必须动手脚的地方：铺铜块里存着上次算好的 `filled_polygon`（24 个铜岛、
62562 个顶点）。原样灌回去，屏幕上铜皮会**瞬间出现**，那是搬运不是计算。
剥掉它只留 outline，再让 KiCad 自己 refill——那 62562 个顶点就是当场算的。
全片最有价值的一帧，靠的就是这几行删除。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import BOARD as SRC  # noqa: E402


def split_top_level(text: str) -> list[tuple[str, str]]:
    """(标签, 原文) 的顶层块列表。括号配平，不能用正则——铺铜里六万个坐标对。"""
    i = text.index("(", text.index("(kicad_pcb") + 1)
    depth, out, start = 0, [], 0
    while i < len(text):
        ch = text[i]
        if ch == '"':
            i += 1
            while i < len(text) and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if ch == "(":
            if depth == 0:
                start = i
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                blob = text[start : i + 1]
                m = re.match(r"\(\s*([A-Za-z_0-9]+)", blob)
                out.append((m.group(1) if m else "", blob))
            elif depth < 0:
                break
        i += 1
    return out


def strip_fill(zone_blob: str) -> str:
    """删掉铺铜里预存的填充结果，只留轮廓。

    留着的话铜皮是「贴」上去的；剥掉之后 refill_zones 会真的重算一遍。
    """
    out, i, removed = [], 0, 0
    while True:
        j = zone_blob.find("(filled_polygon", i)
        if j < 0:
            out.append(zone_blob[i:])
            break
        out.append(zone_blob[i:j])
        depth, k = 0, j
        while k < len(zone_blob):
            if zone_blob[k] == "(":
                depth += 1
            elif zone_blob[k] == ")":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        i = k + 1
        removed += 1
    return "".join(out), removed


def by_layer(blob: str) -> str:
    m = re.search(r'\(layer "([^"]+)"', blob)
    return m.group(1) if m else "?"


def net_of(blob: str) -> str:
    m = re.search(r"\(net (\d+)", blob)
    return m.group(1) if m else ""


def build(chunk: int = 60) -> list[dict]:
    """幕表。顺序是给人看的：先有轮廓，再有器件，再有铜，最后才是丝印。"""
    text = SRC.read_text(encoding="utf-8")
    blocks = split_top_level(text)
    grouped: dict[str, list[str]] = {}
    for tag, blob in blocks:
        grouped.setdefault(tag, []).append(blob)

    scenes: list[dict] = []

    def add(title: str, blobs: list[str], note: str, per: int | None = None) -> None:
        size = per or chunk
        for n, s in enumerate(range(0, len(blobs), size), 1):
            part = blobs[s : s + size]
            scenes.append(
                {
                    "title": title
                    if len(blobs) <= size
                    else f"{title} {n}/{-(-len(blobs) // size)}",
                    "note": note,
                    "count": len(part),
                    "blobs": part,
                }
            )

    add("板框", grouped.get("gr_rect", []), "Edge.Cuts 上的一个矩形，板子的边界", per=1)

    fps = grouped.get("footprint", [])
    add("放置器件", fps, f"{len(fps)} 个封装，几何完整内联，不依赖封装库", per=20)

    zones, total_fill = [], 0
    for z in grouped.get("zone", []):
        stripped, n = strip_fill(z)
        total_fill += n
        zones.append(stripped)
    add("铺铜轮廓", zones, f"只灌轮廓，预存的 {total_fill} 个填充多边形已剥掉", per=2)

    segs = grouped.get("segment", [])
    vias = grouped.get("via", [])
    # 走线按层分幕，镜头切层时观感更连贯
    for layer in ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu"):
        on = [s for s in segs if by_layer(s) == layer]
        if on:
            add(f"布线 {layer}", on, f"{len(on)} 段", per=chunk)
    add("过孔", vias, f"{len(vias)} 个", per=80)

    add("丝印", grouped.get("gr_text", []), "板级文字", per=4)

    return scenes


if __name__ == "__main__":
    sc = build()
    print(f"共 {len(sc)} 幕，{sum(s['count'] for s in sc)} 个对象\n")
    agg: dict[str, list[int]] = {}
    for s in sc:
        key = s["title"].split(" ")[0]
        agg.setdefault(key, [0, 0])
        agg[key][0] += 1
        agg[key][1] += s["count"]
    for k, (n, c) in agg.items():
        print(f"  {k:<10} {n:>3} 幕  {c:>4} 个对象")
    print()
    big = max(sc, key=lambda s: len(" ".join(s["blobs"])))
    print(f"最大一幕: {big['title']}  {len(' '.join(big['blobs'])):,} 字节")
