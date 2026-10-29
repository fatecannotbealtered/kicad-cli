"""Independent consumers for one frozen R1 translation; no product imports."""

import copy
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from bench.saved_scene_checks import children, one, sexp, sha

R1_UUID = "2d4aa815-63ab-5983-950d-99c5287f15a2"
SCH_PATH = "/43ddee60-4186-5f8a-92d6-1101a2bbfaed"
PADS = {
    "1": ("b6399d66-3ccf-5183-bd56-8dee1659878f", "/SIG", [97587500, 100000000]),
    "2": ("4af096ca-59e1-5018-a93c-c3aaa5cd3957", "/OUT", [99412500, 100000000]),
}
INTENT = {
    "reference": "R1",
    "uuid": R1_UUID,
    "before_position_nm": [100000000, 100000000],
    "after_position_nm": [98500000, 100000000],
    "rotation_deg": 0,
    "layer": "F.Cu",
    "locked": False,
    "value": "10k",
    "fpid": "fixture:R_0805",
    "schematic_path": SCH_PATH,
    "only_mutable_saved_structure": "the selected footprint top-level at x/y atoms",
    "copper_repair": "none; all three tracks and two vias remain fixed",
    "geometry_check": "not_performed by move; independent DRC and full images separate",
}


def decimal_nm(atom):
    try:
        value = Decimal(atom) * 1000000
    except InvalidOperation as exc:
        raise ValueError("Invalid millimetre coordinate") from exc
    if not value.is_finite() or value != value.to_integral_value():
        raise ValueError("Coordinate is not an exact integral nanometre value")
    return int(value)


def selected(board):
    matches = [
        fp for fp in children(board, "footprint") if one(fp, "uuid")[1] == json.dumps(R1_UUID)
    ]
    if len(matches) != 1:
        raise ValueError("Expected exactly one frozen R1 UUID")
    return matches[0]


def check_baseline_board(path):
    board = sexp(Path(path).read_text(encoding="utf-8"))
    fp = selected(board)
    props = {json.loads(row[1]): json.loads(row[2]) for row in children(fp, "property")}
    if (
        fp[1] != '"fixture:R_0805"'
        or props.get("Reference") != "R1"
        or props.get("Value") != "10k"
        or one(fp, "path")[1] != json.dumps(SCH_PATH)
        or one(fp, "layer")[1] != '"F.Cu"'
        or "locked" in fp
        or children(fp, "locked")
        or [decimal_nm(atom) for atom in one(fp, "at")[1:3]] != [100000000, 100000000]
        or (len(one(fp, "at")) > 3 and Decimal(one(fp, "at")[3]) != 0)
    ):
        raise ValueError("Frozen R1 identity/pose/lock/value precondition differs")
    counts = {
        "footprints": len(children(board, "footprint")),
        "tracks": len(children(board, "segment")),
        "vias": len(children(board, "via")),
        "zones": len(children(board, "zone")),
        "board_edges": sum(
            bool(children(row, "layer")) and one(row, "layer")[1] == '"Edge.Cuts"'
            for row in board
            if isinstance(row, list)
        ),
    }
    if counts != {"footprints": 12, "tracks": 3, "vias": 2, "zones": 0, "board_edges": 4}:
        raise ValueError("Frozen board object inventory differs")
    return {"status": "passed", "input_sha256": sha(path), "counts": counts, "intent": INTENT}


def compare_board(baseline, actual):
    check_baseline_board(baseline)
    old = sexp(Path(baseline).read_text(encoding="utf-8"))
    new = sexp(Path(actual).read_text(encoding="utf-8"))
    before, after = one(selected(old), "at"), one(selected(new), "at")
    if len(before) != len(after) or [decimal_nm(atom) for atom in after[1:3]] != [
        98500000,
        100000000,
    ]:
        raise ValueError("Saved translation differs from exact frozen nanometre intent")
    after[1:3] = before[1:3]
    if old != new:
        raise ValueError("Protected complete board S-expression changed outside selected at x/y")
    return {
        "status": "passed",
        "baseline_sha256": sha(baseline),
        "actual_sha256": sha(actual),
        "units": "exact integral nanometres from independent Decimal millimetre conversion",
        "all_remaining_atoms_and_child_order": "identical",
        "formatting": "whitespace is not a S-expression atom; complete raw hashes retained",
        "protected": (
            "11 other footprints; every angle/layer/pad/net/UUID/path; copper; edges; setup"
        ),
    }


