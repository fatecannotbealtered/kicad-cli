"""Independent, strict saved-scene consumers; no product parser or writer imports."""

import copy
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

REPORT_NORMALIZATION_POLICY = {
    "ignored_report_metadata": ["date"],
    "baseline_expected_description_rename": {
        "from": "/SIG",
        "to": "/DATA",
        "paths": [
            "sheets[].violations[].description",
            "sheets[].violations[].items[].description",
            "violations[].description",
            "violations[].items[].description",
            "unconnected_items[].description",
            "unconnected_items[].items[].description",
        ],
        "boundary": "neither adjacent character may be a word character or / . : + ~ { } [ ] -",
        "paired_wrapper": "[/SIG] -> [/DATA] when the wrapper has the same exterior boundaries",
        "direction": "baseline description to expected A spelling only",
    },
    "value_description_mapping": "none",
    "all_other_metadata_findings_ignores": "exact equality including order and multiplicity",
    "baseline_and_A_schematic_parity": "must both be empty before normalization",
}
SIG_DESCRIPTION_TOKEN = re.compile(r"(?<![\w/.:+~{}\[\]-])/SIG(?![\w/.:+~{}\[\]-])")
SIG_DESCRIPTION_WRAPPER = re.compile(r"(?<![\w/.:+~{}\[\]-])\[/SIG\](?![\w/.:+~{}\[\]-])")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sexp(text):
    """Retain every atom/string and child order, ignoring whitespace only."""
    token = re.compile(r'\s*("(?:\\.|[^"\\])*"|[()]|[^\s()]+)')
    tokens, at = [], 0
    while at < len(text):
        match = token.match(text, at)
        if not match:
            if text[at:].strip():
                raise ValueError("Invalid S-expression token")
            break
        tokens.append(match.group(1))
        at = match.end()
    stack, roots = [], []
    for item in tokens:
        if item == "(":
            node = []
            (stack[-1] if stack else roots).append(node)
            stack.append(node)
        elif item == ")":
            if not stack:
                raise ValueError("Unexpected S-expression close")
            stack.pop()
        elif stack:
            stack[-1].append(item)
        else:
            raise ValueError("Atom outside S-expression")
    if stack or len(roots) != 1:
        raise ValueError("Incomplete S-expression")
    return roots[0]


def children(node, key):
    return [row for row in node if isinstance(row, list) and row and row[0] == key]


def one(node, key):
    found = children(node, key)
    if len(found) != 1:
        raise ValueError("Expected exactly one " + key)
    return found[0]


def quoted(value):
    return json.dumps(value, ensure_ascii=False)


def uuid_node(node, key, identity):
    found = [row for row in children(node, key) if one(row, "uuid")[1] == quoted(identity)]
    if len(found) != 1:
        raise ValueError("Expected exact object UUID: " + identity)
    return found[0]


def property_node(node, name):
    found = [row for row in children(node, "property") if row[1] == quoted(name)]
    if len(found) != 1:
        raise ValueError("Expected exact property " + name)
    return found[0]


def replace_atom(node, index, old, new):
    if node[index] != old:
        raise ValueError("Frozen allowed-change precondition differs")
    node[index] = new


