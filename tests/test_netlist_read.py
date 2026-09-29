"""The parts `sch sync-preview` and `sch relink` hold a board up to.

Both read the schematic's netlist into one record per part -- reference,
value, footprint, fields, properties, sheet, and each unit's uuid with the
footprint path it makes -- and compare the board with those. The records
came from KiCad's export; they come from this tool's own netlist now, and on
every demo project KiCad ships they are the records KiCad's export gave,
part for part. The order of a part's unit uuids is left out: KiCad's follows
no rule found on 35 projects, and the updater tries every one.

KiCad's export also warned of annotation errors on stderr, and both commands
refused to count when it did. `kicad_cli/fileformat/annotation.py` finds
them itself. No demo has one, and KiCad agrees. The fixture design is
changed to have each kind -- measured first as what makes KiCad's export
warn -- and each is found here; with KiCad installed, its export is asked
too. A sheet whose file is missing KiCad's export passes over without a
word, leaving that sheet's parts out; it is found here as well.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from kicad_demos import DEMOS, SKIP_REASON
from test_fileformat_circuit import demo_roots, export, needs_kicad, official_cli

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from kicad_cli import netlist, sexpr  # noqa: E402
from kicad_cli.fileformat.sexpr import Document, quote  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "schematic"


def ordered(record: dict) -> dict:
    return {**record, "uuids": sorted(record["uuids"]), "paths": sorted(record["paths"])}


@needs_kicad
@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
@pytest.mark.parametrize("root", demo_roots(), ids=lambda p: p.stem)
def test_every_demo_reads_as_kicads_export_read(root: Path) -> None:
    theirs = [ordered(c) for c in netlist.components(sexpr.parse(export(root)))]
    found = netlist.read(root)
    ours = [ordered(c) for c in found.components]
    assert [c["ref"] for c in ours] == [c["ref"] for c in theirs]
    for mine, kicads in zip(ours, theirs, strict=True):
        assert mine == kicads, mine["ref"]
    assert found.annotation == []
    assert found.missing_sheets == []


# -- annotation errors, and a missing sheet --------------------------------------------


def copy_fixture(tmp: Path) -> Path:
    for source in FIXTURE.glob("rules*.kicad_*"):
        shutil.copyfile(source, tmp / source.name)
    return tmp / "rules.kicad_sch"


def rename(root: Path, file: str, old: str, new: str) -> None:
    path = root.parent / file
    text = path.read_bytes().decode("utf-8")
    assert text.count(f'(reference "{old}")') == 1, old
    path.write_bytes(text.replace(f'(reference "{old}")', f'(reference "{new}")').encode())


def second_unit_of_u1(root: Path, change) -> None:
    """Edit the symbol that places U1's unit 2."""
    document = Document.load(root)
    for symbol in document.root.find_all("symbol"):
        for project in (
            symbol.find("instances").find_all("project")
            if symbol.find("instances") is not None
            else []
        ):
            for path in project.find_all("path"):
                if path.find("reference").value(1) == "U1" and path.find("unit").atom(1) == "2":
                    change(symbol)
                    root.write_bytes(document.dumps().encode("utf-8"))
                    return
    raise AssertionError("U1's unit 2 not found")


def unannotated(root: Path) -> None:
    rename(root, "rules.kicad_sch", "R1", "R?")


def duplicate_on_one_sheet(root: Path) -> None:
    rename(root, "rules.kicad_sch", "R2", "R1")


def duplicate_across_instances(root: Path) -> None:
    rename(root, "rules_child.kicad_sch", "R201", "R101")


def duplicate_power_symbol(root: Path) -> None:
    rename(root, "rules_child.kicad_sch", "#PWR201", "#PWR101")


def units_with_different_values(root: Path) -> None:
    def change(symbol):
        for prop in symbol.find_all("property"):
            if prop.value(1) == "Value":
                prop.set(2, quote("DUAL2"))

    second_unit_of_u1(root, change)


def a_unit_the_part_does_not_have(root: Path) -> None:
    def change(symbol):
        instances = symbol.find("instances")
        for project in instances.find_all("project"):
            for path in project.find_all("path"):
                path.find("unit").set(1, "9")

    second_unit_of_u1(root, change)


CASES = [
    (unannotated, "unannotated", "R?"),
    (duplicate_on_one_sheet, "duplicate_reference", "R1"),
    (duplicate_across_instances, "duplicate_reference", "R101"),
    (duplicate_power_symbol, "duplicate_reference", "#PWR101"),
    (units_with_different_values, "unit_value_mismatch", "U1"),
    (a_unit_the_part_does_not_have, "extra_units", "U1"),
]


def test_the_fixture_has_no_annotation_error(tmp_path: Path) -> None:
    found = netlist.read(copy_fixture(tmp_path))
    assert found.annotation == [] and found.missing_sheets == []
    assert found.components


@pytest.mark.parametrize(
    ("edit", "rule", "reference"), CASES, ids=lambda x: getattr(x, "__name__", x)
)
def test_each_annotation_error_is_found(edit, rule, reference, tmp_path: Path) -> None:
    root = copy_fixture(tmp_path)
    edit(root)
    found = netlist.read(root)
    assert [(p.rule, p.reference) for p in found.annotation] == [(rule, reference)]


def test_a_missing_sheet_is_found(tmp_path: Path) -> None:
    root = copy_fixture(tmp_path)
    (tmp_path / "rules_child.kicad_sch").unlink()
    found = netlist.read(root)
    assert len(found.missing_sheets) == 2  # the child sheet is placed twice
    assert all("rules_child.kicad_sch" in m for m in found.missing_sheets)


def kicad_warns(root: Path) -> str:
    """What KiCad's export says besides the netlist. Its annotation warning
    goes to stdout, not stderr -- where the check this replaced looked, and
    so never saw it."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out.net"
        for _ in range(3):  # Windows fails roughly one launch in six
            proc = subprocess.run(
                [official_cli(), "sch", "export", "netlist", "-o", str(out), str(root)],
                capture_output=True,
                timeout=600,
            )
            if out.exists():
                return (proc.stdout + proc.stderr).decode("utf-8", "replace").strip()
    raise AssertionError("KiCad would not export the fixture")


@needs_kicad
def test_kicad_warns_of_each_and_of_nothing_else(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    assert kicad_warns(copy_fixture(base)) == ""
    for edit, _, _ in CASES:
        work = tmp_path / edit.__name__
        work.mkdir()
        root = copy_fixture(work)
        edit(root)
        assert kicad_warns(root), f"KiCad no longer warns of {edit.__name__}"
    work = tmp_path / "missing"
    work.mkdir()
    root = copy_fixture(work)
    (work / "rules_child.kicad_sch").unlink()
    assert kicad_warns(root) == "", "KiCad has started to warn of a missing sheet"
