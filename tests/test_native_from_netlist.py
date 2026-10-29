"""``board from-netlist`` moved off pcbnew: the old implementation is the reference.

One netlist, both versions, and pcbnew reads the two boards: every footprint
-- its name, value, position, every pad's number, shape, layers and net --
must be the same except the explicit correctness fixes below:

- the outline. The pcbnew version fitted it to the parts' text as well as
  their copper, which needs the font's measurements; this one fits copper,
  drawings and courtyards. It is provisional either way: `board footprint place` moves
  the parts.
- the uuids. pcbnew drew them at random; here they follow from the netlist,
  so one netlist gives one board, byte for byte.
- Duplicate-number pads now all receive their pin's net and the requested
  library-qualified footprint ID is retained. The old payload's omissions
  are corrected from the independent fixture, not copied as expected output.

pcbnew's Save also wrote the project files beside the board, which `board
netclass` edits; they come out the same, byte for byte -- but only where
there are none yet, where pcbnew wrote over a project the schematic had.

The netlist exercises what a footprint library holds: chip parts, a TQFP, a
QFN whose exposed pad is a dozen pads of one number, a USB-C receptacle with
its shield pads, a through-hole header, a crystal, a mounting hole.
"""

from __future__ import annotations

import json
import subprocess

import pytest
from native_process import payload as sdk_payload
from payload_reference import REPO, differences, kicad_python, needs_payload
from payload_reference import legacy_port as native

ORACLE = REPO / "tests" / "pcbnew_oracle.py"

PARTS = [
    ("C1", "100nF", "Capacitor_SMD:C_0603_1608Metric"),
    ("J1", "USB_C", "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal"),
    ("J2", "Conn_01x04", "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical"),
    ("H1", "MountingHole", "MountingHole:MountingHole_3.2mm_M3_Pad"),
    ("R1", "10k", "Resistor_SMD:R_0805_2012Metric"),
    ("U1", "MCU", "Package_QFP:TQFP-32_7x7mm_P0.8mm"),
    ("U2", "LDO", "Package_TO_SOT_SMD:SOT-23"),
    ("U3", "DRV", "Package_DFN_QFN:QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm_ThermalVias"),
    ("Y1", "16MHz", "Crystal:Crystal_SMD_HC49-SD"),
]
NETS = {
    "GND": [("C1", "2"), ("J1", "A1"), ("J1", "SH"), ("J2", "4"), ("U1", "5"), ("U2", "1"),
            ("U3", "17")],
    "+5V": [("J1", "A4"), ("J2", "1"), ("U2", "3")],
    "+3V3": [("C1", "1"), ("R1", "1"), ("U1", "4"), ("U2", "2"), ("U3", "1")],
    "XTAL": [("U1", "7"), ("Y1", "1")],
    "RESET": [("R1", "2"), ("U1", "29"), ("J2", "2")],
    "MISSING": [("J2", "3"), ("U2", "9")],
}  # fmt: skip


def netlist_text() -> str:
    comps = "".join(
        f'    (comp (ref "{r}") (value "{v}") (footprint "{f}"))\n' for r, v, f in PARTS
    )
    nets = "".join(
        f'    (net (code {i}) (name "{name}")'
        + "".join(f' (node (ref "{r}") (pin "{p}"))' for r, p in nodes)
        + ")\n"
        for i, (name, nodes) in enumerate(NETS.items(), start=1)
    )
    return f'(export (version "E")\n  (components\n{comps}  )\n  (nets\n{nets}  ))\n'


def build_native(netlist, out) -> tuple[dict, dict]:
    asked = native("board", "from-netlist", "--netlist", str(netlist), "--out", str(out))
    token = asked["error"]["details"]["confirm_token"]
    done = native(
        "board", "from-netlist", "--netlist", str(netlist), "--out", str(out), "--confirm", token
    )
    return asked, done


def build_payload(netlist, out) -> tuple[dict, dict]:
    import tempfile  # noqa: PLC0415

    from kicad_cli import boardgen  # noqa: PLC0415

    plan = boardgen.plan(str(netlist), 10.0, 10.0)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(plan, fh)
    argv = ["--plan", fh.name, "--out", str(out), "--netlist", str(netlist)]
    asked = sdk_payload("board_build", argv)
    token = asked["error"]["details"]["confirm_token"]
    done = sdk_payload("board_build", [*argv, "--confirm", token])
    return asked, done


def pcbnew_reads(board) -> dict:
    proc = subprocess.run(
        [kicad_python(), str(ORACLE), str(board)], capture_output=True, timeout=600
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-2000:]
    dump = json.loads(proc.stdout.decode("utf-8"))
    dump.pop("board")
    return dump


