"""symbol create/update/move/delete, pin update, net update: edits made in a schematic's own text.

The engine (``sch_edit_engine``, ``sch_edit_geometry``) makes every change in memory and reads
the edited design back before anything is written: an edit that would join or split any net
other than the one asked for is refused. The preview's ``after`` values are read from that
edited design with the same functions ``read_back`` uses on the saved files.

``--doc`` is the design's root schematic. Every target key names that file, with the object
in its fragment: ``#/symbols/R1``, ``#/pins/U1.3``, ``#/nets/SDA``. A symbol defined in a
project symbol library is ``<library file>#/symbols/<name>``, and its registration
``<sym-lib-table>#/libraries/<nickname>``.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from .. import base, lib_register
from .. import sch_edit_engine as engine
from .. import sch_edit_library as library
from ..._driver import AdapterError
from ..._driver.targets import path_of
from ..fileformat.circuit import Design, _on_segment, grouped
from ..fileformat.schematic import SchematicError
from ..fileformat.sexpr import SexprError
from ..sch_edit_errors import EditError, kicad_root

GRID_NM = 1_270_000
NM_PER_MM = 1_000_000
LABEL_KINDS = {"label", "global_label", "hierarchical_label", "directive_label", "netclass_flag"}
NOT_FOUND = (
    "no part has that reference", "no net has that name", "the part has no such pin", "the symbol has no such pin",
    "no sheet has that path", "no part has the reference near names", "no symbol library is named",
    "the library ", "a symbol is named LIBRARY:NAME",
)
SYMBOL_FLAGS = {"dnp": "dnp", "in_bom": "in_bom", "on_board": "on_board", "exclude_from_sim": "exclude_from_sim"}
STANDARD_FIELDS = {"value": "Value", "footprint": "Footprint", "datasheet": "Datasheet", "description": "Description"}


# ---------------------------------------------------------------------------- reading

def root_of(call: base.Call) -> Path:
    """The root schematic: --doc, which must not be a sheet of another project schematic."""
    doc = call.require_doc(".kicad_sch")
    for pro in sorted(doc.parent.glob("*.kicad_pro")):
        root = pro.with_suffix(".kicad_sch")
        if root.resolve() == doc.resolve():
            return doc
        if root.is_file():
            raise base.invalid(f"{doc.name} is a sheet of the project {pro.stem}; give --doc its root "
                               f"schematic {root}.")
    return doc


def design(root: Path, sources: dict[Path, str] | None = None) -> Design:
    try:
        return Design(root, sources)
    except (SchematicError, SexprError, UnicodeDecodeError, OSError, ValueError) as exc:
        raise base.invalid(f"The schematic cannot be read: {exc}.") from None


class State:
    """What a design says about symbols, pins and nets, as read back."""

    def __init__(self, d: Design) -> None:
        self.design = d
        self.parts = engine._parts(d)
        _, listed = grouped(d)
        self.net_of = {pin: net.name for net, _ in listed for pin in engine._pins(net)}
        self.nets = {net.name for net, _ in listed}

    def symbol(self, ref: str):
        part = self.parts.get(ref)
        return part.symbols[0] if part and part.symbols else None

    def symbol_fields(self, ref: str) -> dict[str, Any]:
        found = self.symbol(ref)
        if found is None:
            return {"exists": False}
        sch, sym = found
        out: dict[str, Any] = {"exists": True, "library_id": sym.lib_id,
                               "x_mm": mm(sym.position[0]), "y_mm": mm(sym.position[1]),
                               "rotation_deg": sym.angle % 360}
        for name, value in sym.properties.items():
            out[f"fields.{name}"] = value
        for flag in SYMBOL_FLAGS:
            out[flag] = bool(getattr(sym, flag))
        return out

    def pin_at(self, ref: str, number: str):
        part = self.parts.get(ref)
        for sch, sym in part.symbols if part else []:
            lib = sch.library.get(sym.library_name)
            for pin in lib.pins_of(sym.unit, sym.body_style) if lib is not None else []:
                if pin.number == number:
                    return sch, sym.transform(pin.position)
        return None

    def pins_of(self, ref: str) -> dict[str, str | None]:
        """Each pin of a part, by number, with the net it is on (None when on none)."""
        part = self.parts.get(ref)
        out: dict[str, str | None] = {}
        for sch, sym in part.symbols if part else []:
            lib = sch.library.get(sym.library_name)
            for pin in lib.pins_of(sym.unit, sym.body_style) if lib is not None else []:
                out[pin.number] = self.net_of.get((ref, pin.number))
        return out

    def pin_fields(self, ref: str, number: str) -> dict[str, Any]:
        found = self.pin_at(ref, number)
        if found is None:
            return {"exists": False}
        sch, at = found
        return {"exists": True, "net": self.net_of.get((ref, number)), "no_connect": at in sch.no_connects}


# Engine report keys that describe layout bookkeeping, not what the change did.
ENGINE_DETAIL = {"index", "files", "field_layout", "matched_pins", "label_layout"}


def mm(nm: int) -> float:
    return round(nm / NM_PER_MM, 4)


def mm_text(nm: int) -> str:
    return str((Decimal(nm) / NM_PER_MM).normalize()) if nm else "0"


def on_grid(value_mm: float, name: str) -> int:
    nm = round(value_mm * NM_PER_MM)
    if nm % GRID_NM:
        nearest = round(nm / GRID_NM) * GRID_NM / NM_PER_MM
        raise base.invalid(f"--{name} {value_mm:g}mm is off the 1.27 mm schematic grid, where pins do not "
                           f"connect; the nearest grid point is {nearest:g}mm.")
    return nm


def fragment(key: str) -> str:
    return key.split("#", 1)[1] if "#" in key else ""


def selection(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    """One dict per selected object, each with its id and its values."""
    if "items" in inputs:
        return [dict(i) for i in inputs["items"]]
    common = {k: v for k, v in inputs.items() if k not in ("id", "ids", "items")}
    if "ids" in inputs:
        return [{**common, "id": i} for i in inputs["ids"]]
    if "id" in inputs:
        return [{**common, "id": inputs["id"]}]
    return [common]


def refused(exc: EditError) -> AdapterError:
    """The engine's refusal, as the adapter's error: nothing was written."""
    problems = exc.details.get("problems") or []
    texts = [str(p.get("problem", "")) for p in problems if isinstance(p, dict)]
    code = exc.code if exc.code in ("E_VALIDATION", "E_NOT_FOUND", "E_UNSUPPORTED") else "E_VALIDATION"
    if texts and all(t.startswith(NOT_FOUND) for t in texts):
        code = "E_NOT_FOUND"
    detail = []
    for p in problems[:3]:
        where = p.get("pin") or p.get("ref") or p.get("net") or p.get("symbol") or ""
        detail.append(f"{p.get('problem')}{f' ({where})' if where else ''}")
    message = str(exc).rstrip(".") + (": " + "; ".join(detail) if detail else "") + "."
    return AdapterError(code, message, dispatch_state="not_started")


def run_engine(root: Path, changes: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[Path, str]]:
    try:
        return engine.run(root, changes)
    except EditError as exc:
        raise refused(exc) from None
    except (SchematicError, SexprError, UnicodeDecodeError, OSError) as exc:
        raise base.invalid(f"The schematic cannot be read: {exc}.") from None


def blocked(report: dict[str, Any]) -> list[str]:
    """Why the edited design does not read back as asked; empty when it does."""
    verified = report.get("verified") or {}
    out = []
    problems = verified.get("problems") or {}
    if not verified.get("nets_join_the_pins_asked", True):
        joined = problems.get("joined_or_split") if isinstance(problems, dict) else None
        out.append("The edit would join or split nets other than the one asked for"
                   + (f": {joined[0].get('pins')}" if joined else ""))
    if not verified.get("nets_named_as_asked", True):
        out.append("A net would not carry the name asked for")
    if not verified.get("no_other_net_renamed", True):
        out.append("Another net would be renamed")
    if not verified.get("fields_read_back", True):
        out.append("A field would not read back as asked")
    if not verified.get("every_part_still_there", True):
        out.append("A part would be lost")
    if verified.get("connections_preserved") is False:
        out.append("The move would break or make a connection")
    if not out and not engine.passed({k: v for k, v in verified.items() if k != "problems"}):
        out.append("The edited schematic does not read back as asked")
    return out


def write(sources: dict[Path, str]) -> list[str]:
    base.refuse_if_open(*sources)
    for path, text in sources.items():
        base.write_text(path, text)
    return sorted(Path(p).name for p in sources)


# ---------------------------------------------------------------------------- the edits

class Edit(base.Handler):
    """A schematic edit: targets and engine changes from the selection, then the engine."""

    kind = "symbol"

    def plan(self, root: Path, state: State, items: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
        """The targets and the engine changes for the selected items."""
        raise NotImplementedError

    def fields(self, item: dict[str, Any]) -> list[str]:
        raise NotImplementedError

    def observe(self, state: State, frag: str) -> dict[str, Any]:
        kind, _, ident = frag.lstrip("/").partition("/")
        if kind == "symbols":
            return state.symbol_fields(ident)
        if kind == "pins":
            ref, _, number = ident.partition(".")
            return state.pin_fields(ref, number)
        if kind == "nets":
            return {"exists": ident in state.nets, "name": ident if ident in state.nets else None}
        return {}

    def compute(self, root: Path) -> tuple[list[dict], list[dict], dict, dict[Path, str], list[str]]:
        state = State(design(root))
        items = selection(self.call.inputs)
        targets, changes = self.plan(root, state, items)
        report, sources = self.edit(root, changes)
        why = blocked(report) if sources else []
        after = State(design(root, sources)) if sources else state
        preview_changes = []
        for target, item in zip(targets, self.items_for(targets, items), strict=True):
            before_vals = self.observe(state, fragment(target["key"]))
            after_vals = self.observe(after, fragment(target["key"]))
            for name in self.fields(item):
                preview_changes.append({"target": target["key"], "field": name,
                                        "before": before_vals.get(name), "after": after_vals.get(name)})
        return targets, preview_changes, report, sources, why

    def items_for(self, targets: list[dict], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return items

    def edit(self, root: Path, changes: list[dict]) -> tuple[dict, dict[Path, str]]:
        return run_engine(root, changes)

    def preview(self) -> dict[str, Any]:
        root = root_of(self.call)
        targets, changes, report, sources, why = self.compute(root)
        base.refuse_if_open(root, *sources)
        out: dict[str, Any] = {"action": self.action(), "targets": targets, "changes": changes,
                               "can_apply": not why and bool(sources)}
        if not sources:
            why = why or ["The edit changes nothing in the schematic"]
        if why:
            out["can_apply"] = False
            out["blocked_by"] = why
        return out

    def apply(self) -> dict[str, Any]:
        root = root_of(self.call)
        targets, _, report, sources, why = self.compute(root)
        if why or not sources:
            raise base.invalid("The edit cannot be made as previewed: " + "; ".join(why or ["it changes nothing"]) + ".")
        files = write(sources)
        return base.saved({**self.result(targets, report, files), **self.stored(root, targets, report)})

    def stored(self, root: Path, targets: list[dict], report: dict) -> dict[str, Any]:
        """What the change made, read again from the written files (DCLI-068): each target as it is
        now, a symbol with the net of each pin, and the engine's account of what else it changed,
        such as the wires removed with a symbol."""
        state = State(design(root))
        out: dict[str, Any] = {}
        for target in targets:
            frag = fragment(target["key"])
            values = dict(self.observe(state, frag))
            kind, _, ident = frag.lstrip("/").partition("/")
            if kind == "symbols" and values.get("exists"):
                values["pins"] = state.pins_of(ident)
            out[target["label"]] = values
        effects = [{k: v for k, v in change.items() if k not in ENGINE_DETAIL} for change in report.get("changes", [])]
        return {"as_stored": out, "effects": effects}

    def result(self, targets: list[dict], report: dict, files: list[str]) -> dict[str, Any]:
        ids = [fragment(t["key"]).lstrip("/").partition("/")[2] for t in targets]
        return {"id": ids[0] if len(ids) == 1 else ids, "files": files}

    def read_back(self) -> dict[str, Any]:
        root = root_of(self.call)
        state = State(design(root))
        observed = {}
        for key in self.call.targets:
            values = self.observe(state, fragment(key))
            observed[key] = {f: values[f] for f in self.call.fields if f in values} or values
        return {"observed": observed}

    def action(self) -> str:
        return self.call.command

    def target(self, root: Path, frag: str, label: str) -> dict[str, Any]:
        return {"key": base.key(root, frag), "kind": self.kind, "label": label}


def require_part(state: State, ref: Any) -> None:
    if not isinstance(ref, str) or not ref:
        raise base.invalid("Name the symbol by its reference, such as R1.")
    part = state.parts.get(ref)
    if part is None:
        raise base.not_found(f"No symbol has the reference {ref}.")
    if len(part.units) != len(set(part.units)):
        raise base.ambiguous(f"More than one symbol is annotated {ref}; annotate the schematic in KiCad first.",
                             [{"id": sym.uuid, "reference": ref, "unit": sym.unit, "x_mm": mm(sym.position[0]),
                               "y_mm": mm(sym.position[1])} for _, sym in part.symbols])


class SymbolUpdate(Edit):
    def plan(self, root, state, items):
        targets, changes = [], []
        for item in items:
            ref = item.get("id")
            require_part(state, ref)
            change: dict[str, Any] = {"op": "set", "ref": ref}
            for key in STANDARD_FIELDS:
                if key in item:
                    change[key] = item[key]
            if "field_name" in item or "field_value" in item:
                name, value = item.get("field_name"), item.get("field_value")
                if not isinstance(name, str) or not name or value is None:
                    raise base.invalid("--field-name and --field-value are given together.")
                if name in ("Reference",):
                    raise base.unsupported("kicad-cli does not change references; annotate in KiCad.")
                change["fields"] = {name: value}
            for flag in SYMBOL_FLAGS:
                if flag in item:
                    change[flag] = bool(item[flag])
            if len(change) == 2:
                raise base.invalid(f"Nothing to change on {ref}: give a field or a flag.")
            targets.append(self.target(root, f"/symbols/{ref}", ref))
            changes.append(change)
        return targets, changes

    def fields(self, item):
        names = [f"fields.{STANDARD_FIELDS[k]}" for k in STANDARD_FIELDS if k in item]
        if item.get("field_name"):
            names.append(f"fields.{item['field_name']}")
        return names + [flag for flag in SYMBOL_FLAGS if flag in item]

    def action(self):
        return "Change symbol fields"


class SymbolDelete(Edit):
    def plan(self, root, state, items):
        targets, changes = [], []
        for item in items:
            ref = item.get("id")
            require_part(state, ref)
            targets.append(self.target(root, f"/symbols/{ref}", ref))
            changes.append({"op": "remove", "ref": ref})
        return targets, changes

    def fields(self, item):
        return ["exists"]

    def action(self):
        return "Delete symbols with the wires and labels only they had"


class SymbolCreate(Edit):
    """Place a library symbol on a sheet, or define a new symbol in a project symbol library."""

    def preview(self):
        if self.library_mode():
            return LibrarySymbol(self.call).preview()
        return super().preview()

    def apply(self):
        if self.library_mode():
            return LibrarySymbol(self.call).apply()
        return super().apply()

    def read_back(self):
        if self.library_mode():
            return LibrarySymbol(self.call).read_back()
        return super().read_back()

    def library_mode(self) -> bool:
        return self.call.doc is not None and self.call.doc.suffix.lower() == ".kicad_sym"

    def plan(self, root, state, items):
        item = items[0]
        if "definition" in item:
            raise base.invalid("--definition defines a symbol in a library: give --doc a .kicad_sym file.")
        lib_id = item.get("library_id")
        if not isinstance(lib_id, str) or ":" not in lib_id:
            raise base.invalid("symbol create on a schematic needs --library-id LIBRARY:NAME, such as Device:R.")
        change: dict[str, Any] = {"op": "add", "symbol": lib_id}
        ref = item.get("reference") or self.default_reference(root, lib_id)
        change["ref"] = ref
        for key in ("value", "footprint", "near", "sheet"):
            if item.get(key):
                change[key] = item[key]
        if ("x_mm" in item) != ("y_mm" in item):
            raise base.invalid("Give --x and --y together, or neither to let kicad-cli find room.")
        if "x_mm" in item:
            change["at_nm"] = [on_grid(item["x_mm"], "x"), on_grid(item["y_mm"], "y")]
        self._change = change
        return [], [change]

    def compute(self, root):
        state = State(design(root))
        items = selection(self.call.inputs)
        _, changes = self.plan(root, state, items)
        report, sources = self.edit(root, changes)
        added = next((c for c in report.get("changes", []) if c.get("op") == "add"), {})
        ref = added.get("ref") or changes[0]["ref"]
        target = self.target(root, f"/symbols/{ref}", ref)
        why = blocked(report) if sources else []
        after = State(design(root, sources)) if sources else state
        before_vals = self.observe(state, fragment(target["key"]))
        after_vals = self.observe(after, fragment(target["key"]))
        names = ["exists", "library_id", "fields.Value", "fields.Footprint"]
        if "at_nm" in changes[0]:
            names += ["x_mm", "y_mm"]
        preview_changes = [{"target": target["key"], "field": n, "before": before_vals.get(n),
                            "after": after_vals.get(n)} for n in names]
        self._added = added
        return [target], preview_changes, report, sources, why

    def default_reference(self, root: Path, lib_id: str) -> str:
        """The library symbol's own reference prefix, numbered by the engine: R?, U?."""
        try:
            step = engine.Step.read(root, {}, kicad_root())
            resolved, trouble = engine._resolve_symbol(step, step.design.instances[0].schematic, lib_id, kicad_root())
        except (SchematicError, SexprError, UnicodeDecodeError, OSError, ValueError) as exc:
            raise base.invalid(f"The schematic cannot be read: {exc}.") from None
        if resolved is None:
            raise base.not_found(f"{trouble[0].upper()}{trouble[1:]}.")
        prefix = (resolved.symbol.properties.get("Reference") or "U").rstrip("?0123456789") or "U"
        return prefix + "?"

    def result(self, targets, report, files):
        added = getattr(self, "_added", {})
        placed = (added.get("placed") or [{}])[0]
        return {"id": added.get("ref"), "library_id": added.get("symbol"), "sheet": added.get("sheet"),
                "x_mm": (placed.get("at_mm") or [None, None])[0], "y_mm": (placed.get("at_mm") or [None, None])[1],
                "also_added": added.get("also_added", []), "files": files}

    def fields(self, item):
        return []

    def action(self):
        return "Place a library symbol on the schematic"


