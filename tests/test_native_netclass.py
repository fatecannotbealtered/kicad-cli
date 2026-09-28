"""``board netclass`` moved out of KiCad's interpreter.

It never needed pcbnew -- a net class is a JSON edit of the project file --
so what is compared here is exact: the preview, the report, and the project
file the two write, byte for byte.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from payload_reference import DEMO_BOARDS, REPO, differences, kicad_env, native, needs_payload

from kicad_cli.fileformat.board import Board

CASES = {
    "a new class": ("Power", ["--width", "0.5", "--clearance", "0.25"]),
    "an existing class": ("Default", ["--width", "0.3", "--via-diameter", "0.7"]),
}


def copy_project(board: Path, into: Path) -> Path:
    into.mkdir(parents=True)
    for suffix in (".kicad_pcb", ".kicad_pro"):
        source = board.with_suffix(suffix)
        if source.exists():
            shutil.copy2(source, into / source.name)
    return into / board.name


def nets_of(board: Path) -> list[str]:
    names = sorted({pad.net for fp in Board.load(board).footprints for pad in fp.pads if pad.net})
    return names[:2]


def neutral(doc: dict, path: Path) -> dict:
    import json  # noqa: PLC0415

    text = json.dumps(doc, ensure_ascii=False)
    return json.loads(text.replace(json.dumps(str(path.with_suffix(".kicad_pro")))[1:-1], "PRO"))


def payload_netclass(board: Path, argv: list[str]) -> tuple[dict, dict]:
    asked = kicad_env.run_payload("netclass", ["--board", str(board), *argv])
    token = asked["error"]["details"]["confirm_token"]
    done = kicad_env.run_payload("netclass", ["--board", str(board), *argv, "--confirm", token])
    return asked, done


def native_netclass(board: Path, argv: list[str]) -> tuple[dict, dict]:
    asked = native("board", "netclass", "--board", str(board), *argv)
    token = asked["error"]["details"]["confirm_token"]
    done = native("board", "netclass", "--board", str(board), *argv, "--confirm", token)
    return asked, done


BOARDS = [b for b in DEMO_BOARDS if b.with_suffix(".kicad_pro").exists()][:6]


@needs_payload
@pytest.mark.parametrize("case", sorted(CASES))
@pytest.mark.parametrize("board", BOARDS, ids=lambda p: p.name)
def test_the_native_netclass_writes_exactly_what_the_payload_wrote(board, case, tmp_path):
    name, options = CASES[case]
    nets = nets_of(board)
    theirs = copy_project(board, tmp_path / "payload")
    ours = copy_project(board, tmp_path / "native")

    old_argv = ["--name", name, "--nets", ",".join(nets), *options]
    new_argv = ["--name", name, *[a for n in nets for a in ("--nets", n)], *options]
    old_asked, old_done = payload_netclass(theirs, old_argv)
    new_asked, new_done = native_netclass(ours, new_argv)

    old_preview = neutral(old_asked["error"]["details"]["preview"], theirs)
    new_preview = neutral(new_asked["error"]["details"]["preview"], ours)
    assert differences(old_preview, new_preview) == []
    assert old_done.get("ok") and new_done.get("ok"), (old_done, new_done)
    assert differences(neutral(old_done["data"], theirs), neutral(new_done["data"], ours)) == []
    written = [b.with_suffix(".kicad_pro").read_bytes() for b in (theirs, ours)]
    assert written[0] == written[1]


def test_a_board_without_a_project_changes_nothing(tmp_path):
    board = tmp_path / "b" / "mini.kicad_pcb"
    board.parent.mkdir()
    shutil.copy2(REPO / "tests" / "fixtures" / "mini" / "mini.kicad_pcb", board)
    doc = native("board", "netclass", "--board", str(board), "--name", "Power", "--nets", "GND",
                 "--width", "0.5")  # fmt: skip
    assert doc["error"]["code"] == "E_NOT_FOUND", doc
    assert doc["error"]["details"]["write_state"] == "not_started"
    assert not board.with_suffix(".kicad_pro").exists()
