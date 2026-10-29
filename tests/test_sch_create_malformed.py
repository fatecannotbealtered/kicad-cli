"""Finite Host-only malformed hierarchy checks using the acceptance request."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from kicad_cli import engineering_inputs, envelope, schematic
from kicad_cli import sch_hierarchy_contract as hierarchy
from kicad_cli.sync_inputs import InputError

REQUEST = (
    Path(__file__).resolve().parents[1]
    / "hierarchical-create"
    / "requirements"
    / "hier_dual_rc.json"
)
MISSING = object()
VALUES = [
    pytest.param(["root"], id="array"),
    pytest.param({"id": "root"}, id="object"),
    pytest.param(None, id="null"),
    pytest.param(MISSING, id="missing"),
]
FIELDS = [
    pytest.param("/parts/0/sheet_id", id="sheet-id"),
    pytest.param("/hierarchy/children/0/ports/0/direction", id="port-direction"),
    pytest.param("/hierarchy/children/0/id", id="child-id"),
    pytest.param("/hierarchy/children/0/name", id="child-name"),
    pytest.param("/hierarchy/schema", id="hierarchy-schema"),
]


def request():
    return json.loads(REQUEST.read_bytes())


def change(spec, pointer, value):
    *parents, key = pointer.strip("/").split("/")
    owner = spec
    for parent in parents:
        owner = owner[int(parent)] if isinstance(owner, list) else owner[parent]
    if value is MISSING:
        del owner[key]
    else:
        owner[key] = deepcopy(value)


@pytest.mark.parametrize("pointer", FIELDS)
@pytest.mark.parametrize("value", VALUES)
def test_malformed_scalar_has_precise_host_hierarchy_problem(pointer, value):
    spec = request()
    change(spec, pointer, value)
    problems = hierarchy.hierarchy_problems(spec, "hier_dual_rc")
    assert any(problem["field"] == pointer for problem in problems), problems


@pytest.mark.parametrize(
    "pointer",
    [
        pytest.param("/parts/0/sheet_id", id="sheet-id"),
        pytest.param("/hierarchy/children/0/ports/0/direction", id="port-direction"),
    ],
)
@pytest.mark.parametrize("value", [*VALUES, pytest.param("unsupported", id="unsupported")])
def test_public_load_refuses_before_any_formal_output(pointer, value, tmp_path, capsys):
    spec = request()
    change(spec, pointer, value)
    source = tmp_path / "hier_dual_rc.json"
    source.write_text(json.dumps(spec), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    envelope.configure()
    with pytest.raises(SystemExit) as stopped:
        schematic.load_spec(str(source))
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    assert stopped.value.code == 2
    assert doc["ok"] is False and doc["schema_version"] == "1.0"
    assert doc["error"]["code"] == "E_VALIDATION"
    assert doc["error"]["retryable"] is False
    assert doc["error"]["details"]["parameter"] == "--spec"
    assert any(problem["field"] == pointer for problem in doc["error"]["details"]["problems"]), doc
    assert not captured.err
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


@pytest.mark.parametrize("value", VALUES)
def test_host_capture_refuses_before_library_lookup_or_formal_output(value, tmp_path, monkeypatch):
    spec = request()
    change(spec, "/parts/0/sheet_id", value)
    source = tmp_path / "hier_dual_rc.json"
    source.write_text(json.dumps(spec), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    def unexpected(*args, **kwargs):
        pytest.fail("malformed declarations must stop before library lookup")

    monkeypatch.setattr(engineering_inputs.kicad_env, "find_kicad_root", unexpected)
    monkeypatch.setattr(engineering_inputs.lib_tables, "default_config_dir", unexpected)
    monkeypatch.setattr(engineering_inputs.lib_tables.Tables, "of", unexpected)
    with pytest.raises(InputError) as stopped:
        engineering_inputs.capture("sch create", {"spec": str(source), "out": str(tmp_path)})
    assert any(
        problem["field"] == "/parts/0/sheet_id" for problem in stopped.value.details["problems"]
    )
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


@pytest.mark.parametrize(
    ("pointer", "value"),
    [
        pytest.param("/parts/0/ref", [], id="part-ref-array"),
        pytest.param("/parts/0/kind", [], id="part-kind-array"),
        pytest.param("/nets/0/name", {}, id="net-name-object"),
        pytest.param("/nets/0/connect", None, id="connect-null"),
        pytest.param("/hierarchy/children/0/ports/0/kind", {}, id="port-kind-object"),
        pytest.param("/hierarchy/children/0/local_nets/A_RC", [], id="local-name-array"),
        pytest.param("/hierarchy/shared_power_nets", [["GND"]], id="shared-net-array"),
    ],
)
def test_schema_errors_do_not_enter_hierarchy_semantics(pointer, value, monkeypatch):
    spec = request()
    change(spec, pointer, value)

    def unexpected(*args, **kwargs):
        pytest.fail("schema-invalid input must not enter hierarchy semantics")

    monkeypatch.setattr(hierarchy, "hierarchy_problems", unexpected)
    assert schematic.spec_problems(spec)


def test_direct_validation_retains_original_part_index_after_malformed_row():
    spec = request()
    spec["parts"].insert(0, [])
    spec["parts"][1]["sheet_id"] = {"id": "root"}
    problems = hierarchy.hierarchy_problems(spec, "hier_dual_rc")
    assert {"/parts/0", "/parts/1/sheet_id"} <= {problem["field"] for problem in problems}


@pytest.mark.parametrize("case", ["hierarchy", "single-page"])
def test_valid_acceptance_and_single_page_declarations_remain_compatible(case):
    spec = request()
    if case == "single-page":
        del spec["hierarchy"]
        for part in spec["parts"]:
            del part["sheet_id"]
    assert not schematic.spec_problems(spec)
    assert not hierarchy.hierarchy_problems(spec, "hier_dual_rc")
    planned = hierarchy.hierarchy_plan(spec, "hier_dual_rc")
    if case == "single-page":
        assert planned is None
    else:
        assert planned == hierarchy.hierarchy_plan(deepcopy(spec), "hier_dual_rc")
        assert planned["output_names"] == [
            "hier_dual_rc.kicad_sch",
            "channel_a.kicad_sch",
            "channel_b.kicad_sch",
            "hier_dual_rc.net",
        ]