@needs_payload
def test_the_native_board_is_the_payloads_but_for_its_outline(tmp_path):
    netlist = tmp_path / "circuit.net"
    netlist.write_text(netlist_text(), encoding="utf-8")
    theirs, ours = tmp_path / "payload" / "b.kicad_pcb", tmp_path / "native" / "b.kicad_pcb"
    theirs.parent.mkdir()
    ours.parent.mkdir()
    old_asked, old_done = build_payload(netlist, theirs)
    new_asked, new_done = build_native(netlist, ours)

    def preview(doc, out):
        return {**doc["error"]["details"]["preview"], "out": "OUT" if doc else out}

    assert preview(old_asked, theirs) == preview(new_asked, ours)
    assert old_done["ok"] and new_done["ok"], (old_done, new_done)
    mine = ("board", "board_mm", "project_files", "pads_connected")
    old_data = {k: v for k, v in old_done["data"].items() if k not in mine}
    new_data = {k: v for k, v in new_done["data"].items() if k not in mine}
    assert differences(old_data, new_data) == []
    assert new_done["data"]["unmatched_nodes"] == [{"net": "MISSING", "node": "U2.9"}]
    for suffix in (".kicad_pro", ".kicad_prl"):
        made = ours.with_suffix(suffix)
        assert made.read_bytes() == theirs.with_suffix(suffix).read_bytes(), suffix
    assert new_done["data"]["project_files"] == {
        "created": [str(ours.with_suffix(".kicad_pro")), str(ours.with_suffix(".kicad_prl"))],
        "kept": [],
    }

    before, after = pcbnew_reads(theirs), pcbnew_reads(ours)
    expected_ids = {ref: fpid for ref, _, fpid in PARTS}
    expected_nets = {(ref, pin): net for net, pins in NETS.items() for ref, pin in pins}
    connected = 0
    for old_fp, actual_fp in zip(before["footprints"], after["footprints"], strict=True):
        ref = actual_fp["reference"]
        assert actual_fp["fpid"] == expected_ids[ref]
        old_fp["fpid"] = expected_ids[ref]
        for old_pad, actual_pad in zip(old_fp["pads"], actual_fp["pads"], strict=True):
            wanted = expected_nets.get((ref, actual_pad["number"]), "")
            assert actual_pad["net"] == wanted, (ref, actual_pad["number"], wanted)
            old_pad["net"] = wanted
            connected += bool(wanted)
    assert new_done["data"]["pads_connected"] == connected
    for key in ("footprints", "tracks", "zones", "copper_layers"):
        assert differences(before[key], after[key]) == [], key
    x1, y1, x2, y2 = after["edge_bbox"]
    assert x2 - x1 > 0 and y2 - y1 > 0
    assert new_done["data"]["board_mm"] == [round((x2 - x1 - 100_000) / 1e6, 2),
                                             round((y2 - y1 - 100_000) / 1e6, 2)]  # fmt: skip


def test_every_physical_pad_of_a_shared_number_is_connected(tmp_path):
    """SOT-223 tab/split pads are electrically one pin, including after save."""
    from kicad_cli.fileformat.sexpr import Document
    from kicad_cli.native.from_netlist import _wire

    doc = Document.parse(
        '(footprint "Fixture:SOT-223" (layer "F.Cu") '
        '(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu")) '
        '(pad "2" smd rect (at 2 0) (size 1 1) (layers "F.Cu")) '
        '(pad "2" smd rect (at 4 0) (size 2 2) (layers "F.Cu")))'
    )
    joined, missing = _wire(
        {"U2": doc.root}, [{"name": "+5V", "nodes": [{"ref": "U2", "pin": "2"}]}]
    )
    path = tmp_path / "physical-pads.kicad_mod"
    path.write_text(doc.dumps(), encoding="utf-8")
    saved = Document.load(path).root
    pads = saved.find_all("pad")
    assert missing == [] and joined == 2
    assert pads[0].find("net") is None
    assert [p.find("net").value(1) for p in pads[1:]] == ["+5V", "+5V"]


def test_requested_library_identity_survives_footprint_placement(tmp_path):
    from kicad_cli.native.from_netlist import _footprint, _Ids

    library = tmp_path / "SOT-223.kicad_mod"
    library.write_text('(footprint "SOT-223" (layer "F.Cu"))', encoding="utf-8")
    placed = _footprint(
        {"file": str(library), "ref": "U2", "x": 10, "y": 20, "footprint": "Fixture:SOT-223"},
        _Ids("identity-regression"),
    )
    assert placed.value(1) == "Fixture:SOT-223"


@pytest.mark.skipif(kicad_python() is None, reason="needs KiCad's footprint libraries")
def test_one_netlist_gives_one_board(tmp_path):
    netlist = tmp_path / "circuit.net"
    netlist.write_text(netlist_text(), encoding="utf-8")
    first, second = tmp_path / "a" / "b.kicad_pcb", tmp_path / "b" / "b.kicad_pcb"
    first.parent.mkdir()
    second.parent.mkdir()
    build_native(netlist, first)
    build_native(netlist, second)
    assert first.read_bytes() == second.read_bytes()


@pytest.mark.skipif(kicad_python() is None, reason="needs KiCad's footprint libraries")
def test_a_project_already_beside_the_board_is_left_as_it_is(tmp_path):
    """The schematic's project holds its net classes; a new board must not
    reset them to KiCad's defaults, as pcbnew's Save did."""
    netlist = tmp_path / "circuit.net"
    netlist.write_text(netlist_text(), encoding="utf-8")
    board = tmp_path / "b.kicad_pcb"
    project = tmp_path / "b.kicad_pro"
    mine = b'{"meta": {"filename": "b.kicad_pro", "version": 3}, "net_settings": {}}\n'
    project.write_bytes(mine)
    _, done = build_native(netlist, board)
    assert done["ok"], done
    assert project.read_bytes() == mine
    assert done["data"]["project_files"] == {
        "created": [str(tmp_path / "b.kicad_prl")],
        "kept": [str(project)],
    }
