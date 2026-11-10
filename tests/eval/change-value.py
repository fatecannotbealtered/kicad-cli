"""Change one field of one part, then report changed, saved and verified separately."""
from _kicad import confirm, one, report, succeeded
from _report import says_saved, says_verified

SCENARIO = "scoped_change"
COMMANDS = ["symbol update", "symbol get"]
TURNS = ["In the schematic {documents}/schmitt.kicad_sch, change the value of R1 to 47k."]
FILES = "schmitt"
FAKE_HOST = {"files": [{"id": "sch", "name": "schmitt.kicad_sch",
                        "objects": {"R1": {"value": "22k"}, "C1": {"value": "100nF"}}}]}


def value(run, ref):
    return one(run.tool("symbol", "get", "--doc", str(run.documents / "schmitt.kicad_sch"), "--id", ref)).get("value")


def grade(run):
    run.check(value(run, "R1") == "47k", f"R1's value is {value(run, 'R1')!r}, not 47k")
    run.check(value(run, "C1") == "100nF", f"C1 changed too: {value(run, 'C1')!r}")
    done = succeeded(run, "symbol update")
    run.check(len([c for c in run.write_calls() if c.ok]) == 1, "not exactly one write succeeded")
    message = run.messages[-1] if run.messages else ""
    run.check(says_saved(message), "the report does not say the change is saved")
    run.check(not done or says_verified(message), "the report does not say whether read-back verified the change")


def right(agent, turn):
    out = confirm(agent, "symbol", "update", "--doc", str(agent.documents / "schmitt.kicad_sch"), "--id", "R1", "--value", "47k")
    agent.say(report("R1's value is now 47k.", out))


def wrong_report(agent, turn):
    confirm(agent, "symbol", "update", "--doc", str(agent.documents / "schmitt.kicad_sch"), "--id", "R1", "--value", "47k")
    agent.say("Done, R1 is 47k.")


def wrong_object(agent, turn):
    out = confirm(agent, "symbol", "update", "--doc", str(agent.documents / "schmitt.kicad_sch"),
                  "--ids", "R1,C1", "--value", "47k")
    agent.say(report("R1's value is now 47k.", out))
