"""The board model, measured against pcbnew while pcbnew still exists.

Every rule `fileformat/board.py` states about what a number in the file
means was found here, by asking pcbnew what it made of the same file:

- a footprint's children sit in its own frame, already mirrored on the
  back, and are placed by turning them by its angle;
- a pad's stored angle is its final angle on the board;
- KiCad 9 numbers nets and KiCad 10 names them;
- "*.Cu" means the board's copper, not all 32 layers KiCad could have;
- the board outline includes half the width of the line it is drawn with,
  and anything a footprint draws on Edge.Cuts.

`tests/pcbnew_oracle.py` runs under KiCad's interpreter and reports what
pcbnew holds; the comparison is item by item: every footprint, every pad
(position, angle, size, kind, shape, drill, layers, net, copper outline),
every track, arc, via and zone. Rectangles, trapezoids and chamfered pads
are compared vertex by vertex, to the nanometre. Curves are compared within
what two approximations of one curve can differ by.

The demo boards barely use trapezoids, chamfers or custom pads, so a board
made of KiCad's own footprints that do -- at five angles, on both sides --
is built by pcbnew for the purpose and compared the same way.

The offline tests at the top need no KiCad and run everywhere.
"""

from __future__ import annotations

import collections
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest
from kicad_demos import DEMOS, SKIP_REASON

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from kicad_cli import kicad_env  # noqa: E402
from kicad_cli.fileformat import geometry  # noqa: E402
from kicad_cli.fileformat.board import Board  # noqa: E402
from kicad_cli.fileformat.sexpr import Document  # noqa: E402

ORACLE = REPO / "tests" / "pcbnew_oracle.py"

# -- offline ----------------------------------------------------------------------

KICAD_10 = """(kicad_pcb
\t(version 20260206)
\t(generator "pcbnew")
\t(generator_version "10.0")
\t(layers
\t\t(0 "F.Cu" signal "top_copper")
\t\t(4 "In1.Cu" signal)
\t\t(2 "B.Cu" signal)
\t\t(25 "Edge.Cuts" user)
\t)
\t(footprint "Resistor_SMD:R_0603_1608Metric"
\t\t(layer "F.Cu")
\t\t(at 10 20 90)
\t\t(property "Reference" "R1"
\t\t\t(at 0 -1.43 90)
\t\t\t(layer "F.SilkS")
\t\t)
\t\t(property "Value" "10k"
\t\t\t(at 0 1.43 90)
\t\t\t(layer "F.Fab")
\t\t)
\t\t(locked yes)
\t\t(pad "1" smd roundrect
\t\t\t(at -0.825 0 90)
\t\t\t(size 0.8 0.95)
\t\t\t(layers "F.Cu" "F.Mask" "F.Paste")
\t\t\t(roundrect_rratio 0.25)
\t\t\t(net "GND")
\t\t)
\t\t(pad "2" thru_hole circle
\t\t\t(at 0.825 0)
\t\t\t(size 1.6 1.6)
\t\t\t(drill 0.8)
\t\t\t(layers "*.Cu" "*.Mask")
\t\t\t(net "VCC")
\t\t)
\t)
\t(segment
\t\t(start 1 2)
\t\t(end 3 2)
\t\t(width 0.2)
\t\t(layer "F.Cu")
\t\t(net "GND")
\t)
\t(via
\t\t(at 5 5)
\t\t(size 0.6)
\t\t(drill 0.3)
\t\t(layers "F.Cu" "B.Cu")
\t\t(net "VCC")
\t)
\t(gr_rect
\t\t(start 0 0)
\t\t(end 40 30)
\t\t(stroke
\t\t\t(width 0.1)
\t\t\t(type default)
\t\t)
\t\t(fill no)
\t\t(layer "Edge.Cuts")
\t)
)
"""

KICAD_9_NETS = """(kicad_pcb
\t(version 20241229)
\t(generator "pcbnew")
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(2 "B.Cu" signal)
\t)
\t(net 0 "")
\t(net 1 "GND")
\t(net 2 "/Sheet/SIG")
\t(segment
\t\t(start 1 2)
\t\t(end 3 2)
\t\t(width 0.2)
\t\t(layer "F.Cu")
\t\t(net 2)
\t)
\t(zone
\t\t(net 1)
\t\t(net_name "GND")
\t\t(layer "B.Cu")
\t\t(name "pour")
\t\t(priority 2)
\t\t(polygon
\t\t\t(pts
\t\t\t\t(xy 0 0) (xy 10 0) (xy 10 10) (xy 0 10)
\t\t\t)
\t\t)
\t\t(filled_polygon
\t\t\t(layer "B.Cu")
\t\t\t(pts
\t\t\t\t(xy 1 1) (xy 9 1) (xy 9 9) (xy 1 9)
\t\t\t)
\t\t)
\t)
)
"""


