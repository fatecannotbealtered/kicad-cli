"""Finite direct-two-child creation intent, independent of generated documents.

This is a declaration validator and deterministic identity plan, not a netlist
reader. Expected full names follow declared sheet scopes before any file exists.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter

IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
LOCAL_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_+-]*\Z")
BASENAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\Z")
RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{index}" for prefix in ("COM", "LPT") for index in range(1, 10)
}


def safe_basename(value):
    return (
        isinstance(value, str)
        and bool(BASENAME.fullmatch(value))
        and not value.endswith((".", " "))
        and ".." not in value
        and value.split(".", 1)[0].upper() not in RESERVED
    )


def hierarchy_problems(spec, stem=None):
    """Return precise declaration problems without consulting symbol libraries."""
    if not isinstance(spec, dict) or "hierarchy" not in spec:
        return []
    problems = []

    def problem(field, message):
        problems.append({"field": field, "problem": message})

    hierarchy = spec["hierarchy"]
    if not isinstance(hierarchy, dict):
        return [{"field": "/hierarchy", "problem": "hierarchy must be an object"}]
    if hierarchy.get("schema") != "direct-two-child/v1":
        problem("/hierarchy/schema", "only direct-two-child/v1 is supported")
    try:
        namespace = hierarchy.get("namespace_uuid")
        if not isinstance(namespace, str) or str(uuid.UUID(namespace)) != namespace:
            raise ValueError
    except (ValueError, AttributeError):
        problem("/hierarchy/namespace_uuid", "a canonical lowercase UUID is required")
    root = hierarchy.get("root")
    if not isinstance(root, dict) or root.get("id") != "root":
        problem("/hierarchy/root", "the root id must be literal root")
    children = hierarchy.get("children")
    if (
        not isinstance(children, list)
        or len(children) != 2
        or any(not isinstance(row, dict) for row in children)
    ):
        problem("/hierarchy/children", "exactly two different direct child objects are required")
        return problems
    if stem is not None and not safe_basename(stem):
        problem("/hierarchy/root", "the spec filename stem must be a safe Windows basename")
    titles = [("/hierarchy/root/title", root.get("title") if isinstance(root, dict) else None)]
    ids, names, files = ["root"], [], []
    for index, child in enumerate(children):
        where = f"/hierarchy/children/{index}"
        for key in ("id", "name"):
            value = child.get(key)
            if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
                problem(where + "/" + key, "a safe ASCII identifier is required")
            elif value.casefold() == "root":
                problem(where + "/" + key, "root is reserved for the root page")
        ids.append(child.get("id"))
        names.append(child.get("name"))
        filename = child.get("file")
        if not safe_basename(filename) or not filename.endswith(".kicad_sch"):
            problem(where + "/file", "one plain safe .kicad_sch basename is required")
        files.append(filename)
        titles.append((where + "/title", child.get("title")))
    for label, values in (("id", ids), ("name", names), ("file", files)):
        folded = [v.casefold() for v in values if isinstance(v, str)]
        if len(folded) != len(set(folded)):
            problem("/hierarchy/children", f"duplicate or case-ambiguous {label}")
    if isinstance(stem, str) and any(
        isinstance(name, str)
        and name.casefold()
        in {(stem + suffix).casefold() for suffix in (".kicad_sch", ".net", ".kicad_pro")}
        for name in files
    ):
        problem("/hierarchy/children", "child output conflicts with a root/project/netlist name")
    for field, title in titles:
        if not isinstance(title, str) or not title.strip() or any(ord(c) < 32 for c in title):
            problem(field, "a nonempty readable single-line title is required")
    # JSON Schema rejects extra keys and malformed arrays; remain total when
    # called directly on a malformed document, and avoid interpreting strings.
    parts, nets = spec.get("parts"), spec.get("nets")
    for key, value in (("parts", parts), ("nets", nets)):
        if not isinstance(value, list):
            problem("/" + key, "an array is required")
    if not isinstance(parts, list) or not isinstance(nets, list):
        return problems
    assignments = {}
    refs = set()
    valid_ids = {value for value in ids if isinstance(value, str)}
    for index, part in enumerate(parts):
        if not isinstance(part, dict):
            problem(f"/parts/{index}", "each part must be an object")
            continue
        ref, selected = part.get("ref"), part.get("sheet_id")
        valid_page = isinstance(selected, str) and selected in valid_ids
        if not valid_page:
            problem(f"/parts/{index}/sheet_id", "every physical part requires one declared page")
        if isinstance(ref, str):
            if ref.casefold() in refs:
                problem(f"/parts/{index}/ref", "duplicate or case-ambiguous physical reference")
            refs.add(ref.casefold())
            if valid_page:
                assignments[ref] = selected
        else:
            problem(f"/parts/{index}/ref", "a string physical reference is required")
        if part.get("on_board", True) is not True or part.get("dnp", False) is not False:
            problem(f"/parts/{index}", "this hierarchy scope requires on_board=true and dnp=false")
    if any(page not in assignments.values() for page in valid_ids):
        problem("/parts", "root and both children must contain physical parts")
    net_rows = []
    for index, row in enumerate(nets):
        if not isinstance(row, dict):
            problem(f"/nets/{index}", "each net must be an object")
        else:
            net_rows.append(row)
    net_names = [row.get("name") for row in net_rows]
    if any(
        not isinstance(name, str) or not name or any(c in name for c in "[]{}\r\n\t")
        for name in net_names
    ):
        problem("/nets", "nonempty scalar network IDs without bus syntax are required")
    if len([name for name in net_names if isinstance(name, str)]) != len(
        set(name for name in net_names if isinstance(name, str))
    ):
        problem("/nets", "duplicate net IDs")
    known_nets = {name for name in net_names if isinstance(name, str)}
    shared = hierarchy.get("shared_power_nets")
    if not isinstance(shared, list) or any(not isinstance(name, str) for name in shared):
        problem("/hierarchy/shared_power_nets", "an explicit array of power-net IDs is required")
        shared = []
    if len(shared) != len(set(shared)) or set(shared) - known_nets:
        problem("/hierarchy/shared_power_nets", "shared power IDs must be unique declared nets")
    if any(name != "GND" and not name.startswith("+") for name in shared):
        problem("/hierarchy/shared_power_nets", "only GND or explicit +supply nets may be global")
    occurrences = {}
    pins = Counter()
    for row in net_rows:
        endpoints = row.get("connect")
        if not isinstance(row.get("name"), str) or not isinstance(endpoints, list):
            continue
        pages = set()
        for endpoint in endpoints:
            if not isinstance(endpoint, str) or "." not in endpoint:
                continue
            ref, pin = endpoint.split(".", 1)
            pins[(ref, pin)] += 1
            if assignments.get(ref) in valid_ids:
                pages.add(assignments[ref])
        occurrences[row["name"]] = pages
    if any(count > 1 for count in pins.values()):
        problem("/nets", "a physical REF.PIN may belong to only one declared net")
    expected_names = {name: name for name in shared}
    for index, child in enumerate(children):
        where = f"/hierarchy/children/{index}"
        child_id = child.get("id")
        if not isinstance(child_id, str):
            continue
        expected_local = {
            name for name, pages in occurrences.items() if child_id in pages and name not in shared
        }
        local = child.get("local_nets")
        if not isinstance(local, dict) or set(local) != expected_local:
            problem(where + "/local_nets", "map exactly every non-shared net used on this child")
            local = local if isinstance(local, dict) else {}
        values = list(local.values())
        if any(not isinstance(value, str) or not LOCAL_NAME.fullmatch(value) for value in values):
            problem(where + "/local_nets", "local names must be scalar safe ASCII names")
        strings = [value for value in values if isinstance(value, str)]
        if len(strings) != len(set(strings)):
            problem(where + "/local_nets", "different declared nets cannot share one local label")
        ports = child.get("ports")
        if not isinstance(ports, list) or any(not isinstance(row, dict) for row in ports):
            problem(where + "/ports", "an array of explicit port objects is required")
            ports = []
        port_nets, port_names = [], []
        for port_index, port in enumerate(ports):
            direction = port.get("direction")
            if not isinstance(direction, str) or direction not in {
                "input",
                "output",
                "bidirectional",
            }:
                problem(where + f"/ports/{port_index}/direction", "unsupported port direction")
            net, name = port.get("net"), port.get("name")
            if not isinstance(net, str) or not isinstance(name, str):
                problem(where + f"/ports/{port_index}", "string name and net are required")
                continue
            port_nets.append(net)
            port_names.append(name)
            if name != local.get(net):
                problem(
                    where + f"/ports/{port_index}",
                    "port name must match the child's local net name",
                )
            if occurrences.get(net) != {"root", child_id} or net in shared:
                problem(
                    where + f"/ports/{port_index}/net",
                    "a port needs physical root and this child endpoints only",
                )
        expected_ports = {name for name in expected_local if "root" in occurrences.get(name, set())}
        if (
            set(port_nets) != expected_ports
            or len(port_nets) != len(set(port_nets))
            or len(port_names) != len(set(port_names))
        ):
            problem(
                where + "/ports",
                "exactly one uniquely named port is required for each root-child signal",
            )
        for name in expected_local:
            if occurrences[name] - {"root", child_id}:
                problem(where + "/local_nets", "non-shared signals cannot span both child pages")
            if isinstance(child.get("name"), str) and isinstance(local.get(name), str):
                expected_names[name] = f"/{child['name']}/{local[name]}"
    for name, pages in occurrences.items():
        if name not in shared and pages == {"root"}:
            # Root-only signals are deliberately not in this finite first scope.
            problem("/nets", "root-only non-shared signals are unsupported in this hierarchy scope")
    if hierarchy.get("canonical_nets") != expected_names or set(expected_names) != known_nets:
        problem(
            "/hierarchy/canonical_nets",
            "full names must exactly match the declared structural sheet scopes",
        )
    return problems


def hierarchy_plan(spec, stem):
    """Plan every document, sheet pin and label identity before creation."""
    if "hierarchy" not in spec:
        return None
    problems = hierarchy_problems(spec, stem)
    if problems:
        raise ValueError("Invalid hierarchy declaration: " + str(problems[:10]))
    hierarchy = spec["hierarchy"]
    namespace = uuid.UUID(hierarchy["namespace_uuid"])

    def identity(name):
        return str(uuid.uuid5(namespace, name))

    root_uuid = identity("document:root")
    root = {
        "id": "root",
        "uuid": root_uuid,
        "file": stem + ".kicad_sch",
        "title": hierarchy["root"]["title"],
        "instance_path": "/" + root_uuid,
    }
    children = []
    for child in hierarchy["children"]:
        sheet_uuid = identity("sheet:" + child["id"])
        ports = [
            {
                **port,
                "uuid": identity(f"sheet-pin:{child['id']}:{port['net']}"),
                "label_uuid": identity(f"hierarchical-label:{child['id']}:{port['net']}"),
            }
            for port in child["ports"]
        ]
        children.append(
            {
                **child,
                "sheet_uuid": sheet_uuid,
                "document_uuid": identity("document:" + child["id"]),
                "instance_path": f"/{root_uuid}/{sheet_uuid}",
                "ports": ports,
            }
        )
    return {
        "schema": hierarchy["schema"],
        "namespace_uuid": hierarchy["namespace_uuid"],
        "root": root,
        "children": children,
        "canonical_nets": dict(hierarchy["canonical_nets"]),
        "shared_power_nets": list(hierarchy["shared_power_nets"]),
        "part_sheets": {part["ref"]: part["sheet_id"] for part in spec["parts"]},
        "output_names": [root["file"], *(child["file"] for child in children), stem + ".net"],
    }
