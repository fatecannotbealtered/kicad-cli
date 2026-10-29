"""Build a wired functional intent through public CLI only; native reads verify independently."""

from __future__ import annotations

import copy
import json
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from functional_support import REPO, Case, put, sha

EXAMPLE = REPO / "examples/functional-schmitt"


def page_grid(point):
    return [round(round(value / 1.27) * 1.27, 6) for value in point]


def export(t, root, fmt, label):
    out = t.root / label
    out.mkdir()
    t.write("sch", "export", "--schematic", root, "--file-format", fmt, "--out", out)
    return out / (root.stem + {"netlist": ".net", "svg": ".svg", "pdf": ".pdf"}[fmt])


def verify_xml(t, root, intent, label):
    path = t.root / (label + ".xml")
    t.native("sch", "export", "netlist", "--format", "kicadxml", "-o", path, root)
    xml = ET.parse(path)
    actual = {
        n.get("name"): sorted(f"{p.get('ref')}.{p.get('pin')}" for p in n.findall("node"))
        for n in xml.findall("./nets/net")
    }
    for name, nodes in intent["nets"].items():
        assert actual.get(name) == sorted(nodes), (name, actual.get(name), nodes)
    unexpected = [v for k, v in actual.items() if k not in intent["nets"]]
    assert sorted(unexpected) == [["U1.1"]], unexpected
    values = {c.get("ref"): c.findtext("value") for c in xml.findall("./components/comp")}
    assert values["R1"] in {"10k", "22k"}
    erc = t.root / (label + "-erc.json")
    t.native("sch", "erc", "--format", "json", "--severity-all", "-o", erc, root)
    d = json.loads(erc.read_bytes())
    violations = [v for s in d["sheets"] for v in s["violations"]]
    put(
        t.root / (label + "-net-comparison.json"),
        {
            "actual": actual,
            "expected": intent["nets"],
            "values": values,
            "erc_violations": violations,
            "ignored_checks": d.get("ignored_checks"),
        },
    )
    assert not violations, violations
    return values


def prepare_board(t, intent, *, label="initial"):
    project = t.root / "project"
    root = project / "schmitt.kicad_sch"
    verify_xml(t, root, intent, "wired-saved-" + label)
    netlist = export(t, root, "netlist", "netlist-" + label)
    export(t, root, "svg", "sch-svg-" + label)
    export(t, root, "pdf", "sch-pdf-" + label)
    pcb = project / "schmitt.kicad_pcb"
    t.write(
        "board", "from-netlist", "--netlist", netlist, "--out", pcb, "--initialize-empty-project"
    )
    moves = ";".join(f"{ref}:{xy[0]},{xy[1]}" for ref, xy in intent["layout_mm"].items())
    t.write("board", "footprint", "move", "--board", pcb, "--moves", moves)
    t.cli("project", "context", "--board", pcb)
    put(
        t.root / "PCB-READY.json",
        {
            "pcb": str(pcb),
            "schematic": str(root),
            "intent_sha256": sha(t.root / "INTENT-BEFORE-DESIGN.json"),
            "calls": t.index,
        },
    )


