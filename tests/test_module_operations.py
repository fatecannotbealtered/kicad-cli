"""Shared module boundaries; mocked native findings are separate from real trials."""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from driver_test_utils import grant

from kicad_cli import library_build, module_project, pcb_patch
from kicad_cli.module_contract import PATCH_SPEC

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/library/sn74lvc1g14"


def cli(*args):
    proc = subprocess.run(
        [sys.executable, "-B", str(ROOT / "entry.py"), *map(str, args), "--compact"],
        capture_output=True,
        timeout=60,
        cwd=ROOT,
    )
    return proc.returncode, json.loads(proc.stdout)


def test_public_module_discovery_is_actual():
    code, value = cli("modules")
    assert code == 0 and value["ok"]
    from kicad_cli.module_contract import MODULES

    assert value["data"]["observation"]["modules"] == MODULES
    code, value = cli("reference", "--command", "board route patch")
    assert code == 0 and value["data"]["command"]["path"] == "board route patch"
    assert (
        value["data"]["json_schemas"]["urn:kicad-cli:board-route-patch:1"]["$defs"]["spec_file"]
        == PATCH_SPEC
    )


def test_read_context_refuses_missing_project(tmp_path):
    board = tmp_path / "a.kicad_pcb"
    board.write_text("(kicad_pcb (version 20260128))")
    code, value = cli("project", "context", "--board", board)
    assert code == 2 and "Matching .kicad_pro" in value["error"]["details"]["reason"]
    assert not board.with_suffix(".kicad_pro").exists()


def test_registered_local_footprints_and_traversal_refusal(tmp_path):
    from kicad_cli.boardgen import resolve_footprint

    folder = tmp_path / "local.pretty"
    folder.mkdir()
    (folder / "A.kicad_mod").write_text('(footprint "A")')
    (tmp_path / "fp-lib-table").write_text(
        '(fp_lib_table (lib (name "Alias") (type "KiCad")'
        '(uri "${KIPRJMOD}/local.pretty") (options "") (descr "")))'
    )
    assert resolve_footprint("Alias:A", [], tmp_path) == folder / "A.kicad_mod"
    assert resolve_footprint("Alias:../A", [], tmp_path) is None


def test_candidate_context_preserves_parent_relative_project_libraries(tmp_path):
    main = tmp_path / "cad/main"
    main.mkdir(parents=True)
    library = tmp_path / "cad/schematic/local.pretty"
    library.mkdir(parents=True)
    (library / "A.kicad_mod").write_text('(footprint "A")')
    board = main / "p.kicad_pcb"
    board.write_text(
        '(kicad_pcb (version 20260128) (layers (0 "F.Cu" signal)) '
        '(footprint "Alias:A" (layer "F.Cu") (at 0 0) '
        '(property "Reference" "U1" (at 0 0) (layer "F.SilkS"))))'
    )
    board.with_suffix(".kicad_pro").write_text('{"board":{}}')
    (main / "fp-lib-table").write_text(
        '(fp_lib_table (lib (name "Alias") (type "KiCad") '
        '(uri "${KIPRJMOD}/../schematic/local.pretty") '
        '(options "") (descr "")))'
    )
    capture = module_project.capture_board_context(board)
    assert capture.stage_root == tmp_path / "cad"
    assert library / "A.kicad_mod" in capture.files


def library_case(tmp_path):
    spec = json.loads((EXAMPLE / "library.json").read_bytes())
    paths = library_build.output_paths(tmp_path, spec)
    return spec, paths, {p: None for p in paths}


def test_library_builds_real_parseable_assets_and_preserves_others(tmp_path):
    from kicad_cli.fileformat.sexpr import Document

    spec, paths, originals = library_case(tmp_path)
    originals[paths[0]] = b'(kicad_symbol_lib (version 20250114) (symbol "Keep"))'
    originals[paths[2]] = (
        b'(sym_lib_table (version 7) (lib (name "Keep") (type "KiCad") '
        b'(uri "keep.kicad_sym") (options "") (descr "")))'
    )
    spec["baseline"][paths[0].name] = module_project.sha(originals[paths[0]])
    spec["baseline"][paths[2].name] = module_project.sha(originals[paths[2]])
    values, result = library_build.build(spec, tmp_path, originals)
    symbols = Document.parse(values[paths[0]].decode()).root.find_all("symbol")
    assert [s.value(1) for s in symbols] == ["Keep", "SN74LVC1G14DBV"]
    pads = Document.parse(values[paths[1]].decode()).root.find_all("pad")
    assert sorted(p.value(1) for p in pads) == ["1", "2", "3", "4", "5"]
    assert {p.find("size").values()[0] for p in pads} == {"1.1"}
    assert [
        n.find("name").value(1)
        for n in Document.parse(values[paths[2]].decode()).root.find_all("lib")
    ] == ["Keep", "LocalLogic"]
    assert result["mapping"] == spec["mapping"]
    with pytest.raises(ValueError, match="baseline"):
        library_build.build(spec, tmp_path, values)


