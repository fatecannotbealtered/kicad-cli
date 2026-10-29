"""Host-only meaningful negatives for the independent saved-scene method."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from bench.saved_scene_acceptance import commands, config_identity, deltas, formal_plan, image_plan
from bench.saved_scene_checks import (
    compare_complete_sexp,
    compare_findings,
    compare_xml,
    detached_graph,
    normalize_findings,
    sexp,
)


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_complete_child_structure_accepts_only_two_frozen_label_texts(tmp_path):
    old = (
        '(kicad_sch (label "LOCAL" (at 1 2 0) '
        '(uuid "9ae20040-ee73-5751-be29-f1fcb2bb0846")) '
        '(label "LOCAL" (at 4 5 0) (uuid "ac048264-8b68-5a78-9710-4325233838ef")) '
        "(wire (pts (xy 1 2) (xy 3 4))))"
    )
    original = write(tmp_path, "old", old)
    actual = write(tmp_path, "actual", old.replace('"LOCAL"', '"SENSE"'))
    assert compare_complete_sexp(original, actual, "C", "child")["status"] == "passed"
    actual.write_text(actual.read_text().replace("(xy 3 4)", "(xy 3 5)"))
    with pytest.raises(ValueError, match="Protected S-expression"):
        compare_complete_sexp(original, actual, "C", "child")


@pytest.mark.parametrize("text", ["(a", "(a))", "outside", "(a) (b)"])
def test_independent_reader_rejects_incomplete_structure(text):
    with pytest.raises(ValueError):
        sexp(text)


def rows(*pins):
    return [(ref, pin, (("ref", ref), ("pin", pin))) for ref, pin in pins]


def test_singleton_automatic_names_do_not_relax_member_relationships():
    original = {"/OUT": rows(("R1", "2"), ("U1", "1")), "automatic-old": rows(("U1", "3"))}
    changed = {
        "/OUT": rows(("R1", "2"), ("U1", "1")),
        "automatic-new": rows(("U1", "3")),
        "other-singleton": rows(("U1", "5")),
    }
    assert detached_graph(original) == detached_graph(changed)
    changed["automatic-new"] += rows(("U1", "5"))
    with pytest.raises(ValueError, match="Selected pin joined"):
        detached_graph(changed)


def test_detached_pin_duplicate_in_two_nets_is_rejected():
    with pytest.raises(ValueError, match="duplicated"):
        detached_graph({"a": rows(("U1", "3")), "b": rows(("U1", "3"))})


def drc():
    return {
        "date": "old",
        "coordinate_units": "mm",
        "violations": [
            {
                "type": "clearance",
                "severity": "error",
                "description": "exact failure",
                "items": [{"uuid": "pad-1", "pos": {"x": 1, "y": 2}}],
            }
        ],
        "unconnected_items": [],
        "schematic_parity": [],
        "ignored_checks": ["existing"],
    }


def test_report_only_date_changes_and_full_finding_preservation(tmp_path):
    old = drc()
    new = json.loads(json.dumps(old))
    new["date"] = "later"
    before = write(tmp_path, "old.json", json.dumps(old))
    after = write(tmp_path, "new.json", json.dumps(new))
    assert compare_findings(before, after, drc=True)["status"] == "passed"
    assert normalize_findings(old)["ignored_checks"] == ["existing"]
    new["violations"][0]["items"][0]["uuid"] = "different-pad"
    after.write_text(json.dumps(new))
    with pytest.raises(ValueError, match="complete findings"):
        compare_findings(before, after, drc=True)


def test_only_exact_finding_description_net_spelling_may_follow_scene_A(tmp_path):
    old, new = drc(), drc()
    old["violations"][0]["description"] = 'Connection to net "/SIG" is incomplete'
    old["violations"][0]["items"][0]["description"] = "Track on /SIG (original geometry)"
    new["violations"][0]["description"] = 'Connection to net "/DATA" is incomplete'
    new["violations"][0]["items"][0]["description"] = "Track on /DATA (original geometry)"
    before = write(tmp_path, "old.json", json.dumps(old))
    after = write(tmp_path, "new.json", json.dumps(new))
    result = compare_findings(before, after, drc=True)
    assert result["status"] == "passed"
    assert len(result["expected_baseline_description_changes"]) == 2
    assert all(
        row["before"] != row["after"] for row in result["expected_baseline_description_changes"]
    )
    assert result["raw_baseline_sha256"] != result["raw_changed_sha256"]
    new["violations"][0]["items"][0]["uuid"] = "other-object"
    after.write_text(json.dumps(new))
    with pytest.raises(ValueError, match="frozen policy"):
        compare_findings(before, after, drc=True)


def test_erc_sheet_finding_description_policy_keeps_sheet_and_item_identities(tmp_path):
    finding = {
        "type": "example",
        "severity": "warning",
        "description": "Net /SIG is present",
        "items": [{"uuid": "original", "description": "Pin on /SIG", "pos": {"x": 1, "y": 2}}],
    }
    old = {
        "date": "before",
        "sheets": [{"path": "/CHILD_A/", "uuid_path": "/same-instance", "violations": [finding]}],
        "ignored_checks": [],
    }
    new = json.loads(json.dumps(old))
    new["sheets"][0]["violations"][0]["description"] = "Net /DATA is present"
    new["sheets"][0]["violations"][0]["items"][0]["description"] = "Pin on /DATA"
    before = write(tmp_path, "old.json", json.dumps(old))
    after = write(tmp_path, "new.json", json.dumps(new))
    assert len(compare_findings(before, after)["expected_baseline_description_changes"]) == 2
    new["sheets"][0]["uuid_path"] = "/different-instance"
    after.write_text(json.dumps(new))
    with pytest.raises(ValueError, match="frozen policy"):
        compare_findings(before, after)


@pytest.mark.parametrize(
    "spelling", ["/SIG2", "/SIG_2", "/SIG/sub", "/SIG-foo", "/SIG.foo", "/SIG[1]", "/ROOT/SIG"]
)
def test_net_description_normalization_preserves_other_complete_names(spelling):
    report = {"violations": [{"description": "Net '" + spelling + "'"}]}
    assert normalize_findings(report, rename_sig=True) == report


def test_paired_report_wrapper_changes_only_exact_net_name():
    report = {"violations": [{"description": "Pad 1 [/SIG] of R1"}]}
    normalized = normalize_findings(report, rename_sig=True)
    assert normalized["violations"][0]["description"] == "Pad 1 [/DATA] of R1"
    for text in ("Pad 1 [/SIG[1]] of R1", "Pad 1 [/SIG2] of R1", "Net/ROOT[/SIG]"):
        value = {"violations": [{"description": text}]}
        assert normalize_findings(value, rename_sig=True) == value


@pytest.mark.parametrize("kind", ["ignore", "value", "membership", "multiplicity"])
def test_description_policy_never_relaxes_other_finding_information(tmp_path, kind):
    old, new = drc(), drc()
    if kind == "ignore":
        old["ignored_checks"] = [{"description": "Rule for /SIG"}]
        new["ignored_checks"] = [{"description": "Rule for /DATA"}]
    elif kind == "value":
        old["violations"][0]["items"][0]["description"] = "R1 [10k]"
        new["violations"][0]["items"][0]["description"] = "R1 [22k]"
    elif kind == "membership":
        old["violations"][0]["net"] = "/SIG"
        new["violations"][0]["net"] = "/DATA"
    else:
        old["violations"].append(json.loads(json.dumps(old["violations"][0])))
    before = write(tmp_path, "old.json", json.dumps(old))
    after = write(tmp_path, "new.json", json.dumps(new))
    with pytest.raises(ValueError, match="frozen policy"):
        compare_findings(before, after, drc=True)


@pytest.mark.parametrize("mutation", ["parity", "ignore", "severity"])
def test_parity_ignore_and_severity_are_never_normalized_away(tmp_path, mutation):
    old, new = drc(), drc()
    if mutation == "parity":
        new["schematic_parity"] = [{"type": "missing_component"}]
    elif mutation == "ignore":
        new["ignored_checks"].append("new_ignore")
    else:
        new["violations"][0]["severity"] = "warning"
    before = write(tmp_path, "old.json", json.dumps(old))
    after = write(tmp_path, "new.json", json.dumps(new))
    with pytest.raises(ValueError):
        compare_findings(before, after, drc=True)


def test_config_only_one_declared_working_directory_value_may_change(tmp_path):
    folder = tmp_path / "configuration/10.0"
    folder.mkdir(parents=True)
    path = folder / "kicad_common.json"
    original = {
        "system": {"working_dir": str((tmp_path / "baseline").resolve())},
        "rules": {"ignore": []},
    }
    path.write_text(json.dumps(original))
    before = config_identity(tmp_path)
    original["system"]["working_dir"] = str((tmp_path / "A").resolve())
    path.write_text(json.dumps(original))
    assert config_identity(tmp_path) == before
    original["rules"]["ignore"].append("changed")
    path.write_text(json.dumps(original))
    assert config_identity(tmp_path) != before
    original["system"]["working_dir"] = str((tmp_path / "outside").resolve())
    path.write_text(json.dumps(original))
    with pytest.raises(ValueError, match="outside"):
        config_identity(tmp_path)


def test_config_file_addition_replacement_and_deletion_are_visible(tmp_path):
    folder = tmp_path / "configuration/10.0"
    folder.mkdir(parents=True)
    path = folder / "pcbnew.json"
    path.write_text('{"rules":1}')
    original = config_identity(tmp_path)
    path.write_text('{"rules":2}')
    assert config_identity(tmp_path) != original
    path.unlink()
    assert config_identity(tmp_path) != original
    path.write_text('{"rules":1}')
    (folder / "new.json").write_text("{}")
    assert config_identity(tmp_path) != original


def config(tmp_path):
    return {
        "output": str(tmp_path),
        "repository": str(tmp_path / "repo"),
        "native_home": str(tmp_path / "native"),
        "host_python": str(tmp_path / "host/python.exe"),
        "timeout_seconds": 180,
        "library_paths": [],
    }


def test_precise_fifteen_consumer_and_eleven_full_image_sets(tmp_path):
    value = config(tmp_path)
    plan = formal_plan(value)
    assert [row["id"] for row in plan] == [
        "baseline-xml",
        "baseline-erc",
        "baseline-drc",
        "baseline-sdk",
        "final-xml",
        "final-erc",
        "final-drc",
        "final-sdk",
        "final-schematic-svg",
        "final-front-svg",
        "final-back-svg",
        "alias-xml",
        "alias-schematic-svg",
        "child-xml",
        "child-schematic-svg",
    ]
    assert len(commands(value, "baseline", preparation=True)) == 7
    images = image_plan(value)
    assert len(images) == 11
    assert {row["page_identity"] for row in images if row["scene"] == "A"} == {
        "root",
        "CHILD_A",
        "CHILD_B",
        "PCB-front",
        "PCB-back",
    }
    for scene in ("B", "C"):
        assert {row["page_identity"] for row in images if row["scene"] == scene} == {
            "root",
            "CHILD_A",
            "CHILD_B",
        }


def test_initialization_delta_records_add_modify_remove():
    assert deltas({"a": "1", "b": "2"}, {"a": "3", "c": "4"}) == [
        {"relative_path": "a", "before": "1", "after": "3", "kind": "modified"},
        {"relative_path": "b", "before": "2", "after": None, "kind": "removed"},
        {"relative_path": "c", "before": None, "after": "4", "kind": "added"},
    ]


def xml_text(scene):
    value = "22k" if scene == "A" else "10k"
    r3_fields = '<field name="MPN">RC0805</field>' if scene == "A" else ""
    dnp_property = '<property name="dnp"/>' if scene == "A" else ""
    sig = "/DATA" if scene == "A" else "/SIG"
    local = "SENSE" if scene == "C" else "LOCAL"
    components = (
        f'<comp ref="R1"><value>{value}</value><fields/></comp>'
        f'<comp ref="R3"><value>1k</value><fields>{r3_fields}</fields>'
        f"{dnp_property}</comp>"
    )
    nets = [
        (sig, [("R1", "1"), ("R10", "1"), ("R2", "1")]),
        ("/OUT", [("R1", "2"), ("U1", "1")]),
        ("/CHILD_A/" + local, [("C10", "1"), ("R10", "2")]),
        ("/CHILD_B/" + local, [("C20", "1"), ("R20", "2")]),
        ("NC-3", [("U1", "3")]),
        ("NC-5", [("U1", "5")]),
    ]
    text = (
        '<export><design><sheet name="/"/><sheet name="/CHILD_A/"/>'
        '<sheet name="/CHILD_B/"/></design><components>' + components + "</components><nets>"
    )
    for name, members in nets:
        if scene == "B" and name.startswith("NC-"):
            name = "new-" + name
        text += '<net name="' + name + '">'
        text += "".join('<node ref="' + ref + '" pin="' + pin + '"/>' for ref, pin in members)
        text += "</net>"
    return text + "</nets></export>"


@pytest.mark.parametrize("scene", ["A", "B", "C"])
def test_official_xml_complete_component_and_member_contracts(tmp_path, scene):
    before = write(tmp_path, "base.xml", xml_text("baseline"))
    after = write(tmp_path, "actual.xml", xml_text(scene))
    assert compare_xml(before, after, scene)["status"] == "passed"
    after.write_text(after.read_text().replace('ref="R1" pin="2"', 'ref="R1" pin="9"'))
    with pytest.raises(ValueError):
        compare_xml(before, after, scene)


@pytest.mark.parametrize("mutation", ["new_file", "existing_other", "common_rules", "delete"])
def test_preparation_preferences_have_finite_allowed_transition(tmp_path, mutation):
    from bench.saved_scene_acceptance import config_values, validate_config_transition

    folder = tmp_path / "configuration/10.0"
    folder.mkdir(parents=True)
    common = {"system": {"working_dir": str(tmp_path / "preparation-project")}, "ignore": []}
    (folder / "kicad_common.json").write_text(json.dumps(common))
    (folder / "kicad.json").write_text('{"value":1}')
    before = config_values(tmp_path)
    if mutation == "new_file":
        (folder / "unexpected.json").write_text("{}")
    elif mutation == "existing_other":
        (folder / "kicad.json").write_text('{"value":2}')
    elif mutation == "common_rules":
        common["ignore"].append("hidden_failure")
        (folder / "kicad_common.json").write_text(json.dumps(common))
    else:
        (folder / "kicad.json").unlink()
    with pytest.raises(ValueError):
        validate_config_transition(tmp_path, before)


@pytest.mark.parametrize("bad_parity", [False, True])
def test_exact_outputs_are_consumed_by_whole_formal_mock(tmp_path, monkeypatch, bad_parity):
    """Exercise all 15+11 output paths without EXE, native or child processes."""
    from bench import saved_scene_acceptance as method

    value = config(tmp_path)
    for scene in method.SCENES:
        folder = tmp_path / scene
        folder.mkdir()
        (folder / "edit.kicad_pcb").write_text("saved board " + scene)
    (tmp_path / "configuration").mkdir()
    mocked_binding = {"complete": "host synthetic only"}
    monkeypatch.setattr(method, "binding", lambda config: mocked_binding)
    monkeypatch.setattr(method, "input_reference_check", lambda root: {})
    monkeypatch.setattr(method, "structure_checks", lambda root: {"synthetic": "checked elsewhere"})
    planned = {
        "binding": mocked_binding,
        "native_consumers": formal_plan(value),
        "images": image_plan(value),
        "expected_complete_scene_output_names": method.expected_output_names(value),
        "report_normalization": method.REPORT_NORMALIZATION_POLICY,
        "dependency_policy": method.dependency_policy(value),
        "input_reference_checks": {},
    }
    method.put(tmp_path / "FROZEN.json", planned)
    native_base = {
        "native_version": "10.0.6",
        "input_unchanged": True,
        "input_sha256": method.sha(tmp_path / "baseline/edit.kicad_pcb"),
        "footprints": [
            {"ref": "R1", "value": "10k", "fields": {}, "dnp": False, "pads": [{"net": "/SIG"}]},
            {"ref": "R3", "value": "1k", "fields": {}, "dnp": False, "pads": []},
        ],
        "tracks": [{"net": "/SIG", "uuid": "unchanged"}],
        "zones": ["zone-1"],
    }
    seen = []

    def fake_call(config, row, directory):
        seen.append(row["id"])
        directory.mkdir()
        argv = row["argv"]
        if row["id"].startswith("raster-"):
            target = __import__("pathlib").Path(argv[-2])
            target.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic-only")
            return
        kind, scene = row["kind"], row["scene"]
        folder = tmp_path / (scene + "-output")
        if kind == "xml":
            (folder / "netlist.xml").write_text(xml_text(scene))
        elif kind in {"erc", "drc"}:
            report = (
                drc()
                if kind == "drc"
                else {
                    "date": "timestamp",
                    "sheets": [{"path": "/", "violations": []}],
                    "ignored_checks": ["original"],
                }
            )
            if kind == "drc" and scene == "A" and bad_parity:
                report["schematic_parity"] = [{"type": "actual mismatch"}]
            (folder / (kind + ".json")).write_text(json.dumps(report))
        elif kind == "sdk":
            observed = json.loads(json.dumps(native_base))
            if scene == "A":
                observed["input_sha256"] = method.sha(tmp_path / "A/edit.kicad_pcb")
                observed["footprints"][0]["value"] = "22k"
                observed["footprints"][0]["pads"][0]["net"] = "/DATA"
                observed["footprints"][1]["fields"]["MPN"] = "RC0805"
                observed["footprints"][1]["dnp"] = True
                observed["tracks"][0]["net"] = "/DATA"
            (directory / "stdout").write_text(json.dumps(observed))
        elif kind == "schematic-svg":
            images = folder / "schematic-svg"
            images.mkdir()
            for name in method.PAGE_NAMES.values():
                (images / name).write_text('<svg width="100" height="100"/>')
        else:
            (folder / kind.replace("-svg", ".svg")).write_text('<svg width="100" height="100"/>')

    monkeypatch.setattr(method, "record_call", fake_call)
    result = method.execute(value, tmp_path / "FROZEN.json", method.sha(tmp_path / "FROZEN.json"))
    assert result["completed"] is True
    assert result["functional_checks_passed"] is not bad_parity
    assert len(seen) == 26
    outcome = method.read(tmp_path / "NATIVE-RESULT.json")
    assert outcome["formal_consumer_ids"] == [row["id"] for row in formal_plan(value)]
    assert outcome["formal_native_consumers"] == 15
    assert outcome["manual_views_completed"] == 0
    assert len(outcome["images"]) == 11
    assert bool(outcome["validation_failures"]) is bad_parity
    assert {
        scene: sorted(files) for scene, files in outcome["complete_scene_output_trees"].items()
    } == method.expected_output_names(value)


@pytest.mark.parametrize(
    "filename",
    [
        "edit.kicad_sch",
        "edit.kicad_pcb",
        "edit.kicad_dru",
        "fixture.pretty/x.kicad_mod",
        "new.kicad-cli.lock",
    ],
)
def test_preparation_input_drift_refuses_before_any_native_call(tmp_path, monkeypatch, filename):
    from bench import saved_scene_acceptance as method

    value = config(tmp_path)
    for scene in method.SCENES:
        (tmp_path / scene).mkdir()
    prep = tmp_path / "preparation-project"
    prep.mkdir()
    for name in (
        "edit.kicad_sch",
        "edit.kicad_pcb",
        "edit.kicad_dru",
        "fixture.pretty/x.kicad_mod",
    ):
        path = prep / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("frozen original")
    monkeypatch.setattr(method, "method_identity", lambda config: {})
    monkeypatch.setattr(method, "dependency_identity", lambda config: {})
    monkeypatch.setattr(method, "environment_identity", lambda config: {})
    approved = {
        "config": value,
        "methods": {},
        "dependencies": {},
        "environment": {},
        "engineering": {scene: {} for scene in method.SCENES},
        "preparation_engineering": method.tree(prep),
    }
    method.put(tmp_path / "INPUT-FROZEN.json", approved)
    (prep / filename).write_text("unreviewed input")
    calls = []
    monkeypatch.setattr(method, "record_call", lambda *args: calls.append(args))
    with pytest.raises(ValueError, match="preparation engineering copy drifted"):
        method.prepare(value, method.sha(tmp_path / "INPUT-FROZEN.json"))
    assert calls == []
    assert not (tmp_path / "PREPARE-START.json").exists()


def test_host_tree_excludes_only_declared_site_packages_and_keeps_other_bytes(tmp_path):
    from bench.saved_scene_acceptance import tree

    (tmp_path / "site-packages/unrelated").mkdir(parents=True)
    (tmp_path / "site-packages/unrelated/startup.pth").write_text("unrelated startup")
    (tmp_path / "json").mkdir()
    (tmp_path / "json/__init__.py").write_text("bound stdlib")
    (tmp_path / "normal.pth").write_text("bound other byte")
    result = tree(tmp_path, excluded_root_directories=("site-packages",))
    assert set(result) == {"json/__init__.py", "normal.pth"}
    assert len(tree(tmp_path)) == 3


def test_controller_refuses_site_startup_before_any_config_read(tmp_path):
    from bench import saved_scene_acceptance as method

    original = method.sys.flags
    try:
        method.sys.flags = SimpleNamespace(no_site=False, dont_write_bytecode=True)
        with pytest.raises(SystemExit, match="requires Python -B -S"):
            method.main()
    finally:
        method.sys.flags = original
    assert not tmp_path.joinpath("configuration").exists()


def test_site_free_renderer_renders_whole_synthetic_viewport_and_records_module_origins(tmp_path):
    source = write(
        tmp_path,
        "whole.svg",
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="60" '
        'viewBox="0 0 120 60"><rect x="0" y="0" width="120" height="60" '
        'fill="white"/><text x="5" y="30">SYNTHETIC</text></svg>',
    )
    output = tmp_path / "whole.png"
    script = Path(__file__).resolve().parents[1] / "bench/saved_scene_render.py"
    result = subprocess.run(
        [sys.executable, "-B", "-S", str(script), str(source), str(output), "3"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["page_points"] == [0.0, 0.0, 120.0, 60.0]
    assert report["pixel_size"] == [360, 180]
    assert report["startup"]["no_site"] is True
    assert report["startup"]["pth_user_site_sitecustomize_executed"] is False
    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert any(row["module"] == "fitz" for row in report["actual_module_sources"])
    assert any(row["module"] == "pymupdf" for row in report["actual_module_sources"])


def test_renderer_rejects_undeclared_actual_module_file(tmp_path):
    from bench import saved_scene_render as renderer

    source = write(tmp_path, "undeclared.py", "raise AssertionError('never executed')")
    original = renderer.sys.modules
    try:
        renderer.sys.modules = {"undeclared": SimpleNamespace(__file__=str(source))}
        with pytest.raises(ValueError, match="imported undeclared code"):
            renderer.module_sources()
    finally:
        renderer.sys.modules = original


def test_native_reader_rejects_unknown_external_module_root(tmp_path):
    from bench.saved_scene_native_read import module_sources

    native = tmp_path / "native"
    native.mkdir()
    outside = write(tmp_path, "outside.py", "# not executed")
    with pytest.raises(ValueError, match="undeclared external code"):
        module_sources(native, {"outside": SimpleNamespace(__file__=str(outside))})


def test_vendor_addsitedir_bypasses_disabled_user_site_but_our_startup_avoids_it(tmp_path):
    """Pure host fixtures model the actual vendor's unconditional addsitedir."""
    reader = Path(__file__).resolve().parents[1] / "bench/saved_scene_native_read.py"
    for phase in ("explicit-vendor", "our-startup"):
        folder = tmp_path / phase
        vendor, thirdparty = folder / "vendor", folder / "Documents/3rdparty"
        vendor.mkdir(parents=True)
        thirdparty.mkdir(parents=True)
        marker = folder / "thirdparty-executed.txt"
        (thirdparty / "side-effect.pth").write_text(
            "import pathlib; pathlib.Path(" + repr(str(marker)) + ").write_text('owned fixture')\n"
        )
        (vendor / "sitecustomize.py").write_text(
            "import site\nsite.addsitedir(" + repr(str(thirdparty)) + ")\n"
        )
        child_env = dict(os.environ, PYTHONPATH=str(vendor))
        if phase == "explicit-vendor":
            code = "import site; site.ENABLE_USER_SITE=False; import sitecustomize"
        else:
            code = (
                "import runpy,sys; d=runpy.run_path(" + repr(str(reader)) + "); "
                "d['initialize_search_path'](); "
                "assert sys.path == d['_EXPECTED_PATH']; "
                "assert 'sitecustomize' not in sys.modules"
            )
        result = subprocess.run(
            [sys.executable, "-B", "-S", "-c", code],
            env=child_env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert marker.exists() is (phase == "explicit-vendor")


def test_method_manifest_binds_consulted_matrix_and_replacement_or_deletion(tmp_path):
    from bench.saved_scene_acceptance import method_identity

    folder = tmp_path / "bench"
    package = folder / "persistence_acceptance"
    package.mkdir(parents=True)
    for name in (
        "saved_scene_acceptance.py",
        "saved_scene_checks.py",
        "saved_scene_native_read.py",
        "saved_scene_render.py",
        "native_observation.py",
        "owned_process.py",
        "win_debugging.py",
    ):
        (folder / name).write_text("# method fixture")
    matrix = package / "matrix.json"
    matrix.write_text('{"original":true}')
    original = method_identity({"repository": str(tmp_path)})
    assert str(matrix.resolve()) in original
    matrix.write_text('{"replaced":true}')
    assert method_identity({"repository": str(tmp_path)}) != original
    matrix.unlink()
    with pytest.raises(ValueError, match="regular non-link file"):
        method_identity({"repository": str(tmp_path)})
