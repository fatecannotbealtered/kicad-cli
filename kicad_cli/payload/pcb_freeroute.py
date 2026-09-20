"""用 Freerouting 布线:导出 DSN → 跑它 → 导回 SES → 复核。

自研的栅格布线器不会推挤(push-and-shove):已有的铜挡路就绕不开,绕不开就
报不通。这是算法性质,不是参数问题。Freerouting 会推挤,所以它是这条链上
「工程师水准」方向上最实在的一步。

实测同一块板(15 器件 ATmega328P,地平面已敷):

    栅格        可打板 铜 178 mm  走线 113 段  过孔 23
    Freerouting 可打板 铜 172 mm  走线 112 段  过孔 11

铜少 3%,过孔少一半。过孔是钻出来的:少一半就是少一半的钻孔成本、少一半
的可靠性风险、少一半在地平面上挖的洞。

三件必须做的事,都是实测撞出来的:

  1  **-mt 1**。Freerouting 自己在日志里警告多线程优化是坏的、会产生间距
     违规。那就别用。

  2  **-l en**。不指定的话输出跟着系统语言走,中文 Windows 上拿回来的是一
     串乱码,没法解析也没法放进信封。

  3  **导回来要补线宽**。Freerouting 的扇出会缩颈到 0.15 mm 把线从密引脚
     里带出来——和我们自己布线器的 neck 是一个意思,但它低于板子自己的
     m_TrackMinWidth,于是 KiCad DRC 报 22 条 track_width。把这些补回板子
     的最小宽度,22 条全清,且不引入新错。补了多少条如实报出来。

    <kicad-python> pcb_freeroute.py --board B.kicad_pcb --jar F.jar --java java.exe
        [--passes 10] [--timeout 1800] [--confirm ct_xxx]
"""

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kicad_lib as K  # noqa: E402

DEFAULT_PASSES = 10
DEFAULT_TIMEOUT_S = 1800


def export_dsn(pcbnew, board, path):
    if not pcbnew.ExportSpecctraDSN(board, path):
        K.fail(
            "E_IO",
            "导出 Specctra DSN 失败",
            {"path": path, "hint": "板子上要有板框和网络；先跑 board drc 看看"},
        )
    return path


