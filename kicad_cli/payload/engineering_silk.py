"""Private native text-bounds calculation. No engineering file is saved here."""

import json
import sys
from pathlib import Path

import pcbnew
import silkscreen as geometry
import targeted_silk


def main():
    args = json.loads(Path(sys.argv[1]).read_bytes())
    path = Path(args["board"]).resolve(strict=True)
    if "engineering-trials" not in path.parts or "candidate" not in path.parts:
        raise ValueError("Only an operation-owned trial is allowed")
    board = pcbnew.LoadBoard(str(path))
    manager = pcbnew.SETTINGS_MANAGER()
    project_path = str(path.with_suffix(".kicad_pro"))
    if Path(project_path).is_file():
        manager.LoadProject(project_path, False)
        board.SetProject(manager.GetProject(project_path))
    mm = pcbnew.ToMM
    clearance = float(args["clearance_mm"])
    if not 0 <= clearance <= 10:
        raise ValueError("Silk test clearance must be within 0 to 10 mm")
    if "targets" in args:
        print(json.dumps(targeted_silk.measure_targets(board, pcbnew, args["targets"], clearance)))
        return
    refs, _pads, graphics = geometry.collect(board, pcbnew, mm)
    pads = {pcbnew.F_SilkS: [], pcbnew.B_SilkS: []}
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            for mask, silk in ((pcbnew.F_Mask, pcbnew.F_SilkS), (pcbnew.B_Mask, pcbnew.B_SilkS)):
                if not pad.IsOnLayer(mask):
                    continue
                box = geometry.box_of(pad, mm)
                # Actual native expansion, when available. Zero expansion is
                # explicit, never a claim about supplier mask registration.
                expansion = mm(pad.GetSolderMaskExpansion(mask))
                pads[silk].append(
                    tuple(v + (-expansion if i < 2 else expansion) for i, v in enumerate(box))
                )
    edge = board.GetBoardEdgesBoundingBox()
    bounds = tuple(
        mm(v) for v in (edge.GetLeft(), edge.GetTop(), edge.GetRight(), edge.GetBottom())
    )
    moves, stuck = [], []
    for ref in refs:
        openings = pads.get(ref["layer"], [])
        if geometry.inside(ref["box"], bounds) and not geometry.clashes(
            ref, ref["box"], openings, graphics, refs, clearance
        ):
            continue
        position, box = geometry.relocate(ref, openings, graphics, refs, clearance, bounds)
        if position is None:
            stuck.append(ref["name"])
            continue
        text = ref["ref"]
        text.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(position[0]), pcbnew.FromMM(position[1])))
        relative = text.GetFPRelativePosition()
        ref["box"] = geometry.box_of(text, mm)
        moves.append(
            {
                "ref": ref["name"],
                "from_mm": list(ref["home"]),
                "to_mm": list(position),
                "relative_nm": [relative.x, relative.y],
            }
        )
    checks = [
        {
            "ref": r["name"],
            "box_mm": list(r["box"]),
            "inside_edge": geometry.inside(r["box"], bounds),
            "clear": not geometry.clashes(
                r, r["box"], pads.get(r["layer"], []), graphics, refs, clearance
            ),
        }
        for r in refs
    ]
    print(
        json.dumps(
            {
                "references": len(refs),
                "moves": moves,
                "stuck": stuck,
                "clearance_mm": clearance,
                "units": "mm; relative_nm nanometres",
                "native_version": pcbnew.GetBuildVersion(),
                "bounds_mm": bounds,
                "checks": checks,
                "limits": (
                    "Native rectangular text/mask/graphic bounds; conservative overlap, "
                    "board edge bounding rectangle. Complex curved edges and visual "
                    "readability require separate render acceptance."
                ),
            }
        )
    )


if __name__ == "__main__":
    main()
