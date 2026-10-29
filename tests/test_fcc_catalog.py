"""An offline catalogue must not manufacture current FCC from historical runs."""

from __future__ import annotations

import copy
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from bench import fcc_catalog as catalogue
from scripts import fcc_evidence as fcc


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8", newline="\n")


def fixture_source(root):
    parameter = {"name": "input", "type": "string", "required": True, "multiple": False}
    reference = {
        "commands": [
            {
                "path": "example inspect",
                "type": "read",
                "params": [parameter],
                "output_schema": "inspection",
                "driver": {
                    "effects": ["observe"],
                    "phases": ["observe"],
                    "error_codes": ["E_USAGE"],
                },
            }
        ],
        "schemas": {"inspection": {"json_schema": {"$ref": "urn:fixture:inspection"}}},
        "json_schemas": {},
        "global_options": [],
        "error_codes": {"E_USAGE": {"exit": 2, "retryable": False}},
    }
    inventory = {"exhaustive": False, "behaviors": []}
    for identifier, promise, tests in [
        ("command:example inspect:normal", "Read a fixture", ["tests.test_fixture.test_one"]),
        ("parameter:example inspect:input:discovery", parameter, ["tests.test_fixture.test_one"]),
        ("parameter:example inspect:input:normal", "Meaning", []),
        (
            "parameter:example inspect:input:boundary",
            "Refuse invalid values",
            ["tests.test_fixture.test_one[a]", "tests.test_fixture.test_one[b]"],
        ),
    ]:
        inventory["behaviors"].append(
            {
                "id": identifier,
                "promise": promise,
                "tests": tests,
                "sources": [],
                "case_scope": "fixture",
            }
        )
    write(root / fcc.INVENTORY, inventory)
    write(root / catalogue.CONTRACT, {"reference": reference})
    for name in ("build.py", "package.json", "pyproject.toml"):
        write(root / name, {})
    (root / "tests").mkdir()
    (root / "tests/test_fixture.py").write_text("def test_one():\n    pass\n", encoding="utf-8")
    (root / "scripts").mkdir()
    shutil.copyfile(Path(fcc.__file__), root / "scripts/fcc_evidence.py")
    return inventory, reference


def fixture_run(root, folder, binary, *, states=None, kind="frozen", artifact=None):
    """Synthetic checker input only: no genuine product result is asserted."""
    folder.mkdir()
    source_digest = fcc.digest_manifest(fcc.source_manifest(root))
    tree = ET.Element("testsuites")
    suite = ET.SubElement(tree, "testsuite")
    properties = ET.SubElement(suite, "properties")
    ET.SubElement(properties, "property", name="fcc_source_digest", value=source_digest)
    ET.SubElement(properties, "property", name="fcc_source_unchanged", value="true")
    for name, state in states or [("test_one", "passed"), ("test_one[a]", "passed")]:
        case = ET.SubElement(suite, "testcase", classname="tests.test_fixture", name=name)
        if state != "passed":
            ET.SubElement(case, "skipped" if state == "not_run" else "failure")
        if kind == "frozen":
            props = ET.SubElement(case, "properties")
            ET.SubElement(
                props, "property", name="fcc_artifact_sha256", value=artifact or fcc.sha(binary)
            )
    ET.ElementTree(tree).write(folder / "junit.xml")
    manifest = {p.relative_to(root).as_posix(): fcc.sha(p) for p in root.rglob("*") if p.is_file()}
    write(folder / "source-files.sha256.json", manifest)
    source_commit = "a" * 40
    finish = {
        "mode": "exe" if kind == "frozen" else "source",
        "source_commit": source_commit,
        "source_manifest_sha256": fcc.sha(folder / "source-files.sha256.json"),
        "all_tracked_bytes_unchanged": True,
        "tracked_source_unchanged": True,
        "binary_sha256": fcc.sha(binary),
        "exit": 1 if any(state == "failed" for _, state in states or []) else 0,
        "started_at": "2026-10-14T00:00:00Z",
        "finished_at": "2026-10-14T00:00:01Z",
    }
    write(folder / "finish.json", finish)
    return {
        "id": folder.name,
        "kind": kind,
        "source_root": str(root),
        "run_dir": str(folder),
        "source_commit": source_commit,
        "junit_sha256": fcc.sha(folder / "junit.xml"),
        "finish_sha256": fcc.sha(folder / "finish.json"),
        "source_manifest_sha256": fcc.sha(folder / "source-files.sha256.json"),
        "binary": str(binary),
        "binary_sha256": fcc.sha(binary),
    }


@pytest.fixture
def scene(tmp_path):
    source = tmp_path / "retained-source"
    inventory, reference = fixture_source(source)
    binary = tmp_path / "synthetic.exe"
    binary.write_bytes(b"synthetic checker fixture only")
    spec = fixture_run(source, tmp_path / "old-run", binary)
    current = tmp_path / "current-source"
    shutil.copytree(source, current)
    index = {
        "repository": str(current),
        "source_baseline_commit": "b" * 40,
        "candidate": {
            "binary": str(binary),
            "binary_sha256": fcc.sha(binary),
            "build_source_commit": "a" * 40,
        },
        "runs": [spec],
    }
    return index, inventory, reference


