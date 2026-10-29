"""Independent read-only native geometry plus complete bounded ECO comparison."""

import json
import sys

from functional_support import NATIVE, REPO, Case, put, sha

sys.path.insert(0, str(REPO))
from kicad_cli.engineering_readback import canonical
from kicad_cli.fileformat.sexpr import Document
from kicad_cli.module_project import compare_native
from kicad_cli.move_readback import _parse

NATIVE_READER = r"""
import json,sys,pcbnew
from pathlib import Path
board=pcbnew.LoadBoard(sys.argv[1]); rows=[]
for f in board.GetFootprints():
    pads=[{'number':p.GetNumber(),'net':p.GetNetname(),'uuid':p.m_Uuid.AsString(),
           'position_nm':[p.GetPosition().x,p.GetPosition().y],
           'size_nm':[p.GetSize().x,p.GetSize().y]} for p in f.Pads()]
    geometry=[]
    for g in f.GraphicalItems():
        if g.GetLayer() in [pcbnew.F_CrtYd,pcbnew.F_Fab] and hasattr(g,'GetStart'):
            geometry.append({'layer':'F.CrtYd' if g.GetLayer()==pcbnew.F_CrtYd else 'F.Fab',
                             'display_layer':board.GetLayerName(g.GetLayer()),
                             'start_nm':[g.GetStart().x,g.GetStart().y],
                             'end_nm':[g.GetEnd().x,g.GetEnd().y], 'width_nm':g.GetWidth()})
    rows.append({'ref':f.GetReference(),'value':f.GetValue(),
                 'position_nm':[f.GetPosition().x,f.GetPosition().y],
                 'pads':pads,'geometry':geometry})
print(json.dumps({'version':pcbnew.GetBuildVersion(),'footprints':rows,
                  'tracks':len(list(board.GetTracks())), 'zones':len(list(board.Zones()))}))
"""


def without_r1_fields(path):
    doc = Document.load(path)
    for node in doc.root.lists():
        if node.head not in {"symbol", "footprint"}:
            continue
        fields = {p.value(1): p for p in node.find_all("property")}
        if "Reference" in fields and fields["Reference"].value(2) == "R1":
            for name in ("Value", "MPN"):
                if name in fields:
                    node.remove(fields[name])
    return canonical(_parse(doc.dumps(), expected_head=doc.root.head), "")


def main(
    directory, binary=None, baseline_folder="before-eco", intent_file="INTENT-BEFORE-DESIGN.json"
):
    t = Case.attach(directory, binary)
    project = t.root / "project"
    board = project / "schmitt.kicad_pcb"
    intent = json.loads((t.root / intent_file).read_bytes())
    before_hashes = {
        p.relative_to(project).as_posix(): sha(p) for p in project.rglob("*") if p.is_file()
    }
    child = t.raw(
        [NATIVE / "bin/python.exe", "-B", "-c", NATIVE_READER, board],
        "native-geometry",
        native=True,
    )
    assert child.returncode == 0, child.stderr.decode(errors="replace")
    native = json.loads(child.stdout)
    parts = {p["ref"]: p for p in native["footprints"]}
    assert set(parts) == set(intent["parts"])
    actual = {}
    for ref, part in parts.items():
        assert part["position_nm"] == [round(v * 1e6) for v in intent["layout_mm"][ref]]
        for pad in part["pads"]:
            actual.setdefault(pad["net"], []).append(f"{ref}.{pad['number']}")
    for net, nodes in intent["nets"].items():
        assert sorted(actual[net]) == sorted(nodes), (net, actual)
    assert [v for k, v in actual.items() if k not in intent["nets"]] == [["U1.1"]]
    assert sum(len(p["pads"]) for p in parts.values()) == 13
    margins = {}
    for ref in ["U1", "TP1", "TP2", "TP3", "TP4"]:
        p = parts[ref]
        fab = next(g for g in p["geometry"] if g["layer"] == "F.Fab")
        court = next(g for g in p["geometry"] if g["layer"] == "F.CrtYd")
        xs, ys = zip(fab["start_nm"], fab["end_nm"], strict=True)
        boxes = [[min(xs), min(ys), max(xs), max(ys)]]
        for pad in p["pads"]:
            x, y = pad["position_nm"]
            w, h = pad["size_nm"]
            boxes.append([x - w // 2, y - h // 2, x + w // 2, y + h // 2])
        cx, cy = zip(court["start_nm"], court["end_nm"], strict=True)
        half = court["width_nm"] // 2
        gaps = [
            min(b[0] for b in boxes) - min(cx) - half,
            min(b[1] for b in boxes) - min(cy) - half,
            max(cx) - half - max(b[2] for b in boxes),
            max(cy) - half - max(b[3] for b in boxes),
        ]
        assert min(gaps) >= 250000, (ref, gaps)
        margins[ref] = gaps
    assert parts["R1"]["value"] == intent["eco"]["new_value"]
    for suffix in [".kicad_sch", ".kicad_pcb"]:
        assert without_r1_fields(
            t.root / baseline_folder / ("schmitt" + suffix)
        ) == without_r1_fields(project / ("schmitt" + suffix))
    for path in (t.root / baseline_folder).rglob("*"):
        if path.is_file() and path.suffix not in {".kicad_sch", ".kicad_pcb"}:
            assert sha(path) == sha(project / path.relative_to(t.root / baseline_folder))
    report = json.loads((t.root / "FUNCTIONAL-RESULT.json").read_bytes())
    comparison = compare_native(
        {
            "report": report["native_before_routing"],
            "project_exclusions": [],
            "scope": "native DRC",
        },
        {"report": report["native_after"], "project_exclusions": [], "scope": "native DRC"},
    )
    assert comparison["status"] == "passed"
    assert all(sha(project / path) == value for path, value in before_hashes.items())
    put(
        t.root / "INDEPENDENT-NATIVE-COMPARISON.json",
        {
            "native": native,
            "predeclared_intent": intent,
            "courtyard_inner_margins_nm": margins,
            "eco_only_R1_Value_and_MPN": True,
            "other_project_files_unchanged": True,
            "read_only_native_source_hashes_unchanged": True,
            "native_finding_comparison": comparison,
            "not_checked": ["power-on", "simulation", "SI/EMI", "manufacturing approval"],
        },
    )
    print("NATIVE INTENT AND ECO CHECKED", t.root)


if __name__ == "__main__":
    main(*sys.argv[1:])
