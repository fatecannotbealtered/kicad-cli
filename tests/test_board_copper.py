"""Unit tests of the board copper group: via create, zone list, zone create, zone delete.

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
from kicad_cli.adapter.pcb_edit_board import UUID
from kicad_cli.adapter.handlers.board_copper import HANDLERS
from kicad_cli.commands.board_copper import COMMANDS
from test_board_edit import EMPTY_REPORT, ROOT, BoardEditCase, finding

SQUARE = [{"x_mm": 0.0, "y_mm": 0.0}, {"x_mm": 60.0, "y_mm": 0.0}, {"x_mm": 60.0, "y_mm": 50.0}, {"x_mm": 0.0, "y_mm": 50.0}]


class CopperCase(BoardEditCase):
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

    def refused(self, command: str, inputs: dict) -> str:
        with self.assertRaises(AdapterError) as ctx:
            self.call(command, self.board, inputs).preview()
        return ctx.exception.data["code"]


class RegistrationTest(unittest.TestCase):
    def test_every_registration_is_valid(self):
        contract = Contract(ROOT / "contract" / "driver-contract.json")
        paths = {domain_command(reg, contract, ["file"]).path for reg in COMMANDS}
        self.assertEqual(paths, set(HANDLERS))

    def test_only_delete_is_irreversible(self):
        marks = {reg["path"]: reg["irreversible"] for reg in COMMANDS}
        self.assertEqual(marks, {"via create": False, "zone list": False, "zone create": False, "zone delete": True})


class ViaTest(CopperCase):
    VIA = {"net": "GND", "x_mm": 20.0, "y_mm": 30.0}

    def test_a_via_takes_the_net_class_size_and_says_what_it_touches(self):
        pv, facts, rb = self.run_write("via create", self.board, dict(self.VIA))
        result = facts["result"]
        self.assertEqual((result["diameter_mm"], result["drill_mm"], result["layers"]), (0.6, 0.3, ["F.Cu", "B.Cu"]))
        self.assertEqual(set(result["connects_to"]), {"F.Cu", "B.Cu"})
        self.assert_read_back_matches(pv, rb)
        via = next(v for v in Board.load(self.board).vias if v.node.find("uuid").value(1) == result["id"])
        self.assertEqual((via.net, via.position, via.diameter, via.drill), ("GND", (20_000_000, 30_000_000), 600_000, 300_000))

    def test_a_batch_is_one_preview_and_one_apply_per_via(self):
        items = {"items": [dict(self.VIA), dict(self.VIA, x_mm=25.0, diameter_mm=0.8, drill_mm=0.4)]}
        pv = self.call("via create", self.board, items).preview()
        self.assertEqual(len(pv["targets"]), 2)
        for index, item in enumerate(items["items"]):
            facts = self.call("via create", self.board, {"items": [item]}, operation_id=f"op-{index}").apply()
            self.assertTrue(pv["targets"][index]["key"].endswith("/vias/" + facts["result"]["id"]))
        rb = self.call("via create", self.board, items, targets=[t["key"] for t in pv["targets"]],
                       fields=sorted({c["field"] for c in pv["changes"]})).read_back()
        self.assert_read_back_matches(pv, rb)

    def test_bad_vias_are_refused(self):
        self.assertEqual(self.refused("via create", dict(self.VIA, net="NO_SUCH_NET")), "E_VALIDATION")
        self.assertEqual(self.refused("via create", dict(self.VIA, drill_mm=0.7)), "E_VALIDATION")
        self.assertEqual(self.refused("via create", {"net": "GND", "x_mm": 1.0}), "E_VALIDATION")
        self.call("via create", self.board, dict(self.VIA), operation_id="op").apply()
        self.assertEqual(self.refused("via create", dict(self.VIA)), "E_VALIDATION")

    def test_a_via_made_again_where_a_moved_one_was_gets_its_own_uuid(self):
        first = self.call("via create", self.board, dict(self.VIA), operation_id="op").apply()["result"]["id"]
        board = Board.load(self.board)
        node = next(v.node for v in board.vias if v.node.find("uuid").value(1) == first)
        node.find("at").set(1, "22")
        self.board.write_text(board.document.dumps(), encoding="utf-8", newline="")
        pv, facts, rb = self.run_write("via create", self.board, dict(self.VIA))
        self.assertNotEqual(facts["result"]["id"], first)
        self.assertEqual((facts["result"]["x_mm"], facts["result"]["y_mm"]), (20.0, 30.0))
        self.assert_read_back_matches(pv, rb)
        found = UUID.findall(self.board.read_text(encoding="utf-8"))
        self.assertEqual(len(found), len(set(found)))

    def test_a_via_that_adds_a_drc_error_is_refused(self):
        self.after_report = {**EMPTY_REPORT, "violations": [finding("clearance", "error", "x-1", "x-2")]}
        pv = self.call("via create", self.board, dict(self.VIA)).preview()
        self.assertFalse(pv["can_apply"])
        self.assertIn("clearance", pv["blocked_by"][0])


class ZoneTest(CopperCase):
    ZONE = {"net": "GND", "layer": "B.Cu", "points": SQUARE}

    def test_a_zone_is_written_listed_and_deleted(self):
        pv, facts, rb = self.run_write("zone create", self.board, dict(self.ZONE))
        self.assert_read_back_matches(pv, rb)
        uid = facts["result"]["id"]
        self.assertEqual(facts["result"]["points"], SQUARE)
        rows = self.call("zone list", self.board, {"net": "GND"}).read()["items"]
        self.assertEqual([(r["id"], r["layers"], r["filled_in_file"]) for r in rows], [(uid, ["B.Cu"], False)])
        self.assertEqual(self.call("zone list", self.board, {"layer": "F.Cu"}).read()["items"], [])
        pv, facts, rb = self.run_write("zone delete", self.board, {"ids": [uid]})
        self.assertEqual(facts["result"]["deleted"][0]["points"], SQUARE)
        self.assert_read_back_matches(pv, rb)
        self.assertEqual(Board.load(self.board).zones, [])

    def test_a_hole_in_a_zone_is_listed_and_reported_when_deleted(self):
        uid = self.call("zone create", self.board, dict(self.ZONE), operation_id="op").apply()["result"]["id"]
        board = Board.load(self.board)
        board.zones[0].node.append(copy(Document.parse("(polygon (pts (xy 10 10) (xy 20 10) (xy 20 20)))").root))
        self.board.write_text(board.document.dumps(), encoding="utf-8", newline="")
        hole = [{"x_mm": 10.0, "y_mm": 10.0}, {"x_mm": 20.0, "y_mm": 10.0}, {"x_mm": 20.0, "y_mm": 20.0}]
        row = self.call("zone list", self.board, {}).read()["items"][0]
        self.assertEqual((row["points"], row["holes"]), (SQUARE, [hole]))
        facts = self.call("zone delete", self.board, {"ids": [uid]}, operation_id="op-2").apply()
        self.assertEqual(facts["result"]["deleted"][0]["holes"], [hole])

    def test_bad_zones_are_refused(self):
        self.assertEqual(self.refused("zone create", dict(self.ZONE, points=SQUARE[:2])), "E_VALIDATION")
        self.assertEqual(self.refused("zone create", dict(self.ZONE, layer="Edge.Cuts")), "E_VALIDATION")
        self.assertEqual(self.refused("zone create", dict(self.ZONE, points=SQUARE + SQUARE[:1])), "E_VALIDATION")
        self.call("zone create", self.board, dict(self.ZONE), operation_id="op").apply()
        self.assertEqual(self.refused("zone create", dict(self.ZONE)), "E_VALIDATION")
        self.assertEqual(self.refused("zone delete", {"ids": ["not-a-zone"]}), "E_NOT_FOUND")

    def test_rule_areas_are_not_deleted(self):
        drc1 = self.project("drc1") / "drc1.kicad_pcb"
        keepouts = [z for z in Board.load(drc1).zones if z.rule_area]
        if not keepouts:
            self.skipTest("the fixture has no rule area")
        with self.assertRaises(AdapterError) as ctx:
            self.call("zone delete", drc1, {"ids": [keepouts[0].node.find("uuid").value(1)]}).preview()
        self.assertEqual(ctx.exception.data["code"], "E_UNSUPPORTED")


if __name__ == "__main__":
    unittest.main()
