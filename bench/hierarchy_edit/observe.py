"""Read a saved synthetic hierarchy, official XML and native physical pads.

Run with the explicitly selected KiCad Python. No product imports or setters.
"""

import argparse
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schematic", type=Path, required=True)
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--xml", type=Path, required=True)
    parser.add_argument("--readers", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Use a new observation path")
    sys.path.insert(0, str(args.readers.resolve()))
    import pcbnew
    from sexpr_reader import child, children, parse

    netlist = ET.parse(args.xml).getroot()
    xml_parts = []
    for component in netlist.findall("./components/comp"):
        xml_parts.append(
            {
                "ref": component.get("ref"),
                "value": component.findtext("value"),
                "footprint": component.findtext("footprint"),
                "sheet": component.find("sheetpath").attrib,
                "symbol_uuids": component.findtext("tstamps", "").split(),
            }
        )
    pin_nets = {}
    nets = []
    for net in netlist.findall("./nets/net"):
        members = [dict(node.attrib) for node in net.findall("node")]
        nets.append({"name": net.get("name"), "members": members})
        for member in members:
            key = (member["ref"], member["pin"])
            if key in pin_nets:
                raise ValueError("Official XML has a physical pin in multiple nets")
            pin_nets[key] = net.get("name")
    root_path = args.schematic.resolve()
    project = root_path.parent
    root_uuid = child(parse(root_path.read_text(encoding="utf-8")), "uuid")[1]
    units, sheets, sources, raw_schematics = [], [], {}, {}

    def visit(path, uuid_path, name_path, active):
        path = path.resolve()
        if not path.is_relative_to(project) or path in active:
            raise ValueError("Only this owned, acyclic synthetic hierarchy is supported")
        sources[path.relative_to(project).as_posix()] = sha(path)
        tree = parse(path.read_text(encoding="utf-8"))
        raw_schematics[path.relative_to(project).as_posix()] = tree
        sheets.append({"file": path.name, "path": uuid_path, "name_path": name_path})
        libraries = {n[1]: n for n in children(child(tree, "lib_symbols"), "symbol")}
        for symbol in children(tree, "symbol"):
            instance = child(symbol, "instances")
            matches = [
                p
                for owner in children(instance, "project")
                if owner[1] == root_path.stem
                for p in children(owner, "path")
                if p[1] == uuid_path
            ]
            if len(matches) != 1:
                raise ValueError("Each unit needs exactly one frozen project instance")
            reference = child(matches[0], "reference")[1]
            unit = int(child(symbol, "unit")[1])
            library_id = child(symbol, "lib_id")[1]
            library = libraries[library_id]
            pins = []
            for drawing in children(library, "symbol"):
                _, unit_text, style = drawing[1].rsplit("_", 2)
                if int(unit_text) not in (0, unit) or int(style) not in (0, 1):
                    continue
                for pin in children(drawing, "pin"):
                    number = child(pin, "number")[1]
                    pins.append(
                        {
                            "number": number,
                            "alias": child(pin, "name")[1],
                            "electrical_type": pin[1],
                            "net": pin_nets.get((reference, number)),
                        }
                    )
            fields = {p[1]: p[2] for p in children(symbol, "property")}
            units.append(
                {
                    "ref": reference,
                    "unit": unit,
                    "uuid": child(symbol, "uuid")[1],
                    "instance_path": uuid_path,
                    "full_path": uuid_path + "/" + child(symbol, "uuid")[1],
                    "file": path.name,
                    "library_id": library_id,
                    "power_symbol": child(library, "power") is not None,
                    "stored_fields": fields,
                    "position_mm": [float(x) for x in child(symbol, "at")[1:3]],
                    "rotation_deg": float(child(symbol, "at")[3]),
                    "pins": sorted(pins, key=lambda p: p["number"]),
                    "raw_symbol": symbol,
                }
            )
        for sheet in children(tree, "sheet"):
            properties = {p[1]: p[2] for p in children(sheet, "property")}
            visit(
                path.parent / properties["Sheetfile"],
                uuid_path + "/" + child(sheet, "uuid")[1],
                name_path + properties["Sheetname"] + "/",
                active | {path},
            )

    visit(root_path, "/" + root_uuid, "/", set())
    board = pcbnew.LoadBoard(str(args.board.resolve()))

    def xy(v):
        return [v.x, v.y]

    def uid(item):
        return item.m_Uuid.AsString()

    footprints = []
    for fp in board.GetFootprints():
        footprints.append(
            {
                "ref": fp.GetReference(),
                "value": fp.GetValue(),
                "uuid": uid(fp),
                "source_path": fp.GetPath().AsString(),
                "footprint": str(fp.GetFPID().GetLibNickname())
                + ":"
                + str(fp.GetFPID().GetLibItemName()),
                "position_nm": xy(fp.GetPosition()),
                "rotation_deg": fp.GetOrientationDegrees(),
                "layer": board.GetLayerName(fp.GetLayer()),
                "locked": bool(fp.IsLocked()),
                "attributes": fp.GetAttributes(),
                "pads": sorted(
                    [
                        {
                            "uuid": uid(p),
                            "number": p.GetNumber(),
                            "net": p.GetNetname(),
                            "position_nm": xy(p.GetPosition()),
                            "size_nm": xy(p.GetSize()),
                            "drill_nm": xy(p.GetDrillSize()),
                            "shape": int(p.GetShape()),
                            "rotation_deg": p.GetOrientationDegrees(),
                            "layers": [
                                board.GetLayerName(layer) for layer in p.GetLayerSet().Seq()
                            ],
                        }
                        for p in fp.Pads()
                    ],
                    key=lambda p: (p["number"], p["uuid"]),
                ),
            }
        )
    tracks = []
    for track in board.GetTracks():
        via = isinstance(track, pcbnew.PCB_VIA)
        item = {
            "uuid": uid(track),
            "type": track.GetClass(),
            "net": track.GetNetname(),
            "start_nm": xy(track.GetStart()),
            "end_nm": xy(track.GetEnd()),
            "width_nm": track.GetWidth(pcbnew.F_Cu) if via else track.GetWidth(),
            "layer": board.GetLayerName(track.GetLayer()),
            "locked": bool(track.IsLocked()),
        }
        if via:
            item["drill_nm"] = track.GetDrillValue()
        tracks.append(item)
    raw_board = parse(args.board.read_text(encoding="utf-8"))
    result = {
        "native_version": pcbnew.GetBuildVersion(),
        "reader_sha256": sha(__file__),
        "schematic_files": sources,
        "raw_schematics": raw_schematics,
        "board_sha256": sha(args.board),
        "xml_sha256": sha(args.xml),
        "units": sorted(units, key=lambda u: (u["instance_path"], u["ref"], u["unit"])),
        "sheets": sheets,
        "xml_components": xml_parts,
        "xml_nets": nets,
        "footprints": sorted(footprints, key=lambda f: f["ref"]),
        "tracks": sorted(tracks, key=lambda t: t["uuid"]),
        "raw_footprints": children(raw_board, "footprint"),
        "protected_board_nodes": [
            n
            for n in raw_board[1:]
            if isinstance(n, list) and n[0] not in {"footprint", "segment", "arc", "via", "net"}
        ],
        "project": json.loads(args.board.with_suffix(".kicad_pro").read_bytes()),
        "scope": "All instances/units and each physical pad in this saved synthetic fixture; "
        "no new design writes, no manufacturing qualification",
    }
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(
        json.dumps(
            {
                "observed": True,
                "sheets": len(sheets),
                "units": len(units),
                "footprints": len(footprints),
            }
        )
    )


if __name__ == "__main__":
    main()
