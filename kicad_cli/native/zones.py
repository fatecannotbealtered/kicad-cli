"""Separate zone creation and refill, with saved-file observations.

The production reader/filler/checker share the file engine. This is explicitly
writer_self_check/custom_checker evidence; native consumers are separate tests.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from .. import drc_baseline, envelope, kicad_env
from ..fileformat import polygons, zonefill
from ..fileformat.board import Board
from ..fileformat.drc.settings import Settings
from ..fileformat.sexpr import List
from . import load_board, pour, write


def _hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _tree(node):
    return [_tree(item) if isinstance(item, List) else item for item in node.items]


def _id(zone) -> str:
    node = zone.node.find("uuid")
    if node is None or not node.value(1):
        envelope.fail("E_VALIDATION", "A copper zone has no stable UUID; nothing written")
    return str(node.value(1))


def _definition(zone):
    result = []
    for item in zone.node.items:
        if isinstance(item, List) and item.head == "filled_polygon":
            continue
        if isinstance(item, List) and item.head == "fill":
            # The filler may toggle only this state atom, not thermal settings.
            children = [x for x in item.items[1:] if isinstance(x, List)]
            result.append(["fill", *[_tree(x) for x in children]])
        else:
            result.append(_tree(item) if isinstance(item, List) else item)
    return result


def _protected(board: Board, selected: set[str]):
    skipped = {id(z.node) for z in board.zones if _id(z) in selected}
    return [
        _tree(n) if isinstance(n, List) else n for n in board.root.items if id(n) not in skipped
    ]


def _observe(board: Board, selected: set[str]) -> list[dict]:
    return [
        {
            "uuid": _id(z),
            "net": z.net,
            "layers": z.layers,
            "filled_mm2": {
                layer: polygons.total_area(rings) / 1e12 for layer, rings in z.filled.items()
            },
            "filled_polygons": sum(len(rings) for rings in z.filled.values()),
        }
        for z in board.zones
        if _id(z) in selected
    ]


def _capture(path):
    try:
        return drc_baseline.capture(path, kicad_root=kicad_env.find_kicad_root())[1]
    except (OSError, ValueError) as exc:
        envelope.fail(
            "E_VERIFY_INCOMPLETE",
            "Zone verification could not capture custom DRC",
            {"reason": str(exc), "checker": getattr(exc, "provenance", None)},
        )


def _abort(path: str, baseline: str, code: str, message: str, details: dict) -> None:
    write.write_txn.rollback("zone_verification_failure")
    try:
        restored = _hash(path) == baseline
    except OSError:
        restored = False
    envelope.fail(
        code if restored else "E_OUTCOME_UNKNOWN",
        message,
        {**details, "restoration_read_back": restored},
    )


def _persist(path, board, args, preview, selected, definitions, protected, before_report, status):
    envelope.check_confirm(args.get("confirm"), "zones:" + status, preview, path)
    write.begin(path)
    if _hash(path) != preview["input_sha256"]:
        envelope.fail("E_CONFLICT", "Board changed before the zone write; nothing written")
    board.document.save(path)
    try:
        saved = Board.load(path)
        observed_ids = {_id(z) for z in saved.zones}
        definition_ok = selected <= observed_ids and all(
            _definition(z) == definitions[_id(z)] for z in saved.zones if _id(z) in selected
        )
        protected_ok = _protected(saved, selected) == protected
        observation = _observe(saved, selected)
    except (OSError, ValueError) as exc:
        _abort(
            path,
            preview["input_sha256"],
            "E_VERIFY_INCOMPLETE",
            "Saved zones could not be read",
            {"reason": str(exc)},
        )
    verification = {
        "source": "writer_self_check",
        "independence": "shared_file_engine",
        "completion": "completed",
        "zone_definitions_preserved": definition_ok,
        "non_target_content_preserved": protected_ok,
    }
    if not definition_ok or not protected_ok:
        _abort(
            path,
            preview["input_sha256"],
            "E_VERIFY_FAILED",
            "Saved zone contents differ from the plan",
            {"verification": verification},
        )
    try:
        after_report = drc_baseline.capture(path, kicad_root=kicad_env.find_kicad_root())[1]
    except (OSError, ValueError) as exc:
        _abort(
            path,
            preview["input_sha256"],
            "E_VERIFY_INCOMPLETE",
            "Saved zone DRC could not complete",
            {"reason": str(exc), "verification": verification},
        )
    comparison = drc_baseline.compare(before_report, after_report)
    verification["finding_comparison"] = comparison
    if comparison["status"] != "passed":
        _abort(
            path,
            preview["input_sha256"],
            "E_VERIFY_FAILED" if comparison["status"] == "failed" else "E_VERIFY_INCOMPLETE",
            "Zone change did not pass the bounded custom finding gate",
            {"verification": verification},
        )
    try:
        drc_baseline.require_saved_input(path, after_report)
    except (OSError, ValueError) as exc:
        _abort(
            path,
            preview["input_sha256"],
            "E_VERIFY_INCOMPLETE",
            "Zone DRC input changed",
            {"reason": str(exc), "verification": verification},
        )
    write.done(
        {
            "board": str(path),
            "status": status,
            "zones": observation,
            "verification": verification,
            "input_sha256": preview["input_sha256"],
            "output_sha256": _hash(path),
        }
    )


def create(path: str, args: dict) -> None:
    write.start()
    board = load_board(path)
    net, layer_name = str(args["net"]).strip(), str(args["layer"]).strip()
    layer = pour.layer_named(board, layer_name)
    if layer is None or not layer.name.endswith(".Cu"):
        envelope.fail("E_VALIDATION", "Select an existing copper layer", {"layer": layer_name})
    if net not in pour.nets_of(board):
        envelope.fail("E_NOT_FOUND", "Net not present in board", {"net": net})
    if any(z.net == net and layer.name in z.layers and not z.rule_area for z in board.zones):
        envelope.fail(
            "E_CONFLICT", "Zone already exists; use zone refill", {"net": net, "layer": layer.name}
        )
    options = {
        "margin": float(args.get("margin", pour.DEFAULT_MARGIN_MM)),
        "clearance": float(args.get("clearance", pour.DEFAULT_CLEARANCE_MM)),
        "min_width": float(args.get("min-width", pour.DEFAULT_MIN_WIDTH_MM)),
        "connect": str(args.get("connect", "thermal")),
    }
    rect = pour.board_rect(board, options["margin"])
    baseline, before = _hash(path), _capture(path)
    protected = _protected(board, set())
    node = pour.zone_node(net, layer.name, rect, options)
    seed = json.dumps([baseline, net, layer.name, rect, options], sort_keys=True)
    identity = str(uuid.uuid5(uuid.NAMESPACE_URL, seed))
    node.find("uuid").set(1, '"' + identity + '"')
    # KiCad's zone fill grammar has an optional "yes" atom, not "yes|no".
    # An unfilled zone omits that atom while retaining its fill settings.
    node.find("fill").pop(1)
    pour._insert_zone(board, node)
    board = Board(board.document, board.path)
    selected = {identity}
    definitions = {_id(z): _definition(z) for z in board.zones if _id(z) in selected}
    preview = {
        "board": path,
        "input_sha256": baseline,
        "dependencies": before.provenance["dependencies"],
        "zone_uuid": identity,
        "net": net,
        "layer": layer.name,
        "outline_nm": list(rect),
        "options": options,
        "effects": ["create_zone", "persist_board"],
        "will": "create an unfilled zone; existing zone definitions and fills stay unchanged",
    }
    _persist(path, board, args, preview, selected, definitions, protected, before, "CREATED")


def refill(path: str, args: dict) -> None:
    write.start()
    board = load_board(path)
    available = {_id(z): z for z in board.zones if not z.rule_area}
    selected = set(available) if args.get("all") else set(str(args.get("zones", "")).split(","))
    if selected - available.keys():
        envelope.fail(
            "E_NOT_FOUND",
            "Selected zone UUID not present",
            {"missing": sorted(selected - available.keys())},
        )
    baseline = _hash(path)
    if not selected:
        write.done(
            {
                "board": path,
                "status": "NOOP",
                "zones": [],
                "verification": {
                    "source": "writer_self_check",
                    "completion": "completed",
                    "scope": "no copper zones",
                },
                "input_sha256": baseline,
                "output_sha256": baseline,
            }
        )
    before = _capture(path)
    protected = _protected(board, selected)
    definitions = {identity: _definition(available[identity]) for identity in selected}
    preview = {
        "board": path,
        "input_sha256": baseline,
        "dependencies": before.provenance["dependencies"],
        "zone_uuids": sorted(selected),
        "effects": ["replace_selected_zone_fill", "persist_board"],
        "will": "refill selected zones; outlines/settings and non-target content stay unchanged",
    }
    # Fill before confirmation only in memory; no files or native consumers run.
    filler = zonefill.Filler(board, Settings.of(board.path.with_suffix(".kicad_pro")))
    for identity in sorted(selected):
        zonefill.write(filler.fill(available[identity]))
    _persist(path, board, args, preview, selected, definitions, protected, before, "REFILLED")
