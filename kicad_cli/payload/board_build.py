"""从构建计划造一块板：放封装、连网络、画边框、存盘。

这是「Update PCB from Schematic」没有无头入口的那一步，绕过去的做法。
KiCad 实现了那个对话框但 SWIG 不绑定它，官方 CLI 也没有子命令——不过对话框
里干的活 pcbnew 全都暴露了：按 ID 从库里加载封装、摆到坐标上、建网络、把
焊盘连上去。所以缺的是入口，不是能力。

计划由宿主侧的 boardgen 算好并校验过（封装文件是否存在是文件检查，不需要
KiCad），这里只负责执行。

    <kicad-python> board_build.py --plan plan.json --out board.kicad_pcb [--confirm ct_xxx]
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kicad_lib as K  # noqa: E402

# 板框离最外侧封装的距离。够 DRC 不因为边框贴脸报错，也不至于浪费板材。
EDGE_MARGIN_MM = 2.0


def load_plan(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        K.fail("E_IO", "构建计划读不出来", {"path": path, "reason": str(exc)[:200]})


def place(pcbnew, board, components):
    """把每个封装加载并摆到计划给的坐标上。"""
    placed = []
    for item in components:
        library = os.path.dirname(item["file"])
        name = os.path.splitext(os.path.basename(item["file"]))[0]
        footprint = pcbnew.FootprintLoad(library, name)
        if footprint is None:
            # 宿主侧已经确认过文件存在，所以走到这里说明文件在但读不了——
            # 损坏、权限、或者版本不兼容。这三种都不是「拼错了」。
            K.fail(
                "E_IO",
                "封装文件存在但 KiCad 读不了",
                {"ref": item["ref"], "file": item["file"]},
            )
        footprint.SetReference(item["ref"])
        if item.get("value"):
            footprint.SetValue(str(item["value"]))
        footprint.SetPosition(
            pcbnew.VECTOR2I(pcbnew.FromMM(float(item["x"])), pcbnew.FromMM(float(item["y"])))
        )
        board.Add(footprint)
        K.disown(footprint)
        placed.append(item["ref"])
    return placed


def wire(pcbnew, board, nets):
    """建网络并连到焊盘。返回连上的焊盘数与连不上的节点。"""
    pads_by_ref = {}
    for footprint in K.footprints_of(board):
        pads_by_ref[footprint.GetReference()] = {p.GetNumber(): p for p in footprint.Pads()}

    joined, missing = 0, []
    for net in nets:
        info = pcbnew.NETINFO_ITEM(board, net["name"])
        board.Add(info)
        K.disown(info)
        for node in net["nodes"]:
            pad = pads_by_ref.get(node["ref"], {}).get(node["pin"])
            if pad is None:
                # 网表说某个焊盘存在而封装上没有，多半是符号和封装的引脚
                # 编号对不上。这是设计问题，不是执行问题，所以如实报出来
                # 而不是静默跳过——静默跳过会交付一块少了连接的板。
                missing.append({"net": net["name"], "node": node["ref"] + "." + node["pin"]})
                continue
            pad.SetNet(info)
            joined += 1
    return joined, missing


def outline(pcbnew, board, margin_mm):
    """按已摆元件的包围盒画一圈 Edge.Cuts。没有边框的板子出不了制造文件。"""
    box = board.GetBoardEdgesBoundingBox()
    if not box.GetWidth():
        box = board.ComputeBoundingBox(False)
    margin = pcbnew.FromMM(margin_mm)
    left, top = box.GetX() - margin, box.GetY() - margin
    right, bottom = box.GetRight() + margin, box.GetBottom() + margin
    corners = [(left, top), (right, top), (right, bottom), (left, bottom)]
    for index in range(4):
        segment = pcbnew.PCB_SHAPE(board)
        segment.SetShape(pcbnew.SHAPE_T_SEGMENT)
        segment.SetStart(pcbnew.VECTOR2I(*corners[index]))
        segment.SetEnd(pcbnew.VECTOR2I(*corners[(index + 1) % 4]))
        segment.SetLayer(pcbnew.Edge_Cuts)
        segment.SetWidth(pcbnew.FromMM(0.1))
        board.Add(segment)
        K.disown(segment)
    return [
        round(pcbnew.ToMM(right - left), 2),
        round(pcbnew.ToMM(bottom - top), 2),
    ]


def main():
    args = K.parse_args(sys.argv[1:], flags=())
    plan_path = args.get("plan")
    out = args.get("out")
    if not plan_path or not out:
        K.fail("E_USAGE", "--plan 和 --out 都是必需的", {"got": sorted(args)})
    plan = load_plan(plan_path)

    preview = {
        "out": out,
        "components": len(plan["components"]),
        "nets": len(plan["nets"]),
        "placement": plan.get("placement", "grid"),
        "will": "新建一块板：按计划摆放全部封装、建立网络、画矩形板框",
    }
    # 绑定网表，不是计划。板文件还不存在，绑不了；而计划是每次调用新建的
    # 临时文件，绑它等于绑一个必然变化的东西——dry-run 和 confirm 永远对不上。
    # 网表才是真正的输入：它变了，这份计划就该作废，这正是 token 要表达的。
    K.check_confirm(
        args.get("confirm"),
        preview,
        "board from-netlist:" + os.path.basename(out),
        args.get("netlist"),
    )

    pcbnew = K.import_pcbnew()
    board = pcbnew.CreateEmptyBoard()
    placed = place(pcbnew, board, plan["components"])
    joined, missing = wire(pcbnew, board, plan["nets"])
    size = outline(pcbnew, board, EDGE_MARGIN_MM)

    K.begin_write(out)
    board.Save(out)

    K.ok(
        {
            "board": out,
            "footprints": len(placed),
            "nets": len(plan["nets"]),
            "pads_connected": joined,
            "unmatched_nodes": missing,
            "board_mm": size,
            "placement": plan.get("placement", "grid"),
            "note": "布局是按参考编号排的网格，不是按电路关系摆的——先跑 board place "
            "按连接关系重排（实测走线铜长可减一半），再跑 board route 布线，"
            "之后按 verify.width_regressed 决定要不要再 widen",
        }
    )


if __name__ == "__main__":
    main()