def board_from(text: str) -> Board:
    return Board(Document.parse(text))


def test_a_pad_is_placed_by_turning_it_with_its_footprint():
    fp = board_from(KICAD_10).footprints[0]
    pad1, pad2 = fp.pads
    # (-0.825, 0) turned by 90 in KiCad's frame is (0, +0.825): below the origin.
    assert pad1.position == (10_000_000, 20_825_000)
    assert pad2.position == (10_000_000, 19_175_000)
    assert pad1.angle == 90.0
    # Left out in the file means 0 on the board, not the footprint's 90.
    assert pad2.angle == 0.0


def test_a_kicad_10_board_names_its_nets_and_its_layers():
    board = board_from(KICAD_10)
    fp = board.footprints[0]
    assert (fp.reference, fp.value, fp.locked) == ("R1", "10k", True)
    assert [p.net for p in fp.pads] == ["GND", "VCC"]
    assert board.copper_layers == ["F.Cu", "In1.Cu", "B.Cu"]
    assert board.layers[0].user_name == "top_copper" and board.layers[0].name == "F.Cu"
    # "*.Cu" is this board's copper, not every copper layer KiCad knows.
    assert fp.pads[1].layers == ["F.Cu", "In1.Cu", "B.Cu", "F.Mask", "B.Mask"]
    assert board.vias[0].layers == ["F.Cu", "In1.Cu", "B.Cu"]
    assert board.tracks[0].net == "GND"


def test_a_kicad_9_board_numbers_its_nets():
    board = board_from(KICAD_9_NETS)
    assert board.tracks[0].net == "/Sheet/SIG"
    zone = board.zones[0]
    assert (zone.net, zone.name, zone.priority, zone.layers) == ("GND", "pour", 2, ["B.Cu"])
    assert geometry.area(zone.outlines[0]) == 100e12
    assert geometry.area(zone.filled["B.Cu"][0]) == 64e12


def test_the_outline_counts_half_the_width_it_is_drawn_with():
    assert board_from(KICAD_10).edge_bbox() == (-50_000, -50_000, 40_050_000, 30_050_000)


def test_rotation_is_exact_at_right_angles_and_turns_the_way_kicad_does():
    assert geometry.rotate(3, 4, 90) == (4, -3)
    assert geometry.rotate(3, 4, -270) == (4, -3)
    assert geometry.rotate(3, 4, 180) == (-3, -4)
    x, y = geometry.rotate(1_000_000, 0, 30)
    assert (round(x), round(y)) == (866_025, -500_000)


def test_an_arc_goes_through_its_middle_point():
    quarter = geometry.arc_through((10, 0), (7.0710678, 7.0710678), (0, 10), max_error=0.01)
    assert quarter[0] == pytest.approx((10, 0)) and quarter[-1] == pytest.approx((0, 10))
    assert all(math.hypot(x, y) == pytest.approx(10) for x, y in quarter)
    # The same end points through the other side is the long way round.
    long_way = geometry.arc_through((10, 0), (-10, 0), (0, 10), max_error=0.01)
    assert any(x < -9 for x, _ in long_way)


def test_a_chamfer_cuts_only_the_corners_it_names():
    shape = geometry.chamfered_rectangle(4, 2, 0.5, {"bottom_left"})
    assert len(shape) == 5
    assert (-2, 0.5) in shape and (-1.5, 1) in shape
    assert geometry.area(shape) == pytest.approx(8 - 0.125)


# -- against pcbnew ------------------------------------------------------------------

CURVED = 25_000  # nm: two approximations of one curve; KiCad's is the coarser one


def kicad_python() -> str | None:
    try:
        return kicad_env.find_python()
    except Exception:  # noqa: BLE001 - resolution must never break collection
        return None


needs_pcbnew = pytest.mark.skipif(
    not DEMOS.exists() or kicad_python() is None,
    reason=f"needs KiCad's interpreter to run pcbnew ({SKIP_REASON})",
)


