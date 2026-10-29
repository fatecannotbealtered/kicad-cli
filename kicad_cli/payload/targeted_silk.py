"""Finite native measurements for explicitly selected Reference translations.

This module never saves a board. All collision arithmetic uses integer
nanometres and conservative native text, mask and graphic rectangles. In
particular, a rectangle around a mask opening is not its exact polygon.
"""

from __future__ import annotations

import math
from collections import defaultdict
from decimal import Decimal, InvalidOperation

NM_PER_MM = 1_000_000
MAX_TRANSLATION_NM = 5_000_000
MAX_OWNER_GAP_NM = 3_000_000
TARGET_KEYS = {"reference", "footprint_uuid", "reference_uuid", "layer", "from_mm", "to_mm"}


class IncompleteGeometry(ValueError):
    """A selected item or required obstacle cannot be measured reliably."""

    def __init__(self, reason, **details):
        super().__init__(reason)
        self.issue = {"reason": reason, **details}


def nm(value):
    """Reject values which cannot express an exact KiCad integer coordinate."""
    if isinstance(value, bool):
        raise IncompleteGeometry("coordinate_must_be_finite_exact_nanometres")
    try:
        number = Decimal(str(value)) * NM_PER_MM
        if not number.is_finite() or number != number.to_integral_value():
            raise InvalidOperation
        result = int(number)
    except (InvalidOperation, ValueError, TypeError, OverflowError) as exc:
        raise IncompleteGeometry("coordinate_must_be_finite_exact_nanometres") from exc
    if abs(result) > 2_000_000_000:
        raise IncompleteGeometry("coordinate_outside_supported_integer_range")
    return result


def point(values):
    if not isinstance(values, (list, tuple)) or len(values) != 2:
        raise IncompleteGeometry("position_must_have_two_coordinates")
    return tuple(nm(v) for v in values)


def shift(box, dx, dy):
    return (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)


def overlap(a, b, clearance=0):
    return not (
        a[2] + clearance <= b[0]
        or b[2] + clearance <= a[0]
        or a[3] + clearance <= b[1]
        or b[3] + clearance <= a[1]
    )


def inside(box, bounds, clearance=0):
    return (
        box[0] >= bounds[0] + clearance
        and box[1] >= bounds[1] + clearance
        and box[2] <= bounds[2] - clearance
        and box[3] <= bounds[3] - clearance
    )


def gap_squared(a, b):
    dx = max(a[0] - b[2], b[0] - a[2], 0)
    dy = max(a[1] - b[3], b[1] - a[3], 0)
    return dx * dx + dy * dy


def union(boxes):
    if not boxes:
        raise IncompleteGeometry("owner_body_has_no_measurable_nontext_geometry")
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def rectangle_outline(segments):
    """Only one closed, axis-aligned four-segment rectangular outline."""
    if len(segments) != 4:
        raise IncompleteGeometry("outline_requires_exactly_four_straight_segments")
    degree = defaultdict(int)
    edges = set()
    for start, end in segments:
        if start == end or (start[0] != end[0] and start[1] != end[1]):
            raise IncompleteGeometry("outline_is_not_an_axis_aligned_rectangle")
        edge = tuple(sorted((tuple(start), tuple(end))))
        if edge in edges:
            raise IncompleteGeometry("outline_has_duplicate_segments")
        edges.add(edge)
        degree[tuple(start)] += 1
        degree[tuple(end)] += 1
    xs, ys = sorted({p[0] for p in degree}), sorted({p[1] for p in degree})
    if len(xs) != 2 or len(ys) != 2 or len(degree) != 4 or set(degree.values()) != {2}:
        raise IncompleteGeometry("outline_is_not_one_closed_rectangle")
    if set(degree) != {(x, y) for x in xs for y in ys}:
        raise IncompleteGeometry("outline_has_unexpected_vertices")
    return (xs[0], ys[0], xs[1], ys[1])


