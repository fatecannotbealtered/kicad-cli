"""``sch edit``: an existing schematic changed as described, and nothing else.

Each test edits a copy of `tests/fixtures/sch_edit/`, a small design that
names nets every way KiCad does -- local and global labels, power symbols, a
hierarchical label with its sheet pins -- and places one sheet twice. The
command runs as an agent runs it: a dry run, then the token. What is checked
is what the design says afterwards, read back as a design, and that a change
the command cannot make leaves every file as it was.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_fileformat_circuit import needs_kicad, official_cli

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from kicad_cli.fileformat.circuit import Design, connect  # noqa: E402
from kicad_cli.fileformat.schematic import Schematic  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "sch_edit"
FILES = ("edit.kicad_sch", "edit_child.kicad_sch", "edit.kicad_pro")


def run(argv: list[str]) -> dict:
    env = dict(os.environ, KICAD_CLI_STRICT="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "kicad_cli.main", *argv, "--compact"],
        capture_output=True,
        cwd=REPO,
        env=env,
        timeout=600,
    )
    err = proc.stderr.decode("utf-8", "replace")
    assert "contract violation" not in err, err
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(f"no envelope: {err[-800:]}")


@pytest.fixture
def design(tmp_path: Path) -> Path:
    for name in FILES:
        shutil.copy(FIXTURE / name, tmp_path / name)
    return tmp_path / "edit.kicad_sch"


def contents(root: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in root.parent.glob("*.kicad_sch")}


def plan(root: Path, changes: list[dict]) -> dict:
    spec = root.parent / "changes.json"
    spec.write_text(json.dumps({"changes": changes}), encoding="utf-8")
    return run(["sch", "edit", "--schematic", str(root), "--changes", str(spec), "--dry-run"])


def edit(root: Path, changes: list[dict]) -> dict:
    """Dry run, then confirm with the token it gave."""
    asked = plan(root, changes)
    if asked["ok"] or asked["error"]["code"] != "E_CONFIRMATION_REQUIRED":
        return asked
    spec = root.parent / "changes.json"
    token = asked["error"]["details"]["confirm_token"]
    return run(
        ["sch", "edit", "--schematic", str(root), "--changes", str(spec), "--confirm", token]
    )


def nets(root: Path) -> dict[str, set[str]]:
    return {
        n.name: {f"{p.reference}.{p.pin.number}" for p in n.pins} for n in connect(Design(root))
    }


def symbols(root: Path, reference: str) -> list:
    design = Design(root)
    found = []
    for instance in design.instances:
        for sym in instance.schematic.symbols:
            inst = sym.instance(instance.path)
            if inst is not None and inst.reference == reference:
                found.append(sym)
    return found


# -- set --------------------------------------------------------------------------------------


def test_a_parts_fields_and_flags_are_set_on_every_unit(design: Path) -> None:
    before = nets(design)
    done = edit(
        design,
        [{"op": "set", "ref": "U1", "value": "TL072", "fields": {"MPN": "TL072CDR"}, "dnp": True}],
    )
    assert done["ok"] is True, done
    assert done["data"]["status"] == "PASS"
    report = done["data"]["changes"][0]
    assert report["units"] == 2
    assert report["fields"] == {
        "Value": {"from": "LM358", "to": "TL072"},
        "MPN": {"from": None, "to": "TL072CDR"},
    }
    units = symbols(design, "U1")
    assert len(units) == 2
    assert all(u.properties["Value"] == "TL072" for u in units)
    assert all(u.properties["MPN"] == "TL072CDR" for u in units)
    assert all(u.dnp for u in units)
    assert nets(design) == before
    assert all(done["data"]["verified"][k] for k in ("nets_join_the_same_pins", "fields_read_back"))


def test_only_what_changed_is_rewritten(design: Path) -> None:
    """The file is edited, not regenerated: the lines around the change are
    KiCad's own, as they were."""
    old = (design.parent / "edit.kicad_sch").read_text(encoding="utf-8")
    assert edit(design, [{"op": "set", "ref": "R1", "value": "22k"}])["ok"]
    new = (design.parent / "edit.kicad_sch").read_text(encoding="utf-8")
    changed = [
        line
        for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0)
        if line[:1] in "+-" and line[:3] not in ("+++", "---")
    ]
    assert changed == ['-\t\t(property "Value" "10k"', '+\t\t(property "Value" "22k"']
    assert (design.parent / "edit_child.kicad_sch").read_bytes() == (
        FIXTURE / "edit_child.kicad_sch"
    ).read_bytes()


