"""The explicitly bounded root + two distinct direct children drawing path.

This is the public create generator, not an acceptance fixture writer. Document
identity and sheet-instance identity are deliberately separate. Full net names
and physical pin sets are checked before the first output document is written.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from .. import envelope
from ..fileformat import netlist
from ..fileformat.circuit import Design
from ..fileformat.sexpr import Document, List, quote, symbol
from ..sch_hierarchy_contract import hierarchy_plan
from . import sch_create as base


class _HierarchyIds:
    def __init__(self, namespace: str) -> None:
        self.namespace = uuid.UUID(namespace)

    def __call__(self, *parts: object) -> str:
        return str(uuid.uuid5(self.namespace, "draw/" + "/".join(str(p) for p in parts)))


def _page_writer(libs, ids, project, page, index):
    writer = base._Writer(libs, ids, project)
    writer.root = page.get("document_uuid", page.get("uuid"))
    writer.instance_root = page["instance_path"].lstrip("/")
    # Helper references are unique across the complete hierarchy too.
    writer.power_count = index * 1000
    writer.flag_count = index * 100
    return writer


def _wire(writer, points, key):
    for index, (a, b) in enumerate(zip(points, points[1:], strict=False)):
        if a != b:
            writer.wire(a, b, (*key, index))


def _junction(writer, point, key):
    writer.items.append(
        List.new(
            "junction",
            List.new("at", *base._xy(point)),
            List.new("diameter", "0"),
            List.new("color", "0", "0", "0", "0"),
            List.new("uuid", quote(writer.ids("junction", *key))),
        )
    )


def _label(writer, text, point, key):
    writer.items.append(
        List.new(
            "label",
            quote(text),
            List.new("at", *base._xy(point), "0"),
            List.new("fields_autoplaced", symbol("yes")),
            *base._effects("left bottom"),
            List.new("uuid", quote(writer.ids("port-local", *key))),
        )
    )


def _child_ports(writer, child):
    """True hierarchical labels, wired to that page's local naming carriers."""
    for index, port in enumerate(child["ports"]):
        # Native KiCad renders an angle-0 hierarchical label's glyphs to the
        # right of its anchor. Its electrical stub must approach from the left,
        # rather than run through that text side. The local label stays above
        # the left end using the existing left/bottom typography.
        at = (30 * base.GRID, (20 + index * 12) * base.GRID)
        end = (20 * base.GRID, at[1])
        writer.items.append(
            List.new(
                "hierarchical_label",
                quote(port["name"]),
                List.new("shape", symbol(port["direction"])),
                List.new("at", *base._xy(at), "0"),
                List.new("fields_autoplaced", symbol("yes")),
                *base._effects("left"),
                List.new("uuid", quote(port["label_uuid"])),
            )
        )
        _wire(writer, [at, end], ("port", child["id"], port["name"]))
        _label(writer, port["name"], end, (child["id"], port["name"]))


def _flag_drawings(writer, groups):
    for group in groups:
        for kind, net, at in group.extra:
            if kind != "flag":
                continue
            out = (0, 1) if base._is_ground(net) else (0, -1)
            back = (0, -out[1])
            writer.power(net, at, out)
            flag_at = (at[0], at[1] + back[1] * 2 * base.GRID)
            writer.wire(at, flag_at, ("flag", net))
            writer.flag(net, flag_at, back)