def normalize_targets(targets):
    if not isinstance(targets, list) or not targets:
        raise IncompleteGeometry("targets_must_be_a_nonempty_array")
    names, identities, result = set(), set(), []
    for row in targets:
        if not isinstance(row, dict) or set(row) != TARGET_KEYS:
            raise IncompleteGeometry("target_has_inexact_fields")
        if any(
            not isinstance(row[k], str) or not row[k]
            for k in ("reference", "footprint_uuid", "reference_uuid", "layer")
        ):
            raise IncompleteGeometry("target_identity_requires_nonempty_strings")
        identity = (row["footprint_uuid"], row["reference_uuid"])
        if row["reference"] in names or identity in identities:
            raise IncompleteGeometry("duplicate_target_identity", reference=row["reference"])
        if row["layer"] != "F.SilkS":
            raise IncompleteGeometry("unsupported_target_layer", layer=row["layer"])
        before, after = point(row["from_mm"]), point(row["to_mm"])
        dx, dy = after[0] - before[0], after[1] - before[1]
        if dx * dx + dy * dy > MAX_TRANSLATION_NM * MAX_TRANSLATION_NM:
            raise IncompleteGeometry("target_translation_exceeds_5mm", reference=row["reference"])
        names.add(row["reference"])
        identities.add(identity)
        result.append(
            {
                **row,
                "from_mm": [v / NM_PER_MM for v in before],
                "to_mm": [v / NM_PER_MM for v in after],
            }
        )
    return result


def _uuid(item):
    try:
        return str(item.m_Uuid.AsString())
    except AttributeError as exc:
        raise IncompleteGeometry(
            "object_uuid_unavailable", object_type=type(item).__name__
        ) from exc


def _object_key(item):
    # GetFields may include the same C++ Reference returned by Reference().
    # A second wrapper for that same object is not a second physical field.
    return int(item.this) if hasattr(item, "this") else id(item)


def _xy(value):
    return int(value.x), int(value.y)


def _bbox(box):
    result = tuple(int(v) for v in (box.GetLeft(), box.GetTop(), box.GetRight(), box.GetBottom()))
    if result[2] <= result[0] or result[3] <= result[1]:
        raise IncompleteGeometry("native_envelope_is_empty_or_inverted")
    return result


def _box_of(item):
    try:
        return _bbox(item.GetBoundingBox())
    except (AttributeError, TypeError, RuntimeError) as exc:
        raise IncompleteGeometry(
            "native_graphic_envelope_unavailable", object_uuid=_uuid(item)
        ) from exc


def _angle(item, accessor):
    try:
        value = getattr(item, accessor)()
        result = float(value.AsDegrees() if hasattr(value, "AsDegrees") else value)
    except (AttributeError, TypeError, RuntimeError, ValueError) as exc:
        raise IncompleteGeometry("native_orientation_unavailable", object_uuid=_uuid(item)) from exc
    if not math.isfinite(result):
        raise IncompleteGeometry("native_orientation_is_not_finite", object_uuid=_uuid(item))
    return result % 360


