"""Owned pure models for true three-page creation; no KiCad or subprocess.

These test physical memberships and instance paths rather than treating file
count, common local names, or a zero ERC count as electrical proof. The installed
symbols, official XML/ERC and complete actual pages are separate acceptance.
"""

import hashlib
import json
from pathlib import Path

import pytest
from test_sch_create_layout import NETS, topology

from kicad_cli.fileformat import netlist
from kicad_cli.fileformat.circuit import Design
from kicad_cli.fileformat.schematic import LibPin, LibSymbol
from kicad_cli.fileformat.sexpr import Document, List, number, quote, render, symbol
from kicad_cli.fileformat.symbols import Resolved
from kicad_cli.native import sch_create as base
from kicad_cli.native import sch_create_hierarchy as hierarchy
from kicad_cli.native.sch_create_feedback import inspect_created
from kicad_cli.sch_hierarchy_contract import hierarchy_plan


def _pin_node(pin):
    return List.new(
        "pin",
        symbol(pin.electrical),
        symbol(pin.shape),
        List.new(
            "at",
            number(pin.position[0] / base.NM),
            number(pin.position[1] / base.NM),
            str(pin.angle),
        ),
        List.new("length", number(pin.length / base.NM)),
        List.new("name", quote(pin.name), *base._effects()),
        List.new("number", quote(pin.number), *base._effects()),
    )


class OwnedLibraries:
    """Explicit model library with real parseable pin nodes, never installed."""

    def __init__(self, parts):
        self.parts = {p.lib.lib_id: p.lib for p in parts}
        for part in parts:
            for sub in part.lib.node.find_all("symbol"):
                unit = int(sub.value(1).rsplit("_", 2)[-2])
                for pin in part.lib.symbol.pins_of(unit, 1):
                    sub.append(_pin_node(pin))

    def resolve(self, name):
        if name in self.parts:
            return self.parts[name]
        if not name.startswith("power:"):
            raise AssertionError("Unexpected library lookup " + name)
        electrical = "power_out" if name == "power:PWR_FLAG" else "power_in"
        pin = LibPin("1", "~", electrical, "line", (0, 0), 90, 0, 1, 1)
        short = name.split(":", 1)[1]
        sub = List.new("symbol", quote(short + "_1_1"), _pin_node(pin))
        node = List.new("symbol", quote(name), List.new("power"), sub)
        lib = LibSymbol(name, {}, [pin], True, False, 1, node=node)
        return Resolved(name, node, lib)


def requirement():
    root = {"U1", "C1", "C2", "J5"}
    a = {"J1", "J2", "R10", "R11", "C10", "C11", "TP1", "TP2"}
    _, parts, on = topology()
    for part in parts:
        part.sheet_id = "root" if part.ref in root else "a" if part.ref in a else "b"
        part.in_bom = not part.ref.startswith("TP")
        if part.ref == "R10":
            part.value = "12k"
    spec = {
        "parts": [
            dict(
                ref=p.ref,
                symbol=p.lib.lib_id,
                value=p.value,
                footprint=p.footprint,
                sheet_id=p.sheet_id,
                in_bom=p.in_bom,
            )
            for p in parts
        ],
        "nets": [dict(name=name, connect=list(pins)) for name, pins in NETS.items()],
        "hierarchy": {
            "schema": "direct-two-child/v1",
            "namespace_uuid": "92aa6649-5374-44c9-afad-f2b773fc1b5a",
            "root": {"id": "root", "title": "Dual RC power and buffers"},
            "shared_power_nets": ["+3V3", "GND"],
            "children": [
                {
                    "id": page,
                    "name": "CHANNEL_" + channel,
                    "file": "channel_" + page + ".kicad_sch",
                    "title": "Channel " + channel,
                    "local_nets": {
                        channel + "_" + name: name for name in ("IN", "RC", "BUF", "OUT")
                    },
                    "ports": [
                        {"name": "RC", "direction": "output", "net": channel + "_RC"},
                        {"name": "BUF", "direction": "input", "net": channel + "_BUF"},
                    ],
                }
                for page, channel in (("a", "A"), ("b", "B"))
            ],
            "canonical_nets": {
                name: name if name in {"+3V3", "GND"} else "/CHANNEL_" + name[0] + "/" + name[2:]
                for name in NETS
            },
        },
    }
    return spec, parts, on


