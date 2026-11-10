"""Unit tests of the board shape group: board get, board update, footprint update.

KiCad's DRC is replaced by a mock (``kicad_tools``), so these tests check kicad-cli's own logic
on copies of the fixtures.
"""
from __future__ import annotations

import unittest
from unittest import mock

from kicad_cli._driver import AdapterError
from kicad_cli._driver.contract import Contract
from kicad_cli._driver.registry import domain_command
from kicad_cli.adapter import kicad_tools
from kicad_cli.adapter.fileformat.board import Board
from kicad_cli.adapter.fileformat.sexpr import Document, copy
from kicad_cli.adapter.handlers.board_read import reference_text
from kicad_cli.adapter.handlers.board_shape import HANDLERS, outline
from kicad_cli.commands.board_shape import COMMANDS
from test_board_edit import EMPTY_REPORT, ROOT, BoardEditCase, finding


class ShapeCase(BoardEditCase):
    def setUp(self) -> None:
        super().setUp()
        self.board = self.project("schmitt") / "schmitt.kicad_pcb"
        self.after_report = EMPTY_REPORT
        p = mock.patch.object(kicad_tools, "drc", side_effect=lambda kicad, path, parity, **kw:
                              EMPTY_REPORT if path.parent.name == "before" else self.after_report)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.dict("test_board_edit.HANDLERS", HANDLERS)
        p.start()
        self.addCleanup(p.stop)

    def refused(self, command: str, doc, inputs: dict) -> str:
        with self.assertRaises(AdapterError) as ctx:
            self.call(command, doc, inputs).preview()
        return ctx.exception.data["code"]


class RegistrationTest(unittest.TestCase):
    def test_every_registration_is_valid(self):
        contract = Contract(ROOT / "contract" / "driver-contract.json")
        paths = {domain_command(reg, contract, ["file"]).path for reg in COMMANDS}
        self.assertEqual(paths, set(HANDLERS))


class BoardTest(ShapeCase):
    def test_board_get_reads_the_outline_and_where_parts_sit(self):
        data = self.call("board get", self.board, {}).read()
        self.assertEqual(data["outline"], {"shape": "rectangle", "x_mm": 5.85, "y_mm": 7.2, "width_mm": 26.95,
                                           "height_mm": 26.55})
        self.assertEqual(data["copper_layers"], ["F.Cu", "B.Cu"])
        self.assertEqual(data["footprints_outside"], [])
        self.assertEqual(data["margins_mm"], {"left": 4.375, "top": 7.025, "right": 7.32, "bottom": 5.975})
        mini = self.project("mini") / "mini.kicad_pcb"
        self.assertEqual(self.call("board get", mini, {}).read()["outline"]["shape"], "none")

    def test_four_lines_become_one_rectangle(self):
        pv, facts, rb = self.run_write("board update", self.board, {"width_mm": 25.0, "height_mm": 20.0})
        self.assertEqual([(c["field"], c["before"], c["after"]) for c in pv["changes"]],
                         [("x_mm", 5.85, 5.85), ("y_mm", 7.2, 7.2), ("width_mm", 26.95, 25.0), ("height_mm", 26.55, 20.0)])
        self.assert_read_back_matches(pv, rb)
        kind, box, shapes = outline(Board.load(self.board))
        self.assertEqual((kind, box, [s.head for s in shapes]), ("rectangle", (5_850_000, 7_200_000, 30_850_000, 27_200_000),
                                                                 ["gr_rect"]))
        self.assertIn("footprints_outside", facts["result"])

    def test_a_rectangle_is_edited_in_place(self):
        drc1 = self.project("drc1") / "drc1.kicad_pcb"
        before = drc1.read_text(encoding="utf-8")
        self.run_write("board update", drc1, {"x_mm": 12.0})
        after = drc1.read_text(encoding="utf-8")
        changed = [(a, b) for a, b in zip(before.splitlines(), after.splitlines(), strict=True) if a != b]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0][0].replace("(start 10 10) (end 180 140)", "(start 12 10) (end 182 140)"), changed[0][1])

    def test_bad_outlines_are_refused(self):
        self.assertEqual(self.refused("board update", self.board, {}), "E_VALIDATION")
        self.assertEqual(self.refused("board update", self.board, {"width_mm": 0.0}), "E_VALIDATION")
        self.assertEqual(self.refused("board update", self.board, {"x_mm": 5.85}), "E_VALIDATION")
        text = self.board.read_text(encoding="utf-8")
        board = Board.load(self.board)
        for shape in board.edge_shapes():
            board.root.remove(shape)
        self.board.write_text(board.document.dumps(), encoding="utf-8", newline="")
        self.assertEqual(self.refused("board update", self.board, {"width_mm": 10.0}), "E_VALIDATION")
        pv = self.call("board update", self.board, {"x_mm": 0.0, "y_mm": 0.0, "width_mm": 40.0, "height_mm": 40.0}).preview()
        self.assertEqual([c["before"] for c in pv["changes"]], [None, None, None, None])
        arc = '\t(gr_arc (start 1 1) (mid 2 0) (end 3 1) (stroke (width 0.05) (type default)) (layer "Edge.Cuts"))\n'
        self.board.write_text(text.replace("\n\t(embedded_fonts", "\n" + arc + "\t(embedded_fonts", 1), encoding="utf-8")
        self.assertEqual(self.refused("board update", self.board, {"width_mm": 10.0}), "E_UNSUPPORTED")

    def without_outline(self) -> Board:
        board = Board.load(self.board)
        for shape in board.edge_shapes():
            board.root.remove(shape)
        return board

    def test_an_outline_with_an_arc_is_not_a_rectangle(self):
        board = self.without_outline()
        board.root.append(copy(Document.parse(
            "(gr_poly (pts (xy 0 0) (xy 40 0) (xy 40 40) (xy 0 40) (arc (start 0 40) (mid -5 20) (end 0 0)))"
            ' (stroke (width 0.05) (type default)) (fill no) (layer "Edge.Cuts"))').root))
        self.board.write_text(board.document.dumps(), encoding="utf-8", newline="")
        self.assertEqual(self.call("board get", self.board, {}).read()["outline"]["shape"], "other")
        self.assertEqual(self.refused("board update", self.board, {"width_mm": 30.0}), "E_UNSUPPORTED")

    def test_a_locked_outline_is_not_changed(self):
        board = Board.load(self.board)
        board.edge_shapes()[0].append(copy(Document.parse("(locked yes)").root))
        self.board.write_text(board.document.dumps(), encoding="utf-8", newline="")
        with self.assertRaises(AdapterError) as ctx:
            self.call("board update", self.board, {"width_mm": 20.0}).preview()
        self.assertIn("locked", str(ctx.exception))

    def test_an_outline_that_adds_a_drc_error_is_refused(self):
        self.after_report = {**EMPTY_REPORT, "violations": [finding("copper_edge_clearance", "error", "x-1")]}
        pv = self.call("board update", self.board, {"width_mm": 5.0}).preview()
        self.assertFalse(pv["can_apply"])


