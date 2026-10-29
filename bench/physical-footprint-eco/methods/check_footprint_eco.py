"""Independent finite C10 0805-to-0603 physical footprint ECO acceptance.

Host stdlib only; no product/KiCad imports or subprocesses. Original baseline and
installed libraries are frozen before execution. Native process freshness, DLL
loading provenance and job termination remain the Root's external obligation.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from decimal import Decimal
from pathlib import Path, PureWindowsPath

FILES = (
    "channel_a.kicad_sch",
    "channel_b.kicad_sch",
    "hier_dual_rc.kicad_pcb",
    "hier_dual_rc.kicad_prl",
    "hier_dual_rc.kicad_pro",
    "hier_dual_rc.kicad_sch",
    "hier_dual_rc.net",
)
PCB, PRO, ROOT, A, B = FILES[2], FILES[4], FILES[5], FILES[0], FILES[1]
COUNTS = {"footprints": 20, "physical_pads": 42, "tracks": 100, "vias": 12, "nets": 10}
GROUPS = {
    "+3V3": ["U1.8", "J5.1", "C1.1", "C2.1"],
    "GND": [
        "U1.4",
        "J1.2",
        "J2.2",
        "J3.2",
        "J4.2",
        "J5.2",
        "C10.2",
        "C11.2",
        "C20.2",
        "C21.2",
        "C1.2",
        "C2.2",
    ],
    "/CHANNEL_A/IN": ["J1.1", "R10.1"],
    "/CHANNEL_A/SENSE": ["U1.3", "R10.2", "C10.1", "TP1.1"],
    "/CHANNEL_A/BUF": ["U1.1", "U1.2", "R11.1"],
    "/CHANNEL_A/OUT": ["J2.1", "R11.2", "C11.1", "TP2.1"],
    "/CHANNEL_B/IN": ["J3.1", "R20.1"],
    "/CHANNEL_B/RC": ["U1.5", "R20.2", "C20.1", "TP3.1"],
    "/CHANNEL_B/BUF": ["U1.6", "U1.7", "R21.1"],
    "/CHANNEL_B/OUT": ["J4.1", "R21.2", "C21.1", "TP4.1"],
}
ENDPOINTS = {pin: net for net, pins in GROUPS.items() for pin in pins}
ERC_IGNORES = {
    "single_global_label",
    "four_way_junction",
    "simulation_model_issue",
    "footprint_filter",
}
DRC_IGNORES = {
    "missing_courtyard",
    "track_not_centered_on_via",
    "tuning_profile_track_geometries",
    "footprint_filters_mismatch",
    "footprint_type_mismatch",
}
TOKEN = re.compile(rb'\s+|;[^\r\n]*|"(?:\\.|[^"\\])*"|[()]|[^\s()";]+')


class IncompleteEvidence(ValueError):
    pass


def need(value, reason):
    if not value:
        raise ValueError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class Node(list):
    pass


class Atom:
    def __init__(self, value, start, end):
        self.raw, self.start, self.end = value, start, end

    @property
    def value(self):
        text = self.raw.decode("utf-8")
        return json.loads(text) if text.startswith('"') else text


def parse(raw):
    stack, roots, end = [], [], 0
    for match in TOKEN.finditer(raw):
        need(match.start() == end, "Unrecognized source byte")
        end, token = match.end(), match.group()
        if token.isspace() or token.startswith(b";"):
            continue
        if token == b"(":
            node = Node()
            node.start = match.start()
            (stack[-1] if stack else roots).append(node)
            stack.append(node)
        elif token == b")":
            need(bool(stack), "Unbalanced source close")
            stack.pop().end = match.end()
        else:
            need(bool(stack), "Source atom outside root")
            stack[-1].append(Atom(token, match.start(), match.end()))
    need(end == len(raw) and not stack and len(roots) == 1, "Malformed source")
    return roots[0]


def head(node):
    return node[0].value if node and isinstance(node[0], Atom) else None


def children(node, name):
    return [item for item in node if isinstance(item, list) and head(item) == name]


def one(node, name):
    rows = children(node, name)
    need(len(rows) == 1, "Missing/duplicate source " + name)
    return rows[0]


def val(node, name):
    row = one(node, name)
    need(len(row) == 2 and isinstance(row[1], Atom), "Malformed source " + name)
    return row[1].value


def prop(node, name):
    rows = [row for row in children(node, "property") if row[1].value == name]
    need(
        len(rows) == 1 and isinstance(rows[0][2], Atom),
        "Missing/duplicate property " + name,
    )
    return rows[0]


def nm(atom):
    value = Decimal(atom.value) * 1_000_000
    need(
        value.is_finite() and value == value.to_integral_value(),
        "Noninteger-nm source dimension",
    )
    return int(value)


def xy(node, name="at"):
    row = one(node, name)
    need(len(row) in {3, 4}, "Unsupported source coordinate expression")
    return [nm(item) for item in row[1:3]]


def zero_angle(node):
    at = one(node, "at")
    need(
        len(at) == 3 or Decimal(at[3].value) == 0, "Unsupported nonzero object rotation"
    )


def index(rows, key="uuid"):
    result = {}
    for row in rows:
        ident = row[key]
        need(
            isinstance(ident, str) and ident and ident not in result,
            "Missing/duplicate " + key,
        )
        result[ident] = row
    return result


def named(tree, kind, ref):
    rows = [
        node for node in children(tree, kind) if prop(node, "Reference")[2].value == ref
    ]
    need(len(rows) == 1, "Missing/duplicate saved component " + ref)
    return rows[0]


def erase(raw, spans):
    need(len(set(spans)) == len(spans), "Repeated authorized source span")
    for start, end in sorted(spans, reverse=True):
        raw = raw[:start] + b"<ALLOWED_ATOM>" + raw[end:]
    return raw


def node_bytes(raw, node):
    return raw[node.start:node.end]


def plain(node, omit=()):
    if isinstance(node, Atom):
        return node.value
    return tuple(plain(item, omit) for item in node
                 if not (isinstance(item, list) and head(item) in omit))


def decode_snapshot(snapshot):
    need(snapshot["schema"] == "physical-footprint-eco-frozen-inputs/v1", "Wrong snapshot schema")
    def decode(row):
        raw = base64.b64decode(row["base64"], validate=True)
        need(sha(raw) == row["sha256"], "Frozen snapshot digest differs")
        return raw
    files = {name: decode(row) for name, row in snapshot["files"].items()}
    need(set(files) == set(FILES), "Frozen baseline must contain exact seven files")
    libraries = {name: decode(row) for name, row in snapshot["libraries"].items()}
    need(set(libraries) == {"C_0805_2012Metric", "C_0603_1608Metric"}, "Wrong library resources")
    return files, libraries


OLD_FPID = "Capacitor_SMD:C_0805_2012Metric"
NEW_FPID = "Capacitor_SMD:C_0603_1608Metric"
C10_UUID = "d6f728ba-64cf-5264-85b4-c9296995b208"
LIBRARY_HEADS = {"descr", "tags", "attr", "duplicate_pad_numbers_are_jumpers",
                 "fp_line", "fp_rect", "fp_text", "pad", "embedded_fonts", "model"}


def unique_board_uuids(tree):
    seen = set()
    def visit(node):
        for item in node:
            if isinstance(item, list):
                if head(item) == "uuid":
                    ident = item[1].value
                    need(re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", ident)
                         and ident not in seen, "Duplicate/invalid board object UUID")
                    seen.add(ident)
                visit(item)
    visit(tree)
    return seen


def library_projection(fp):
    # Net labels and newly allocated object UUIDs are instance bindings, not library geometry.
    return Counter(plain(node, ("uuid", "net")) for node in fp
                   if isinstance(node, list) and head(node) in LIBRARY_HEADS)


def validate_c10(raw, library_raw, fpid):
    tree, library = parse(raw), parse(library_raw)
    fp = named(tree, "footprint", "C10")
    need(fp[1].value == fpid and val(fp, "uuid") == C10_UUID, "C10 footprint identity differs")
    need(library_projection(fp) == library_projection(library),
         "C10 physical pad/artwork/model geometry differs from frozen installed library")
    pads = children(fp, "pad")
    for child in fp:
        if isinstance(child, list) and head(child) in {"property", "fp_line", "fp_rect", "fp_text", "pad"}:
            val(child, "uuid")
    need(len(pads) == 2 and {p[1].value for p in pads} == {"1", "2"}, "C10 pad numbering differs")
    for pad in pads:
        number = pad[1].value
        need(val(pad, "net") == ENDPOINTS["C10." + number], "C10 logical pad net differs")
    need(xy(fp) == [23_000_000, 22_000_000] and val(fp, "layer") == "F.Cu", "C10 pose differs")
    zero_angle(fp)
    return fp


def protect(before, after, libraries):
    need(set(before) == set(after) == set(FILES), "Exactly seven engineering files required")
    bfp = validate_c10(before[PCB], libraries["C_0805_2012Metric"], OLD_FPID)
    afp = validate_c10(after[PCB], libraries["C_0603_1608Metric"], NEW_FPID)
    unique_board_uuids(parse(after[PCB]))
    # Whole-byte projection protects all 19 other footprints, 112 copper objects,
    # board frame/settings and all other board text. Only C10's subtree is replaced.
    need(erase(before[PCB], [(bfp.start, bfp.end)]) == erase(after[PCB], [(afp.start, afp.end)]),
         "Protected board bytes changed outside C10 library replacement")
    # Owner identity/path/pose/lock remain exact. Replacement can reorder children.
    def protected_c10(raw, fp):
        return Counter(node_bytes(raw, x) if isinstance(x, list) else x.raw for x in fp[2:]
                       if not (isinstance(x, list) and head(x) in LIBRARY_HEADS | {"property"}))
    need(protected_c10(before[PCB], bfp) == protected_c10(after[PCB], afp),
         "Protected C10 identity, path, pose or lock bytes changed")
    def canonical(node):
        atoms = tuple(x.value for x in node if isinstance(x, Atom))
        nested = sorted((canonical(x) for x in node if isinstance(x, list) and head(x) != "uuid"), key=repr)
        return atoms, tuple(nested)
    bprops = index([{ "name": p[1].value, "node": p } for p in children(bfp, "property")], "name")
    aprops = index([{ "name": p[1].value, "node": p } for p in children(afp, "property")], "name")
    need(set(bprops) == set(aprops), "C10 property inventory changed")
    for name in bprops:
        old, new = bprops[name]["node"], aprops[name]["node"]
        if name in {"Reference", "Value"}:
            need(canonical(old) == canonical(new), "C10 Ref/Value text or geometry/style changed")
        elif name == "KiLib_Generator":
            need(canonical(new) == canonical(prop(parse(libraries["C_0603_1608Metric"]), name)),
                 "C10 library property differs")
        else:
            need(canonical(old) == canonical(new), "C10 non-UUID property content or geometry/style changed")
    for name in FILES:
        if name == PCB:
            continue
        if name == A:
            def projection(raw, expected):
                atom = prop(named(parse(raw), "symbol", "C10"), "Footprint")[2]
                need(atom.value == expected and atom.raw == json.dumps(expected).encode("ascii"),
                     "Wrong literal schematic C10 Footprint atom")
                return erase(raw, [(atom.start, atom.end)])
            need(projection(before[name], OLD_FPID) == projection(after[name], NEW_FPID),
                 "Protected SCH bytes changed beyond C10 Footprint atom")
        else:
            need(before[name] == after[name], "Protected engineering bytes changed: " + name)
    return [A, PCB]

def source_board(raw):
    tree = parse(raw)
    need(
        head(tree) == "kicad_pcb"
        and not children(tree, "zone")
        and not children(tree, "arc"),
        "Unsupported source board/zone/arc",
    )
    fps, pads, copper = {}, {}, {}
    for node in children(tree, "footprint"):
        ref, value = prop(node, "Reference")[2].value, prop(node, "Value")[2].value
        uid = val(node, "uuid")
        need(
            ref not in fps and uid not in {f["uuid"] for f in fps.values()},
            "Duplicate footprint ref/UUID",
        )
        zero_angle(node)
        origin = xy(node)
        fp = {
            "uuid": uid,
            "ref": ref,
            "value": value,
            "position_nm": origin,
            "rotation_deg": 0,
            "layer": val(node, "layer"),
            "fpid": node[1].value,
            "path": val(node, "path"),
            "pads": [],
        }
        for pad in children(node, "pad"):
            number, kind, shape = [item.value for item in pad[1:4]]
            need(
                kind == "smd" and shape in {"rect", "roundrect"},
                "Unsupported source pad geometry",
            )
            zero_angle(pad)
            ident = val(pad, "uuid")
            need(ident not in pads, "Duplicate pad UUID")
            local = xy(pad)
            netrow = one(pad, "net")
            need(len(netrow) == 2, "Unsupported legacy/malformed pad net")
            row = {
                "uuid": ident,
                "number": number,
                "ref": ref,
                "net": netrow[1].value,
                "position_nm": [a + b for a, b in zip(origin, local)],
                "size_nm": xy(pad, "size"),
                "layers": sorted(item.value for item in one(pad, "layers")[1:]),
                "shape_name": shape,
                "roundrect_ratio": Decimal(val(pad, "roundrect_rratio"))
                if shape == "roundrect"
                else None,
            }
            pads[ident] = row
            fp["pads"].append(ident)
        fps[ref] = fp
    for kind in ("segment", "via"):
        for node in children(tree, kind):
            uid = val(node, "uuid")
            need(uid not in copper, "Duplicate copper UUID")
            netrow = one(node, "net")
            need(len(netrow) == 2, "Unsupported legacy/malformed copper net")
            row = {
                "uuid": uid,
                "kind": "PCB_TRACK" if kind == "segment" else "PCB_VIA",
                "net": netrow[1].value,
                "start_nm": xy(node, "start" if kind == "segment" else "at"),
                "end_nm": xy(node, "end" if kind == "segment" else "at"),
                "width_nm": nm(one(node, "width" if kind == "segment" else "size")[1]),
            }
            if kind == "segment":
                row["layer"] = val(node, "layer")
            else:
                row["drill_nm"] = nm(one(node, "drill")[1])
                row["layers"] = [item.value for item in one(node, "layers")[1:]]
            copper[uid] = row
    need(
        len(fps) == 20
        and len(pads) == 42
        and Counter(r["kind"] for r in copper.values())
        == {"PCB_TRACK": 100, "PCB_VIA": 12},
        "Literal 20/42/100/12 inventory differs",
    )
    endpoints = {p["ref"] + "." + p["number"]: p["net"] for p in pads.values()}
    need(
        len(endpoints) == 42 and endpoints == ENDPOINTS,
        "Literal all42 physical pin/net matrix differs",
    )
    need(
        fps["TP1"]["value"] == "SENSE"
        and fps["R10"]["value"] == "12k"
        and fps["R20"]["value"] == "10k",
        "Required TP1/R10/R20 PCB values differ",
    )
    return fps, pads, copper


def startup(record):
    start = record.get("startup")
    if not isinstance(start, dict):
        raise IncompleteEvidence(
            "Native startup proof missing; never synthesize startup=true"
        )
    need(
        start["no_site"] is True and start["dont_write_bytecode"] is True,
        "Native startup flags differ",
    )
    for key in (
        "sitecustomize_or_usercustomize_loaded",
        "Documents_KiCad_thirdparty_consulted",
        "normal_site_addsitedir_executed",
    ):
        need(start[key] is False, "Native consulted undeclared startup code")
    root = PureWindowsPath(start["native_bin_dll_directory"])
    paths = list(map(PureWindowsPath, start["actual_sys_path"]))
    need(
        root == PureWindowsPath("D:/software/kicad/home/bin")
        and paths[2] == root / "python311.zip"
        and len(paths) == 4
        and paths[:2] == [root / "Lib", root / "DLLs"]
        and paths[3] == root / "Lib/site-packages"
        and paths[2].parent == root
        and re.fullmatch(r"python\d+\.zip", paths[2].name),
        "Native search path unbound",
    )
    need(
        PureWindowsPath(start["explicit_site_packages"]) == paths[3],
        "Native explicit site-packages differs",
    )
    modules = index(start["actual_module_sources"], "module")
    need(
        not {"sitecustomize", "usercustomize"} & modules.keys(),
        "Startup customization loaded",
    )
    for key, row in modules.items():
        if "path" in row:
            need(
                key == "__main__" or root in PureWindowsPath(row["path"]).parents,
                "Undeclared Native module path",
            )
            need(
                bool(row.get("sha256") or row.get("archive_sha256")),
                "Native module bytes unbound",
            )
        elif "provider" in row:
            need(
                root in PureWindowsPath(row["provider"]).parents
                and bool(row.get("provider_sha256")),
                "Native module provider unbound",
            )
        else:
            need(
                row["origin"] in {"built-in", "frozen"},
                "Native source-free module unbound",
            )
    need(
        modules["pcbnew"]["sha256"] == record["pcbnew_wrapper_sha256"]
        and PureWindowsPath(modules["pcbnew"]["path"])
        == root / "Lib/site-packages/pcbnew.py",
        "Native wrapper differs",
    )
    need(
        modules["__main__"]["sha256"] == record["reader_sha256"]
        and PureWindowsPath(modules["__main__"]["path"]).is_absolute(),
        "Native reader source differs",
    )


def native_check(record, files, scene, bindings):
    startup(record)
    need(record["reader_sha256"] == bindings["reader_sha256"]
         and record["bundle_sha256"] == bindings["bundle_sha256"], "Native method/requirement binding differs")
    if record.get("incomplete_reasons"):
        raise IncompleteEvidence("Native geometry observation incomplete")
    need(
        record["schema"] == "physical-footprint-eco-native-read/v1"
        and record["native_version"] == "10.0.6",
        "Wrong Native schema/version",
    )
    need(
        record["input_sha256"] == sha(files[PCB])
        and record["project_sha256"] == sha(files[PRO]),
        "Native input/project digest differs",
    )
    need(
        record["input_unchanged"] is True and record["project_unchanged"] is True,
        "Native observation changed input",
    )
    need(
        record["copper_layer_count"] == 2 and not record["zones"],
        "Unsupported Native layers/zones",
    )
    fps, pads, copper = scene
    native_fps = index(record["footprints"], "ref")
    need(set(native_fps) == set(fps), "Native footprint inventory differs")
    native_pads = {}
    for ref, fp in fps.items():
        observed = native_fps[ref]
        for key in (
            "uuid",
            "value",
            "position_nm",
            "rotation_deg",
            "layer",
            "fpid",
            "path",
        ):
            need(
                observed[key] == fp[key],
                "Native/source footprint differs: " + ref + ":" + key,
            )
        need(
            observed["fields"]["Reference"] == ref
            and observed["fields"]["Value"] == fp["value"],
            "Native fields differ",
        )
        rows = index(observed["pads"])
        need(set(rows) == set(fp["pads"]), "Native/source pad UUID set differs")
        for uid, pad in rows.items():
            need(uid not in native_pads, "Repeated Native pad UUID")
            source = pads[uid]
            native_pads[uid] = {**pad, "ref": ref}
            for key in ("number", "net", "position_nm", "size_nm"):
                need(
                    pad[key] == source[key],
                    "Native/source pad differs: " + ref + ":" + key,
                )
            need(
                sorted(pad["layers"]) == source["layers"]
                and pad["rotation_deg"] == 0
                and pad["drill_nm"] == [0, 0]
                and pad["offset_nm"] == [0, 0],
                "Unsupported Native pad geometry",
            )
            const = record["native_constants"]
            need(
                pad["attribute"] == const["PAD_ATTRIB_SMD"]
                and pad["shape"]
                == const[
                    "PAD_SHAPE_RECTANGLE"
                    if source["shape_name"] == "rect"
                    else "PAD_SHAPE_ROUNDRECT"
                ],
                "Native/source pad shape/type differs",
            )
            if source["shape_name"] == "roundrect":
                rr = pad["roundrect"]
                need(
                    rr["status"] == "observed"
                    and Decimal(str(rr["ratio"])) == source["roundrect_ratio"]
                    and rr["corner_radius_nm"]
                    == int(
                        min(pad["size_nm"]) * source["roundrect_ratio"] + Decimal("0.5")
                    ),
                    "Native pad roundrect differs",
                )
    native_copper = index(record["tracks"])
    need(
        set(native_copper) == set(copper), "Native/source copper UUID inventory differs"
    )
    for uid, source in copper.items():
        row = native_copper[uid]
        need(
            all(row[key] == value for key, value in source.items()),
            "Native/source copper geometry/net differs: " + uid,
        )
        if row["kind"] == "PCB_TRACK":
            need(
                row["width_nm"] == 200_000
                and row["start_nm"] != row["end_nm"]
                and row["layer"] in {"F.Cu", "B.Cu"},
                "Unexpected track dimensions/layer",
            )
        else:
            native_via_check(row, record["native_constants"]["VIATYPE_THROUGH"])
    effective = index(record["effective_netclasses"], "net")
    need(set(effective) == set(GROUPS), "Native effective rule net inventory differs")
    rule = {
        "track_width_nm": 200_000,
        "clearance_nm": 200_000,
        "via_diameter_nm": 600_000,
        "via_drill_nm": 300_000,
    }
    need(
        all(
            all(row[key] == value for key, value in rule.items())
            for row in effective.values()
        ),
        "Native effective route dimensions differ",
    )
    return graph_check(record["physical_connectivity"], native_pads, native_copper)


def native_via_check(row, through_constant):
    need(row["through_via"] is True and row["via_type"] == through_constant
         and row["width_nm"] == 600_000 and row["drill_nm"] == 300_000
         and row["layers"] == ["F.Cu", "B.Cu"]
         and row["width_by_layer_nm"] == {"F.Cu": 600_000, "B.Cu": 600_000},
         "Unsupported or changed ordinary via")


def graph_check(conn, pads, copper):
    if (
        conn["external_direct_items"]
        or conn["asymmetric_direct_edges"]
        or conn["global_incomplete_reasons"]
        or conn.get("incomplete_reasons")
    ):
        raise IncompleteEvidence("Native direct contact graph incomplete")
    objects = {**pads, **copper}
    need(len(objects) == len(pads) + len(copper), "Pad/copper UUID collision")
    adjacency = conn["direct_native_adjacency"]
    need(set(adjacency) == set(objects), "Native graph omits/adds physical object")
    pairs = set()
    for left, neighbors in adjacency.items():
        need(
            isinstance(neighbors, list)
            and len(neighbors) == len(set(neighbors))
            and left not in neighbors
            and set(neighbors) <= objects.keys(),
            "Malformed Native adjacency",
        )
        for right in neighbors:
            need(
                left in adjacency[right]
                and objects[left]["net"] == objects[right]["net"] != "",
                "Asymmetric/cross-net/empty-net Native contact",
            )
            pairs.add(tuple(sorted((left, right))))
    witnessed = set()
    for edge in conn["global_contact_witnesses"]:
        left, right = edge["from_uuid"], edge["to_uuid"]
        pair = tuple(sorted((left, right)))
        need(
            pair in pairs and pair not in witnessed and edge["added_clearance_nm"] == 0,
            "Invalid/duplicate Native shape contact",
        )
        witnessed.add(pair)

        def layers(item):
            return (
                {item["layer"]}
                if item.get("kind") == "PCB_TRACK"
                else set(item["layers"])
            )

        common = layers(objects[left]) & layers(objects[right]) & {"F.Cu", "B.Cu"}
        rows = edge["native_effective_copper_shape_contacts"]
        need(
            rows
            and len(rows) == len({row["layer"] for row in rows})
            and {row["layer"] for row in rows} == common
            and any(row["copper_shapes_intersect"] is True for row in rows),
            "Actual Native zero-clearance copper contact unproved",
        )
    need(witnessed == pairs, "Missing Native shape-contact witnesses")
    components = index(conn["pad_components"])
    need(
        set(components) == set(pads) and len(pads) == 42,
        "All42 Native pad components required",
    )
    results = {}
    for uid, pad in pads.items():
        seen, todo = {uid}, [uid]
        while todo:
            for other in adjacency[todo.pop()]:
                if other not in seen:
                    seen.add(other)
                    todo.append(other)
        need(
            all(objects[item]["net"] == pad["net"] for item in seen),
            "Actual cross-net component",
        )
        row = components[uid]
        need(
            (row["ref"], row["number"], row["net"])
            == (pad["ref"], pad["number"], pad["net"]),
            "Native pad component identity differs",
        )
        for field in ("same_net_physical_cluster", "ignore_nets_physical_cluster"):
            need(
                len(row[field]) == len(set(row[field])) and set(row[field]) == seen,
                "Native physical cluster disagrees with direct graph",
            )
        for field in (
            "same_net_connected_pad_uuids",
            "ignore_nets_connected_pad_uuids",
        ):
            need(
                len(row[field]) == len(set(row[field]))
                and set(row[field]) == seen & pads.keys(),
                "Native connected-pad cluster disagrees with direct graph",
            )
        expected_ids = {key for key, obj in objects.items() if obj["net"] == pad["net"]}
        need(
            seen == expected_ids,
            "Disconnected/floating Native physical objects on net " + pad["net"],
        )
        results[pad["net"]] = {"pads": len(seen & pads.keys()), "objects": len(seen)}
    need(
        set(results) == set(GROUPS)
        and all(row["net"] in GROUPS for row in objects.values()),
        "Missing/extra Native network",
    )
    return {"checked_pads": 42, "nets": results, "contacts": len(witnessed)}


def xml_check(raw, fps, expected_source, files):
    root = ET.fromstring(raw)
    need(root.tag == "export" and root.findtext("design/tool") == "Eeschema 10.0.6", "Wrong official XML producer")
    need(PureWindowsPath(root.findtext("design/source")) == PureWindowsPath(expected_source),
         "Official XML source path differs")
    source_root = parse(files[ROOT])
    sheet_paths = {"/": "/"}
    sheet_files = {ROOT: ("/", "/")}
    for sheet in children(source_root, "sheet"):
        name = "/" + prop(sheet, "Sheetname")[2].value + "/"
        path = "/" + val(sheet, "uuid") + "/"
        filename = prop(sheet, "Sheetfile")[2].value
        need(name not in sheet_paths and filename not in sheet_files, "Duplicate source hierarchy")
        sheet_paths[name] = path
        sheet_files[filename] = (name, path)
    sheets = root.findall("design/sheet")
    need(len(sheets) == 3 and {s.get("name"): s.get("tstamps") for s in sheets} == sheet_paths,
         "Official XML full sheet hierarchy differs")
    symbols = {}
    for filename, (names, path) in sheet_files.items():
        for symbol in children(parse(files[filename]), "symbol"):
            ref = prop(symbol, "Reference")[2].value
            if ref.startswith("#"):
                continue
            entry = symbols.setdefault(ref, {"names": names, "path": path, "uuids": set()})
            need((entry["names"], entry["path"]) == (names, path), "Package split across sheet instances unsupported")
            entry["uuids"].add(val(symbol, "uuid"))
    comps = index([{**row.attrib, "node": row} for row in root.findall("components/comp")], "ref")
    need(set(comps) == set(fps) == set(symbols), "Official XML component inventory differs")
    for ref, row in comps.items():
        node, fp, expected = row["node"], fps[ref], symbols[ref]
        need(node.findtext("value") == fp["value"] and node.findtext("footprint") == fp["fpid"],
             "Official XML/PCB value or footprint differs: " + ref)
        sheetpath = node.find("sheetpath")
        stamps = (node.findtext("tstamps") or "").split()
        need(sheetpath is not None and sheetpath.get("names") == expected["names"]
             and sheetpath.get("tstamps") == expected["path"]
             and len(stamps) == len(set(stamps)) and set(stamps) == expected["uuids"]
             and fp["path"] in {expected["path"] + stamp for stamp in stamps},
             "Official XML complete package instance path/units differ: " + ref)
        pins = [p.get("num") for p in node.findall("units/unit/pins/pin")]
        expected_pins = {endpoint.rsplit(".", 1)[1] for endpoint in ENDPOINTS if endpoint.rsplit(".", 1)[0] == ref}
        need(len(pins) == len(set(pins)) and set(pins) == expected_pins,
             "Official XML omits/duplicates a physical unit pin including power pins: " + ref)
        units = node.findall("units/unit")
        unit_map = {unit.get("name"): {p.get("num") for p in unit.findall("pins/pin")} for unit in units}
        expected_units = ({"A": {"1", "2", "3"}, "B": {"5", "6", "7"}, "C": {"8", "4"}}
                          if ref == "U1" else {"A": expected_pins})
        need(len(unit_map) == len(units) and unit_map == expected_units,
             "Official XML unit physical pin allocation differs: " + ref)
    nets, endpoints = {}, {}
    for node in root.findall("nets/net"):
        name = node.get("name")
        need(name not in nets, "Duplicate official XML net")
        pins = [row.get("ref") + "." + row.get("pin") for row in node.findall("node")]
        need(len(pins) == len(set(pins)), "Repeated official XML pin")
        nets[name] = set(pins)
        for pin in pins:
            need(pin not in endpoints, "Official XML pin on multiple nets")
            endpoints[pin] = name
    need(nets == {net: set(pins) for net, pins in GROUPS.items()} and endpoints == ENDPOINTS,
         "Official XML all42-pin/10-net hierarchy differs")
    return {"components": len(comps), "physical_pins": len(endpoints), "nets": len(nets), "sheets": 3,
            "full_package_and_sheet_paths_checked": True}

def official_check(erc, drc, files):
    reports = {}
    for name, doc, ignores in (("ERC", erc, ERC_IGNORES), ("DRC", drc, DRC_IGNORES)):
        need(
            doc["$schema"] == "https://schemas.kicad.org/" + name.lower() + ".v1.json"
            and doc["kicad_version"] == "10.0.6",
            "Wrong official report schema/version",
        )
        keys = [row["key"] for row in doc["ignored_checks"]]
        need(
            len(keys) == len(ignores) and set(keys) == ignores,
            name + " ignored checks differ",
        )
        severities = doc["included_severities"]
        need(
            len(severities) == 3
            and set(severities) == {"error", "warning", "exclusion"},
            name + " severity/exclusion observation incomplete",
        )
        need(
            doc["source"] == (ROOT if name == "ERC" else PCB), name + " source differs"
        )
        if name == "ERC":
            sheets = doc["sheets"]
            need(
                len(sheets) == 3
                and {row["path"] for row in sheets}
                == {"/", "/CHANNEL_A/", "/CHANNEL_B/"},
                "Official ERC page coverage differs",
            )
            root_uuid = val(parse(files[ROOT]), "uuid")
            sch_sheets = {
                prop(row, "Sheetname")[2].value: val(row, "uuid")
                for row in children(parse(files[ROOT]), "sheet")
            }
            for row in sheets:
                expected = (
                    "/"
                    + root_uuid
                    + (
                        "/" + sch_sheets[row["path"].strip("/")]
                        if row["path"] != "/"
                        else ""
                    )
                )
                need(
                    row["uuid_path"] == expected, "Official ERC sheet instance differs"
                )
            findings = [v for sheet in sheets for v in sheet["violations"]]
            extra = {}
        else:
            findings = doc["violations"]
            extra = {
                key: len(doc[key]) for key in ("unconnected_items", "schematic_parity")
            }
            need(
                all(count == 0 for count in extra.values()),
                "Official DRC unconnected/parity findings remain",
            )
        severity_counts = dict(Counter(row["severity"] for row in findings))
        need(
            not findings,
            "Official " + name + " findings remain, including warnings/exclusions",
        )
        reports[name] = {
            "ignored_checks": keys,
            "included_severities": severities,
            "findings": len(findings),
            "severity_counts": severity_counts,
            **extra,
        }
    project = json.loads(files[PRO])
    settings = project["board"]["design_settings"]
    need(settings["drc_exclusions"] == [], "Saved DRC exclusions changed/nonempty")
    ignored = {
        key
        for key, severity in settings["rule_severities"].items()
        if severity == "ignore"
    }
    need(
        ignored == DRC_IGNORES,
        "Saved DRC severity policy differs from observed ignores",
    )
    reports["saved_policy"] = {
        "drc_exclusions": [],
        "drc_rule_severities": settings["rule_severities"],
        "erc_policy": "Observed in supplied official ERC report; not inferred from missing project fields",
    }
    return reports


def accept(snapshot, physical_intent, after, native, xml_raw, erc, drc, expected_source, bindings):
    before, libraries = decode_snapshot(snapshot)
    need(physical_intent["schema"] == "literal-current-native-physical-pin-intent42/v1"
         and physical_intent["physical_pin_count"] == 42 and physical_intent["distinct_net_count"] == 10
         and physical_intent["physical_pin_intent"] == ENDPOINTS,
         "Frozen foreground physical intent differs from finite all42 contract")
    changes = protect(before, after, libraries)
    scene = source_board(after[PCB])
    graph = native_check(native, after, scene, bindings)
    xml = xml_check(xml_raw, scene[0], expected_source, after)
    official = official_check(erc, drc, after)
    return {
        "schema": "physical-footprint-eco-finite-acceptance/v1", "status": "passed",
        "expected_counts": COUNTS, "changed_files": changes,
        "change": {"reference": "C10", "from": OLD_FPID, "to": NEW_FPID},
        "protected_other19_footprints_and_all112_copper_and_board_bytes": True,
        "protected_C10_owner_path_pose_and_ref_value_text_geometry": True,
        "actual_C10_frozen_new_library_pad_artwork_model_geometry": True,
        "all42_direct_native_pad_components": graph, "official_xml": xml, "official_reports": official,
        "native_dependency_identity": {key: native[key] for key in
                                       ("native_version", "reader_sha256", "pcbnew_wrapper_sha256", "bundle_sha256")},
        "before_sha256": {name: sha(raw) for name, raw in before.items()},
        "after_sha256": {name: sha(raw) for name, raw in after.items()},
        "limits": [
            "Report freshness, invocation, process/DLL identity and whole Job termination require external Root binding.",
            "Four ERC and five DRC ignored categories are unobserved; zero findings is not all-rule approval.",
            "No new Native call, hole-clearance certification, image review, analog or manufacturing approval is performed.",
            "Historical .net is protected archival input, never current network truth."]}

def read_seven(directory):
    base = Path(directory)
    selected = {
        path.name
        for path in base.iterdir()
        if path.is_file()
        and path.suffix.lower()
        in {
            ".kicad_sch",
            ".kicad_pcb",
            ".kicad_pro",
            ".kicad_prl",
            ".net",
            ".kicad_dru",
            ".kicad_wks",
        }
    }
    need(
        selected == set(FILES),
        "Missing/unexpected engineering file in selected directory",
    )
    return {name: (base / name).read_bytes() for name in FILES}


def report_output(path, input_paths):
    need(
        path.suffix.lower() == ".json" and path.resolve().suffix.lower() == ".json",
        "Report output must be a JSON file, never an engineering file",
    )
    for source in input_paths:
        need(path.resolve() != source.resolve(), "Report output would overwrite input")
        if path.exists() and source.exists():
            need(not path.samefile(source), "Report output aliases input")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("current-dir", "native", "xml", "erc", "drc", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    requirement_dir = Path(__file__).parent / "requirements"
    parser.add_argument("--baseline-inputs", type=Path, default=requirement_dir / "BASELINE-INPUTS.json")
    parser.add_argument("--physical-intent", type=Path, default=requirement_dir / "PHYSICAL-PIN-INTENT42.json")
    parser.add_argument("--reader", type=Path, default=Path(__file__).with_name("saved_board_native_read.py"))
    args = parser.parse_args()
    report_paths = {name: getattr(args, name) for name in ("native", "xml", "erc", "drc")}
    requirement_paths = {"baseline": args.baseline_inputs, "physical_intent": args.physical_intent}
    report_output(args.output, [*report_paths.values(), *requirement_paths.values(), args.reader,
                                *[args.current_dir / name for name in FILES]])
    raw = {name: path.read_bytes() for name, path in report_paths.items()}
    requirements = {name: path.read_bytes() for name, path in requirement_paths.items()}
    bindings = {"reader_sha256": sha(args.reader.read_bytes()),
                "bundle_sha256": {name: sha(value) for name, value in requirements.items()}}
    try:
        result = accept(json.loads(requirements["baseline"]), json.loads(requirements["physical_intent"]),
                        read_seven(args.current_dir), json.loads(raw["native"]), raw["xml"],
                        json.loads(raw["erc"]), json.loads(raw["drc"]),
                        str((args.current_dir / ROOT).resolve()), bindings)
        code = 0
    except IncompleteEvidence as exc:
        result, code = {"status": "incomplete", "reason": str(exc)}, 2
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, ArithmeticError, ET.ParseError) as exc:
        result, code = {"status": "rejected", "reason": type(exc).__name__ + ": " + str(exc)}, 1
    result.update(schema="physical-footprint-eco-finite-acceptance/v1",
                  checker_sha256=sha(Path(__file__).read_bytes()),
                  input_reports_sha256={name: sha(value) for name, value in raw.items()}, **bindings)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
