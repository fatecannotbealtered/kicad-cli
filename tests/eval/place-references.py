"""Tidy the silkscreen of a routed board: every reference designator beside its own part."""
import json

from _kicad import box_distance, confirm, items, succeeded

SCENARIO = "scoped_change"
COMMANDS = ["footprint list", "footprint update"]
TURNS = ["The reference designators on {documents}/layout-practice.kicad_pcb are all over the place. "
         "Put each one beside its own part."]
FILES = "practice-routed"
FAKE_HOST = None  # the grader measures the texts against the courtyards in KiCad's file


def misplaced(run, doc):
    rows = [f for f in items(run, "footprint", "list", "--doc", doc, "--fields", "id,courtyard,reference_text")
            if f.get("courtyard") and f.get("reference_text") and not f["reference_text"].get("hidden")]
    out = []
    for row in rows:
        text = row["reference_text"]
        own = box_distance(row["courtyard"], text["x_mm"], text["y_mm"])
        nearest = min(((box_distance(o["courtyard"], text["x_mm"], text["y_mm"]), o["id"]) for o in rows if o is not row),
                      default=(float("inf"), None))
        if own >= nearest[0]:
            out.append(f"{row['id']} (nearer {nearest[1]})")
    return out, len(rows)


def grade(run):
    doc = str(run.documents / "layout-practice.kicad_pcb")
    wrong, total = misplaced(run, doc)
    run.check(total >= 15, f"only {total} visible reference texts were found")
    run.check(not wrong, f"references not nearest their own part: {', '.join(wrong)}")
    run.check(bool(succeeded(run, "footprint update")), "no reference text was moved")


TEXTS = {  # where KiCad's footprints put each reference on this board
    "C1": (18.0, 11.52, 0), "C2": (32.32, 15.5, 90), "C3": (13.82, 26.5, 90), "C4": (19.0, 23.82, 0),
    "C5": (28.0, 24.32, 0), "D1": (34.5, 31.015, 180), "D2": (38.5, 18.65, 180), "J1": (12.5, 11.62, 0),
    "J2": (42.0, 25.12, 0), "R1": (13.85, 33.5, 90), "R2": (13.85, 30.0, 90), "R3": (30.0, 27.715, 0),
    "R4": (38.5, 11.35, 0), "U1": (27.0, 11.0, 0), "U2": (22.0, 33.4, 180),
}


def right(agent, turn):
    doc = str(agent.documents / "layout-practice.kicad_pcb")
    agent.run("footprint", "list", "--doc", doc, "--fields", "id,courtyard,reference_text")
    texts = agent.workspace / "texts.json"
    texts.write_text(json.dumps({"items": [
        {"id": ref, "reference_x": f"{x}mm", "reference_y": f"{y}mm", "reference_rotation": f"{r}deg"}
        for ref, (x, y, r) in TEXTS.items()]}), encoding="utf-8")
    confirm(agent, "footprint", "update", "--doc", doc, "--input", str(texts))
    agent.say("Changed: all 15 reference designators moved beside their parts. Saved: yes. Verified: yes.")


def wrong_half(agent, turn):
    doc = str(agent.documents / "layout-practice.kicad_pcb")
    half = dict(list(TEXTS.items())[:7])
    texts = agent.workspace / "texts.json"
    texts.write_text(json.dumps({"items": [
        {"id": ref, "reference_x": f"{x}mm", "reference_y": f"{y}mm"} for ref, (x, y, _) in half.items()]}),
        encoding="utf-8")
    confirm(agent, "footprint", "update", "--doc", doc, "--input", str(texts))
    agent.say("Changed: the reference designators are beside their parts. Saved: yes. Verified: yes.")
