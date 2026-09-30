"""``board update``: the schematic's changes carried onto its board.

Each test changes a copy of `tests/fixtures/sch_edit/` -- a schematic, and a
board made from it with footprints of its own library (`make_board.py`) --
with `sch edit`, the way an agent would, and then updates the board. What
is checked is the board read back: its footprints, their pads' nets and its
copper. With KiCad installed, KiCad's own check of a board against its
schematic -- `pcb drc --schematic-parity` -- is asked too, and must find
nothing but the footprints a removed part left.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_fileformat_circuit import needs_kicad, official_cli

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from kicad_cli.fileformat.board import Board  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "sch_edit"


def run(argv: list[str]) -> dict:
    env = dict(os.environ, KICAD_CLI_STRICT="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "kicad_cli.main", *argv, "--compact"],
        capture_output=True,
        cwd=REPO,
        env=env,
        timeout=900,
    )
    err = proc.stderr.decode("utf-8", "replace")
    assert "contract violation" not in err, err
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(f"no envelope: {err[-800:]}")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A copy of the design and its board; the board's path."""
    for item in FIXTURE.iterdir():
        if item.suffix == ".py":
            continue
        if item.is_dir():
            shutil.copytree(item, tmp_path / item.name)
        else:
            shutil.copy(item, tmp_path / item.name)
    return tmp_path / "edit.kicad_pcb"


def confirmed(argv: list[str]) -> dict:
    asked = run([*argv, "--dry-run"])
    if asked["ok"] or asked["error"]["code"] != "E_CONFIRMATION_REQUIRED":
        return asked
    return run([*argv, "--confirm", asked["error"]["details"]["confirm_token"]])


def edit(board: Path, changes: list[dict]) -> None:
    spec = board.parent / "changes.json"
    spec.write_text(json.dumps({"changes": changes}), encoding="utf-8")
    done = confirmed(
        ["sch", "edit", "--schematic", str(board.with_suffix(".kicad_sch")), "--changes", str(spec)]
    )
    assert done["ok"] is True, done


def update(board: Path) -> dict:
    return confirmed(["board", "update", "--board", str(board)])


def footprint(board: Path, reference: str):
    return next(fp for fp in Board.load(board).footprints if fp.reference == reference)


def test_a_board_in_step_with_its_schematic_is_left_alone(project: Path) -> None:
    before = project.read_bytes()
    done = run(["board", "update", "--board", str(project), "--dry-run"])
    assert done["ok"] is True, done
    assert done["data"]["status"] == "NOOP"
    assert project.read_bytes() == before


def test_a_value_and_a_mark_reach_the_board(project: Path) -> None:
    edit(
        project,
        [
            {"op": "set", "ref": "R1", "value": "22k"},
            {"op": "set", "ref": "R3", "dnp": True, "fields": {"MPN": "RC0805"}},
        ],
    )
    done = update(project)
    assert done["ok"] is True, done
    assert done["data"]["status"] == "PASS"
    assert footprint(project, "R1").value == "22k"
    r3 = footprint(project, "R3")
    assert "dnp" in r3.attributes
    mpn = next(p for p in r3.node.find_all("property") if p.value(1) == "MPN")
    assert mpn.value(2) == "RC0805"


def test_a_renamed_net_keeps_its_copper(project: Path) -> None:
    edit(project, [{"op": "rename", "net": "/SIG", "to": "DATA"}])
    done = update(project)
    assert done["ok"] is True, done
    assert done["data"]["changes"]["renamed_nets"] == [{"from": "/SIG", "to": "/DATA"}]
    board = Board.load(project)
    assert {t.net for t in board.tracks} == {"/DATA"}
    assert {v.net for v in board.vias} == {"/DATA"}
    assert footprint(project, "R1").pads[0].net == "/DATA"


def test_a_changed_footprint_is_swapped_where_the_old_one_was(project: Path) -> None:
    old = footprint(project, "R2")
    edit(project, [{"op": "set", "ref": "R2", "footprint": "fixture:R_0603"}])
    done = update(project)
    assert done["ok"] is True, done
    new = footprint(project, "R2")
    assert new.library_id == "fixture:R_0603"
    assert (new.position, new.angle) == (old.position, old.angle)
    assert [(p.number, p.net) for p in new.pads] == [(p.number, p.net) for p in old.pads]
    # The 0603's own pads, not the 0805's.
    assert abs(new.pads[0].position[0] - new.position[0]) == 825_000
    path = new.node.find("path")
    assert path is not None and path.value(1) == old.node.find("path").value(1)


