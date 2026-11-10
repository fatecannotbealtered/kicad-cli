"""Run KiCad's ERC and DRC and report how many errors each found, without changing anything."""
import re

SCENARIO = "read_answer"
COMMANDS = ["schematic check", "board check"]
TURNS = ["Run KiCad's electrical rules check on {documents}/schmitt.kicad_sch and its design rules check on "
         "{documents}/schmitt.kicad_pcb. How many errors does each report? Do not change anything."]
FILES = "schmitt"
FAKE_HOST = {"files": [{"id": "sch", "name": "schmitt.kicad_sch", "objects": {}},
                       {"id": "pcb", "name": "schmitt.kicad_pcb", "objects": {}}]}


def errors(call):
    data = call.data or {}
    if isinstance(data.get("errors"), int):
        return data["errors"]
    if isinstance(data.get("error_count"), int):
        return data["error_count"]
    return sum(1 for i in data.get("items", []) if i.get("severity") == "error")


def grade(run):
    text = run.messages[-1] if run.messages else ""
    for path in COMMANDS:
        done = [c for c in run.ran(path) if c.ok]
        run.check(bool(done), f"{path} never ran successfully")
        if done:
            count = errors(done[-1])
            run.check(re.search(rf"\b{count}\b" + ("|no errors|zero|没有错误|0 个错误" if count == 0 else ""), text) is not None,
                      f"the answer does not give the {count} error(s) {path} reported")
    run.check(not run.write_calls(), f"a write command ran: {run.write_calls()}")


def right(agent, turn):
    erc = agent.run("schematic", "check", "--doc", str(agent.documents / "schmitt.kicad_sch"))
    drc = agent.run("board", "check", "--doc", str(agent.documents / "schmitt.kicad_pcb"))
    agent.say(f"ERC reports {errors(erc)} error(s) and DRC reports {errors(drc)} error(s). Nothing was changed.")


def wrong_unchecked(agent, turn):
    agent.say("The design looks clean: 5 errors in ERC and 5 in DRC.")
