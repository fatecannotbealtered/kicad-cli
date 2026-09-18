"""Every command emits exactly the fields it advertises.

``reference`` is the only thing an agent reads before deciding what to call, so
a schema that has drifted from its command is not a documentation problem -- it
is the tool lying about itself. Nothing checked this until a strict mode was
added, and the first run found a read command declaring three fields it had
never emitted and omitting four it always did.

The check runs the real commands with ``KICAD_CLI_STRICT`` set, which makes the
envelope refuse to emit mismatched data. Write commands are exercised on copies
of KiCad's demo projects, all the way through the confirmation gate, because a
contract that only holds for the easy half is not a contract.
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

sys.path.insert(0, str(REPO))
from kicad_cli import registry  # noqa: E402


def run(argv: list[str]) -> tuple[dict, str]:
    env = dict(os.environ, KICAD_CLI_STRICT="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "kicad_cli.main", *argv, "--compact"],
        capture_output=True,
        cwd=REPO,
        timeout=3600,
        env=env,
    )
    err = proc.stderr.decode("utf-8", "replace")
    out = proc.stdout.decode("utf-8", "replace")
    for line in out.splitlines():
        if line.startswith("{"):
            return json.loads(line), err
    raise AssertionError(f"no envelope on stdout.\nstdout: {out[:300]}\nstderr: {err[:500]}")


def check(argv: list[str]) -> dict:
    """Run and fail loudly on a contract violation, quoting what drifted."""
    doc, err = run(argv)
    assert "contract violation" not in err, err.strip()
    return doc


def demo(name: str, dest: Path) -> Path:
    shutil.copytree(DEMOS / name, dest)
    return next(dest.glob("*.kicad_pcb"))


def test_every_command_declares_a_schema_that_exists() -> None:
    commands = registry.build()
    assert commands, "the registry is empty"
    for c in commands:
        assert c["output_schema"] in registry.SCHEMAS, f"{c['path']} names a schema that is absent"
        assert registry.SCHEMAS[c["output_schema"]]["fields"], f"{c['path']} declares no fields"
        assert c["examples"], f"{c['path']} carries no example"
        assert c["type"] in ("read", "write")


def test_every_write_command_is_declared_as_a_write() -> None:
    """A read command that writes would slip past the confirmation gate."""
    by_path = {c["path"]: c for c in registry.build()}
    for path in (
        "sch relink",
        "board route",
        "board stitch",
        "board rewidth",
        "board widen",
        "board move",
        "fab gerber",
        "fab drill",
    ):
        assert by_path[path]["type"] == "write", f"{path} is not gated"


@pytest.mark.parametrize("argv", [["reference"], ["context"], ["doctor"], ["changelog"]])
def test_self_describing_commands_match_their_contract(argv: list[str]) -> None:
    doc = check(argv)
    assert doc["ok"] is True


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
@pytest.mark.parametrize(
    "command",
    [
        ["board", "audit"],
        ["board", "parity"],
        ["board", "plane"],
        ["sch", "link"],
        ["sch", "audit"],
        ["sch", "sync-preview"],
    ],
)
def test_read_commands_match_their_contract(command: list[str], tmp_path: Path) -> None:
    board = demo("interf_u", tmp_path / "iu")
    doc = check([*command, "--board", str(board)])
    assert doc["ok"] is True, doc


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
@pytest.mark.parametrize(
    ("command", "extra"),
    [
        (["board", "stitch"], ["--net", "GND"]),
        (["board", "widen"], []),
        (["board", "move"], ["--moves", "R5:50,50"]),
        (["board", "route"], ["--mode", "repair"]),
        # full clears every track before routing -- the most destructive mode
        # this tool has, and until now the only one with no live test at all.
        # Dispatch coverage counted `board route` as covered either way, which
        # is exactly the gap between dispatch coverage and FCC.
        (["board", "route"], ["--mode", "full"]),
        # rewidth is route's third mode and had no live test either; it exits
        # through a different K.ok than repair/full, so it is a separate shape.
        (["board", "route"], ["--mode", "rewidth"]),
        (["board", "rewidth"], []),
        (["sch", "relink"], []),
        (["fab", "drill"], []),
    ],
)
def test_write_commands_match_their_contract(
    command: list[str], extra: list[str], tmp_path: Path
) -> None:
    board = demo("interf_u", tmp_path / "iu")
    if command[:2] == ["sch", "relink"]:
        # relink is a no-op on a healthy board; strip the links so it has work.
        text = board.read_text(encoding="utf-8")
        board.write_text(
            "\n".join(ln for ln in text.splitlines() if '(path "' not in ln) + "\n",
            encoding="utf-8",
        )
    if command[:2] == ["fab", "drill"]:
        extra = [*extra, "--out", str(tmp_path / "fab")]

    plan = check([*command, "--board", str(board), *extra, "--dry-run"])
    assert plan["ok"] is False
    assert plan["error"]["code"] == "E_CONFIRMATION_REQUIRED"
    details = plan["error"]["details"]
    # The preview travels with the token: a gate nobody can see through is worse
    # than no gate, because it turns a decision into a reflex.
    assert details.get("preview"), f"{command} asks for confirmation without showing what for"
    assert details["confirm_token"].startswith("ct_")

    done = check([*command, "--board", str(board), *extra, "--confirm", details["confirm_token"]])
    assert done["ok"] is True, done


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_a_stale_token_is_refused(tmp_path: Path) -> None:
    """The token binds to the previewed state, so a plan computed against an
    older board must not be applied to a newer one."""
    board = demo("interf_u", tmp_path / "iu")
    plan = check(["board", "stitch", "--board", str(board), "--net", "GND", "--dry-run"])
    token = plan["error"]["details"]["confirm_token"]

    doc, _ = run(["board", "stitch", "--board", str(board), "--net", "GND", "--confirm", "ct_dead"])
    assert doc["ok"] is False
    assert doc["error"]["code"] == "E_CONFLICT"
    assert token != "ct_dead"


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_a_write_refuses_while_the_project_is_open_in_kicad(tmp_path: Path) -> None:
    board = demo("interf_u", tmp_path / "iu")
    (board.parent / f"~{board.name}.lck").write_text("{}", encoding="utf-8")

    doc, _ = run(["board", "stitch", "--board", str(board), "--net", "GND", "--dry-run"])
    assert doc["ok"] is False
    assert doc["error"]["code"] == "E_CONFLICT"
    assert doc["error"]["details"]["lock_files"]
