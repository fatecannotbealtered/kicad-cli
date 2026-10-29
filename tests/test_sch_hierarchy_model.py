"""Bounded hierarchy discovery and precise tree authorization; no Native execution."""

from __future__ import annotations

from copy import deepcopy

import pytest

from kicad_cli.fileformat.circuit import Design
from kicad_cli.sch_circuit_guard import CircuitGuardError, verify
from kicad_cli.sch_objects import Catalog


def source(uid, body=""):
    return f'(kicad_sch (version 20250114) (uuid "{uid}") (paper "A4") {body})\n'


def sheet(uid, name, file, pins=""):
    return (
        f'(sheet (at 20 20) (size 20 15) (uuid "{uid}") '
        f'(property "Sheetname" "{name}" (at 20 18 0)) '
        f'(property "Sheetfile" "{file}" (at 20 37 0)) {pins})'
    )


def wire(uid="lead"):
    return f'(wire (pts (xy 10 10) (xy 20 10)) (stroke (width 0) (type default)) (uuid "{uid}"))'


def models(tmp_path, before, after, children):
    root = tmp_path / "root.kicad_sch"
    root.write_text(before, encoding="utf-8")
    for name, text in children.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    old = Design(root)
    new = Design(root, {root: after})
    return old, new, Catalog(old), Catalog(new)


def target(kind, uid, relative="root.kicad_sch"):
    return {"kind": kind, "source_relative": relative, "uuids": [uid]}


