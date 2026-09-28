"""``board move`` moved off pcbnew: the old implementation is the reference.

Each demo board is copied twice. The payload moves three parts on one copy,
dry run then confirmation, and the port moves the same parts on the other.
Then:

- the previews and the reports must match, field for field;
- pcbnew must read the two written boards as the same board -- every
  footprint, pad, track and zone -- which is the test that matters for a
  write: whatever this tool writes, KiCad has to read what was meant;
- and the port must have changed nothing but the moved footprints' own
  `(at x y)`: pcbnew rewrote the whole file, this tool rewrites one line.

The moves are 1.005 mm across and 2.0005 mm up: the payload truncated
millimetres to nanometres, and 1.005 is where that shows.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from payload_reference import (
    DEMO_BOARDS,
    REPO,
    differences,
    kicad_env,
    kicad_python,
    native,
    needs_payload,
)

from kicad_cli.fileformat.board import Board
from kicad_cli.native.move import courtyard_box

ORACLE = REPO / "tests" / "pcbnew_oracle.py"
APPROXIMATION_MM = 0.010


def copy_project(board: Path, into: Path) -> Path:
    into.mkdir(parents=True)
    for sibling in board.parent.glob(board.stem + ".kicad_*"):
        if sibling.suffix in (".kicad_pcb", ".kicad_pro", ".kicad_prl"):
            shutil.copy2(sibling, into / sibling.name)
    return into / board.name


def moves_for(board: Path) -> tuple[str, set[str]]:
    """Three parts to move: first, middle and last of the unique references.

    A board whose references are all shared (KiCad's microwave demo is four
    footprints called POLY) moves one of them: both versions take the last
    footprint of a reference, and that is part of what is compared.
    """
    footprints = Board.load(board).footprints
    counts: dict[str, int] = {}
    for fp in footprints:
        counts[fp.reference] = counts.get(fp.reference, 0) + 1
    unique = sorted(r for r, n in counts.items() if n == 1 and r) or sorted(counts)
    picked = sorted({unique[0], unique[len(unique) // 2], unique[-1]})
    where = {fp.reference: fp.position for fp in footprints}
    spec = "; ".join(
        f"{ref}:{where[ref][0] / 1e6 + 1.005:.4f},{where[ref][1] / 1e6 - 2.0005:.4f}"
        for ref in picked
    )
    return spec, set(picked)


def payload_move(board: Path, moves: str) -> tuple[dict, dict]:
    asked = kicad_env.run_payload("pcb_place", ["--board", str(board), "--moves", moves])
    token = asked["error"]["details"]["confirm_token"]
    done = kicad_env.run_payload(
        "pcb_place", ["--board", str(board), "--moves", moves, "--confirm", token]
    )
    return asked, done


def native_move(board: Path, moves: str) -> tuple[dict, dict]:
    asked = native("board", "move", "--board", str(board), "--moves", moves)
    token = asked["error"]["details"]["confirm_token"]
    done = native("board", "move", "--board", str(board), "--moves", moves, "--confirm", token)
    return asked, done


def pcbnew_reads(board: Path) -> dict:
    proc = subprocess.run(
        [kicad_python(), str(ORACLE), str(board)], capture_output=True, timeout=1800
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-2000:]
    dump = json.loads(proc.stdout.decode("utf-8"))
    dump.pop("board")
    return dump


def without(doc: dict, path: str, *keys: str) -> dict:
    """The same document with the board's path made neutral and ``keys`` dropped."""
    text = json.dumps(doc, ensure_ascii=False).replace(json.dumps(path)[1:-1], "BOARD")
    out = json.loads(text)
    for key in keys:
        out.pop(key, None)
    return out


def split(dump: dict, moved: set[str]) -> tuple[list, list]:
    """A pcbnew dump's footprints: those with a moved reference, and the rest."""

    def key(fp):
        return json.dumps(fp, sort_keys=True)

    ours = sorted((fp for fp in dump["footprints"] if fp["reference"] in moved), key=key)
    rest = sorted((fp for fp in dump["footprints"] if fp["reference"] not in moved), key=key)
    return ours, rest


