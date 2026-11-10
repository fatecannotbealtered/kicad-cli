"""Unit tests of the board read and output group: board create, check, export, render,
footprint list, footprint create and track list.

KiCad's own program never runs here: ``kicad_tools`` is replaced by fakes, and KiCad's
configuration folder points at an empty temporary folder.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kicad_cli._driver import AdapterError
from kicad_cli._driver.contract import Contract
from kicad_cli._driver.registry import domain_command
from kicad_cli._driver.targets import file_key
from kicad_cli.adapter import Adapter, base, kicad_tools, pcb_read_build, pcb_read_footprint_lib, pcb_read_netlist
from kicad_cli.adapter.fileformat.board import Board
from kicad_cli.adapter.fileformat.sexpr import Document
from kicad_cli.adapter.install import Installation
from kicad_cli.commands.board_read import COMMANDS
from tool_run import link, write_through_tool

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"

NETLIST = """(export (version "E")
  (components
    (comp (ref "R1") (value "10k") (footprint "fixture:R_0805")
      (sheetpath (names "/") (tstamps "/")) (tstamps "11111111-1111-4111-8111-111111111111"))
    (comp (ref "U1") (value "OPAMP") (footprint "fixture:SO8")
      (sheetpath (names "/amp/") (tstamps "/22222222-2222-4222-8222-222222222222/"))
      (tstamps "33333333-3333-4333-8333-333333333333"))
    (comp (ref "TP1") (value "TP") (footprint "fixture:R_0603")
      (property (name "exclude_from_board"))
      (sheetpath (names "/") (tstamps "/")) (tstamps "44444444-4444-4444-8444-444444444444")))
  (nets
    (net (code "1") (name "GND") (node (ref "R1") (pin "2")) (node (ref "U1") (pin "4")))
    (net (code "2") (name "/OUT") (node (ref "R1") (pin "1")) (node (ref "U1") (pin "1")) (node (ref "U1") (pin "9")))))
