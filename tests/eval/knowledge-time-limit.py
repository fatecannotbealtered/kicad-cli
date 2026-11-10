"""A question only kicad-cli's knowledge answers: the answer agrees with the entry the Agent read."""

SCENARIO = "knowledge_lookup"
COMMANDS = ["knowledge search", "knowledge get"]
TURNS = ["With kicad-cli, how many seconds does KiCad's design rules check get before kicad-cli gives up on it?"]
FAKE_HOST = {"files": []}


def grade(run):
    run.check(run.said("25"), "the answer does not give the 25 seconds of the knowledge entry checks-time-limit")
    run.check(any(c.ok and c.value("--id") == "checks-time-limit" for c in run.ran("knowledge get")),
              "the Agent never read the knowledge entry checks-time-limit")
    run.check(not run.write_calls(), f"a write command ran: {run.write_calls()}")


def right(agent, turn):
    found = agent.run("knowledge", "search", "--query", "time limit").data["items"]
    entry = next((i["id"] for i in found if i["id"] == "checks-time-limit"), found[0]["id"] if found else "checks-time-limit")
    body = agent.run("knowledge", "get", "--id", entry).stdout
    seconds = "25" if "25 seconds" in body else "unknown"
    agent.say(f"kicad-cli gives KiCad's DRC {seconds} seconds (knowledge entry {entry}).")


def wrong(agent, turn):
    agent.say("kicad-cli waits up to 60 seconds for KiCad's DRC.")
