"""Write outline/: one board per question about the board's outline -- the
one thing on a board there is only one of. Each is a 30 mm square drawn on
Edge.Cuts, but for how the case changes it: a side left off, a gap between
two ends, a side that crosses another, no outline at all.

`outline/cases.json` holds, for each board, what KiCad's DRC says of its
outline -- "" for nothing, else the reason it gives -- as KiCad 10 answered
it in English; the reasons are its words, and the tests hold this tool to
them. To change the boards: edit this, run it, and ask KiCad again.
"""

import json
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from kicad_cli.fileformat import new_board, new_project  # noqa: E402

OUT = HERE / "outline"
NS = uuid.UUID("7a1c5d2e-0000-4000-8000-0000000d4c0e")


def uid(*parts) -> str:
    return str(uuid.uuid5(NS, "/".join(map(str, parts))))


def line(key, a, b):
    return (
        f"(gr_line (start {a[0]} {a[1]}) (end {b[0]} {b[1]}) (stroke (width 0.1) (type solid))"
        f' (layer "Edge.Cuts") (uuid "{uid(key, a, b)}"))'
    )


def square(key, gap=0.0, skip=False):
    a, b, c, d = (10, 10), (40, 10), (40, 40), (10, 40)
    out = [line(key, a, b), line(key, b, c), line(key, c, d)]
    if not skip:
        out.append(line(key, d, (a[0], a[1] + gap)))
    return out


def rectangle(key, x1, y1, x2, y2):
    corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    return [line(key, corners[i - 1], corners[i]) for i in range(4)]


CASES = {
    # name: (drawings, what KiCad says)
    "closed": (square("closed"), ""),
    "open": (square("open", skip=True), "not a closed shape"),
    "gap_10um": (square("gap_10um", gap=0.01), ""),  # ends 10 um apart still join
    "gap_10_5um": (square("gap_10_5um", gap=0.0105), "not a closed shape"),
    "bowtie": (
        [
            line("bowtie", (10, 10), (40, 40)),
            line("bowtie", (40, 40), (40, 10)),
            line("bowtie", (40, 10), (10, 40)),
            line("bowtie", (10, 40), (10, 10)),
        ],
        "self-intersecting",
    ),  # fmt: skip
    "none": ([], "no edges found on Edge.Cuts layer"),
    "two_boards": (square("two_boards") + rectangle("two_boards_b", 50, 10, 60, 20), ""),
    "cut_out": (square("cut_out") + rectangle("cut_out_b", 20, 20, 30, 30), ""),
    "crossing": (
        square("crossing") + rectangle("crossing_b", 30, 20, 50, 30),
        "self-intersecting",
    ),  # fmt: skip
    "stray_line": (
        square("stray_line") + [line("stray_line_b", (20, 20), (25, 25))],
        "not a closed shape",
    ),  # fmt: skip
}

tail = "\t(embedded_fonts no)\n)\n"
answers = {}
for name, (drawings, said) in CASES.items():
    folder = OUT / name
    folder.mkdir(parents=True, exist_ok=True)
    text = new_board.EMPTY[: -len(tail)] + "".join(f"\t{d}\n" for d in drawings) + tail
    (folder / f"{name}.kicad_pcb").write_bytes(text.encode("utf-8"))
    (folder / f"{name}.kicad_pro").write_bytes(new_project.project(name).encode("utf-8"))
    answers[name] = said
(OUT / "cases.json").write_bytes((json.dumps(answers, indent=1) + "\n").encode("utf-8"))
print("written", len(CASES), "boards")