def oracle(board: Path) -> dict:
    command = [kicad_python(), str(ORACLE), str(board)]
    proc = subprocess.run(command, capture_output=True, timeout=600)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-2000:]
    return json.loads(proc.stdout.decode("utf-8"))


def demo_boards() -> list[Path]:
    return sorted(DEMOS.rglob("*.kicad_pcb")) if DEMOS.exists() else []


def within(a, b, tolerance) -> bool:
    return max(abs(u - v) for u, v in zip(a, b, strict=True)) <= tolerance


def perimeter(points) -> float:
    return sum(math.dist(points[i - 1], points[i]) for i in range(len(points)))


def turned(angle: float) -> float:
    return round(angle % 360, 6)


def elements(counter: collections.Counter) -> list:
    return list(counter.elements())[:2]


def compare_pad(pad, want: dict, where: str, relevant, counted) -> list[str]:
    problems = []
    shape = "chamfered_rect" if pad.chamfer_ratio and pad.chamfer_corners else pad.shape
    drill = tuple(want["drill"]) if want["drill"] else None
    facts = {
        "angle": (turned(pad.angle), turned(want["orientation"])),
        "size": (pad.size, tuple(want["size"])),
        "kind": (pad.kind, want["type"]),
        "shape": (shape, want["shape"]),
        "drill": (pad.drill, drill),
        "layers": (relevant(pad.layers), want["layers"]),
        "net": (pad.net, want["net"]),
    }
    for name, (mine, theirs) in facts.items():
        if mine != theirs:
            problems.append(f"{where}: {name} {mine} != {theirs}")
    stats = want["polygon"]
    polygon = pad.polygon()
    if polygon is None:
        counted["custom pads, outline box"] += 1
        if not within(pad.bbox(), stats["bbox"], CURVED):
            problems.append(f"{where}: custom outline {pad.bbox()} != {stats['bbox']}")
        return problems
    exact = "vertices" in stats and len(polygon) == len(stats["vertices"])
    if exact:
        counted[f"{shape} pads, vertex by vertex"] += 1
        # To the nanometre: KiCad's own corners can land one either side.
        theirs = [tuple(v) for v in stats["vertices"]]
        if not all(within(a, b, 1) for a, b in zip(sorted(polygon), theirs, strict=True)):
            problems.append(f"{where}: vertices {sorted(polygon)} != {theirs}")
    elif shape in ("rect", "trapezoid"):
        count = len(stats.get("vertices", []))
        problems.append(f"{where}: {shape} has {len(polygon)} vertices, KiCad {count}")
    if not within(geometry.bbox(polygon), stats["bbox"], 2 if exact else CURVED):
        problems.append(f"{where}: box {geometry.bbox(polygon)} != {stats['bbox']}")
    area = geometry.area(polygon)
    if abs(area - stats["area"]) > max(CURVED * perimeter(polygon), 1e-6 * stats["area"]):
        problems.append(f"{where}: area {area} != {stats['area']}")
    return problems


