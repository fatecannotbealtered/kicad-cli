"""``board move``, in this process: put parts where they are told to go.

A port of `payload/pcb_place.py`, which ran on pcbnew; what it writes and what
it reports are identical on KiCad's demo boards (`tests/test_native_move.py`).

Some routing problems are placement problems. When the order of the parts
on the board does not match the order of the pins they connect to, the
tracks have to cross, and no router untangles that. This does the smallest
thing: move the named parts to the given coordinates, then report whose
courtyards now overlap. Where a part goes is the caller's decision.

A footprint's pads, drawings and text are stored relative to it, so a move
rewrites one thing in the file: the footprint's own `(at x y)`. Everything
else keeps its bytes.
"""

from __future__ import annotations

import os

from .. import envelope
from ..fileformat import geometry
from ..fileformat.board import Board, Footprint, shape_points, stroke_width
from ..fileformat.sexpr import number
from . import load_board, write

NM = 1_000_000
COURTYARDS = ("F.CrtYd", "B.CrtYd")
COURTYARD_SHRINK = 5_000  # nm: see courtyard_box


def mm(value: float) -> float:
    return value / NM


def from_mm(value: float) -> int:
    """Millimetres to nanometres as the payload converted them: truncated,
    not rounded -- 1.005 mm lands on 1.004999. Kept so the port is provable;
    listed in DEVELOPMENT_STATUS.md to be fixed on its own."""
    return int(float(value) * float(NM))


def parse_moves(text: str) -> dict[str, tuple[float, float]]:
    moves = {}
    for item in text.split(";"):
        item = item.strip()
        if not item:
            continue
        try:
            ref, xy = item.split(":")
            x, y = xy.split(",")
            moves[ref.strip()] = (float(x), float(y))
        except ValueError:
            envelope.fail(
                "E_USAGE",
                "--moves is REF:x,y separated by ';'",
                {
                    "param": "moves",
                    "got": item,
                    "format": 'REF:x,y separated by ";" -- for example "U1:120.5,60.0; C3:118,62"',
                },
            )
    return moves


def courtyard_box(fp: Footprint) -> tuple[float, float, float, float]:
    """The courtyard's box in mm, front layer first; failing that, the box
    around the footprint's copper and drawings without its text.

    What pcbnew reports for a courtyard is not the box of the lines as drawn:
    each side lies further out by the line's width less 5 um -- 45 um for the
    usual 0.05 mm line, 95 for 0.10, 115 for 0.12, measured on every footprint
    of five demo boards. Where a courtyard has arcs or circles pcbnew's own
    approximation of them adds a few micrometres more, which this does not
    reproduce.
    """
    for layer in COURTYARDS:
        boxes = []
        for node in fp.node.lists():
            if not node.head.startswith("fp_"):
                continue
            found = node.find("layer")
            if found is None or found.value(1) != layer:
                continue
            points = shape_points(node)
            if not points:
                continue
            grow = stroke_width(node) - COURTYARD_SHRINK
            x1, y1, x2, y2 = geometry.bbox(geometry.place(points, *fp.position, fp.angle))
            boxes.append((x1 - grow, y1 - grow, x2 + grow, y2 + grow))
        if boxes:
            return (
                mm(min(b[0] for b in boxes)),
                mm(min(b[1] for b in boxes)),
                mm(max(b[2] for b in boxes)),
                mm(max(b[3] for b in boxes)),
            )
    box = fp.bbox() or (*fp.position, *fp.position)
    return (mm(box[0]), mm(box[1]), mm(box[2]), mm(box[3]))


def overlaps(a, b, gap=0.0) -> bool:
    return not (
        a[2] + gap <= b[0] or b[2] + gap <= a[0] or a[3] + gap <= b[1] or b[3] + gap <= a[1]
    )


def run(path: str, moves_text: str, confirm: str | None) -> None:
    write.start()
    board = load_board(path)
    moves = parse_moves(moves_text)

    fps = {fp.reference: fp for fp in board.footprints}
    missing = [ref for ref in moves if ref not in fps]
    if missing:
        envelope.fail("E_NOT_FOUND", "板上没有这些位号", {"refs": missing})

    plan = []
    for ref, (x, y) in sorted(moves.items()):
        fp = fps[ref]
        plan.append(
            {
                "ref": ref,
                "from": [round(mm(fp.position[0]), 2), round(mm(fp.position[1]), 2)],
                "to": [x, y],
                "rot_deg": None,
            }
        )
    preview = {"board": path, "moves": plan, "will": f"搬动 {len(plan)} 个器件；搬完检查庭院重叠"}
    envelope.check_confirm(
        confirm,
        f"pcb_place:{','.join(sorted(moves))}:{os.path.basename(path)}",
        preview,
        path,
    )

    for ref, (x, y) in moves.items():
        move_footprint(board, fps[ref], from_mm(x), from_mm(y))

    # Courtyards after the move. An overlap is not always wrong -- parts can be
    # stacked on purpose -- but one this move made has to be seen.
    boxes = [(fp.reference, courtyard_box(fp)) for fp in board.footprints]
    clash = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if boxes[i][0] not in moves and boxes[j][0] not in moves:
                continue  # neither moved: not this command's doing
            if overlaps(boxes[i][1], boxes[j][1]):
                clash.append([boxes[i][0], boxes[j][0]])

    write.save(board)
    write.done(
        {
            "moved": len(moves),
            "plan": plan,
            "courtyard_clash": clash,
            "note": "器件搬动后，连到它们的走线已经失效，"
            "接下来必须重布相关网络（pcb_route.py --mode rewidth --nets ...）",
        }
    )


def move_footprint(board: Board, fp: Footprint, x: int, y: int) -> None:
    """Put a footprint's origin at (x, y), in nm, and keep the model in step."""
    at = fp.node.find("at")
    at.set(1, number(x / NM))
    at.set(2, number(y / NM))
    dx, dy = x - fp.position[0], y - fp.position[1]
    fp.position = (x, y)
    for pad in fp.pads:
        pad.position = (pad.position[0] + dx, pad.position[1] + dy)
