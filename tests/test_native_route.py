"""``board route`` and ``board route rewidth`` moved off pcbnew: the old payload is
the reference.

Each demo board is copied twice; the payload routes one copy, dry run then
confirmation, and the port the other. The copper laid must be the payload's,
track for track and via for via, to the nanometre; the reports must match
field for field but for the DRC's counts, which are this tool's own DRC now
(`verify.oracle` "engine") where the payload asked KiCad's.

repair routes what the DRC lists as missing, in the order listed, from the
first item's position to the second's. This tool's DRC finds the missing
connections KiCad finds, but lists them in another order and turns some the
other way -- KiCad's order and direction come out of its triangulation of the
pads. So for repair the port is given KiCad's own list, from KiCad's DRC of
the same board: what is compared is the routing, not the list. KiCad does not
name the same anchors every run, so the payload is given that same report
(`fake_drc_oracle`) where it would have run KiCad's DRC itself.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import fake_upstream
import pytest
from driver_test_utils import grant
from native_process import payload as sdk_payload
from payload_reference import DEMO_BOARDS, differences, native, needs_payload
from test_fileformat_circuit import official_cli

from kicad_cli import arguments, envelope, registry
from kicad_cli.fileformat.board import Board
from kicad_cli.fileformat.drc.items import Item
from kicad_cli.native import route

FULL = [b for b in DEMO_BOARDS if b.stem in ("ecc83-pp", "ecc83-pp_v2", "interf_u")]
REPAIR = [b for b in DEMO_BOARDS if b.stem in ("ecc83-pp", "ecc83-pp_v2")]


def copy_project(board: Path, into: Path) -> Path:
    into.mkdir(parents=True)
    for sibling in board.parent.glob(board.stem + ".kicad_*"):
        if sibling.suffix in (".kicad_pcb", ".kicad_pro", ".kicad_prl", ".kicad_dru"):
            shutil.copy2(sibling, into / sibling.name)
    return into / board.name


def neutral(doc: dict, path: Path) -> dict:
    return json.loads(json.dumps(doc, ensure_ascii=False).replace(json.dumps(str(path))[1:-1], "B"))


def copper(path: Path) -> Counter:
    board = Board.load(path)
    out: Counter = Counter()
    for t in board.tracks:
        out[(t.kind, t.start, t.end, t.width, t.layer, t.net)] += 1
    for v in board.vias:
        out[("via", v.position, v.diameter, v.drill, v.net)] += 1
    return out


def payload_route(path: Path, argv: list[str]) -> tuple[dict, dict | None]:
    args = ["--board", str(path), *argv]
    asked = sdk_payload("pcb_route", args)
    if asked["error"]["code"] != "E_CONFIRMATION_REQUIRED":
        return asked, None
    token = asked["error"]["details"]["confirm_token"]
    return asked, sdk_payload("pcb_route", [*args, "--confirm", token])


def same_report(done: dict, new_done: dict, theirs: Path, ours: Path) -> None:
    if not new_done.get("ok") and new_done["error"]["code"] in {
        "E_INTEGRITY",
        "E_VERIFY_INCOMPLETE",
    }:
        # Identity/provenance gates can intentionally reject writes that the
        # old count-only payload accepted. Confirm the actual rejection and
        # restored bytes rather than treating this as geometry equivalence.
        details = new_done["error"]["details"]
        comparison = details["finding_comparison"]
        assert comparison["status"] in {"failed", "incomplete"}, new_done
        if comparison["status"] == "failed":
            assert comparison["new_findings"], new_done
        else:
            assert comparison["incomplete_reasons"], new_done
        assert details["write_state"] == "rolled_back", new_done
        assert (
            hashlib.sha256(ours.read_bytes()).hexdigest()
            == (comparison["baseline"]["provenance"]["input"]["sha256"])
        )
        return
    assert done.get("ok") == new_done.get("ok"), (done, new_done)
    if not done.get("ok"):
        assert done["error"]["code"] == new_done["error"]["code"]
        return
    old, new = neutral(done["data"], theirs), neutral(new_done["data"], ours)
    comparison = new["verify"].pop("finding_comparison")
    assert comparison["status"] == "passed"
    assert comparison["final"]["provenance"]["source"]["kind"] == "custom_checker"
    assert not comparison["new_findings"]
    new["verify"].pop("intermediate_checks", None)
    # The new note describes the bounded finding gate rather than promising
    # a whole-board DRC improvement from totals. Geometry remains compared.
    new["verify"]["note"] = old["verify"]["note"]
    for doc in (old, new):
        for key in ("oracle", "errors_baseline", "errors_final"):
            (doc.get("verify") or {}).pop(key, None)
    # The command fills each field it declares and this mode does not set
    # with None; the payload's data had none of them.
    new = {k: v for k, v in new.items() if k in old or v is not None}
    assert differences(old, new) == []
    assert copper(theirs) == copper(ours)


@needs_payload
@pytest.mark.parametrize("board", FULL, ids=lambda p: p.stem)
def test_full_routes_as_the_payload_routed(board, tmp_path):
    theirs = copy_project(board, tmp_path / "payload")
    ours = copy_project(board, tmp_path / "native")
    asked, done = payload_route(theirs, ["--mode", "full"])
    new_asked = native("board", "route", "rebuild", "--board", str(ours))
    assert (
        differences(
            neutral(asked["error"]["details"]["preview"], theirs),
            neutral(new_asked["error"]["details"]["preview"], ours),
        )
        == []
    )
    token = new_asked["error"]["details"]["confirm_token"]
    new_done = native("board", "route", "rebuild", "--board", str(ours), "--confirm", token)
    same_report(done, new_done, theirs, ours)


# -- repair, on the payload's list ----------------------------------------------------


def damaged(board: Path, into: Path) -> Path:
    """The project with every fourth track taken out: connections to make."""
    path = copy_project(board, into)
    b = Board.load(path)
    for k, node in enumerate([n for n in b.root.lists() if n.head == "segment"]):
        if k % 4 == 0:
            b.root.remove(node)
    b.document.save(path)
    return path


class Missing:
    def __init__(self, items: list[Item]) -> None:
        self.items = items


def kicads_report(path: Path, into: Path) -> Path:
    """KiCad's DRC report of the board, kept for both sides to read."""
    from native_process import directory, run

    out = into / "kicad-report.json"
    if out.exists():
        raise FileExistsError("A native reference call needs a new report path")
    run(
        [
            official_cli(),
            "pcb",
            "drc",
            "--format",
            "json",
            "--severity-all",
            "-o",
            str(out),
            str(path),
        ],
        directory("repair-native-drc"),
        timeout=3600,
        report={"path": str(out), "kind": "drc"},
    )
    return out


