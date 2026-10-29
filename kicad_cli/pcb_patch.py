"""Identity-scoped explicit routing patches and read-only centreline diagnostics."""

from __future__ import annotations

import heapq
import json
import math
import uuid
from collections import defaultdict
from pathlib import Path

from .fileformat.board import Board
from .fileformat.sexpr import Document, copy, quote, render
from .module_project import compare_native, native_check, sha


def ident(node):
    value = node.find("uuid")
    return value.value(1) if value is not None else None


def segment_key(t):
    return t.net, t.layer, t.width, *sorted((t.start, t.end))


def locked(node):
    marker = node.find("locked")
    return (marker is not None and marker.value(1) != "no") or "locked" in node.values()


def copper_bounds(start, end, width):
    half = (width + 1) // 2
    return [
        min(start[0], end[0]) - half,
        min(start[1], end[1]) - half,
        max(start[0], end[0]) + half,
        max(start[1], end[1]) + half,
    ]


def inside(bounds, allowed):
    return (
        bounds[0] >= allowed[0]
        and bounds[1] >= allowed[1]
        and bounds[2] <= allowed[2]
        and bounds[3] <= allowed[3]
    )


def on_segment(p, a, b):
    return (
        (p[0] - a[0]) * (b[1] - a[1]) == (p[1] - a[1]) * (b[0] - a[0])
        and min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
        and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])
    )


def graph(board, net):
    tracks = [t for t in board.tracks if t.net == net and t.kind == "segment"]
    vertices = defaultdict(set)
    for t in tracks:
        vertices[t.layer].update((t.start, t.end))
    for v in board.vias:
        if v.net == net:
            for layer in v.layers:
                vertices[layer].add(v.position)
    for fp in board.footprints:
        for pad in fp.pads:
            if pad.net == net:
                for layer in pad.layers:
                    vertices[layer].add(pad.position)
    edges = defaultdict(dict)
    for t in tracks:
        points = sorted(
            (p for p in vertices[t.layer] if on_segment(p, t.start, t.end)),
            key=lambda p: math.dist(p, t.start),
        )
        for a, b in zip(points, points[1:], strict=False):
            u, v = (*a, t.layer), (*b, t.layer)
            edges[u][v] = edges[v][u] = math.dist(a, b) / 1e6
    for v in board.vias:
        if v.net == net:
            points = [(*v.position, layer) for layer in v.layers]
            for a, b in zip(points, points[1:], strict=False):
                edges[a][b] = edges[b][a] = 0
    return edges


def distance(edges, a, b):
    queue, costs = [(0, a)], {a: 0}
    while queue:
        cost, here = heapq.heappop(queue)
        if cost != costs[here]:
            continue
        if here == b:
            return cost
        for there, weight in edges[here].items():
            candidate = cost + weight
            if candidate < costs.get(there, math.inf):
                costs[there] = candidate
                heapq.heappush(queue, (candidate, there))
    return None


def partial_overlaps(board, net=None):
    lines = defaultdict(list)
    for t in board.tracks:
        if t.kind != "segment" or (net and t.net != net) or t.start == t.end:
            continue
        dx, dy = t.end[0] - t.start[0], t.end[1] - t.start[1]
        g = math.gcd(dx, dy)
        dx, dy = dx // g, dy // g
        if dx < 0 or (dx == 0 and dy < 0):
            dx, dy = -dx, -dy
        key = (t.net, t.layer, dx, dy, dx * t.start[1] - dy * t.start[0])
        axis = 0 if dx else 1
        lines[key].append((min(t.start[axis], t.end[axis]), max(t.start[axis], t.end[axis]), t))
    result = []
    examined = 0
    for key, rows in sorted(lines.items()):
        rows.sort(key=lambda r: (r[0], r[1], ident(r[2].node)))
        active = []
        for lo, hi, t in rows:
            active = [r for r in active if r[1] > lo]
            for start, end, other in active:
                examined += 1
                if examined > 100000:
                    return result, {
                        "truncated": True,
                        "examined_pairs": examined,
                        "pair_budget": 100000,
                    }
                if segment_key(t) == segment_key(other):
                    continue
                result.append(
                    {
                        "net": key[0],
                        "layer": key[1],
                        "ids": [ident(other.node), ident(t.node)],
                        "classification": "coincident_centerline_different_width"
                        if lo == start and hi == end
                        else "partial_collinear_overlap",
                        "overlap_axis_nm": min(hi, end) - max(lo, start),
                        "widths_nm": [other.width, t.width],
                        "purpose": "unreviewed; not eligible for exact-duplicate removal",
                    }
                )
            active.append((lo, hi, t))
    return result, {"truncated": False, "examined_pairs": examined, "pair_budget": 100000}


