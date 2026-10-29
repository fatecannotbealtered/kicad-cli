"""Fixed saved-fill counterexamples derived from the pinned, owned J1 fixture.

These are deliberately constructed saved geometries, not zone-filler results
or a board-design API. Project bytes, pad, net and thermal settings stay fixed.
"""

import argparse
import json
import re
import shutil
from pathlib import Path

from change_support import sha, write

PIN = "1444094ee139d81be904a31c2c9f73f6357aa853ce0e58329dd811f8c80e83a2"


def filled(points):
    return (
        '\t\t(filled_polygon (layer "F.Cu") (pts '
        + " ".join(f"(xy {x} {y})" for x, y in points)
        + "))"
    )


def prepare(original, output):
    if sha(original) != PIN:
        raise ValueError("Expected the pinned retained J1 fixture")
    output.mkdir(parents=True, exist_ok=False)
    source = original.read_text(encoding="utf-8")
    segments = re.findall(r"(?ms)^\t\(segment\b.*?^\t\)", source)
    assert len(segments) == 2
    short = next(s for s in segments if "8a6aaa7a-88b0-4d3d-9244-c1de3c3cd845" in s)
    long = next(s for s in segments if "899cf854-55db-41a5-8da7-0b15b341007e" in s)
    reduced = source.replace(short, "")
    # Fixed dimensions, chosen before native/custom observations. Each spoke
    # crosses the 1.10 mm middle-gap ring of the 1.70 mm circular pad.
    rectangles = [
        [(40.8, 29.83), (42.04, 29.83), (42.04, 30.33), (40.8, 30.33)],
        [(43.04, 29.83), (44.28, 29.83), (44.28, 30.33), (43.04, 30.33)],
        [(42, 37), (43, 37), (43, 39), (42, 39)],
    ]
    pattern = r"(?ms)^\t\t\(filled_polygon\b.*?^\t\t\)"
    assert len(re.findall(pattern, reduced)) == 3
    simple = re.sub(pattern, "", reduced)
    at = simple.index("\t\t(polygon")
    simple = simple[:at] + "\n".join(map(filled, rectangles)) + "\n" + simple[at:]
    front = simple.replace(long, long.replace('(layer "B.Cu")', '(layer "F.Cu")'))
    joined = [
        (40.8, 29.83),
        (42.04, 29.83),
        (42.04, 30.33),
        (41.3, 30.33),
        (41.3, 37),
        (43.78, 37),
        (43.78, 30.33),
        (43.04, 30.33),
        (43.04, 29.83),
        (44.28, 29.83),
        (44.28, 39),
        (40.8, 39),
    ]
    positive = front.replace("\n".join(map(filled, rectangles)), filled(joined))
    definitions = [
        ("remove-short", "retained", reduced, 1, "Remove only the short B.Cu stub"),
        (
            "simple-back",
            "remove-short",
            simple,
            1,
            "Replace the three saved contours with three rectangles",
        ),
        ("simple-front", "simple-back", front, 1, "Move only the long track from B.Cu to F.Cu"),
        (
            "joined-front",
            "simple-front",
            positive,
            0,
            "Join both spoke islands to the remote copper using two 0.50 mm front bridges",
        ),
        (
            "no-track-control",
            "remove-short",
            reduced.replace(long, ""),
            0,
            "Remove remaining B.Cu track; native all-isolated fallback, not positive connectivity",
        ),
    ]
    plan = {
        "original_sha256": PIN,
        "project_sha256": sha(original.with_suffix(".kicad_pro")),
        "method": "Explicit owned saved-fill fixtures; not regenerated zone-fill geometry",
        "invariants": {
            "net": "GND",
            "layer": "F.Cu",
            "thermal_gap_mm": 0.5,
            "thermal_spoke_mm": 0.5,
            "min_spokes": 2,
            "clearance_mm": 0.3,
            "min_thickness_mm": 0.25,
            "mode": "thermal",
            "rules": "unchanged project bytes",
        },
        "cases": [],
    }
    for name, parent, text, native_count, factor in definitions:
        case = output / name
        case.mkdir()
        board = case / "case.kicad_pcb"
        board.write_text(text, encoding="utf-8")
        shutil.copyfile(original.with_suffix(".kicad_pro"), board.with_suffix(".kicad_pro"))
        plan["cases"].append(
            {
                "id": name,
                "parent": parent,
                "factor": factor,
                "board": str(board.resolve()),
                "sha256": sha(board),
                "expected_topology": "joined" if name == "joined-front" else "separated",
                "expected_native_thermal": native_count,
                "expected_custom_thermal": 0,
            }
        )
    write(output / "plan.json", plan)
    return plan


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    print(json.dumps(prepare(options.original, options.output)))