class SymbolMove(Edit):
    """Move or rotate symbols; the wires, labels and flags only they use move with them."""

    def items_for(self, targets, items):
        return items

    def plan(self, root, state, items):
        targets = []
        for item in items:
            ref = item.get("id")
            require_part(state, ref)
            if not any(k in item for k in ("x_mm", "y_mm", "rotation_deg")):
                raise base.invalid(f"Give --x and --y, --rotation, or both for {ref}.")
            if ("x_mm" in item) != ("y_mm" in item):
                raise base.invalid("Give --x and --y together.")
            if "x_mm" in item:
                on_grid(item["x_mm"], "x")
                on_grid(item["y_mm"], "y")
            if "rotation_deg" in item:
                angle = item["rotation_deg"]
                if round(angle) != angle or round(angle) % 90:
                    raise base.invalid("--rotation is 0, 90, 180 or 270 degrees.")
            targets.append(self.target(root, f"/symbols/{ref}", ref))
        return targets, items

    def fields(self, item):
        return (["x_mm", "y_mm"] if "x_mm" in item else []) + (["rotation_deg"] if "rotation_deg" in item else [])

    def edit(self, root, changes):
        from .. import sch_edit_geometry as geometry  # noqa: PLC0415
        from ..sch_edit_objects import Catalog, CatalogError  # noqa: PLC0415

        sources: dict[Path, str] = {}
        report: dict[str, Any] = {"verified": {}, "moved": []}
        for item in changes:
            d = design(root, sources)
            try:
                catalog = Catalog(d)
            except CatalogError as exc:
                raise base.invalid(f"The schematic cannot be read for a move: {exc}.") from None
            transform, moved, shared = move_transform(d, catalog, item)
            if shared:
                where = ", ".join(f"({mm(p[0]):g} mm, {mm(p[1]):g} mm)" for p in shared[:3])
                report["verified"] = {"connections_preserved": False}
                report.setdefault("blocked", []).append(
                    f"{item['id']} is wired directly to other parts at {where}; move them together, or connect "
                    "them through net labels with pin update")
                continue
            if transform is None:
                continue
            try:
                result, edited = geometry.run_geometry(root, [transform], base=sources)
            except EditError as exc:
                raise refused(exc) from None
            sources.update(edited)
            verified = result.get("verified") or {}
            if not verified.get("connections_preserved", True):
                report["verified"] = {"connections_preserved": False}
            report["moved"].append({"id": item["id"], "with": moved})
        return report, sources

    def compute(self, root):
        targets, changes, report, sources, why = super().compute(root)
        return targets, changes, report, sources, report.get("blocked") or why

    def result(self, targets, report, files):
        out = super().result(targets, report, files)
        out["moved_with"] = {m["id"]: m["with"] for m in report.get("moved", [])}
        return out

    def action(self):
        return "Move or rotate symbols with their own wires and labels"


