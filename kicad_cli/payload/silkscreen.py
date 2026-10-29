"""把位号文字挪到读得出来的地方。

链条产出的板子最后剩下的一类问题就是这个:20 条 DRC warning,6 条丝印压丝印、
14 条丝印压焊盘。两类的一边都是**参考字段**——也就是位号文字——所以能动的
也是它。

这不只是好看。位号看不清就没法手工贴片、没法返修、没法对着 BOM 找元件。
板厂照样能做,做出来的板子人用不了。丝印压在焊盘上更直接:那部分丝印会被
阻焊开窗切掉,印出来是缺笔画的半个字。

做法和 board place 是同一件事,只是对象从封装换成了文字:列出障碍(所有焊
盘、所有丝印图形、其它位号),给每个撞上的位号在它自己封装周围试一圈候选
位置,取第一个干净的。挪不动的如实报出来,不硬塞。

只挪,不旋转、不缩小、不隐藏。缩小到看不清和挪不开是一回事;隐藏是把信息
丢掉,那是人该做的决定,不是这个命令该替他做的。

    <kicad-python> silkscreen.py --board B.kicad_pcb [--clearance 0.15]
        [--confirm ct_xxx]
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kicad_lib as K  # noqa: E402

# 文字与任何东西之间要留的空。KiCad 默认的丝印间距检查大约是 0.15mm,
# 这里跟着它走——松了 DRC 还是要报,紧了挪了也白挪。
DEFAULT_CLEARANCE_MM = 0.15

# 候选位置:先在封装四周按八个方向试,再逐圈往外。半径步长取 0.5mm,
# 对 0402 这种小件够细,对大件也不会试太多轮。
RING_STEP_MM = 0.5
MAX_RINGS = 8
DIRECTIONS = (
    (0, -1),
    (0, 1),
    (-1, 0),
    (1, 0),
    (-1, -1),
    (1, -1),
    (-1, 1),
    (1, 1),
)


def box_of(item, mm):
    bb = item.GetBoundingBox()
    return (mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom()))


def overlaps(a, b, gap):
    return not (
        a[2] + gap <= b[0] or b[2] + gap <= a[0] or a[3] + gap <= b[1] or b[3] + gap <= a[1]
    )


def shift(box, dx, dy):
    return (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)


def silk_layer_of(pcbnew, ref):
    return ref.GetLayer()


def collect(board, pcbnew, mm):
    """每个可见位号,以及它要躲开的东西。

    障碍分两类,因为它们对应两种不同的 DRC 报告:焊盘(丝印压上去会被阻焊
    切掉)和丝印图形(丝印压丝印)。其它位号自己也是障碍,但它们会动,所以
    单独拿一份、每挪一个更新一次。
    """
    refs, pads, graphics = [], [], []
    for footprint in K.footprints_of(board):
        ref = footprint.Reference()
        for pad in footprint.Pads():
            pads.append(box_of(pad, mm))
        for item in footprint.GraphicalItems():
            if item.GetLayer() in (pcbnew.F_SilkS, pcbnew.B_SilkS):
                graphics.append((item.GetLayer(), box_of(item, mm)))
        if not ref.IsVisible() or not ref.GetText():
            continue
        refs.append(
            {
                "ref": ref,
                "name": ref.GetText(),
                "layer": ref.GetLayer(),
                "box": box_of(ref, mm),
                "home": (mm(ref.GetPosition().x), mm(ref.GetPosition().y)),
                "anchor": box_of(footprint, mm),
            }
        )
    return refs, pads, graphics


def clashes(entry, box, pads, graphics, others, clearance):
    """这个位置撞不撞。撞了就说明还得挪。

    焊盘不分层:丝印在顶层、焊盘在顶层铜,阻焊开窗是按铜层开的,压上就被切。
    丝印图形和其它位号分层比——顶层的丝印不会和底层的丝印打架。
    """
    for pad in pads:
        if overlaps(box, pad, clearance):
            return True
    for layer, graphic in graphics:
        if layer == entry["layer"] and overlaps(box, graphic, clearance):
            return True
    for other in others:
        if other is entry:
            continue
        if other["layer"] == entry["layer"] and overlaps(box, other["box"], clearance):
            return True
    return False


def inside(box, bounds):
    """文字整体落在板框里吗。

    板框外的丝印 DRC 不管——它不违反任何间距规则,只是印不出来。但一个印不
    出来的位号和一个压在焊盘上的位号一样没用,而这个命令的全部意义就是让位号
    读得出来。实测第一版把 J2 和 Y1 推到了 y=51,板子到 48.75 就没了:DRC 干净,
    板上没字。
    """
    if bounds is None:
        return True
    left, top, right, bottom = bounds
    return box[0] >= left and box[1] >= top and box[2] <= right and box[3] <= bottom


def relocate(entry, pads, graphics, others, clearance, bounds):
    """在封装周围找一个干净位置。找不到就留在原地并说明。

    候选点从封装包围盒外沿往外推,而不是从文字当前位置推:文字一开始就可能
    压在封装中间,从那里往外挪几步还在里面。
    """
    left, top, right, bottom = entry["anchor"]
    width = entry["box"][2] - entry["box"][0]
    height = entry["box"][3] - entry["box"][1]
    cx, cy = (left + right) / 2.0, (top + bottom) / 2.0
    half_w, half_h = (right - left) / 2.0, (bottom - top) / 2.0

    for ring in range(1, MAX_RINGS + 1):
        pad_out = ring * RING_STEP_MM
        for dx, dy in DIRECTIONS:
            x = cx + dx * (half_w + pad_out + width / 2.0)
            y = cy + dy * (half_h + pad_out + height / 2.0)
            candidate = (x - width / 2.0, y - height / 2.0, x + width / 2.0, y + height / 2.0)
            if not inside(candidate, bounds):
                continue
            if not clashes(entry, candidate, pads, graphics, others, clearance):
                return (x, y), candidate
    return None, None


def main():
    args = K.parse_args(sys.argv[1:], flags=())
    path = K.board_arg(args)
    clearance = float(args.get("clearance") or DEFAULT_CLEARANCE_MM)

    pcbnew = K.import_pcbnew()
    mm = pcbnew.ToMM
    board = K.load_board(pcbnew, path)
    refs, pads, graphics = collect(board, pcbnew, mm)
    box = board.GetBoardEdgesBoundingBox()
    bounds = (
        (mm(box.GetLeft()), mm(box.GetTop()), mm(box.GetRight()), mm(box.GetBottom()))
        if box.GetWidth() and box.GetHeight()
        else None
    )
    if not refs:
        K.fail(
            "E_VALIDATION",
            "板上没有可见的位号文字",
            {"board": path, "hint": "位号可能被隐藏了；本命令只挪位置，不改可见性"},
        )

    crowded = [e for e in refs if clashes(e, e["box"], pads, graphics, refs, clearance)]
    preview = {
        "board": path,
        "references": len(refs),
        "crowded": len(crowded),
        "crowded_refs": sorted(e["name"] for e in crowded)[:40],
        "clearance_mm": clearance,
        "will": f"把 {len(crowded)} 个压住焊盘或其它丝印的位号挪到封装旁边的空位；"
        "只动文字位置，不改大小、角度和可见性",
    }
    K.check_confirm(args.get("confirm"), preview, "silkscreen:" + os.path.basename(path), path)

    errors_baseline = K.err_count(K.run_drc(path))

    moved, stuck = [], []
    for entry in crowded:
        position, placed = relocate(entry, pads, graphics, refs, clearance, bounds)
        if position is None:
            stuck.append(entry["name"])
            continue
        entry["ref"].SetPosition(
            pcbnew.VECTOR2I(pcbnew.FromMM(position[0]), pcbnew.FromMM(position[1]))
        )
        entry["box"] = placed  # 后面的位号要躲开它的新位置，不是旧的
        moved.append(
            {
                "ref": entry["name"],
                "from": [round(v, 2) for v in entry["home"]],
                "to": [round(position[0], 2), round(position[1], 2)],
            }
        )

    if not moved:
        K.ok(
            {
                "board": path,
                "references": len(refs),
                "crowded": len(crowded),
                "moved": 0,
                "moves": [],
                "stuck": stuck,
                "clearance_mm": clearance,
                "max_move_mm": None,
                "mean_move_mm": None,
                "verify": {
                    "ran": False,
                    "oracle": "kicad-cli pcb drc",
                    "errors_baseline": errors_baseline,
                    "errors_final": errors_baseline,
                    "note": "没有动过板子，所以没有复核的必要",
                },
                "note": "没有位号需要挪，或者需要挪的都挪不开——板子未改动。"
                + (f"挪不开的：{', '.join(stuck[:20])}。" if stuck else ""),
            }
        )

    # 挪多远要报出来。位号读得出来只是及格线,它还得让人认得出是哪个器件的:
    # 一个离自己封装 6mm 的 C3,在一排 0603 中间等于没标。板子越挤挪得越远,
    # 而这个命令没法把板子变大——能做的是把代价摆出来让人看见。
    distances = [math.dist(m["from"], m["to"]) for m in moved]
    max_mm = round(max(distances), 2)
    mean_mm = round(sum(distances) / len(distances), 2)
    K.fill_and_save(pcbnew, board, path)
    errors_final = K.err_count(K.run_drc(path))
    if errors_final > errors_baseline:
        # 和 board route / place / rewidth / freeroute 同一条判据同一个码。
        # 丝印是外观,但外观的改动也不该把板子弄得更差。
        K.fail(
            "E_INTEGRITY",
            "挪完位号后 DRC error 数高于动手前，已整盘回滚",
            {
                "errors_baseline": errors_baseline,
                "errors_final": errors_final,
                "next_action": "板子已恢复原状。加大 --clearance 再试，或手工在 KiCad 里挪",
            },
        )

    K.ok(
        {
            "board": path,
            "references": len(refs),
            "crowded": len(crowded),
            "moved": len(moved),
            "moves": moved[:60],
            "stuck": stuck,
            "clearance_mm": clearance,
            "max_move_mm": max_mm,
            "mean_move_mm": mean_mm,
            "verify": {
                "ran": True,
                "oracle": "kicad-cli pcb drc",
                "errors_baseline": errors_baseline,
                "errors_final": errors_final,
                "note": "error 数没超过动手前的基线，否则这次调用已经回滚。"
                "丝印本身多数是 warning 不是 error，所以要看 warning 降了没有，"
                "跑一次 board drc",
            },
            "note": f"挪了 {len(moved)} 个位号，平均 {mean_mm} mm、"
            f"最远 {max_mm} mm。板子越挤挪得越远，"
            "挪太远的位号虽然读得出来、却不容易看出是哪个器件的——"
            "这种时候要么板子做大一点，要么那几个手工摆。"
            + (
                f"还有 {len(stuck)} 个挪不开（{', '.join(stuck[:10])}）——"
                "周围一圈都被占满了，只能靠板子做大一点或手工处理。"
                if stuck
                else ""
            )
            + "跑 board drc 看丝印类 warning 降了多少。",
        }
    )


if __name__ == "__main__":
    main()
