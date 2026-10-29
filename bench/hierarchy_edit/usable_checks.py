"""Explicit protected drawings in the one usable hierarchy; no layout generator."""

import copy
import json
from collections import Counter
from decimal import Decimal


def child(node, name):
    return next((n for n in node[1:] if isinstance(n, list) and n[0] == name), None)


def intermediate_isolated_labels(baseline, current, disconnected):
    """Derive singleton-net label identities from frozen pins and retained drawings.

    This finite orthogonal-symbol fixture has no buses or mirrored symbols. A
    physical shared-child label is counted once, as in the pinned native report.
    No warning text, current report UUID, or post-edit baseline supplies intent.
    """
    singletons = {}
    for net in baseline["xml_nets"]:
        pins = {p["ref"] + "." + p["pin"] for p in net["members"]}
        remaining = pins - disconnected
        if pins & disconnected and len(remaining) == 1:
            singletons[net["name"]] = next(iter(remaining))
    labels = set()
    for sheet in current["sheets"]:
        for node in current["raw_schematics"][sheet["file"]][1:]:
            if isinstance(node, list) and node[0] == "label":
                if sheet["name_path"] + node[1] in singletons:
                    labels.add(child(node, "uuid")[1])
    for pin in singletons.values():
        ref, number = pin.rsplit(".", 1)
        units = [u for u in baseline["units"] if u["ref"] == ref]
        unit = next(u for u in units if any(p["number"] == number for p in u["pins"]))
        if child(unit["raw_symbol"], "mirror") is not None:
            raise ValueError("Mirrored fixture pins need a separate intermediate ERC policy")
        tree = baseline["raw_schematics"][unit["file"]]
        library = next(n for n in child(tree, "lib_symbols")[1:] if n[1] == unit["library_id"])
        pins = [
            p
            for drawing in library[1:]
            if isinstance(drawing, list)
            and drawing[0] == "symbol"
            and int(drawing[1].rsplit("_", 2)[1]) in (0, unit["unit"])
            and drawing[1].rsplit("_", 1)[1] in ("0", "1")
            for p in drawing[1:]
            if isinstance(p, list) and p[0] == "pin" and child(p, "number")[1] == number
        ]
        if len(pins) != 1:
            raise ValueError("Intermediate ERC needs one exact physical pin position")
        x, y = map(Decimal, child(pins[0], "at")[1:3])
        rotated = {0: (x, y), 90: (-y, x), 180: (-x, -y), 270: (y, -x)}
        if unit["rotation_deg"] not in rotated:
            raise ValueError("Only the frozen orthogonal fixture pin pose is supported")
        x, y = rotated[unit["rotation_deg"]]
        point = (Decimal(str(unit["position_mm"][0])) + x, Decimal(str(unit["position_mm"][1])) - y)
        actual = current["raw_schematics"][unit["file"]]
        wires = [child(n, "pts")[1:] for n in actual[1:] if isinstance(n, list) and n[0] == "wire"]
        carriers = [
            n
            for n in actual[1:]
            if isinstance(n, list) and n[0] in {"label", "global_label", "hierarchical_label"}
        ]
        positions = {tuple(map(Decimal, child(n, "at")[1:3])) for n in carriers}
        segments = [(tuple(map(Decimal, a[1:3])), tuple(map(Decimal, b[1:3]))) for a, b in wires]
        positions.update(p for segment in segments for p in segment)

        def on_segment(p, a, b):
            return (
                (p[0] - a[0]) * (b[1] - a[1]) == (p[1] - a[1]) * (b[0] - a[0])
                and min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
                and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])
            )

        reached = {point}
        while True:
            expanded = reached | {
                p
                for a, b in segments
                if any(on_segment(q, a, b) for q in reached)
                for p in positions
                if on_segment(p, a, b)
            }
            if expanded == reached:
                break
            reached = expanded
        labels.update(
            child(n, "uuid")[1]
            for n in carriers
            if tuple(map(Decimal, child(n, "at")[1:3])) in reached
        )
    return Counter(("isolated_pin_label", "warning", (uuid,)) for uuid in labels)


def compare_drawings(baseline, current, frozen, stage):
    failures = []
    wanted = frozen["stages"][stage]
    regions = wanted.get("drawing_regions_mm", {})
    naming = frozen["rename_label"]

    def selected_region(node, file):
        if node[0] not in {"wire", "label", "no_connect"}:
            return False
        if node[0] == "wire":
            points = child(node, "pts")[1:]
        else:
            points = [child(node, "at")]
        return any(
            all(r[0] <= float(p[1]) <= r[2] and r[1] <= float(p[2]) <= r[3] for p in points)
            for r in regions.get(file, [])
        )

    def protected(tree, file, after):
        rows = []
        for node in tree[1:]:
            if not isinstance(node, list) or node[0] == "symbol" or selected_region(node, file):
                continue  # full symbol instances/units are checked separately
            node = copy.deepcopy(node)
            if (
                wanted["net_renames"]
                and file == naming["file"]
                and child(node, "uuid") == ["uuid", naming["uuid"]]
            ):
                expected_text = naming["new_text"] if wanted["net_renames"] else naming["old_text"]
                if after:
                    at = child(node, "at")
                    if (
                        node[1] != expected_text
                        or [float(x) for x in at[1:3]] != naming["at_mm"]
                        or float(at[3]) not in (0, 90, 180, 270)
                    ):
                        failures.append({"check": "declared_rename_label_text_anchor_orientation"})
                node[1] = expected_text
                child(node, "at")[3] = "declared-orthogonal-angle"
                effects = child(node, "effects")
                for item in effects:
                    if isinstance(item, list) and item[0] == "justify":
                        if after and item[1:] not in (["left", "bottom"], ["right", "bottom"]):
                            failures.append({"check": "declared_rename_label_justification"})
                        item[1:] = ["declared-orthogonal-justification"]
            rows.append(json.dumps(node, sort_keys=True))
        return Counter(rows)

    if set(current["raw_schematics"]) != set(baseline["raw_schematics"]):
        failures.append({"check": "all_schematic_files_preserved"})
    for file, tree in baseline["raw_schematics"].items():
        actual = current["raw_schematics"].get(file)
        if actual is None or protected(tree, file, False) != protected(actual, file, True):
            failures.append({"check": "unselected_schematic_drawings_and_libraries:" + file})
    return failures
