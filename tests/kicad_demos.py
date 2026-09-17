"""Where KiCad's demo projects are, on whatever machine this is.

Seven test modules used to hardcode an absolute path to one developer's KiCad
install and skip when it did not exist. On any other machine -- including a CI
runner with KiCad installed somewhere perfectly ordinary -- every live test
skipped silently and the run still reported green. A suite that disappears
rather than fails is worse than one that fails.

So the location is derived from the KiCad the tool itself resolved, which is the
same KiCad the tests are exercising. `KICAD_CLI_DEMOS` overrides it for an
unusual install.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from kicad_cli import kicad_env  # noqa: E402

# Relative to KiCad's bin directory and to its parent. Covers the Windows and
# Linux layouts (bin/ beside share/) and the macOS app bundle, where the binary
# sits in Contents/MacOS and the demos in Contents/SharedSupport.
_RELATIVE = (
    "../share/kicad/demos",
    "../share/demos",
    "../SharedSupport/demos",
    "../../share/kicad/demos",
    "demos",
)


def _resolve() -> Path:
    override = os.environ.get("KICAD_CLI_DEMOS")
    if override:
        return Path(override)

    roots: list[Path] = []
    for finder in (kicad_env.find_official_cli, kicad_env.find_python):
        try:
            found = finder()
        except Exception:  # noqa: BLE001 - resolution must never break collection
            found = None
        if found:
            roots.append(Path(found).resolve().parent)

    for root in roots:
        for rel in _RELATIVE:
            candidate = (root / rel).resolve()
            if candidate.is_dir():
                return candidate

    # Nothing found. Return a path that does not exist so the skip conditions
    # read the same as before, rather than raising during collection.
    return Path("kicad-demos-not-found")


DEMOS = _resolve()

#: Reason string for `skipif`, naming what was looked for so a skipped run says
#: something more useful than "not installed".
SKIP_REASON = (
    f"KiCad demo projects not found (looked next to the resolved KiCad; "
    f"set KICAD_CLI_DEMOS to override). Tried: {DEMOS}"
)
