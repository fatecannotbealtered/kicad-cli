"""按电路关系自动摆器件，摆完报出线长变化。

`board from-netlist` 摆出来的是按位号排的网格——「有个确定位置」，不是布局。
它不知道哪些器件是一伙的，于是去耦电容可能落在芯片对角。走线长度是后面
一切的成本：阻抗、串扰、面积、能不能布通，全压在这上面。

做法是力导向：同一网络上的焊盘互相吸引，庭院重叠就推开，反复迭代。
这不是商业布局器——没有旋转、没有镜像、没有分区约束——但它知道哪些器件
连在一起，这是网格完全不知道的事情。

判定用 HPWL（各网络包围盒半周长之和），布局领域的标准指标。算法结束后
如果 HPWL 没变好就一个器件都不动、如实报出来：一个会把手工布局改坏还
不吭声的「自动布局」，比没有更糟。

    <kicad-python> pcb_autoplace.py --board B.kicad_pcb [--confirm ct_xxx]
        [--iterations 200] [--clearance 0.5] [--keep REF,REF]
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kicad_lib as K  # noqa: E402

# 扇出超过这个数的网络不参与吸引。电源和地连到几乎所有器件，把它们算进
# 引力等于让所有器件往同一个点挤，布局会塌成一团。工业布局器同样特殊
# 对待它们——它们靠敷铜解决，不靠走线。
FANOUT_CAP = 8

# 迭代次数与步长。步长从大到小退火：先让器件大致归位，再细调。
DEFAULT_ITERATIONS = 200
STEP_START = 0.45
STEP_END = 0.03

# 每轮解重叠的最大推挤次数。超过还解不开说明板框放不下，不是没推够。
LEGALIZE_PASSES = 24


def _footprint_geometry(footprint, pcbnew, mm):
    """庭院相对封装原点的偏移与半宽半高。

    器件搬动时庭院跟着动，形状不变，所以只在开头算一次，之后纯数值平移。
    这让迭代不必反复进 SWIG。
    """
    box = None
    for layer in (pcbnew.F_CrtYd, pcbnew.B_CrtYd):
        courtyard = footprint.GetCourtyard(layer)
        if courtyard.OutlineCount():
            box = courtyard.BBox()
            break
    if box is None:
        box = footprint.GetBoundingBox(False, False)
    origin = footprint.GetPosition()
    return {
        "dx": mm(box.GetLeft() + box.GetRight()) / 2.0 - mm(origin.x),
        "dy": mm(box.GetTop() + box.GetBottom()) / 2.0 - mm(origin.y),
        "hw": max(mm(box.GetRight() - box.GetLeft()) / 2.0, 0.05),
        "hh": max(mm(box.GetBottom() - box.GetTop()) / 2.0, 0.05),
    }


def collect(board, pcbnew, mm, keep):
    """读出摆放所需的一切：位置、庭院、焊盘偏移、网络。

    锁定的器件和 --keep 点名的器件记为固定：它们参与引力和避让，但不动。
    把连接器锁在板边是人表达布局意图的方式，自动布局不该推翻它。
    """
    parts, nets = {}, {}
    for footprint in K.footprints_of(board):
        ref = footprint.GetReference()
        if not ref:
            continue
        position = footprint.GetPosition()
        geometry = _footprint_geometry(footprint, pcbnew, mm)
        parts[ref] = {
            "ref": ref,
            "fp": footprint,
            "x": mm(position.x),
            "y": mm(position.y),
            "fixed": bool(footprint.IsLocked()) or ref in keep,
            **geometry,
        }
        for pad in footprint.Pads():
            name = pad.GetNetname()
            if not name:
                continue
            pad_position = pad.GetPosition()
            nets.setdefault(name, []).append(
                {
                    "ref": ref,
                    "ox": mm(pad_position.x) - parts[ref]["x"],
                    "oy": mm(pad_position.y) - parts[ref]["y"],
                }
            )
    # 只连到一个器件的网络（含只有一个焊盘的）对布局没有约束力。
    nets = {name: nodes for name, nodes in nets.items() if len({n["ref"] for n in nodes}) > 1}
    return parts, nets


def hpwl(parts, nets):
    """各网络包围盒的半周长之和，单位 mm。

    布局质量的标准代理指标：它是任何布线方案长度的下界，且不需要真的布线
    就能算——正因为如此，它才能用来比较两个还没走线的布局。
    """
    total = 0.0
    for nodes in nets.values():
        xs = [parts[n["ref"]]["x"] + n["ox"] for n in nodes]
        ys = [parts[n["ref"]]["y"] + n["oy"] for n in nodes]
        total += (max(xs) - min(xs)) + (max(ys) - min(ys))
    return total


def _attract(parts, nets, step):
    """把每个器件往它所连网络的中心拉一点。"""
    force = {ref: [0.0, 0.0] for ref in parts}
    weight = {ref: 0.0 for ref in parts}
    for nodes in nets.values():
        fanout = len({n["ref"] for n in nodes})
        if fanout > FANOUT_CAP:
            continue
        # 少引脚网络对布局的指示更强：两点网络说「这两个必须挨着」，
        # 八点网络只说「这些大致在一起」。按 1/(n-1) 加权正是这个意思。
        share = 1.0 / (fanout - 1)
        cx = sum(parts[n["ref"]]["x"] + n["ox"] for n in nodes) / len(nodes)
        cy = sum(parts[n["ref"]]["y"] + n["oy"] for n in nodes) / len(nodes)
        for node in nodes:
            part = parts[node["ref"]]
            force[node["ref"]][0] += share * (cx - (part["x"] + node["ox"]))
            force[node["ref"]][1] += share * (cy - (part["y"] + node["oy"]))
            weight[node["ref"]] += share
    for ref, part in parts.items():
        if part["fixed"] or weight[ref] <= 0.0:
            continue
        part["x"] += step * force[ref][0] / weight[ref]
        part["y"] += step * force[ref][1] / weight[ref]


def _legalize(order, parts, clearance, bounds):
    """推开重叠的庭院，并留在板框内。

    沿重叠较小的那个轴推——那是把两个器件分开代价最低的方向。
    推完分没分开不在这里回答：inspect 按最终坐标算一遍，那是唯一的判据。
    这里再返回一个「大概分开了」的布尔值，就成了同一件事的第二个说法。
    """
    for _ in range(LEGALIZE_PASSES):
        moved = False
        for index, left_ref in enumerate(order):
            left = parts[left_ref]
            for right_ref in order[index + 1 :]:
                right = parts[right_ref]
                if left["fixed"] and right["fixed"]:
                    continue
                dx = (right["x"] + right["dx"]) - (left["x"] + left["dx"])
                dy = (right["y"] + right["dy"]) - (left["y"] + left["dy"])
                gap_x = (left["hw"] + right["hw"] + clearance) - abs(dx)
                gap_y = (left["hh"] + right["hh"] + clearance) - abs(dy)
                if gap_x <= 0 or gap_y <= 0:
                    continue
                moved = True
                if gap_x <= gap_y:
                    push = gap_x if dx >= 0 else -gap_x
                    axis = "x"
                else:
                    push = gap_y if dy >= 0 else -gap_y
                    axis = "y"
                # 两个都能动就各让一半；一个锁着就让另一个走满。
                if left["fixed"]:
                    right[axis] += push
                elif right["fixed"]:
                    left[axis] -= push
                else:
                    right[axis] += push / 2.0
                    left[axis] -= push / 2.0
        _clamp(parts, bounds)
        if not moved:
            return


def _clamp(parts, bounds):
    """把器件关在板框里。板框是制造边界，布局不能越过它。"""
    if bounds is None:
        return
    left, top, right, bottom = bounds
    for part in parts.values():
        if part["fixed"]:
            continue
        low_x, high_x = left + part["hw"] - part["dx"], right - part["hw"] - part["dx"]
        low_y, high_y = top + part["hh"] - part["dy"], bottom - part["hh"] - part["dy"]
        if low_x <= high_x:
            part["x"] = min(max(part["x"], low_x), high_x)
        if low_y <= high_y:
            part["y"] = min(max(part["y"], low_y), high_y)


def severities(path):
    """按严重度数一遍 DRC。裁判是 KiCad 自己的 drc，不是本脚本。

    警告也要数。把器件挤紧正是本命令的目的，代价通常落在丝印上——位号
    文字互相压、被阻焊切掉。那不是 error，板子照样能做，但它是本命令带来的
    变化，不报出来就成了「悄悄变差」：board route 的线宽回归就是这么被
    漏掉的，这里不重犯。
    """
    report = K.run_drc(path)
    counts = {}
    for violation in (report or {}).get("violations", []):
        counts[violation["severity"]] = counts.get(violation["severity"], 0) + 1
    return counts


def board_bounds(board, pcbnew, mm):
    """Edge.Cuts 的包围盒，没有板框就返回 None（不约束）。"""
    box = board.GetBoardEdgesBoundingBox()
    if not box.GetWidth() or not box.GetHeight():
        return None
    return (mm(box.GetLeft()), mm(box.GetTop()), mm(box.GetRight()), mm(box.GetBottom()))


def edge_clearance(board, pcbnew, mm):
    """铜到板边的最小间距，读板子自己的设计规则。

    贴着板框摆是不行的，而「贴多近算行」不由这个脚本定：庭院只比焊盘大
    0.25mm 左右，把庭院压在板框上，焊盘离板边就只剩 0.25mm——第一次跑
    出来的两条 error 正是这个。板子自己写着 m_CopperEdgeClearance，
    照着读就不必猜。
    """
    try:
        return mm(board.GetDesignSettings().m_CopperEdgeClearance)
    except AttributeError:  # 老版本没有这个字段
        return 0.5


def usable_area(bounds, edge):
    """板框内实际可以摆器件的范围：外框往里收一个铜-板边间距。"""
    if bounds is None:
        return None
    left, top, right, bottom = bounds
    if right - left <= 2 * edge or bottom - top <= 2 * edge:
        return bounds  # 板子小到收不动，交给 DRC 去报，别在这里把坐标算反
    return (left + edge, top + edge, right - edge, bottom - edge)


def run(parts, nets, iterations, clearance, bounds, edge=0.0):
    area = usable_area(bounds, edge)
    order = sorted(parts)  # 固定顺序：同样的板子跑两次必须得到同样的布局
    for step_index in range(iterations):
        ratio = step_index / max(1, iterations - 1)
        _attract(parts, nets, STEP_START + (STEP_END - STEP_START) * ratio)
        _legalize(order, parts, clearance, area)


def inspect(parts, bounds):
    """当前摆法有哪些庭院真的重叠、最小间距多少、哪些器件出了板框。

    描述的是「板上现在是什么样」，所以两个分支——改好了的和没改的——
    用同一个函数算，报出来的是留在盘上的那块板的事实。

    这里不带 --clearance。clearance 是解重叠时争取的余量，不是判据：
    按 0.5mm 的余量推开，推到刚好 0.5mm，再拿 0.5mm 去判「撞了没有」，
    会把每一个推得恰到好处的器件对都报成碰撞。重叠就是重叠——和
    `board move` 的 courtyard_clash 同一个意思，否则同名不同义更糟。
    余量够不够另说，那是 min_courtyard_gap_mm 要回答的。
    """
    order = sorted(parts)
    clash = []
    min_gap = None
    for index, left_ref in enumerate(order):
        left = parts[left_ref]
        for right_ref in order[index + 1 :]:
            right = parts[right_ref]
            dx = abs((right["x"] + right["dx"]) - (left["x"] + left["dx"]))
            dy = abs((right["y"] + right["dy"]) - (left["y"] + left["dy"]))
            # 轴对齐盒的间距：两轴各自的余量取大的那个。任一轴分开了就
            # 不重叠，所以最宽松的那一轴决定这一对分没分开。
            gap = max(dx - (left["hw"] + right["hw"]), dy - (left["hh"] + right["hh"]))
            if gap < 0:
                clash.append([left_ref, right_ref])
            min_gap = gap if min_gap is None else min(min_gap, gap)

    outside = []
    if bounds is not None:
        left_edge, top, right_edge, bottom = bounds
        for ref in order:
            part = parts[ref]
            cx, cy = part["x"] + part["dx"], part["y"] + part["dy"]
            if (
                cx - part["hw"] < left_edge - 0.01
                or cx + part["hw"] > right_edge + 0.01
                or cy - part["hh"] < top - 0.01
                or cy + part["hh"] > bottom + 0.01
            ):
                outside.append(ref)
    return clash, outside, min_gap


def main():
    args = K.parse_args(sys.argv[1:], flags=())
    path = K.board_arg(args)
    iterations = max(1, int(args.get("iterations") or DEFAULT_ITERATIONS))
    clearance = float(args.get("clearance") or 0.5)
    keep = {r.strip() for r in (args.get("keep") or "").split(",") if r.strip()}

    pcbnew = K.import_pcbnew()
    mm = pcbnew.ToMM
    board = K.load_board(pcbnew, path)

    parts, nets = collect(board, pcbnew, mm, keep)
    if not parts:
        K.fail("E_VALIDATION", "板上没有封装", {"board": path})
    unknown = sorted(keep - set(parts))
    if unknown:
        K.fail("E_NOT_FOUND", "--keep 点名的位号板上没有", {"refs": unknown})

    movable = [p for p in parts.values() if not p["fixed"]]
    if not movable:
        K.fail(
            "E_VALIDATION",
            "所有器件都是锁定或 --keep 的，没有可动的",
            {"parts": len(parts), "hint": "解锁需要重排的器件，或去掉 --keep"},
        )
    if not nets:
        K.fail(
            "E_VALIDATION",
            "板上没有跨器件的网络，布局没有可优化的依据",
            {"hint": "先跑 board from-netlist 把网表连上去"},
        )

    before = hpwl(parts, nets)
    start = {ref: (part["x"], part["y"]) for ref, part in parts.items()}

    tracks = len(list(board.GetTracks()))
    preview = {
        "board": path,
        "movable": len(movable),
        "fixed": len(parts) - len(movable),
        "nets_considered": len(nets),
        "hpwl_before_mm": round(before, 2),
        "existing_tracks": tracks,
        "will": "按网络连接关系重排 %d 个器件；线长没变好就不写盘" % len(movable),
    }
    if tracks:
        # 器件一动，原来的走线就连不上了。这是不可逆的一步，必须在
        # dry-run 里说清楚，让调用方在确认之前就看见代价。
        preview["warning"] = "板上已有 %d 段走线，器件搬动后它们会失效，须重新布线" % tracks
    K.check_confirm(args.get("confirm"), preview, "pcb_autoplace:" + os.path.basename(path), path)

    drc_before = severities(path)
    bounds = board_bounds(board, pcbnew, mm)
    edge = edge_clearance(board, pcbnew, mm)
    run(parts, nets, iterations, clearance, bounds, edge)
    after = hpwl(parts, nets)

    # 改坏了就原样不动。力导向是启发式，不保证单调下降；一个能把手工布局
    # 改差还报成功的命令，会让人再也不敢用它。
    improved = after < before
    if not improved:
        for ref, part in parts.items():
            part["x"], part["y"] = start[ref]
        after = before

    moved = []
    if improved:
        for ref in sorted(parts):
            part = parts[ref]
            if part["fixed"]:
                continue
            x, y = round(part["x"], 3), round(part["y"], 3)
            part["x"], part["y"] = x, y
            if abs(x - start[ref][0]) < 0.005 and abs(y - start[ref][1]) < 0.005:
                continue
            part["fp"].SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
            moved.append(
                {
                    "ref": ref,
                    "from": [round(v, 2) for v in start[ref]],
                    "to": [round(x, 2), round(y, 2)],
                }
            )
        board.BuildConnectivity()
        K.save_board(board, path)

    drc_after = severities(path) if improved else dict(drc_before)
    if drc_after.get("error", 0) > drc_before.get("error", 0):
        # 和 board route 同一条规矩：DRC error 数超过动手前就整盘回滚。
        # 线更短但板子做不出来，不是改进。
        K.fail(
            "E_INTEGRITY",
            "重排后 DRC error 数高于动手前，已整盘回滚",
            {
                "errors_before": drc_before.get("error", 0),
                "errors_after": drc_after.get("error", 0),
                "next_action": "板子已恢复原状。放大 Edge.Cuts 或加大 --clearance 再试，"
                "或用 --keep 固定不该动的器件",
            },
        )

    clash, outside, min_gap = inspect(parts, bounds)
    note = (
        "布局变了，走线要重来：接下来跑 board route。"
        if improved
        else "没有找到比现状更短的摆法，板子一个器件都没动。"
        "现有布局可能已经够好，或者板框太紧——放宽 Edge.Cuts 再试。"
    )
    if clash:
        note += "还有 %d 对庭院重叠，这块板过不了 DRC，须放大 Edge.Cuts 或手工 board move。" % len(
            clash
        )
    if outside:
        note += "有器件超出板框，须放大 Edge.Cuts。"
    warnings_added = drc_after.get("warning", 0) - drc_before.get("warning", 0)
    if warnings_added > 0:
        note += (
            "DRC warning 多了 %d 条（器件挨得更近，多半是丝印位号互相压）。"
            "这些不挡打板，但要么接受，要么手工挪位号文字，要么加大 --clearance。" % warnings_added
        )

    K.ok(
        {
            "board": path,
            "improved": improved,
            "moved": len(moved),
            "moves": moved[:60],
            "hpwl_before_mm": round(before, 2),
            "hpwl_after_mm": round(after, 2),
            "hpwl_reduction_pct": round(100.0 * (before - after) / before, 1) if before else 0.0,
            "courtyard_clash": clash[:40],
            "min_courtyard_gap_mm": None if min_gap is None else round(min_gap, 3),
            "outside_outline": outside,
            "existing_tracks": tracks,
            "verify": {
                "ran": True,
                "oracle": "kicad-cli pcb drc",
                "drc_before": drc_before,
                "drc_after": drc_after,
                "warnings_added": warnings_added,
                "note": "error 数没超过动手前的基线，否则这次调用已经回滚。"
                "warnings_added 为正说明挤得更紧了——那是本命令的目的，也是它的代价",
            },
            "note": note,
        }
    )


if __name__ == "__main__":
    main()
