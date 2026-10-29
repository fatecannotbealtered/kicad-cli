"""The four commands the coverage guard was quietly not checking.

`test_fcc_guard.py` enumerates the leaf commands from `reference` and asserts
each has a command-level test — but it skips itself unless `fcc_status` is
`verified`, and the status is `present`. So it had never run, and four commands
had no test at all: `session inspect`, `fab pdf`, `fab svg`, `fab dxf`.

`fab gerber` was covered only by a string in a list of write commands, which the
guard's matching happened to accept. That is the kind of coverage that measures
nothing, so it gets a real exercise here too.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest
from driver_test_utils import grant_once
from kicad_demos import DEMOS, SKIP_REASON

REPO = Path(__file__).resolve().parents[1]


def run(*argv: str) -> tuple[dict, int]:
    from native_process import directory as evidence_directory
    from native_process import run as recorded

    env = dict(os.environ, KICAD_CLI_STRICT="1", PYTHONIOENCODING="utf-8")
    if "--board" in argv:
        directory = Path(argv[argv.index("--board") + 1]).parent
        env.update(grant_once(directory))
        scope = Path(env["KICAD_CLI_SCOPE_FILE"])
        record = json.loads(scope.read_bytes())
        record.update(
            effects=["observe", "change", "persist", "artifact"], outputs=[str(directory.parent)]
        )
        scope.write_text(json.dumps(record), encoding="utf-8")
    record, stdout, stderr = recorded(
        [sys.executable, "-m", "kicad_cli.main", *argv, "--compact"],
        evidence_directory("source-export"),
        cwd=REPO,
        timeout=3600,
        env=env,
        allowed=None,
        role="source-public-export-check",
    )
    err = stderr.decode("utf-8", "replace")
    assert "contract violation" not in err, err.strip()
    value = json.loads(stdout.decode("utf-8"))
    assert (record["exit"] == 0) == value["ok"], (record, value)
    return value, record["exit"]


def board(tmp_path: Path) -> Path:
    # The installed interf_u project references an external drawing sheet,
    # deliberately refused by the bounded adapter. Use the existing saved
    # two-via fixture for format positives; keep the earlier refusals as-is.
    target = tmp_path / "plot"
    target.mkdir()
    for suffix in (".kicad_pcb", ".kicad_pro"):
        shutil.copyfile(
            REPO / "tests/fixtures/sch_edit" / ("edit" + suffix), target / ("edit" + suffix)
        )
    return target / "edit.kicad_pcb"


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
@pytest.mark.parametrize("fmt", ["gerber", "pdf", "svg", "dxf"])
def test_each_plot_format_writes_files(fmt: str, tmp_path: Path) -> None:
    """All four share an implementation, but each is a separate advertised
    command and the format reaches pcbnew as a different enum."""
    b = board(tmp_path)
    out = tmp_path / f"fab-{fmt}"

    command = ["fab", "gerber"] if fmt == "gerber" else ["board", "plot", "--file-format", fmt]
    plan, code = run(*command, "--board", str(b), "--out", str(out), "--dry-run")
    assert plan["ok"] is True, plan
    assert code == 0
    preview = plan["data"]["preview"]
    assert preview["extensions"]["format"] == fmt

    done, _ = run(
        *command,
        "--board",
        str(b),
        "--out",
        str(out),
        "--confirm",
        plan["data"]["confirm_token"],
    )
    assert done["ok"] is True, done
    assert done["data"]["receipt"]["artifacts"], "reported success but wrote nothing"
    for f in done["data"]["receipt"]["artifacts"]:
        assert Path(f["path"]).exists()
        assert Path(f["path"]).stat().st_size > 0


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
    assert plan["ok"] is False
    assert plan["error"]["code"] == "E_VALIDATION"
    assert plan["error"]["details"]["dispatch_state"] == "not_started"
    assert "NoSuchLayer" in json.dumps(plan["error"]["details"], ensure_ascii=False)


def test_board_live_reports_a_usable_fix_when_kicad_is_unreachable() -> None:
    """`session inspect` is the one command that needs a running KiCad, so its
    failure path is the path most callers will meet. It must say which
    checkbox to tick rather than just refusing.

    Deliberately does not require KiCad to be running: the assertion holds
    either way, and the test must never touch a board the user has open.
    """
    doc, code = run("session", "inspect")
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
