"""Standalone RS-274X/Excellon/CSV consumer for the declared small task B.

No product writer/parser or KiCad SDK import. Ground fill contours, via flashes,
round drill coordinates and assembly values/poses only; no full artwork claim.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from decimal import Decimal
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cycle(points):
    points = [tuple(p) for i, p in enumerate(points) if i == 0 or p != points[i - 1]]
    if len(points) > 1 and points[-1] == points[0]:
        points.pop()
    start = min(range(len(points)), key=lambda i: points[i])
    a = points[start:] + points[:start]
    reverse = list(reversed(points))
    start = min(range(len(reverse)), key=lambda i: reverse[i])
    b = reverse[start:] + reverse[:start]
    return min(tuple(a), tuple(b))


def gerber(path):
    """Read dark, untransformed metric lines/regions, refusing subtractive art.

    Macro/noncircular *flashes* are identified but not shape-validated. Under
    the supported dark-only layer policy they cannot erase a matched stroke.
    Unknown drawing state is a refusal, never an empty/ignored geometry record.
    """
    text = path.read_text(encoding="utf-8")
    match = re.search(r"%FSLAX(\d)(\d)Y(\d)(\d)\*%", text)
    if not match or match[2] != match[4] or "%MOMM*%" not in text:
        raise ValueError("Unsupported Gerber coordinate format or units")
    scale = Decimal(10) ** (6 - int(match[2]))
    apertures, macros, unchecked_flashes = {}, set(), Counter()
    position, code, region = [None, None], None, None
    regions, flashes, silk_points, strokes = [], [], [], []
    ended, consumed = False, 0
    have_format = have_units = linear = False
    for block in re.finditer(r"%[^%]*%|[^%*]+\*", text):
        if text[consumed : block.start()].strip():
            raise ValueError("Unparsed Gerber content")
        consumed = block.end()
        token = block[0].strip()
        if ended:
            raise ValueError("Geometry after Gerber end marker")
        # Ucamco 2026.05 section 5.1: standardized comment attributes have
        # exactly the direct attribute's meaning, including FilePolarity.
        if re.match(r"G04\s*#@!", token):
            attribute = re.fullmatch(r"G04\s+#@!\s+((?:TF\.|TA\.|TO\.|TD\.)[^*]+|TD)\*", token)
            if not attribute:
                raise ValueError("Unsupported Gerber comment attribute")
            token = "%" + attribute[1] + "*%"
        if token.startswith("%"):
            content = token[1:-1]
            if not content.endswith("*"):
                raise ValueError("Incomplete Gerber parameter")
            command = content[:-1]
            if command.startswith("AM"):
                name = command[2:].split("*", 1)[0]
                if not name or name in macros:
                    raise ValueError("Invalid/duplicate aperture macro")
                macros.add(name)
                continue
            if command.startswith("LP") and command != "LPD":
                raise ValueError("Clear or unknown layer polarity is unsupported")
            if (
                command.split(",", 1)[0] == "TF.FilePolarity"
                and command != "TF.FilePolarity,Positive"
            ):
                raise ValueError("Unsupported declared file polarity")
            if command.startswith(("TF.", "TA.", "TO.", "TD.")) or command == "TD":
                continue
            if command == "MOMM":
                have_units = True
                continue
            if command == match[0][1:-2]:
                have_format = True
                continue
            if command in ("LPD", "IPPOS", "LMN", "LR0", "LS1"):
                continue
            aperture = re.fullmatch(r"ADD(\d+)([A-Za-z_][\w.]*),?([^*]*)", command)
            if aperture:
                if not have_units:
                    raise ValueError("Aperture before units")
                number, shape, params = int(aperture[1]), aperture[2], aperture[3]
                if number in apertures or shape not in {"C", "R", "O", "P"} | macros:
                    raise ValueError("Unknown/duplicate aperture")
                width = None
                if shape == "C":
                    if not re.fullmatch(r"\d+(?:\.\d+)?", params) or Decimal(params) <= 0:
                        raise ValueError("Circular aperture holes/parameters are unsupported")
                    width = int(Decimal(params) * 1000000)
                apertures[number] = (shape, width)
                continue
            raise ValueError("Unsupported Gerber parameter: " + command[:40])
        token = token[:-1].strip()
        if token == "G01":
            linear = True
            continue
        if token.startswith("G04") or token == "G75":
            continue
        if token == "M02":
            if region is not None:
                raise ValueError("Unclosed Gerber region")
            ended = True
            continue
        if token == "G36":
            if region is not None:
                raise ValueError("Nested region")
            region = []
        elif token == "G37":
            if not region or len(region) < 4 or region[0] != region[-1]:
                raise ValueError("Only explicit closed straight region contours are supported")
            regions.append(cycle(region))
            region = None
        elif re.fullmatch(r"D\d+", token):
            code = int(token[1:])
            if code not in apertures:
                raise ValueError("Undefined aperture")
        else:
            coordinate = re.fullmatch(r"(?:G0?1)?(?:X([+-]?\d+))?(?:Y([+-]?\d+))?D0?([123])", token)
            if not coordinate or not any(coordinate[i] is not None for i in (1, 2)):
                raise ValueError("Unsupported Gerber geometry/state: " + token[:40])
            previous = tuple(position)
            for axis in (0, 1):
                if coordinate[axis + 1] is not None:
                    position[axis] = int(Decimal(coordinate[axis + 1]) * scale) * (
                        1 if axis == 0 else -1
                    )
            operation = coordinate[3]
            if token.startswith(("G01", "G1")):
                linear = True
            if not have_format or not have_units or (operation == "1" and not linear):
                raise ValueError("Undefined coordinate/linear plotting state")
            if None in position or (operation == "1" and None in previous):
                raise ValueError("Undefined drawing position")
            point = tuple(position)
            if region is not None:
                if operation == "3" or (operation == "2" and region):
                    raise ValueError("Region flashes/multiple contours are unsupported")
                region.append(point)
            elif operation in ("1", "3"):
                if code not in apertures:
                    raise ValueError("Geometry uses an undefined aperture")
                shape, width = apertures[code]
                if operation == "1":
                    if shape != "C":
                        raise ValueError("Noncircular/macro strokes are not supported")
                    strokes.append((previous, point, width))
                else:
                    flashes.append((point, width))
                    if shape != "C":
                        unchecked_flashes[shape] += 1
            silk_points.append(point)
    if text[consumed:].strip() or not ended:
        raise ValueError("Incomplete Gerber stream")
    return {
        "regions": regions,
        "flashes": flashes,
        "points": silk_points,
        "fractional_digits": int(match[2]),
        "straight_strokes": strokes,
        "polarity_scope": "dark only; clear polarity and nonidentity transforms refused",
        "unchecked_flash_shapes": dict(unchecked_flashes),
    }


def consume(root):
    native = json.loads((root / "reopened-sdk.stdout").read_bytes())
    shapes = []
    for layer in ("F.Cu", "B.Cu"):
        file = next((root / "gerber").glob("*-" + layer.replace(".", "_") + ".gbr"))
        artwork = gerber(file)
        polygons = [
            p
            for z in native["zone_geometry"]
            if z["net"] == "GND" and not z["rule_area"]
            for p in z["filled_polygons"].get(layer, [])
        ]
        assert all(p["outline"]["arcs"] == 0 and not p["holes"] for p in polygons)
        expected = Counter(cycle(p["outline"]["vertices_nm"]) for p in polygons)
        assert Counter(artwork["regions"]) == expected, (
            "Ground fill contours differ from saved native snapshot"
        )
        via = next(
            t for t in native["track_geometry"] if t["kind"] == "PCB_VIA" and t["net"] == "GND"
        )
        assert (tuple(via["start_nm"]), via["width_nm"]) in artwork["flashes"]
        shapes.append(
            {
                "layer": layer,
                "expected_regions": len(polygons),
                "observed_regions": len(artwork["regions"]),
                "vertex_count": sum(len(p["outline"]["vertices_nm"]) for p in polygons),
                "ground_via_flash_nm": via["start_nm"],
                "diameter_nm": via["width_nm"],
                "tolerance_nm": 1,
                "file_sha256": sha(file),
                "status": "passed",
            }
        )
    wanted = Counter()
    for fp in native["footprints"]:
        for pad in fp["pads"]:
            if pad["drill_nm"][0] > 0:
                assert pad["drill_nm"][0] == pad["drill_nm"][1], "Slot is out of declared scope"
                wanted[(tuple(pad["position_nm"]), pad["drill_nm"][0])] += 1
    for via in native["track_geometry"]:
        if via["kind"] == "PCB_VIA":
            wanted[(tuple(via["start_nm"]), via["drill_nm"])] += 1
    found = Counter()
    for file in (root / "drill").glob("*.drl"):
        text = file.read_text(encoding="utf-8")
        assert "METRIC" in text and "INCH" not in text
        tools = {
            int(m[1]): int(Decimal(m[2]) * 1000000)
            for m in re.finditer(r"(?m)^T(\d+)C([\d.]+)", text)
        }
        code = None
        for line in text.splitlines():
            if re.fullmatch(r"T\d+", line):
                code = int(line[1:])
            match = re.fullmatch(r"X([+-]?[\d.]+)Y([+-]?[\d.]+)", line)
            if match:
                assert "." in match[1] or "." in match[2], (
                    "Implicit suppression is out of this bounded decimal consumer"
                )
                point = (int(Decimal(match[1]) * 1000000), -int(Decimal(match[2]) * 1000000))
                # Locate by diameter and coordinate, allow declared drill rounding.
                key = next(
                    (
                        key
                        for key in wanted
                        if key[1] == tools[code]
                        and all(abs(a - b) <= 1000 for a, b in zip(point, key[0], strict=True))
                    ),
                    None,
                )
                assert key is not None, (file, point, tools[code])
                found[key] += 1
    assert found == wanted
    cpl = list(csv.DictReader((root / "cpl.csv").open(encoding="utf-8-sig")))
    by_ref = {p["ref"]: p for p in native["footprints"]}
    assert len(cpl) == len(by_ref)
    for row in cpl:
        fp = by_ref[row["Ref"]]
        assert abs(Decimal(row["PosX"]) * 1000000 - fp["position_nm"][0]) <= 1000
        assert abs(-Decimal(row["PosY"]) * 1000000 - fp["position_nm"][1]) <= 1000
        assert Decimal(row["Rot"]) == Decimal(str(fp["rotation_deg"]))
        assert row["Side"] == ("top" if fp["layer"] == "F.Cu" else "bottom")
    bom = list(csv.DictReader((root / "bom.csv").open(encoding="utf-8-sig")))
    assert {r["Refs"]: r["Value"] for r in bom} == {
        p["ref"]: p["value"] for p in native["footprints"]
    }
    silk = []
    if "identity_text" in native:
        file = next((root / "silk-svg").glob("*-F_SilkS.svg"))
        xml = ET.parse(file).getroot()
        saved = {r["ref"]: r["reference_text"] for r in native["identity_text"]}
        for group in xml.iter("{http://www.w3.org/2000/svg}g"):
            if group.attrib.get("class") != "stroked-text":
                continue
            label = group.find("{http://www.w3.org/2000/svg}desc").text
            assert label in saved
            points = [
                (int(Decimal(x) * 1000000), int(Decimal(y) * 1000000))
                for path in group.iter("{http://www.w3.org/2000/svg}path")
                for x, y in re.findall(r"[ML]\s*([+-]?[\d.]+)[, ]+([+-]?[\d.]+)", path.attrib["d"])
            ]
            assert points
            bb = saved[label]["box_nm"]
            edge = native["edge_bbox_nm"]
            assert all(
                bb[0] - 1 <= x <= bb[2] + 1 and bb[1] - 1 <= y <= bb[3] + 1 for x, y in points
            )
            assert all(edge[0] <= x <= edge[2] and edge[1] <= y <= edge[3] for x, y in points)
            silk.append(
                {
                    "ref": label,
                    "stroke_points": len(points),
                    "saved_box_nm": bb,
                    "svg_matches_saved_bounds": True,
                    "inside_edge": True,
                    "tolerance_nm": 1,
                }
            )
        assert {r["ref"] for r in silk} == set(saved)
        import fitz

        image = fitz.open(file)
        document = fitz.open("pdf", image.convert_to_pdf())
        document[0].get_pixmap(matrix=fitz.Matrix(2, 2)).save(root / "silk-full-page.png")
        edge = native["edge_bbox_nm"]
        scale = 72 / 25.4 / 1000000
        clip = fitz.Rect(*[v * scale + (-4 if i < 2 else 4) for i, v in enumerate(edge)])
        document[0].get_pixmap(matrix=fitz.Matrix(6, 6), clip=clip).save(root / "silk-render.png")
    result = {
        "status": "passed",
        "native_input_sha256": native["input_sha256"],
        "native_reader_sha256": native["zone_reader_sha256"],
        "consumer_sha256": sha(__file__),
        "ground_regions": shapes,
        "round_holes": {
            "expected": sum(wanted.values()),
            "observed": sum(found.values()),
            "tolerance_nm": 1000,
            "units": "mm decoded to nm",
            "status": "passed",
        },
        "bom_cpl": {
            "expected_refs": sorted(by_ref),
            "observed_refs": sorted(r["Ref"] for r in cpl),
            "origin": "board origin; Y negated in placement output",
            "pose_tolerance_nm": 1000,
            "angle_tolerance_deg": 0,
            "status": "passed",
        },
        "silk_svg": silk,
        "not_checked": [
            "full composite artwork/pad macros/mask geometry",
            "slots/blind or buried vias",
            "supplier rules/approval",
            "all KiCad capabilities",
        ],
    }
    (root / "manufacturing-readback.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    print(json.dumps(consume(parser.parse_args().root)))
