"""Stands in for KiCad's own ``kicad-cli`` binary. See fake_upstream.py.

Three subcommands are reached through this boundary: ``sch export netlist``,
``sch erc`` and ``pcb drc``. Each writes its result to the path after ``-o``,
which is the only part of the invocation this needs to understand.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

NETLIST = '(export (version "E")\n  (components)\n  (nets))\n'
ERC = {"$schema": "fake", "source": "fake.kicad_sch", "sheets": []}
DRC = {"$schema": "fake", "source": "fake.kicad_pcb", "violations": [], "unconnected_items": []}


def out_path(argv: list[str]) -> Path | None:
    if "-o" in argv:
        i = argv.index("-o")
        if i + 1 < len(argv):
            return Path(argv[i + 1])
    return None


def record() -> int:
    """Count invocations so `flaky` can fail the first two and pass the third."""
    counter = os.environ.get("FAKE_KICAD_CALLS")
    if not counter:
        return 1
    p = Path(counter)
    n = len(p.read_text(encoding="utf-8").split()) + 1 if p.exists() else 1
    with open(p, "a", encoding="utf-8") as fh:
        fh.write("x\n")
    return n


def main() -> int:
    argv = sys.argv[1:]
    behaviour = os.environ.get("FAKE_KICAD_BEHAVIOUR", "ok")
    n = record()

    if behaviour == "hang":
        # Ten seconds, not ten minutes. Any caller testing this sets its own
        # timeout well below it, so the mapping to E_TIMEOUT still fires on
        # time -- but the stub then exits on its own. It has to: on Windows the
        # launcher is a .bat, killing it does not kill this process, and
        # subprocess.run waits on the inherited pipes after the kill. A stub
        # that sleeps forever hangs the test runner rather than the tool.
        time.sleep(10)
        return 0
    if behaviour == "launch_fail":
        sys.stderr.write("fake kicad-cli: cannot open display\n")
        return 3
    if behaviour == "no_output":
        # The transient this whole retry path exists for: success is reported
        # and no file appears. Silent, and indistinguishable from a real
        # failure unless you check for the file.
        return 0
    if behaviour == "flaky" and n <= 2:
        return 0

    dest = out_path(argv)
    if dest is None:
        sys.stderr.write("fake kicad-cli: no -o given\n")
        return 2
    dest.parent.mkdir(parents=True, exist_ok=True)

    if behaviour == "garbage":
        dest.write_text("<<< not the format anyone asked for >>>", encoding="utf-8")
        return 0

    if "erc" in argv:
        dest.write_text(json.dumps(ERC), encoding="utf-8")
    elif "drc" in argv:
        dest.write_text(json.dumps(DRC), encoding="utf-8")
    else:
        dest.write_text(NETLIST, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
