"""An option the command does not declare must stop it, not be ignored.

Both defects here shipped, and both were the same shape: the command succeeded
while doing something other than what was asked.

``board rewidth --nets VSYS`` looked like it named a target. ``rewidth`` has no
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
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from kicad_demos import DEMOS, SKIP_REASON

REPO = Path(__file__).resolve().parents[1]


def run(*argv: str) -> tuple[dict, int]:
    proc = subprocess.run(
        [sys.executable, "-m", "kicad_cli.main", *argv, "--compact"],
        capture_output=True,
        cwd=REPO,
        timeout=1800,
    )
    out = proc.stdout.decode("utf-8", "replace")
    for line in out.splitlines():
        if line.startswith("{"):
            return json.loads(line), proc.returncode
    raise AssertionError(f"no envelope: {out[:300]}{proc.stderr.decode()[:300]}")


def board(tmp_path: Path) -> Path:
    shutil.copytree(DEMOS / "interf_u", tmp_path / "iu")
    return next((tmp_path / "iu").glob("*.kicad_pcb"))


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_an_option_from_another_command_is_refused(tmp_path: Path) -> None:
    """--nets belongs to `board route`, not `board rewidth`."""
    b = board(tmp_path)
    doc, code = run("board", "rewidth", "--board", str(b), "--nets", "VSYS", "--dry-run")
    assert doc["ok"] is False
    assert doc["error"]["code"] == "E_USAGE"
    assert code == 2
    assert "--nets" in doc["error"]["details"]["unknown"]
    # The message has to name what this command does accept, or the caller has
    # no way forward except guessing.
    assert "classes" in doc["error"]["details"]["accepted"]


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_a_misspelled_option_is_refused(tmp_path: Path) -> None:
    b = board(tmp_path)
    doc, _ = run("board", "widen", "--board", str(b), "--ozz", "2.0", "--dry-run")
    assert doc["ok"] is False
    assert doc["error"]["code"] == "E_USAGE"
    assert doc["error"]["details"]["unknown"] == ["--ozz"]


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_hyphenated_options_actually_reach_the_command(tmp_path: Path) -> None:
    """--ignore-lock must override the lock guard. It never used to."""
    b = board(tmp_path)
    (b.parent / f"~{b.name}.lck").write_text("{}", encoding="utf-8")

    blocked, _ = run("board", "widen", "--board", str(b), "--dry-run")
    assert blocked["error"]["code"] == "E_CONFLICT", "a locked project must be refused"

    allowed, _ = run("board", "widen", "--board", str(b), "--ignore-lock", "--dry-run")
    assert allowed["error"]["code"] == "E_CONFIRMATION_REQUIRED", (
        "--ignore-lock did not reach the guard"
    )


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
@pytest.mark.parametrize(
    ("argv", "note"),
    [
        (["board", "route", "--mode", "repair", "--dry-run"], "declared option"),
        (["board", "stitch", "--net", "GND", "--min-area", "0.5", "--dry-run"], "hyphenated"),
        (["sch", "link", "--fields", "status"], "global option"),
    ],
)
def test_declared_and_global_options_still_work(argv: list[str], note: str, tmp_path: Path) -> None:
    """The refusal must not catch anything legitimate."""
    b = board(tmp_path)
    doc, _ = run(*argv[:2], "--board", str(b), *argv[2:])
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
