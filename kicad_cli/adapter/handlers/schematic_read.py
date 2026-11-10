"""schematic create, check, export and render; symbol list and get; net list.

Reads come from KiCad's files through ``fileformat``. The check and the exports run KiCad's
own command-line program (``kicad_tools``): ERC, the KiCad netlist, PDF and SVG plots.
"""
from __future__ import annotations

import os

import shutil
import tempfile
import uuid as uuidlib
from collections import Counter
from pathlib import Path
from typing import Any

from ..._driver.targets import path_of
from .. import base, kicad_tools
from ..fileformat import circuit
from ..fileformat import new_project
from ..fileformat.board import Board, BoardError
from ..fileformat.lib_tables import _SymbolLibrary
from ..fileformat.schematic import LibSymbol, SchematicError, Symbol, _lib_symbol
from ..fileformat.sexpr import List, SexprError, quote, render, symbol as atom
from ..fileformat.symbols import _flatten

SCH = (".kicad_sch",)
PAPERS = ("A5", "A4", "A3", "A2", "A1", "A0", "A", "B", "C", "D", "E", "USLetter", "USLegal", "USLedger")
SEVERITIES = ("error", "warning", "all")
NET_PIN_CAP = 100  # pins listed per net in net list; pin_count always has them all


# ---------------------------------------------------------------------------- loading

def load_design(path: Path) -> circuit.Design:
    try:
        return circuit.Design(path)
    except FileNotFoundError as exc:
        raise base.not_found(f"A sheet of the schematic is missing: {exc}.") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise base.invalid(f"{path.name} cannot be read: {exc}.") from None
    except (SexprError, SchematicError, ValueError) as exc:
        raise base.invalid(f"{path.name} cannot be read as a KiCad schematic: {exc}.") from None


