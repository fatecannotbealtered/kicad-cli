"""``board place``, in this process: rearrange parts so that connected ones
sit together, measured in wirelength.

A port of `payload/pcb_autoplace.py`, which ran on pcbnew. The placement is
the payload's to the micrometre (`tests/test_native_place.py`); its DRC
before and after is this tool's own (`fileformat/drc/`), where the payload
asked KiCad's -- the same findings but for those this tool does not check
yet, the silkscreen's among them, which are counted in neither.

`board from-netlist` lays parts out in a grid ordered by reference -- a
place for each, not a layout: it does not know which parts belong together,
so a decoupling capacitor can land across the board from its chip. This is
force-directed: the pads of one net pull their parts together, overlapping
courtyards push apart, again and again. It is not a commercial placer -- no
rotation, no flipping, no regions -- but it knows what is connected, which
the grid does not.

It is judged by HPWL, the sum over nets of half the perimeter of the box
round their pads; if that did not improve, nothing is moved and it says so.
"""

from __future__ import annotations

import os

from .. import envelope, kicad_env
from ..fileformat import drc
from ..fileformat.board import Board, Footprint
from ..fileformat.drc.settings import Settings
from . import load_board, write
from .move import courtyard_box, move_footprint

NM = 1_000_000
# Nets fanning out to more parts than this pull nothing: power and ground
# reach nearly every part, and counting them pulls everything to one point.
FANOUT_CAP = 8
DEFAULT_ITERATIONS = 200
STEP_START = 0.45
STEP_END = 0.03
# Pushes a round of untangling may make; past that the board is too small.
LEGALIZE_PASSES = 24


def mm(value: float) -> float:
    return value / NM


def from_mm(value: float) -> int:
    """Millimetres to nanometres as the payload converted them, truncated
    (see `move.from_mm`)."""
    return int(float(value) * float(NM))


def locked(fp: Footprint) -> bool:
    """`(locked yes)`, or KiCad's older `locked` after the footprint's name."""
    node = fp.node
    found = node.find("locked")
    if found is not None and found.atom(1) in (None, "yes"):
        return True
    return any(item == "locked" for item in node.items[1:3] if isinstance(item, str))


def _footprint_geometry(fp: Footprint) -> dict:
    """The courtyard's offset from the footprint's origin, and its half width
    and height: measured once, then moved with the part."""
    x1, y1, x2, y2 = courtyard_box(fp)
    return {
        "dx": (x1 + x2) / 2.0 - mm(fp.position[0]),
        "dy": (y1 + y2) / 2.0 - mm(fp.position[1]),
        "hw": max((x2 - x1) / 2.0, 0.05),
        "hh": max((y2 - y1) / 2.0, 0.05),
    }


def collect(board: Board, keep: set[str]):
    """Everything placing needs: positions, courtyards, pads, nets. Locked
    parts and those --keep names are fixed: they pull and are avoided, but do
    not move."""
    parts, nets = {}, {}
    for fp in board.footprints:
        ref = fp.reference
        if not ref:
            continue
        parts[ref] = {
            "ref": ref,
            "fp": fp,
            "x": mm(fp.position[0]),
            "y": mm(fp.position[1]),
            "fixed": locked(fp) or ref in keep,
            **_footprint_geometry(fp),
        }
        for pad in fp.pads:
            if not pad.net:
                continue
            nets.setdefault(pad.net, []).append(
                {
                    "ref": ref,
                    "ox": mm(pad.position[0]) - parts[ref]["x"],
                    "oy": mm(pad.position[1]) - parts[ref]["y"],
                }
            )
    # A net on one part only says nothing about where parts go.
    nets = {name: nodes for name, nodes in nets.items() if len({n["ref"] for n in nodes}) > 1}
    return parts, nets


def hpwl(parts, nets) -> float:
    total = 0.0
    for nodes in nets.values():
        xs = [parts[n["ref"]]["x"] + n["ox"] for n in nodes]
        ys = [parts[n["ref"]]["y"] + n["oy"] for n in nodes]
        total += (max(xs) - min(xs)) + (max(ys) - min(ys))
    return total


def _attract(parts, nets, step) -> None:
    force = {ref: [0.0, 0.0] for ref in parts}
    weight = {ref: 0.0 for ref in parts}
    for nodes in nets.values():
        fanout = len({n["ref"] for n in nodes})
        if fanout > FANOUT_CAP:
            continue
        # A two-pin net says "these two side by side"; an eight-pin one only
        # "roughly together": weighted 1 / (n - 1).
        share = 1.0 / (fanout - 1)
        cx = sum(parts[n["ref"]]["x"] + n["ox"] for n in nodes) / len(nodes)
        cy = sum(parts[n["ref"]]["y"] + n["oy"] for n in nodes) / len(nodes)
        for node in nodes:
            part = parts[node["ref"]]
            force[node["ref"]][0] += share * (cx - (part["x"] + node["ox"]))
            force[node["ref"]][1] += share * (cy - (part["y"] + node["oy"]))
            weight[node["ref"]] += share
    for ref, part in parts.items():
        if part["fixed"] or weight[ref] <= 0.0:
            continue
        part["x"] += step * force[ref][0] / weight[ref]
        part["y"] += step * force[ref][1] / weight[ref]