def created(tmp_path, monkeypatch):
    spec, parts, on = requirement()
    libs = OwnedLibraries(parts)
    monkeypatch.setattr(base, "_libraries", lambda: libs)
    monkeypatch.setattr(base, "plan", lambda unused: (parts, on, [], {"+3V3", "GND"}))
    result = hierarchy.create_hierarchy(spec, tmp_path / "circuit.json", tmp_path / "out")
    return spec, result


def test_all_three_documents_have_distinct_identity_and_true_instance_paths(tmp_path, monkeypatch):
    spec, result = created(tmp_path, monkeypatch)
    plan = hierarchy_plan(spec, "circuit")
    design = Design(result["written"]["schematic"])
    assert len(design.instances) == 3 and not design.missing
    assert {i.path for i in design.instances} == {
        page["instance_path"] for page in [plan["root"], *plan["children"]]
    }
    assert len({i.schematic.uuid for i in design.instances}) == 3
    assert {i.schematic.version for i in design.instances} == {20260101}
    assert all(i.schematic.uuid != i.sheet.uuid for i in design.instances if i.sheet)
    for instance in design.instances:
        for sym in instance.schematic.symbols:
            assert sym.instance(instance.path) is not None
            assert sym.node.find("instances").find("project").value(1) == "circuit"


def test_complete_42_pin_membership_and_same_local_labels_stay_isolated(tmp_path, monkeypatch):
    spec, result = created(tmp_path, monkeypatch)
    built = netlist.build(result["written"]["schematic"])
    assert len(built.components) == 20
    assert sum(len(n.nodes) for n in built.nets) == 42
    for declared in spec["nets"]:
        expected = set(declared["connect"])
        full = spec["hierarchy"]["canonical_nets"][declared["name"]]
        actual = next(n for n in built.nets if n.name == full)
        assert {p.ref + "." + p.pin for p in actual.nodes} == expected
    assert {n.name for n in built.nets} >= {"/CHANNEL_A/IN", "/CHANNEL_B/IN"}
    assert not hierarchy._check_hierarchy(
        {
            (pin.split(".")[0], pin.split(".")[1]): item["name"]
            for item in spec["nets"]
            for pin in item["connect"]
        },
        built,
        spec["hierarchy"]["canonical_nets"],
    )


def test_root_has_no_signal_label_overriding_real_child_ports(tmp_path, monkeypatch):
    _, result = created(tmp_path, monkeypatch)
    design = Design(result["written"]["schematic"])
    root = design.instances[0].schematic
    assert root.labels == []
    assert all({p.name for p in s.pins} == {"RC", "BUF"} for s in root.sheets)
    for child in design.instances[1:]:
        assert {
            label.text for label in child.schematic.labels if label.kind == "hierarchical_label"
        } == {
            "RC",
            "BUF",
        }
        assert child.schematic.wires


def test_each_sheet_pin_matches_one_child_label_with_stub_away_from_native_text_side(
    tmp_path, monkeypatch
):
    _, result = created(tmp_path, monkeypatch)
    design = Design(result["written"]["schematic"])
    for instance in design.instances[1:]:
        child = instance.schematic
        ports = [label for label in child.labels if label.kind == "hierarchical_label"]
        assert {pin.name for pin in instance.sheet.pins} == {label.text for label in ports}
        assert len(ports) == len(instance.sheet.pins)
        for label in ports:
            pin = next(pin for pin in instance.sheet.pins if pin.name == label.text)
            assert pin.electrical == label.shape
            assert label.angle == 0
            assert label.node.find("effects").find("justify").values() == ["left"]
            wires = [w for w in child.wires if label.position in (w.start, w.end)]
            assert len(wires) == 1
            wire = wires[0]
            other = wire.end if wire.start == label.position else wire.start
            assert other[0] < label.position[0] and other[1] == label.position[1]
            assert any(
                local.kind == "label" and local.text == label.text and local.position == other
                for local in child.labels
            )
        report = inspect_created(child.path)
        assert not any(
            row["status"] == "review_hierarchical_port_stub_text_direction"
            for row in report["findings"]
        )