def inspect(board_path, net=None):
    board = Board.load(board_path)
    groups = defaultdict(list)
    for t in board.tracks:
        if t.kind == "segment":
            groups[segment_key(t)].append(ident(t.node))
    duplicates = [
        {
            "net": k[0],
            "layer": k[1],
            "width_nm": k[2],
            "endpoints_nm": k[3:],
            "ids": v,
            "extra": len(v) - 1,
        }
        for k, v in sorted(groups.items())
        if len(v) > 1
    ]
    names = [net] if net else sorted({t.net for t in board.tracks})
    nets = []
    for name in names:
        selected = [t for t in board.tracks if t.net == name]
        edges = graph(board, name)
        pads = [
            {
                "uuid": ident(p.node),
                "ref": fp.reference,
                "pin": p.number,
                "at_nm": p.position,
                "layers": p.layers,
            }
            for fp in board.footprints
            for p in fp.pads
            if p.net == name
        ]
        paths = []
        if len(pads) == 2:
            a, b = pads
            candidates = [
                distance(edges, (*a["at_nm"], la), (*b["at_nm"], lb))
                for la in a["layers"]
                for lb in b["layers"]
            ]
            valid = [v for v in candidates if v is not None]
            paths = [
                {
                    "from": a["uuid"],
                    "to": b["uuid"],
                    "straight_mm": math.dist(a["at_nm"], b["at_nm"]) / 1e6,
                    "centerline_path_mm": min(valid) if valid else None,
                }
            ]
        pad_points = {(*p["at_nm"], layer) for p in pads for layer in p["layers"]}
        nets.append(
            {
                "net": name,
                "pads": pads,
                "raw_branch_sum_mm": sum(t.length() for t in selected) / 1e6,
                "unique_branch_sum_mm": sum(
                    math.dist(k[3], k[4]) / 1e6 for k in groups if k[0] == name
                ),
                "two_pad_path": paths,
                "centerline_open_ends": [
                    v
                    for v, neighbors in edges.items()
                    if len(neighbors) == 1 and v not in pad_points
                ],
                "vias": [
                    {"uuid": ident(v.node), "at_nm": v.position, "layers": v.layers}
                    for v in board.vias
                    if v.net == name
                ],
                "segments": [
                    {
                        "uuid": ident(t.node),
                        "start_nm": t.start,
                        "end_nm": t.end,
                        "layer": t.layer,
                        "width_nm": t.width,
                        "locked": locked(t.node),
                    }
                    for t in selected
                ],
            }
        )
    overlap, overlap_coverage = partial_overlaps(board, net)
    return {
        "duplicate_groups": duplicates,
        "duplicate_group_count": len(duplicates),
        "extra_objects": sum(r["extra"] for r in duplicates),
        "partial_overlaps": overlap,
        "partial_overlap_coverage": overlap_coverage,
        "classification_boundary": "Exact duplicates require same net/layer/width/endpoints. "
        "Endpoint-only contact or normal branching is not positive-length overlap. "
        "Pad refs expose test contacts; intentional purpose needs independent intent review.",
        "nets": nets,
        "copper_layers": board.copper_layers,
        "zones": [
            {"net": z.net, "layers": z.layers, "name": z.name, "rule_area": z.rule_area}
            for z in board.zones
        ],
        "limits": [
            "Exact segment duplicates; arcs are excluded from duplicate groups",
            "Centreline graph uses exact endpoints, collinear splits, vias and pad centres;"
            " arbitrary crossings/pad area/zone contact and arcs need native connectivity",
            "Open-end candidates are not native dangling findings; no SI acceptance",
            "No global length threshold; compare classified nets with design requirements",
        ],
    }