@pytest.mark.parametrize("damage", ["mapping", "duplicate", "unit"])
def test_invalid_library_definitions_refuse(tmp_path, damage):
    spec, _, _ = library_case(tmp_path)
    if damage == "mapping":
        spec["mapping"]["2"] = "4"
    elif damage == "duplicate":
        spec["footprint"]["pads"][0]["number"] = "2"
    else:
        spec["footprint"]["pads"][0]["size_mm"][0] = -1
    with pytest.raises(ValueError):
        library_build.validate_spec(spec)


def native_result(findings=()):
    return {
        "report": {
            "violations": list(findings),
            "unconnected_items": [],
            "schematic_parity": [],
            "ignored_checks": [],
        },
        "project_exclusions": [],
        "scope": "mock native report",
    }


def route_case(tmp_path):
    path = tmp_path / "b.kicad_pcb"
    text = (
        '(kicad_pcb (version 20260128) (layers (0 "F.Cu" signal) (2 "B.Cu" signal)) '
        "(setup (pad_to_mask_clearance 0))"
    )
    for identity, a, b, net in [
        ("a", (0, 0), (0, 3), "SIG"),
        ("b", (0, 3), (4, 3), "SIG"),
        ("c", (4, 3), (4, 0), "SIG"),
        ("keep", (8, 0), (8, 3), "OTHER"),
    ]:
        text += (
            f"(segment (start {a[0]} {a[1]}) (end {b[0]} {b[1]}) "
            f'(width 0.2) (layer "F.Cu") (net "{net}") (uuid "{identity}"))'
        )
    path.write_text(text + ")")
    path.with_suffix(".kicad_pro").write_text('{"board":{}}')
    spec = {
        "baseline": module_project.strict_context(path),
        "net": "SIG",
        "remove_ids": ["a", "b", "c"],
        "allow_locked_ids": [],
        "allowed_layers": ["F.Cu"],
        "bounds_nm": [-200000, -200000, 5000000, 4000000],
        "endpoints": [
            {"object_uuid": "a", "terminal": "start", "at_nm": [0, 0], "layer": "F.Cu"},
            {"object_uuid": "c", "terminal": "end", "at_nm": [4000000, 0], "layer": "F.Cu"},
        ],
        "segments": [
            {"start_nm": [0, 0], "end_nm": [4000000, 0], "layer": "F.Cu", "width_nm": 200000}
        ],
        "minimum_improvement_mm": 1,
    }
    return path, spec


def test_route_patch_preserves_non_target_and_improves_explicit_endpoints(tmp_path, monkeypatch):
    path, spec = route_case(tmp_path)
    monkeypatch.setattr(pcb_patch, "native_check", lambda _: native_result())
    result, comparison = pcb_patch.prepare(path, spec)
    assert result["path_before_mm"] == 10 and result["path_after_mm"] == 4
    assert result["non_target_objects_preserved"] and comparison["status"] == "passed"
    assert 'uuid "keep"' in path.read_text()
    assert path.with_suffix(".kicad_pro").read_text() == '{"board":{}}'


@pytest.mark.parametrize(
    "damage,category",
    [
        ("endpoint", "invalid_endpoint"),
        ("duplicate", "duplicate_object"),
        ("outside", "scope_violation"),
        ("foreign", "scope_violation"),
    ],
)
def test_route_refusals_are_structured(tmp_path, monkeypatch, damage, category):
    path, spec = route_case(tmp_path)
    monkeypatch.setattr(pcb_patch, "native_check", lambda _: native_result())
    if damage == "endpoint":
        spec["endpoints"][0]["at_nm"][0] = 1
    elif damage == "duplicate":
        spec["segments"].append(copy.deepcopy(spec["segments"][0]))
    elif damage == "outside":
        spec["segments"][0]["end_nm"][0] = 6000000
    else:
        spec["remove_ids"].append("keep")
    with pytest.raises(pcb_patch.RouteError) as caught:
        pcb_patch.prepare(path, spec)
    assert caught.value.details["route_failure"]["category"] == category
    assert caught.value.details["search"]["performed"] is False


