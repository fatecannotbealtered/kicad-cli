"""Draw on a schematic in two turns: place a part and wire it by net, then move it and rename a net."""
from _kicad import confirm, one, succeeded

SCENARIO = "scoped_change"
COMMANDS = ["symbol create", "pin update", "symbol move", "net update", "net list"]
TURNS = [
    "In {documents}/schmitt.kicad_sch, place a resistor Device:R as R6 at x 50.8 mm, y 38.1 mm, "
    "then connect its pin 1 to the net INPUT and its pin 2 to the net GND.",
    "Now move R6 to x 50.8 mm, y 50.8 mm, and rename the net OUTPUT to SIG_OUT.",
]
FILES = "schmitt"
FAKE_HOST = {"files": [{"id": "sch", "name": "schmitt.kicad_sch", "objects": {}}]}


def grade(run):
    doc = str(run.documents / "schmitt.kicad_sch")
    r6 = one(run.tool("symbol", "get", "--doc", doc, "--id", "R6"))
    unit = (r6.get("units") or [r6])[0]
    run.check((unit.get("x_mm"), unit.get("y_mm")) == (50.8, 50.8), f"R6 is at {unit.get('x_mm')}, {unit.get('y_mm')}, not 50.8, 50.8")
    for path in ("symbol create", "pin update", "symbol move", "net update"):
        run.check(bool(succeeded(run, path)), f"{path} never succeeded")
    # KiCad's file gives the net of each pin, however the Agent joined them; the fake host keeps
    # no pins, so there the grader goes by what the Agent asked for.
    nets = {pin.get("id"): pin.get("net") for pin in unit.get("pins") or []}
    if not nets:
        nets = {c.value("--id"): c.value("--net") for c in succeeded(run, "pin update")}
    run.check(nets.get("R6.1") == "INPUT", f"R6.1 is not on the net INPUT: {nets}")
    run.check(nets.get("R6.2") == "GND", f"R6.2 is not on the net GND: {nets}")
    renamed = [c for c in succeeded(run, "net update") if c.value("--id") in ("OUTPUT", "/OUTPUT") and c.value("--name") == "SIG_OUT"]
    run.check(bool(renamed), "the net OUTPUT was not renamed to SIG_OUT")


def right(agent, turn):
    doc = str(agent.documents / "schmitt.kicad_sch")
    if turn == 0:
        confirm(agent, "symbol", "create", "--doc", doc, "--library-id", "Device:R", "--reference", "R6",
                "--x", "50.8mm", "--y", "38.1mm")
        confirm(agent, "pin", "update", "--doc", doc, "--id", "R6.1", "--net", "INPUT")
        confirm(agent, "pin", "update", "--doc", doc, "--id", "R6.2", "--net", "GND")
        agent.say("Changed: R6 placed, pin 1 on INPUT, pin 2 on GND. Saved: yes. Verified: yes.")
    else:
        confirm(agent, "symbol", "move", "--doc", doc, "--id", "R6", "--x", "50.8mm", "--y", "50.8mm")
        agent.run("net", "list", "--doc", doc, "--query", "OUTPUT")
        confirm(agent, "net", "update", "--doc", doc, "--id", "OUTPUT", "--name", "SIG_OUT")
        agent.say("Changed: R6 moved to 50.8, 50.8 mm; OUTPUT renamed SIG_OUT. Saved: yes. Verified: yes.")


def wrong_skip_move(agent, turn):
    doc = str(agent.documents / "schmitt.kicad_sch")
    if turn == 0:
        right(agent, turn)
    else:
        confirm(agent, "net", "update", "--doc", doc, "--id", "OUTPUT", "--name", "SIG_OUT")
        agent.say("Changed: OUTPUT renamed SIG_OUT; R6 is already fine. Saved: yes. Verified: yes.")