def compare_complete_sexp(baseline, changed, scene, kind):
    """Reverse only explicitly frozen changes; compare every remaining token."""
    old = sexp(Path(baseline).read_text(encoding="utf-8"))
    new = sexp(Path(changed).read_text(encoding="utf-8"))
    changes = []
    if scene == "A" and kind == "schematic":
        r1 = uuid_node(new, "symbol", "43ddee60-4186-5f8a-92d6-1101a2bbfaed")
        replace_atom(property_node(r1, "Value"), 2, '"22k"', '"10k"')
        r3 = uuid_node(new, "symbol", "b5407fe9-9c1d-52cf-b36c-1cd92aca1a53")
        replace_atom(one(r3, "dnp"), 1, "yes", "no")
        mpn = property_node(r3, "MPN")
        expected = sexp(
            '(property "MPN" "RC0805" (at 76.2 50.8 0) (hide yes) '
            "(effects (font (size 1.27 1.27))))"
        )
        if mpn != expected:
            raise ValueError("R3 MPN exact structure differs")
        r3.remove(mpn)
        for identity in (
            "1dd854bc-3b7b-52a0-9b38-40d03916a0c3",
            "d00eccee-d248-50aa-85a8-33ac17ee0bb1",
            "e9620ce7-4cfd-54ae-8abc-730d8ed69a3e",
        ):
            label = uuid_node(new, "label", identity)
            replace_atom(label, 1, '"DATA"', '"SIG"')
            if identity == "e9620ce7-4cfd-54ae-8abc-730d8ed69a3e":
                at = one(label, "at")
                if at != ["at", "121.92", "40.64", "180"]:
                    raise ValueError("Only declared label retained-anchor turn allowed")
                at[3] = "0"
                justify = one(one(label, "effects"), "justify")
                if justify != ["justify", "right", "bottom"]:
                    raise ValueError("Declared label alignment differs")
                justify[1] = "left"
        changes = [
            "R1.Value",
            "R3.DNP/MPN",
            "three SIG labels to DATA",
            "one declared retained-anchor label turn",
        ]
    elif scene == "A" and kind == "board":
        r1 = uuid_node(new, "footprint", "2d4aa815-63ab-5983-950d-99c5287f15a2")
        replace_atom(property_node(r1, "Value"), 2, '"22k"', '"10k"')
        r3 = uuid_node(new, "footprint", "be4843af-792b-5035-beb6-fc3f475e06a3")
        attr = one(r3, "attr")
        if attr != ["attr", "smd", "dnp"]:
            raise ValueError("R3 footprint attribute differs")
        attr.remove("dnp")
        mpn = property_node(r3, "MPN")
        expected = sexp(
            '(property "MPN" "RC0805" (at 0 0 0) (layer "F.Fab") (hide yes) '
            '(uuid "069cc47a-c44b-50b0-be44-7f4f69426b6b") '
            "(effects (font (size 1.27 1.27))))"
        )
        if mpn != expected:
            raise ValueError("Native MPN exact structure differs")
        r3.remove(mpn)
        allowed = {
            "b6399d66-3ccf-5183-bd56-8dee1659878f",
            "db64a8d6-b47b-57d0-ab0b-52be970367fc",
            "83ed57e3-78f7-5e55-a335-850177d80cb6",
            "4b209b2e-134d-5ded-a039-51cc9692dac5",
            "1ecbcd17-799b-58b7-a8c6-e4c92fb01cf5",
            "c0d58ae9-1cb6-5aa1-9933-f997aebfb572",
            "f510283f-ec3c-5db2-9e68-f71324b70ad5",
            "03188d0f-2ccb-53b9-9f15-34e991404fc0",
        }
        seen = set()

        def walk(node):
            for child in node:
                if not isinstance(child, list):
                    continue
                if child[0] in {"pad", "segment", "via"}:
                    identity = json.loads(one(child, "uuid")[1])
                    if identity in allowed:
                        replace_atom(one(child, "net"), 1, '"/DATA"', '"/SIG"')
                        seen.add(identity)
                walk(child)

        walk(new)
        if seen != allowed:
            raise ValueError("Exact pad/copper rename object set differs")
        changes = ["R1.Value", "R3.DNP/MPN", "exact three pads/three segments/two vias net rename"]
    elif scene == "B" and kind == "schematic":
        mapping = {
            "8edd0e74-5e17-5d83-b37b-5fccc8d0dfed": (
                "b77f389f-988d-5f79-a380-0731400eb763",
                ["at", "93.98", "53.34"],
            ),
            "97e2bbd5-6491-5af6-a2f7-3957763e4159": (
                "973c3667-4fd1-56f5-85e3-ee84a398816a",
                ["at", "93.98", "78.74"],
            ),
        }
        for identity, (prior, at) in mapping.items():
            nc = uuid_node(new, "no_connect", identity)
            if one(nc, "at") != at:
                raise ValueError("No-connect anchor differs")
            one(nc, "uuid")[1] = quoted(prior)
        # KiCad append order of the replaced two no-connects is immaterial;
        # everything else retains exact order and every no-connect exact content.
        old_nc, new_nc = children(old, "no_connect"), children(new, "no_connect")
        if sorted(old_nc) != sorted(new_nc):
            raise ValueError("Nonselected no-connect objects differ")
        old = [node for node in old if node not in old_nc]
        new = [node for node in new if node not in new_nc]
        changes = ["exact two no-connect identities at retained anchors"]
    elif scene == "C" and kind == "child":
        for identity in (
            "9ae20040-ee73-5751-be29-f1fcb2bb0846",
            "ac048264-8b68-5a78-9710-4325233838ef",
        ):
            replace_atom(uuid_node(new, "label", identity), 1, '"SENSE"', '"LOCAL"')
        changes = ["exact two shared child labels LOCAL to SENSE"]
    else:
        raise ValueError("Unsupported scene structure check")
    if old != new:
        raise ValueError("Protected S-expression structure changed outside exact allowed set")
    return {
        "status": "passed",
        "allowed_changes": changes,
        "remaining_all_tokens": "equal",
        "baseline_sha256": sha(baseline),
        "changed_sha256": sha(changed),
    }


