"""Finite KiCad metric/absolute straight-Gerber observation, not a CAM engine.

SR/LM/LR/LS and unknown extended instructions are retained as unsupported state.
Ucamco Gerber 2026.05 sections 4.9 and 4.12 define their geometric effects.
"""

import re
from decimal import Decimal


def nm(value):
    return int(Decimal(value) * 1000000)


def gerber(text):
    apertures, strokes, flashes, contours, unknown = {}, [], [], [], []
    aperture, point, region, dark = None, (0, 0), False, True
    current = []
    clear = arc = False
    macro_count = 0
    mm = absolute = False
    for item in re.findall(r"%[^%]*%|[^%]*?\*", text, re.S):
        item = item.strip()
        if not item:
            continue
        if item.startswith("G04"):
            attribute = re.fullmatch(r"G04\s+#@!\s*(T[FAOD].*)\*", item, re.S)
            if not attribute:
                continue
            item = "%" + attribute[1] + "*%"
        if item.startswith("%"):
            body = item[1:-1].strip()
            if body.startswith("ADD"):
                match = re.fullmatch(r"ADD(\d+)([^,*]+)(?:,([^*]+))?\*", body)
                if match:
                    apertures[int(match[1])] = (match[2], match[3])
                else:
                    unknown.append(body)
            elif body.startswith("AM"):
                macro_count += 1
            elif body == "LPC*":
                dark, clear = False, True
            elif body == "LPD*":
                dark = True
            elif body == "MOMM*":
                mm = True
            elif body == "FSLAX46Y46*":
                absolute = True
            elif body.startswith(("TF", "TA", "TO", "TD")):
                if "FilePolarity," in body and body != "TF.FilePolarity,Positive*":
                    clear = True
            else:
                unknown.append(body)
            continue
        body = item[:-1].strip()
        if body == "G36":
            region, current = True, []
            continue
        if body == "G37":
            if current:
                contours.append(current)
            current, region = [], False
            continue
        if body in ("G01", "G1", "G75", "M02"):
            continue
        if body.startswith(("G02", "G03", "G2", "G3", "G74")):
            arc = True
            continue
        selected = re.fullmatch(r"D(\d+)", body)
        if selected and int(selected[1]) >= 10:
            aperture = int(selected[1])
            continue
        coordinate = re.fullmatch(r"(?:X(-?\d+))?(?:Y(-?\d+))?D0?([123])", body)
        if coordinate:
            destination = (
                int(coordinate[1]) if coordinate[1] is not None else point[0],
                int(coordinate[2]) if coordinate[2] is not None else point[1],
            )
            op = int(coordinate[3])
            shape, params = apertures.get(aperture, (None, None))
            width = nm(params.split("X")[0]) if shape == "C" and params else None
            if region:
                if op == 2:
                    if current:
                        contours.append(current)
                    current = [destination]
                elif op == 1:
                    if not current:
                        current = [point]
                    current.append(destination)
            elif op == 1:
                strokes.append(
                    {"a": point, "b": destination, "width_nm": width, "shape": shape, "dark": dark}
                )
            elif op == 3:
                flashes.append(
                    {"point": destination, "shape": shape, "parameters": params, "dark": dark}
                )
            point = destination
        else:
            unknown.append(body)
    if region:
        unknown.append("unclosed region")
    return {
        "units_mm_4_6_absolute": mm and absolute,
        "strokes": strokes,
        "flashes": flashes,
        "contours": contours,
        "clear_polarity": clear,
        "macros_unverified": macro_count,
        "arcs_unverified": arc,
        "unknown_commands": unknown,
    }


def outline_problems(data):
    problems = []
    for key in (
        "clear_polarity",
        "macros_unverified",
        "arcs_unverified",
        "unknown_commands",
        "flashes",
        "contours",
    ):
        if data[key]:
            problems.append(key)
    if not data["units_mm_4_6_absolute"]:
        problems.append("units_or_format")
    if not data["strokes"] or any(
        s["shape"] != "C" or s["width_nm"] is None or s["width_nm"] <= 0 or not s["dark"]
        for s in data["strokes"]
    ):
        problems.append("non_positive_circular_straight_stroke")
    return problems
