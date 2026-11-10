"""board get, board update, footprint update: the board's outline and the footprints' texts.

``board get`` reads what an Agent needs to size and place a board: the outline, the copper
layers and the box around the footprints' courtyards. ``board update`` sets the outline to a
rectangle; it is checked first with KiCad's own DRC on staged copies, as a footprint move is.
``footprint update`` moves a footprint's reference text; it changes no copper, so KiCad's DRC
is not run for it.
"""
from __future__ import annotations

from typing import Any

from .. import base, pcb_edit_drc
from ..fileformat import geometry
from ..fileformat.board import Board, shape_points, stroke_width
from ..fileformat.sexpr import Document, List, copy, number, quote
from ..pcb_edit_board import NM, derived_uuid, footprint, fp_key, insert_before_tail, load, load_for_write, to_nm
from .board_edit import _fragment, _locked, angle_text, eligible, items_of
from .board_read import courtyard_box, reference_text

DEFAULT_EDGE_WIDTH = 50_000  # nm: KiCad's default line width on Edge.Cuts

Box = tuple[int, int, int, int]


# ------------------------------------------------------------------------------- the outline


def outline(board: Board) -> tuple[str, Box | None, list[List]]:
    """The board's own outline: ``none``, ``rectangle`` with its corners as drawn (x0, y0, x1, y1),
    or ``other``. Footprints' drawings on Edge.Cuts are not part of it here."""
    shapes = board.edge_shapes()
    if not shapes:
        return "none", None, shapes
    edges: list[tuple[tuple[int, int], ...]] = []
    if len(shapes) == 1 and shapes[0].head in ("gr_rect", "gr_poly"):
        pts = shapes[0].find("pts")
        corners = shape_points(shapes[0]) if pts is None or pts.find("arc") is None else []
        if len(corners) == 4:
            edges = list(zip(corners, corners[1:] + corners[:1]))
    elif len(shapes) == 4 and all(s.head == "gr_line" for s in shapes):
        edges = [tuple(shape_points(s)) for s in shapes]
    ends = {p for edge in edges for p in edge}
    xs, ys = sorted({p[0] for p in ends}), sorted({p[1] for p in ends})
    closed = len(ends) == 4 and len({tuple(sorted(e)) for e in edges}) == 4
    if edges and closed and len(xs) == 2 and len(ys) == 2 and all(a[0] == b[0] or a[1] == b[1] for a, b in edges):
        return "rectangle", (xs[0], ys[0], xs[-1], ys[-1]), shapes
    return "other", None, shapes


def _box(x0: int, y0: int, x1: int, y1: int) -> dict[str, float]:
    return {"x_mm": base.mm(x0), "y_mm": base.mm(y0), "width_mm": base.mm(x1 - x0), "height_mm": base.mm(y1 - y0)}


def footprint_box(fp: Any) -> Box | None:
    """The box around a footprint's courtyard, or around its pads and drawings where it has none."""
    court = courtyard_box(fp)
    if court is None:
        return fp.bbox()
    return tuple(to_nm(court[k]) for k in ("min_x_mm", "min_y_mm", "max_x_mm", "max_y_mm"))


def placement(board: Board, box: Box | None) -> dict[str, Any]:
    """Where the footprints sit against an outline box: their extent, the margin from it to each
    side of the outline, and the footprints whose box is not inside."""
    boxes = [(fp.reference, b) for fp in board.footprints if (b := footprint_box(fp)) is not None]
    extent = (min(b[0] for _, b in boxes), min(b[1] for _, b in boxes),
              max(b[2] for _, b in boxes), max(b[3] for _, b in boxes)) if boxes else None
    out: dict[str, Any] = {"parts_extent": _box(*extent) if extent else None}
    if box is None or extent is None:
        return out
    x0, y0, x1, y1 = box
    out["margins_mm"] = {"left": base.mm(extent[0] - x0), "top": base.mm(extent[1] - y0),
                         "right": base.mm(x1 - extent[2]), "bottom": base.mm(y1 - extent[3])}
    out["footprints_outside"] = sorted(ref for ref, b in boxes if b[0] < x0 or b[1] < y0 or b[2] > x1 or b[3] > y1)
    return out


