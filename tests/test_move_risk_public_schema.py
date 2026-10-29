"""Exact output/Schema agreement for the finite report-tree proof; no subprocesses."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from test_move_unrouted_reselection import compare, pad_id, report, saved_board

from scripts.public_surface import inventory

ROOT = Path(__file__).resolve().parents[1]
URI = "kicad-cli://schemas/move-preflight/v1"
PROVED_FIELDS = {
    "status",
    "kind",
    "official_native",
    "source",
    "kicad_version",
    "no_routed_or_other_copper",
    "same_physical_pad_identities_local_geometry_and_nets",
    "all_pad_copper_bounds_strictly_disjoint_per_board",
    "bounding_rectangle_units",
    "board_sha256",
    "isolated_physical_pads",
    "complete_report_trees",
    "reselected_edges",
    "limit",
}
COMPARISON_FIELDS = {
    "status",
    "source",
    "new_findings",
    "removed_findings",
    "retained",
    "counts",
    "connection_changes",
    "moved_footprints",
    "ignored_checks",
    "included_severities",
    "not_checked",
    "comparison_limits",
    "_untrusted",
}
CONNECTION_FIELDS = {
    "reported_connection_edges",
    "counts",
    "equivalent_anchor_replacements",
    "equivalent_anchor_replacement_count",
    "isolated_pad_tree_reselection",
    "blocking_new",
    "blocking_new_count",
    "basis",
    "complete_connectivity_graph",
    "anchors_may_be_reselected",
    "interpretation",
}


def schema():
    declaration = json.loads((ROOT / "kicad_cli/driver_contract.json").read_bytes())
    return declaration["reference"]["json_schemas"][URI]["properties"]["comparison"]["properties"][
        "connection_changes"
    ]["properties"]["isolated_pad_tree_reselection"]


def actual_proof(tmp_path, status="proved"):
    before = saved_board(tmp_path, "before")
    after = saved_board(tmp_path, "after", moved=True)
    old, new = report(before), report(after, after=True)
    if status == "not_needed":
        new = report(after)
    elif status == "not_applicable":
        # A footprint rotation is outside this finite proof, even if reports exist.
        after = saved_board(
            tmp_path,
            "unsupported",
            moved=True,
            edit=lambda text: text.replace("(at 35 20 0)", "(at 35 20 90)"),
        )
        new = report(after, after=True)
    result = compare(before, after, old, new)
    proof = result["connection_changes"]["isolated_pad_tree_reselection"]
    assert proof["status"] == status
    return proof


def actual_comparison(tmp_path):
    before = saved_board(tmp_path, "before")
    after = saved_board(tmp_path, "after", moved=True)
    return compare(before, after)


def comparison_schema():
    return inventory()["json_schemas"][URI]["properties"]["comparison"]


def test_actual_comparison_and_connection_objects_are_declared_field_for_field(tmp_path):
    actual = actual_comparison(tmp_path)
    document = comparison_schema()
    Draft202012Validator(document).validate(actual)
    assert (
        set(actual) == set(document["properties"]) == set(document["required"]) == COMPARISON_FIELDS
    )
    connection = document["properties"]["connection_changes"]
    assert (
        set(actual["connection_changes"])
        == set(connection["properties"])
        == set(connection["required"])
        == CONNECTION_FIELDS
    )
    assert document["additionalProperties"] is False
    assert connection["additionalProperties"] is False


@pytest.mark.parametrize("field", sorted(COMPARISON_FIELDS))
def test_public_comparison_cannot_omit_an_actual_output_field(tmp_path, field):
    actual = actual_comparison(tmp_path)
    del actual[field]
    assert list(Draft202012Validator(comparison_schema()).iter_errors(actual))


@pytest.mark.parametrize("field", sorted(CONNECTION_FIELDS))
def test_public_connection_change_cannot_omit_its_proof_or_other_actual_field(tmp_path, field):
    actual = actual_comparison(tmp_path)
    del actual["connection_changes"][field]
    assert list(Draft202012Validator(comparison_schema()).iter_errors(actual))


@pytest.mark.parametrize("status", ["proved", "not_needed", "not_applicable"])
def test_actual_report_tree_proof_has_exact_public_schema_fields(tmp_path, status):
    proof = actual_proof(tmp_path, status)
    document = schema()
    Draft202012Validator.check_schema(document)
    Draft202012Validator(document).validate(proof)
    expected = PROVED_FIELDS if status == "proved" else {"status", "reason"}
    branch = next(s for s in document["oneOf"] if s["properties"]["status"]["const"] == status)
    assert set(proof) == set(branch["properties"]) == set(branch["required"]) == expected
    assert branch["additionalProperties"] is False
    generated = inventory()["json_schemas"][URI]
    assert (
        generated["properties"]["comparison"]["properties"]["connection_changes"]["properties"][
            "isolated_pad_tree_reselection"
        ]
        == document
    )


@pytest.mark.parametrize("field", sorted(PROVED_FIELDS))
def test_proved_output_cannot_omit_a_declared_field(tmp_path, field):
    proof = actual_proof(tmp_path)
    del proof[field]
    assert list(Draft202012Validator(schema()).iter_errors(proof))


@pytest.mark.parametrize(
    "defect",
    ["old-world-geometry-name", "unexpected-field", "false-local-identity", "unknown-status"],
)
def test_proved_output_rejects_misspelled_extra_or_incorrect_fields(tmp_path, defect):
    proof = copy.deepcopy(actual_proof(tmp_path))
    if defect == "old-world-geometry-name":
        proof["same_physical_pad_identities_geometry_and_nets"] = proof.pop(
            "same_physical_pad_identities_local_geometry_and_nets"
        )
    elif defect == "unexpected-field":
        proof["unknown"] = True
    elif defect == "false-local-identity":
        proof["same_physical_pad_identities_local_geometry_and_nets"] = False
    else:
        proof["status"] = "complete-graph"
    assert list(Draft202012Validator(schema()).iter_errors(proof))


def test_local_pad_geometry_proof_allows_requested_world_translation(tmp_path):
    proof = actual_proof(tmp_path)
    assert proof["same_physical_pad_identities_local_geometry_and_nets"] is True
    old = proof["isolated_physical_pads"]["baseline"][pad_id(3)]
    new = proof["isolated_physical_pads"]["candidate"][pad_id(3)]
    assert old["world_nm"] != new["world_nm"]
    assert {
        k: v for k, v in old.items() if k not in {"world_nm", "bounding_rectangle_twice_nm"}
    } == {k: v for k, v in new.items() if k not in {"world_nm", "bounding_rectangle_twice_nm"}}
    Draft202012Validator(schema()).validate(proof)
