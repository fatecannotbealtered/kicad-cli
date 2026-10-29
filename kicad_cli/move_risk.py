"""Compare official KiCad findings for an isolated footprint translation trial.

This module does not calculate DRC or infer a net from translated descriptions.
The caller must bind the actual native invocation, reports and saved input bytes.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import defaultdict, deque
from decimal import Decimal

from .fileformat import geometry
from .fileformat.board import NM, nm
from .payload import drc_runner


class RiskIncomplete(ValueError):
    """A complete, identity-bound native finding comparison cannot be established."""


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _identity(node):
    for kind in ("uuid", "tstamp"):
        identities = node.find_all(kind)
        if len(identities) > 1:
            raise RiskIncomplete("A saved object has duplicate identity fields")
        if identities:
            value = identities[0].value(1)
            if not isinstance(value, str) or not value:
                raise RiskIncomplete("A saved object has an empty identity")
            return value
    return None


def _layers(board, node):
    layer, layers = node.find("layer"), node.find("layers")
    return board.expand_layers(
        [layer.value(1)] if layer is not None else layers.values() if layers is not None else []
    )


def _standard_alias_shape(node, *, via):
    """Only the saved shapes whose annular endpoint contact is proved below."""
    if any(isinstance(item, str) for item in node.items[1:]):
        return False
    lengths = (
        {"at": {3}, "size": {2}, "drill": {2}, "layers": {3}, "net": {2, 3}}
        if via
        else {"start": {3}, "end": {3}, "width": {2}, "layer": {2}, "net": {2, 3}}
    )
    allowed = set(lengths) | {"uuid", "tstamp"}
    children = node.lists()
    heads = [child.head for child in children]
    if len(heads) != len(set(heads)) or not set(heads) <= allowed:
        return False
    for name, sizes in lengths.items():
        child = node.find(name)
        if child is None or len(child.items) not in sizes:
            return False
        if any(not isinstance(item, str) for item in child.items):
            return False
    if via and len(set(node.find("layers").values())) != 2:
        return False
    return True


def object_index(board) -> dict[str, dict]:
    """Index real saved UUIDs and machine nets; positions are integer world nm.

    Native finding UUIDs must resolve into this index. Duplicate identities or
    unidentified footprints/pads/copper make this bounded comparison incomplete.
    Extra saved graphics with identities remain identifiable through their kind,
    machine layer, saved geometry and containing footprint, without a collision claim.
    """
    try:
        if board.document.dumps() != board.document.source:
            raise RiskIncomplete(
                "Use a fresh saved-board observation, not a modified in-memory model"
            )
        typed, owners = {}, {}

        def remember(node, metadata):
            identity = _identity(node)
            if identity is None:
                raise RiskIncomplete("A saved " + metadata["kind"] + " has no UUID/tstamp")
            typed[id(node)] = dict(metadata, uuid=identity)
            return identity

        for footprint in board.footprints:
            identity = remember(
                footprint.node,
                {
                    "kind": "footprint",
                    "ref": footprint.reference,
                    "position_nm": list(footprint.position),
                    "rotation_deg": footprint.angle,
                    "layers": [footprint.layer],
                    "locked": footprint.locked,
                    "nets": sorted({pad.net for pad in footprint.pads}),
                    "net": None,
                },
            )
            owners[id(footprint.node)] = (identity, footprint)
            for pad in footprint.pads:
                remember(
                    pad.node,
                    {
                        "kind": "pad",
                        "ref": footprint.reference,
                        "number": pad.number,
                        "footprint_uuid": identity,
                        "position_nm": list(pad.position),
                        "rotation_deg": pad.angle,
                        "layers": list(pad.layers),
                        "net": pad.net,
                    },
                )
        for track in board.tracks:
            remember(
                track.node,
                {
                    "kind": track.kind,
                    "net": track.net,
                    "layers": [track.layer],
                    "start_nm": list(track.start),
                    "end_nm": list(track.end),
                    "mid_nm": list(track.mid) if track.mid is not None else None,
                    "width_nm": track.width,
                    "simple_anchor_alias_geometry": track.kind == "segment"
                    and track.layer in board.copper_layers
                    and _standard_alias_shape(track.node, via=False),
                },
            )
        for via in board.vias:
            remember(
                via.node,
                {
                    "kind": "via",
                    "net": via.net,
                    "layers": list(via.layers),
                    "position_nm": list(via.position),
                    "diameter_nm": via.diameter,
                    "drill_nm": via.drill,
                    "simple_anchor_alias_geometry": via.kind == "through"
                    and {"F.Cu", "B.Cu"}.issubset(board.copper_layers)
                    and via.node.find("layers").values() == ["F.Cu", "B.Cu"]
                    and _standard_alias_shape(via.node, via=True),
                },
            )
        for zone in [*board.zones, *board.footprint_zones]:
            remember(
                zone.node,
                {
                    "kind": "zone",
                    "net": zone.net,
                    "layers": list(zone.layers),
                    "rule_area": zone.rule_area,
                },
            )

        result = {}
        for node in board.root.walk():
            identity = _identity(node)
            if identity is None:
                continue
            if identity in result:
                raise RiskIncomplete("A saved object UUID/tstamp is not unique: " + identity)
            metadata = typed.get(id(node))
            if metadata is None:
                owner_node = node.parent
                while owner_node is not None and id(owner_node) not in owners:
                    owner_node = owner_node.parent
                owner = owners.get(id(owner_node))
                points = {}
                for name in ("at", "start", "end", "mid", "center"):
                    point = node.find(name)
                    if point is not None:
                        x, y = nm(point.atom(1)), nm(point.atom(2))
                        if owner is not None:
                            footprint = owner[1]
                            x, y = geometry.rotate(x, y, footprint.angle)
                            x += footprint.position[0]
                            y += footprint.position[1]
                        points[name] = [round(x), round(y)]
                metadata = {
                    "uuid": identity,
                    "kind": node.head,
                    "net": board.net_of(node) if node.find("net") is not None else None,
                    "layers": _layers(board, node),
                    "geometry_world_nm": points,
                    "ref": owner[1].reference if owner else None,
                    "footprint_uuid": owner[0] if owner else None,
                }
            span = board.document.span(node)
            if span is None:
                raise RiskIncomplete("A saved object has no observable source span")
            metadata = dict(metadata)
            metadata["saved_object_sha256"] = hashlib.sha256(
                board.document.source[slice(*span)].encode("utf-8")
            ).hexdigest()
            result[identity] = metadata
        return result
    except RiskIncomplete:
        raise
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        raise RiskIncomplete("Saved objects cannot be indexed unambiguously: " + str(exc)) from exc


def _validate(report, board):
    if board.path is None:
        raise RiskIncomplete("A saved board path is required to bind the native report source")
    try:
        drc_runner.validate_report(report, board.path.resolve())
        _canonical(report)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RiskIncomplete("The official DRC report is malformed: " + str(exc)) from exc
    if not isinstance(report.get("ignored_checks"), list):
        raise RiskIncomplete("The official report does not declare ignored_checks")
    severities = report.get("included_severities")
    if (
        not isinstance(severities, list)
        or any(not isinstance(value, str) for value in severities)
        or len(severities) != 3
        or set(severities) != {"error", "warning", "exclusion"}
    ):
        raise RiskIncomplete(
            "The official report must include error, warning and exclusion findings"
        )


def _position(position):
    converted = [Decimal(str(position[axis])) * NM for axis in ("x", "y")]
    return [int(value) if value == value.to_integral_value() else str(value) for value in converted]


def _normalise(report, index, group):
    result = []
    for finding in report[group]:
        if not finding["type"] or not finding["items"]:
            raise RiskIncomplete("A native finding lacks a machine type or identifiable objects")
        items = []
        for item in finding["items"]:
            if item["uuid"] not in index:
                raise RiskIncomplete("A native finding references an unknown UUID: " + item["uuid"])
            items.append(
                {
                    "uuid": item["uuid"],
                    "object": copy.deepcopy(index[item["uuid"]]),
                    "reported_position_nm": _position(item["pos"]),
                    "reported_description": item["description"],
                }
            )
        if len({item["uuid"] for item in items}) != len(items):
            raise RiskIncomplete("A native finding repeats an object UUID")
        if group == "unconnected_items" and (
            len(items) != 2 or any(not isinstance(item["object"].get("net"), str) for item in items)
        ):
            raise RiskIncomplete("A reported connection edge needs two machine-net objects")
        result.append(
            {
                "group": group,
                "type": finding["type"],
                "severity": finding["severity"],
                "excluded": finding.get("excluded", False),
                "reported_description": finding["description"],
                "items": items,
            }
        )
    return result


def _key(finding, *, connection):
    identities = []
    for item in finding["items"]:
        if connection:
            # Each report reference point was already checked against its own
            # saved board. A moved pad may legitimately keep the same edge.
            # A different endpoint UUID/net/kind still forms a different edge.
            identity = {
                name: item["object"].get(name)
                for name in ("uuid", "kind", "ref", "number", "footprint_uuid", "net", "layers")
            }
        else:
            identity = {
                "object": item["object"],
                "reported_position_nm": item["reported_position_nm"],
            }
        identities.append(_canonical(identity))
    return _canonical(
        {
            "group": finding["group"],
            "type": finding["type"],
            "severity": finding["severity"],
            "excluded": finding["excluded"],
            "objects": sorted(identities),
        }
    )


def _partition(before, after, *, connection=False):
    available = defaultdict(deque)
    for finding in before:
        available[_key(finding, connection=connection)].append(finding)
    new, retained = [], []
    for finding in after:
        previous = available[_key(finding, connection=connection)]
        if previous:
            retained.append({"before": previous.popleft(), "after": finding})
        else:
            new.append(finding)
    removed = [finding for rows in available.values() for finding in rows]
    return {"new": new, "removed": removed, "retained": retained}


def _integer_point(value):
    return isinstance(value, list) and len(value) == 2 and all(type(v) is int for v in value)


def _unchanged_object(identity, before, after):
    old, new = before.get(identity), after.get(identity)
    return (
        old is not None
        and old == new
        and isinstance(old.get("saved_object_sha256"), str)
        and bool(old["saved_object_sha256"])
    )


def _reported_anchor_matches(item):
    point, obj = item["reported_position_nm"], item["object"]
    if not _integer_point(point):
        return False
    if obj["kind"] in {"pad", "via"}:
        return point == obj["position_nm"]
    if obj["kind"] == "segment":
        # KiCad 10.0.6 serializes PCB_TRACK::GetPosition(), which is m_Start.
        return point == obj["start_nm"]
    return False


def _validate_connection_reference_points(findings, *, version, role):
    """Check every edge item before identity grouping can retain an old pair.

    This uses the verified KiCad 10.0.6 GetPosition reference, not a physical
    contact point. Baseline and candidate must each match their own saved board;
    a translated pad is not required to keep its previous world coordinates.
    """
    if findings and version != "10.0.6":
        raise RiskIncomplete(
            role + " unconnected report reference points are unverified for KiCad " + version
        )
    for finding in findings:
        for item in finding["items"]:
            obj = item["object"]
            if obj["kind"] not in {"segment", "via", "pad"}:
                raise RiskIncomplete(
                    role
                    + " unconnected report reference point has an unsupported object kind: "
                    + obj["kind"]
                    + " ("
                    + item["uuid"]
                    + ")"
                )
            expected = obj["start_nm"] if obj["kind"] == "segment" else obj["position_nm"]
            if not _integer_point(expected) or not _reported_anchor_matches(item):
                raise RiskIncomplete(
                    role
                    + " unconnected report reference point does not match the saved "
                    + obj["kind"]
                    + " GetPosition for KiCad 10.0.6: "
                    + item["uuid"]
                    + " (reported_nm="
                    + _canonical(item["reported_position_nm"])
                    + ", expected_nm="
                    + _canonical(expected)
                    + ")"
                )


def _junction_geometry(via, tracks):
    """A standard through-via directly bridges these endpoint centerlines."""
    center = via["position_nm"]
    if not via["simple_anchor_alias_geometry"] or not _integer_point(center):
        return None
    diameter, drill = via["diameter_nm"], via["drill_nm"]
    if any(type(value) is not int for value in (diameter, drill)) or not 0 < drill < diameter:
        return None
    geometry_proofs = []
    for track in tracks:
        shared = sorted(
            layer
            for layer in set(via["layers"]) & set(track["layers"])
            if re.fullmatch(r"(?:F|B|In[1-9][0-9]*)\.Cu", layer)
        )
        width = track["width_nm"]
        if (
            not track["simple_anchor_alias_geometry"]
            or not shared
            or center not in (track["start_nm"], track["end_nm"])
            or type(width) is not int
            or width <= 0
            or not _integer_point(track["start_nm"])
            or not _integer_point(track["end_nm"])
        ):
            return None
        length_squared = sum(
            (start - end) ** 2
            for start, end in zip(track["start_nm"], track["end_nm"], strict=True)
        )
        if 4 * length_squared <= diameter**2:
            return None
        geometry_proofs.append(
            {
                "uuid": track["uuid"],
                "start_nm": track["start_nm"],
                "end_nm": track["end_nm"],
                "width_nm": width,
                "shared_copper_layers": shared,
                "length_squared_nm2": length_squared,
                "annular_contact_test": "4 * length_squared_nm2 > via_diameter_nm ** 2",
                "annular_contact_test_passed": True,
            }
        )
    return {
        "via_uuid": via["uuid"],
        "anchor_nm": center,
        "junction_position_nm": center,
        "via_layers": via["layers"],
        "via_diameter_nm": diameter,
        "via_drill_nm": drill,
        "segments": geometry_proofs,
    }


def _anchor_alias_proof(old, new, before, after, moved_nets):
    """Prove one direct endpoint alias with one unchanged ordinary through-via."""
    if any(old[name] != new[name] for name in ("group", "type", "severity", "excluded")):
        return None
    if old["type"] != "unconnected_items":
        return None
    old_items = {item["uuid"]: item for item in old["items"]}
    new_items = {item["uuid"]: item for item in new["items"]}
    common = old_items.keys() & new_items.keys()
    if len(common) != 1:
        return None
    common_id = next(iter(common))
    old_id = next(iter(old_items.keys() - common))
    new_id = next(iter(new_items.keys() - common))
    participants = [common_id, old_id, new_id]
    if not all(_unchanged_object(identity, before, after) for identity in participants):
        return None
    common_old, common_new = old_items[common_id], new_items[common_id]
    if (
        common_old["reported_position_nm"] != common_new["reported_position_nm"]
        or not _reported_anchor_matches(common_old)
        or not _reported_anchor_matches(common_new)
        or not _reported_anchor_matches(old_items[old_id])
        or not _reported_anchor_matches(new_items[new_id])
    ):
        return None
    parent_id = before[common_id].get("footprint_uuid")
    if parent_id is not None and (
        not _unchanged_object(parent_id, before, after) or before[parent_id]["kind"] != "footprint"
    ):
        return None
    old_object, new_object = before[old_id], before[new_id]
    net = old_object.get("net")
    if (
        not net
        or new_object.get("net") != net
        or before[common_id].get("net") != net
        or net in moved_nets
    ):
        return None
    kinds = {old_object["kind"], new_object["kind"]}
    tracks = [obj for obj in (old_object, new_object) if obj["kind"] == "segment"]
    if kinds == {"via", "segment"}:
        witnesses = [obj for obj in (old_object, new_object) if obj["kind"] == "via"]
        proof_kind = "unchanged-via-straight-segment-endpoint.v1"
    elif kinds == {"segment"}:
        witnesses = [
            obj
            for identity, obj in before.items()
            if obj["kind"] == "via"
            and obj["net"] == net
            and _unchanged_object(identity, before, after)
        ]
        proof_kind = "unchanged-single-via-junction-anchor.v1"
    else:
        return None
    proofs = []
    for witness in witnesses:
        geometry_proof = _junction_geometry(witness, tracks)
        if geometry_proof is not None:
            proofs.append(geometry_proof)
    if len(proofs) != 1:
        return None
    geometry_proof = proofs[0]
    checked = list(
        dict.fromkeys(
            [*participants, geometry_proof["via_uuid"], *([parent_id] if parent_id else [])]
        )
    )
    return {
        "kind": proof_kind,
        "source": "Bounded saved-geometry comparison; no additional native connectivity run",
        "common_endpoint_uuid": common_id,
        "common_parent_footprint_uuid": parent_id,
        "old_anchor_uuid": old_id,
        "new_anchor_uuid": new_id,
        "old_reported_position_nm": old_items[old_id]["reported_position_nm"],
        "new_reported_position_nm": new_items[new_id]["reported_position_nm"],
        "reported_position_version": "10.0.6",
        "reported_position_basis": "KiCad track start / via center, separate from junction",
        "net": net,
        "moved_pad_nets": sorted(moved_nets),
        "net_disjoint_from_moved_pad_nets": True,
        **geometry_proof,
        "unchanged_objects": [
            {
                "uuid": identity,
                "before_sha256": before[identity]["saved_object_sha256"],
                "after_sha256": after[identity]["saved_object_sha256"],
                "geometry_and_machine_metadata_equal": True,
            }
            for identity in checked
        ],
        "limit": "One direct endpoint alias; no transitive closure or full connectivity claim",
    }


def _classify_anchor_aliases(edges, before, after, moved_nets):
    candidates, old_matches, new_matches = {}, defaultdict(list), defaultdict(list)
    for new_index, new in enumerate(edges["new"]):
        for old_index, old in enumerate(edges["removed"]):
            proof = _anchor_alias_proof(old, new, before, after, moved_nets)
            if proof is not None:
                candidates[new_index, old_index] = proof
                new_matches[new_index].append(old_index)
                old_matches[old_index].append(new_index)
    equivalent, allowed = [], set()
    for (new_index, old_index), proof in candidates.items():
        if len(new_matches[new_index]) == len(old_matches[old_index]) == 1:
            allowed.add(new_index)
            equivalent.append(
                {
                    "before": edges["removed"][old_index],
                    "after": edges["new"][new_index],
                    "proof": proof,
                }
            )
    return equivalent, [row for index, row in enumerate(edges["new"]) if index not in allowed]


def _saved_form(node, *, translated_footprint=False):
    """Saved structure, masking only the footprint origin's two coordinates."""
    result = []
    for item in node.items:
        if isinstance(item, str):
            result.append(item)
        elif translated_footprint and item.head == "at":
            result.append(["at", "<translation-x>", "<translation-y>", *item.items[3:]])
        else:
            result.append(_saved_form(item))
    return result


