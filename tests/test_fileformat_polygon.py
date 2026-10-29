"""Inside or outside, on the boundary, as pcbnew decides it.

Every "is this point in the copper" question the tool asks -- connectivity,
the plane check -- comes down to `Polygon.contains`, and away from the
boundary any ray-casting rule agrees with pcbnew's. On it they differ: a
point on an edge has to be put on one side, and the textbook rule puts it on
the other side from pcbnew's for horizontal edges. That is not a corner case
in practice. A track drawn along the edge of a pour lies on that edge for its
whole length; on KiCad's interf_u demo the textbook rule turned eight samples
under one track from "on GND" to "no copper" and the plane report changed.

`PCBNEW_SAID` is what pcbnew answered, recorded so the rule is checked on
every run with or without KiCad; the oracle test asks pcbnew again, so a new
KiCad that changes its mind shows here first.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from kicad_cli import kicad_env  # noqa: E402
from kicad_cli.fileformat.connectivity import Polygon  # noqa: E402

ORACLE = REPO / "tests" / "pcbnew_oracle.py"

SQUARE = [(0, 0), (1000, 0), (1000, 1000), (0, 1000)]
POLYGONS = {
    "square": SQUARE,
    "square, other way round": SQUARE[::-1],
    "diamond": [(500, 0), (1000, 500), (500, 1000), (0, 500)],
    "slanted right side": [(0, 0), (1000, 0), (1300, 700), (0, 700)],
    # A right side leaning by a few nm, so where it crosses a row is a
    # fraction of a nanometre: how that fraction is rounded decides the
    # points one nanometre either side of it.
    "leaning out by 3": [(0, 0), (1000, 0), (1003, 1000), (0, 1000)],
    "leaning in by 3": [(0, 0), (1003, 0), (1000, 1000), (0, 1000)],
    "leaning out by 2": [(0, 0), (1000, 0), (1002, 1000), (0, 1000)],
    "leaning in by 2": [(0, 0), (1002, 0), (1000, 1000), (0, 1000)],
}

# (polygon, point, what pcbnew said), from SHAPE_POLY_SET.Contains, KiCad 10.0.
PCBNEW_SAID = [
    ("square", (500, 500), True),
    ("square", (1500, 500), False),
    ("square", (500, 0), False),  # on the top edge: outside
    ("square", (500, 1000), True),  # on the bottom edge: inside
    ("square", (0, 500), True),  # on the left edge: inside
    ("square", (1000, 500), False),  # on the right edge: outside
    ("square", (0, 0), False),
    ("square", (1000, 1000), False),
    ("square", (500, 999), True),
    ("square", (500, 1001), False),
    ("square", (1001, 500), False),
    ("square, other way round", (500, 0), False),
    ("square, other way round", (500, 1000), True),
    ("square, other way round", (0, 500), True),
    ("square, other way round", (1000, 500), False),
    ("square, other way round", (0, 0), False),
    ("square, other way round", (1000, 1000), False),
    ("diamond", (500, 0), False),
    ("diamond", (500, 1000), False),
    ("diamond", (0, 500), True),
    ("diamond", (1000, 500), False),
    ("diamond", (750, 250), False),
    ("diamond", (500, 999), True),
    ("slanted right side", (1150, 350), False),  # on the slanted edge
    ("slanted right side", (1149, 350), True),
    ("slanted right side", (1151, 350), False),
    ("slanted right side", (500, 700), True),  # on the bottom edge
    ("slanted right side", (1001, 500), True),
    ("leaning out by 3", (1000, 100), False),  # edge at +0.3: rounds to 0
    ("leaning out by 3", (1000, 233), True),  # edge at +0.699: rounds to 1
    ("leaning in by 3", (1002, 233), False),  # edge at -0.699 from 1003: rounds to -1
    ("leaning in by 3", (1003, 100), False),
    ("leaning out by 2", (1000, 250), True),  # edge at +0.5: away from zero, to 1
    ("leaning in by 2", (1001, 250), False),  # edge at -0.5 from 1002: away from zero, to -1
    ("leaning in by 2", (1002, 250), False),
]


@pytest.mark.parametrize(
    ("polygon", "point", "inside"),
    PCBNEW_SAID,
    ids=[f"{p}@{x},{y}" for p, (x, y), _ in PCBNEW_SAID],
)
def test_a_point_is_inside_where_pcbnew_says_it_is(polygon, point, inside):
    assert Polygon(POLYGONS[polygon]).contains(*point) is inside


def test_a_large_polygon_uses_the_same_rule():
    """Past 64 edges the polygon is searched through an index; the rule
    must not change with it."""
    steps = 100
    top = [(i * 10, 0) for i in range(steps)]
    bottom = [(1000 - i * 10, 1000) for i in range(steps)]
    polygon = Polygon(top + [(1000, 0)] + bottom + [(0, 1000)])
    assert polygon.contains(500, 1000) is True
    assert polygon.contains(500, 0) is False
    assert polygon.contains(0, 500) is True
    assert polygon.contains(1000, 500) is False


def kicad_python() -> str | None:
    try:
        return kicad_env.find_python()
    except Exception:  # noqa: BLE001 - resolution must never break collection
        return None


@pytest.mark.skipif(kicad_python() is None, reason="needs KiCad's interpreter to ask pcbnew")
def test_pcbnew_still_says_what_was_recorded():
    cases = [[POLYGONS[p], list(point)] for p, point, _ in PCBNEW_SAID]
    proc = subprocess.run(
        [kicad_python(), str(ORACLE), "--contains"],
        input=json.dumps(cases).encode(),
        capture_output=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-2000:]
    said = [bool(v) for v in json.loads(proc.stdout.decode("utf-8"))]
    assert said == [inside for _, _, inside in PCBNEW_SAID]
