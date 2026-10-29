"""Read-only, bounded feedback for retained schematic field positions.

Uses the existing drawing canvas approximation. It is deliberately advisory:
native glyphs, rotated fields and complete page readability remain unmeasured.
"""

from ..fileformat.schematic import _hidden
from . import sch_draw as draw
from .sch_label_layout import _supported as supported_label


def _field(sym, name):
    return next((p for p in sym.node.find_all("property") if p.value(1) == name), None)


def _field_hidden(prop):
    """Read both property-level and historical effects-level hide encodings."""
    effects = prop.find("effects")
    return _hidden(prop) or (effects is not None and _hidden(effects))


def _box(sym, prop):
    if prop is None or _field_hidden(prop) or not prop.value(2):
        return None, "hidden, empty or absent field"
    at = prop.find("at")
    effects = prop.find("effects")
    font = effects.find("font") if effects is not None else None
    size = font.find("size") if font is not None else None
    justify = effects.find("justify") if effects is not None else None
    words = justify.values() if justify is not None else []
    value = prop.value(2)
    if (
        sym.angle != 0
        or at is None
        or float(at.atom(3) or 0) != 0
        or font is None
        or size is None
        or font.values()
        or {n.head for n in font.lists()} != {"size"}
        or draw._nm(size.atom(1)) != draw.FONT
        or draw._nm(size.atom(2)) != draw.FONT
        or words not in ([], ["left"], ["right"])
        or not value.isascii()
        or any(c in value for c in "\n\r\t{}~^")
    ):
        return None, (
            "Only unrotated symbols and horizontal plain ASCII fields with the default "
            "1.27mm font are measured; rotated symbols/font/rich text need native rendering"
        )
    x, y = draw._nm(at.atom(1)), draw._nm(at.atom(2))
    width = draw._text_width(value)
    left = x if words == ["left"] else x - width if words == ["right"] else x - width // 2
    return (left, y - draw.FONT, left + width, y + draw.FONT), None


def field_feedback(sch, sym, name):
    prop = _field(sym, name)
    at = prop.find("at") if prop is not None else None
    report = {
        "text": prop.value(2) if prop is not None else None,
        "at_mm": [draw._nm(at.atom(i)) / draw.NM for i in (1, 2)] if at is not None else None,
        "geometry_check": "not_performed",
        "status": "requires_visual_review",
        "conflicts": [],
    }
    box, reason = _box(sym, prop)
    if box is None:
        report["limit"] = reason
        if prop is None or _field_hidden(prop) or not prop.value(2):
            report["status"] = "not_visible"
        return report
    conflicts, unmeasured = [], []
    for other in sch.symbols:
        lib = sch.library.get(other.library_name)
        body = draw._body(other, lib) if lib is not None else None
        if body is not None and draw._meets(box, body):
            conflicts.append(
                {
                    "kind": "symbol_body_or_pin_bounds",
                    "symbol_uuid": other.uuid,
                    "ref": other.properties.get("Reference"),
                }
            )
        for field in other.node.find_all("property"):
            if field is prop or _field_hidden(field) or not field.value(2):
                continue
            other_box, why = _box(other, field)
            item = {
                "kind": "visible_field",
                "symbol_uuid": other.uuid,
                "ref": other.properties.get("Reference"),
                "field": field.value(1),
            }
            if other_box is None:
                unmeasured.append(dict(item, reason=why))
            elif draw._meets(box, other_box):
                conflicts.append(item)
    for wire in sch.wires:
        if draw._meets(box, draw._pad(draw._span(wire.start, wire.end), draw.GRID // 4)):
            conflicts.append(
                {
                    "kind": wire.kind,
                    "from_mm": [v / draw.NM for v in wire.start],
                    "to_mm": [v / draw.NM for v in wire.end],
                }
            )
    for label in sch.labels:
        identifier = label.node.find("uuid")
        item = {
            "kind": label.kind,
            "text": label.text,
            "uuid": identifier.value(1) if identifier is not None else None,
        }
        if not supported_label(label):
            unmeasured.append(
                dict(item, reason="Naming carrier/font outside the local-label bound")
            )
        elif draw._meets(box, draw._label_box(label.kind, label.text, label.position, label.angle)):
            conflicts.append(item)
    canvas = draw.Canvas.of(sch)
    report.update(
        geometry_check="partial_conservative_schematic_bounds",
        status="potential_conflicts" if conflicts else "no_overlap_in_measured_bounds",
        box_mm=[v / draw.NM for v in box],
        inside_page_bounds=canvas.inside(box),
        conflicts=conflicts,
        unmeasured_objects=unmeasured,
        limit=(
            "Advisory existing conservative body/pin/text boxes and grid wire margin; "
            "box intersections are potential conflicts, not exact native glyph violations. "
            "Sheet graphics, other styles and complete page readability still require review."
        ),
    )
    return report
