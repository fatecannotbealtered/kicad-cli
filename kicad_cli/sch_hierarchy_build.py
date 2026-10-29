"""In-memory incremental sheet construction for the public circuit batch.

Source files and logical occurrences deliberately have separate identities.
This module never writes a project or silently annotates a physical component.
"""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

from .fileformat.circuit import Design
from .fileformat.schematic import Schematic
from .fileformat.sexpr import Document, List, copy, quote, render, symbol

SHAPES = {"input", "output", "bidirectional", "tri_state", "passive"}
OPS = {"create_sheet", "reference_sheet", "update_sheet", "update_interface", "add_sheet_pin"}
# Native sheet-pin text is anchored on the frame and extends into the sheet.
# Top/bottom use the native vertical-text horizontal justification convention.
PIN_JUSTIFY = {"left": "left", "right": "right", "top": "right", "bottom": "left"}


class Hierarchy:
    def __init__(self, owner):
        self.c = owner
        self.aliases = {}
        self.import_catalogs = []
        self.interface_sources = set()
        self.metadata = {
            "new_sources": [],
            "new_instances": [],
            "symbol_instances": [],
            "sheet_instances": [],
        }
        self.events = []

    def fail(self, message, path="/changes/0", **details):
        from .sch_construct import ConstructError

        raise ConstructError(message, path, **details)

    def instance(self, value, location="/changes/0"):
        if isinstance(value, str):
            if value not in self.c.instances:
                self.fail(
                    "Unknown logical instance; re-query the project", location, instance=value
                )
            return value
        if not isinstance(value, dict) or set(value) - {"new", "parent", "source_instance"}:
            self.fail("Instance is a full path or an explicit earlier sheet occurrence", location)
        alias = self.aliases.get(value.get("new"))
        if alias is None or "parent" not in value:
            self.fail(
                "Instance reference requires an earlier sheet id and explicit parent", location
            )
        parent = self.instance(value["parent"], location + "/parent")
        if parent not in alias["parents"]:
            self.fail("The sheet does not occur in this parent", location, parents=alias["parents"])
        result = parent + "/" + alias["uuid"]
        source = value.get("source_instance")
        if source is not None:
            origins = alias["source_instances"]
            matches = [p for p in origins if source == p or source.startswith(p + "/")]
            if not matches:
                self.fail("source_instance is outside the referenced source subtree", location)
            origin = max(matches, key=len)
            result += source[len(origin) :]
        if result not in self.c.instances:
            self.fail("The batch occurrence does not exist", location, instance=result)
        return result

    def refresh(self):
        c = self.c
        try:
            c.working = Design(c.root, {p: sch.document.dumps() for p, sch in c.documents.items()})
        except (ValueError, OSError, RecursionError) as exc:
            self.fail("The requested hierarchy cannot be loaded", reason=str(exc))
        if c.working.missing:
            self.fail("Missing child source", missing=c.working.missing)
        c.instances = {i.path: i for i in c.working.instances}
        # A later action may add an occurrence of a parent source. Earlier
        # child-sheet aliases denote that same source object, not a frozen list
        # of the occurrences that happened to exist when it was created.
        for alias in self.aliases.values():
            alias["parents"] = sorted(
                i.path
                for i in c.instances.values()
                if i.schematic.path.resolve() == alias["parent_source"]
            )
        for p, sch in c.working.schematics.items():
            c.documents.setdefault(p.resolve(), sch)

    def path(self, name, location):
        if not isinstance(name, str) or not name or "\\" in name:
            self.fail("file is a project-root-relative POSIX .kicad_sch path", location)
        raw = Path(name)
        if (
            raw.is_absolute()
            or any(x in {".", ".."} for x in raw.parts)
            or raw.suffix != ".kicad_sch"
            or ":" in name
        ):
            self.fail("Child path must remain inside the authorized root project", location)
        p = self.c.root.parent / raw
        if not p.parent.is_dir() or p.parent.resolve() != p.parent.absolute():
            self.fail("Child parent must already exist without symbolic/reparse aliases", location)
        if p.is_symlink() or p.resolve() != p.absolute():
            self.fail("Child source aliases are not accepted", location)
        for old in self.c.documents:
            if str(old).casefold() == str(p).casefold() and old != p:
                self.fail("Case/path alias conflicts with an existing source", location)
            if p.exists() and old.exists() and p.samefile(old) and old != p:
                self.fail("A file identity may have only one canonical source path", location)
        return p.resolve()

    def _pin(self, row, at, size, index, ordinal, location):
        from .sch_construct import _known, _mm, _pair

        _known(row, {"name", "shape", "edge", "offset_mm"}, location)
        name = self.c._scalar(row.get("name"), location + "/name")
        shape, edge = row.get("shape"), row.get("edge")
        if shape not in SHAPES or edge not in {"left", "right", "top", "bottom"}:
            self.fail("Unsupported sheet pin shape or edge", location)
        offset = _pair([row.get("offset_mm"), 0], location)[0]
        limit = size[1] if edge in {"left", "right"} else size[0]
        if not 0 < offset < limit:
            self.fail("Pin offset must be strictly inside its frame edge", location)
        x, y = at
        dx, dy, angle = {
            "left": (0, offset, 180),
            "right": (size[0], offset, 0),
            "top": (offset, 0, 90),
            "bottom": (offset, size[1], 270),
        }[edge]
        return List.new(
            "pin",
            quote(name),
            symbol(shape),
            List.new("at", _mm(x + dx), _mm(y + dy), str(angle)),
            List.new(
                "effects",
                List.new("font", List.new("size", "1.27", "1.27")),
                List.new("justify", symbol(PIN_JUSTIFY[edge])),
            ),
            List.new("uuid", quote(self.c.uid(index, f"sheet-pin/{ordinal}"))),
        )

    def add(self, action, index):
        from .sch_construct import _known, _mm, _pair

        c, loc = self.c, f"/changes/0/actions/{index}"
        _known(
            action,
            {
                "op",
                "id",
                "page",
                "file",
                "name",
                "at_mm",
                "size_mm",
                "pins",
                "annotations",
                "expose",
                "contact_targets",
            },
            loc,
        )
        parent_path = self.instance(action.get("page"), loc + "/page")
        parent = c.page(parent_path, loc + "/page")
        file = self.path(action.get("file"), loc + "/file")
        name = c._scalar(action.get("name"), loc + "/name")
        if "/" in name or any(s.name == name for s in parent.sheets):
            self.fail(
                "Sheet display name must be unique in its source parent and contain no slash",
                loc + "/name",
            )
        at, size = (
            _pair(action.get("at_mm"), loc + "/at_mm"),
            _pair(action.get("size_mm"), loc + "/size_mm"),
        )
        if min(size) <= 0:
            self.fail("Sheet dimensions must be positive", loc + "/size_mm")
        _pair([_mm(at[0] + size[0]), _mm(at[1] + size[1])], loc + "/size_mm")
        if action["op"] == "create_sheet":
            if file.exists() or file in c.documents:
                self.fail(
                    "New child source already exists; use explicit reference_sheet", loc + "/file"
                )
            child = List.new(
                "kicad_sch",
                List.new("version", "20250114"),
                List.new("generator", quote("kicad-cli")),
                List.new("uuid", quote(c.uid(index, "document"))),
                List.new("paper", quote("A4")),
                List.new("title_block", List.new("title", quote(name))),
                List.new("lib_symbols"),
                List.new("embedded_fonts", symbol("no")),
            )
            c.documents[file] = Schematic(Document.parse(render(child)), file)
            self.metadata["new_sources"].append(c._relative(c.documents[file]))
            c.changed_files.add(file)
        elif file not in c.documents:
            from .sch_objects import Catalog

            try:
                imported = Design(file)
            except (ValueError, OSError, RecursionError) as exc:
                self.fail("Referenced module cannot be loaded", loc + "/file", reason=str(exc))
            if imported.missing:
                self.fail(
                    "Referenced module has missing source files", loc, missing=imported.missing
                )
            for source, sch in imported.schematics.items():
                if not source.resolve().is_relative_to(c.root.parent):
                    self.fail(
                        "Referenced child source escapes the project",
                        loc + "/file",
                        source=str(source),
                    )
                relative = source.resolve().relative_to(c.root.parent).as_posix()
                self.path(relative, loc + "/file")
                if source.resolve() not in c.documents:
                    c.documents[source.resolve()] = sch
                    self.metadata["new_sources"].append(relative)
                    self.metadata.setdefault("imported_sources", {})[relative] = sch.document.source
            for source in imported.schematics:
                self.import_catalogs.append(
                    Catalog(
                        Design(source),
                        identity_root=c.identity_root.parent
                        / c._relative(c.documents[source.resolve()]),
                    )
                )
        source_instances = sorted(
            i.path for i in c.instances.values() if i.schematic.path.resolve() == file
        )
        if not source_instances and action["op"] == "reference_sheet":
            source_instances = ["/" + c.documents[file].uuid]
        uid = c.uid(index, "sheet")
        pins = action.get("pins", [])
        if not isinstance(pins, list) or len(pins) > 128:
            self.fail("A sheet has at most128 explicit scalar pins", loc + "/pins")
        pin_nodes = [
            self._pin(p, at, size, index, j, loc + f"/pins/{j}") for j, p in enumerate(pins)
        ]
        if len({p.value(1) for p in pin_nodes}) != len(pin_nodes) or len(
            {tuple(p.find("at").values()[0:2]) for p in pin_nodes}
        ) != len(pin_nodes):
            self.fail("Sheet pin names and anchor positions must be distinct", loc + "/pins")
        from .native.sch_create import _property

        node = List.new(
            "sheet",
            List.new("at", *map(_mm, at)),
            List.new("size", *map(_mm, size)),
            List.new("stroke", List.new("width", "0.1524"), List.new("type", symbol("default"))),
            List.new("fill", List.new("color", "0", "0", "0", "0")),
            List.new("uuid", quote(uid)),
            _property("Sheetname", name, (at[0], at[1] - 1_270_000)),
            _property(
                "Sheetfile",
                Path(os.path.relpath(file, parent.path.parent)).as_posix(),
                (at[0], at[1] + size[1] + 1_270_000),
            ),
            *pin_nodes,
        )
        parent.root.append(node)
        c.changed_files.add(parent.path.resolve())
        target = c._alias(action, parent, "sheet", [node], index)
        self.aliases[action["id"]] = {
            "uuid": uid,
            "parent_source": parent.path.resolve(),
            "parents": c._instance_paths(parent),
            "source_instances": source_instances,
        }
        for ordinal, row in enumerate(action.get("expose", [])):
            _known(row, {"id", "target"}, loc + f"/expose/{ordinal}")
            sch, nodes = c._anchor_nodes({"selector": row.get("target")}, loc)
            if len(nodes) != 1:
                self.fail("Expose selects one saved source object", loc)
            saved = dict(c.action_targets)
            c._alias({"id": row.get("id")}, sch, nodes[0].head, nodes, index)
            c.action_targets = saved
        before_paths = set(c.instances)
        self.refresh()
        new_paths = sorted(set(c.instances) - before_paths)
        self.metadata["new_instances"].extend(new_paths)
        self._annotate_pages(new_paths)
        self._annotate_symbols(action.get("annotations", []), new_paths, index)
        self.refresh()
        self.events.append(
            {
                "index": index,
                "op": action["op"],
                **target,
                "file": c._relative(c.documents[file]),
                "new_instances": new_paths,
                "name": name,
                "at_mm": action["at_mm"],
                "size_mm": action["size_mm"],
                "pins": deepcopy(pins),
                "source_is_copied": False,
            }
        )
        c.report.append(self.events[-1])

    def imported_target(self, selector, location):
        from .sch_objects import CatalogError

        for catalog in self.import_catalogs:
            if isinstance(selector, dict) and selector.get("root") == catalog.root:
                obj = catalog.resolve(selector)
                if not obj.record["uuid"]:
                    self.fail("Imported editable objects require saved UUIDs", location)
                sch = self.c.documents[obj.schematic.path.resolve()]
                node = next(
                    (
                        n
                        for n in sch.root.lists()
                        if n.find("uuid") is not None
                        and n.find("uuid").value(1) == obj.record["uuid"]
                    ),
                    None,
                )
                if node is None or obj.kind != node.head:
                    self.fail("Imported selector no longer identifies its source object", location)
                return sch, [node]
        raise CatalogError("Selector is outside the captured project/module catalogs")

    def _annotate_pages(self, paths):
        c = self.c
        used = {i.page for i in c.instances.values() if i.page}
        number = 2
        for path in paths:
            i = c.instances[path]
            if i.parent is None:
                continue
            parent = c.documents[i.parent.schematic.path.resolve()]
            node = next(s.node for s in parent.sheets if s.uuid == i.sheet.uuid)
            while str(number) in used:
                number += 1
            page = str(number)
            used.add(page)
            container = node.find("instances")
            if container is None:
                container = List.new("instances")
                node.append(container)
            project = next(
                (p for p in container.find_all("project") if p.value(1) == c.project), None
            )
            if project is None:
                project = List.new("project", quote(c.project))
                container.append(project)
            project.append(List.new("path", quote(i.parent.path), List.new("page", quote(page))))
            c.changed_files.add(parent.path.resolve())
            self.metadata["sheet_instances"].append(
                {
                    "source_relative": c._relative(parent),
                    "uuid": i.sheet.uuid,
                    "parent_instance": i.parent.path,
                    "page": page,
                    "project": c.project,
                }
            )

    def _annotate_symbols(self, rows, new_paths, index):
        from .sch_construct import REFERENCE, _known

        c, loc = self.c, f"/changes/0/actions/{index}/annotations"
        if not isinstance(rows, list):
            self.fail("annotations must explicitly map every imported symbol occurrence", loc)
        required = {
            (c._relative(c.documents[c.instances[p].schematic.path.resolve()]), s.uuid, p)
            for p in new_paths
            for s in c.documents[c.instances[p].schematic.path.resolve()].symbols
        }
        seen = set()
        for j, row in enumerate(rows):
            _known(row, {"target", "instance", "ref", "unit"}, loc + f"/{j}")
            target = row.get("target")
            anchor = (
                {"new": target["new"]}
                if isinstance(target, dict) and set(target) == {"new"}
                else {"selector": target}
            )
            sch, nodes = c._anchor_nodes(anchor, loc)
            if len(nodes) != 1 or nodes[0].head != "symbol":
                self.fail("Annotation target must identify one symbol unit", loc)
            node = nodes[0]
            path = self.instance(row.get("instance"), loc)
            key = (c._relative(sch), node.find("uuid").value(1), path)
            ref = row.get("ref")
            if (
                key not in required
                or key in seen
                or not isinstance(ref, str)
                or not REFERENCE.fullmatch(ref)
            ):
                self.fail(
                    "Annotation must name one distinct new symbol occurrence and a legal reference",
                    loc + f"/{j}",
                )
            seen.add(key)
            placed = c._symbol(sch, node)
            prototype_units = {
                (s.instance(i.path).unit if s.instance(i.path) else s.unit)
                for i in [
                    *c.before.instances,
                    *(i for catalog in self.import_catalogs for i in catalog.design.instances),
                ]
                if i.schematic.path.resolve() == sch.path.resolve()
                for s in i.schematic.symbols
                if s.uuid == placed.uuid
            }
            if "unit" not in row and len(prototype_units) > 1:
                self.fail(
                    "Different prototype units require an explicit annotation unit",
                    loc + f"/{j}/unit",
                )
            unit = row.get("unit", next(iter(prototype_units), placed.unit))
            lib = sch.library.get(placed.library_name)
            if (
                type(unit) is not int
                or unit < 1
                or lib is None
                or unit > lib.unit_count
                or not any(
                    p.unit in (0, unit) and p.body_style in (0, placed.body_style) for p in lib.pins
                )
            ):
                self.fail(
                    "Annotation unit must identify a supported pin-bearing library unit",
                    loc + f"/{j}/unit",
                )
            container = node.find("instances")
            if container is None:
                container = List.new("instances")
                node.append(container)
            project = next(
                (p for p in container.find_all("project") if p.value(1) == c.project), None
            )
            if project is None:
                project = List.new("project", quote(c.project))
                container.append(project)
            project.append(
                List.new(
                    "path",
                    quote(path),
                    List.new("reference", quote(ref)),
                    List.new("unit", str(unit)),
                )
            )
            c.changed_files.add(sch.path.resolve())
            self.metadata["symbol_instances"].append(
                {
                    "source_relative": c._relative(sch),
                    "uuid": key[1],
                    "instance_path": path,
                    "reference": ref,
                    "unit": unit,
                    "project": c.project,
                }
            )
        if seen != required:
            self.fail(
                "Every newly referenced symbol unit needs explicit per-instance annotation",
                loc,
                missing=[list(k) for k in sorted(required - seen)],
            )

    def interface(self, action, index):
        from .sch_construct import _known

        c, loc = self.c, f"/changes/0/actions/{index}"
        _known(action, {"op", "id", "sheets", "labels", "name", "shape", "contact_targets"}, loc)
        name, shape = c._scalar(action.get("name"), loc + "/name"), action.get("shape")
        if shape not in SHAPES:
            self.fail("Unsupported interface direction", loc + "/shape")
        targets, touched, signature = [], [], None
        for j, row in enumerate(action.get("sheets", [])):
            _known(row, {"target", "pin_ordinal"}, loc + f"/sheets/{j}")
            obj = c.catalog.resolve(row.get("target"))
            ordinal = row.get("pin_ordinal")
            if obj.kind != "sheet" or type(ordinal) is not int:
                self.fail("Interface target requires a public sheet selector and pin ordinal", loc)
            sch = c.documents[obj.schematic.path.resolve()]
            node = c.work_nodes[obj.object_id]
            pins = node.find_all("pin")
            if not 0 <= ordinal < len(pins):
                self.fail("Sheet pin ordinal does not exist", loc)
            p = pins[ordinal]
            child = (
                sch.path.parent
                / next(x.value(2) for x in node.find_all("property") if x.value(1) == "Sheetfile")
            ).resolve()
            current = (child, p.value(1))
            if signature is None:
                signature = current
            if signature != current:
                self.fail("One interface action modifies one explicit source interface", loc)
            touched.append((obj, sch, node, p))
            targets.append(
                {
                    "kind": "sheet",
                    "source_relative": obj.source_relative,
                    "uuids": [obj.record["uuid"]],
                }
            )
        if signature is None:
            self.fail("Interface update requires all incoming sheet pin source objects", loc)
        child, old_name = signature
        actual = {
            (s.path.resolve(), sheet.uuid, j)
            for s in c.documents.values()
            for sheet in s.sheets
            if (s.path.parent / sheet.file).resolve() == child
            for j, p in enumerate(sheet.pins)
            if p.name == old_name
        }
        selected = {
            (sch.path.resolve(), node.find("uuid").value(1), node.find_all("pin").index(p))
            for _, sch, node, p in touched
        }
        if len(selected) != len(touched) or selected != actual:
            self.fail(
                "Declare every parent source pin referencing this shared interface",
                loc,
                required=[list(map(str, k)) for k in sorted(actual)],
            )
        labels = []
        for target in action.get("labels", []):
            obj, sch, node = c._target(target, loc + "/labels")
            if (
                obj.kind != "hierarchical_label"
                or sch.path.resolve() != child
                or node.value(1) != old_name
            ):
                self.fail("Paired label does not define the selected interface", loc)
            labels.append((obj, sch, node))
            targets.append(
                {
                    "kind": obj.kind,
                    "source_relative": obj.source_relative,
                    "uuids": [obj.record["uuid"]],
                }
            )
        required_labels = {
            id(n)
            for n in c.documents[child].root.find_all("hierarchical_label")
            if n.value(1) == old_name
        }
        if {id(n) for _, _, n in labels} != required_labels or not required_labels:
            self.fail("Declare all child hierarchical labels for the selected interface", loc)
        for obj, sch, _node, pin in touched:
            if obj.object_id in c.used_targets:
                self.fail("A source object may be edited only once in a batch", loc)
            c.used_targets.add(obj.object_id)
            c.touched.append(obj)
            pin.set(1, quote(name))
            pin.set(2, symbol(shape))
            c.changed_files.add(sch.path.resolve())
        for _, sch, node in labels:
            node.set(1, quote(name))
            node.replace(node.find("shape"), List.new("shape", symbol(shape)))
            c.changed_files.add(sch.path.resolve())
        c.action_targets[index] = {"objects": targets}
        c.report.append(
            {
                "index": index,
                "op": "update_interface",
                "old_name": old_name,
                "name": name,
                "shape": shape,
                "objects": targets,
            }
        )
        self.refresh()

    def frame(self, action, index):
        from .sch_construct import _known, _mm, _pair

        c, loc = self.c, f"/changes/0/actions/{index}"
        _known(
            action, {"op", "target", "at_mm", "size_mm", "pin_positions", "contact_targets"}, loc
        )
        obj = c.catalog.resolve(action.get("target"))
        if obj.kind != "sheet" or obj.object_id in c.used_targets:
            self.fail("Select one unmodified sheet source", loc)
        sch = c.documents[obj.schematic.path.resolve()]
        node = c.work_nodes[obj.object_id]
        at = _pair(action.get("at_mm"), loc + "/at_mm")
        size = _pair(action.get("size_mm"), loc + "/size_mm")
        if min(size) <= 0:
            self.fail("Sheet dimensions must be positive", loc)
        _pair([_mm(at[0] + size[0]), _mm(at[1] + size[1])], loc + "/size_mm")
        pins = node.find_all("pin")
        rows = action.get("pin_positions", [])
        if len(rows) != len(pins):
            self.fail("Frame update explicitly positions every existing pin", loc)
        for j, (pin, row) in enumerate(zip(pins, rows, strict=True)):
            replacement = self._pin(
                {"name": pin.value(1), "shape": pin.atom(2), **row}, at, size, index, j, loc
            )
            pin.replace(pin.find("at"), copy(replacement.find("at")))
            # SetSide in KiCad derives text direction from the frame edge.
            # Retaining the old justification after switching edges would put
            # text outside the frame. Preserve font/other effects and identity.
            effects = pin.find("effects")
            if effects is None:
                effects = List.new("effects")
                pin.append(effects)
            other_justification = [
                copy(item) if isinstance(item, List) else item
                for old in effects.find_all("justify")
                for item in old.items[1:]
                if not isinstance(item, str) or item not in {"left", "right"}
            ]
            for old in effects.find_all("justify"):
                effects.remove(old)
            effects.append(
                List.new("justify", symbol(PIN_JUSTIFY[row["edge"]]), *other_justification)
            )
        node.replace(node.find("at"), List.new("at", *map(_mm, at)))
        node.replace(node.find("size"), List.new("size", *map(_mm, size)))
        c.used_targets.add(obj.object_id)
        c.touched.append(obj)
        c.changed_files.add(sch.path.resolve())
        c.action_targets[index] = {
            "kind": "sheet",
            "source_relative": obj.source_relative,
            "uuids": [obj.record["uuid"]],
        }
        c.report.append(
            {
                "index": index,
                "op": "update_sheet",
                **c.action_targets[index],
                "at_mm": action["at_mm"],
                "size_mm": action["size_mm"],
                "pin_positions": rows,
            }
        )
        self.refresh()

    def add_pin(self, action, index):
        from .sch_construct import _known, _pair

        c, loc = self.c, f"/changes/0/actions/{index}"
        _known(action, {"op", "id", "target", "pin", "contact_targets"}, loc)
        obj = c.catalog.resolve(action.get("target"))
        if obj.kind != "sheet" or obj.object_id in c.used_targets:
            self.fail("Select one unmodified existing sheet source", loc)
        sch = c.documents[obj.schematic.path.resolve()]
        node = c.work_nodes[obj.object_id]
        pins = node.find_all("pin")
        if len(pins) >= 128:
            self.fail("A sheet has at most128 scalar pins", loc)
        at, size = (node.find(k) for k in ("at", "size"))
        pin = self._pin(
            action.get("pin"),
            _pair([at.atom(1), at.atom(2)], loc),
            _pair([size.atom(1), size.atom(2)], loc),
            index,
            len(pins),
            loc + "/pin",
        )
        if any(
            p.value(1) == pin.value(1) or p.find("at").values()[:2] == pin.find("at").values()[:2]
            for p in pins
        ):
            self.fail("Sheet pin name and anchor must be distinct", loc)
        node.append(pin)
        c.used_targets.add(obj.object_id)
        c.touched.append(obj)
        c.changed_files.add(sch.path.resolve())
        target = c._alias(action, sch, "sheet", [node], index)
        c.report.append(
            {
                "index": index,
                "op": "add_sheet_pin",
                **target,
                "sheet_pin_ordinal": len(pins),
                "pin": deepcopy(action["pin"]),
            }
        )
        self.refresh()

    def validate(self):
        from collections import Counter

        self.refresh()
        c = self.c
        references = {}
        old_references = {}
        for instance in c.before.instances:
            for placed in instance.schematic.symbols:
                occurrence = placed.instance(instance.path)
                ref = occurrence.reference if occurrence else placed.properties.get("Reference", "")
                unit = occurrence.unit if occurrence else placed.unit
                signature = (placed.lib_id, placed.properties.get("Value", ""))
                old_references.setdefault(ref, []).append((unit, signature))
        for instance in c.working.instances:
            for placed in instance.schematic.symbols:
                occurrence = placed.instance(instance.path)
                if instance.path in self.metadata["new_instances"] and occurrence is None:
                    self.fail(
                        "New occurrence is missing its explicit symbol annotation",
                        instance=instance.path,
                        uuid=placed.uuid,
                    )
                ref = occurrence.reference if occurrence else placed.properties.get("Reference", "")
                unit = occurrence.unit if occurrence else placed.unit
                signature = (placed.lib_id, placed.properties.get("Value", ""))
                current = references.setdefault(ref, [])
                current.append((unit, signature))
                baseline = old_references.get(ref, [])
                if Counter(u for u, _ in current)[unit] > max(
                    1, Counter(u for u, _ in baseline)[unit]
                ) or (len({s for _, s in current}) > 1 and not set(current) <= set(baseline)):
                    self.fail(
                        "Duplicate physical reference/unit or inconsistent multi-unit component",
                        reference=ref,
                        unit=unit,
                    )
        # Existing unrelated malformed interfaces remain observable, but every
        # newly introduced or edited sheet source must have a complete pairing.
        targets = set()
        for t in c.action_targets.values():
            for row in t.get("objects", [t]):
                if row.get("kind") == "sheet":
                    targets.update((row["source_relative"], u) for u in row["uuids"])
        changed_label_sources = self.interface_sources
        for path in self.metadata["new_instances"]:
            instance = c.instances[path]
            if instance.sheet is not None and instance.parent is not None:
                targets.add((c._relative(instance.parent.schematic), instance.sheet.uuid))
        for sch in c.documents.values():
            for sheet in sch.sheets:
                child_path = (sch.path.parent / sheet.file).resolve()
                if (
                    c._relative(sch),
                    sheet.uuid,
                ) not in targets and child_path not in changed_label_sources:
                    continue
                child = c.documents[child_path]
                labels = child.root.find_all("hierarchical_label")
                actual = {(n.value(1), n.find("shape").atom(1)) for n in labels}
                declared = {(p.name, p.electrical) for p in sheet.pins}
                if (
                    actual != declared
                    or len({p.name for p in sheet.pins}) != len(sheet.pins)
                    or len({n.value(1) for n in labels}) != len(labels)
                ):
                    self.fail(
                        "Sheet pins and child hierarchical labels must pair by exact name and type",
                        sheet_uuid=sheet.uuid,
                        declared=sorted(declared),
                        actual=sorted(actual),
                    )

    def normalize(self, request):
        result = deepcopy(request)

        def walk(value):
            if isinstance(value, list):
                return [walk(v) for v in value]
            if not isinstance(value, dict):
                return value
            return {
                k: self.instance(v)
                if k in {"page", "instance_path", "instance"} and isinstance(v, (str, dict))
                else walk(v)
                for k, v in value.items()
            }

        result = walk(result)
        result["affected_instances"] = [self.instance(v) for v in request["affected_instances"]]
        return result
