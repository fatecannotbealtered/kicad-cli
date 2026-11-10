"""Answer questions about a schematic: a part's value, a net's pins and the number of parts."""
import re

SCENARIO = "read_answer"
COMMANDS = ["symbol list", "symbol get", "net list"]
TURNS = ["In {documents}/schmitt.kicad_sch: how many parts are placed (leave out power symbols), what is R1's value, "
         "and which pins are on the GND net?"]
FILES = "schmitt"
FAKE_HOST = {"files": [{"id": "sch", "name": "schmitt.kicad_sch", "objects": {"R1": {"value": "22k"}}}]}
PINS = ("C1", "2"), ("R1", "2"), ("TP2", "1"), ("U1", "3")


def grade(run):
    text = run.messages[-1] if run.messages else ""
    run.check(run.said("22k") or run.said("22 k"), "the answer does not give R1's value, 22k")
    run.check(re.search(r"\b7\b|seven|七", text) is not None, "the answer does not say that 7 parts are placed")
    for ref, pin in PINS:
        run.check(re.search(rf"{ref}\D{{0,12}}{pin}\b", text) is not None, f"the answer does not name pin {pin} of {ref} on GND")
    run.check(not run.write_calls(), f"a write command ran: {run.write_calls()}")


def right(agent, turn):
    doc = str(agent.documents / "schmitt.kicad_sch")
    agent.run("symbol", "list", "--doc", doc)
    agent.run("symbol", "get", "--doc", doc, "--id", "R1")
    agent.run("net", "list", "--doc", doc, "--query", "GND")
    agent.say("7 parts are placed. R1 is 22k. GND joins C1.2, R1.2, TP2.1 and U1.3.")


def wrong_count(agent, turn):
    agent.say("9 parts are placed. R1 is 22k. GND joins C1.2, R1.2, TP2.1 and U1.3.")
