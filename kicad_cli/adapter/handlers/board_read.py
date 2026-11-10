"""board create, board check, board export, board render, footprint list, footprint create, track list.

Reads parse the saved board. Checks and exports run KiCad's own program (``kicad_tools``)
on the saved file, so a board KiCad has open with unsaved changes is checked as saved.
Writes create new files only: ``board create`` and ``footprint create`` never overwrite one.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ..._driver.targets import path_of
from .. import base, kicad_tools, lib_register, pcb_read_build, pcb_read_drc, pcb_read_footprint_lib, pcb_read_netlist
from ..fileformat import geometry, lib_tables
from ..fileformat.board import Board, BoardError, shape_points
from ..fileformat.sexpr import SexprError

DEFAULT_LAYERS = "F.Cu,B.Cu,F.SilkS,B.SilkS,Edge.Cuts"
COURTYARD = {"F.CrtYd", "B.CrtYd"}
SHAPES = {"fp_line", "fp_arc", "fp_circle", "fp_rect", "fp_poly"}


def load_board(path: Path) -> Board:
    try:
        return Board.load(path)
    except (OSError, SexprError, BoardError, UnicodeDecodeError) as exc:
        raise base.invalid(f"{path.name} is not a KiCad board file: {str(exc)[:200]}") from None


def _uuid(node: Any) -> str:
    found = node.find("uuid") if node is not None else None
    return (found.value(1) or "") if found is not None else ""


def _choice(call: base.Call, key: str, allowed: tuple[str, ...], default: str | None = None) -> str:
    value = call.inputs.get(key, default)
    if value not in allowed:
        flag = "--" + key.replace("_", "-")
        raise base.usage(f"{call.command} needs {flag} to be one of {', '.join(allowed)}.")
    return value


# ---------------------------------------------------------------------------- reads

def courtyard_box(fp: Any) -> dict[str, float] | None:
    """The box around a footprint's courtyard drawings, on the board, in mm."""
    boxes = []
    for node in fp.node.lists() if fp.node is not None else []:
        if node.head not in SHAPES:
            continue
        layer = node.find("layer")
        if layer is None or layer.value(1) not in COURTYARD:
            continue
        local = shape_points(node)
        if local:
            boxes.append(geometry.bbox(geometry.place(local, *fp.position, fp.angle)))
    if not boxes:
        return None
    return {"min_x_mm": base.mm(min(b[0] for b in boxes)), "min_y_mm": base.mm(min(b[1] for b in boxes)),
            "max_x_mm": base.mm(max(b[2] for b in boxes)), "max_y_mm": base.mm(max(b[3] for b in boxes))}


def reference_text(fp: Any) -> dict[str, Any] | None:
    """Where a footprint's reference text sits on the board: its anchor, its angle and its layer."""
    node = next((p for p in fp.node.find_all("property") if p.value(1) == "Reference"), None) if fp.node is not None else None
    at = node.find("at") if node is not None else None
    if at is None:
        return None
    lx, ly = round(1e6 * float(at.atom(1))), round(1e6 * float(at.atom(2)))
    rx, ry = geometry.rotate(lx, ly, fp.angle)
    layer = node.find("layer")
    return {"x_mm": base.mm(round(fp.position[0] + rx)), "y_mm": base.mm(round(fp.position[1] + ry)),
            "rotation_deg": round(float(at.atom(3) or 0) % 360, 6), "layer": layer.value(1) if layer else None,
            "hidden": node.find("hide") is not None and node.find("hide").value(1) != "no"}


def pad_row(pad: Any) -> dict[str, Any]:
    """A pad on the board: its centre, its type and the box its copper takes, as placed."""
    x1, y1, x2, y2 = pad.bbox()
    return {"number": pad.number, "net": pad.net, "x_mm": base.mm(pad.position[0]), "y_mm": base.mm(pad.position[1]),
            "type": pad.kind, "width_mm": base.mm(x2 - x1), "height_mm": base.mm(y2 - y1)}