def _isolated_pad_inventory(board):
    """Prove only isolated, axis-aligned ordinary SMD pad components.

    Rectangles bound roundrect copper conservatively. Strictly separated bounds
    prove non-contact; intersecting bounds are unsupported, not guessed apart.
    Unknown copper-bearing syntax cannot opt into this limited exception.
    """

    def require(condition, reason):
        if not condition:
            raise ValueError(reason)

    def coordinates(node, dimensions=2):
        require(node is not None and len(node.items) == dimensions + 1, "coordinate shape")
        values = [Decimal(node.atom(index)) * NM for index in range(1, dimensions + 1)]
        require(
            all(value.is_finite() and value == value.to_integral_value() for value in values),
            "coordinates must be exact integer nanometres",
        )
        return [int(value) for value in values]

    root_allowed = {
        "version",
        "generator",
        "generator_version",
        "general",
        "paper",
        "layers",
        "setup",
        "footprint",
        "gr_line",
        "gr_arc",
        "gr_circle",
        "gr_rect",
        "gr_poly",
        "gr_curve",
        "gr_text",
        "gr_text_box",
        "dimension",
        "embedded_fonts",
        "title_block",
        "net",
    }
    footprint_allowed = {
        "layer",
        "uuid",
        "tstamp",
        "at",
        "descr",
        "tags",
        "property",
        "path",
        "attr",
        "duplicate_pad_numbers_are_jumpers",
        "embedded_fonts",
        "model",
        "pad",
        "fp_line",
        "fp_arc",
        "fp_circle",
        "fp_rect",
        "fp_poly",
        "fp_curve",
        "fp_text",
        "fp_text_box",
    }
    drawable_heads = {
        "gr_line",
        "gr_arc",
        "gr_circle",
        "gr_rect",
        "gr_poly",
        "gr_curve",
        "gr_text",
        "gr_text_box",
        "dimension",
        "fp_line",
        "fp_arc",
        "fp_circle",
        "fp_rect",
        "fp_poly",
        "fp_curve",
        "fp_text",
        "fp_text_box",
        "property",
    }
    non_copper_layers = {
        "F.Adhes",
        "B.Adhes",
        "F.Paste",
        "B.Paste",
        "F.SilkS",
        "B.SilkS",
        "F.Mask",
        "B.Mask",
        "Dwgs.User",
        "Cmts.User",
        "Eco1.User",
        "Eco2.User",
        "Edge.Cuts",
        "Margin",
        "F.CrtYd",
        "B.CrtYd",
        "F.Fab",
        "B.Fab",
    }
    require(
        not board.tracks and not board.vias and not board.zones and not board.footprint_zones,
        "routed copper or zones exist",
    )
    require(
        board.copper_layers == ["F.Cu", "B.Cu"], "only an ordinary two-layer board is supported"
    )
    require(
        len([item for item in board.root.items if isinstance(item, str)]) == 1, "unknown board atom"
    )
    require(all(node.head in root_allowed for node in board.root.lists()), "unknown board child")
    for name in ("version", "layers", "setup"):
        require(len(board.root.find_all(name)) <= 1, "duplicate board " + name)
    for node in board.root.walk():
        require(node.head not in {"segment", "arc", "via", "zone"}, "routed copper or zones exist")
        if node is board.root or node.head in {"footprint", "pad"}:
            continue
        if node.head in drawable_heads:
            fields = node.find_all("layer")
            require(
                len(fields) == 1
                and not node.find_all("layers")
                and len(fields[0].items) == 2
                and all(isinstance(item, str) for item in fields[0].items)
                and fields[0].value(1) in non_copper_layers,
                "drawable needs one explicit known canonical non-copper layer",
            )
        for layer_field in [*node.find_all("layer"), *node.find_all("layers")]:
            require(
                not any(value.endswith(".Cu") for value in layer_field.values()),
                "copper graphic, text, or unsupported copper-layer child exists",
            )
    footprints, pads, references = {}, {}, set()
    for footprint in board.footprints:
        node = footprint.node
        require(
            footprint.reference and footprint.reference not in references,
            "footprint references must be nonempty and unique",
        )
        references.add(footprint.reference)
        require(footprint.layer == "F.Cu" and footprint.angle == 0, "footprint layer or rotation")
        require(
            len([item for item in node.items if isinstance(item, str)]) == 2,
            "unknown footprint atom",
        )
        require(
            all(child.head in footprint_allowed for child in node.lists()),
            "unknown footprint child",
        )
        for name in ("at", "layer"):
            require(len(node.find_all(name)) == 1, "ambiguous footprint " + name)
        require(
            len(node.find_all("uuid")) + len(node.find_all("tstamp")) == 1,
            "ambiguous footprint identity encoding",
        )
        require(len(node.find("layer").items) == 2, "footprint layer shape")
        saved_identity = node.find("uuid") or node.find("tstamp")
        require(len(saved_identity.items) == 2, "footprint identity shape")
        origin = node.find("at")
        require(len(origin.items) in {3, 4}, "footprint origin shape")
        if len(origin.items) == 4:
            require(Decimal(origin.atom(3)) == 0, "footprint rotation")
        for index in (1, 2):
            value = Decimal(origin.atom(index)) * NM
            require(
                value.is_finite() and value == value.to_integral_value(),
                "footprint origin must be exact integer nanometres",
            )
            require(int(value) == footprint.position[index - 1], "footprint origin mismatch")
        identity = _identity(node)
        footprints[identity] = _saved_form(node, translated_footprint=True)
        numbers = set()
        require(bool(footprint.pads), "footprint has no supported physical pad")
        for pad in footprint.pads:
            node = pad.node
            allowed = {"at", "size", "layers", "net", "uuid", "tstamp"}
            if pad.shape == "roundrect":
                allowed.add("roundrect_rratio")
            heads = [child.head for child in node.lists()]
            require(
                set(heads) <= allowed and len(heads) == len(set(heads)),
                "unknown or duplicate pad child",
            )
            require(
                len([atom for atom in node.items if isinstance(atom, str)]) == 4, "unknown pad atom"
            )
            require(
                len(node.find_all("uuid")) + len(node.find_all("tstamp")) == 1,
                "ambiguous pad identity encoding",
            )
            saved_identity = node.find("uuid") or node.find("tstamp")
            require(len(saved_identity.items) == 2, "pad identity shape")
            require(
                pad.kind == "smd" and pad.shape in {"rect", "roundrect"},
                "unsupported pad type or shape",
            )
            require(
                pad.number and pad.number not in numbers and pad.net,
                "pad number and net must be nonempty; physical numbers must be unique",
            )
            numbers.add(pad.number)
            net = node.find("net")
            require(
                net is not None
                and len(net.items) == 2
                and net.atom(1).startswith('"')
                and net.value(1) == pad.net,
                "unsupported pad net encoding",
            )
            at = node.find("at")
            require(at is not None and len(at.items) in {3, 4}, "pad position shape")
            if len(at.items) == 4:
                require(Decimal(at.atom(3)) == 0, "pad rotation")
            require(pad.angle == 0, "pad rotation")
            local = []
            for index in (1, 2):
                value = Decimal(at.atom(index)) * NM
                require(
                    value.is_finite() and value == value.to_integral_value(),
                    "pad origin must be exact integer nanometres",
                )
                local.append(int(value))
            size = coordinates(node.find("size"))
            require(all(value > 0 for value in size) and size == list(pad.size), "pad size")
            layers = node.find("layers")
            require(
                layers is not None and all(isinstance(item, str) for item in layers.items),
                "pad layers shape",
            )
            raw_layers = layers.values()
            require(
                "F.Cu" in raw_layers
                and set(raw_layers) <= {"F.Cu", "F.Mask", "F.Paste"}
                and len(raw_layers) == len(set(raw_layers))
                and raw_layers == pad.layers,
                "unsupported or wildcard pad layers",
            )
            require(
                pad.drill is None
                and pad.drill_offset == (0, 0)
                and not pad.chamfer_corners
                and pad.chamfer_ratio == 0
                and pad.rect_delta == (0, 0)
                and pad.anchor is None,
                "modified pad geometry",
            )
            if pad.shape == "roundrect":
                ratio = node.find("roundrect_rratio")
                require(ratio is not None and len(ratio.items) == 2, "roundrect ratio missing")
                value = Decimal(ratio.atom(1))
                require(value.is_finite() and 0 <= value <= Decimal("0.5"), "roundrect ratio")
            world = [
                origin + delta for origin, delta in zip(footprint.position, local, strict=True)
            ]
            require(world == list(pad.position), "pad world position mismatch")
            x, y = world
            w, h = size
            pads[_identity(node)] = {
                "footprint_uuid": identity,
                "ref": footprint.reference,
                "number": pad.number,
                "net": pad.net,
                "local_saved_structure": _saved_form(node),
                "world_nm": world,
                "bounding_rectangle_twice_nm": [2 * x - w, 2 * y - h, 2 * x + w, 2 * y + h],
            }
    require(bool(pads), "no supported physical pads")
    rows = list(pads.items())
    for index, (identity, row) in enumerate(rows):
        a = row["bounding_rectangle_twice_nm"]
        for other, candidate in rows[index + 1 :]:
            b = candidate["bounding_rectangle_twice_nm"]
            require(
                a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1],
                "pad copper bounds touch or overlap: " + identity + " / " + other,
            )
    return {
        "pads": pads,
        "footprints": footprints,
        "protected_board_structure": [
            _saved_form(node) for node in board.root.lists() if node.head != "footprint"
        ],
        "board_sha256": hashlib.sha256(board.document.source.encode("utf-8")).hexdigest(),
    }


