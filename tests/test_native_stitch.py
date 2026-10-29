"""``board zone stitch`` moved off pcbnew: the old payload is the reference.

Each demo board with a pour is copied twice; the payload stitches one copy,
dry run then confirmation, and the port the other. The plan -- each island,
each via and where -- comes from the pour as the file has it, and must be
the payload's, field for field; so must the report. The zones filled again
afterwards are this tool's fill on one side and pcbnew's on the other, and
the vias the DRC took back are judged by this tool's DRC on one side and
KiCad's on the other -- on the coldfire kit's board, two vias that land on
copper text, which this DRC finds by the text's box until the stroke font
is in.

pic_programmer is left out for time: its pour has ten thousand edges, and
the payload takes twenty minutes over it where the port takes half a minute.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from native_process import payload as sdk_payload
from payload_reference import DEMO_BOARDS, differences, needs_payload
from payload_reference import legacy_port as native

BOARDS = [b for b in DEMO_BOARDS if b.stem in (
    "ecc83-pp", "ecc83-pp_v2", "interf_u", "kit-dev-coldfire-xilinx_5213",
)]  # fmt: skip


def copy_project(board: Path, into: Path) -> Path:
    into.mkdir(parents=True)
    for sibling in board.parent.glob(board.stem + ".kicad_*"):
        if sibling.suffix in (".kicad_pcb", ".kicad_pro", ".kicad_prl"):
            shutil.copy2(sibling, into / sibling.name)
    for extra in ("fp-lib-table",):
        if (board.parent / extra).exists():
            shutil.copy2(board.parent / extra, into / extra)
    return into / board.name


def neutral(doc: dict, path: Path) -> dict:
    import json  # noqa: PLC0415

    return json.loads(json.dumps(doc, ensure_ascii=False).replace(json.dumps(str(path))[1:-1], "B"))


@needs_payload
@pytest.mark.parametrize("board", BOARDS, ids=lambda p: p.stem)
def test_the_native_stitch_stitches_as_the_payload_stitched(board, tmp_path):
    theirs = copy_project(board, tmp_path / "payload")
    ours = copy_project(board, tmp_path / "native")
    # Each side asks and confirms in turn: a confirmation is good for fifteen
    # minutes, and the payload is slow enough on a big pour to outlast it.
    asked = sdk_payload("pcb_stitch", ["--board", str(theirs)])
    done = None
    if asked["error"]["code"] == "E_CONFIRMATION_REQUIRED":
        token = asked["error"]["details"]["confirm_token"]
        done = sdk_payload("pcb_stitch", ["--board", str(theirs), "--confirm", token])
    new_asked = native("board", "zone", "stitch", "--board", str(ours))
    if asked["error"]["code"] != "E_CONFIRMATION_REQUIRED":
        assert new_asked["error"]["code"] == asked["error"]["code"], new_asked
        return
    old_preview = neutral(asked["error"]["details"]["preview"], theirs)
    new_preview = neutral(new_asked["error"]["details"]["preview"], ours)
    assert differences(old_preview, new_preview) == []

    token = new_asked["error"]["details"]["confirm_token"]
    new_done = native("board", "zone", "stitch", "--board", str(ours), "--confirm", token)
    assert done.get("ok") and new_done.get("ok"), (done, new_done)
    assert differences(neutral(done["data"], theirs), neutral(new_done["data"], ours)) == []
