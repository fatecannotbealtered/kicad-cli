"""Stands in for `java -jar freerouting.jar`: hands back a session prepared
beforehand, so routing through Freerouting can be tested where Freerouting
is not installed -- what is tested is everything this tool does around it.

`FAKE_FREEROUTING_SES` names the session; it is written where `-do` asks.
The DSN `-de` names is kept beside it as `FAKE_FREEROUTING_DSN` if that is
set, for a test to read what Freerouting was given.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def main() -> int:
    argv = sys.argv[1:]

    def value(flag: str) -> str | None:
        return argv[argv.index(flag) + 1] if flag in argv else None

    dsn, ses = value("-de"), value("-do")
    if dsn is None or ses is None or not os.path.isfile(dsn):
        sys.stderr.write("fake freerouting: no -de board or no -do session\n")
        return 2
    keep = os.environ.get("FAKE_FREEROUTING_DSN")
    if keep:
        shutil.copy2(dsn, keep)
    changed = os.environ.get("FAKE_FREEROUTING_CHANGE_BOARD")
    if changed:
        target = Path(changed)
        target.write_bytes(target.read_bytes() + b"\n")
    shutil.copy2(os.environ["FAKE_FREEROUTING_SES"], ses)
    print(
        "Auto-routing was completed for 0 unrouted nets, completed in 0.01 seconds, "
        "final score: 100.00 "
        "(0 unrouted and 0 violations), using 0.01 CPU seconds"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
