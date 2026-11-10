"""Unit tests of the schematic read group: schematic create, check, export, render; symbol list
and get; net list. KiCad's own program is never run: ERC and exports are mocked."""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kicad_cli import TOOL
from kicad_cli._driver import AdapterError
from kicad_cli._driver.contract import Contract
from kicad_cli._driver.registry import domain_command
from kicad_cli.adapter import base, install, kicad_tools
from kicad_cli.adapter.handlers import schematic_read as handlers
from kicad_cli.commands.schematic_read import COMMANDS
from tool_run import write_through_tool

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
SCHMITT = FIXTURES / "schmitt"
FAKE_KICAD = install.Installation(root=Path("C:/fake-kicad"), cli=Path("C:/fake-kicad/bin/kicad-cli.exe"),
                                  version="10.0.6")


def call(command: str, doc: Path | None, **inputs) -> base.Call:
    return base.Call(command=command, inputs=inputs, doc=doc.resolve() if doc else None)


class ToolRun(unittest.TestCase):
    def run_tool(self, *args: str) -> tuple[int, dict]:
        with tempfile.TemporaryDirectory() as state:
            env = {k: v for k, v in os.environ.items() if not k.startswith("KICAD_CLI_")}
            env["KICAD_CLI_STATE_DIR"] = state
            out = io.StringIO()
            code = TOOL.run(list(args), env, out)
        return code, json.loads(out.getvalue())


class RegistrationTest(unittest.TestCase):
    def test_registrations_are_valid_and_have_handlers(self):
        contract = Contract(ROOT / "contract" / "driver-contract.json")
        for reg in COMMANDS:
            command = domain_command(reg, contract, ["file"])
            self.assertIn(command.path, handlers.HANDLERS)
        self.assertEqual(sorted(r["path"] for r in COMMANDS), sorted(handlers.HANDLERS))


class SymbolListTest(ToolRun):
    def test_lists_placed_symbols_without_power(self):
        code, env = self.run_tool("symbol list", "--doc", str(SCHMITT / "schmitt.kicad_sch"), "--fields", "all")
        self.assertEqual(code, 0, env)
        refs = [i["reference"] for i in env["data"]["items"]]
        self.assertEqual(refs, ["C1", "R1", "TP1", "TP2", "TP3", "TP4", "U1"])
        u1 = env["data"]["items"][-1]
        self.assertEqual((u1["x_mm"], u1["y_mm"], u1["rotation_deg"], u1["sheet"]), (100.33, 69.85, 0, "/"))

    def test_query_filters(self):
        code, env = self.run_tool("symbol list", "--doc", str(SCHMITT / "schmitt.kicad_sch"), "--query", "22k")
        self.assertEqual((code, [i["reference"] for i in env["data"]["items"]]), (0, ["R1"]))

    def test_hierarchy_names_each_sheet(self):
        rows = handlers.SymbolList(call("symbol list", FIXTURES / "schematic" / "rules.kicad_sch")).read()["items"]
        self.assertGreater(len({r["sheet"] for r in rows}), 1)

    def test_power_symbols_on_request(self):
        doc = FIXTURES / "sch_edit" / "edit.kicad_sch"
        plain = handlers.SymbolList(call("symbol list", doc)).read()["items"]
        everything = handlers.SymbolList(call("symbol list", doc, include_power=True)).read()["items"]
        self.assertTrue(all(not r["reference"].startswith("#") for r in plain))
        self.assertGreaterEqual(len(everything), len(plain))


class SymbolGetTest(ToolRun):
    def test_pins_carry_their_nets(self):
        code, env = self.run_tool("symbol get", "--doc", str(SCHMITT / "schmitt.kicad_sch"), "--id", "U1")
        self.assertEqual(code, 0, env)
        pins = {p["id"]: p["net"] for p in env["data"]["units"][0]["pins"]}
        self.assertEqual(pins["U1.2"], "INPUT")
        self.assertEqual(pins["U1.5"], "+3V3")
        self.assertTrue(pins["U1.1"].startswith("unconnected-"))

    def test_uuid_selects_too(self):
        got = handlers.SymbolGet(call("symbol get", SCHMITT / "schmitt.kicad_sch", id="U1")).read()
        again = handlers.SymbolGet(call("symbol get", SCHMITT / "schmitt.kicad_sch", id=got["units"][0]["uuid"])).read()
        self.assertEqual(again["reference"], "U1")

    def test_missing_symbol_is_not_found(self):
        code, env = self.run_tool("symbol get", "--doc", str(SCHMITT / "schmitt.kicad_sch"), "--id", "R99")
        self.assertEqual((code, env["error"]["code"]), (3, "E_NOT_FOUND"))

    def test_duplicate_reference_is_ambiguous(self):
        with tempfile.TemporaryDirectory() as tmp:
            for f in SCHMITT.iterdir():
                if f.is_file():
                    shutil.copy(f, tmp)
            path = Path(tmp) / "schmitt.kicad_sch"
            path.write_text(path.read_text(encoding="utf-8").replace('"R1"', '"C1"'), encoding="utf-8")
            with self.assertRaises(AdapterError) as caught:
                handlers.SymbolGet(call("symbol get", path, id="C1")).read()
        self.assertEqual(caught.exception.data["code"], "E_AMBIGUOUS")
        self.assertEqual([c["reference"] for c in caught.exception.data["candidates"]], ["C1", "C1"])

    def test_library_definition(self):
        got = handlers.SymbolGet(call("symbol get", SCHMITT / "LocalLogic.kicad_sym", id="SN74LVC1G14DBV")).read()
        self.assertEqual(got["name"], "SN74LVC1G14DBV")
        gnd = next(p for p in got["pins"] if p["number"] == "3")
        self.assertEqual((gnd["name"], gnd["type"], gnd["y_mm"]), ("GND", "power_in", 7.62))
        with self.assertRaises(AdapterError):
            handlers.SymbolGet(call("symbol get", SCHMITT / "LocalLogic.kicad_sym", id="Nope")).read()


