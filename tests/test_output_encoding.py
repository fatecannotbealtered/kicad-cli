"""The envelope is UTF-8 on a console that is not.

Every other test in this suite sets ``PYTHONIOENCODING=utf-8`` before running
the CLI. That is the one setting which hides this defect, so these tests do the
opposite: they hand the child a hostile text encoding and require the bytes on
stdout to be UTF-8 anyway. Without that, a zh-CN Windows console emitted GBK
and an agent decoding the document as UTF-8 got a decode error instead of a
result -- on every command whose output carries a non-ASCII character.

No KiCad is needed. A board path that is not ASCII puts non-ASCII into the
error envelope, which is enough to observe the encoding of the stream.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
NON_ASCII_BOARD = "板子-ünïcode.kicad_pcb"


def run(argv: list[str], encoding: str | None) -> subprocess.CompletedProcess[bytes]:
    env = dict(os.environ, KICAD_CLI_STRICT="1")
    env.pop("PYTHONIOENCODING", None)
    env.pop("PYTHONUTF8", None)
    if encoding is not None:
        env["PYTHONIOENCODING"] = encoding
    return subprocess.run(
        [sys.executable, "-m", "kicad_cli.main", *argv],
        cwd=REPO,
        env=env,
        capture_output=True,
        timeout=60,
    )


# latin-1 cannot represent the text at all and would raise; gbk and cp932 can,
# and would silently emit bytes no UTF-8 reader can decode. Both are failures,
# and the second is the worse one, so cover both shapes.
@pytest.mark.parametrize("encoding", [None, "latin-1", "gbk", "cp932", "ascii"])
def test_stdout_is_utf8_whatever_the_console_claims(encoding):
    proc = run(["board", "audit", "--board", NON_ASCII_BOARD, "--compact"], encoding)
    assert proc.returncode != 0, proc.stderr[-400:]
    doc = json.loads(proc.stdout.decode("utf-8"))
    assert doc["ok"] is False
    assert doc["error"]["code"] == "E_NOT_FOUND"
    assert doc["error"]["details"]["path"].endswith(NON_ASCII_BOARD)


@pytest.mark.parametrize("encoding", ["gbk", "latin-1"])
def test_text_format_is_utf8_too(encoding):
    """`--format text` is for people, but it still has to survive the pipe."""
    proc = run(["board", "audit", "--board", NON_ASCII_BOARD, "--format", "text"], encoding)
    assert NON_ASCII_BOARD in proc.stdout.decode("utf-8")


def test_stderr_progress_is_utf8(tmp_path):
    """Progress shares the defect and the fix; it is only on the other stream."""
    script = tmp_path / "emit.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(REPO)!r})\n"
        "from kicad_cli import envelope\n"
        "envelope.progress('进度：正在重填铺铜')\n",
        encoding="utf-8",
    )
    env = dict(os.environ, PYTHONIOENCODING="gbk")
    proc = subprocess.run([sys.executable, str(script)], capture_output=True, timeout=60, env=env)
    assert proc.stderr.decode("utf-8").strip() == "进度：正在重填铺铜"


def test_the_harness_really_hands_the_child_a_hostile_encoding():
    """Guard the guard.

    If `run` ever leaks the suite's own PYTHONIOENCODING=utf-8 into the child,
    every test above keeps passing and stops meaning anything -- which is
    exactly how the defect they cover survived this long. So assert on what the
    child's stream actually reports, not on the environment we think we set.
    """
    probe = ["-c", "import sys; print(sys.stdout.encoding)"]
    for encoding in ("gbk", "latin-1"):
        env = dict(os.environ, PYTHONIOENCODING=encoding)
        env.pop("PYTHONUTF8", None)
        proc = subprocess.run(
            [sys.executable, *probe], capture_output=True, timeout=60, env=env, text=True
        )
        assert proc.stdout.strip().lower().replace("iso8859-1", "latin-1") == encoding