def test_new_nested_overlay_sources_need_not_exist_on_disk(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(source("r"), encoding="utf-8")
    child = tmp_path / "leaf.kicad_sch"
    grand = tmp_path / "tip.kicad_sch"
    design = Design(
        root,
        {
            root: source("r", sheet("one", "First", child.name)),
            child: source("c", sheet("two", "Second", grand.name)),
            grand: source("g", wire()),
        },
    )
    assert [item.path for item in design.instances] == ["/r", "/r/one", "/r/one/two"]
    assert not design.missing and not child.exists() and not grand.exists()


def test_repeated_source_is_not_a_cycle_but_ancestor_reference_is(tmp_path):
    root = tmp_path / "root.kicad_sch"
    child = tmp_path / "module.kicad_sch"
    sources = {
        root: source("r", sheet("a", "A", child.name) + sheet("b", "B", child.name)),
        child: source("c"),
    }
    assert len(Design(root, sources).instances) == 3
    sources[child] = source("c", sheet("back", "Back", root.name))
    with pytest.raises(ValueError, match="cyclic"):
        Design(root, sources)


def test_general_depth_and_instance_caps_have_distinct_diagnostics(tmp_path, monkeypatch):
    import kicad_cli.fileformat.circuit as circuit

    root = tmp_path / "root.kicad_sch"
    child = tmp_path / "module.kicad_sch"
    sources = {root: source("r", sheet("a", "A", child.name)), child: source("c")}
    monkeypatch.setattr(circuit, "MAX_HIERARCHY_DEPTH", 0)
    with pytest.raises(ValueError, match="nested sheet"):
        Design(root, sources)
    monkeypatch.setattr(circuit, "MAX_HIERARCHY_DEPTH", 32)
    monkeypatch.setattr(circuit, "MAX_HIERARCHY_INSTANCES", 1)
    with pytest.raises(ValueError, match="logical instances"):
        Design(root, sources)
    monkeypatch.setattr(circuit, "MAX_HIERARCHY_INSTANCES", 256)
    monkeypatch.setattr(circuit, "MAX_HIERARCHY_SOURCES", 1)
    with pytest.raises(ValueError, match="source files"):
        Design(root, sources)


@pytest.mark.parametrize("dimension,maximum", [("depth", 32), ("instances", 256), ("sources", 128)])
def test_public_resource_maximum_and_first_excess_are_general(tmp_path, dimension, maximum):
    root = tmp_path / "root.kicad_sch"

    def inputs(count):
        sources = {}
        if dimension == "depth":
            for index in range(count + 1):
                path = root if index == 0 else tmp_path / f"level-{index}.kicad_sch"
                body = (
                    sheet(f"edge-{index}", f"Depth{index}", f"level-{index + 1}.kicad_sch")
                    if index < count
                    else ""
                )
                sources[path] = source(f"source-{index}", body)
        elif dimension == "instances":
            sources[root] = source(
                "r",
                "".join(
                    sheet(f"edge-{i}", f"Instance{i}", "module.kicad_sch") for i in range(count - 1)
                ),
            )
            sources[tmp_path / "module.kicad_sch"] = source("module")
        else:
            sources[root] = source(
                "r",
                "".join(
                    sheet(f"edge-{i}", f"File{i}", f"file-{i}.kicad_sch") for i in range(count - 1)
                ),
            )
            sources.update(
                {tmp_path / f"file-{i}.kicad_sch": source(f"source-{i}") for i in range(count - 1)}
            )
        return sources

    accepted = Design(root, inputs(maximum))
    assert len(accepted.instances) == (maximum + 1 if dimension == "depth" else maximum)
    with pytest.raises(
        ValueError,
        match={
            "depth": "nested sheet",
            "instances": "logical instances",
            "sources": "source files",
        }[dimension],
    ):
        Design(root, inputs(maximum + 1))


def test_query_distinguishes_source_and_reused_interface_occurrences(tmp_path):
    pin = '(pin "IN" input (at 20 25 180) (effects (font (size 1.27 1.27))) (uuid "port"))'
    child = source(
        "child",
        '(hierarchical_label "IN" (shape input) (at 10 10 0) '
        '(effects (font (size 1.27 1.27))) (uuid "label"))',
    )
    second = sheet("b", "ModuleB", "module.kicad_sch", pin.replace('"port"', '"port-b"'))
    second = second.replace("(at 20 20)", "(at 60 20)").replace("(at 20 25 180)", "(at 60 25 180)")
    root = source("root", sheet("a", "ModuleA", "module.kicad_sch", pin) + second)
    before, _, catalog, _ = models(tmp_path, root, root, {"module.kicad_sch": child})
    query = catalog.query(limit=1)
    hierarchy = query["hierarchy"]
    assert query["has_more"] and not hierarchy["truncated"]
    assert hierarchy["interface_status"] == "complete"
    children = [row for row in hierarchy["instances"] if row["parent_instance"]]
    assert [row["instance_path"] for row in children] == ["/root/a", "/root/b"]
    assert all(row["source_instances"] == ["/root/a", "/root/b"] for row in children)
    assert all(row["interfaces"][0]["pairing_status"] == "paired" for row in children)
    assert children[0]["interfaces"][0]["parent_anchor"]["instance_path"] == "/root"
    assert (
        children[0]["interfaces"][0]["connection"]["connection_id"]
        != children[1]["interfaces"][0]["connection"]["connection_id"]
    )
    assert len([obj for obj in catalog.objects if obj.kind == "hierarchical_label"]) == 1
    assert len(before.instances) == 3


@pytest.mark.parametrize("case", ["missing", "direction", "duplicate_pin", "missing_pin"])
def test_interface_mismatch_remains_explicitly_incomplete(tmp_path, case):
    pin = '(pin "IN" input (at 20 25 180) (uuid "p"))'
    label = '(hierarchical_label "IN" (shape input) (at 10 10 0) (uuid "l"))'
    if case == "missing":
        label = ""
    elif case == "direction":
        label = label.replace("shape input", "shape output")
    elif case == "duplicate_pin":
        pin += pin.replace('"p"', '"p2"')
    else:
        pin = ""
    root = source("r", sheet("s", "Child", "child.kicad_sch", pin))
    _, _, catalog, _ = models(tmp_path, root, root, {"child.kicad_sch": source("c", label)})
    assert catalog.hierarchy["interface_status"] == "incomplete"
    assert catalog.hierarchy["interface_problems"]


def test_empty_new_page_authorized_with_exact_tree_and_source_inventory(tmp_path):
    root = tmp_path / "root.kicad_sch"
    root.write_text(source("r"), encoding="utf-8")
    old = Design(root)
    new = Design(
        root,
        {
            root: source("r", sheet("s", "Child", "child.kicad_sch")),
            tmp_path / "child.kicad_sch": source("c"),
        },
    )
    request = {
        "actions": [{"op": "create_sheet"}],
        "affected_instances": ["/r", "/r/s"],
        "expected": {"components": []},
    }
    metadata = {"new_instances": ["/r/s"], "new_sources": ["child.kicad_sch"]}
    result = verify(
        old,
        new,
        Catalog(old),
        Catalog(new),
        request,
        {},
        [],
        {0: target("sheet", "s")},
        hierarchy=metadata,
    )
    assert result["ok"], result
    metadata["new_instances"] = []
    with pytest.raises(CircuitGuardError, match="instance inventory"):
        verify(
            old,
            new,
            Catalog(old),
            Catalog(new),
            request,
            {},
            [],
            {0: target("sheet", "s")},
            hierarchy=metadata,
        )


def test_reused_existing_source_requires_new_component_expectation(tmp_path):
    first = sheet("one", "First", "module.kicad_sch")
    second = sheet("two", "Second", "module.kicad_sch")
    old, new, a, b = models(
        tmp_path,
        source("r", first),
        source("r", first + second),
        {"module.kicad_sch": source("c", wire())},
    )
    old_wire = next(obj for obj in a.objects if obj.kind == "wire")
    expected = {
        "members": [{"selector": old_wire.record["selector"], "instance_path": "/r/two"}],
        "explicit_names": [],
        "nc_members": [],
    }
    request = {
        "actions": [{"op": "reference_sheet"}],
        "affected_instances": ["/r", "/r/two"],
        "expected": {"components": [expected]},
    }
    metadata = {"new_instances": ["/r/two"], "new_sources": []}
    args = (old, new, a, b, request, {}, [], {0: target("sheet", "two")})
    assert verify(*args, hierarchy=metadata)["ok"]
    request["expected"]["components"] = []
    result = verify(*args, hierarchy=metadata)
    assert not result["ok"]
    assert "undeclared_new_or_changed_component" in {row["kind"] for row in result["differences"]}


def test_instance_annotations_only_append_declared_new_paths(tmp_path):
    library = (
        '(lib_symbols (symbol "T:P" (symbol "P_1_1" '
        '(pin passive line (at 0 0 0) (length 1) (name "P") (number "1")))))'
    )
    symbol = (
        '(symbol (lib_id "T:P") (at 10 10 0) (unit 1) (uuid "u") '
        '(property "Reference" "U1" (at 10 8 0)) (property "Value" "P" (at 10 12 0))'
    )
    original = symbol + '(instances (project "root" (path "/r/one" (reference "U1") (unit 1)))))'
    appended = (
        symbol + '(instances (project "root" (path "/r/one" (reference "U1") (unit 1)) '
        '(path "/r/two" (reference "U2") (unit 1)))))'
    )
    first = sheet("one", "First", "module.kicad_sch")
    second = sheet("two", "Second", "module.kicad_sch")
    old, _, a, _ = models(
        tmp_path,
        source("r", first),
        source("r", first + second),
        {"module.kicad_sch": source("c", library + original)},
    )
    new = Design(
        old.root_path,
        {
            old.root_path: source("r", first + second),
            tmp_path / "module.kicad_sch": source("c", library + appended),
        },
    )
    obj = next(item for item in a.objects if item.kind == "symbol")
    request = {
        "actions": [{"op": "reference_sheet"}],
        "affected_instances": ["/r", "/r/one", "/r/two"],
        "expected": {
            "components": [
                {
                    "members": [
                        {
                            "selector": obj.record["selector"],
                            "instance_path": "/r/two",
                            "pin_ordinal": 0,
                        }
                    ],
                    "explicit_names": [],
                    "nc_members": [],
                }
            ]
        },
    }
    metadata = {
        "new_instances": ["/r/two"],
        "new_sources": [],
        "symbol_instances": [
            {
                "source_relative": "module.kicad_sch",
                "uuid": "u",
                "instance_path": "/r/two",
                "reference": "U2",
                "unit": 1,
            }
        ],
    }
    args = (old, new, a, Catalog(new), request, {}, [], {0: target("sheet", "two")})
    assert verify(*args, hierarchy=metadata)["ok"]
    wrong = deepcopy(metadata)
    wrong["symbol_instances"][0]["reference"] = "U3"
    assert not verify(*args, hierarchy=wrong)["ok"]
    altered = Design(
        old.root_path,
        {
            old.root_path: source("r", first + second),
            tmp_path / "module.kicad_sch": source(
                "c", library + appended.replace("(at 10 10 0)", "(at 11 10 0)")
            ),
        },
    )
    assert not verify(
        old,
        altered,
        a,
        Catalog(altered),
        request,
        {},
        [],
        {0: target("sheet", "two")},
        hierarchy=metadata,
    )["ok"]
