"""读回校验载荷：用 KiCad 自己的解析器确认板文件被正确理解。

回填 path 是我们在文本层面做的，为的是不把一块 KiCad 9 存的板子顺手升级成
KiCad 10 的格式——用户要的是恢复链接，不是迁移文件格式。代价是：写出来的
字节得由别人来判卷。

所以这里只做两件事，都不写盘：让 pcbnew 加载一次，把它读到的 ref -> path
如实报回；顺带点一次各类对象的数量。加载成功本身就是一次格式校验——KiCad
读不懂的东西，它会在这一步就报错，而不是等到工程师打开板子的时候。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kicad_lib as K  # noqa: E402


def main():
    args = K.parse_args(sys.argv[1:])
    path = K.board_arg(args)

    pcbnew = K.import_pcbnew()
    board = K.load_board(pcbnew, path)

    links = {}
    duplicates = []
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        value = str(fp.GetPath().AsString())
        if ref in links and links[ref] != value:
            duplicates.append(ref)
        links[ref] = value

    K.ok(
        {
            "board": path,
            "links": links,
            "duplicate_references": sorted(set(duplicates)),
            "census": {
                "footprints": len(board.GetFootprints()),
                "tracks": len(board.GetTracks()),
                "zones": len(board.Zones()),
                "drawings": len(board.GetDrawings()),
                "nets": board.GetNetCount(),
            },
            "note": "由 pcbnew 加载并读回，未作任何修改；加载成功即表示文件格式可被 KiCad 解析",
        }
    )


main()
