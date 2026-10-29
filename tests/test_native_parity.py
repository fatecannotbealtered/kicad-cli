"""``project parity``, both sides read by this tool, held to two references.

The board side has been this tool's since the port off pcbnew; the schematic
side is now this tool's own netlist instead of KiCad's binary. So:

- against KiCad's own S-expression netlist, run through the same comparison,
  the report must be identical on every demo board: what changed is only
  where the netlist comes from;
- against the old payload, which read KiCad's XML netlist, the report must be
  identical except where the XML was wrong for the purpose. It keeps the
  parts marked "exclude from board", so the payload reported them missing
  from the board -- five on CM5_MINIMA_3 -- and nets holding their pins as
  not matching.

The payload's sample of up to ten mismatched nets came from a Python set, so
its order changed from process to process; the port sorts it. Where ten or
fewer are missing, the samples must hold the same nets.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict

import pytest
from kicad_demos import copy_demo
from native_process import payload as sdk_payload
from payload_reference import DEMO_BOARDS, REPO, assert_same, needs_payload
from payload_reference import legacy_port as native
from test_fileformat_circuit import export, needs_kicad

sys.path.insert(0, str(REPO))

from kicad_cli import sexpr  # noqa: E402
from kicad_cli.native import parity  # noqa: E402

SAMPLED = "net_membership_mismatch"
WITH_SCHEMATIC = [b for b in DEMO_BOARDS if b.with_suffix(".kicad_sch").exists()]


def kicads_side(board) -> tuple[dict, dict]:
    """The schematic side as KiCad's own S-expression netlist has it."""
    node = sexpr.parse(export(board.with_suffix(".kicad_sch")))
    parts = {}
    for comp in sexpr.children(sexpr.child(node, "components"), "comp"):
        parts[sexpr.value(sexpr.child(comp, "ref"))] = {
            "value": str(sexpr.value(sexpr.child(comp, "value"), default="")).strip(),
            "footprint": str(sexpr.value(sexpr.child(comp, "footprint"), default="")).strip(),
        }
    nets: dict[str, set[str]] = defaultdict(set)
    for net in sexpr.children(sexpr.child(node, "nets"), "net"):
        name = sexpr.value(sexpr.child(net, "name"))
        for n in sexpr.children(net, "node"):
            ref, pin = sexpr.value(sexpr.child(n, "ref")), sexpr.value(sexpr.child(n, "pin"))
            nets[name].add(f"{ref}.{pin}")
    return parts, nets


@needs_kicad
@pytest.mark.parametrize("board", WITH_SCHEMATIC, ids=lambda p: p.name)
def test_parity_says_what_kicads_own_netlist_says(board):
    parts, nets = kicads_side(board)
    source = f"现导（{board.with_suffix('.kicad_sch').name}）"
    expected = parity.report(str(board), parts, nets, source)
    got = native("project", "parity", "--board", str(board))
    assert got["ok"], got
    assert json.loads(json.dumps(expected, ensure_ascii=False)) == {
        **got["data"],
        "board": str(board),
    }


def payload_parity(board) -> dict:
    """One recorded native attempt. An exception ends this cycle, never retries."""
    return sdk_payload("parity", ["--board", str(board)], timeout=1800)


def split_samples(doc: dict) -> list[tuple[int, list]]:
    """Take the sampled lists out of an envelope, with the count each samples."""
    taken = []
    for diff in (doc.get("data") or {}).get("diffs", []):
        if diff["kind"] == SAMPLED:
            count = int(re.search(r"\d+", diff["detail"]).group())
            taken.append((count, diff["evidence"].pop("samples")))
    return taken


# Parts the XML netlist kept although they are marked "exclude from board".
EXCLUDED = {"CM5_MINIMA_3.kicad_pcb": ["M303", "M705", "R601", "SW601", "TP701"]}


@needs_payload
@pytest.mark.parametrize("board", DEMO_BOARDS, ids=lambda p: p.name)
def test_parity_says_what_the_payload_said_but_for_excluded_parts(board, tmp_path):
    # Official netlist export may leave native sidecars on failure. The saved
    # installation fixture is never the target of that subprocess.
    board = copy_demo(board.parent, tmp_path / "project", whole=False) / board.name
    old = payload_parity(board)
    new = native("project", "parity", "--board", str(board))
    if board.name in EXCLUDED:
        missing = next(d for d in old["data"]["diffs"] if d["kind"] == "component_missing_on_pcb")
        assert missing["evidence"]["refs"] == EXCLUDED[board.name]
        assert all(d["kind"] != "component_missing_on_pcb" for d in new["data"]["diffs"])
        return
    old_samples, new_samples = split_samples(old), split_samples(new)
    assert_same(old, new)
    assert [count for count, _ in old_samples] == [count for count, _ in new_samples]
    for (count, theirs), (_, ours) in zip(old_samples, new_samples, strict=True):
        assert len(ours) == min(count, 10)
        assert ours == sorted(ours, key=lambda s: s["pins"]), "the sample is sorted"
        if count <= 10:
            key = json.dumps
            assert sorted(theirs, key=key) == sorted(ours, key=key)


def test_a_schematic_that_cannot_be_read_says_why(tmp_path):
    board = tmp_path / "b.kicad_pcb"
    board.write_bytes((REPO / "tests" / "fixtures" / "mini" / "mini.kicad_pcb").read_bytes())
    board.with_suffix(".kicad_sch").write_text("(kicad_pcb (version 1))", encoding="utf-8")
    doc = native("project", "parity", "--board", str(board))
    assert doc["error"]["code"] == "E_VALIDATION", doc
    assert "not a schematic" in doc["error"]["details"]["reason"]
