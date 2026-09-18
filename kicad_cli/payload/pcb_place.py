"""按坐标搬器件，并检查庭院是否撞上。

存在的理由：有些布线问题根本不是布线问题。器件的物理顺序和芯片引脚顺序
对不上时，走线只能互相斜穿——四条粗线交叉在一起，任何布线器都解不开，
调参数、换顺序、加宽走廊全都白费。这时候要动的是布局，不是布线。

本脚本只做最小的事：把指定位号搬到指定坐标，搬完检查有没有庭院重叠。
不自动决定搬去哪——那是工程判断，由调用方给出。

用法：
    <kicad-python> pcb_place.py --board B.kicad_pcb --moves "L801:144.3,85.7;L803:144.3,69.2"
    加 --confirm ct_xxx 落实。坐标是封装原点（GetPosition），单位 mm。
    --rot 可选：形如 "L801:90" 另行指定旋转角。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kicad_lib as K  # noqa: E402


def courtyard_box(f, pcbnew, mm):
    for lay in (pcbnew.F_CrtYd, pcbnew.B_CrtYd):
        cy = f.GetCourtyard(lay)
        if cy.OutlineCount():
            bb = cy.BBox()
            return (mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom()))
    bb = f.GetBoundingBox(False, False)
    return (mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom()))


def overlaps(a, b, gap=0.0):
    return not (
        a[2] + gap <= b[0] or b[2] + gap <= a[0] or a[3] + gap <= b[1] or b[3] + gap <= a[1]
    )


def main():
    args = K.parse_args(sys.argv[1:], required=("moves",))
    path = K.board_arg(args)
    pcbnew = K.import_pcbnew()
    mm = pcbnew.ToMM
    b = K.load_board(pcbnew, path)

    moves = {}
    for item in args["moves"].split(";"):
        item = item.strip()
        if not item:
            continue
        ref, xy = item.split(":")
        x, y = xy.split(",")
        moves[ref.strip()] = (float(x), float(y))
    rots = {}
    for item in (args.get("rot") or "").split(";"):
        item = item.strip()
        if item:
            ref, a = item.split(":")
            rots[ref.strip()] = float(a)

    fps = {f.GetReference(): f for f in K.footprints_of(b)}
    missing = [r for r in moves if r not in fps]
    if missing:
        K.fail("E_NOT_FOUND", "板上没有这些位号", {"refs": missing})

    plan = []
    for ref, (x, y) in sorted(moves.items()):
        f = fps[ref]
        plan.append(
            {
                "ref": ref,
                "from": [round(mm(f.GetPosition().x), 2), round(mm(f.GetPosition().y), 2)],
                "to": [x, y],
                "rot_deg": rots.get(ref),
            }
        )

    preview = {"board": path, "moves": plan, "will": "搬动 %d 个器件；搬完检查庭院重叠" % len(plan)}
    K.check_confirm(
        args.get("confirm"),
        preview,
        "pcb_place:%s:%s" % (",".join(sorted(moves)), os.path.basename(path)),
        path,
    )

    for ref, (x, y) in moves.items():
        f = fps[ref]
        f.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
        if ref in rots:
            f.SetOrientationDegrees(rots[ref])

    # 搬完查庭院。重叠不一定是错（有意堆叠的除外），但必须报出来让人看见
    boxes = [(f.GetReference(), courtyard_box(f, pcbnew, mm)) for f in K.footprints_of(b)]
    clash = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if boxes[i][0] not in moves and boxes[j][0] not in moves:
                continue  # 两个都没搬过的，不是本次引入的
            if overlaps(boxes[i][1], boxes[j][1]):
                clash.append([boxes[i][0], boxes[j][0]])

    b.BuildConnectivity()
    b.Save(path)
    K.ok(
        {
            "moved": len(moves),
            "plan": plan,
            "courtyard_clash": clash,
            "note": "器件搬动后，连到它们的走线已经失效，"
            "接下来必须重布相关网络（pcb_route.py --mode rewidth --nets ...）",
        }
    )


main()