@pytest.mark.parametrize("justification", ["left", "default-centered"])
def test_rightward_hierarchical_own_stub_reports_native_text_direction_risk(
    tmp_path, monkeypatch, justification
):
    _, result = created(tmp_path, monkeypatch)
    path = Path(result["written"]["children"][0])
    doc = Document.load(path)
    label = doc.root.find("hierarchical_label")
    anchor = label.find("at")
    # Reproduce the actual rejected create03 glyph/stub direction while
    # keeping the same port names, grid, electrical wire and local connection.
    old_x, y = anchor.atom(1), anchor.atom(2)
    label_name = label.value(1)
    local = next(
        node
        for node in doc.root.find_all("label")
        if node.value(1) == label_name and node.find("at").atom(2) == y
    )
    local_x = local.find("at").atom(1)
    anchor.set(1, local_x)
    local.find("at").set(1, old_x)
    if justification == "default-centered":
        label.find("effects").remove(label.find("effects").find("justify"))
    doc.save(path)
    report = inspect_created(path)
    finding = next(
        row
        for row in report["findings"]
        if row["status"] == "review_hierarchical_port_stub_text_direction"
    )
    assert finding["kind"] == "hierarchical_label"
    assert any(c["kind"] == "hierarchical_port_stub_into_text" for c in finding["conflicts"])
    assert report["status"] == "requires_visual_review"


def test_u1_all_units_supply_pins_and_bare_testpad_flags_are_present(tmp_path, monkeypatch):
    _, result = created(tmp_path, monkeypatch)
    design = Design(result["written"]["schematic"])
    root = design.instances[0].schematic
    assert {s.unit for s in root.symbols if s.properties["Reference"] == "U1"} == {1, 2, 3}
    built = netlist.build(design)
    membership = {n.name: {p.ref + "." + p.pin for p in n.nodes} for n in built.nets}
    assert "U1.4" in membership["GND"] and "U1.8" in membership["+3V3"]
    physical = [
        s
        for i in design.instances
        for s in i.schematic.symbols
        if not s.properties["Reference"].startswith("#")
    ]
    assert {s.properties["Reference"] for s in physical if not s.in_bom} == {
        "TP1",
        "TP2",
        "TP3",
        "TP4",
    }
    assert all(s.on_board and not s.dnp for s in physical)
    assert (
        sum(
            s.properties.get("Value") == "PWR_FLAG"
            for i in design.instances
            for s in i.schematic.symbols
        )
        == 2
    )
    assert all(
        not s.properties["Reference"].startswith("#FLG")
        for i in design.instances[1:]
        for s in i.schematic.symbols
    )


def test_each_page_feedback_binds_its_actual_bytes_and_sheet_objects(tmp_path, monkeypatch):
    _, result = created(tmp_path, monkeypatch)
    geometry = result["creation_geometry"]
    assert geometry["schema"] == "sch-create-geometry/v2"
    assert {p["source"]["file"] for p in geometry["pages"]} == {
        Path(p).name for p in [result["written"]["schematic"], *result["written"]["children"]]
    }
    for report in geometry["pages"]:
        raw = (tmp_path / "out" / report["source"]["file"]).read_bytes()
        assert report["source"]["sha256"] == hashlib.sha256(raw).hexdigest()
        assert report["status"] == "requires_visual_review"
    root = geometry["pages"][0]
    assert sum(row["kind"] == "sheet" for row in root["hierarchy_objects"]) == 2
    assert sum(row["kind"] == "sheet_pin" for row in root["hierarchy_objects"]) == 4


