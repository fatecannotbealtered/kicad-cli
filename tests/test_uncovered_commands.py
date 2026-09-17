"""The four commands the coverage guard was quietly not checking.

`test_fcc_guard.py` enumerates the leaf commands from `reference` and asserts
each has a command-level test — but it skips itself unless `fcc_status` is
`verified`, and the status is `present`. So it had never run, and four commands
had no test at all: `board live`, `fab pdf`, `fab svg`, `fab dxf`.

`fab gerber` was covered only by a string in a list of write commands, which the
guard's matching happened to accept. That is the kind of coverage that measures
nothing, so it gets a real exercise here too.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from kicad_demos import DEMOS, SKIP_REASON

REPO = Path(__file__).resolve().parents[1]


def run(*argv: str) -> tuple[dict, int]:
    env = dict(os.environ, KICAD_CLI_STRICT="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "kicad_cli.main", *argv, "--compact"],
        capture_output=True,
        cwd=REPO,
        timeout=3600,
        env=env,
    )
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    assert "contract violation" not in err, err.strip()
    for line in out.splitlines():
        if line.startswith("{"):
            return json.loads(line), proc.returncode
    raise AssertionError(f"no envelope: {out[:300]}{err[:300]}")


def board(tmp_path: Path) -> Path:
    shutil.copytree(DEMOS / "interf_u", tmp_path / "iu")
    return next((tmp_path / "iu").glob("*.kicad_pcb"))


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
@pytest.mark.parametrize("fmt", ["gerber", "pdf", "svg", "dxf"])
def test_each_plot_format_writes_files(fmt: str, tmp_path: Path) -> None:
    """All four share an implementation, but each is a separate advertised
    command and the format reaches pcbnew as a different enum."""
    b = board(tmp_path)
    out = tmp_path / f"fab-{fmt}"

    plan, code = run("fab", fmt, "--board", str(b), "--out", str(out), "--dry-run")
    assert plan["ok"] is False
    assert plan["error"]["code"] == "E_CONFIRMATION_REQUIRED"
    assert code == 5
    preview = plan["error"]["details"]["preview"]
    assert preview["format"] == fmt, "the preview must name the format being written"

    done, _ = run(
        "fab",
        fmt,
        "--board",
        str(b),
        "--out",
        str(out),
        "--confirm",
        plan["error"]["details"]["confirm_token"],
    )
    assert done["ok"] is True, done
    assert done["data"]["format"] == fmt
    assert done["data"]["files"], "reported success but wrote nothing"
    for f in done["data"]["files"]:
        assert Path(f["file"]).exists()
        assert f["bytes"] > 0, f"{f['file']} is empty"


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_a_plot_refuses_an_unknown_layer(tmp_path: Path) -> None:
    b = board(tmp_path)
    plan, _ = run(
        "fab",
        "gerber",
        "--board",
        str(b),
        "--out",
        str(tmp_path / "f"),
        "--layers",
        "NoSuchLayer",
        "--dry-run",
    )
    token = plan["error"]["details"]["confirm_token"]
    doc, code = run(
        "fab",
        "gerber",
        "--board",
        str(b),
        "--out",
        str(tmp_path / "f"),
        "--layers",
        "NoSuchLayer",
        "--confirm",
        token,
    )
    assert doc["ok"] is False
    assert doc["error"]["code"] == "E_VALIDATION"
    assert code == 2
    assert "NoSuchLayer" in json.dumps(doc["error"]["details"], ensure_ascii=False)


def test_board_live_reports_a_usable_fix_when_kicad_is_unreachable() -> None:
    """`board live` is the one command that needs a running KiCad, so its
    failure path is the path most callers will meet. It must say which
    checkbox to tick rather than just refusing.

    Deliberately does not require KiCad to be running: the assertion holds
    either way, and the test must never touch a board the user has open.
    """
    doc, code = run("board", "live")
    if doc["ok"]:
        # KiCad happens to be up. Then it must report what it is looking at.
        assert doc["data"]["connected"] is True
        assert "board" in doc["data"]
        return
    assert doc["error"]["code"] == "E_CONFIG"
    assert code == 4
    details = doc["error"]["details"]

    # Two different E_CONFIG paths, and this test used to know only one. On the
    # machine it was written on, .vendor was present, so it always reached the
    # "KiCad is not answering" path. CI has no .vendor -- it is gitignored and
    # fetched at build time -- so a fresh clone fails earlier, at the import,
    # with an entirely different and equally correct fix. The test was wrong,
    # not the tool.
    if "import_error" in details:
        assert ".vendor" in details["fix"] and "kicad-python" in details["fix"]
        assert details["import_error"], "the reason the import failed is the useful part"
    else:
        assert "Preferences" in details["fix"] and "KiCad API" in details["fix"]

    # Either way it must say that the rest of the tool does not need KiCad
    # running, so a caller does not conclude the whole CLI is unusable.
    assert "file" in details.get("note", "")