def footprint_row(fp: Any) -> dict[str, Any]:
    return {
        "id": fp.reference,
        "uuid": _uuid(fp.node),
        "value": fp.value,
        "lib_id": fp.library_id,
        "x_mm": base.mm(fp.position[0]),
        "y_mm": base.mm(fp.position[1]),
        "rotation_deg": round(fp.angle, 6),
        "side": "back" if fp.layer.startswith("B.") else "front",
        "locked": fp.locked,
        "courtyard": courtyard_box(fp),
        "pads": [pad_row(p) for p in fp.pads],
        "reference_text": reference_text(fp),
    }


class FootprintList(base.Handler):
    def read(self) -> Any:
        board = load_board(self.call.require_doc(".kicad_pcb"))
        query = (self.call.inputs.get("query") or "").casefold()
        side = self.call.inputs.get("side")
        if side is not None and side not in ("front", "back"):
            raise base.usage("footprint list needs --side to be front or back.")
        items = []
        for fp in board.footprints:
            row = footprint_row(fp)
            if query and query not in row["id"].casefold() and query not in row["value"].casefold():
                continue
            if side and row["side"] != side:
                continue
            items.append(row)
        return {"items": items}


class TrackList(base.Handler):
    def read(self) -> Any:
        board = load_board(self.call.require_doc(".kicad_pcb"))
        net = self.call.inputs.get("net")
        layer = self.call.inputs.get("layer")
        items: list[dict[str, Any]] = []
        for t in board.tracks:
            if (net is not None and t.net != net) or (layer is not None and t.layer != layer):
                continue
            items.append({"id": _uuid(t.node), "type": t.kind, "net": t.net, "layer": t.layer,
                          "width_mm": base.mm(t.width), "length_mm": round(t.length() / 1_000_000, 6),
                          "start_x_mm": base.mm(t.start[0]), "start_y_mm": base.mm(t.start[1]),
                          "end_x_mm": base.mm(t.end[0]), "end_y_mm": base.mm(t.end[1]), "locked": _locked(t.node)})
        for v in board.vias:
            if (net is not None and v.net != net) or (layer is not None and layer not in v.layers):
                continue
            items.append({"id": _uuid(v.node), "type": "via", "net": v.net, "layer": ",".join(v.layers),
                          "x_mm": base.mm(v.position[0]), "y_mm": base.mm(v.position[1]),
                          "diameter_mm": base.mm(v.diameter), "drill_mm": base.mm(v.drill), "via_type": v.kind,
                          "locked": _locked(v.node)})
        return {"items": items}


def _locked(node: Any) -> bool:
    marker = node.find("locked") if node is not None else None
    return (marker is not None and marker.value(1) != "no") or (node is not None and "locked" in node.values())


class BoardCheck(base.Handler):
    def read(self) -> Any:
        board = self.call.require_doc(".kicad_pcb")
        load_board(board)
        parity = board.with_suffix(".kicad_sch").is_file()
        severity = _choice(self.call, "severity", ("error", "warning", "all"), "all")
        report = kicad_tools.drc(base.kicad(), board, parity=parity)
        result = pcb_read_drc.shape(report, parity)
        if severity != "all":
            result["items"] = [i for i in result["items"] if i["severity"] == severity and not i["excluded"]]
        return result


# ---------------------------------------------------------------------------- board create

def _schematic(call: base.Call) -> Path:
    doc = call.require_doc(".kicad_sch", ".kicad_pro")
    schematic = doc if doc.suffix == ".kicad_sch" else doc.with_suffix(".kicad_sch")
    if not schematic.is_file():
        raise base.not_found(f"There is no schematic {schematic.name} beside {doc.name}.")
    return schematic


def _board_out(call: base.Call, schematic: Path) -> Path:
    out = Path(os.path.abspath(call.inputs["output"])) if call.inputs.get("output") else schematic.with_suffix(".kicad_pcb")
    if out.suffix != ".kicad_pcb":
        raise base.usage("board create needs --output to end in .kicad_pcb.")
    if out.exists():
        raise base.invalid(f"{out.name} already exists; board create never overwrites a board. "
                           "Choose another --output, or delete the board first.")
    if not out.parent.is_dir():
        raise base.not_found(f"The folder {out.parent} does not exist.")
    return out


def _size(call: base.Call) -> tuple[float, float] | None:
    w, h = call.inputs.get("width_mm"), call.inputs.get("height_mm")
    if (w is None) != (h is None):
        raise base.usage("board create needs both --width and --height, or neither.")
    if w is None:
        return None
    if w <= 0 or h <= 0:
        raise base.usage("--width and --height must be greater than zero.")
    return (w, h)


