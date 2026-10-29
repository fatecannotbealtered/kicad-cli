"""Bounded RC+TP saved observation comparison; no product imports or setters."""

import json
import sys
from pathlib import Path


def compare(before, current, frozen, stage, previous=None):
    failures = []

    def require(ok, check, **details):
        if not ok:
            failures.append({"check": check, **details})

    rename = {"OUT2": "OUT2_SENSE", "/OUT2": "/OUT2_SENSE"} if stage != "S0" else {}

    def renamed(row):
        if isinstance(row, dict):
            return {k: renamed(v) for k, v in row.items()}
        if isinstance(row, list):
            return [renamed(v) for v in row]
        return rename.get(row, row) if isinstance(row, str) else row

    original = {p["ref"]: p for p in before["footprints"]}
    now = {p["ref"]: p for p in current["footprints"]}

    def symbol_id(node):
        return next(child[1] for child in node if isinstance(child, list) and child[0] == "uuid")

    original_symbols = before.get("schematic_symbols")
    current_symbols = current.get("schematic_symbols")
    require(
        original_symbols is not None and current_symbols is not None,
        "saved_schematic_symbol_observation",
    )
    if original_symbols is not None and current_symbols is not None:
        symbols_now = {symbol_id(node): node for node in current_symbols}
        for node in original_symbols:
            require(
                symbols_now.get(symbol_id(node)) == node,
                "entire_original_schematic_symbol_identity_pose_fields",
                uuid=symbol_id(node),
            )
    for ref, expected in original.items():
        require(now.get(ref) == renamed(expected), "original_footprint_identity_pose_pads", ref=ref)
    require(current["edges"] == before["edges"], "entire_edge_geometry_and_identity")
    require(current["zone_definitions"] == before["zone_definitions"], "zone_definitions_unchanged")
    require(current["copper_layer_count"] == before["copper_layer_count"] == 2, "two_copper_layers")
    same_board = frozen["pcb_strategy"].get("same_board", False)
    selected_changes = stage in {"S3", "S4"} or (same_board and stage == "S5")
    protected = [t for t in before["tracks"] if not selected_changes or t["logical_net"] != "OUT2"]
    actual = [
        t for t in current["tracks"] if not selected_changes or t["logical_net"] != "OUT2_SENSE"
    ]
    require(
        sorted(actual, key=lambda t: t["uuid"])
        == sorted(renamed(protected), key=lambda t: t["uuid"]),
        "all_unselected_copper_identity_and_geometry",
    )
    rules = frozen["requirements"]["rules"]
    for track in current["tracks"]:
        if "drill_nm" in track:
            require(
                track["width_nm"] == round(rules["via_diameter_mm"] * 1e6)
                and track["drill_nm"] == round(rules["via_drill_mm"] * 1e6),
                "actual_via_dimensions",
                uuid=track["uuid"],
            )
        else:
            require(
                track["type"] == "PCB_TRACK"
                and track["width_nm"] == round(rules["track_width_mm"] * 1e6),
                "actual_straight_track_width",
                uuid=track["uuid"],
            )
    if stage in {"S2", "S3", "S4"}:
        tp = frozen["requirements"]["testpoint"]
        part = now.get("TP1", {})
        require(
            part.get("position_nm") == [round(x * 1e6) for x in tp["pcb_position_mm"]]
            and part.get("rotation_deg") == tp["pcb_rotation_deg"]
            and part.get("layer") == tp["pcb_layer"],
            "testpoint_pose_mm",
            expected=tp["pcb_position_mm"],
            observed=part.get("position_nm"),
        )
        require(part.get("exclude_from_bom") is True, "testpoint_bom_exclusion")
        pads = part.get("pads", [])
        require(
            len(pads) == 1
            and pads[0]["number"] == "1"
            and pads[0]["size_nm"] == [1500000, 1500000]
            and pads[0]["drill_nm"] == [0, 0]
            and pads[0]["position_nm"] == part.get("position_nm"),
            "frozen_testpoint_1p5mm_smd_pad_geometry",
        )
    else:
        require("TP1" not in now, "no_testpoint_in_completed_stage")
    if same_board and stage == "S5":
        require(previous is not None, "previous_lifecycle_observation_required")
        if previous is not None:
            removed = [part for part in previous["footprints"] if part["ref"] == "TP1"]
            require(len(removed) == 1, "previous_board_actually_contained_testpoint")
            require(
                sorted(current["tracks"], key=lambda t: t["uuid"])
                == sorted(previous["tracks"], key=lambda t: t["uuid"]),
                "explicit_removal_preserves_all_S4_copper",
            )
            if removed:
                gone = set(previous.get("footprint_owned_uuids", {}).get("TP1", []))
                require(
                    bool(gone) and "all_uuid_mentions" in current,
                    "independent_attachment_observation_required",
                )
                require(
                    not gone.intersection(current.get("all_uuid_mentions", [])),
                    "removed_footprint_and_all_attached_uuid_mentions_absent",
                )
    project = current["project"]
    original_project = before["project"]
    require(
        project.get("board", {}).get("design_settings")
        == original_project.get("board", {}).get("design_settings"),
        "original_board_rules_and_ignores",
    )
    require(project.get("erc") == original_project.get("erc"), "original_erc_rules_and_ignores")
    settings = project["net_settings"]
    require(
        settings["classes"] == original_project["net_settings"]["classes"],
        "numeric_netclasses_unchanged",
    )
    if stage != "S0":
        require(
            settings.get("netclass_assignments", {}).get("/OUT2_SENSE") == ["RC4"],
            "new_net_retains_explicit_RC4_assignment",
        )
    for net, assignment in original_project["net_settings"].get("netclass_assignments", {}).items():
        if net != "/OUT2":
            require(
                settings.get("netclass_assignments", {}).get(net) == assignment,
                "other_netclass_assignment_unchanged",
                net=net,
            )
    return {
        "passed": not failures,
        "failures": failures,
        "stage": stage,
        "board_sha256": current["board_sha256"],
        "baseline_board_sha256": before["board_sha256"],
        "units": (
            "Saved native coordinates/dimensions in nm, intent poses/rules in mm; "
            "exact integer nm comparisons"
        ),
        "protected_copper_items": len(protected),
        "coverage": {
            "checked": [
                "all original native footprint identities/poses/physical pad records",
                "complete supported native edges and zone definitions",
                "all unselected saved tracks/vias",
                "actual copper widths/via sizes",
                "testpoint pose/BOM flag",
                "unchanged numeric rules/ignores and explicit literal netclass mapping",
            ],
            "separate_required": [
                "native ERC/DRC and actual connectivity",
                "logical/physical intent",
                "complete current images and explicit visual verdict",
            ],
            "not_checked": [
                "all manufacturing geometry",
                "general wildcard-rule evaluation",
                "native glyph bounding-box geometry",
                "arbitrary footprint shapes/layouts beyond this frozen case",
            ],
        },
    }


def main():
    baseline, observation, requirements, stage, destination, *prior = sys.argv[1:]
    result = compare(
        *(json.loads(Path(p).read_bytes()) for p in (baseline, observation, requirements)),
        stage,
        json.loads(Path(prior[0]).read_bytes()) if prior else None,
    )
    Path(destination).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
