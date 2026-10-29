"""Checker-only host cases; these records are never fresh native acceptance evidence.

Only the eight explicitly named original inputs are read. SDK dictionaries, DRC
JSON and dummy dependency files are built in pytest's temporary directory. No
native library, product command, renderer, operation state or old report is used.
"""

import copy
import hashlib
import json
import subprocess
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest

from bench import move_safety_acceptance as acceptance
from bench import move_safety_checks as checks
from bench.saved_scene_checks import children, one, sexp

REPO = Path(__file__).resolve().parents[1]
ORIGINAL = Path(
    "D:/personalSpace/kicad-method-continuation-20261016/native-scenes-cycle-01/baseline"
)
ORIGINAL_FILES = {
    "edit.kicad_pcb": "59b5c3a7f5569031c2c098c01cdee5c5be2c71aed77d33eb5992126ac7b5cb1f",
    "edit.kicad_sch": "a09670ef0296e2f8d1f04cd03d988bc51af8d8fad533ee1850bdbee0b17588b9",
    "edit_child.kicad_sch": "6715fe5edc5331bc7f057402d4880e27016c298cca21b51e485ce234c61e520c",
    "edit.kicad_pro": "1ae5eadae20d00b18fb02b6a5e60711a41631493c0850ab65858e15e788b957a",
    "fp-lib-table": "c4bf377f22a68eb34f7386058c893bed2c08fd6b6b4c6eb5cdaeacd3dc724689",
    "fixture.pretty/R_0603.kicad_mod": (
        "beca418a4b278bf5349d1cea36d8f8f099162aaa5535ef61429385ef3b65e6f0"
    ),
    "fixture.pretty/R_0805.kicad_mod": (
        "6a5cf66eaccbce89e33c19b03dfc2193449719dc4db0048cb6af41d1c0ec55cf"
    ),
    "fixture.pretty/SO8.kicad_mod": (
        "d40f7614c38d42bcb294dd3f322617e2c2c849bedebeaefedc0188b67ca6fed3"
    ),
}
POSES = {
    "negative": ("R1", "2d4aa815-63ab-5983-950d-99c5287f15a2", "98.5", -1500000),
    "positive": ("R4", "6c5beb16-fbc7-5af2-918d-da26a4b5251c", "99.5", -500000),
}
OPEN_PAIRS = [
    [
        ("4b209b2e-134d-5ded-a039-51cc9692dac5", 99.0875, 100),
        ("83ed57e3-78f7-5e55-a335-850177d80cb6", 99.0875, 120),
    ],
    [
        ("4af096ca-59e1-5018-a93c-c3aaa5cd3957", 100.9125, 100),
        ("e042066d-1387-5783-a87c-c4f2f3f2656e", 119.525, 102.095),
    ],
    [
        ("970c6eca-ebde-5240-87d3-e527b34c070e", 105.9125, 100),
        ("d6645bea-6405-5137-b186-14d1e2483ac8", 105.9125, 120),
    ],
    [
        ("d6645bea-6405-5137-b186-14d1e2483ac8", 105.9125, 120),
        ("1a9d5d34-9943-5cec-9115-1107d43b002e", 105.9125, 130),
    ],
    [
        ("cfde6d55-5dd4-5ae1-a970-624324c50f75", 100.9125, 110),
        ("4b634433-1194-5a3a-9557-242ae941f9c4", 105.9125, 110),
    ],
    [
        ("084e6c4d-63bd-5a38-a2d7-905dbd2bed6a", 100.9125, 120),
        ("460a9112-6fab-51de-9787-3baa1b76be9f", 104.0875, 120),
    ],
    [
        ("8acc67a0-70dd-51af-9dd6-b7879aaecd65", 100.9125, 130),
        ("c8b5f166-ca02-5cc5-a46d-a2f6fdc3c9e2", 104.0875, 130),
    ],
]
IGNORE_KEYS = [
    "missing_courtyard",
    "track_not_centered_on_via",
    "tuning_profile_track_geometries",
    "footprint_filters_mismatch",
    "footprint_type_mismatch",
]
CHECKER_ONLY = "CHECKER_ONLY_SYNTHETIC_NO_NATIVE_EXECUTION"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def emit(node):
    return "(" + " ".join(emit(value) if isinstance(value, list) else value for value in node) + ")"


def footprint(board, reference):
    return next(
        fp
        for fp in children(board, "footprint")
        if any(
            prop[1:3] == ['"Reference"', json.dumps(reference)] for prop in children(fp, "property")
        )
    )


def millimetres_to_nm(atoms):
    return [int(Decimal(atom) * 1000000) for atom in atoms]


@pytest.fixture(scope="module")
def original_inputs():
    if not all((ORIGINAL / name).is_file() for name in ORIGINAL_FILES):
        pytest.skip("Requires the explicitly named original eight-file owned corpus")
    values = {name: (ORIGINAL / name).read_bytes() for name in ORIGINAL_FILES}
    assert {name: hashlib.sha256(raw).hexdigest() for name, raw in values.items()} == ORIGINAL_FILES
    yield values
    assert {name: digest(ORIGINAL / name) for name in ORIGINAL_FILES} == ORIGINAL_FILES


@pytest.fixture
def checker_boards(tmp_path, original_inputs):
    paths = {}
    for scene in ("baseline", "negative", "positive"):
        path = tmp_path / ("CHECKER_ONLY_" + scene) / "edit.kicad_pcb"
        path.parent.mkdir()
        if scene == "baseline":
            path.write_bytes(original_inputs["edit.kicad_pcb"])
        else:
            board = sexp(original_inputs["edit.kicad_pcb"].decode("utf-8"))
            reference, _identity, target_x, _delta = POSES[scene]
            one(footprint(board, reference), "at")[1] = target_x
            path.write_text(emit(board), encoding="utf-8")
        paths[scene] = path
    return paths


def synthetic_startup(tmp_path):
    """Bind harmless dummy source bytes; never import or launch these files."""
    root = tmp_path / "CHECKER_ONLY_DUMMY_NATIVE" / "bin"
    site = root / "Lib/site-packages"
    site.mkdir(parents=True)
    (root / "DLLs").mkdir()
    wrapper, extension = site / "pcbnew.py", site / "_pcbnew.pyd"
    wrapper.write_text("# " + CHECKER_ONLY + "\n", encoding="utf-8")
    extension.write_bytes(CHECKER_ONLY.encode("ascii"))
    reader = REPO / "bench/saved_scene_native_read.py"
    modules = [
        {"module": "__main__", "path": str(reader), "sha256": digest(reader)},
        {"module": "pcbnew", "path": str(wrapper), "sha256": digest(wrapper)},
        {"module": "_pcbnew", "path": str(extension), "sha256": digest(extension)},
    ]
    return {
        "no_site": True,
        "dont_write_bytecode": True,
        "actual_sys_path": list(
            map(str, [root / "Lib", root / "DLLs", root / "python311.zip", site])
        ),
        "explicit_site_packages": str(site),
        "sitecustomize_or_usercustomize_loaded": False,
        "Documents_KiCad_thirdparty_consulted": False,
        "normal_site_addsitedir_executed": False,
        "native_bin_dll_directory": str(root),
        "actual_module_sources": modules,
    }


def synthetic_native(path, startup):
    """Model the host checker schema, without claiming any SDK observation occurred."""
    board = sexp(path.read_text(encoding="utf-8"))
    footprints = []
    for fp in children(board, "footprint"):
        fields = {json.loads(prop[1]): json.loads(prop[2]) for prop in children(fp, "property")}
        origin = millimetres_to_nm(one(fp, "at")[1:3])
        pads = []
        for pad in children(fp, "pad"):
            local = millimetres_to_nm(one(pad, "at")[1:3])
            pads.append(
                {
                    "uuid": json.loads(one(pad, "uuid")[1]),
                    "number": json.loads(pad[1]),
                    "net": json.loads(one(pad, "net")[1]) if children(pad, "net") else "",
                    "position_nm": [origin[axis] + local[axis] for axis in (0, 1)],
                    "size_nm": millimetres_to_nm(one(pad, "size")[1:3]),
                    "drill_nm": [0, 0],
                    "shape": 4,
                    "rotation_deg": 0.0,
                    "layers": [json.loads(layer) for layer in one(pad, "layers")[1:]],
                }
            )
        footprints.append(
            {
                "ref": fields["Reference"],
                "uuid": json.loads(one(fp, "uuid")[1]),
                "path": json.loads(one(fp, "path")[1]),
                "value": fields["Value"],
                "fields": fields,
                "dnp": any("dnp" in attr[1:] for attr in children(fp, "attr")),
                "fpid": json.loads(fp[1]),
                "position_nm": origin,
                "rotation_deg": 0.0,
                "layer": json.loads(one(fp, "layer")[1]),
                "locked": False,
                "pads": sorted(pads, key=lambda item: item["uuid"]),
            }
        )
    tracks = []
    for kind in ("segment", "via"):
        for track in children(board, kind):
            via = kind == "via"
            tracks.append(
                {
                    "uuid": json.loads(one(track, "uuid")[1]),
                    "kind": "PCB_VIA" if via else "PCB_TRACK",
                    "net": json.loads(one(track, "net")[1]),
                    "start_nm": millimetres_to_nm(one(track, "at" if via else "start")[1:3]),
                    "end_nm": millimetres_to_nm(one(track, "at" if via else "end")[1:3]),
                    "width_nm": millimetres_to_nm(one(track, "size" if via else "width")[1:2])[0],
                    "layer": "F.Cu" if via else json.loads(one(track, "layer")[1]),
                    "locked": False,
                    **({"drill_nm": millimetres_to_nm(one(track, "drill")[1:2])[0]} if via else {}),
                }
            )
    return {
        "native_version": "10.0.6",
        "input_unchanged": True,
        "input_sha256": digest(path),
        "reader_sha256": digest(REPO / "bench/saved_scene_native_read.py"),
        "pcbnew_wrapper_sha256": startup["actual_module_sources"][1]["sha256"],
        "footprints": sorted(footprints, key=lambda item: item["ref"]),
        "tracks": sorted(tracks, key=lambda item: item["uuid"]),
        "zones": [],
        "drawings": sorted(
            ["PCB_SHAPE", json.loads(one(row, "uuid")[1])] for row in children(board, "gr_line")
        ),
        "startup": copy.deepcopy(startup),
        "scope": (
            "Native semantic readback; independent complete file token comparison protects "
            "every zone/fill, stackup, setup, graphic, pad and copper property. "
            "DRC and full-image review are separate."
        ),
    }


