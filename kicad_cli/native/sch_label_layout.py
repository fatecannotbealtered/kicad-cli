"""Bounded rename layout: turn ordinary labels at their existing electrical anchor."""

from ..fileformat.schematic import _hidden
from ..fileformat.sexpr import symbol
from . import sch_draw as draw


def _supported(label):
    effects = label.node.find("effects")
    font = effects.find("font") if effects is not None else None
    size = font.find("size") if font is not None else None
    justify = effects.find("justify") if effects is not None else None
    return (
        label.kind == "label"
        and label.angle in draw.ANGLE.values()
        and not _hidden(label.node)
        and font is not None
        and size is not None
        and justify is not None
        and not font.values()
        and {v.head for v in font.lists()} == {"size"}
        and draw._nm(size.atom(1)) == draw.FONT == draw._nm(size.atom(2))
        and justify.values()
        == (["left", "bottom"] if label.angle in (0, 90) else ["right", "bottom"])
        and label.text.isascii()
        and not any(c in label.text for c in "{}~^")
    )


def _text_box(label, angle):
    # The label anchor must touch its own wire. Exclude only the first half
    # grid along the text baseline, as the existing drawn-label anchor region.
    out = next(direction for direction, degrees in draw.ANGLE.items() if degrees == angle)
    skip = draw.GRID // 2
    start = tuple(a + d * skip for a, d in zip(label.position, out, strict=True))
    return draw._along(start, out, max(1, draw._text_width(label.text) - skip))


def _objects(sch, box, omitted):
    objects = []
    for other in sch.labels:
        if id(other.node) != omitted and draw._meets(
            box, draw._label_box(other.kind, other.text, other.position, other.angle)
        ):
            uuid = other.node.find("uuid")
            objects.append(
                {
                    "kind": other.kind,
                    "text": other.text,
                    "uuid": uuid.value(1) if uuid is not None else None,
                }
            )
    for part in sch.symbols:
        lib = sch.library.get(part.library_name)
        body = draw._body(part, lib) if lib is not None else None
        if (body is not None and draw._meets(box, body)) or any(
            draw._meets(box, field) for field in draw._field_boxes(part)
        ):
            objects.append(
                {
                    "kind": "symbol_or_visible_field",
                    "ref": part.properties.get("Reference"),
                    "uuid": part.uuid,
                }
            )
    return objects


def retained_anchor(sch, node):
    """Return measured feedback and success; change only angle/justification.

    Other fonts, power/global/hierarchical carriers and rich text keep the
    existing explicit visual-only boundary. No anchors, wires or symbols move.
    """
    label = next((item for item in sch.labels if item.node is node), None)
    uid = node.find("uuid")
    report = {
        "uuid": uid.value(1) if uid is not None else None,
        "file": sch.path.name,
        "kind": node.head,
        "status": "requires_visual_review",
        "geometry_check": "not_performed",
    }
    if label is None or not _supported(label):
        report["limit"] = "Only plain 1.27mm default-font orthogonal local labels are measured"
        return report, True
    canvas = draw.Canvas.of(sch, omit_labels=(id(node),))
    before_angle = label.angle
    before_box = _text_box(label, before_angle)
    before_conflicts = canvas.overlaps(before_box)
    candidates = [before_angle] + [angle for angle in (0, 180, 90, 270) if angle != before_angle]
    chosen = next(
        (
            angle
            for angle in candidates
            if canvas.inside(_text_box(label, angle)) and canvas.free(_text_box(label, angle))
        ),
        None,
    )
    report.update(
        text=label.text,
        anchor_mm=[v / draw.NM for v in label.position],
        before_angle_deg=before_angle,
        after_angle_deg=chosen,
        overlaps_before=before_conflicts,
        inside_before=canvas.inside(before_box),
        conflicts_before=_objects(sch, before_box, id(node)),
        geometry_check="partial_conservative_schematic_bounds",
        limit="Existing conservative canvas bounds; not native glyph or whole-page layout proof",
    )
    if chosen is None:
        report.update(status="no_clear_orientation_at_retained_anchor", overlaps=before_conflicts)
        return report, False
    if chosen != before_angle:
        node.find("at").set(3, str(chosen))
        node.find("effects").find("justify").set(
            1, symbol("left" if chosen in (0, 90) else "right")
        )
    report.update(
        status="turned_at_retained_anchor" if chosen != before_angle else "retained", overlaps=0
    )
    return report, True
