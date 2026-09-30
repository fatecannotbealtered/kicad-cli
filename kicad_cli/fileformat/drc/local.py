"""The checks one item answers by itself: its sizes, its padstack, its text.

What KiCad 10 does, measured on `tests/fixtures/drc/drc1`:

- A track narrower than the board's minimum is too narrow, an arc as well.
- A via: its diameter against the board's minimum -- a micro via's against
  its net class's micro via diameter, though the message still says board
  setup; its hole against the minimum hole, a micro via's against the micro
  via minimum; its ring, (diameter - hole) / 2, against the minimum annular
  width, whatever kind of via it is.
- A pad's hole is held to the minimum hole too, plated or not; a slot by its
  width. A plated pad's ring is how much copper is left around its hole at
  the narrowest -- none where the hole reaches out of the copper -- and a
  hole of another pad or via that cuts into the copper narrows it: a pad
  drawn twice over, one on top of the other, has no ring at all. A pad whose
  hole takes all of its copper has no ring to measure, and is questioned
  instead (`padstack`), as is one whose hole is exactly as big.
- A padstack is questioned for what KiCad's pad dialog would question: a
  surface pad with no outer copper, with copper on both sides, or with its
  mask or paste on the side its copper is not; paste on a connector pad; a
  pad property that does not suit the pad (BGA on anything but a surface
  pad, castellated on anything but a plated one, heatsink, test point or
  fiducial on an unplated one); and paste that a negative margin shrinks to
  nothing -- the pad's own margin and ratio, when margin + ratio x size
  reaches -size; the footprint's margin is not counted. It is invalid when a
  plated pad has no hole, or a custom pad's pieces do not all touch.
- A footprint said to be through-hole whose pads are all surface pads, or
  the other way round, is a type mismatch: plated pads make it through-hole
  and surface pads on copper make it SMD, heatsink, castellated,
  mechanical and fiducial pads counting for neither.
- Text: only what is shown. On a silkscreen, its height against the
  minimum text height and its stroke against the minimum thickness -- the
  stroke as drawn, which is at most a quarter of the text's smaller size.
  Text on a front layer must not be mirrored and text on a back layer must
  be, whatever the layer; text on Edge.Cuts is an error of its own.
"""

from __future__ import annotations

import re
from collections import defaultdict

from ..board import Footprint, Pad, nm
from ..sexpr import List
from . import items as describe
from . import mm
from .shapes import Shape, covers, depth, distance, hole, pad_copper, ring, via_hole

# The smallest hole KiCad calls a hole at all.
NO_HOLE = 4


BOARD = "board setup constraints"


def limits(run, kind: str, subject, layer: str | None, least: int | None):
    """What an item is held to for a kind of constraint: ((min, who says
    so), (max, who says so)) -- each bound the last custom rule to set it
    (`rules.py`), else the board's minimum and no maximum."""
    low, high = (least, BOARD, None), (None, BOARD, None)
    rules = run.settings.dru
    if rules is not None and rules.rules and subject is not None:
        found = rules.find(kind, subject, layer=layer, bound="min")
        if found is not None:
            low = (found[1].min, f"rule '{found[0].name}'", found[0].severity)
        found = rules.find(kind, subject, layer=layer, bound="max")
        if found is not None:
            high = (found[1].max, f"rule '{found[0].name}'", found[0].severity)
    return low, high


def _out_of(value: int, limit) -> tuple[str, int, str, str | None] | None:
    """Which bound a value breaks: ("min" or "max", the bound, who set it,
    the severity its rule gives)."""
    (least, low_who, low_severity), (most, high_who, high_severity) = limit
    if least is not None and value < least:
        return "min", least, low_who, low_severity
    if most is not None and value > most:
        return "max", most, high_who, high_severity
    return None


