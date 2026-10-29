"""Independent literal intent check over an official KiCad XML netlist.

Only XML and frozen intent are consumed. No product parser, writer or SDK.
Pin occurrences are lists/Counter values so duplicate or substituted pins fail.
"""

import copy
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from frozen_intent_view import expand


def compare(document, intent, root_qualification=False):
    named_count = len(intent["nets"])
    intent, isolated = expand(intent)
    failures = []
    parts = document.findall("./components/comp")
    actual_refs = [part.attrib["ref"] for part in parts]
    expected_refs = [part["ref"] for part in intent["components"]]
    if Counter(actual_refs) != Counter(expected_refs):
        failures.append(
            {
                "check": "component_reference_multiset",
                "actual": actual_refs,
                "expected": expected_refs,
            }
        )
    expected_parts = {part["ref"]: part for part in intent["components"]}
    actual_parts = []
    for part in parts:
        reference = part.attrib["ref"]
        library = part.find("libsource")
        observed = {
            "ref": reference,
            "value": part.findtext("value"),
            "footprint": part.findtext("footprint"),
            "symbol": f"{library.attrib.get('lib')}:{library.attrib.get('part')}"
            if library is not None
            else None,
            "uuid": part.findtext("tstamps"),
            "sheetpath": part.find("sheetpath").attrib
            if part.find("sheetpath") is not None
            else None,
        }
        actual_parts.append(observed)
        if reference in expected_parts:
            for field in ("value", "footprint", "symbol"):
                if observed[field] != expected_parts[reference][field]:
                    failures.append(
                        {
                            "check": field,
                            "ref": reference,
                            "expected": expected_parts[reference][field],
                            "actual": observed[field],
                        }
                    )
            expected = expected_parts[reference]
            if "in_bom" in expected:
                excluded = part.find("property[@name='exclude_from_bom']") is not None
                if excluded == expected["in_bom"]:
                    failures.append({"check": "bom_flag", "ref": reference})
    nets = document.findall("./nets/net")

    def logical_name(name):
        if root_qualification and name.startswith("/") and name.count("/") == 1:
            return name[1:]
        return name

    names = [logical_name(net.attrib["name"]) for net in nets]
    if Counter(names) != Counter(intent["nets"].keys()):
        failures.append(
            {"check": "net_name_multiset", "expected": list(intent["nets"]), "actual": names}
        )
    actual_nets = []
    occurrences = []
    for net in nets:
        name = logical_name(net.attrib["name"])
        nodes = [f"{node.attrib['ref']}.{node.attrib['pin']}" for node in net.findall("node")]
        for node in net.findall("node"):
            identity = f"{node.attrib['ref']}.{node.attrib['pin']}"
            if identity in isolated and "no_connect" not in node.get("pintype", "").split("+"):
                failures.append({"check": "explicit_no_connect_marker", "pin": identity})
        occurrences.extend((name, pin) for pin in nodes)
        actual_nets.append({"name": name, "native_name": net.attrib["name"], "pins": nodes})
        if Counter(nodes) != Counter(intent["nets"].get(name, [])):
            failures.append(
                {
                    "check": "literal_pin_occurrences",
                    "net": name,
                    "expected": intent["nets"].get(name, []),
                    "actual": nodes,
                }
            )
    expected_occurrences = [(name, pin) for name, pins in intent["nets"].items() for pin in pins]
    if Counter(occurrences) != Counter(expected_occurrences):
        failures.append(
            {
                "check": "all_pin_occurrences",
                "missing": list((Counter(expected_occurrences) - Counter(occurrences)).elements()),
                "extra": list((Counter(occurrences) - Counter(expected_occurrences)).elements()),
            }
        )
    return {
        "passed": not failures,
        "component_count": len(parts),
        "net_count": len(nets),
        "intended_named_net_count": named_count,
        "isolated_pin_count": len(isolated),
        "pin_occurrence_count": len(occurrences),
        "parts": actual_parts,
        "nets": actual_nets,
        "failures": failures,
    }


def main():
    xml_path, intent_path = map(Path, sys.argv[1:3])
    document = ET.parse(xml_path).getroot()
    intent = json.loads(intent_path.read_text(encoding="utf-8-sig"))
    strict_result = compare(document, intent)
    root_only = all(
        part.find("sheetpath") is not None and part.find("sheetpath").get("names") == "/"
        for part in document.findall("./components/comp")
    )
    result = compare(document, intent, root_qualification=root_only)
    result["strict_native_name_comparison"] = {
        "passed": strict_result["passed"],
        "failures": strict_result["failures"],
    }
    result["root_qualification_policy"] = {
        "root_only_verified": root_only,
        "mapping": (
            "Only remove one leading / for a single-root-sheet local net; never "
            "take arbitrary hierarchy basename or rename a net."
        ),
    }
    controls = {}
    same_net = copy.deepcopy(document)
    candidates = {part["ref"] for part in intent["components"] if len(part["pins"]) > 1}
    first = next(
        node for node in same_net.findall("./nets/net/node") if node.get("ref") in candidates
    )
    original_pin = first.get("pin")
    valid_pins = next(
        part["pins"] for part in intent["components"] if part["ref"] == first.get("ref")
    )
    replacement_pin = next(pin for pin in valid_pins if pin != original_pin)
    first.set("pin", replacement_pin)
    controls["same_net_wrong_pin_rejected"] = not compare(same_net, intent, root_only)["passed"]
    result["same_net_wrong_pin_control"] = {
        "ref": first.get("ref"),
        "from_pin": original_pin,
        "to_existing_valid_pin": replacement_pin,
        "net_name_unchanged": True,
        "component_and_net_counts_unchanged": True,
    }
    disconnected = copy.deepcopy(document)
    net = disconnected.find("./nets/net")
    net.remove(net.find("node"))
    controls["missing_pin_rejected"] = not compare(disconnected, intent, root_only)["passed"]
    duplicate = copy.deepcopy(document)
    net = duplicate.find("./nets/net")
    net.append(copy.deepcopy(net.find("node")))
    controls["duplicate_pin_occurrence_rejected"] = not compare(duplicate, intent, root_only)[
        "passed"
    ]
    result.update(
        {
            "source": "official CLI kicadxml, independent standard-library XML consumer",
            "inputs": {
                str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (xml_path, intent_path)
            },
            "negative_controls": controls,
            "negative_control_scope": (
                "In-memory XML observations only; no engineering file was modified"
            ),
        }
    )
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] and all(controls.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
