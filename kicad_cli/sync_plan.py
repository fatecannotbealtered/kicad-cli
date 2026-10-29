"""Pure preparation of a sync task from captured inputs; no envelope or file writes."""

from __future__ import annotations

import hashlib
from collections import Counter

from . import sync_readback
from .fileformat.board import Board
from .fileformat.sexpr import Document
from .native import board_update


class PlanError(ValueError):
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details


def prepare(inputs, removal_path=None):
    """A bounded defaults task. Unsatisfied required parts/pins are never a PASS."""
    from .commands import sync  # noqa: PLC0415

    raw = inputs.raw(inputs.board_path)
    baseline = hashlib.sha256(raw).hexdigest()
    board = Board(Document.parse(raw.decode("utf-8")), inputs.board_path)
    before, _, _ = sync_readback.inspect_bytes(raw)
    found, built = inputs.found, inputs.built
    problems = [item.to_dict() for item in found.annotation]
    if not found.components:
        raise PlanError(
            "Cannot sync an empty source netlist.",
            source_components=0,
            annotation=problems,
            missing_sheets=found.missing_sheets,
        )
    if problems or found.missing_sheets or any("?" in c["ref"] for c in found.components):
        raise PlanError(
            "Complete annotation and every referenced sheet before sync.",
            annotation=problems,
            missing_sheets=found.missing_sheets,
        )
    duplicates = [
        ref
        for ref, count in Counter(c["ref"].casefold() for c in found.components).items()
        if count != 1
    ]
    source_paths = [path for c in found.components for path in c["paths"]]
    duplicate_paths = [path for path, count in Counter(source_paths).items() if count != 1]
    if duplicates or duplicate_paths:
        raise PlanError(
            "Schematic component identities are ambiguous.",
            references=duplicates,
            duplicate_paths=duplicate_paths,
        )
    pathless = [
        item["ref"]
        for item in before
        if not item["path"] and "board_only" not in item["attributes"]
    ]
    if pathless:
        raise PlanError("Restore schematic UUID links before sync.", unlinked=pathless)
    records = board_update._records(board)
    pairs, unmatched, _ = sync._match(found.components, records, True)
    rows = sync._rows(found.components, records, sync.DEFAULTS)
    replacements = {row["ref"] for row in rows["change_footprint"]}
    required = {row["footprint"] for row in rows["add"]} | {
        row["to"] for row in rows["change_footprint"]
    }
    missing = sorted(fpid for fpid in required if inputs.library(fpid) is None)
    if missing:
        raise PlanError(
            "Required footprints are unavailable; no partial sync is dispatched.",
            missing_footprints=missing,
        )
    bottom = [ref for ref in replacements if pairs[ref][1]["footprint"].layer != "F.Cu"]
    if bottom:
        raise PlanError(
            "Bottom footprint replacement is not implemented; replace in KiCad first.",
            references=sorted(bottom),
        )
    removal = None
    source_raw = inputs.raw(inputs.schematic_path)
    schematic_identity = {
        "root_uuid": Document.parse(source_raw.decode("utf-8-sig")).root.find("uuid").value(1),
        "sha256": hashlib.sha256(source_raw).hexdigest(),
        "path_convention": "Saved footprint path omits root UUID; root-sheet path is /symbol-uuid",
    }
    if removal_path is not None:
        from . import sync_removal

        removal = sync_removal.prepare(
            board, before, found, sync_removal.parse(inputs.raw(removal_path)), schematic_identity
        )
    update, text = board_update.prepare(
        board,
        found,
        built,
        inputs.tables,
        baseline,
        library=inputs.library,
        footprint_source=inputs.footprint_source,
    )
    if update.problems:
        raise PlanError(
            "The requested sync could not fulfill every required item.", problems=update.problems
        )
    if removal is not None:
        result = Document.parse(text)
        node = next(
            node
            for node in result.root.find_all("footprint")
            if node.find("uuid").value(1) == removal["before"]["id"]
        )
        result.root.remove(node)
        text = result.dumps()
    intended = text.encode("utf-8")
    after, protected, _ = sync_readback.inspect_bytes(intended)
    expected_root = sync_readback.inspect_bytes(raw, renamed=update.renamed)[1]
    if protected != expected_root:
        raise PlanError("Preparation changed copper/board semantics beyond approved net renames.")
    by_ref = {item["ref"]: item for item in after}
    by_id = {item["id"]: item for item in after}
    old = {item["ref"]: item for item in before}
    matched_ids = {record["footprint"].node.find("uuid").value(1) for _, record in pairs.values()}
    for prior in before:
        if removal is not None and prior["id"] == removal["before"]["id"]:
            if prior["id"] in by_id:
                raise PlanError("The explicitly removed footprint remains in the prepared board")
            continue
        if prior["id"] not in matched_ids and by_id.get(prior["id"]) != prior:
            raise PlanError(
                "Preparation changed an extra/board-only footprint.", reference=prior["ref"]
            )
    for ref, (_part, record) in pairs.items():
        prior = old[record["ref"]]
        actual = by_id.get(prior["id"])
        if actual is None or any(
            actual[field] != prior[field]
            for field in ("id", "path", "position_nm", "rotation_deg", "layer")
        ):
            raise PlanError("Matched footprint identity or pose changed.", reference=ref)
        if prior["saved_semantics_sha256"] != actual["saved_semantics_sha256"] and record["locked"]:
            raise PlanError(
                "This adapter does not override a changed locked footprint.", reference=ref
            )
        if ref not in replacements:
            # New hidden fields are permitted; existing physical primitives remain protected.
            if prior["geometry_sha256"] != actual["geometry_sha256"] or any(
                actual["property_geometry_sha256"].get(key) != value
                for key, value in prior["property_geometry_sha256"].items()
            ):
                raise PlanError("A metadata-only sync changed footprint geometry.", reference=ref)
            if [{k: p[k] for k in ("id", "number", "geometry_sha256")} for p in prior["pads"]] != [
                {k: p[k] for k in ("id", "number", "geometry_sha256")} for p in actual["pads"]
            ]:
                raise PlanError("A metadata-only sync changed physical pads.", reference=ref)
    pins = board_update._net_of(built.nets)
    tasks = []
    for part in found.components:
        ref, actual = part["ref"], by_ref.get(part["ref"])
        if actual is None:
            raise PlanError("A required schematic component has no saved footprint.", reference=ref)
        requested_fields = {
            key: value
            for key, value in part["fields"].items()
            if key not in sync.FIELD_EXEMPT and (value or key in actual["fields"])
        }
        expected_marks = {
            "dnp": "dnp" in part["properties"],
            "exclude_from_bom": "exclude_from_bom" in part["properties"],
        }
        if actual["value"] != part["value"] or not sync._fpid_equivalent(
            actual["fpid"], part["fpid"]
        ):
            raise PlanError("A required value or footprint ID was not prepared.", reference=ref)
        if actual["path"] not in part["paths"] or any(
            actual["fields"].get(key) != value for key, value in requested_fields.items()
        ):
            raise PlanError("A required schematic link/field was not prepared.", reference=ref)
        if any((word in actual["attributes"]) != on for word, on in expected_marks.items()):
            raise PlanError("A required population/BOM mark was not prepared.", reference=ref)
        required_pins = {
            number: net for (reference, number), net in pins.items() if reference == ref
        }
        physical = {pad["number"] for pad in actual["pads"]}
        missing_pins = sorted(set(required_pins) - physical)
        wrong = [
            pad["id"]
            for pad in actual["pads"]
            if pad["net"] != required_pins.get(pad["number"], "")
        ]
        if missing_pins or wrong:
            raise PlanError(
                "Every logical pin must reach every matching physical pad.",
                reference=ref,
                missing_pins=missing_pins,
                wrong_pad_ids=wrong,
            )
        tasks.append(
            {
                "ref": ref,
                "value": part["value"],
                "fpid": part["fpid"],
                "paths": part["paths"],
                "fields": requested_fields,
                "marks": expected_marks,
                "pins": required_pins,
            }
        )
    rows = dict(update.rows)
    if removal is not None:
        removed = removal["before"]
        rows["not_removed"] = [
            row for row in rows.get("not_removed", []) if row["ref"] != removed["ref"]
        ]
        rows["removed"] = [
            {
                "ref": removed["ref"],
                "id": removed["id"],
                "why": "Explicit identity-bound isolated-footprint selection",
            }
        ]
    return {
        "intended": intended,
        "expected": after,
        "protected_sha256": expected_root,
        "tasks": tasks,
        "rows": rows,
        "renamed_nets": update.renamed,
        "added": [part["ref"] for part in unmatched],
        "replaced": sorted(replacements),
        "baseline": baseline,
        "removal": removal,
        "schematic_identity": schematic_identity,
    }