def _singleton_report_trees(findings, pads, *, role):
    """Validate all physical pads and exact unique connected report trees."""
    by_net, edges = defaultdict(set), defaultdict(list)
    for identity, pad in pads.items():
        by_net[pad["net"]].add(identity)
    parents = {identity: identity for identity in pads}

    def representative(identity):
        while parents[identity] != identity:
            identity = parents[identity]
        return identity

    unique = set()
    for finding in findings:
        if (
            finding["type"] != "unconnected_items"
            or finding["severity"] != "error"
            or finding["excluded"]
            or len(finding["items"]) != 2
        ):
            raise RiskIncomplete(role + " isolated-pad report tree has unsupported finding flags")
        a, b = [item["uuid"] for item in finding["items"]]
        if a not in pads or b not in pads or pads[a]["net"] != pads[b]["net"]:
            raise RiskIncomplete(role + " isolated-pad report tree has a non-pad or wrong-net edge")
        pair = tuple(sorted((a, b)))
        if pair in unique or representative(a) == representative(b):
            raise RiskIncomplete(
                role + " isolated-pad report tree repeats an edge or contains a cycle"
            )
        unique.add(pair)
        parents[representative(a)] = representative(b)
        edges[pads[a]["net"]].append(list(pair))
    result = []
    for net, members in sorted(by_net.items()):
        if (
            len(edges[net]) != len(members) - 1
            or len({representative(pad) for pad in members}) != 1
        ):
            raise RiskIncomplete(
                role + " isolated-pad report tree omits physical pads or edges: " + net
            )
        result.append(
            {"net": net, "physical_pad_uuids": sorted(members), "edges": sorted(edges[net])}
        )
    return result