def test_a_new_field_is_written_hidden_as_the_file_writes_its_fields(design: Path) -> None:
    assert edit(design, [{"op": "set", "ref": "R1", "fields": {"MPN": "RC0603"}}])["ok"]
    sym = symbols(design, "R1")[0]
    field = next(p for p in sym.node.find_all("property") if p.value(1) == "MPN")
    # A version-20250114 file hides a field with its own (hide yes).
    assert field.find("hide") is not None
    assert [a for a in field.find("at").values()] == ["50.8", "50.8", "0"]


def test_a_field_can_be_removed_and_a_mandatory_one_emptied(design: Path) -> None:
    assert edit(design, [{"op": "set", "ref": "R1", "fields": {"MPN": "X"}}])["ok"]
    done = edit(design, [{"op": "set", "ref": "R1", "fields": {"MPN": None}, "footprint": ""}])
    assert done["ok"] is True, done
    sym = symbols(design, "R1")[0]
    assert "MPN" not in sym.properties and sym.properties["Footprint"] == ""


def test_a_part_on_a_sheet_placed_twice_changes_in_both(design: Path) -> None:
    """One drawing, two references: the other is named, not surprised."""
    done = edit(design, [{"op": "set", "ref": "R10", "value": "10k"}])
    assert done["ok"] is True, done
    assert done["data"]["changes"][0]["also_changes"] == ["R20"]
    assert symbols(design, "R20")[0].properties["Value"] == "10k"


# -- rename ----------------------------------------------------------------------------------


def test_renaming_a_local_label(design: Path) -> None:
    before = nets(design)
    done = edit(design, [{"op": "rename", "net": "/OUT", "to": "DRIVE"}])
    assert done["ok"] is True, done
    assert done["data"]["changes"][0]["to"] == "/DRIVE"
    after = nets(design)
    assert after["/DRIVE"] == before["/OUT"] and "/OUT" not in after


def test_renaming_a_label_on_a_sheet_placed_twice_renames_both_placements(design: Path) -> None:
    done = edit(design, [{"op": "rename", "net": "/CHILD_A/LOCAL", "to": "NODE"}])
    assert done["ok"] is True, done
    assert done["data"]["changes"][0]["also_renames"] == [
        {"net": "/CHILD_B/LOCAL", "to": "/CHILD_B/NODE"}
    ]
    after = nets(design)
    assert after["/CHILD_A/NODE"] == {"R10.2", "C10.1"}
    assert after["/CHILD_B/NODE"] == {"R20.2", "C20.1"}


def test_a_global_name_is_renamed_on_every_sheet(design: Path) -> None:
    done = edit(design, [{"op": "rename", "net": "EN", "to": "ENABLE"}])
    assert done["ok"] is True, done
    assert done["data"]["changes"][0]["files"] == ["edit.kicad_sch", "edit_child.kicad_sch"]
    assert nets(design)["ENABLE"] == {"R2.2", "C10.2", "C20.2"}


def test_a_power_net_is_renamed_by_its_symbols_values(design: Path) -> None:
    done = edit(design, [{"op": "rename", "net": "+3V3", "to": "+3V3A"}])
    assert done["ok"] is True, done
    assert nets(design)["+3V3A"] == {"R3.1"}


