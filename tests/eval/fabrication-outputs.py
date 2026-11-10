"""Produce the usual output files of a design, and a new empty sheet, and report each file."""
from _kicad import confirm, succeeded, verified

SCENARIO = "scoped_change"
COMMANDS = ["board export", "board render", "schematic export", "schematic render", "schematic create"]
TURNS = [
    "For the design in {documents}: export the Gerber files and the drill files of schmitt.kicad_pcb into the folder "
    "{documents}/fab, plot the board to {documents}/board.pdf, export the schematic's netlist to {documents}/schmitt.net "
    "and plot the schematic to {documents}/schmitt.pdf. Also create a new empty schematic sheet "
    "{documents}/power.kicad_sch in the project schmitt.kicad_pro."
]
FILES = "schmitt"
FAKE_HOST = {"files": [{"id": "pcb", "name": "schmitt.kicad_pcb", "objects": {}},
                       {"id": "sch", "name": "schmitt.kicad_sch", "objects": {}},
                       {"id": "pro", "name": "schmitt.kicad_pro", "objects": {}}]}


def grade(run):
    kinds = {c.value("--file-type") for c in succeeded(run, "board export") if verified(c)}
    run.check({"gerber", "drill"} <= kinds, f"Gerber and drill files were not both exported and verified: {sorted(kinds)}")
    for path, ending in (("board render", "board.pdf"), ("schematic export", "schmitt.net"), ("schematic render", "schmitt.pdf"),
                         ("schematic create", "power.kicad_sch")):
        done = [c for c in succeeded(run, path) if str(c.value("--output") or "").replace("\\", "/").endswith(ending)]
        run.check(bool(done) and verified(done[-1]), f"{path} did not produce {ending} with a verified write")


def right(agent, turn):
    d = agent.documents
    out = {}
    for kind in ("gerber", "drill"):
        out[kind] = confirm(agent, "board", "export", "--doc", str(d / "schmitt.kicad_pcb"), "--file-type", kind, "--output", str(d / "fab"))
    confirm(agent, "board", "render", "--doc", str(d / "schmitt.kicad_pcb"), "--file-type", "pdf", "--output", str(d / "board.pdf"))
    confirm(agent, "schematic", "export", "--doc", str(d / "schmitt.kicad_sch"), "--file-type", "netlist", "--output", str(d / "schmitt.net"))
    confirm(agent, "schematic", "render", "--doc", str(d / "schmitt.kicad_sch"), "--file-type", "pdf", "--output", str(d / "schmitt.pdf"))
    confirm(agent, "schematic", "create", "--doc", str(d / "schmitt.kicad_pro"), "--output", str(d / "power.kicad_sch"))
    agent.say("Changed: Gerber and drill files in fab, board.pdf, schmitt.net, schmitt.pdf and the empty sheet power.kicad_sch. "
              "Saved: yes, all written. Verified: yes, every file was checked.")


def wrong_no_drill(agent, turn):
    d = agent.documents
    confirm(agent, "board", "export", "--doc", str(d / "schmitt.kicad_pcb"), "--file-type", "gerber", "--output", str(d / "fab"))
    agent.say("Changed: Gerber files in fab. Saved: yes. Verified: yes.")