@pytest.mark.parametrize("grid_mils", [50, 25])
def test_electrical_anchors_preserve_connection_grid_despite_fractional_text_bounds(
    tmp_path, monkeypatch, grid_mils
):
    # KiCad 10 Schematic Setup / Schematic formatting documents Connection
    # width as the ERC endpoint grid (normally 50 mil). A point on its 25 mil
    # subdivision alone is not proof of satisfying a 50 mil connection grid.
    # https://docs.kicad.org/10.0/en/eeschema/eeschema.html#schematic-formatting
    _, result = created(tmp_path, monkeypatch)
    design = Design(result["written"]["schematic"])
    step = grid_mils * 25_400  # exact integer nm; no rounded display-mm check
    physical = []
    for instance in design.instances:
        schematic = instance.schematic
        for placed in design.placed_pins(instance):
            if not placed.power_symbol:
                physical.append((placed.reference, placed.pin.number))
            assert all(value % step == 0 for value in placed.position), (
                instance.path,
                placed.reference,
                placed.pin.number,
                placed.position,
                grid_mils,
            )
        for wire in schematic.wires:
            assert all(value % step == 0 for point in (wire.start, wire.end) for value in point)
        for label in schematic.labels:
            assert all(value % step == 0 for value in label.position), (label.text, label.position)
        for sheet in schematic.sheets:
            assert all(value % step == 0 for pin in sheet.pins for value in pin.position)
        for junction in schematic.root.find_all("junction"):
            at = junction.find("at")
            point = (round(float(at.atom(1)) * base.NM), round(float(at.atom(2)) * base.NM))
            assert all(value % step == 0 for value in point)
    assert len(physical) == 42


@pytest.mark.parametrize(
    "mutation",
    ["missing-child", "wrong-port", "missing-sheet-wire", "missing-power-unit", "same-local-merge"],
)
def test_wrong_hierarchy_or_missing_units_changes_frozen_pin_intent(
    tmp_path, monkeypatch, mutation
):
    spec, result = created(tmp_path, monkeypatch)
    root_path = Path(result["written"]["schematic"])
    if mutation == "missing-child":
        Path(result["written"]["children"][0]).unlink()
    else:
        path = root_path
        if mutation == "wrong-port":
            path = Path(result["written"]["children"][0])
        doc = Document.load(path)
        if mutation == "wrong-port":
            doc.root.find("hierarchical_label").set(1, quote("WRONG"))
        elif mutation == "missing-sheet-wire":
            wire = doc.root.find("wire")
            doc.root.remove(wire)
        elif mutation == "missing-power-unit":
            for sym in list(doc.root.find_all("symbol")):
                if sym.find("lib_id").value(1) == "Owned:U1" and sym.find("unit").atom(1) == "3":
                    doc.root.remove(sym)
        else:
            # Both symbols naming one shared child silently aliases instances;
            # it is not equivalent to two independent local signal namespaces.
            sheets = doc.root.find_all("sheet")
            props = sheets[1].find_all("property")
            next(p for p in props if p.value(1) == "Sheetname").set(2, quote("CHANNEL_A"))
        doc.save(path)
    built = netlist.build(root_path)
    on = {
        (pin.split(".")[0], pin.split(".")[1]): item["name"]
        for item in spec["nets"]
        for pin in item["connect"]
    }
    assert hierarchy._check_hierarchy(on, built, spec["hierarchy"]["canonical_nets"])


def test_same_request_is_byte_deterministic_and_namespace_keeps_symbol_identity_on_value_edit(
    tmp_path, monkeypatch
):
    spec, parts, on = requirement()
    libs = OwnedLibraries(parts)
    monkeypatch.setattr(base, "_libraries", lambda: libs)
    monkeypatch.setattr(base, "plan", lambda unused: (parts, on, [], {"+3V3", "GND"}))
    first = hierarchy.create_hierarchy(spec, tmp_path / "circuit.json", tmp_path / "a")
    hierarchy.create_hierarchy(spec, tmp_path / "circuit.json", tmp_path / "b")
    for name in hierarchy_plan(spec, "circuit")["output_names"][:-1]:
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
    original = Design(first["written"]["schematic"])
    next(p for p in parts if p.ref == "R10").value = "15k"
    changed = hierarchy.create_hierarchy(spec, tmp_path / "circuit.json", tmp_path / "c")
    edited = Design(changed["written"]["schematic"])
    assert {s.uuid for i in original.instances for s in i.schematic.symbols} == {
        s.uuid for i in edited.instances for s in i.schematic.symbols
    }