class BoardGet(base.Handler):
    def read(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        board = load(doc)
        kind, box, _ = outline(board)
        edge = board.edge_bbox()
        general = board.root.find("general")
        thickness = general.find("thickness") if general is not None else None
        out: dict[str, Any] = {
            "outline": {"shape": kind, **(_box(*box) if box else {}),
                        **({"extent": _box(*edge)} if edge and kind != "rectangle" else {})},
            "copper_layers": board.copper_layers,
            "thickness_mm": float(thickness.atom(1)) if thickness is not None else None,
        }
        out.update(placement(board, box or edge))
        return out


class BoardUpdate(base.Handler):
    """Set the board's outline to a rectangle. Only an outline that is one rectangle, or none,
    is replaced, so the change can always be made back."""

    FIELDS = ("x_mm", "y_mm", "width_mm", "height_mm")

    def _plan(self, board: Board) -> dict[str, Any]:
        inputs = self.call.inputs
        kind, box, shapes = outline(board)
        if kind == "other":
            raise base.unsupported("The board's outline is not one rectangle; kicad-cli sets only rectangular "
                                   "outlines, so change this one in KiCad.")
        if any(_locked(s) for s in shapes):
            raise base.invalid("The board outline is locked; unlock it in KiCad first.")
        if box is None and any(inputs.get(f) is None for f in self.FIELDS):
            raise base.usage("The board has no outline yet; give --x, --y, --width and --height.")
        x0 = to_nm(inputs["x_mm"]) if inputs.get("x_mm") is not None else box[0]
        y0 = to_nm(inputs["y_mm"]) if inputs.get("y_mm") is not None else box[1]
        width = to_nm(inputs["width_mm"]) if inputs.get("width_mm") is not None else box[2] - box[0]
        height = to_nm(inputs["height_mm"]) if inputs.get("height_mm") is not None else box[3] - box[1]
        if width <= 0 or height <= 0:
            raise base.invalid("--width and --height must be more than 0.")
        new = (x0, y0, x0 + width, y0 + height)
        if box == new:
            raise base.invalid("The outline is already this rectangle.")
        return {"before": box, "after": new, "shapes": shapes}

    @staticmethod
    def _write(board: Board, plan: dict[str, Any]) -> None:
        x0, y0, x1, y1 = plan["after"]
        shapes = plan["shapes"]
        if len(shapes) == 1 and shapes[0].head == "gr_rect":
            node = shapes[0]
            node.find("start").set(1, number(x0 / NM))
            node.find("start").set(2, number(y0 / NM))
            node.find("end").set(1, number(x1 / NM))
            node.find("end").set(2, number(y1 / NM))
            return
        width = max((stroke_width(s) for s in shapes), default=0) or DEFAULT_EDGE_WIDTH
        for shape in shapes:
            board.root.remove(shape)
        uid = derived_uuid("outline", x0, y0, x1, y1)
        insert_before_tail(board.root, copy(Document.parse(
            f"(gr_rect (start {number(x0 / NM)} {number(y0 / NM)}) (end {number(x1 / NM)} {number(y1 / NM)})"
            f" (stroke (width {number(width / NM)}) (type default)) (fill no) (layer \"Edge.Cuts\")"
            f" (uuid {quote(uid)}))").root))

    def preview(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        board = load_for_write(doc)
        before_text = board.document.dumps()
        plan = self._plan(board)
        self._write(board, plan)
        check = pcb_edit_drc.precheck(doc, before_text, board.document.dumps())
        key = base.key(doc, "/outline")
        old = _box(*plan["before"]) if plan["before"] else {f: None for f in self.FIELDS}
        changes = [{"target": key, "field": f, "before": old[f], "after": v} for f, v in _box(*plan["after"]).items()]
        out = {"action": f"Set the outline of {doc.name} to a {changes[2]['after']:g} x {changes[3]['after']:g} mm "
                         f"rectangle. {check.summary()}.",
               "targets": [{"key": key, "kind": "board", "label": "outline"}], "changes": changes,
               "can_apply": not check.blocked}
        if check.blocked:
            out["blocked_by"] = check.blocked_by()
        return out

    def apply(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        base.refuse_if_open(doc)
        board = load_for_write(doc)
        plan = self._plan(board)
        self._write(board, plan)
        base.write_text(doc, board.document.dumps())
        stored = load(doc)
        _, box, _ = outline(stored)
        return base.saved({"outline": _box(*box) if box else None, **placement(stored, box)})

    def read_back(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        kind, box, _ = outline(load(doc))
        observed = {key: (_box(*box) if box else {f: None for f in self.FIELDS}) for key in self.call.targets}
        return {"observed": observed}


# ------------------------------------------------------------------------------- texts


def courtyard_distance(court: dict[str, float], point: tuple[float, float]) -> float:
    """How far a point is from a courtyard box, in mm; 0 inside it."""
    dx = max(court["min_x_mm"] - point[0], 0, point[0] - court["max_x_mm"])
    dy = max(court["min_y_mm"] - point[1], 0, point[1] - court["max_y_mm"])
    return round((dx * dx + dy * dy) ** 0.5, 6)


def nearest_courtyard(board: Board, point: tuple[float, float]) -> str | None:
    """The footprint whose courtyard box is nearest to a point."""
    found = [(courtyard_distance(court, point), fp.reference) for fp in board.footprints
             if (court := courtyard_box(fp)) is not None]
    return min(found)[1] if found else None


class FootprintUpdate(base.Handler):
    """Move and turn footprints' reference texts, given where they go on the board."""

    FIELDS = ("reference_x_mm", "reference_y_mm", "reference_rotation_deg")

    def _items(self) -> list[dict[str, Any]]:
        items = items_of(self.call.inputs)
        for item in items:
            if all(item.get(f) is None for f in self.FIELDS):
                raise base.usage(f"footprint update needs --reference-x, --reference-y or --reference-rotation "
                                 f"for {item['id']}.")
        return items

    def _change(self, board: Board, item: dict[str, Any]) -> dict[str, Any]:
        fp = footprint(board, str(item["id"]))
        eligible(fp)
        before = reference_text(fp)
        if before is None:
            raise base.unsupported(f"{fp.reference} has no reference text to move.")
        node = next(p for p in fp.node.find_all("property") if p.value(1) == "Reference")
        at = node.find("at")
        if item.get("reference_x_mm") is not None or item.get("reference_y_mm") is not None:
            # The file holds the text's place in the footprint's frame; an unmoved text keeps it as it is.
            x = to_nm(item["reference_x_mm"]) if item.get("reference_x_mm") is not None else to_nm(before["x_mm"])
            y = to_nm(item["reference_y_mm"]) if item.get("reference_y_mm") is not None else to_nm(before["y_mm"])
            lx, ly = geometry.rotate(x - fp.position[0], y - fp.position[1], -fp.angle)
            at.set(1, number(round(lx) / NM))
            at.set(2, number(round(ly) / NM))
        angle = item["reference_rotation_deg"] % 360 if item.get("reference_rotation_deg") is not None \
            else before["rotation_deg"]
        while len(at.items) > 3:
            at.pop(3)
        at.append(angle_text(angle))
        return {"id": fp.reference, "before": before, "after": reference_text(fp)}

    @staticmethod
    def _values(text: dict[str, Any]) -> dict[str, Any]:
        return {"reference_x_mm": text["x_mm"], "reference_y_mm": text["y_mm"],
                "reference_rotation_deg": text["rotation_deg"]}

    def preview(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        board = load_for_write(doc)
        done = [self._change(board, item) for item in self._items()]
        targets, changes = [], []
        for d in done:
            key = fp_key(doc, d["id"])
            targets.append({"key": key, "kind": "footprint", "label": d["id"]})
            old, new = self._values(d["before"]), self._values(d["after"])
            changes += [{"target": key, "field": f, "before": old[f], "after": new[f]} for f in self.FIELDS]
        return {"action": f"Move the reference text of {len(done)} footprint(s) on {doc.name}.", "targets": targets,
                "changes": changes, "can_apply": True}

    def apply(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        base.refuse_if_open(doc)
        board = load_for_write(doc)
        done = [self._change(board, item) for item in self._items()]
        base.write_text(doc, board.document.dumps())
        # The runtime applies a batch one item at a time (DCLI-064), so this is one footprint.
        stored = load(doc)
        fp = footprint(stored, done[0]["id"])
        text = reference_text(fp)
        point = (text["x_mm"], text["y_mm"])
        own = courtyard_box(fp)
        return base.saved({"id": fp.reference, "reference_text": text, "nearest_courtyard": nearest_courtyard(stored, point),
                           "own_courtyard_distance_mm": courtyard_distance(own, point) if own is not None else None})

    def read_back(self) -> dict[str, Any]:
        doc = self.call.require_doc(".kicad_pcb")
        board = load(doc)
        observed: dict[str, dict[str, Any]] = {}
        for key in self.call.targets:
            ref = _fragment(key)[len("/footprints/"):]
            found = [fp for fp in board.footprints if fp.reference == ref]
            text = reference_text(found[0]) if len(found) == 1 else None
            observed[key] = self._values(text) if text else {}
        return {"observed": observed}


HANDLERS = {
    "board get": BoardGet,
    "board update": BoardUpdate,
    "footprint update": FootprintUpdate,
}
