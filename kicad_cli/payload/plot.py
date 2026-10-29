"""绘图输出载荷：Gerber / PDF / SVG / DXF，在 KiCad 自带解释器里跑。

不调官方二进制。PLOT_CONTROLLER 与 PCB_PLOT_PARAMS 就在 pcbnew 的绑定里，
能力本来就在进程内，只是没人把它做成给 Agent 用的接口。

一个必须说清的边界：这里**不改任何绘图语义**，参数全部取自板文件保存的
既有设置，我们只负责把它驱动起来并如实报告产出了什么。板厂拿到的 Gerber
必须和工程师在 KiCad 里点导出得到的一致，否则这层封装就是有害的。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kicad_lib as K  # noqa: E402
from plot_settings import copy_saved  # noqa: E402

# 面向 Agent 的格式名 -> pcbnew 的枚举名。用小写短名，不让调用方去记 KiCad 的常量。
FORMATS = {
    "gerber": "PLOT_FORMAT_GERBER",
    "pdf": "PLOT_FORMAT_PDF",
    "svg": "PLOT_FORMAT_SVG",
    "dxf": "PLOT_FORMAT_DXF",
}

# 最常用的一组层。给 --layers 留了口子，但默认这组能覆盖多数投板场景。
DEFAULT_LAYERS = "F.Cu,B.Cu,F.Paste,B.Paste,F.SilkS,B.SilkS,F.Mask,B.Mask,Edge.Cuts"


def layer_ids(board, pcbnew, names):
    out = []
    unknown = []
    for name in names:
        lid = board.GetLayerID(name)
        if lid < 0:
            unknown.append(name)
        else:
            out.append((name, lid))
    if unknown:
        K.fail(
            "E_VALIDATION",
            "板上没有这些层",
            {"unknown": unknown, "hint": "用 board audit 看这块板启用了哪些层"},
        )
    return out


def main():
    args = K.parse_args(sys.argv[1:], required=("fmt",))
    path = K.board_arg(args)
    fmt = str(args["fmt"]).lower()
    if fmt not in FORMATS:
        K.fail(
            "E_VALIDATION",
            "不支持的输出格式",
            {"got": fmt, "supported": sorted(FORMATS)},
        )

    pcbnew = K.import_pcbnew()
    board = K.load_board(pcbnew, path)
    names = [x.strip() for x in str(args.get("layers") or DEFAULT_LAYERS).split(",") if x.strip()]
    layers = layer_ids(board, pcbnew, names)

    outdir = args.get("out") or os.path.join(os.path.dirname(path), "fab")
    ctl = pcbnew.PLOT_CONTROLLER(board)
    opts = ctl.GetPlotOptions()
    try:
        copied = copy_saved(board.GetPlotOptions(), opts)
    except (AttributeError, TypeError, ValueError) as exc:
        K.fail(
            "E_VERIFY_INCOMPLETE",
            "Saved plot settings could not be preserved",
            {"reason": str(exc)},
        )
    # 全部沿用工程既有设置，只指定输出目录与格式。绘图语义不是我们该有主张的地方。
    opts.SetOutputDirectory(outdir)
    fmt_enum = getattr(pcbnew, FORMATS[fmt])
    opts.SetFormat(fmt_enum)
    os.makedirs(outdir, exist_ok=True)

    written = []
    for name, lid in layers:
        ctl.SetLayer(lid)
        ctl.OpenPlotfile(name.replace(".", "_"), fmt_enum, name)
        ok = ctl.PlotLayer()
        fname = ctl.GetPlotFileName()
        ctl.ClosePlot()
        if not ok:
            K.fail("E_IO", "绘图失败", {"layer": name, "file": fname})
        written.append(
            {
                "layer": name,
                "file": os.path.abspath(fname),
                "bytes": os.path.getsize(fname) if os.path.exists(fname) else 0,
            }
        )

    K.ok(
        {
            "board": path,
            "format": fmt,
            "output_dir": os.path.abspath(outdir),
            "files": written,
            "settings": {
                "source": "saved board SDK plot parameters",
                "copied_properties": copied,
                "native_comparison": "PCB_PLOT_PARAMS.IsSameAs before output/format overrides",
                "use_aux_origin": bool(opts.GetUseAuxOrigin()),
                "origin_nm": [
                    int(board.GetDesignSettings().GetAuxOrigin().x),
                    int(board.GetDesignSettings().GetAuxOrigin().y),
                ]
                if opts.GetUseAuxOrigin()
                else [0, 0],
            },
            "note": "保留板文件的 SDK 绘图设置，只指定输出目录、格式和所选层；"
            "启用辅助原点可能使图形超出固定页面。文件语法检查不证明完整可见，"
            "绘图仍需渲染验收。",
        }
    )


main()
