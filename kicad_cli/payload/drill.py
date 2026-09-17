"""钻孔文件载荷：Excellon 钻带 + 钻孔图 + 报告，在进程内出。

没有它，`fab gerber` 那一组产出的包是不完整的——板厂拿到只有铜层的 Gerber，
什么也做不了。这是制造包里最容易被忘、又最不能少的一件。

绘图那条路能把参数全部沿用 .kicad_pro 的既有设置，钻孔这条不行：钻孔对话框的
选择并不以可读的形式存在工程文件里。所以这里的做法是——**把选择摆到台面上**：
用板厂通吃的默认值（公制、PTH/NPTH 分开、十进制、板原点），每一项都作为参数
可改，并且原样写进输出。不假装读到了不存在的设置，也不藏着替用户做的决定。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kicad_lib as K  # noqa: E402

MAP_FORMATS = {"pdf": "PLOT_FORMAT_PDF", "gerber": "PLOT_FORMAT_GERBER", "svg": "PLOT_FORMAT_SVG"}


def main():
    args = K.parse_args(sys.argv[1:], flags=("merge", "inch", "aux_origin"))
    path = K.board_arg(args)
    map_fmt = str(args.get("map") or "pdf").lower()
    if map_fmt not in MAP_FORMATS and map_fmt != "none":
        K.fail(
            "E_VALIDATION",
            "不支持的钻孔图格式",
            {"got": map_fmt, "supported": sorted(MAP_FORMATS) + ["none"]},
        )

    pcbnew = K.import_pcbnew()
    board = K.load_board(pcbnew, path)

    outdir = args.get("out") or os.path.join(os.path.dirname(path), "fab")
    os.makedirs(outdir, exist_ok=True)

    metric = not args.get("inch")
    merge = bool(args.get("merge"))
    # 钻孔坐标的零点。板厂按哪个原点算，必须和 Gerber 一致，否则钻孔会整体偏移。
    if args.get("aux_origin"):
        origin = board.GetDesignSettings().GetAuxOrigin()
        origin_name = "aux"
    else:
        origin = pcbnew.VECTOR2I(0, 0)
        origin_name = "absolute"

    writer = pcbnew.EXCELLON_WRITER(board)
    writer.SetOptions(False, False, origin, merge)
    # 十进制、不压缩前后导零：可读，且不依赖板厂猜小数位。
    writer.SetFormat(metric, writer.DECIMAL_FORMAT, 3, 3)
    writer.SetRouteModeForOvalHoles(True)
    if map_fmt != "none":
        writer.SetMapFileFormat(getattr(pcbnew, MAP_FORMATS[map_fmt]))

    seen = set(os.listdir(outdir))
    writer.CreateDrillandMapFilesSet(outdir, True, map_fmt != "none")

    report = os.path.join(outdir, os.path.splitext(os.path.basename(path))[0] + "-drl.rpt")
    writer.GenDrillReportFile(report)

    produced = []
    for name in sorted(os.listdir(outdir)):
        full = os.path.join(outdir, name)
        if name in seen or not os.path.isfile(full):
            continue
        produced.append({"file": os.path.abspath(full), "bytes": os.path.getsize(full)})

    if not produced:
        K.fail(
            "E_IO",
            "没有产出任何钻孔文件",
            {"output_dir": os.path.abspath(outdir), "hint": "这块板可能一个孔都没有"},
        )

    # 数一遍板上到底有多少孔，好让调用方拿产出对账，而不是只看到「成功」两个字。
    pth = npth = vias = 0
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetDrillSizeX() <= 0:
                continue
            if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                npth += 1
            else:
                pth += 1
    for t in board.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA):
            vias += 1

    # 拿 KiCad 自己写的报告跟我们数出来的对账。一个静默漏孔的钻带比没有钻带更危险：
    # 板厂照着做，板子回来才发现少钻，而 Gerber 看上去一切正常。
    reported = {}
    try:
        with open(report, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                low = line.lower()
                if "total plated holes count" in low:
                    reported["plated"] = int(line.split()[-1])
                elif "total unplated holes count" in low:
                    reported["unplated"] = int(line.split()[-1])
    except OSError:
        reported = {}

    counted = {"plated": pth + vias, "unplated": npth}
    agrees = bool(reported) and reported == counted
    if reported and not agrees:
        K.fail(
            "E_INTEGRITY",
            "钻孔数量与板上实际孔数对不上，产出不可信",
            {"kicad_report": reported, "counted_from_board": counted, "output_dir": outdir},
        )

    K.ok(
        {
            "board": path,
            "output_dir": os.path.abspath(outdir),
            "files": produced,
            "reconciled": {
                "kicad_report": reported or "报告文件未能解析",
                "counted_from_board": counted,
                "agrees": agrees,
            },
            "settings": {
                "units": "mm" if metric else "inch",
                "origin": origin_name,
                "pth_npth": "merged" if merge else "separate",
                "map_format": map_fmt,
                "number_format": "decimal, 3.3",
                "oval_holes": "route mode",
            },
            "holes": {"plated_pads": pth, "non_plated_pads": npth, "vias": vias},
            "note": "钻孔参数不存在于工程文件中，以上为本次实际使用的取值，"
            "全部可由参数覆盖；板厂若要求英制或辅助原点，请显式指定",
        }
    )


main()
