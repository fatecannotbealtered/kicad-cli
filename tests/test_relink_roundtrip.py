"""Strip every link from a KiCad-generated board, rebuild it, compare.

These boards are the ground truth: KiCad wrote their ``path`` fields itself.
Removing them and asking ``sch relink`` to put them back turns a claim that is
otherwise unfalsifiable -- "the uuid we wrote is the right one" -- into a plain
string comparison against what KiCad actually produces.

The demos cover the cases that matter: a flat sheet, a hierarchy, a sheet
instantiated more than once, and parts with several units.
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

CASES = [
    "complex_hierarchy",
    "ecc83",
    "interf_u",
    "kit-dev-coldfire-xilinx_5213",
    "cm5_minima",
]


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


def original_paths(board: Path) -> dict[str, str]:
    """Reference -> path, straight out of the untouched board file."""
    sys.path.insert(0, str(REPO))
    from kicad_cli.commands.sch import _board_footprints

    return {fp["ref"]: fp["path"] for fp in _board_footprints(board) if fp["path"]}


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
@pytest.mark.parametrize("demo", CASES)
def test_relink_reproduces_kicads_own_paths(demo: str, tmp_path: Path) -> None:
    src = DEMOS / demo
    work = tmp_path / demo
    shutil.copytree(src, work)
    board = next(p for p in work.glob("*.kicad_pcb"))

    expected = original_paths(board)
    assert expected, "the demo board has no links to begin with"

    text = board.read_text(encoding="utf-8")
    board.write_text(
        "\n".join(ln for ln in text.splitlines() if not PATH_LINE.match(ln)) + "\n",
        encoding="utf-8",
    )

    before = run("sch", "link", "--board", str(board))
    assert before["data"]["linked"] == 0, "stripping the links should leave none behind"

    plan = run("sch", "relink", "--board", str(board), "--dry-run")
    assert plan["ok"] is False
    assert plan["error"]["code"] == "E_CONFIRMATION_REQUIRED"
    token = plan["error"]["details"]["confirm_token"]

    done = run("sch", "relink", "--board", str(board), "--confirm", token)
    assert done["ok"] is True, done
    assert done["data"]["verified"]["object_counts_unchanged"] is True
    assert done["data"]["verified"]["diff_contains_only_link_fields"] is True

    # The real assertion: not "a path was written" but "KiCad's path was written".
    #
    # For a part with one unit there is exactly one right answer, so demand the
    # original value back, character for character. A multi-unit part exports one
    # uuid per unit and the updater tries them all, so any of them is correct and
    # which one the board happened to record is an accident of its history --
    # there, the contract is membership, not equality.
    from kicad_cli.commands.sch import _netlist_paths

    accepted = _netlist_paths(board)
    written = original_paths(board)
    assert set(written) == set(expected), "relink changed which footprints carry a link"
    for ref, want in expected.items():
        options = accepted[ref]["accepted"]
        if len(options) == 1:
            assert written[ref] == want, f"{ref}: single-unit part must round-trip exactly"
        else:
            assert written[ref] in options, f"{ref}: not a uuid of any of its units"


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_relink_on_an_already_linked_board_is_a_noop(tmp_path: Path) -> None:
    work = tmp_path / "interf_u"
    shutil.copytree(DEMOS / "interf_u", work)
    board = next(p for p in work.glob("*.kicad_pcb"))
    digest = board.read_bytes()

    done = run("sch", "relink", "--board", str(board))
    assert done["ok"] is True
    assert done["data"]["status"] == "NOOP"
    assert board.read_bytes() == digest, "a no-op must not rewrite the file"


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_relink_refuses_when_a_footprint_has_no_symbol(tmp_path: Path) -> None:
    """A part that exists only on the board cannot be linked, and a partial
    write would leave the board in a state nobody asked for."""
    work = tmp_path / "interf_u"
    shutil.copytree(DEMOS / "interf_u", work)
    board = next(p for p in work.glob("*.kicad_pcb"))

    text = board.read_text(encoding="utf-8")
    text = "\n".join(ln for ln in text.splitlines() if not PATH_LINE.match(ln))
    # Rename one reference to something the schematic has never heard of.
    text = text.replace('(property "Reference" "R5"', '(property "Reference" "R999"', 1)
    board.write_text(text + "\n", encoding="utf-8")

    plan = run("sch", "relink", "--board", str(board), "--dry-run")
    assert plan["ok"] is False
    assert plan["error"]["code"] == "E_CONFLICT"
    assert "R999" in plan["error"]["details"]["unresolved"]
