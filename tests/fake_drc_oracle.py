"""A DRC oracle that answers with a report KiCad wrote earlier.

KiCad's DRC does not list a board's missing connections the same way twice:
which two anchors it names for a connection comes out of its ratsnest, and on
ecc83-pp with tracks taken out one run names a track end 4.5 mm from the pad
and the next another 4.6 mm away. `board route --mode repair` routes what the
list names, so the payload, which asks KiCad's DRC itself, and the port, which
is handed KiCad's list, must be given the same run of it to be compared.

`FAKE_DRC_REPORT` names the report; it is written wherever the caller asked,
with the board it was asked about as its source.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    argv = sys.argv[1:]
    if "-o" not in argv:
        sys.stderr.write("fake drc oracle: no -o given\n")
        return 2
    dest = Path(argv[argv.index("-o") + 1])
    board = Path(argv[-1])
    report = json.loads(Path(os.environ["FAKE_DRC_REPORT"]).read_text(encoding="utf-8"))
    report["source"] = board.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
