"""Compare every saved instance/unit and physical pad to pre-edit frozen intent."""

import argparse
import copy
import json
from collections import Counter
from pathlib import Path

from hierarchy_frozen import BaselineBindingError, read_verified_baseline


def compare(baseline, current, frozen, stage, erc, drc, baseline_footprints):
    wanted = frozen["stages"][stage]
    failures = []

    def require(condition, check, expected=None, observed=None):
        if not condition:
            failures.append({"check": check, "expected": expected, "observed": observed})

    disconnected = set(wanted.get("disconnected_pins", []))
    disconnected_names = {}
    for key in sorted(disconnected):
        found = [
            (n, m)
            for n in current["xml_nets"]
            for m in n["members"]
            if m["ref"] + "." + m["pin"] == key
        ]
        valid = (
            len(found) == 1
            and len(found[0][0]["members"]) == 1
            and found[0][0]["name"].startswith("unconnected-(")
            and "+no_connect" in found[0][1]["pintype"]
        )
        require(valid, "declared_isolated_no_connect:" + key)
        disconnected_names[key] = found[0][0]["name"] if valid else "__invalid_no_connect__"

    def net(ref, number, original):
        if ref + "." + number in disconnected:
            return disconnected_names[ref + "." + number]
        return wanted["pin_connections"].get(
            ref + "." + number, wanted["net_renames"].get(original, original)
        )

    require(
        current["native_version"] == baseline["native_version"],
        "exact_native_version",
        baseline["native_version"],
        current["native_version"],
    )

    expected_units = copy.deepcopy(baseline["units"])
    for unit in expected_units:
        ref = unit["ref"]
        if ref in wanted["values"]:
            unit["stored_fields"]["Value"] = wanted["values"][ref]
            for node in unit["raw_symbol"]:
                if isinstance(node, list) and node[:2] == ["property", "Value"]:
                    node[2] = wanted["values"][ref]
        for pin in unit["pins"]:
            pin["net"] = net(ref, pin["number"], pin["net"])
    require(current["sheets"] == baseline["sheets"], "all_instance_paths_and_shared_files")
    expected_by_path = {u["full_path"]: u for u in expected_units}
    actual_by_path = {u["full_path"]: u for u in current["units"]}
    require(
        len(actual_by_path) == len(current["units"]) == len(expected_units),
        "no_duplicate_or_missing_unit",
    )
    require(set(actual_by_path) == set(expected_by_path), "all_symbol_instance_identities")
    for path, expected in expected_by_path.items():
        require(
            actual_by_path.get(path) == expected,
            "full_unit_fields_pose_and_pin_membership:" + path,
            expected,
            actual_by_path.get(path),
        )

    expected_parts = copy.deepcopy(baseline["xml_components"])
    for part in expected_parts:
        part["value"] = wanted["values"].get(part["ref"], part["value"])
    require(
        sorted(current["xml_components"], key=lambda x: x["ref"])
        == sorted(expected_parts, key=lambda x: x["ref"]),
        "official_xml_all_component_identities_fields_and_unit_uuids",
    )

    def graph(nets, changed=False):
        result = {}
        for original in nets:
            for member in original["members"]:
                member = dict(member)
                key = member["ref"] + "." + member["pin"]
                if key in result:
                    raise ValueError("Duplicate physical pin in official XML graph")
                name = original["name"]
                if changed:
                    name = net(member["ref"], member["pin"], name)
                    if key in wanted["pin_connections"]:
                        member["pintype"] = member["pintype"].replace("+no_connect", "")
                    if key in disconnected:
                        member["pintype"] = member["pintype"].split("+")[0] + "+no_connect"
                result[key] = {"net": name, "member": member}
        return result

    expected_graph, actual_graph = graph(baseline["xml_nets"], True), graph(current["xml_nets"])
    require(
        actual_graph == expected_graph,
        "complete_official_pin_network_graph",
        expected_graph,
        actual_graph,
    )
    expected_footprints = copy.deepcopy(baseline["footprints"])
    for fp in expected_footprints:
        ref = fp["ref"]
        fp["value"] = wanted["values"].get(ref, fp["value"])
        for pad in fp["pads"]:
            pad["net"] = net(ref, pad["number"], pad["net"])
    require(
        current["footprints"] == expected_footprints,
        "every_physical_pad_identity_net_and_full_layout",
        expected_footprints,
        current["footprints"],
    )
    actual_units = {}
    expected_raw = copy.deepcopy(baseline_footprints)
    for fp in expected_raw:
        props = [p for p in fp if isinstance(p, list) and p[0] == "property"]
        ref = next(p[2] for p in props if p[1] == "Reference")
        for prop in props:
            if prop[1] == "Value" and ref in wanted["values"]:
                prop[2] = wanted["values"][ref]
        for pad in (p for p in fp if isinstance(p, list) and p[0] == "pad"):
            for member in pad:
                if isinstance(member, list) and member[0] == "net":
                    if len(member) != 2:
                        raise ValueError("This frozen fixture uses modern direct-name pad nets")
                    member[1] = net(ref, pad[1], member[1])
    require(
        current["raw_footprints"] == expected_raw,
        "entire_saved_footprint_geometry_including_rounding_and_chamfers",
    )
    for unit in current["units"]:
        if not unit["power_symbol"]:
            actual_units.setdefault(unit["ref"], []).append(unit)
    for fp in current["footprints"]:
        units = actual_units.get(fp["ref"], [])
        accepted_paths = {"/" + "/".join(u["full_path"].strip("/").split("/")[1:]) for u in units}
        require(
            fp["source_path"] in accepted_paths,
            "board_matches_full_symbol_path:" + fp["ref"],
            sorted(accepted_paths),
            fp["source_path"],
        )
        require(
            all(u["stored_fields"]["Value"] == fp["value"] for u in units),
            "all_units_have_board_value:" + fp["ref"],
        )
        for pad in fp["pads"]:
            key = fp["ref"] + "." + pad["number"]
            if key in actual_graph:
                require(
                    pad["net"] == actual_graph[key]["net"], "native_physical_to_logical_pin:" + key
                )
            else:
                require(
                    fp["ref"] == "U1"
                    and pad["number"] in frozen["physical_mapping"]["fixture_SO8_unmapped_pads"]
                    and pad["net"] == "",
                    "explicit_unmapped_pad_boundary:" + key,
                )
    expected_tracks = copy.deepcopy(baseline["tracks"])
    usable = frozen["format"] == "usable-hierarchy-edit-intent.v1"
    if usable:
        for track in expected_tracks:
            track["net"] = wanted["net_renames"].get(track["net"], track["net"])
    require(
        current["tracks"] == expected_tracks,
        "all_original_copper_unchanged_except_declared_net_names"
        if usable
        else "all_original_copper_unchanged",
    )
    require(
        current["protected_board_nodes"] == baseline["protected_board_nodes"],
        "full_board_graphics_zones_setup_and_outline_unchanged",
    )
    require(current["project"] == baseline["project"], "project_rules_and_ignores_unchanged")
    erc_findings = [v for sheet in erc["sheets"] for v in sheet["violations"]]
    erc_baseline = [
        v
        for sheet in frozen["native_findings"]["erc_baseline"]["sheets"]
        for v in sheet["violations"]
    ]

    def identities(rows):
        return Counter(
            (r["type"], r["severity"], tuple(sorted(i["uuid"] for i in r.get("items", []))))
            for r in rows
        )

    intermediate = usable and wanted.get("native_policy") == "intentional_disconnect"
    expected_erc = identities(erc_baseline)
    if intermediate:
        from usable_checks import intermediate_isolated_labels

        expected_erc += intermediate_isolated_labels(baseline, current, disconnected)
    require(
        identities(erc_findings) == expected_erc,
        "native_erc_findings_match_declared_intermediate_labels"
        if intermediate
        else "native_erc_findings_match_frozen_identities",
    )
    if not intermediate:
        require(
            not drc["violations"], "no_native_drc_geometry_violations", 0, len(drc["violations"])
        )
    require(not drc["schematic_parity"], "native_schematic_parity", 0, len(drc["schematic_parity"]))
    if intermediate:
        # Only this explicitly intermediate stage may retain native findings.
        # It can never be counted as an electrically complete delivery.
        allowed_pads = {
            pad["uuid"]
            for fp in current["footprints"]
            for pad in fp["pads"]
            if fp["ref"] + "." + pad["number"] in disconnected
        }
        for finding in drc["violations"] + drc["unconnected_items"]:
            require(
                bool(allowed_pads & {i.get("uuid") for i in finding.get("items", [])}),
                "intermediate_findings_must_involve_declared_disconnected_pads",
            )
    else:
        expected_open = frozen["native_findings"]["unconnected_counts"][stage]
        require(
            len(drc["unconnected_items"]) == expected_open,
            "declared_unrouted_native_findings",
            expected_open,
            len(drc["unconnected_items"]),
        )
    if usable:
        from usable_checks import compare_drawings

        failures.extend(compare_drawings(baseline, current, frozen, stage))
    structure = frozen["expected_structure"]
    return {
        "stage": stage,
        "bounded_edit_verified": not failures,
        "failures": failures,
        "expected": {
            "footprints": structure["footprints"],
            "physical_pads": structure["physical_pads"],
            "component_units": structure["component_units"],
            "power_symbols": structure["power_symbol_instances"],
        },
        "observed": {
            "footprints": len(current["footprints"]),
            "physical_pads": sum(len(f["pads"]) for f in current["footprints"]),
            "component_units": sum(not u["power_symbol"] for u in current["units"]),
            "power_symbols": sum(u["power_symbol"] for u in current["units"]),
        },
        "native_erc": {
            "verdict": "failed_with_findings" if erc_findings else "passed",
            "violations": dict(Counter(v["type"] for v in erc_findings)),
        },
        "native_drc": {
            "verdict": "failed_with_findings"
            if any(drc[k] for k in ("violations", "unconnected_items", "schematic_parity"))
            else "passed",
            "violations": len(drc["violations"]),
            "unconnected": len(drc["unconnected_items"]),
            "parity": len(drc["schematic_parity"]),
        },
        "scope": "Explicit shared-instance/multi-unit edit and PCB synchronization preservation. "
        "Nonzero native findings remain failed native checks; no electrical, layout "
        "or manufacturing approval.",
        "units_and_tolerance": frozen["units"],
        "delivery_eligible": usable
        and wanted.get("native_policy") == "require_clean"
        and not failures,
        "not_checked": [
            "general hierarchy creation",
            "arbitrary geometry editing",
            "manufacturing outputs",
            "visual readability (separate full-page review required)",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "current", "frozen", "erc", "drc", "output", "readers"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument(
        "--stage",
        choices=["H0", "H1", "H2", "H3", "H4", "N1", "M1", "M2", "U0", "U1", "U2", "U3", "U4"],
        required=True,
    )
    args = parser.parse_args()

    def read(path):
        return json.loads(path.read_bytes())

    import hashlib
    import sys

    sys.path.insert(0, str(args.readers.resolve()))
    from sexpr_reader import children, parse

    try:
        frozen, baseline, baseline_binding = read_verified_baseline(args.frozen, args.baseline)
    except BaselineBindingError as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "HIERARCHY_BASELINE_MISMATCH",
                        "message": str(error),
                        "next_action": (
                            "Restore the pre-edit frozen inputs and restart the complete review; "
                            "do not inherit approval from a regenerated baseline."
                        ),
                    },
                }
            )
        )
        raise SystemExit(2) from error
    if args.stage not in frozen["stages"] and args.stage not in {"N1", "M1", "M2"}:
        raise ValueError("Stage is not declared in the supplied frozen case")
    raw_board = args.frozen.with_name("frozen-baseline.kicad_pcb").read_bytes()
    if (
        hashlib.sha256(raw_board).hexdigest()
        != frozen["input_preparation"]["fixture_files"]["edit.kicad_pcb"]
    ):
        raise ValueError("Baseline board differs from the input frozen before edits")
    if args.stage in {"N1", "M1", "M2"}:
        addendum = read(args.frozen.with_name("SELECTOR_ADDENDUM.json"))
        assert addendum["original_frozen_sha256"] == baseline_binding["frozen_sha256"]
        frozen["stages"].update(addendum["stages"])
        frozen["native_findings"]["unconnected_counts"].update(addendum["unconnected_counts"])
    result = compare(
        baseline,
        read(args.current),
        frozen,
        args.stage,
        read(args.erc),
        read(args.drc),
        children(parse(raw_board.decode("utf-8")), "footprint"),
    )
    result["baseline_binding"] = baseline_binding
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(
        json.dumps(
            {
                "stage": args.stage,
                "bounded_edit_verified": result["bounded_edit_verified"],
                "failure_count": len(result["failures"]),
            }
        )
    )
    raise SystemExit(0 if result["bounded_edit_verified"] else 2)
