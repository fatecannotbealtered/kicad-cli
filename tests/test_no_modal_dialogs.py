"""A headless command must never wait for someone to click OK.

pcbnew shows some of its messages as modal dialogs. On a machine nobody is
watching that is not a message, it is a hang: the command prints nothing and
never exits. Asserts were already silenced for exactly this reason. Log
messages were not: loading a board whose text names a font this machine
lacks makes pcbnew log "Font 'X' not found; substituting 'Y'", and wx shows
that as a message box. Three of KiCad's own demo boards do it on a stock
Windows -- Lato, Ubuntu Sans, FreeMono -- and it was found when each load
sat on such a box until the person at the machine dismissed it.

If the fix regresses, these tests fail by timing out, and the dialog they
are waiting on is on screen until then.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from kicad_cli import kicad_env  # noqa: E402

# A board whose only text asks for a font no machine has.
FONT_BOARD = """(kicad_pcb
\t(version 20260206)
\t(generator "pcbnew")
\t(generator_version "10.0")
\t(general
\t\t(thickness 1.6)
\t)
\t(paper "A4")
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(2 "B.Cu" signal)
\t\t(5 "F.SilkS" user "F.Silkscreen")
\t\t(25 "Edge.Cuts" user)
\t)
\t(gr_text "font probe"
\t\t(at 20 20 0)
\t\t(layer "F.SilkS")
\t\t(effects
\t\t\t(font
\t\t\t\t(face "NoSuchFont KicadCli Probe")
\t\t\t\t(size 1.5 1.5)
\t\t\t\t(thickness 0.1)
\t\t\t)
\t\t)
\t)
)
"""


def kicad_python() -> str | None:
    try:
        return kicad_env.find_python()
    except Exception:  # noqa: BLE001 - resolution must never break collection
        return None


needs_pcbnew = pytest.mark.skipif(kicad_python() is None, reason="needs KiCad's interpreter")


@pytest.fixture
def font_board(tmp_path) -> Path:
    board = tmp_path / "font.kicad_pcb"
    board.write_text(FONT_BOARD, encoding="utf-8")
    return board


@needs_pcbnew
def test_a_missing_font_is_a_note_on_stderr_not_a_dialog(font_board, tmp_path):
    """The payloads' own entry to pcbnew, and the board load it guards."""
    script = tmp_path / "load.py"
    payload = REPO / "kicad_cli" / "payload"
    script.write_text(
        textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {str(payload)!r})
            import kicad_lib as K
            pcbnew = K.import_pcbnew()
            pcbnew.LoadBoard({str(font_board)!r})
            print("loaded")
            """
        ),
        encoding="utf-8",
    )
    proc = subprocess.run([kicad_python(), str(script)], capture_output=True, timeout=60)
    assert proc.stdout.decode("utf-8", "replace").strip() == "loaded", proc.stderr
    # Said, not swallowed: the note is where diagnostics belong.
    assert b"not found; substituting" in proc.stderr


@needs_pcbnew
def test_a_command_on_such_a_board_finishes(font_board):
    env = dict(os.environ, KICAD_CLI_STRICT="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "kicad_cli.main", "board", "plane", "--board", str(font_board)],
        capture_output=True,
        cwd=REPO,
        env=env,
        timeout=120,
    )
    doc = json.loads(proc.stdout.decode("utf-8"))
    assert doc["ok"] is True, doc