def finding(kind, pair):
    return {
        "type": kind,
        "severity": "error",
        "description": CHECKER_ONLY,
        "items": [
            {"uuid": identity, "pos": {"x": x, "y": y}, "description": CHECKER_ONLY}
            for identity, x, y in pair
        ],
    }


def synthetic_drc(scene, path):
    pairs = copy.deepcopy(OPEN_PAIRS)
    violations = []
    if scene == "negative":
        pairs[0][0] = ("f510283f-ec3c-5db2-9e68-f71324b70ad5", 99.0875, 102)
        pairs[1][0] = ("4af096ca-59e1-5018-a93c-c3aaa5cd3957", 99.4125, 100)
        pairs.append(
            [
                ("4b209b2e-134d-5ded-a039-51cc9692dac5", 99.0875, 100),
                ("b6399d66-3ccf-5183-bd56-8dee1659878f", 97.5875, 100),
            ]
        )
        conflict = [
            ("4af096ca-59e1-5018-a93c-c3aaa5cd3957", 99.4125, 100),
            ("4b209b2e-134d-5ded-a039-51cc9692dac5", 99.0875, 100),
        ]
        violations = [finding(kind, conflict) for kind in ("shorting_items", "solder_mask_bridge")]
    elif scene == "positive":
        pairs[4][0] = ("cfde6d55-5dd4-5ae1-a970-624324c50f75", 100.4125, 110)
    return {
        "$schema": "https://schemas.kicad.org/drc.v1.json",
        "coordinate_units": "mm",
        "date": CHECKER_ONLY,
        "ignored_checks": [{"key": key, "description": CHECKER_ONLY} for key in IGNORE_KEYS],
        "included_severities": ["error", "warning", "exclusion"],
        "kicad_version": "10.0.6",
        "schematic_parity": [],
        "source": path.name,
        "violations": violations,
        "unconnected_items": [finding("unconnected_items", pair) for pair in pairs],
    }


def test_frozen_targets_and_complete_scene_structure(checker_boards):
    assert checks.EXPECTED == {
        "baseline": {
            "reference": None,
            "uuid": None,
            "from_nm": None,
            "to_nm": None,
            "violations": 0,
            "unconnected_items": 7,
        },
        "negative": {
            "reference": "R1",
            "uuid": "2d4aa815-63ab-5983-950d-99c5287f15a2",
            "from_nm": [100000000, 100000000],
            "to_nm": [98500000, 100000000],
            "violations": 2,
            "unconnected_items": 8,
        },
        "positive": {
            "reference": "R4",
            "uuid": "6c5beb16-fbc7-5af2-918d-da26a4b5251c",
            "from_nm": [100000000, 110000000],
            "to_nm": [99500000, 110000000],
            "violations": 0,
            "unconnected_items": 7,
        },
    }
    for scene, actual in checker_boards.items():
        result = checks.check_board(checker_boards["baseline"], actual, scene)
        assert result["status"] == "passed"
        assert result["inputs"]["actual"] == {
            "path": str(actual.resolve()),
            "sha256": digest(actual),
        }
        assert result["all_remaining_atoms_and_child_order"] == "identical"
        assert json.loads(json.dumps(result)) == result


@pytest.mark.parametrize(
    "defect", ["other_fp", "layer", "uuid", "pad_net", "copper", "edge", "rule"]
)
def test_complete_board_protection_rejects_each_object_category(checker_boards, defect):
    actual = checker_boards["positive"]
    board = sexp(actual.read_text(encoding="utf-8"))
    selected = footprint(board, "R4")
    if defect == "other_fp":
        one(footprint(board, "R2"), "at")[1] = "105.000001"
    elif defect == "layer":
        one(selected, "layer")[1] = '"B.Cu"'
    elif defect == "uuid":
        one(selected, "uuid")[1] = '"CHECKER_ONLY_WRONG_UUID"'
    elif defect == "pad_net":
        one(children(selected, "pad")[0], "net")[1] = '"CHECKER_ONLY_OTHER_NET"'
    elif defect == "copper":
        one(children(board, "segment")[0], "width")[1] = "0.26"
    elif defect == "edge":
        one(children(board, "gr_line")[0], "end")[1] = "128"
    else:
        one(one(board, "setup"), "pad_to_mask_clearance")[1] = "0.1"
    actual.write_text(emit(board), encoding="utf-8")
    with pytest.raises(ValueError):
        checks.check_board(checker_boards["baseline"], actual, "positive")


@pytest.mark.parametrize(
    "actual_scene,declared_scene", [("positive", "negative"), ("negative", "positive")]
)
def test_r1_and_r4_targets_cannot_be_interchanged(checker_boards, actual_scene, declared_scene):
    with pytest.raises(ValueError):
        checks.check_board(checker_boards["baseline"], checker_boards[actual_scene], declared_scene)


@pytest.mark.parametrize("scene", ["negative", "positive"])
def test_checker_only_native_models_accept_exact_world_translation(tmp_path, checker_boards, scene):
    startup = synthetic_startup(tmp_path)
    baseline, actual = checker_boards["baseline"], checker_boards[scene]
    before, after = synthetic_native(baseline, startup), synthetic_native(actual, startup)
    u1 = next(fp for fp in before["footprints"] if fp["ref"] == "U1")
    assert {pad["number"]: pad["net"] for pad in u1["pads"] if pad["number"] in {"4", "8"}} == {
        "4": "",
        "8": "",
    }
    result = checks.check_native(before, after, baseline, actual, scene)
    assert result["status"] == "passed"
    assert "identical" in result["startup_and_all_module_bindings"]
    reference, _identity, _target_x, delta = POSES[scene]
    fp_before = next(fp for fp in before["footprints"] if fp["ref"] == reference)
    fp_after = next(fp for fp in after["footprints"] if fp["ref"] == reference)
    assert fp_after["position_nm"][0] - fp_before["position_nm"][0] == delta
    assert [pad["position_nm"][0] for pad in fp_after["pads"]] == [
        pad["position_nm"][0] + delta for pad in fp_before["pads"]
    ]


@pytest.mark.parametrize(
    "defect",
    [
        "fp_rotation_bool",
        "pad_rotation_bool",
        "pad_shape_bool",
        "dnp_int",
        "coordinate_bool",
        "protected_track",
        "reader_sha",
        "dependency_source",
    ],
)
def test_native_schema_and_runtime_bindings_reject_protected_changes(
    tmp_path, checker_boards, defect
):
    startup = synthetic_startup(tmp_path)
    baseline, actual = checker_boards["baseline"], checker_boards["positive"]
    before, after = synthetic_native(baseline, startup), synthetic_native(actual, startup)
    fp = next(fp for fp in after["footprints"] if fp["ref"] == "R4")
    if defect == "fp_rotation_bool":
        fp["rotation_deg"] = False
    elif defect == "pad_rotation_bool":
        fp["pads"][0]["rotation_deg"] = False
    elif defect == "pad_shape_bool":
        fp["pads"][0]["shape"] = True
    elif defect == "dnp_int":
        fp["dnp"] = 0
    elif defect == "coordinate_bool":
        # Drill zero must remain an integer; False compares equal to zero in Python.
        fp["pads"][0]["drill_nm"][0] = False
    elif defect == "protected_track":
        after["tracks"][0]["end_nm"][0] += 1
    elif defect == "reader_sha":
        after["reader_sha256"] = "0" * 64
    else:
        Path(startup["actual_module_sources"][1]["path"]).write_text(
            "# CHECKER_ONLY_DEPENDENCY_DRIFT\n", encoding="utf-8"
        )
    with pytest.raises(ValueError):
        checks.check_native(before, after, baseline, actual, "positive")


