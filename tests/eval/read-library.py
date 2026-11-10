"""Find what a project library holds, by reading the project's library tables and the library."""

SCENARIO = "read_answer"
COMMANDS = ["library list", "library get"]
TURNS = ["Which footprints does the project-local footprint library of {documents}/schmitt.kicad_pro hold? "
         "Name the library too."]
FILES = "schmitt"
FAKE_HOST = {"files": [{"id": "pro", "name": "schmitt.kicad_pro", "objects": {"LocalLogic": {"type": "footprint"}}}]}


def grade(run):
    run.check(run.said("LocalLogic"), "the answer does not name the library LocalLogic")
    run.check(run.said("DBV0005A") and run.said("TestPad"), "the answer does not name both footprints, DBV0005A and TestPad")
    run.check(not run.write_calls(), f"a write command ran: {run.write_calls()}")


def right(agent, turn):
    doc = str(agent.documents / "schmitt.kicad_pro")
    agent.run("library", "list", "--doc", doc, "--type", "footprint")
    agent.run("library", "get", "--doc", doc, "--id", "LocalLogic", "--type", "footprint")
    agent.say("The project library LocalLogic holds two footprints: DBV0005A and TestPad.")


def wrong_partial(agent, turn):
    agent.say("The project library LocalLogic holds the footprint DBV0005A.")
