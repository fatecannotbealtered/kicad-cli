"""建网络类并把网络指派进去。

链条一直缺这一环。`board from-netlist` 造出来的板只有 Default 一个类，
线宽 0.20 mm——1 oz 铜、10C 温升下大约 0.74 A。信号线够，电源线不够。
`board rewidth` 按网络类重布，`board widen` 原地加宽到不撞为止，两个都
读网络类；而没有任何命令能**建**一个类。于是 `board audit` 会对自己链条
产出的板报 error（「电源网络落在 Default 类」），而链条里没有东西能修它。

网络类写在 .kicad_pro 的 net_settings 里，不在板文件里：classes 是定义，
netclass_patterns 是「哪个网络归哪个类」，按 fnmatch 匹配。所以这是一次
JSON 编辑，不需要 pcbnew——但它改的是盘上的文件，所以照样走写门禁和
写事务。

改完线还是原来的宽度：网络类是目标，不是现状。要让铜真的变宽，接着跑
board rewidth（按类整条重布）或 board widen（原地加宽）。

    <kicad-python> netclass.py --board B.kicad_pcb --name Power --width 0.5
        --nets +3V3,VIN [--clearance 0.2] [--via-diameter 0.8] [--via-drill 0.4]
        [--confirm ct_xxx]
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kicad_lib as K  # noqa: E402

# IPC-2221 外层走线：1 oz 铜、10C 温升下的载流量。和 audit 用的是同一套
# 常数，所以这里报出来的 amp 和 audit 之后报的对得上。
IPC_K, IPC_B, IPC_C = 0.048, 0.44, 0.725
COPPER_OZ, DELTA_T = 1.0, 10.0


def ampacity(width_mm, oz=COPPER_OZ, dt=DELTA_T):
    """这个宽度大概能过多少安培。给的是量级，不是设计依据。"""
    area_mil2 = (width_mm / 0.0254) * (oz * 1.378)
    return round(IPC_K * (dt**IPC_B) * (area_mil2**IPC_C), 2)


def load_project(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        K.fail(
            "E_NOT_FOUND",
            "找不到工程文件，网络类存在其中",
            {"path": path, "hint": "板文件旁边应当有同名的 .kicad_pro"},
        )
    except (OSError, ValueError) as exc:
        K.fail("E_IO", "工程文件读不出来", {"path": path, "reason": str(exc)[:200]})


def apply(project, name, params, nets):
    """建/改类，并把每个网络指派过去。返回这次真正改了什么。

    指派用精确网络名当 pattern，不用通配符：调用方给的是网络名，替他
    编一条 `+3V3*` 出来会顺手匹配上 `+3V3_SENSE`，那是他没要的。
    """
    settings = project.setdefault("net_settings", {})
    classes = settings.setdefault("classes", [])
    patterns = settings.setdefault("netclass_patterns", [])

    existing = next((c for c in classes if c.get("name") == name), None)
    if existing is None:
        # 新类按 Default 起底再覆盖，免得缺字段——KiCad 对缺字段的类会退回
        # 内建默认值，那和这里报出去的数字对不上。
        base = next((c for c in classes if c.get("name") == "Default"), {})
        created = dict(base)
        created.update({"name": name, **params})
        classes.append(created)
        action = "created"
    else:
        existing.update(params)
        action = "updated"

    assigned, already = [], []
    for net in nets:
        match = next((p for p in patterns if p.get("pattern") == net), None)
        if match is None:
            patterns.append({"pattern": net, "netclass": name})
            assigned.append(net)
        elif match.get("netclass") != name:
            match["netclass"] = name
            assigned.append(net)
        else:
            already.append(net)
    return action, assigned, already


def main():
    args = K.parse_args(sys.argv[1:], flags=(), required=("name", "nets"))
    board = K.board_arg(args)
    name = str(args["name"]).strip()
    if not name:
        K.fail("E_USAGE", "--name 不能为空", {"param": "name"})
    nets = [n.strip() for n in str(args["nets"]).split(",") if n.strip()]
    if not nets:
        K.fail("E_USAGE", "--nets 至少要给一个网络名", {"param": "nets"})

    params = {}
    for option, key in (
        ("width", "track_width"),
        ("clearance", "clearance"),
        ("via-diameter", "via_diameter"),
        ("via-drill", "via_drill"),
    ):
        if args.get(option) is not None:
            try:
                value = float(args[option])
            except (TypeError, ValueError):
                K.fail("E_USAGE", "尺寸必须是数字，单位 mm", {"param": option, "got": args[option]})
            if value <= 0:
                K.fail("E_USAGE", "尺寸必须大于 0", {"param": option, "got": value})
            params[key] = value
    if "track_width" not in params:
        K.fail("E_USAGE", "--width 是必需的：网络类的意义就是给出目标线宽", {"param": "width"})

    path = K.project_path(board)
    project = load_project(path)
    classes = project.get("net_settings", {}).get("classes", [])
    before = next((c for c in classes if c.get("name") == name), None)

    preview = {
        "project": path,
        "netclass": name,
        "exists": before is not None,
        "track_width_mm": params["track_width"],
        "ampacity_a": ampacity(params["track_width"]),
        "nets": nets,
        "will": f"在 .kicad_pro 里{'改写' if before else '新建'}网络类 {name} "
        f"并把 {len(nets)} 个网络指派给它；铜先不变",
    }
    K.check_confirm(args.get("confirm"), preview, "netclass:" + name, path)

    action, assigned, already = apply(project, name, params, nets)

    K.begin_write(path)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(project, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    K.ok(
        {
            "project": path,
            "netclass": name,
            "action": action,
            "params": params,
            "ampacity_a": ampacity(params["track_width"]),
            "assigned": assigned,
            "already_assigned": already,
            "note": "网络类是目标，不是现状——板上的铜还是原来的宽度。"
            "接着跑 board rewidth 按新宽度整条重布（推荐），或 board widen 原地加宽；"
            "然后 board audit 复查 width_compliance，board drc 复查间距。"
            "载流量按 1 oz 铜、10C 温升估算，仅供量级参考。",
        }
    )


if __name__ == "__main__":
    main()
