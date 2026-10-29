"""Finite independent identity check for native all-hierarchy SVG outputs.

Checks document/page markers AND actual nonhidden stroked-text groups. It does
not decode glyph outlines or replace human full-page review. No product import.
"""

import argparse
import copy
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from decimal import Decimal
from pathlib import Path


class Incomplete(ValueError):
    pass


def need(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def local(tag):
    return tag.rsplit("}", 1)[-1]


def style(node, inherited):
    result = dict(inherited)
    attrs = dict(node.attrib)
    for atom in attrs.get("style", "").split(";"):
        if atom.strip():
            need(":" in atom, "Malformed inline SVG style")
            key, value = atom.split(":", 1)
            attrs[key.strip()] = value.strip()
    for key in ["fill", "stroke", "fill-opacity", "stroke-opacity", "stroke-width", "visibility"]:
        if key in attrs:
            result[key] = attrs[key]
    result["cumulative-opacity"] = inherited.get("cumulative-opacity", Decimal(1)) * Decimal(attrs.get("opacity", "1"))
    result["display-none"] = inherited.get("display-none", False) or attrs.get("display") == "none"
    if attrs.get("transform") not in {None, "", "translate(0 0) scale(1 1)"}:
        result["unsupported-transform"] = attrs["transform"]
    for key in ["clip-path", "mask", "filter"]:
        if attrs.get(key) not in {None, "", "none"}:
            result["unsupported-effect"] = key + "=" + attrs[key]
    return result


def visible_paths(group, inherited):
    count = 0
    for node in group:
        state = style(node, inherited)
        if state.get("unsupported-transform") or state.get("unsupported-effect"):
            raise Incomplete("Unqualified transform/clip/mask in identity glyph group")
        visible = not state.get("display-none") and state.get("visibility", "visible") not in {"hidden", "collapse"} and state["cumulative-opacity"] > 0
        if visible and local(node.tag) == "path" and node.attrib.get("d", "").strip():
            stroke = state.get("stroke", "none") != "none" and Decimal(state.get("stroke-opacity", "1")) > 0 and Decimal(state.get("stroke-width", "1")) > 0
            fill = state.get("fill", "black") != "none" and Decimal(state.get("fill-opacity", "1")) > 0
            if stroke or fill:
                count += 1
        count += visible_paths(node, state)
    return count


def drawing_observation(data):
    need(b"<!ENTITY" not in data.upper(), "SVG entity declarations are not allowed")
    root = ET.fromstring(data)
    need(local(root.tag) == "svg", "Not an SVG document")
    if any(local(node.tag) in {"style", "image", "use", "script", "foreignObject"} for node in root.iter()):
        raise Incomplete("External/reference/CSS raster SVG content is not qualified")
    texts = Counter()
    group_rows = []
    def walk(node, inherited):
        state = style(node, inherited)
        if local(node.tag) == "g" and "stroked-text" in node.attrib.get("class", "").split():
            descs = [v for v in node if local(v.tag) == "desc"]
            need(len(descs) == 1, "Stroked glyph group lacks unique text semantics")
            name = descs[0].text or ""
            # KiCad emits empty native text groups (e.g. blank fields). They
            # contribute NO identity evidence and need no glyph strokes. Every
            # required title/file/scope/page/ref marker remains nonempty and
            # must independently have visible paths below.
            if name.strip():
                if state.get("unsupported-transform") or state.get("unsupported-effect"):
                    raise Incomplete("Unqualified transform/clip/mask on identity text")
                need(not state.get("display-none") and state.get("visibility", "visible") not in {"hidden", "collapse"}
                     and state["cumulative-opacity"] > 0, "Identity text group is hidden")
                paths = visible_paths(node, state)
                need(paths > 0, "Semantic identity text has no actual visible path strokes")
                texts[name] += 1
                group_rows.append({"text": name, "visible_path_primitives": paths})
        for child in node:
            walk(child, state)
    walk(root, {"fill": "black", "stroke": "none", "cumulative-opacity": Decimal(1)})
    normalized = copy.deepcopy(root)
    for item in list(normalized):
        if local(item.tag) == "title":
            normalized.remove(item)  # Native filename/date metadata only.
    canonical = hashlib.sha256(ET.tostring(normalized, encoding="utf-8")).hexdigest()
    return {"visible_text_counts": dict(texts), "stroked_text_groups": group_rows, "canonical_drawing_sha256_without_creation_title": canonical,
            "viewport": {key: root.attrib.get(key) for key in ["width", "height", "viewBox"]}}


def expected_pages(spec):
    need(spec["hierarchy"]["schema"] == "direct-two-child/v1", "Unqualified hierarchy")
    parts = spec["parts"]
    need(len(parts) == 20 and len({v["ref"] for v in parts}) == 20, "Expected20 unique physical parts")
    pages = {"root": {"file": "hier_dual_rc.kicad_sch", "title": spec["hierarchy"]["root"]["title"], "sheet": "/", "page": "1/3"}}
    for number, child in enumerate(spec["hierarchy"]["children"], start=2):
        pages[child["id"]] = {"file": child["file"], "title": child["title"], "sheet": "/" + child["name"] + "/", "page": str(number) + "/3"}
    need(set(pages) == {"root", "A", "B"}, "Expected root+A+B only")
    for page, row in pages.items():
        refs = [part["ref"] for part in parts if part["sheet_id"] == page]
        if "U1" in refs:
            refs.remove("U1")
            refs += ["U1A", "U1B", "U1C"]
        row["displayed_references"] = sorted(refs)
    return pages


def map_pages(observations, expected):
    need(len(observations) == 3, "Expected exactly3 complete native SVG pages")
    result = {}
    geometry_hashes = set()
    for name, observation in observations.items():
        texts = observation["visible_text_counts"]
        matches = []
        for page, row in expected.items():
            markers = ["File: " + row["file"], "Title: " + row["title"], "Sheet: " + row["sheet"], "Id: " + row["page"]]
            if all(texts.get(marker) == 1 for marker in markers):
                matches.append(page)
        need(len(matches) == 1 and matches[0] not in result, "Wrong, duplicate or ambiguous actual page identity: " + name)
        page = matches[0]
        actual_refs = {key: count for key, count in texts.items() if re.fullmatch(r"(?:C|R|J|TP|U)\d+[ABC]?", key)}
        need(actual_refs == {ref: 1 for ref in expected[page]["displayed_references"]}, "Wrong/missing/duplicate displayed physical ref/unit on page " + page)
        canonical = observation["canonical_drawing_sha256_without_creation_title"]
        need(canonical not in geometry_hashes, "Two page results have the same actual drawing after creation timestamp removal")
        geometry_hashes.add(canonical)
        result[page] = {"svg_filename": name, "expected_markers": expected[page], "observation": observation}
    need(set(result) == set(expected), "Missing actual child page; root image cannot stand in")
    return result


def check(spec_path, intent_path, project, svg_dir):
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8-sig"))
    intent = json.loads(Path(intent_path).read_text(encoding="utf-8-sig"))
    need(sha(spec_path) == intent["spec_sha256"], "Spec drift from predeclared intent")
    expected = expected_pages(spec)
    project, svg_dir = Path(project), Path(svg_dir)
    source_bindings = {}
    for page, row in expected.items():
        path = project / row["file"]
        need(path.is_file() and not path.is_symlink(), "Actual bound page input missing")
        source_bindings[page] = {"path": str(path.resolve()), "sha256": sha(path)}
    files = sorted(svg_dir.iterdir())
    need(len(files) == 3 and all(p.is_file() and not p.is_symlink() and p.suffix == ".svg" for p in files), "Expected exact3 regular raw SVG files only")
    before = {p.name: sha(p) for p in files}
    observations = {p.name: drawing_observation(p.read_bytes()) for p in files}
    mapping = map_pages(observations, expected)
    for row in mapping.values():
        path = svg_dir / row["svg_filename"]
        row["source_svg"] = {"path": str(path.resolve()), "sha256": before[path.name]}
    need(before == {p.name: sha(p) for p in files}, "SVG changed during independent identity read")
    need(all(row["sha256"] == sha(row["path"]) for row in source_bindings.values()), "Input SCH changed during identity read")
    return {"schema": "finite-native-hierarchy-SVG-page-identity/v2", "status": "passed", "pages": mapping,
            "source_schematic_bindings": source_bindings, "spec_sha256": sha(spec_path), "intent_sha256": sha(intent_path),
            "method_sha256": sha(__file__), "raw_SVG_hashes": before,
            "limits": ["Official native command/output and exact SVG inputs need separate frozen process evidence.",
                       "Semantic desc plus actual nonhidden path identity is finite Native-SVG checking, not OCR or glyph-outline decoding.",
                       "All3 complete rendered pages still require actual human/agent viewing; no child input reannotation or loss of root context.",
                       "No aesthetic, ERC, physical connectivity or manufacturing conclusion from this identity check."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ["spec", "intent", "project", "svgs", "out"]:
        parser.add_argument("--" + key, required=True)
    args = parser.parse_args()
    try:
        result, code = check(args.spec, args.intent, args.project, args.svgs), 0
    except Incomplete as error:
        result, code = {"status": "incomplete", "reason": str(error)}, 3
    except (ValueError, OSError, ET.ParseError, KeyError, TypeError) as error:
        result, code = {"status": "failed", "reason": str(error)}, 2
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "result": str(path.resolve()), "sha256": sha(path)}))
    return code


if __name__ == "__main__":
    sys.exit(main())
