"""Answer a question about a project's design rules by reading the project, and change nothing."""

SCENARIO = "read_answer"
COMMANDS = ["project get"]
TURNS = ["What minimum copper-to-board-edge clearance and what minimum hole clearance does the KiCad project "
         "{documents}/schmitt.kicad_pro set?"]
FILES = "schmitt"
FAKE_HOST = {"files": [{"id": "pro", "name": "schmitt.kicad_pro", "objects": {}}]}


def grade(run):
    run.check(run.said("0.5"), "the answer does not give the copper-to-edge clearance, 0.5 mm")
    run.check(run.said("0.25"), "the answer does not give the hole clearance, 0.25 mm")
    run.check(not run.write_calls(), f"a write command ran: {run.write_calls()}")


def right(agent, turn):
    agent.run("project", "get", "--doc", str(agent.documents / "schmitt.kicad_pro"))
    agent.say("The project sets a minimum copper-to-edge clearance of 0.5 mm and a minimum hole clearance of 0.25 mm.")


def wrong_guess(agent, turn):
    agent.say("KiCad's defaults apply: 0.3 mm to the edge and 0.2 mm between holes.")