def compare(board: Board, truth: dict) -> tuple[list[str], collections.Counter]:
    """Every difference between the model and pcbnew, and what was compared."""
    problems: list[str] = []
    counted: collections.Counter = collections.Counter()
    enabled = set(truth["copper_layers"])

    def relevant(layers):
        return sorted(n for n in set(layers) if n in enabled or not n.endswith(".Cu"))

    if board.copper_layers != truth["copper_layers"]:
        problems.append(f"copper layers {board.copper_layers} != {truth['copper_layers']}")

    ours = {(f.reference, f.position): f for f in board.footprints}
    for want in truth["footprints"]:
        fp = ours.get((want["reference"], tuple(want["position"])))
        if fp is None:
            problems.append(f"footprint {want['reference']} at {want['position']} not found")
            continue
        counted["footprints"] += 1
        if (fp.layer, turned(fp.angle)) != (want["layer"], turned(want["orientation"])):
            problems.append(
                f"{fp.reference}: {fp.layer} {fp.angle} != {want['layer']} {want['orientation']}"
            )
        by_number = collections.defaultdict(list)
        for pad in fp.pads:
            by_number[pad.number].append(pad)
        for wp in want["pads"]:
            here = [p for p in by_number[wp["number"]] if p.position == tuple(wp["position"])]
            if not here:
                problems.append(f"{fp.reference} pad {wp['number']}: not at {wp['position']}")
                continue

            # Two pads can share a number and a place -- a mounting hole's ring
            # and its "connect" pad, or one on each side.
            def agreement(p, wp=wp):
                size, layers = tuple(wp["size"]), wp["layers"]
                return (p.kind == wp["type"], p.size == size, relevant(p.layers) == layers)

            pad = max(here, key=agreement)
            counted["pads"] += 1
            where = f"{fp.reference} pad {pad.number}"
            problems += compare_pad(pad, wp, where, relevant, counted)

    mine = collections.Counter(
        (t.kind, t.layer, t.net, t.width, t.start, t.end, t.mid) for t in board.tracks
    )
    theirs = collections.Counter(
        (
            t["kind"], t["layer"], t["net"], t["width"], tuple(t["start"]), tuple(t["end"]),
            tuple(t["mid"]) if "mid" in t else None,
        )
        for t in truth["tracks"]
        if t["kind"] != "via"
    )  # fmt: skip
    counted["tracks and arcs"] += sum(theirs.values())
    if mine != theirs:
        extra, missing = elements(mine - theirs), elements(theirs - mine)
        problems.append(f"tracks: extra {extra}, missing {missing}")

    mine = collections.Counter(
        (v.net, v.position, v.diameter, v.drill, tuple(sorted(v.layers))) for v in board.vias
    )
    theirs = collections.Counter(
        (t["net"], tuple(t["position"]), t["width"], t["drill"], tuple(sorted(t["layers"])))
        for t in truth["tracks"]
        if t["kind"] == "via"
    )
    counted["vias"] += sum(theirs.values())
    if mine != theirs:
        extra, missing = elements(mine - theirs), elements(theirs - mine)
        problems.append(f"vias: extra {extra}, missing {missing}")

    def outline_box(zone):
        points = [p for poly in zone.outlines for p in poly]
        return list(geometry.bbox(points)) if points else None

    for want in truth["zones"]:
        # Teardrops are zones, many to a net under one name: the outline's
        # box is what tells them apart.
        key = (
            want["net"], want["name"], want["layers"], want["priority"], want["outline"]["bbox"]
        )  # fmt: skip
        zone = next(
            (
                z
                for z in board.zones
                if (z.net, z.name, sorted(z.layers), z.priority, outline_box(z)) == key
            ),
            None,
        )
        if zone is None:
            problems.append(f"zone {want['net']!r} {want['name']!r} on {want['layers']} missing")
            continue
        counted["zones"] += 1
        outline = [p for poly in zone.outlines for p in poly]
        area = sum(geometry.area(poly) for poly in zone.outlines)
        # An outline may hold arcs, and two approximations of an arc differ.
        slack = max(geometry.MAX_ERROR * perimeter(outline), 1e-6 * area, 1)
        if abs(area - want["outline"]["area"]) > slack:
            problems.append(f"zone {want['net']!r}: outline {area} != {want['outline']['area']}")
        for layer, stats in want["filled"].items():
            filled = sum(geometry.area(poly) for poly in zone.filled.get(layer, []))
            if abs(filled - stats["area"]) > max(1e-6 * stats["area"], 1):
                problems.append(f"zone {want['net']!r}: {layer} fill {filled} != {stats['area']}")

    box = board.edge_bbox()
    if box is not None:
        counted["outlines"] += 1
        if not within(box, truth["edge_bbox"], 2 * geometry.MAX_ERROR + 2):
            problems.append(f"outline box {box} != {truth['edge_bbox']}")
    return problems, counted


@needs_pcbnew
@pytest.mark.parametrize("path", demo_boards(), ids=lambda p: p.name)
def test_every_demo_board_reads_as_pcbnew_reads_it(path):
    problems, counted = compare(Board.load(path), oracle(path))
    assert problems == [], "\n".join(problems[:40])
    assert counted["footprints"] > 0 or path.name == "microwave.kicad_pcb", counted


@needs_pcbnew
def test_rare_pad_shapes_at_every_angle_on_both_sides(tmp_path):
    board = tmp_path / "shapes.kicad_pcb"
    footprints = DEMOS.parent / "footprints"
    proc = subprocess.run(
        [kicad_python(), str(ORACLE), "--build-shapes-board", str(footprints), str(board)],
        capture_output=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-2000:]
    problems, counted = compare(Board.load(board), oracle(board))
    assert problems == [], "\n".join(problems[:40])
    # What this board exists for, counted rather than assumed.
    assert counted["trapezoid pads, vertex by vertex"] >= 40, counted
    assert counted["chamfered_rect pads, vertex by vertex"] >= 10, counted
    assert counted["custom pads, outline box"] >= 20, counted
