"""Every command emits exactly the fields it advertises.

``reference`` is the only thing an agent reads before deciding what to call, so
a schema that has drifted from its command is not a documentation problem -- it
is the tool lying about itself. Nothing checked this until a strict mode was
added, and the first run found a read command declaring three fields it had
never emitted and omitting four it always did.

The check runs the real commands with ``KICAD_CLI_STRICT`` set, which makes the
envelope refuse to emit mismatched data. Write commands are exercised on copies
of KiCad's demo projects, all the way through the confirmation gate, because a
contract that only holds for the easy half is not a contract.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from kicad_demos import DEMOS, SKIP_REASON, copy_demo
from public_test_cli import copy_export_fixture, invoke, invoke_details, planned
from public_test_cli import public_cli_scope as public_cli_scope

REPO = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(REPO))
from kicad_cli import registry  # noqa: E402


def run(argv: list[str]) -> tuple[dict, str]:
    doc, err, code = invoke_details(argv)
    if doc["ok"]:
        assert code == 0, (code, doc)
    return doc, err


def check(argv: list[str]) -> dict:
    """Run and fail loudly on a contract violation, quoting what drifted."""
    doc, err = run(argv)
    assert "contract violation" not in err, err.strip()
    return doc


def demo(name: str, dest: Path) -> Path:
    copy_demo(DEMOS / name, dest)
    return next(dest.glob("*.kicad_pcb"))


def test_every_command_declares_a_schema_that_exists() -> None:
    commands = registry.build()
    assert commands, "the registry is empty"
    for c in commands:
        assert c["output_schema"] in registry.SCHEMAS, f"{c['path']} names a schema that is absent"
        assert registry.SCHEMAS[c["output_schema"]]["fields"], f"{c['path']} declares no fields"
        assert c["examples"], f"{c['path']} carries no example"
        assert c["type"] in ("read", "write")


def test_every_write_command_is_declared_as_a_write() -> None:
    """A read command that writes would slip past the confirmation gate."""
    by_path = {c["path"]: c for c in registry.build()}
    for path in (
        "project links repair",
        "board route repair",
        "board zone stitch",
        "board route rewidth",
        "board track widen",
        "board footprint move",
        "fab gerber",
        "fab drill",
    ):
        assert by_path[path]["type"] == "write", f"{path} is not gated"


@pytest.mark.parametrize("argv", [["reference"], ["context"], ["doctor"], ["changelog"]])
def test_self_describing_commands_match_their_contract(argv: list[str]) -> None:
    doc = check(argv)
    assert doc["ok"] is True


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
@pytest.mark.parametrize(
    "command",
    [
        ["board", "analyze", "ampacity"],
        ["project", "parity"],
        ["board", "analyze", "reference-plane"],
        ["project", "links", "inspect"],
        ["sch", "check"],
    ],
)
def test_read_commands_match_their_contract(command: list[str], tmp_path: Path) -> None:
    board = demo("interf_u", tmp_path / "iu")
    target = (
        ["--schematic", str(board.with_suffix(".kicad_sch"))]
        if command == ["sch", "check"]
        else ["--board", str(board)]
    )
    doc = check([*command, *target])
    assert doc["ok"] is True, doc


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
@pytest.mark.parametrize(
    ("command", "extra"),
    [
        (["board", "zone", "stitch"], ["--net", "GND"]),
        (["board", "track", "widen"], []),
        (["board", "footprint", "move"], ["--moves", "R5:50,50"]),
        (["board", "route", "repair"], []),
        # rewidth is route's third mode and had no live test either; it exits
        # through a different K.ok than repair/full, so it is a separate shape.
        (["board", "route", "rewidth"], ["--classes", "Power"]),
        (["board", "route", "rewidth", "--classes", "Power"], []),
        (["project", "links", "repair"], []),
        (["fab", "drill"], []),
    ],
    ids=[f"command{i}-extra{i}" for i in (0, 1, 2, 3, 5, 6, 7, 8)],
)
def test_write_commands_match_their_contract(
    command: list[str], extra: list[str], tmp_path: Path
) -> None:
    board = (
        copy_export_fixture(tmp_path / "saved-export")
        if command[:2] == ["fab", "drill"]
        else demo("interf_u", tmp_path / "iu")
    )
    if command[:3] == ["project", "links", "repair"]:
        # relink is a no-op on a healthy board; strip the links so it has work.
        text = board.read_text(encoding="utf-8")
        board.write_text(
            "\n".join(ln for ln in text.splitlines() if '(path "' not in ln) + "\n",
            encoding="utf-8",
        )
    if command[:2] == ["fab", "drill"]:
        extra = [*extra, "--out", str(tmp_path / "fab")]

    plan = check([*command, "--board", str(board), *extra, "--dry-run"])
    planned(plan)
    details = plan["data"]
    # The preview travels with the token: a gate nobody can see through is worse
    # than no gate, because it turns a decision into a reflex.
    assert details.get("preview"), f"{command} asks for confirmation without showing what for"
    assert details["confirm_token"].startswith("ct_")

    done = check([*command, "--board", str(board), *extra, "--confirm", details["confirm_token"]])
    assert done["ok"] is True, done


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_rebuild_refuses_new_findings_and_preserves_the_saved_demo(tmp_path):
    """This input is a refusal case, not evidence of a successful full rebuild.

    The no-new-findings gate intentionally rejects a newly introduced hole
    spacing finding even when the total error count falls. Successful rebuild
    receipts are separately exercised on the existing small power fixture.
    """
    board = demo("interf_u", tmp_path / "iu")
    protected = {p: p.read_bytes() for p in board.parent.glob("*.kicad_*")}
    refused = check(["board", "route", "rebuild", "--board", str(board), "--dry-run"])
    assert not refused["ok"] and refused["error"]["code"] == "E_VALIDATION", refused
    details = refused["error"]["details"]
    assert details["phase"] == "candidate_validation"
    assert details["primary_write_state"] == "not_applied"
    assert details["operation_registered"] is False
    assert details["dispatch_state"] == "not_started" and details["next_action"]
    comparison = details["candidate_error"]["details"]["finding_comparison"]
    assert comparison["criterion"] == "no_new_findings" and comparison["status"] == "failed"
    assert any(row["identity"]["rule"] == "hole_to_hole" for row in comparison["new_findings"])
    assert not refused.get("data", {}).get("confirm_token")
    assert {p: p.read_bytes() for p in protected} == protected


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_external_drawing_sheet_refuses_export_without_changing_the_demo(tmp_path):
    board = demo("interf_u", tmp_path / "iu")
    settings = board.with_suffix(".kicad_pro")
    assert json.loads(settings.read_bytes())["pcbnew"]["page_layout_descr_file"]
    before = {p: p.read_bytes() for p in (board, settings)}
    output = tmp_path / "export"
    doc, code = invoke(["fab", "drill", "--board", str(board), "--out", str(output), "--dry-run"])
    assert code == 2 and not doc["ok"] and doc["error"]["code"] == "E_VALIDATION", doc
    assert "External drawing sheets" in doc["error"]["details"]["reason"]
    assert doc["error"]["details"]["dispatch_state"] == "not_started"
    assert not output.exists() and not doc.get("data", {}).get("confirm_token")
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_a_stale_token_is_refused(tmp_path: Path) -> None:
    """The token binds to the previewed state, so a plan computed against an
    older board must not be applied to a newer one."""
    board = demo("interf_u", tmp_path / "iu")
    plan = check(["board", "zone", "stitch", "--board", str(board), "--net", "GND", "--dry-run"])
    planned(plan)
    token = plan["data"]["confirm_token"]

    doc, _ = run(
        ["board", "zone", "stitch", "--board", str(board), "--net", "GND", "--confirm", "ct_dead"]
    )
    assert doc["ok"] is False
    assert doc["error"]["code"] == "E_CONFLICT"
    assert token != "ct_dead"


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_document_lock_indicator_refuses_stitch_without_writing(tmp_path: Path) -> None:
    board = demo("interf_u", tmp_path / "iu")
    lock = board.parent / f"~{board.name}.lck"
    lock.write_text("{}", encoding="utf-8")
    before = {p: p.read_bytes() for p in (board, board.with_suffix(".kicad_pro"), lock)}

    doc, code = invoke(
        ["board", "zone", "stitch", "--board", str(board), "--net", "GND", "--dry-run"]
    )
    # This is the document guard. The move/power guard separately uses E_BUSY
    # and lock_files; its different contract must not be imposed on stitch.
    assert code == 6 and doc["ok"] is False
    assert doc["error"]["code"] == "E_CONFLICT" and doc["error"]["retryable"] is False
    assert doc["error"]["details"]["path"] == str(lock)
    assert doc["error"]["details"]["dispatch_state"] == "not_started"
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
@pytest.mark.parametrize(
    ("mode", "expect_regression"),
    [("full", True), ("repair", False)],
)
def test_route_reports_the_width_drc_cannot_see(mode, expect_regression, tmp_path):
    """A clean DRC verdict is not a claim about current-carrying capacity.

    `--mode full` routes at the router's neck width by design and expects
    `board track widen` after. On KiCad's ecc83 demo that takes the board from 100%
    width compliance to 0% and its minimum ampacity from 2.03 A to 0.74 A --
    while DRC error count, unconnected count and the envelope's `ok` all stay
    exactly as they were. The only thing that said so was a sentence of prose
    telling the caller to run another command, which a machine does not act on.

    `repair` leaves existing copper alone, so it is the negative case: without
    one, a flag that is always true would pass this test too.
    """
    board = demo("ecc83", tmp_path / mode)
    plan = check(
        [
            "board",
            "route",
            {"full": "rebuild", "repair": "repair", "rewidth": "rewidth"}[mode],
            "--board",
            str(board),
            "--dry-run",
        ]
    )
    planned(plan)
    done = check(
        [
            "board",
            "route",
            {"full": "rebuild", "repair": "repair", "rewidth": "rewidth"}[mode],
            "--board",
            str(board),
            "--confirm",
            plan["data"]["confirm_token"],
        ]
    )
    assert done["ok"] is True, done
    receipt = done["data"]["receipt"]
    assert receipt["outcome"]["verification"]["status"] == "passed"
    assert receipt["extensions"]["finding_comparison"]["status"] == "passed"
    before = plan["data"]["preview"]["extensions"]["before"]["objects"]
    after = receipt["extensions"]["readback"]["objects"]
    old_min = min(o["width_nm"] for o in before if o["kind"] != "via")
    new_min = min(o["width_nm"] for o in after if o["kind"] != "via")
    assert (new_min < old_min) is expect_regression
    assert receipt["extensions"]["quality"]["ampacity_assessed"] is False
