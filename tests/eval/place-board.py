"""Lay out a board straight from its schematic: place the parts, turning some, and fit the outline."""
import json

from _kicad import box_area, confirm, errors, items, succeeded, verified

SCENARIO = "scoped_change"
COMMANDS = ["board get", "footprint list", "footprint move", "board update", "board check"]
TURNS = ["The board {documents}/layout-practice.kicad_pcb was just made from its schematic, with the parts in a grid. "
         "Lay it out: place the parts sensibly and shrink the board outline to a suitable size."]
FILES = "practice"
FAKE_HOST = None  # the grader measures courtyards and the outline in KiCad's file
MARGIN_MM = 5.0  # the most the outline may stand off the parts on any side
AREA_FACTOR = 5.0  # the outline may be at most this many times the parts' courtyard area


def grade(run):
    doc = str(run.documents / "layout-practice.kicad_pcb")
    board = (run.tool("board", "get", "--doc", doc) or {}).get("data") or {}
    outline = board.get("outline") or {}
    run.check(outline.get("shape") == "rectangle", f"the outline is not one rectangle: {outline}")
    run.check(board.get("footprints_outside") == [], f"footprints stick out of the outline: {board.get('footprints_outside')}")
    margins = board.get("margins_mm") or {}
    run.check(bool(margins) and all(0 <= v <= MARGIN_MM for v in margins.values()),
              f"the outline stands off the parts by more than {MARGIN_MM} mm on a side: {margins}")
    courtyards = [f["courtyard"] for f in items(run, "footprint", "list", "--doc", doc, "--fields", "id,courtyard")
                  if f.get("courtyard")]
    parts = sum(box_area(c) for c in courtyards)
    area = (outline.get("width_mm") or 0) * (outline.get("height_mm") or 0)
    run.check(0 < area <= AREA_FACTOR * parts,
              f"the outline is {area:.0f} mm2, more than {AREA_FACTOR:g} times the parts' {parts:.0f} mm2")
    check = (run.tool("board", "check", "--doc", doc, "--limit", "100") or {}).get("data") or {}
    run.check(not errors(check), f"KiCad's DRC reports errors: {[e.get('type') for e in errors(check)]}")
    moves = succeeded(run, "footprint move")
    run.check(bool(moves) and all(verified(c) or all(i.get("ok") for i in (c.data or {}).get("items", [{}])) for c in moves),
              "no verified footprint move")
    run.check(any(verified(c) for c in succeeded(run, "board update")), "the outline was not set with a verified write")


PLACE = {  # the layout tests/eval/practice-placed holds: x, y, rotation
    "J1": (12.5, 14.0, 0), "C1": (17.0, 15.27, 0), "U1": (27.0, 15.5, 0), "C2": (34.0, 15.5, 90),
    "R4": (38.5, 13.0, 0), "D2": (38.5, 17.0, 180), "C3": (15.5, 26.5, 90), "R2": (15.5, 30.0, 90),
    "R1": (15.5, 33.5, 90), "C4": (19.0, 25.5, 0), "U2": (22.0, 30.0, 180), "C5": (28.0, 26.0, 0),
    "R3": (30.0, 29.365, 0), "D1": (34.5, 29.365, 180), "J2": (42.0, 27.5, 0),
}


def right(agent, turn):
    doc = str(agent.documents / "layout-practice.kicad_pcb")
    agent.run("board", "get", "--doc", doc)
    agent.run("footprint", "list", "--doc", doc, "--fields", "id,courtyard,pads")
    moves = agent.workspace / "moves.json"
    moves.write_text(json.dumps({"items": [{"id": ref, "x": f"{x}mm", "y": f"{y}mm", "rotation": f"{r}deg"}
                                           for ref, (x, y, r) in PLACE.items()]}), encoding="utf-8")
    confirm(agent, "footprint", "move", "--doc", doc, "--input", str(moves))
    confirm(agent, "board", "update", "--doc", doc, "--x", "10mm", "--y", "10mm", "--width", "35mm", "--height", "27mm")
    agent.run("board", "check", "--doc", doc)
    agent.say("Changed: 15 parts placed by function, the outline set to 35 x 27 mm. Saved: yes. Verified: yes.")


def wrong_outline_only(agent, turn):
    doc = str(agent.documents / "layout-practice.kicad_pcb")
    extent = agent.run("board", "get", "--doc", doc).data["parts_extent"]
    confirm(agent, "board", "update", "--doc", doc, "--x", f"{extent['x_mm'] - 1:.3f}mm", "--y", f"{extent['y_mm'] - 1:.3f}mm",
            "--width", f"{extent['width_mm'] + 2:.3f}mm", "--height", f"{extent['height_mm'] + 2:.3f}mm")
    agent.say("Changed: the outline now fits the parts. Saved: yes. Verified: yes.")