def test_a_name_across_a_sheets_edge_moves_with_its_sheet_pins(design: Path) -> None:
    """The child's hierarchical label and the pin meeting it on every sheet
    symbol placing the child: both placements stay joined as before."""
    before = nets(design)
    done = edit(design, [{"op": "rename", "net": "/CHILD_B/IN", "to": "DATA_IN"}])
    assert done["ok"] is True, done
    after = nets(design)
    assert after["/CHILD_B/DATA_IN"] == before["/CHILD_B/IN"]
    assert after["/SIG"] == before["/SIG"]  # CHILD_A's pin, renamed, still meets its label
    root = Schematic.load(design)
    assert sorted(p.name for s in root.sheets for p in s.pins) == ["DATA_IN", "DATA_IN"]


# -- what is refused -------------------------------------------------------------------------


def test_a_rename_that_would_join_two_nets_writes_nothing(design: Path) -> None:
    before = contents(design)
    done = edit(design, [{"op": "rename", "net": "/OUT", "to": "SIG"}])
    assert done["ok"] is False
    assert done["error"]["code"] == "E_CONFLICT"
    moved = done["error"]["details"]["verified"]["problems"]["joined_or_split"]
    assert any(m["before"] is None and m["after"] == "/SIG" for m in moved)
    assert contents(design) == before


def test_a_net_nothing_names_has_no_name_to_change(design: Path) -> None:
    done = plan(design, [{"op": "rename", "net": "unconnected-(U1A-IN+-Pad3)", "to": "X"}])
    assert done["error"]["code"] == "E_VALIDATION"
    assert "no name to change" in done["error"]["details"]["problems"][0]["problem"]


def test_every_problem_is_refused_together(design: Path) -> None:
    before = contents(design)
    done = plan(
        design,
        [
            {"op": "set", "ref": "R11", "value": "1k"},
            {"op": "set", "ref": "R1", "colour": "red"},
            {"op": "set", "ref": "#PWR", "value": "+5V"},
            {"op": "set", "ref": "R2", "fields": {"Reference": "R9"}},
            {"op": "rename", "net": "/NOPE", "to": "X"},
        ],
    )
    assert done["error"]["code"] == "E_VALIDATION"
    problems = done["error"]["details"]["problems"]
    assert {p["index"] for p in problems} >= {0, 1, 3, 4}
    assert "R1" in next(p for p in problems if p["index"] == 0)["nearest"]
    assert contents(design) == before


def test_a_power_symbols_value_is_its_net_and_is_not_set(design: Path) -> None:
    root = Schematic.load(design)
    power = next(s for s in root.symbols if s.lib_id == "fixture:PWR")
    done = plan(design, [{"op": "set", "ref": power.instances[0].reference, "value": "+5V"}])
    assert "rename the net instead" in done["error"]["details"]["problems"][0]["problem"]


def test_changes_that_change_nothing_write_nothing(design: Path) -> None:
    before = contents(design)
    done = plan(design, [{"op": "set", "ref": "R1", "value": "10k"}])
    assert done["ok"] is True and done["data"]["status"] == "NOOP"
    assert contents(design) == before


def test_a_token_is_spent_only_on_the_design_it_was_shown(design: Path) -> None:
    """Every file's hash is in the preview: a design changed since the dry
    run -- here the child, which the change does not even touch -- is not
    written to."""
    asked = plan(design, [{"op": "set", "ref": "R1", "value": "22k"}])
    child = design.parent / "edit_child.kicad_sch"
    child.write_bytes(child.read_bytes().replace(b'"4k7"', b'"4k70"'))
    before = contents(design)
    spec = design.parent / "changes.json"
    token = asked["error"]["details"]["confirm_token"]
    done = run(
        ["sch", "edit", "--schematic", str(design), "--changes", str(spec), "--confirm", token]
    )
    assert done["ok"] is False
    assert contents(design) == before


