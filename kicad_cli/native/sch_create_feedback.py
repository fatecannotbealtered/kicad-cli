"""Read-only, finite geometry feedback for the actual created sheet.

This reuses the edit canvas' conservative boxes, not KiCad's font renderer.
Its intersections are review warnings. They are not electrical or aesthetic
proof, and unsupported drawings remain explicitly unmeasured.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..fileformat.schematic import Schematic, _unit_style, uuid_of
from ..fileformat.sexpr import Document
from . import sch_draw as draw
from .sch_field_feedback import _box, _field_hidden, field_feedback
from .sch_label_layout import _supported as supported_label
from .sch_label_layout import _text_box

LIMIT = (
    "Conservative default-font body/pin/text boxes and grid wire margin; "
    "intersections are potential conflicts, not exact native glyph violations. "
    "Pin-name/number glyphs, native unit-reference suffixes, unmeasured styles/graphics "
    "and complete page readability "
    "still require full native images. Electrical label scope is not proved by geometry."
)


def _mm(box):
    return [v / draw.NM for v in box]


def _body(sch, sym):
    lib = sch.library.get(sym.library_name)
    if lib is None or lib.node is None:
        return None, "Missing embedded library geometry"
    if sym.angle not in (0, 90, 180, 270):
        return None, "Nonorthogonal symbol rotation is unmeasured"
    if any(pin.angle not in (0, 90, 180, 270) for pin in lib.pins_of(sym.unit, sym.body_style)):
        return None, "Nonorthogonal pin geometry is unmeasured"
    for sub in lib.node.find_all("symbol"):
        unit, style = _unit_style(sub.value(1) or "", lib.node.value(1) or "")
        if unit not in (0, sym.unit) or style not in (0, sym.body_style):
            continue
        unsupported = [
            item.head
            for item in sub.lists()
            if item.head not in {"rectangle", "polyline", "circle", "pin"}
        ]
        if unsupported:
            return None, "Unmeasured embedded graphic types: " + ", ".join(sorted(set(unsupported)))
    box = draw._body(sym, lib)
    return box, None if box is not None else "No measurable body/pin geometry"


def _hierarchy_text_supported(node, text, angle):
    effects = node.find("effects")
    font = effects.find("font") if effects is not None else None
    size = font.find("size") if font is not None else None
    return (
        bool(text)
        and text.isascii()
        and not any(c in text for c in "\n\r\t{}~^")
        and angle in (0, 90, 180, 270)
        and font is not None
        and not font.values()
        and {v.head for v in font.lists()} == {"size"}
        and size is not None
        and draw._nm(size.atom(1)) == draw.FONT == draw._nm(size.atom(2))
    )


def _hierarchy_feedback(sch, report, measure, bodies):
    """Conservative generated sheet/pin bounds, never exact native glyphs."""
    if not sch.sheets and not any(label.kind == "hierarchical_label" for label in sch.labels):
        return
    rows = report["hierarchy_objects"] = []
    report["_untrusted"].append("hierarchy_objects")
    for sheet in sch.sheets:
        item = {"kind": "sheet", "uuid": sheet.uuid, "name": sheet.name, "file": sheet.file}
        x, y = sheet.position
        width, height = sheet.size
        box = (x, y, x + width, y + height)
        row = measure(item, box)
        row["conflicts"] = [other for other, bounds in bodies if draw._meets(box, bounds)]
        rows.append(row)
        if row["conflicts"]:
            report["findings"].append(dict(row, status="review_sheet_symbol_overlap"))
        for pin in sheet.pins:
            at = pin.node.find("at")
            angle = float(at.atom(3) or 0)
            target = {
                "kind": "sheet_pin",
                "sheet_uuid": sheet.uuid,
                "uuid": uuid_of(pin.node),
                "text": pin.name,
                "anchor_mm": _mm(pin.position),
                "direction": pin.electrical,
            }
            if not _hierarchy_text_supported(pin.node, pin.name, angle):
                reason = "Only plain default-font 1.27mm orthogonal sheet pins are bounded"
                rows.append(dict(target, status="unmeasured", reason=reason))
                report["unmeasured_objects"].append(dict(target, reason=reason))
                continue
            # Pin text on a right-side pin points back into its owning sheet.
            pin_box = draw._label_box(
                "hierarchical_label", pin.name, pin.position, (angle + 180) % 360
            )
            rows.append(measure(target, pin_box))
        for prop in sheet.node.find_all("property"):
            at = prop.find("at")
            angle = float(at.atom(3) or 0) if at is not None else None
            text = prop.value(2) or ""
            target = {
                "kind": "sheet_property",
                "sheet_uuid": sheet.uuid,
                "field": prop.value(1),
                "text": text,
            }
            if at is None or not _hierarchy_text_supported(prop, text, angle):
                reason = "Sheet property typography is unmeasured"
                rows.append(dict(target, status="unmeasured", reason=reason))
                report["unmeasured_objects"].append(dict(target, reason=reason))
                continue
            position = (draw._nm(at.atom(1)), draw._nm(at.atom(2)))
            rows.append(measure(target, draw._label_box("label", text, position, angle)))


def inspect_created(path, *, source=None):
    """Measure a saved disposable candidate without changing any document byte."""
    path = Path(path)
    raw = path.read_bytes() if source is None else source.encode("utf-8")
    sch = Schematic(Document.parse(raw.decode("utf-8")), path)
    report = {
        "schema": "sch-create-geometry/v1",
        "source": {"file": path.name, "sha256": hashlib.sha256(raw).hexdigest()},
        "geometry_check": "partial_conservative_schematic_bounds",
        "status": "requires_visual_review",
        "symbols": [],
        "fields": [],
        "labels": [],
        "findings": [],
        "unmeasured_objects": [],
        "limit": LIMIT,
        "_untrusted": [
            "source.file",
            "symbols",
            "fields",
            "labels",
            "findings",
            "unmeasured_objects",
        ],
    }
    try:
        canvas = draw.Canvas.of(sch)
    except (KeyError, ValueError, TypeError) as exc:
        report.update(
            geometry_check="incomplete", reason="Unsupported canvas geometry: " + str(exc)
        )
        report["unmeasured_objects"].append({"kind": "whole_sheet", "reason": report["reason"]})
        return report
    report["page"] = {
        "paper": sch.root.find("paper").value(1) if sch.root.find("paper") else "A4",
        "usable_bounds_mm": _mm(canvas.area),
        "title_block_bounds_mm": _mm(canvas.boxes[0]),
        "limit": "Existing canvas frame margin and rectangular title-block reserve",
    }
    if report["page"]["paper"] not in draw.PAPERS and report["page"]["paper"] != "User":
        report.update(geometry_check="incomplete", reason="Unknown paper size is unmeasured")
        report["unmeasured_objects"].append({"kind": "page", "reason": report["reason"]})
        report["page"].update(usable_bounds_mm=None, title_block_bounds_mm=None)
        return report
    objects = []

    def measure(item, box):
        row = dict(item, box_mm=_mm(box), inside_page_bounds=canvas.inside(box))
        row["overlaps_title_block"] = draw._meets(box, canvas.boxes[0])
        if not row["inside_page_bounds"] or row["overlaps_title_block"]:
            report["findings"].append(dict(row, status="review_page_bounds"))
        objects.append((item, box))
        return row

    for sym in sch.symbols:
        item = {
            "kind": "symbol_body_or_pin_bounds",
            "ref": sym.properties.get("Reference"),
            "symbol_uuid": sym.uuid,
            "unit": sym.unit,
        }
        box, why = _body(sch, sym)
        if box is None:
            report["unmeasured_objects"].append(dict(item, reason=why))
            report["symbols"].append(dict(item, status="unmeasured", reason=why))
        else:
            row = measure(item, box)
            report["symbols"].append(row)
        for prop in sym.node.find_all("property"):
            if _field_hidden(prop) or not prop.value(2):
                continue
            field = dict(item, kind="visible_field", field=prop.value(1))
            lib = sch.library.get(sym.library_name)
            if prop.value(1) == "Reference" and lib is not None and lib.unit_count > 1:
                box, why = (
                    None,
                    (
                        "Native multi-unit reference suffix typography is unmeasured; "
                        "the file's base reference alone is not its complete rendered text"
                    ),
                )
            else:
                box, why = _box(sym, prop)
            if box is None:
                report["unmeasured_objects"].append(dict(field, reason=why))
                report["fields"].append(dict(field, status="unmeasured", reason=why))
                continue
            details = field_feedback(sch, sym, prop.value(1))
            row = dict(measure(field, box), **details)
            report["fields"].append(row)
            if details["conflicts"]:
                report["findings"].append(dict(row, status="review_field_conflicts"))
    bodies = [(item, box) for item, box in objects if item["kind"] == "symbol_body_or_pin_bounds"]
    for index, (item, box) in enumerate(bodies):
        conflicts = [other for other, bounds in bodies[index + 1 :] if draw._meets(box, bounds)]
        if conflicts:
            report["findings"].append(
                dict(item, status="review_symbol_overlap", conflicts=conflicts)
            )
    # The half-grid text-anchor exclusion is the existing label helper's rule.
    # The electrical attachment to a label's own stub must not become a warning.
    labels = []
    for label in sch.labels:
        item = {"kind": label.kind, "text": label.text, "uuid": uuid_of(label.node)}
        hierarchical = label.kind == "hierarchical_label" and _hierarchy_text_supported(
            label.node, label.text, label.angle
        )
        if (not supported_label(label) and not hierarchical) or any(
            c in label.text for c in "\n\r\t"
        ):
            why = "Only plain 1.27mm default-font orthogonal local labels are measured"
            report["unmeasured_objects"].append(dict(item, reason=why))
            report["labels"].append(dict(item, status="unmeasured", reason=why))
            continue
        box = (
            draw._label_box(label.kind, label.text, label.position, label.angle)
            if hierarchical
            else _text_box(label, label.angle)
        )
        labels.append((label, item, box))
    non_label_objects = tuple(objects)
    for label, item, box in labels:
        conflicts = [other for other, bounds in non_label_objects if draw._meets(box, bounds)]
        conflicts += [
            other for _, other, bounds in labels if other is not item and draw._meets(box, bounds)
        ]
        for wire in sch.wires:
            if label.kind == "hierarchical_label" and label.position in (wire.start, wire.end):
                # The actual KiCad angle-0 default hierarchical-label glyphs
                # extend rightward. A rightward own stub is electrical contact
                # but also a visible text-through-wire risk, not an exemption.
                effects = label.node.find("effects")
                justify = effects.find("justify") if effects is not None else None
                justified = [] if justify is None else justify.values()
                other = wire.end if wire.start == label.position else wire.start
                if (
                    label.angle == 0
                    and justified in ([], ["left"], ["left", "bottom"], ["left", "top"])
                    and other[1] == label.position[1]
                    and other[0] > label.position[0]
                ):
                    conflicts.append(
                        {
                            "kind": "hierarchical_port_stub_into_text",
                            "from_mm": _mm(label.position),
                            "to_mm": _mm(other),
                            "reason": "Angle-0 hierarchical text extends right of its anchor",
                        }
                    )
                continue  # A leftward electrical attachment is intentional.
            if draw._meets(box, draw._pad(draw._span(wire.start, wire.end), draw.GRID // 4)):
                conflicts.append(
                    {"kind": wire.kind, "from_mm": _mm(wire.start), "to_mm": _mm(wire.end)}
                )
        row = measure(item, box)
        row.update(
            anchor_mm=_mm(label.position),
            angle_deg=label.angle,
            conflicts=conflicts,
            status="potential_conflicts" if conflicts else "no_overlap_in_measured_bounds",
        )
        report["labels"].append(row)
        if conflicts:
            direction_conflict = any(
                conflict.get("kind") == "hierarchical_port_stub_into_text" for conflict in conflicts
            )
            report["findings"].append(
                dict(
                    row,
                    status=(
                        "review_hierarchical_port_stub_text_direction"
                        if direction_conflict
                        else "review_label_conflicts"
                    ),
                )
            )
    for wire in sch.wires:
        item = {"kind": wire.kind, "from_mm": _mm(wire.start), "to_mm": _mm(wire.end)}
        measure(
            item,
            draw._pad(draw._span(wire.start, wire.end), draw.GRID // 4),
        )
        if wire.start[0] != wire.end[0] and wire.start[1] != wire.end[1]:
            report["unmeasured_objects"].append(
                dict(item, reason="Nonorthogonal wire/body intersections are unmeasured")
            )
            continue
        # A genuine own-stub attachment ends on the pin bounds. Strict overlap
        # leaves that endpoint alone, while exposing wire runs through a body.
        conflicts = [
            other for other, box in bodies if draw._meets(draw._span(wire.start, wire.end), box)
        ]
        if conflicts:
            report["findings"].append(
                dict(item, status="review_wire_body_conflicts", conflicts=conflicts)
            )
    _hierarchy_feedback(sch, report, measure, bodies)
    for item in sch.root.lists():
        if item.head in {"arc", "circle", "polyline", "rectangle", "text", "text_box"}:
            report["unmeasured_objects"].append(
                {
                    "kind": item.head,
                    "uuid": uuid_of(item),
                    "reason": "Standalone graphics are unmeasured",
                }
            )
    report["counts"] = {
        "placed_symbol_units": len(report["symbols"]),
        "visible_fields": len(report["fields"]),
        "naming_carriers": len(report["labels"]),
        "potential_findings": len(report["findings"]),
        "unmeasured_objects": len(report["unmeasured_objects"]),
    }
    return report
