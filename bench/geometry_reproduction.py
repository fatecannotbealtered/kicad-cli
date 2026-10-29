"""Public before/after repro. Direct CAD creation is limited to labeled input fixtures."""

from __future__ import annotations

import copy
import json
import sys
import uuid

from functional_support import NATIVE, REPO, Case, put, sha

sys.path.insert(0, str(REPO))
from kicad_cli.fileformat.new_board import EMPTY
from kicad_cli.fileformat.new_project import project
from kicad_cli.fileformat.sexpr import Document
from kicad_cli.fileformat.sexpr import copy as clone

GEOMETRIES = {
    "BodyLarge": ([10, 8], [(-0.5, 0, 0.6, 0.6), (0.5, 0, 0.6, 0.6)], 0.25),
    "PadsLarge": ([2, 2], [(-4, -2, 1, 1), (4, 2, 1, 1)], 0.25),
    "Asymmetric": ([2, 2], [(3, 2, 2, 1), (0, -1, 0.6, 0.6)], 0.3),
    "Critical": ([2, 2], [(-0.5, 0, 1, 1), (0.5, 0, 1, 1)], 0.000001),
    "OddNm": (
        [0.000003, 0.000003],
        [(-0.000001, 0, 0.000003, 0.000001), (0.000001, 0, 0.000003, 0.000001)],
        0.000001,
    ),
}

INSPECT = r"""
import json,pcbnew,sys
fp=pcbnew.FootprintLoad(sys.argv[1],sys.argv[2])
assert fp
shapes=[]
for x in fp.GraphicalItems():
 if x.GetLayer() in (pcbnew.F_CrtYd,pcbnew.F_Fab):
  shapes.append({'layer':'courtyard' if x.GetLayer()==pcbnew.F_CrtYd else 'body',
                 'start_nm':[x.GetStart().x,x.GetStart().y],
                 'end_nm':[x.GetEnd().x,x.GetEnd().y],'width_nm':x.GetWidth()})
print(json.dumps({'shapes':shapes,'pads':[{'number':p.GetNumber(),'at_nm':[p.GetPosition().x,p.GetPosition().y],
 'size_nm':[p.GetSize().x,p.GetSize().y]} for p in fp.Pads()]}))
"""