def move_transform(d: Design, catalog, item: dict[str, Any]):
    """The transform of one symbol and its own stubs; or the points it shares with others."""
    ref = item["id"]
    objects = [o for o in catalog.objects if o.kind == "symbol"
               and any(row.get("ref") == ref for row in o.record.get("instances", []))]
    if len(objects) != 1:
        raise base.unsupported(f"{ref} has {len(objects)} units; symbol move moves single-unit symbols.")
    obj = objects[0]
    sch = obj.schematic
    sym = next(s for s in sch.symbols if s.node is obj.node)
    lib = sch.library.get(sym.library_name)
    if lib is None:
        raise base.invalid(f"The schematic has no drawing of {sym.lib_id} for {ref}.")
    own = {sym.transform(p.position) for p in lib.pins_of(sym.unit, sym.body_style)}
    others: set[tuple[int, int]] = set()
    # A power symbol at the end of the part's own wire names that wire's net: it moves too.
    powers: list[tuple[Any, set[tuple[int, int]]]] = []
    by_node = {id(o.node): o for o in catalog.objects if o.schematic is sch and o.kind == "symbol"}
    for other in sch.symbols:
        if other.node is obj.node:
            continue
        olib = sch.library.get(other.library_name)
        pins = {other.transform(p.position) for p in olib.pins_of(other.unit, other.body_style)} if olib else set()
        if olib is not None and olib.power and id(other.node) in by_node:
            powers.append((by_node[id(other.node)], pins))
        else:
            others |= pins
    for sheet in sch.sheets:
        others.update(pin.position for pin in sheet.pins)
    same = [o for o in catalog.objects if o.schematic is sch and o is not obj]
    group, shared = stub_group(same, own, others, powers)
    x0, y0 = sym.position
    dx = round(item["x_mm"] * NM_PER_MM) - x0 if "x_mm" in item else 0
    dy = round(item["y_mm"] * NM_PER_MM) - y0 if "y_mm" in item else 0
    turn = (round(item["rotation_deg"]) - sym.angle) % 360 if "rotation_deg" in item else 0
    if shared:
        return None, {}, sorted(shared)
    if not dx and not dy and not turn:
        return None, {}, []
    selected = [obj, *group]
    selectors = [{"root": catalog.root, "snapshot": catalog.snapshot, "object_id": o.object_id} for o in selected]
    transform = {
        "op": "transform", "objects": selectors, "translate_mm": [mm_text(dx), mm_text(dy)],
        "rotate_deg": turn, "pivot_mm": [mm_text(x0), mm_text(y0)],
        "affected_instances": sorted({inst.path for o in selected for inst in o.instances}),
    }
    counts: dict[str, int] = {}
    for o in group:
        kind = "labels" if o.kind in LABEL_KINDS else "power_symbols" if o.kind == "symbol" else o.kind + "s"
        counts[kind] = counts.get(kind, 0) + 1
    return transform, counts, []


