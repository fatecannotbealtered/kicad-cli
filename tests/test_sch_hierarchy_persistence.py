"""Declared source inventory and controlled sequential hierarchy persistence.

These are source/runtime tests; official KiCad and final EXE acceptance are
separate. Failure shims never claim a real process-interruption result.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from kicad_cli import candidate_output, engineering_inputs, hierarchy_publication
from kicad_cli._driver_sdk.models import DriverError
from kicad_cli.engineering_driver_adapter import EngineeringAdapter
from kicad_cli.sch_hierarchy_inputs import capture, directory_rows, reference_catalogs, source_path
from kicad_cli.sync_inputs import Drift, InputError

EMPTY = (
    '(kicad_sch (version 20250114) (generator "test") '
    '(uuid "root-id") (paper "A4") (lib_symbols))\n'
)


def schematic(path, child=None):
    raw = EMPTY
    if child:
        raw = raw[:-2] + (
            f' (sheet (at 20 20) (size 30 20) (uuid "sheet-id") '
            f'(property "Sheetname" "CHILD") (property "Sheetfile" "{child}")))\n'
        )
    path.write_text(raw, encoding="utf-8")
    return path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(tmp_path, actions):
    root = schematic(tmp_path / "root.kicad_sch")
    inputs = engineering_inputs.Inputs()
    inputs.add(root, required=True)
    capture(inputs, root, actions)
    return root, inputs


@pytest.mark.parametrize(
    "file",
    [
        "../x.kicad_sch",
        "/x.kicad_sch",
        "x\\z.kicad_sch",
        "C:x.kicad_sch",
        "x//y.kicad_sch",
        "CON.kicad_sch",
        "x.kicad_sch.",
        "x.net",
    ],
)
def test_new_source_paths_refuse_ambiguous_or_escaping_inputs(tmp_path, file):
    with pytest.raises((InputError, OSError)):
        source_path(tmp_path / "root.kicad_sch", file)
    assert not list(tmp_path.iterdir())


def test_new_source_missing_parent_is_not_created(tmp_path):
    with pytest.raises(FileNotFoundError):
        source_path(tmp_path / "root.kicad_sch", "missing/child.kicad_sch")
    assert not (tmp_path / "missing").exists()


def test_new_source_absence_and_same_byte_original_replacement_are_bound(tmp_path):
    root, inputs = inventory(tmp_path, [{"op": "create_sheet", "file": "child.kicad_sch"}])
    inputs.validate()
    child = tmp_path / "child.kicad_sch"
    child.write_text(EMPTY, encoding="utf-8")
    with pytest.raises(Drift):
        inputs.validate()
    child.unlink()
    replacement = tmp_path / "replacement"
    replacement.write_bytes(root.read_bytes())
    os.replace(replacement, root)
    with pytest.raises(Drift):
        inputs.validate()


def test_existing_reference_descendants_are_captured_before_candidate(tmp_path):
    schematic(tmp_path / "leaf.kicad_sch")
    schematic(tmp_path / "module.kicad_sch", "leaf.kicad_sch")
    _, inputs = inventory(tmp_path, [{"op": "reference_sheet", "file": "module.kicad_sch"}])
    assert {path.name for path in inputs.files if path.suffix == ".kicad_sch"} == {
        "root.kicad_sch",
        "module.kicad_sch",
        "leaf.kicad_sch",
    }
    (tmp_path / "leaf.kicad_sch").write_bytes(b"changed")
    with pytest.raises(Drift):
        inputs.validate()


@pytest.mark.parametrize("bound", ["depth", "sources"])
def test_import_inventory_bounds_refuse_before_recursive_resource_exhaustion(
    tmp_path, monkeypatch, bound
):
    from kicad_cli import sch_hierarchy_inputs

    schematic(tmp_path / "third.kicad_sch")
    schematic(tmp_path / "second.kicad_sch", "third.kicad_sch")
    schematic(tmp_path / "first.kicad_sch", "second.kicad_sch")
    if bound == "depth":
        monkeypatch.setattr(sch_hierarchy_inputs, "MAX_HIERARCHY_DEPTH", 1)
    else:
        monkeypatch.setattr(sch_hierarchy_inputs, "MAX_HIERARCHY_SOURCES", 2)
    with pytest.raises(InputError, match="exceeds"):
        inventory(tmp_path, [{"op": "reference_sheet", "file": "first.kicad_sch"}])


@pytest.mark.parametrize("bound", ["depth", "sources"])
def test_initial_saved_observation_has_the_same_early_hierarchy_bounds(
    tmp_path, monkeypatch, bound
):
    from kicad_cli import read_inputs

    root = schematic(tmp_path / "root.kicad_sch", "child.kicad_sch")
    child = schematic(tmp_path / "child.kicad_sch")
    if bound == "depth":
        monkeypatch.setattr(read_inputs, "MAX_HIERARCHY_DEPTH", 1)
    else:
        monkeypatch.setattr(read_inputs, "MAX_HIERARCHY_SOURCES", 2)
    # The exact finite boundary remains readable.
    assert len(read_inputs.capture(schematic=root).design.instances) == 2
    schematic(child, "grandchild.kicad_sch")
    schematic(tmp_path / "grandchild.kicad_sch")
    with pytest.raises(InputError, match="exceeds"):
        read_inputs.capture(schematic=root)


def test_reference_to_earlier_new_source_is_allowed_but_forward_reference_is_not(tmp_path):
    create = {"op": "create_sheet", "file": "new.kicad_sch"}
    reuse = {"op": "reference_sheet", "file": "new.kicad_sch"}
    _, inputs = inventory(tmp_path, [create, reuse])
    assert inputs.files[tmp_path / "new.kicad_sch"] is None
    with pytest.raises(InputError, match="follow"):
        capture(engineering_inputs.Inputs(), tmp_path / "root.kicad_sch", [reuse, create])


def test_only_explicit_import_catalogs_accept_exact_standalone_selectors(tmp_path):
    from kicad_cli.fileformat.circuit import Design
    from kicad_cli.sch_objects import Catalog, CatalogError

    module = schematic(tmp_path / "module.kicad_sch")
    module.write_text(
        module.read_text().replace("root-id", "module-id")[:-2]
        + " (wire (pts (xy 20 20) (xy 30 20)) (stroke (width 0) (type default)) "
        '(uuid "wire-id")))\n',
        encoding="utf-8",
    )
    unrelated = schematic(tmp_path / "unrelated.kicad_sch")
    standalone = Catalog(Design(module))
    selection = standalone.query()["items"][0]["selector"]
    actions = [{"op": "reference_sheet", "file": "module.kicad_sch"}]
    root, inputs = inventory(tmp_path, actions)
    catalogs = reference_catalogs(inputs, root, actions)
    assert set(catalogs) == {module.as_uri()}
    assert unrelated.as_uri() not in catalogs
    assert catalogs[module.as_uri()].resolve(selection).kind == "wire"
    stale = dict(selection, snapshot="outdated")
    with pytest.raises(CatalogError):
        catalogs[module.as_uri()].resolve(stale)
    module.with_suffix(".kicad_pro").write_text("{}", encoding="utf-8")
    with pytest.raises(Drift):
        inputs.validate()


@pytest.mark.parametrize("defect", ["duplicate", "existing", "cycle"])
def test_conflicting_new_files_and_cyclic_references_are_rejected(tmp_path, defect):
    actions = [{"op": "create_sheet", "file": "new.kicad_sch"}]
    if defect == "duplicate":
        actions.append({"op": "create_sheet", "file": "NEW.kicad_sch"})
    elif defect == "existing":
        schematic(tmp_path / "new.kicad_sch")
    else:
        schematic(tmp_path / "new.kicad_sch", "new.kicad_sch")
        actions = [{"op": "reference_sheet", "file": "new.kicad_sch"}]
    with pytest.raises((InputError, FileExistsError)):
        inventory(tmp_path, actions)


def test_parent_directory_replacement_invalidates_absent_target(tmp_path):
    parent = tmp_path / "owned"
    parent.mkdir()
    root, inputs = inventory(parent, [{"op": "create_sheet", "file": "new.kicad_sch"}])
    parent.rename(tmp_path / "preserved")
    parent.mkdir()
    root.write_text(EMPTY, encoding="utf-8")
    with pytest.raises(Drift, match="directory"):
        inputs.validate()
    assert not (parent / "new.kicad_sch").exists()


@pytest.mark.skipif(os.name != "nt", reason="actual Windows junction")
def test_junction_parent_is_rejected_before_new_source_capture(tmp_path):
    import _winapi

    outside = tmp_path / "separate-owned-target"
    outside.mkdir()
    linked = tmp_path / "junction"
    _winapi.CreateJunction(str(outside), str(linked))
    with pytest.raises(InputError):
        source_path(tmp_path / "root.kicad_sch", "junction/new.kicad_sch")
    assert not (outside / "new.kicad_sch").exists()


def test_existing_hardlinked_source_is_not_treated_as_a_distinct_module(tmp_path):
    original = schematic(tmp_path / "original.kicad_sch")
    alias = tmp_path / "alias.kicad_sch"
    alias.hardlink_to(original)
    with pytest.raises(OSError, match="single regular file"):
        inventory(tmp_path, [{"op": "reference_sheet", "file": "alias.kicad_sch"}])
    assert alias.read_bytes() == original.read_bytes()


def test_missing_platform_guarantee_refuses_before_any_transaction_sidecar(tmp_path, monkeypatch):
    root = schematic(tmp_path / "root.kicad_sch")
    target = tmp_path / "new.kicad_sch"
    candidate = schematic(tmp_path / "candidate.kicad_sch")
    before = root.read_bytes()

    def unavailable(*args):
        raise candidate_output.OutputError("controlled unavailable backend", "unsupported_platform")

    monkeypatch.setattr(hierarchy_publication, "Directory", unavailable)
    adapter = EngineeringAdapter(None)
    monkeypatch.setattr(adapter, "require_current_scope", lambda *args: None)
    monkeypatch.setattr(adapter, "validate_plan", lambda *args: None)
    plan = {
        "extensions": {
            "hierarchy_incremental": True,
            "files": [
                {
                    "path": str(target),
                    "baseline": "absent",
                    "candidate": str(candidate),
                    "intended": digest(candidate),
                }
            ],
            "output_directories": directory_rows(tmp_path),
        }
    }
    with pytest.raises(DriverError) as failed:
        adapter.apply(SimpleNamespace(), plan)
    assert failed.value.code == "E_CONFIG"
    assert root.read_bytes() == before and not target.exists()
    assert not list(tmp_path.glob("*.kicad-cli.*"))


def test_changed_children_are_ordered_before_changed_parents(tmp_path):
    leaf = schematic(tmp_path / "leaf.kicad_sch")
    child = schematic(tmp_path / "child.kicad_sch", "leaf.kicad_sch")
    root = schematic(tmp_path / "root.kicad_sch", "child.kicad_sch")
    assert hierarchy_publication.save_order([root, child, leaf]) == [leaf, child, root]


@pytest.mark.skipif(os.name != "nt", reason="real Windows NTFS handle backend")
def test_new_source_publish_is_no_clobber_even_after_initial_absence(tmp_path, monkeypatch):
    candidate = schematic(tmp_path / "candidate.kicad_sch")
    target = tmp_path / "new.kicad_sch"
    row = {
        "path": str(target),
        "candidate": str(candidate),
        "intended": digest(candidate),
        "baseline": "absent",
    }
    write = candidate_output.File.write

    def compete(file, raw):
        write(file, raw)
        target.write_bytes(b"external writer")

    monkeypatch.setattr(candidate_output.File, "write", compete)
    plan = {
        "extensions": {
            "hierarchy_incremental": True,
            "files": [row],
            "output_directories": directory_rows(tmp_path),
        }
    }
    with hierarchy_publication.reserve(plan) as parents, pytest.raises(OSError):
        hierarchy_publication.publish_new(row, "op-controlled", parents)
    assert target.read_bytes() == b"external writer"
    assert (
        target.with_name(target.name + ".op-controlled.pending").read_bytes()
        == candidate.read_bytes()
    )


@pytest.mark.skipif(os.name != "nt", reason="real Windows NTFS handle backend")
def test_partial_pending_source_is_preserved_not_deleted(tmp_path, monkeypatch):
    candidate = schematic(tmp_path / "candidate.kicad_sch")
    target = tmp_path / "new.kicad_sch"
    row = {
        "path": str(target),
        "candidate": str(candidate),
        "intended": digest(candidate),
        "baseline": "absent",
    }
    write = candidate_output.File.write

    def interrupt(file, raw):
        write(file, raw[:17])
        raise OSError("controlled write failure")

    monkeypatch.setattr(candidate_output.File, "write", interrupt)
    plan = {
        "extensions": {
            "hierarchy_incremental": True,
            "files": [row],
            "output_directories": directory_rows(tmp_path),
        }
    }
    with hierarchy_publication.reserve(plan) as parents, pytest.raises(OSError, match="controlled"):
        hierarchy_publication.publish_new(row, "op-controlled", parents)
    assert not target.exists()
    assert (
        target.with_name(target.name + ".op-controlled.pending").read_bytes()
        == candidate.read_bytes()[:17]
    )


@pytest.mark.skipif(os.name != "nt", reason="real Windows NTFS handle backend")
def test_failure_after_child_save_preserves_actual_range_and_original_journal(
    tmp_path, monkeypatch
):
    root = schematic(tmp_path / "root.kicad_sch")
    baseline = root.read_bytes()
    staged = tmp_path / "staged"
    staged.mkdir()
    new_child = schematic(staged / "child.kicad_sch")
    new_root = schematic(staged / "root.kicad_sch", "child.kicad_sch")
    child = tmp_path / "child.kicad_sch"
    rows = [
        {
            "path": str(child),
            "candidate": str(new_child),
            "baseline": "absent",
            "intended": digest(new_child),
        },
        {
            "path": str(root),
            "candidate": str(new_root),
            "baseline": digest(root),
            "intended": digest(new_root),
        },
    ]
    state = tmp_path / "state"
    state.mkdir()
    adapter = EngineeringAdapter(None)
    adapter.state_dir = state
    adapter.secret_provider = lambda: b"controlled-runtime-key"
    monkeypatch.setattr(adapter, "require_current_scope", lambda *args: None)
    monkeypatch.setattr(adapter, "validate_plan", lambda *args: None)
    monkeypatch.setattr(adapter, "_guard_documents", lambda *args: None)
    replace = os.replace

    def fail_parent(source, destination):
        if Path(destination) == root and str(source).endswith(".pending"):
            raise OSError("controlled parent-save failure")
        return replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_parent)
    operation = "op-controlled-hierarchy"
    plan = {
        "target": {},
        "authorization_scope_id": "scope-controlled",
        "effects": ["change", "persist"],
        "base_snapshot": {},
        "changes": [],
        "extensions": {
            "primary": str(root),
            "hierarchy_incremental": True,
            "files": rows,
            "output_directories": directory_rows(tmp_path),
            "expected": [],
            "trial": {"result": {}, "finding_comparison": None},
        },
    }
    call = SimpleNamespace(operation_id=operation, capability_id="controlled", plan_id="controlled")
    with pytest.raises(DriverError) as failed:
        adapter.apply(call, plan)
    receipt = failed.value.receipt
    assert failed.value.code == "E_UNKNOWN"
    assert receipt["outcome"]["change"] == "partial"
    assert receipt["outcome"]["persistence"] == "partial"
    assert receipt["extensions"]["saved_documents"] == [str(child)]
    assert child.read_bytes() == new_child.read_bytes()
    assert root.read_bytes() == baseline
    assert root.with_name(root.name + ".kicad-cli.journal").exists()
    assert receipt["extensions"]["pending_documents"] == [
        str(root.with_name(root.name + "." + operation + ".pending"))
    ]