@pytest.mark.parametrize("scene", ["baseline", "negative", "positive"])
def test_checker_only_drc_models_match_machine_relations_without_parity_claim(
    checker_boards, scene
):
    baseline, actual = checker_boards["baseline"], checker_boards[scene]
    before, after = synthetic_drc("baseline", baseline), synthetic_drc(scene, actual)
    after["date"] = "CHECKER_ONLY_DIFFERENT_DATE"
    after["unconnected_items"].reverse()
    for row in after["unconnected_items"]:
        row["description"] = "CHECKER_ONLY_TRANSLATED_DESCRIPTION"
        row["items"].reverse()
    result = checks.check_drc(before, after, baseline, actual, scene)
    assert (result["violations"], result["unconnected_items"]) == {
        "baseline": (0, 7),
        "negative": (2, 8),
        "positive": (0, 7),
    }[scene]
    assert result["schematic_parity"] == "not_executed"
    assert result["parity_pass_claim"] is False
    assert result["whole_board_compliance_claim"] is False
    assert result["ignored_checks"] == IGNORE_KEYS
    assert result["included_severities"] == ["error", "warning", "exclusion"]
    assert result["inputs"]["actual"]["sha256"] == digest(actual)


@pytest.mark.parametrize(
    "defect", ["replace_uuid", "replace_type", "nm_shift", "negative_type", "scope", "source"]
)
def test_drc_equal_counters_cannot_hide_changed_relations_or_scope(checker_boards, defect):
    scene = "negative" if defect == "negative_type" else "positive"
    baseline, actual = checker_boards["baseline"], checker_boards[scene]
    before, after = synthetic_drc("baseline", baseline), synthetic_drc(scene, actual)
    if defect == "replace_uuid":
        # Keep seven obligations while duplicating one and dropping another.
        before["unconnected_items"][0] = copy.deepcopy(before["unconnected_items"][1])
    elif defect == "replace_type":
        before["unconnected_items"][0]["type"] = "clearance"
    elif defect == "nm_shift":
        after["unconnected_items"][4]["items"][0]["pos"]["x"] = 100.412501
    elif defect == "negative_type":
        after["violations"][0]["type"] = "clearance"
    elif defect == "scope":
        after["ignored_checks"][0]["key"] = "clearance"
        with pytest.raises(ValueError):
            checks.check_drc(before, after, baseline, actual, scene)
        after = synthetic_drc(scene, actual)
        after["included_severities"].remove("warning")
    else:
        after["source"] = "CHECKER_ONLY_UNBOUND_SOURCE.kicad_pcb"
    with pytest.raises(ValueError):
        checks.check_drc(before, after, baseline, actual, scene)


JOINT_REPRESENTATIVES = (
    "4b209b2e-134d-5ded-a039-51cc9692dac5",
    "1ecbcd17-799b-58b7-a8c6-e4c92fb01cf5",
    "f510283f-ec3c-5db2-9e68-f71324b70ad5",
)
JOINT_R10_PAD = "83ed57e3-78f7-5e55-a335-850177d80cb6"


def set_checker_joint_representative(report, identity, *, point=None, reverse=False):
    if point is None:
        point = (99.0875, 100) if identity == JOINT_REPRESENTATIVES[0] else (99.0875, 102)
    row = report["unconnected_items"][0]
    fixed = next(copy.deepcopy(item) for item in row["items"] if item["uuid"] == JOINT_R10_PAD)
    representative = {
        "uuid": identity,
        "pos": {"x": point[0], "y": point[1]},
        "description": CHECKER_ONLY,
    }
    row["items"] = [fixed, representative] if reverse else [representative, fixed]


@pytest.mark.parametrize(
    "scene,before_id,after_id,reverse",
    [
        ("baseline", JOINT_REPRESENTATIVES[0], JOINT_REPRESENTATIVES[1], False),
        ("positive", JOINT_REPRESENTATIVES[1], JOINT_REPRESENTATIVES[2], True),
        ("negative", JOINT_REPRESENTATIVES[2], JOINT_REPRESENTATIVES[0], True),
        ("positive", None, JOINT_REPRESENTATIVES[1], False),
        ("positive", JOINT_REPRESENTATIVES[1], JOINT_REPRESENTATIVES[0], False),
    ],
)
def test_only_the_r10_open_accepts_proven_joint_representatives_without_rewriting_reports(
    checker_boards, scene, before_id, after_id, reverse
):
    baseline, actual = checker_boards["baseline"], checker_boards[scene]
    before, after = synthetic_drc("baseline", baseline), synthetic_drc(scene, actual)
    if before_id is not None:
        set_checker_joint_representative(before, before_id, reverse=not reverse)
    set_checker_joint_representative(after, after_id, reverse=reverse)
    raw_before, raw_after = copy.deepcopy(before), copy.deepcopy(after)
    result = checks.check_drc(before, after, baseline, actual, scene)
    assert result["status"] == "passed"
    assert (result["violations"], result["unconnected_items"]) == {
        "baseline": (0, 7),
        "positive": (0, 7),
        "negative": (2, 8),
    }[scene]
    assert before == raw_before and after == raw_after
    assert result["joint_representation"]
    assert (
        result["joint_representation"]["raw_baseline_unconnected"]
        == raw_before["unconnected_items"]
    )
    assert (
        result["joint_representation"]["raw_actual_unconnected"] == raw_after["unconnected_items"]
    )
    assert result["joint_representation"]["proof"]["status"] == "passed"
    json.dumps(result["joint_representation"], allow_nan=False)


@pytest.mark.parametrize(
    "defect",
    [
        "joint_point_1nm",
        "legacy_point_1nm",
        "front_track_contact_is_not_report_position",
        "other_uuid",
        "other_point_1nm",
        "other_via",
        "new_r1_open",
        "short",
        "mask",
        "duplicate_seven",
        "duplicate_eight",
    ],
)
def test_joint_representation_does_not_relax_any_other_machine_relation(checker_boards, defect):
    scene = (
        "negative" if defect in {"new_r1_open", "short", "mask", "duplicate_eight"} else "positive"
    )
    baseline, actual = checker_boards["baseline"], checker_boards[scene]
    before, after = synthetic_drc("baseline", baseline), synthetic_drc(scene, actual)
    set_checker_joint_representative(after, JOINT_REPRESENTATIVES[1])
    row = after["unconnected_items"][0]
    if defect == "joint_point_1nm":
        row["items"][0]["pos"]["y"] = 102.000001
    elif defect == "legacy_point_1nm":
        set_checker_joint_representative(
            after, JOINT_REPRESENTATIVES[0], point=(99.0875, 100.000001)
        )
    elif defect == "front_track_contact_is_not_report_position":
        set_checker_joint_representative(after, JOINT_REPRESENTATIVES[0], point=(99.0875, 102))
    elif defect == "other_uuid":
        row["items"][1]["uuid"] = "db64a8d6-b47b-57d0-ab0b-52be970367fc"
    elif defect == "other_point_1nm":
        row["items"][1]["pos"]["x"] = 99.087501
    elif defect == "other_via":
        row["items"][0]["uuid"] = "03188d0f-2ccb-53b9-9f15-34e991404fc0"
    elif defect == "new_r1_open":
        item = next(
            item
            for item in after["unconnected_items"][-1]["items"]
            if item["uuid"] == JOINT_REPRESENTATIVES[0]
        )
        item.update(uuid=JOINT_REPRESENTATIVES[1], pos={"x": 99.0875, "y": 102})
    elif defect in {"short", "mask"}:
        finding_type = "shorting_items" if defect == "short" else "solder_mask_bridge"
        conflict = next(value for value in after["violations"] if value["type"] == finding_type)
        item = next(item for item in conflict["items"] if item["uuid"] == JOINT_REPRESENTATIVES[0])
        item.update(uuid=JOINT_REPRESENTATIVES[1], pos={"x": 99.0875, "y": 102})
    else:
        after["unconnected_items"][2] = copy.deepcopy(row)
    assert len(after["unconnected_items"]) == (8 if scene == "negative" else 7)
    with pytest.raises(ValueError):
        checks.check_drc(before, after, baseline, actual, scene)