def kicads_list(report: Path) -> list[Missing]:
    """KiCad's missing connections in its report, as the payload read them."""
    found = json.loads(report.read_text(encoding="utf-8"))
    return [
        Missing(
            [
                Item(
                    "",
                    i.get("uuid", ""),
                    i["description"],
                    (round(i["pos"]["x"] * 1e6), round(i["pos"]["y"] * 1e6)),
                )
                for i in v["items"]
            ]
        )
        for v in found["unconnected_items"]
    ]


def in_process(argv: list[str], capsys) -> dict:
    """Private algorithm comparison: keep the injected target list in this process.

    The fixed public driver uses a separate worker and different receipts; it
    is not the subject of this old-payload equivalence test.
    """
    assert argv[:3] == ["board", "route", "repair"]
    words = ["board", "route", *argv[3:], "--mode", "repair"]
    _, opts = arguments.parse(words, registry._base_commands(), registry.GLOBAL_OPTIONS)
    assert (
        Path(opts["board"])
        .resolve()
        .is_relative_to(Path(os.environ["KICAD_CLI_NATIVE_TEST_ROOT"]).resolve())
    )
    envelope.configure(compact=True)
    with pytest.raises(SystemExit):
        route.run(opts["board"], opts)
    return json.loads(capsys.readouterr().out)