def test_offline_report_separates_missing_variant_from_exact_pass(scene):
    index, _, _ = scene
    report = catalogue.collect(index)
    assert report["counts"]["enumerated"] == 4
    assert report["counts"]["same_candidate_exe"] == {"passed": 2, "not_run": 2}
    boundary = report["behaviors"][-1]
    assert boundary["same_candidate_exe"]["cases"][-1]["status"] == "not_run"
    assert boundary["same_candidate_exe"]["unexecuted_or_failed_reason"]
    assert report["fcc_status"] == "incomplete"
    assert report["release_readiness"] == "unpublishable"


@pytest.mark.parametrize("state", ["failed", "not_run"])
def test_bad_member_is_not_hidden_by_a_later_pass(scene, tmp_path, state):
    index, _, _ = scene
    spec = index["runs"][0]
    second = fixture_run(
        Path(spec["source_root"]),
        tmp_path / "second-run",
        Path(spec["binary"]),
        states=[("test_one", state)],
    )
    index["runs"].append(second)
    first = catalogue.collect(index)["behaviors"][0]
    assert first["same_candidate_exe"]["status"] == state
    assert first["evidence"][0]["historical_inventory_status"] == "passed"


def test_bare_selector_does_not_mean_any_observed_variant(scene, tmp_path):
    index, _, _ = scene
    previous = index["runs"][0]
    index["runs"] = [
        fixture_run(
            Path(previous["source_root"]),
            tmp_path / "variant-only",
            Path(previous["binary"]),
            states=[("test_one[a]", "passed"), ("test_one[b]", "passed")],
        )
    ]
    report = catalogue.collect(index)
    assert report["behaviors"][0]["same_candidate_exe"]["status"] == "not_run"
    assert report["behaviors"][-1]["same_candidate_exe"]["status"] == "passed"


@pytest.mark.parametrize("kind", ["source", "unbound-exe", "different-candidate"])
def test_another_source_or_executable_does_not_certify_candidate(scene, tmp_path, kind):
    index, _, _ = scene
    spec = index["runs"][0]
    if kind == "different-candidate":
        binary = tmp_path / "other.exe"
        binary.write_bytes(b"a different unexecuted artifact")
        index["candidate"]["binary"] = str(binary)
        index["candidate"]["binary_sha256"] = fcc.sha(binary)
    else:
        index["runs"] = [
            fixture_run(
                Path(spec["source_root"]),
                tmp_path / "separate-kind",
                Path(spec["binary"]),
                kind="source" if kind == "source" else "frozen",
                artifact="0" * 64 if kind == "unbound-exe" else None,
            )
        ]
    report = catalogue.collect(index)
    assert report["counts"]["same_candidate_exe"] == {"not_run": 4}
    assert all(
        not e["current_source_admission"] for r in report["behaviors"] for e in r["evidence"]
    )


def test_revised_promise_does_not_inherit_passing_requirement(scene):
    index, inventory, _ = scene
    revised = copy.deepcopy(inventory)
    revised["behaviors"][0]["promise"] = "An additional untested guarantee"
    write(Path(index["repository"]) / fcc.INVENTORY, revised)
    first = catalogue.collect(index)["behaviors"][0]
    assert first["evidence"][0]["historical_inventory_status"] == "passed"
    assert first["evidence"][0]["requirement_relation"] == "revised_requirement"
    assert first["same_candidate_exe"]["status"] == "not_run"


@pytest.mark.parametrize("changed_definition", [False, True])
def test_retained_source_binding_stays_visible_and_cannot_certify_exe(
    scene, tmp_path, changed_definition
):
    index, inventory, _ = scene
    previous = index["runs"][0]
    index["runs"] = [
        fixture_run(
            Path(previous["source_root"]),
            tmp_path / "retained-source-run",
            Path(previous["binary"]),
            states=[("test_one", "passed")],
            kind="source",
        )
    ]
    revised = copy.deepcopy(inventory)
    row = revised["behaviors"][0]
    row["source_tests"] = list(row["tests"])
    row["source_test_scope"] = "Historical source only; no executable admission."
    row["tests"] = ["tests.test_fixture.test_one[b]"]
    current = Path(index["repository"])
    write(current / fcc.INVENTORY, revised)
    if changed_definition:
        definition = current / "tests/test_fixture.py"
        definition.write_bytes(definition.read_bytes() + b"\n# New method revision.\n")
    first = catalogue.collect(index)["behaviors"][0]
    assert first["required_test_ids"] == ["tests.test_fixture.test_one[b]"]
    assert first["same_candidate_exe"]["status"] == "not_run"
    assert first["retained_source_test_ids"] == ["tests.test_fixture.test_one"]
    assert first["retained_source_definitions"][0]["definition_sha256"] == fcc.sha(
        current / "tests/test_fixture.py"
    )
    evidence = first["retained_source_evidence"]
    assert len(evidence) == 1
    assert evidence[0]["candidate_exe_admission"] is False
    assert evidence[0]["current_source_admission"] is False
    case = evidence[0]["cases"][0]
    assert case["historically_bound"] is True
    assert case["status"] == "passed" and case["observations"] == ["passed"]
    assert case["matches_current_definition"] is not changed_definition


