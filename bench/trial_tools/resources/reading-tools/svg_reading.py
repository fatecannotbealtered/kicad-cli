"""Finite PCB SVG reading derivative; stdlib only, no engineering writer.

Native raw SVG is immutable. All explicit M/L/Z path vertices, circles and
rectangles contribute to bounds, even outside the original SVG viewport. The
reading copy changes only root viewport/physical size, exact colour values and
one first-child background rectangle. Opacity, paths, text, order and transforms
stay exact. Unsupported geometry/effects refuse rather than guess bounds.
"""

import argparse
import colorsys
import copy
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from decimal import Decimal as D
from pathlib import Path

NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", NS)
NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
TOKEN = re.compile(r"[MLZ]|" + NUMBER)
PAINT = re.compile(r"(?P<prefix>(?:^|;)\s*(?:fill|stroke)\s*:\s*)(?P<paint>#[0-9A-Fa-f]{6})(?=\s*(?:;|$))")
BACKGROUND = "#111827"
MARGIN = D("2")
ALLOWED = {"svg", "title", "desc", "g", "path", "circle", "rect", "text"}


class Incomplete(ValueError):
    pass


def need(condition, message):
    if not condition:
        raise Incomplete(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def local(tag):
    return tag.rsplit("}", 1)[-1]


def decimal(value):
    need(isinstance(value, str) and re.fullmatch(NUMBER, value.strip()), "Unsupported SVG number")
    result = D(value)
    need(result.is_finite(), "Nonfinite SVG number")
    return result


def fmt(value):
    return format(value, "f")


def style(node, inherited):
    result = dict(inherited)
    attrs = dict(node.attrib)
    for field in attrs.get("style", "").split(";"):
        if field.strip():
            need(":" in field, "Malformed inline style")
            key, value = field.split(":", 1)
            attrs[key.strip()] = value.strip()
    for key in ["stroke", "stroke-width", "stroke-linejoin", "stroke-linecap", "fill"]:
        if key in attrs:
            result[key] = attrs[key]
    result["opacity"] = inherited.get("opacity", D(1)) * decimal(attrs.get("opacity", "1"))
    return result


def path_points(data):
    tokens, end = [], 0
    for match in TOKEN.finditer(data):
        need(not data[end:match.start()].strip(" \t\r\n,"), "Unqualified SVG path command/token")
        tokens.append(match.group())
        end = match.end()
    need(not data[end:].strip(" \t\r\n,"), "Unqualified SVG path suffix")
    points, command, cursor, opened = [], None, 0, False
    while cursor < len(tokens):
        if tokens[cursor] in {"M", "L", "Z"}:
            command = tokens[cursor]
            cursor += 1
            if command == "Z":
                need(opened, "Path close without move")
                command = None
                continue
        need(command in {"M", "L"} and cursor + 1 < len(tokens), "Incomplete native straight path")
        need(tokens[cursor] not in {"M", "L", "Z"} and tokens[cursor + 1] not in {"M", "L", "Z"}, "Odd SVG coordinate list")
        need(command == "M" or opened, "Path line without move")
        points.append((decimal(tokens[cursor]), decimal(tokens[cursor + 1])))
        opened = True
        cursor += 2
        if command == "M":
            command = "L"
    need(points, "Empty path has no measurable geometry")
    return points


def hidden_metadata_only(node, inherited):
    """Actual KiCad Fab SVG wraps invisible semantic text in rotated groups.

    No visible geometry may inherit this exception. Its original rotation,
    opacity and text remain exact in the derivative; glyph paths are measured
    independently where the native exporter actually emits them.
    """
    text_count = 0

    def visit(element, parent):
        nonlocal text_count
        if local(element.tag) not in {"g", "text", "desc"}:
            return False
        state = style(element, parent)
        if local(element.tag) == "text":
            text_count += 1
            if state["opacity"] != 0:
                return False
        return all(visit(child, state) for child in element)

    return visit(node, inherited) and text_count > 0


def inspect(raw):
    need(b"<!ENTITY" not in raw.upper(), "SVG entities refused")
    root = ET.fromstring(raw)
    need(root.tag == "{" + NS + "}svg", "Expected native SVG namespace")
    viewport = [decimal(value) for value in root.attrib["viewBox"].split()]
    need(len(viewport) == 4 and viewport[2] > 0 and viewport[3] > 0, "Incomplete native viewport")
    for key, size in [("width", viewport[2]), ("height", viewport[3])]:
        need(root.attrib[key].endswith("mm") and decimal(root.attrib[key][:-2]) == size,
             "Only native1 SVG-unit-per-mm viewport is qualified")
    bounds, primitives, text_groups = [], [], []

    def walk(node, inherited):
        tag = local(node.tag)
        need(tag in ALLOWED and node.tag == "{" + NS + "}" + tag, "Unsupported SVG element")
        need(all(not key.lower().startswith("on") and "href" not in key.lower() for key in node.attrib), "Script/external resource refused")
        transform = node.attrib.get("transform")
        identity = transform in {None, "", "translate(0 0) scale(1 1)"}
        rotated_metadata = (tag == "g" and isinstance(transform, str)
            and re.fullmatch(r"rotate\(" + NUMBER + r"\s+" + NUMBER + r"\s+" + NUMBER + r"\)", transform)
            and hidden_metadata_only(node, inherited))
        need(identity or rotated_metadata, "Unqualified PCB SVG transform")
        need(all(key not in node.attrib for key in ["clip-path", "mask", "filter"])
             and all("url(" not in value.lower() for value in node.attrib.values()), "Clipping/indirect SVG effects refused")
        state = style(node, inherited)
        if "style" in node.attrib:
            need(not any(value in node.attrib["style"].lower() for value in ["display:", "visibility:", "clip-path:", "filter:", "mask:"]),
                 "Unqualified visibility or effect style")
        points = None
        if tag == "path":
            points = path_points(node.attrib.get("d", ""))
        elif tag == "circle":
            x, y, radius = (decimal(node.attrib[key]) for key in ["cx", "cy", "r"])
            need(radius >= 0, "Negative circle radius")
            points = [(x - radius, y - radius), (x + radius, y + radius)]
        elif tag == "rect":
            x, y = decimal(node.attrib.get("x", "0")), decimal(node.attrib.get("y", "0"))
            width, height = decimal(node.attrib["width"]), decimal(node.attrib["height"])
            need(width >= 0 and height >= 0, "Negative rectangle extent")
            points = [(x, y), (x + width, y + height)]
        elif tag == "text":
            # Native exporter retains hidden semantic text and emits its actual
            # glyph strokes separately. Keep it byte-semantically unchanged.
            need(state["opacity"] == 0, "Visible font text has no qualified glyph metrics")
        if points:
            need(all(value == "none" or re.fullmatch(r"#[0-9a-fA-F]{6}", value)
                     for value in [state.get("fill", "black"), state.get("stroke", "none")]),
                 "Native visible geometry requires explicit six-digit RGB/none paint")
            width = decimal(state.get("stroke-width", "1")) if state.get("stroke", "none") != "none" else D(0)
            need(width >= 0, "Negative native stroke width")
            if width:
                need(state.get("stroke-linejoin", "round") == "round" and state.get("stroke-linecap", "round") == "round",
                     "Only native round stroke caps/joins are qualified")
            box = [min(p[0] for p in points) - width / 2, min(p[1] for p in points) - width / 2,
                   max(p[0] for p in points) + width / 2, max(p[1] for p in points) + width / 2]
            bounds.append(box)
            primitives.append({"tag": tag, "bounds_mm": [fmt(value) for value in box], "geometry_sha256": sha(ET.tostring(node))})
        if tag == "g" and node.attrib.get("class") == "stroked-text":
            descriptions = [value for value in node if local(value.tag) == "desc"]
            need(len(descriptions) == 1, "Native stroked text lacks exact desc")
            paths = [value.attrib.get("d", "") for value in node.iter() if local(value.tag) == "path"]
            if (descriptions[0].text or "").strip():
                need(paths, "Native text identity has no glyph paths")
                vertices = [p for path in paths for p in path_points(path)]
                text_groups.append({"text": descriptions[0].text, "path_count": len(paths),
                    "path_sequences_sha256": sha(json.dumps(paths, ensure_ascii=False).encode()),
                    "glyph_vertex_bounds_mm": [fmt(min(p[0] for p in vertices)), fmt(min(p[1] for p in vertices)),
                                               fmt(max(p[0] for p in vertices)), fmt(max(p[1] for p in vertices))]})
        for child in node:
            walk(child, state)

    walk(root, {"stroke": "none", "fill": "black", "opacity": D(1)})
    need(bounds, "No measured native geometry")
    bbox = [min(value[0] for value in bounds), min(value[1] for value in bounds),
            max(value[2] for value in bounds), max(value[3] for value in bounds)]
    return root, {"raw_sha256": sha(raw), "geometry_bounds_mm": [fmt(value) for value in bbox],
                  "primitive_count": len(primitives), "primitive_bounds": primitives,
                  "stroked_text_groups": text_groups,
                  "original_viewport": {key: root.attrib[key] for key in ["width", "height", "viewBox"]}}


def bright_colour(value):
    need(re.fullmatch(r"#[0-9a-fA-F]{6}", value), "Only explicit native six-digit RGB paint is qualified")
    rgb = [int(value[k:k + 2], 16) / 255 for k in [1, 3, 5]]
    hue, light, saturation = colorsys.rgb_to_hls(*rgb)
    converted = colorsys.hls_to_rgb(hue, max(light, 0.78), min(saturation, 0.8))
    return "#" + "".join("%02X" % round(channel * 255) for channel in converted)


def paint_copy(root):
    output, edits, palette = copy.deepcopy(root), [], {}
    for index, node in enumerate(output.iter()):
        for attribute in ["fill", "stroke", "style"]:
            before = node.attrib.get(attribute)
            if before is None:
                continue
            if attribute == "style":
                def replace(match):
                    paint = match.group("paint")
                    colour = palette.setdefault(paint, bright_colour(paint))
                    return match.group("prefix") + colour
                after = PAINT.sub(replace, before)
            else:
                after = palette.setdefault(before, bright_colour(before)) if before.startswith("#") else before
            if before != after:
                edits.append({"node_index": index, "attribute": attribute, "before": before, "after": after})
                node.attrib[attribute] = after
    return output, edits, palette


def derive(raw):
    root, observation = inspect(raw)
    output, edits, palette = paint_copy(root)
    left, top, right, bottom = map(D, observation["geometry_bounds_mm"])
    left, top, right, bottom = left - MARGIN, top - MARGIN, right + MARGIN, bottom + MARGIN
    viewport = {"width": fmt(right - left) + "mm", "height": fmt(bottom - top) + "mm",
                "viewBox": " ".join(map(fmt, [left, top, right - left, bottom - top]))}
    output.attrib.update(viewport)
    background = {"id": "reading-background", "x": fmt(left), "y": fmt(top),
                  "width": fmt(right - left), "height": fmt(bottom - top),
                  "fill": BACKGROUND, "stroke": "none"}
    output.insert(0, ET.Element("{" + NS + "}rect", background))
    derivative = ET.tostring(output, encoding="utf-8", xml_declaration=True)
    proof = {"schema": "finite-PCB-reading-derivative/v1", "raw_sha256": sha(raw),
             "derivative_sha256": sha(derivative), "observation": observation,
             "palette": palette, "colour_edits": edits, "reading_viewport": viewport,
             "background": background, "margin_mm": "2", "geometry_or_opacity_edits": 0,
             "method_sha256": sha(Path(__file__).read_bytes())}
    verify(raw, derivative, proof)
    return derivative, proof


def verify(raw, derivative, proof):
    original, observed = inspect(raw)
    need(proof["schema"] == "finite-PCB-reading-derivative/v1" and proof["raw_sha256"] == sha(raw)
         and proof["derivative_sha256"] == sha(derivative), "Exact raw/derivative binding differs")
    need(proof["observation"] == observed and proof["margin_mm"] == "2", "Independent geometry observation differs")
    _painted, expected_edits, expected_palette = paint_copy(original)
    need(proof["colour_edits"] == expected_edits and proof["palette"] == expected_palette,
         "Palette proof differs from exact deterministic RGB changes")
    need(proof["method_sha256"] == sha(Path(__file__).read_bytes()), "Reading derivative method bytes differ")
    tree = ET.fromstring(derivative)
    need(len(tree) > 0 and tree[0].tag == "{" + NS + "}rect" and tree[0].attrib == proof["background"]
         and tree[0].attrib["fill"] == BACKGROUND, "Reading background is not an exact first-child underlay")
    need({key: tree.attrib[key] for key in ["width", "height", "viewBox"]} == proof["reading_viewport"], "Reading viewport differs")
    left, top, right, bottom = map(D, observed["geometry_bounds_mm"])
    expected = [left - MARGIN, top - MARGIN, right - left + 2 * MARGIN, bottom - top + 2 * MARGIN]
    expected_background = {"id": "reading-background", "x": fmt(expected[0]), "y": fmt(expected[1]),
                           "width": fmt(expected[2]), "height": fmt(expected[3]), "fill": BACKGROUND, "stroke": "none"}
    need(proof["background"] == expected_background, "Background must be the exact full reading viewport")
    need([decimal(value) for value in tree.attrib["viewBox"].split()] == expected
         and decimal(tree.attrib["width"][:-2]) == expected[2] and decimal(tree.attrib["height"][:-2]) == expected[3],
         "Reading viewport is not every-path bounds plus exactly2mm")
    tree.remove(tree[0])
    nodes = list(tree.iter())
    for edit in proof["colour_edits"]:
        node = nodes[edit["node_index"]]
        need(node.attrib[edit["attribute"]] == edit["after"], "Palette edit binding differs")
        node.attrib[edit["attribute"]] = edit["before"]
    tree.attrib.update(observed["original_viewport"])
    need(ET.tostring(tree) == ET.tostring(original), "Derivative changed geometry/text/order/style/opacity outside authorized palette/viewport")
    return {"schema": "finite-PCB-reading-check/v1", "status": "geometry_preserved_pending_actual_view",
            "raw_sha256": sha(raw), "derivative_sha256": sha(derivative), "all_original_elements_preserved": True,
            "every_primitive_inside_reading_viewport": True, "primitive_count": observed["primitive_count"],
            "margin_mm": "2", "geometry_or_opacity_edits": 0, "actual_view_performed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["derive", "check"])
    for key in ["raw", "derivative", "proof", "out"]:
        parser.add_argument("--" + key, required=True)
    args = parser.parse_args()
    raw = Path(args.raw).read_bytes()
    if args.mode == "derive":
        derivative, proof = derive(raw)
        with Path(args.derivative).open("xb") as stream:
            stream.write(derivative)
        with Path(args.proof).open("x", encoding="utf-8") as stream:
            json.dump(proof, stream, ensure_ascii=False, indent=2)
    else:
        derivative, proof = Path(args.derivative).read_bytes(), json.loads(Path(args.proof).read_bytes())
    result = verify(raw, derivative, proof)
    with Path(args.out).open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