"""


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="kicad-cli-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        config = self.tmp / "kicad-config"
        (config / "10.0").mkdir(parents=True)
        patcher = mock.patch.dict(os.environ, {"KICAD_CONFIG_HOME": str(config)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.kicad = Installation(root=self.tmp / "kicad", cli=self.tmp / "kicad" / "bin" / "kicad-cli", version="10.0.6")
        found = mock.patch.object(base, "_found", return_value=self.kicad)
        found.start()
        self.addCleanup(found.stop)
        self.adapter = Adapter(None)

    def copy(self, name: str) -> Path:
        target = self.tmp / name
        shutil.copytree(FIXTURES / name, target)
        return target

    def request(self, command: str, doc: Path | None, **inputs) -> dict:
        req = {"command": command, "inputs": inputs}
        if doc is not None:
            req["document"] = str(doc)
        return req


class RegistrationTest(unittest.TestCase):
    def test_every_registration_is_valid(self):
        contract = Contract(ROOT / "contract" / "driver-contract.json")
        paths = [domain_command(reg, contract, ["file"]).path for reg in COMMANDS]
        self.assertEqual(paths, ["board create", "board check", "board export", "board render",
                                 "footprint list", "footprint create", "track list"])


class ReadTest(Case):
    def test_footprint_list_reports_position_side_and_courtyard(self):
        board = FIXTURES / "schmitt" / "schmitt.kicad_pcb"
        items = self.adapter.read(self.request("footprint list", board))["result"]["items"]
        refs = [i["id"] for i in items]
        self.assertIn("C1", refs)
        c1 = items[refs.index("C1")]
        self.assertEqual((c1["side"], c1["lib_id"]), ("front", "Capacitor_SMD:C_0603_1608Metric"))
        self.assertIsNotNone(c1["courtyard"])
        self.assertLess(c1["courtyard"]["min_x_mm"], c1["x_mm"])
        self.assertGreater(c1["courtyard"]["max_x_mm"], c1["x_mm"])
        self.assertTrue(all(set(p) == {"number", "net", "x_mm", "y_mm", "type", "width_mm", "height_mm"}
                            for p in c1["pads"]))
        self.assertEqual({p["type"] for p in c1["pads"]}, {"smd"})
        text = c1["reference_text"]
        self.assertEqual((text["layer"], text["hidden"]), ("F.SilkS", False))
        self.assertLess(abs(text["x_mm"] - c1["x_mm"]) + abs(text["y_mm"] - c1["y_mm"]), 5)

    def test_footprint_list_filters(self):
        board = FIXTURES / "schmitt" / "schmitt.kicad_pcb"
        items = self.adapter.read(self.request("footprint list", board, query="tp"))["result"]["items"]
        self.assertTrue(items and all(i["id"].startswith("TP") for i in items))
        self.assertEqual(self.adapter.read(self.request("footprint list", board, side="back"))["result"]["items"], [])
        with self.assertRaises(AdapterError):
            self.adapter.read(self.request("footprint list", board, side="top"))

    def test_track_list_reports_segments_and_vias(self):
        board = FIXTURES / "drc1" / "drc1.kicad_pcb"
        items = self.adapter.read(self.request("track list", board))["result"]["items"]
        kinds = {i["type"] for i in items}
        self.assertTrue(kinds & {"segment", "arc"})
        track = next(i for i in items if i["type"] == "segment")
        self.assertGreater(track["width_mm"], 0)
        self.assertTrue(track["id"])
        if "via" in kinds:
            via = next(i for i in items if i["type"] == "via")
            self.assertGreater(via["diameter_mm"], via["drill_mm"])
        some_net = track["net"]
        filtered = self.adapter.read(self.request("track list", board, net=some_net))["result"]["items"]
        self.assertTrue(filtered and all(i["net"] == some_net for i in filtered))

    def test_a_file_that_is_not_a_board_is_invalid(self):
        bad = self.tmp / "bad.kicad_pcb"
        bad.write_text("(kicad_sch (version 1))", encoding="utf-8")
        with self.assertRaises(AdapterError) as caught:
            self.adapter.read(self.request("track list", bad))
        self.assertEqual(caught.exception.data["code"], "E_VALIDATION")


class CheckTest(Case):
    def test_board_check_shapes_kicads_report(self):
        report = json.loads((FIXTURES / "drc1" / "drc1.kicad.json").read_text(encoding="utf-8"))
        board = FIXTURES / "drc1" / "drc1.kicad_pcb"
        with mock.patch.object(kicad_tools, "drc", return_value=report) as drc:
            result = self.adapter.read(self.request("board check", board))["result"]
        drc.assert_called_once()
        self.assertEqual(drc.call_args.kwargs["parity"], False)
        self.assertEqual(len(result["items"]), len(report["violations"]) + len(report["unconnected_items"]))
        self.assertFalse(result["passed"])
        self.assertEqual(result["summary"]["unconnected_items"]["error"], len(report["unconnected_items"]))
        self.assertNotIn("schematic_parity", result["summary"])
        first = result["items"][0]
        self.assertEqual(set(first), {"group", "type", "severity", "excluded", "description", "items"})
        self.assertTrue(first["items"][0]["uuid"])

    def test_board_check_adds_parity_beside_a_schematic(self):
        project = self.copy("schmitt")
        report = {"kicad_version": "10.0.6", "coordinate_units": "mm", "violations": [], "unconnected_items": [],
                  "schematic_parity": [{"type": "missing_footprint", "severity": "warning", "description": "x",
                                        "items": [{"uuid": "u", "description": "d", "pos": {"x": 1.5, "y": 2}}]}]}
        with mock.patch.object(kicad_tools, "drc", return_value=report) as drc:
            result = self.adapter.read(self.request("board check", project / "schmitt.kicad_pcb"))["result"]
        self.assertTrue(drc.call_args.kwargs["parity"])
        self.assertTrue(result["passed"])
        self.assertEqual(result["warnings"], 1)
        self.assertEqual(result["items"][0]["items"][0], {"uuid": "u", "description": "d", "x_mm": 1.5, "y_mm": 2.0})


class NetlistTest(unittest.TestCase):
    def test_components_carry_their_symbol_link(self):
        parsed = pcb_read_netlist.parse(NETLIST)
        by_ref = {c.ref: c for c in parsed.components}
        self.assertEqual(by_ref["R1"].path, "/11111111-1111-4111-8111-111111111111")
        self.assertEqual(by_ref["U1"].path, "/22222222-2222-4222-8222-222222222222/33333333-3333-4333-8333-333333333333")
        self.assertFalse(by_ref["TP1"].on_board)
        self.assertEqual([n["name"] for n in parsed.nets], ["GND", "/OUT"])

    def test_kicads_netlist_fixture(self):
        text = (FIXTURES / "schematic" / "rules.net").read_text(encoding="utf-8")
        parsed = pcb_read_netlist.parse(text)
        self.assertEqual(len(parsed.components), len(re.findall(r"\(comp\s", text)))
        self.assertTrue(all(c.path.startswith("/") for c in parsed.components))

    def test_not_a_netlist(self):
        with self.assertRaises(AdapterError):
            pcb_read_netlist.parse("(kicad_sch (version 1))")


class BoardCreateTest(Case):
    def setUp(self) -> None:
        super().setUp()
        self.project = self.copy("sch_edit")
        (self.project / "edit.kicad_pcb").unlink()
        self.schematic = self.project / "edit.kicad_sch"
        patcher = mock.patch.object(kicad_tools, "netlist", return_value=NETLIST)
        self.netlist = patcher.start()
        self.addCleanup(patcher.stop)

    def test_preview_lists_only_the_new_board(self):
        preview = self.adapter.preview(self.request("board create", self.schematic))
        board = self.project / "edit.kicad_pcb"
        self.assertTrue(preview["can_apply"])
        self.assertEqual(preview["targets"], [{"key": file_key(str(board)), "kind": "board", "label": "edit.kicad_pcb"}])
        self.assertEqual(preview["changes"], [{"target": file_key(str(board)), "field": "exists",
                                               "before": False, "after": True}])
        self.assertNotIn("pairs a board", preview["action"])
        self.assertFalse(board.exists())

    def test_a_board_named_unlike_the_schematic_is_not_the_projects(self):
        out = self.project / "fresh.kicad_pcb"
        preview = self.adapter.preview(self.request("board create", self.schematic, output=str(out)))
        self.assertIn("so fresh.kicad_pcb is not the board of the edit project", preview["action"])
        self.assertIn(f"--schematic {self.schematic}", preview["action"])
        facts = self.adapter.apply({**self.request("board create", self.schematic, output=str(out)), "operation_id": "op"})
        self.assertEqual(facts["files"], [{"path": str(out)}])
        self.assertEqual(sorted(p.name for p in self.project.glob("fresh.*")), ["fresh.kicad_pcb"])

    def test_apply_builds_a_linked_wired_board(self):
        facts = self.adapter.apply({**self.request("board create", self.schematic), "operation_id": "op-1"})
        board_path = self.project / "edit.kicad_pcb"
        self.assertEqual((facts["persistence"], facts["change"]), ("saved", "not_applicable"))
        self.assertEqual(facts["files"][0]["path"], str(board_path))
        self.assertEqual(facts["result"]["footprints"], 2)
        self.assertEqual(facts["result"]["skipped"], ["TP1"])
        self.assertEqual(facts["result"]["unmatched_pins"], ["U1.9 (/OUT)"])
        board = Board.load(board_path)
        fps = {fp.reference: fp for fp in board.footprints}
        self.assertEqual(sorted(fps), ["R1", "U1"])
        self.assertEqual(fps["U1"].library_id, "fixture:SO8")
        self.assertEqual(fps["U1"].node.find("path").value(1),
                         "/22222222-2222-4222-8222-222222222222/33333333-3333-4333-8333-333333333333")
        nets = {p.number: p.net for p in fps["R1"].pads}
        self.assertEqual(nets, {"1": "/OUT", "2": "GND"})
        self.assertEqual(len(board.edge_shapes()), 4)
        self.assertEqual(facts["files"], [{"path": str(board_path)}])
        self.assertFalse((self.project / "edit.kicad_prl").exists())
        observed = self.adapter.read_back({**self.request("board create", self.schematic), "operation_id": "op-1",
                                           "targets": [file_key(str(board_path))]})
        self.assertEqual(observed["observed"][file_key(str(board_path))], {"exists": True})

    def test_one_netlist_gives_one_board(self):
        tables = pcb_read_build.lib_tables.Tables.of(self.project, None)
        plan = pcb_read_build.plan(pcb_read_netlist.parse(NETLIST), tables)
        self.assertEqual(pcb_read_build.build(plan)[0], pcb_read_build.build(plan)[0])

    def test_outline_of_a_given_size(self):
        tables = pcb_read_build.lib_tables.Tables.of(self.project, None)
        plan = pcb_read_build.plan(pcb_read_netlist.parse(NETLIST), tables)
        text, summary = pcb_read_build.build(plan, (50.0, 40.0))
        self.assertEqual((summary["width_mm"], summary["height_mm"]), (50.0, 40.0))
        x1, y1, x2, y2 = Board(Document.parse(text)).edge_bbox()
        self.assertEqual((x2 - x1, y2 - y1), (50_100_000, 40_100_000))  # the 0.1 mm line counted

    def test_missing_footprints_block_the_preview(self):
        self.netlist.return_value = NETLIST.replace("fixture:SO8", "fixture:NOPE").replace('(footprint "fixture:R_0805")', "")
        preview = self.adapter.preview(self.request("board create", self.schematic))
        self.assertFalse(preview["can_apply"])
        self.assertEqual(len(preview["blocked_by"]), 2)

    def test_an_existing_board_is_never_overwritten(self):
        (self.project / "edit.kicad_pcb").write_text("(kicad_pcb (version 20260206))", encoding="utf-8")
        with self.assertRaises(AdapterError) as caught:
            self.adapter.preview(self.request("board create", self.schematic))
        self.assertEqual(caught.exception.data["code"], "E_VALIDATION")

    def test_width_without_height_is_a_usage_error(self):
        with self.assertRaises(AdapterError) as caught:
            self.adapter.preview(self.request("board create", self.schematic, width_mm=30.0))
        self.assertEqual(caught.exception.data["code"], "E_VALIDATION")


def fake_export(written: list[str]):
    def export(kicad, args, source, staging, timeout=40):
        raw = args[args.index("--output") + 1]
        out = Path(raw)
        if raw.endswith(os.sep):
            for name in written:
                (staging / name).write_text("x", encoding="utf-8")
        else:
            out.write_text("x", encoding="utf-8")
        return sorted(p for p in staging.rglob("*") if p.is_file())
    return export


class ExportTest(Case):
    def test_gerber_export_uses_the_saved_plot_settings(self):
        project = self.copy("schmitt")
        folder = self.tmp / "fab"
        req = self.request("board export", project / "schmitt.kicad_pcb", file_type="gerber", output=str(folder))
        preview = self.adapter.preview(req)
        self.assertEqual(preview["targets"][0]["key"], file_key(str(folder)))
        with mock.patch.object(kicad_tools, "export", side_effect=fake_export(["a-F_Cu.gtl", "a-B_Cu.gbl"])) as export:
            facts = self.adapter.apply({**req, "operation_id": "op"})
        args = export.call_args.args[1]
        self.assertEqual(args[:3], ["pcb", "export", "gerbers"])
        self.assertIn("--board-plot-params", args)
        self.assertIn("--check-zones", args)
        self.assertEqual(sorted(Path(f["path"]).name for f in facts["files"]), ["a-B_Cu.gbl", "a-F_Cu.gtl"])
        self.assertTrue((folder / "a-F_Cu.gtl").is_file())
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["a-B_Cu.gbl", "a-F_Cu.gtl"])
        self.assertFalse(any(p.name.startswith(".kicad-cli-export-") for p in folder.iterdir()))
        key = file_key(str(folder))
        observed = self.adapter.read_back({**req, "operation_id": "op", "targets": [key], "fields": ["export"]})
        self.assertEqual(observed["observed"][key], {"export": "gerber"})
        for f in folder.iterdir():
            f.unlink()
        observed = self.adapter.read_back({**req, "operation_id": "op", "targets": [key], "fields": ["export"]})
        self.assertEqual(observed["observed"][key], {"export": None})

    def test_drill_export(self):
        project = self.copy("schmitt")
        req = self.request("board export", project / "schmitt.kicad_pcb", file_type="drill", output=str(self.tmp / "fab"))
        with mock.patch.object(kicad_tools, "export", side_effect=fake_export(["a.drl"])) as export:
            facts = self.adapter.apply({**req, "operation_id": "op"})
        self.assertEqual(export.call_args.args[1][:3], ["pcb", "export", "drill"])
        self.assertEqual(len(facts["files"]), 1)

    def test_an_export_of_a_board_open_in_kicad_says_it_used_the_file_on_disk(self):
        project = self.copy("schmitt")
        board = project / "schmitt.kicad_pcb"
        req = self.request("board export", board, file_type="drill", output=str(self.tmp / "fab"))
        with mock.patch.object(kicad_tools, "export", side_effect=fake_export(["a.drl"])):
            self.assertNotIn("notices", self.adapter.apply({**req, "operation_id": "op"}))
            base.lock_file(board).write_text("", encoding="utf-8")
            facts = self.adapter.apply({**req, "operation_id": "op2"})
        self.assertEqual(len(facts["files"]), 1)
        self.assertEqual(facts["notices"][0]["severity"], "warning")
        self.assertIn("schmitt.kicad_pcb", facts["notices"][0]["message"])
        self.assertIn("file on disk", facts["notices"][0]["message"])
        listed = self.adapter.read(self.request("footprint list", board))
        self.assertIn("file on disk", listed["notices"][0]["message"])

    def test_an_export_into_a_folder_is_verified_by_the_write_flow(self):
        project = self.copy("schmitt")
        folder = self.tmp / "fab"
        with mock.patch.object(kicad_tools, "export", side_effect=fake_export(["a-F_Cu.gtl", "a-B_Cu.gbl"])):
            reply = write_through_tool(self.tmp, "board export", "--doc", str(project / "schmitt.kicad_pcb"),
                                       "--file-type", "gerber", "--output", str(folder))
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["data"]["receipt"]["outcome"]["verification"], "passed")
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["a-B_Cu.gbl", "a-F_Cu.gtl"])

    def test_a_board_reached_through_a_link_keeps_the_path_given(self):
        project = self.copy("schmitt")
        alias = self.tmp / "alias"
        if not link(project, alias):
            self.skipTest("this system makes no folder links")
        self.addCleanup(os.rmdir if os.name == "nt" else os.unlink, alias)
        with mock.patch.object(kicad_tools, "export", side_effect=fake_export(["a.drl"])):
            reply = write_through_tool(self.tmp, "board export", "--doc", str(alias / "schmitt.kicad_pcb"),
                                       "--file-type", "drill", "--output", str(alias / "fab"))
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["data"]["receipt"]["target"]["key"], file_key(str(alias / "fab")))
        self.assertEqual(reply["data"]["receipt"]["outcome"]["verification"], "passed")

    def test_unknown_file_type(self):
        req = self.request("board export", FIXTURES / "schmitt" / "schmitt.kicad_pcb", file_type="step",
                           output=str(self.tmp / "fab"))
        with self.assertRaises(AdapterError) as caught:
            self.adapter.preview(req)
        self.assertEqual(caught.exception.data["code"], "E_VALIDATION")

    def test_render_svg_writes_one_file(self):
        project = self.copy("schmitt")
        out = self.tmp / "renders" / "board.svg"
        req = self.request("board render", project / "schmitt.kicad_pcb", file_type="svg", output=str(out))
        preview = self.adapter.preview(req)
        self.assertEqual(preview["changes"][0]["field"], "exists")
        with mock.patch.object(kicad_tools, "export", side_effect=fake_export([])) as export:
            facts = self.adapter.apply({**req, "operation_id": "op"})
        args = export.call_args.args[1]
        self.assertEqual(args[:4], ["pcb", "export", "svg", "--mode-single"])
        self.assertIn("--page-size-mode", args)
        self.assertEqual(args[args.index("--layers") + 1], "F.Cu,B.Cu,F.SilkS,B.SilkS,Edge.Cuts")
        self.assertEqual(facts["files"], [{"path": str(out)}])
        self.assertTrue(out.is_file())
        self.assertEqual([p.name for p in out.parent.iterdir()], ["board.svg"])

    def test_render_output_must_match_the_type(self):
        req = self.request("board render", FIXTURES / "schmitt" / "schmitt.kicad_pcb", file_type="pdf",
                           output=str(self.tmp / "board.svg"))
        with self.assertRaises(AdapterError):
            self.adapter.preview(req)


DEFINITION = {"name": "SOT-23-5_Demo", "body_mm": [3.0, 1.6], "courtyard_mm": 0.25,
              "pads": [{"number": str(n + 1), "at_mm": [x, y], "size_mm": [1.06, 0.65]}
                       for n, (x, y) in enumerate([(-1.1, -0.95), (-1.1, 0), (-1.1, 0.95), (1.1, 0.95), (1.1, -0.95)])]}


class FootprintCreateTest(Case):
    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp / "demo"
        self.project.mkdir()
        (self.project / "demo.kicad_pro").write_text("{}", encoding="utf-8")
        self.definition = dict(DEFINITION)
        self.library = self.project / "Demo.pretty"

    def req(self, **extra) -> dict:
        return {**self.request("footprint create", self.library, definition=self.definition), **extra}

    def test_preview_registers_a_new_library(self):
        preview = self.adapter.preview(self.req())
        mod = self.library / "SOT-23-5_Demo.kicad_mod"
        self.assertEqual(preview["changes"][0], {"target": file_key(str(mod)), "field": "exists",
                                                 "before": False, "after": True})
        self.assertEqual(preview["changes"][1]["field"], "registered")
        self.assertFalse(self.library.exists())

    def test_apply_writes_the_footprint_and_the_table(self):
        facts = self.adapter.apply(self.req(operation_id="op"))
        mod = self.library / "SOT-23-5_Demo.kicad_mod"
        self.assertEqual(facts["change"], "applied")
        root = Document.load(mod).root
        self.assertEqual(root.value(1), "SOT-23-5_Demo")
        self.assertEqual(len(root.find_all("pad")), 5)
        self.assertEqual(facts["result"]["pads"][0], {"number": "1", "at_mm": [-1.1, -0.95], "size_mm": [1.06, 0.65]})
        table = (self.project / "fp-lib-table").read_text(encoding="utf-8")
        self.assertIn('"${KIPRJMOD}/Demo.pretty"', table)
        entry = file_key(str(self.project / "fp-lib-table"), "/libs/Demo")
        observed = self.adapter.read_back(self.req(operation_id="op", targets=[file_key(str(mod)), entry]))
        self.assertEqual(observed["observed"][entry], {"registered": True})

    def test_existing_footprint_is_never_overwritten(self):
        self.adapter.apply(self.req(operation_id="op"))
        with self.assertRaises(AdapterError) as caught:
            self.adapter.preview(self.req())
        self.assertEqual(caught.exception.data["code"], "E_VALIDATION")

    def test_a_second_footprint_does_not_touch_the_table(self):
        self.adapter.apply(self.req(operation_id="op"))
        self.definition = dict(DEFINITION, name="Other")
        preview = self.adapter.preview(self.req())
        self.assertEqual([c["field"] for c in preview["changes"]], ["exists"])

    def test_library_outside_a_project_is_refused(self):
        (self.project / "demo.kicad_pro").unlink()
        with self.assertRaises(AdapterError):
            self.adapter.preview(self.req())

    def test_a_library_in_a_missing_folder_is_not_found(self):
        self.library = self.tmp / "no-such-folder" / "Demo.pretty"
        with self.assertRaises(AdapterError) as caught:
            self.adapter.preview(self.req())
        self.assertEqual(caught.exception.data["code"], "E_NOT_FOUND")

    def test_sub_nanometre_values_are_refused(self):
        bad = dict(DEFINITION, body_mm=[3.0000001, 1.6])
        with self.assertRaises(AdapterError):
            pcb_read_footprint_lib.validate(bad)

    def test_courtyard_is_rounded_outward(self):
        text = pcb_read_footprint_lib.footprint_text(DEFINITION)
        root = Document.parse(text).root
        court = next(r for r in root.find_all("fp_rect") if r.find("layer").value(1) == "F.CrtYd")
        left = float(court.find("start").atom(1))
        # pads reach x = -1.1 - 0.53 = -1.63; plus 0.25 margin and half the 0.05 line
        self.assertAlmostEqual(left, -1.905, places=6)


if __name__ == "__main__":
    unittest.main()
