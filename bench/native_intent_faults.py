"""Owned native PCB-only fault creation; source schematic remains untouched."""

from __future__ import annotations

import argparse
from pathlib import Path

import pcbnew
import wx

wx.Log.SetActiveTarget(wx.LogStderr())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("board", type=Path)
    parser.add_argument("kind")
    args = parser.parse_args()
    board = pcbnew.LoadBoard(str(args.board))
    footprints = {f.GetReference(): f for f in board.GetFootprints()}
    r1 = footprints["R1"]
    pad = next(p for p in r1.Pads() if p.GetNumber() == "2")
    if args.kind == "pcb-wrong-net":
        pad.SetNet(next(p for p in r1.Pads() if p.GetNumber() == "1").GetNet())
    elif args.kind == "pcb-wrong-pad":
        pad.SetNumber("9")
    elif args.kind == "pcb-wrong-footprint":
        r1.SetFPIDAsString("Resistor_SMD:R_0402_1005Metric")
    elif args.kind == "outline-same-bbox":
        drawings = [s for s in board.GetDrawings() if s.GetLayer() == pcbnew.Edge_Cuts]
        assert drawings and all(s.GetShape() in (pcbnew.S_SEGMENT, pcbnew.S_RECT) for s in drawings)
        points = [p for s in drawings for p in (s.GetStart(), s.GetEnd())]
        x1, x2 = min(p.x for p in points), max(p.x for p in points)
        y1, y2 = min(p.y for p in points), max(p.y for p in points)
        width = drawings[0].GetWidth()
        assert all(s.GetWidth() == width for s in drawings)
        a, b, notch = x1 + (x2 - x1) // 3, x1 + 2 * (x2 - x1) // 3, y1 + (y2 - y1) // 10
        vertices = [
            (x1, y1),
            (a, y1),
            (a, notch),
            (b, notch),
            (b, y1),
            (x2, y1),
            (x2, y2),
            (x1, y2),
            (x1, y1),
        ]
        for shape in drawings:
            board.Remove(shape)
        for start, end in zip(vertices, vertices[1:], strict=False):
            shape = pcbnew.PCB_SHAPE(board)
            shape.SetShape(pcbnew.S_SEGMENT)
            shape.SetLayer(pcbnew.Edge_Cuts)
            shape.SetWidth(width)
            shape.SetStart(pcbnew.VECTOR2I(*start))
            shape.SetEnd(pcbnew.VECTOR2I(*end))
            board.Add(shape)
    elif args.kind == "stitch-single-layer-contact":
        via = next(
            v for v in board.GetTracks() if v.GetClass() == "PCB_VIA" and v.GetNetname() == "GND"
        )
        zone = next(z for z in board.Zones() if z.IsOnLayer(pcbnew.B_Cu))
        # Keep the same two zones, nets and thermal parameters. Only remove B
        # fill around the selected via; the project criterion requires F+B contact.
        half = (
            via.GetWidth(pcbnew.B_Cu) // 2
            + zone.GetOwnClearance(pcbnew.B_Cu)
            + zone.GetMinThickness()
        )
        outline = zone.Outline()
        hole = outline.NewHole(0)
        center = via.GetPosition()
        for dx, dy in ((-half, -half), (half, -half), (half, half), (-half, half)):
            outline.Append(center.x + dx, center.y + dy, 0, hole)
        if not pcbnew.ZONE_FILLER(board).Fill(board.Zones()):
            raise ValueError("Native refill did not complete for owned contact negative")
    else:
        zone = next(iter(board.Zones()))
        if args.kind == "thermal-solid":
            zone.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
        elif args.kind == "thermal-layer":
            zone.SetLayer(pcbnew.F_Cu if zone.IsOnLayer(pcbnew.B_Cu) else pcbnew.B_Cu)
        elif args.kind == "thermal-net":
            zone.SetNet(next(p for p in r1.Pads() if p.GetNumber() == "1").GetNet())
        elif args.kind == "thermal-gap":
            zone.SetThermalReliefGap(400000)
        elif args.kind == "thermal-spoke":
            zone.SetThermalReliefSpokeWidth(400000)
        else:
            raise ValueError("Unknown declared fault")
    pcbnew.SaveBoard(str(args.board), board)


if __name__ == "__main__":
    main()