def _pack_child(units, parts, on, power, child):
    groups = base._groups(units, parts, on, power)
    ordered = []
    for group in groups:
        signals = {
            s.net for unit in group.units for s in unit.spots if s.net not in power and s.net
        }
        ports = [p for p in child["ports"] if p["name"] in signals]
        if len(ports) != 1 or ports[0]["direction"] == "bidirectional":
            ordered.append((2, group))
            continue
        port = ports[0]
        neighbours = {net: set() for net in signals}
        for part in parts:
            nets = {net for (ref, _), net in on.items() if ref == part.ref and net in signals}
            for net in nets:
                neighbours[net].update(nets - {net})
        distance, todo = {port["name"]: 0}, [port["name"]]
        while todo:
            net = todo.pop(0)
            for other in sorted(neighbours[net] - distance.keys()):
                distance[other] = distance[net] + 1
                todo.append(other)

        def order(unit, *, distance=distance, direction=port["direction"]):
            nets = {s.net for s in unit.spots if s.net in distance}
            levels = [distance[n] for n in nets] or [0]
            if direction == "output":
                # Child input connector -> series RC -> filtered port.
                return (-max(levels), len(nets), base._natural(unit.part.ref), unit.unit)
            return (min(levels), -len(nets), base._natural(unit.part.ref), unit.unit)

        arranged = base._lay_row(sorted(group.units, key=order))
        arranged.layout = "direct_child_port_signal_flow"
        arranged.layout_reason = "Signal graph distance to the declared hierarchical port"
        ordered.append((0 if port["direction"] == "output" else 1, arranged))
    groups = [group for _, group in sorted(ordered, key=lambda item: item[0])]
    paper = base.PAPERS[-1][0]
    for name, width, height in base.PAPERS:
        right = max((group.box[2] - group.box[0] for group in groups), default=0)
        bottom = sum(group.box[3] - group.box[1] + base.GROUP_GAP for group in groups)
        # The first 55 mm is a separate, visible hierarchical-port area.
        if right <= (width - 30) * base.NM and bottom <= (height - 100) * base.NM:
            paper = name
            break
    y = base.snap(65 * base.NM)
    for group in groups:
        # Text/body bounds can end between electrical grid points. Translate
        # the complete group by whole grid steps, rather than making its
        # conservative text box edge an electrical symbol origin.
        left = base.snap(20 * base.NM)
        dx, dy = base.snap(left - group.box[0]), base.snap(y - group.box[1])
        if group.box[0] + dx < left:
            dx += base.GRID
        if group.box[1] + dy < y:
            dy += base.GRID
        group.place(dx, dy)
        y = group.box[3] + base.GROUP_GAP
    return groups, paper


def _root_layout(units, children):
    """Separate two signal regions above one explicit supply region.

    The finite placement is automatic, not user-coordinate support. Cross-page
    nets are wired rather than relabelled on the root, so the child's true
    hierarchical port continues to determine the full signal net name.
    """
    allocated = set()
    regions = []
    bottom = 0
    for index, child in enumerate(children):
        nets = {port["net"] for port in child["ports"]}
        selected = [u for u in units if any(s.net in nets for s in u.spots)]
        if any(id(u) in allocated for u in selected):
            envelope.fail(
                "E_VALIDATION",
                "one root unit cannot span both child signal regions",
                {"child": child["id"], "limit": "direct-two-child/v1 root signal layout"},
            )
        origin_y = (40 + index * 65) * base.NM
        for offset, unit in enumerate(selected):
            unit.origin = (base.snap((155 + offset * 40) * base.NM), base.snap(origin_y))
            allocated.add(id(unit))
            bottom = max(bottom, base._placed_box(unit)[3])
        regions.append((child, selected, base.snap(origin_y)))
    extra = [u for u in units if id(u) not in allocated]
    supply_y = base.snap(max(bottom + 30 * base.NM, 150 * base.NM))
    x = base.snap(35 * base.NM)
    for unit in extra:
        base._at(unit, x, supply_y)
        x = base._placed_box(unit)[2] + 15 * base.GRID
    return regions, supply_y, x