def _legalize(order, parts, clearance, bounds) -> None:
    """Overlapping courtyards pushed apart along the axis they overlap less
    on, and kept within the board."""
    for _ in range(LEGALIZE_PASSES):
        moved = False
        for index, left_ref in enumerate(order):
            left = parts[left_ref]
            for right_ref in order[index + 1 :]:
                right = parts[right_ref]
                if left["fixed"] and right["fixed"]:
                    continue
                dx = (right["x"] + right["dx"]) - (left["x"] + left["dx"])
                dy = (right["y"] + right["dy"]) - (left["y"] + left["dy"])
                gap_x = (left["hw"] + right["hw"] + clearance) - abs(dx)
                gap_y = (left["hh"] + right["hh"] + clearance) - abs(dy)
                if gap_x <= 0 or gap_y <= 0:
                    continue
                moved = True
                if gap_x <= gap_y:
                    push = gap_x if dx >= 0 else -gap_x
                    axis = "x"
                else:
                    push = gap_y if dy >= 0 else -gap_y
                    axis = "y"
                if left["fixed"]:
                    right[axis] += push
                elif right["fixed"]:
                    left[axis] -= push
                else:
                    right[axis] += push / 2.0
                    left[axis] -= push / 2.0
        _clamp(parts, bounds)
        if not moved:
            return


def _clamp(parts, bounds) -> None:
    if bounds is None:
        return
    left, top, right, bottom = bounds
    for part in parts.values():
        if part["fixed"]:
            continue
        low_x, high_x = left + part["hw"] - part["dx"], right - part["hw"] - part["dx"]
        low_y, high_y = top + part["hh"] - part["dy"], bottom - part["hh"] - part["dy"]
        if low_x <= high_x:
            part["x"] = min(max(part["x"], low_x), high_x)
        if low_y <= high_y:
            part["y"] = min(max(part["y"], low_y), high_y)


def severities(path) -> dict[str, int]:
    """DRC counted by severity: this tool's, each excluded finding counted
    as KiCad counts it, an exclusion."""
    report = drc.check(path, kicad_root=kicad_env.find_kicad_root())
    counts: dict[str, int] = {}
    for violation in report.violations:
        severity = "exclusion" if violation.excluded else violation.severity
        counts[severity] = counts.get(severity, 0) + 1
    return counts


def board_bounds(board: Board):
    box = board.edge_bbox()
    if box is None or box[2] == box[0] or box[3] == box[1]:
        return None
    return mm(box[0]), mm(box[1]), mm(box[2]), mm(box[3])


def usable_area(bounds, edge):
    if bounds is None:
        return None
    left, top, right, bottom = bounds
    if right - left <= 2 * edge or bottom - top <= 2 * edge:
        return bounds
    return left + edge, top + edge, right - edge, bottom - edge


def place(parts, nets, iterations, clearance, bounds, edge=0.0) -> None:
    area = usable_area(bounds, edge)
    order = sorted(parts)  # one order, so one board gives one layout
    for step_index in range(iterations):
        ratio = step_index / max(1, iterations - 1)
        _attract(parts, nets, STEP_START + (STEP_END - STEP_START) * ratio)
        _legalize(order, parts, clearance, area)


def inspect(parts, bounds):
    """Which courtyards overlap now, the least gap, which parts stick out."""
    order = sorted(parts)
    clash = []
    min_gap = None
    for index, left_ref in enumerate(order):
        left = parts[left_ref]
        for right_ref in order[index + 1 :]:
            right = parts[right_ref]
            dx = abs((right["x"] + right["dx"]) - (left["x"] + left["dx"]))
            dy = abs((right["y"] + right["dy"]) - (left["y"] + left["dy"]))
            gap = max(dx - (left["hw"] + right["hw"]), dy - (left["hh"] + right["hh"]))
            if gap < 0:
                clash.append([left_ref, right_ref])
            min_gap = gap if min_gap is None else min(min_gap, gap)
    outside = []
    if bounds is not None:
        left_edge, top, right_edge, bottom = bounds
        for ref in order:
            part = parts[ref]
            cx, cy = part["x"] + part["dx"], part["y"] + part["dy"]
            if (
                cx - part["hw"] < left_edge - 0.01
                or cx + part["hw"] > right_edge + 0.01
                or cy - part["hh"] < top - 0.01
                or cy + part["hh"] > bottom + 0.01
            ):
                outside.append(ref)
    return clash, outside, min_gap


