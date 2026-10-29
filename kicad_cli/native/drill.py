"""Excellon drill files and their report, written in this process.

A port of the drill-file half of `payload/drill.py`, which had pcbnew's
EXCELLON_WRITER write them. Everything here was measured on what that writer
wrote for KiCad's demo boards, with the settings the payload chose: metric
or inch, decimal numbers, plated and unplated holes in two files or merged,
the board's origin or its auxiliary one, oval holes as routed slots.

- A hole's tool is its drill's smaller size, to the nanometre: a pad drilled
  0.500024 mm gets a tool of its own beside one drilled 0.5 mm, though both
  print as 0.500.
- Tools are numbered by size; a via's hole and a pad's of one size are two
  tools (ViaDrill and ComponentDrill). In a merged file the unplated tools
  follow the plated ones.
- Under each tool its holes go by position, x then y; slots after the round
  holes, each routed from its first end to its second (G00, M15, G01, M16,
  G05), the first end being the one the drill's longer side points back to.
- Numbers are millimetres to three places or inches to four, trailing zeros
  dropped but one kept after the point; y is turned up, as Excellon has it.
- The files' lines end as the platform's text files do; the report's in a
  newline only.
"""

from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass, field

from .. import __version__
from ..fileformat import geometry
from ..fileformat.board import Board

NM = 1_000_000
INCH = 25_400_000  # nm


@dataclass
class Hole:
    x: int
    y: int
    diameter: int  # nm: the smaller of the drill's two sizes
    plated: bool
    via: bool
    slot: tuple[tuple[float, float], tuple[float, float]] | None = None


@dataclass
class Tool:
    diameter: int
    plated: bool
    via: bool
    holes: list[Hole] = field(default_factory=list)

    @property
    def round(self) -> list[Hole]:
        return [h for h in self.holes if h.slot is None]

    @property
    def slots(self) -> list[Hole]:
        return [h for h in self.holes if h.slot is not None]

    @property
    def function(self) -> str:
        if self.plated:
            return "Plated,PTH,ViaDrill" if self.via else "Plated,PTH,ComponentDrill"
        return "NonPlated,NPTH,ComponentDrill"


def holes(board: Board) -> list[Hole]:
    """Every hole of the board: its vias' and its through-hole pads'."""
    out = []
    for via in board.vias:
        out.append(Hole(*via.position, via.drill, True, True))
    for fp in board.footprints:
        for pad in fp.pads:
            if pad.kind not in ("thru_hole", "np_thru_hole") or not pad.drill:
                continue
            dx, dy = pad.drill
            if min(dx, dy) <= 0:
                continue
            hole = Hole(*pad.position, min(dx, dy), pad.kind == "thru_hole", False)
            if dx != dy:
                half = abs(dx - dy) / 2
                delta = (half, 0) if dx > dy else (0, half)
                ex, ey = geometry.rotate(*delta, pad.angle)
                px, py = pad.position
                hole.slot = ((px - ex, py - ey), (px + ex, py + ey))
            out.append(hole)
    return out


def tools(found: list[Hole]) -> list[Tool]:
    """The holes grouped into tools, numbered as pcbnew numbered them."""
    by: dict[tuple, Tool] = {}
    for hole in found:
        key = (not hole.plated, hole.diameter, not hole.via)
        if key not in by:
            by[key] = Tool(hole.diameter, hole.plated, hole.via)
        by[key].holes.append(hole)
    ordered = [by[k] for k in sorted(by)]
    for tool in ordered:
        tool.holes.sort(key=lambda h: (h.x, h.y))
    return ordered


# Nanometres to the file's unit by multiplying, as pcbnew converted them: a
# hole at 63.1775 mm, 63177500 nm times 1e-6, is 63.17749999... and is
# written 63.177.
TO_MM = 1.0 / 1e6
TO_INCH = 1.0 / 25400 / 1000


def _number(value_nm: float, inch: bool) -> str:
    text = f"{value_nm * TO_INCH:.4f}" if inch else f"{value_nm * TO_MM:.3f}"
    text = text.rstrip("0")
    return text + "0" if text.endswith(".") else text


def _point(x: float, y: float, origin: tuple[int, int], inch: bool) -> str:
    return f"X{_number(x - origin[0], inch)}Y{_number(-(y - origin[1]), inch)}"