@pytest.mark.parametrize(
    "defect",
    [
        "inactive_copper",
        "no_annulus",
        "zero_width",
        "endpoint_off_joint",
        "wrong_net",
        "missing_via_layer",
        "remove_unused_layers",
        "zone_layer_connections",
        "short_segment",
    ],
)
def test_known_joint_proof_refuses_geometry_without_the_required_physical_connection(
    checker_boards, defect
):
    board = sexp(checker_boards["baseline"].read_text(encoding="utf-8"))
    assert checks._known_joint_proof(board, copy.deepcopy(board), "baseline")["status"] == "passed"
    altered = copy.deepcopy(board)
    front = next(
        row
        for row in children(altered, "segment")
        if json.loads(one(row, "uuid")[1]) == JOINT_REPRESENTATIVES[0]
    )
    back = next(
        row
        for row in children(altered, "segment")
        if json.loads(one(row, "uuid")[1]) == JOINT_REPRESENTATIVES[1]
    )
    via = next(
        row
        for row in children(altered, "via")
        if json.loads(one(row, "uuid")[1]) == JOINT_REPRESENTATIVES[2]
    )
    if defect == "inactive_copper":
        layers = one(altered, "layers")
        layers.remove(next(row for row in layers[1:] if row[1] == '"F.Cu"'))
    elif defect == "no_annulus":
        one(via, "drill")[1] = "0.6"
    elif defect == "zero_width":
        one(front, "width")[1] = "0"
    elif defect == "endpoint_off_joint":
        one(front, "end")[1] = "99.087501"
    elif defect == "wrong_net":
        one(back, "net")[1] = '"/OUT"'
    elif defect == "missing_via_layer":
        one(via, "layers")[2] = '"F.Cu"'
    elif defect == "remove_unused_layers":
        via.append("remove_unused_layers")
    elif defect == "zone_layer_connections":
        via.append(["zone_layer_connections", '"F.Cu"'])
    else:
        # Preserve the track's actual report reference at its start; shorten
        # its far end inside the via radius to exercise extent independently.
        one(back, "end")[1] = "99.1875"
    # Change both input trees identically: equality alone cannot establish the
    # rejected physical joint predicate. The public hash/whole-board guard is
    # tested separately, rather than being used to make these cases pass.
    with pytest.raises(ValueError):
        checks._known_joint_proof(altered, copy.deepcopy(altered), "baseline")


def test_controller_declares_exact_twelve_native_six_host_six_views_and_five_preparation(tmp_path):
    """Generate argv records only; never execute or approve any consumer."""
    config = {
        "output": str(tmp_path / "CHECKER_ONLY_OUTPUT"),
        "native_home": str(tmp_path / "CHECKER_ONLY_NATIVE_HOME"),
        "host_python": str(tmp_path / "CHECKER_ONLY_HOST_PYTHON.exe"),
        "repository": str(REPO),
        "timeout_seconds": 17,
    }
    assert acceptance.SCENES == ("baseline", "negative", "positive")
    assert acceptance.EXPECTED_FILES == set(ORIGINAL_FILES)
    native = [row for scene in acceptance.SCENES for row in acceptance.native_calls(config, scene)]
    host = [row for scene in acceptance.SCENES for row in acceptance.render_calls(config, scene)]
    prepare = acceptance.preparation_calls(config)
    assert len(native) == 12
    assert {row["id"] for row in native} == {
        scene + "-" + kind
        for scene in ("baseline", "negative", "positive")
        for kind in ("sdk", "drc", "front-svg", "back-svg")
    }
    assert len(host) == 6
    assert {row["id"] for row in host} == {
        scene + "-" + side + "-render"
        for scene in ("baseline", "negative", "positive")
        for side in ("front", "back")
    }
    expected_views = {
        str(Path(config["output"]) / (scene + "-output") / (side + ".png"))
        for scene in ("baseline", "negative", "positive")
        for side in ("front", "back")
    }
    assert {path for row in host for path in row["expected_outputs"]} == expected_views
    assert len(prepare) == 5
    assert {row["id"] for row in prepare} == {
        "prepare-version",
        "prepare-sdk",
        "prepare-drc",
        "prepare-front-svg",
        "prepare-back-svg",
    }
    assert prepare[0]["argv"][-1] == "--version"
    assert acceptance.complete_output_names() == [
        "back.png",
        "back.svg",
        "drc.json",
        "front.png",
        "front.svg",
    ]
    assert not set(row["id"] for row in prepare) & set(row["id"] for row in native + host)
    for row in native + host + prepare:
        assert row["allowed_exit_codes"] == [0]
        assert row["timeout_seconds"] == 17
        assert "--schematic-parity" not in row["argv"]
        assert Path(row["cwd"]).is_absolute()
        if row["kind"] in {"sdk", "render"}:
            assert row["argv"][1:3] == ["-B", "-S"]


def test_controller_fixed_counts_agree_with_the_five_preparation_argv_records(tmp_path):
    assert acceptance.CONSUMER_COUNTS == {
        "preparation_native": 5,
        "formal_native": 12,
        "formal_host": 6,
        "full_images": 6,
    }
    calls = acceptance.preparation_calls(
        {
            "output": str(tmp_path / "CHECKER_ONLY_OUTPUT"),
            "native_home": str(tmp_path / "CHECKER_ONLY_NATIVE_HOME"),
            "repository": str(REPO),
            "timeout_seconds": 17,
        }
    )
    assert len(calls) == acceptance.CONSUMER_COUNTS["preparation_native"]
    assert [row["kind"] for row in calls] == ["version", "sdk", "drc", "front-svg", "back-svg"]
    assert all("--schematic-parity" not in row["argv"] for row in calls)