def test_changed_native_error_identity_cannot_hide_behind_equal_count():
    a = {
        "type": "clearance",
        "severity": "error",
        "items": [{"uuid": "one", "pos": {"x": 0, "y": 0}}],
    }
    b = copy.deepcopy(a)
    b["items"][0]["uuid"] = "two"
    comparison = module_project.compare_native(native_result([a]), native_result([b]))
    assert comparison["status"] == "failed" and len(comparison["resolved"]) == 1


def test_public_library_confirm_replay_and_competing_preview(tmp_path):
    import shutil

    project = tmp_path / "owned.kicad_pro"
    project.write_text("{}")
    (tmp_path / "LocalLogic.pretty").mkdir()
    for name in ("source-facts.json", "library.json"):
        shutil.copyfile(EXAMPLE / name, tmp_path / name)
    env = dict(os.environ, **grant(tmp_path, outputs=[str(tmp_path)]), PYTHONIOENCODING="utf-8")
    args = ["library", "build", "--project", str(project), "--spec", str(tmp_path / "library.json")]

    def public(*extra):
        proc = subprocess.run(
            [sys.executable, "-B", str(ROOT / "entry.py"), *args, *extra, "--compact"],
            env=env,
            capture_output=True,
            timeout=60,
            cwd=ROOT,
        )
        return proc.returncode, json.loads(proc.stdout)

    code, first = public("--dry-run")
    assert code == 0 and first["ok"], first
    code, competing = public("--dry-run")
    assert code == 0 and competing["ok"]
    assert not (tmp_path / "LocalLogic.kicad_sym").exists()
    token = first["data"]["confirm_token"]
    code, saved = public("--confirm", token, "--idempotency-key", "same-library-operation")
    assert code == 0 and saved["ok"], saved
    assert saved["data"]["receipt"]["outcome"]["persistence"] == "saved"
    current = (tmp_path / "LocalLogic.kicad_sym").read_bytes()
    code, replayed = public("--confirm", token, "--idempotency-key", "same-library-operation")
    assert code == 0 and replayed["data"]["receipt"]["replayed"]
    code, stale = public("--confirm", competing["data"]["confirm_token"])
    assert code == 6 and stale["error"]["code"] == "E_CONFLICT"
    assert (tmp_path / "LocalLogic.kicad_sym").read_bytes() == current
    assert project.read_text() == "{}"


def test_grid_budget_exhaustion_is_distinct_from_blocked_window(monkeypatch):
    from kicad_cli.native import router

    def make(blocks):
        return router.Router(router.Copper((0, 0, 10, 10), blocks, [], []))

    r = make([])
    monkeypatch.setattr(router, "LIMIT", 0)
    assert r.route(1, [(2, 5, 0)], [(8, 5, 0)], 0.2, 0.1, 0.6, 0.3, allow_via=False) is None
    assert r.last_search["status"] == "budget_exhausted"
    assert r.last_search["expanded_cells"] == 1
    monkeypatch.setattr(router, "LIMIT", 900000)
    r = make([(4, 0, 6, 10)])
    assert r.route(1, [(2, 5, 0)], [(8, 5, 0)], 0.2, 0.1, 0.6, 0.3, allow_via=False) is None
    assert r.last_search["status"] == "search_window_exhausted"
    assert r.route(1, [], [(8, 5, 0)], 0.2, 0.1, 0.6, 0.3) is None
    assert r.last_search["status"] == "invalid_endpoint"


def test_native_comparison_retains_finding_multiplicity():
    finding = {
        "type": "clearance",
        "severity": "error",
        "items": [{"uuid": "one", "pos": {"x": 0, "y": 0}}],
    }
    value = module_project.compare_native(
        native_result([finding]), native_result([finding, finding])
    )
    assert value["status"] == "failed" and len(value["introduced"]) == 1