def _isolated_pad_tree_reselection(edges, before_edges, after_edges, before_board, after_board):
    """A bounded report-representation proof, never a general connectivity exemption."""
    if not edges["new"] and not edges["removed"]:
        return {"status": "not_needed", "reason": "No reported endpoint pair changed"}
    inventories = {}
    for role, board in (("baseline", before_board), ("candidate", after_board)):
        try:
            inventories[role] = _isolated_pad_inventory(board)
        except (TypeError, ValueError, ArithmeticError) as exc:
            return {"status": "not_applicable", "reason": role + ": " + str(exc)}
    before, after = inventories["baseline"], inventories["candidate"]
    if (
        before["footprints"] != after["footprints"]
        or before["protected_board_structure"] != after["protected_board_structure"]
    ):
        return {
            "status": "not_applicable",
            "reason": "Saved structures changed beyond footprint translation",
        }
    for inventory in inventories.values():
        inventory["membership"] = {
            identity: {
                key: value
                for key, value in pad.items()
                if key not in {"world_nm", "bounding_rectangle_twice_nm"}
            }
            for identity, pad in inventory["pads"].items()
        }
    if before["membership"] != after["membership"]:
        return {
            "status": "not_applicable",
            "reason": "Physical pad identities, local geometry or net membership changed",
        }
    trees = {
        "baseline": _singleton_report_trees(before_edges, before["pads"], role="Baseline"),
        "candidate": _singleton_report_trees(after_edges, after["pads"], role="Candidate"),
    }
    return {
        "status": "proved",
        "kind": "isolated-singleton-pad-report-tree-reselection.v1",
        "official_native": False,
        "source": (
            "custom saved-board conservative AABB + official-report spanning-tree proof; "
            "no native connectivity rerun"
        ),
        "kicad_version": "10.0.6",
        "no_routed_or_other_copper": True,
        "same_physical_pad_identities_local_geometry_and_nets": True,
        "all_pad_copper_bounds_strictly_disjoint_per_board": True,
        "bounding_rectangle_units": "twice integer nanometres; no half-nanometre truncation",
        "board_sha256": {
            role: inventory["board_sha256"] for role, inventory in inventories.items()
        },
        "isolated_physical_pads": {
            role: inventory["pads"] for role, inventory in inventories.items()
        },
        "complete_report_trees": trees,
        "reselected_edges": {name: copy.deepcopy(edges[name]) for name in ("new", "removed")},
        "limit": (
            "Only the proved pad-only isolated components; "
            "not a complete electrical graph or DRC implementation"
        ),
    }


