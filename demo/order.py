"""决定走线按什么顺序画出来。

这件事决定了演示像「在画」还是像「在刷新」。

按文件顺序灌，线会在板子上乱跳——因为文件顺序是布线器求解时的顺序，跟空间无关。
观众看到的是随机位置不断闪现，认不出在干什么。

这里做两件事：
  1. **按网络分组**：一个网络画完再画下一个。观众看到的是「这条电源接过去了」，
     不是一堆碎片。顺带也方便口播——每组都能说出名字。
  2. **组内首尾相接**：一个网络的线段在文件里是乱序的，把它们按端点串成通路，
     画出来才是一笔一笔延伸，而不是同一条线上东一段西一段。

网络之间的顺序按载流从大到小：先粗后细，先电源后信号。这既好看，也跟工程上
「先保证供电通路」的次序一致。
"""

from __future__ import annotations

from collections import defaultdict


def chain(segments):
    """把一组线段串成尽量连续的画笔顺序。

    贪心：从任意一段起，每次找一个端点能接上当前笔尖的段接上去；接不上就重新起笔。
    不追求最优——这是给眼睛看的，不是求解旅行商。
    """
    if not segments:
        return []
    remaining = list(segments)
    out = []
    cur = remaining.pop(0)
    out.append(cur)
    tip = (cur.end.x, cur.end.y)
    while remaining:
        best, flip, dist = None, False, None
        for s in remaining:
            for pt, is_end in (((s.start.x, s.start.y), False), ((s.end.x, s.end.y), True)):
                d = (pt[0] - tip[0]) ** 2 + (pt[1] - tip[1]) ** 2
                if dist is None or d < dist:
                    dist, best, flip = d, s, is_end
        remaining.remove(best)
        out.append(best)
        tip = (best.start.x, best.start.y) if flip else (best.end.x, best.end.y)
    return out


def by_net(tracks, netclass_of=None):
    """(网名, 该网络的线段按画笔顺序) 的列表，粗线优先。"""
    groups = defaultdict(list)
    for t in tracks:
        name = t.net.name if t.net else ""
        groups[name].append(t)

    def weight(item):
        name, segs = item
        # 最宽的那一段代表这个网络的量级：电源粗、信号细
        widest = max(s.width for s in segs)
        return (-widest, -len(segs), name)

    return [(name, chain(segs)) for name, segs in sorted(groups.items(), key=weight)]