def tracks(run) -> None:
    if not run.on("track_width"):
        return
    board, least = run.board, run.settings.nm("min_track_width")
    rules = run.settings.dru
    for track in board.tracks:
        subject = None
        if rules is not None and rules.rules:
            from . import subjects  # noqa: PLC0415
            from .copper import arc_chords  # noqa: PLC0415
            from .shapes import Shape  # noqa: PLC0415

            pieces = (
                arc_chords(track.start, track.mid, track.end, track.width)
                if track.kind == "arc" and track.mid is not None
                else [Shape((track.start, track.end), track.width / 2)]
            )  # fmt: skip
            subject = subjects.of_track(run, track, pieces)
        broken = _out_of(track.width, limits(run, "track_width", subject, track.layer, least))
        if broken is not None:
            bound, value, who, severity = broken
            run.report(
                "track_width",
                f"Track width ({who} {bound} width {mm(value)}; actual {mm(track.width)})",
                [describe.track(board, track)],
                severity=severity,
            )


def vias(run) -> None:
    board, settings = run.board, run.settings
    rules = settings.dru
    for via in board.vias:
        item = describe.via(board, via)
        micro = via.kind == "micro"
        subject = None
        if rules is not None and rules.rules:
            from . import subjects  # noqa: PLC0415

            subject = subjects.of_via(run, via)
        least = (
            settings.netclasses.of(via.net).nm("microvia_diameter") if micro
            else settings.nm("min_via_diameter")
        )  # fmt: skip
        broken = _out_of(via.diameter, limits(run, "via_diameter", subject, None, least))
        if broken is not None:
            bound, value, who, severity = broken
            run.report(
                "via_diameter",
                f"Via diameter ({who} {bound} diameter {mm(value)}; actual {mm(via.diameter)})",
                [item],
                severity=severity,
            )
        annular = (via.diameter - via.drill) // 2
        least = settings.nm("min_via_annular_width")
        broken = _out_of(annular, limits(run, "annular_width", subject, None, least))
        if broken is not None:
            bound, value, who, severity = broken
            run.report(
                "annular_width",
                f"Annular width ({who} {bound} annular width {mm(value)}; actual {mm(annular)})",
                [item],
                severity=severity,
            )
        least = settings.nm("min_microvia_drill" if micro else "min_through_hole_diameter")
        broken = _out_of(via.drill, limits(run, "hole_size", subject, None, least))
        if broken is not None:
            bound, value, who, severity = broken
            rule = "microvia_drill_out_of_range" if micro else "drill_out_of_range"
            what = "Micro via hole size" if micro else "Hole size"
            run.report(
                rule,
                f"{what} out of range ({who} {bound} hole {mm(value)}; actual {mm(via.drill)})",
                [item],
                severity=severity,
            )


# -- pads -----------------------------------------------------------------------------------


def _properties(pad: Pad) -> set[str]:
    node = pad.node
    found = set()
    for prop in node.find_all("property") if node is not None else []:
        found.update(a for a in prop.items[1:] if isinstance(a, str))
    return found


def _outer(pad: Pad) -> tuple[bool, bool]:
    return "F.Cu" in pad.layers, "B.Cu" in pad.layers


HOLE_CELL = 2_000_000  # nm: the grid that finds which holes are near a pad


