"""Observe an explicitly frozen thermal case; no refilling or rule changes."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
from pathlib import Path

from change_support import REPO, Run, arguments, sha, snapshot, write


def decision_trace(board):
    """Instrument the product decision, not an independent acceptance oracle."""
    sys.path.insert(0, str(REPO))
    from kicad_cli.fileformat import drc
    from kicad_cli.fileformat.drc import fills

    calls, geometry = [], []
    old_judge, old_crossings = fills._judge, fills._crossings

    def crossings(ring, polygons):
        hits = old_crossings(ring, polygons)
        points = []
        for offset, which in hits:
            i = math.floor(offset)
            fraction = offset - i
            a, b = ring[(i - 1) % len(ring)], ring[i % len(ring)]
            points.append(
                {
                    "connectivity_item_index": which,
                    "position_nm": [round(a[j] + fraction * (b[j] - a[j])) for j in (0, 1)],
                    "ring_offset": offset,
                }
            )
        geometry.append(
            {
                "ring_nm": [[round(x), round(y)] for x, y in ring],
                "intersections": points,
                "polygons": [
                    {"connectivity_item_index": i, "vertices_nm": p.points} for i, p in polygons
                ],
            }
        )
        return hits

    def judge(run, board, zone_item, layer, fp, pad, count, minimum):
        calls.append(
            dict(
                ref=fp.reference,
                pad=pad.number,
                layer=layer,
                counted_spokes=count,
                minimum=minimum,
                geometry=geometry[-1],
            )
        )
        return old_judge(run, board, zone_item, layer, fp, pad, count, minimum)

    try:
        fills._judge, fills._crossings = judge, crossings
        drc.check(board)
    finally:
        fills._judge, fills._crossings = old_judge, old_crossings
    return calls


def run(options):
    original = options.board.resolve()
    project = original.with_suffix(".kicad_pro")
    if sha(original) != options.expected_board_sha256:
        raise ValueError("Thermal input does not match the predeclared board SHA-256")
    if not project.is_file():
        raise ValueError("Thermal experiment requires the corresponding frozen project rules")
    before = {str(p): sha(p) for p in (original, project)}
    config_before = snapshot(options.config_seed)
    trial = Run(options)
    root = trial.root
    board = root / original.name
    for p in (original, project):
        shutil.copyfile(p, root / p.name)
    write(
        root / "plan.json",
        {
            "expected_native_thermal": options.expect_native_thermal,
            "expected_custom_thermal": options.expect_custom_thermal,
            "input": before,
            "configuration": config_before,
            "hypothesis": options.hypothesis,
            "factor": options.factor,
            "mode": "Saved fill observation; no implicit refill or board writes",
        },
    )
    custom = trial.call("custom", ["board", "check", "--board", board, "--compact"])
    observed = trial.native("geometry", "native_thermal_reader.py", board)
    zones = observed["zone_geometry"]
    expected_zone = {
        "net": "GND",
        "layers": ["F.Cu"],
        "pad_connection_name": "thermal",
        "thermal_gap_nm": 500000,
        "thermal_spoke_nm": 500000,
        "min_thickness_nm": 250000,
        "clearance_nm": 300000,
    }
    rules = json.loads(project.read_bytes())["board"]["design_settings"]
    contacts = observed["thermal_polygon_contacts"]
    on_pad = [c for c in contacts if c["pad_contacts"]]
    joined = [c for c in on_pad if any(t["direct_copper_contact"] for t in c["tracks"])]
    geometry_checks = {
        "zone_parameters": len(zones) == 1
        and all(zones[0][k] == v for k, v in expected_zone.items()),
        "minimum_spokes": rules["rules"]["min_resolved_spokes"] == 2,
        "thermal_rule_enabled": rules["rule_severities"]["starved_thermal"] == "error",
        "intended_pad": all(
            [(p["ref"], p["pad"]) for p in c["pad_contacts"]] == [("J1", "6")] for c in on_pad
        ),
        "topology": (len(contacts) == 1 and len(joined) == 1)
        if options.expect_topology == "joined"
        else (len(contacts) == 3 and len(on_pad) == 2 and not joined),
    }
    write(
        root / "geometry-checks.json",
        {
            "expected_zone": expected_zone,
            "expected_topology": options.expect_topology,
            "observed_contacts": contacts,
            "checks": geometry_checks,
            "status": "passed" if all(geometry_checks.values()) else "failed",
        },
    )
    write(root / "custom-trace.json", decision_trace(board))
    trial.official_call(
        "native",
        [
            "pcb",
            "drc",
            "--format",
            "json",
            "--severity-all",
            "--output",
            root / "native-report.json",
            board,
        ],
    )
    native = json.loads((root / "native-report.json").read_bytes())
    thermal = [v for v in native["violations"] if v["type"] == "starved_thermal"]
    custom_thermal = [v for v in custom["data"]["violations"] if v["type"] == "starved_thermal"]
    unchanged = before == {str(p): sha(p) for p in (original, project)}
    unchanged &= all(sha(root / Path(p).name) == digest for p, digest in before.items())
    unchanged &= snapshot(options.config_seed) == config_before
    result = {
        "status": "passed"
        if (
            len(thermal) == options.expect_native_thermal
            and len(custom_thermal) == options.expect_custom_thermal
            and unchanged
            and all(geometry_checks.values())
        )
        else "failed",
        "thermal_finding_absent": len(thermal) == 0,
        "acceptance_scope": "Thermal finding only; all other native findings remain in report",
        "root_cause_fixed": False,
        "native_thermal": thermal,
        "custom_thermal": custom_thermal,
        "input": before,
        "unchanged": unchanged,
        "native_required": custom["data"]["observation"]["coverage"]["native_drc_required_rules"],
        "units": "native geometry nm; spoke count dimensionless",
        "native_reader": observed["thermal_reader_sha256"],
        "decision_trace": "custom-trace.json (product instrumentation, not independent oracle)",
        "independent_geometry": "geometry.stdout",
        "native_report": "native-report.json",
        "limit": "Middle-gap crossings alone do not establish usable thermal connectivity",
    }
    assert "starved_thermal" in result["native_required"]
    trial.finish_binding()
    write(root / "result.json", result)
    if result["status"] != "passed":
        raise ValueError("Thermal observation differs from frozen expectation; inspect result.json")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    arguments(parser)
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--expected-board-sha256", required=True)
    parser.add_argument("--expect-native-thermal", type=int, required=True)
    parser.add_argument("--expect-custom-thermal", type=int, default=0)
    parser.add_argument("--expect-topology", choices=["separated", "joined"], required=True)
    parser.add_argument("--hypothesis", required=True)
    parser.add_argument("--factor", required=True)
    try:
        result = run(parser.parse_args())
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "thermal_finding_absent": result["thermal_finding_absent"],
                }
            )
        )
    except (ValueError, RuntimeError, AssertionError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
