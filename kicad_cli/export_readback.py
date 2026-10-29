"""Independent bounded format consumption, not full artwork certification."""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .move_transaction import regular

MAX_BYTES = 64 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024


def excellon(text):
    if not text.startswith("M48") or not re.search(r"(?m)^M30\s*$", text):
        raise ValueError("incomplete Excellon header/end")
    metric = re.search(r"(?m)^METRIC(?:,|\s*$)", text) is not None
    inch = re.search(r"(?m)^INCH(?:,|\s*$)", text) is not None
    if metric == inch:
        raise ValueError("missing or ambiguous Excellon units")
    scale, tools, selected = (1.0 if metric else 25.4), {}, None
    holes, slots, position, routing = [], [], None, False
    for line in text.splitlines():
        line = line.strip()
        tool = re.fullmatch(r"T(\d+)C([\d.]+)", line)
        if tool:
            diameter = float(tool[2]) * scale
            if diameter <= 0 or int(tool[1]) in tools:
                raise ValueError("invalid or duplicate drill tool")
            tools[int(tool[1])] = diameter
            continue
        tool = re.fullmatch(r"T(\d+)", line)
        if tool:
            selected = int(tool[1])
            if selected not in tools:
                raise ValueError("undefined drill tool")
            continue
        if line == "M15":
            routing = True
            continue
        if line == "M16":
            routing = False
            continue
        if line.startswith(("X", "Y", "G00", "G01", "G05")):
            if line == "G05":
                continue
            match = re.fullmatch(r"(G00|G01|G05)?X(-?\d+\.\d*)Y(-?\d+\.\d*)", line)
            if not match or selected is None:
                raise ValueError("unsupported drill coordinate, slot or number format: " + line)
            point = [float(match[2]) * scale, float(match[3]) * scale]
            if match[1] == "G00":
                position = point
            elif routing:
                if position is None:
                    raise ValueError("slot has no start")
                slots.append(
                    {"start_mm": position, "end_mm": point, "diameter_mm": tools[selected]}
                )
                position = point
            else:
                holes.append({"position_mm": point, "diameter_mm": tools[selected]})
                position = point
    return {
        "format": "excellon",
        "units": "mm" if metric else "inch",
        "tools_mm": {str(key): value for key, value in tools.items()},
        "holes": holes,
        "slots": slots,
    }


def consume(path: Path):
    regular(path)
    if not 0 < path.stat().st_size <= MAX_BYTES:
        raise ValueError("empty or oversized export file")
    raw = path.read_bytes()
    text = raw.decode("utf-8", "replace")
    suffix = path.suffix.lower()
    if suffix in {
        ".gbr",
        ".gtl",
        ".gbl",
        ".gts",
        ".gbs",
        ".gtp",
        ".gbp",
        ".gto",
        ".gbo",
        ".gm1",
    } or re.search(r"%FS[LT]A", text[:4000]):
        fs = re.search(r"%FSLAX(\d)(\d)Y(\d)(\d)\*%", text)
        units = re.search(r"%MO(MM|IN)\*%", text)
        if not fs or not units or not text.rstrip().endswith("M02*"):
            raise ValueError("incomplete or unsupported Gerber framing")
        result = {
            "format": "gerber",
            "units": "mm" if units[1] == "MM" else "inch",
            "coordinate_digits": list(map(int, fs.groups())),
            "file_function": re.findall(r"%TF\.FileFunction,([^*]+)\*%", text),
            "apertures": len(re.findall(r"%ADD\d+", text)),
            "flashes": len(re.findall(r"D0?3\*", text)),
            "regions": len(re.findall(r"G36\*", text)),
            "limits": "Framing, units and record counts; full shape equivalence is unchecked.",
        }
    elif suffix == ".drl":
        result = excellon(text)
    elif suffix == ".svg":
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise ValueError("invalid SVG XML") from exc
        if root.tag != "{http://www.w3.org/2000/svg}svg":
            raise ValueError("not an SVG document")
        result = {
            "format": "svg",
            "viewBox": root.get("viewBox"),
            "width": root.get("width"),
            "height": root.get("height"),
            "elements": len(list(root.iter())),
        }
    elif suffix == ".pdf":
        pages = len(re.findall(rb"/Type\s*/Page\b", raw))
        if not raw.startswith(b"%PDF-") or b"%%EOF" not in raw[-1024:] or pages < 1:
            raise ValueError("unsupported or incomplete PDF page structure")
        result = {
            "format": "pdf",
            "pages": pages,
            "limits": "Page dictionaries and framing; rendering is separate.",
        }
    elif suffix == ".dxf":
        if not text.rstrip().endswith("EOF") or not re.search(r"(?m)^ENTITIES\s*$", text):
            raise ValueError("incomplete DXF entities/end")
        result = {
            "format": "dxf",
            "entities": len(
                re.findall(r"(?m)^\s*0\s*\n(?:LINE|POLYLINE|LWPOLYLINE|CIRCLE|ARC|TEXT)\s*$", text)
            ),
        }
    elif suffix == ".rpt":
        totals = {
            k: int(v)
            for k, v in re.findall(
                r"Total (plated|unplated) holes count\s+(\d+)", text, re.IGNORECASE
            )
        }
        if not totals:
            raise ValueError("unrecognized drill report")
        result = {"format": "drill-report", "totals": {k.lower(): v for k, v in totals.items()}}
    else:
        raise ValueError("unrecognized generated artifact: " + path.name)
    return dict(result, name=path.name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def inventory(directory):
    if not directory.exists():
        return []
    files = sorted(directory.iterdir())
    if len(files) > 128 or sum(p.stat().st_size for p in files) > MAX_TOTAL:
        raise ValueError("export inventory bounds exceeded")
    return [consume(p) for p in files]