def excellon(board: Board, chosen: list[Tool], function: str, inch: bool,
             origin: tuple[int, int], when: _dt.datetime) -> str:  # fmt: skip
    """One drill file: its header, its tools, then each tool's holes."""
    stamp = when.strftime("%Y-%m-%dT%H:%M:%S")
    zone = when.strftime("%z")
    zone = f"{zone[:3]}:{zone[3:]}" if zone else "+00:00"
    lines = [
        "M48",
        f"; DRILL file kicad-cli {__version__} date {stamp}",
        "; FORMAT={-:-/ absolute / %s / decimal}" % ("inch" if inch else "metric"),  # noqa: UP031
        f"; #@! TF.CreationDate,{stamp}{zone}",
        f"; #@! TF.GenerationSoftware,fateforge,kicad-cli,{__version__}",
        f"; #@! TF.FileFunction,{function}",
        "FMAT,2",
        "INCH" if inch else "METRIC",
    ]
    for n, tool in enumerate(chosen, 1):
        size = f"{tool.diameter * TO_INCH:.4f}" if inch else f"{tool.diameter * TO_MM:.3f}"
        lines += [f"; #@! TA.AperFunction,{tool.function}", f"T{n}C{size}"]
    lines += ["%", "G90", "G05"]
    # The round holes of every tool, then the slots of every tool.
    for n, tool in enumerate(chosen, 1):
        if tool.round:
            lines.append(f"T{n}")
            lines += [_point(h.x, h.y, origin, inch) for h in tool.round]
    for n, tool in enumerate(chosen, 1):
        if not tool.slots:
            continue
        lines.append(f"T{n}")
        for h in tool.slots:
            start, end = h.slot
            lines += [
                "G00" + _point(*start, origin, inch),
                "M15",
                "G01" + _point(*end, origin, inch),
                "M16",
                "G05",
            ]
    lines.append("M30")
    return os.linesep.join(lines) + os.linesep


def report(board: Board, name: str, files: list[tuple[str, str, list[Tool]]],
           merged: bool, when: _dt.datetime) -> str:  # fmt: skip
    """The drill report: the copper stack, then each file's tools and counts."""
    out = [f"Drill report for {name}", f"Created on {when.strftime('%Y-%m-%dT%H:%M:%S')}", "",
           "Copper Layer Stackup:", "    " + "=" * 61]  # fmt: skip
    copper = board.copper_layers
    for n, layer in enumerate(copper, 1):
        kind = "front" if n == 1 else ("back" if n == len(copper) else f"in{n - 1}")
        out.append(f"    L{n} :  {board.layer_name(layer):<25} {kind}")
    out += ["", ""]
    numbered = 0
    for title, label, chosen in files:
        out += [title, f"    {label} through holes:", "    " + "=" * 61]
        for tool in chosen:
            numbered += 1
            count, slots = len(tool.holes), len(tool.slots)
            text = "(1 hole)" if count == 1 else f"({count} holes)"
            if slots:
                text += f"  (with {slots} slot{'s' if slots != 1 else ''})"
            elif count != 1:
                text += ")"  # sic: pcbnew's report closes a plural twice
            out.append(
                f'    T{numbered}  {tool.diameter * TO_MM:.3f}mm  {tool.diameter * TO_INCH:.4f}"  '
                + text
            )
        total = sum(len(t.holes) for t in chosen)
        out += ["", f"    Total {label} holes count {total}", "", ""]
        if not merged:
            numbered = 0
    return "\n".join(out[:-2]) + "\n"


def write(board: Board, outdir: str, inch: bool = False, merge: bool = False,
          aux_origin: bool = False, when: _dt.datetime | None = None) -> list[str]:  # fmt: skip
    """Write the drill files and the report; the paths written."""
    when = when or _dt.datetime.now().astimezone()
    stem = board.path.stem
    origin = (0, 0)
    if aux_origin:
        setup = board.root.find("setup")
        found = setup.find("aux_axis_origin") if setup is not None else None
        if found is not None:
            origin = (round(float(found.atom(1)) * NM), round(float(found.atom(2)) * NM))
    every = tools(holes(board))
    plated = [t for t in every if t.plated]
    unplated = [t for t in every if not t.plated]
    n = len(board.copper_layers)
    written = []
    os.makedirs(outdir, exist_ok=True)
    if merge:
        files = [(f"{stem}.drl", f"MixedPlating,1,{n}", plated + unplated)]
        sections = [
            (f"Drill file '{stem}.drl' contains", "plated", plated),
            ("Not plated through holes are merged with plated holes", "unplated", unplated),
        ]
    else:
        files = [
            (f"{stem}-PTH.drl", f"Plated,1,{n},PTH", plated),
            (f"{stem}-NPTH.drl", f"NonPlated,1,{n},NPTH", unplated),
        ]
        sections = [
            (f"Drill file '{stem}-PTH.drl' contains", "plated", plated),
            (f"Drill file '{stem}-NPTH.drl' contains", "unplated", unplated),
        ]
    for name, function, chosen in files:
        path = os.path.join(outdir, name)
        with open(path, "wb") as fh:
            fh.write(excellon(board, chosen, function, inch, origin, when).encode("ascii"))
        written.append(path)
    rpt = os.path.join(outdir, f"{stem}-drl.rpt")
    with open(rpt, "wb") as fh:
        fh.write(report(board, board.path.name, sections, merge, when).encode("utf-8"))
    written.append(rpt)
    return written