def _root_sheets(writer, regions):
    for page_index, (child, units, origin_y) in enumerate(regions, start=2):
        left, right = 30 * base.GRID, 85 * base.GRID
        top = origin_y - 15 * base.GRID
        pins = []
        max_y = top + 30 * base.GRID
        for index, port in enumerate(child["ports"]):
            ends = []
            for unit in units:
                for spot in unit.spots:
                    if spot.net == port["net"]:
                        x, y = unit.origin
                        end = (
                            x + spot.at[0] + spot.out[0] * base.STUB,
                            y + spot.at[1] + spot.out[1] * base.STUB,
                        )
                        ends.append((end, spot.out, unit))
            if not ends:
                envelope.fail("E_INTEGRITY", "a declared sheet port has no root pin", port)
            if len(ends) == 1 and ends[0][1] == (-1, 0):
                at = (right, ends[0][0][1])
                _wire(writer, [at, ends[0][0]], ("sheet", child["id"], index))
            else:
                # Output plus feedback: a visible lower return path, split at
                # actual junctions. Merely crossing unrelated wires is not a
                # connection, and no hidden root label is used.
                bus_y = base.snap(
                    max(base._placed_box(u)[3] for u in units) + (8 + index * 6) * base.GRID
                )
                at = (right, bus_y)
                branches = []
                for offset, (end, out, _) in enumerate(ends):
                    bx = end[0] + out[0] * (8 + offset * 3) * base.GRID
                    branch = (bx, bus_y)
                    _wire(
                        writer,
                        [end, (bx, end[1]), branch],
                        ("sheet-return", child["id"], index, offset),
                    )
                    branches.append(branch)
                line = sorted({at, *branches})
                _wire(writer, line, ("sheet-bus", child["id"], index))
                for branch in branches:
                    _junction(writer, branch, (child["id"], index, *branch))
            max_y = max(max_y, at[1] + 5 * base.GRID)
            pins.append(
                List.new(
                    "pin",
                    quote(port["name"]),
                    symbol(port["direction"]),
                    List.new("at", *base._xy(at), "0"),
                    *base._effects("right"),
                    List.new("uuid", quote(port["uuid"])),
                )
            )
        writer.items.append(
            List.new(
                "sheet",
                List.new("at", *base._xy((left, top))),
                List.new("size", *base._xy((right - left, max_y - top))),
                List.new("exclude_from_sim", symbol("no")),
                List.new("in_bom", symbol("yes")),
                List.new("on_board", symbol("yes")),
                List.new("dnp", symbol("no")),
                List.new("fields_autoplaced", symbol("yes")),
                List.new("stroke", List.new("width", "0.1524"), List.new("type", "solid")),
                List.new("fill", List.new("color", "0", "0", "0", "0")),
                List.new("uuid", quote(child["sheet_uuid"])),
                base._property("Sheetname", child["name"], (left, top - 2 * base.GRID), "left"),
                base._property("Sheetfile", child["file"], (left, max_y + 2 * base.GRID), "left"),
                *pins,
                List.new(
                    "instances",
                    List.new(
                        "project",
                        quote(writer.project),
                        List.new(
                            "path",
                            quote("/" + writer.root),
                            List.new("page", quote(str(page_index))),
                        ),
                    ),
                ),
            )
        )


def _check_hierarchy(on, built, canonical):
    """Exact full paths, including child-local isolation; no slash stripping."""
    expected = {}
    for pin, net in on.items():
        expected.setdefault(canonical[net], set()).add(pin)
    observed = {n.name: {(p.ref, p.pin) for p in n.nodes} for n in built.nets}
    problems = []
    for name in sorted(set(expected) | set(observed)):
        wanted, actual = expected.get(name, set()), observed.get(name, set())
        if wanted != actual:
            problems.append(
                {
                    "net": name,
                    "missing": sorted(f"{r}.{p}" for r, p in wanted - actual),
                    "extra": sorted(f"{r}.{p}" for r, p in actual - wanted),
                }
            )
    return problems