def anchor(board, spec, net):
    obj = spec["object_uuid"]
    found = []
    for fp in board.footprints:
        for p in fp.pads:
            if ident(p.node) == obj and spec["terminal"] == "center" and p.net == net:
                found.append((p.position, p.layers))
    for v in board.vias:
        if ident(v.node) == obj and spec["terminal"] == "center" and v.net == net:
            found.append((v.position, v.layers))
    for t in board.tracks:
        if ident(t.node) == obj and spec["terminal"] in {"start", "end"} and t.net == net:
            found.append((getattr(t, spec["terminal"]), [t.layer]))
    if len(found) != 1 or tuple(spec["at_nm"]) != found[0][0] or spec["layer"] not in found[0][1]:
        raise RouteError(
            "invalid_endpoint", "Endpoint identity/net/layer/coordinate differs", endpoint=spec
        )
    return (*found[0][0], spec["layer"])


class RouteError(ValueError):
    def __init__(self, category, message, **evidence):
        super().__init__(message)
        self.details = {
            "route_failure": {"category": category, **evidence},
            "search": {"performed": False, "reason": "explicit caller polyline"},
        }


def prepare(path, spec):
    from jsonschema import Draft202012Validator

    from .module_contract import PATCH_SPEC

    errors = list(Draft202012Validator(PATCH_SPEC).iter_errors(spec))
    if errors:
        raise RouteError("invalid_request", errors[0].json_path + ": " + errors[0].message)
    before_bytes = Path(path).read_bytes()
    board = Board(Document.parse(before_bytes.decode()), Path(path))
    if board.version < 20250000:
        raise RouteError(
            "unsupported_format", "Explicit patch currently requires KiCad10 named nets"
        )
    net = spec["net"]
    a, b = [anchor(board, s, net) for s in spec["endpoints"]]
    ids = spec["remove_ids"]
    if len(ids) != len(set(ids)):
        raise RouteError("invalid_request", "Repeated removal identity")
    if len(set(spec["allow_locked_ids"])) != len(spec["allow_locked_ids"]) or not set(
        spec["allow_locked_ids"]
    ) <= set(ids):
        raise RouteError(
            "invalid_request", "Locked-object permission must name unique selected removals"
        )
    objects = {ident(t.node): t for t in [*board.tracks, *board.vias]}
    for identity in ids:
        if identity not in objects or objects[identity].net != net:
            raise RouteError(
                "scope_violation", "Removal must identify target-net copper", object_uuid=identity
            )
        node = objects[identity].node
        item = objects[identity]
        if item.kind == "arc":
            raise RouteError(
                "unsupported_geometry",
                "Arc removal is unsupported; no endpoint-only scope check",
                object_uuid=identity,
                geometry="arc",
            )
        if item.kind != "segment":
            raise RouteError(
                "scope_violation",
                "Existing vias are protected by this patch capability",
                object_uuid=identity,
            )
        box = copper_bounds(item.start, item.end, item.width)
        if item.layer not in spec["allowed_layers"] or not inside(box, spec["bounds_nm"]):
            raise RouteError(
                "scope_violation",
                "Removal copper envelope escapes allowed layer/rectangle",
                object_uuid=identity,
                copper_bounds_nm=box,
                allowed_bounds_nm=spec["bounds_nm"],
            )
        if locked(node) and identity not in spec["allow_locked_ids"]:
            raise RouteError("scope_violation", "Locked copper is protected", object_uuid=identity)
    old_native = native_check(path)
    original_protected = [render(n) for n in board.root.lists() if ident(n) not in set(ids)]
    for identity in ids:
        board.root.remove(objects[identity].node)
    seen = {
        segment_key(t)
        for t in board.tracks
        if ident(t.node) not in set(ids) and t.kind == "segment"
    }
    additions = []
    for index, s in enumerate(spec["segments"]):
        layer, start, end, width = (
            s["layer"],
            tuple(s["start_nm"]),
            tuple(s["end_nm"]),
            s["width_nm"],
        )
        if layer not in spec["allowed_layers"] or layer not in board.copper_layers:
            raise RouteError(
                "scope_violation", "Segment uses an unauthorized layer", segment=index, layer=layer
            )
        if start == end:
            raise RouteError("invalid_endpoint", "Zero-length segment", segment=index)
        box = copper_bounds(start, end, width)
        if not inside(box, spec["bounds_nm"]):
            raise RouteError(
                "scope_violation",
                "Segment copper envelope escapes allowed rectangle",
                segment=index,
                copper_bounds_nm=box,
                allowed_bounds_nm=spec["bounds_nm"],
            )
        key = (net, layer, width, *sorted((start, end)))
        if key in seen:
            raise RouteError(
                "duplicate_object",
                "Requested segment duplicates retained/new copper",
                segment=index,
            )
        seen.add(key)
        identity = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                sha(before_bytes) + json.dumps(spec, sort_keys=True) + str(index),
            )
        )
        lock_marker = "(locked yes)" if any(locked(objects[i].node) for i in ids) else ""
        node = copy(
            Document.parse(
                f"(segment {lock_marker} (start {start[0] / 1e6:.6f} {start[1] / 1e6:.6f})"
                f"(end {end[0] / 1e6:.6f} {end[1] / 1e6:.6f}) (width {width / 1e6:.6f})"
                f"(layer {quote(layer)}) (net {quote(net)}) (uuid {quote(identity)}))"
            ).root
        )
        board.root.append(node)
        additions.append(identity)
    Path(path).write_bytes(board.document.dumps().encode())
    reopened = Board.load(path)
    protected = [render(n) for n in reopened.root.lists() if ident(n) not in set(additions)]
    if original_protected != protected:
        raise RouteError("internal_exception", "Non-target saved data changed")
    before_graph = graph(Board(Document.parse(before_bytes.decode())), net)
    after_graph = graph(reopened, net)
    old_length, new_length = distance(before_graph, a, b), distance(after_graph, a, b)
    if new_length is None or (
        old_length is not None and old_length - new_length < spec["minimum_improvement_mm"]
    ):
        raise RouteError(
            "invalid_endpoint",
            "Declared endpoints are not joined/improved by the explicit patch",
            before_mm=old_length,
            after_mm=new_length,
        )
    new_native = native_check(path)
    comparison = compare_native(old_native, new_native)
    if comparison["status"] != "passed":
        types = {r["type"] for r in comparison["introduced"]}
        category = (
            "rule_constraint"
            if types & {"track_width", "via_diameter", "drill_out_of_range"}
            else "geometry_blocked"
        )
        raise RouteError(
            category,
            "Native candidate introduces errors or unconnected findings",
            findings=comparison["introduced"],
        )
    return {
        "net": net,
        "removed": ids,
        "added": additions,
        "endpoints": spec["endpoints"],
        "path_before_mm": old_length,
        "path_after_mm": new_length,
        "protected_sha256": sha(json.dumps(protected).encode()),
        "native_before": old_native,
        "native_after": new_native,
        "search": {"performed": False, "reason": "explicit caller polyline"},
        "vias_added": 0,
        "non_target_objects_preserved": True,
    }, comparison