def compare_native(before, after, baseline_path, actual_path):
    check_native_baseline(before, baseline_path)
    validate_native_observation(after, actual_path)
    for observation, path in ((before, baseline_path), (after, actual_path)):
        if (
            not observation["native_version"].startswith("10.0.6")
            or not observation["input_unchanged"]
            or observation["input_sha256"] != sha(path)
            or len(observation["footprints"]) != 12
            or len(observation["tracks"]) != 5
            or observation["zones"]
        ):
            raise ValueError("Native readback input/version/inventory binding differs")
    expected = copy.deepcopy(before)
    refs = {fp["ref"]: fp for fp in expected["footprints"]}
    fp = refs["R1"]
    if (
        fp["uuid"] != R1_UUID
        or fp["position_nm"] != INTENT["before_position_nm"]
        or fp["rotation_deg"] != 0
        or fp["layer"] != "F.Cu"
        or fp["locked"]
        or fp["value"] != "10k"
        or fp["fpid"] != "fixture:R_0805"
        or fp["path"] != SCH_PATH
    ):
        raise ValueError("Native R1 baseline identity/pose differs")
    fp["position_nm"] = INTENT["after_position_nm"]
    if {pad["number"] for pad in fp["pads"]} != set(PADS):
        raise ValueError("Native R1 pad selection differs")
    for pad in fp["pads"]:
        identity, net, wanted = PADS[pad["number"]]
        if (
            pad["uuid"] != identity
            or pad["net"] != net
            or pad["position_nm"] != [wanted[0] + 1500000, wanted[1]]
        ):
            raise ValueError("Native baseline pad UUID/network/world position differs")
        pad["position_nm"] = wanted
    for key in ("input_sha256", "startup"):
        expected[key] = after[key]
    if expected != after:
        raise ValueError("Native complete readback differs outside exact R1/pad world translation")
    return {
        "status": "passed",
        "R1_position_nm": INTENT["after_position_nm"],
        "pad_world_positions_nm": {number: value[2] for number, value in PADS.items()},
        "all_other_observed_fields_and_copper": "identical",
        "unobserved_native_properties": "protected by independent complete board S-expression",
    }


def validate_native_observation(observation, path):
    required = {
        "native_version",
        "input_unchanged",
        "input_sha256",
        "reader_sha256",
        "pcbnew_wrapper_sha256",
        "footprints",
        "tracks",
        "zones",
        "drawings",
        "startup",
        "scope",
    }
    footprint_fields = {
        "ref",
        "uuid",
        "path",
        "value",
        "fields",
        "dnp",
        "fpid",
        "position_nm",
        "rotation_deg",
        "layer",
        "locked",
        "pads",
    }
    if (
        not isinstance(observation, dict)
        or set(observation) != required
        or not observation["native_version"].startswith("10.0.6")
        or observation["input_unchanged"] is not True
        or observation["input_sha256"] != sha(path)
        or not isinstance(observation["footprints"], list)
        or len(observation["footprints"]) != 12
        or not isinstance(observation["tracks"], list)
        or len(observation["tracks"]) != 5
        or observation["zones"] != []
        or not isinstance(observation["drawings"], list)
        or len(observation["drawings"]) != 4
        or not isinstance(observation["startup"], dict)
    ):
        raise ValueError("Critical native reader schema/input/version/inventory is incomplete")
    for fp in observation["footprints"]:
        if (
            not isinstance(fp, dict)
            or set(fp) != footprint_fields
            or not isinstance(fp["pads"], list)
        ):
            raise ValueError("Critical native footprint observation is incomplete")
    if (
        len({fp["uuid"] for fp in observation["footprints"]}) != 12
        or len({fp["ref"] for fp in observation["footprints"]}) != 12
    ):
        raise ValueError("Critical native footprint identities are ambiguous")


def check_native_baseline(observation, path):
    validate_native_observation(observation, path)
    selected = [
        fp for fp in observation["footprints"] if fp["uuid"] == R1_UUID or fp["ref"] == "R1"
    ]
    if len(selected) != 1:
        raise ValueError("Native baseline R1 selection is ambiguous")
    fp = selected[0]
    if (
        fp["uuid"] != R1_UUID
        or fp["ref"] != "R1"
        or fp["path"] != SCH_PATH
        or fp["value"] != "10k"
        or fp["fpid"] != "fixture:R_0805"
        or fp["position_nm"] != [100000000, 100000000]
        or fp["rotation_deg"] != 0
        or fp["layer"] != "F.Cu"
        or fp["locked"] is not False
        or fp["dnp"] is not False
        or len(fp["pads"]) != 2
        or {pad["number"] for pad in fp["pads"]} != set(PADS)
    ):
        raise ValueError("Critical native baseline R1 pose/identity differs")
    for pad in fp["pads"]:
        identity, net, world_after = PADS[pad["number"]]
        if (
            pad["uuid"] != identity
            or pad["net"] != net
            or pad["position_nm"] != [world_after[0] + 1500000, world_after[1]]
            or pad["rotation_deg"] != 0
        ):
            raise ValueError("Critical native baseline pad identity/network/world pose differs")
    return {"status": "passed", "input_sha256": observation["input_sha256"]}


