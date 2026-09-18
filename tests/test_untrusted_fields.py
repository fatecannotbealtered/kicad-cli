"""`untrusted_fields` is a security claim; measure it instead of trusting it.

SECURITY.md tells an agent that fields carrying text from the board file --
reference designators, net names, silkscreen -- are declared in each schema's
`untrusted_fields`, and that everything else may be read as the tool's own
words. Nothing checked that. A field that starts carrying a net name without
being added to the list turns a design file into a channel for instructions
aimed at whoever reads the output.

So the design is poisoned with a marker and the commands are run against it:
whatever comes back carrying the marker must be declared. Over-declaring is
fine and is not asserted against -- a field that is clean on this board may
carry text on another, and the safe direction is to say so in advance.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from kicad_demos import DEMOS, SKIP_REASON

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from kicad_cli import registry  # noqa: E402

MARKER = "ZZINJECTZZ"

# Read commands that report on board or schematic content. Write commands are
# covered by test_contract_flag_coverage; what matters here is the declaration.
CASES = [
    ("board audit", "board_audit"),
    ("board parity", "board_parity"),
    ("board plane", "board_plane"),
    ("sch link", "sch_link"),
    ("sch audit", "sch_audit"),
    ("sch sync-preview", "sch_sync_preview"),
]


@pytest.fixture(scope="module")
def poisoned_design(tmp_path_factory):
    """A design whose net names and reference designators are marked."""
    if not DEMOS.exists():
        pytest.skip(SKIP_REASON)
    directory = tmp_path_factory.mktemp("poisoned")
    for item in (DEMOS / "interf_u").iterdir():
        if item.is_file():
            shutil.copy2(item, directory / item.name)
    board = next(directory.glob("*.kicad_pcb"))
    text = board.read_text(encoding="utf-8")
    text, nets = re.subn(r'\(net (\d+) "([^"]+)"\)', rf'(net \1 "{MARKER}_\2")', text)
    text, refs = re.subn(
        r'\(property "Reference" "([^"]+)"', rf'(property "Reference" "{MARKER}_\1"', text
    )
    # A run that poisoned nothing would pass every assertion below and mean
    # nothing, which is the failure mode this whole file exists to avoid.
    assert nets and refs, "the marker never reached the design; this test proves nothing"
    board.write_text(text, encoding="utf-8")
    return board


def _run(argv: list[str]) -> dict | None:
    env = dict(os.environ, KICAD_CLI_STRICT="1", PYTHONIOENCODING="utf-8")
    env.pop("KICAD_CLI_TRACE", None)
    proc = subprocess.run(
        [sys.executable, "-m", "kicad_cli.main", *argv, "--compact"],
        capture_output=True,
        cwd=REPO,
        env=env,
        timeout=3600,
    )
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith("{"):
            return json.loads(line)
    return None


def _carries(value) -> bool:
    return MARKER in json.dumps(value, ensure_ascii=False, default=str)


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
@pytest.mark.parametrize(("path", "schema"), CASES, ids=[c[0] for c in CASES])
def test_design_text_only_leaves_through_declared_fields(path, schema, poisoned_design):
    doc = _run([*path.split(), "--board", str(poisoned_design)])
    assert doc is not None and doc["ok"] is True, doc
    declared = set(registry.SCHEMAS[schema].get("untrusted_fields", []))
    tainted = {name for name, value in doc["data"].items() if _carries(value)}
    undeclared = sorted(tainted - declared)
    assert not undeclared, (
        f"{path} returns design text through {undeclared}, which `reference` does not "
        f"declare untrusted. An agent is told it may read these as the tool's own words."
    )


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_at_least_one_command_actually_carries_the_marker(poisoned_design):
    """Guard the guard: all-clean results would satisfy every test above.

    If the injection stops reaching the output -- a changed board format, a
    command that stops reporting names -- every assertion here passes while
    checking nothing. One positive observation keeps the file honest.
    """
    doc = _run(["board", "audit", "--board", str(poisoned_design)])
    assert doc is not None and doc["ok"] is True, doc
    assert any(_carries(value) for value in doc["data"].values()), (
        "no command carried the injected marker, so the untrusted-field "
        "assertions above are vacuous"
    )
