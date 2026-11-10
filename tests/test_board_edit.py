"""Unit tests of the board edits: netlist import, footprint move, track create, track delete.

KiCad itself never runs here: its netlist export and its DRC are replaced by mocks
(``kicad_tools``), so these tests check kicad-cli's own logic on copies of the fixtures.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kicad_cli._driver import AdapterError
from kicad_cli._driver.contract import Contract
from kicad_cli._driver.registry import domain_command
from kicad_cli.adapter import base, kicad_tools, pcb_edit_drc, pcb_edit_links
from kicad_cli.adapter.fileformat import netlist
from kicad_cli.adapter.fileformat.board import Board
from kicad_cli.adapter.fileformat.circuit import Design
from kicad_cli.adapter.fileformat.sexpr import Document, copy
from kicad_cli.adapter.handlers.board_edit import HANDLERS
from kicad_cli.adapter.install import Installation
from kicad_cli.adapter.pcb_edit_board import UUID
from kicad_cli.commands.board_edit import COMMANDS

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
EMPTY_REPORT = {"violations": [], "unconnected_items": [], "schematic_parity": []}


def finding(kind: str, severity: str, *uuids: str, excluded: bool = False) -> dict:
    return {"type": kind, "description": kind.replace("_", " "), "severity": severity, "excluded": excluded,
            "items": [{"uuid": u, "description": f"item {u[:8]}", "pos": {"x": 1.0, "y": 2.0}} for u in uuids]}


def pad_uuids(board: Path, net: str) -> list[str]:
    out = []
    for fp in Board.load(board).footprints:
        for pad in fp.pads:
            if pad.net == net and pad.node is not None and pad.node.find("uuid") is not None:
                out.append(pad.node.find("uuid").value(1))
    return out


class BoardEditCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="kicad-cli-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        config = self.tmp / "kicad-config"
        config.mkdir()
        patches = [
            mock.patch.dict(os.environ, {"KICAD_CONFIG_HOME": str(config)}),
            mock.patch.object(base, "_found", return_value=Installation(root=self.tmp, cli=self.tmp / "kicad-cli",
                                                                           version="10.0.6")),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def project(self, name: str) -> Path:
        shutil.copytree(FIXTURES / name, self.tmp / name)
        return self.tmp / name

    @staticmethod
    def call(command: str, doc: Path, inputs: dict, **extra) -> base.Handler:
        return HANDLERS[command](base.Call.of({"command": command, "inputs": inputs, "document": str(doc), **extra}))

    def run_write(self, command: str, doc: Path, inputs: dict) -> tuple[dict, dict, dict]:
        """preview, apply and read_back, as the runtime calls them for one object."""
        pv = self.call(command, doc, inputs).preview()
        facts = self.call(command, doc, inputs, operation_id="op-1").apply()
        targets = sorted({c["target"] for c in pv["changes"]})
        fields = sorted({c["field"] for c in pv["changes"]})
        rb = self.call(command, doc, inputs, operation_id="op-1", targets=targets, fields=fields).read_back()
        return pv, facts, rb

    def assert_read_back_matches(self, pv: dict, rb: dict, tolerance: float = 1e-6) -> None:
        for change in pv["changes"]:
            observed = rb["observed"][change["target"]][change["field"]]
            if isinstance(change["after"], float):
                self.assertAlmostEqual(observed, change["after"], delta=tolerance)
            else:
                self.assertEqual(observed, change["after"], change)


class RegistrationTest(unittest.TestCase):
    def test_every_registration_is_valid(self):
        contract = Contract(ROOT / "contract" / "driver-contract.json")
        paths = {domain_command(reg, contract, ["file"]).path for reg in COMMANDS}
        self.assertEqual(paths, set(HANDLERS))

    def test_only_delete_is_irreversible(self):
        marks = {reg["path"]: reg["irreversible"] for reg in COMMANDS}
        self.assertEqual(marks, {"netlist import": False, "footprint move": False, "track create": False,
                                 "track delete": True})


class NetlistImportTest(BoardEditCase):
    def setUp(self) -> None:
        super().setUp()
        self.dir = self.project("sch_edit")
        self.board = self.dir / "edit.kicad_pcb"
        self.text = netlist.dumps(netlist.build(Design(self.dir / "edit.kicad_sch")))
        p = mock.patch.object(kicad_tools, "netlist", side_effect=lambda kicad, sch: self.text)
        p.start()
        self.addCleanup(p.stop)

    def edit_board(self, old: str, new: str, count: int = 1) -> None:
        text = self.board.read_text(encoding="utf-8")
        self.assertIn(old, text)
        self.board.write_text(text.replace(old, new, count), encoding="utf-8", newline="")

    def test_a_board_in_step_changes_nothing(self):
        before = self.board.read_bytes()
        pv = self.call("netlist import", self.board, {}).preview()
        self.assertEqual(pv["changes"], [])
        facts = self.call("netlist import", self.board, {}, operation_id="op").apply()
        self.assertEqual((facts["change"], facts["persistence"]), ("not_applied", "unchanged"))
        self.assertEqual(self.board.read_bytes(), before)

    def test_a_value_follows_the_schematic(self):
        self.edit_board('"Value" "10k"', '"Value" "22k"')
        pv, facts, rb = self.run_write("netlist import", self.board, {})
        self.assertEqual([(c["field"], c["before"], c["after"]) for c in pv["changes"]], [("value", "22k", "10k")])
        self.assertEqual(facts["persistence"], "saved")
        self.assert_read_back_matches(pv, rb)

    def test_a_missing_footprint_is_added_beside_the_board(self):
        board = Board.load(self.board)
        r5 = next(fp for fp in board.footprints if fp.reference == "R5")
        board.root.remove(r5.node)
        self.board.write_text(board.document.dumps(), encoding="utf-8", newline="")
        pv, facts, rb = self.run_write("netlist import", self.board, {})
        key = base.key(self.board, "/footprints/R5")
        exists = [c for c in pv["changes"] if c["target"] == key and c["field"] == "exists"]
        self.assertEqual(exists, [{"target": key, "field": "exists", "before": False, "after": True}])
        self.assert_read_back_matches(pv, rb)
        added = facts["result"]["added"]
        self.assertEqual([a["ref"] for a in added], ["R5"])
        outline = Board.load(self.board).edge_bbox()
        if outline is not None:
            self.assertGreater(added[0]["x_mm"], outline[2] / 1e6)

    def test_a_renamed_net_takes_its_copper_along(self):
        self.edit_board('"/SIG"', '"OLD_SIG"', count=-1)
        pv, facts, rb = self.run_write("netlist import", self.board, {})
        copper = {c["target"].rsplit("/nets/", 1)[1]: (c["before"], c["after"]) for c in pv["changes"]
                  if c["field"] == "copper_items"}
        self.assertEqual(copper["OLD_SIG"][1], 0)
        self.assertGreater(copper["/SIG"][1], 0)
        self.assertEqual(facts["result"]["renamed_nets"], [{"from": "OLD_SIG", "to": "/SIG"}])
        self.assert_read_back_matches(pv, rb)
        self.assertNotIn('"OLD_SIG"', self.board.read_text(encoding="utf-8"))

    def test_an_unmatched_footprint_is_listed_not_deleted(self):
        board = Board.load(self.board)
        r2 = next(fp for fp in board.footprints if fp.reference == "R2")
        r2.node.find("path").set(1, '"/00000000-0000-0000-0000-000000000000"')
        self.board.write_text(board.document.dumps(), encoding="utf-8", newline="")
        _, facts, _ = self.run_write("netlist import", self.board, {})
        self.assertIn({"ref": "R2", "footprint": "fixture:R_0805"}, facts["result"]["not_removed"])
        refs = [fp.reference for fp in Board.load(self.board).footprints]
        self.assertEqual(refs.count("R2"), 2)

    def test_an_unannotated_schematic_is_refused(self):
        self.text = self.text.replace('(ref "R1")', '(ref "R?")', 1)
        with self.assertRaises(AdapterError) as ctx:
            self.call("netlist import", self.board, {}).preview()
        self.assertEqual(ctx.exception.data["code"], "E_VALIDATION")

    def test_a_board_kicad_has_open_is_not_written(self):
        self.edit_board('"Value" "10k"', '"Value" "22k"')
        base.lock_file(self.board).write_text("lock", encoding="utf-8")
        with self.assertRaises(AdapterError) as ctx:
            self.call("netlist import", self.board, {}, operation_id="op").apply()
        self.assertEqual((ctx.exception.data["code"], ctx.exception.data["knowledge"]),
                         ("E_CONFLICT", ["files-open-in-kicad"]))

    def test_an_older_board_is_read_only(self):
        mini = self.project("mini") / "mini.kicad_pcb"
        with self.assertRaises(AdapterError) as ctx:
            self.call("netlist import", mini, {}).preview()
        self.assertEqual(ctx.exception.data["code"], "E_UNSUPPORTED")


class FootprintMoveTest(BoardEditCase):
    def setUp(self) -> None:
        super().setUp()
        self.board = self.project("schmitt") / "schmitt.kicad_pcb"
        self.reports: list[dict] = [EMPTY_REPORT, EMPTY_REPORT]
        self.staged: list[Path] = []

        def fake_drc(kicad, path, parity, **kw):
            self.staged.append(path)
            if path.stem == "schmitt":
                self.assertTrue((path.parent / "schmitt.kicad_pro").is_file(), "rules beside the staged board")
            self.assertFalse(parity)
            return self.reports[0] if path.parent.name == "before" else self.reports[1]

        p = mock.patch.object(kicad_tools, "drc", side_effect=fake_drc)
        p.start()
        self.addCleanup(p.stop)

    def test_a_move_changes_only_the_position(self):
        before = self.board.read_text(encoding="utf-8")
        pv, facts, rb = self.run_write("footprint move", self.board, {"id": "TP1", "x_mm": 23.5, "y_mm": 15.25})
        self.assertTrue(pv["can_apply"])
        self.assertEqual([(c["field"], c["before"], c["after"]) for c in pv["changes"]],
                         [("x_mm", 23.225, 23.5), ("y_mm", 15.0, 15.25)])
        self.assert_read_back_matches(pv, rb)
        after = self.board.read_text(encoding="utf-8")
        changed = [(a, b) for a, b in zip(before.splitlines(), after.splitlines(), strict=True) if a != b]
        self.assertEqual(changed, [("\t\t(at 23.225 15)", "\t\t(at 23.5 15.25)")])
        moved = facts["result"]["moved"][0]
        self.assertEqual((moved["from_x_mm"], moved["x_mm"], moved["rotation_deg"], moved["layer"]),
                         (23.225, 23.5, 0, "F.Cu"))
        self.assertEqual([p["number"] for p in moved["pads"]], ["1"])
        self.assertEqual(len(self.staged), 2)
        self.assertFalse(self.staged[0].parent.exists(), "the staged copies are deleted")

    def test_the_result_names_the_tracks_a_move_leaves_behind(self):
        board = Board.load(self.board)
        before = [hit for p in pcb_edit_links.pad_links(pcb_edit_links.copper(board), "TP1").values() for hit in p]
        self.assertTrue(before, "TP1 has a track in the fixture")
        _, facts, _ = self.run_write("footprint move", self.board, {"id": "TP1", "x_mm": 40.0, "y_mm": 40.0})
        moved = facts["result"]["moved"][0]
        self.assertEqual(moved["pads"][0]["connects_to"], [])
        self.assertEqual(moved["tracks_left_behind"], sorted(set(before)))

    def test_a_batch_moves_each_footprint(self):
        items = [{"id": "TP1", "x_mm": 22.0, "y_mm": 14.0}, {"id": "TP2", "x_mm": 18.0}]
        pv = self.call("footprint move", self.board, {"items": items}).preview()
        self.assertEqual([t["label"] for t in pv["targets"]], ["TP1", "TP2"])
        for item in items:  # the runtime applies one child per object
            self.call("footprint move", self.board, {"items": [item]}, operation_id="op").apply()
        rb = self.call("footprint move", self.board, {"items": items}, operation_id="op",
                       targets=[t["key"] for t in pv["targets"]], fields=["x_mm", "y_mm"]).read_back()
        self.assert_read_back_matches(pv, rb)

    def test_a_new_drc_error_blocks_the_move(self):
        tp1 = pad_uuids(self.board, "+3V3")[0]
        self.reports = [EMPTY_REPORT, {**EMPTY_REPORT, "violations": [finding("courtyards_overlap", "error", tp1)]}]
        pv = self.call("footprint move", self.board, {"id": "TP1", "x_mm": 24.0}).preview()
        self.assertFalse(pv["can_apply"])
        self.assertIn("courtyards_overlap", pv["blocked_by"][0])

    def test_an_existing_violation_carried_along_is_not_new(self):
        tp1 = pad_uuids(self.board, "+3V3")[0]
        old = finding("silk_overlap", "error", tp1)
        self.reports = [{**EMPTY_REPORT, "violations": [old]}, {**EMPTY_REPORT, "violations": [old]}]
        pv = self.call("footprint move", self.board, {"id": "TP1", "x_mm": 24.0}).preview()
        self.assertTrue(pv["can_apply"])

    def test_a_new_warning_does_not_block(self):
        tp1 = pad_uuids(self.board, "+3V3")[0]
        self.reports = [EMPTY_REPORT, {**EMPTY_REPORT, "violations": [finding("silk_over_copper", "warning", tp1)]}]
        pv = self.call("footprint move", self.board, {"id": "TP1", "x_mm": 24.0}).preview()
        self.assertTrue(pv["can_apply"])
        self.assertIn("1 new warnings", pv["action"])

    def test_more_unconnected_items_block_the_move(self):
        pads = pad_uuids(self.board, "+3V3")
        self.reports = [EMPTY_REPORT, {**EMPTY_REPORT, "unconnected_items": [finding("unconnected_items", "error",
                                                                                      *pads[:2])]}]
        pv = self.call("footprint move", self.board, {"id": "TP1", "x_mm": 30.0}).preview()
        self.assertFalse(pv["can_apply"])
        self.assertIn("Net +3V3 would have 1 unconnected items instead of 0", pv["blocked_by"])

    def test_locked_unknown_and_empty_moves_are_refused(self):
        text = self.board.read_text(encoding="utf-8")
        self.board.write_text(text.replace("\t\t(at 23.225 15)", "\t\t(locked yes)\n\t\t(at 23.225 15)", 1),
                              encoding="utf-8", newline="")
        self.assert_refused("footprint move", self.board, {"id": "TP1", "x_mm": 1.0}, "E_VALIDATION")
        self.assert_refused("footprint move", self.board, {"id": "TP9", "x_mm": 1.0}, "E_NOT_FOUND")
        self.assert_refused("footprint move", self.board, {"id": "TP2"}, "E_VALIDATION")

    def test_a_footprint_holding_a_zone_is_not_moved(self):
        board = Board.load(self.board)
        tp1 = next(fp for fp in board.footprints if fp.reference == "TP1")
        tp1.node.append(copy(Document.parse(
            '(zone (net "") (layer "F.Cu") (uuid "11111111-2222-3333-4444-555555555555") '
            "(keepout (tracks not_allowed)) (polygon (pts (xy 20 14) (xy 21 14) (xy 21 15))))").root))
        self.board.write_text(board.document.dumps(), encoding="utf-8", newline="")
        self.assert_refused("footprint move", self.board, {"id": "TP1", "x_mm": 1.0}, "E_UNSUPPORTED")

    def test_through_hole_and_back_side_footprints_move(self):
        drc1 = self.project("drc1") / "drc1.kicad_pcb"
        for ref in ("TW1", "TF2"):
            with self.subTest(ref=ref):
                pv = self.call("footprint move", drc1, {"id": ref, "x_mm": 1.0}).preview()
                self.assertEqual(pv["changes"][0]["after"], 1.0)

    def test_turns_match_kicad(self):
        """pcbnew turned these footprints of base.kicad_pcb and saved rotated.kicad_pcb; R2 is on the back."""
        turns = {"R1": 90, "U1": 270, "J1": 90, "C1": 180, "U2": 45, "R2": 90, "D1": 180}
        base_board = self.project("rotate") / "base.kicad_pcb"
        items = [{"id": ref, "rotation_deg": float(angle)} for ref, angle in turns.items()]
        pv = self.call("footprint move", base_board, {"items": items}).preview()
        for item in items:
            self.call("footprint move", base_board, {"items": [item]}, operation_id="op").apply()
        rb = self.call("footprint move", base_board, {"items": items}, operation_id="op",
                       targets=[t["key"] for t in pv["targets"]], fields=["rotation_deg"]).read_back()
        self.assert_read_back_matches(pv, rb)

        def angles(path: Path) -> dict[str, list[str]]:
            out = {}
            for fp in Board.load(path).footprints:
                if fp.reference in turns:
                    at = [fp.node.find("at").dumps() if hasattr(fp.node.find("at"), "dumps") else str(fp.node.find("at").values())]
                    for child in fp.node.lists():
                        if child.head in ("pad", "property", "fp_text") and child.find("at") is not None:
                            at.append(f"{child.head} {child.value(1)} {child.find('at').values()}")
                    out[fp.reference] = at
            return out

        mine, kicads = angles(base_board), angles(self.tmp / "rotate" / "rotated.kicad_pcb")
        for ref in turns:
            self.assertEqual(mine[ref], kicads[ref], ref)

    def assert_refused(self, command: str, doc: Path, inputs: dict, code: str) -> None:
        with self.assertRaises(AdapterError) as ctx:
            self.call(command, doc, inputs).preview()
        self.assertEqual(ctx.exception.data["code"], code)


class TrackTest(BoardEditCase):
    def setUp(self) -> None:
        super().setUp()
        self.board = self.project("schmitt") / "schmitt.kicad_pcb"
        self.after_report = EMPTY_REPORT
        p = mock.patch.object(kicad_tools, "drc", side_effect=lambda kicad, path, parity, **kw:
                              EMPTY_REPORT if path.parent.name == "before" else self.after_report)
        p.start()
        self.addCleanup(p.stop)

    SEGMENT = {"net": "GND", "layer": "F.Cu", "x1_mm": 10.0, "y1_mm": 30.0, "x2_mm": 12.5, "y2_mm": 30.0}

    def test_a_segment_takes_the_net_class_width(self):
        pv, facts, rb = self.run_write("track create", self.board, dict(self.SEGMENT))
        self.assertEqual(facts["result"]["width_mm"], 0.2)
        self.assert_read_back_matches(pv, rb)
        board = Board.load(self.board)
        new = [t for t in board.tracks if t.node.find("uuid").value(1) == facts["result"]["id"]]
        self.assertEqual((new[0].net, new[0].layer, new[0].start, new[0].end),
                         ("GND", "F.Cu", (10_000_000, 30_000_000), (12_500_000, 30_000_000)))

    def test_the_result_says_what_each_end_touches(self):
        old = next(tr for tr in Board.load(self.board).tracks if tr.kind == "segment")
        start = {"x1_mm": old.end[0] / 1e6, "y1_mm": old.end[1] / 1e6}
        segment = {"net": old.net, "layer": old.layer, **start, "x2_mm": old.end[0] / 1e6 + 0.5, "y2_mm": old.end[1] / 1e6}
        _, facts, _ = self.run_write("track create", self.board, segment)
        result = facts["result"]
        self.assertIn(f"track {old.node.find('uuid').value(1)}", result["start"]["connects_to"])
        self.assertEqual(result["unconnected_ends"], [] if result["end"]["connects_to"] else ["end"])
        self.assertEqual((result["net"], result["layer"]), (old.net, old.layer))

    def test_the_preview_and_the_apply_make_the_same_segment(self):
        pv = self.call("track create", self.board, dict(self.SEGMENT, width_mm=0.3)).preview()
        facts = self.call("track create", self.board, dict(self.SEGMENT, width_mm=0.3), operation_id="op").apply()
        self.assertTrue(pv["targets"][0]["key"].endswith("/tracks/" + facts["result"]["id"]))

    def test_bad_segments_are_refused(self):
        refused = [
            (dict(self.SEGMENT, layer="F.SilkS"), "E_VALIDATION"),
            (dict(self.SEGMENT, net="NO_SUCH_NET"), "E_VALIDATION"),
            (dict(self.SEGMENT, x2_mm=10.0), "E_VALIDATION"),
            ({k: v for k, v in self.SEGMENT.items() if k != "y2_mm"}, "E_VALIDATION"),
        ]
        for inputs, code in refused:
            with self.subTest(inputs=inputs), self.assertRaises(AdapterError) as ctx:
                self.call("track create", self.board, inputs).preview()
            self.assertEqual(ctx.exception.data["code"], code)

    def test_a_duplicate_segment_is_refused(self):
        self.call("track create", self.board, dict(self.SEGMENT), operation_id="op").apply()
        with self.assertRaises(AdapterError) as ctx:
            self.call("track create", self.board, dict(self.SEGMENT)).preview()
        self.assertEqual(ctx.exception.data["code"], "E_VALIDATION")

    def test_a_segment_that_adds_a_drc_error_is_refused(self):
        self.after_report = {**EMPTY_REPORT, "violations": [finding("clearance", "error", "x-1", "x-2")]}
        pv = self.call("track create", self.board, dict(self.SEGMENT)).preview()
        self.assertFalse(pv["can_apply"])

    def test_a_track_drawn_again_after_the_first_was_moved_gets_its_own_uuid(self):
        first = self.call("track create", self.board, dict(self.SEGMENT), operation_id="op-1").apply()["result"]["id"]
        board = Board.load(self.board)
        node = next(t.node for t in board.tracks if t.node.find("uuid").value(1) == first)
        node.find("start").set(2, "31")
        node.find("end").set(2, "31")
        self.board.write_text(board.document.dumps(), encoding="utf-8", newline="")
        pv, facts, rb = self.run_write("track create", self.board, dict(self.SEGMENT))
        self.assertNotEqual(facts["result"]["id"], first)
        self.assert_read_back_matches(pv, rb)
        found = UUID.findall(self.board.read_text(encoding="utf-8"))
        self.assertEqual(len(found), len(set(found)))

    def test_a_segment_is_deleted(self):
        uid = Board.load(self.board).tracks[0].node.find("uuid").value(1)
        pv, facts, rb = self.run_write("track delete", self.board, {"ids": [uid]})
        deleted = facts["result"]["deleted"]
        self.assertEqual([d["id"] for d in deleted], [uid])
        self.assertEqual(set(deleted[0]), {"id", "type", "net", "layer", "width_mm", "start", "end"})
        self.assertEqual(deleted[0]["type"], "segment")
        self.assertIsInstance(deleted[0]["start"]["connects_to"], list)
        self.assert_read_back_matches(pv, rb)
        self.assertNotIn(uid, self.board.read_text(encoding="utf-8"))

    def test_a_via_is_deleted_and_arcs_and_unknown_tracks_are_refused(self):
        drc1 = self.project("drc1") / "drc1.kicad_pcb"
        board = Board.load(drc1)
        arc = next(t for t in board.tracks if t.kind == "arc").node.find("uuid").value(1)
        for uid, code in ((arc, "E_UNSUPPORTED"), ("not-a-track", "E_NOT_FOUND")):
            with self.subTest(uid=uid), self.assertRaises(AdapterError) as ctx:
                self.call("track delete", drc1, {"ids": [uid]}).preview()
            self.assertEqual(ctx.exception.data["code"], code)
        via = board.vias[0]
        uid = via.node.find("uuid").value(1)
        pv, facts, rb = self.run_write("track delete", drc1, {"ids": [uid]})
        self.assertTrue(pv["targets"][0]["key"].endswith("/vias/" + uid))
        deleted = facts["result"]["deleted"][0]
        self.assertEqual((deleted["type"], deleted["id"], deleted["net"]), ("via", uid, via.net))
        self.assertEqual(set(deleted["connects_to"]), set(via.layers))
        self.assert_read_back_matches(pv, rb)
        self.assertNotIn(uid, drc1.read_text(encoding="utf-8"))

    PATH = {"net": "GND", "layer": "F.Cu", "width_mm": 0.5,
            "points": [{"x_mm": 10.0, "y_mm": 30.0}, {"x_mm": 12.5, "y_mm": 30.0}, {"x_mm": 13.5, "y_mm": 31.0}]}

    def test_a_path_is_one_target_with_a_segment_per_corner(self):
        pv, facts, rb = self.run_write("track create", self.board, dict(self.PATH))
        self.assertEqual(len(pv["targets"]), 1)
        result = facts["result"]
        self.assertEqual(len(result["segments"]), 2)
        self.assertEqual(result["segments"][0], result["id"])
        self.assertEqual((result["start"]["x_mm"], result["end"]["x_mm"], result["end"]["y_mm"]), (10.0, 13.5, 31.0))
        self.assert_read_back_matches(pv, rb)
        new = {t.node.find("uuid").value(1): t for t in Board.load(self.board).tracks}
        self.assertEqual([(new[u].start, new[u].end) for u in result["segments"]],
                         [((10_000_000, 30_000_000), (12_500_000, 30_000_000)),
                          ((12_500_000, 30_000_000), (13_500_000, 31_000_000))])

    def test_a_batch_is_checked_once_and_applied_item_by_item(self):
        second = dict(self.PATH, points=[{"x_mm": 20.0, "y_mm": 30.0}, {"x_mm": 22.0, "y_mm": 30.0}])
        items = {"items": [dict(self.PATH), second]}
        with mock.patch.object(pcb_edit_drc, "precheck", wraps=pcb_edit_drc.precheck) as pre:
            pv = self.call("track create", self.board, items).preview()
        self.assertEqual(pre.call_count, 1)
        self.assertEqual(len(pv["targets"]), 2)
        for index, item in enumerate(items["items"]):
            facts = self.call("track create", self.board, {"items": [item]}, operation_id=f"op-{index}").apply()
            self.assertTrue(pv["targets"][index]["key"].endswith("/tracks/" + facts["result"]["id"]))
        targets = [t["key"] for t in pv["targets"]]
        fields = sorted({c["field"] for c in pv["changes"]})
        rb = self.call("track create", self.board, items, targets=targets, fields=fields).read_back()
        self.assert_read_back_matches(pv, rb)

    def test_bad_paths_are_refused(self):
        refused = [
            dict(self.PATH, points=[{"x_mm": 10.0, "y_mm": 30.0}]),
            dict(self.PATH, points=[{"x_mm": 10.0, "y_mm": 30.0}, {"x_mm": 10.0, "y_mm": 30.0}]),
            dict(self.PATH, points=[{"x": 10.0, "y": 30.0}, {"x_mm": 11.0, "y_mm": 30.0}]),
            dict(self.PATH, points=[{"x_mm": float("nan"), "y_mm": 30.0}, {"x_mm": 11.0, "y_mm": 30.0}]),
            dict(self.PATH, points=[{"x_mm": 10.0, "y_mm": 30.0}, {"x_mm": 12.5, "y_mm": 30.0},
                                    {"x_mm": 10.0, "y_mm": 30.0}]),
            {"items": [dict(self.PATH), dict(self.PATH, points=self.PATH["points"][:2])]},
        ]
        for inputs in refused:
            with self.subTest(inputs=inputs), self.assertRaises(AdapterError) as ctx:
                self.call("track create", self.board, inputs).preview()
            self.assertEqual(ctx.exception.data["code"], "E_VALIDATION")
        with self.assertRaises(AdapterError) as ctx:
            self.call("track create", self.board, dict(self.PATH, x1_mm=1.0)).preview()
        self.assertIn("not both", str(ctx.exception))


    def test_an_end_does_not_count_the_rest_of_its_own_path(self):
        short = dict(self.PATH, points=[{"x_mm": 7.0, "y_mm": 31.0}, {"x_mm": 7.1, "y_mm": 31.0},
                                        {"x_mm": 7.1, "y_mm": 32.0}])
        facts = self.call("track create", self.board, short, operation_id="op").apply()
        self.assertEqual(facts["result"]["unconnected_ends"], ["start", "end"])


class DrcCompareTest(unittest.TestCase):
    def test_findings_are_compared_as_a_multiset(self):
        a = finding("clearance", "error", "u1", "u2")
        before = {"violations": [a], "unconnected_items": []}
        after = {"violations": [a, dict(a)], "unconnected_items": []}
        result = pcb_edit_drc.compare(before, after, {})
        self.assertEqual(len(result.new_errors), 1)

    def test_excluded_findings_never_block(self):
        after = {"violations": [finding("clearance", "error", "u1", excluded=True)], "unconnected_items": []}
        result = pcb_edit_drc.compare(EMPTY_REPORT, after, {})
        self.assertFalse(result.blocked)

    def test_unconnected_items_are_counted_per_net(self):
        before = {"violations": [], "unconnected_items": [finding("unconnected_items", "error", "a", "b")]}
        after = {"violations": [], "unconnected_items": [finding("unconnected_items", "error", "a", "c")]}
        nets = {"a": "N1", "b": "N1", "c": "N1"}
        self.assertFalse(pcb_edit_drc.compare(before, after, nets).blocked)


if __name__ == "__main__":
    unittest.main()
