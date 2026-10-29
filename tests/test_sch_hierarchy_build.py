"""Prospective in-memory circuit tasks for nested/reused sheet interfaces.

These are source-controlled preparation tests. They do not claim public EXE,
official KiCad, or formal engineering saves. Electrical intent is written before
calling Construction; no expected projection is inferred from its candidate.
"""

from __future__ import annotations

import hashlib

import pytest

from kicad_cli import kicad_env
from kicad_cli.fileformat.circuit import Design
from kicad_cli.sch_construct import ConstructError, Construction
from kicad_cli.sch_objects import Catalog

LIBRARY = (
    '(kicad_symbol_lib (version 20250114) (generator "test") '
    '(symbol "P" (property "Reference" "R" (at 0 0 0)) '
    '(property "Value" "P" (at 0 0 0)) '
    '(symbol "P_1_1" (pin passive line (at 0 0 0) (length 1) '
    '(name "P") (number "1")))))'
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("KICAD_CONFIG_HOME", str(tmp_path / "isolated-config"))
    monkeypatch.setattr(kicad_env, "find_kicad_root", lambda: None)
    root = tmp_path / "task.kicad_sch"
    root.write_bytes(
        b'(kicad_sch (version 20250114) (generator "test") '
        b'(uuid "root") (paper "A4") (lib_symbols))\n'
    )
    (tmp_path / "ports.kicad_sym").write_text(LIBRARY, encoding="utf-8")
    (tmp_path / "sym-lib-table").write_text(
        '(sym_lib_table (lib (name "T") (type "KiCad") '
        '(uri "${KIPRJMOD}/ports.kicad_sym") (options "") (descr "")))',
        encoding="utf-8",
    )
    return root


def request(root):
    catalog = Catalog(Design(root))
    return {
        "op": "circuit",
        "snapshot": {"root": catalog.root, "snapshot": catalog.snapshot},
        "affected_instances": ["/root"],
        "actions": [],
        "expected": {"components": []},
    }


def instance(name, parent="/root"):
    return {"new": name, "parent": parent}


def anchor(name, page="/root", **more):
    return {"new": name, "instance_path": page, **more}


def component(members, names=()):
    return {
        "members": members,
        "explicit_names": [{"kind": kind, "name": name} for kind, name in names],
        "nc_members": [],
    }


def frame(name, file, page="/root", *, display="Module", at=(40, 20), pins=True):
    return {
        "op": "create_sheet",
        "id": name,
        "page": page,
        "file": file,
        "name": display,
        "at_mm": list(at),
        "size_mm": [20, 20],
        "pins": [{"name": "IN", "shape": "input", "edge": "left", "offset_mm": 10}] if pins else [],
    }


def part(name, ref, page, xy):
    return {
        "op": "place_symbol",
        "id": name,
        "page": page,
        "symbol": "T:P",
        "unit": 1,
        "ref": ref,
        "at_mm": xy,
        "angle_deg": 0,
    }


def label(name, page, xy):
    return {
        "op": "create",
        "id": name,
        "page": page,
        "kind": "hierarchical_label",
        "text": "IN",
        "shape": "input",
        "at": xy,
        "angle_deg": 0,
    }


def lead(name, page, first, last):
    return {
        "op": "create",
        "id": name,
        "page": page,
        "kind": "wire",
        "points": [first, last],
    }


def nested_request(root):
    child, leaf = instance("module"), instance("cell", instance("module"))
    task = request(root)
    task["affected_instances"] += [child, leaf]
    task["actions"] = [
        frame("module", "filter.kicad_sch"),
        frame("cell", "core.kicad_sch", child, display="Cell"),
        part("input", "R71", "/root", [10, 30]),
        part("load", "R93", leaf, [30, 30]),
        label("middle", child, [10, 30]),
        label("tip", leaf, [10, 30]),
        lead(
            "parent_wire",
            "/root",
            {"pin": anchor("input", pin_ordinal=0)},
            {"sheet_pin": anchor("module", sheet_pin_ordinal=0)},
        ),
        lead(
            "middle_wire",
            child,
            {"object": {"new": "middle"}, "instance_path": child, "endpoint": 0},
            {"sheet_pin": anchor("cell", child, sheet_pin_ordinal=0)},
        ),
        lead(
            "leaf_wire",
            leaf,
            {"object": {"new": "tip"}, "instance_path": leaf, "endpoint": 0},
            {"pin": anchor("load", leaf, pin_ordinal=0)},
        ),
    ]
    task["expected"]["components"] = [
        component(
            [
                anchor("input", pin_ordinal=0),
                anchor("module", sheet_pin_ordinal=0),
                anchor("parent_wire"),
                anchor("middle", child),
                anchor("cell", child, sheet_pin_ordinal=0),
                anchor("middle_wire", child),
                anchor("tip", leaf),
                anchor("load", leaf, pin_ordinal=0),
                anchor("leaf_wire", leaf),
            ],
            [
                ("sheet_pin", "/Module/IN"),
                ("sheet_pin", "/Module/Cell/IN"),
                ("hierarchical_label", "/Module/IN"),
                ("hierarchical_label", "/Module/Cell/IN"),
            ],
        )
    ]
    return task


def prepare(root, task):
    report, sources = Construction(root, task).build()
    assert report["verified"]["expected_connections"], report["circuit"]["guard"]
    return report, sources


@pytest.mark.parametrize("copies", [2, 4])
def test_child_batch_alias_tracks_parent_sources_reused_later(project, copies):
    task = request(project)
    wrapper = instance("outer")
    task["actions"] = [
        frame("outer", "outer.kicad_sch", pins=False),
        frame("inner", "inner.kicad_sch", wrapper, pins=False),
    ]
    parents = [wrapper]
    for index in range(1, copies):
        name = f"replica{index}"
        row = frame(
            name, "outer.kicad_sch", display=f"Replica{index}", at=(40 + 30 * index, 50), pins=False
        )
        row["op"] = "reference_sheet"
        task["actions"].append(row)
        parents.append(instance(name))
    leaves = [instance("inner", p) for p in parents]
    task["affected_instances"] += parents + leaves
    action = part("resistor", "R1", leaves[0], [20, 30])
    del action["ref"]
    action["references"] = [{"instance": p, "ref": f"R{41 + i * 13}"} for i, p in enumerate(leaves)]
    task["actions"].append(action)
    task["expected"]["components"] = [
        component([anchor("resistor", p, pin_ordinal=0)]) for p in leaves
    ]
    report, sources = prepare(project, task)
    assert len(Design(project, sources).instances) == 1 + 2 * copies
    assert len(report["circuit"]["hierarchy"]["new_instances"]) == 2 * copies


def save_controlled_sources(sources):
    """Fixture installation only; production formal writes belong to runtime."""
    for path, text in sources.items():
        path.write_bytes(text.encode("utf-8"))


def test_real_parent_pin_child_label_device_path_is_prepared_before_any_write(project):
    before = project.read_bytes()
    task = nested_request(project)
    report, sources = prepare(project, task)
    design = Design(project, sources)
    assert len(design.instances) == 3
    assert len([pin for item in design.instances for pin in design.placed_pins(item)]) == 2
    catalog = Catalog(design)
    assert catalog.hierarchy["interface_status"] == "complete"
    assert len(catalog.connections) == 1
    assert catalog.connections[0]["pin_occurrences"] == 2
    assert project.read_bytes() == before
    assert not (project.parent / "filter.kicad_sch").exists()
    assert not (project.parent / "core.kicad_sch").exists()
    assert {
        item["source_relative"]: item["source"]["sha256"]
        for item in report["circuit"]["hierarchy_geometry"]
    } == {
        path.relative_to(project.parent).as_posix(): hashlib.sha256(text.encode()).hexdigest()
        for path, text in sources.items()
    }


def test_candidate_geometry_discloses_sheet_symbol_overlap_before_save(project):
    task = nested_request(project)
    task["actions"][0]["at_mm"] = [5, 20]
    report, _ = prepare(project, task)
    root = next(
        row
        for row in report["circuit"]["hierarchy_geometry"]
        if row["source_relative"] == project.name
    )
    assert "review_sheet_symbol_overlap" in {row["status"] for row in root["findings"]}
    assert root["geometry_check"] == "partial_conservative_schematic_bounds"
    assert root["status"] == "requires_visual_review"


def shared_request(root):
    first, second = instance("module"), instance("replica")
    task = request(root)
    task["affected_instances"] += [first, second]
    shared_part = part("device", "R1", first, [30, 30])
    shared_part.pop("ref")
    shared_part["references"] = [
        {"instance": first, "ref": "R11"},
        {"instance": second, "ref": "R22"},
    ]
    connection = lead(
        "link",
        first,
        {"object": {"new": "port"}, "instance_path": first, "endpoint": 0},
        {"pin": anchor("device", first, pin_ordinal=0)},
    )
    connection["contact_targets"] = [
        anchor("port", second),
        anchor("device", second, pin_ordinal=0),
    ]
    task["actions"] = [
        frame("module", "shared.kicad_sch"),
        {
            **frame("replica", "shared.kicad_sch", display="Replica", at=(80, 20)),
            "op": "reference_sheet",
        },
        shared_part,
        label("port", first, [10, 30]),
        connection,
    ]
    task["expected"]["components"] = [
        component(
            [
                anchor(sheet_id, sheet_pin_ordinal=0),
                anchor("device", page, pin_ordinal=0),
                anchor("port", page),
                anchor("link", page),
            ],
            [("sheet_pin", f"/{display}/IN"), ("hierarchical_label", f"/{display}/IN")],
        )
        for sheet_id, page, display in [
            ("module", first, "Module"),
            ("replica", second, "Replica"),
        ]
    ]
    return task


def test_same_batch_shared_source_has_explicit_instance_refs_and_contact_grants(project):
    task = shared_request(project)
    _, sources = prepare(project, task)
    catalog = Catalog(Design(project, sources))
    assert len(catalog.hierarchy["sources"]) == 2
    assert len(catalog.hierarchy["instances"]) == 3
    assert len(catalog.connections) == 2
    assert (
        catalog.query(ref="R11")["items"][0]["uuid"] == catalog.query(ref="R22")["items"][0]["uuid"]
    )
    assert len([obj for obj in catalog.objects if obj.kind == "symbol"]) == 1


def add_shared_interface_request(root):
    catalog = Catalog(Design(root))
    sheets = sorted(
        [obj for obj in catalog.objects if obj.kind == "sheet"], key=lambda obj: obj.node.start
    )
    first, second = ["/root/" + obj.record["uuid"] for obj in sheets]
    task = request(root)
    task["affected_instances"] = ["/root", first, second]
    shared = shared_request(root)
    placement = shared["actions"][2]
    placement["page"] = first
    placement["references"] = [
        {"instance": first, "ref": "R31"},
        {"instance": second, "ref": "R48"},
    ]
    port = label("port", first, [10, 30])
    wire = lead(
        "link",
        first,
        {"object": {"new": "port"}, "instance_path": first, "endpoint": 0},
        {"pin": anchor("device", first, pin_ordinal=0)},
    )
    wire["contact_targets"] = [anchor("port", second), anchor("device", second, pin_ordinal=0)]
    task["actions"] = [
        {
            "op": "add_sheet_pin",
            "id": ident,
            "target": obj.record["selector"],
            "pin": {"name": "IN", "shape": "input", "edge": "left", "offset_mm": 10},
        }
        for ident, obj in zip(["a_pin", "b_pin"], sheets, strict=True)
    ] + [placement, port, wire]
    task["expected"]["components"] = [
        component(
            [
                anchor(ident, sheet_pin_ordinal=0),
                anchor("device", page, pin_ordinal=0),
                anchor("port", page),
                anchor("link", page),
            ],
            [("sheet_pin", f"/{display}/IN"), ("hierarchical_label", f"/{display}/IN")],
        )
        for ident, page, display in [("a_pin", first, "Module"), ("b_pin", second, "Replica")]
    ]
    return task


def prepare_empty_shared(root):
    task = shared_request(root)
    task["actions"] = task["actions"][:2]
    for action in task["actions"]:
        action["pins"] = []
    task["expected"]["components"] = []
    _, sources = prepare(root, task)
    save_controlled_sources(sources)


def test_existing_shared_sheet_can_gain_paired_pins_and_real_connection(project):
    prepare_empty_shared(project)
    _, sources = prepare(project, add_shared_interface_request(project))
    catalog = Catalog(Design(project, sources))
    assert catalog.hierarchy["interface_status"] == "complete"
    assert len(catalog.connections) == 2
    assert {
        obj.record["instances"][0]["ref"] for obj in catalog.objects if obj.kind == "symbol"
    } <= {"R31", "R48"}
    assert all(
        row["interfaces"][0]["name"] == "IN"
        for row in catalog.hierarchy["instances"]
        if row["parent_instance"]
    )


def test_shared_new_interface_missing_one_parent_pair_refuses(project):
    prepare_empty_shared(project)
    task = add_shared_interface_request(project)
    del task["actions"][1]
    # Remove the now absent second-parent expected pin without changing the
    # retained child label intent; missing interface validation must still fire.
    task["expected"]["components"][1]["members"].pop(0)
    with pytest.raises(ConstructError, match="pair"):
        Construction(project, task).build()


def test_shared_source_contact_grant_must_cover_every_logical_instance(project):
    task = shared_request(project)
    task["actions"][-1].pop("contact_targets")
    report, _ = Construction(project, task).build()
    assert not report["verified"]["expected_connections"]
    assert "undeclared_geometric_contact_change" in {
        item["kind"] for item in report["circuit"]["guard"]["differences"]
    }


def interface_request(root):
    catalog = Catalog(Design(root))
    sheets = [obj for obj in catalog.objects if obj.kind == "sheet"]
    child = {obj.kind: obj for obj in catalog.objects if obj.source_relative == "shared.kicad_sch"}
    task = request(root)
    task["affected_instances"] = [row["instance_path"] for row in catalog.pages]
    task["actions"] = [
        {
            "op": "update_interface",
            "sheets": [{"target": obj.record["selector"], "pin_ordinal": 0} for obj in sheets],
            "labels": [child["hierarchical_label"].record["selector"]],
            "name": "SENSE",
            "shape": "input",
        }
    ]
    for sheet_obj in sheets:
        name = next(p["value"] for p in sheet_obj.record["fields"] if p["name"] == "Sheetname")
        page = next(row for row in catalog.pages if row["sheet"] == f"/{name}/")
        task["expected"]["components"].append(
            component(
                [
                    {
                        "selector": sheet_obj.record["selector"],
                        "instance_path": "/root",
                        "sheet_pin_ordinal": 0,
                    },
                    *[
                        {
                            "selector": child[kind].record["selector"],
                            "instance_path": page["instance_path"],
                            **({"pin_ordinal": 0} if kind == "symbol" else {}),
                        }
                        for kind in ["symbol", "wire", "hierarchical_label"]
                    ],
                ],
                [("sheet_pin", f"/{name}/SENSE"), ("hierarchical_label", f"/{name}/SENSE")],
            )
        )
    return task


def test_shared_interface_rename_updates_both_parents_and_preserves_components(project):
    _, first = prepare(project, shared_request(project))
    save_controlled_sources(first)
    before = {path: path.read_bytes() for path in first}
    _, sources = prepare(project, interface_request(project))
    catalog = Catalog(Design(project, sources))
    assert catalog.hierarchy["interface_status"] == "complete"
    assert {
        pair["name"] for row in catalog.hierarchy["instances"] for pair in row["interfaces"]
    } == {"SENSE"}
    assert len(catalog.connections) == 2
    assert all(path.read_bytes() == raw for path, raw in before.items())


def test_incomplete_shared_interface_source_scope_refuses_before_write(project):
    _, first = prepare(project, shared_request(project))
    save_controlled_sources(first)
    task = interface_request(project)
    task["actions"][0]["sheets"].pop()
    with pytest.raises(ConstructError, match="every parent source"):
        Construction(project, task).build()


@pytest.mark.parametrize("operation", ["rename", "type", "delete"])
def test_scalar_edit_cannot_leave_only_one_side_of_existing_interface_changed(project, operation):
    _, first = prepare(project, shared_request(project))
    save_controlled_sources(first)
    catalog = Catalog(Design(project))
    label_obj = next(obj for obj in catalog.objects if obj.kind == "hierarchical_label")
    task = request(project)
    task["affected_instances"] = [row["instance_path"] for row in catalog.pages]
    action = {
        "op": "delete" if operation == "delete" else "update",
        "target": label_obj.record["selector"],
    }
    if operation == "rename":
        action["text"] = "SENSE"
    elif operation == "type":
        action["shape"] = "output"
    task["actions"] = [action]
    before = {path: path.read_bytes() for path in first}
    with pytest.raises(ConstructError, match="pair"):
        Construction(project, task).build()
    assert all(path.read_bytes() == raw for path, raw in before.items())


def test_reopened_shared_parent_can_gain_nested_shared_definition(project):
    _, first = prepare(project, shared_request(project))
    save_controlled_sources(first)
    before = {path: path.read_bytes() for path in first}
    catalog = Catalog(Design(project))
    pages = [
        row["instance_path"]
        for row in catalog.pages
        if row["source_relative"] == "shared.kicad_sch"
    ]
    task = request(project)
    task["actions"] = [
        frame("new_tip", "nested.kicad_sch", pages[0], display="Auxiliary", at=(80, 60), pins=False)
    ]
    task["affected_instances"] = [*pages, *[instance("new_tip", page) for page in pages]]
    _, sources = prepare(project, task)
    after = Catalog(Design(project, sources))
    nested = next(
        row for row in after.hierarchy["sources"] if row["source_relative"] == "nested.kicad_sch"
    )
    assert len(nested["instances"]) == 2
    assert len(after.connections) == 2
    assert project not in sources
    assert project.read_bytes() == before[project]


def test_global_label_cannot_silently_merge_distinct_reused_interfaces(project):
    task = shared_request(project)
    first, second = instance("module"), instance("replica")
    task["actions"].append(
        {
            "op": "create",
            "kind": "global_label",
            "id": "global",
            "page": first,
            "text": "ALL_CHANNELS",
            "shape": "passive",
            "at": {"pin": anchor("device", first, pin_ordinal=0)},
            "contact_targets": [
                anchor("link", first),
                anchor("link", second),
                anchor("device", second, pin_ordinal=0),
            ],
        }
    )
    for expected, page in zip(task["expected"]["components"], [first, second], strict=True):
        expected["members"].append(anchor("global", page))
        expected["explicit_names"].append({"kind": "global_label", "name": "ALL_CHANNELS"})
    report, _ = Construction(project, task).build()
    assert not report["verified"]["expected_connections"]
    assert "expected_component_mismatch" in {
        item["kind"] for item in report["circuit"]["guard"]["differences"]
    }


def test_reopen_then_reference_populated_leaf_keeps_original_circuit(project):
    _, first = prepare(project, nested_request(project))
    save_controlled_sources(first)
    before = {path: path.read_bytes() for path in first}
    catalog = Catalog(Design(project))
    leaf = next(row for row in catalog.pages if row["source_relative"] == "core.kicad_sch")
    existing = {
        item.kind: item for item in catalog.objects if item.source_relative == "core.kicad_sch"
    }
    task = request(project)
    new = instance("reuse")
    task["affected_instances"] += [leaf["instance_path"], new]
    action = frame("reuse", "core.kicad_sch", display="Probe", at=(80, 20))
    action["op"] = "reference_sheet"
    action["annotations"] = [
        {"target": existing["symbol"].record["selector"], "instance": new, "ref": "R112"}
    ]
    task["actions"] = [action]
    task["expected"]["components"] = [
        component(
            [
                anchor("reuse", sheet_pin_ordinal=0),
                {
                    "selector": existing["symbol"].record["selector"],
                    "instance_path": new,
                    "pin_ordinal": 0,
                },
                {"selector": existing["wire"].record["selector"], "instance_path": new},
                {
                    "selector": existing["hierarchical_label"].record["selector"],
                    "instance_path": new,
                },
            ],
            [("sheet_pin", "/Probe/IN"), ("hierarchical_label", "/Probe/IN")],
        )
    ]
    _, second = prepare(project, task)
    design = Design(project, second)
    assert len(design.instances) == 4
    assert len(Catalog(design).connections) == 2
    assert all(path.read_bytes() == raw for path, raw in before.items())
    save_controlled_sources(second)
    reopened = Catalog(Design(project))
    assert len(reopened.query(ref="R112")["items"]) == 1
    assert len(reopened.query(ref="R93")["items"]) == 1
    assert (project.parent / "filter.kicad_sch").read_bytes() == before[
        project.parent / "filter.kicad_sch"
    ]


def test_standalone_existing_module_is_referenced_with_queried_exposed_objects(project):
    _, built = prepare(project, nested_request(project))
    module = project.parent / "template.kicad_sch"
    module.write_bytes(built[project.parent / "core.kicad_sch"].encode("utf-8"))
    original = module.read_bytes()
    catalog = Catalog(Design(module))
    objects = {obj.kind: obj for obj in catalog.objects}
    new = instance("existing")
    task = request(project)
    task["affected_instances"] += [new]
    action = frame("existing", module.name, display="Reusable")
    action["op"] = "reference_sheet"
    action["annotations"] = [
        {"target": objects["symbol"].record["selector"], "instance": new, "ref": "R215"}
    ]
    action["expose"] = [
        {"id": alias, "target": objects[kind].record["selector"]}
        for alias, kind in [("device", "symbol"), ("line", "wire"), ("port", "hierarchical_label")]
    ]
    task["actions"] = [action]
    task["expected"]["components"] = [
        component(
            [
                anchor("existing", sheet_pin_ordinal=0),
                anchor("device", new, pin_ordinal=0),
                anchor("line", new),
                anchor("port", new),
            ],
            [("sheet_pin", "/Reusable/IN"), ("hierarchical_label", "/Reusable/IN")],
        )
    ]
    _, sources = prepare(project, task)
    assert len(Design(project, sources).instances) == 2
    assert module.read_bytes() == original
    assert Catalog(Design(project, sources)).query(ref="R215")["count"] == 1


def test_unattached_nested_source_outside_project_returns_field_error(project, tmp_path):
    owned = tmp_path / "project"
    owned.mkdir()
    root = owned / "root.kicad_sch"
    root.write_bytes(project.read_bytes())
    outside = tmp_path / "external.kicad_sch"
    outside.write_bytes(project.read_bytes().replace(b'"root"', b'"external"'))
    module = owned / "module.kicad_sch"
    module.write_text(
        '(kicad_sch (version 20250114) (uuid "module") '
        '(sheet (at 20 20) (size 20 20) (uuid "outside") '
        '(property "Sheetname" "Outside" (at 20 18 0)) '
        '(property "Sheetfile" "../external.kicad_sch" (at 20 42 0))))',
        encoding="utf-8",
    )
    task = request(root)
    task["actions"] = [{**frame("module", module.name, pins=False), "op": "reference_sheet"}]
    with pytest.raises(ConstructError, match="escapes") as failed:
        Construction(root, task).build()
    assert failed.value.details["path"].endswith("/file")
    assert root.read_bytes() == project.read_bytes()


def test_duplicate_child_label_cannot_claim_complete_interface(project):
    task = nested_request(project)
    task["actions"].append(label("duplicate_tip", instance("cell", instance("module")), [50, 30]))
    with pytest.raises(ConstructError, match="pair"):
        Construction(project, task).build()


def test_existing_unrelated_duplicate_reference_is_preserved_not_global_ban(project):
    _, first = prepare(project, shared_request(project))
    save_controlled_sources(first)
    child = project.parent / "shared.kicad_sch"
    child.write_bytes(child.read_bytes().replace(b'(reference "R22")', b'(reference "R11")'))
    before = child.read_bytes()
    task = request(project)
    task["actions"] = [
        frame("unrelated", "new.kicad_sch", display="Unrelated", at=(120, 20), pins=False)
    ]
    task["affected_instances"] += [instance("unrelated")]
    _, sources = prepare(project, task)
    assert child not in sources and child.read_bytes() == before


def test_referencing_after_root_filename_change_preserves_original_project_wrapper(project):
    _, first = prepare(project, shared_request(project))
    save_controlled_sources(first)
    renamed = project.with_name("renamed.kicad_sch")
    renamed.write_bytes(project.read_bytes())
    catalog = Catalog(Design(renamed))
    child = {obj.kind: obj for obj in catalog.objects if obj.source_relative == "shared.kicad_sch"}
    new = instance("third")
    task = request(renamed)
    task["affected_instances"] = [*[row["instance_path"] for row in catalog.pages], new]
    action = {
        **frame("third", "shared.kicad_sch", display="Third", at=(120, 20)),
        "op": "reference_sheet",
    }
    action["annotations"] = [
        {"target": child["symbol"].record["selector"], "instance": new, "ref": "R103"}
    ]
    task["actions"] = [action]
    task["expected"]["components"] = [
        component(
            [
                anchor("third", sheet_pin_ordinal=0),
                {
                    "selector": child["symbol"].record["selector"],
                    "instance_path": new,
                    "pin_ordinal": 0,
                },
                {"selector": child["wire"].record["selector"], "instance_path": new},
                {"selector": child["hierarchical_label"].record["selector"], "instance_path": new},
            ],
            [("sheet_pin", "/Third/IN"), ("hierarchical_label", "/Third/IN")],
        )
    ]
    _, sources = prepare(renamed, task)
    assert Catalog(Design(renamed, sources)).query(ref="R103")["count"] == 1


def test_renamed_root_can_import_a_module_with_a_different_project_wrapper(project):
    _, first = prepare(project, shared_request(project))
    save_controlled_sources(first)
    renamed = project.with_name("renamed.kicad_sch")
    renamed.write_bytes(project.read_bytes())
    module = project.with_name("foreign.kicad_sch")
    module.write_bytes(
        first[project.parent / "shared.kicad_sch"].encode().replace(b'"task"', b'"foreign"')
    )
    catalog = Catalog(Design(module))
    objects = {obj.kind: obj for obj in catalog.objects}
    new = instance("imported")
    task = request(renamed)
    task["affected_instances"] += [new]
    task["actions"] = [
        {
            **frame("imported", module.name, display="Imported", at=(120, 20)),
            "op": "reference_sheet",
            "annotations": [
                {"target": objects["symbol"].record["selector"], "instance": new, "ref": "R800"}
            ],
            "expose": [
                {"id": alias, "target": objects[kind].record["selector"]}
                for alias, kind in [
                    ("part", "symbol"),
                    ("line", "wire"),
                    ("port", "hierarchical_label"),
                ]
            ],
        }
    ]
    task["expected"]["components"] = [
        component(
            [
                anchor("imported", sheet_pin_ordinal=0),
                anchor("part", new, pin_ordinal=0),
                anchor("line", new),
                anchor("port", new),
            ],
            [("sheet_pin", "/Imported/IN"), ("hierarchical_label", "/Imported/IN")],
        )
    ]
    _, sources = prepare(renamed, task)
    assert Catalog(Design(renamed, sources)).query(ref="R800")["count"] == 1


@pytest.mark.parametrize("case", ["missing_port", "wrong_type", "extra_port", "bad_pin"])
def test_interface_failures_are_rejected_before_formal_writes(project, case):
    task = nested_request(project)
    if case == "missing_port":
        task["actions"][0]["pins"] = []
    elif case == "wrong_type":
        task["actions"][0]["pins"][0]["shape"] = "output"
    elif case == "extra_port":
        task["actions"][0]["pins"].append(
            {"name": "EXTRA", "shape": "input", "edge": "right", "offset_mm": 10}
        )
    else:
        task["actions"][0]["pins"][0]["offset_mm"] = 0
    before = project.read_bytes()
    with pytest.raises(ConstructError):
        Construction(project, task).build()
    assert project.read_bytes() == before
    assert not (project.parent / "filter.kicad_sch").exists()


@pytest.mark.parametrize("case", ["escape", "root_collision", "duplicate_name", "bad_ref", "cycle"])
def test_hierarchy_batch_errors_preserve_all_input(project, case):
    task = request(project)
    child = frame("module", "filter.kicad_sch", pins=False)
    task["actions"] = [child]
    task["affected_instances"].append(instance("module"))
    if case == "escape":
        child["file"] = "../outside.kicad_sch"
    elif case == "root_collision":
        child["file"] = project.name
    elif case == "duplicate_name":
        task["actions"].append(frame("other", "second.kicad_sch", pins=False))
    elif case == "bad_ref":
        child["page"] = instance("not_yet_created")
    else:
        task["actions"].append(
            {
                **frame("loop", project.name, instance("module"), display="Loop", pins=False),
                "op": "reference_sheet",
            }
        )
    before = project.read_bytes()
    with pytest.raises(ConstructError):
        Construction(project, task).build()
    assert project.read_bytes() == before
    assert not (project.parent / "filter.kicad_sch").exists()


def test_omitted_new_instance_scope_cannot_be_inferred_from_candidate(project):
    task = nested_request(project)
    task["affected_instances"] = ["/root"]
    with pytest.raises(ConstructError, match="every occurrence"):
        Construction(project, task).build()


def test_global_effect_or_changed_pin_group_requires_full_prospective_intent(project):
    task = nested_request(project)
    task["expected"]["components"][0]["members"].pop()
    before = project.read_bytes()
    report, _ = Construction(project, task).build()
    assert not report["verified"]["expected_connections"]
    assert project.read_bytes() == before
    assert not (project.parent / "core.kicad_sch").exists()


def test_shared_interface_rename_requires_all_source_parents(project):
    _, sources = prepare(project, nested_request(project))
    save_controlled_sources(sources)
    catalog = Catalog(Design(project))
    sheet = next(
        obj
        for obj in catalog.objects
        if obj.kind == "sheet" and obj.source_relative == "filter.kicad_sch"
    )
    label_obj = next(
        obj
        for obj in catalog.objects
        if obj.kind == "hierarchical_label" and obj.source_relative == "core.kicad_sch"
    )
    task = request(project)
    task["actions"] = [
        {
            "op": "update_interface",
            "sheets": [{"target": sheet.record["selector"], "pin_ordinal": 0}],
            "labels": [],
            "name": "SENSE",
            "shape": "input",
        }
    ]
    task["affected_instances"] = [
        row["instance_path"]
        for row in catalog.pages
        if row["source_relative"] in {"filter.kicad_sch", "core.kicad_sch"}
    ]
    before = {path: path.read_bytes() for path in sources}
    with pytest.raises(ConstructError, match="all child"):
        Construction(project, task).build()
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert label_obj.record["text"] == "IN"