def _moved_footprints(before, after):
    original = {key: row for key, row in before.items() if row["kind"] == "footprint"}
    candidate = {key: row for key, row in after.items() if row["kind"] == "footprint"}
    if original.keys() != candidate.keys():
        raise RiskIncomplete(
            "Footprint identities changed; this is not an identifiable translation"
        )
    moved = []
    for identity, old in original.items():
        new = candidate[identity]
        if old["position_nm"] == new["position_nm"]:
            continue
        old_pads = {
            key: row
            for key, row in before.items()
            if row["kind"] == "pad" and row["footprint_uuid"] == identity
        }
        new_pads = {
            key: row
            for key, row in after.items()
            if row["kind"] == "pad" and row["footprint_uuid"] == identity
        }
        if old_pads.keys() != new_pads.keys():
            raise RiskIncomplete("Moved pad identities changed")
        moved.append(
            {
                "uuid": identity,
                "ref": old["ref"],
                "before_position_nm": old["position_nm"],
                "after_position_nm": new["position_nm"],
                "copper_follows_footprint": False,
                "pads": [
                    {
                        "uuid": key,
                        "number": pad["number"],
                        "net_before": pad["net"],
                        "net_after": new_pads[key]["net"],
                        "before_world_nm": pad["position_nm"],
                        "after_world_nm": new_pads[key]["position_nm"],
                        "layers_before": pad["layers"],
                        "layers_after": new_pads[key]["layers"],
                    }
                    for key, pad in old_pads.items()
                ],
            }
        )
    return moved