class NetListTest(ToolRun):
    def test_schematic_nets(self):
        code, env = self.run_tool("net list", "--doc", str(SCHMITT / "schmitt.kicad_sch"))
        self.assertEqual(code, 0, env)
        nets = {i["name"]: i["pins"] for i in env["data"]["items"]}
        self.assertEqual(nets["GND"], ["C1.2", "R1.2", "TP2.1", "U1.3"])
        self.assertNotIn("unconnected-(U1-NC-Pad1)", nets)

    def test_unconnected_on_request(self):
        rows = handlers.NetList(call("net list", SCHMITT / "schmitt.kicad_sch", include_unconnected=True)).read()
        self.assertIn("unconnected-(U1-NC-Pad1)", [r["name"] for r in rows["items"]])

    def test_board_nets_match_the_schematic(self):
        sch = handlers.NetList(call("net list", SCHMITT / "schmitt.kicad_sch")).read()["items"]
        pcb = handlers.NetList(call("net list", SCHMITT / "schmitt.kicad_pcb")).read()["items"]
        self.assertEqual({r["name"]: r["pins"] for r in sch}, {r["name"]: r["pins"] for r in pcb})

    def test_query(self):
        rows = handlers.NetList(call("net list", SCHMITT / "schmitt.kicad_sch", query="put")).read()["items"]
        self.assertEqual(sorted(r["name"] for r in rows), ["INPUT", "OUTPUT"])


class SchematicCheckTest(unittest.TestCase):
    def setUp(self):
        self.report = json.loads((FIXTURES / "schematic_read" / "erc-report.json").read_text(encoding="utf-8"))

    def check(self, **inputs):
        with mock.patch.object(base, "kicad", return_value=FAKE_KICAD), \
                mock.patch.object(kicad_tools, "erc", return_value=self.report) as erc:
            out = handlers.SchematicCheck(call("schematic check", SCHMITT / "schmitt.kicad_sch", **inputs)).read()
        erc.assert_called_once()
        return out

    def test_counts_and_items(self):
        out = self.check()
        self.assertEqual((out["error_count"], out["warning_count"], out["exclusion_count"]), (1, 1, 1))
        first = out["items"][0]
        self.assertEqual((first["type"], first["sheet"]), ("pin_not_connected", "/"))
        self.assertEqual(first["objects"][0]["x_mm"], 101.6)

    def test_severity_filter(self):
        self.assertEqual([i["type"] for i in self.check(severity="warning")["items"]], ["endpoint_off_grid"])
        with self.assertRaises(AdapterError):
            self.check(severity="fatal")

    def test_flat_report_is_read_too(self):
        flat = json.loads((FIXTURES / "erc" / "erc.kicad.json").read_text(encoding="utf-8"))
        items = handlers.erc_items(flat)
        self.assertEqual(len(items), len(flat["violations"]))


class SchematicCreateTest(unittest.TestCase):
    def test_creates_schematic_and_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "demo.kicad_sch"
            handler = handlers.SchematicCreate(call("schematic create", doc.with_suffix(".kicad_pro"), title="Demo", paper="A3"))
            preview = handler.preview()
            self.assertEqual([c["field"] for c in preview["changes"]], ["exists", "exists"])
            facts = handler.apply()
            self.assertEqual((facts["change"], facts["persistence"]), ("not_applicable", "saved"))
            self.assertTrue(doc.is_file() and doc.with_suffix(".kicad_pro").is_file())
            text = doc.read_text(encoding="utf-8")
            self.assertIn('(paper "A3")', text)
            self.assertIn('(title "Demo")', text)
            rows = handlers.SymbolList(call("symbol list", doc)).read()["items"]
            self.assertEqual(rows, [])
            back = handlers.SchematicCreate(base.Call("schematic create", {}, doc.with_suffix(".kicad_pro").resolve(),
                                                      targets=[c["target"] for c in preview["changes"]])).read_back()
            self.assertTrue(all(v["exists"] for v in back["observed"].values()))

    def test_keeps_an_existing_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo.kicad_pro"
            project.write_text("{}\n", encoding="utf-8")
            handler = handlers.SchematicCreate(call("schematic create", project, output=str(Path(tmp) / "power.kicad_sch")))
            self.assertEqual(len(handler.preview()["changes"]), 1)
            handler.apply()
            self.assertEqual(project.read_text(encoding="utf-8"), "{}\n")
            self.assertTrue((Path(tmp) / "power.kicad_sch").is_file())

    def test_refuses_existing_file_and_bad_input(self):
        with self.assertRaises(AdapterError) as caught:
            handlers.SchematicCreate(call("schematic create", SCHMITT / "schmitt.kicad_pro")).preview()
        self.assertEqual(caught.exception.data["code"], "E_VALIDATION")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(AdapterError) as caught:
                handlers.SchematicCreate(call("schematic create", Path(tmp) / "x.kicad_pro", paper="A9")).preview()
            self.assertEqual(caught.exception.data["code"], "E_VALIDATION")
            with self.assertRaises(AdapterError):
                handlers.SchematicCreate(call("schematic create", Path(tmp) / "x.kicad_pcb")).preview()