class BoardCreate(base.Handler):
    def sources(self) -> list[Path]:
        doc = self.call.doc
        return [doc if doc.suffix == ".kicad_sch" else doc.with_suffix(".kicad_sch")] if doc is not None else []

    def _plan(self) -> tuple[Path, Path, pcb_read_build.Plan, list[Path]]:
        schematic = _schematic(self.call)
        out = _board_out(self.call, schematic)
        pitch = self.call.inputs.get("pitch_mm", 10.0)
        if pitch <= 0:
            raise base.usage("--pitch must be greater than zero.")
        kicad = base.kicad()
        netlist = pcb_read_netlist.parse(kicad_tools.netlist(kicad, schematic))
        tables = lib_tables.Tables.of(schematic.parent, kicad.root)
        plan = pcb_read_build.plan(netlist, tables, pitch_mm=pitch)
        return schematic, out, plan

    def preview(self) -> dict[str, Any]:
        _size(self.call)
        schematic, out, plan = self._plan()
        changes = [{"target": base.key(out), "field": "exists", "before": False, "after": True}]
        action = (f"Create {out.name} from {schematic.name}: {len(plan.components)} footprints on a grid, "
                  f"{len(plan.nets)} nets, a rectangular outline.")
        if out.stem != schematic.stem or out.parent != schematic.parent:
            action += (f" KiCad pairs a board with the schematic of the same name, so {out.name} is not the board of "
                       f"the {schematic.stem} project: netlist import needs --schematic {schematic} for it, and "
                       "board check runs without schematic parity.")
        preview = {"action": action,
                   "targets": [{"key": base.key(out), "kind": "board", "label": out.name}],
                   "changes": changes, "can_apply": not plan.problems}
        if plan.problems:
            preview["blocked_by"] = plan.problems[:20]
        return preview

    def apply(self) -> dict[str, Any]:
        size = _size(self.call)
        schematic, out, plan = self._plan()
        if plan.problems:
            raise base.AdapterError("E_VALIDATION", "The board cannot be built: " + "; ".join(plan.problems[:5]),
                                    dispatch_state="not_started")
        text, summary = pcb_read_build.build(plan, size)
        try:
            base.write_text(out, text)
        except OSError as exc:
            raise base.AdapterError("E_NATIVE_FAILURE", f"Writing the board failed: {exc}.",
                                    dispatch_state="not_started") from None
        summary.update({"board": str(out), "schematic": str(schematic), "skipped": plan.skipped})
        return base.produced([out], summary)

    def read_back(self) -> dict[str, Any]:
        return {"observed": {t: {"exists": _exists(t)} for t in self.call.targets}}


def _exists(key: str) -> bool:
    path = path_of(key)
    return bool(path) and os.path.exists(path)


# ---------------------------------------------------------------------------- exports

def uses_aux_origin(board: Path) -> bool:
    """Whether the board's saved plot settings put coordinates on the drill/place origin."""
    plot = load_board(board).root.find("setup")
    plot = plot.find("pcbplotparams") if plot is not None else None
    flag = plot.find("useauxorigin") if plot is not None else None
    return flag is not None and flag.atom(1) in ("yes", "true")


def _staging(near: Path) -> Path:
    near.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=".kicad-cli-export-", dir=str(near)))