def run(path: str, args: dict) -> None:
    write.start()
    iterations = max(1, int(args.get("iterations") or DEFAULT_ITERATIONS))
    clearance = float(args.get("clearance") or 0.5)
    keep_arg = args.get("keep") or []
    refs = keep_arg if isinstance(keep_arg, list) else [keep_arg]
    keep = {r.strip() for item in refs for r in str(item).split(",") if r.strip()}

    board = load_board(path)
    parts, nets = collect(board, keep)
    if not parts:
        envelope.fail("E_VALIDATION", "板上没有封装", {"board": path})
    unknown = sorted(keep - set(parts))
    if unknown:
        envelope.fail("E_NOT_FOUND", "--keep 点名的位号板上没有", {"refs": unknown})
    movable = [p for p in parts.values() if not p["fixed"]]
    if not movable:
        envelope.fail(
            "E_VALIDATION",
            "所有器件都是锁定或 --keep 的，没有可动的",
            {"parts": len(parts), "hint": "解锁需要重排的器件，或去掉 --keep"},
        )
    if not nets:
        envelope.fail(
            "E_VALIDATION",
            "板上没有跨器件的网络，布局没有可优化的依据",
            {"hint": "先跑 board from-netlist 把网表连上去"},
        )

    before = hpwl(parts, nets)
    start = {ref: (part["x"], part["y"]) for ref, part in parts.items()}
    tracks = len(board.tracks) + len(board.vias)
    preview = {
        "board": path,
        "movable": len(movable),
        "fixed": len(parts) - len(movable),
        "nets_considered": len(nets),
        "hpwl_before_mm": round(before, 2),
        "existing_tracks": tracks,
        "will": f"按网络连接关系重排 {len(movable)} 个器件；线长没变好就不写盘",
    }
    if tracks:
        # Moving the parts cuts every track off: say so before the token.
        preview["warning"] = f"板上已有 {tracks} 段走线，器件搬动后它们会失效，须重新布线"
    envelope.check_confirm(
        args.get("confirm"), "pcb_autoplace:" + os.path.basename(path), preview, path
    )

    drc_before = severities(path)
    bounds = board_bounds(board)
    edge = mm(Settings.of(board.path.with_suffix(".kicad_pro")).nm("min_copper_edge_clearance"))
    place(parts, nets, iterations, clearance, bounds, edge)
    after = hpwl(parts, nets)

    # Worse is left alone: force-directed placing does not always improve.
    improved = after < before
    if not improved:
        for ref, part in parts.items():
            part["x"], part["y"] = start[ref]
        after = before

    moved = []
    if improved:
        for ref in sorted(parts):
            part = parts[ref]
            if part["fixed"]:
                continue
            x, y = round(part["x"], 3), round(part["y"], 3)
            part["x"], part["y"] = x, y
            if abs(x - start[ref][0]) < 0.005 and abs(y - start[ref][1]) < 0.005:
                continue
            move_footprint(board, part["fp"], from_mm(x), from_mm(y))
            moved.append(
                {
                    "ref": ref,
                    "from": [round(v, 2) for v in start[ref]],
                    "to": [round(x, 2), round(y, 2)],
                }
            )
        write.save(board)

    drc_after = severities(path) if improved else dict(drc_before)
    if drc_after.get("error", 0) > drc_before.get("error", 0):
        # As `board route`: more DRC errors than before, the whole board back.
        envelope.fail(
            "E_INTEGRITY",
            "重排后 DRC error 数高于动手前，已整盘回滚",
            {
                "errors_before": drc_before.get("error", 0),
                "errors_after": drc_after.get("error", 0),
                "next_action": "板子已恢复原状。放大 Edge.Cuts 或加大 --clearance 再试，"
                "或用 --keep 固定不该动的器件",
            },
        )

    clash, outside, min_gap = inspect(parts, bounds)
    note = (
        "布局变了，走线要重来：接下来跑 board route。"
        if improved
        else "没有找到比现状更短的摆法，板子一个器件都没动。"
        "现有布局可能已经够好，或者板框太紧——放宽 Edge.Cuts 再试。"
    )
    if clash:
        note += (
            f"还有 {len(clash)} 对庭院重叠，这块板过不了 DRC，须放大 Edge.Cuts 或手工 board move。"
        )
    if outside:
        note += "有器件超出板框，须放大 Edge.Cuts。"
    warnings_added = drc_after.get("warning", 0) - drc_before.get("warning", 0)
    if warnings_added > 0:
        note += (
            f"DRC warning 多了 {warnings_added} 条（器件挨得更近，多半是丝印位号互相压）。"
            "这些不挡打板，但要么接受，要么手工挪位号文字，要么加大 --clearance。"
        )

    write.done(
        {
            "board": path,
            "improved": improved,
            "moved": len(moved),
            "moves": moved[:60],
            "hpwl_before_mm": round(before, 2),
            "hpwl_after_mm": round(after, 2),
            "hpwl_reduction_pct": round(100.0 * (before - after) / before, 1) if before else 0.0,
            "courtyard_clash": clash[:40],
            "min_courtyard_gap_mm": None if min_gap is None else round(min_gap, 3),
            "outside_outline": outside,
            "existing_tracks": tracks,
            "verify": {
                "ran": True,
                "oracle": "engine",
                "drc_before": drc_before,
                "drc_after": drc_after,
                "warnings_added": warnings_added,
                "note": "error 数没超过动手前的基线，否则这次调用已经回滚。"
                "warnings_added 为正说明挤得更紧了——那是本命令的目的，也是它的代价",
            },
            "note": note,
        }
    )