def stub_group(objects, own: set, others: set, powers=()):
    """The wires, labels, flags, junctions and power symbols joined only to these pins, and
    any other symbol's or sheet's pin they reach."""
    wires = [o for o in objects if o.kind == "wire"]
    points = {o.object_id: [tuple(p) for p in o.record["geometry"].get("points_nm") or []] for o in wires}
    reached = set(own)
    chosen: dict[str, Any] = {}
    grew = True
    while grew:
        grew = False
        for wire in wires:
            if wire.object_id in chosen:
                continue
            pts = points[wire.object_id]
            segments = list(zip(pts, pts[1:], strict=False))
            if any(p in reached for p in pts) or any(_on_segment(q, a, b) for q in reached for a, b in segments):
                chosen[wire.object_id] = wire
                reached.update(pts)
                grew = True
    segments = [seg for w in chosen.values() for seg in zip(points[w.object_id], points[w.object_id][1:], strict=False)]

    def on(point) -> bool:
        return point in reached or any(_on_segment(point, a, b) for a, b in segments)

    shared = {p for p in others if on(p)}
    group = list(chosen.values())
    for o in objects:
        if o.kind in LABEL_KINDS | {"no_connect", "junction"}:
            at = o.record["geometry"].get("coordinate_nm")
            if at and on(tuple(at)):
                group.append(o)
    for power, pins in powers:
        if any(on(pin) for pin in pins):
            group.append(power)
    return group, shared


