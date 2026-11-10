"""Answer which files a project folder holds and whether one of them is read-only."""
import os
import stat

SCENARIO = "read_answer"
COMMANDS = ["doc list", "doc get"]
TURNS = ["Which KiCad files are in {documents}?",
         "I cannot save schmitt.kicad_sch from there. Is that file read-only?"]
FILES = "schmitt"
FAKE_HOST = None  # the question is about the file's own read-only flag


def before(run, index):
    if index == 0:
        os.chmod(run.documents / "schmitt.kicad_sch", stat.S_IREAD)


def after(run):
    os.chmod(run.documents / "schmitt.kicad_sch", stat.S_IREAD | stat.S_IWRITE)


def grade(run):
    first = run.messages[0] if run.messages else ""
    for name in ("schmitt.kicad_sch", "schmitt.kicad_pcb", "schmitt.kicad_pro"):
        run.check(name in first, f"the first answer does not list {name}")
    run.check(bool(run.ran("doc list")), "doc list never ran")
    run.check(run.said("read-only", turn=1) or run.said("read only", turn=1) or run.said("只读", turn=1),
              "the second answer does not say the schematic is read-only")
    run.check(not run.said("not read-only", turn=1) and not run.said("不是只读", turn=1),
              "the second answer says the schematic is not read-only")
    run.check(not run.write_calls(), f"a write command ran: {run.write_calls()}")


def right(agent, turn):
    if turn == 0:
        agent.run("doc", "list", "--doc", str(agent.documents))
        agent.say("The folder holds schmitt.kicad_pro, schmitt.kicad_sch and schmitt.kicad_pcb, plus the LocalLogic "
                  "libraries.")
    else:
        agent.run("doc", "get", "--doc", str(agent.documents / "schmitt.kicad_sch"))
        agent.say("Yes: schmitt.kicad_sch is read-only (readonly: true), so nothing can save it until that flag is cleared.")


def wrong_writable(agent, turn):
    if turn == 0:
        right(agent, turn)
    else:
        agent.run("doc", "get", "--doc", str(agent.documents / "schmitt.kicad_sch"))
        agent.say("No, schmitt.kicad_sch is not read-only; it can be written.")
