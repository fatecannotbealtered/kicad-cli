"""Unit tests of the shared adapter code: finding KiCad, documents, writes, and the project commands."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kicad_cli._driver import AdapterError
from kicad_cli.adapter import Adapter, base, install, kicad_tools
from kicad_cli.adapter.handlers import HANDLERS
from kicad_cli.adapter.lib_register import registered, uri_for, with_library

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class InstallTest(unittest.TestCase):
    def test_supported_range(self):
        self.assertTrue(install.supported("10.0.6"))
        self.assertTrue(install.supported("10.0.8"))
        self.assertFalse(install.supported("10.0.5"))
        self.assertFalse(install.supported("11.0.0"))
        self.assertFalse(install.supported("9.0.7"))
        self.assertFalse(install.supported("unknown"))

    def test_override_must_be_absolute(self):
        with mock.patch.dict(os.environ, {install.ENV_DIR: "relative/kicad"}):
            self.assertEqual(install.candidates(), [])
        with mock.patch.dict(os.environ, {install.ENV_DIR: ""}):
            self.assertEqual(install.candidates(), [])

    def test_override_is_exclusive(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {install.ENV_DIR: tmp}):
            self.assertEqual(install.candidates(), [install.cli_in(Path(tmp))])

    def test_kicads_own_needs_an_installation(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            cli = bin_dir / install.EXE
            cli.write_bytes(b"")
            self.assertFalse(install.is_kicads_own(cli))
            (bin_dir / ("pcbnew.exe" if os.name == "nt" else "pcbnew")).write_bytes(b"")
            self.assertTrue(install.is_kicads_own(cli))
            script = bin_dir / "kicad-cli.cmd"
            script.write_text("@echo off\n")
            self.assertFalse(install.is_kicads_own(script))

    def test_root_of_and_resources(self):
        self.assertEqual(install.root_of(Path("/opt/kicad/bin/kicad-cli")).as_posix().rstrip("/").split(":")[-1], "/opt/kicad")
        app = Path("/Applications/KiCad/KiCad.app")
        self.assertEqual(install.resources(app), app / "Contents" / "SharedSupport")
        self.assertEqual(install.cli_in(app), app / "Contents" / "MacOS" / "kicad-cli")


class KicadRunTest(unittest.TestCase):
    """How a run of KiCad's own program ends; subprocess is mocked, KiCad never runs."""

    KICAD = install.Installation(root=Path("kicad"), cli=Path("kicad/bin/kicad-cli"), version="10.0.6")

    def run_with(self, *codes: int, reading: bool = True):
        results = [mock.Mock(returncode=c, stdout=b"", stderr=b"") for c in codes]
        with mock.patch.object(kicad_tools.subprocess, "run", side_effect=results) as run:
            done = kicad_tools.run(self.KICAD, ["sch", "erc"], Path("."), 25, reading=reading)
        return done, run.call_count

    def test_a_crashed_read_runs_once_more(self):
        done, calls = self.run_with(0xC0000409, 0)
        self.assertEqual((done.returncode, calls), (0, 2))

    def test_an_export_or_an_answer_is_not_repeated(self):
        self.assertEqual(self.run_with(0xC0000409, 0, reading=False)[1], 1)
        self.assertEqual(self.run_with(5, 0)[1], 1)

    def test_kicads_program_runs_on_a_copy_of_the_settings(self):
        with tempfile.TemporaryDirectory() as home:
            mine = Path(home) / "10.0" / "kicad_common.json"
            mine.parent.mkdir()
            mine.write_text('{"working_dir": "mine"}', encoding="utf-8")
            seen = []

            def fake(argv, **kw):
                copy = Path(kw["env"]["KICAD_CONFIG_HOME"]) / "10.0" / "kicad_common.json"
                seen.append(copy.read_text(encoding="utf-8"))
                copy.write_text('{"working_dir": "elsewhere"}', encoding="utf-8")
                return mock.Mock(returncode=0, stdout=b"", stderr=b"")

            with mock.patch.dict(os.environ, {"KICAD_CONFIG_HOME": home}),                     mock.patch.object(kicad_tools.subprocess, "run", side_effect=fake):
                kicad_tools.run(self.KICAD, ["sch", "erc"], Path("."), 25)
            self.assertEqual(seen, ['{"working_dir": "mine"}'])
            self.assertEqual(mine.read_text(encoding="utf-8"), '{"working_dir": "mine"}')

    def test_a_read_that_takes_too_long_is_a_timeout(self):
        with mock.patch.object(kicad_tools.subprocess, "run", side_effect=kicad_tools.subprocess.TimeoutExpired("k", 25)),                 self.assertRaises(AdapterError) as caught:
            kicad_tools.run(self.KICAD, ["sch", "erc"], Path("."), 25)
        self.assertEqual(caught.exception.data["code"], "E_TIMEOUT")


class DocumentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_document_of_existing_and_missing_files(self):
        path = self.tmp / "a.kicad_sch"
        missing = base.document(path)
        self.assertEqual((missing["disk"], missing["revision"], missing["dirty"]), ("missing", "missing", False))
        path.write_text("(kicad_sch (version 20260101))\n", encoding="utf-8")
        doc = base.document(path)
        self.assertEqual((doc["id"], doc["path"], doc["disk"]), (str(path), str(path), "in_sync"))
        self.assertEqual(len(doc["revision"]), 64)

    def test_revision_changes_with_content(self):
        path = self.tmp / "b.kicad_pcb"
        base.write_text(path, "one\n")
        first = base.revision(path)
        base.write_text(path, "two\n")
        self.assertNotEqual(first, base.revision(path))
        self.assertEqual(path.read_text(encoding="utf-8"), "two\n")
        self.assertEqual([p.name for p in self.tmp.iterdir()], ["b.kicad_pcb"])  # no temporary file left

    def test_refuses_a_file_kicad_has_open(self):
        path = self.tmp / "board.kicad_pcb"
        path.write_text("x", encoding="utf-8")
        base.refuse_if_open(path)
        base.lock_file(path).write_text("user", encoding="utf-8")
        with self.assertRaises(AdapterError) as caught:
            base.refuse_if_open(path)
        self.assertEqual(caught.exception.data["code"], "E_CONFLICT")
        self.assertEqual(caught.exception.data["conflict"], "file_open_in_session")
        self.assertEqual(caught.exception.data["knowledge"], ["files-open-in-kicad"])
        self.assertEqual(caught.exception.data["dispatch_state"], "not_started")

    def test_adapter_documents_in_file_mode(self):
        path = self.tmp / "c.kicad_pro"
        path.write_text("{}", encoding="utf-8")
        items = Adapter(store=None).documents({"path": str(path)})["items"]
        self.assertEqual([d["path"] for d in items], [str(path)])
        self.assertEqual(Adapter(store=None).documents({}), {"items": []})

    def test_unknown_command_is_unsupported(self):
        with self.assertRaises(AdapterError) as caught:
            Adapter(store=None).read({"command": "nothing get", "inputs": {}})
        self.assertEqual(caught.exception.data["code"], "E_UNSUPPORTED")


class LibraryTableTest(unittest.TestCase):
    def test_adds_once_and_refuses_another_path(self):
        text, added = with_library(None, "footprint", "Demo", "${KIPRJMOD}/Demo.pretty")
        self.assertTrue(added)
        again, added_again = with_library(text, "footprint", "Demo", "${KIPRJMOD}/Demo.pretty")
        self.assertFalse(added_again)
        self.assertEqual(text, again)
        with self.assertRaises(AdapterError):
            with_library(text, "footprint", "Demo", "${KIPRJMOD}/Other.pretty")

    def test_registered_and_uri(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            text, _ = with_library(None, "symbol", "Demo", uri_for(project, project / "lib" / "Demo.kicad_sym"))
            (project / "sym-lib-table").write_text(text, encoding="utf-8")
            self.assertEqual(registered(project, "symbol", "Demo"), "${KIPRJMOD}/lib/Demo.kicad_sym")
            self.assertIsNone(registered(project, "symbol", "Other"))
            self.assertIsNone(registered(project, "footprint", "Demo"))


class ProjectCommandsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        shutil.copytree(FIXTURES / "schmitt", self.tmp / "schmitt")
        self.project = self.tmp / "schmitt" / "schmitt.kicad_pro"
        config = self.tmp / "config" / "10.0"
        config.mkdir(parents=True)
        text, _ = with_library(None, "symbol", "Global", str(self.tmp / "global" / "Global.kicad_sym"))
        (config / "sym-lib-table").write_text(text, encoding="utf-8")
        patches = [mock.patch.dict(os.environ, {"KICAD_CONFIG_HOME": str(self.tmp / "config")}),
                   mock.patch.object(base, "_found", return_value=None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def read(self, command, **inputs):
        return Adapter(store=None).read({"command": command, "inputs": inputs, "document": str(self.project)})["result"]

    def test_project_get(self):
        out = self.read("project get")
        self.assertEqual(out["name"], "schmitt")
        self.assertIn("schematic", out["files"])
        self.assertIn("board", out["files"])
        self.assertEqual(out["net_classes"][0]["name"], "Default")
        self.assertEqual(out["rules"]["min_track_width_mm"], json.loads(self.project.read_text(encoding="utf-8"))[
            "board"]["design_settings"]["rules"]["min_track_width"])

    def test_project_get_from_the_board(self):
        out = Adapter(store=None).read({"command": "project get", "inputs": {},
                                        "document": str(self.project.with_suffix(".kicad_pcb"))})["result"]
        self.assertEqual(out["path"], str(self.project))

    def test_library_list_has_global_and_project_libraries(self):
        items = self.read("library list")["items"]
        by_id = {(i["type"], i["id"]): i for i in items}
        self.assertEqual(by_id[("symbol", "Global")]["scope"], "global")
        self.assertFalse(by_id[("symbol", "Global")]["writable"])
        self.assertEqual(by_id[("symbol", "LocalLogic")]["scope"], "project")
        self.assertTrue(by_id[("symbol", "LocalLogic")]["writable"])
        self.assertEqual({i["type"] for i in self.read("library list", type="footprint")["items"]}, {"footprint"})

    def test_library_get_lists_items(self):
        sym = self.read("library get", id="LocalLogic", type="symbol")
        self.assertEqual(sym["type"], "symbol")
        self.assertGreaterEqual(sym["item_count"], 1)
        fp = self.read("library get", id="LocalLogic", type="footprint")
        self.assertIn("DBV0005A", [i["name"] for i in fp["items"]])

    def test_library_get_errors(self):
        with self.assertRaises(AdapterError) as missing:
            self.read("library get", id="Nope")
        self.assertEqual(missing.exception.data["code"], "E_NOT_FOUND")
        with self.assertRaises(AdapterError) as both:
            self.read("library get", id="LocalLogic")
        self.assertEqual(both.exception.data["code"], "E_AMBIGUOUS")
        self.assertEqual(sorted(c["type"] for c in both.exception.data["candidates"]), ["footprint", "symbol"])

    def test_every_handler_path_is_registered(self):
        from kicad_cli.commands import COMMANDS
        registered_paths = {c["path"] for c in COMMANDS}
        self.assertEqual(set(HANDLERS) - registered_paths, set())


if __name__ == "__main__":
    unittest.main()