class PinUpdate(Edit):
    kind = "pin"

    def plan(self, root, state, items):
        targets, changes = [], []
        step = None
        for item in items:
            ident = item.get("id")
            ref, dot, number = str(ident or "").partition(".")
            if not dot or not ref or not number:
                raise base.invalid("Name a pin as REFERENCE.NUMBER, such as U1.3.")
            require_part(state, ref)
            if state.pin_at(ref, number) is None:
                raise base.not_found(f"{ref} has no pin {number}.")
            net, flag = item.get("net"), item.get("no_connect")
            if (net is None) == (not flag):
                raise base.invalid(f"Give {ident} either --net NAME or --no-connect.")
            current = state.pin_fields(ref, number)
            if step is None:
                step = engine.Step.read(root, {}, kicad_root())
            touched = pin_touched(step, ref, number)
            if net is not None:
                if current["net"] == net and touched:
                    raise base.invalid(f"{ident} is already on the net {net}.")
                if touched:
                    changes.append({"op": "disconnect", "ref": ref, "pin": number, "no_connect": False})
                changes.append({"op": "connect", "ref": ref, "pin": number, "net": net})
            else:
                if current["no_connect"] and not touched:
                    raise base.invalid(f"{ident} is already marked as not connected.")
                changes.append({"op": "disconnect", "ref": ref, "pin": number, "no_connect": True})
            targets.append(self.target(root, f"/pins/{ref}.{number}", f"{ref}.{number}"))
        return targets, changes

    def fields(self, item):
        return ["net"] if item.get("net") is not None else ["net", "no_connect"]

    def result(self, targets, report, files):
        out = super().result(targets, report, files)
        joined = [j for c in report.get("changes", []) for j in c.get("joined", [])]
        if joined:
            out["net"] = joined[-1].get("net")
        return out

    def action(self):
        return "Connect pins to nets, or mark them as not connected"


