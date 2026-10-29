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
import sys
from pathlib import Path

import pytest
from kicad_demos import DEMOS, SKIP_REASON

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from kicad_cli.fileformat.sexpr import Document, List, quote  # noqa: E402

MARKER = "ZZINJECTZZ"

# Read commands that report on board or schematic content. Write commands are
# covered by test_contract_flag_coverage; what matters here is the declaration.
CASES = [
    "board analyze ampacity",
    "project parity",
    "board analyze reference-plane",
    "project links inspect",
    "sch check",
]
REFERENCE = json.loads((REPO / "kicad_cli/driver_contract.json").read_bytes())["reference"]


@pytest.fixture(scope="module")
def poisoned_design(tmp_path_factory):
    """A design whose net names and reference designators are marked."""
    if not DEMOS.exists():
        pytest.skip(SKIP_REASON)
    directory = tmp_path_factory.mktemp("poisoned-" + MARKER)
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
    schematic = board.with_suffix(".kicad_sch")
    document = Document.load(schematic)
    document.root.append(
        List.new(
            "label",
            quote(MARKER + "_DANGLING"),
            List.new("at", "-123.456", "-234.567", "0"),
            List.new("effects", List.new("font", List.new("size", "1.27", "1.27"))),
            List.new("uuid", quote("a219af75-4461-4506-bcf1-0ddce0a7b124")),
        )
    )
    document.save(schematic)
    return board


def _run(argv: list[str]) -> dict | None:
    from native_process import directory, run

    env = dict(os.environ, KICAD_CLI_STRICT="1", PYTHONIOENCODING="utf-8")
    env.pop("KICAD_CLI_TRACE", None)
    record, stdout, _ = run(
        [sys.executable, "-m", "kicad_cli.main", *argv, "--compact"],
        directory("untrusted-public"),
        cwd=REPO,
        env=env,
        timeout=3600,
        allowed=None,
        role="source-public-untrusted-boundary",
    )
    value = json.loads(stdout.decode("utf-8"))
    assert (record["exit"] == 0) == value["ok"], (record, value)
    return value


def _carries(value) -> bool:
    return MARKER in json.dumps(value, ensure_ascii=False, default=str)


def tainted_paths(value, path=()):
    if isinstance(value, dict):
        for key, item in value.items():
            if MARKER in key:
                yield path + (key,)
            yield from tainted_paths(item, path + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from tainted_paths(item, path + (index,))
    elif isinstance(value, str) and MARKER in value:
        yield path


def undeclared_paths(value, declared):
    prefixes = [tuple(field.split(".")) for field in declared]
    return sorted(
        ".".join(map(str, path))
        for path in tainted_paths(value)
        if not any(
            tuple(part for part in path if isinstance(part, str))[: len(prefix)] == prefix
            for prefix in prefixes
        )
    )


def test_nested_declaration_does_not_exempt_a_sibling():
    value = {"observation": {"inputs": [{"path": MARKER}], "source": MARKER}}
    assert undeclared_paths(value, ["observation.inputs"]) == ["observation.source"]


def test_dictionary_keys_are_user_text_too():
    value = {"nets": {MARKER: 1}}
    assert undeclared_paths(value, []) == ["nets." + MARKER]
    assert undeclared_paths(value, ["nets"]) == []


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
@pytest.mark.parametrize("path", CASES, ids=CASES)
def test_design_text_only_leaves_through_declared_fields(path, poisoned_design):
    command = next(c for c in REFERENCE["commands"] if c["path"] == path)
    schema = command["output_schema"]
    declared = REFERENCE["schemas"][schema]["untrusted_fields"]
    published = _run(["reference"])["data"]
    actual_command = next(c for c in published["commands"] if c["path"] == path)
    assert actual_command["output_schema"] == schema
    assert published["schemas"][schema]["untrusted_fields"] == declared
    flag = "--schematic" if path == "sch check" else "--board"
    target = poisoned_design.with_suffix(".kicad_sch") if flag == "--schematic" else poisoned_design
    before = target.read_bytes()
    doc = _run([*path.split(), flag, str(target)])
    assert doc is not None and doc["ok"] is True, doc
    assert target.read_bytes() == before
    assert set(doc["data"]) - {"_untrusted"} <= set(REFERENCE["schemas"][schema]["fields"])
    assert list(tainted_paths(doc["data"])), "No user-origin text or path was observed"
    undeclared = undeclared_paths(doc["data"], declared)
    assert not undeclared, (
        f"{path} returns design text through {undeclared}, which `reference` does not "
        f"declare untrusted. An agent is told it may read these as the tool's own words."
    )
    assert not undeclared_paths(doc["data"], doc["data"].get("_untrusted", [])), (
        "The runtime marking must cover the same untrusted values as reference"
    )


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_at_least_one_command_actually_carries_the_marker(poisoned_design):
    """Guard the guard: all-clean results would satisfy every test above.

    If the injection stops reaching the output -- a changed board format, a
    command that stops reporting names -- every assertion here passes while
    checking nothing. One positive observation keeps the file honest.
    """
    doc = _run(["board", "analyze", "ampacity", "--board", str(poisoned_design)])
    assert doc is not None and doc["ok"] is True, doc
    assert _carries(doc["data"]["nets"]) or _carries(doc["data"]["tracks"]), (
        "no command carried the injected marker, so the untrusted-field "
        "assertions above are vacuous"
    )


@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
def test_removed_sync_preview_alias_is_a_usage_error(poisoned_design):
    before = poisoned_design.read_bytes()
    doc = _run(["sch", "sync-preview", "--board", str(poisoned_design)])
    assert not doc["ok"] and doc["error"]["code"] == "E_USAGE"
    assert doc["error"]["details"]["problem"] == "unknown_command"
    assert poisoned_design.read_bytes() == before
