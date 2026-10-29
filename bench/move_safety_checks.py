"""Independent consumers for three frozen move scenes; no product imports.

Every public check raises ValueError on a mismatch and returns a JSON-compatible
passed record only after its complete comparison. The controller must bind each
fresh native invocation and report to these exact input paths and hashes; the
official DRC JSON itself contains only a source basename, not an input digest.
Historical findings inform the declarations below and are never read as evidence.
"""

import copy
import hashlib
import json
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from functools import wraps
from math import isfinite
from pathlib import Path

from bench.move_engineering_checks import validate_native_observation
from bench.saved_scene_checks import children, one, sexp, sha

BASELINE_SHA256 = "59b5c3a7f5569031c2c098c01cdee5c5be2c71aed77d33eb5992126ac7b5cb1f"
ORIGINAL_HASHES = {
    "edit.kicad_pcb": BASELINE_SHA256,
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
EXPECTED = {
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
FOOTPRINTS = {
    "R1": {
        "scene": "negative",
        "value": "10k",
        "path": "/43ddee60-4186-5f8a-92d6-1101a2bbfaed",
        "pads": {
            "1": {
                "uuid": "b6399d66-3ccf-5183-bd56-8dee1659878f",
                "net": "/SIG",
                "position_nm": [99087500, 100000000],
            },
            "2": {
                "uuid": "4af096ca-59e1-5018-a93c-c3aaa5cd3957",
                "net": "/OUT",
                "position_nm": [100912500, 100000000],
            },
        },
    },
    "R4": {
        "scene": "positive",
        "value": "1k",
        "path": "/c806d962-a396-5331-9be1-d73cc9a1d527",
        "pads": {
            "1": {
                "uuid": "4659dff2-f105-5efd-b5cb-e57fe253caf9",
                "net": "unconnected-(R4-Pad1)",
                "position_nm": [99087500, 110000000],
            },
            "2": {
                "uuid": "cfde6d55-5dd4-5ae1-a970-624324c50f75",
                "net": "Net-(R4-Pad2)",
                "position_nm": [100912500, 110000000],
            },
        },
    },
}
SIG_TRACK = "4b209b2e-134d-5ded-a039-51cc9692dac5"
SIG_BACK_TRACK = "1ecbcd17-799b-58b7-a8c6-e4c92fb01cf5"
SIG_VIA = "f510283f-ec3c-5db2-9e68-f71324b70ad5"
R10_PAD1 = "83ed57e3-78f7-5e55-a335-850177d80cb6"
JOINT_POINT_NM = [99087500, 102000000]
R10_POINT_NM = [99087500, 120000000]
JOINT_REPORT_POINTS_NM = {
    SIG_TRACK: [99087500, 100000000],
    SIG_BACK_TRACK: [99087500, 102000000],
    SIG_VIA: [99087500, 102000000],
}
JOINT_POLICY = {
    "scope": "only the single /SIG-to-R10.1 unconnected obligation",
    "joint_nm": JOINT_POINT_NM,
    "allowed_joint_uuids": [SIG_TRACK, SIG_BACK_TRACK, SIG_VIA],
    "counterpart_uuid": R10_PAD1,
    "counterpart_nm": R10_POINT_NM,
    "report_reference_positions_nm": JOINT_REPORT_POINTS_NM,
    "report_reference_basis": "KiCad 10.0.6 item.GetPosition: straight track m_Start; via center",
    "proof": (
        "unchanged complete copper objects; simple straight tracks at the same via center; "
        "positive width and extent beyond via outside radius; positive annular ring; "
        "same /SIG net, actual active F.Cu/B.Cu and shared via copper; unchanged R10.1"
    ),
    "raw_findings": "retained unchanged; counts, multiplicity and every other edge remain strict",
}
BASELINE_OPEN_PAIRS = [
    [(SIG_TRACK, [99087500, 100000000]), (R10_PAD1, [99087500, 120000000])],
    [
        ("4af096ca-59e1-5018-a93c-c3aaa5cd3957", [100912500, 100000000]),
        ("e042066d-1387-5783-a87c-c4f2f3f2656e", [119525000, 102095000]),
    ],
    [
        ("970c6eca-ebde-5240-87d3-e527b34c070e", [105912500, 100000000]),
        ("d6645bea-6405-5137-b186-14d1e2483ac8", [105912500, 120000000]),
    ],
    [
        ("d6645bea-6405-5137-b186-14d1e2483ac8", [105912500, 120000000]),
        ("1a9d5d34-9943-5cec-9115-1107d43b002e", [105912500, 130000000]),
    ],
    [
        ("cfde6d55-5dd4-5ae1-a970-624324c50f75", [100912500, 110000000]),
        ("4b634433-1194-5a3a-9557-242ae941f9c4", [105912500, 110000000]),
    ],
    [
        ("084e6c4d-63bd-5a38-a2d7-905dbd2bed6a", [100912500, 120000000]),
        ("460a9112-6fab-51de-9787-3baa1b76be9f", [104087500, 120000000]),
    ],
    [
        ("8acc67a0-70dd-51af-9dd6-b7879aaecd65", [100912500, 130000000]),
        ("c8b5f166-ca02-5cc5-a46d-a2f6fdc3c9e2", [104087500, 130000000]),
    ],
]
IGNORED_CHECK_KEYS = [
    "missing_courtyard",
    "track_not_centered_on_via",
    "tuning_profile_track_geometries",
    "footprint_filters_mismatch",
    "footprint_type_mismatch",
]
INCLUDED_SEVERITIES = ["error", "warning", "exclusion"]
NATIVE_SCOPE = (
    "Native semantic readback; independent complete file token comparison protects "
    "every zone/fill, stackup, setup, graphic, pad and copper property. "
    "DRC and full-image review are separate."
)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _consumer(function):
    """Make malformed external records fail through the same stop contract."""

    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (KeyError, TypeError, IndexError, AttributeError, ArithmeticError, OSError) as exc:
            raise ValueError("Incomplete or unreadable frozen consumer input: " + str(exc)) from exc

    return checked


def _scene(scene):
    _require(isinstance(scene, str) and scene in EXPECTED, "Unknown frozen move scene")
    return EXPECTED[scene]


def _nm(value):
    _require(isinstance(value, (str, int, float)) and not isinstance(value, bool), "Bad mm value")
    try:
        result = Decimal(str(value)) * 1000000
    except InvalidOperation as exc:
        raise ValueError("Invalid millimetre coordinate") from exc
    _require(
        result.is_finite() and result == result.to_integral_value(),
        "Coordinate must be an exact integral nanometre value",
    )
    return int(result)


def _vector(value):
    return isinstance(value, list) and len(value) == 2 and all(type(axis) is int for axis in value)


def _digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _binding(path):
    path = Path(path)
    _require(path.is_absolute(), "Consumer input path must be absolute")
    _require(path.is_file() and not path.is_symlink(), "Consumer input must be a regular file")
    return {"path": str(path.resolve()), "sha256": sha(path)}


def _board(path):
    board = sexp(Path(path).read_text(encoding="utf-8"))
    _require(board and board[0] == "kicad_pcb", "Consumer input is not a complete board")
    return board


def _properties(fp):
    rows = children(fp, "property")
    _require(all(len(row) >= 3 for row in rows), "Malformed footprint property")
    result = {json.loads(row[1]): json.loads(row[2]) for row in rows}
    _require(len(result) == len(rows), "Duplicate footprint property")
    return result


def _selected(board, scene):
    intent = _scene(scene)
    matches = [
        fp
        for fp in children(board, "footprint")
        if one(fp, "uuid")[1] == json.dumps(intent["uuid"])
        or _properties(fp).get("Reference") == intent["reference"]
    ]
    _require(len(matches) == 1, "Selected footprint UUID/reference is missing or ambiguous")
    fp = matches[0]
    _require(
        one(fp, "uuid")[1] == json.dumps(intent["uuid"])
        and _properties(fp).get("Reference") == intent["reference"],
        "Selected footprint UUID and reference disagree",
    )
    return fp


def _check_frozen_baseline(board):
    for reference, expected in FOOTPRINTS.items():
        intent = EXPECTED[expected["scene"]]
        fp = _selected(board, expected["scene"])
        pose = one(fp, "at")
        _require(
            fp[1] == '"fixture:R_0805"'
            and _properties(fp).get("Value") == expected["value"]
            and one(fp, "path")[1] == json.dumps(expected["path"])
            and one(fp, "layer")[1] == '"F.Cu"'
            and "locked" not in fp
            and not children(fp, "locked")
            and len(pose) in {3, 4}
            and [_nm(atom) for atom in pose[1:3]] == intent["from_nm"]
            and (len(pose) == 3 or Decimal(pose[3]) == 0),
            "Frozen baseline footprint identity/pose differs: " + reference,
        )


@_consumer
def check_board(baseline_path, actual_path, scene):
    """Compare every saved atom and child order after one declared root-at reversal."""
    intent = _scene(scene)
    bindings = {"baseline": _binding(baseline_path), "actual": _binding(actual_path)}
    _require(bindings["baseline"]["sha256"] == BASELINE_SHA256, "Frozen baseline hash differs")
    old, new = _board(baseline_path), _board(actual_path)
    _check_frozen_baseline(old)
    if scene != "baseline":
        before, after = one(_selected(old, scene), "at"), one(_selected(new, scene), "at")
        _require(
            len(after) == len(before) and [_nm(atom) for atom in after[1:3]] == intent["to_nm"],
            "Saved footprint translation differs from frozen nanometre intent",
        )
        after[1:3] = before[1:3]
    else:
        _require(bindings["actual"]["sha256"] == BASELINE_SHA256, "Baseline copy bytes differ")
    _require(old == new, "Protected complete board structure changed outside selected root at x/y")
    _require(
        sha(baseline_path) == bindings["baseline"]["sha256"]
        and sha(actual_path) == bindings["actual"]["sha256"],
        "Board changed during complete structure comparison",
    )
    return {
        "status": "passed",
        "scene": scene,
        "inputs": bindings,
        "selected_uuid": intent["uuid"],
        "position_nm": intent["to_nm"],
        "allowed_saved_change": None if scene == "baseline" else "selected root at x/y only",
        "all_remaining_atoms_and_child_order": "identical",
        "raw_byte_hashes_retained": True,
    }


def _native_startup(observation):
    startup = observation["startup"]
    keys = {
        "no_site",
        "dont_write_bytecode",
        "actual_sys_path",
        "explicit_site_packages",
        "sitecustomize_or_usercustomize_loaded",
        "Documents_KiCad_thirdparty_consulted",
        "normal_site_addsitedir_executed",
        "native_bin_dll_directory",
        "actual_module_sources",
    }
    _require(set(startup) == keys, "Native startup schema differs")
    for key in ("no_site", "dont_write_bytecode"):
        _require(startup[key] is True, "Native startup flag must be true: " + key)
    for key in (
        "sitecustomize_or_usercustomize_loaded",
        "Documents_KiCad_thirdparty_consulted",
        "normal_site_addsitedir_executed",
    ):
        _require(startup[key] is False, "Native startup flag must be false: " + key)
    _require(isinstance(startup["native_bin_dll_directory"], str), "Native DLL root is missing")
    root = Path(startup["native_bin_dll_directory"])
    _require(root.is_absolute() and root.is_dir(), "Native DLL root must be an absolute directory")
    root = root.resolve()
    paths = startup["actual_sys_path"]
    _require(
        isinstance(paths, list)
        and len(paths) == 4
        and all(isinstance(path, str) and Path(path).is_absolute() for path in paths),
        "Native search paths are incomplete",
    )
    archive = Path(paths[2]).resolve()
    site = root / "Lib/site-packages"
    _require(
        [Path(path).resolve() for path in paths] == [root / "Lib", root / "DLLs", archive, site]
        and archive.parent == root
        and re.fullmatch(r"python[0-9]+\.zip", archive.name) is not None
        and startup["explicit_site_packages"] == str(site),
        "Native search paths exceed the exact site-free path set",
    )
    modules = startup["actual_module_sources"]
    _require(isinstance(modules, list) and modules, "Native module bindings are missing")
    seen, bound = set(), {}
    reader = Path(__file__).with_name("saved_scene_native_read.py").resolve()
    for row in modules:
        _require(isinstance(row, dict), "Native module binding must be an object")
        name = row.get("module")
        _require(
            isinstance(name, str) and name and name not in seen, "Duplicate/missing native module"
        )
        seen.add(name)
        _require(
            name not in {"site", "sitecustomize", "usercustomize"},
            "Native reader loaded site startup code",
        )
        fields = set(row)
        if fields == {"module", "origin"}:
            _require(row["origin"] in {"built-in", "frozen"}, "Unbound source-free native module")
            continue
        if fields == {"module", "path", "sha256"}:
            path = Path(row["path"])
            _require(path.is_absolute(), "Native module path must be absolute")
            path = path.resolve()
            _require(path == reader or root in path.parents, "Undeclared native module source")
            _require(
                _digest(row["sha256"]) and path.is_file() and sha(path) == row["sha256"],
                "Native module source digest drifted",
            )
            bound[name] = (path, row["sha256"])
        elif fields == {"module", "path", "archive_sha256"}:
            path = Path(row["path"])
            _require(
                path.is_absolute()
                and archive in path.resolve().parents
                and _digest(row["archive_sha256"])
                and archive.is_file()
                and sha(archive) == row["archive_sha256"],
                "Native standard-library archive binding differs",
            )
        elif fields in (
            {"module", "origin", "provider", "provider_sha256"},
            {"module", "origin", "keys", "provider", "provider_sha256"},
        ):
            swig = name == "swig_runtime_data4"
            _require(
                name in {"swig_runtime_data4", "typing.io", "typing.re"}
                and fields
                == {"module", "origin", "provider", "provider_sha256"}
                | ({"keys"} if swig else set()),
                "Undeclared native source-free provider",
            )
            provider = root / ("Lib/site-packages/_pcbnew.pyd" if swig else "Lib/typing.py")
            origin = (
                "SWIG registration from bound _pcbnew.pyd"
                if swig
                else "exact alias in bound standard-library typing"
            )
            _require(
                row["origin"] == origin
                and Path(row["provider"]).is_absolute()
                and Path(row["provider"]).resolve() == provider
                and _digest(row["provider_sha256"])
                and sha(provider) == row["provider_sha256"],
                "Native registration/alias provider digest differs",
            )
            if swig:
                _require(
                    isinstance(row["keys"], list)
                    and all(isinstance(key, str) for key in row["keys"])
                    and row["keys"] == sorted(set(row["keys"])),
                    "Native SWIG registration key inventory differs",
                )
        else:
            raise ValueError("Unknown native module binding schema")
    _require(bound.get("__main__") == (reader, sha(reader)), "Native reader source is unbound")
    _require(
        bound.get("pcbnew") == (site / "pcbnew.py", observation["pcbnew_wrapper_sha256"])
        and bound.get("_pcbnew", (None,))[0] == site / "_pcbnew.pyd"
        and observation["reader_sha256"] == sha(reader),
        "Native wrapper/extension/reader binding differs",
    )


def _native(observation, path):
    _require(
        isinstance(observation, dict) and observation.get("native_version") == "10.0.6",
        "Native readback must use exactly the frozen 10.0.6 version",
    )
    validate_native_observation(observation, path)
    _require(
        _digest(observation["reader_sha256"])
        and _digest(observation["pcbnew_wrapper_sha256"])
        and observation["scope"] == NATIVE_SCOPE,
        "Native reader source/scope declaration differs",
    )
    _native_startup(observation)
    pad_fields = {
        "uuid",
        "number",
        "net",
        "position_nm",
        "size_nm",
        "drill_nm",
        "shape",
        "rotation_deg",
        "layers",
    }
    identities = []
    for fp in observation["footprints"]:
        _require(_vector(fp["position_nm"]), "Native footprint pose is not integral nanometres")
        _require(
            type(fp["locked"]) is bool
            and type(fp["dnp"]) is bool
            and type(fp["rotation_deg"]) in (int, float)
            and isfinite(fp["rotation_deg"])
            and isinstance(fp["fields"], dict),
            "Native footprint lock/fields/DNP/rotation schema differs",
        )
        identities.append(fp["uuid"])
        _require(
            len({pad.get("number") for pad in fp["pads"] if isinstance(pad, dict)})
            == len(fp["pads"]),
            "Native pad numbers are ambiguous",
        )
        for pad in fp["pads"]:
            _require(isinstance(pad, dict) and set(pad) == pad_fields, "Native pad schema differs")
            _require(
                all(_vector(pad[key]) for key in ("position_nm", "size_nm", "drill_nm"))
                and isinstance(pad["layers"], list)
                and all(isinstance(layer, str) for layer in pad["layers"])
                and type(pad["shape"]) is int
                and type(pad["rotation_deg"]) in (int, float)
                and isfinite(pad["rotation_deg"]),
                "Native pad geometry/layer schema differs",
            )
            identities.append(pad["uuid"])
    for track in observation["tracks"]:
        _require(isinstance(track, dict), "Native copper observation must be an object")
        fields = {"uuid", "kind", "net", "start_nm", "end_nm", "width_nm", "layer", "locked"}
        _require(track.get("kind") in {"PCB_TRACK", "PCB_VIA"}, "Unknown native copper class")
        if track["kind"] == "PCB_VIA":
            fields.add("drill_nm")
        _require(
            set(track) == fields
            and _vector(track["start_nm"])
            and _vector(track["end_nm"])
            and type(track["width_nm"]) is int
            and type(track["locked"]) is bool
            and ("drill_nm" not in track or type(track["drill_nm"]) is int),
            "Native copper schema differs",
        )
        identities.append(track["uuid"])
    for drawing in observation["drawings"]:
        _require(
            isinstance(drawing, (list, tuple)) and len(drawing) == 2 and drawing[0] == "PCB_SHAPE",
            "Native drawing class/schema differs",
        )
        identities.append(drawing[1])
    _require(
        sum(len(fp["pads"]) for fp in observation["footprints"]) == 30
        and len(identities) == len(set(identities))
        and all(isinstance(identity, str) and identity for identity in identities),
        "Native object UUID inventory or multiplicity differs",
    )
    for reference, frozen in FOOTPRINTS.items():
        intent = EXPECTED[frozen["scene"]]
        matches = [
            fp
            for fp in observation["footprints"]
            if fp["ref"] == reference or fp["uuid"] == intent["uuid"]
        ]
        _require(len(matches) == 1, "Native selected footprint identity is ambiguous")
        fp = matches[0]
        _require(
            fp["ref"] == reference
            and fp["uuid"] == intent["uuid"]
            and fp["path"] == frozen["path"]
            and fp["value"] == frozen["value"]
            and fp["fpid"] == "fixture:R_0805"
            and fp["rotation_deg"] == 0
            and fp["layer"] == "F.Cu"
            and fp["locked"] is False
            and fp["dnp"] is False
            and {pad["number"] for pad in fp["pads"]} == {"1", "2"},
            "Native frozen footprint identity/properties differ",
        )
        for pad in fp["pads"]:
            wanted = frozen["pads"][pad["number"]]
            _require(
                pad["uuid"] == wanted["uuid"]
                and pad["net"] == wanted["net"]
                and pad["rotation_deg"] == 0,
                "Native selected pad UUID/net/rotation differs",
            )


@_consumer
def check_native(baseline_observation, actual_observation, baseline_path, actual_path, scene):
    """Compare complete native objects and runtime identity with one rigid translation."""
    board_check = check_board(baseline_path, actual_path, scene)
    _native(baseline_observation, baseline_path)
    _native(actual_observation, actual_path)
    expected = copy.deepcopy(baseline_observation)
    for reference, frozen in FOOTPRINTS.items():
        intent = EXPECTED[frozen["scene"]]
        fp = next(fp for fp in expected["footprints"] if fp["ref"] == reference)
        _require(fp["position_nm"] == intent["from_nm"], "Native baseline footprint pose differs")
        for pad in fp["pads"]:
            _require(
                pad["position_nm"] == frozen["pads"][pad["number"]]["position_nm"],
                "Native baseline pad world position differs",
            )
        if scene == frozen["scene"]:
            delta = [intent["to_nm"][axis] - intent["from_nm"][axis] for axis in (0, 1)]
            fp["position_nm"] = intent["to_nm"][:]
            for pad in fp["pads"]:
                pad["position_nm"] = [pad["position_nm"][axis] + delta[axis] for axis in (0, 1)]
    expected["input_sha256"] = board_check["inputs"]["actual"]["sha256"]
    _require(
        expected == actual_observation,
        "Complete native readback/runtime identity changed outside declared fp/pad translation",
    )
    return {
        "status": "passed",
        "scene": scene,
        "inputs": board_check["inputs"],
        "selected_uuid": EXPECTED[scene]["uuid"],
        "all_other_observed_fields_and_copper": "identical",
        "startup_and_all_module_bindings": "identical and checked against current dependency bytes",
        "reader_sha256": actual_observation["reader_sha256"],
        "pcbnew_wrapper_sha256": actual_observation["pcbnew_wrapper_sha256"],
        "unobserved_native_properties": "protected by complete saved-board comparison",
    }


def _finding_key(kind, severity, items):
    return (
        kind,
        severity,
        tuple(sorted((identity, tuple(position)) for identity, position in items)),
    )


def _findings(rows):
    _require(isinstance(rows, list), "Official DRC findings must be a list")
    result = []
    for row in rows:
        _require(
            isinstance(row, dict) and set(row) == {"type", "severity", "description", "items"},
            "Official DRC finding schema differs",
        )
        _require(
            isinstance(row["type"], str)
            and row["type"]
            and row["severity"] in INCLUDED_SEVERITIES
            and isinstance(row["description"], str)
            and isinstance(row["items"], list)
            and len(row["items"]) == 2,
            "Official DRC machine finding identity/severity/item multiplicity differs",
        )
        items = []
        for item in row["items"]:
            _require(
                isinstance(item, dict) and set(item) == {"uuid", "pos", "description"},
                "Official DRC item schema differs",
            )
            position = item["pos"]
            _require(
                isinstance(item["uuid"], str)
                and item["uuid"]
                and isinstance(item["description"], str)
                and isinstance(position, dict)
                and set(position) == {"x", "y"},
                "Official DRC item UUID/position schema differs",
            )
            items.append((item["uuid"], [_nm(position["x"]), _nm(position["y"])]))
        _require(items[0][0] != items[1][0], "Official DRC pair repeats one UUID")
        result.append(_finding_key(row["type"], row["severity"], items))
    return Counter(result)


def _drc(report, path):
    fields = {
        "$schema",
        "coordinate_units",
        "date",
        "ignored_checks",
        "included_severities",
        "kicad_version",
        "schematic_parity",
        "source",
        "unconnected_items",
        "violations",
    }
    _require(
        isinstance(report, dict) and set(report) == fields, "Official DRC report schema differs"
    )
    _require(
        report["$schema"] == "https://schemas.kicad.org/drc.v1.json"
        and report["coordinate_units"] == "mm"
        and report["kicad_version"] == "10.0.6"
        and report["source"] == Path(path).name
        and isinstance(report["date"], str)
        and report["schematic_parity"] == [],
        "Official DRC version/units/source differs or unexecuted parity contains findings",
    )
    ignored = report["ignored_checks"]
    _require(
        isinstance(ignored, list)
        and all(
            isinstance(row, dict)
            and set(row) == {"key", "description"}
            and isinstance(row["description"], str)
            for row in ignored
        )
        and [row["key"] for row in ignored] == IGNORED_CHECK_KEYS
        and report["included_severities"] == INCLUDED_SEVERITIES,
        "Official DRC ignored checks or included severities differ from frozen scope",
    )
    return {
        "violations": _findings(report["violations"]),
        "unconnected_items": _findings(report["unconnected_items"]),
    }


def _expected_findings(scene):
    pairs = copy.deepcopy(BASELINE_OPEN_PAIRS)
    violations = Counter()
    if scene == "negative":
        pads = FOOTPRINTS["R1"]["pads"]
        pairs[1][0] = (pads["2"]["uuid"], [99412500, 100000000])
        pairs.append(
            [(SIG_TRACK, [99087500, 100000000]), (pads["1"]["uuid"], [97587500, 100000000])]
        )
        conflict = [(pads["2"]["uuid"], [99412500, 100000000]), (SIG_TRACK, [99087500, 100000000])]
        violations = Counter(
            _finding_key(kind, "error", conflict)
            for kind in ("shorting_items", "solder_mask_bridge")
        )
    elif scene == "positive":
        pairs[4][0] = (FOOTPRINTS["R4"]["pads"]["2"]["uuid"], [100412500, 110000000])
    return {
        "violations": violations,
        "unconnected_items": Counter(
            _finding_key("unconnected_items", "error", pair) for pair in pairs
        ),
    }


def _known_joint_proof(baseline_board, actual_board, scene):
    """Prove just the declared three-object joint, independently of the product."""
    _scene(scene)

    def objects(board):
        found = {}
        for kind in ("segment", "via"):
            for node in children(board, kind):
                identity = json.loads(one(node, "uuid")[1])
                if identity in {SIG_TRACK, SIG_BACK_TRACK, SIG_VIA}:
                    _require(identity not in found, "Known joint UUID is duplicated")
                    found[identity] = node
        _require(len(found) == 3, "Known joint is missing a declared copper UUID")
        return found

    before, after = objects(baseline_board), objects(actual_board)
    _require(before == after, "Known joint copper bytes/attributes changed")

    def exact_children(node, kind, names):
        _require(
            node[0] == kind
            and len(node[1:]) == len(names)
            and all(isinstance(row, list) and row and row[0] in names for row in node[1:])
            and {row[0] for row in node[1:]} == names,
            "Known joint requires simple copper without special attributes",
        )

    for board in (baseline_board, actual_board):
        copper = []
        for row in one(board, "layers")[1:]:
            if isinstance(row, list) and len(row) >= 3 and json.loads(row[1]).endswith(".Cu"):
                copper.append((row[0], json.loads(row[1]), row[2]))
        _require(
            len(copper) == 2 and set(copper) == {("0", "F.Cu", "signal"), ("2", "B.Cu", "signal")},
            "Known joint F.Cu/B.Cu must be the actually enabled copper layers",
        )
    via = before[SIG_VIA]
    exact_children(via, "via", {"at", "size", "drill", "layers", "net", "uuid"})
    center = [_nm(atom) for atom in one(via, "at")[1:]]
    size, drill = _nm(one(via, "size")[1]), _nm(one(via, "drill")[1])
    _require(
        center == JOINT_POINT_NM
        and size > drill > 0
        and one(via, "net") == ["net", '"/SIG"']
        and one(via, "layers") == ["layers", '"F.Cu"', '"B.Cu"'],
        "Known joint via center, annular copper, net or active span differs",
    )
    for identity, layer in ((SIG_TRACK, "F.Cu"), (SIG_BACK_TRACK, "B.Cu")):
        node = before[identity]
        exact_children(node, "segment", {"start", "end", "width", "layer", "net", "uuid"})
        start = [_nm(atom) for atom in one(node, "start")[1:]]
        end = [_nm(atom) for atom in one(node, "end")[1:]]
        width = _nm(one(node, "width")[1])
        _require(
            len(start) == len(end) == 2
            and start == JOINT_REPORT_POINTS_NM[identity]
            and start != end
            and JOINT_POINT_NM in (start, end)
            and width > 0
            and one(node, "net") == ["net", '"/SIG"']
            and one(node, "layer") == ["layer", json.dumps(layer)],
            "Known joint track endpoint, width, net or shared active copper differs",
        )
        other = end if start == JOINT_POINT_NM else start
        length_squared = sum((other[axis] - center[axis]) ** 2 for axis in (0, 1))
        _require(
            4 * length_squared > size * size,
            "Known track extent does not reach outside via annulus",
        )
    r10_before = [
        fp
        for fp in children(baseline_board, "footprint")
        if _properties(fp).get("Reference") == "R10"
    ]
    r10_after = [
        fp
        for fp in children(actual_board, "footprint")
        if _properties(fp).get("Reference") == "R10"
    ]
    _require(
        len(r10_before) == len(r10_after) == 1 and r10_before == r10_after,
        "Known joint R10 parent changed",
    )
    r10 = r10_before[0]
    pose = one(r10, "at")
    pads = [pad for pad in children(r10, "pad") if one(pad, "uuid")[1] == json.dumps(R10_PAD1)]
    _require(
        len(pads) == 1
        and one(r10, "layer") == ["layer", '"F.Cu"']
        and (len(pose) == 3 or Decimal(pose[3]) == 0),
        "Known joint counterpart pad parent/layer/angle differs",
    )
    pad = pads[0]
    local = one(pad, "at")
    _require(
        pad[1] == '"1"'
        and one(pad, "net") == ["net", '"/SIG"']
        and '"F.Cu"' in one(pad, "layers")[1:]
        and (len(local) == 3 or Decimal(local[3]) == 0)
        and [_nm(pose[axis]) + _nm(local[axis]) for axis in (1, 2)] == R10_POINT_NM,
        "Known joint counterpart UUID/net/world pose differs",
    )
    if scene == "positive":
        moved = _selected(actual_board, "positive")
        _require(
            all(one(pad, "net")[1] != '"/SIG"' for pad in children(moved, "pad")),
            "Positive move is not independent of the known /SIG joint",
        )
    return {
        "status": "passed",
        "joint_nm": JOINT_POINT_NM[:],
        "copper_uuids": [SIG_TRACK, SIG_BACK_TRACK, SIG_VIA],
        "active_shared_copper": ["F.Cu", "B.Cu"],
        "via_size_nm": size,
        "via_drill_nm": drill,
        "counterpart_uuid": R10_PAD1,
        "counterpart_nm": R10_POINT_NM[:],
        "report_reference_positions_nm": copy.deepcopy(JOINT_REPORT_POINTS_NM),
        "geometry_and_parent": "unchanged; simple valid annular copper and track extents proved",
    }


def _normalize_joint_open(findings):
    """Canonicalize one endpoint of one declared obligation; never merge a net."""
    result, aliases = Counter(), []
    canonical = _finding_key("unconnected_items", "error", BASELINE_OPEN_PAIRS[0])
    for key, count in findings.items():
        kind, severity, items = key
        points = dict(items)
        counterparts = set(points) - {R10_PAD1}
        valid = (
            kind == "unconnected_items"
            and severity == "error"
            and len(items) == 2
            and points.get(R10_PAD1) == tuple(R10_POINT_NM)
            and len(counterparts) == 1
        )
        identity = next(iter(counterparts)) if valid else None
        if (
            valid
            and identity in JOINT_REPORT_POINTS_NM
            and points[identity] == tuple(JOINT_REPORT_POINTS_NM[identity])
        ):
            result[canonical] += count
            if key != canonical:
                aliases.append(
                    {
                        "uuid": identity,
                        "report_point_nm": list(points[identity]),
                        "multiplicity": count,
                    }
                )
        else:
            result[key] += count
    return result, aliases


@_consumer
def check_drc(baseline_report, actual_report, baseline_path, actual_path, scene):
    """Check frozen machine findings; parity is explicitly unexecuted in this scope."""
    intent = _scene(scene)
    board_check = check_board(baseline_path, actual_path, scene)
    baseline = _drc(baseline_report, baseline_path)
    actual = _drc(actual_report, actual_path)
    proof = _known_joint_proof(_board(baseline_path), _board(actual_path), scene)
    baseline_open, baseline_aliases = _normalize_joint_open(baseline["unconnected_items"])
    actual_open, actual_aliases = _normalize_joint_open(actual["unconnected_items"])
    baseline_compared = dict(baseline, unconnected_items=baseline_open)
    actual_compared = dict(actual, unconnected_items=actual_open)
    _require(
        baseline_compared == _expected_findings("baseline"),
        "Fresh baseline DRC machine relations differ",
    )
    _require(
        actual_compared == _expected_findings(scene), "Fresh scene DRC machine relations differ"
    )
    return {
        "status": "passed",
        "scene": scene,
        "inputs": board_check["inputs"],
        "report_source_basename": {
            "baseline": baseline_report["source"],
            "actual": actual_report["source"],
        },
        "report_input_binding": (
            "basename checked here; controller must retain each fresh invocation with the "
            "absolute input path and matching input hash; DRC JSON has no input digest"
        ),
        "violations": intent["violations"],
        "unconnected_items": intent["unconnected_items"],
        "machine_relationships": (
            "type, severity, UUID, exact integral nanometre position, multiplicity"
        ),
        "ignored_report_fields": ["date", "finding/item/ignored-check descriptions"],
        "ignored_checks": IGNORED_CHECK_KEYS[:],
        "included_severities": INCLUDED_SEVERITIES[:],
        "schematic_parity": "not_executed",
        "parity_pass_claim": False,
        "whole_board_compliance_claim": False,
        "joint_representation": {
            "policy": copy.deepcopy(JOINT_POLICY),
            "proof": proof,
            "raw_baseline_unconnected": copy.deepcopy(baseline_report["unconnected_items"]),
            "raw_actual_unconnected": copy.deepcopy(actual_report["unconnected_items"]),
            "baseline_aliases": baseline_aliases,
            "actual_aliases": actual_aliases,
            "normalized_obligation_multiplicity": 1,
        },
        "negative_original_open_obligations": (
            "seven retained; only one proved known /SIG joint endpoint may change representative, "
            "R1.2 follows translation; one new /SIG track-to-R1.1 open"
            if scene == "negative"
            else None
        ),
    }


def _public_files(files, *, original_board):
    _require(
        isinstance(files, dict) and set(files) == set(ORIGINAL_HASHES),
        "Public project must bind exactly the original eight file names",
    )
    result = {}
    for name, row in files.items():
        _require(
            isinstance(row, dict) and set(row) == {"path", "sha256"},
            "Public project file binding schema differs",
        )
        actual = _binding(row["path"])
        _require(
            _digest(row["sha256"]) and row["sha256"] == actual["sha256"],
            "Public project file digest differs: " + name,
        )
        if original_board or name != "edit.kicad_pcb":
            _require(
                actual["sha256"] == ORIGINAL_HASHES[name],
                "Protected original public project file changed: " + name,
            )
        result[name] = actual
    root = Path(result["edit.kicad_pcb"]["path"]).parent
    _require(
        all(Path(row["path"]) == (root / name).resolve() for name, row in result.items()),
        "Public project files do not identify one exact eight-file project",
    )
    return result


def _public_data(envelope):
    _require(
        isinstance(envelope, dict)
        and envelope.get("ok") is True
        and isinstance(envelope.get("data"), dict),
        "A successful public JSON envelope is required",
    )
    return envelope["data"]


def _public_captured_project_inputs(binding, files):
    """Bind the six project inputs actually collected by the move preflight.

    The collector reads R_0805 and SO8, the two FPIDs used by this board. It
    records R_0603 only in directory membership, not as a captured file. The
    complete eight-file outer manifest still protects that unused library file.
    External configuration and tool records are bound by the controller.
    """
    board = Path(files["edit.kicad_pcb"]["path"])
    project = board.parent
    expected = {
        board: BASELINE_SHA256,
        Path(files["edit.kicad_pro"]["path"]): files["edit.kicad_pro"]["sha256"],
        board.with_suffix(".kicad_dru"): None,
        Path(files["fp-lib-table"]["path"]): files["fp-lib-table"]["sha256"],
        Path(files["fixture.pretty/R_0805.kicad_mod"]["path"]): files[
            "fixture.pretty/R_0805.kicad_mod"
        ]["sha256"],
        Path(files["fixture.pretty/SO8.kicad_mod"]["path"]): files["fixture.pretty/SO8.kicad_mod"][
            "sha256"
        ],
    }
    rows = binding["files"]
    _require(isinstance(rows, list), "Public preflight captured file records are missing")
    seen, project_seen = set(), set()
    for row in rows:
        _require(
            isinstance(row, dict)
            and set(row) == {"path", "resolved", "present", "sha256", "identity"},
            "Public preflight captured file schema differs",
        )
        requested, resolved = Path(row["path"]), Path(row["resolved"])
        _require(
            requested.is_absolute()
            and resolved.is_absolute()
            and requested.resolve() == resolved.resolve(),
            "Public preflight captured file locators disagree",
        )
        target = resolved.resolve()
        _require(
            target not in seen and type(row["present"]) is bool,
            "Public preflight captured file identity is duplicated or incomplete",
        )
        seen.add(target)
        if row["present"]:
            identity = row["identity"]
            _require(
                _digest(row["sha256"])
                and isinstance(identity, dict)
                and set(identity) == {"device", "inode", "size", "mtime_ns"}
                and all(type(value) is int for value in identity.values())
                and identity["size"] >= 0,
                "Public preflight captured file digest/stat identity is incomplete",
            )
        else:
            _require(
                row["sha256"] is None and row["identity"] is None,
                "Absent public preflight input has a digest or stat identity",
            )
        if target.is_relative_to(project) or requested.is_relative_to(project):
            _require(
                target in expected, "Unexpected project rule/table/library in public preflight"
            )
            project_seen.add(target)
            present = expected[target] is not None
            _require(
                row["present"] is present and row["sha256"] == expected[target],
                "Public preflight captured project bytes/presence differ: " + str(target),
            )
            if not present:
                _require(
                    not target.exists() and not target.is_symlink(),
                    "An undeclared current project custom-rule file is present",
                )
    _require(
        project_seen == set(expected),
        "Public preflight did not capture exactly PCB/PRO/absent DRU/table/two used libraries",
    )


def _public_preview(envelope, files, scene, candidate_sha):
    plan = _public_data(envelope)["preview"]
    intent = EXPECTED[scene]
    _require(isinstance(plan, dict), "Public preview must be an object")
    board = Path(files["edit.kicad_pcb"]["path"])
    target = plan["target"]
    _require(
        isinstance(target, dict)
        and target.get("locator") == board.as_uri()
        and target.get("mode") == "file"
        and target.get("engine_id") == "kicad-cli.saved-board"
        and target.get("object_id") == board.as_uri()
        and target.get("instance_id") is None,
        "Public preview target identifies another project",
    )
    _require(
        plan["base_snapshot"]["revision"] == BASELINE_SHA256,
        "Public preview baseline snapshot differs",
    )
    for key in ("operation_id", "plan_id", "capability_id"):
        _require(
            isinstance(plan[key], str) and plan[key], "Public preview identity missing: " + key
        )
    changes = plan["changes"]
    _require(
        isinstance(changes, list) and len(changes) == 1 and isinstance(changes[0], dict),
        "Exactly one public footprint translation must be planned",
    )
    change = changes[0]
    before, after = change["before"], change["after"]["position_mm"]
    _require(
        change["action"] == "translate"
        and change["resource"] == "footprint"
        and change["id"] == intent["uuid"]
        and before["id"] == intent["uuid"]
        and before["ref"] == intent["reference"]
        and _vector(before["position_nm"])
        and before["position_nm"] == intent["from_nm"]
        and isinstance(after, list)
        and len(after) == 2
        and all(type(value) in (int, float) for value in after)
        and [_nm(value) for value in after] == intent["to_nm"],
        "Public preview differs from the exact selected frozen translation",
    )
    preflight = plan["extensions"]["preflight"]
    _require(
        isinstance(preflight, dict)
        and type(preflight["schema_version"]) is int
        and preflight["schema_version"] == 1
        and preflight["status"] == ("passed" if scene == "positive" else "blocked")
        and plan["can_apply"] is (scene == "positive")
        and preflight["engineering_write_started"] is False
        and preflight["baseline_sha256"] == BASELINE_SHA256
        and preflight["candidate_sha256"] == candidate_sha
        and preflight["staging"]["candidate_sha256"] == candidate_sha,
        "Public preview preflight status or bound input/candidate differs",
    )
    _require(
        isinstance(preflight["input_binding"]["board"], str)
        and Path(preflight["input_binding"]["board"]).is_absolute()
        and Path(preflight["input_binding"]["board"]).resolve() == board
        and preflight["staging"]["baseline_sha256"] == BASELINE_SHA256,
        "Public preflight captured target or staged baseline differs from preview",
    )
    _public_captured_project_inputs(preflight["input_binding"], files)
    return plan


def _public_manifest(data, files):
    board = Path(files["edit.kicad_pcb"]["path"])
    _require(
        isinstance(data.get("board"), str)
        and Path(data["board"]).is_absolute()
        and Path(data["board"]).resolve() == board,
        "Public readback board differs",
    )
    observation = data["observation"]
    manifest = observation["inputs"]
    expected = {
        Path(files[name]["path"]): files[name]["sha256"]
        for name in ("edit.kicad_pcb", "edit.kicad_pro", "edit.kicad_sch", "edit_child.kicad_sch")
    }
    expected[board.with_suffix(".kicad_dru")] = None
    _require(
        isinstance(manifest, list) and len(manifest) == 5,
        "Public readback requires the exact five captured input records",
    )
    seen = set()
    for row in manifest:
        _require(
            isinstance(row, dict) and set(row) == {"path", "resolved", "present", "sha256"},
            "Public captured input record schema differs",
        )
        path, resolved = Path(row["path"]), Path(row["resolved"])
        _require(
            path.is_absolute() and resolved.is_absolute() and path.resolve() == resolved.resolve(),
            "Public captured input locators disagree",
        )
        resolved = resolved.resolve()
        _require(
            resolved in expected and resolved not in seen,
            "Public captured input set is duplicated or targets another project",
        )
        seen.add(resolved)
        present = expected[resolved] is not None
        _require(
            row["present"] is present
            and row["sha256"] == expected[resolved]
            and resolved.exists() is present
            and not resolved.is_symlink(),
            "Public captured input presence/digest differs",
        )
    _require(seen == set(expected), "Public captured input set is incomplete")
    revision = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    snapshot, source = observation["snapshot"], observation["source"]
    _require(
        snapshot["revision"] == revision
        and snapshot["basis"] == "complete captured input manifest"
        and snapshot["observed_at"] == source["completed_at"]
        and isinstance(source["completed_at"], str)
        and source["completed_at"]
        and observation["state"] == "saved_files"
        and observation["input_drift_checked"] is True
        and observation["live_editor_observed"] is False
        and source["completed"] is True
        and source["official_native"] is False
        and source["origin"] == "kicad_cli.saved-project-comparison",
        "Public readback snapshot/source/completion binding differs",
    )
    return revision


def _public_selected(data, board_path):
    board = _board(board_path)
    rows = data["observed_footprints"]
    _require(
        isinstance(rows, list) and len(rows) == 12 and all(isinstance(row, dict) for row in rows),
        "Public readback footprint inventory is incomplete",
    )
    pairs = [(row["id"], row["ref"]) for row in rows]
    saved_pairs = [
        (json.loads(one(fp, "uuid")[1]), _properties(fp)["Reference"])
        for fp in children(board, "footprint")
    ]
    _require(
        len({identity for identity, _ in pairs}) == 12
        and len({ref for _, ref in pairs}) == 12
        and sorted(pairs) == sorted(saved_pairs),
        "Public readback footprint UUID/reference inventory differs from actual saved board",
    )
    selected = _selected(board, "positive")
    actual = next(row for row in rows if row["ref"] == "R4")
    saved_pose = one(selected, "at")
    props = _properties(selected)
    attributes = sorted(word for attr in children(selected, "attr") for word in attr[1:])
    _require(
        actual["id"] == EXPECTED["positive"]["uuid"]
        and actual["ref"] == "R4"
        and actual["path"] == json.loads(one(selected, "path")[1])
        and actual["value"] == props["Value"]
        and actual["fields"] == props
        and actual["fpid"] == json.loads(selected[1])
        and actual["layer"] == json.loads(one(selected, "layer")[1])
        and _vector(actual["position_nm"])
        and actual["position_nm"]
        == [_nm(value) for value in saved_pose[1:3]]
        == EXPECTED["positive"]["to_nm"]
        and type(actual["rotation_deg"]) in (int, float)
        and actual["rotation_deg"] == 0
        and actual["attributes"] == attributes,
        "Public R4 identity/fields/pose differ from actual saved board",
    )
    pads = actual["pads"]
    saved_pads = children(selected, "pad")
    _require(
        isinstance(pads, list)
        and len(pads) == len(saved_pads) == 2
        and len({pad["id"] for pad in pads}) == 2
        and len({pad["number"] for pad in pads}) == 2,
        "Public R4 pad inventory is missing or duplicated",
    )
    world = {}
    for pad in pads:
        matches = [row for row in saved_pads if json.loads(one(row, "uuid")[1]) == pad["id"]]
        _require(len(matches) == 1, "Public R4 pad UUID differs from saved board")
        saved = matches[0]
        number = json.loads(saved[1])
        local = [_nm(value) for value in one(saved, "at")[1:3]]
        _require(
            pad["number"] == number
            and number in FOOTPRINTS["R4"]["pads"]
            and pad["net"] == json.loads(one(saved, "net")[1])
            and pad["kind"] == saved[2]
            and pad["shape"] == saved[3]
            and _vector(pad["position_nm"])
            and pad["position_nm"] == local
            and type(pad["rotation_deg"]) in (int, float)
            and pad["rotation_deg"] == 0,
            "Public R4 pad local UUID/net/geometry differs from saved board",
        )
        world[number] = [actual["position_nm"][axis] + local[axis] for axis in (0, 1)]
        _require(
            world[number]
            == [
                FOOTPRINTS["R4"]["pads"][number]["position_nm"][0] - 500000,
                FOOTPRINTS["R4"]["pads"][number]["position_nm"][1],
            ],
            "Public R4 pad local-to-world translation differs from fixed intent",
        )
    return world


@_consumer
def check_public_move_chain(preview, confirm, readback, positive_files, original_hashes):
    """Bind the public R4 move and readback chain to the actual eight saved files."""
    _require(original_hashes == ORIGINAL_HASHES, "Original eight-file public input hashes differ")
    files = _public_files(positive_files, original_board=False)
    actual_sha = files["edit.kicad_pcb"]["sha256"]
    plan = _public_preview(preview, files, "positive", actual_sha)
    receipt = _public_data(confirm)["receipt"]
    _require(isinstance(receipt, dict), "Public confirm receipt must be an object")
    for key in ("target", "operation_id", "plan_id", "capability_id"):
        _require(receipt[key] == plan[key], "Public confirm identity differs from preview: " + key)
    _require(
        receipt["snapshot_before"] == plan["base_snapshot"]
        and receipt["snapshot_before"]["revision"] == BASELINE_SHA256
        and receipt["snapshot_after"]["revision"] == actual_sha
        and receipt["outcome"]["execution"] == "completed"
        and receipt["outcome"]["change"] == "applied"
        and receipt["outcome"]["persistence"] == "saved"
        and receipt["outcome"]["verification"]["status"] == "passed"
        and receipt["extensions"]["failures"] == []
        and json.dumps(receipt["extensions"]["preflight"], sort_keys=True, allow_nan=False)
        == json.dumps(plan["extensions"]["preflight"], sort_keys=True, allow_nan=False),
        "Public confirm snapshots/outcome/preflight do not match the unique saved move",
    )
    changed = receipt["changed_items"]
    _require(
        isinstance(changed, list)
        and len(changed) == 1
        and changed[0]["id"] == EXPECTED["positive"]["uuid"],
        "Public confirm changed-item set is not exactly R4",
    )
    data = _public_data(readback)
    revision = _public_manifest(data, files)
    world = _public_selected(data, Path(files["edit.kicad_pcb"]["path"]))
    _require(
        _public_files(positive_files, original_board=False) == files,
        "Public project changed during receipt/readback comparison",
    )
    return {
        "status": "passed",
        "scene": "positive",
        "files": files,
        "operation_id": plan["operation_id"],
        "plan_id": plan["plan_id"],
        "capability_id": plan["capability_id"],
        "readback_snapshot_revision": revision,
        "R4_pad_world_positions_nm": world,
        "protected_other_seven_files": "original bytes",
        "official_native_claim": False,
        "independent_complete_board_and_native_checks": "required separately",
    }


@_consumer
def check_negative_preview(preview, negative_project_files, candidate_file):
    """Validate a blocked R1 preview and an exported candidate without reading its origin."""
    files = _public_files(negative_project_files, original_board=True)
    _require(
        isinstance(candidate_file, dict)
        and set(candidate_file) == {"path", "sha256", "origin"}
        and isinstance(candidate_file["origin"], str)
        and candidate_file["origin"],
        "Exported negative candidate needs path/hash and a public origin string",
    )
    candidate = _binding(candidate_file["path"])
    _require(
        candidate["sha256"] == candidate_file["sha256"],
        "Exported negative candidate digest differs",
    )
    plan = _public_preview(preview, files, "negative", candidate["sha256"])
    _require(
        plan["extensions"]["preflight"]["staging"]["stages"]["candidate"]["board"]
        == candidate_file["origin"],
        "Exported negative candidate origin string differs",
    )
    board_check = check_board(files["edit.kicad_pcb"]["path"], candidate["path"], "negative")
    _require(
        _public_files(negative_project_files, original_board=True) == files,
        "Blocked preview original project changed during comparison",
    )
    return {
        "status": "passed",
        "scene": "negative",
        "can_apply": False,
        "files": files,
        "operation_id": plan["operation_id"],
        "plan_id": plan["plan_id"],
        "candidate": dict(candidate, origin=candidate_file["origin"]),
        "candidate_complete_structure": board_check,
        "original_eight_files": "unchanged",
        "confirm_executed_by_checker": False,
        "candidate_origin_policy": "compared as a public string only; never opened",
    }
