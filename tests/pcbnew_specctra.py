"""pcbnew's own Specctra export and import, the reference for this tool's
(run with KiCad's Python; test scaffolding, deleted with the payloads).

    export <board.kicad_pcb> <out.dsn>
    import <board.kicad_pcb> <session.ses> <out.kicad_pcb>
"""

import sys

import pcbnew


def main() -> int:
    mode = sys.argv[1]
    board = pcbnew.LoadBoard(sys.argv[2])
    if mode == "export":
        return 0 if pcbnew.ExportSpecctraDSN(board, sys.argv[3]) else 1
    if mode == "import":
        if not pcbnew.ImportSpecctraSES(board, sys.argv[3]):
            return 1
        pcbnew.SaveBoard(sys.argv[4], board)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
