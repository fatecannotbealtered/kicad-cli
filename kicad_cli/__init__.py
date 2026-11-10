"""kicad-cli: lets an AI Agent operate KiCad safely from the command line.

The driver runtime in ``_driver/`` does everything every driver tool shares. This package adds
the domain commands (``commands/``) and the adapter that talks to KiCad (``adapter/``).
"""
from __future__ import annotations

import sys
from pathlib import Path

from ._driver import Tool
from .adapter import Adapter
from .commands import COMMANDS

# package.json, CHANGELOG.md, knowledge/, skills/ and contract/ sit at the project root; in the
# native program they are bundled next to the code.
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))

TOOL = Tool(
    root=ROOT,
    adapter=Adapter,
    commands=COMMANDS,
    application="KiCad",
    versions=">=10.0.6 <11",
    modes=["file"],
    host_component=False,
    # Standard writes that take KiCad more than a minute, such as "doc open" of a large
    # project; the runtime runs them as long jobs (DCLI-065, contract jobs.standard_long).
    long_commands=[],
)


def main() -> None:
    TOOL.main()