def xml_observe(path):
    root = ET.parse(path).getroot()
    sheets = root.findall("./design/sheet")
    if sorted(sheet.get("name") for sheet in sheets) != ["/", "/CHILD_A/", "/CHILD_B/"]:
        raise ValueError("Official XML does not contain root and both real child instances")
    parts = {}
    for item in root.findall("./components/comp"):
        ref = item.get("ref")
        if not ref or ref in parts:
            raise ValueError("Duplicate/missing official component identity")

        # Preserve every component XML attribute, child and nested value.
        def canonical(node):
            text = node.text or ""
            if len(node) and not text.strip():
                text = ""
            return [
                node.tag,
                sorted(node.attrib.items()),
                text,
                [canonical(c) for c in node],
            ]

        parts[ref] = canonical(item)
    nets = {}
    observed_pins = set()
    for item in root.findall("./nets/net"):
        name = item.get("name")
        if name in nets:
            raise ValueError("Duplicate official network name")
        members = sorted(
            (node.get("ref"), node.get("pin"), tuple(sorted(node.attrib.items())))
            for node in item.findall("node")
        )
        for ref, pin, _ in members:
            if not ref or not pin or (ref, pin) in observed_pins:
                raise ValueError("Official pin is missing identity or duplicated across networks")
            observed_pins.add((ref, pin))
        nets[name] = members
    return parts, nets


def simple_graph(nets):
    return {name: sorted((ref, pin) for ref, pin, _ in rows) for name, rows in nets.items()}


def detached_graph(nets):
    selected, seen, result = {("U1", "3"), ("U1", "5")}, set(), {}
    for name, rows in nets.items():
        chosen = [row[:2] for row in rows if row[:2] in selected]
        if chosen:
            if len(rows) != 1 or chosen[0] in seen:
                raise ValueError("Selected pin joined or duplicated in official graph")
            seen.add(chosen[0])
        else:
            result[name] = rows
    return result


def compare_xml(baseline, changed, scene):
    parts, nets = xml_observe(baseline)
    actual_parts, actual_nets = xml_observe(changed)
    graph = simple_graph(nets)
    if graph.get("/OUT") != [("R1", "2"), ("U1", "1")]:
        raise ValueError("Original OUT survivor precondition differs")
    if graph.get("/SIG") != [("R1", "1"), ("R10", "1"), ("R2", "1")]:
        raise ValueError("Original SIG members differ")
    if scene == "A":
        expected = copy.deepcopy(parts)

        def xml_value(node, name):
            return next(row for row in node[3] if row[0] == name)

        if xml_value(expected["R1"], "value")[2] != "10k":
            raise ValueError("Original R1 value differs")
        xml_value(expected["R1"], "value")[2] = "22k"
        # Explicit field/property representations emitted by official XML only.
        for row in expected["R1"][3]:
            if row[0] == "fields":
                for field in row[3]:
                    if ("name", "Value") in field[1]:
                        field[2] = "22k"
        observed_r3 = actual_parts["R3"]
        old_fields = xml_value(expected["R3"], "fields")
        new_fields = xml_value(observed_r3, "fields")
        additional = [row for row in new_fields[3] if row not in old_fields[3]]
        if additional != [["field", [("name", "MPN")], "RC0805", []]]:
            raise ValueError("Official R3 MPN field differs")
        reversed_r3 = copy.deepcopy(observed_r3)
        xml_value(reversed_r3, "fields")[3].remove(additional[0])
        dnp = [row for row in reversed_r3[3] if row == ["property", [("name", "dnp")], "", []]]
        if len(dnp) != 1:
            raise ValueError("Official R3 DNP property differs")
        reversed_r3[3].remove(dnp[0])
        optional_mpn_property = ["property", [("name", "MPN"), ("value", "RC0805")], "", []]
        if optional_mpn_property in reversed_r3[3]:
            reversed_r3[3].remove(optional_mpn_property)
        if reversed_r3 != parts["R3"]:
            raise ValueError("Other R3 component XML changed")
        expected["R3"] = copy.deepcopy(observed_r3)
        expected_nets = {"/DATA" if name == "/SIG" else name: rows for name, rows in nets.items()}
        # Node metadata has no net name and remains exact.
    elif scene == "B":
        expected = parts
        if (
            detached_graph(actual_nets) != detached_graph(nets)
            or simple_graph(actual_nets).get("/OUT") != graph["/OUT"]
        ):
            raise ValueError("Official detached-pin/OUT survivor relationship differs")
        expected_nets = actual_nets
    elif scene == "C":
        expected = parts
        required = {
            "/CHILD_A/LOCAL": [("C10", "1"), ("R10", "2")],
            "/CHILD_B/LOCAL": [("C20", "1"), ("R20", "2")],
        }
        if any(graph.get(name) != members for name, members in required.items()):
            raise ValueError("Original shared-child instance members differ")
        expected_nets = {
            name[:-5] + "SENSE" if name in required else name: rows for name, rows in nets.items()
        }
    else:
        raise ValueError("Unsupported XML scene")
    if actual_parts != expected or actual_nets != expected_nets:
        raise ValueError("Official XML exact component/member comparison failed")
    return {
        "status": "passed",
        "components": len(actual_parts),
        "nets": len(actual_nets),
        "all_component_and_member_metadata": "checked",
        "PCB_parity_credit": scene == "A",
    }