def compare_drc(before, after):
    validate_drc_observation(before)
    validate_drc_observation(after)
    for report in (before, after):
        if report["coordinate_units"] != "mm" or report["kicad_version"] != "10.0.6":
            raise ValueError("Official DRC units/version differ")
    if (
        len(before["violations"]) != 0
        or len(before["unconnected_items"]) != 7
        or before["schematic_parity"]
    ):
        raise ValueError("Actual frozen baseline DRC counters differ")
    if len(before["ignored_checks"]) != 5 or before["ignored_checks"] != after["ignored_checks"]:
        raise ValueError("DRC ignore checks changed")
    if before["included_severities"] != after["included_severities"]:
        raise ValueError("DRC included severities changed")
    if after["schematic_parity"]:
        raise ValueError("Move changed schematic parity")
    summaries = {
        name: {
            key: len(report[key])
            for key in ("violations", "unconnected_items", "schematic_parity", "ignored_checks")
        }
        for name, report in (("before", before), ("after", after))
    }
    return {
        "status": "observed; no clean-DRC or completed-routing claim",
        **summaries,
        "new_unconnected_count_delta": summaries["after"]["unconnected_items"] - 7,
        "full_before_findings": before,
        "full_after_findings": after,
        "copper": "unchanged by complete board and native comparisons; explicit follow-up required",
    }


def validate_drc_observation(report):
    if (
        not isinstance(report, dict)
        or report.get("coordinate_units") != "mm"
        or report.get("kicad_version") != "10.0.6"
        or any(
            not isinstance(report.get(key), list)
            for key in (
                "violations",
                "unconnected_items",
                "schematic_parity",
                "ignored_checks",
                "included_severities",
            )
        )
    ):
        raise ValueError("Critical official DRC schema/units/version is incomplete")
    if report["schematic_parity"]:
        raise ValueError("Official DRC schematic parity is not empty")