class BoardExport(base.Handler):
    def _inputs(self) -> tuple[Path, str, Path]:
        board = self.call.require_doc(".kicad_pcb")
        kind = _choice(self.call, "file_type", ("gerber", "drill"))
        if not self.call.inputs.get("output"):
            raise base.usage("board export needs --output: the folder for the files.")
        folder = Path(os.path.abspath(self.call.inputs["output"]))
        if folder.exists() and not folder.is_dir():
            raise base.usage(f"--output {folder} is a file; board export writes into a folder.")
        return board, kind, folder

    def preview(self) -> dict[str, Any]:
        board, kind, folder = self._inputs()
        load_board(board)
        base.kicad()
        what = "Gerber files, from the plot settings saved in the board, zones refilled first" if kind == "gerber" \
            else "Excellon drill files"
        return {"action": f"Export {what} of {board.name} into {folder}.",
                "targets": [{"key": base.key(folder), "kind": "folder", "label": folder.name}],
                "changes": [{"target": base.key(folder), "field": "export", "before": None, "after": kind}],
                "can_apply": True}

    def apply(self) -> dict[str, Any]:
        board, kind, folder = self._inputs()
        kicad = base.kicad()
        staging = _staging(folder)
        try:
            out = str(staging) + os.sep
            if kind == "gerber":
                args = ["pcb", "export", "gerbers", "--output", out, "--board-plot-params", "--check-zones"]
            else:
                # Holes on the same origin as the Gerber files, which follow the board's plot settings.
                origin = "plot" if uses_aux_origin(board) else "absolute"
                args = ["pcb", "export", "drill", "--output", out, "--drill-origin", origin]
            files = kicad_tools.export(kicad, args, board, staging)
            placed = kicad_tools.move_into(files, staging, folder)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return base.produced(placed, {"file_type": kind, "folder": str(folder), "files": [p.name for p in placed]})

    def read_back(self) -> dict[str, Any]:
        """The kind of files the folder now holds: the export's own kind when files of that kind
        are there, otherwise none."""
        _, kind, folder = self._inputs()
        found = folder.is_dir() and any(p.is_file() and is_export_file(p, kind) for p in folder.iterdir())
        return {"observed": {t: {"export": kind if found else None} for t in self.call.targets}}


GERBER_SUFFIXES = {".gbr", ".gbrjob", ".gtl", ".gbl", ".gto", ".gbo", ".gts", ".gbs", ".gtp", ".gbp", ".gko", ".gm1"}


def is_export_file(path: Path, kind: str) -> bool:
    """Whether a file is one of KiCad's Gerber files (also with Protel names, such as .gtl or .g2)
    or drill files."""
    suffix = path.suffix.lower()
    if kind == "drill":
        return suffix in (".drl", ".xln")
    return suffix in GERBER_SUFFIXES or (len(suffix) > 2 and suffix[:2] in (".g", ".gm") and suffix[2:].isdigit())


class BoardRender(base.Handler):
    def _inputs(self) -> tuple[Path, str, Path, str]:
        board = self.call.require_doc(".kicad_pcb")
        kind = _choice(self.call, "file_type", ("pdf", "svg"))
        if not self.call.inputs.get("output"):
            raise base.usage("board render needs --output: the file to write.")
        out = Path(os.path.abspath(self.call.inputs["output"]))
        if out.suffix.lower() != "." + kind:
            raise base.usage(f"board render needs --output to end in .{kind} for --file-type {kind}.")
        if out.is_dir():
            raise base.usage(f"--output {out} is a folder; board render writes one file.")
        layers = self.call.inputs.get("layers") or DEFAULT_LAYERS
        if not all(part.strip() for part in layers.split(",")):
            raise base.usage("--layers is a comma-separated list of layer names, such as F.Cu,Edge.Cuts.")
        return board, kind, out, ",".join(part.strip() for part in layers.split(","))

    def preview(self) -> dict[str, Any]:
        board, kind, out, layers = self._inputs()
        load_board(board)
        base.kicad()
        return {"action": f"Render the layers {layers} of {board.name} as {kind.upper()} to {out}, zones refilled first.",
                "targets": [{"key": base.key(out), "kind": "file", "label": out.name}],
                "changes": [{"target": base.key(out), "field": "exists", "before": out.exists(), "after": True}],
                "can_apply": True}

    def apply(self) -> dict[str, Any]:
        board, kind, out, layers = self._inputs()
        kicad = base.kicad()
        staging = _staging(out.parent)
        try:
            target = staging / out.name
            args = ["pcb", "export", kind, "--mode-single", "--layers", layers, "--check-zones", "--output", str(target)]
            if kind == "svg":
                args[4:4] = ["--page-size-mode", "2", "--exclude-drawing-sheet"]
            files = kicad_tools.export(kicad, args, board, staging)
            if target not in files:
                raise base.AdapterError("E_NATIVE_FAILURE", f"KiCad wrote no {out.name}.", dispatch_state="dispatched")
            placed = kicad_tools.move_into([target], staging, out.parent)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return base.produced(placed, {"file_type": kind, "file": str(placed[0]), "layers": layers.split(",")})

    def read_back(self) -> dict[str, Any]:
        return {"observed": {t: {"exists": _exists(t)} for t in self.call.targets}}


