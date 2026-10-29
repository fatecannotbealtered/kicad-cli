"""A saved one-spoke relief must not disappear at exact polygon vertices."""

from pathlib import Path

from kicad_cli.fileformat.board import Board
from kicad_cli.fileformat.connectivity import Polygon
from kicad_cli.fileformat.drc import Run, Settings, fills
from kicad_cli.fileformat.sexpr import Document


def test_distinct_crossings_include_vertex_hits_once_and_ignore_duplicate_edges():
    ring = [(0, 0), (10, 0), (10, 10), (0, 10)]
    polygon = Polygon([(2, -2), (8, -2), (8, 0), (8, 5), (2, 5), (2, 0)])
    hits = fills._crossings(ring, [(0, polygon)])
    assert len(hits) == 2
    assert len(fills._crossings(ring, [(0, Polygon(polygon.points * 2))])) == 2


def test_saved_thermal_single_spoke_with_vertices_on_gap_is_a_real_violation(tmp_path):
    # Standalone geometric fixture, same native rule and thermal semantics as
    # the externally recorded one-pad/one-zone counterexample.
    source = """(kicad_pcb (version 20260206) (layers (0 "F.Cu" signal) (2 "B.Cu" signal))
      (footprint "Owned" (layer "F.Cu") (at 5 5)
       (property "Reference" "R1") (property "Value" "thermal") (attr smd)
       (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net "GND")))
      (zone (net "GND") (layer "F.Cu") (connect_pads (clearance 0.3)) (min_thickness 0.25)
       (filled_areas_thickness no) (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))
       (polygon (pts (xy 1 1) (xy 9 1) (xy 9 9) (xy 1 9)))
       (filled_polygon (layer "F.Cu")
        (pts (xy 4.75 5) (xy 4.75 4.25) (xy 4.75 3) (xy 5.25 3)
             (xy 5.25 4.25) (xy 5.25 5)))))"""
    board = Board(Document.parse(source), Path(tmp_path / "case.kicad_pcb"))
    run = Run(board, Settings.of(None))
    fills.check(run)
    violations = [v for v in run.found if v.rule == "starved_thermal"]
    assert len(violations) == 1 and "actual 1" in violations[0].message