def fake_run(written: list[list[str]]):
    """Stands in for KiCad's program: writes what --output names, as KiCad would."""
    def run(kicad, args, cwd, timeout, reading=True):
        written.append(args)
        out = Path(args[args.index("--output") + 1])
        if args[2] == "svg":
            out.mkdir(parents=True, exist_ok=True)
            (out / "schmitt.svg").write_text("<svg/>", encoding="utf-8")
            (out / "schmitt-child.svg").write_text("<svg/>", encoding="utf-8")
        else:
            out.write_text("output", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, b"", b"")
    return run


class ExportRenderTest(unittest.TestCase):
    def run_artifact(self, cls, command, output, file_type):
        written: list[list[str]] = []
        handler = cls(base.Call(command, {"file_type": file_type, "output": str(output)},
                                (SCHMITT / "schmitt.kicad_sch").resolve()))
        with mock.patch.object(base, "kicad", return_value=FAKE_KICAD), \
                mock.patch.object(kicad_tools, "run", side_effect=fake_run(written)):
            preview = handler.preview()
            facts = handler.apply()
        return preview, facts, written

    def test_netlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out" / "demo.net"
            preview, facts, written = self.run_artifact(handlers.SchematicExport, "schematic export", out, "netlist")
            self.assertEqual(preview["changes"][0]["field"], "exists")
            self.assertEqual(written[0][:6], ["sch", "export", "netlist", "--format", "kicadsexpr", "--output"])
            self.assertTrue(out.is_file())
            self.assertEqual(facts["files"], [{"path": str(out)}])

    def test_pdf_and_svg(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "demo.pdf"
            _, facts, written = self.run_artifact(handlers.SchematicRender, "schematic render", pdf, "pdf")
            self.assertEqual(written[0][:3], ["sch", "export", "pdf"])
            self.assertTrue(pdf.is_file())
            folder = Path(tmp) / "svg"
            preview, facts, _ = self.run_artifact(handlers.SchematicRender, "schematic render", folder, "svg")
            self.assertEqual(preview["changes"][0], {"target": base.key(folder), "field": "export",
                                                     "before": None, "after": "svg"})
            self.assertEqual(sorted(Path(f["path"]).name for f in facts["files"]), ["schmitt-child.svg", "schmitt.svg"])

    def test_a_render_into_a_folder_is_verified_by_the_write_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "schmitt"
            shutil.copytree(SCHMITT, project)
            folder = Path(tmp) / "svg"
            with mock.patch.object(base, "kicad", return_value=FAKE_KICAD), \
                    mock.patch.object(kicad_tools, "run", side_effect=fake_run([])):
                reply = write_through_tool(Path(tmp), "schematic render", "--doc", str(project / "schmitt.kicad_sch"),
                                           "--file-type", "svg", "--output", str(folder))
            self.assertTrue(reply["ok"], reply)
            self.assertEqual(reply["data"]["receipt"]["outcome"]["verification"], "passed")
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ["schmitt-child.svg", "schmitt.svg"])
            # With the SVG files gone, the read-back no longer sees the export.
            for f in folder.iterdir():
                f.unlink()
            key = base.key(folder)
            again = handlers.SchematicRender(base.Call("schematic render", {"file_type": "svg", "output": str(folder)},
                                                       (project / "schmitt.kicad_sch").resolve(), targets=[key]))
            self.assertEqual(again.read_back(), {"observed": {key: {"export": None}}})

    def test_bad_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            for cls, command, output, file_type in (
                (handlers.SchematicExport, "schematic export", Path(tmp) / "x.txt", "netlist"),
                (handlers.SchematicExport, "schematic export", Path(tmp) / "x.net", "bom"),
                (handlers.SchematicRender, "schematic render", Path(tmp) / "x.svg", "svg"),
                (handlers.SchematicRender, "schematic render", Path(tmp) / "x.png", "pdf"),
            ):
                with self.assertRaises(AdapterError) as caught:
                    cls(base.Call(command, {"file_type": file_type, "output": str(output)},
                                  (SCHMITT / "schmitt.kicad_sch").resolve())).preview()
                self.assertEqual(caught.exception.data["code"], "E_VALIDATION")


if __name__ == "__main__":
    unittest.main()
