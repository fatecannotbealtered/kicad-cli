"""A DRC oracle that reports the board getting worse, on demand.

`fake_official_cli` deliberately emits a report that `drc_runner.validate_report`
rejects -- that is what it is for. This one is the opposite: a report valid
enough to be believed, so the caller's reaction to its *verdict* can be tested
rather than its reaction to a malformed file.

Why a stub at all, when the rest of the routing runs against a real KiCad: a
router that makes a board worse is not something a test can ask for. The
judgement under test is "error count rose, so put the board back", and the only
way to stage that deterministically is to control the judge.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

VIOLATION = {
    "type": "clearance",
    "description": "staged clearance violation",
    "severity": "error",
    "items": [
        {
            "uuid": "00000000-0000-0000-0000-000000000001",
            "description": "track",
            "pos": {"x": 1.0, "y": 2.0},
        },
    ],
}


def _calls() -> int:
    counter = os.environ.get("FAKE_DRC_CALLS")
    if not counter:
        return 1
    path = Path(counter)
    n = len(path.read_text(encoding="utf-8").split()) + 1 if path.exists() else 1
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("x\n")
    return n


def main() -> int:
    argv = sys.argv[1:]
    if "-o" not in argv:
        sys.stderr.write("fake drc oracle: no -o given\n")
        return 2
    dest = Path(argv[argv.index("-o") + 1])
    board = Path(argv[-1])

    # The board this was asked about, by basename, because that is what KiCad's
    # own report carries and what validate_report checks against.
    after = int(os.environ.get("FAKE_DRC_WORSEN_AFTER", "1"))
    violations = [VIOLATION] if _calls() > after else []
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(
            {
                "source": board.name,
                "date": "2026-09-18T00:00:00+0000",
                "kicad_version": "10.0.6",
                "coordinate_units": "mm",
                "violations": violations,
                "unconnected_items": [],
                "schematic_parity": [],
            }
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