def check_public_readback(data, actual_sha, engineer_board):
    """Bind the real project-parity manifest and selected public saved object.

    The public snapshot hashes its complete captured manifest, not just the PCB.
    Public pad positions are footprint-local; pcbnew world positions remain a
    separate independent consumer and are never inferred from display decimals.
    """
    board = Path(engineer_board).resolve()
    observation = data["observation"]
    source, snapshot = observation["source"], observation["snapshot"]
    manifest = observation["inputs"]
    required = {
        board,
        board.with_suffix(".kicad_pro"),
        board.with_suffix(".kicad_dru"),
        board.with_suffix(".kicad_sch"),
        board.parent / "edit_child.kicad_sch",
    }
    if not isinstance(manifest, list) or len(manifest) != len(required):
        raise ValueError("Public readback complete input set differs")
    seen = set()
    for row in manifest:
        if set(row) != {"path", "resolved", "present", "sha256"}:
            raise ValueError("Public readback manifest entry schema differs")
        path = Path(row["path"])
        resolved = Path(row["resolved"])
        if (
            not path.is_absolute()
            or not resolved.is_absolute()
            or path.resolve() != resolved.resolve()
            or resolved.resolve() not in required
            or resolved.resolve() in seen
        ):
            raise ValueError("Public readback input target/identity differs")
        seen.add(resolved.resolve())
        expected_present = resolved.suffix != ".kicad_dru"
        if row["present"] is not expected_present or path.exists() is not expected_present:
            raise ValueError("Public readback input presence differs")
        if row["sha256"] != (sha(path) if expected_present else None):
            raise ValueError("Public readback input bytes differ")
        if resolved.resolve() == board and row["sha256"] != actual_sha:
            raise ValueError("Public readback PCB digest differs from the saved move")
    revision = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    if (
        Path(data["board"]).resolve() != board
        or snapshot["revision"] != revision
        or snapshot["basis"] != "complete captured input manifest"
        or snapshot["observed_at"] != source["completed_at"]
        or observation["state"] != "saved_files"
        or observation["input_drift_checked"] is not True
        or observation["live_editor_observed"] is not False
        or source["completed"] is not True
        or source["official_native"] is not False
        or source["origin"] != "kicad_cli.saved-project-comparison"
        or observation["coverage"]["native"] is not False
        or not observation["coverage"]["checked"]
        or not re.fullmatch(r"[0-9a-f]{64}", source["code_sha256"])
    ):
        raise ValueError("Public saved readback snapshot/source/coverage binding differs")
    rows = data["observed_footprints"]
    if (
        len(rows) != 12
        or len({row["id"] for row in rows}) != 12
        or len({row["ref"] for row in rows}) != 12
    ):
        raise ValueError("Public readback footprint inventory/identity differs")
    matches = [row for row in rows if row["id"] == R1_UUID or row["ref"] == "R1"]
    if len(matches) != 1:
        raise ValueError("Public readback R1 selection is not unique")
    fp = matches[0]
    saved = selected(sexp(board.read_text(encoding="utf-8")))
    fields = {json.loads(row[1]): json.loads(row[2]) for row in children(saved, "property")}
    attrs = children(saved, "attr")
    attributes = sorted(attrs[0][1:]) if attrs else []
    if (
        fp["id"] != R1_UUID
        or fp["ref"] != "R1"
        or fp["path"] != SCH_PATH
        or fp["value"] != "10k"
        or fp["fpid"] != "fixture:R_0805"
        or fp["layer"] != "F.Cu"
        or fp["position_nm"] != [98500000, 100000000]
        or fp["rotation_deg"] != 0
        or fp["fields"] != fields
        or fp["attributes"] != attributes
        or len(fp["pads"]) != 2
    ):
        raise ValueError("Public readback R1 saved identity/pose/fields differ")
    seen_pads, world = set(), {}
    for pad in fp["pads"]:
        number = pad["number"]
        if number not in PADS or number in seen_pads:
            raise ValueError("Public readback pad selection differs")
        seen_pads.add(number)
        identity, net, expected_world = PADS[number]
        local = [expected_world[0] - 98500000, expected_world[1] - 100000000]
        if (
            pad["id"] != identity
            or pad["net"] != net
            or pad["position_nm"] != local
            or pad["rotation_deg"] != 0
            or pad["kind"] != "smd"
            or pad["shape"] != "roundrect"
        ):
            raise ValueError("Public readback pad local UUID/net/pose differs")
        world[number] = [fp["position_nm"][axis] + local[axis] for axis in (0, 1)]
        if world[number] != expected_world:
            raise ValueError("Public readback local-to-world translation differs")
    return {
        "snapshot_revision": revision,
        "complete_input_count": len(manifest),
        "actual_board_sha256": actual_sha,
        "R1_uuid": R1_UUID,
        "R1_position_nm": fp["position_nm"],
        "R1_pad_local_to_world_nm": world,
        "source": "completed custom saved-file observer; official_native=false",
        "routing": "no requirement to clear unconnected copper; official DRC is separate",
    }


def check_receipts(files, original_sha, actual_sha, engineer_board):
    values = {key: json.loads(Path(path).read_bytes()) for key, path in files.items()}
    if set(values) != {"preview", "confirm", "readback"} or not all(
        value.get("ok") is True for value in values.values()
    ):
        raise ValueError("Three successful public EXE output envelopes are required")
    plan = values["preview"]["data"]["preview"]
    receipt = values["confirm"]["data"]["receipt"]
    change = plan["changes"]
    if (
        len(change) != 1
        or change[0]["action"] != "translate"
        or change[0]["id"] != R1_UUID
        or change[0]["after"]["position_mm"] != [98.5, 100.0]
        or plan["base_snapshot"]["revision"] != original_sha
        or plan["target"]["locator"] != Path(engineer_board).resolve().as_uri()
        or receipt["target"] != plan["target"]
        or receipt["snapshot_before"]["revision"] != original_sha
        or receipt["snapshot_after"]["revision"] != actual_sha
        or receipt["operation_id"] != plan["operation_id"]
        or receipt["plan_id"] != plan["plan_id"]
        or receipt["outcome"]["verification"]["status"] != "passed"
        or receipt["outcome"]["persistence"] != "saved"
        or receipt["outcome"]["execution"] != "completed"
        or receipt["outcome"]["change"] != "applied"
        or receipt["extensions"]["failures"]
        or plan["extensions"]["engineering_feedback"]["geometry_check"] != "not_performed"
    ):
        raise ValueError("Public preview/receipt does not bind the unique frozen saved move")
    public_readback = check_public_readback(values["readback"]["data"], actual_sha, engineer_board)
    return {
        "status": "passed",
        "files": {key: {"path": str(path), "sha256": sha(path)} for key, path in files.items()},
        "operation_id": receipt["operation_id"],
        "plan_id": receipt["plan_id"],
        "engineer_board": str(engineer_board),
        "public_readback": public_readback,
        "independent_truth": "independent complete file and native checks required",
    }
