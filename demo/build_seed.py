"""生成演示用的「空板」种子工程。

演示要从一块空板开始长出整块 PCB。但 IPC 没有任何新建/打开文档的命令
（59 个已注册 handler 里一个都没有），所以空板必须离线先做好，由人手动打开一次。

**种子里必须保留什么，是这段代码最关键的地方。** 层表、网络类、自定义设计规则
这三样 IPC 都写不了：
  - 层表：没有可用的 SetBoardEnabledLayers 写法能保住 4 个自定义显示名，
    而且它会不可撤销地删掉层上内容，演示脚本里绝对不能调
  - 叠层：UpdateBoardStackup 在 proto 里有、在 _pcbnew.dll 里没实现
  - .kicad_dru 的自定义规则：完全没有对应命令

所以这些留在种子文件里，脚本只负责「往板上放东西」。哪些是预置的、哪些是当场
建的，在输出里写清楚——演示的可信度全在这条界线上。
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import BOARD, OUT_DIR as OUT, PRETTY, SRC_DIR as SRC, STEM  # noqa: E402

# 顶层要剥掉的对象：这些正是演示要当场长出来的东西。
# 两种种子。默认留着封装——原因写在下面。
#
# 「空到只剩层表」原本是首选：ParseAndCreateItemsFromString 能直接吃 s-expr，
# 而 113 个封装的几何是内联的，切块喂进去就行。实测下来这个命令在 KiCad 10.0.6
# 里**注册了但没实现**：喂它自己 SaveSelectionToString 吐出来的字符串，
# 返回 created_items=0、status=IRS_UNKNOWN，板上什么都没多。
#
# 封装仍然能建（create_items 收 FootprintInstance，实测成功），但得把焊盘和图元
# 从 s-expr 逐个翻译成 protobuf：4 种焊盘形状、3 种焊盘类型、5 种图元。翻得动，
# 但任何一处翻错都会在最后那次 DRC 上当场炸掉。
#
# 而「113 个器件从空板长出来」恰恰是诚实故事最弱的一幕：106 个位置是接手前就有的。
# 所以默认留着封装，演示真正属于机器的那部分——900 段走线里没有一段是原来的。
STRIP_COPPER = (
    "segment",
    "via",
    "zone",
    "gr_rect",
    "gr_line",
    "gr_text",
    "gr_poly",
    "gr_circle",
    "gr_arc",
    "dimension",
    "group",
    "image",
)
STRIP_ALL = ("footprint",) + STRIP_COPPER


def top_level_blocks(text: str):
    """按括号配平切出顶层块。正则做不了这件事——铺铜里有六万个坐标对。"""
    start = text.index("(kicad_pcb")
    i = text.index("(", start + 1)
    depth = 0
    blocks = []
    head_end = i
    while i < len(text):
        ch = text[i]
        if ch == '"':
            i += 1
            while i < len(text) and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if ch == "(":
            if depth == 0:
                block_start = i
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                blocks.append((block_start, i + 1))
            elif depth < 0:
                break
        i += 1
    return head_end, blocks


def tag_of(text: str, span) -> str:
    m = re.match(r"\(\s*([A-Za-z_0-9]+)", text[span[0] : span[0] + 40])
    return m.group(1) if m else ""


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--bare", action="store_true", help="连封装也剥掉（需要封装翻译器，尚未实现）")
    a = ap.parse_args()
    strip = STRIP_ALL if a.bare else STRIP_COPPER

    board = BOARD
    text = board.read_text(encoding="utf-8")
    head_end, blocks = top_level_blocks(text)

    kept, dropped = [], {}
    for span in blocks:
        t = tag_of(text, span)
        if t in strip:
            dropped[t] = dropped.get(t, 0) + 1
        else:
            kept.append(span)

    OUT.mkdir(parents=True, exist_ok=True)
    body = "\n".join("\t" + text[a:b] for a, b in kept)
    seed = text[:head_end].rstrip() + "\n" + body + "\n)\n"
    (OUT / f"{STEM}.kicad_pcb").write_text(seed, encoding="utf-8")

    # 规则与库跟着走：这些是 IPC 写不了的，必须预置
    carried = []
    for name, dest in (
        (f"{BOARD.stem}.kicad_pro", f"{STEM}.kicad_pro"),
        (f"{BOARD.stem}.kicad_dru", f"{STEM}.kicad_dru"),
        ("fp-lib-table", "fp-lib-table"),
        ("sym-lib-table", "sym-lib-table"),
    ):
        s = SRC / name
        if s.exists():
            shutil.copy2(s, OUT / dest)
            carried.append(dest)
    pretty = SRC / PRETTY if PRETTY else None
    if pretty and pretty.is_dir():
        shutil.copytree(pretty, OUT / PRETTY, dirs_exist_ok=True)
        carried.append(f"{PRETTY}/")

    layers = re.search(r"\(layers\s*(.*?)\n\t\)", seed, re.S)
    n_layers = len(re.findall(r"^\s*\(\d+ ", layers.group(1), re.M)) if layers else 0

    print(f"种子空板: {OUT / (STEM + '.kicad_pcb')}")
    print(f"  {len(seed):,} 字节（原板 {len(text):,}）")
    print(f"  保留的顶层块: {[tag_of(text, s) for s in kept]}")
    print(f"  层表: {n_layers} 层（含自定义显示名，IPC 改不了，必须预置）")
    print(f"  模式: {'空板(含封装翻译)' if a.bare else '保留封装，只剥铜'}")
    print(f"  随行文件: {carried}")
    print()
    print("剥掉的（这些是演示要当场长出来的）:")
    for t, n in sorted(dropped.items(), key=lambda kv: -kv[1]):
        print(f"  {t:<12} {n}")


if __name__ == "__main__":
    sys.exit(main())
