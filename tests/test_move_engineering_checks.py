"""Host-only negatives for the finite independent move consumer method."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from bench import move_engineering_acceptance as acceptance
from bench.move_engineering_checks import (
    INTENT,
    PADS,
    R1_UUID,
    SCH_PATH,
    check_baseline_board,
    check_native_baseline,
    check_public_readback,
    check_receipts,
    compare_board,
    compare_drc,
    compare_native,
    decimal_nm,
)
from bench.saved_scene_checks import sha

BASELINE = Path(__file__).parent / "fixtures/sch_edit/edit.kicad_pcb"


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def moved(tmp_path):
    path = tmp_path / "moved.kicad_pcb"
    text = BASELINE.read_text(encoding="utf-8")
    assert text.count("(at 100 100)") == 1
    path.write_text(text.replace("(at 100 100)", "(at 98.5 100)", 1), encoding="utf-8")
    return path


def test_complete_saved_board_accepts_only_the_frozen_translation(tmp_path):
    actual = moved(tmp_path)
    assert check_baseline_board(BASELINE)["counts"] == {
        "footprints": 12,
        "tracks": 3,
        "vias": 2,
        "zones": 0,
        "board_edges": 4,
    }
    assert compare_board(BASELINE, actual)["status"] == "passed"


@pytest.mark.parametrize(
    "old,new",
    [
        ("(at 98.5 100)", "(at 98.500001 100)"),
        ("(at 98.5 100)", "(at 98.5 100.000001)"),
        ("(at 98.5 100)", "(at 98.5 100 1)"),
        ('(layer "F.Cu")', '(layer "B.Cu")'),
        ('(property "Value" "10k"', '(property "Value" "22k"'),
        ('(path "' + SCH_PATH + '")', '(path "/wrong")'),
        ('(uuid "' + R1_UUID + '")', '(uuid "wrong")'),
        ("(at 98.5 100)", "locked (at 98.5 100)"),
        ("(width 0.25)", "(width 0.26)"),
        ('(net "/SIG")', '(net "/OTHER")'),
    ],
)
def test_complete_board_rejects_protected_or_wrong_position_changes(tmp_path, old, new):
    actual = moved(tmp_path)
    text = actual.read_text(encoding="utf-8")
    assert old in text
    actual.write_text(text.replace(old, new, 1), encoding="utf-8")
    with pytest.raises(ValueError):
        compare_board(BASELINE, actual)


@pytest.mark.parametrize(
    "text,wanted", [("98.5", 98500000), ("100", 100000000), ("1e-6", 1), ("-0.000001", -1)]
)
def test_decimal_units_are_exact_nanometres(text, wanted):
    assert decimal_nm(text) == wanted


@pytest.mark.parametrize("text", ["nan", "inf", "-inf", "bad", "98.5000001"])
def test_decimal_refuses_nonfinite_or_sub_nanometre_input(text):
    with pytest.raises(ValueError):
        decimal_nm(text)


def native(path):
    selected = {
        "ref": "R1",
        "uuid": R1_UUID,
        "path": SCH_PATH,
        "value": "10k",
        "fields": {"Reference": "R1", "Value": "10k"},
        "dnp": False,
        "fpid": "fixture:R_0805",
        "position_nm": [100000000, 100000000],
        "rotation_deg": 0.0,
        "layer": "F.Cu",
        "locked": False,
        "pads": [
            {
                "number": number,
                "uuid": identity,
                "net": net,
                "position_nm": [xy[0] + 1500000, xy[1]],
                "rotation_deg": 0.0,
            }
            for number, (identity, net, xy) in PADS.items()
        ],
    }
    other = []
    for i in range(11):
        fp = copy.deepcopy(selected)
        fp.update(
            ref="other-" + str(i),
            uuid="other-" + str(i),
            path="/other-" + str(i),
            position_nm=[i, i],
            pads=[],
        )
        other.append(fp)
    return {
        "native_version": "10.0.6",
        "input_unchanged": True,
        "input_sha256": sha(path),
        "reader_sha256": "reader",
        "pcbnew_wrapper_sha256": "wrapper",
        "footprints": [selected, *other],
        "tracks": [{"uuid": str(i), "start_nm": [i, i], "end_nm": [i, i + 1]} for i in range(5)],
        "zones": [],
        "drawings": [["PCB_SHAPE", str(i)] for i in range(4)],
        "startup": {"bound": True},
        "scope": "synthetic native observation; separate complete structure protection",
    }


def native_pair(tmp_path):
    actual = moved(tmp_path)
    before = native(BASELINE)
    after = copy.deepcopy(before)
    after["input_sha256"] = sha(actual)
    after["footprints"][0]["position_nm"] = INTENT["after_position_nm"]
    for pad in after["footprints"][0]["pads"]:
        pad["position_nm"] = PADS[pad["number"]][2]
    return actual, before, after


def test_native_readback_checks_world_pad_geometry_and_every_other_observed_property(tmp_path):
    actual, before, after = native_pair(tmp_path)
    assert check_native_baseline(before, BASELINE)["status"] == "passed"
    assert compare_native(before, after, BASELINE, actual)["status"] == "passed"


@pytest.mark.parametrize(
    "defect",
    [
        "input",
        "pad_position",
        "pad_net",
        "pad_uuid",
        "angle",
        "layer",
        "locked",
        "other_footprint",
        "track",
        "drawings",
        "source_hash",
        "version",
    ],
)
def test_native_comparison_rejects_every_protected_observation(tmp_path, defect):
    actual, before, after = native_pair(tmp_path)
    fp = after["footprints"][0]
    if defect == "input":
        after["input_sha256"] = "wrong"
    elif defect == "pad_position":
        fp["pads"][0]["position_nm"] = [0, 0]
    elif defect == "pad_net":
        fp["pads"][0]["net"] = "/OTHER"
    elif defect == "pad_uuid":
        fp["pads"][0]["uuid"] = "wrong"
    elif defect == "angle":
        fp["rotation_deg"] = 90.0
    elif defect == "layer":
        fp["layer"] = "B.Cu"
    elif defect == "locked":
        fp["locked"] = True
    elif defect == "other_footprint":
        after["footprints"][1]["position_nm"] = [0, 100]
    elif defect == "track":
        after["tracks"][0]["end_nm"] = [1, 2]
    elif defect == "drawings":
        after["drawings"][0][1] = "other"
    elif defect == "source_hash":
        after["reader_sha256"] = "different-method"
    elif defect == "version":
        after["native_version"] = "11.0.0"
    with pytest.raises(ValueError):
        compare_native(before, after, BASELINE, actual)


def drc():
    return {
        "coordinate_units": "mm",
        "kicad_version": "10.0.6",
        "date": "retained",
        "violations": [],
        "unconnected_items": [{"uuid": str(i)} for i in range(7)],
        "schematic_parity": [],
        "ignored_checks": [{"key": str(i)} for i in range(5)],
        "included_severities": ["error", "warning", "exclusion"],
    }


def test_drc_records_connection_regression_without_claiming_routing_pass():
    before, after = drc(), drc()
    after["unconnected_items"].append({"uuid": PADS["1"][0]})
    result = compare_drc(before, after)
    assert result["before"]["unconnected_items"] == 7
    assert result["after"]["unconnected_items"] == 8
    assert result["new_unconnected_count_delta"] == 1
    assert "no clean-DRC" in result["status"]


@pytest.mark.parametrize(
    "defect",
    [
        "ignored_added",
        "ignored_replaced",
        "ignored_deleted",
        "severity",
        "parity",
        "unit",
        "baseline_count",
    ],
)
def test_drc_cannot_hide_rule_ignore_or_parity_drift(defect):
    before, after = drc(), drc()
    if defect == "ignored_added":
        after["ignored_checks"].append({"key": "new"})
    elif defect == "ignored_replaced":
        after["ignored_checks"][0] = {"key": "new"}
    elif defect == "ignored_deleted":
        after["ignored_checks"].pop()
    elif defect == "severity":
        after["included_severities"] = ["warning"]
    elif defect == "parity":
        after["schematic_parity"] = [{"type": "mismatch"}]
    elif defect == "unit":
        after["coordinate_units"] = "in"
    elif defect == "baseline_count":
        before["unconnected_items"].pop()
    with pytest.raises(ValueError):
        compare_drc(before, after)


def test_exact_consumer_and_full_image_sets_are_predeclared(tmp_path):
    config = {
        "output": str(tmp_path),
        "native_home": "/native",
        "repository": "/method",
        "timeout_seconds": 60,
    }
    assert [
        row["id"] for phase in ("before", "after") for row in acceptance.calls(config, phase)
    ] == [
        phase + "-" + kind
        for phase in ("before", "after")
        for kind in ("sdk", "drc", "front-svg", "back-svg")
    ]
    assert sum(len(acceptance.images(config, phase)) for phase in ("before", "after")) == 4
    assert acceptance.output_names() == [
        "back.png",
        "back.svg",
        "drc.json",
        "front.png",
        "front.svg",
    ]
    for phase in ("before", "after"):
        sdk = acceptance.calls(config, phase)[0]
        assert sdk["argv"][1:3] == ["-B", "-S"]
        assert "saved_scene_native_read.py" in sdk["argv"][3]


def config_json(tmp_path):
    return put(
        tmp_path / "configuration/10.0/kicad_common.json",
        {
            "system": {"working_dir": str(tmp_path / "before")},
            "rules": {"clearance": 1},
            "ignore": [],
        },
    )


def test_only_predeclared_consumer_working_dir_is_canonical_metadata(tmp_path):
    path = config_json(tmp_path)
    original = acceptance.config_identity(tmp_path)
    value = json.loads(path.read_bytes())
    value["system"]["working_dir"] = str(tmp_path / "after")
    put(path, value)
    assert acceptance.config_identity(tmp_path) == original
    value["rules"]["clearance"] = 0
    put(path, value)
    assert acceptance.config_identity(tmp_path) != original


def test_outside_working_directory_is_rejected(tmp_path):
    path = config_json(tmp_path)
    value = json.loads(path.read_bytes())
    value["system"]["working_dir"] = str(tmp_path / "other")
    put(path, value)
    with pytest.raises(ValueError, match="predeclared"):
        acceptance.config_identity(tmp_path)


@pytest.mark.parametrize("defect", ["addition", "deletion", "replacement", "rule_mutation"])
def test_preparation_preferences_have_limited_additions_and_no_rule_mutation(tmp_path, defect):
    path = config_json(tmp_path)
    before = acceptance.config_values(tmp_path)
    if defect == "addition":
        put(tmp_path / "configuration/10.0/unknown.json", {})
    elif defect == "deletion":
        path.unlink()
    elif defect == "replacement":
        path.write_text("[]", encoding="utf-8")
    elif defect == "rule_mutation":
        value = json.loads(path.read_bytes())
        value["rules"]["clearance"] = 0
        put(path, value)
    with pytest.raises(ValueError):
        acceptance.validate_config_transition(tmp_path, before)


def parity_readback(board):
    for name, content in (
        ("edit.kicad_pro", "{}"),
        ("edit.kicad_sch", "saved root schematic"),
        ("edit_child.kicad_sch", "saved child schematic"),
    ):
        (board.parent / name).write_text(content, encoding="utf-8")
    paths = [
        board,
        board.with_suffix(".kicad_pro"),
        board.with_suffix(".kicad_dru"),
        board.with_suffix(".kicad_sch"),
        board.parent / "edit_child.kicad_sch",
    ]
    manifest = [
        {
            "path": str(path.resolve()),
            "resolved": str(path.resolve()),
            "present": path.is_file(),
            "sha256": sha(path) if path.is_file() else None,
        }
        for path in sorted(paths)
    ]
    fp = {
        "id": R1_UUID,
        "ref": "R1",
        "path": SCH_PATH,
        "value": "10k",
        "fpid": "fixture:R_0805",
        "layer": "F.Cu",
        "position_nm": [98500000, 100000000],
        "rotation_deg": 0.0,
        "fields": {"Reference": "R1", "Value": "10k", "Datasheet": "", "Description": ""},
        "attributes": ["smd"],
        "pads": [
            {
                "id": identity,
                "number": number,
                "net": net,
                "kind": "smd",
                "shape": "roundrect",
                "position_nm": [xy[0] - 98500000, xy[1] - 100000000],
                "rotation_deg": 0.0,
                "geometry_sha256": "0" * 64,
            }
            for number, (identity, net, xy) in PADS.items()
        ],
        "geometry_sha256": "1" * 64,
        "property_geometry_sha256": {},
        "saved_semantics_sha256": "2" * 64,
    }
    return {
        "board": str(board.resolve()),
        "observed_footprints": [
            fp,
            *[{"id": "other-" + str(i), "ref": "other-" + str(i)} for i in range(11)],
        ],
        "observation": {
            "source": {
                "version": "2.0.0",
                "code_sha256": "0" * 64,
                "origin": "kicad_cli.saved-project-comparison",
                "completed": True,
                "completed_at": "2026-10-16T00:00:00Z",
                "official_native": False,
            },
            "inputs": manifest,
            "snapshot": {
                "revision": hashlib.sha256(
                    json.dumps(manifest, sort_keys=True).encode()
                ).hexdigest(),
                "basis": "complete captured input manifest",
                "observed_at": "2026-10-16T00:00:00Z",
            },
            "coverage": {
                "native": False,
                "checked": ["saved component/value/footprint"],
                "not_checked": ["Circuit/layout/manufacturing correctness"],
            },
            "state": "saved_files",
            "input_drift_checked": True,
            "live_editor_observed": False,
        },
        "unconnected": 8,
    }


def receipt_files(tmp_path):
    board = tmp_path / "engineer/edit.kicad_pcb"
    board.parent.mkdir()
    board.write_bytes(moved(tmp_path).read_bytes())
    target = {"locator": board.resolve().as_uri(), "type": "board"}
    plan = {
        "operation_id": "operation",
        "plan_id": "plan",
        "target": target,
        "base_snapshot": {"revision": "before"},
        "changes": [
            {"id": R1_UUID, "action": "translate", "after": {"position_mm": [98.5, 100.0]}}
        ],
        "extensions": {"engineering_feedback": {"geometry_check": "not_performed"}},
    }
    receipt = {
        "operation_id": "operation",
        "plan_id": "plan",
        "target": target,
        "snapshot_before": {"revision": "before"},
        "snapshot_after": {"revision": sha(board)},
        "outcome": {
            "verification": {"status": "passed"},
            "persistence": "saved",
            "execution": "completed",
            "change": "applied",
        },
        "extensions": {"failures": []},
    }
    files = {name: tmp_path / (name + ".json") for name in ("preview", "confirm", "readback")}
    put(files["preview"], {"ok": True, "data": {"preview": plan}})
    put(files["confirm"], {"ok": True, "data": {"receipt": receipt}})
    put(files["readback"], {"ok": True, "data": parity_readback(board)})
    return board, files


def test_public_receipt_chain_is_bound_but_does_not_replace_independent_geometry(tmp_path):
    board, files = receipt_files(tmp_path)
    result = check_receipts(files, "before", sha(board), board)
    assert result["status"] == "passed"
    assert result["public_readback"]["actual_board_sha256"] == sha(board)
    assert result["public_readback"]["R1_pad_local_to_world_nm"] == {
        number: value[2] for number, value in PADS.items()
    }
    assert "independent complete file and native checks required" in result["independent_truth"]


@pytest.mark.parametrize(
    "defect", ["old_sha", "new_sha", "plan_id", "target", "move", "geometry_claim", "failure"]
)
def test_receipt_chain_drift_is_rejected(tmp_path, defect):
    board, files = receipt_files(tmp_path)
    preview, confirm = (
        json.loads(files["preview"].read_bytes()),
        json.loads(files["confirm"].read_bytes()),
    )
    p, r = preview["data"]["preview"], confirm["data"]["receipt"]
    if defect == "old_sha":
        r["snapshot_before"]["revision"] = "other"
    elif defect == "new_sha":
        r["snapshot_after"]["revision"] = "other"
    elif defect == "plan_id":
        r["plan_id"] = "other"
    elif defect == "target":
        p["target"]["locator"] = "file:///wrong"
    elif defect == "move":
        p["changes"][0]["after"]["position_mm"] = [99, 100]
    elif defect == "geometry_claim":
        p["extensions"]["engineering_feedback"]["geometry_check"] = "passed"
    elif defect == "failure":
        r["extensions"]["failures"] = [{"criterion": "different"}]
    put(files["preview"], preview)
    put(files["confirm"], confirm)
    with pytest.raises(ValueError):
        check_receipts(files, "before", sha(board), board)


@pytest.mark.parametrize(
    "defect",
    [
        "snapshot_revision",
        "snapshot_pcb_hash",
        "board_input_hash",
        "input_removed",
        "input_added",
        "input_duplicate",
        "input_path",
        "input_resolved",
        "dru_present",
        "schematic_modified",
        "root_board",
        "source_incomplete",
        "native_claim",
        "state",
        "drift_unchecked",
        "duplicate_R1",
        "wrong_id",
        "wrong_position",
        "wrong_layer",
        "wrong_angle",
        "wrong_path",
        "wrong_field",
        "wrong_pad_id",
        "wrong_pad_net",
        "world_instead_of_local",
        "wrong_pad_rotation",
    ],
)
def test_real_public_parity_readback_rejects_stale_or_wrong_saved_observation(tmp_path, defect):
    board, files = receipt_files(tmp_path)
    data = json.loads(files["readback"].read_bytes())["data"]
    obs, fp = data["observation"], data["observed_footprints"][0]
    manifest = obs["inputs"]
    board_row = next(row for row in manifest if Path(row["path"]) == board.resolve())
    if defect == "snapshot_revision":
        obs["snapshot"]["revision"] = "wrong"
    elif defect == "snapshot_pcb_hash":
        obs["snapshot"]["revision"] = sha(board)
    elif defect == "board_input_hash":
        board_row["sha256"] = "0" * 64
    elif defect == "input_removed":
        manifest.pop()
    elif defect == "input_added":
        manifest.append(copy.deepcopy(manifest[0]))
    elif defect == "input_duplicate":
        manifest[-1] = copy.deepcopy(manifest[0])
    elif defect == "input_path":
        board_row["path"] = str(board.parent / "other.kicad_pcb")
    elif defect == "input_resolved":
        board_row["resolved"] = str(board.parent / "other.kicad_pcb")
    elif defect == "dru_present":
        next(row for row in manifest if Path(row["path"]).suffix == ".kicad_dru")["present"] = True
    elif defect == "schematic_modified":
        board.with_suffix(".kicad_sch").write_text("changed", encoding="utf-8")
    elif defect == "root_board":
        data["board"] = str(board.parent / "other.kicad_pcb")
    elif defect == "source_incomplete":
        obs["source"]["completed"] = False
    elif defect == "native_claim":
        obs["coverage"]["native"] = True
    elif defect == "state":
        obs["state"] = "live_editor"
    elif defect == "drift_unchecked":
        obs["input_drift_checked"] = False
    elif defect == "duplicate_R1":
        data["observed_footprints"][1] = copy.deepcopy(fp)
    elif defect == "wrong_id":
        fp["id"] = "wrong"
    elif defect == "wrong_position":
        fp["position_nm"][0] += 1
    elif defect == "wrong_layer":
        fp["layer"] = "B.Cu"
    elif defect == "wrong_angle":
        fp["rotation_deg"] = 90
    elif defect == "wrong_path":
        fp["path"] = "/wrong"
    elif defect == "wrong_field":
        fp["fields"]["Value"] = "22k"
    elif defect == "wrong_pad_id":
        fp["pads"][0]["id"] = "wrong"
    elif defect == "wrong_pad_net":
        fp["pads"][0]["net"] = "/wrong"
    elif defect == "world_instead_of_local":
        fp["pads"][0]["position_nm"] = PADS[fp["pads"][0]["number"]][2]
    elif defect == "wrong_pad_rotation":
        fp["pads"][0]["rotation_deg"] = 90
    if defect not in {"snapshot_revision", "snapshot_pcb_hash"}:
        obs["snapshot"]["revision"] = hashlib.sha256(
            json.dumps(manifest, sort_keys=True).encode()
        ).hexdigest()
    with pytest.raises(ValueError):
        check_public_readback(data, sha(board), board)


@pytest.mark.parametrize("present", [False, True])
def test_preparation_explicitly_binds_optional_metadata_absence_or_regular_bytes(tmp_path, present):
    for name in acceptance.PROJECTS:
        (tmp_path / name).mkdir()
    metadata = tmp_path / "preparation-project/edit.kicad_prl"
    if present:
        metadata.write_text('{"prepared": true}', encoding="utf-8")
    result = acceptance.stage_prepared_metadata(tmp_path)
    assert result["present"] is present
    assert result["sha256"] == (sha(metadata) if present else None)
    assert result["consumer_file_count"] == (9 if present else 8)
    for name in ("before", "after"):
        target = tmp_path / name / metadata.name
        assert target.exists() is present
        if present:
            assert target.read_bytes() == metadata.read_bytes()


def test_preparation_rejects_metadata_replaced_with_directory(tmp_path):
    for name in acceptance.PROJECTS:
        (tmp_path / name).mkdir()
    (tmp_path / "preparation-project/edit.kicad_prl").mkdir()
    with pytest.raises(ValueError, match="regular"):
        acceptance.stage_prepared_metadata(tmp_path)


def admission_plan(tmp_path, monkeypatch, *, metadata=True):
    """Build a no-process plan with intentionally tiny declared dependencies."""
    root = tmp_path / "cycle"
    root.mkdir()
    config_json(root)
    source, engineer = tmp_path / "source", tmp_path / "engineer"
    for project in (source, engineer, *[root / name for name in acceptance.PROJECTS]):
        project.mkdir(exist_ok=True)
        (project / "edit.kicad_pcb").write_text("original board", encoding="utf-8")
    if metadata:
        for project in (root / name for name in acceptance.PROJECTS):
            (project / "edit.kicad_prl").write_text("prepared metadata", encoding="utf-8")
    config = {
        "output": str(root),
        "source": str(source),
        "source_files": acceptance.tree(source),
        "engineer_project": str(engineer),
        "engineer_call_files": {
            name: str(tmp_path / (name + ".stdout")) for name in ("preview", "confirm", "readback")
        },
        "native_home": "/native",
        "repository": "/method",
        "timeout_seconds": 60,
    }
    method = tmp_path / "method.py"
    method.write_text("bound implementation", encoding="utf-8")
    monkeypatch.setattr(
        acceptance,
        "identity",
        lambda c: {"config": c, "source": acceptance.tree(c["source"]), "method": sha(method)},
    )
    (root / "preparation-output").mkdir()
    (root / "preparation-calls").mkdir()
    initial = put(root / "INPUT-FROZEN.json", {"schema_version": 1})
    preparation = put(
        root / "PREPARATION.json", {"preparation_outputs": {}, "preparation_call_evidence": {}}
    )
    plan = put(
        root / "FROZEN.json",
        {
            "binding": acceptance.identity(config),
            "configuration": acceptance.config_identity(root),
            "intent": INTENT,
            "native_consumers": {
                phase: acceptance.calls(config, phase) for phase in ("before", "after")
            },
            "images": {phase: acceptance.images(config, phase) for phase in ("before", "after")},
            "complete_output_names": acceptance.output_names(),
            "input_sha256": sha(initial),
            "preparation_sha256": sha(preparation),
            "engineering": {name: acceptance.tree(root / name) for name in acceptance.PROJECTS},
            "project_metadata": acceptance.metadata_identity(root),
        },
    )
    return root, config, method, plan, sha(plan)


@pytest.mark.parametrize(
    "defect",
    [
        "plan",
        "method",
        "source",
        "baseline",
        "after_template",
        "engineer",
        "configuration_added",
        "configuration_deleted",
        "configuration_replaced",
        "configuration_rule",
        "prepared_output",
        "prepared_call",
        "premature_public_output",
    ],
)
def test_formal_admission_rejects_drift_before_any_consumer_process(tmp_path, monkeypatch, defect):
    root, config, method, plan, digest = admission_plan(tmp_path, monkeypatch)
    called = []
    monkeypatch.setattr(acceptance, "record_call", lambda *args: called.append(args))
    if defect == "plan":
        plan.write_text(plan.read_text() + " ", encoding="utf-8")
    elif defect == "method":
        method.write_text("replacement", encoding="utf-8")
    elif defect == "source":
        (Path(config["source"]) / "new.py").write_text("new input", encoding="utf-8")
    elif defect == "baseline":
        (root / "before/edit.kicad_pcb").write_text("changed", encoding="utf-8")
    elif defect == "after_template":
        (root / "after/edit.kicad_pcb").write_text("changed", encoding="utf-8")
    elif defect == "engineer":
        (Path(config["engineer_project"]) / "edit.kicad_pcb").write_text(
            "changed", encoding="utf-8"
        )
    elif defect == "configuration_added":
        put(root / "configuration/new.json", {})
    elif defect == "configuration_deleted":
        (root / "configuration/10.0/kicad_common.json").unlink()
    elif defect == "configuration_replaced":
        (root / "configuration/10.0/kicad_common.json").write_text("{}", encoding="utf-8")
    elif defect == "configuration_rule":
        path = root / "configuration/10.0/kicad_common.json"
        value = json.loads(path.read_bytes())
        value["rules"]["clearance"] = 0
        put(path, value)
    elif defect == "prepared_output":
        (root / "preparation-output/unbound.svg").write_text("new", encoding="utf-8")
    elif defect == "prepared_call":
        (root / "preparation-calls/new.log").write_text("new", encoding="utf-8")
    elif defect == "premature_public_output":
        put(Path(config["engineer_call_files"]["confirm"]), {"ok": True})
    with pytest.raises(ValueError):
        acceptance.run_consumers(config, "before", plan, digest)
    assert called == []
    assert not (root / "BEFORE-START.json").exists()


def test_original_plan_admission_allows_only_declared_working_directory_metadata(
    tmp_path, monkeypatch
):
    root, config, _, plan, digest = admission_plan(tmp_path, monkeypatch)
    path = root / "configuration/10.0/kicad_common.json"
    value = json.loads(path.read_bytes())
    value["system"]["working_dir"] = str(root / "after")
    put(path, value)
    assert acceptance.verify_plan(config, plan, digest)["intent"] == INTENT


@pytest.mark.parametrize(
    "defect",
    ["json", "missing_field", "wrong_input", "missing_footprint", "wrong_pad", "wrong_version"],
)
def test_bad_native_sdk_output_stops_before_later_native_consumers(tmp_path, monkeypatch, defect):
    root, config, _, plan, digest = admission_plan(tmp_path, monkeypatch)
    called = []

    def fake_call(config, row, directory):
        called.append(row["id"])
        directory.mkdir()
        result = native(root / "before/edit.kicad_pcb")
        if defect == "json":
            (directory / "stdout").write_text("{", encoding="utf-8")
            return
        if defect == "missing_field":
            result.pop("input_sha256")
        elif defect == "wrong_input":
            result["input_sha256"] = "wrong"
        elif defect == "missing_footprint":
            result["footprints"].pop()
        elif defect == "wrong_pad":
            result["footprints"][0]["pads"][0]["position_nm"] = [0, 0]
        elif defect == "wrong_version":
            result["native_version"] = "11.0.0"
        put(directory / "stdout", result)

    monkeypatch.setattr(acceptance, "record_call", fake_call)
    with pytest.raises((ValueError, KeyError)):
        acceptance.run_consumers(config, "before", plan, digest)
    assert called == ["before-sdk"]
    assert not (root / "before-calls/before-drc").exists()


@pytest.mark.parametrize(
    "present,defect",
    [
        (True, "deleted"),
        (True, "modified"),
        (True, "directory"),
        (False, "added"),
        (False, "directory"),
    ],
)
def test_frozen_metadata_presence_and_bytes_cannot_drift_before_native(
    tmp_path, monkeypatch, present, defect
):
    root, config, _, plan, digest = admission_plan(tmp_path, monkeypatch, metadata=present)
    path = root / "before/edit.kicad_prl"
    called = []
    monkeypatch.setattr(acceptance, "record_call", lambda *args: called.append(args))
    if defect == "deleted":
        path.unlink()
    elif defect == "modified":
        path.write_text("other metadata", encoding="utf-8")
    elif defect == "added":
        path.write_text("new metadata", encoding="utf-8")
    elif defect == "directory":
        if path.exists():
            path.unlink()
        path.mkdir()
    with pytest.raises(ValueError):
        acceptance.run_consumers(config, "before", plan, digest)
    assert called == []
    assert not (root / "BEFORE-START.json").exists()


@pytest.mark.parametrize("defect", ["json", "schema", "parity"])
def test_bad_official_drc_stops_before_following_svg_consumers(tmp_path, monkeypatch, defect):
    root, config, _, plan, digest = admission_plan(tmp_path, monkeypatch)
    called = []

    def fake_call(config, row, directory):
        called.append(row["id"])
        directory.mkdir()
        if row["kind"] == "sdk":
            put(directory / "stdout", native(root / "before/edit.kicad_pcb"))
            return
        report = drc()
        if defect == "schema":
            report.pop("unconnected_items")
        elif defect == "parity":
            report["schematic_parity"] = [{"type": "mismatch"}]
        path = root / "before-output/drc.json"
        if defect == "json":
            path.write_text("{", encoding="utf-8")
        else:
            put(path, report)

    monkeypatch.setattr(acceptance, "record_call", fake_call)
    with pytest.raises(ValueError):
        acceptance.run_consumers(config, "before", plan, digest)
    assert called == ["before-sdk", "before-drc"]
    assert not (root / "before-calls/before-front-svg").exists()
