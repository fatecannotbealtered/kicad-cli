"""Adversarial acceptance tests; synthetic witnesses are not native engineering runs."""

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from bench.persistence_acceptance.evidence import project_refill
from bench.persistence_acceptance.f5 import verdict
from bench.persistence_acceptance.refill_binding import tree

REPO = Path(__file__).resolve().parents[1]
PUBLIC = REPO / "docs/evidence/persistence-boundaries-20261013"
REQUIRED = (
    "query_verified",
    "cleanup_attributable",
    "resolve_succeeded",
    "resolution_partial",
    "resolved_query_partial",
    "blocked_before_resolution",
    "new_preview_allowed",
    "duplicate_resolution_refused",
    "ordered_calls",
    "formal_files_preserved",
    "protected_files_preserved",
    "final_file_set_exact",
    "baselines_archived",
    "confirmation_not_replayed",
)


def partial_facts():
    return dict.fromkeys(REQUIRED, True) | {
        "stage_hit": True,
        "observed_outcome": "partial",
        "resolved_top_level_status": "unknown",
    }


def test_partial_resolution_does_not_require_a_new_sdk_terminal_state():
    assert verdict(partial_facts())["status"] == "passed"


@pytest.mark.parametrize("outcome", ["unknown", "applied", "not_applied"])
def test_mixed_files_without_partial_readback_never_pass(outcome):
    facts = partial_facts() | {"observed_outcome": outcome}
    assert verdict(facts)["stage_hit"] and verdict(facts)["status"] == "failed"


@pytest.mark.parametrize("missing", REQUIRED)
def test_each_missing_recovery_observation_prevents_complete_f5(missing):
    facts = partial_facts()
    facts.pop(missing)
    assert not verdict(facts)["partial_recovery_passed"]
    assert verdict(facts)["status"] == "failed"


def test_unknown_safety_is_separate_from_partial_registration():
    facts = partial_facts() | {
        "observed_outcome": "unknown",
        "unknown_still_blocked": True,
        "resolve_succeeded": False,
        "resolution_partial": False,
        "new_preview_allowed": False,
    }
    checked = verdict(facts)
    assert checked["unknown_safety_passed"] and checked["status"] == "failed"
    assert not checked["partial_recovery_passed"]


def test_recovery_without_physical_hit_is_uncovered():
    assert verdict(partial_facts() | {"stage_hit": False})["status"] == "uncovered"


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, indent=2) + "\n").encode())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def refill_scene(tmp_path):
    root = tmp_path / "owned evidence"
    root.mkdir()
    result = json.loads((PUBLIC / "refill-changed.json").read_bytes())
    result["status"] = "automated_passed_visual_pending"
    result["visual_images"] = result["visual_review"]["images"]
    for stage, folder in (
        ("before", "original-copy"),
        ("prepared", "prepared-copy"),
        ("after", "final-files"),
    ):
        project = root / folder / "project"
        project.mkdir(parents=True)
        (project / "rc4.kicad_pcb").write_bytes(("synthetic board " + stage).encode())
        (project / "rc4.kicad_sch").write_bytes(b"protected schematic")
        (project / "rc4.kicad_pro").write_bytes(b"protected rules dependency")
        save(root / folder / "FILES.json", {"project/" + n: h for n, h in tree(project).items()})
    shutil.copytree(root / "final-files/project", root / "project")
    configuration = root / "configuration"
    configuration.mkdir()
    (configuration / "config.json").write_bytes(b"isolated configuration")
    dependency = tmp_path / "protected-dependency.bin"
    dependency.write_bytes(b"recorded external dependency")
    baseline = tree(root / "original-copy/project")
    save(
        root / "SETUP.json",
        {
            "binding": result["binding"],
            "files": baseline,
            "configuration": tree(configuration),
            "environment": {"KICAD_CLI_STATE_DIR": str(root / "state")},
        },
    )
    save(
        root / "FROZEN.json",
        {
            "binding": result["binding"],
            "files": baseline,
            "intent_sha256": "synthetic-binding-only",
            "preparation": "synthetic three-stage fixture",
        },
    )
    identities = {
        stage: sha(root / folder / "project/rc4.kicad_pcb")
        for stage, folder in (
            ("before", "original-copy"),
            ("prepared", "prepared-copy"),
            ("after", "final-files"),
        )
    }

    def call(name, value):
        stdout = root / "calls" / (name + ".stdout")
        save(stdout, value)
        save(
            root / "calls" / (name + ".call.json"),
            {
                "source_binding": result["binding"],
                "stdout_sha256": sha(stdout),
                "exit": 0,
                "execution": "completed",
                "program": [{"path": str(dependency), "present": True, "sha256": sha(dependency)}],
            },
        )

    for key, command, before, after in (
        ("preparation", "move", "before", "prepared"),
        ("refill", "refill", "prepared", "after"),
        ("repeat", "repeat-refill", "after", "after"),
    ):
        result[key].update(before=identities[before], after=identities[after])
        call(
            command + "-confirm",
            {
                "data": {
                    "receipt": {
                        "operation_id": result[key]["operation_id"],
                        "snapshot_before": {"revision": identities[before]},
                        "snapshot_after": {"revision": identities[after]},
                        "outcome": {
                            "change": result[key]["change"],
                            "persistence": result[key]["persistence"],
                        },
                    }
                }
            },
        )
    for stage, key in (("before", "baseline"), ("prepared", "prepared"), ("after", "after")):
        result[key]["board_sha256"] = identities[stage]
        save(root / stage / "SUMMARY.json", result[key])
        save(root / stage / "native-board.json", {"board_sha256": identities[stage]})
        fill = {"board_sha256": identities[stage], "zones": []}
        save(root / stage / "fill.json", fill)
        call(stage + "-native-board", {"report_sha256": sha(root / stage / "native-board.json")})
        call(stage + "-native-fill", fill)
        if stage == "after":
            result["native_fill_after"] = fill
    save(root / "visual/VISUAL-REVIEW.json", result["visual_review"])
    for side in ("front", "back"):
        shutil.copyfile(PUBLIC / ("refill-" + side + ".png"), root / "visual" / (side + ".png"))
    save(root / "RESULT.json", result)
    return root, dependency


