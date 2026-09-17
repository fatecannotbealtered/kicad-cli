"""``sch sync-preview`` has to be right about a button nobody wants to press twice.

The claim under test is not "it produces a number" but "it produces the number
KiCad's updater would produce". So each case constructs a board state whose
correct answer is known from the matching rule -- links intact, links stripped,
references renamed -- and checks the prediction against it.

The renamed case matters most. It is the one where KiCad's own
``--schematic-parity`` check disagrees, because that check matches by reference
and the updater matches by uuid. If this test ever starts reporting adds there,
the implementation has drifted onto the wrong key.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from kicad_demos import DEMOS, SKIP_REASON

REPO = Path(__file__).resolve().parents[1]

PATH_LINE = re.compile(r'^\s*\(path "[^"]*"\)\s*$')


def run(*argv: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "kicad_cli.main", *argv, "--compact"],
        capture_output=True,
        cwd=REPO,
        timeout=1800,
    )
    out = proc.stdout.decode("utf-8", "replace")
    for line in out.splitlines():
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(f"no envelope on stdout: {out[:400]}{proc.stderr.decode()[:400]}")


def copy_demo(name: str, dest: Path) -> Path:
    shutil.copytree(DEMOS / name, dest)
    return next(dest.glob("*.kicad_pcb"))


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
@pytest.mark.parametrize("demo", ["complex_hierarchy", "interf_u", "kit-dev-coldfire-xilinx_5213"])
def test_linked_board_predicts_no_changes(demo: str, tmp_path: Path) -> None:
    board = copy_demo(demo, tmp_path / demo)
    r = run("sch", "sync-preview", "--board", str(board))
    assert r["ok"] is True, r
    d = r["data"]
    assert d["status"] == "CLEAN"
    assert d["counts"]["add"] == 0
    assert d["counts"]["remove"] == 0
    # Every checkbox combination is safe on a board whose links are intact.
    for combo, counts in d["matrix"].items():
        assert counts["add"] == 0, f"{combo} predicted additions on a linked board"
        assert counts["remove"] == 0, f"{combo} predicted removals on a linked board"


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_stripped_links_predict_a_destructive_update(tmp_path: Path) -> None:
    """The case the whole command exists for: references fine, links gone."""
    board = copy_demo("complex_hierarchy", tmp_path / "ch")
    before = run("sch", "sync-preview", "--board", str(board))["data"]
    assert before["counts"]["add"] == 0

    text = board.read_text(encoding="utf-8")
    board.write_text(
        "\n".join(ln for ln in text.splitlines() if not PATH_LINE.match(ln)) + "\n",
        encoding="utf-8",
    )

    after = run("sch", "sync-preview", "--board", str(board))["data"]
    assert after["status"] == "DESTRUCTIVE"
    assert after["counts"]["add"] == before["components"], "every component should read as new"
    # Not deleted by default, deleted when the box is ticked -- and that
    # difference is the whole reason the matrix is reported.
    assert after["matrix"]["relink=off, delete-extra=off"]["remove"] == 0
    # ...except the locked ones. complex_hierarchy locks Q304, and a locked
    # footprint is reported as a warning and kept whatever the boxes say, so
    # "delete everything" still spares it.
    locked = [x for x in after["dialog_preview"]["not_removed"] if "locked" in x["why"]]
    assert after["matrix"]["relink=off, delete-extra=on"]["remove"] == (
        before["footprints"] - len(locked)
    )
    # Ticking "re-link by reference" is KiCad's own repair for this state.
    assert after["matrix"]["relink=on, delete-extra=off"]["add"] == 0
    assert any(f["id"] == "S1-would-add" for f in after["findings"])
    assert any(c["id"] == "no_unlinked_footprints" and not c["pass"] for c in after["preflight"])


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_renaming_a_reference_is_not_an_add(tmp_path: Path) -> None:
    """Renumbering the board alone must read as a rename, never as add+delete.

    This is where matching by reference gives the wrong answer: the part is the
    same part, its uuid link is intact, and the updater simply renames it.
    """
    board = copy_demo("interf_u", tmp_path / "iu")
    text = board.read_text(encoding="utf-8")
    assert '(property "Reference" "R5"' in text
    board.write_text(
        text.replace('(property "Reference" "R5"', '(property "Reference" "R5X"', 1),
        encoding="utf-8",
    )

    d = run("sch", "sync-preview", "--board", str(board))["data"]
    assert d["counts"]["add"] == 0, "a renamed part is not a new part"
    assert d["counts"]["remove"] == 0
    assert d["counts"]["change_reference"] == 1
    assert d["dialog_preview"]["change_reference"][0] == {"from": "R5X", "to": "R5"}
    # But with reference matching it *does* look like one appeared and one left.
    assert d["matrix"]["relink=on, delete-extra=off"]["add"] == 1


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_board_only_footprints_are_never_removed(tmp_path: Path) -> None:
    board = copy_demo("cm5_minima", tmp_path / "cm5")
    d = run("sch", "sync-preview", "--board", str(board))["data"]
    for combo, counts in d["matrix"].items():
        assert counts["remove"] == 0, f"{combo} wanted to delete board-only parts"
    reasons = {x["why"] for x in d["dialog_preview"]["not_removed"]}
    assert any("board_only" in r for r in reasons)


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_refuses_on_an_unannotated_schematic(tmp_path: Path) -> None:
    """A confident zero from a broken input is worse than an error."""
    board = copy_demo("ecc83", tmp_path / "ecc83")
    sch = board.with_suffix(".kicad_sch")
    text = sch.read_text(encoding="utf-8")
    assert '"R1"' in text
    sch.write_text(text.replace('"R1"', '"R?"'), encoding="utf-8")

    r = run("sch", "sync-preview", "--board", str(board))
    assert r["ok"] is False
    assert r["error"]["code"] == "E_VALIDATION"
    failed = {c["id"] for c in r["error"]["details"]["failed"]}
    assert "fully_annotated" in failed or "netlist_exported_cleanly" in failed