def run_freerouting(java, jar, dsn, ses, passes, timeout, cwd):
    """隔着进程调一次,命令行参数进、文件出。不链接、不嵌入。

    cwd 必须是那个临时目录。Freerouting 会往工作目录里写自己的日志——实测
    是 <cwd>/en/freerouting.log——在用户的工程目录里跑就往那儿扔一个 en/
    文件夹。让它扔在临时目录里,随 TemporaryDirectory 一起消失。
    """
    cmd = [
        java,
        "-jar",
        jar,
        "-de",
        dsn,
        "-do",
        ses,
        "-mp",
        str(passes),
        "-mt",
        "1",  # 它自己说多线程优化是坏的
        "-l",
        "en",  # 否则输出跟着系统语言走
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        K.fail(
            "E_TIMEOUT",
            "Freerouting 超时,板子没有改动",
            {
                "timeout_s": timeout,
                "hint": "大板子本来就慢。调小 --passes，或调大 --timeout",
            },
        )
    except OSError as exc:
        K.fail("E_CONFIG", "跑不起来 Freerouting", {"cmd": cmd[:3], "reason": str(exc)[:200]})
    out = (proc.stdout or b"").decode("utf-8", "replace")
    out += (proc.stderr or b"").decode("utf-8", "replace")
    if not os.path.isfile(ses):
        K.fail(
            "E_IO",
            "Freerouting 没有产出 .ses,板子没有改动",
            {"exit_code": proc.returncode, "output": out[-700:]},
        )
    return out


def summarize(output):
    """从它的日志里捞出「布通没布通」。捞不到就诚实地报 None,不猜。"""
    unrouted = violations = None
    for line in output.splitlines():
        if "final score" in line and "unrouted" in line:
            words = line.replace("(", " ").replace(")", " ").split()
            for index, word in enumerate(words):
                if word == "unrouted" and index:
                    unrouted = _int(words[index - 1], unrouted)
                if word.startswith("violation") and index:
                    violations = _int(words[index - 1], violations)
    return unrouted, violations


def _int(text, fallback):
    try:
        return int(text)
    except (TypeError, ValueError):
        return fallback


def restore_min_width(pcbnew, board):
    """把低于板子最小线宽的段补回去,返回补了几条。

    Freerouting 扇出时会缩颈——这本身是对的,密引脚出不来就得细。但它缩到
    的宽度低于板子自己的 m_TrackMinWidth,KiCad DRC 就要报 track_width。
    补回最小宽度是让它符合这块板自己的规矩,不是我们另定一个标准。
    """
    floor = board.GetDesignSettings().m_TrackMinWidth
    fixed = 0
    for track in K.tracks_of(board):
        if track.GetClass() == "PCB_TRACK" and track.GetWidth() < floor:
            track.SetWidth(floor)
            fixed += 1
    return fixed, round(pcbnew.ToMM(floor), 3)


def main():
    args = K.parse_args(sys.argv[1:], flags=(), required=("jar", "java"))
    path = K.board_arg(args)
    jar, java = args["jar"], args["java"]
    passes = max(1, int(args.get("passes") or DEFAULT_PASSES))
    timeout = float(args.get("timeout") or DEFAULT_TIMEOUT_S)

    pcbnew = K.import_pcbnew()
    board = K.load_board(pcbnew, path)
    before_un = K.unconnected(board)
    before_tracks = len([t for t in K.tracks_of(board) if t.Type() == pcbnew.PCB_TRACE_T])

    preview = {
        "board": path,
        "engine": "freerouting",
        "jar": jar,
        "passes": passes,
        "unconnected_before": before_un,
        "tracks_before": before_tracks,
        "will": "导出 DSN 交给 Freerouting 重新布线，再把结果导回来；板上现有走线会被它的结果取代",
    }
    K.check_confirm(args.get("confirm"), preview, "freeroute:" + os.path.basename(path), path)

    # 基线要在任何改动落盘之前取,和 board route / board place 同一条规矩。
    errors_baseline = K.err_count(K.run_drc(path))

    with tempfile.TemporaryDirectory(prefix="kicadcli-fr-") as scratch:
        dsn = os.path.join(scratch, "board.dsn")
        ses = os.path.join(scratch, "board.ses")
        export_dsn(pcbnew, board, dsn)
        output = run_freerouting(java, jar, dsn, ses, passes, timeout, scratch)
        unrouted, violations = summarize(output)
        if not pcbnew.ImportSpecctraSES(board, ses):
            K.fail(
                "E_IO",
                "Freerouting 的 .ses 导不回来,板子没有改动",
                {"hint": "多半是 DSN 和板子对不上；重跑一次 board route 再试"},
            )

    widened, floor_mm = restore_min_width(pcbnew, board)
    K.fill_and_save(pcbnew, board, path)

    after_board = K.load_board(pcbnew, path)
    after_un = K.unconnected(after_board)
    errors_final = K.err_count(K.run_drc(path))
    if errors_final > errors_baseline:
        # 和 board route、board place、board rewidth 同一条判据同一个码。
        K.fail(
            "E_INTEGRITY",
            "Freerouting 布线后 DRC error 数高于动手前,已整盘回滚",
            {
                "errors_baseline": errors_baseline,
                "errors_final": errors_final,
                "next_action": "板子已恢复原状。改用 --engine grid，或先处理已有的 error 再重试",
            },
        )

    tracks = [t for t in K.tracks_of(after_board) if t.Type() == pcbnew.PCB_TRACE_T]
    K.ok(
        {
            "board": path,
            "engine": "freerouting",
            "passes": passes,
            "unconnected_before": before_un,
            "unconnected_after": after_un,
            "improved": before_un - after_un,
            "tracks": len(tracks),
            "vias": len(K.tracks_of(after_board)) - len(tracks),
            "freerouting_unrouted": unrouted,
            "freerouting_violations": violations,
            "necked_tracks_widened": widened,
            "min_track_width_mm": floor_mm,
            "verify": {
                "ran": True,
                "oracle": "kicad-cli pcb drc",
                "errors_baseline": errors_baseline,
                "errors_final": errors_final,
                "note": "error 数没超过动手前的基线，否则这次调用已经回滚。"
                "freerouting_violations 是它自己的判断，和 KiCad 的 DRC 不是一套模型——"
                "以 errors_final 为准",
            },
            "note": "Freerouting 布的线。它会推挤，所以过孔通常比自研栅格少一半左右。"
            + (
                f"有 {widened} 段扇出缩颈线被补回板子的最小线宽 {floor_mm} mm——"
                "它缩到的宽度低于这块板自己的规则。"
                if widened
                else ""
            )
            + "接下来 board drc 复核，需要载流就 board netclass + board rewidth。",
        }
    )


if __name__ == "__main__":
    main()