def test_stopped_cycle_is_rejected_without_dispatching_any_consumer(tmp_path):
    (tmp_path / "EXECUTE-STOP.json").write_text(
        json.dumps({"checker_only": CHECKER_ONLY}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="stopped cycle"):
        acceptance.reject_stopped(tmp_path)


def synthetic_captured_file(path):
    path = Path(path)
    present = path.exists()
    info = path.stat() if present else None
    return {
        "path": str(path),
        "resolved": str(path.resolve()),
        "present": present,
        "sha256": digest(path) if present else None,
        "identity": {
            "device": info.st_dev,
            "inode": info.st_ino,
            "size": info.st_size,
            "mtime_ns": info.st_mtime_ns,
        }
        if info is not None
        else None,
    }


def synthetic_public_preview(scene, files, candidate_sha, origin):
    reference, identity, _target_x, _delta = POSES[scene]
    board = Path(files["edit.kicad_pcb"]["path"])
    captured = [
        synthetic_captured_file(files[name]["path"])
        for name in (
            "edit.kicad_pcb",
            "edit.kicad_pro",
            "fp-lib-table",
            "fixture.pretty/R_0805.kicad_mod",
            "fixture.pretty/SO8.kicad_mod",
        )
    ]
    captured[0]["sha256"] = ORIGINAL_FILES["edit.kicad_pcb"]
    captured.append(synthetic_captured_file(board.with_suffix(".kicad_dru")))
    plan = {
        "operation_id": "CHECKER_ONLY_OP_" + reference,
        "plan_id": "CHECKER_ONLY_PLAN_" + reference,
        "capability_id": "CHECKER_ONLY_MOVE_CAPABILITY",
        "target": {
            "mode": "file",
            "engine_id": "kicad-cli.saved-board",
            "instance_id": None,
            "object_id": board.as_uri(),
            "locator": board.as_uri(),
        },
        "can_apply": scene == "positive",
        "base_snapshot": {"revision": ORIGINAL_FILES["edit.kicad_pcb"]},
        "changes": [
            {
                "action": "translate",
                "resource": "footprint",
                "id": identity,
                "before": {
                    "id": identity,
                    "ref": reference,
                    "position_nm": [100000000, 110000000 if scene == "positive" else 100000000],
                },
                "after": {"position_mm": [99.5, 110] if scene == "positive" else [98.5, 100]},
            }
        ],
        "extensions": {
            "preflight": {
                "schema_version": 1,
                "status": "passed" if scene == "positive" else "blocked",
                "engineering_write_started": False,
                "input_binding": {"board": str(board), "files": captured},
                "baseline_sha256": ORIGINAL_FILES["edit.kicad_pcb"],
                "candidate_sha256": candidate_sha,
                "staging": {
                    "baseline_sha256": ORIGINAL_FILES["edit.kicad_pcb"],
                    "candidate_sha256": candidate_sha,
                    "stages": {"candidate": {"board": origin}},
                },
            }
        },
    }
    return {
        "ok": True,
        "schema_version": 1,
        "data": {"preview": plan, "confirm_token": "CHECKER_ONLY_TOKEN_NEVER_USED"},
    }


def synthetic_public_objects(board_path):
    board = sexp(board_path.read_text(encoding="utf-8"))
    result = []
    for fp in children(board, "footprint"):
        fields = {json.loads(prop[1]): json.loads(prop[2]) for prop in children(fp, "property")}
        result.append(
            {
                "id": json.loads(one(fp, "uuid")[1]),
                "ref": fields["Reference"],
                "path": json.loads(one(fp, "path")[1]),
                "value": fields["Value"],
                "fpid": json.loads(fp[1]),
                "layer": json.loads(one(fp, "layer")[1]),
                "position_nm": millimetres_to_nm(one(fp, "at")[1:3]),
                "rotation_deg": 0.0,
                "fields": fields,
                "attributes": sorted(word for attr in children(fp, "attr") for word in attr[1:]),
                "pads": [
                    {
                        "id": json.loads(one(pad, "uuid")[1]),
                        "number": json.loads(pad[1]),
                        "net": json.loads(one(pad, "net")[1]) if children(pad, "net") else "",
                        "position_nm": millimetres_to_nm(one(pad, "at")[1:3]),
                        "rotation_deg": 0.0,
                        "kind": pad[2],
                        "shape": pad[3],
                        "geometry_sha256": "0" * 64,
                    }
                    for pad in children(fp, "pad")
                ],
                "geometry_sha256": "0" * 64,
                "property_geometry_sha256": {},
                "saved_semantics_sha256": "0" * 64,
            }
        )
    return result


@pytest.fixture
def checker_public_chain(tmp_path, original_inputs, checker_boards):
    """Write only host fixtures; public records below are deliberately synthetic."""
    projects = {}
    for scene, board_scene in (("positive", "positive"), ("negative", "baseline")):
        root = tmp_path / ("CHECKER_ONLY_PUBLIC_" + scene)
        projects[scene] = {}
        for name, raw in original_inputs.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(
                checker_boards[board_scene].read_bytes() if name == "edit.kicad_pcb" else raw
            )
            projects[scene][name] = {"path": str(path.resolve()), "sha256": digest(path)}
    exported = tmp_path / "CHECKER_ONLY_EXPORTED_R1.kicad_pcb"
    exported.write_bytes(checker_boards["negative"].read_bytes())
    candidate = {
        "path": str(exported.resolve()),
        "sha256": digest(exported),
        "origin": "CHECKER_ONLY_PUBLIC_ORIGIN_STRING_MUST_NEVER_BE_OPENED",
    }
    positive = projects["positive"]
    board = Path(positive["edit.kicad_pcb"]["path"])
    preview = synthetic_public_preview("positive", positive, digest(board), CHECKER_ONLY)
    plan = preview["data"]["preview"]
    receipt = {
        **{
            key: copy.deepcopy(plan[key])
            for key in ("target", "operation_id", "plan_id", "capability_id")
        },
        "snapshot_before": copy.deepcopy(plan["base_snapshot"]),
        "snapshot_after": {"revision": digest(board)},
        "outcome": {
            "execution": "completed",
            "change": "applied",
            "persistence": "saved",
            "verification": {"status": "passed"},
        },
        "changed_items": [{"id": "6c5beb16-fbc7-5af2-918d-da26a4b5251c", "change": CHECKER_ONLY}],
        "extensions": {"failures": [], "preflight": copy.deepcopy(plan["extensions"]["preflight"])},
    }
    manifest = [
        {
            "path": str(board.with_suffix(".kicad_dru")),
            "resolved": str(board.with_suffix(".kicad_dru")),
            "present": False,
            "sha256": None,
        }
    ]
    for name in ("edit.kicad_pcb", "edit.kicad_pro", "edit.kicad_sch", "edit_child.kicad_sch"):
        row = positive[name]
        manifest.append(
            {"path": row["path"], "resolved": row["path"], "present": True, "sha256": row["sha256"]}
        )
    manifest.sort(key=lambda row: row["path"])
    source = {
        "completed": True,
        "official_native": False,
        "origin": "kicad_cli.saved-project-comparison",
        "completed_at": CHECKER_ONLY,
    }
    readback = {
        "ok": True,
        "schema_version": 1,
        "data": {
            "board": str(board),
            "observed_footprints": synthetic_public_objects(board),
            "observation": {
                "inputs": manifest,
                "source": source,
                "snapshot": {
                    "revision": hashlib.sha256(
                        json.dumps(manifest, sort_keys=True).encode()
                    ).hexdigest(),
                    "basis": "complete captured input manifest",
                    "observed_at": CHECKER_ONLY,
                },
                "state": "saved_files",
                "input_drift_checked": True,
                "live_editor_observed": False,
            },
        },
    }
    return {
        "positive_files": positive,
        "negative_files": projects["negative"],
        "candidate": candidate,
        "preview": preview,
        "confirm": {"ok": True, "schema_version": 1, "data": {"receipt": receipt}},
        "readback": readback,
        "negative_preview": synthetic_public_preview(
            "negative", projects["negative"], candidate["sha256"], candidate["origin"]
        ),
    }


def check_synthetic_public_chain(case):
    return checks.check_public_move_chain(
        case["preview"],
        case["confirm"],
        case["readback"],
        case["positive_files"],
        ORIGINAL_FILES,
    )


def test_public_r4_checker_chain_binds_actual_files_and_pad_local_to_world(checker_public_chain):
    result = check_synthetic_public_chain(checker_public_chain)
    assert result["status"] == "passed"
    assert result["R4_pad_world_positions_nm"] == {
        "1": [98587500, 110000000],
        "2": [100412500, 110000000],
    }
    assert result["protected_other_seven_files"] == "original bytes"
    assert result["official_native_claim"] is False


@pytest.mark.parametrize(
    "defect", ["changed_pro", "added_dru", "replaced_library", "extra_unused_library"]
)
def test_public_preflight_captured_project_inputs_cannot_be_hidden_by_restored_outer_files(
    checker_public_chain, defect
):
    case = checker_public_chain
    preflight = case["preview"]["data"]["preview"]["extensions"]["preflight"]
    rows = preflight["input_binding"]["files"]
    board = Path(case["positive_files"]["edit.kicad_pcb"]["path"])
    if defect == "extra_unused_library":
        rows.append(
            synthetic_captured_file(
                case["positive_files"]["fixture.pretty/R_0603.kicad_mod"]["path"]
            )
        )
    else:
        path = (
            board.with_suffix(".kicad_dru")
            if defect == "added_dru"
            else Path(
                case["positive_files"][
                    "edit.kicad_pro"
                    if defect == "changed_pro"
                    else "fixture.pretty/R_0805.kicad_mod"
                ]["path"]
            )
        )
        original = path.read_bytes() if path.exists() else None
        path.write_text(CHECKER_ONLY + "_ALTERED_PREFLIGHT_INPUT", encoding="utf-8")
        fake_captured = synthetic_captured_file(path)
        index = next(index for index, row in enumerate(rows) if row["path"] == str(path))
        rows[index] = fake_captured
        if original is None:
            path.unlink()
        else:
            path.write_bytes(original)
        # The current files and their eight outer SHA bindings are restored;
        # only the false preflight capture still describes the changed input.
        assert {name: digest(row["path"]) for name, row in case["positive_files"].items()} == {
            name: row["sha256"] for name, row in case["positive_files"].items()
        }
    case["confirm"]["data"]["receipt"]["extensions"]["preflight"] = copy.deepcopy(preflight)
    with pytest.raises(ValueError):
        check_synthetic_public_chain(case)


@pytest.mark.parametrize(
    "defect",
    [
        "target",
        "candidate_sha",
        "move",
        "receipt_op",
        "receipt_plan",
        "receipt_snapshot",
        "receipt_preflight",
        "preflight_input_board",
        "staging_baseline",
        "readback_manifest",
        "missing_protection",
        "pad_local_position",
        "changed_items",
    ],
)
def test_public_r4_chain_rejects_wrong_project_candidate_receipt_or_readback(
    checker_public_chain, defect
):
    case = checker_public_chain
    plan = case["preview"]["data"]["preview"]
    receipt = case["confirm"]["data"]["receipt"]
    if defect == "target":
        plan["target"]["locator"] = Path(case["negative_files"]["edit.kicad_pcb"]["path"]).as_uri()
    elif defect == "candidate_sha":
        plan["extensions"]["preflight"]["candidate_sha256"] = "0" * 64
    elif defect == "move":
        plan["changes"][0]["after"]["position_mm"] = [98.5, 100]
    elif defect == "receipt_op":
        receipt["operation_id"] = "CHECKER_ONLY_OTHER_OPERATION"
    elif defect == "receipt_plan":
        receipt["plan_id"] = "CHECKER_ONLY_OTHER_PLAN"
    elif defect == "receipt_snapshot":
        receipt["snapshot_after"]["revision"] = "0" * 64
    elif defect == "receipt_preflight":
        receipt["extensions"]["preflight"]["status"] = "blocked"
    elif defect == "preflight_input_board":
        plan["extensions"]["preflight"]["input_binding"]["board"] = case["negative_files"][
            "edit.kicad_pcb"
        ]["path"]
    elif defect == "staging_baseline":
        plan["extensions"]["preflight"]["staging"]["baseline_sha256"] = "0" * 64
    elif defect == "readback_manifest":
        manifest = case["readback"]["data"]["observation"]["inputs"]
        manifest[0] = copy.deepcopy(manifest[1])
        case["readback"]["data"]["observation"]["snapshot"]["revision"] = hashlib.sha256(
            json.dumps(manifest, sort_keys=True).encode()
        ).hexdigest()
    elif defect == "missing_protection":
        del case["positive_files"]["fixture.pretty/R_0603.kicad_mod"]
    elif defect == "pad_local_position":
        fp = next(
            row for row in case["readback"]["data"]["observed_footprints"] if row["ref"] == "R4"
        )
        fp["pads"][0]["position_nm"] = [98587500, 110000000]
    else:
        receipt["changed_items"].append({"id": "2d4aa815-63ab-5983-950d-99c5287f15a2"})
    with pytest.raises(ValueError):
        check_synthetic_public_chain(case)


def test_negative_preview_allows_a_blocked_token_without_accessing_candidate_origin(
    checker_public_chain,
):
    case = checker_public_chain
    assert case["negative_preview"]["data"]["confirm_token"]
    result = checks.check_negative_preview(
        case["negative_preview"], case["negative_files"], case["candidate"]
    )
    assert result["status"] == "passed" and result["can_apply"] is False
    assert result["original_eight_files"] == "unchanged"
    assert result["confirm_executed_by_checker"] is False


@pytest.mark.parametrize(
    "defect", ["target", "candidate_sha", "move", "origin", "missing_protection", "protected_bytes"]
)
def test_negative_preview_rejects_unbound_export_or_changed_original_project(
    checker_public_chain, defect
):
    case = checker_public_chain
    plan = case["negative_preview"]["data"]["preview"]
    if defect == "target":
        plan["target"]["locator"] = Path(case["positive_files"]["edit.kicad_pcb"]["path"]).as_uri()
    elif defect == "candidate_sha":
        plan["extensions"]["preflight"]["staging"]["candidate_sha256"] = "0" * 64
    elif defect == "move":
        plan["changes"][0]["after"]["position_mm"] = [99.5, 110]
    elif defect == "origin":
        case["candidate"]["origin"] = "CHECKER_ONLY_DIFFERENT_ORIGIN"
    elif defect == "missing_protection":
        del case["negative_files"]["edit_child.kicad_sch"]
    else:
        row = case["negative_files"]["edit.kicad_pro"]
        Path(row["path"]).write_text('{"checker_only":"changed"}', encoding="utf-8")
        row["sha256"] = digest(row["path"])
    with pytest.raises(ValueError):
        checks.check_negative_preview(
            case["negative_preview"], case["negative_files"], case["candidate"]
        )


@pytest.mark.parametrize(
    "defect", ["relative_board", "duplicate_token", "missing_token", "unexpected_option"]
)
def test_public_argv_parser_refuses_ambiguous_target_or_token_forms(tmp_path, defect):
    argv = [
        str(tmp_path / "CHECKER_ONLY.exe"),
        "board",
        "footprint",
        "move",
        "--board",
        str(tmp_path / "edit.kicad_pcb"),
        "--moves",
        "R4:99.5,110",
    ]
    if defect == "relative_board":
        argv[5] = "edit.kicad_pcb"
    elif defect == "duplicate_token":
        argv.extend(["--confirm", "CHECKER_ONLY_A", "--confirm", "CHECKER_ONLY_B"])
    elif defect == "missing_token":
        argv.append("--confirm")
    else:
        argv.append("--skip-drc")
    with pytest.raises(ValueError):
        acceptance.public_arguments({"argv": argv})


@pytest.mark.parametrize("defect", ["exe_bytes", "unlisted_member", "candidate_pin"])
def test_candidate_zip_checks_actual_members_and_exe_identity(tmp_path, defect):
    """The ZIP contains dummy text bytes, never an executable or a release artifact."""
    binary = CHECKER_ONLY.encode("ascii")
    binary_sha = hashlib.sha256(binary).hexdigest()
    candidate = {
        "sha256": binary_sha,
        "external_driver_pin": acceptance.PIN,
        "native_case_version": "10.0.6",
        "release_readiness": "unpublishable",
        "executable": "bin/kicad-cli.exe",
        "package_source_commit": "a" * 40,
    }
    archive = tmp_path / "CHECKER_ONLY_PACKAGE.zip"

    def write_zip(*, changed=False):
        document = copy.deepcopy(candidate)
        if changed and defect == "candidate_pin":
            document["external_driver_pin"] = "b" * 40
        members = {
            "CANDIDATE.json": json.dumps(document).encode(),
            "bin/kicad-cli.exe": binary
            + (b"CHANGED" if changed and defect == "exe_bytes" else b""),
        }
        manifest = {
            "source_commit": "a" * 40,
            "files": {name: hashlib.sha256(raw).hexdigest() for name, raw in members.items()},
        }
        if changed and defect == "unlisted_member":
            members["CHECKER_ONLY_UNDECLARED.txt"] = b"unlisted"
        with zipfile.ZipFile(archive, "w") as packed:
            for name, raw in members.items():
                packed.writestr(name, raw)
            packed.writestr("FILES.sha256.json", json.dumps(manifest))

    config = {
        "candidate_archive": str(archive),
        "expected_binary_sha256": binary_sha,
        "expected_native_version": "10.0.6",
    }
    write_zip()
    assert acceptance.package_identity(config) == candidate
    write_zip(changed=True)
    with pytest.raises(ValueError):
        acceptance.package_identity(config)


def test_build_record_rejects_nonzero_outcome_before_using_its_references(tmp_path):
    reference = {"path": str(tmp_path / "CHECKER_ONLY_NOT_OPENED"), "sha256": "0" * 64}
    record = {
        "schema_version": 1,
        "kind": "move-build-v1",
        "source_commit": "a" * 40,
        "source_checkout": str(tmp_path / "CHECKER_ONLY_CHECKOUT"),
        "command": [],
        "cwd": str(tmp_path),
        "python": reference,
        "build_script": reference,
        "source_archive": reference,
        "build_log": reference,
        "binary": reference,
        "process_evidence": reference,
        "exit_code": 1,
    }
    path = tmp_path / "CHECKER_ONLY_BUILD_RECORD.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="identity/outcome"):
        acceptance.build_record_identity(
            {"path": str(path), "sha256": digest(path)}, "a" * 40, "0" * 64
        )


@pytest.mark.parametrize("flags", [[], ["-B"]])
def test_python_build_package_argv_allows_zero_or_one_bytecode_flag(tmp_path, flags):
    program, script = str(tmp_path / "CHECKER_ONLY_PYTHON"), "build.py"
    assert acceptance.python_script_argv([program, *flags, script]) == [program, script]


@pytest.mark.parametrize("flags", [["-B", "-B"], ["-S"], ["-B", "-S"]])
def test_python_build_package_argv_rejects_repeated_or_other_interpreter_flags(tmp_path, flags):
    with pytest.raises(ValueError):
        acceptance.python_script_argv([str(tmp_path / "CHECKER_ONLY_PYTHON"), *flags, "build.py"])


@pytest.mark.parametrize("defect", ["command_line", "image_sha", "wrong_exe"])
def test_public_call_record_rejects_trace_command_line_or_exe_mismatch(
    tmp_path, checker_public_chain, defect
):
    """A wholly synthetic event journal tests parsing; no observed process is claimed."""
    directory = tmp_path / "CHECKER_ONLY_FAKE_CALL"
    directory.mkdir()
    binary = tmp_path / "CHECKER_ONLY_DUMMY_EXE_FILE"
    binary.write_text(CHECKER_ONLY, encoding="utf-8")
    files = checker_public_chain["positive_files"]
    board = Path(files["edit.kicad_pcb"]["path"])
    argv = [
        str(binary),
        "board",
        "footprint",
        "move",
        "--board",
        str(board),
        "--moves",
        "R4:99.5,110",
        "--dry-run",
    ]
    events = [
        {"index": 0, "code": 3, "pid": 42, "thread_id": 7},
        {"index": 1, "code": 5, "pid": 42, "thread_id": 7, "exit_code": 0},
    ]
    for event in events:
        event.update(cleanup=False, continued=True, continuation="0x10002")
    journal = []
    for event in events:
        journal.extend(
            [
                {"kind": "received", "event": dict(event, continued=False)},
                {"kind": "continued", "event": event},
            ]
        )
    stdout, stderr, event_file = (
        directory / "stdout",
        directory / "stderr",
        directory / "events.jsonl",
    )
    stdout.write_text(
        json.dumps({"ok": True, "data": {"checker_only": CHECKER_ONLY}}), encoding="utf-8"
    )
    stderr.write_bytes(b"")
    event_file.write_text("\n".join(json.dumps(row) for row in journal) + "\n", encoding="utf-8")
    empty = {"members": [], "all_members_attributed": True}
    trace = {
        "observation_schema": 2,
        "complete_tree": True,
        "root_pid": 42,
        "root_identity": {
            "pid": 42,
            "creation_filetime": 1234567,
            "image": str(binary),
            "assigned_before_resume": True,
            "owned_job_member": True,
        },
        "exit": 0,
        "timed_out": False,
        "events": events,
        "processes": [
            {
                "pid": 42,
                "creation_filetime": 1234567,
                "image": str(binary),
                "image_sha256": digest(binary),
                "owned": True,
                "exit": 0,
                "metadata": {"ProcessId": 42, "CommandLine": subprocess.list2cmdline(argv)},
            }
        ],
        "exceptions": [],
        "job_active_before_close": 0,
        "job_finalization": {
            "natural_tree_complete": True,
            "controlled_termination": False,
            "control_events": [],
            "before_close": empty,
            "after_cleanup": empty,
        },
        "stdout_sha256": digest(stdout),
        "stderr_sha256": digest(stderr),
        "event_journal_sha256": digest(event_file),
        "checker_only": CHECKER_ONLY,
    }
    trace_path, record_path = directory / "TRACE.json", directory / "CHECKER_ONLY_CALL.json"
    record = {
        "schema_version": 1,
        "kind": "move-public-call-v1",
        "argv": argv,
        "cwd": str(board.parent),
        "stdout": {"path": str(stdout), "sha256": digest(stdout)},
        "before_files": files,
        "after_files": files,
    }

    def write_record():
        trace_path.write_text(json.dumps(trace), encoding="utf-8")
        record["trace"] = {"path": str(trace_path), "sha256": digest(trace_path)}
        record_path.write_text(json.dumps(record), encoding="utf-8")
        return {"path": str(record_path), "sha256": digest(record_path)}

    config = {"binary": str(binary), "expected_binary_sha256": digest(binary)}
    bound = write_record()
    assert acceptance.public_call_identity(bound, config)[0]["argv"] == argv
    process = trace["processes"][0]
    if defect == "command_line":
        process["metadata"]["CommandLine"] += " CHECKER_ONLY_OTHER_INVOCATION"
    elif defect == "image_sha":
        process["image_sha256"] = "0" * 64
    else:
        record["argv"][0] = str(tmp_path / "CHECKER_ONLY_ANOTHER_EXE")
        process["metadata"]["CommandLine"] = subprocess.list2cmdline(record["argv"])
    bound = write_record()
    with pytest.raises(ValueError, match="another EXE or invocation"):
        acceptance.public_call_identity(bound, config)


def write_checker_only_call(directory, *, binary, argv, cwd, envelope, before, after, sequence):
    """Write a synthetic complete call journal; never start the named dummy file."""
    directory.mkdir(exist_ok=True)
    stdout, stderr, journal_path = (
        directory / "stdout",
        directory / "stderr",
        directory / "events.jsonl",
    )
    stdout.write_text(json.dumps(envelope), encoding="utf-8")
    stderr.write_bytes(b"")
    events = [
        {"index": 0, "code": 3, "pid": 42, "thread_id": 7},
        {"index": 1, "code": 5, "pid": 42, "thread_id": 7, "exit_code": 0},
    ]
    journal = []
    for event in events:
        event.update(cleanup=False, continued=True, continuation="0x10002")
        journal.extend(
            [
                {"kind": "received", "event": dict(event, continued=False)},
                {"kind": "continued", "event": event},
            ]
        )
    journal_path.write_text("\n".join(json.dumps(row) for row in journal) + "\n", encoding="utf-8")
    empty = {"members": [], "all_members_attributed": True}
    trace = {
        "observation_schema": 2,
        "complete_tree": True,
        "root_pid": 42,
        "root_identity": {
            "pid": 42,
            "creation_filetime": 1234567,
            "image": str(binary),
            "assigned_before_resume": True,
            "owned_job_member": True,
        },
        "exit": 0,
        "timed_out": False,
        "events": events,
        "processes": [
            {
                "pid": 42,
                "creation_filetime": 1234567,
                "image": str(binary),
                "image_sha256": digest(binary),
                "owned": True,
                "exit": 0,
                "metadata": {"ProcessId": 42, "CommandLine": subprocess.list2cmdline(argv)},
            }
        ],
        "exceptions": [],
        "job_active_before_close": 0,
        "job_finalization": {
            "natural_tree_complete": True,
            "controlled_termination": False,
            "control_events": [],
            "before_close": empty,
            "after_cleanup": empty,
        },
        "stdout_sha256": digest(stdout),
        "stderr_sha256": digest(stderr),
        "event_journal_sha256": digest(journal_path),
        "started_at": f"2026-10-16T10:00:0{sequence}+00:00",
        "finished_at": f"2026-10-16T10:00:0{sequence}+00:00",
        "cwd": str(cwd),
        "checker_only": CHECKER_ONLY,
    }
    trace_path = directory / "TRACE.json"
    trace_path.write_text(json.dumps(trace), encoding="utf-8")
    record = {
        "schema_version": 1,
        "kind": "move-public-call-v1",
        "actor": "root",
        "argv": argv,
        "cwd": str(cwd),
        "trace": {"path": str(trace_path), "sha256": digest(trace_path)},
        "stdout": {"path": str(stdout), "sha256": digest(stdout)},
        "before_files": before,
        "after_files": after,
    }
    path = directory / "CHECKER_ONLY_CALL.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return {"path": str(path), "sha256": digest(path)}


def write_checker_only_process(directory, name, *, program, command, cwd, script, stdout, hour):
    """Build/package process-shaped host data; the dummy program is never executed."""
    directory.mkdir()
    out, err = directory / (name + ".stdout"), directory / (name + ".stderr")
    out.write_bytes(stdout)
    err.write_bytes(b"")
    captured_program = [{"path": str(program), "present": True, "sha256": digest(program)}]
    inputs = [{"path": str(script), "present": True, "sha256": digest(script)}]
    empty = {"members": [], "all_members_attributed": True}
    record = {
        "name": name,
        "state": "finished",
        "status": "passed",
        "execution": "completed",
        "exit": 0,
        "command": command,
        "cwd": str(cwd),
        "pid": 43,
        "program": captured_program,
        "program_after": captured_program,
        "inputs_before": inputs,
        "inputs_after": inputs,
        "root_identity": {
            "pid": 43,
            "creation_filetime": 1234567,
            "image": str(program),
            "assigned_before_resume": True,
            "owned_job_member": True,
        },
        "job_finalization": {
            "natural_tree_complete": True,
            "controlled_termination": False,
            "control_events": [],
            "before_close": empty,
            "after_cleanup": empty,
        },
        "stdout_sha256": digest(out),
        "stderr_sha256": digest(err),
        "started_at": f"2026-10-16T0{hour}:00:00+00:00",
        "finished_at": f"2026-10-16T0{hour}:00:01+00:00",
        "checker_only": CHECKER_ONLY,
    }
    path = directory / (name + ".call.json")
    path.write_text(json.dumps(record), encoding="utf-8")
    return {"path": str(path), "sha256": digest(path)}, {"path": str(out), "sha256": digest(out)}


def attach_checker_only_preflight_context(envelope, native, seed):
    binding = envelope["data"]["preview"]["extensions"]["preflight"]["input_binding"]
    binding["selection"] = {
        "installation": str(native),
        "official_executable": str(native / "bin/kicad-cli.exe"),
        "config_root": str(seed),
        "expected_version_series": "10.0",
    }
    binding["files"].extend(
        synthetic_captured_file(seed / "10.0" / name)
        for name in ("kicad_common.json", "fp-lib-table")
    )
    binding["configuration_tree"] = {
        "path": str(seed),
        "present": True,
        "files": [],
        "directories": [{"relative": ".", "identity": {}, "members": []}],
    }
    binding["tools"] = {
        "files": [synthetic_captured_file(native / "bin/kicad-cli.exe")],
        "bin_DLL_members": [],
        "resource_trees": [
            {
                "path": str(native / "share/kicad" / name),
                "present": False,
                "directories": [],
                "files": [],
            }
            for name in ("resources", "schemas", "internat")
        ],
    }


@pytest.fixture
def checker_source_binding(tmp_path, checker_public_chain):
    """Complete v1 host fixture, including real checker paths without any patching."""
    case = checker_public_chain
    base = tmp_path / "CHECKER_ONLY_COMPLETE_BINDING"
    base.mkdir()
    checkout, work = base / "checkout", base / "work"
    (checkout / "kicad_cli").mkdir(parents=True)
    work.mkdir()
    init = checkout / "kicad_cli/__init__.py"
    init.write_text('# CHECKER_ONLY\n__version__ = "2.0.0"\n', encoding="utf-8")
    script, python, log, binary = (
        checkout / "build.py",
        base / "dummy-python",
        base / "build.log",
        base / "kicad-cli.exe",
    )
    for path in (script, python, log, binary):
        path.write_text(CHECKER_ONLY, encoding="utf-8")
    source_archive = work / "checker-sources.zip"
    with zipfile.ZipFile(source_archive, "w") as packed:
        packed.writestr("__init__.py", init.read_bytes())
        packed.writestr(
            "source-manifest.json",
            json.dumps(
                {
                    "format": 1,
                    "version": "2.0.0",
                    "files": {"__init__.py": digest(init)},
                }
            ),
        )

    def reference(path):
        return {"path": str(path.resolve()), "sha256": digest(path)}

    commit = "a" * 40
    (checkout / ".git").mkdir()
    (checkout / ".git/HEAD").write_text(commit + "\n", encoding="ascii")
    build_record = {
        "schema_version": 1,
        "kind": "move-build-v1",
        "source_commit": commit,
        "source_checkout": str(checkout),
        "cwd": str(checkout),
        "command": [
            str(python),
            "build.py",
            "--output",
            str(binary.parent),
            "--work-dir",
            str(work),
        ],
        "python": reference(python),
        "build_script": reference(script),
        "source_archive": reference(source_archive),
        "build_log": reference(log),
        "binary": reference(binary),
        "exit_code": 0,
    }
    build_process, build_stdout = write_checker_only_process(
        base / "build-process",
        "build",
        program=python,
        command=build_record["command"],
        cwd=checkout,
        script=script,
        stdout=CHECKER_ONLY.encode(),
        hour=8,
    )
    build_record["process_evidence"] = build_process
    build_record["build_log"] = build_stdout
    build_path = base / "CHECKER_ONLY_BUILD.json"
    build_path.write_text(json.dumps(build_record), encoding="utf-8")
    candidate = {
        "sha256": digest(binary),
        "external_driver_pin": acceptance.PIN,
        "native_case_version": "10.0.6",
        "release_readiness": "unpublishable",
        "executable": "bin/kicad-cli.exe",
        "source_commit": commit,
        "package_source_commit": commit,
    }
    archive = base / "CHECKER_ONLY_CANDIDATE.zip"
    members = {
        "CANDIDATE.json": json.dumps(candidate).encode(),
        "bin/kicad-cli.exe": binary.read_bytes(),
    }
    member_hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in members.items()}
    with zipfile.ZipFile(archive, "w") as packed:
        for name, raw in members.items():
            packed.writestr(name, raw)
        packed.writestr(
            "FILES.sha256.json", json.dumps({"source_commit": commit, "files": member_hashes})
        )
    package_raw = json.dumps(
        {
            "package": str(base / "CHECKER_ONLY_PACKAGE"),
            "archive": str(archive),
            "archive_sha256": digest(archive),
            "candidate": candidate,
            "files": len(member_hashes),
            "local_links_checked": True,
        }
    ).encode()
    package_script = checkout / "scripts/build_internal_candidate.py"
    package_script.parent.mkdir()
    package_script.write_text("# " + CHECKER_ONLY + "\n", encoding="utf-8")
    package_command = [
        str(python),
        "-B",
        "scripts/build_internal_candidate.py",
        "--source",
        str(checkout),
        "--exe",
        str(binary),
        "--output",
        str(base / "CHECKER_ONLY_PACKAGE"),
    ]
    package_process, package_stdout = write_checker_only_process(
        base / "package-process",
        "package",
        program=python,
        command=package_command,
        cwd=checkout,
        script=package_script,
        stdout=package_raw,
        hour=9,
    )
    package_path = base / "CHECKER_ONLY_PACKAGE_RECORD.json"
    package_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "move-package-v1",
                "source_checkout": str(checkout),
                "python": reference(python),
                "script": reference(package_script),
                "command": package_command,
                "cwd": str(checkout),
                "process_evidence": package_process,
                "stdout": package_stdout,
            }
        ),
        encoding="utf-8",
    )
    formal_negative = {}
    for name, row in case["negative_files"].items():
        path = base / "formal-negative" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            Path(case["candidate"]["path"]).read_bytes()
            if name == "edit.kicad_pcb"
            else Path(row["path"]).read_bytes()
        )
        formal_negative[name] = reference(path)
    scenes = {
        "baseline": case["negative_files"],
        "negative": formal_negative,
        "positive": case["positive_files"],
    }
    positive = scenes["positive"]
    before = {name: dict(row, sha256=ORIGINAL_FILES[name]) for name, row in positive.items()}
    positive_board = Path(positive["edit.kicad_pcb"]["path"])
    negative_board = Path(case["negative_files"]["edit.kicad_pcb"]["path"])
    native, seed = base / "CHECKER_ONLY_NATIVE", base / "CHECKER_ONLY_SEED"
    (native / "bin").mkdir(parents=True)
    (native / "bin/kicad-cli.exe").write_text(CHECKER_ONLY, encoding="utf-8")
    seed.mkdir()
    attach_checker_only_preflight_context(case["preview"], native, seed)
    attach_checker_only_preflight_context(case["negative_preview"], native, seed)
    case["confirm"]["data"]["receipt"]["extensions"]["preflight"] = copy.deepcopy(
        case["preview"]["data"]["preview"]["extensions"]["preflight"]
    )
    unpacked = base / "CHECKER_ONLY_UNPACKED_ROOT"
    unpacked.mkdir()
    public_binary = unpacked / "kicad-cli.exe"
    public_binary.write_bytes(binary.read_bytes())
    move = [str(public_binary), "board", "footprint", "move", "--board"]
    token = case["preview"]["data"]["confirm_token"]
    case["confirm"]["data"]["receipt"]["request_id"] = "CHECKER_ONLY_REQUEST"
    call_inputs = {
        "negative_preflight": {
            "argv": [*move, str(negative_board), "--moves", "R1:98.5,100", "--dry-run"],
            "cwd": unpacked,
            "envelope": case["negative_preview"],
            "before": case["negative_files"],
            "after": case["negative_files"],
            "sequence": 0,
        },
        "positive_preview": {
            "argv": [*move, str(positive_board), "--moves", "R4:99.5,110", "--dry-run"],
            "cwd": unpacked,
            "envelope": case["preview"],
            "before": before,
            "after": before,
            "sequence": 1,
        },
        "positive_confirm": {
            "argv": [
                *move,
                str(positive_board),
                "--moves",
                "R4:99.5,110",
                "--confirm",
                token,
                "--idempotency-key",
                "CHECKER_ONLY_REQUEST",
            ],
            "cwd": unpacked,
            "envelope": case["confirm"],
            "before": before,
            "after": positive,
            "sequence": 2,
        },
        "positive_readback": {
            "argv": [
                str(public_binary),
                "project",
                "parity",
                "--board",
                str(positive_board),
                "--schematic",
                positive["edit.kicad_sch"]["path"],
            ],
            "cwd": unpacked,
            "envelope": case["readback"],
            "before": positive,
            "after": positive,
            "sequence": 3,
        },
    }
    evidence = {"build": reference(build_path), "package": reference(package_path)}
    for role, inputs in call_inputs.items():
        evidence[role] = write_checker_only_call(base / role, binary=public_binary, **inputs)
    document = {
        "schema_version": 1,
        "kind": "move-safety-source-binding",
        "build_commit": commit,
        "binary": reference(binary),
        "candidate_archive": reference(archive),
        "scenes": scenes,
        "negative_candidate_export": {
            "origin": case["candidate"]["origin"],
            "saved": formal_negative["edit.kicad_pcb"],
        },
        "evidence": evidence,
    }
    binding_path = base / "CHECKER_ONLY_SOURCE_BINDING.json"
    config = {
        "native_home": str(native),
        "binary": str(binary),
        "expected_binary_sha256": digest(binary),
        "candidate_archive": str(archive),
        "expected_candidate_archive_sha256": digest(archive),
        "expected_native_version": "10.0.6",
        "sources": {
            scene: {name: row["path"] for name, row in files.items()}
            for scene, files in scenes.items()
        },
        "source_files": {
            scene: {name: row["sha256"] for name, row in files.items()}
            for scene, files in scenes.items()
        },
    }

    def seal():
        config["source_evidence"] = [dict(row, role=role) for role, row in evidence.items()]
        binding_path.write_text(json.dumps(document), encoding="utf-8")
        config["source_binding"] = reference(binding_path)

    seal()
    return {
        "config": config,
        "document": document,
        "seal": seal,
        "base": base,
        "evidence": evidence,
        "call_inputs": call_inputs,
        "binary": public_binary,
    }