def main(directory, binary=None):
    resumed = Path(directory).is_dir()
    t = Case.attach(directory, binary) if resumed else Case(directory, binary)
    intent = json.loads((EXAMPLE / "intent.json").read_bytes())
    put(t.root / "INTENT-BEFORE-DESIGN.json", intent)
    shutil.copyfile(EXAMPLE / "source-facts.json", t.root / "source-facts.json")
    project = t.root / "project"
    project.mkdir(exist_ok=resumed)
    if not resumed:
        shutil.copyfile(t.root / "source-facts.json", project / "source-facts.json")
    blank = t.root / "schmitt.json"
    put(blank, {"mode": "blank", "title": "3V3 Schmitt input - bias and bypass"})
    if not resumed:
        t.write("sch", "create", "--spec", blank, "--out", project)
    root = project / "schmitt.kicad_sch"
    (project / "LocalLogic.pretty").mkdir(exist_ok=resumed)
    base = json.loads((REPO / "examples/library/sn74lvc1g14/library.json").read_bytes())
    base["revision"] = "functional-1"
    base["sources"][0].update(file="source-facts.json", sha256=sha(t.root / "source-facts.json"))
    specs = [base]
    roles = [
        ("VccPort", "power_out", [0, -5.08], 90),
        ("GroundPort", "power_out", [0, 5.08], 270),
        ("InputPort", "output", [5.08, 0], 180),
        ("OutputPort", "input", [-5.08, 0], 0),
    ]
    for name, kind, at, angle in roles:
        s = copy.deepcopy(base)
        s["mapping"] = {"1": "1"}
        s["symbol"] = {
            "name": name,
            "reference": "TP",
            "body_mm": [5.08, 5.08],
            "pins": [
                {
                    "number": "1",
                    "name": {
                        "VccPort": "VCC",
                        "GroundPort": "GND",
                        "InputPort": "IN",
                        "OutputPort": "OUT",
                    }[name],
                    "type": kind,
                    "at_mm": at,
                    "angle_deg": angle,
                    "length_mm": 2.54,
                }
            ],
        }
        s["footprint"] = {
            "name": "TestPad",
            "body_mm": [0.8, 0.8],
            "courtyard_mm": 0.25,
            "pads": [{"number": "1", "at_mm": [0, 0], "size_mm": [1, 1]}],
        }
        s["sources"][0].update(
            url="https://www.kicad.org/libraries/",
            revision="declared project contact geometry 2026-10-27",
            locator="source-facts.json test_pad and external-boundary roles",
        )
        specs.append(s)
    for index, spec in enumerate(specs):
        if resumed:
            continue
        names = [
            "LocalLogic.kicad_sym",
            "LocalLogic.pretty/" + spec["footprint"]["name"] + ".kicad_mod",
            "sym-lib-table",
            "fp-lib-table",
            "LocalLogic.provenance.json",
        ]
        spec["baseline"] = {
            n: sha(project / n) if (project / n).is_file() else "absent" for n in names
        }
        file = project / f"library-input-{index}.json"
        put(file, spec)
        t.write("library", "build", "--project", project / "schmitt.kicad_pro", "--spec", file)
    t.cli(
        "library", "inspect", "--project", project / "schmitt.kicad_pro", "--library", "LocalLogic"
    )
    objects = t.cli("sch", "objects", "--schematic", root)
    page = objects["extensions"]["pages"][0]["instance_path"]
    placement = [
        ("U1", "LocalLogic:SN74LVC1G14DBV", [100, 70]),
        ("R1", "Device:R", [70, 87.78]),
        ("C1", "Device:C", [125.4, 55]),
        ("TP1", "LocalLogic:VccPort", [100, 34.92]),
        ("TP2", "LocalLogic:GroundPort", [100, 107.54]),
        ("TP3", "LocalLogic:InputPort", [37.46, 70]),
        ("TP4", "LocalLogic:OutputPort", [162.54, 70]),
    ]
    actions = []
    members = {name: [] for name in intent["nets"]}
    pins = {}
    field_positions = {
        "U1": [[110.49, 63.5], [116.84, 78.74]],
        "R1": [[74.93, 85.09], [74.93, 88.9]],
        "C1": [[130.81, 53.34], [130.81, 57.15]],
        "TP1": [[100.33, 29.21], [107.95, 34.29]],
        "TP2": [[107.95, 105.41], [107.95, 109.22]],
        "TP3": [[38.1, 64.77], [38.1, 76.2]],
        "TP4": [[162.56, 64.77], [162.56, 76.2]],
    }
    for ref, symbol, at in placement:
        data = t.cli("sch", "symbols", "--schematic", root, "--symbol", symbol)["items"][0]
        actions.append(
            {
                "op": "place_symbol",
                "id": ref,
                "page": page,
                "symbol": symbol,
                "unit": 1,
                "ref": ref,
                "at_mm": page_grid(at),
                "value": intent["parts"][ref]["value"],
                "fields": {
                    name: {"at_mm": xy, "angle_deg": 0}
                    for name, xy in zip(("Reference", "Value"), field_positions[ref], strict=True)
                },
            }
        )
        for pin in data["pins"]:
            pins[ref + "." + pin["number"]] = {
                "new": ref,
                "instance_path": page,
                "pin_ordinal": pin["pin_ordinal"],
            }
    for name, nodes in intent["nets"].items():
        members[name] += [pins[node] for node in nodes]

    contacts = []

    def add(name, kind, **fields):
        alias = f"{kind}{len(actions)}"
        if "points" in fields:
            fields["points"] = [
                page_grid(p) if isinstance(p, list) else p for p in fields["points"]
            ]
        if isinstance(fields.get("at"), list):
            fields["at"] = page_grid(fields["at"])
        points = fields.get("points", [fields.get("at")])
        keys = {json.dumps(point, sort_keys=True) for point in points}
        exact = [
            entry["ref"] for entry in contacts if entry["net"] == name and keys & entry["keys"]
        ]
        if exact:
            fields["contact_targets"] = exact
        actions.append({"op": "create", "id": alias, "page": page, "kind": kind, **fields})
        contacts.append({"net": name, "keys": keys, "ref": {"new": alias, "instance_path": page}})
        if kind != "junction":
            members[name].append({"new": alias, "instance_path": page})

    def p(node):
        return {"pin": pins[node]}

    def wire(name, points):
        for a, b in zip(points, points[1:], strict=False):
            add(name, "wire", points=[a, b])

    wire("+3V3", [p("TP1.1"), [100, 45], p("U1.5")])
    wire("+3V3", [[100, 45], [125.4, 45], p("C1.1")])
    wire("GND", [p("U1.3"), [100, 96], p("TP2.1")])
    wire("GND", [p("R1.2"), [70, 96], [100, 96]])
    wire("GND", [p("C1.2"), [180, 58.81], [180, 96], [100, 96]])
    wire("INPUT", [p("TP3.1"), [70, 70], p("U1.2")])
    wire("INPUT", [[70, 70], p("R1.1")])
    wire("OUTPUT", [p("U1.4"), [140, 70], p("TP4.1")])
    for name, at in [
        ("+3V3", [100, 45]),
        ("GND", [100, 96]),
        ("INPUT", [70, 70]),
        ("OUTPUT", [140, 70]),
    ]:
        add(name, "global_label", at=at, text=name, angle_deg=0)
    for name, at in [("+3V3", [100, 45]), ("GND", [100, 96]), ("INPUT", [70, 70])]:
        add(name, "junction", at=at)
    actions.append(
        {"op": "create", "id": "ncU1", "page": page, "kind": "no_connect", "at": p("U1.1")}
    )
    expected = [
        {
            "members": nodes,
            "explicit_names": [{"kind": "global_label", "name": name}],
            "nc_members": [],
        }
        for name, nodes in members.items()
    ]
    expected.append(
        {
            "members": [pins["U1.1"], {"new": "ncU1", "instance_path": page}],
            "explicit_names": [],
            "nc_members": [pins["U1.1"]],
        }
    )
    changes = t.root / "construct.json"
    put(
        changes,
        {
            "changes": [
                {
                    "op": "circuit",
                    "snapshot": {
                        "root": objects["extensions"]["root"],
                        "snapshot": objects["snapshot"]["revision"],
                    },
                    "affected_instances": [page],
                    "actions": actions,
                    "expected": {"components": expected},
                }
            ]
        },
    )
    t.write("sch", "edit", "--schematic", root, "--changes", changes)
    fields = t.root / "footprints.json"
    put(
        fields,
        {
            "changes": [
                {
                    "op": "set",
                    "ref": "R1",
                    "footprint": "Resistor_SMD:R_0603_1608Metric",
                    "fields": {"MPN": intent["parts"]["R1"]["mpn"]},
                },
                {
                    "op": "set",
                    "ref": "C1",
                    "footprint": "Capacitor_SMD:C_0603_1608Metric",
                    "fields": {"MPN": intent["parts"]["C1"]["mpn"]},
                },
                {"op": "set", "ref": "U1", "fields": {"MPN": intent["parts"]["U1"]["mpn"]}},
            ]
        },
    )
    t.write("sch", "edit", "--schematic", root, "--changes", fields)
    t.cli("sch", "objects", "--schematic", root)
    prepare_board(t, intent)
    print(t.root)


if __name__ == "__main__":
    main(*sys.argv[1:])
