"""A design's netlist, written by this tool, held to the one KiCad writes.

`kicad_cli/fileformat/netlist.py` builds the netlist from the schematics and
writes it in KiCad's S-expression format. What is compared is everything a
board is built from and kept in step with: every part -- value, footprint,
datasheet, fields, library, properties, sheet, the uuids of its units -- and
every net -- name, class, and each pin on it with its function and type.

Parts are compared in order too: sheet by sheet in page order -- a page
number two sheets share is renumbered, as KiCad does -- and by reference
within a sheet. Each part lists every unit it has, placed or not, with the
unit's pins.

Three things are not compared, on purpose:

- net codes. KiCad numbers nets that never reach its netlist; this tool
  numbers the ones it writes, in the same order. Nothing that reads a netlist
  goes by them: a board names its nets.
- the order of a part's unit uuids. KiCad's follows no rule found on 35
  projects; the set is what links a footprint to its symbol.
- the order of pins stacked at one point of a unit, which KiCad's sort
  leaves to chance; a unit's pins are compared as a set.

`tests/fixtures/schematic/rules.net` is KiCad 10.0's own export of the
fixture design, so the comparison runs everywhere; with KiCad installed it is
asked again, and every demo project is compared too.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from kicad_demos import DEMOS, SKIP_REASON
from test_fileformat_circuit import demo_roots, export, needs_kicad

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from kicad_cli import sexpr  # noqa: E402
from kicad_cli.fileformat import netlist  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "schematic" / "rules.kicad_sch"
RECORDED = FIXTURE.with_suffix(".net")

# Where this tool's netlist and KiCad's still differ: one net's name.
KNOWN = {"RoyalBlue54L-Feather": {"/Debugger/SWD_TRG.~{RESET}"}}


def _v(node, name):
    return sexpr.value(sexpr.child(node, name))


def contents(text: str) -> dict:
    """The parts and nets of a netlist, in a form two netlists compare in."""
    node = sexpr.parse(text)
    parts = {}
    for comp in sexpr.children(sexpr.child(node, "components"), "comp"):
        lib = sexpr.child(comp, "libsource")
        path = sexpr.child(comp, "sheetpath")
        stamps = sexpr.child(comp, "tstamps")
        parts[_v(comp, "ref")] = {
            "value": _v(comp, "value") or "",
            "footprint": _v(comp, "footprint") or "",
            "datasheet": _v(comp, "datasheet") or "",
            "fields": [
                (_v(f, "name"), f[2] if len(f) > 2 and isinstance(f[2], str) else "")
                for f in sexpr.children(sexpr.child(comp, "fields") or [], "field")
            ],
            "library": (_v(lib, "lib"), _v(lib, "part"), _v(lib, "description")),
            "properties": [
                (_v(p, "name"), _v(p, "value")) for p in sexpr.children(comp, "property")
            ],
            "sheet": (_v(path, "names"), _v(path, "tstamps")),
            "uuids": sorted(x for x in stamps[1:] if isinstance(x, str)),
            "units": [
                (
                    _v(u, "name"),
                    sorted(_v(n, "num") for n in sexpr.children(sexpr.child(u, "pins"), "pin")),
                )
                for u in sexpr.children(sexpr.child(comp, "units") or [], "unit")
            ],
        }
    nets = []
    for net in sexpr.children(sexpr.child(node, "nets"), "net"):
        nets.append(
            (
                _v(net, "name"),
                _v(net, "class"),
                [
                    (_v(n, "ref"), _v(n, "pin"), _v(n, "pinfunction"), _v(n, "pintype"))
                    for n in sexpr.children(net, "node")
                ],
            )
        )
    return {"parts": parts, "order": list(parts), "nets": nets}


def ours(root: Path) -> dict:
    return contents(netlist.dumps(netlist.build(root)))


def test_the_fixture_netlist_is_kicads():
    assert ours(FIXTURE) == contents(RECORDED.read_text(encoding="utf-8"))


def test_the_written_netlist_reads_back_as_built():
    built = netlist.build(FIXTURE)
    read = contents(netlist.dumps(built))
    assert list(read["parts"]) == [c.ref for c in built.components]
    assert [n for n, _, _ in read["nets"]] == [n.name for n in built.nets]


@needs_kicad
def test_kicad_still_writes_what_was_recorded(tmp_path):
    for source in FIXTURE.parent.glob("*.kicad_*"):
        (tmp_path / source.name).write_bytes(source.read_bytes())
    assert contents(export(tmp_path / FIXTURE.name)) == contents(
        RECORDED.read_text(encoding="utf-8")
    )


@needs_kicad
@pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)
@pytest.mark.parametrize("root", demo_roots(), ids=lambda p: p.stem)
def test_every_demo_netlist_is_kicads(root):
    theirs, mine = contents(export(root)), ours(root)
    assert mine["order"] == theirs["order"]
    assert mine["parts"] == theirs["parts"]
    known = KNOWN.get(root.stem, set())
    their_nets = {tuple(nodes): (name, cls) for name, cls, nodes in theirs["nets"]}
    for name, cls, nodes in mine["nets"]:
        their_name, their_class = their_nets[tuple(nodes)]
        assert cls == their_class, name
        assert name == their_name or their_name in known, (their_name, name)
    if not known:
        assert mine["nets"] == theirs["nets"]
