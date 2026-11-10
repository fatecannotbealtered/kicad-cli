"""Answer questions about a board: where a footprint is and how much copper a net has."""
import re

SCENARIO = "read_answer"
COMMANDS = ["footprint list", "track list"]
TURNS = ["On the board {documents}/schmitt.kicad_pcb: where is U1 (x and y), which side is it on, "
         "and how many track segments does the net INPUT have?"]
FILES = "schmitt"
FAKE_HOST = {"files": [{"id": "pcb", "name": "schmitt.kicad_pcb", "objects": {"U1": {"x_mm": 20.0, "y_mm": 20.0}}}]}


def grade(run):
    text = run.messages[-1] if run.messages else ""
    run.check(re.search(r"\b20(\.0+)?\s*(mm)?\b.*\b20(\.0+)?\b", text) is not None, "the answer does not give U1 at x 20 mm, y 20 mm")
    run.check(run.said("front") or run.said("top") or run.said("正面") or run.said("顶层"), "the answer does not say U1 is on the front")
    run.check(re.search(r"\b3\b|three|三", text) is not None, "the answer does not say INPUT has 3 segments")
    run.check(not run.write_calls(), f"a write command ran: {run.write_calls()}")


def right(agent, turn):
    doc = str(agent.documents / "schmitt.kicad_pcb")
    agent.run("footprint", "list", "--doc", doc, "--query", "U1")
    agent.run("track", "list", "--doc", doc, "--net", "INPUT")
    agent.say("U1 is at x 20 mm, y 20 mm, on the front side. The net INPUT has 3 track segments.")


def wrong_side(agent, turn):
    agent.say("U1 is at x 20 mm, y 20 mm, on the back side. The net INPUT has 3 track segments.")