def text_measure(item, pcbnew):
    """Default KiCad stroke, ordinary unrotated single-line text only."""
    try:
        text = item.GetText()
        font = item.GetFont()
        font_name, font_property = item.GetFontName(), item.GetFontProp()
        if font is not None or font_name != "" or font_property not in ("", "KiCad Font"):
            raise IncompleteGeometry(
                "unsupported_font",
                object_uuid=_uuid(item),
                font_name=font_name,
                font_property=font_property,
            )
        if (
            not isinstance(text, str)
            or not text
            or any(ord(c) < 32 or ord(c) > 126 for c in text)
            or "${" in text
        ):
            raise IncompleteGeometry("unsupported_text_content", object_uuid=_uuid(item))
        angle = _angle(item, "GetTextAngleDegrees")
        if angle != 0 or item.IsMirrored():
            raise IncompleteGeometry(
                "unsupported_rotated_or_mirrored_text", object_uuid=_uuid(item)
            )
        drawn = _bbox(item.GetEffectiveTextShape().BBox())
        bounding = _bbox(item.GetBoundingBox())
        size = _xy(item.GetTextSize())
        thickness = int(item.GetTextThickness())
        if min(size) <= 0 or thickness <= 0:
            raise IncompleteGeometry("unreadable_text_size_or_stroke", object_uuid=_uuid(item))
        style = {
            "text": text,
            "visible": bool(item.IsVisible()),
            "font_name": font_name,
            "font_property": font_property,
            "font_pointer_present": font is not None,
            "size_nm": list(size),
            "thickness_nm": thickness,
            "effective_pen_width_nm": int(item.GetEffectiveTextPenWidth()),
            "auto_thickness": bool(item.GetAutoThickness()),
            "angle_degrees": angle,
            "mirrored": bool(item.IsMirrored()),
            "bold": bool(item.IsBold()),
            "italic": bool(item.IsItalic()),
            "horizontal_justify": int(item.GetHorizJustify()),
            "vertical_justify": int(item.GetVertJustify()),
            "layer": int(item.GetLayer()),
            "keep_upright": bool(item.IsKeepUpright()),
            "multiline_allowed": bool(item.IsMultilineAllowed()),
            "line_spacing": float(item.GetLineSpacing()),
            "knockout": bool(item.IsKnockout()),
        }
        if not style["visible"]:
            raise IncompleteGeometry(
                "selected_or_obstacle_text_is_not_visible", object_uuid=_uuid(item)
            )
        return {
            "position": _xy(item.GetPosition()),
            "drawn_box": drawn,
            "box": bounding,
            "collision_box": drawn,
            "style": style,
        }
    except IncompleteGeometry:
        raise
    except (AttributeError, TypeError, RuntimeError, ValueError) as exc:
        raise IncompleteGeometry(
            "native_text_envelope_or_style_unavailable", object_uuid=_uuid(item)
        ) from exc


def _fields(fp):
    try:
        fields = [fp.Reference(), fp.Value(), *list(fp.GetFields())]
    except AttributeError as exc:
        raise IncompleteGeometry(
            "footprint_fields_cannot_be_enumerated", footprint_uuid=_uuid(fp)
        ) from exc
    distinct, seen = [], set()
    for item in fields:
        key = _object_key(item)
        if key not in seen:
            seen.add(key)
            distinct.append(item)
    return distinct


def select_targets(board, targets, pcbnew):
    """Resolve both identities through multimaps, never a ref-keyed overwrite."""
    footprints = list(board.GetFootprints())
    by_ref, by_uuid, field_ids = defaultdict(list), defaultdict(list), defaultdict(list)
    for fp in footprints:
        by_ref[str(fp.GetReference())].append(fp)
        by_uuid[_uuid(fp)].append(fp)
        for field in _fields(fp):
            field_ids[_uuid(field)].append((fp, field))
    selected = []
    for row in targets:
        identity = {k: row[k] for k in ("reference", "footprint_uuid", "reference_uuid")}
        candidates = by_ref[row["reference"]]
        if len(candidates) != 1:
            raise IncompleteGeometry(
                "reference_missing_or_ambiguous", **identity, matches=len(candidates)
            )
        if len(by_uuid[row["footprint_uuid"]]) != 1:
            raise IncompleteGeometry("footprint_uuid_missing_or_ambiguous", **identity)
        fp = candidates[0]
        if _uuid(fp) != row["footprint_uuid"]:
            raise IncompleteGeometry("reference_footprint_identity_mismatch", **identity)
        ref = fp.Reference()
        if len(field_ids[row["reference_uuid"]]) != 1 or _uuid(ref) != row["reference_uuid"]:
            raise IncompleteGeometry(
                "reference_field_uuid_missing_ambiguous_or_mismatched", **identity
            )
        if ref.GetText() != row["reference"]:
            raise IncompleteGeometry("reference_field_content_mismatch", **identity)
        if fp.IsLocked() or ref.IsLocked():
            raise IncompleteGeometry("selected_footprint_or_reference_is_locked", **identity)
        if ref.GetLayer() != pcbnew.F_SilkS:
            raise IncompleteGeometry("target_layer_mismatch", **identity)
        measure = text_measure(ref, pcbnew)
        if measure["position"] != point(row["from_mm"]):
            raise IncompleteGeometry("target_original_position_mismatch", **identity)
        selected.append({"target": row, "fp": fp, "ref": ref, "before": measure})
    return footprints, selected