def test_cached_library_symbol_can_be_embedded_on_all_pages_without_stealing_ownership(
    tmp_path, monkeypatch
):
    spec, parts, on = requirement()
    libs = OwnedLibraries(parts)
    capacitor = next(p.lib for p in parts if p.ref == "C1")
    resistor = next(p.lib for p in parts if p.ref == "R10")
    original = {r.lib_id: render(r.node) for r in (capacitor, resistor)}
    # Match the real Libraries.resolve cache: all instances of the same
    # installed symbol return one Resolved resource tree, across all pages.
    for part in parts:
        if part.ref.startswith("C"):
            part.lib = capacitor
        elif part.ref.startswith("R"):
            part.lib = resistor
    monkeypatch.setattr(base, "_libraries", lambda: libs)
    monkeypatch.setattr(base, "plan", lambda unused: (parts, on, [], {"+3V3", "GND"}))
    result = hierarchy.create_hierarchy(spec, tmp_path / "circuit.json", tmp_path / "out")
    design = Design(result["written"]["schematic"])
    assert all(capacitor.lib_id in i.schematic.library for i in design.instances)
    assert all(r.node.parent is None for r in (capacitor, resistor))
    assert {r.lib_id: render(r.node) for r in (capacitor, resistor)} == original
    embedded = [i.schematic.root.find("lib_symbols").find_all("symbol") for i in design.instances]
    assert len({id(n) for page in embedded for n in page if n.value(1) == capacitor.lib_id}) == 3
    assert len({id(n) for page in embedded for n in page if n.value(1) == resistor.lib_id}) == 2


def test_existing_target_conflict_refuses_before_any_output_replacement(tmp_path, monkeypatch):
    spec, parts, on = requirement()
    libs = OwnedLibraries(parts)
    monkeypatch.setattr(base, "_libraries", lambda: libs)
    monkeypatch.setattr(base, "plan", lambda unused: (parts, on, [], {"+3V3", "GND"}))
    out = tmp_path / "out"
    out.mkdir()
    path = out / "channel_a.kicad_sch"
    path.write_bytes(b"owned preexisting target")
    with pytest.raises(SystemExit):
        hierarchy.create_hierarchy(spec, tmp_path / "circuit.json", out)
    assert path.read_bytes() == b"owned preexisting target"
    assert list(out.iterdir()) == [path]


def test_unreadable_hierarchical_font_is_not_silently_declared_measured(tmp_path, monkeypatch):
    _, result = created(tmp_path, monkeypatch)
    path = Path(result["written"]["children"][0])
    doc = Document.load(path)
    doc.root.find("hierarchical_label").find("effects").find("font").append(
        List.new("face", quote("Unknown"))
    )
    doc.save(path)
    report = inspect_created(path)
    assert any(item["kind"] == "hierarchical_label" for item in report["unmeasured_objects"])
    assert report["status"] == "requires_visual_review"


def test_plain_single_page_still_dispatches_to_legacy_generator_without_hierarchy(
    tmp_path, monkeypatch
):
    spec, parts, on = requirement()
    spec.pop("hierarchy")
    # A legacy request has no partition or assembly-flag additions. Removing
    # hierarchy alone leaves sheet_id and is correctly refused by the Schema.
    for item in spec["parts"]:
        item.pop("sheet_id")
        item.pop("in_bom")
    for part in parts:
        part.sheet_id = None
        part.in_bom = True
    libs = OwnedLibraries(parts)
    monkeypatch.setattr(base, "_libraries", lambda: libs)
    monkeypatch.setattr(base, "plan", lambda unused: (parts, on, [], {"+3V3", "GND"}))
    path = tmp_path / "single.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    result = base.create(str(path), str(tmp_path / "out"))
    assert result["creation_geometry"]["schema"] == "sch-create-geometry/v1"
    assert "children" not in result["written"]
    schematic = Design(result["written"]["schematic"]).instances[0].schematic
    assert not schematic.sheets
    assert all(s.in_bom and s.on_board and not s.dnp for s in schematic.symbols)