def compare_reports(before_report, after_report, before_board, after_board) -> dict:
    """Compare validated official report multisets without cancelling new issues.

    Native violations retain object and reported positions in their identity.
    Moving an existing violation conservatively counts as a new finding. Reported
    unconnected endpoint pairs instead retain identity across anchor translation,
    after every reference point matches its respective saved board. These pairs
    are not the full connectivity graph and KiCad may reselect anchors.
    """
    _validate(before_report, before_board)
    _validate(after_report, after_board)
    for name in ("ignored_checks", "included_severities", "kicad_version"):
        if before_report[name] != after_report[name]:
            raise RiskIncomplete("Native DRC policy/version drift: " + name)
    before_index, after_index = object_index(before_board), object_index(after_board)
    before_edges = _normalise(before_report, before_index, "unconnected_items")
    after_edges = _normalise(after_report, after_index, "unconnected_items")
    for role, report, edge_findings in (
        ("Baseline", before_report, before_edges),
        ("Candidate", after_report, after_edges),
    ):
        _validate_connection_reference_points(
            edge_findings, version=report["kicad_version"], role=role
        )
    before_findings, after_findings = [], []
    for group in ("violations", "schematic_parity"):
        before_findings += _normalise(before_report, before_index, group)
        after_findings += _normalise(after_report, after_index, group)
    findings = _partition(before_findings, after_findings)
    edges = _partition(before_edges, after_edges, connection=True)
    moved = _moved_footprints(before_index, after_index)
    moved_nets = {
        pad[name]
        for footprint in moved
        for pad in footprint["pads"]
        for name in ("net_before", "net_after")
    }
    if before_report["kicad_version"] == "10.0.6":
        aliases, blocking_edges = _classify_anchor_aliases(
            edges, before_index, after_index, moved_nets
        )
    else:
        aliases, blocking_edges = [], edges["new"]
    reselection = (
        _isolated_pad_tree_reselection(edges, before_edges, after_edges, before_board, after_board)
        if before_report["kicad_version"] == "10.0.6"
        else {"status": "not_applicable", "reason": "Unverified native version"}
    )
    if reselection["status"] == "proved":
        blocking_edges = []
    return {
        "status": "blocked" if findings["new"] or blocking_edges else "passed",
        "source": {
            "official_native": True,
            "origin": "KiCad CLI pcb drc JSON",
            "kicad_version": before_report["kicad_version"],
            "comparison": "Identity-bound report multiset comparison; no custom DRC calculation",
            "invocation_binding": "Caller binds the actual tool, inputs and native reports",
        },
        "new_findings": findings["new"],
        "removed_findings": findings["removed"],
        "retained": findings["retained"],
        "counts": {
            "before": {name: len(before_report[name]) for name in drc_runner.REPORT_GROUPS},
            "after": {name: len(after_report[name]) for name in drc_runner.REPORT_GROUPS},
            "new_findings": len(findings["new"]),
            "removed_findings": len(findings["removed"]),
            "retained_findings": len(findings["retained"]),
        },
        "connection_changes": {
            "reported_connection_edges": edges,
            "counts": {name: len(rows) for name, rows in edges.items()},
            "equivalent_anchor_replacements": aliases,
            "equivalent_anchor_replacement_count": len(aliases),
            "isolated_pad_tree_reselection": reselection,
            "blocking_new": blocking_edges,
            "blocking_new_count": len(blocking_edges),
            "basis": (
                "Verified per-board report references, then unordered endpoint UUID, "
                "machine object kind/net/layers and finding flags"
            ),
            "complete_connectivity_graph": False,
            "anchors_may_be_reselected": True,
            "interpretation": "Changed reported endpoint pairs; not proof of all net connections",
        },
        "moved_footprints": moved,
        "ignored_checks": copy.deepcopy(before_report["ignored_checks"]),
        "included_severities": list(before_report["included_severities"]),
        "not_checked": [
            "This official DRC comparison does not run the separate finite placement guard; "
            "arbitrary-outline and exact polygon courtyard/complex-geometry algorithms are absent",
            "A complete connectivity graph or routing repair; copper does not follow moved pads",
            "Whole-board image readability and hidden-layer visual review",
            "Manufacturing acceptance or a claim that the existing board has zero violations",
        ],
        "comparison_limits": [
            "An existing violation at a changed object/position conservatively blocks as new",
            "Every unconnected report reference matches its own saved board before grouping",
            "Unchanged endpoint identities retain verified before/after reference positions",
            "A unique unchanged via/straight-segment endpoint alias may be proved separately",
            "Reported conductor reference positions and the proved junction position are distinct",
            "Unconnected references use KiCad 10.0.6 track start / via center / pad world center",
            "Alias proof excludes moved-pad nets, special via geometry and transitive connections",
            "A pad-only tree reselection requires unchanged identities/nets/local geometry, "
            "no other copper, strictly disjoint supported pad bounds "
            "and a complete unique report tree on each saved board",
            "A report alone does not prove invocation provenance or protected-file preservation",
        ],
        "_untrusted": [
            "new_findings",
            "removed_findings",
            "retained",
            "connection_changes.reported_connection_edges",
            "connection_changes.equivalent_anchor_replacements",
            "connection_changes.isolated_pad_tree_reselection",
            "connection_changes.blocking_new",
            "moved_footprints",
            "ignored_checks",
        ],
    }
