"""Which copper touches which, measured against pcbnew.

`fileformat/connectivity.py` states one rule -- same net, same layer, the
shapes meet -- and a way of counting what is left open. Both were settled by
asking pcbnew, and two of the guesses they replaced were wrong in ways no
demo board could show: every demo agreed with "tracks connect only at their
ends", because no demo has two same-net tracks crossing or a track crossing
a fill with both ends outside it.

So the rules are pinned by a board built to ask:
`fixtures/connectivity/scenarios.kicad_pcb` puts one situation on each net,
and what pcbnew made of each is written into the offline test below as a
fact. The live tests hold the model to pcbnew on that board -- which says
whether a new KiCad still agrees with the facts -- and on every demo board,
net by net, cluster by cluster.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from kicad_demos import DEMOS, SKIP_REASON

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from kicad_cli import kicad_env  # noqa: E402
from kicad_cli.fileformat.board import Board  # noqa: E402
from kicad_cli.fileformat.connectivity import connect  # noqa: E402
from kicad_cli.fileformat.sexpr import Document  # noqa: E402

ORACLE = REPO / "tests" / "pcbnew_oracle.py"
SCENARIOS = REPO / "tests" / "fixtures" / "connectivity" / "scenarios.kicad_pcb"

# What pcbnew (KiCad 10.0.6) made of each net on the scenarios board: the
# number of pads in each cluster.
PCBNEW_SAID = {
    "CROSS": [2],  # two tracks crossing mid-way join the pads they come from
    "THROUGH": [2],  # a track crossing a fill, both ends outside, joins it
    "SHORT": [2],  # a track ending 0.28 mm short of a pad, its copper on it
    "OVERLAP": [2],  # two pads whose edges overlap, neither centre in the other
    "APART": [1, 1],  # the same two pads 0.1 mm apart: not joined
    "LOOSE": [1],  # a pad, and a track touching nothing
    "LONEVIA": [1],  # a pad, and a via touching nothing
    "SHAPE": [1],  # a pad, and a filled rectangle on F.Cu with the net
    "ISLAND": [1],  # a pad, and a fill touching nothing
}
# Open connections, counted by pcbnew: APART, and the loose track, via and
# rectangle -- each a cluster with copper worth connecting. ISLAND's fill is
# not: a pour's island is isolated copper, which DRC reports separately.
PCBNEW_UNCONNECTED = 4


def test_the_scenarios_come_out_as_pcbnew_said():
    conn = connect(Board.load(SCENARIOS))
    clusters = {net: sorted(len(c) for c in found) for net, found in conn.pad_clusters().items()}
    assert clusters == {net: sorted(sizes) for net, sizes in PCBNEW_SAID.items()}
    assert conn.unconnected == PCBNEW_UNCONNECTED


SHORTED = """(kicad_pcb
\t(version 20260206)
\t(generator "pcbnew")
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(2 "B.Cu" signal)
\t)
\t(footprint "Test:Pad"
\t\t(layer "F.Cu")
\t\t(at 10 10)
\t\t(property "Reference" "A1"
\t\t\t(at 0 -2 0)
\t\t\t(layer "F.SilkS")
\t\t)
\t\t(pad "1" smd rect
\t\t\t(at 0 0)
\t\t\t(size 1 1)
\t\t\t(layers "F.Cu")
\t\t\t(net "A")
\t\t)
\t)
\t(footprint "Test:Pad"
\t\t(layer "F.Cu")
\t\t(at 20 10)
\t\t(property "Reference" "B1"
\t\t\t(at 0 -2 0)
\t\t\t(layer "F.SilkS")
\t\t)
\t\t(pad "1" smd rect
\t\t\t(at 0 0)
\t\t\t(size 1 1)
\t\t\t(layers "F.Cu")
\t\t\t(net "B")
\t\t)
\t)
\t(segment
\t\t(start 10 10)
\t\t(end 20 10)
\t\t(width 0.25)
\t\t(layer "F.Cu")
\t\t(net "B")
\t)
)
"""


def test_two_nets_that_touch_are_not_joined():
    """A short is DRC's to report; connectivity must not merge it away.

    Net B's track runs from A1's centre to B1's: copper of two nets meets on
    A1. B's track and pad are one cluster; A1 stays its own.
    """
    conn = connect(Board(Document.parse(SHORTED)))
    assert conn.pad_clusters() == {"A": [["A1.1@10000000,10000000"]],
                                   "B": [["B1.1@20000000,10000000"]]}  # fmt: skip
    nets_per_cluster: dict[int, set[str]] = {}
    for index, item in enumerate(conn.items):
        nets_per_cluster.setdefault(conn.cluster_of[index], set()).add(item.net)
    assert all(len(nets) == 1 for nets in nets_per_cluster.values())
    # And the track did join its own pad: B is one cluster holding both.
    b_clusters = {conn.cluster_of[i] for i, item in enumerate(conn.items) if item.net == "B"}
    assert len(b_clusters) == 1


# -- against pcbnew ------------------------------------------------------------------


def kicad_python() -> str | None:
    try:
        return kicad_env.find_python()
    except Exception:  # noqa: BLE001 - resolution must never break collection
        return None


needs_pcbnew = pytest.mark.skipif(
    not DEMOS.exists() or kicad_python() is None,
    reason=f"needs KiCad's interpreter to run pcbnew ({SKIP_REASON})",
)


def pcbnew_connectivity(board: Path) -> dict:
    command = [kicad_python(), str(ORACLE), "--connectivity", str(board)]
    proc = subprocess.run(command, capture_output=True, timeout=600)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-2000:]
    return json.loads(proc.stdout.decode("utf-8"))


def differences(board: Path) -> list[str]:
    truth = pcbnew_connectivity(board)
    conn = connect(Board.load(board))
    ours = conn.pad_clusters()
    theirs = {
        net: sorted(sorted(set(c)) for c in found) for net, found in truth["clusters"].items()
    }
    problems = [
        f"{net!r}: ours {[len(c) for c in ours.get(net, [])]}, "
        f"pcbnew {[len(c) for c in theirs.get(net, [])]}"
        for net in sorted(set(ours) | set(theirs))
        if ours.get(net, []) != theirs.get(net, [])
    ]
    if conn.unconnected != truth["unconnected"]:
        problems.append(f"unconnected: ours {conn.unconnected}, pcbnew {truth['unconnected']}")
    return problems


@needs_pcbnew
def test_pcbnew_still_rules_on_the_scenarios_as_recorded():
    """If a new KiCad changes its mind, this is where it shows."""
    truth = pcbnew_connectivity(SCENARIOS)
    said = {net: sorted(len(c) for c in found) for net, found in truth["clusters"].items()}
    assert said == {net: sorted(sizes) for net, sizes in PCBNEW_SAID.items()}
    assert truth["unconnected"] == PCBNEW_UNCONNECTED


@needs_pcbnew
@pytest.mark.parametrize(
    "path",
    sorted(DEMOS.rglob("*.kicad_pcb")) if DEMOS.exists() else [],
    ids=lambda p: p.name,
)
def test_every_demo_board_connects_as_pcbnew_connects_it(path):
    assert differences(path) == []
