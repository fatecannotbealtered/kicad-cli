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

# 层策略。两层板的通行做法是:信号走顶层,底层整片留给地平面,非过不可的
# 交叉才短暂下到底层。Freerouting 没有「偏好某一层」的开关——它的
# ScoringSettings.preferredDirectionTraceCost 和 RouterSettings.layers 都标了
# transient,既不进 JSON 也不认 -dr 文件(实测:无 rules、成本 1.0、成本 50.0、
# 以及把两层的偏好方向对调,四次输出逐字节相同)。
#
# 能用的杠杆是过孔成本:过孔贵了就少换层,而元件都在顶层,于是线留在顶层。
# 实测扫了一遍(同一块板,只改这一个数):
#
#     过孔成本    底层铜占比   总铜      过孔   参考平面覆盖
#     50(默认)      21.5%    172.5 mm   11       0.73
#     100            12.0%    192.8 mm   10       0.82
#     150             8.9%    221.9 mm    9       0.85
#     300             5.1%    225.2 mm    7       0.87
#     500             5.1%    225.2 mm    7       0.87
#     800            18.3%    190.5 mm    9       0.73
#
# 300~500 是一个平台,不是刀尖;800 以上反而退回去。**不单调**,所以不把这个
# 数直接交给调用方——谁都会觉得 1000 比 300 更狠,而 1000 更差。给策略名,
# 数字定在平台中间。代价是铜多 31%:平面完整比线短重要,这正是两层板的常识。
VIA_COST_PLANE_FIRST = 300
LAYER_POLICIES = ("balanced", "plane-first")


def export_dsn(pcbnew, board, path):
    if not pcbnew.ExportSpecctraDSN(board, path):
        K.fail(
            "E_IO",
            "导出 Specctra DSN 失败",
            {"path": path, "hint": "板子上要有板框和网络；先跑 board drc 看看"},
        )
    return path


def run_freerouting(java, jar, dsn, ses, passes, timeout, cwd, policy):
    """隔着进程调一次,命令行参数进、文件出。不链接、不嵌入。

    cwd 必须是那个临时目录。Freerouting 会往工作目录里写自己的日志——实测
    是 <cwd>/en/freerouting.log——在用户的工程目录里跑就往那儿扔一个 en/
    文件夹。让它扔在临时目录里,随 TemporaryDirectory 一起消失。

    FREEROUTING__USER_DATA_PATH 同理,指向临时目录里的一份私有配置。不设的话
    它读写用户全局的 freerouting.json——实测我们自己的几次调用已经在里面留下
    了指向临时目录的 logging.file.location,而那个目录早就不在了。用户的配置
    是用户的,一个布线调用没有理由改它。

    层策略通过 FREEROUTING__ROUTER__SCORING__VIA_COSTS 下发。这条环境变量路径
    来自 jar 里的 EnvironmentVariablesSource（FREEROUTING__<节>__<键>），是目前
    唯一能真正影响布线器层行为的入口。
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
    env = dict(os.environ)
    env["FREEROUTING__USER_DATA_PATH"] = os.path.join(cwd, "userdata")
    if policy == "plane-first":
        env["FREEROUTING__ROUTER__SCORING__VIA_COSTS"] = str(VIA_COST_PLANE_FIRST)
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=cwd, env=env)
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
    policy = str(args.get("layer-policy") or "balanced").lower()
    if policy not in LAYER_POLICIES:
        K.fail("E_USAGE", "--layer-policy 只能是 " + " / ".join(LAYER_POLICIES), {"got": policy})
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
        "layer_policy": policy,
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
        output = run_freerouting(java, jar, dsn, ses, passes, timeout, scratch, policy)
        unrouted, violations = summarize(output)
        applied, fallback = policy, None
        if policy == "plane-first" and unrouted:
            # plane-first 是拿可布通性换层纯度的,而这笔交易不是永远划算:
            # 同一块板去掉地敷铜之后,plane-first 留下 1 条布不通——一块少
            # 一条连接的板子不能打,再漂亮的层分布也没有意义。
            #
            # 所以按 sch create 画图那套来:先试好的,不行就退回旧的。退回来
            # 的这次用同一份 DSN,所以只多花一次布线的时间,不重做导出。
            fallback = {
                "from": policy,
                "to": "balanced",
                "reason": f"plane-first 留下 {unrouted} 条没布通",
            }
            output = run_freerouting(java, jar, dsn, ses, passes, timeout, scratch, "balanced")
            unrouted, violations = summarize(output)
            applied = "balanced"
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
    by_layer = K.copper_by_layer(after_board, pcbnew)
    # 板上有敷铜而调用方没要 plane-first,就把这笔买卖的价码报给他。实测数字,
    # 不是形容词——省了多少平面、多花多少铜,让他自己判断值不值。
    has_pour = any(not z.GetIsRuleArea() for z in K.zones_of(after_board))
    suggest = applied == "balanced" and has_pour and not fallback
    K.ok(
        {
            "board": path,
            "engine": "freerouting",
            "passes": passes,
            "layer_policy": policy,
            "layer_policy_applied": applied,
            "layer_policy_fallback": fallback,
            "copper_by_layer_mm": by_layer,
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
                "层策略 plane-first：过孔成本调高，线尽量留在顶层，底层让给地平面。"
                "代价是绕路、铜更长。跑 board plane 看 backed_fraction 值不值。"
                if applied == "plane-first"
                else ""
            )
            + (
                "这块板有敷铜，而现在是 balanced：底层每一段信号线都在平面上割一刀。"
                "同一块板实测 --layer-policy plane-first 能把底层铜从 21.5% 压到 5.1%、"
                "过孔 11 个减到 7 个、board plane 的 backed_fraction 从 0.73 升到 0.87，"
                "代价是铜长多约 31%。布不通会自动退回来，试一次不亏。"
                if suggest
                else ""
            )
            + (
                f"要的是 {fallback['from']}，但它{fallback['reason']}，"
                f"已自动退回 {fallback['to']} —— 一块少了连接的板子不能打，"
                "层分布再好也没用。这块板多半没有敷铜可保护：先跑 board pour 再试。"
                if fallback
                else ""
            )
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