def renamed(nets: set[str], old: str, new: Any) -> str | None:
    """The name of the net that was ``old``: still ``old``, or the net ``new`` now names. A local
    label's net carries its sheet's path, so ``/SIG`` renamed to ``SIGNAL`` is ``/SIGNAL``."""
    if old in nets:
        return old
    if not isinstance(new, str) or not new:
        return None
    prefix = old[: old.rfind("/") + 1] if old.startswith("/") else ""
    for name in (prefix + new, new, "/" + new):
        if name in nets:
            return name
    found = [n for n in nets if n.endswith("/" + new)]
    return found[0] if len(found) == 1 else None


def pin_touched(step, ref: str, number: str) -> bool:
    """Whether anything but a no-connect flag meets the pin: then it is connected."""
    part = step.parts.get(ref)
    for sch, sym in part.symbols if part else []:
        lib = sch.library.get(sym.library_name)
        if lib is None:
            continue
        canvas = step.canvas(sch)
        for placed in canvas.pins(sym, lib):
            if placed.pin.number == number:
                return bool([t for t in canvas.touching(placed.at, besides="pin") if t != "no_connect"])
    return False


class NetUpdate(Edit):
    kind = "net"

    def plan(self, root, state, items):
        item = items[0]
        old, new = item.get("id"), item.get("name")
        if not isinstance(new, str) or not new.strip():
            raise base.invalid("net update needs --name: the new name.")
        if old not in state.nets:
            raise base.not_found(f"No net is named {old}.")
        if new in state.nets:
            raise base.invalid(f"A net is already named {new}; renaming {old} to it would join the two nets.")
        self._new = new
        return [self.target(root, f"/nets/{old}", old)], [{"op": "rename", "net": old, "to": new}]

    def fields(self, item):
        return ["name"]

    def observe(self, state, frag):
        kind, _, ident = frag.lstrip("/").partition("/")
        if kind != "nets":
            return super().observe(state, frag)
        return {"name": renamed(state.nets, ident, self.call.inputs.get("name"))}

    def result(self, targets, report, files):
        return {"id": self.call.inputs.get("id"), "name": self.call.inputs.get("name"), "files": files}

    def action(self):
        return "Rename a net: every label that names it"