def normalize_findings(report, *, rename_sig=False, changes=None):
    """Apply the predeclared date and exact finding-description spelling policy."""
    value = copy.deepcopy(report)
    timestamp = value.pop("date", None)
    if timestamp is not None and not isinstance(timestamp, str):
        raise ValueError("Report date must be text")
    if rename_sig:
        records = [] if changes is None else changes

        def description(node, path):
            if "description" in node:
                old = node["description"]
                if not isinstance(old, str):
                    raise ValueError("Native finding description must be text")
                new = SIG_DESCRIPTION_TOKEN.sub(
                    "/DATA", SIG_DESCRIPTION_WRAPPER.sub("[/DATA]", old)
                )
                if old != new:
                    records.append({"path": path + "/description", "before": old, "after": new})
                    node["description"] = new

        def findings(rows, path):
            if not isinstance(rows, list):
                raise ValueError("Native findings must be an array")
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    raise ValueError("Native finding must be an object")
                at = path + "/" + str(index)
                description(row, at)
                for item_index, item in enumerate(row.get("items", [])):
                    if not isinstance(item, dict):
                        raise ValueError("Native finding item must be an object")
                    description(item, at + "/items/" + str(item_index))

        for key in ("violations", "unconnected_items"):
            if key in value:
                findings(value[key], "/" + key)
        for index, sheet in enumerate(value.get("sheets", [])):
            findings(sheet["violations"], "/sheets/" + str(index) + "/violations")
    return value


def compare_findings(baseline, changed, *, drc=False):
    old, new = json.loads(Path(baseline).read_bytes()), json.loads(Path(changed).read_bytes())
    if drc:
        if not all(
            isinstance(row.get(key), list)
            for row in (old, new)
            for key in ("violations", "unconnected_items", "schematic_parity")
        ):
            raise ValueError("Incomplete native DRC report")
        if old["schematic_parity"] or new["schematic_parity"]:
            raise ValueError("Main saved scene schematic parity is not empty")
    elif not all(isinstance(row.get("sheets"), list) for row in (old, new)):
        raise ValueError("Incomplete native ERC report")
    changes = []
    if normalize_findings(old, rename_sig=True, changes=changes) != normalize_findings(new):
        raise ValueError(
            "Native complete findings/ignore/source report differs beyond frozen policy"
        )
    return {
        "status": "passed",
        "normalization_policy": REPORT_NORMALIZATION_POLICY,
        "expected_baseline_description_changes": changes,
        "raw_baseline_sha256": sha(baseline),
        "raw_changed_sha256": sha(changed),
        "all_findings_and_ignores": "equal",
        "clean_claimed": False,
    }