def _holes(board) -> dict[tuple[int, int], list]:
    """Every hole on the board, (shape, owner) -- a pad or a via -- by the
    grid cells its box covers."""
    grid: dict[tuple[int, int], list] = defaultdict(list)
    found = [
        (hole(pad), pad) for fp in board.footprints for pad in fp.pads
        if pad.kind in ("thru_hole", "np_thru_hole")
    ] + [(via_hole(via), via) for via in board.vias]  # fmt: skip
    for shape, owner in found:
        if shape is None:
            continue
        x1, y1, x2, y2 = shape.bbox()
        for cx in range(int(x1 // HOLE_CELL), int(x2 // HOLE_CELL) + 1):
            for cy in range(int(y1 // HOLE_CELL), int(y2 // HOLE_CELL) + 1):
                grid[(cx, cy)].append((shape, owner))
    return grid


def _near(grid, box):
    seen = set()
    for cx in range(int(box[0] // HOLE_CELL), int(box[2] // HOLE_CELL) + 1):
        for cy in range(int(box[1] // HOLE_CELL), int(box[3] // HOLE_CELL) + 1):
            for entry in grid.get((cx, cy), ()):
                if id(entry[1]) not in seen:
                    seen.add(id(entry[1]))
                    yield entry


def _annular(pad: Pad, own: Shape, copper: list[Shape], others) -> float | None:
    """The ring of copper around a plated pad's hole, or None where the hole
    is bigger than the copper all round and leaves none to measure. A hole
    exactly as big leaves a ring of nothing."""
    if all(_strictly_inside(piece, own) for piece in copper):
        return None
    best = max(ring(piece, own) for piece in copper)
    box = _box(copper)
    for shape, owner in _near(others, box):
        if owner is pad:
            continue
        sx1, sy1, sx2, sy2 = shape.bbox()
        if sx2 < box[0] or sx1 > box[2] or sy2 < box[1] or sy1 > box[3]:
            continue
        if not any(distance(shape, piece) == 0 for piece in copper):
            continue
        if _strictly_inside(shape, own):
            continue  # a hole within this hole takes no copper
        best = min(best, distance(shape, own))
    return best


def _strictly_inside(inner: Shape, outer: Shape) -> bool:
    """Whether `inner` is inside `outer` and touches its edge nowhere."""
    return all(depth(p, outer.core) + outer.radius > inner.radius + 1 for p in inner.core)


def _box(shapes: list[Shape]):
    boxes = [s.bbox() for s in shapes]
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def pads(run) -> None:
    board, settings = run.board, run.settings
    holes = _holes(board) if run.on("annular_width") else {}
    least_hole = settings.nm("min_through_hole_diameter")
    least_ring = settings.nm("min_via_annular_width")
    rules = settings.dru
    for fp in board.footprints:
        for pad in fp.pads:
            item = describe.pad(board, fp, pad)
            if pad.kind == "thru_hole" and pad.drill is not None and max(pad.drill) == 0:
                run.report("through_hole_pad_without_hole", "Through hole pad has no hole", [item])
                _padstack(run, pad, item)
                continue
            subject = None
            if rules is not None and rules.rules and pad.kind in ("thru_hole", "np_thru_hole"):
                from . import subjects  # noqa: PLC0415

                subject = subjects.of_pad(run, fp, pad)
            if pad.kind in ("thru_hole", "np_thru_hole"):
                size = min(pad.drill) if pad.drill is not None else 0
                broken = _out_of(size, limits(run, "hole_size", subject, None, least_hole))
                if broken is not None:
                    bound, value, who, severity = broken
                    run.report(
                        "drill_out_of_range",
                        f"Hole size out of range ({who} {bound} hole {mm(value)}; "
                        f"actual {mm(size)})",
                        [item],
                        severity=severity,
                    )
            if pad.kind == "thru_hole":
                _plated(run, fp, pad, item, holes, limits(run, "annular_width", subject, None,
                                                          least_ring))  # fmt: skip
            _padstack(run, pad, item)


def _plated(run, fp: Footprint, pad: Pad, item, holes, ring_limits) -> None:
    own = hole(pad)
    if own is None or (pad.drill is not None and min(pad.drill) <= NO_HOLE):
        run.report(
            "padstack_invalid",
            "Padstack is not valid (PTH pad hole size must be larger than 0.000004 mm)",
            [item],
        )
        return
    copper = pad_copper(pad)
    if all(covers(own, piece) for piece in copper):
        run.report("padstack", "Padstack is questionable (PTH pad hole leaves no copper)", [item])
    if not run.on("annular_width"):
        return
    annular = _annular(pad, own, copper, holes)
    if annular is None:
        return
    broken = _out_of(round(annular), ring_limits)
    if broken is not None and (broken[0] == "max" or annular < broken[1]):
        bound, value, who, severity = broken
        run.report(
            "annular_width",
            f"Annular width ({who} {bound} annular width {mm(value)}; actual {mm(round(annular))})",
            [item],
            severity=severity,
        )


def _padstack(run, pad: Pad, item) -> None:
    kind = pad.kind
    props = _properties(pad)
    problems = []
    if kind == "smd":
        front, back = _outer(pad)
        copper = [layer for layer in pad.layers if layer.endswith(".Cu")]
        if copper and not front and not back:
            problems.append("SMD pad has no outer layers")
        elif front and back:
            problems.append("SMD pad has copper on both sides of the board")
        elif front or back:
            side = "F" if front else "B"
            other = "B" if front else "F"
            for layer in ("mask", "paste"):
                own, far = f"{side}.{layer.title()}", f"{other}.{layer.title()}"
                if far in pad.layers and own not in pad.layers:
                    problems.append(
                        f"SMD pad has copper and {layer} layers on different sides of the board"
                    )
    if kind == "connect" and any(layer.endswith(".Paste") for layer in pad.layers):
        problems.append("connector pads normally have no solder paste; use a SMD pad instead")
    if "pad_prop_bga" in props and kind != "smd":
        problems.append("'BGA' property is for SMD pads")
    if "pad_prop_castellated" in props and kind != "thru_hole":
        problems.append("'castellated' pads are normally PTH")
    if kind == "np_thru_hole":
        for prop, name in (("pad_prop_heatsink", "heatsink"), ("pad_prop_testpoint", "testpoint"),
                           ("pad_prop_fiducial_glob", "fiducial"),
                           ("pad_prop_fiducial_loc", "fiducial")):  # fmt: skip
            if prop in props:
                problems.append(f"'{name}' pads are normally plated")
    if any(layer.endswith(".Paste") for layer in pad.layers) and _no_paste(pad):
        problems.append(
            "negative solder paste margin is larger than pad; "
            "no solder paste mask will be generated"
        )
    for problem in problems:
        run.report("padstack", f"Padstack is questionable ({problem})", [item])
    if pad.shape == "custom" and not _single(pad):
        run.report(
            "padstack_invalid",
            "Padstack is not valid (custom pad shape must resolve to a single polygon)",
            [item],
        )


def _no_paste(pad: Pad) -> bool:
    node = pad.node
    margin = node.find("solder_paste_margin") if node is not None else None
    ratio = node.find("solder_paste_margin_ratio") if node is not None else None
    m = nm(margin.atom(1)) if margin is not None else 0
    r = float(ratio.atom(1)) if ratio is not None else 0.0
    return any(size + m + r * size <= 0 for size in pad.size)


def _single(pad: Pad) -> bool:
    """Whether a custom pad's anchor and primitives all hang together."""
    pieces = pad_copper(pad)
    if len(pieces) <= 1:
        return True
    parent = list(range(len(pieces)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(pieces)):
        for j in range(i + 1, len(pieces)):
            if find(i) != find(j) and distance(pieces[i], pieces[j]) <= 1:
                parent[find(i)] = find(j)
    return len({find(i) for i in range(len(pieces))}) == 1


# -- footprints -----------------------------------------------------------------------------

NEITHER = {"pad_prop_heatsink", "pad_prop_castellated", "pad_prop_mechanical",
           "pad_prop_fiducial_glob", "pad_prop_fiducial_loc"}  # fmt: skip


def footprint_types(run) -> None:
    if not run.on("footprint_type_mismatch"):
        return
    for fp in run.board.footprints:
        given = (
            "Through hole" if "through_hole" in fp.attributes
            else "SMD" if "smd" in fp.attributes else None
        )  # fmt: skip
        if given is None:
            continue
        plated = surface = False
        for pad in fp.pads:
            if _properties(pad) & NEITHER:
                continue
            if pad.kind == "thru_hole":
                plated = True
            elif pad.kind == "smd" and any(layer.endswith(".Cu") for layer in pad.layers):
                surface = True
        likely = "Through hole" if plated else "SMD" if surface else None
        if likely is not None and likely != given:
            run.report(
                "footprint_type_mismatch",
                f"Footprint component type doesn't match footprint pads "
                f"(expected '{likely}'; actual '{given}')",
                [describe.footprint(fp)],
            )


# -- layers ---------------------------------------------------------------------------------


def layers(run) -> None:
    """Copper on a copper layer the board has not got."""
    if not run.on("item_on_disabled_layer"):
        return
    board = run.board
    enabled = {layer.name for layer in board.layers}
    message = "Item on a disabled copper layer (layer {})"
    for track in board.tracks:
        if track.layer.endswith(".Cu") and track.layer not in enabled:
            run.report("item_on_disabled_layer", message.format(track.layer),
                       [describe.track(board, track)])  # fmt: skip
    for zone in board.zones:
        missing = [la for la in zone.layers if la.endswith(".Cu") and la not in enabled]
        if missing and not zone.rule_area:
            run.report("item_on_disabled_layer", message.format(missing[0]),
                       [describe.zone(board, zone)])  # fmt: skip
    for node in board.root.lists():
        if node.head.startswith("gr_"):
            found = node.find("layer")
            layer = found.value(1) if found is not None else ""
            if layer.endswith(".Cu") and layer not in enabled:
                item = describe.Item("shape", describe.uuid_of(node), node.head, (0, 0))
                run.report("item_on_disabled_layer", message.format(layer), [item])


# -- text -----------------------------------------------------------------------------------

FRONT = {"F.Cu", "F.SilkS", "F.Fab", "F.Mask", "F.Paste", "F.Adhes", "F.CrtYd"}
BACK = {"B.Cu", "B.SilkS", "B.Fab", "B.Mask", "B.Paste", "B.Adhes", "B.CrtYd"}
SILK = {"F.SilkS", "B.SilkS"}


def _hidden(node: List) -> bool:
    for owner in (node, node.find("effects")):
        if owner is None:
            continue
        hide = owner.find("hide")
        if hide is not None and hide.value(1) != "no":
            return True
        if "hide" in [a for a in owner.items[1:] if isinstance(a, str)]:
            return True
    return False


def _font(node: List) -> tuple[int, int, int, bool, bool]:
    """Height, width, stroke as given, bold, mirrored."""
    effects = node.find("effects")
    font = effects.find("font") if effects is not None else None
    size = font.find("size") if font is not None else None
    height = nm(size.atom(1)) if size is not None else 1_000_000
    width = nm(size.atom(2)) if size is not None and size.atom(2) is not None else height
    thickness = font.find("thickness") if font is not None else None
    stroke = nm(thickness.atom(1)) if thickness is not None else 0
    bold_node = font.find("bold") if font is not None else None
    bold = (bold_node is not None and bold_node.value(1) != "no") or (
        font is not None and "bold" in [a for a in font.items[1:] if isinstance(a, str)]
    )
    justify = effects.find("justify") if effects is not None else None
    mirrored = justify is not None and "mirror" in justify.values()
    return height, width, stroke, bold, mirrored


def _pen(height: int, width: int, stroke: int, bold: bool) -> int:
    """The stroke a text is drawn with: as given, or KiCad's default for its
    size, and never more than a quarter of its smaller size."""
    if stroke <= 0:
        stroke = round(height / 5) if bold else round(height / 8)
    return min(stroke, round(min(height, width) * 0.25))


# -- text variables -------------------------------------------------------------------------

# What a board resolves without being told (`tests/fixtures/drc/drc3`): its
# title block, its files, the date, a text's own layer. A cross-reference,
# ${R1:VALUE}, is never reported, whether R1 or its field is there or not.
BOARD_VARIABLES = {
    "TITLE", "REVISION", "COMPANY", "ISSUE_DATE", "FILENAME", "FILEPATH", "PROJECTNAME",
    "CURRENT_DATE", "LAYER", "VCSHASH", "VCSSHORTHASH", "DRC_ERROR", "DRC_WARNING",
    *(f"COMMENT{n}" for n in range(1, 10)),
}  # fmt: skip
# What a footprint's text resolves besides: the footprint's own fields, by name.
FOOTPRINT_VARIABLES = {"REFERENCE", "VALUE", "FOOTPRINT_NAME", "FOOTPRINT_LIBRARY"}
_VARIABLE = re.compile(r"\$\{([^}]*)\}")
_MARKER = re.compile(r"\$\{DRC_(ERROR|WARNING)(?:\s([^}]*))?\}")


def _text_of(node) -> str:
    head = node.head
    if head in ("property", "fp_text"):
        return node.value(2) or ""
    return node.value(1) or ""


class Variables:
    def __init__(self, run) -> None:
        import json  # noqa: PLC0415

        project = run.settings.project
        names: set[str] = set()
        if project is not None and project.is_file():
            try:
                names = set(
                    json.loads(project.read_text(encoding="utf-8")).get("text_variables") or {}
                )
            except (OSError, ValueError):
                names = set()
        self.board = BOARD_VARIABLES | names

    def unresolved(self, node) -> bool:
        head = node.head
        text = _text_of(node)
        if head in ("gr_text", "gr_text_box"):
            known = self.board
        elif head in ("property", "fp_text", "fp_text_box"):
            fp = node.parent
            fields = {p.value(1) for p in fp.find_all("property")} if fp is not None else set()
            known = self.board | FOOTPRINT_VARIABLES | fields
        else:
            return False
        return any(
            ":" not in name and name.split(" ", 1)[0] not in known
            for name in _VARIABLE.findall(text)
        )


def _texts(board):
    """Every text on the board: (item, layer, node)."""
    from .. import geometry  # noqa: PLC0415

    for fp in board.footprints:
        for node in fp.node.lists():
            head = node.head
            if head not in ("property", "fp_text", "fp_text_box"):
                continue
            layer = node.find("layer")
            if layer is None:
                continue
            at = node.find("at") or node.find("start")
            if at is not None:
                rx, ry = geometry.rotate(nm(at.atom(1)), nm(at.atom(2)), fp.angle)
                position = (round(fp.position[0] + rx), round(fp.position[1] + ry))
            else:
                position = fp.position
            if head == "property":
                item = describe.field(fp, node.value(1) or "", node.value(2) or "", node, position)
            elif head == "fp_text":
                kind = node.atom(1)
                if kind == "reference":
                    item = describe.field(fp, "Reference", node.value(2) or "", node, position)
                elif kind == "value":
                    item = describe.field(fp, "Value", node.value(2) or "", node, position)
                else:
                    item = describe.Item(
                        "text", describe.uuid_of(node),
                        f"Footprint text of {describe.reference(fp)} ({node.value(2) or ''})",
                        position,
                    )  # fmt: skip
            else:
                item = describe.Item(
                    "text", describe.uuid_of(node),
                    f"Footprint text box of {describe.reference(fp)}", position,
                )  # fmt: skip
            yield item, layer.value(1), node
    for node in board.root.lists():
        if node.head not in ("gr_text", "gr_text_box"):
            continue
        layer = node.find("layer")
        if layer is None:
            continue
        at = node.find("at") or node.find("start")
        position = (nm(at.atom(1)), nm(at.atom(2))) if at is not None else (0, 0)
        value = node.value(1) or ""
        kind = "PCB text box" if node.head == "gr_text_box" else "PCB text"
        item = describe.Item(
            "text", describe.uuid_of(node),
            f"{kind} '{value}' on {board.layer_name(layer.value(1))}", position,
        )  # fmt: skip
        yield item, layer.value(1), node


def texts(run) -> None:
    settings = run.settings
    least_height = settings.nm("min_text_height")
    least_stroke = settings.nm("min_text_thickness")
    variables = Variables(run) if run.on("unresolved_variable") else None
    for item, layer, node in _texts(run.board):
        if variables is not None and variables.unresolved(node):
            run.report("unresolved_variable", "Unresolved text variable", [item])
        for kind, said in _MARKER.findall(_text_of(node)):
            # ${DRC_ERROR} and ${DRC_WARNING} are markers a person puts on
            # the board for DRC to raise, with what follows as the message.
            rule = f"generic_{kind.lower()}"
            run.report(rule, said.strip() or kind.title(), [item])
        if _hidden(node):
            continue
        height, width, stroke, bold, mirrored = _font(node)
        if layer == "Edge.Cuts":
            run.report("text_on_edge_cuts", "Text or graphic on Edge.Cuts layer", [item])
        if layer in SILK:
            if height < least_height:
                run.report(
                    "text_height",
                    f"Text height out of range (board setup constraints silk text height "
                    f"min height {mm(least_height)}; actual {mm(height)})",
                    [item],
                )
            pen = _pen(height, width, stroke, bold)
            if pen < least_stroke:
                run.report(
                    "text_thickness",
                    f"Text thickness out of range (board setup constraints silk text "
                    f"thickness min thickness {mm(least_stroke)}; actual {mm(pen)})",
                    [item],
                )
        if layer in FRONT and mirrored:
            run.report("mirrored_text_on_front_layer", "Mirrored text on front layer", [item])
        elif layer in BACK and not mirrored:
            run.report("nonmirrored_text_on_back_layer", "Non-Mirrored text on back layer", [item])
