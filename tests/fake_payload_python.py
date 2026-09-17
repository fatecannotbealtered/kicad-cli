"""Stands in for KiCad's bundled Python. See fake_upstream.py.

The contract at this boundary is narrow: the CLI runs
``<interpreter> -B -u <payload>.py <args>`` and takes the first line of stdout
that parses as JSON. Everything else on stdout is noise to be stepped over --
SWIG writes to the C-level stdout and can get there first, which is why the
rule is "first line that parses" rather than "the output".
"""

from __future__ import annotations

import json
import os
import sys
import time


def main() -> int:
    behaviour = os.environ.get("FAKE_KICAD_BEHAVIOUR", "ok")

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
        sys.stderr.write("ImportError: No module named pcbnew\n")
        return 1
    if behaviour == "no_output":
        # Exits clean, says nothing. The CLI must not read this as success.
        return 0
    if behaviour == "garbage":
        sys.stdout.write("not json at all\n{ still not json\n")
        return 0

    if behaviour == "noisy":
        # What a real SWIG payload looks like on a bad day: C-level chatter
        # ahead of the envelope, and a line that starts with { but is not JSON.
        sys.stdout.write("Loading board...\n")
        sys.stdout.write("{ this brace is noise, not an envelope\n")

    sys.stdout.write(
        json.dumps(
            {
                "ok": True,
                "schema_version": "1.0",
                "data": {"fake": True},
                "meta": {"duration_ms": 0},
            }
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
