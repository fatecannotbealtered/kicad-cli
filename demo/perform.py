"""把这块板一幕一幕画到屏幕上。

    python demo/perform.py --rehearse     排练：不停顿，只验证流程跑得通
    python demo/perform.py                正式：按录屏节奏

前提：KiCad 里已经打开 build_seed.py 生成的那块种子板（路径见 demo/config.py，
有 113 个封装、没有一丝铜）。IPC 没有任何打开文档的命令，这一步只能人点一次。

**这段演示是什么、不是什么**

屏幕上出现的每一段走线的两个端点，都来自两周前算完的那块板。这里做的是「按顺序
建回去，让 KiCad 渲染和校验」。分幕和停顿是编排——机器裸跑这一整块只要几十秒。

当场真算的只有三件，输出里单独列出来，不和前面的搬运混着说：
  1. 铺铜填充——源板里存着算好的 24 个铜岛、62562 个顶点，这里一概不灌，
     只给轮廓，让 KiCad 自己填
  2. 连通性——飞线数由 KiCad 重算
  3. DRC——IPC 没有读 DRC 的命令，收尾必须人点菜单

能理直气壮说的是另一件事：接手时这块板只有 85 段走线，现在 900 段里
**没有一段和原来相同**。布线全是机器产出的，只不过那发生在两周里，不是这几分钟。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / ".vendor"))
sys.path.insert(0, str(REPO / "demo"))

import config  # noqa: E402
import order as O  # noqa: E402
import translate as T  # noqa: E402
from kipy import KiCad  # noqa: E402

EXPECT = config.STEM
# 一次 IPC 往返实测 0~16 ms，多数在 1 ms 以下，所以节奏完全由 sleep 决定，
# 不受协议限制。一条一条画得起。
PER_TRACK_S = 0.03  # 每段走线之间的停顿
PER_VIA_S = 0.012  # 过孔密，快一点
GAP_S = 0.25  # 换网络时的停顿，给镜头和口播留空


def patient(fn, tries=12, wait=0.4):
    """重填铺铜之后 KiCad 会忙一阵，这期间 IPC 一律回 "KiCad is busy"。

    不是错误，是它在算。等一下再问就是了——但不能无限等，所以有次数上限。
    """
    for i in range(tries):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            if "busy" not in str(exc).lower() or i == tries - 1:
                raise
            time.sleep(wait)
    return None


def frame_board(kicad) -> None:
    """把整块板放进画面，然后**不再动**。

    第一版是每画一个网络就 zoomFitSelection 一次。想法是「带着观众的眼睛走」，
    实际效果是整块板一直在飘：每个网络位置不同、尺寸不同，镜头就不停跳动和缩放，
    看的人找不到参照物。

    固定画面反而更有力——板子在一个不动的框里一点点长满，观众看得见进度，
    也看得清哪一块先有了。要看细节，录完再剪特写，不该靠镜头乱跑来代替。
    """
    try:
        kicad.run_action("common.Control.zoomFitObjects")
    except Exception:  # noqa: BLE001, S110
        pass


def look(kicad, board, items) -> None:
    """只做高亮，不动镜头。"""
    try:
        board.clear_selection()
        if items:
            board.add_to_selection(items)
    except Exception:  # noqa: BLE001
        pass


def scene(kicad, board, title, items, slow, made, total):
    if not items:
        return made
    commit = board.begin_commit()
    got = board.create_items(items)
    board.push_commit(commit, f"AI 绘制：{title}")
    made += len(got)
    print(f"  {title:<18} +{len(got):>3}   累计 {made:>4}/{total}", flush=True)
    if slow:
        look(kicad, board, got)
        time.sleep(0.4 * slow)
    return made


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rehearse", action="store_true", help="排练：不停顿")
    ap.add_argument("--pace", type=float, default=1.0, help="节奏倍率，越大越慢")
    ap.add_argument("--reset", action="store_true", help="先回到空板（重新读磁盘上的种子）")
    args = ap.parse_args()
    slow = 0.0 if args.rehearse else args.pace

    kicad = KiCad()
    board = kicad.get_board()
    if args.reset:
        # 不用关软件重开。revert 读回磁盘上的文件——只要种子文件是干净的，
        # 板子就回到起点，KiCad 全程开着。排练能反复来，录制当天一次都不用关。
        import subprocess

        subprocess.run(
            [sys.executable, str(REPO / "demo" / "build_seed.py")], capture_output=True, check=False
        )
        board.revert()
        board = patient(kicad.get_board)
        print(f"已回到空板：封装 {len(board.get_footprints())}，铜 0", flush=True)
        print(flush=True)
    if EXPECT not in board.name:
        print(f"停：KiCad 里开的是 {board.name}，不是 {EXPECT}。")
        return 2
    if board.get_tracks() or board.get_vias():
        print(f"停：板上已经有铜（走线 {len(board.get_tracks())}）。请重新打开种子文件。")
        return 2
    if len(board.get_footprints()) != 113:
        print(f"停：封装数是 {len(board.get_footprints())}，期望 113。种子文件不对。")
        return 2

    live = {n.name: n for n in board.get_nets()}
    root = T.load()
    outline = T.board_outline(root)
    by_layer: dict[str, list] = {}
    for layer, t in T.tracks(root, live):
        by_layer.setdefault(layer, []).append(t)
    vias = T.vias(root, live)
    zones = T.zones(root, live)
    texts = T.texts(root)
    total = (
        len(outline) + sum(len(v) for v in by_layer.values()) + len(vias) + len(zones) + len(texts)
    )

    t0 = time.monotonic()
    print(f"开始：{total} 个对象要建\n", flush=True)
    made = 0

    made = scene(kicad, board, "板框", outline, slow, made, total)
    # 板框一出来就定好画面，此后整段不再动镜头
    frame_board(kicad)
    if slow:
        time.sleep(0.6 * slow)

    # 走线：按网络一条一条画。粗的先来——先把供电通路铺出去，再走信号。
    groups = O.by_net([t for _layer, t in T.tracks(root, live)])
    # 每个网络一个提交。理由有两条：撤销历史会读成「AI 绘制：VRAW」「AI 绘制：
    # DC_FUSED」，一眼看得出画了什么；而且脚本万一中途挂掉，丢的最多是一个网络，
    # 不是整块板的走线——共用一个大提交时试过一次，管道一断，四百段全没了。
    for name, segs in groups:
        commit = board.begin_commit()
        if slow:
            look(kicad, board, board.create_items(segs[:1]))
            made += 1
            rest = segs[1:]
            time.sleep(GAP_S * slow)
        else:
            rest = segs
        for seg in rest:
            board.create_items(seg)
            made += 1
            if slow:
                time.sleep(PER_TRACK_S * slow)
        board.push_commit(commit, f"AI 绘制：{name or '未命名网络'}")
        print(f"  {name or '(无网络)':<26} {len(segs):>3} 段   累计 {made:>4}/{total}", flush=True)

    commit = board.begin_commit()
    for v in vias:
        board.create_items(v)
        made += 1
        if slow:
            time.sleep(PER_VIA_S * slow)
    board.push_commit(commit, f"AI 绘制：{len(vias)} 个过孔")
    print(f"  {'过孔':<26} {len(vias):>3} 个   累计 {made:>4}/{total}", flush=True)

    for layer, net, z in zones:
        made = scene(kicad, board, f"铺铜 {layer} {net}", [z], slow, made, total)

    made = scene(kicad, board, "丝印", texts, slow, made, total)

    build = time.monotonic() - t0
    print(f"\n建完 {made} 个对象，{build:.1f}s", flush=True)

    # —— 到这里为止都是把已知结果搬过去。下面才是当场算的。——
    print("\n当场计算：", flush=True)
    t1 = time.monotonic()
    board.refill_zones(block=True)
    print(f"  铺铜填充    {time.monotonic() - t1:.2f}s   铜岛与顶点由 KiCad 现算", flush=True)

    patient(board.clear_selection)
    frame_board(kicad)

    after = patient(kicad.get_board)
    print(
        f"  板上现在    封装 {len(after.get_footprints())}  走线 {len(after.get_tracks())}  "
        f"过孔 {len(after.get_vias())}  铺铜 {len(after.get_zones())}",
        flush=True,
    )
    print(f"\n全程 {time.monotonic() - t0:.1f}s", flush=True)
    print("  收尾由人做：看飞线数，然后点菜单跑一次 DRC", flush=True)
    print("  （IPC 没有读 DRC 结果的命令，这一步没法也不该由脚本代劳）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
