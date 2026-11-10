"""Route a placed board: every connection, vias where layers change, wide supply tracks, a GND plane."""
import json

from _kicad import confirm, errors, items, succeeded

SCENARIO = "scoped_change"
COMMANDS = ["footprint list", "board check", "track create", "via create", "zone create", "track list"]
TURNS = ["Route every connection on {documents}/layout-practice.kicad_pcb. Change layers with vias where needed, "
         "make the power tracks -- VIN, +5V and GND -- 0.5 mm wide, and fill the whole bottom layer with GND."]
FILES = "practice-placed"
FAKE_HOST = None  # the grader reads KiCad's DRC and the copper in KiCad's file
POWER = {"/VIN", "+5V", "GND"}
OUTLINE_MM2 = 35.0 * 27.0  # the outline of tests/eval/practice-placed


def polygon_area(points):
    pts = [(p["x_mm"], p["y_mm"]) for p in points]
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))) / 2


def grade(run):
    doc = str(run.documents / "layout-practice.kicad_pcb")
    check = (run.tool("board", "check", "--doc", doc, "--limit", "100") or {}).get("data") or {}
    run.check(not errors(check), f"KiCad's DRC reports errors: {[e.get('type') for e in errors(check)]}")
    unconnected = ((check.get("summary") or {}).get("unconnected_items") or {}).get("error")
    run.check(unconnected == 0, f"{unconnected} connections are not routed")
    thin = [t for t in items(run, "track", "list", "--doc", doc)
            if t.get("type") == "segment" and t.get("net") in POWER and t.get("width_mm", 0) < 0.5 - 1e-6]
    run.check(not thin, f"{len(thin)} power track segments are thinner than 0.5 mm")
    planes = [z for z in items(run, "zone", "list", "--doc", doc)
              if z.get("net") == "GND" and "B.Cu" in (z.get("layers") or []) and not z.get("keepout")]
    covered = max((polygon_area(z.get("points") or []) for z in planes), default=0)
    run.check(covered >= 0.9 * OUTLINE_MM2, f"no GND zone on B.Cu covers the board ({covered:.0f} of {OUTLINE_MM2:.0f} mm2)")
    run.check(bool(succeeded(run, "track create")), "no track was added")


P, S = 0.5, 0.25
TRACKS = [  # net, width, layer, points: a routing that KiCad's DRC passes
    ("/VIN", P, [(12.5, 14.0), (15.73, 14.0), (17.0, 15.27)]),
    ("/VIN", P, [(17.0, 15.27), (17.0, 17.8), (23.85, 17.8)]),
    ("+5V", P, [(23.85, 15.5), (30.15, 15.5)]),
    ("+5V", P, [(30.15, 16.45), (34.0, 16.45)]),
    ("+5V", P, [(34.0, 16.45), (35.5, 16.45), (35.5, 13.0), (37.587, 13.0)]),
    ("+5V", P, [(34.0, 16.45), (34.0, 19.5), (43.8, 19.5), (43.8, 30.04), (42.0, 30.04)]),
    ("+5V", P, [(34.0, 19.5), (29.55, 19.5), (27.05, 22.0), (27.05, 26.0)]),
    ("+5V", P, [(27.05, 26.0), (27.05, 26.545), (25.5, 28.095), (24.475, 28.095)]),
    ("+5V", P, [(19.525, 31.905), (18.2, 31.905), (18.2, 34.413), (15.5, 34.413)]),
    ("+5V", P, [(15.5, 34.413), (13.0, 34.413), (13.0, 22.0), (27.05, 22.0)]),
    ("/THR", S, [(15.5, 27.45), (15.5, 29.087)]),
    ("/THR", S, [(15.5, 29.087), (15.5, 29.365), (19.525, 29.365)]),
    ("/THR", S, [(19.525, 29.365), (21.0, 29.365), (22.27, 30.635), (24.475, 30.635)]),
    ("/DIS", S, [(15.5, 32.587), (15.5, 30.913)]),
    ("/DIS", S, [(15.5, 30.913), (15.5, 30.635), (19.525, 30.635)]),
    ("/CV", S, [(18.05, 25.5), (18.05, 26.62), (19.525, 28.095)]),
    ("/OUT", S, [(24.475, 29.365), (29.087, 29.365)]),
    ("/OUT", S, [(29.087, 29.365), (29.087, 27.5), (42.0, 27.5)]),
    ("/LED_A", S, [(30.913, 29.365), (33.562, 29.365)]),
    ("/PWR_LED", S, [(39.413, 13.0), (39.413, 14.5), (37.562, 16.351), (37.562, 17.0)]),
    ("GND", P, [(23.85, 13.2), (21.6, 13.2)]),
    ("GND", P, [(34.0, 14.55), (34.0, 12.6)]),
    ("GND", P, [(39.438, 17.0), (41.0, 17.0)]),
    ("GND", P, [(28.95, 26.0), (30.6, 26.0)]),
    ("GND", P, [(24.475, 31.905), (26.3, 31.905)]),
    ("GND", P, [(15.5, 25.55), (15.5, 23.6)]),
    ("GND", P, [(19.95, 25.5), (21.6, 25.5)]),
    ("GND", P, [(35.438, 29.365), (35.438, 31.3)]),
]
VIAS = [(21.6, 13.2), (34.0, 12.6), (41.0, 17.0), (30.6, 26.0), (26.3, 31.905), (15.5, 23.6), (21.6, 25.5), (35.438, 31.3)]
PLANE = [{"x_mm": 10, "y_mm": 10}, {"x_mm": 45, "y_mm": 10}, {"x_mm": 45, "y_mm": 37}, {"x_mm": 10, "y_mm": 37}]


def route(agent, width_of):
    doc = str(agent.documents / "layout-practice.kicad_pcb")
    agent.run("footprint", "list", "--doc", doc, "--fields", "id,pads")
    agent.run("board", "check", "--doc", doc)
    tracks = agent.workspace / "tracks.json"
    tracks.write_text(json.dumps({"items": [
        {"net": net, "layer": "F.Cu", "width": f"{width_of(net, width)}mm",
         "points": [{"x_mm": x, "y_mm": y} for x, y in points]} for net, width, points in TRACKS]}), encoding="utf-8")
    confirm(agent, "track", "create", "--doc", doc, "--input", str(tracks))
    vias = agent.workspace / "vias.json"
    vias.write_text(json.dumps({"items": [{"net": "GND", "x": f"{x}mm", "y": f"{y}mm"} for x, y in VIAS]}), encoding="utf-8")
    confirm(agent, "via", "create", "--doc", doc, "--input", str(vias))
    confirm(agent, "zone", "create", "--doc", doc, "--net", "GND", "--layer", "B.Cu", "--points", json.dumps(PLANE))
    agent.run("board", "check", "--doc", doc)


def right(agent, turn):
    route(agent, lambda net, width: width)
    agent.say("Changed: every net routed on F.Cu, GND vias to a GND plane on B.Cu, power tracks 0.5 mm. "
              "Saved: yes. Verified: yes; board check reports no errors and no unconnected items.")


def wrong_thin_power(agent, turn):
    route(agent, lambda net, width: 0.25 if net in ("/VIN", "+5V") else width)
    agent.say("Changed: every net routed. Saved: yes. Verified: yes.")
