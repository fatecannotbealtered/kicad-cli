"""Define a new part in the project libraries: a footprint and a symbol that uses it."""
from _kicad import succeeded

SCENARIO = "scoped_change"
COMMANDS = ["footprint create", "symbol create", "library get"]
TURNS = [
    "Add a part to a new project library PracticeLib of {documents}/schmitt.kicad_pro. First a footprint PRACTICE_PAD2 in "
    "{documents}/PracticeLib.pretty: two SMD pads, numbered 1 and 2, each 0.8 mm wide and 1.0 mm high, at x -0.75 mm "
    "and x 0.75 mm (y 0); body 2.0 x 1.2 mm; courtyard margin 0.25 mm. Then a symbol PRACTICE_R2 in "
    "{documents}/PracticeLib.kicad_sym with two passive pins, 1 and 2, reference prefix R, whose footprint is "
    "PracticeLib:PRACTICE_PAD2."
]
FILES = "schmitt"
FAKE_HOST = {"files": [{"id": "pretty", "name": "PracticeLib.pretty", "objects": {}},
                       {"id": "sym", "name": "PracticeLib.kicad_sym", "objects": {}},
                       {"id": "pro", "name": "schmitt.kicad_pro", "objects": {}}]}


def grade(run):
    fp = [c for c in succeeded(run, "footprint create") if str(c.value("--doc") or "").endswith("PracticeLib.pretty")]
    sym = [c for c in succeeded(run, "symbol create") if str(c.value("--doc") or "").endswith("PracticeLib.kicad_sym")]
    run.check(bool(fp), "no footprint was defined in PracticeLib.pretty")
    run.check(bool(sym), "no symbol was defined in PracticeLib.kicad_sym")
    if (run.documents / "PracticeLib.pretty").is_dir():
        run.check((run.documents / "PracticeLib.pretty" / "PRACTICE_PAD2.kicad_mod").is_file(),
                  "PracticeLib.pretty has no PRACTICE_PAD2.kicad_mod")
        text = (run.documents / "PracticeLib.kicad_sym").read_text(encoding="utf-8")
        run.check('"PRACTICE_R2"' in text, "PracticeLib.kicad_sym has no symbol PRACTICE_R2")
        run.check("PracticeLib:PRACTICE_PAD2" in text, "PRACTICE_R2 does not name the footprint PracticeLib:PRACTICE_PAD2")
        tables = "".join((run.documents / t).read_text(encoding="utf-8") for t in ("sym-lib-table", "fp-lib-table"))
        run.check(tables.count('"PracticeLib"') == 2, "PracticeLib is not registered in both project library tables")
    run.check(len([c for c in run.write_calls() if c.ok]) == 2, "not exactly two writes succeeded")


FOOTPRINT = ('{"name": "PRACTICE_PAD2", "body_mm": [2.0, 1.2], "courtyard_mm": 0.25, "pads": ['
             '{"number": "1", "at_mm": [-0.75, 0], "size_mm": [0.8, 1.0]}, {"number": "2", "at_mm": [0.75, 0], "size_mm": [0.8, 1.0]}]}')
SYMBOL = ('{"name": "PRACTICE_R2", "reference": "R", "footprint": "PracticeLib:PRACTICE_PAD2", "body_mm": [2.54, 5.08], '
          '"pins": [{"number": "1", "name": "~", "type": "passive", "at_mm": [0, 5.08], "angle_deg": 270, "length_mm": 2.54}, '
          '{"number": "2", "name": "~", "type": "passive", "at_mm": [0, -5.08], "angle_deg": 90, "length_mm": 2.54}]}')


def right(agent, turn):
    from _kicad import confirm
    (agent.workspace / "pad2.json").write_text(FOOTPRINT, encoding="utf-8")
    (agent.workspace / "r2.json").write_text(SYMBOL, encoding="utf-8")
    confirm(agent, "footprint", "create", "--doc", str(agent.documents / "PracticeLib.pretty"), "--definition", "pad2.json")
    confirm(agent, "symbol", "create", "--doc", str(agent.documents / "PracticeLib.kicad_sym"), "--definition", "r2.json")
    agent.say("Changed: PRACTICE_PAD2 added to PracticeLib.pretty and PRACTICE_R2 to PracticeLib.kicad_sym, both registered. Saved: yes. Verified: yes.")


def wrong_symbol_only(agent, turn):
    from _kicad import confirm
    (agent.workspace / "r2.json").write_text(SYMBOL, encoding="utf-8")
    confirm(agent, "symbol", "create", "--doc", str(agent.documents / "PracticeLib.kicad_sym"), "--definition", "r2.json")
    agent.say("Changed: PRACTICE_R2 added. Saved: yes. Verified: yes.")
