"""``board place`` moved off pcbnew: the old payload is the reference.

Each demo board is copied twice. The payload places the parts of one copy,
dry run then confirmation, and the port the parts of the other. Then:

- the previews must match, field for field;
- the reports must match, field for field -- but the DRC counts, which are
  this tool's own now (`verify.oracle` "engine") where the payload asked
  KiCad's, and so leave out what this tool does not check yet;
- and every footprint must stand where the payload put it, to the
  micrometre: the placement is the payload's.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from payload_reference import DEMO_BOARDS, differences, kicad_env, native, needs_payload

from kicad_cli.fileformat.board import Board

# Placing is quadratic in the parts, two hundred times over: small boards.
SMALL = [b for b in DEMO_BOARDS if b.stem in ("ecc83-pp", "ecc83-pp_v2", "interf_u", "microwave")]


def copy_project(board: Path, into: Path) -> Path:
    into.mkdir(parents=True)
    for sibling in board.parent.glob(board.stem + ".kicad_*"):
        if sibling.suffix in (".kicad_pcb", ".kicad_pro", ".kicad_prl"):
            shutil.copy2(sibling, into / sibling.name)
    return into / board.name


def neutral(doc: dict, path: Path) -> dict:
    import json  # noqa: PLC0415

    return json.loads(json.dumps(doc, ensure_ascii=False).replace(json.dumps(str(path))[1:-1], "B"))


def positions(board: Path) -> dict[str, tuple[int, int]]:
    return {fp.reference: fp.position for fp in Board.load(board).footprints}


@needs_payload
@pytest.mark.parametrize("board", SMALL, ids=lambda p: p.stem)
def test_the_native_place_places_as_the_payload_placed(board, tmp_path):
    theirs = copy_project(board, tmp_path / "payload")
    ours = copy_project(board, tmp_path / "native")
    asked = kicad_env.run_payload("pcb_autoplace", ["--board", str(theirs)])
    new_asked = native("board", "place", "--board", str(ours))
    if asked["error"]["code"] != "E_CONFIRMATION_REQUIRED":
        # Refused before any change: the same refusal, the same reasons.
        assert new_asked["error"]["code"] == asked["error"]["code"], new_asked
        assert (
            neutral(new_asked["error"]["details"], ours)
            == {**neutral(asked["error"]["details"], theirs), "write_state": "not_started"}
            or new_asked["error"]["message"] == asked["error"]["message"]
        )
        return
    token = asked["error"]["details"]["confirm_token"]
    done = kicad_env.run_payload("pcb_autoplace", ["--board", str(theirs), "--confirm", token])
    token = new_asked["error"]["details"]["confirm_token"]
    new_done = native("board", "place", "--board", str(ours), "--confirm", token)

    old_preview = neutral(asked["error"]["details"]["preview"], theirs)
    new_preview = neutral(new_asked["error"]["details"]["preview"], ours)
    assert differences(old_preview, new_preview) == []
    assert done.get("ok") == new_done.get("ok"), (done, new_done)
    if not done.get("ok"):
        assert done["error"]["code"] == new_done["error"]["code"]
        return
    old, new = neutral(done["data"], theirs), neutral(new_done["data"], ours)
    for doc in (old, new):
        for key in ("oracle", "drc_before", "drc_after", "warnings_added"):
            doc["verify"].pop(key)
        doc.pop("note")  # names the DRC's warnings, which differ with the oracle
    assert differences(old, new) == []
    old_at, new_at = positions(theirs), positions(ours)
    assert old_at == new_at