def test_a_design_open_in_kicad_is_not_written(design: Path) -> None:
    (design.parent / "~edit.kicad_sch.lck").write_text("{}", encoding="utf-8")
    done = plan(design, [{"op": "set", "ref": "R1", "value": "22k"}])
    assert done["error"]["code"] == "E_CONFLICT"
    spec = design.parent / "changes.json"
    forced = run(
        ["sch", "edit", "--schematic", str(design), "--changes", str(spec), "--ignore-lock",
         "--dry-run"]
    )  # fmt: skip
    assert forced["error"]["code"] == "E_CONFIRMATION_REQUIRED"


def test_a_changes_file_that_is_not_changes_is_refused(design: Path) -> None:
    spec = design.parent / "changes.json"
    spec.write_text('{"changes": [{"op": "delete", "ref": "R1"}]}', encoding="utf-8")
    done = run(["sch", "edit", "--schematic", str(design), "--changes", str(spec), "--dry-run"])
    assert done["error"]["code"] == "E_VALIDATION"


# -- ERC, before and after --------------------------------------------------------------------


def test_what_an_edit_adds_to_erc_is_reported(design: Path) -> None:
    """A label renamed to differ from another only in case: ERC's warning
    about it is new, and said to be."""
    done = plan(design, [{"op": "rename", "net": "/OUT", "to": "Sig"}])
    erc = done["error"]["details"]["preview"]["erc"]
    assert erc["new_count"] >= 1
    assert {v["rule"] for v in erc["new"]} == {"similar_labels"}


# -- KiCad reads it the same way --------------------------------------------------------------


def _kicad_nets(root: Path) -> dict[str, set[str]]:
    out = root.parent / "kicad.net"
    for _ in range(3):  # Windows fails roughly one launch in six
        subprocess.run(
            [official_cli(), "sch", "export", "netlist", "--format", "kicadsexpr", "-o",
             str(out), str(root)],
            capture_output=True,
            timeout=600,
        )  # fmt: skip
        if out.exists():
            break
    text = out.read_text(encoding="utf-8")
    found = {}
    for name, body in re.findall(
        r'\(net\s+\(code "\d+"\)\s+\(name "([^"]*)"\)(.*?)\n\t\t\)', text, re.S
    ):
        found[name] = {
            f"{r}.{p}" for r, p in re.findall(r'\(ref "([^"]+)"\)\s+\(pin "([^"]+)"\)', body)
        }
    return found


@needs_kicad
def test_kicad_reads_the_edited_design_as_this_tool_does(design: Path) -> None:
    done = edit(
        design,
        [
            {"op": "set", "ref": "R10", "value": "10k", "fields": {"MPN": "RC0603"}},
            {"op": "rename", "net": "/CHILD_A/LOCAL", "to": "NODE"},
            {"op": "rename", "net": "EN", "to": "ENABLE"},
            {"op": "rename", "net": "+3V3", "to": "+3V3A"},
            {"op": "rename", "net": "/CHILD_B/IN", "to": "DATA_IN"},
        ],
    )
    assert done["ok"] is True, done
    assert _kicad_nets(design) == nets(design)
    text = (design.parent / "kicad.net").read_text(encoding="utf-8")
    assert re.search(r'\(ref "R20"\)\s+\(value "10k"\)', text)
    assert '(name "MPN") "RC0603"' in text


def test_a_name_a_bus_on_its_sheet_carries_is_not_renamed(tmp_path: Path) -> None:
    """DATA0 is a label, and a member of the bus DATA[0..3] on the same
    sheet: renamed, the net would leave the bus. Refused, and said why."""
    erc = REPO / "tests" / "fixtures" / "erc"
    for source in erc.glob("erc2*.kicad_*"):
        shutil.copy(source, tmp_path / source.name)
    done = plan(tmp_path / "erc2.kicad_sch", [{"op": "rename", "net": "/DATA0", "to": "D0"}])
    assert done["error"]["code"] == "E_VALIDATION"
    assert "leave the bus" in done["error"]["details"]["problems"][0]["problem"]