def _pad_mask_box(pad, pcbnew):
    if pad.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
        raise IncompleteGeometry("unsupported_mask_pad_type", object_uuid=_uuid(pad))
    if pad.GetShape() not in (pcbnew.PAD_SHAPE_RECT, pcbnew.PAD_SHAPE_ROUNDRECT):
        raise IncompleteGeometry("unsupported_mask_pad_shape", object_uuid=_uuid(pad))
    if _angle(pad, "GetOrientationDegrees") != 0:
        raise IncompleteGeometry("unsupported_rotated_mask_pad", object_uuid=_uuid(pad))
    try:
        expansion = int(pad.GetSolderMaskExpansion(pcbnew.F_Mask))
    except (AttributeError, TypeError, RuntimeError) as exc:
        raise IncompleteGeometry(
            "effective_mask_expansion_unavailable", object_uuid=_uuid(pad)
        ) from exc
    box = _box_of(pad)
    expanded = (box[0] - expansion, box[1] - expansion, box[2] + expansion, box[3] + expansion)
    if expanded[2] <= expanded[0] or expanded[3] <= expanded[1]:
        raise IncompleteGeometry("mask_opening_collapsed_or_inverted", object_uuid=_uuid(pad))
    return expanded, expansion


def _native_bool(value, reason, item):
    if type(value) is not bool:
        raise IncompleteGeometry(reason, object_uuid=_uuid(item))
    return value


def _effective_box(item, layer):
    """Read an actual native layer shape, not a display-only rectangle."""
    try:
        return _bbox(item.GetEffectiveShape(layer).BBox())
    except (AttributeError, TypeError, RuntimeError, ValueError) as exc:
        raise IncompleteGeometry(
            "effective_mask_shape_unavailable", object_uuid=_uuid(item), layer=int(layer)
        ) from exc


def _mask_graphic_box(item, pcbnew):
    if hasattr(item, "GetText"):
        # Ordinary default stroke text on F.Mask is itself an opening. The
        # already-qualified native drawn envelope bounds the actual strokes.
        measure = text_measure(item, pcbnew)
        if measure["style"]["knockout"]:
            raise IncompleteGeometry("unsupported_mask_knockout_text", object_uuid=_uuid(item))
        return measure["collision_box"]
    try:
        supported = (
            pcbnew.SHAPE_T_SEGMENT,
            pcbnew.SHAPE_T_RECTANGLE,
            pcbnew.SHAPE_T_CIRCLE,
            pcbnew.SHAPE_T_ARC,
        )
        shape = item.GetShape()
    except (AttributeError, TypeError, RuntimeError) as exc:
        raise IncompleteGeometry(
            "mask_graphic_shape_cannot_be_identified", object_uuid=_uuid(item)
        ) from exc
    if shape not in supported:
        raise IncompleteGeometry("unsupported_mask_graphic_shape", object_uuid=_uuid(item))
    return _effective_box(item, pcbnew.F_Mask)


