"""Replay the retained original clipping input with one-factor native controls."""

import argparse
import json
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

from change_support import Run, arguments, sha, snapshot, write

ORIGINAL = "813c33fec3fe09a915c1b83dfb122e08554b99ba49f28679b31ed7d8fd18aaeb"


def coordinates(file):
    xml = ET.parse(file).getroot()
    assert not [
        node
        for node in xml.iter()
        if node.attrib.get("transform", "translate(0 0) scale(1 1)") != "translate(0 0) scale(1 1)"
    ], "Non-identity transforms need a different consumer"
    # This counterexample uses explicit paired KiCad SVG path endpoints.
    points = [
        tuple(map(float, pair))
        for node in xml.iter("{http://www.w3.org/2000/svg}path")
        for pair in re.findall(r"[ML]\s*([+-]?[\d.]+)[, ]+([+-]?[\d.]+)", node.attrib["d"])
    ]
    assert points
    page = list(map(float, xml.attrib["viewBox"].split()))
    box = [
        min(p[0] for p in points),
        min(p[1] for p in points),
        max(p[0] for p in points),
        max(p[1] for p in points),
    ]
    outside = [
        p
        for p in points
        if not (page[0] <= p[0] <= page[0] + page[2] and page[1] <= p[1] <= page[1] + page[3])
    ]
    return {
        "page": page,
        "path_endpoint_bounds_mm": box,
        "outside_endpoints": len(outside),
        "observed_endpoint_count": len(points),
        "points": sorted(set(points)),
        "sha256": sha(file),
        "limits": (
            "Explicit M/L endpoints, identity transforms; render corroborates clipping. "
            "Not all path/arc/font geometry"
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    arguments(parser)
    parser.add_argument("--baseline", type=Path, required=True)
    options = parser.parse_args()
    source = options.baseline / "atmega328p.kicad_pcb"
    if not source.is_file() or sha(source) != ORIGINAL:
        parser.error("Original clipping snapshot is missing or changed; no substitute scene")
    trial = Run(options)
    results = []
    for mode in ("original", "no-aux", "svg-fit"):
        case = trial.root / mode
        case.mkdir()
        for name in ("atmega328p.kicad_pcb", "atmega328p.kicad_pro", "atmega328p.kicad_sch"):
            shutil.copyfile(options.baseline / name, case / name)
        board = case / source.name
        control = trial.call(
            mode + "-native-control",
            [
                "-I",
                "-B",
                Path(__file__).with_name("native_plot_control.py"),
                board,
                case / "direct-sdk",
                mode,
            ],
            program=[str(trial.sdk)],
        )
        before = snapshot(case)
        trial.mutate(
            mode + "-public-svg",
            [
                "board",
                "plot",
                "--board",
                str(board),
                "--file-format",
                "svg",
                "--layers",
                "F.Cu,B.Cu,Edge.Cuts",
                "--out",
                str(case / "public-svg"),
            ],
        )
        assert all(sha(case / name) == digest for name, digest in before.items())
        trial.mutate(
            mode + "-public-pdf",
            [
                "board",
                "plot",
                "--board",
                str(board),
                "--file-format",
                "pdf",
                "--layers",
                "F.Cu,B.Cu,Edge.Cuts",
                "--out",
                str(case / "public-pdf"),
            ],
        )
        layer_results = []
        for layer in ("F_Cu", "B_Cu", "Edge_Cuts"):
            public = case / "public-svg" / ("atmega328p-" + layer + ".svg")
            direct = case / "direct-sdk" / public.name
            public_read, direct_read = coordinates(public), coordinates(direct)
            assert public_read["points"] == direct_read["points"], (
                "Public versus direct native geometry differs"
            )
            import fitz

            picture = fitz.open(public)
            pdf = fitz.open("pdf", picture.convert_to_pdf())
            pdf[0].get_pixmap(matrix=fitz.Matrix(2, 2)).save(case / (layer + "-page.png"))
            bounds = public_read["path_endpoint_bounds_mm"]
            # Context rendering deliberately expands viewport. It is an inspection
            # artifact, not a corrected deliverable or modified input profile.
            expanded = ET.parse(public)
            expanded.getroot().set(
                "viewBox",
                f"{bounds[0] - 2} {bounds[1] - 2} "
                f"{bounds[2] - bounds[0] + 4} {bounds[3] - bounds[1] + 4}",
            )
            expanded.getroot().set("width", str(bounds[2] - bounds[0] + 4) + "mm")
            expanded.getroot().set("height", str(bounds[3] - bounds[1] + 4) + "mm")
            inspection = case / (layer + "-inspection.svg")
            expanded.write(inspection)
            pic = fitz.open(inspection)
            doc = fitz.open("pdf", pic.convert_to_pdf())
            doc[0].get_pixmap(matrix=fitz.Matrix(4, 4)).save(case / (layer + "-inspection.png"))
            public_read.pop("points")
            direct_read.pop("points")
            pdf_results = []
            for folder in ("public-pdf", "direct-sdk"):
                file = case / folder / ("atmega328p-" + layer + ".pdf")
                pdf = fitz.open(file)
                drawings = pdf[0].get_drawings()
                # A horizontal/vertical line has a zero-area Rect. Rect union
                # treats it as empty, so extrema must include every coordinate.
                rects = [d["rect"] for d in drawings]
                box = fitz.Rect(
                    min(r.x0 for r in rects),
                    min(r.y0 for r in rects),
                    max(r.x1 for r in rects),
                    max(r.y1 for r in rects),
                )
                pdf_results.append(
                    {
                        "source": folder,
                        "page_pt": list(pdf[0].rect),
                        "drawing_bounds_pt": list(box),
                        "outside_page": not pdf[0].rect.contains(box),
                        "sha256": sha(file),
                    }
                )
                if folder == "public-pdf":
                    pdf[0].get_pixmap(matrix=fitz.Matrix(2, 2)).save(
                        case / (layer + "-pdf-page.png")
                    )
            assert pdf_results[0]["drawing_bounds_pt"] == pdf_results[1]["drawing_bounds_pt"]
            layer_results.append(
                {
                    "layer": layer,
                    "public": public_read,
                    "direct_native": direct_read,
                    "endpoints_equal": True,
                    "pdf_native_control": pdf_results,
                }
            )
        results.append({"mode": mode, "profile": control, "layers": layer_results})
        write(trial.root / "progress.json", results)
    assert all(r["public"]["outside_endpoints"] > 0 for r in results[0]["layers"][:2])
    assert all(r["public"]["outside_endpoints"] == 0 for r in results[1]["layers"])
    assert results[0]["profile"]["saved"] == results[2]["profile"]["saved"]
    origin = [v / 1e6 for v in results[0]["profile"]["aux_origin_nm"]]
    for first, second in zip(results[0]["layers"], results[1]["layers"], strict=True):
        a, b = (
            first["public"]["path_endpoint_bounds_mm"],
            second["public"]["path_endpoint_bounds_mm"],
        )
        assert all(abs(b[i] - a[i] - origin[i % 2]) < 0.0002 for i in range(4))
    assert sha(source) == ORIGINAL
    trial.finish_binding()
    write(
        trial.root / "result.json",
        {
            "status": "root_cause_reproduced_support_boundary",
            "original_sha256": ORIGINAL,
            "expected": (
                "Aux-origin subtraction can place artwork outside the fixed page; "
                "disabling only that saved option translates it back"
            ),
            "observed": results,
            "source": trial.binding,
            "conclusion": (
                "Public output follows native saved plot semantics. No silent origin/page "
                "override is authorized. Original profile remains clipped; explicit alternate "
                "profile has different output intent. SVG fit setter was not persisted by "
                "this installed native save/load."
            ),
            "not_claimed": [
                "repair of the original profile",
                "full drawing or manufacturing geometry correctness",
                "native SVG persistence root cause",
            ],
        },
    )
    print(
        json.dumps(
            {"status": "root_cause_reproduced_support_boundary", "evidence": str(trial.root)}
        )
    )


if __name__ == "__main__":
    main()