@needs_payload
@pytest.mark.parametrize("board", DEMO_BOARDS, ids=lambda p: p.name)
def test_the_native_move_does_exactly_what_the_payload_did(board, tmp_path):
    moves, moved = moves_for(board)
    theirs = copy_project(board, tmp_path / "payload")
    ours = copy_project(board, tmp_path / "native")

    old_asked, old_done = payload_move(theirs, moves)
    new_asked, new_done = native_move(ours, moves)

    assert old_asked["error"]["code"] == new_asked["error"]["code"] == "E_CONFIRMATION_REQUIRED"
    old_preview = without(old_asked["error"]["details"]["preview"], str(theirs))
    new_preview = without(new_asked["error"]["details"]["preview"], str(ours))
    assert differences(old_preview, new_preview) == []

    assert old_done.get("ok") and new_done.get("ok"), (old_done, new_done)
    old_clash = {tuple(pair) for pair in old_done["data"].pop("courtyard_clash")}
    new_clash = {tuple(pair) for pair in new_done["data"].pop("courtyard_clash")}
    assert differences(old_done["data"], new_done["data"]) == []
    # pcbnew draws a courtyard's arcs and circles with its own approximation,
    # a few micrometres off the true curve; this tool does not reproduce it
    # (see courtyard_box). A pair may differ only where that could decide it.
    boxes = {fp.reference: courtyard_box(fp) for fp in Board.load(ours).footprints}
    for a, b in old_clash ^ new_clash:
        (a1, b1, a2, b2), (c1, d1, c2, d2) = boxes[a], boxes[b]
        gap = max(c1 - a2, a1 - c2, d1 - b2, b1 - d2)
        assert abs(gap) < APPROXIMATION_MM, (a, b, gap)

    # pcbnew reads the moved parts where the payload put them, and everything
    # else as it was. Not "as the payload left it": pcbnew's own save is not a
    # no-op -- load and save CM5_MINIMA_3 and nine of its pads come back on
    # four more copper layers -- and this tool rewrites only what it changed.
    before, after, payload = pcbnew_reads(board), pcbnew_reads(ours), pcbnew_reads(theirs)
    assert differences(split(after, moved)[0], split(payload, moved)[0]) == []
    assert differences(split(after, moved)[1], split(before, moved)[1]) == []
    # Tracks as pcbnew reads them after the payload's save, not as they were:
    # pcbnew gives a track the net of the pad it lands on when it loads a
    # board, so a part moved onto other tracks changes their nets as read --
    # hundreds of them on video, for both versions alike.
    for key in ("tracks", "copper_layers", "edge_bbox"):
        assert differences(after[key], payload[key]) == [], key
    # Zones as they were: pcbnew's save names every teardrop "$teardrop_padvia$"
    # (complex_hierarchy, RoyalBlue54L-Feather), and nothing here touches zones.
    assert differences(after["zones"], before["zones"]) == []

    old_lines = board.read_bytes().splitlines()
    new_lines = ours.read_bytes().splitlines()
    assert len(old_lines) == len(new_lines), "a move changes lines, it does not add or remove them"
    changed = [(a, b) for a, b in zip(old_lines, new_lines, strict=True) if a != b]
    assert len(changed) == len(moved), changed
    assert all(b.strip().startswith(b"(at ") for _, b in changed), changed


def test_a_malformed_move_is_a_usage_error(tmp_path):
    """The payload split the text and crashed on anything else, which reached
    the caller as E_IO around a traceback."""
    board = copy_project(REPO / "tests" / "fixtures" / "mini" / "mini.kicad_pcb", tmp_path / "b")
    doc = native("board", "move", "--board", str(board), "--moves", "U1 to 10,10")
    assert doc["error"]["code"] == "E_USAGE", doc
    assert doc["error"]["details"]["got"] == "U1 to 10,10"
    assert doc["error"]["details"]["write_state"] == "not_started"


def test_an_unknown_reference_changes_nothing(tmp_path):
    board = copy_project(REPO / "tests" / "fixtures" / "mini" / "mini.kicad_pcb", tmp_path / "b")
    before = board.read_bytes()
    doc = native("board", "move", "--board", str(board), "--moves", "U1:10,10")
    assert doc["error"]["code"] == "E_NOT_FOUND", doc
    assert doc["error"]["details"] == {"refs": ["U1"], "write_state": "not_started"}
    assert board.read_bytes() == before
