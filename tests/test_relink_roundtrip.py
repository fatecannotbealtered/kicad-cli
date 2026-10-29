"""Strip every link from a KiCad-generated board, rebuild it, compare.

These boards are the ground truth: KiCad wrote their ``path`` fields itself.
Removing them and asking ``project links repair`` to put them back turns a claim that is
otherwise unfalsifiable -- "the uuid we wrote is the right one" -- into a plain
string comparison against what KiCad actually produces.

The demos cover the cases that matter: a flat sheet, a hierarchy, a sheet
instantiated more than once, and parts with several units.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import fake_upstream
import pytest
from driver_test_utils import domain_receipt
from kicad_demos import DEMOS, SKIP_REASON, copy_demo
from public_test_cli import invoke, planned
from public_test_cli import public_cli_scope as public_cli_scope

REPO = Path(__file__).resolve().parents[1]

PATH_LINE = re.compile(r'^\s*\(path "[^"]*"\)\s*$')

CASES = [
    "complex_hierarchy",
    "ecc83",
    "interf_u",
    "kit-dev-coldfire-xilinx_5213",
    "cm5_minima",
]


def run(*argv: str, env: dict | None = None) -> dict:
    value, _ = invoke(list(argv), env)
    return domain_receipt(value)


def without_paths(raw):
    return "\n".join(line for line in raw.splitlines() if not PATH_LINE.match(line))


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
    copy_demo(src, work)
    board = next(p for p in work.glob("*.kicad_pcb"))

    expected = original_paths(board)
    assert expected, "the demo board has no links to begin with"

    text = board.read_text(encoding="utf-8")
    board.write_text(
        "\n".join(ln for ln in text.splitlines() if not PATH_LINE.match(ln)) + "\n",
        encoding="utf-8",
    )

    before_bytes = board.read_text(encoding="utf-8")
    before = run("project", "links", "inspect", "--board", str(board))
    if demo == "cm5_minima" and not before["ok"]:
        from collections import Counter

        from kicad_cli.fileformat.sexpr import Document

        identities = Counter(node.value() for node in Document.load(board).root.walk("uuid"))
        assert any(count > 1 for count in identities.values())
        assert before["error"]["code"] == "E_VALIDATION"
        assert before["error"]["details"]["reason"] == "duplicate object UUID"
        assert before["error"]["details"]["dispatch_state"] == "not_started"
        assert board.read_text(encoding="utf-8") == before_bytes
        pytest.skip(
            "cm5_minima has duplicate object UUIDs; complete-identity observation refuses, "
            "so relink roundtrip was not executed"
        )
    assert before["ok"], before
    assert before["data"]["linked"] == 0, "stripping the links should leave none behind"

    plan = run("project", "links", "repair", "--board", str(board), "--dry-run")
    planned(plan)
    token = plan["data"]["confirm_token"]

    done = run("project", "links", "repair", "--board", str(board), "--confirm", token)
    assert done["ok"] is True, done
    assert without_paths(board.read_text(encoding="utf-8")) == without_paths(before_bytes)

    # The real assertion: not "a path was written" but "KiCad's path was written".
    #
    # For a part with one unit there is exactly one right answer, so demand the
    # original value back, character for character. A multi-unit part exports one
    # uuid per unit and the updater tries them all, so any of them is correct and
    # which one the board happened to record is an accident of its history --
    # there, the contract is membership, not equality.
    from kicad_cli.commands.sch import _netlist_paths

    accepted, _ = _netlist_paths(board)
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
    copy_demo(DEMOS / "interf_u", work)
    board = next(p for p in work.glob("*.kicad_pcb"))
    digest = board.read_bytes()

    plan = run("project", "links", "repair", "--board", str(board), "--dry-run")
    planned(plan)
    done = run(
        "project",
        "links",
        "repair",
        "--board",
        str(board),
        "--confirm",
        plan["data"]["confirm_token"],
    )
    assert done["ok"] is True
    assert done["data"]["status"] == "NOOP"
    assert board.read_bytes() == digest, "a no-op must not rewrite the file"


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_relink_refuses_when_a_footprint_has_no_symbol(tmp_path: Path) -> None:
    """A part that exists only on the board cannot be linked, and a partial
    write would leave the board in a state nobody asked for."""
    work = tmp_path / "interf_u"
    copy_demo(DEMOS / "interf_u", work)
    board = next(p for p in work.glob("*.kicad_pcb"))

    text = board.read_text(encoding="utf-8")
    text = "\n".join(ln for ln in text.splitlines() if not PATH_LINE.match(ln))
    # Rename one reference to something the schematic has never heard of.
    text = text.replace('(property "Reference" "R5"', '(property "Reference" "R999"', 1)
    board.write_text(text + "\n", encoding="utf-8")
    before_bytes = board.read_bytes()

    plan = run("project", "links", "repair", "--board", str(board), "--dry-run")
    assert plan["ok"] is False
    assert plan["error"]["code"] == "E_VALIDATION", plan
    assert plan["error"]["details"]["dispatch_state"] == "not_started"
    assert plan["error"]["details"]["problem"] == "invalid_value"
    assert plan["error"]["message"] == "Candidate invariants incomplete; primary inputs unchanged"
    assert "confirm_token" not in plan.get("data", {})
    assert board.read_bytes() == before_bytes
    # This generic public refusal does not promise a reason field or name the
    # orphan reference. Diagnostic specificity remains a documented limitation.


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_relink_needs_no_kicad(tmp_path: Path) -> None:
    """The paths come from this tool's own netlist and the board is read back
    by this tool's own reader. KiCad's binary and interpreter are stubs that
    fail when called, and neither is called."""
    work = tmp_path / "interf_u"
    copy_demo(DEMOS / "interf_u", work)
    board = next(p for p in work.glob("*.kicad_pcb"))
    text = board.read_bytes().decode("utf-8")
    board.write_bytes(
        ("\n".join(ln for ln in text.splitlines() if not PATH_LINE.match(ln)) + "\n").encode()
    )
    env = fake_upstream.env(tmp_path / "kicad", "launch_fail")

    plan = run("project", "links", "repair", "--board", str(board), "--dry-run", env=env)
    planned(plan)
    token = plan["data"]["confirm_token"]
    done = run("project", "links", "repair", "--board", str(board), "--confirm", token, env=env)
    assert done["ok"] is True, done
    assert done["data"]["status"] == "PASS"
    assert (
        run("project", "links", "inspect", "--board", str(board), env=env)["data"]["unlinked"] == 0
    )
    assert fake_upstream.attempts(tmp_path / "kicad") == 0, "relink started KiCad after all"
