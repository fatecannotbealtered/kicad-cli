"""在一层上给一个网络敷铜，并把它灌满。

链条的第三个「自己的体检报告说不合格，自己却没有命令能修」的洞。前两个是
电源线宽（board netclass 补上）和布线卡死（--mode full 的提示补上），这是
最后一个，也是电气上最要紧的一个。

`board plane` 检查每一段走线下面有没有铜——回流电流要贴着信号线走，下面没
铜它就得绕，绕出来的环路面积就是天线。链条产出的板子跑 board plane 的结果是
status FAIL、plane_layers 空、backed_fraction 0.0、170 段走线一段都没有参考
平面。板上一个敷铜区都没有。

而 `board stitch` 的职责是把同网络的敷铜孤岛用过孔连起来，`board plane` 的
职责是审敷铜——两个命令都以敷铜存在为前提，没有一个能把它造出来。

DRC 不管这件事：间距合规、连通性合规，板子照样能打出来，只是做出来的东西
辐射超标。所以这不是 ok_to_fabricate 能回答的问题，board plane 才是。

    <kicad-python> pour.py --board B.kicad_pcb --net GND --layer B.Cu
        [--margin 0.5] [--clearance 0.3] [--min-width 0.25]
        [--connect thermal|solid] [--confirm ct_xxx]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kicad_lib as K  # noqa: E402

# 敷铜边界离板框的距离。铜不能压到板边——切割公差会把它切出来，成品边缘
# 露铜。0.5mm 是常见的保守值，比多数板厂的最小要求宽。
DEFAULT_MARGIN_MM = 0.5

# 敷铜自己的间距与最小铜宽。min_width 太小会在窄缝里挤出细丝，蚀刻不出来。
DEFAULT_CLEARANCE_MM = 0.3
DEFAULT_MIN_WIDTH_MM = 0.25

# 热焊盘参数。实心连接把焊盘直接焊死在整片铜上，手工焊接时热量全被铜吸走，
# 焊不上；热隔离用几根辐条连，既导通又焊得动。所以默认热隔离。
THERMAL_GAP_MM = 0.5
THERMAL_SPOKE_MM = 0.5


def board_rect(pcbnew, board, margin_mm):
    """板框包围盒往里收 margin。没有板框就没法敷铜——敷到哪为止？"""
    box = board.GetBoardEdgesBoundingBox()
    if not box.GetWidth() or not box.GetHeight():
        K.fail(
            "E_VALIDATION",
            "板上没有 Edge.Cuts 板框，敷铜没有边界",
            {"hint": "board from-netlist 会画一圈板框；手工板请先在 KiCad 里画 Edge.Cuts"},
        )
    margin = pcbnew.FromMM(margin_mm)
    left, top = box.GetX() + margin, box.GetY() + margin
    right, bottom = box.GetRight() - margin, box.GetBottom() - margin
    if right <= left or bottom <= top:
        K.fail(
            "E_VALIDATION",
            "板框收掉 margin 之后不剩面积",
            {
                "margin_mm": margin_mm,
                "board_mm": [pcbnew.ToMM(box.GetWidth()), pcbnew.ToMM(box.GetHeight())],
            },
        )
    return left, top, right, bottom


def existing_zone(board, net_name, layer_id):
    for zone in K.zones_of(board):
        if zone.GetNetname() == net_name and zone.GetLayer() == layer_id:
            return zone
    return None


def add_zone(pcbnew, board, net, layer_id, rect, options):
    """建一个覆盖整个可用板面的矩形敷铜区。"""
    zone = pcbnew.ZONE(board)
    zone.SetLayer(layer_id)
    zone.SetNet(net)
    zone.SetZoneName(net.GetNetname() + " pour")

    left, top, right, bottom = rect
    outline = zone.Outline()
    outline.NewOutline()
    for x, y in ((left, top), (right, top), (right, bottom), (left, bottom)):
        outline.Append(x, y)

    zone.SetLocalClearance(pcbnew.FromMM(options["clearance"]))
    zone.SetMinThickness(pcbnew.FromMM(options["min_width"]))
    if options["connect"] == "solid":
        zone.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
    else:
        zone.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
        zone.SetThermalReliefGap(pcbnew.FromMM(THERMAL_GAP_MM))
        zone.SetThermalReliefSpokeWidth(pcbnew.FromMM(THERMAL_SPOKE_MM))
    # 优先级低于任何后来手工画的局部敷铜：整板地铜应当让位给特意画的小块。
    zone.SetAssignedPriority(0)
    board.Add(zone)
    K.disown(zone)
    return zone


def filled_area_mm2(pcbnew, zone):
    """灌完之后实际有多少铜。灌之前这个数是 0，所以只在灌完之后问。"""
    try:
        poly = zone.GetFilledPolysList(zone.GetLayer())
    except (AttributeError, TypeError):
        return None
    try:
        return round(pcbnew.ToMM(pcbnew.ToMM(poly.Area())), 1)
    except (AttributeError, TypeError):
        return None


def main():
    args = K.parse_args(sys.argv[1:], flags=(), required=("net", "layer"))
    path = K.board_arg(args)
    net_name = str(args["net"]).strip()
    layer_name = str(args["layer"]).strip()
    options = {
        "margin": float(args.get("margin") or DEFAULT_MARGIN_MM),
        "clearance": float(args.get("clearance") or DEFAULT_CLEARANCE_MM),
        "min_width": float(args.get("min-width") or DEFAULT_MIN_WIDTH_MM),
        "connect": str(args.get("connect") or "thermal").lower(),
    }
    if options["connect"] not in ("thermal", "solid"):
        K.fail("E_USAGE", "--connect 只能是 thermal 或 solid", {"got": options["connect"]})

    pcbnew = K.import_pcbnew()
    board = K.load_board(pcbnew, path)

    layer_id = board.GetLayerID(layer_name)
    if layer_id < 0:
        K.fail(
            "E_NOT_FOUND",
            "板上没有这一层",
            {"layer": layer_name, "hint": "铜层名形如 F.Cu / B.Cu / In1.Cu"},
        )
    if not pcbnew.IsCopperLayer(layer_id):
        K.fail("E_VALIDATION", "只能往铜层上敷铜", {"layer": layer_name})

    net = board.FindNet(net_name)
    if net is None:
        K.fail(
            "E_NOT_FOUND",
            "板上没有这个网络",
            {
                "net": net_name,
                "hint": "网络名区分大小写；board audit 的 width_by_net 列出板上的网络",
            },
        )

    already = existing_zone(board, net_name, layer_id)
    if already is not None:
        K.fail(
            "E_CONFLICT",
            "这一层上这个网络已经有敷铜区了",
            {
                "net": net_name,
                "layer": layer_name,
                "hint": "本命令只负责从无到有。要改参数请在 KiCad 里改，或先删掉原来的",
            },
        )

    rect = board_rect(pcbnew, board, options["margin"])
    width_mm = round(pcbnew.ToMM(rect[2] - rect[0]), 2)
    height_mm = round(pcbnew.ToMM(rect[3] - rect[1]), 2)

    preview = {
        "board": path,
        "net": net_name,
        "layer": layer_name,
        "area_mm": [width_mm, height_mm],
        "connect": options["connect"],
        "clearance_mm": options["clearance"],
        "will": f"在 {layer_name} 上给 {net_name} 敷一块 {width_mm}x{height_mm} mm 的铜并灌满",
    }
    K.check_confirm(args.get("confirm"), preview, f"pour:{net_name}:{layer_name}", path)

    tracks_before = len(K.tracks_of(board))
    zone = add_zone(pcbnew, board, net, layer_id, rect, options)
    K.fill_and_save(pcbnew, board, path)

    # 灌完再问面积。灌之前问到的是 0，报出去会让人以为敷铜是空的。
    area = filled_area_mm2(pcbnew, zone)
    islands = zone.Outline().OutlineCount() if zone.Outline() else None

    K.ok(
        {
            "board": path,
            "net": net_name,
            "layer": layer_name,
            "area_mm": [width_mm, height_mm],
            "filled_mm2": area,
            "outlines": islands,
            "connect": options["connect"],
            "clearance_mm": options["clearance"],
            "tracks": tracks_before,
            "note": "敷铜已灌。接着跑 board plane 看参考平面覆盖到了多少（这正是它要回答的），"
            "再跑 board stitch 用过孔把同网络的孤岛连起来——灌出来的铜被走线切开之后，"
            "看着连成一片的其实不是一片。DRC 不检查参考平面，所以 ok_to_fabricate 为真"
            "也不代表这块板的回流路径是好的。",
        }
    )


if __name__ == "__main__":
    main()