def _via_mask_observation(via, pcbnew):
    """Qualified KiCad10 ordinary through-via front mask; no guessed openings.

    PCB_VIA.GetEffectiveShape(F_Mask) does not add solder-mask expansion in
    KiCad10.0.6. Read the actual front circular padstack and effective margin.
    The returned rectangle conservatively encloses that opening, including an
    odd-nanometre diameter. It is not an exact circular collision calculation.
    """
    identity = {"object_uuid": _uuid(via), "layer": "F.Mask", "source": "via"}
    try:
        if via.GetViaType() != pcbnew.VIATYPE_THROUGH:
            raise IncompleteGeometry("unsupported_mask_via_type", **identity)
        if via.TopLayer() != pcbnew.F_Cu or via.BottomLayer() != pcbnew.B_Cu:
            raise IncompleteGeometry("unsupported_mask_via_layer_pair", **identity)
        if not _native_bool(
            via.IsOnLayer(pcbnew.F_Cu), "via_front_copper_membership_unproven", via
        ):
            raise IncompleteGeometry("via_front_copper_membership_mismatch", **identity)
        tented = _native_bool(via.IsTented(pcbnew.F_Mask), "effective_via_tenting_unproven", via)
        on_mask = _native_bool(via.IsOnLayer(pcbnew.F_Mask), "via_mask_membership_unproven", via)
        if on_mask == tented:
            raise IncompleteGeometry("via_tenting_and_mask_membership_disagree", **identity)
        if _native_bool(
            via.IsBackdrilledOrPostMachined(pcbnew.F_Cu),
            "via_post_machining_state_unproven",
            via,
        ):
            raise IncompleteGeometry("unsupported_mask_via_post_machining", **identity)
        padstack = via.Padstack()
        if padstack.Shape(pcbnew.F_Cu) != pcbnew.PAD_SHAPE_CIRCLE:
            raise IncompleteGeometry("unsupported_mask_via_padstack_shape", **identity)
        size = _xy(padstack.Size(pcbnew.F_Cu))
        width = int(via.GetWidth(pcbnew.F_Cu))
        if min(size) <= 0 or size[0] != size[1] or width != size[0]:
            raise IncompleteGeometry("via_front_circular_size_unproven", **identity)
        result = {
            **identity,
            "effective_tented": tented,
            "on_mask_layer": on_mask,
            "front_copper_diameter_nm": width,
            "geometry": "conservative_rectangle_around_front_circular_opening",
        }
        if tented:
            return {
                **result,
                "geometry": "effective_front_tenting_no_opening",
                "box_nm": None,
                "mask_expansion_nm": None,
            }
        expansion = int(via.GetSolderMaskExpansion())
        diameter = width + 2 * expansion
        if diameter <= 0:
            raise IncompleteGeometry("via_mask_opening_collapsed_or_inverted", **identity)
        x, y = _xy(via.GetPosition())
        radius = (diameter + 1) // 2
        return {
            **result,
            "mask_expansion_nm": expansion,
            "box_nm": [x - radius, y - radius, x + radius, y + radius],
        }
    except IncompleteGeometry:
        raise
    except (AttributeError, TypeError, RuntimeError, ValueError, OverflowError) as exc:
        raise IncompleteGeometry("required_via_mask_measurement_unavailable", **identity) from exc


