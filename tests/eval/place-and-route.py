"""Place a part and add copper on an unrouted board, with KiCad's DRC checking each change first."""
from _kicad import confirm, succeeded, verified

SCENARIO = "scoped_change"
COMMANDS = ["footprint move", "track create"]
TURNS = ["On the board {documents}/schmitt.kicad_pcb, move C1 to x 28 mm, y 19.05 mm, then add a GND track on F.Cu "
         "from (18.7 mm, 27 mm) to (18.7 mm, 30 mm)."]
FILES = "layout"
FAKE_HOST = {"files": [{"id": "pcb", "name": "schmitt.kicad_pcb", "objects": {"C1": {"x_mm": 24.0, "y_mm": 19.05}}}]}


def millimetres(value):
    """A length flag given in mm, such as 28mm or 28.00 mm, as a number; None otherwise."""
    text = str(value or "").replace(" ", "")
    try:
        return round(float(text[:-2]), 4) if text.endswith("mm") else None
    except ValueError:
        return None


def grade(run):
    moves = [c for c in succeeded(run, "footprint move") if c.value("--id") == "C1"]
    run.check(bool(moves) and verified(moves[-1]), "C1 was not moved with a verified write")
    if moves:
        x, y = moves[-1].value("--x"), moves[-1].value("--y")
        run.check((millimetres(x), millimetres(y)) == (28.0, 19.05), f"C1 was moved to {x}, {y}")
    tracks = [c for c in succeeded(run, "track create") if c.value("--net") == "GND" and c.value("--layer") == "F.Cu"]
    run.check(bool(tracks) and verified(tracks[-1]), "no verified GND track was added on F.Cu")
    writes = [c for c in run.write_calls() if c.ok]
    run.check(len(writes) == 2, f"{len(writes)} writes succeeded, not the move and the track: {writes}")


def right(agent, turn):
    doc = str(agent.documents / "schmitt.kicad_pcb")
    confirm(agent, "footprint", "move", "--doc", doc, "--id", "C1", "--x", "28mm", "--y", "19.05mm")
    confirm(agent, "track", "create", "--doc", doc, "--net", "GND", "--layer", "F.Cu",
            "--x1", "18.7mm", "--y1", "27mm", "--x2", "18.7mm", "--y2", "30mm")
    agent.say("Changed: C1 moved to 28, 19.05 mm; a GND track added on F.Cu. Saved: yes. Verified: yes.")


def wrong_layer(agent, turn):
    doc = str(agent.documents / "schmitt.kicad_pcb")
    confirm(agent, "footprint", "move", "--doc", doc, "--id", "C1", "--x", "28mm", "--y", "19.05mm")
    confirm(agent, "track", "create", "--doc", doc, "--net", "GND", "--layer", "B.Cu",
            "--x1", "18.7mm", "--y1", "27mm", "--x2", "18.7mm", "--y2", "30mm")
    agent.say("Changed: C1 moved; a GND track added. Saved: yes. Verified: yes.")