def load_board(path: Path) -> Board:
    try:
        return Board.load(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise base.invalid(f"{path.name} cannot be read: {exc}.") from None
    except (SexprError, BoardError, ValueError) as exc:
        raise base.invalid(f"{path.name} cannot be read as a KiCad board: {exc}.") from None


def placed(design: circuit.Design) -> list[tuple[circuit.SheetInstance, Symbol, str, int]]:
    """Every placed symbol unit in every sheet instance: (instance, symbol, reference, unit)."""
    out = []
    for instance in design.instances:
        for sym in instance.schematic.symbols:
            inst = sym.instance(instance.path)
            reference = inst.reference if inst else sym.properties.get("Reference", "")
            unit = inst.unit if inst else sym.unit
            out.append((instance, sym, reference, unit))
    return out


def symbol_row(instance: circuit.SheetInstance, sym: Symbol, reference: str, unit: int) -> dict[str, Any]:
    return {
        "reference": reference,
        "value": sym.properties.get("Value", ""),
        "footprint": sym.properties.get("Footprint", ""),
        "lib_id": sym.lib_id,
        "unit": unit,
        "sheet": instance.name,
        "x_mm": base.mm(sym.position[0]),
        "y_mm": base.mm(sym.position[1]),
        "rotation_deg": sym.angle,
        "mirror": sym.mirror,
        "uuid": sym.uuid,
        "dnp": sym.dnp,
        "in_bom": sym.in_bom,
        "on_board": sym.on_board,
    }


def pin_id(reference: str, number: str) -> str:
    return f"{reference}.{number}"


# ---------------------------------------------------------------------------- schematic create

def blank_schematic(title: str, paper: str) -> str:
    """An empty root sheet, as KiCad 10 reads it."""
    sch = List.new(
        "kicad_sch",
        List.new("version", "20260101"),
        List.new("generator", quote("kicad-cli")),
        List.new("generator_version", quote("1.0")),
        List.new("uuid", quote(str(uuidlib.uuid4()))),
        List.new("paper", quote(paper)),
        List.new("title_block", List.new("title", quote(title))),
        List.new("lib_symbols"),
        List.new("sheet_instances", List.new("path", quote("/"), List.new("page", quote("1")))),
        List.new("embedded_fonts", atom("no")),
    )
    return render(sch) + "\n"


class SchematicCreate(base.Handler):
    """--doc is the project file (made when missing); --output the new sheet, default <project>.kicad_sch."""

    def _plan(self) -> tuple[Path, Path, bool]:
        project = self.call.doc
        if project is None or project.suffix.lower() != ".kicad_pro":
            raise base.usage("schematic create needs --doc to be the project file (.kicad_pro); it is made when missing.")
        if not project.parent.is_dir():
            raise base.not_found(f"The folder {project.parent} does not exist.")
        output = self.call.inputs.get("output")
        doc = Path(os.path.abspath(output)) if output else project.with_suffix(".kicad_sch")
        if doc.suffix.lower() != ".kicad_sch":
            raise base.usage(f"schematic create needs --output to be a .kicad_sch file, not {doc.name}.")
        if doc.exists():
            raise base.invalid(f"{doc} already exists; schematic create never overwrites a schematic.")
        if not doc.parent.is_dir():
            raise base.not_found(f"The folder {doc.parent} does not exist.")
        paper = self.call.inputs.get("paper") or "A4"
        if paper not in PAPERS:
            raise base.usage(f"--paper must be one of {', '.join(PAPERS)}.")
        return doc, project, not project.exists()

    def preview(self) -> dict[str, Any]:
        doc, project, new_project_file = self._plan()
        files = [doc] + ([project] if new_project_file else [])
        return {
            "action": f"Create the empty schematic {doc.name}" + (f" and the project {project.name}" if new_project_file else ""),
            "targets": [{"key": base.key(doc), "kind": "schematic", "label": doc.name}],
            "changes": [{"target": base.key(f), "field": "exists", "before": False, "after": True} for f in files],
            "can_apply": True,
        }

    def apply(self) -> dict[str, Any]:
        doc, project, new_project_file = self._plan()
        title = self.call.inputs.get("title") or doc.stem
        base.write_text(doc, blank_schematic(title, self.call.inputs.get("paper") or "A4"))
        files = [doc]
        if new_project_file:
            base.write_text(project, new_project.project(project.stem))
            files.append(project)
        return base.produced(files, {"schematic": str(doc), "project": str(project),
                                     "created": [str(f) for f in files]})

    def read_back(self) -> dict[str, Any]:
        return {"observed": {k: {"exists": Path(path_of(k) or "").is_file()} for k in self.call.targets}}


# ---------------------------------------------------------------------------- schematic check

def erc_items(report: dict[str, Any]) -> list[dict[str, Any]]:
    """KiCad's ERC report as a flat list of findings, sheet by sheet."""
    groups = [(s.get("path", ""), s.get("violations") or []) for s in report.get("sheets") or []]
    if not groups:
        groups = [("", report.get("violations") or [])]
    out = []
    for sheet, violations in groups:
        for v in violations:
            objects = []
            for item in v.get("items") or []:
                if isinstance(item, dict):
                    pos = item.get("pos") or {}
                    obj: dict[str, Any] = {"description": item.get("description", ""), "uuid": item.get("uuid", "")}
                    if isinstance(pos.get("x"), (int, float)) and isinstance(pos.get("y"), (int, float)):
                        obj["x_mm"], obj["y_mm"] = pos["x"], pos["y"]
                    objects.append(obj)
                else:
                    objects.append({"uuid": str(item)})
            out.append({
                "type": v.get("type", ""),
                "severity": "exclusion" if v.get("excluded") else v.get("severity", ""),
                "description": v.get("description", ""),
                "sheet": sheet,
                "objects": objects,
            })
    return out


class SchematicCheck(base.Handler):
    def read(self) -> Any:
        doc = self.call.require_doc(*SCH)
        severity = self.call.inputs.get("severity") or "all"
        if severity not in SEVERITIES:
            raise base.usage(f"--severity must be one of {', '.join(SEVERITIES)}.")
        report = kicad_tools.erc(base.kicad(), doc)
        items = erc_items(report)
        counts = Counter(i["severity"] for i in items)
        shown = [i for i in items if severity == "all" or i["severity"] == severity]
        return {
            "kicad_version": report.get("kicad_version", ""),
            "error_count": counts.get("error", 0),
            "warning_count": counts.get("warning", 0),
            "exclusion_count": counts.get("exclusion", 0),
            "items": shown,
        }


# ---------------------------------------------------------------------------- export and render

class _Artifact(base.Handler):
    """An export of the schematic by KiCad's program into --output."""

    kinds: dict[str, str] = {}  # file type -> "file" or "folder"

    def _plan(self) -> tuple[Path, str, Path]:
        doc = self.call.require_doc(*SCH)
        file_type = self.call.inputs.get("file_type") or ""
        if file_type not in self.kinds:
            raise base.usage(f"--file-type must be one of {', '.join(self.kinds)}.")
        raw = self.call.inputs.get("output")
        if not raw:
            raise base.usage(f"{self.call.command} needs --output.")
        output = Path(os.path.abspath(raw))
        if self.kinds[file_type] == "file":
            if output.is_dir():
                raise base.usage(f"--output for {file_type} is a file, and {output} is a folder.")
            wanted = "." + ("net" if file_type == "netlist" else file_type)
            if output.suffix.lower() != wanted:
                raise base.usage(f"--output for {file_type} must end in {wanted}.")
        elif output.suffix.lower() == ".svg" or output.is_file():
            raise base.usage("--output for svg is a folder: KiCad writes one SVG file per sheet.")
        return doc, file_type, output

    def preview(self) -> dict[str, Any]:
        doc, file_type, output = self._plan()
        base.kicad()
        if self.kinds[file_type] == "file":
            change = {"target": base.key(output), "field": "exists", "before": output.is_file(), "after": True}
        else:
            # KiCad writes one SVG file per sheet; the read-back sees the kind of files the folder holds.
            change = {"target": base.key(output), "field": "export", "before": None, "after": file_type}
        return {"action": f"Write the {file_type} of {doc.name} to {output}",
                "targets": [{"key": base.key(output), "kind": file_type, "label": output.name}],
                "changes": [change], "can_apply": True}

    def args(self, file_type: str, staged: Path) -> list[str]:
        raise NotImplementedError

    def apply(self) -> dict[str, Any]:
        doc, file_type, output = self._plan()
        kicad = base.kicad()
        staging = Path(tempfile.mkdtemp(prefix="kicad-cli-export-"))
        try:
            if self.kinds[file_type] == "file":
                staged_out = staging / output.name
                files = kicad_tools.export(kicad, self.args(file_type, staged_out), doc, staging)
                target = output.parent
            else:
                files = kicad_tools.export(kicad, self.args(file_type, staging), doc, staging)
                target = output
            for f in files:
                dest = target / f.relative_to(staging)
                if dest.is_file():
                    dest.unlink()
            placed_files = kicad_tools.move_into(files, staging, target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return base.produced(placed_files, {"file_type": file_type, "output": str(output),
                                            "files": [str(f) for f in placed_files]})

    def read_back(self) -> dict[str, Any]:
        """A file: whether it exists. A folder: the kind of files it holds, the export's own kind
        when such files are there, otherwise none."""
        _, file_type, output = self._plan()
        if self.kinds[file_type] == "file":
            return {"observed": {k: {"exists": Path(path_of(k) or "").exists()} for k in self.call.targets}}
        found = output.is_dir() and any(p.is_file() and p.suffix.lower() == f".{file_type}" for p in output.iterdir())
        return {"observed": {k: {"export": file_type if found else None} for k in self.call.targets}}


class SchematicExport(_Artifact):
    kinds = {"netlist": "file"}

    def args(self, file_type: str, staged: Path) -> list[str]:
        return ["sch", "export", "netlist", "--format", "kicadsexpr", "--output", str(staged)]


class SchematicRender(_Artifact):
    kinds = {"pdf": "file", "svg": "folder"}

    def args(self, file_type: str, staged: Path) -> list[str]:
        return ["sch", "export", file_type, "--output", str(staged)]


# ---------------------------------------------------------------------------- symbol list and get

class SymbolList(base.Handler):
    def read(self) -> Any:
        doc = self.call.require_doc(*SCH)
        design = load_design(doc)
        include_power = bool(self.call.inputs.get("include_power"))
        query = (self.call.inputs.get("query") or "").casefold()
        rows = []
        for instance, sym, reference, unit in placed(design):
            if not include_power and reference.startswith("#"):
                continue
            row = symbol_row(instance, sym, reference, unit)
            if query and not any(query in str(row[k]).casefold() for k in ("reference", "value", "lib_id", "footprint")):
                continue
            rows.append(row)
        rows.sort(key=lambda r: (circuit.natural_key(r["reference"]), r["unit"], r["sheet"]))
        return {"items": rows}


def net_map(design: circuit.Design) -> tuple[dict[tuple[str, str, str], str], dict[tuple[str, str], str]]:
    """Each placed pin's net: by (sheet path, symbol uuid, pin number), and by (reference, pin)."""
    exact: dict[tuple[str, str, str], str] = {}
    loose: dict[tuple[str, str], str] = {}
    for net in circuit.connect(design):
        for pp in net.pins:
            exact[(pp.instance.path, pp.symbol.uuid, pp.pin.number)] = net.name
            loose.setdefault((pp.reference, pp.pin.number), net.name)
    return exact, loose


class SymbolGet(base.Handler):
    def read(self) -> Any:
        doc = self.call.require_doc(".kicad_sch", ".kicad_sym")
        wanted = str(self.call.inputs.get("id") or "")
        if not wanted:
            raise base.usage("symbol get needs --id.")
        if doc.suffix.lower() == ".kicad_sym":
            return library_symbol(doc, wanted)
        design = load_design(doc)
        rows = placed(design)
        hits = [r for r in rows if r[2] == wanted] or [r for r in rows if r[1].uuid == wanted]
        if not hits:
            raise base.not_found(f"The schematic has no symbol {wanted}.")
        units = Counter(unit for _, _, _, unit in hits)
        references = {ref for _, _, ref, _ in hits}
        if len(references) > 1 or any(n > 1 for n in units.values()):
            candidates = [{"id": sym.uuid, "reference": ref, "unit": unit, "sheet": inst.name,
                           "x_mm": base.mm(sym.position[0]), "y_mm": base.mm(sym.position[1])}
                          for inst, sym, ref, unit in hits]
            raise base.ambiguous(f"{wanted} names {len(hits)} placed symbols; give a UUID as --id.", candidates)
        exact, loose = net_map(design)
        first_inst, first, reference, _ = hits[0]
        lib = first_inst.schematic.library.get(first.library_name)
        out_units = []
        for inst, sym, ref, unit in sorted(hits, key=lambda h: h[3]):
            pins = []
            unit_lib = inst.schematic.library.get(sym.library_name)
            for pin in unit_lib.pins_of(unit, sym.body_style) if unit_lib else []:
                net = exact.get((inst.path, sym.uuid, pin.number)) or loose.get((ref, pin.number))
                chosen = sym.alternates.get(pin.number)
                name, kind = (chosen, pin.alternates[chosen][0]) if chosen and chosen in pin.alternates \
                    else (pin.name, pin.electrical)
                x, y = sym.transform(pin.position)
                pins.append({"id": pin_id(ref, pin.number), "number": pin.number, "name": name, "type": kind,
                             "net": net, "x_mm": base.mm(x), "y_mm": base.mm(y), "hidden": pin.hidden})
            pins.sort(key=lambda p: circuit.natural_key(p["number"]))
            out_units.append({"unit": unit, "uuid": sym.uuid, "sheet": inst.name, "x_mm": base.mm(sym.position[0]),
                              "y_mm": base.mm(sym.position[1]), "rotation_deg": sym.angle, "mirror": sym.mirror,
                              "pins": pins})
        return {
            "reference": reference,
            "lib_id": first.lib_id,
            "value": first.properties.get("Value", ""),
            "footprint": first.properties.get("Footprint", ""),
            "fields": dict(first.properties),
            "unit_count": lib.unit_count if lib else 1,
            "dnp": first.dnp,
            "in_bom": first.in_bom,
            "on_board": first.on_board,
            "units": out_units,
        }


def library_symbol(path: Path, name: str) -> dict[str, Any]:
    """A symbol's definition in a .kicad_sym library, a derived symbol written out whole.

    Pin positions are the connection points relative to the symbol origin, y down as on a sheet
    (the file stores them y up)."""
    try:
        library = _SymbolLibrary.load(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise base.invalid(f"{path.name} cannot be read: {exc}.") from None
    except (SexprError, ValueError) as exc:
        raise base.invalid(f"{path.name} cannot be read as a KiCad symbol library: {exc}.") from None
    raw = library.symbols.get(name)
    if raw is None:
        raise base.not_found(f"The library {path.name} has no symbol {name}.")
    node = _flatten(library.document, raw, name, name, library.find)
    lib: LibSymbol = _lib_symbol(name, node, {name: node})
    pins = sorted(({"number": p.number, "name": p.name, "type": p.electrical, "unit": p.unit,
                    "x_mm": base.mm(p.position[0]), "y_mm": base.mm(-p.position[1]), "angle_deg": p.angle,
                    "length_mm": base.mm(p.length), "hidden": p.hidden} for p in lib.pins),
                  key=lambda p: (p["unit"], circuit.natural_key(p["number"])))
    extends = raw.find("extends")
    return {
        "name": name,
        "library": str(path),
        "extends": extends.value(1) if extends is not None else None,
        "fields": dict(lib.properties),
        "unit_count": lib.unit_count,
        "power": lib.power,
        "pins": pins,
    }


# ---------------------------------------------------------------------------- net list

class NetList(base.Handler):
    def read(self) -> Any:
        doc = self.call.require_doc(".kicad_sch", ".kicad_pcb")
        include_unconnected = bool(self.call.inputs.get("include_unconnected"))
        query = (self.call.inputs.get("query") or "").casefold()
        if doc.suffix.lower() == ".kicad_pcb":
            rows = board_nets(load_board(doc))
        else:
            rows = []
            for net in circuit.connect(load_design(doc)):
                pins = sorted({pin_id(pp.reference, pp.pin.number) for pp in net.pins}, key=circuit.natural_key)
                rows.append({"name": net.name, "pin_count": len(pins), "pins": pins, "no_connect": net.no_connect})
        out = []
        for row in rows:
            if not include_unconnected and row["name"].startswith("unconnected-"):
                continue
            if query and query not in row["name"].casefold():
                continue
            if len(row["pins"]) > NET_PIN_CAP:
                row = {**row, "pins": row["pins"][:NET_PIN_CAP], "pins_truncated": True}
            out.append(row)
        return {"items": out}


def board_nets(board: Board) -> list[dict[str, Any]]:
    nets: dict[str, set[str]] = {}
    for fp in board.footprints:
        for pad in fp.pads:
            if pad.net:
                nets.setdefault(pad.net, set()).add(pin_id(fp.reference, pad.number))
    rows = []
    for name in sorted(nets, key=circuit.natural_key):
        pins = sorted(nets[name], key=circuit.natural_key)
        rows.append({"name": name, "pin_count": len(pins), "pins": pins})
    return rows


HANDLERS: dict[str, type[base.Handler]] = {
    "schematic create": SchematicCreate,
    "schematic check": SchematicCheck,
    "schematic export": SchematicExport,
    "schematic render": SchematicRender,
    "symbol list": SymbolList,
    "symbol get": SymbolGet,
    "net list": NetList,
}