# ---------------------------------------------------------------------------- library symbols

class LibrarySymbol(base.Handler):
    """symbol create on a project symbol library: a new symbol, and the library's registration."""

    def context(self) -> tuple[Path, Path, dict[str, Any]]:
        lib = self.call.doc
        if lib is None or lib.suffix.lower() != ".kicad_sym":
            raise base.invalid("--doc is a project symbol library (.kicad_sym).")
        definition = self.call.inputs.get("definition")
        if definition is None:
            raise base.invalid("symbol create on a library needs --definition: the symbol as JSON, inline or in a file.")
        project = next((p for p in [lib.parent, *lib.parent.parents][:4] if any(p.glob("*.kicad_pro"))), None)
        if project is None:
            raise base.invalid(f"{lib.name} is not in a project folder: a project library sits beside a .kicad_pro "
                               "or below it.")
        try:
            spec = library.check(definition)
        except library.DefinitionError as exc:
            raise base.invalid(f"The symbol definition is invalid: {exc}.") from None
        return lib, project, spec

    def plan(self):
        lib, project, spec = self.context()
        raw = lib.read_text(encoding="utf-8") if lib.is_file() else None
        try:
            text = library.with_symbol(raw, spec)
        except library.DefinitionError as exc:
            raise base.invalid(f"{lib.name}: {exc}.") from None
        except SexprError as exc:
            raise base.invalid(f"{lib.name} cannot be read as a symbol library: {exc}.") from None
        nickname = lib.stem
        uri = lib_register.uri_for(project, lib)
        table = lib_register.table_path(project, "symbol")
        known = lib_register.registered(project, "symbol", nickname)
        sources = {lib: text}
        if known is None:
            table_raw = table.read_text(encoding="utf-8") if table.is_file() else None
            sources[table], _ = lib_register.with_library(table_raw, "symbol", nickname, uri)
        elif known != uri:
            raise base.invalid(f"The project's sym-lib-table names {nickname} for {known}, not this library.")
        return lib, table, spec, nickname, sources, known is None

    def keys(self, lib: Path, table: Path, name: str, nickname: str) -> tuple[str, str]:
        return base.key(lib, f"/symbols/{name}"), base.key(table, f"/libraries/{nickname}")

    def preview(self) -> dict[str, Any]:
        lib, table, spec, nickname, sources, registering = self.plan()
        base.refuse_if_open(*sources)
        sym_key, table_key = self.keys(lib, table, spec["name"], nickname)
        targets = [{"key": sym_key, "kind": "library_symbol", "label": f"{nickname}:{spec['name']}"}]
        changes = [{"target": sym_key, "field": "exists", "before": False, "after": True},
                   {"target": sym_key, "field": "pin_count", "before": None, "after": len(spec["pins"])}]
        if registering:
            changes.append({"target": table_key, "field": "exists", "before": False, "after": True})
        return {"action": "Define a symbol in a project symbol library", "targets": targets,
                "changes": changes, "can_apply": True}

    def apply(self) -> dict[str, Any]:
        lib, table, spec, nickname, sources, registering = self.plan()
        base.refuse_if_open(*sources)
        for path, text in sources.items():
            base.write_text(path, text)
        facts = base.saved({"id": spec["name"], "library": nickname, "registered": registering,
                            "files": sorted(p.name for p in sources)})
        facts["files"] = [{"path": str(p)} for p in sorted(sources)]
        return facts

    def read_back(self) -> dict[str, Any]:
        observed: dict[str, dict[str, Any]] = {}
        for key in self.call.targets:
            path = Path(path_of(key) or "")
            kind, _, ident = fragment(key).lstrip("/").partition("/")
            if kind == "symbols":
                found = library.read_symbol(path.read_text(encoding="utf-8"), ident) if path.is_file() else None
                observed[key] = {"exists": found is not None, "pin_count": len(found["pins"]) if found else None}
            elif kind == "libraries":
                observed[key] = {"exists": lib_register.registered(path.parent, "symbol", ident) is not None}
        return {"observed": observed}


HANDLERS: dict[str, type[base.Handler]] = {
    "symbol create": SymbolCreate,
    "symbol update": SymbolUpdate,
    "symbol move": SymbolMove,
    "symbol delete": SymbolDelete,
    "pin update": PinUpdate,
    "net update": NetUpdate,
}
