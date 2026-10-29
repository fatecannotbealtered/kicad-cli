"""Authenticated disposable candidate, using domain primitives, not CLI handlers."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
from pathlib import Path

from . import drc_baseline
from . import engineering_readback as consumer
from .move_transaction import digest, regular


def prepare(request):
    command, params = request["command"], request["params"]
    if command == "sch export":
        from .sch_export import prepare as export_prepare

        return export_prepare(request)
    from .module_contract import WRITE_COMMANDS

    if command in WRITE_COMMANDS:
        from .module_operations import prepare as module_prepare

        return module_prepare(request)
    mapping = {Path(k): Path(v) for k, v in request["mapping"].items()}
    primary = Path(request["primary"])
    before_raw = primary.read_bytes() if primary.exists() else None
    before = (
        drc_baseline.record(drc_baseline.capture(primary)[1])
        if primary.suffix == ".kicad_pcb" and before_raw
        else None
    )
    outputs = []
    hierarchy = None
    if command == "sch create":
        from .native.sch_create import create
        from .sch_hierarchy_contract import hierarchy_plan

        captured_spec = mapping[Path(params["spec"]).resolve()]
        hierarchy = hierarchy_plan(json.loads(captured_spec.read_bytes()), primary.stem)

        result = create(str(captured_spec), str(primary.parent))
        if hierarchy:
            outputs = [primary.parent / name for name in hierarchy["output_names"]]
            written = result.get("written", {})
            expected_written = {
                "schematic": str(primary),
                "children": [
                    str(primary.parent / child["file"]) for child in hierarchy["children"]
                ],
                "netlist": str(primary.with_suffix(".net")),
            }
            if written != expected_written or result.get("hierarchy") != hierarchy:
                raise ValueError("Hierarchy candidate file/identity set differs from frozen intent")
        else:
            outputs = [primary, primary.with_suffix(".net")]
            if result.get("drawing", {}).get("style") == "blank":
                outputs.append(primary.with_suffix(".kicad_pro"))
    elif command == "sch edit":
        from .native import sch_edit

        changes = sch_edit.load_changes(str(mapping[Path(params["changes"]).resolve()]))
        identity_root = next(
            (source for source, candidate in mapping.items() if candidate == primary), None
        )
        if identity_root is None:
            raise ValueError("Schematic primary has no captured original identity")
        if any(change.get("op") == "circuit" for change in changes):
            resource_files = {
                source: candidate.read_bytes() if candidate.is_file() else None
                for source, candidate in mapping.items()
            }
            result, sources = sch_edit.run(
                primary, changes, identity_root=identity_root, resource_files=resource_files
            )
        else:
            result, sources = sch_edit.run(primary, changes, identity_root=identity_root)
        if not sch_edit.passed(result["verified"]):
            from . import sch_edit_contract

            return {
                "ok": False,
                "error": {
                    "code": "E_VALIDATION",
                    "message": "Schematic edit changes unintended pins, nets, fields or geometry",
                    "details": {
                        "verified": result["verified"],
                        **sch_edit_contract.details(
                            [
                                {
                                    "path": getattr(changes, "path_prefix", "") or "/",
                                    "problem": (
                                        "The combined edits change unintended pins, nets, fields "
                                        "or geometry; inspect verified.problems "
                                        "and revise the batch"
                                    ),
                                }
                            ]
                        ),
                    },
                },
            }
        for name, source in sources.items():
            # Only disposable candidate directories may be created here. Formal
            # source parents are predeclared existing directories.
            name.parent.mkdir(parents=True, exist_ok=True)
            name.write_bytes(source.encode())
            outputs.append(name)
        if not outputs:
            outputs = [primary]
        if request.get("hierarchy_incremental"):
            from .hierarchy_publication import save_order

            outputs = save_order(outputs)
    elif command == "project links repair":
        from .commands import sch

        report = sch._analyse(primary)
        if sch._blockers(report):
            raise ValueError(
                "Ambiguous, dangling or mismatched identities require explicit reconciliation"
            )
        available, missing = sch._netlist_paths(primary)
        if missing:
            raise ValueError("Required schematic hierarchy is incomplete")
        selected = set(params.get("refs") or [])
        targets = report["details"]["unlinked"]
        if selected:
            if selected - {row["ref"] for row in targets}:
                raise ValueError("Requested repair refs must identify unlinked footprints")
            targets = [row for row in targets if row["ref"] in selected]
        wanted = {
            row["ref"]: available[row["ref"]]["path"] for row in targets if row["ref"] in available
        }
        if len(wanted) != len(targets):
            raise ValueError("Unlinked footprint has no unambiguous schematic identity")
        text = before_raw.decode()
        after, inserted, missed = sch._insert_paths(text, wanted)
        if missed or not sch._only_added_paths(text, after)[0]:
            raise ValueError("Repair must only add the selected identity paths")
        primary.write_bytes(after.encode())
        result = {
            "written": inserted,
            "remaining_unlinked": report["unlinked"] - len(inserted),
            "electrical_connections_changed": False,
        }
        outputs = [primary]
    elif command == "board from-netlist":
        from . import boardgen
        from .native import from_netlist

        name = mapping[Path(params["netlist"]).resolve()]
        plan = boardgen.plan(str(name), params["pitch"], params["margin"], primary.parent)
        for component in plan["components"]:
            source = Path(component["file"]).resolve()
            component["file"] = str(mapping.get(source, source))
        document, joined, missing, size = from_netlist.prepare(plan, name.read_bytes())
        if missing:
            raise ValueError("Netlist pins have no physical footprint pads: " + json.dumps(missing))
        refs = [item["ref"] for item in plan["components"]]
        if len(refs) != len(set(refs)):
            raise ValueError("Duplicate component references")
        primary.write_bytes(document.dumps().encode())
        result = {
            "components": refs,
            "pads_connected": joined,
            "board_mm": size,
            "layout_policy": "create new; existing primary refused",
        }
        outputs = [primary]
        if not primary.with_suffix(".kicad_pro").exists():
            from_netlist._project_files(primary)
            outputs.append(primary.with_suffix(".kicad_pro"))
        elif params.get("initialize-empty-project"):
            from .fileformat import new_project

            project_file = primary.with_suffix(".kicad_pro")
            if json.loads(project_file.read_bytes()) != {}:
                raise ValueError("Configured project cannot be initialized as empty")
            project_file.write_text(new_project.project(primary.stem), encoding="utf-8")
            outputs.append(project_file)
    elif command == "board footprint place":
        from .native import place

        if not params.get("allow-routed"):
            from .fileformat.board import Board

            board = Board.load(primary)
            if board.tracks or board.vias:
                raise ValueError("Existing routes need --allow-routed; pads may disconnect tracks")
        result = place.prepare(str(primary), params)
        outputs = [primary]
    elif command == "board zone stitch":
        from .engineering_fill import fill
        from .native import stitch

        native_before = fill(primary)
        result = stitch.prepare(str(primary), params)
        result["native_fill_before"] = native_before
        result["native_fill_after"] = fill(primary)
        outputs = [primary]
        result["connections"] = stitch_connections(before_raw, primary, params["net"])
    elif command == "board silkscreen place":
        from .engineering_silk import prepare as silk
        from .engineering_silk import targets_document
        from .sync_inputs import _absolute

        selection = params.get("targets")
        targets = (
            targets_document(mapping[_absolute(selection)].read_bytes()) if selection else None
        )
        try:
            result = silk(primary, params, targets)
        except ValueError as exc:
            return {
                "ok": False,
                "error": {
                    "code": "E_VALIDATION",
                    "message": str(exc),
                    "details": getattr(exc, "details", {}),
                },
            }
        outputs = [primary]
    else:
        raise ValueError("Unknown engineering preparation command")
    from .payload import write_txn

    write_txn.commit()  # Domain primitives' private scratch sidecars only.
    if primary.suffix == ".kicad_pcb" and before_raw:
        silk_targets = (
            result.get("targets") if result.get("selection_mode") == "explicit_targets" else None
        )
        if consumer.protection(
            before_raw, command, silk_targets=silk_targets
        ) != consumer.protection(primary.read_bytes(), command, silk_targets=silk_targets):
            raise ValueError("Candidate changed protected board identities/geometry/format")
    after = (
        drc_baseline.record(drc_baseline.capture(primary)[1])
        if primary.suffix == ".kicad_pcb"
        else None
    )
    comparison = drc_baseline.compare_records(before, after) if before is not None else None
    inverse = {v: k for k, v in mapping.items()}
    values = []
    for path in outputs:
        original = inverse.get(path)
        if original is None:
            if hierarchy or command == "sch edit":
                raise ValueError("Hierarchy output is absent from the captured path mapping")
            original = Path(request["original_primary"]).with_suffix(path.suffix)
        intended = digest(path)
        if command == "sch create" and (
            path == primary or (hierarchy and path.suffix == ".kicad_sch")
        ):
            geometry = result.get("creation_geometry")
            if hierarchy:
                pages = geometry.get("pages") if isinstance(geometry, dict) else None
                if (
                    not isinstance(pages, list)
                    or len(pages) != 3
                    or any(
                        not isinstance(row, dict) or not isinstance(row.get("source"), dict)
                        for row in pages
                    )
                    or {row["source"].get("file") for row in pages}
                    != {name for name in hierarchy["output_names"] if name.endswith(".kicad_sch")}
                ):
                    raise ValueError(
                        "Every hierarchy page requires its own captured geometry observation"
                    )
                observed = next(
                    row["source"] for row in pages if row["source"]["file"] == path.name
                )
            else:
                observed = geometry.get("source") if isinstance(geometry, dict) else None
            if (hierarchy or isinstance(observed, dict)) and (
                observed.get("file") != path.name or observed.get("sha256") != intended
            ):
                return {
                    "ok": False,
                    "error": {
                        "code": "E_CONFLICT",
                        "message": "Created candidate changed after geometry observation",
                        "details": {"candidate": str(path)},
                    },
                }
        values.append({"path": str(original), "candidate": str(path), "intended": intended})
    return {
        "ok": True,
        "primary_candidate": str(primary),
        "files": values,
        "result": result,
        "finding_comparison": comparison,
        "official_native": False,
    }


def stitch_connections(before_raw, primary, net):
    from .fileformat.board import Board
    from .fileformat.connectivity import connect
    from .fileformat.sexpr import Document

    before = Board(Document.parse(before_raw.decode()))
    after = Board.load(primary)
    old_ids = {v.node.find("uuid").value(1) for v in before.vias}
    # Existing tracks/vias are separately protected against modification/deletion.
    old_root, new_root = (
        consumer._parse(before_raw.decode()),
        consumer._parse(primary.read_text(encoding="utf-8")),
    )
    old = {
        json.dumps(consumer.canonical(n, ""))
        for n in old_root.items
        if not isinstance(n, consumer._Atom) and n.head in {"via", "segment", "arc"}
    }
    new = {
        json.dumps(consumer.canonical(n, ""))
        for n in new_root.items
        if not isinstance(n, consumer._Atom) and n.head in {"via", "segment", "arc"}
    }
    if not old <= new:
        raise ValueError("Stitch changed existing copper")
    connected = connect(after)
    rows = []
    for index, item in enumerate(connected.items):
        if item.kind != "via":
            continue
        uid = item.owner.node.find("uuid").value(1)
        if uid in old_ids:
            continue
        cluster = connected.cluster_of[index]
        members = [
            other for k, other in enumerate(connected.items) if connected.cluster_of[k] == cluster
        ]
        layers = sorted(
            {
                layer
                for other in members
                if other.kind == "fill" and other.net == net
                for layer in other.layers
                if layer in item.layers
            }
        )
        pads = sorted(other.label for other in members if other.kind == "pad" and other.net == net)
        if item.net != net or len(layers) < 2 or not pads:
            raise ValueError(
                "New stitch lacks two actual filled layers and a ground-pad connection"
            )
        rows.append(
            {
                "id": uid,
                "net": item.net,
                "filled_layers": layers,
                "connected_pads": pads,
                "position_nm": list(item.owner.position),
                "diameter_nm": item.owner.diameter,
                "drill_nm": item.owner.drill,
                "span": list(item.owner.layers),
            }
        )
    return {
        "new_vias": rows,
        "method": "custom filled-copper topology; separate SDK and native DRC acceptance required",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True, type=Path)
    args = parser.parse_args()
    request = json.loads(args.request.read_bytes())
    root = args.request.resolve().parent
    from . import driver_runtime

    with driver_runtime.create_cli() as cli:
        from ._driver_sdk import tokens

        scope = cli.scopes.resolve()
        signature = request.pop("hmac", "")
        expected = hmac.new(
            cli._store_open().secret(), tokens.canonical(request), hashlib.sha256
        ).hexdigest()
        if (
            request.get("owner") != "kicad-cli.engineering-trial.v1"
            or not root.is_relative_to(cli.state_dir.resolve() / "engineering-trials")
            or not root.name.startswith("op-")
            or not hmac.compare_digest(signature, expected)
            or request["binding"] != cli._binding()
            or request["scope_digest"] != tokens.digest(scope.record)
        ):
            raise ValueError("Private preparation identity/build/grant mismatch")
        scope.require(
            effects=request.get("effects", ["change", "persist"]),
            target=request["target"],
            outputs=[{"path": str(root), "effect": request.get("output_effect", "persist")}],
        )
    for name, expected in request["captured"].items():
        path = Path(name)
        regular(path)
        if not path.resolve().is_relative_to(root / "candidate") or digest(path) != expected:
            raise ValueError("Private captured inputs changed")
    if not Path(request["primary"]).resolve().is_relative_to(root / "candidate"):
        raise ValueError("Private primary escaped stage")
    value = prepare(request)
    sys.stdout.buffer.write(json.dumps(value, ensure_ascii=False).encode() + b"\n")
    return 0 if value["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
