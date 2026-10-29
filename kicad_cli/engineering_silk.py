"""Use externally installed KiCad for text bounds; edit only reference translations."""

import json
import subprocess
from decimal import Decimal, DecimalException
from pathlib import Path
from uuid import UUID

from .sync_inputs import InputError

TARGET_SCHEMA = "silkscreen-targets/v1"
TARGET_SCHEMA_REF = "urn:kicad-cli:silkscreen-request:1#/$defs/targets_file"
TARGET_KEYS = {"reference", "footprint_uuid", "reference_uuid", "layer", "from_mm", "to_mm"}


def invalid(message, **details):
    return InputError(
        message,
        parameter="--targets",
        schema_ref=TARGET_SCHEMA_REF,
        next_action=(
            "Read reference --command 'board silkscreen place'; "
            "correct the target file and request a new preview."
        ),
        **details,
    )


def point_nm(value):
    """Exact decimal millimetres; never round a caller's requested position."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise invalid("Target position must contain exactly two numeric millimetre coordinates")
    result = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float, Decimal)):
            raise invalid("Target coordinates must be finite numbers, not booleans or strings")
        try:
            nm = Decimal(str(item)) * 1_000_000
        except DecimalException as exc:
            raise invalid("Target coordinate is invalid") from exc
        if not nm.is_finite() or nm != nm.to_integral_value():
            raise invalid(
                "Target coordinates must be finite and exactly representable in nanometres"
            )
        if not -(2**31) <= nm <= 2**31 - 1:
            raise invalid("Target coordinates exceed native signed-32-bit nanometre storage")
        result.append(int(nm))
    return result


def targets_document(raw):
    """Validate the captured file before starting the native bounds process."""

    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise invalid("Duplicate JSON object key", key=key)
            value[key] = item
        return value

    try:
        value = json.loads(raw, parse_float=Decimal, object_pairs_hook=pairs)
    except (ValueError, UnicodeDecodeError) as exc:
        if isinstance(exc, InputError):
            raise
        raise invalid("Targets file is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict) or set(value) != {"schema", "targets"}:
        raise invalid("Targets file requires exactly schema and targets")
    rows = value["targets"]
    if value["schema"] != TARGET_SCHEMA or not isinstance(rows, list) or not rows:
        raise invalid(
            "Targets file requires schema silkscreen-targets/v1 and a nonempty targets array"
        )
    seen = {"reference": set(), "footprint_uuid": set(), "reference_uuid": set()}
    result = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != TARGET_KEYS:
            raise invalid("Each target requires exactly the documented six fields", index=index)
        name = row["reference"]
        if (
            not isinstance(name, str)
            or not name
            or name.strip() != name
            or "\n" in name
            or "\r" in name
        ):
            raise invalid(
                "Target reference must be a nonempty single-line exact reference", index=index
            )
        clean = dict(row)
        for key in ("footprint_uuid", "reference_uuid"):
            try:
                clean[key] = str(UUID(row[key]))
            except (ValueError, TypeError, AttributeError) as exc:
                raise invalid("Target identity must be a UUID", index=index, field=key) from exc
        if row["layer"] != "F.SilkS":
            raise invalid("Explicit targets currently support only F.SilkS", index=index)
        for key in seen:
            if clean[key] in seen[key]:
                raise invalid("Repeated target identity or reference", index=index, field=key)
            seen[key].add(clean[key])
        for key in ("from_mm", "to_mm"):
            clean[key] = [n / 1_000_000 for n in point_nm(row[key])]
        result.append(clean)
    return result


def _uuid(node):
    values = node.find_all("uuid")
    return values[0].value(1) if len(values) == 1 else None


def _locked(node):
    value = node.find("locked")
    return (value is not None and value.value(1) != "no") or "locked" in node.items


def selected_fields(board, targets):
    """Resolve every selected property before any candidate translation is written."""
    from .fileformat.geometry import rotate

    all_uuids = [node.value(1) for node in board.root.walk("uuid")]
    result = {}
    for row in targets:
        name, fp_id, field_id = row["reference"], row["footprint_uuid"], row["reference_uuid"]
        matching = [fp for fp in board.footprints if fp.reference == name]
        if len(matching) != 1:
            raise invalid("Target reference is absent or ambiguous", reference=name)
        fp = matching[0]
        properties = [p for p in fp.node.find_all("property") if p.value(1) == "Reference"]
        if len(properties) != 1 or _uuid(fp.node) != fp_id or _uuid(properties[0]) != field_id:
            raise invalid(
                "Target footprint or Reference field identity does not match", reference=name
            )
        if any(all_uuids.count(uid) != 1 for uid in (fp_id, field_id)):
            raise invalid("Target UUID is repeated in the board", reference=name)
        prop = properties[0]
        effects = prop.find("effects")
        font = effects.find("font") if effects is not None else None
        if font is not None and any(face.value(1) for face in font.find_all("face")):
            raise invalid(
                "Explicit target font face is unsupported; "
                "only implicit default KiCad stroke text is qualified",
                reference=name,
            )
        if fp.locked or _locked(prop):
            raise invalid(
                "Locked target cannot be translated",
                reference=name,
                footprint_uuid=fp_id,
                reference_uuid=field_id,
            )
        layers = prop.find_all("layer")
        if len(layers) != 1 or layers[0].value(1) != row["layer"]:
            raise invalid("Target Reference layer does not match", reference=name)
        at = prop.find("at")
        if at is None:
            raise invalid("Target Reference position is missing", reference=name)
        try:
            relative = point_nm([Decimal(at.atom(1)), Decimal(at.atom(2))])
        except (DecimalException, TypeError) as exc:
            raise invalid("Target Reference position cannot be read", reference=name) from exc
        dx, dy = rotate(*relative, fp.angle)
        world = [round(dx + fp.position[0]), round(dy + fp.position[1])]
        if world != point_nm(row["from_mm"]):
            raise invalid(
                "Target Reference original position does not match",
                reference=name,
                observed_nm=world,
            )
        result[(fp_id, field_id)] = prop
    return result


def _integer_vector(value, size, label):
    if (
        not isinstance(value, list)
        or len(value) != size
        or any(type(v) is not int or not -(2**31) <= v <= 2**31 - 1 for v in value)
    ):
        raise invalid("Native targeted silk integer geometry is missing or invalid", field=label)
    return value


def _measured_box(value, label):
    box = _integer_vector(value, 4, label)
    if box[0] >= box[2] or box[1] >= box[3]:
        raise invalid("Native targeted silk envelope is empty or inverted", field=label)
    return box


def _measured_style(style, reference):
    import math

    booleans = {
        "visible",
        "font_pointer_present",
        "auto_thickness",
        "mirrored",
        "bold",
        "italic",
        "keep_upright",
        "multiline_allowed",
        "knockout",
    }
    integers = {
        "thickness_nm",
        "effective_pen_width_nm",
        "horizontal_justify",
        "vertical_justify",
        "layer",
    }
    keys = (
        booleans
        | integers
        | {"text", "font_name", "font_property", "size_nm", "angle_degrees", "line_spacing"}
    )
    if not isinstance(style, dict) or set(style) != keys:
        raise invalid("Native targeted silk style measurement is incomplete")
    if any(not isinstance(style[key], str) for key in ("text", "font_name", "font_property")):
        raise invalid("Native targeted silk style text is invalid")
    if any(type(style[key]) is not bool for key in booleans) or any(
        type(style[key]) is not int for key in integers
    ):
        raise invalid("Native targeted silk style types are invalid")
    if (
        style["text"] != reference
        or style["font_name"] != ""
        or style["font_property"] not in {"", "KiCad Font"}
        or style["font_pointer_present"]
        or not style["visible"]
        or style["mirrored"]
    ):
        raise invalid("Native targeted silk style does not match the supported Reference")
    size = _integer_vector(style["size_nm"], 2, "size_nm")
    if (
        min(size) <= 0
        or style["thickness_nm"] <= 0
        or style["effective_pen_width_nm"] <= 0
        or style["layer"] < 0
        or style["horizontal_justify"] not in {-1, 0, 1}
        or style["vertical_justify"] not in {-1, 0, 1}
    ):
        raise invalid("Native targeted silk style dimensions or alignment are invalid")
    for key in ("angle_degrees", "line_spacing"):
        if type(style[key]) not in (int, float) or not math.isfinite(style[key]):
            raise invalid("Native targeted silk style has a nonfinite measurement", field=key)
    if style["angle_degrees"] != 0 or style["line_spacing"] <= 0:
        raise invalid("Native targeted silk style orientation or spacing is unsupported")
    return style


def _measurement(row, target):
    if not isinstance(row, dict):
        raise invalid("Native targeted silk measurement is not an object")
    for key, expected in (
        ("ref", target["reference"]),
        ("footprint_uuid", target["footprint_uuid"]),
        ("reference_uuid", target["reference_uuid"]),
        ("layer", target["layer"]),
    ):
        if row.get(key) != expected:
            raise invalid("Native targeted silk measured identity does not match", field=key)
    old = _integer_vector(row.get("from_nm"), 2, "from_nm")
    new = _integer_vector(row.get("to_nm"), 2, "to_nm")
    if old != point_nm(target["from_mm"]) or new != point_nm(target["to_mm"]):
        raise invalid("Native targeted silk measured position does not match the frozen request")
    style = _measured_style(row.get("style"), target["reference"])
    dx, dy = new[0] - old[0], new[1] - old[1]
    if dx * dx + dy * dy > 5_000_000**2:
        raise invalid("Native targeted silk translation exceeds the finite 5 mm limit")
    for prefix in ("bbox", "drawn_bbox"):
        before = _measured_box(row.get(prefix + "_before_nm"), prefix + "_before_nm")
        after = _measured_box(row.get(prefix + "_after_nm"), prefix + "_after_nm")
        if after != [before[0] + dx, before[1] + dy, before[2] + dx, before[3] + dy]:
            raise invalid(
                "Native targeted silk dual envelopes do not preserve exact translation",
                field=prefix,
            )
    return {
        key: row[key]
        for key in (
            "ref",
            "footprint_uuid",
            "reference_uuid",
            "layer",
            "from_nm",
            "to_nm",
            "bbox_before_nm",
            "bbox_after_nm",
            "drawn_bbox_before_nm",
            "drawn_bbox_after_nm",
        )
    } | {"style": style}


def _reported_identity(row):
    if not isinstance(row, dict) or any(
        not isinstance(row.get(key), str) or not row[key]
        for key in ("footprint_uuid", "reference_uuid")
    ):
        raise invalid("Native targeted silk identity measurement is invalid")
    return row["footprint_uuid"], row["reference_uuid"]


def _mask_observations(result):
    """Consume each reported front opening, including proved no-opening vias."""
    observations = result.get("mask_observations")
    if not isinstance(observations, list):
        raise invalid("Native targeted silk solder-mask observations are missing")
    boxes, seen = [], set()
    base = {"object_uuid", "source", "layer", "box_nm"}
    for row in observations:
        if not isinstance(row, dict) or any(
            not isinstance(row.get(key), str) or not row[key]
            for key in ("object_uuid", "source", "layer")
        ):
            raise invalid("Native targeted silk solder-mask identity is incomplete")
        identity = row["source"], row["object_uuid"]
        if identity in seen or row["layer"] != "F.Mask":
            raise invalid("Native targeted silk solder-mask identity/layer is ambiguous")
        seen.add(identity)
        if row["source"] == "via":
            if set(row) != base | {
                "effective_tented",
                "on_mask_layer",
                "front_copper_diameter_nm",
                "geometry",
                "mask_expansion_nm",
            }:
                raise invalid("Native via solder-mask observation fields are incomplete")
            tented, on_mask = row["effective_tented"], row["on_mask_layer"]
            if type(tented) is not bool or type(on_mask) is not bool or tented == on_mask:
                raise invalid("Native via effective tenting or mask membership is unproved")
            width = row["front_copper_diameter_nm"]
            if type(width) is not int or width <= 0:
                raise invalid("Native via front circular padstack diameter is unproved")
            if tented:
                if (
                    row["geometry"] != "effective_front_tenting_no_opening"
                    or row["box_nm"] is not None
                    or row["mask_expansion_nm"] is not None
                ):
                    raise invalid("Tented via observation claims an inconsistent mask opening")
                continue
            if (
                row["geometry"] != "conservative_rectangle_around_front_circular_opening"
                or type(row["mask_expansion_nm"]) is not int
            ):
                raise invalid("Exposed via mask shape or effective expansion is unproved")
            diameter = width + 2 * row["mask_expansion_nm"]
            if diameter <= 0:
                raise invalid("Exposed via mask opening is collapsed")
            box = _measured_box(row["box_nm"], "mask_box_nm")
            expected = 2 * ((diameter + 1) // 2)
            if box[2] - box[0] != expected or box[3] - box[1] != expected:
                raise invalid("Exposed via mask box differs from reported circular opening")
        else:
            expected = base | {"kind"}
            if row["source"] == "pad":
                expected |= {"footprint_uuid", "mask_expansion_nm"}
                if (
                    row.get("kind") != "mask_opening_conservative_rectangle"
                    or not isinstance(row.get("footprint_uuid"), str)
                    or not row["footprint_uuid"]
                    or type(row.get("mask_expansion_nm")) is not int
                ):
                    raise invalid("Native pad effective mask geometry is incomplete")
            elif row["source"] == "track":
                if row.get("kind") != "track_mask_opening_conservative_rectangle":
                    raise invalid("Native track effective mask geometry is incomplete")
            elif row["source"] in {"board_drawing", "footprint_graphic", "footprint_field"}:
                if row.get("kind") != "mask_graphic_conservative_rectangle":
                    raise invalid("Native front-mask graphic geometry is incomplete")
            else:
                raise invalid("Native solder-mask object source is unsupported")
            if set(row) != expected:
                raise invalid("Native solder-mask observation fields differ from its source")
            box = _measured_box(row["box_nm"], "mask_box_nm")
        boxes.append(box)
    return boxes


def validate_result(result, targets, *, clearance_mm=None):
    """Require each native measurement and move to agree with the frozen selection."""
    if (
        not isinstance(result, dict)
        or result.get("selection_mode") != "explicit_targets"
        or result.get("schema") != "targeted-silkscreen/v1"
        or result.get("complete") is not True
    ):
        raise invalid("Native targeted silk preparation is incomplete", native_report=result)
    if (
        result.get("targets") != targets
        or result.get("stuck") != []
        or result.get("issues") != []
        or type(result.get("references")) is not int
        or result["references"] != len(targets)
    ):
        raise invalid(
            "Native targeted silk selection is incomplete or mismatched", native_report=result
        )
    mask_boxes = _mask_observations(result)
    clearance = point_nm([result.get("clearance_mm"), 0])[0]
    if not 0 <= clearance <= 10_000_000 or (
        clearance_mm is not None and clearance != point_nm([clearance_mm, 0])[0]
    ):
        raise invalid("Native targeted silk clearance differs from the frozen request")
    expected = {(r["footprint_uuid"], r["reference_uuid"]): r for r in targets}
    changed = {
        key for key, row in expected.items() if point_nm(row["from_mm"]) != point_nm(row["to_mm"])
    }
    moves = result.get("moves")
    if not isinstance(moves, list):
        raise invalid("Native targeted silk moves are missing")
    moved = {}
    for move in moves:
        if not isinstance(move, dict):
            raise invalid("Native targeted silk move is not an object")
        key = _reported_identity(move)
        if key not in changed or key in moved:
            raise invalid("Native targeted silk moved an unselected, duplicate or no-op field")
        target = expected[key]
        moved[key] = _measurement(move, target)
        _integer_vector(move.get("relative_nm"), 2, "relative_nm")
        if point_nm(move.get("from_mm")) != point_nm(target["from_mm"]) or point_nm(
            move.get("to_mm")
        ) != point_nm(target["to_mm"]):
            raise invalid("Native targeted silk move millimetres disagree with integer positions")
    if set(moved) != changed:
        raise invalid("Native targeted silk did not translate every requested field")
    checks = result.get("checks")
    if not isinstance(checks, list) or len(checks) != len(expected):
        raise invalid("Native targeted silk checks are incomplete")
    seen = set()
    for check in checks:
        if not isinstance(check, dict):
            raise invalid("Native targeted silk check is not an object")
        key = _reported_identity(check)
        if key not in expected or key in seen:
            raise invalid("Native targeted silk check identities are incomplete")
        seen.add(key)
        measured = _measurement(check, expected[key])
        text_box = measured["drawn_bbox_after_nm"]
        for mask_box in mask_boxes:
            if not (
                text_box[2] + clearance <= mask_box[0]
                or mask_box[2] + clearance <= text_box[0]
                or text_box[3] + clearance <= mask_box[1]
                or mask_box[3] + clearance <= text_box[1]
            ):
                raise invalid("Reported front solder-mask opening conflicts with a target")
        if key in moved and moved[key] != measured:
            raise invalid("Native targeted silk move and check measurements disagree")
        if (
            check.get("inside_edge") is not True
            or check.get("clear") is not True
            or check.get("manufacturing_conflicts") != []
            or check.get("owner_body_within_3mm") is not True
        ):
            raise invalid(
                "Native targeted silk measured conflict prevents the entire batch",
                native_report=result,
            )


def prepare(primary, params, targets=None):
    from . import kicad_env
    from .fileformat.board import Board
    from .fileformat.sexpr import number

    python = kicad_env.find_python()
    if not python:
        raise ValueError("External KiCad interpreter is required for native font bounds")
    board = Board.load(primary)
    selected = selected_fields(board, targets) if targets is not None else None
    request = primary.parent / "silk-request.json"
    arguments = {"board": str(primary), "clearance_mm": params["clearance"]}
    if targets is not None:
        arguments["targets"] = targets
    request.write_text(json.dumps(arguments), encoding="utf-8")
    worker = Path(__file__).parent / "payload" / "engineering_silk.py"
    child = subprocess.run(
        [python, "-B", str(worker), str(request)], capture_output=True, timeout=120
    )
    if child.returncode:
        raise ValueError(
            "Native silk bounds preparation failed: "
            + child.stderr.decode("utf-8", "replace")[-800:]
        )
    result = json.loads(child.stdout)
    if targets is not None:
        validate_result(result, targets, clearance_mm=params["clearance"])
    by_ref = {fp.reference: fp for fp in board.footprints}
    for move in result["moves"]:
        if selected is not None:
            prop = selected[(move["footprint_uuid"], move["reference_uuid"])]
        else:
            fp = by_ref[move["ref"]]
            prop = next(p for p in fp.node.find_all("property") if p.value(1) == "Reference")
        at = prop.find("at")
        x, y = move["relative_nm"]
        at.set(1, number(x / 1000000))
        at.set(2, number(y / 1000000))
    if targets is not None:
        selected_fields(board, [{**row, "from_mm": row["to_mm"]} for row in targets])
    primary.write_bytes(board.document.dumps().encode())
    return result
