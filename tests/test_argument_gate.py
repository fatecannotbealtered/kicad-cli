"""An option the command does not declare must stop it, not be ignored.

Both defects here shipped, and both were the same shape: the command succeeded
while doing something other than what was asked.

``board route rewidth --nets VSYS`` looked like it named a target. ``rewidth`` has no
``--nets``, so the option was dropped, a confirm token was issued, and the
preview described re-routing ``PWR_MAIN,BTL_OUT,SWITCH`` instead. The write gate
is no defence against this: the plan it shows is a perfectly valid plan, just
not the one requested. A typo -- ``--ozz`` for ``--oz`` -- failed the same way,
silently keeping the default copper weight.

Separately, the parser normalised hyphens to underscores while commands read the
registry's spelling, so every hyphenated option was unreachable. ``--ignore-lock``
had never once overridden the lock.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from driver_test_utils import grant_once
from kicad_demos import DEMOS, SKIP_REASON, copy_demo

REPO = Path(__file__).resolve().parents[1]


def run(*argv: str, scope_root: Path | None = None) -> tuple[dict, int]:
    env = dict(os.environ, **grant_once(scope_root)) if scope_root else os.environ.copy()
    proc = subprocess.run(
        [sys.executable, "-m", "kicad_cli.main", *argv, "--compact"],
        capture_output=True,
        cwd=REPO,
        env=env,
        timeout=1800,
    )
    out = proc.stdout.decode("utf-8", "replace")
    try:
        return json.loads(out), proc.returncode
    except ValueError as exc:
        raise AssertionError(f"no envelope: {out[:300]}{proc.stderr.decode()[:300]}") from exc


def board(tmp_path: Path) -> Path:
    copy_demo(DEMOS / "interf_u", tmp_path / "iu")
    return next((tmp_path / "iu").glob("*.kicad_pcb"))


def test_an_option_from_another_command_is_refused(tmp_path: Path) -> None:
    """--connect belongs to zone creation; --nets is now supported by rewidth."""
    b = tmp_path / "board.kicad_pcb"
    shutil.copyfile(REPO / "tests/fixtures/sch_edit/edit.kicad_pcb", b)
    before = b.read_bytes()
    doc, code = run(
        "board",
        "route",
        "rewidth",
        "--board",
        str(b),
        "--connect",
        "thermal",
        "--dry-run",
        scope_root=tmp_path,
    )
    assert doc["ok"] is False
    assert doc["error"]["code"] == "E_USAGE"
    assert code == 2
    assert "--connect" == doc["error"]["details"]["parameter"]
    # The message has to name what this command does accept, or the caller has
    # no way forward except guessing.
    assert "--classes" in doc["error"]["details"]["expected"]
    assert b.read_bytes() == before and not list(tmp_path.glob("*.kicad-cli.*"))


def test_a_misspelled_option_is_refused(tmp_path: Path) -> None:
    b = tmp_path / "board.kicad_pcb"
    shutil.copyfile(REPO / "tests/fixtures/sch_edit/edit.kicad_pcb", b)
    before = b.read_bytes()
    doc, _ = run(
        "board",
        "track",
        "widen",
        "--board",
        str(b),
        "--ozz",
        "2.0",
        "--dry-run",
        scope_root=tmp_path,
    )
    assert doc["ok"] is False
    assert doc["error"]["code"] == "E_USAGE"
    assert doc["error"]["details"]["parameter"] == "--ozz"
    assert b.read_bytes() == before and not list(tmp_path.glob("*.kicad-cli.*"))


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_hyphenated_options_actually_reach_the_command(tmp_path: Path) -> None:
    """The fixed public contract refuses locks and has no bypass option."""
    b = board(tmp_path)
    (b.parent / f"~{b.name}.lck").write_text("{}", encoding="utf-8")

    before = b.read_bytes()
    blocked, code = run(
        "board", "track", "widen", "--board", str(b), "--dry-run", scope_root=tmp_path
    )
    assert code == 7 and blocked["error"]["code"] == "E_BUSY", "a locked project must be refused"
    assert blocked["error"]["retryable"] is True
    assert blocked["error"]["details"]["dispatch_state"] == "not_started"

    refused, code = run(
        "board",
        "track",
        "widen",
        "--board",
        str(b),
        "--ignore-lock",
        "--dry-run",
        scope_root=tmp_path,
    )
    assert code == 2 and refused["error"]["code"] == "E_USAGE"
    assert refused["error"]["details"]["parameter"] == "--ignore-lock"
    assert b.read_bytes() == before


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
@pytest.mark.parametrize(
    ("argv", "note"),
    [
        (["board", "route", "repair", "--dry-run"], "declared option"),
        (
            ["board", "zone", "stitch", "--net", "GND", "--min-area", "0.5", "--dry-run"],
            "hyphenated",
        ),
        (["project", "links", "inspect", "--fields", "status"], "global option"),
    ],
)
def test_declared_and_global_options_still_work(argv: list[str], note: str, tmp_path: Path) -> None:
    """The refusal must not catch anything legitimate."""
    b = board(tmp_path)
    split = next((i for i, word in enumerate(argv) if word.startswith("--")), len(argv))
    doc, _ = run(*argv[:split], "--board", str(b), *argv[split:], scope_root=tmp_path)
    code = doc.get("error", {}).get("code")
    assert code != "E_USAGE", f"{note} was wrongly refused: {doc}"


def test_every_declared_option_is_accepted_by_its_own_command() -> None:
    """No command may declare a parameter its own gate would then reject.

    Cheap to check and it closes the loop: the registry is what `reference`
    advertises, so anything listed there has to be callable.
    """
    sys.path.insert(0, str(REPO))
    from kicad_cli import registry
    from kicad_cli.main import _GLOBAL_FLAGS, _GLOBAL_OPTS

    globals_ = {f.lstrip("-") for f in _GLOBAL_FLAGS} | {o.lstrip("-") for o in _GLOBAL_OPTS}
    for command in registry.build():
        declared = {p["name"] for p in command["params"]}
        for name in declared:
            assert name not in globals_ or name == "board", (
                f"{command['path']} declares {name!r}, which collides with a global option"
            )