def collect_environment(board, footprints, selected, pcbnew):
    selected_keys = {_object_key(row["ref"]) for row in selected}
    obstacles, copper, owner, mask_observations = [], [], {}, []
    edge_segments = []
    seen_objects = set()
    try:
        zones = list(board.Zones())
    except (AttributeError, TypeError, RuntimeError) as exc:
        raise IncompleteGeometry("board_mask_zones_cannot_be_enumerated") from exc
    for zone in zones:
        on_mask = _native_bool(zone.IsOnLayer(pcbnew.F_Mask), "zone_mask_membership_unproven", zone)
        if on_mask:
            raise IncompleteGeometry(
                "unsupported_mask_zone", object_uuid=_uuid(zone), layer="F.Mask"
            )

    def graphic(item, source):
        key = _object_key(item)
        if key in seen_objects:
            return
        seen_objects.add(key)
        layer = item.GetLayer()
        if layer == pcbnew.Edge_Cuts:
            if not hasattr(item, "GetShape") or item.GetShape() != pcbnew.SHAPE_T_SEGMENT:
                raise IncompleteGeometry(
                    "outline_contains_unsupported_curved_or_other_geometry", object_uuid=_uuid(item)
                )
            edge_segments.append((_xy(item.GetStart()), _xy(item.GetEnd())))
        if layer == pcbnew.F_Mask:
            box = _mask_graphic_box(item, pcbnew)
            observation = {
                "kind": "mask_graphic_conservative_rectangle",
                "object_uuid": _uuid(item),
                "source": source,
                "layer": "F.Mask",
                "box_nm": list(box),
            }
            mask_observations.append(dict(observation))
            obstacles.append(observation)
            return
        if layer == pcbnew.F_Cu:
            # A copper graphic can explicitly request a mask opening too.
            # Its shape expansion has different semantics from F.Mask graphics;
            # that combination is not part of this bounded implementation.
            on_mask = _native_bool(
                item.IsOnLayer(pcbnew.F_Mask), "graphic_mask_membership_unproven", item
            )
            if on_mask:
                raise IncompleteGeometry(
                    "unsupported_copper_graphic_mask_opening", object_uuid=_uuid(item)
                )
        if layer != pcbnew.F_SilkS or key in selected_keys:
            return
        if hasattr(item, "GetText"):
            if not item.IsVisible() or not item.GetText():
                return
            box = text_measure(item, pcbnew)["collision_box"]
            kind = "fixed_text"
        else:
            box, kind = _box_of(item), "silkscreen_graphic"
        obstacles.append(
            {"kind": kind, "object_uuid": _uuid(item), "source": source, "box_nm": list(box)}
        )

    for fp in footprints:
        body = []
        for pad in fp.Pads():
            body.append(_box_of(pad))
            on_mask = _native_bool(
                pad.IsOnLayer(pcbnew.F_Mask), "pad_mask_membership_unproven", pad
            )
            front_copper = _native_bool(
                pad.IsOnLayer(pcbnew.F_Cu), "pad_front_copper_membership_unproven", pad
            )
            if on_mask:
                mask, expansion = _pad_mask_box(pad, pcbnew)
                observation = {
                    "kind": "mask_opening_conservative_rectangle",
                    "object_uuid": _uuid(pad),
                    "footprint_uuid": _uuid(fp),
                    "source": "pad",
                    "layer": "F.Mask",
                    "mask_expansion_nm": expansion,
                    "box_nm": list(mask),
                }
                obstacles.append(observation)
                mask_observations.append(dict(observation))
            if front_copper:
                copper.append(
                    {
                        "kind": "pad_copper_projection",
                        "object_uuid": _uuid(pad),
                        "box_nm": list(_box_of(pad)),
                    }
                )
        for field in _fields(fp):
            graphic(field, "footprint_field")
        for item in fp.GraphicalItems():
            graphic(item, "footprint_graphic")
            if not hasattr(item, "GetText"):
                body.append(_box_of(item))
        owner[_uuid(fp)] = union(body)
    for item in board.GetDrawings():
        graphic(item, "board_drawing")
    for item in board.GetTracks():
        try:
            kind = item.Type()
            front_copper = _native_bool(
                item.IsOnLayer(pcbnew.F_Cu), "track_front_copper_membership_unproven", item
            )
            if kind == pcbnew.PCB_VIA_T:
                observation = _via_mask_observation(item, pcbnew)
                mask_observations.append(observation)
                if observation["on_mask_layer"]:
                    obstacles.append(
                        {"kind": "via_mask_opening_conservative_rectangle", **observation}
                    )
            elif kind in (pcbnew.PCB_TRACE_T, pcbnew.PCB_ARC_T):
                on_mask = _native_bool(
                    item.IsOnLayer(pcbnew.F_Mask), "track_mask_membership_unproven", item
                )
                if on_mask:
                    if not front_copper:
                        raise IncompleteGeometry(
                            "track_mask_and_copper_layer_mismatch", object_uuid=_uuid(item)
                        )
                    observation = {
                        "kind": "track_mask_opening_conservative_rectangle",
                        "object_uuid": _uuid(item),
                        "source": "track",
                        "layer": "F.Mask",
                        "box_nm": list(_effective_box(item, pcbnew.F_Mask)),
                    }
                    mask_observations.append(dict(observation))
                    obstacles.append(observation)
            else:
                raise IncompleteGeometry(
                    "unsupported_track_collection_object", object_uuid=_uuid(item)
                )
        except IncompleteGeometry:
            raise
        except (AttributeError, TypeError, RuntimeError, ValueError) as exc:
            raise IncompleteGeometry(
                "track_or_via_mask_classification_unavailable", object_uuid=_uuid(item)
            ) from exc
        if front_copper:
            copper.append(
                {
                    "kind": "track_or_via_copper_projection",
                    "object_uuid": _uuid(item),
                    "box_nm": list(_box_of(item)),
                }
            )
    return obstacles, copper, owner, rectangle_outline(edge_segments), mask_observations


