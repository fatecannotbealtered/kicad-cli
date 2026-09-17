"""把板文件里的铜和图形翻译成 IPC 能收的 protobuf 对象。

本来不需要这一层：`ParseAndCreateItemsFromString` 直接吃 s-expr，喂进去就完事。
实测它在 KiCad 10.0.6 里**注册了但没实现**——把 KiCad 自己
`SaveSelectionToString` 吐出来的字符串原样喂回去，返回 `created_items=0`、
`status=IRS_UNKNOWN`，板上一个对象都不多。和 `UpdateBoardStackup` 一样，
proto 里有、handler 表里有、实际是空的。

所以只能一个个照着造。走线、过孔、铺铜、板框、丝印这五类都在 kipy 的 16 个
可打包类型里，构造出来 `create_items` 收得下——已实测。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / ".vendor"))
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "demo"))

from kipy.board_types import (  # noqa: E402
    BoardLayer,
    BoardRectangle,
    BoardText,
    Net,
    Track,
    Via,
    Zone,
)
from kipy.geometry import PolyLineNode, Vector2  # noqa: E402
from kipy.proto.board.board_types_pb2 import ZoneConnectionStyle, ZoneType  # noqa: E402

from kicad_cli import sexpr  # noqa: E402

from config import BOARD as SRC  # noqa: E402

# KiCad 的层名 -> kipy 的层枚举。板上用到的这些就够了。
LAYERS = {
    "F.Cu": BoardLayer.BL_F_Cu,
    "B.Cu": BoardLayer.BL_B_Cu,
    "In1.Cu": BoardLayer.BL_In1_Cu,
    "In2.Cu": BoardLayer.BL_In2_Cu,
    "F.SilkS": BoardLayer.BL_F_SilkS,
    "B.SilkS": BoardLayer.BL_B_SilkS,
    "Edge.Cuts": BoardLayer.BL_Edge_Cuts,
    "Margin": BoardLayer.BL_Margin,
    "F.CrtYd": BoardLayer.BL_F_CrtYd,
    "B.CrtYd": BoardLayer.BL_B_CrtYd,
}


def mm(v) -> int:
    """毫米 -> KiCad 内部单位（纳米）。"""
    return int(round(float(v) * 1_000_000))


def load():
    return sexpr.parse(SRC.read_text(encoding="utf-8"))


def net_name(node) -> str:
    """这个对象属于哪个网络。

    KiCad 10 的板文件里，走线和过孔直接存网**名**（`(net "VRAW")`），不再是
    指向顶层网络表的编号。这一点很省事：实时板上的网络编号跟文件里的未必一样，
    按名字对反而是唯一可靠的办法。
    """
    n = sexpr.child(node, "net")
    if n is None:
        return ""
    v = sexpr.value(n, 1, "")
    return str(v) if not str(v).lstrip("-").isdigit() else ""


def _xy(node, tag):
    c = sexpr.child(node, tag)
    return Vector2.from_xy(mm(sexpr.value(c, 1)), mm(sexpr.value(c, 2)))


def tracks(root, live: dict[str, Net]):
    out = []
    for s in sexpr.children(root, "segment"):
        layer = str(sexpr.value(sexpr.child(s, "layer"), 1, ""))
        if layer not in LAYERS:
            continue
        t = Track()
        t.start = _xy(s, "start")
        t.end = _xy(s, "end")
        t.width = mm(sexpr.value(sexpr.child(s, "width"), 1))
        t.layer = LAYERS[layer]
        name = net_name(s)
        if name and name in live:
            t.net = live[name]
        out.append((layer, t))
    return out


def vias(root, live: dict[str, Net]):
    out = []
    for v in sexpr.children(root, "via"):
        item = Via()
        item.position = _xy(v, "at")
        item.diameter = mm(sexpr.value(sexpr.child(v, "size"), 1))
        item.drill_diameter = mm(sexpr.value(sexpr.child(v, "drill"), 1))
        name = net_name(v)
        if name and name in live:
            item.net = live[name]
        out.append(item)
    return out


def board_outline(root):
    """板框。源板上是一个 gr_rect。"""
    out = []
    for r in sexpr.children(root, "gr_rect"):
        layer = str(sexpr.value(sexpr.child(r, "layer"), 1, ""))
        if layer not in LAYERS:
            continue
        item = BoardRectangle()
        item.top_left = _xy(r, "start")
        item.bottom_right = _xy(r, "end")
        item.layer = LAYERS[layer]
        stroke = sexpr.child(r, "stroke")
        if stroke is not None:
            item.attributes.stroke.width = mm(sexpr.value(sexpr.child(stroke, "width"), 1, 0.1))
        out.append(item)
    return out


def texts(root):
    out = []
    for t in sexpr.children(root, "gr_text"):
        layer = str(sexpr.value(sexpr.child(t, "layer"), 1, ""))
        if layer not in LAYERS:
            continue
        item = BoardText()
        item.value = str(sexpr.value(t, 1, ""))
        at = sexpr.child(t, "at")
        item.position = Vector2.from_xy(mm(sexpr.value(at, 1)), mm(sexpr.value(at, 2)))
        item.layer = LAYERS[layer]
        eff = sexpr.child(t, "effects")
        font = sexpr.child(eff, "font") if eff is not None else None
        if font is not None:
            size = sexpr.child(font, "size")
            if size is not None:
                item.attributes.size = Vector2.from_xy(
                    mm(sexpr.value(size, 1)), mm(sexpr.value(size, 2))
                )
            th = sexpr.child(font, "thickness")
            if th is not None:
                item.attributes.stroke_width = mm(sexpr.value(th, 1))
        out.append(item)
    return out


def zones(root, live: dict[str, Net]):
    """铺铜：只灌轮廓。

    源板的 zone 块里存着上次算好的填充结果（24 个铜岛、62562 个顶点）。
    这里一概不读——那些顶点要留给 KiCad 当场算，那是全片唯一真正在计算的一帧。
    """
    out = []
    for z in sexpr.children(root, "zone"):
        keepout = sexpr.child(z, "keepout")
        item = Zone()
        if keepout is not None:
            # 禁布区。这块板上那个是 ESP32 天线净空——四层禁铜禁走线禁过孔，
            # 是这次设计里为数不多的 RF 处置，不该在演示里被跳过。
            item._proto.type = ZoneType.ZT_RULE_AREA
            ra = item._proto.rule_area_settings
            for tag, field in (
                ("tracks", "keepout_tracks"),
                ("vias", "keepout_vias"),
                ("pads", "keepout_pads"),
                ("copperpour", "keepout_copper"),
                ("footprints", "keepout_footprints"),
            ):
                rule = sexpr.child(keepout, tag)
                if rule is not None:
                    setattr(ra, field, str(sexpr.value(rule, 1, "")) == "not_allowed")
        layer_names = []
        single = sexpr.child(z, "layer")
        multi = sexpr.child(z, "layers")
        if single is not None:
            layer_names = [str(sexpr.value(single, 1, ""))]
        elif multi is not None:
            layer_names = [str(x) for x in multi[1:] if isinstance(x, str)]
        picked = [LAYERS[n] for n in layer_names if n in LAYERS]
        if not picked:
            continue
        item.layers = picked
        poly = sexpr.child(z, "polygon")
        pts = sexpr.child(poly, "pts") if poly is not None else None
        if pts is None:
            continue
        # Zone 的 outline 属性读的是 polygons[0]，新对象里那个列表是空的，
        # 得先 add 一个出来，否则 IndexError。
        item._proto.outline.polygons.add()
        outline = item.outline.outline
        for xy in sexpr.children(pts, "xy"):
            outline.append(PolyLineNode.from_xy(mm(sexpr.value(xy, 1)), mm(sexpr.value(xy, 2))))
        outline.closed = True
        # 铺铜的网络写在 `(net "GND")` 里，不是 net_name。读错这一个标签，
        # 六块铜皮全都没网络——GND 铜谁也连不上，DRC 直接冒出 122 个未连接。
        # 演示跑起来照样好看，结果是错的。
        name = net_name(z)
        if keepout is None and name and name in live:
            item.net = live[name]
        # 填充参数也得照抄。kipy 的默认值是 0：间距 0、热焊盘间隙 0、辐条宽 0，
        # 和源板的 0.3/0.5/0.3 差着量级，铺铜出来的形状会完全不同。
        if keepout is None:
            cp = sexpr.child(z, "connect_pads")
            if cp is not None:
                cl = sexpr.child(cp, "clearance")
                if cl is not None:
                    item.clearance = mm(sexpr.value(cl, 1))
            mt = sexpr.child(z, "min_thickness")
            if mt is not None:
                item.min_thickness = mm(sexpr.value(mt, 1))
            conn = item._proto.copper_settings.connection
            # 这两个不在板文件里，只能按 KiCad 的默认补。补漏了的后果很具体：
            # zone_connection 留在 ZCS_UNKNOWN(0)、辐条角度留在 0°，花焊盘只长得出
            # 一根辐条，DRC 报 79 条 starved_thermal——而且只建铺铜、不建任何走线
            # 过孔时就已经全都在了，跟铜的疏密无关。
            conn.zone_connection = ZoneConnectionStyle.ZCS_THERMAL
            conn.thermal_spokes.angle.value_degrees = 45.0
            fill = sexpr.child(z, "fill")
            if fill is not None:
                spokes = conn.thermal_spokes
                tg = sexpr.child(fill, "thermal_gap")
                if tg is not None:
                    spokes.gap.value_nm = mm(sexpr.value(tg, 1))
                tb = sexpr.child(fill, "thermal_bridge_width")
                if tb is not None:
                    spokes.width.value_nm = mm(sexpr.value(tb, 1))
                mia = sexpr.child(fill, "island_removal_mode")
                if mia is not None:
                    item._proto.copper_settings.island_mode = int(sexpr.value(mia, 1, 0)) + 1
        nm = sexpr.child(z, "name")
        if nm is not None:
            item.name = str(sexpr.value(nm, 1, ""))
        pr = sexpr.child(z, "priority")
        if pr is not None:
            item.priority = int(sexpr.value(pr, 1, 0))
        label = "禁布区" if keepout is not None else name
        out.append((layer_names[0] if layer_names else "?", label, item))
    return out


def summarise() -> None:
    root = load()
    names = {net_name(s) for s in sexpr.children(root, "segment")}
    print(f"走线涉及网络 {len(names - {''})} 个")
    print(f"segment {len(sexpr.children(root, 'segment'))}")
    print(f"via     {len(sexpr.children(root, 'via'))}")
    print(f"zone    {len(sexpr.children(root, 'zone'))}")
    print(f"gr_rect {len(sexpr.children(root, 'gr_rect'))}")
    print(f"gr_text {len(sexpr.children(root, 'gr_text'))}")
    seen = set()
    for s in sexpr.children(root, "segment"):
        seen.add(str(sexpr.value(sexpr.child(s, "layer"), 1, "")))
    print(f"走线用到的层: {sorted(seen)}")


if __name__ == "__main__":
    summarise()