def main(directory, binary=None):
    t = Case(directory, binary)
    work = t.root / "project"
    work.mkdir()
    blank = t.root / "geometry.json"
    put(blank, {"mode": "blank", "title": "Geometry input repro"})
    t.write("sch", "create", "--spec", blank, "--out", work)
    evidence = t.root / "source.json"
    put(evidence, {"source": "Explicit synthetic regression geometry; not a circuit"})
    results = {}
    for name, (body, pads, margin) in GEOMETRIES.items():
        spec = copy.deepcopy(
            json.loads((REPO / "examples/library/sn74lvc1g14/library.json").read_bytes())
        )
        spec.update(
            library=name,
            sources=[
                {
                    "url": "https://www.kicad.org/",
                    "revision": "synthetic-geometry-repro-1",
                    "locator": "declared fixture",
                    "file": "source.json",
                    "sha256": sha(evidence),
                }
            ],
            mapping={str(i): str(i) for i in (1, 2)},
        )
        spec["symbol"].update(name=name, pins=spec["symbol"]["pins"][:2])
        spec["footprint"].update(
            name=name,
            body_mm=body,
            courtyard_mm=margin,
            pads=[
                {"number": str(i), "at_mm": [x, y], "size_mm": [w, h]}
                for i, (x, y, w, h) in enumerate(pads, 1)
            ],
        )
        (work / (name + ".pretty")).mkdir()
        files = [
            name + ".kicad_sym",
            name + ".pretty/" + name + ".kicad_mod",
            "sym-lib-table",
            "fp-lib-table",
            name + ".provenance.json",
        ]
        spec["baseline"] = {n: sha(work / n) if (work / n).exists() else "absent" for n in files}
        request = t.root / (name + ".json")
        put(request, spec)
        t.write("library", "build", "--project", work / "geometry.kicad_pro", "--spec", request)
        process = t.raw(
            [NATIVE / "bin/python.exe", "-B", "-c", INSPECT, work / (name + ".pretty"), name],
            "native-geometry",
            True,
        )
        assert process.returncode == 0, process.stderr
        measured = json.loads(process.stdout)
        court = next(s for s in measured["shapes"] if s["layer"] == "courtyard")
        x0, y0 = court["start_nm"]
        x1, y1 = court["end_nm"]
        bounds = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
        fab = next(s for s in measured["shapes"] if s["layer"] == "body")
        boxes = [
            [
                min(fab["start_nm"][0], fab["end_nm"][0]),
                min(fab["start_nm"][1], fab["end_nm"][1]),
                max(fab["start_nm"][0], fab["end_nm"][0]),
                max(fab["start_nm"][1], fab["end_nm"][1]),
            ]
        ]
        for p in measured["pads"]:
            x, y = p["at_nm"]
            w, h = p["size_nm"]
            boxes.append([x - w / 2, y - h / 2, x + w / 2, y + h / 2])
        expected = [
            min(b[0] for b in boxes) / 1e6,
            min(b[1] for b in boxes) / 1e6,
            max(b[2] for b in boxes) / 1e6,
            max(b[3] for b in boxes) / 1e6,
        ]
        clear = [
            expected[0] * 1e6 - bounds[0] - court["width_nm"] / 2,
            expected[1] * 1e6 - bounds[1] - court["width_nm"] / 2,
            bounds[2] - court["width_nm"] / 2 - expected[2] * 1e6,
            bounds[3] - court["width_nm"] / 2 - expected[3] * 1e6,
        ]
        results[name] = {
            "native": measured,
            "courtyard_bounds_nm": bounds,
            "body_and_pad_union_mm": expected,
            "inner_clearance_nm": clear,
            "required_nm": margin * 1e6,
            "satisfies_body_and_pad_margin": min(clear) >= margin * 1e6 - 0.001,
        }
    invalid = copy.deepcopy(spec)
    invalid["footprint"]["pads"][0]["at_mm"][0] = 0.0000005
    invalid_path = t.root / "sub-nm.json"
    put(invalid_path, invalid)
    before = {p.relative_to(work).as_posix(): sha(p) for p in work.rglob("*") if p.is_file()}
    # Refresh the target asset baselines; only the unsupported precision is invalid.
    invalid["baseline"] = {n: sha(work / n) if (work / n).exists() else "absent" for n in files}
    put(invalid_path, invalid)
    rejection = t.cli(
        "library",
        "build",
        "--project",
        work / "geometry.kicad_pro",
        "--spec",
        invalid_path,
        "--dry-run",
        require=False,
    )
    assert rejection.get("error", {}).get("code") == "E_VALIDATION", "Sub-nm must refuse"
    assert before == {
        p.relative_to(work).as_posix(): sha(p) for p in work.rglob("*") if p.is_file()
    }
    # Data-only minimal input fixture: one retained straight path and one bowing arc.
    board = work / "arc-case.kicad_pcb"
    document = Document.parse(EMPTY)

    def uid(name):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, "functional-repro/" + name))

    for text in [
        "(gr_rect (start 2 2) (end 28 18) (stroke (width 0.05) (type default)) "
        '(fill none) (layer "Edge.Cuts") (uuid "' + uid("edge") + '"))',
        '(segment (start 10 10) (end 20 10) (width 0.25) (layer "F.Cu") (net "SIG") (uuid "'
        + uid("line")
        + '"))',
        '(arc (start 10 10) (mid 15 5) (end 20 10) (width 0.25) (layer "F.Cu") (net "SIG") (uuid "'
        + uid("arc")
        + '"))',
    ]:
        document.root.append(clone(Document.parse(text).root))
    board.write_text(document.dumps(), encoding="utf-8")
    board.with_suffix(".kicad_pro").write_text(project(board.stem), encoding="utf-8")
    original = sha(board)
    context = t.cli("project", "context", "--board", board)["observation"]["context"]
    spec = {
        "baseline": context,
        "net": "SIG",
        "remove_ids": [uid("arc")],
        "allow_locked_ids": [],
        "allowed_layers": ["F.Cu"],
        "bounds_nm": [9000000, 9000000, 21000000, 11000000],
        "endpoints": [
            {"object_uuid": uid("line"), "terminal": term, "at_nm": point, "layer": "F.Cu"}
            for term, point in [("start", [10000000, 10000000]), ("end", [20000000, 10000000])]
        ],
        "segments": [],
        "minimum_improvement_mm": 0,
    }
    request = t.root / "arc-delete.json"
    put(request, spec)
    preview = t.cli(
        "board", "route", "patch", "--board", board, "--spec", request, "--dry-run", require=False
    )
    arc_result = {
        "preview": preview,
        "source_unchanged_after_preview": sha(board) == original,
        "arc_mid_outside_bounds": True,
    }
    if "error" not in preview and preview["preview"]["can_apply"]:
        result = t.cli(
            "board",
            "route",
            "patch",
            "--board",
            board,
            "--spec",
            request,
            "--confirm",
            preview["confirm_token"],
        )
        arc_result.update(
            saved=True,
            receipt=result["receipt"]["outcome"]["persistence"],
            source_changed=sha(board) != original,
        )
    else:
        arc_result.update(saved=False, source_changed=sha(board) != original)
    put(
        t.root / "RESULT.json",
        {
            "courtyard": results,
            "arc": arc_result,
            "sub_nm_rejection": rejection,
            "sub_nm_formal_assets_unchanged": True,
            "input_fixture_only": "arc-case CAD authored only as a minimal regression input",
        },
    )
    print(
        json.dumps(
            {
                "courtyard": {k: v["satisfies_body_and_pad_margin"] for k, v in results.items()},
                "arc_saved": arc_result["saved"],
            }
        )
    )


if __name__ == "__main__":
    main(*sys.argv[1:])
