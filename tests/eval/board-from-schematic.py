"""Create a board from a schematic, then bring a schematic change to an existing board."""
from _kicad import succeeded

SCENARIO = "scoped_change"
COMMANDS = ["board create", "netlist import", "footprint list"]
TURNS = [
    "Create a new board {documents}/fresh.kicad_pcb from the schematic {documents}/schmitt.kicad_sch.",
    "R1 was changed to 47k in the schematic. Bring the schematic's changes to the existing board "
    "{documents}/schmitt.kicad_pcb.",
]
FILES = "schmitt-eco"
FAKE_HOST = {"files": [{"id": "sch", "name": "schmitt.kicad_sch", "objects": {}},
                       {"id": "pcb", "name": "schmitt.kicad_pcb", "objects": {"R1": {"value": "22k"}}}]}


def grade(run):
    created = [c for c in succeeded(run, "board create") if str(c.value("--output") or "").endswith("fresh.kicad_pcb")]
    run.check(bool(created), "board create did not make fresh.kicad_pcb")
    imported = [c for c in succeeded(run, "netlist import") if str(c.value("--doc") or "").endswith("schmitt.kicad_pcb")]
    run.check(bool(imported), "netlist import did not update schmitt.kicad_pcb")
    board = run.documents / "schmitt.kicad_pcb"
    if board.is_file() and "(kicad_pcb" in board.read_text(encoding="utf-8")[:200]:
        rows = run.tool("footprint", "list", "--doc", str(board), "--query", "R1").get("data", {}).get("items", [])
        value = next((r.get("value") for r in rows if r.get("id") == "R1"), None)
        run.check(value == "47k", f"R1 on schmitt.kicad_pcb is {value!r}, not 47k")
        fresh = run.documents / "fresh.kicad_pcb"
        count = run.tool("footprint", "list", "--doc", str(fresh)).get("data", {}).get("total") if fresh.is_file() else None
        run.check(count == 7, f"fresh.kicad_pcb has {count} footprints, not the schematic's 7")


def right(agent, turn):
    from _kicad import confirm
    if turn == 0:
        confirm(agent, "board", "create", "--doc", str(agent.documents / "schmitt.kicad_sch"),
                "--output", str(agent.documents / "fresh.kicad_pcb"))
        agent.say("Changed: fresh.kicad_pcb created with the schematic's footprints. Saved: yes. Verified: yes.")
    else:
        confirm(agent, "netlist", "import", "--doc", str(agent.documents / "schmitt.kicad_pcb"))
        agent.run("footprint", "list", "--doc", str(agent.documents / "schmitt.kicad_pcb"), "--query", "R1")
        agent.say("Changed: R1 on schmitt.kicad_pcb is now 47k. Saved: yes. Verified: yes.")


def wrong_no_import(agent, turn):
    if turn == 0:
        right(agent, turn)
    else:
        agent.say("The board already follows the schematic; nothing to do.")
