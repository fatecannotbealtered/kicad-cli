"""Unit tests of the schematic edit group: symbol create/update/move/delete, pin update, net update.

The handlers are called as the runtime calls the adapter: preview, apply, then read_back with
the targets and fields of the preview's changes. Fixtures are copied to a temporary folder;
KiCad is never run and the user's KiCad settings are never read.
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
from kicad_cli._driver.registry import Tiers, domain_command
from kicad_cli.adapter import base
from kicad_cli.adapter.handlers import schematic_edit as se
from kicad_cli.commands.schematic_edit import COMMANDS
from tool_run import link

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


class EditCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="kicad-cli-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        config = self.tmp / "config"
        config.mkdir()
        patches = [mock.patch.object(base, "_found", return_value=None),
                   mock.patch.dict(os.environ, {"KICAD_CONFIG_HOME": str(config)})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        for name in ("schmitt", "sch_edit"):
            shutil.copytree(FIXTURES / name, self.tmp / name)
        self.schmitt = self.tmp / "schmitt" / "schmitt.kicad_sch"
        self.hier = self.tmp / "sch_edit" / "edit.kicad_sch"

    def handler(self, command: str, doc: Path, inputs: dict, **more) -> base.Handler:
        return se.HANDLERS[command](base.Call(command, dict(inputs), doc, **more))

    def edit(self, command: str, doc: Path, inputs: dict) -> tuple[dict, dict, dict]:
        """Preview, apply and read back, checking the read-back against the preview."""
        preview = self.handler(command, doc, inputs).preview()
        self.assertTrue(preview["can_apply"], preview.get("blocked_by"))
        facts = self.handler(command, doc, inputs).apply()
        self.assertEqual((facts["change"], facts["persistence"]), ("applied", "saved"))
        keys = sorted({c["target"] for c in preview["changes"]})
        fields = sorted({c["field"] for c in preview["changes"]})
        observed = self.handler(command, doc, inputs, targets=keys, fields=fields).read_back()["observed"]
        for change in preview["changes"]:
            self.assertEqual(observed[change["target"]][change["field"]], change["after"], change)
        return preview, facts, observed

    def refused(self, command: str, doc: Path, inputs: dict) -> AdapterError:
        with self.assertRaises(AdapterError) as caught:
            self.handler(command, doc, inputs).preview()
        return caught.exception

    def state(self, doc: Path) -> se.State:
        return se.State(se.design(doc))


class RegistrationTest(unittest.TestCase):
    def test_registrations_are_valid_and_deletes_are_dangerous(self):
        contract = Contract(ROOT / "contract" / "driver-contract.json")
        tiers = Tiers(contract)
        found = {reg["path"]: tiers.per_mode(domain_command(reg, contract, ["file"])) for reg in COMMANDS}
        self.assertEqual(found, {"symbol create": "two_step", "symbol update": "two_step", "symbol move": "two_step",
                                 "symbol delete": "dangerous", "pin update": "two_step", "net update": "two_step"})
        self.assertEqual(set(found), set(se.HANDLERS))


class SymbolUpdateTest(EditCase):
    def test_value_changes_and_reads_back(self):
        preview, facts, _ = self.edit("symbol update", self.schmitt, {"id": "R1", "value": "4k7"})
        self.assertEqual([(c["field"], c["before"], c["after"]) for c in preview["changes"]],
                         [("fields.Value", "22k", "4k7")])
        self.assertEqual(facts["result"]["files"], ["schmitt.kicad_sch"])
        self.assertEqual(self.state(self.schmitt).symbol_fields("C1")["fields.Value"], "100nF")

    def test_batch_with_ids_and_with_items(self):
        preview, _, _ = self.edit("symbol update", self.schmitt, {"ids": ["R1", "C1"], "field_name": "MPN", "field_value": "X-1"})
        self.assertEqual([t["label"] for t in preview["targets"]], ["R1", "C1"])
        self.edit("symbol update", self.schmitt, {"items": [{"id": "R1", "value": "1k"}, {"id": "C1", "dnp": True}]})
        st = self.state(self.schmitt)
        self.assertEqual((st.symbol_fields("R1")["fields.Value"], st.symbol_fields("C1")["dnp"]), ("1k", True))

    def test_unknown_reference_is_not_found(self):
        self.assertEqual(self.refused("symbol update", self.schmitt, {"id": "R99", "value": "1"}).data["code"], "E_NOT_FOUND")

    def test_a_sheet_of_a_project_names_its_root(self):
        error = self.refused("symbol update", self.tmp / "sch_edit" / "edit_child.kicad_sch", {"id": "R10", "value": "1k"})
        self.assertEqual(error.data["code"], "E_VALIDATION")
        self.assertIn("edit.kicad_sch", str(error))

    def test_a_duplicated_reference_is_ambiguous(self):
        self.schmitt.write_text(self.schmitt.read_text(encoding="utf-8").replace('"R1"', '"C1"'), encoding="utf-8")
        error = self.refused("symbol update", self.schmitt, {"id": "C1", "value": "1k"})
        self.assertEqual(error.data["code"], "E_AMBIGUOUS")
        self.assertEqual([c["reference"] for c in error.data["candidates"]], ["C1", "C1"])

    def test_a_file_open_in_kicad_is_refused(self):
        base.lock_file(self.schmitt).write_text("", encoding="utf-8")
        error = self.refused("symbol update", self.schmitt, {"id": "R1", "value": "1k"})
        self.assertEqual((error.data["code"], error.data["knowledge"]), ("E_CONFLICT", ["files-open-in-kicad"]))


class PinAndNetTest(EditCase):
    def test_connect_move_and_mark_not_connected(self):
        preview, _, _ = self.edit("pin update", self.schmitt, {"id": "U1.1", "net": "SPARE"})
        self.assertEqual(preview["changes"][0]["after"], "/SPARE")
        self.edit("pin update", self.schmitt, {"id": "U1.1", "net": "INPUT"})
        self.edit("pin update", self.schmitt, {"id": "TP4.1", "no_connect": True})
        st = self.state(self.schmitt)
        self.assertEqual(st.net_of[("U1", "1")], "INPUT")
        self.assertTrue(st.pin_fields("TP4", "1")["no_connect"])
        self.assertEqual(st.net_of[("U1", "4")], "OUTPUT")

    def test_a_second_pin_joins_a_local_net_by_its_bare_name(self):
        self.edit("pin update", self.schmitt, {"id": "U1.1", "net": "SPARE"})
        preview, _, _ = self.edit("pin update", self.schmitt, {"id": "TP4.1", "net": "SPARE"})
        self.assertEqual(preview["changes"][0]["after"], "/SPARE")
        st = self.state(self.schmitt)
        self.assertEqual((st.net_of[("U1", "1")], st.net_of[("TP4", "1")]), ("/SPARE", "/SPARE"))

    def test_a_pin_joins_the_local_net_of_its_own_sheet_placement(self):
        for ref in ("R11", "R21"):
            preview, _, _ = self.edit("pin update", self.hier, {"id": f"{ref}.1", "net": "LOCAL"})
            self.assertTrue(preview["changes"][0]["after"].endswith("/LOCAL"), preview["changes"])
        st = self.state(self.hier)
        self.assertNotEqual(st.net_of[("R11", "1")], st.net_of[("R21", "1")])

    def test_pin_already_on_the_net_and_unknown_pin(self):
        self.assertEqual(self.refused("pin update", self.schmitt, {"id": "U1.2", "net": "INPUT"}).data["code"], "E_VALIDATION")
        self.assertEqual(self.refused("pin update", self.schmitt, {"id": "U1.9", "net": "X"}).data["code"], "E_NOT_FOUND")
        self.assertEqual(self.refused("pin update", self.schmitt, {"id": "U1", "net": "X"}).data["code"], "E_VALIDATION")

    def test_rename_a_local_net(self):
        preview, _, _ = self.edit("net update", self.hier, {"id": "/SIG", "name": "SIGNAL"})
        self.assertEqual((preview["changes"][0]["before"], preview["changes"][0]["after"]), ("/SIG", "/SIGNAL"))
        self.assertEqual(self.state(self.hier).net_of[("R1", "1")], "/SIGNAL")

    def test_rename_refuses_joining_and_unknown_nets(self):
        self.assertEqual(self.refused("net update", self.schmitt, {"id": "OUTPUT", "name": "GND"}).data["code"], "E_VALIDATION")
        self.assertEqual(self.refused("net update", self.schmitt, {"id": "NOPE", "name": "X"}).data["code"], "E_NOT_FOUND")


class CreateMoveDeleteTest(EditCase):
    def test_place_at_a_grid_position_then_connect_move_and_rotate(self):
        self.edit("symbol create", self.schmitt, {"library_id": "Device:C", "reference": "C5", "x_mm": 50.8, "y_mm": 30.48})
        self.edit("pin update", self.schmitt, {"items": [{"id": "C5.1", "net": "+3V3"}, {"id": "C5.2", "net": "GND"}]})
        _, facts, _ = self.edit("symbol move", self.schmitt, {"id": "C5", "x_mm": 55.88, "y_mm": 35.56})
        self.assertEqual(facts["result"]["moved_with"]["C5"], {"wires": 2, "labels": 2})
        self.edit("symbol move", self.schmitt, {"id": "C5", "rotation_deg": 90.0})
        st = self.state(self.schmitt)
        self.assertEqual((st.net_of[("C5", "1")], st.net_of[("C5", "2")]), ("+3V3", "GND"))
        self.assertEqual(st.symbol_fields("C5")["rotation_deg"], 90)

    def test_a_move_on_a_schematic_reached_through_a_link(self):
        alias = self.tmp / "alias"
        if not link(self.schmitt.parent, alias):
            self.skipTest("this system makes no folder links")
        self.addCleanup(os.rmdir if os.name == "nt" else os.unlink, alias)
        doc = alias / "schmitt.kicad_sch"
        self.edit("symbol create", doc, {"library_id": "Device:C", "reference": "C5", "x_mm": 50.8, "y_mm": 30.48})
        preview, _, _ = self.edit("symbol move", doc, {"id": "C5", "x_mm": 55.88, "y_mm": 35.56})
        self.assertTrue(all(c["target"].startswith(base.key(alias)) for c in preview["changes"]), preview["changes"])

    def test_power_symbols_on_its_own_wires_move_with_it(self):
        self.edit("symbol create", self.hier, {"library_id": "fixture:R", "reference": "R9", "x_mm": 25.4, "y_mm": 25.4})
        self.edit("pin update", self.hier, {"items": [{"id": "R9.1", "net": "+3V3"}, {"id": "R9.2", "net": "GND"}]})
        _, facts, _ = self.edit("symbol move", self.hier, {"id": "R9", "x_mm": 30.48, "y_mm": 27.94, "rotation_deg": 270.0})
        self.assertEqual(facts["result"]["moved_with"]["R9"], {"wires": 2, "power_symbols": 2})
        st = self.state(self.hier)
        self.assertEqual((st.net_of[("R9", "1")], st.net_of[("R9", "2")]), ("+3V3", "GND"))

    def test_next_free_reference_and_room_found(self):
        preview, facts, _ = self.edit("symbol create", self.schmitt, {"library_id": "Device:R", "value": "10k"})
        self.assertEqual(facts["result"]["id"], "R2")
        self.assertTrue(preview["targets"][0]["key"].endswith("#/symbols/R2"))

    def test_off_grid_and_taken_positions_are_refused(self):
        error = self.refused("symbol create", self.schmitt, {"library_id": "Device:C", "x_mm": 50.8, "y_mm": 30.5})
        self.assertIn("30.48mm", str(error))
        x, y = (self.state(self.schmitt).symbol_fields("U1")[k] for k in ("x_mm", "y_mm"))
        self.assertEqual(self.refused("symbol create", self.schmitt, {"library_id": "Device:C", "x_mm": x, "y_mm": y}).data["code"],
                         "E_VALIDATION")

    def test_a_symbol_wired_directly_to_others_is_not_moved(self):
        preview = self.handler("symbol move", self.schmitt, {"id": "R1", "x_mm": 69.85, "y_mm": 92.71}).preview()
        self.assertFalse(preview["can_apply"])
        self.assertIn("wired directly", preview["blocked_by"][0])

    def test_multi_unit_symbols_are_not_moved(self):
        self.assertEqual(self.refused("symbol move", self.hier, {"id": "U1", "x_mm": 0.0, "y_mm": 0.0}).data["code"],
                         "E_UNSUPPORTED")

    def test_delete(self):
        preview, _, _ = self.edit("symbol delete", self.schmitt, {"id": "TP3"})
        self.assertEqual([(c["field"], c["after"]) for c in preview["changes"]], [("exists", False)])
        self.assertNotIn("TP3", self.state(self.schmitt).parts)

    def test_preview_is_repeatable(self):
        inputs = {"id": "U1.1", "net": "SPARE"}
        self.assertEqual(self.handler("pin update", self.schmitt, inputs).preview(),
                         self.handler("pin update", self.schmitt, inputs).preview())


class LibrarySymbolTest(EditCase):
    def definition(self, **more) -> dict:
        spec = {"name": "BUF1", "reference": "U", "body_mm": [7.62, 5.08],
                "pins": [{"number": "1", "name": "A", "type": "input", "at_mm": [-7.62, 0], "angle_deg": 0},
                         {"number": "2", "name": "Y", "type": "output", "at_mm": [7.62, 0], "angle_deg": 180}]}
        spec.update(more)
        return spec

    def test_a_new_library_is_created_and_registered(self):
        library = self.tmp / "schmitt" / "Extra.kicad_sym"
        preview, facts, _ = self.edit("symbol create", library, {"definition": self.definition()})
        self.assertEqual(len(preview["changes"]), 3)
        self.assertTrue(facts["result"]["registered"])
        self.assertIn("${KIPRJMOD}/Extra.kicad_sym", (self.tmp / "schmitt" / "sym-lib-table").read_text(encoding="utf-8"))

    def test_an_existing_library_gets_the_symbol_once(self):
        library = self.tmp / "schmitt" / "LocalLogic.kicad_sym"
        _, facts, _ = self.edit("symbol create", library, {"definition": self.definition()})
        self.assertFalse(facts["result"]["registered"])
        self.assertEqual(self.refused("symbol create", library, {"definition": self.definition()}).data["code"],
                         "E_VALIDATION")

    def test_bad_definitions_and_libraries_outside_a_project(self):
        library = self.tmp / "schmitt" / "Extra.kicad_sym"
        bad = self.definition(pins=[{"number": "1", "type": "input", "at_mm": [0.5, 0]}])
        self.assertIn("grid", str(self.refused("symbol create", library, {"definition": bad})))
        outside = self.tmp / "loose" / "Loose.kicad_sym"
        outside.parent.mkdir()
        self.assertEqual(self.refused("symbol create", outside, {"definition": self.definition()}).data["code"],
                         "E_VALIDATION")


if __name__ == "__main__":
    unittest.main()
