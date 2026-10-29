"""Bounded independent Gerber/Excellon consumer for this frozen straight-geometry case."""

import hashlib
import json
import re
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

from gerber_subset import gerber, outline_problems

snapshot, observation_path, fab_dir, output = map(Path, sys.argv[1:5])
if output.exists():
    raise SystemExit("Existing report refused")
manifest = json.loads((snapshot / "MANIFEST.json").read_text(encoding="utf-8-sig"))
observation = json.loads(observation_path.read_text(encoding="utf-8-sig"))
failures = []


def require(ok, kind, **facts):
    if not ok:
        failures.append({"check": kind, **facts})


require(
    observation["board_sha256"] == manifest["files"]["project/rc4.kicad_pcb"],
    "observation_bound_to_snapshot_board",
    observed=observation["board_sha256"],
    expected=manifest["files"]["project/rc4.kicad_pcb"],
)


def nm(text):
    return int(Decimal(str(text)) * 1000000)


def segment(a, b, width):
    return (*sorted((tuple(a), tuple(b))), int(width))


def outline_segment(a, b):
    return tuple(sorted((tuple(a), tuple(b))))


files = {}
expected_layers = ["F_Cu", "B_Cu", "F_SilkS", "B_SilkS", "F_Mask", "B_Mask", "Edge_Cuts"]
for layer in expected_layers:
    path = fab_dir / "gerber" / f"rc4-{layer}.gbr"
    require(path.exists(), "required_gerber_layer", layer=layer)
    if path.exists():
        files[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
parsed = {
    layer: gerber((fab_dir / "gerber" / f"rc4-{layer}.gbr").read_text(encoding="utf-8-sig"))
    for layer in expected_layers
}
for layer, data in parsed.items():
    require(data["units_mm_4_6_absolute"], "gerber_units_coordinates", layer=layer)


def exported(point):
    return (point[0], -point[1])


checks = {}
for layer in ("F.Cu", "B.Cu"):
    data = parsed[layer.replace(".", "_")]
    require(
        not data["clear_polarity"] and not data["unknown_commands"] and not data["arcs_unverified"],
        "supported_positive_copper_geometry",
        layer=layer,
        clear=data["clear_polarity"],
        unknown=data["unknown_commands"],
        arcs=data["arcs_unverified"],
    )
    actual = Counter(
        segment(s["a"], s["b"], s["width_nm"])
        for s in data["strokes"]
        if s["dark"] and s["width_nm"] == 250000
    )
    expected = Counter(
        segment(exported(t["start_nm"]), exported(t["end_nm"]), t["width_nm"])
        for t in observation["tracks"]
        if "drill_nm" not in t and t["layer"] == layer
    )
    require(
        actual == expected,
        "all_selected_trace_strokes",
        layer=layer,
        missing=list((expected - actual).elements()),
        extra=list((actual - expected).elements()),
    )
    region_edges = Counter(
        outline_segment(a, b)
        for contour in data["contours"]
        for a, b in zip(contour, contour[1:] + contour[:1], strict=True)
        if a != b
    )
    expected_edges = Counter()
    for zone_fills in observation["saved_filled_polygons"]:
        for fill in zone_fills:
            items = {v[0]: v for v in fill[1:] if isinstance(v, list) and v}
            if items.get("layer", [None, None])[1] != layer:
                continue
            points = [(nm(p[1]), -nm(p[2])) for p in items["pts"][1:]]
            expected_edges.update(
                outline_segment(a, b)
                for a, b in zip(points, points[1:] + points[:1], strict=True)
                if a != b
            )
    require(
        not (expected_edges - region_edges),
        "saved_ground_contour_edges_present",
        layer=layer,
        missing_count=sum((expected_edges - region_edges).values()),
    )
    checks[layer] = {
        "trace_strokes": sum(actual.values()),
        "ground_contour_edges_checked": sum(expected_edges.values()),
        "exported_region_contours": len(data["contours"]),
        "unverified_macro_count": data["macros_unverified"],
        "unverified_flash_shapes": len(data["flashes"]),
    }
edge_data = parsed["Edge_Cuts"]
require(
    not outline_problems(edge_data),
    "supported_edge_geometry",
    unsupported=outline_problems(edge_data),
    unknown=edge_data["unknown_commands"],
)
expected_outline = Counter(
    segment(exported(e["start_nm"]), exported(e["end_nm"]), e["width_nm"])
    for e in observation["edges"]
)
actual_outline = Counter(
    segment(s["a"], s["b"], s["width_nm"]) for s in edge_data["strokes"] if s["dark"]
)
require(
    actual_outline == expected_outline and not edge_data["clear_polarity"],
    "complete_outline_segment_multiset",
    expected_count=sum(expected_outline.values()),
    actual_count=sum(actual_outline.values()),
)
holes = []
for name in ("rc4-PTH.drl", "rc4-NPTH.drl"):
    path = fab_dir / "drill" / name
    text = path.read_text(encoding="utf-8-sig")
    files[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    require(
        "METRIC" in text and "G90" in text and "M30" in text, "metric_absolute_excellon", file=name
    )
    tools = {}
    tool = None
    for line in text.splitlines():
        if match := re.fullmatch(r"T(\d+)C([\d.]+)", line):
            tools[int(match[1])] = nm(match[2])
        elif match := re.fullmatch(r"T(\d+)", line):
            tool = int(match[1])
        elif match := re.fullmatch(r"X(-?[\d.]+)Y(-?[\d.]+)", line):
            holes.append((nm(match[1]), nm(match[2]), tools[tool], "NPTH" not in name))
        elif line.startswith(("G85", "G00", "G01", "M15", "M16", "R")):
            require(False, "unsupported_drill_geometry", line=line)
expected_holes = []
for f in observation["footprints"]:
    for p in f["pads"]:
        if any(p["drill_nm"]):
            require(p["drill_nm"][0] == p["drill_nm"][1], "circular_drill_supported")
            expected_holes.append((*exported(p["position_nm"]), p["drill_nm"][0], True))
for t in observation["tracks"]:
    if "drill_nm" in t:
        expected_holes.append((*exported(t["start_nm"]), t["drill_nm"], True))
require(
    Counter(holes) == Counter(expected_holes),
    "all_circular_drill_coordinates_diameters_plating",
    missing=list((Counter(expected_holes) - Counter(holes)).elements()),
    extra=list((Counter(holes) - Counter(expected_holes)).elements()),
)
drift = []
actual_snapshot_files = {
    p.relative_to(snapshot).as_posix()
    for p in snapshot.rglob("*")
    if p.is_file() and p != snapshot / "MANIFEST.json"
}
unexpected_snapshot_files = sorted(actual_snapshot_files - set(manifest["files"]))
drift.extend("unexpected:" + name for name in unexpected_snapshot_files)
for name, expected in manifest["files"].items():
    path = snapshot / name
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        drift.append(name)
require(not drift, "frozen_source_file_hashes_unchanged", drift=drift)
report = {
    "bounded_passed": not failures,
    "failures": failures,
    "units": (
        "Gerber metric absolute 4.6; Excellon metric decimal absolute; "
        "source nm; native plot origin (0,0), Y reflected in manufacturing "
        "files"
    ),
    "snapshot_manifest_sha256": hashlib.sha256(
        (snapshot / "MANIFEST.json").read_bytes()
    ).hexdigest(),
    "observation_board_sha256": observation["board_sha256"],
    "gerber_layers": expected_layers,
    "selected_geometry": checks,
    "outline_segments": sum(actual_outline.values()),
    "circular_drill_hits": len(holes),
    "files": files,
    "frozen_source_drift": drift,
    "coverage": {
        "checked": [
            "all declared layer file inventory/metric framing",
            f"all {sum(v['trace_strokes'] for v in checks.values())} "
            "source straight trace strokes at 0.25mm",
            "every saved ground contour edge on declared copper layers",
            "complete rectangle segment multiset",
            "all circular PTH and via drill coordinates/diameters",
            "frozen source hashes after exports",
        ],
        "not_checked": [
            "full macro/noncircular pad flash shapes",
            "complete mask aperture geometry and silk strokes",
            "arbitrary Gerber region boolean composition",
            "supplier mapping or manufacturing signoff",
            "BOM/CPL optional outputs not requested",
        ],
    },
}
output.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(
    json.dumps(
        {
            k: report[k]
            for k in (
                "bounded_passed",
                "failures",
                "selected_geometry",
                "outline_segments",
                "circular_drill_hits",
                "frozen_source_drift",
                "coverage",
            )
        },
        indent=2,
    )
)
raise SystemExit(0 if not failures else 1)
