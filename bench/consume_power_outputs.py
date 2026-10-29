"""Independent consumers of the original ATmega assembly profile.

Straight selected copper, circular holes and BOM/CPL only. Region/pad/mask/arc
composition is explicitly outside this consumer. No product/SDK imports.
"""

import csv
import re
from collections import Counter
from decimal import Decimal

from change_support import sha


def strokes(file):
    # Share the strict dark-only state policy; selected lines are returned in
    # Gerber Y-up coordinates for the original assembly-origin comparison.
    from consume_engineering_outputs import gerber

    parsed = gerber(file)
    return Counter(
        (*sorted(((a[0], -a[1]), (b[0], -b[1]))), width)
        for a, b, width in parsed["straight_strokes"]
    )


def consume(root, native, profile):
    origin = [round(v * 1e6) for v in profile["origin_mm"]]
    assert native["origin_nm"] == origin
    copper = []
    for layer in ("F.Cu", "B.Cu"):
        file = next((root / "gerber").glob("*-" + layer.replace(".", "_") + ".gbr"))
        actual = strokes(file)
        expected = Counter()
        for t in native["track_geometry"]:
            if t["net"] not in ("VIN", "+5V") or t["kind"] != "PCB_TRACK" or t["layer"] != layer:
                continue
            points = [(p[0] - origin[0], origin[1] - p[1]) for p in (t["start_nm"], t["end_nm"])]
            expected[(*sorted(points), t["width_nm"])] += 1
        assert not expected - actual, (layer, expected - actual)
        copper.append(
            {
                "layer": layer,
                "expected": sum(expected.values()),
                "matched": sum(expected.values()),
                "tolerance_nm": 0,
                "sha256": sha(file),
            }
        )
    desired_holes = []
    for fp in native["footprints"]:
        for p in fp["pads"]:
            if p["drill_nm"][0]:
                assert p["drill_nm"][0] == p["drill_nm"][1], (
                    "Slots are not supported by this consumer"
                )
                desired_holes.append((tuple(p["position_nm"]), p["drill_nm"][0]))
    for t in native["track_geometry"]:
        if t["kind"] == "PCB_VIA":
            assert sorted(t["width_by_layer_nm"]) == ["B.Cu", "F.Cu"], "Blind/buried not covered"
            desired_holes.append((tuple(t["start_nm"]), t["drill_nm"]))
    remaining = list(desired_holes)
    for file in (root / "drill").glob("*.drl"):
        text = file.read_text(encoding="utf-8")
        assert "METRIC" in text and "INCH" not in text and "G85" not in text and "M15" not in text
        diameters = {
            int(m[1]): int(Decimal(m[2]) * 1000000)
            for m in re.finditer(r"(?m)^T(\d+)C([\d.]+)", text)
        }
        tool = None
        for line in text.splitlines():
            if re.fullmatch(r"T\d+", line):
                tool = int(line[1:])
            match = re.fullmatch(r"X([+-]?[\d.]+)Y([+-]?[\d.]+)", line)
            if match:
                point = (
                    int(Decimal(match[1]) * 1000000) + origin[0],
                    origin[1] - int(Decimal(match[2]) * 1000000),
                )
                found = next(
                    (
                        i
                        for i, (p, diameter) in enumerate(remaining)
                        if diameter == diameters[tool]
                        and all(abs(a - b) <= 1000 for a, b in zip(p, point, strict=True))
                    ),
                    None,
                )
                assert found is not None, (file, point)
                remaining.pop(found)
    assert not remaining
    assembly = []
    for kind in ("bom", "cpl"):
        for suffix in ("", "-all"):
            file = root / (kind + suffix + ".csv")
            rows = list(csv.DictReader(file.open(encoding="utf-8-sig")))
            expected_refs = sorted(
                profile["parts"] if suffix else profile["primary_bom_cpl"]["populated_refs"]
            )
            assert sorted(r["Refs" if kind == "bom" else "Ref"] for r in rows) == expected_refs
            for row in rows:
                ref = row["Refs" if kind == "bom" else "Ref"]
                want = profile["parts"][ref]
                if kind == "bom":
                    assert (row["Value"], row["Footprint"], row["Qty"]) == (
                        want["value"],
                        want["footprint"],
                        "1",
                    )
                    assert bool(row["DNP"].strip()) == want["dnp"]
                else:
                    assert (
                        abs(float(row["PosX"]) - want["position_mm"][0] + profile["origin_mm"][0])
                        <= profile["tolerances"]["cpl_mm"]
                    )
                    assert (
                        abs(float(row["PosY"]) + want["position_mm"][1] - profile["origin_mm"][1])
                        <= profile["tolerances"]["cpl_mm"]
                    )
                    assert (
                        abs(float(row["Rot"]) - want["rotation_deg"])
                        <= profile["tolerances"]["cpl_rotation_deg"]
                    )
                    assert row["Side"] == ("top" if want["layer"] == "F.Cu" else "bottom")
                    assert (row["Val"], row["Package"]) == (
                        want["value"],
                        want["footprint"].split(":")[1],
                    )
            assembly.append({"file": file.name, "refs": expected_refs, "sha256": sha(file)})
    return {
        "status": "passed_bounded_selected_outputs",
        "straight_copper": copper,
        "circular_holes": {
            "expected": len(desired_holes),
            "matched": len(desired_holes),
            "tolerance_nm": 1000,
        },
        "assembly": assembly,
        "profile_tolerances": profile["tolerances"],
        "origin_mm": profile["origin_mm"],
        "not_checked": [
            "full composed copper/regions/macros/arcs/mask",
            "slots/blind/buried",
            "supplier rules/approval",
            "plating process quality",
        ],
    }