def test_an_added_part_waits_beside_the_board_on_its_nets(project: Path) -> None:
    edit(project, [{"op": "add", "ref": "R?", "symbol": "fixture:R", "footprint": "fixture:R_0805",
                    "pins": {"1": "/OUT", "2": "GND"}, "near": "R3"}])  # fmt: skip
    done = update(project)
    assert done["ok"] is True, done
    added = done["data"]["changes"]["add"][0]["ref"]
    board = Board.load(project)
    new = next(fp for fp in board.footprints if fp.reference == added)
    assert new.position[0] > board.edge_bbox()[2]
    assert {(p.number, p.net) for p in new.pads} == {("1", "/OUT"), ("2", "GND")}


def test_a_removed_part_stays_on_the_board_and_is_listed(project: Path) -> None:
    edit(project, [{"op": "remove", "ref": "R4"}])
    done = update(project)
    assert done["ok"] is True, done
    assert footprint(project, "R4") is not None
    assert "R4" in [r["ref"] for r in done["data"]["changes"]["not_removed"]]


def test_a_pin_moved_to_another_net_leaves_the_copper_and_says_so(project: Path) -> None:
    edit(project, [{"op": "disconnect", "ref": "R2", "pin": "1"},
                   {"op": "connect", "ref": "R2", "pin": "1", "net": "EN"}])  # fmt: skip
    done = update(project)
    assert done["ok"] is True, done
    assert footprint(project, "R2").pads[0].net == "EN"
    left = done["data"]["changes"]["copper_left_on_old_nets"]
    assert left == [{"net": "/SIG", "now": ["/SIG", "EN"]}]
    assert {t.net for t in Board.load(project).tracks} == {"/SIG"}


def test_a_token_is_spent_only_on_what_it_was_shown(project: Path) -> None:
    edit(project, [{"op": "set", "ref": "R1", "value": "22k"}])
    asked = run(["board", "update", "--board", str(project), "--dry-run"])
    edit(project, [{"op": "set", "ref": "R1", "value": "33k"}])
    before = project.read_bytes()
    token = asked["error"]["details"]["confirm_token"]
    done = run(["board", "update", "--board", str(project), "--confirm", token])
    assert done["ok"] is False
    assert project.read_bytes() == before


def _parity(board: Path) -> list[dict]:
    out = board.parent / "parity.json"
    for _ in range(3):  # Windows fails roughly one launch in six
        subprocess.run(
            [official_cli(), "pcb", "drc", "--schematic-parity", "--severity-all", "--format",
             "json", "-o", str(out), str(board)],
            capture_output=True,
            timeout=900,
        )  # fmt: skip
        if out.exists():
            break
    return json.loads(out.read_text(encoding="utf-8"))["schematic_parity"]


@needs_kicad
def test_kicad_finds_the_updated_board_true_to_its_schematic(project: Path) -> None:
    assert _parity(project) == []
    edit(
        project,
        [
            {"op": "set", "ref": "R1", "value": "22k"},
            {"op": "set", "ref": "R2", "footprint": "fixture:R_0603"},
            {"op": "rename", "net": "/SIG", "to": "DATA"},
            {"op": "rename", "net": "Net-(R4-Pad2)", "to": "BRIDGE"},
            {"op": "connect", "ref": "R5", "pin": "1", "net": "+3V3"},
            {"op": "add", "ref": "R?", "symbol": "fixture:R", "footprint": "fixture:R_0805",
             "pins": {"1": "/OUT", "2": "GND"}},
            {"op": "remove", "ref": "R11"},
        ],
    )  # fmt: skip
    assert {v["type"] for v in _parity(project)} >= {"net_conflict", "missing_footprint"}
    done = update(project)
    assert done["ok"] is True, done
    left = _parity(project)
    assert [(v["type"], [i["description"] for i in v["items"]]) for v in left if
            v["type"] != "extra_footprint"] == []  # fmt: skip
    assert len(left) == 2  # R11 and R21, the removed part's footprints