class TextTest(ShapeCase):
    def test_the_reference_text_goes_where_asked(self):
        pv, facts, rb = self.run_write("footprint update", self.board,
                                       {"id": "C1", "reference_x_mm": 20.0, "reference_y_mm": 12.0,
                                        "reference_rotation_deg": 90.0})
        self.assert_read_back_matches(pv, rb)
        result = facts["result"]
        self.assertEqual((result["reference_text"]["x_mm"], result["reference_text"]["y_mm"],
                          result["reference_text"]["rotation_deg"]), (20.0, 12.0, 90.0))
        self.assertIsNotNone(result["nearest_courtyard"])

    def test_on_a_turned_footprint_the_text_lands_on_the_board_point(self):
        board = self.project("rotate") / "rotated.kicad_pcb"
        self.run_write("footprint update", board, {"id": "U2", "reference_x_mm": 31.0, "reference_y_mm": 35.5})
        u2 = next(fp for fp in Board.load(board).footprints if fp.reference == "U2")
        text = reference_text(u2)
        self.assertEqual((text["x_mm"], text["y_mm"], text["rotation_deg"]), (31.0, 35.5, 45.0))

    def test_turning_a_text_leaves_its_place_as_written(self):
        board = self.project("rotate") / "rotated.kicad_pcb"

        def place() -> list:
            u2 = next(fp for fp in Board.load(board).footprints if fp.reference == "U2")
            at = next(p for p in u2.node.find_all("property") if p.value(1) == "Reference").find("at")
            return [at.atom(1), at.atom(2)]

        self.run_write("footprint move", board, {"id": "U2", "rotation_deg": 47.0})
        before = place()
        self.run_write("footprint update", board, {"id": "U2", "reference_rotation_deg": 90.0})
        self.assertEqual(place(), before)

    def test_bad_text_moves_are_refused(self):
        self.assertEqual(self.refused("footprint update", self.board, {"id": "C1"}), "E_VALIDATION")
        self.assertEqual(self.refused("footprint update", self.board, {"id": "C99", "reference_x_mm": 1.0}),
                         "E_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