def create_hierarchy(spec, spec_path, out_dir):
    from .sch_create_feedback import inspect_created  # noqa: PLC0415

    stem = Path(spec_path).stem
    layout = hierarchy_plan(spec, stem)
    if layout is None:
        raise ValueError("hierarchy generator requires a hierarchy request")
    parts, on, joins, _ = base.plan(spec)
    power = set(layout["shared_power_nets"])
    libs = base._libraries()
    ids = _HierarchyIds(spec["hierarchy"]["namespace_uuid"])
    destination = Path(out_dir)
    pages = [layout["root"], *layout["children"]]
    paths = [destination / page["file"] for page in pages]
    net_path = destination / f"{stem}.net"
    conflicts = [str(p) for p in [*paths, net_path] if p.exists()]
    if conflicts:
        envelope.fail(
            "E_CONFLICT", "hierarchical create requires absent output files", {"files": conflicts}
        )
    texts, group_reports = {}, []
    for page_index, (page, path) in enumerate(zip(pages, paths, strict=True)):
        page_parts = [p for p in parts if layout["part_sheets"][p.ref] == page["id"]]
        local = {} if page_index == 0 else page["local_nets"]
        page_on = {
            key: (net if net in power or page_index == 0 else local[net])
            for key, net in on.items()
            if key[0] in {p.ref for p in page_parts}
        }
        units = [u for p in page_parts for u in base._units(p, page_on)]
        for unit in units:
            base._plan(unit, power)
        writer = _page_writer(libs, ids, stem, page, page_index)
        if page_index == 0:
            cross = {p["net"] for c in layout["children"] for p in c["ports"]}
            for unit in units:
                unit.drawn = [
                    d for d in unit.drawn if not isinstance(d, base.Label) or d.text not in cross
                ]
            regions, supply_y, supply_x = _root_layout(units, layout["children"])
            groups = []
            flags = base._flags_group(base._undriven(parts, on, power))
            if flags is not None:
                flags.place(base.snap(max(supply_x, 160 * base.NM)), supply_y)
                groups.append(flags)
            _root_sheets(writer, regions)
            paper = "A3" if supply_x > 250 * base.NM else "A4"
        else:
            groups, paper = _pack_child(units, page_parts, page_on, power, page)
            _child_ports(writer, page)
        for unit in sorted(units, key=lambda u: (base._natural(u.part.ref), u.unit)):
            writer.unit(unit)
        _flag_drawings(writer, groups)
        text = writer.document(page["title"], paper)
        if page_index:
            # Page numbering belongs to the sheet instance in its parent,
            # not to a second artificial root inside the child document.
            document = Document.parse(text)
            document.root.remove(document.root.find("sheet_instances"))
            text = document.dumps()
        texts[path] = text
        group_reports.append(
            {
                "sheet_id": page["id"],
                "paper": paper,
                "units": [{"ref": u.part.ref, "unit": u.unit} for u in units],
            }
        )
    snapshot = {p.absolute(): text.encode("utf-8") for p, text in texts.items()}
    snapshot[paths[0].absolute().with_suffix(".kicad_pro")] = None
    built = netlist.build(Design(paths[0].absolute(), snapshot=snapshot))
    mismatches = _check_hierarchy(on, built, layout["canonical_nets"])
    if mismatches:
        envelope.fail(
            "E_INTEGRITY",
            "the hierarchical drawing differs from the frozen full-path pin intent",
            {"mismatches": mismatches[:40], "outputs_written": False},
        )
    try:
        destination.mkdir(parents=True, exist_ok=True)
        for path, text in texts.items():
            path.write_bytes(text.encode("utf-8"))
        net_path.write_bytes(netlist.dumps(built, tool="kicad-cli").encode("utf-8"))
    except OSError as exc:
        envelope.fail(
            "E_IO",
            "hierarchical candidate generation did not finish writing its declared files",
            {
                "files": [str(p) for p in [*paths, net_path]],
                "set_atomic": False,
                "hint": "This disposable generation result must not be promoted after failure.",
            },
        )
        raise AssertionError from exc
    feedback = []
    for page, path in zip(pages, paths, strict=True):
        report = inspect_created(path)
        report["sheet_id"] = page["id"]
        feedback.append(report)
    geometry = {
        "schema": "sch-create-geometry/v2",
        "geometry_check": "partial_conservative_schematic_bounds",
        "status": "requires_visual_review",
        "pages": feedback,
        "findings": [
            dict(item, sheet_id=p["sheet_id"]) for p in feedback for item in p["findings"]
        ],
        "unmeasured_objects": [
            dict(item, sheet_id=p["sheet_id"]) for p in feedback for item in p["unmeasured_objects"]
        ],
        "automatic_grouping": {
            "pages": group_reports,
            "limit": (
                "Bounded root and two direct children; default-font estimates require "
                "every full native page."
            ),
        },
        "_untrusted": ["pages", "automatic_grouping"],
    }
    return {
        "title": layout["root"]["title"],
        "parts": [
            {"ref": p.ref, "symbol": p.lib.lib_id, "value": p.value}
            for p in sorted(parts, key=lambda p: base._natural(p.ref))
        ],
        "nets": joins,
        "unconnected_parts": sorted(
            (p.ref for p in parts if not any(k[0] == p.ref for k in on)), key=base._natural
        ),
        "written": {
            "schematic": str(paths[0]),
            "children": [str(p) for p in paths[1:]],
            "netlist": str(net_path),
        },
        "drawing": {
            "status": "drawn",
            "style": "bounded_hierarchical",
            "reason": None,
            "paper": group_reports[0]["paper"],
            "pages": group_reports,
        },
        "hierarchy": layout,
        "creation_geometry": geometry,
    }
