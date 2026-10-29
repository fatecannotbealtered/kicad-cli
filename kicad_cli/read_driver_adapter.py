"""Real immutable saved-file observations. No legacy command handler is dispatched."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import drc_baseline, netlist, read_inputs, sync_readback
from ._driver_sdk.models import DriverError
from .commands import erc as erc_audit
from .commands import sch
from .fileformat import annotation, drc, erc
from .fileformat import netlist as built_netlist
from .fileformat.board import Board
from .fileformat.sexpr import Document
from .native import parity
from .sync_inputs import Drift, InputError

PATHS = frozenset(
    {
        "project links inspect",
        "project parity",
        "board check",
        "sch check",
        "sch objects",
        "sch symbols",
        "sch connections",
    }
)
LIBRARY_LIMIT = (
    "Installed-library comparisons are deferred in this immutable read adapter; "
    "no library bytes are consulted."
)
THERMAL_LIMIT = (
    "Bounded custom ring/intersection/island geometry has recorded native thermal "
    "counterexamples; not equivalent to official DRC. Retained J1 has two counted "
    "middle-gap spokes but native 10.0.6 reports two spokes joined to dead copper. "
    "A sufficient custom count cannot clear native thermal DRC."
)


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def observed_page(selected, params, started, manifest, root, origin, sort_fields):
    """SDK query-page envelope for captured construction resources/connections."""
    source = drc_baseline._source()
    source.update(
        origin=origin,
        kind="custom_saved_file_observer",
        completed=True,
        method="Enumerate captured saved inputs and recheck membership, bytes and identities.",
        started_at=started,
        completed_at=now(),
    )
    revision = selected["snapshot"]
    snapshot = {
        "id": "sch-query-" + revision,
        "revision": revision,
        "source": "disk",
        "observed_at": source["completed_at"],
        "dependencies": [
            {"identity": Path(row["path"]).as_uri(), "revision": row["sha256"] or "absent"}
            for row in manifest
        ],
    }
    count = len(selected["items"])
    if selected.get("count", count) != count:
        raise ValueError("Observed query count does not match the actual page")
    page = {
        "items": selected["items"],
        "count": count,
        "has_more": selected["has_more"],
        "truncated": False,
        "offset": selected["offset"],
        "scope": {
            "description": "Captured construction resources or raw connection occurrences",
            "targets": [root],
            "selector": {
                key: params[key] for key in ("library", "symbol", "page") if key in params
            },
        },
        "snapshot": snapshot,
        "snapshot_unavailable_reason": None,
        "sort": [{"field": key, "direction": "asc"} for key in sort_fields.split(",")],
        "consistency": {
            "mode": "snapshot",
            "drift_policy": "reject",
            "limitations": [
                "Finite saved-file capture with before/after validation; "
                "not an atomic host snapshot.",
                "Custom occurrence analysis is not independent official KiCad XML/ERC validation.",
            ],
        },
        "limits": {
            "requested": params.get("limit", 50),
            "effective": selected["limit"],
            "truncation_reason": None,
        },
        "unread_scopes": [],
        "extensions": {
            **{
                key: value
                for key, value in selected.items()
                if key not in {"items", "count", "has_more", "offset", "next_offset", "limit"}
            },
            "observation": {
                "source": source,
                "inputs": manifest,
                "snapshot": snapshot,
                "state": "saved_files",
                "live_editor_observed": False,
                "input_drift_checked": True,
            },
        },
        "_untrusted": ["items", "extensions"],
    }
    if selected["has_more"]:
        page["next_offset"] = selected["next_offset"]
    return page


class ReadAdapter:
    def run(self, call):
        started = now()
        try:
            if call.command == "sch symbols":
                from .sch_symbols import query

                selected = query(
                    call.params["schematic"],
                    library=call.params.get("library"),
                    symbol=call.params.get("symbol"),
                    offset=call.params.get("offset", 0),
                    limit=call.params.get("limit", 50),
                    page=call.params.get("page"),
                )
                return observed_page(
                    selected,
                    call.params,
                    started,
                    selected["inputs"],
                    Path(call.params["schematic"]).expanduser().resolve().as_uri(),
                    "kicad_cli.sch_symbols",
                    "library_id,symbol_id",
                )
            if call.command == "board check" and not 1 <= call.params.get("limit", 50) <= 10000:
                raise InputError("Detail limit must be 1 through 10000.", parameter="--limit")
            board = call.params.get("board")
            schematic = call.params.get("schematic")
            if call.command.startswith("project ") and schematic is None:
                schematic = str(Path(board).with_suffix(".kicad_sch"))
            inputs = read_inputs.capture(board=board, schematic=schematic)
            if call.command == "sch objects":
                return self.sch_objects(inputs, call.params, started)
            if call.command == "sch connections":
                from .sch_circuit_guard import query_connections
                from .sch_objects import Catalog

                catalog = Catalog(inputs.design)
                selected = query_connections(
                    catalog,
                    offset=call.params.get("offset", 0),
                    limit=call.params.get("limit", 50),
                )
                inputs.validate()
                selected.setdefault("snapshot", catalog.snapshot)
                selected.setdefault("root", catalog.root)
                selected.setdefault("pages", catalog.pages)
                return observed_page(
                    selected,
                    call.params,
                    started,
                    inputs.manifest,
                    catalog.root,
                    "kicad_cli.sch_circuit_guard",
                    "connection_id",
                )
            if call.command == "board check":
                data, coverage = self.board_check(inputs, call.params)
                engine = "kicad_cli.fileformat.drc"
            elif call.command == "sch check":
                data, coverage = self.sch_check(inputs)
                engine = "kicad_cli.fileformat.erc"
            else:
                data, coverage = self.project(inputs, links=call.command == "project links inspect")
                engine = "kicad_cli.saved-project-comparison"
            inputs.validate()
            source = drc_baseline._source()
            source.update(
                origin=engine,
                method="Compute from captured saved bytes; recheck bytes/existence/identity "
                "before returning.",
                kind="custom_saved_file_observer",
                completed=True,
                started_at=started,
                completed_at=now(),
            )
            digest = hashlib.sha256(
                json.dumps(inputs.manifest, sort_keys=True).encode()
            ).hexdigest()
            data["observation"] = {
                "source": source,
                "inputs": inputs.manifest,
                "snapshot": {
                    "revision": digest,
                    "basis": "complete captured input manifest",
                    "observed_at": source["completed_at"],
                },
                "coverage": coverage,
                "state": "saved_files",
                "live_editor_observed": False,
                "input_drift_checked": True,
            }
            return data
        except Drift as exc:
            raise DriverError(
                "E_CONFLICT",
                str(exc),
                details={"conflict": "state_drift", **exc.details},
                dispatch_state="not_started",
            ) from exc
        except FileNotFoundError as exc:
            raise DriverError(
                "E_NOT_FOUND",
                "A required saved input does not exist.",
                details={"path": str(exc)},
                dispatch_state="not_started",
            ) from exc
        except OSError as exc:
            raise DriverError(
                "E_IO",
                "A saved input could not be read.",
                details={"reason": str(exc)},
                dispatch_state="not_started",
            ) from exc
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise DriverError(
                "E_VALIDATION",
                "A complete saved observation could not be computed.",
                details={
                    "problem": "invalid_value",
                    "parameter": "--board/--schematic",
                    "reason": str(exc),
                    **getattr(exc, "details", {}),
                },
                dispatch_state="not_started",
            ) from exc

    def sch_objects(self, inputs, params, started):
        from .sch_objects import Catalog, CatalogError

        try:
            selected = Catalog(inputs.design).query(params)
        except CatalogError as exc:
            raise DriverError(
                exc.code,
                str(exc),
                details={
                    **({"problem": "invalid_value"} if exc.code == "E_VALIDATION" else {}),
                    "parameter": "--page/--instance/--kind/--uuid/--ref/--net/--offset/--limit",
                    **exc.details,
                },
                dispatch_state="not_started",
            ) from exc
        inputs.validate()
        source = drc_baseline._source()
        source.update(
            origin="kicad_cli.sch_objects",
            method="Enumerate captured saved schematic source objects and every hierarchy "
            "occurrence; recheck captured bytes, existence and identity before returning.",
            kind="custom_saved_file_observer",
            completed=True,
            started_at=started,
            completed_at=now(),
        )
        snapshot = {
            "id": "sch-objects-" + selected["snapshot"],
            "revision": selected["snapshot"],
            "source": "disk",
            "observed_at": source["completed_at"],
            "dependencies": [
                {"identity": Path(row["path"]).as_uri(), "revision": row["sha256"] or "absent"}
                for row in inputs.manifest
            ],
        }
        filters = {
            key: params[key]
            for key in ("page", "instance", "kind", "uuid", "ref", "net")
            if params.get(key) is not None
        }
        data = {
            "items": selected["items"],
            "count": selected["count"],
            "has_more": selected["has_more"],
            "truncated": False,
            "offset": selected["offset"],
            "scope": {
                "description": "Source objects in the complete captured reachable "
                "schematic hierarchy",
                "targets": [selected["root"]],
                "selector": filters,
            },
            "snapshot": snapshot,
            "snapshot_unavailable_reason": None,
            "sort": [
                {"field": "source_relative", "direction": "asc"},
                {"field": "source_span.0", "direction": "asc"},
                {"field": "kind", "direction": "asc"},
            ],
            "consistency": {
                "mode": "snapshot",
                "drift_policy": "reject",
                "limitations": [
                    "Saved files only; live editor memory is unobserved.",
                    "One source object may occur in several hierarchy instances; editing it "
                    "affects every listed occurrence.",
                ],
            },
            "limits": {
                "requested": params.get("limit", 100),
                "effective": selected["limit"],
                "truncation_reason": None,
            },
            "unread_scopes": [],
            "extensions": {
                **{
                    key: value
                    for key, value in selected.items()
                    if key not in {"items", "count", "has_more", "offset", "next_offset", "limit"}
                },
                "observation": {
                    "source": source,
                    "inputs": inputs.manifest,
                    "snapshot": snapshot,
                    "snapshot_basis": "Reachable schematic saved bytes and root project "
                    "bytes/existence; root URI bound separately",
                    "state": "saved_files",
                    "live_editor_observed": False,
                    "input_drift_checked": True,
                },
            },
            "_untrusted": ["items", "extensions"],
        }
        if selected["has_more"]:
            data["next_offset"] = selected["next_offset"]
        return data

    def board_check(self, inputs, params):
        board = Board(Document.parse(inputs.raw(inputs.board).decode("utf-8")), inputs.board)
        settings = drc.Settings.of(inputs.board.with_suffix(".kicad_pro"), reader=inputs.raw)
        report = drc.check(inputs.board, settings=settings, board=board)
        for name in drc.LIBRARY:
            if name in report.not_checked:
                report.not_checked[name] = LIBRARY_LIMIT
        report.partial["starved_thermal"] = THERMAL_LIMIT
        severity, limit = params.get("severity", "all"), params.get("limit", 50)
        counts = dict(Counter(v.severity for v in report.violations if not v.excluded))
        shown = [v for v in report.violations if severity in ("all", v.severity)]

        def flatten(v):
            return {
                "severity": v.severity,
                "type": v.rule,
                "description": v.message,
                "items": [i.description for i in v.items],
                "item_records": [i.to_dict() for i in v.items],
                "excluded": v.excluded,
                "exclusion_comment": v.comment,
            }

        passed = counts.get("error", 0) == 0 and not any(not v.excluded for v in report.unconnected)
        return {
            "board": str(inputs.board),
            "engine": "kicad-cli custom DRC on captured saved files",
            "counts": counts,
            "excluded_count": sum(v.excluded for v in report.violations),
            "unconnected_count": sum(not v.excluded for v in report.unconnected),
            "limited_checks_passed": passed,
            "ok_to_fabricate": passed,
            "violations": [flatten(v) for v in shown[:limit]],
            "violations_shown": min(limit, len(shown)),
            "violations_total": len(report.violations),
            "violations_matching_severity": len(shown),
            "unconnected": [flatten(v) for v in report.unconnected[:limit]],
            "unconnected_total": len(report.unconnected),
            "truncated": len(shown) > limit or len(report.unconnected) > limit,
            "not_checked": report.not_checked,
            "partial": report.partial,
            "ignored": report.ignored,
            "custom_rules": report.custom_rules,
            "note": "ok_to_fabricate is a deprecated compatibility alias of limited_checks_passed; "
            "no manufacturing approval. Counts cover the whole custom report, detail arrays obey "
            "severity/limit. Native DRC is separate evidence.",
        }, {
            "checked": sorted(set(drc.CHECKED) - set(report.not_checked) - set(report.ignored)),
            "not_checked": report.not_checked,
            "partial": report.partial,
            "ignored": report.ignored,
            "native": False,
            "manufacturing_approval": False,
            "native_drc_required_rules": ["starved_thermal"]
            if "starved_thermal" in report.partial
            else [],
        }

    def sch_check(self, inputs):
        data = erc_audit.inspect(inputs.schematic, design=inputs.design, reader=inputs.raw)
        data["not_checked"] = [
            text.replace(
                "KiCad's installation was not found",
                "installed-library comparisons are deferred by this immutable adapter",
            )
            for text in data["not_checked"]
        ]
        project = json.loads(
            (inputs.raw(inputs.schematic.with_suffix(".kicad_pro")) or b"{}").decode("utf-8")
        )
        data["configured_exclusions"] = (project.get("erc") or {}).get("erc_exclusions") or []
        data["exclusions_applied"] = False
        data["detail_samples"] = {
            "configured": True,
            "disabled": True,
            "basis": "Full counts; configured/hidden details are per-type samples.",
        }
        ignored = data["silenced_rules"]["all"]
        return data, {
            "checked": sorted(set(erc.CHECKED) - set(erc.LIBRARY) - set(ignored)),
            "not_checked": {
                **erc.NOT_CHECKED,
                **{r: LIBRARY_LIMIT for r in erc.LIBRARY},
                "project_erc_exclusions": "Recorded, not applied; counts retain excluded findings.",
            },
            "ignored": ignored,
            "disabled_rules_diagnostic_ran": True,
            "native": False,
            "circuit_correctness_approval": False,
        }

    def project(self, inputs, *, links):
        design = inputs.design
        if not any(instance.schematic.symbols for instance in design.instances):
            raise DriverError(
                "E_NOT_FOUND",
                "No schematic symbols were found for this project.",
                details={
                    "path": str(inputs.schematic),
                    "expected_root": str(inputs.schematic),
                },
                dispatch_state="not_started",
            )
        built = built_netlist.build(design)
        parts = [netlist.record(c) for c in built.components]
        problems = [p.to_dict() for p in annotation.problems(design)]
        if problems or len({p["ref"].casefold() for p in parts}) != len(parts):
            raise InputError(
                "Source annotation is ambiguous; no confident project verdict.", annotation=problems
            )
        actual, _, _ = sync_readback.inspect_bytes(inputs.raw(inputs.board))
        if links:
            footprints = [
                {
                    "ref": p["ref"],
                    "uuid": p["id"],
                    "path": p["path"],
                    "footprint": p["fpid"],
                    "board_only": "board_only" in p["attributes"],
                    "unannotated": "**" in p["ref"],
                }
                for p in actual
            ]
            symbols = []
            for instance in design.instances:
                for symbol in instance.schematic.symbols:
                    placed = symbol.instance(instance.path)
                    ref = placed.reference if placed else symbol.properties.get("Reference", "")
                    if not symbol.on_board and not ref.startswith("#"):
                        continue
                    symbols.append(
                        {
                            "ref": ref,
                            "unit": placed.unit if placed else symbol.unit,
                            "uuid": symbol.uuid,
                            "sheet_path": instance.path,
                            "full_path": instance.board_path + symbol.uuid,
                            "sheet_file": instance.schematic.path.name,
                            "lib_id": symbol.lib_id,
                        }
                    )
            data = sch._analyse(
                inputs.board,
                observed=(
                    footprints,
                    symbols,
                    list(dict.fromkeys(i.schematic.path.name for i in design.instances)),
                    inputs.schematic.name,
                ),
            )
            data["observed_footprints"] = actual
            data["observed_symbol_instances"] = symbols
            coverage = {
                "checked": [
                    "saved footprint UUID/path/reference",
                    "every reachable schematic instance path",
                    "board-only and excluded-symbol intent",
                ],
                "not_checked": data["not_checked"],
                "native": False,
            }
        else:
            board = Board(Document.parse(inputs.raw(inputs.board).decode("utf-8")), inputs.board)
            components, nets = parity.schematic_side(built)
            data = parity.report(
                str(inputs.board),
                components,
                nets,
                "captured complete schematic hierarchy",
                observed_board=board,
            )
            pcb = {p["ref"]: p for p in actual}
            pins = {(n.ref, n.pin): net.name for net in built.nets for n in net.nodes}
            details = []
            for part in parts:
                p = pcb.get(part["ref"])
                if p is None:
                    continue
                fields = {
                    k: {"schematic": v, "pcb": p["fields"].get(k)}
                    for k, v in part["fields"].items()
                    if k not in {"Reference", "Value", "Footprint"}
                    and (v or k in p["fields"])
                    and p["fields"].get(k) != v
                }
                marks = {
                    k: {"schematic": k in part["properties"], "pcb": k in p["attributes"]}
                    for k in ("dnp", "exclude_from_bom")
                    if (k in part["properties"]) != (k in p["attributes"])
                }
                expected = {
                    number: name for (ref, number), name in pins.items() if ref == part["ref"]
                }
                saved = {}
                for pad in p["pads"]:
                    saved.setdefault(pad["number"], set()).add(pad["net"])
                changed = [
                    {
                        "pin": number,
                        "schematic": expected.get(number),
                        "pcb": sorted(saved.get(number, set())),
                    }
                    for number in sorted(set(expected) | set(saved))
                    if saved.get(number, set()) != {expected.get(number)}
                    and number
                    and not (number not in expected and saved.get(number) == {""})
                ]
                if fields or marks or changed:
                    details.append(
                        {"ref": part["ref"], "fields": fields, "marks": marks, "pin_nets": changed}
                    )
            data.update(
                observed_footprints=actual,
                metadata_pin_differences=details,
                schematic_pins=len(pins),
                comparison_basis="reference and exact pin net names; UUID linking: "
                "project links inspect",
            )
            if details:
                data["status"] = "FAIL"
            coverage = {
                "checked": [
                    "saved component/value/footprint",
                    "full source fields and marks",
                    "every logical/physical pin net",
                    "custom copper connectivity",
                ],
                "not_checked": [
                    "Native schematic export/parity",
                    "Native editor memory",
                    "Library resolution and replacement availability",
                    "Circuit/layout/manufacturing correctness",
                ],
                "native": False,
                "legacy_diff_details_are_samples": True,
                "metadata_pin_differences_complete": True,
            }
        return data, coverage