def evaluate_candidates(records, obstacles, copper, owner, bounds, clearance):
    """Evaluate all destinations together; a selected old position is not fixed."""
    checks = []
    for record in records:
        target, before, after = record["target"], record["before"], record["after"]
        box = after["collision_box"]
        conflicts = [
            dict(obstacle) for obstacle in obstacles if overlap(box, obstacle["box_nm"], clearance)
        ]
        for other in records:
            if other is record:
                continue
            if overlap(box, other["after"]["collision_box"], clearance):
                conflicts.append(
                    {
                        "kind": "other_selected_reference_destination",
                        "object_uuid": other["target"]["reference_uuid"],
                        "box_nm": list(other["after"]["collision_box"]),
                    }
                )
        owner_box = owner[target["footprint_uuid"]]
        owner_gap2 = gap_squared(box, owner_box)
        in_edge = inside(box, bounds, clearance)
        owner_near = owner_gap2 <= MAX_OWNER_GAP_NM * MAX_OWNER_GAP_NM
        projected = [dict(item) for item in copper if overlap(box, item["box_nm"])]
        checks.append(
            {
                "ref": target["reference"],
                "footprint_uuid": target["footprint_uuid"],
                "reference_uuid": target["reference_uuid"],
                "layer": target["layer"],
                "from_nm": list(before["position"]),
                "to_nm": list(after["position"]),
                "style": before["style"],
                "bbox_before_nm": list(before["box"]),
                "bbox_after_nm": list(after["box"]),
                "drawn_bbox_before_nm": list(before["drawn_box"]),
                "drawn_bbox_after_nm": list(after["drawn_box"]),
                "box_mm": [v / NM_PER_MM for v in box],
                "inside_edge": in_edge,
                "clear": not conflicts and in_edge and owner_near,
                "manufacturing_conflicts": conflicts,
                "owner_body_bbox_nm": list(owner_box),
                "owner_body_gap_mm": math.sqrt(owner_gap2) / NM_PER_MM,
                "owner_body_within_3mm": owner_near,
                "translation_distance_mm": math.dist(before["position"], after["position"])
                / NM_PER_MM,
                "reading_copper_projection_advisories": projected,
                "copper_projection_is_manufacturing_violation": False,
            }
        )
    return checks


