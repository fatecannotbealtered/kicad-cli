"""Evidence tampering/gaps cannot be promoted by readiness declarations."""

from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from scripts import fcc_evidence as fcc


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def scene(tmp_path):
    # Synthetic evidence exercises the validator. Never used as product FCC.
    inventory = {
        "exhaustive": True,
        "limits": ["synthetic validator fixture"],
        "behaviors": [{"id": "fixture", "tests": ["fixture.test_public"]}],
    }
    write(tmp_path / fcc.INVENTORY, inventory)
    for name in ("build.py", "package.json", "pyproject.toml"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    digest = fcc.digest_manifest(fcc.source_manifest(tmp_path))
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    props = ET.SubElement(suite, "properties")
    ET.SubElement(props, "property", name="fcc_source_digest", value=digest)
    ET.SubElement(props, "property", name="fcc_source_unchanged", value="true")
    ET.SubElement(suite, "testcase", classname="fixture", name="test_public")
    run = tmp_path / "run.xml"
    ET.ElementTree(root).write(run)
    report = tmp_path / "report.json"
    write(report, fcc.collect(tmp_path, [run]))
    return tmp_path, run, report


def test_intact_execution_fixture_is_checked(scene):
    root, _run, report = scene
    assert fcc.admission(root, report) == []


@pytest.mark.parametrize(
    "kind", ["manifest", "inventory", "test-status", "artifact", "claim", "run", "missing"]
)
def test_each_evidence_binding_is_required(scene, kind):
    root, run, report = scene
    value = json.loads(report.read_bytes())
    if kind == "manifest":
        (root / "build.py").write_text("changed", encoding="utf-8")
    elif kind == "inventory":
        inventory = json.loads((root / fcc.INVENTORY).read_bytes())
        inventory["behaviors"].append({"id": "untested", "tests": []})
        write(root / fcc.INVENTORY, inventory)
    elif kind == "test-status":
        value["behaviors"][0]["status"] = "not_run"
    elif kind == "artifact":
        value["artifact"] = {"kind": "frozen", "sha256": "0" * 64}
    elif kind == "claim":
        value["fcc_status"] = "manual-verified"
    elif kind == "run":
        run.write_text("<testsuites/>", encoding="utf-8")
    else:
        run.unlink()
    write(report, value)
    assert fcc.admission(root, report)


@pytest.mark.parametrize("status", ["skipped", "failure", "absent", "unbound"])
def test_missing_or_skipped_evidence_is_not_passing(scene, status):
    root, run, report = scene
    tree = ET.parse(run)
    case = tree.find(".//testcase")
    if status == "absent":
        case.set("name", "another")
    elif status == "unbound":
        tree.find(".//property[@name='fcc_source_digest']").set("value", "foreign")
    else:
        ET.SubElement(case, status)
    tree.write(run)
    value = fcc.collect(root, [run])
    write(report, value)
    assert value["fcc_status"] == "incomplete"
    assert fcc.admission(root, report)


def test_source_junit_cannot_certify_a_frozen_artifact(scene):
    root, run, report = scene
    binary = root / "fixture.exe"
    binary.write_bytes(b"not executed")
    value = fcc.collect(root, [run], binary)
    assert value["counts"]["not_run"] == 1
    write(report, value)
    assert fcc.admission(root, report, binary)


def test_inventory_schema_and_reference_parameters_are_reviewable():
    root = Path(__file__).resolve().parents[1]
    inventory = json.loads((root / fcc.INVENTORY).read_bytes())
    identifiers = [r["id"] for r in inventory["behaviors"]]
    assert len(identifiers) == len(set(identifiers))
    reference = json.loads((root / "kicad_cli/driver_contract.json").read_bytes())["reference"]
    for command in reference["commands"]:
        for parameter in command["params"]:
            prefix = f"parameter:{command['path']}:{parameter['name']}:"
            assert {prefix + mode for mode in ("discovery", "normal", "boundary")} <= set(
                identifiers
            )
    assert inventory["exhaustive"] is False  # Open promises cannot become 100% by dispatch trace.


def observed_run(path, rows, source_digest="bound-source"):
    """Synthetic validator input only; this never counts as execution FCC."""
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    props = ET.SubElement(suite, "properties")
    ET.SubElement(props, "property", name="fcc_source_digest", value=source_digest)
    ET.SubElement(props, "property", name="fcc_source_unchanged", value="true")
    for name, status, artifact in rows:
        case = ET.SubElement(suite, "testcase", classname="fixture", name=name)
        if status != "passed":
            ET.SubElement(case, "skipped" if status == "not_run" else "failure")
        if artifact is not None:
            properties = ET.SubElement(case, "properties")
            ET.SubElement(properties, "property", name="fcc_artifact_sha256", value=artifact)
    ET.ElementTree(root).write(path)
    return path


def test_bare_selector_does_not_infer_parameter_collection(tmp_path):
    run = observed_run(tmp_path / "run.xml", [("test_export[gerber]", "passed", None)])
    rows = fcc.measure(
        {"behaviors": [{"id": "all-exports", "tests": ["fixture.test_export"]}]},
        [run],
        source_digest="bound-source",
    )
    assert rows[0]["status"] == "not_run"


@pytest.mark.parametrize("missing", ["gerber", "drill", "plot"])
def test_required_variant_set_cannot_be_inferred_from_observations(tmp_path, missing):
    expected = ["gerber", "drill", "plot"]
    run = observed_run(
        tmp_path / "run.xml",
        [(f"test_export[{name}]", "passed", None) for name in expected if name != missing],
    )
    rows = fcc.measure(
        {"behaviors": [{"id": "all", "tests": [f"fixture.test_export[{n}]" for n in expected]}]},
        [run],
        source_digest="bound-source",
    )
    assert rows[0]["status"] == "not_run"


def test_one_export_variant_cannot_certify_other_commands(tmp_path):
    names = ["gerber", "drill", "plot"]
    run = observed_run(tmp_path / "run.xml", [("test_export[gerber]", "passed", None)])
    rows = fcc.measure(
        {"behaviors": [{"id": n, "tests": [f"fixture.test_export[{n}]"]} for n in names]},
        [run],
        source_digest="bound-source",
    )
    assert {r["id"]: r["status"] for r in rows} == {
        "gerber": "passed",
        "drill": "not_run",
        "plot": "not_run",
    }


@pytest.mark.parametrize("status", ["not_run", "failed"])
def test_skipped_or_failed_member_is_not_hidden_by_passed_run(tmp_path, status):
    runs = [
        observed_run(tmp_path / f"{i}.xml", [("test_export[gerber]", s, "binary")])
        for i, s in enumerate([status, "passed"])
    ]
    rows = fcc.measure(
        {"behaviors": [{"id": "gerber", "tests": ["fixture.test_export[gerber]"]}]},
        runs,
        source_digest="bound-source",
        binary_sha256="binary",
    )
    assert rows[0]["status"] == status


def test_artifact_filter_leaves_missing_variant_unexecuted(tmp_path):
    run = observed_run(
        tmp_path / "run.xml",
        [
            ("test_export[gerber]", "passed", "binary"),
            ("test_export[drill]", "passed", "another-binary"),
        ],
    )
    rows = fcc.measure(
        {
            "behaviors": [
                {
                    "id": "both",
                    "tests": [
                        "fixture.test_export[gerber]",
                        "fixture.test_export[drill]",
                    ],
                }
            ]
        },
        [run],
        source_digest="bound-source",
        binary_sha256="binary",
    )
    assert rows[0]["status"] == "not_run"