@pytest.mark.parametrize(
    "defect",
    [
        "binary_path",
        "zip_path",
        "scene_source",
        "candidate_origin",
        "candidate_hash",
        "confirm_token",
        "before_after_chain",
    ],
)
def test_complete_host_source_binding_passes_then_rejects_semantic_cross_links(
    checker_source_binding, defect
):
    """Exercise the complete validator with synthetic host files and no bypasses."""
    case = checker_source_binding
    config, document, base = case["config"], case["document"], case["base"]
    if defect == "before_after_chain":
        inputs = copy.deepcopy(case["call_inputs"]["positive_confirm"])
        inputs["argv"][inputs["argv"].index("--idempotency-key")] = "--request-id"
        case["call_inputs"]["positive_confirm"] = inputs
        case["evidence"]["positive_confirm"] = write_checker_only_call(
            base / "positive_confirm", binary=case["binary"], **inputs
        )
        case["seal"]()
    assert acceptance.validate_source_binding(config)["status"] == "passed"
    if defect in {"binary_path", "zip_path"}:
        key = "binary" if defect == "binary_path" else "candidate_archive"
        other = base / (
            "CHECKER_ONLY_OTHER_EXE" if defect == "binary_path" else "CHECKER_ONLY_OTHER.zip"
        )
        other.write_bytes(Path(document[key]["path"]).read_bytes())
        document[key] = {"path": str(other), "sha256": digest(other)}
    elif defect == "scene_source":
        original = document["scenes"]["positive"]["edit_child.kicad_sch"]
        other = base / "CHECKER_ONLY_OTHER_CHILD.kicad_sch"
        other.write_bytes(Path(original["path"]).read_bytes())
        document["scenes"]["positive"]["edit_child.kicad_sch"] = {
            "path": str(other),
            "sha256": digest(other),
        }
    elif defect == "candidate_origin":
        document["negative_candidate_export"]["origin"] = "CHECKER_ONLY_OTHER_ORIGIN"
    elif defect == "candidate_hash":
        document["negative_candidate_export"]["saved"] = dict(
            document["negative_candidate_export"]["saved"], sha256="0" * 64
        )
    else:
        inputs = copy.deepcopy(case["call_inputs"]["positive_confirm"])
        if defect == "confirm_token":
            inputs["argv"][inputs["argv"].index("--confirm") + 1] = "CHECKER_ONLY_OTHER_TOKEN"
        else:
            inputs["before"] = copy.deepcopy(document["scenes"]["positive"])
        case["evidence"]["positive_confirm"] = write_checker_only_call(
            base / "positive_confirm", binary=case["binary"], **inputs
        )
    case["seal"]()
    with pytest.raises(ValueError):
        acceptance.validate_source_binding(config)