# ---------------------------------------------------------------------------- footprint create

class FootprintCreate(base.Handler):
    def _inputs(self) -> tuple[Path, Path, dict[str, Any], Path, str]:
        if self.call.doc is None:
            raise base.usage("footprint create needs --doc: the project footprint library (.pretty folder).")
        library = self.call.doc
        if library.suffix != ".pretty":
            raise base.usage("footprint create needs --doc to be a .pretty folder.")
        if library.exists() and not library.is_dir():
            raise base.usage(f"{library} is not a folder.")
        if not library.parent.is_dir():
            raise base.not_found(f"There is no folder {library.parent}.")
        projects = sorted(library.parent.glob("*.kicad_pro"))
        if len(projects) != 1:
            raise base.invalid("footprint create writes only project libraries: the .pretty folder must sit in a "
                               "folder with exactly one KiCad project file.")
        spec = self.call.inputs.get("definition")
        if spec is None:
            raise base.usage("footprint create needs --definition: the footprint as JSON, inline or in a file.")
        pcb_read_footprint_lib.validate(spec)
        mod = library / (spec["name"] + ".kicad_mod")
        if mod.exists():
            raise base.invalid(f"{mod.name} already exists in {library.name}; footprint create never overwrites one.")
        return library, projects[0].parent, spec, mod, library.stem

    def _table(self, project_dir: Path, library: Path, nickname: str) -> tuple[Path, str | None]:
        """The project table and its new text, or None when the library is already registered."""
        table = lib_register.table_path(project_dir, "footprint")
        raw = table.read_text(encoding="utf-8") if table.is_file() else None
        text, added = lib_register.with_library(raw, "footprint", nickname, lib_register.uri_for(project_dir, library))
        return table, text if added else None

    def preview(self) -> dict[str, Any]:
        library, project_dir, spec, mod, nickname = self._inputs()
        table, text = self._table(project_dir, library, nickname)
        changes = [{"target": base.key(mod), "field": "exists", "before": False, "after": True}]
        targets = [{"key": base.key(mod), "kind": "footprint", "label": f"{nickname}:{spec['name']}"}]
        if text is not None:
            entry = base.key(table, f"/libs/{nickname}")
            changes.append({"target": entry, "field": "registered", "before": False, "after": True})
        return {"action": f"Define the footprint {nickname}:{spec['name']} with {len(spec['pads'])} SMD pads"
                          + (f", and register {nickname} in the project's fp-lib-table." if text is not None else "."),
                "targets": targets, "changes": changes, "can_apply": True}

    def apply(self) -> dict[str, Any]:
        library, project_dir, spec, mod, nickname = self._inputs()
        table, text = self._table(project_dir, library, nickname)
        written = []
        try:
            base.write_text(mod, pcb_read_footprint_lib.footprint_text(spec))
            written.append(mod)
            if text is not None:
                base.write_text(table, text)
                written.append(table)
        except OSError as exc:
            raise base.AdapterError("E_NATIVE_FAILURE", f"Writing the footprint failed: {exc}.",
                                    dispatch_state="dispatched" if written else "not_started") from None
        stored = pcb_read_footprint_lib.pads_of(mod.read_text(encoding="utf-8"))
        facts = base.produced(written, {"id": f"{nickname}:{spec['name']}", "file": str(mod), "pads": stored,
                                        "registered": text is not None})
        if text is not None:
            facts["change"] = "applied"
        return facts

    def read_back(self) -> dict[str, Any]:
        observed: dict[str, dict[str, Any]] = {}
        for target in self.call.targets:
            if "#/libs/" in target:
                nickname = target.rsplit("/", 1)[1]
                folder = Path(path_of(target) or ".").parent
                observed[target] = {"registered": lib_register.registered(folder, "footprint", nickname) is not None}
            else:
                observed[target] = {"exists": _exists(target)}
        return {"observed": observed}


HANDLERS: dict[str, type[base.Handler]] = {
    "board create": BoardCreate,
    "board check": BoardCheck,
    "board export": BoardExport,
    "board render": BoardRender,
    "footprint list": FootprintList,
    "footprint create": FootprintCreate,
    "track list": TrackList,
}
