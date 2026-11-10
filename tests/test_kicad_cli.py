"""Unit tests of kicad-cli's own code: the registrations and the adapter.

The behavior every driver tool shares is tested by the conformance suites
(`python scripts/spec.py suites`), which run the tool against the fake host.
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from kicad_cli import TOOL

ROOT = Path(__file__).resolve().parents[1]


class ToolTest(unittest.TestCase):
    def run_tool(self, *args: str) -> tuple[int, dict]:
        with tempfile.TemporaryDirectory() as state:
            env = {k: v for k, v in os.environ.items() if not k.startswith("KICAD_CLI_")}
            env["KICAD_CLI_STATE_DIR"] = state
            out = io.StringIO()
            code = TOOL.run(list(args), env, out)
        return code, json.loads(out.getvalue())

    def test_version_comes_from_package_json(self):
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        code, envelope = self.run_tool("--version")
        self.assertEqual((code, envelope["data"]["version"]), (0, package["version"]))

    def test_reference_describes_every_command(self):
        code, envelope = self.run_tool("reference")
        self.assertEqual(code, 0)
        self.assertEqual(envelope["data"]["driver"]["application"], "KiCad")

    def test_commands_json_is_current(self):
        written = json.loads((ROOT / "contract" / "commands.json").read_text(encoding="utf-8"))
        self.assertEqual(written, TOOL.registration(), "run `python scripts/spec.py generate`")


if __name__ == "__main__":
    unittest.main()
