"""Where the recording rig finds its board.

The rig runs against a real, in-progress design on one machine. This repository
is public, so the paths and the project name live in `demo/local.json`, which is
gitignored — see `demo/local.example.json` for the shape. Nothing here is part
of the distribution; `package.json` does not ship `demo/`.

Read `BOARD` / `OUT_DIR` / `STEM` from this module rather than writing a path
into a script, so there is exactly one place to point at a different board.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCAL = HERE / "local.json"

if not LOCAL.exists():
    raise SystemExit(
        f"{LOCAL} is missing.\n"
        f"Copy {HERE / 'local.example.json'} to local.json and point it at your own board.\n"
        "It is gitignored on purpose: this rig runs against a real design."
    )

_cfg = json.loads(LOCAL.read_text(encoding="utf-8"))

#: Directory holding the source project.
SRC_DIR = Path(_cfg["src_dir"])
#: The source .kicad_pcb the demo is rebuilt from.
BOARD = SRC_DIR / _cfg["board"]
#: Where build_seed.py writes the stripped seed project.
OUT_DIR = Path(_cfg["out_dir"])
#: Basename for every generated file, and the window title focus.py looks for.
STEM = _cfg["stem"]
#: Optional project-local footprint library carried into the seed.
PRETTY = _cfg.get("pretty")

#: The seed board the operator opens in KiCad before running perform.py.
SEED = OUT_DIR / f"{STEM}.kicad_pcb"