@pytest.mark.parametrize("fault", ["duplicate", "missing-scope"])
def test_retained_source_requirements_have_unique_ids_and_explicit_scope(scene, fault):
    _, inventory, reference = scene
    changed = copy.deepcopy(inventory)
    row = changed["behaviors"][0]
    row["source_tests"] = ["tests.test_fixture.test_one"]
    if fault == "duplicate":
        row["source_tests"] *= 2
        row["source_test_scope"] = "Historical source only."
    with pytest.raises(ValueError, match="retained source|Retained source"):
        catalogue.validate_inventory(changed, reference)


@pytest.mark.parametrize(
    "target", ["junit.xml", "finish.json", "source-files.sha256.json", "source"]
)
def test_retained_bytes_must_still_match(scene, target):
    index, _, _ = scene
    spec = index["runs"][0]
    path = (
        Path(spec["source_root"]) / "tests/test_fixture.py"
        if target == "source"
        else Path(spec["run_dir"]) / target
    )
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="digest differs"):
        catalogue.collect(index)


@pytest.mark.parametrize(
    "fault", ["duplicate-path", "wrong-path", "parameter", "output", "selection"]
)
def test_equal_counts_do_not_hide_wrong_identities_or_schema_links(scene, fault):
    _, inventory, reference = scene
    changed = copy.deepcopy(reference)
    command = changed["commands"][0]
    if fault == "duplicate-path":
        changed["commands"].append(copy.deepcopy(command))
    elif fault == "wrong-path":
        command["path"] = "example other"
    elif fault == "parameter":
        command["params"][0]["name"] = "other"
    elif fault == "output":
        command["output_schema"] = "unregistered"
    else:
        command["selection_schema"] = "urn:missing#/$defs/selection"
    with pytest.raises(ValueError):
        catalogue.validate_inventory(inventory, changed)


@pytest.mark.parametrize(
    "value",
    [
        {"argv": []},
        {"environment": {"LOCAL_SECRET": "redacted"}},
        {"note": "C:/private/project"},
        {"note": "\\\\host\\share"},
    ],
)
def test_private_projection_rejects_local_locations_and_raw_calls(value):
    with pytest.raises(ValueError):
        catalogue.portable(value)


def test_public_environment_category_count_is_not_raw_environment():
    catalogue.portable({"by_category": {"environment": 11}})


@pytest.mark.parametrize(
    "property_name,value", [("fcc_source_digest", "0" * 64), ("fcc_source_unchanged", "false")]
)
def test_rehashed_junit_with_wrong_source_claim_is_still_refused(scene, property_name, value):
    index, _, _ = scene
    spec = index["runs"][0]
    junit = Path(spec["run_dir"]) / "junit.xml"
    tree = ET.parse(junit)
    tree.find(f".//property[@name='{property_name}']").set("value", value)
    tree.write(junit)
    spec["junit_sha256"] = fcc.sha(junit)
    with pytest.raises(ValueError, match="retained source digest"):
        catalogue.collect(index)


def test_export_is_utf8_lf_exclusive_and_check_is_read_only(scene, tmp_path):
    index, _, _ = scene
    input_path, output = tmp_path / "private-index.json", tmp_path / "catalogue.json"
    write(input_path, index)
    args = ["--index", str(input_path), "--out", str(output)]
    assert catalogue.main(args) == 0
    original = output.read_bytes()
    assert b"\r" not in original and not original.startswith(b"\xef\xbb\xbf")
    with pytest.raises(FileExistsError):
        catalogue.main(args)
    assert catalogue.main([*args, "--check"]) == 0
    assert output.read_bytes() == original


def test_actual_inventory_declares_exact_tree_and_all_required_test_definitions():
    root = Path(catalogue.__file__).resolve().parents[1]
    inventory = fcc.load(root / fcc.INVENTORY)
    reference = fcc.load(root / catalogue.CONTRACT)["reference"]
    tree = catalogue.validate_inventory(inventory, reference)
    assert tree["commands"] == 33
    assert tree["command_parameters"] == 99
    assert len(inventory["behaviors"]) == 451
    for selector in {
        test
        for row in inventory["behaviors"]
        for test in row["tests"] + row.get("source_tests", [])
    }:
        assert catalogue.test_definition(root, selector)["id"] == selector