def measure_targets(board, pcbnew, targets, clearance_mm):
    """Return a whole-batch report; only the caller's in-memory board is changed."""
    normalized = []
    try:
        normalized = normalize_targets(targets)
        clearance = nm(clearance_mm)
        if not 0 <= clearance <= 10_000_000:
            raise IncompleteGeometry("silkscreen_clearance_outside_0_to_10mm")
        footprints, selected = select_targets(board, normalized, pcbnew)
        obstacles, copper, owner, bounds, mask_observations = collect_environment(
            board, footprints, selected, pcbnew
        )
        # Environment and every original identity/style are measured before any
        # selected in-memory destination is set. No partial file is ever saved.
        for record in selected:
            before = record["before"]
            destination = point(record["target"]["to_mm"])
            dx, dy = destination[0] - before["position"][0], destination[1] - before["position"][1]
            record["ref"].SetPosition(pcbnew.VECTOR2I(*destination))
            after = text_measure(record["ref"], pcbnew)
            if (
                after["position"] != destination
                or after["style"] != before["style"]
                or after["drawn_box"] != shift(before["drawn_box"], dx, dy)
                or after["box"] != shift(before["box"], dx, dy)
            ):
                raise IncompleteGeometry(
                    "native_translation_did_not_preserve_style_and_envelope",
                    reference=record["target"]["reference"],
                    reference_uuid=record["target"]["reference_uuid"],
                )
            record["after"] = after
        checks = evaluate_candidates(selected, obstacles, copper, owner, bounds, clearance)
        stuck = [check["ref"] for check in checks if not check["clear"]]
        moves = []
        if not stuck:
            for record in selected:
                target, before, after = record["target"], record["before"], record["after"]
                if before["position"] == after["position"]:
                    continue
                relative = _xy(record["ref"].GetFPRelativePosition())
                moves.append(
                    {
                        "ref": target["reference"],
                        "footprint_uuid": target["footprint_uuid"],
                        "reference_uuid": target["reference_uuid"],
                        "layer": target["layer"],
                        "from_mm": target["from_mm"],
                        "to_mm": target["to_mm"],
                        "from_nm": list(before["position"]),
                        "to_nm": list(after["position"]),
                        "relative_nm": list(relative),
                        "style": before["style"],
                        "bbox_before_nm": list(before["box"]),
                        "bbox_after_nm": list(after["box"]),
                        "drawn_bbox_before_nm": list(before["drawn_box"]),
                        "drawn_bbox_after_nm": list(after["drawn_box"]),
                    }
                )
        return {
            "schema": "targeted-silkscreen/v1",
            "selection_mode": "explicit_targets",
            "complete": not stuck,
            "targets": normalized,
            "references": len(selected),
            "moves": moves,
            "stuck": stuck,
            "checks": checks,
            "mask_observations": mask_observations,
            "issues": (
                [
                    {
                        "reason": "selected_destination_not_clear_or_too_far_from_owner",
                        "references": stuck,
                    }
                ]
                if stuck
                else []
            ),
            "clearance_mm": clearance / NM_PER_MM,
            "units": "mm; *_nm integer nanometres",
            "bounds_mm": [v / NM_PER_MM for v in bounds],
            "bounds_nm": list(bounds),
            "native_version": pcbnew.GetBuildVersion(),
            "limits": (
                "Explicit F.SilkS Reference translations only; ordinary single-line default KiCad "
                "stroke text at zero degrees, unmirrored; unchanged style and visibility. Native "
                "drawn and bounding envelopes are both checked for exact translation. Mask "
                "rectangles use actual effective expansion for zero-degree SMD rect/roundrect "
                "pads; these conservative rectangles are not exact mask polygons. Same-side "
                "visible fields/text and graphic rectangles are fixed obstacles. Actual front "
                "mask graphics and exposed track shapes use conservative native layer-shape "
                "rectangles; ordinary circular through-via front openings use effective "
                "tenting, front padstack diameter and mask expansion. Unknown via tenting, "
                "unsupported padstacks, mask graphics or required APIs are incomplete, not "
                "absence of openings. Tented vias are recorded without claiming filled holes. "
                "Only one "
                "closed axis-aligned rectangular outline is supported. Maximum translation "
                "5mm and maximum conservative owner-body envelope gap 3mm. Copper projection "
                "is a reading advisory, not a manufacturing collision. Font glyph interpretation, "
                "supplier process, mask registration and human readability need separate review."
            ),
        }
    except IncompleteGeometry as exc:
        return {
            "schema": "targeted-silkscreen/v1",
            "selection_mode": "explicit_targets",
            "complete": False,
            "targets": normalized,
            "references": len(normalized),
            "moves": [],
            "stuck": [row["reference"] for row in normalized],
            "checks": [],
            "mask_observations": [],
            "issues": [exc.issue],
            "clearance_mm": clearance_mm,
            "limits": (
                "Required identity, style or geometry was not proven; no complete placement result."
            ),
        }
    except (AttributeError, TypeError, RuntimeError, ValueError, OverflowError) as exc:
        # A missing native accessor is an incomplete measurement, never a clean
        # result and never permission to substitute guessed geometry.
        return {
            "schema": "targeted-silkscreen/v1",
            "selection_mode": "explicit_targets",
            "complete": False,
            "targets": normalized,
            "references": len(normalized),
            "moves": [],
            "stuck": [row["reference"] for row in normalized],
            "checks": [],
            "mask_observations": [],
            "issues": [
                {
                    "reason": "required_native_measurement_unavailable",
                    "exception_type": type(exc).__name__,
                }
            ],
            "clearance_mm": clearance_mm,
            "limits": "Required native measurement was unavailable; no complete placement result.",
        }