def test_refill_export_binds_three_distinct_legal_stages_without_writing(refill_scene):
    root, _ = refill_scene
    before = tree(root)
    value = project_refill(root)
    assert value["status"] == "passed" and tree(root) == before
    assert len(set(value["final_identity_validation"]["stage_board_sha256"].values())) == 3


@pytest.mark.parametrize("name", ["board", "schematic", "configuration", "rules", "dependency"])
@pytest.mark.parametrize("mutation", ["edit", "replace", "delete"])
def test_refill_drift_refuses_passed_export_without_modifying_evidence(
    refill_scene, name, mutation
):
    root, dependency = refill_scene
    path = {
        "board": root / "project/rc4.kicad_pcb",
        "schematic": root / "project/rc4.kicad_sch",
        "configuration": root / "configuration/config.json",
        "rules": root / "project/rc4.kicad_pro",
        "dependency": dependency,
    }[name]
    if mutation == "delete":
        path.unlink()
    elif mutation == "replace":
        replacement = path.with_suffix(".replacement")
        replacement.write_bytes(b"external replacement")
        os.replace(replacement, path)
    else:
        path.write_bytes(path.read_bytes() + b" drift")
    before = tree(root.parent)
    with pytest.raises((ValueError, FileNotFoundError)):
        project_refill(root)
    assert tree(root.parent) == before


@pytest.mark.parametrize(
    "file",
    [
        "before/native-board.json",
        "prepared/fill.json",
        "after/native-board.json",
        "after/SUMMARY.json",
        "RESULT.json",
    ],
)
def test_wrong_stage_observation_is_not_final_evidence(refill_scene, file):
    root, _ = refill_scene
    path = root / file
    value = json.loads(path.read_bytes())
    if file == "RESULT.json":
        value["repeat"]["after"] = value["preparation"]["before"]
    else:
        value["board_sha256"] = "different-stage"
    save(path, value)
    before = tree(root)
    with pytest.raises(ValueError):
        project_refill(root)
    assert tree(root) == before


@pytest.mark.parametrize("change", ["extra-project-file", "extra-config-file", "lost-final-copy"])
def test_changed_file_set_or_lost_freeze_prevents_export(refill_scene, change):
    root, _ = refill_scene
    if change == "lost-final-copy":
        (root / "final-files/project/rc4.kicad_sch").unlink()
    else:
        parent = root / ("project" if change == "extra-project-file" else "configuration")
        (parent / "unexpected").write_bytes(b"external addition")
    before = tree(root)
    with pytest.raises(ValueError):
        project_refill(root)
    assert tree(root) == before