@needs_payload
@pytest.mark.skipif(official_cli() is None, reason="needs KiCad's own binary")
@pytest.mark.parametrize("board", REPAIR, ids=lambda p: p.stem)
def test_repair_routes_as_the_payload_routed(board, tmp_path, monkeypatch, capsys):
    base = damaged(board, tmp_path / "base")
    shutil.copytree(base.parent, tmp_path / "payload")
    shutil.copytree(base.parent, tmp_path / "native")
    theirs, ours = tmp_path / "payload" / board.name, tmp_path / "native" / board.name
    report = kicads_report(ours, tmp_path)
    targets = kicads_list(report)
    assert targets, "the damaged board has nothing missing"
    oracle = fake_upstream.make_launcher(
        tmp_path / "bin", "kicad-cli", Path(__file__).parent / "fake_drc_oracle.py"
    )
    monkeypatch.setenv("KICAD_CLI_OFFICIAL", str(oracle))
    monkeypatch.setenv("FAKE_DRC_REPORT", str(report))
    _asked, done = payload_route(theirs, ["--mode", "repair"])

    monkeypatch.setenv("KICAD_CLI_STRICT", "1")
    monkeypatch.setenv("KICAD_CLI_STATE", str(tmp_path / "domain-state"))
    monkeypatch.delenv("KICAD_CLI_TRACE", raising=False)
    monkeypatch.delenv("KICAD_CLI_ERROR_TRACE", raising=False)
    monkeypatch.setattr(route, "missing_connections", lambda _path: targets)
    argv = ["board", "route", "repair", "--board", str(ours)]
    new_asked = in_process(argv, capsys)
    token = new_asked["error"]["details"]["confirm_token"]
    new_done = in_process([*argv, "--confirm", token], capsys)
    same_report(done, new_done, theirs, ours)


def test_the_router_does_not_need_kicad_for_its_list(tmp_path):
    """Without KiCad's list, repair asks this tool's DRC what is missing."""
    board = Path(__file__).parent / "fixtures" / "drc" / "drc1" / "drc1.kicad_pcb"
    listed = route.missing_connections(str(board))
    assert listed and all(len(m.items) == 2 for m in listed)


def test_a_grid_painted_by_rows_reaches_as_a_numpy_slice_does():
    """A slice whose stop is negative counts from the far end, as numpy's
    did in the payload: a track drawn far off the board's left edge paints
    what the payload's painted."""
    from kicad_cli.native.router import _span, dilate

    assert _span(0, -7, 20) == (0, 13)
    assert _span(25, 30, 20) == (20, 20)
    assert _span(-3, 20, 20) == (17, 20)
    # A disc, not a square: the diagonal neighbour two away is not reached.
    grown = dilate([0, 0, 0b100, 0, 0], 2, 5)
    assert grown[0] == 0b00100 and grown[2] == 0b11111 and grown[1] == 0b01110


def test_rewidth_names_an_explicitly_requested_missing_class(tmp_path):
    """An empty project must not pretend the selected class exists."""
    board = tmp_path / "one.kicad_pcb"
    board.write_bytes(
        b'(kicad_pcb (version 20241229) (generator "t") (general (thickness 1.6))\n'
        b'\t(layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))\n)\n'
    )
    (tmp_path / "one.kicad_pro").write_bytes(b"{}")
    env = dict(os.environ, **grant(tmp_path), KICAD_CLI_STRICT="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kicad_cli.main",
            "board",
            "route",
            "rewidth",
            "--board",
            str(board),
            "--classes",
            "Default",
            "--dry-run",
        ],
        capture_output=True,
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        timeout=600,
    )
    doc = json.loads(proc.stdout.decode("utf-8"))
    assert doc["error"]["code"] == "E_NOT_FOUND"
    assert doc["error"]["details"]["missing"] == ["Default"]
    assert doc["error"]["details"]["dispatch_state"] == "not_started"
