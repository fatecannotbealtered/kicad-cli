"""``kicad-cli sch sync-preview`` -- what "Update PCB from Schematic" would do.

The dialog behind F8 is the single most destructive button in KiCad: it can
delete and recreate every footprint on a board, taking the placement and the
routing with them. It is also GUI-only. So the honest thing a command line can
do is not to press it, but to say in advance exactly what it would report.

That is computable. The matching rule is in ``board_netlist_updater.cpp``: a
component matches a footprint when the footprint's ``path`` equals the sheet
path plus *any* of the component's unit uuids. Both sides of that comparison
are readable from files, so the preview needs no GUI at all.

Two traps are worth naming here, because both were live enough to nearly ship
a wrong answer:

*``pcb drc --schematic-parity`` is not this.* It is KiCad's own headless
schematic-versus-board check, it looks like the right tool, and it matches by
**reference designator** while the updater matches by **uuid path**. A board
whose links are broken but whose references are all correct -- exactly the
state this project's board was in -- reads as perfectly in sync. It is offered
here only as a cross-check and never counted.

*Zero added and zero deleted does not mean nothing happens.* "Update fields"
is ticked by default, so a symbol field the footprint lacks is written into
every affected footprint.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import envelope, netlist, sexpr

# The state DIALOG_UPDATE_PCB is in the moment it opens. Only two boxes are
# ticked in dupb.cpp, and nothing restores a previous session, so these are not
# a guess about what a user might have chosen -- they are the defaults every
# time the dialog is opened.
DEFAULTS: dict[str, bool] = {
    "lookup_by_timestamp": True,  # inverse of "Re-link footprints by reference"
    "replace_footprints": True,
    "update_fields": True,
    "delete_unused": False,
    "override_locks": False,
    "remove_extra_fields": False,
}

# Handled by the footprint-replacement path, not the field comparison, so
# counting them as missing fields would invent rows the dialog never shows.
FIELD_EXEMPT = {"Reference", "Value", "Footprint"}


def _board_footprints(board: Path) -> list[dict[str, Any]]:
    root = sexpr.parse(board.read_text(encoding="utf-8"))
    out = []
    for fp in sexpr.walk(root, "footprint"):
        attr = sexpr.child(fp, "attr") or []
        fields = {
            p[1]: (p[2] if len(p) > 2 and isinstance(p[2], str) else "")
            for p in sexpr.children(fp, "property")
            if len(p) >= 2 and isinstance(p[1], str)
        }
        out.append(
            {
                "ref": sexpr.prop(fp, "Reference"),
                "value": sexpr.prop(fp, "Value") or "",
                "fpid": sexpr.value(fp, 1),
                "path": netlist.normalise(sexpr.value(sexpr.child(fp, "path"), default="")),
                "fields": fields,
                "board_only": "board_only" in attr,
                # Locked footprints are reported as a warning and never removed,
                # whatever the checkboxes say.
                "locked": sexpr.value(sexpr.child(fp, "locked"), default="no") == "yes",
            }
        )
    return out


def _fpid_equivalent(board_id: str, sch_id: str) -> bool:
    """KiCad treats a library-less id as matching on the bare footprint name.

    A schematic that predates library nicknames stores ``SOT-23`` where the
    board stores ``Package_TO_SOT_SMD:SOT-23``. Comparing those as strings
    would report a footprint change for every part on such a board.
    """
    if ":" not in sch_id:
        return board_id.split(":", 1)[-1] == sch_id
    return board_id == sch_id


def _match(comps: list[dict], fps: list[dict], by_timestamp: bool) -> tuple[dict, list, list]:
    """Pair components with footprints exactly as the updater does."""
    pairs: dict[str, tuple[dict, dict]] = {}
    taken: set[int] = set()
    unmatched_comps = []

    if by_timestamp:
        by_path = {f["path"]: f for f in fps if f["path"]}
        for c in comps:
            hit = next((by_path[p] for p in c["paths"] if p in by_path), None)
            if hit is None or id(hit) in taken:
                unmatched_comps.append(c)
            else:
                taken.add(id(hit))
                pairs[c["ref"]] = (c, hit)
    else:
        by_ref = {str(f["ref"]).casefold(): f for f in fps}
        for c in comps:
            hit = by_ref.get(c["ref"].casefold())
            if hit is None or id(hit) in taken:
                unmatched_comps.append(c)
            else:
                taken.add(id(hit))
                pairs[c["ref"]] = (c, hit)

    orphans = [f for f in fps if id(f) not in taken]
    return pairs, unmatched_comps, orphans


def _rows(comps: list[dict], fps: list[dict], sw: dict[str, bool]) -> dict[str, Any]:
    pairs, unmatched, orphans = _match(comps, fps, sw["lookup_by_timestamp"])

    add = [{"ref": c["ref"], "footprint": c["fpid"]} for c in unmatched]

    remove, kept = [], []
    for f in orphans:
        if f["board_only"]:
            kept.append({"ref": f["ref"], "why": "marked board_only"})
        elif f["locked"] and not sw["override_locks"]:
            kept.append({"ref": f["ref"], "why": "locked; reported as a warning, never removed"})
        elif not sw["delete_unused"]:
            kept.append({"ref": f["ref"], "why": "'delete extra footprints' is not ticked"})
        else:
            remove.append({"ref": f["ref"], "footprint": f["fpid"]})

    change_fp, change_value, change_ref, field_rows = [], [], [], []
    for ref, (c, f) in pairs.items():
        if sw["replace_footprints"] and not _fpid_equivalent(str(f["fpid"]), c["fpid"]):
            change_fp.append({"ref": ref, "from": f["fpid"], "to": c["fpid"]})
        if c["value"] != f["value"]:
            change_value.append({"ref": ref, "from": f["value"], "to": c["value"]})
        if str(f["ref"]) != ref:
            change_ref.append({"from": f["ref"], "to": ref})
        if sw["update_fields"]:
            missing = sorted(
                k
                for k, v in c["fields"].items()
                if k not in FIELD_EXEMPT and v != "" and k not in f["fields"]
            )
            differs = sorted(
                k
                for k, v in c["fields"].items()
                if k not in FIELD_EXEMPT and k in f["fields"] and f["fields"][k] != v
            )
            if missing or differs:
                field_rows.append({"ref": ref, "missing": missing, "differs": differs})

    return {
        "add": add,
        "remove": remove,
        "not_removed": kept,
        "change_footprint": change_fp,
        "change_value": change_value,
        "change_reference": change_ref,
        "update_fields": field_rows,
    }


def _preflight(board: Path, comps: list[dict], fps: list[dict], stderr: str) -> list[dict]:
    """Conditions under which a count would be confidently wrong.

    Each of these produces a plausible number from a broken input, which is
    worse than producing nothing: an agent reading `add: 0` has no way to tell
    a synchronised board from a netlist that failed to export.
    """
    checks = []

    unannotated = sorted({c["ref"] for c in comps if "?" in c["ref"]})
    checks.append(
        {
            "id": "fully_annotated",
            "pass": not unannotated,
            "detail": "KiCad refuses to open the update dialog at all on a partly "
            "annotated schematic, so any count here would describe a dialog that "
            "never appears",
            "evidence": {"unannotated_refs": unannotated[:20]},
        }
    )

    warned = "annotat" in stderr.lower() or "批注" in stderr
    checks.append(
        {
            "id": "netlist_exported_cleanly",
            "pass": bool(comps) and not warned,
            "detail": "the netlist export writes a well-formed file and exits 0 even "
            "when it has complaints; the only signal is on stderr",
            "evidence": {"components": len(comps), "stderr": stderr[-300:] or "(quiet)"},
        }
    )

    seen: dict[tuple[str, str], int] = {}
    for c in comps:
        seen[(c["sheet"], c["ref"])] = seen.get((c["sheet"], c["ref"]), 0) + 1
    dupes = sorted(f"{r} on sheet {s}" for (s, r), n in seen.items() if n > 1)
    checks.append(
        {
            "id": "no_duplicate_references",
            "pass": not dupes,
            "detail": "the exporter silently folds same-sheet duplicate references into "
            "one component, so a duplicate makes the component count itself unreliable",
            "evidence": {"duplicates": dupes[:20]},
        }
    )

    locks = sorted(p.name for p in board.parent.glob("~*.lck"))
    checks.append(
        {
            "id": "board_not_open_elsewhere",
            "pass": not locks,
            "detail": "a lock file means KiCad has this project open; the board on disk "
            "may be behind what is in the editor's memory, and saving from the GUI "
            "would overwrite anything written here",
            "evidence": {"lock_files": locks},
        }
    )

    pathless = [f["ref"] for f in fps if not f["path"] and not f["board_only"]]
    checks.append(
        {
            "id": "no_unlinked_footprints",
            "pass": not pathless,
            "detail": "a footprint with no path cannot be matched by uuid, so the "
            "default update treats it as a new part: it is added, and deleted first if "
            "'delete extra footprints' is ticked",
            "evidence": {"count": len(pathless), "sample": pathless[:10]},
        }
    )
    return checks


def _findings(preview: dict, matrix: dict, fps: list[dict], comps: list[dict]) -> list[dict]:
    out = []
    if preview["add"]:
        out.append(
            {
                "id": "S1-would-add",
                "severity": "error",
                "title": "the update would add footprints that are already on the board",
                "detail": f"{len(preview['add'])} components do not match any footprint by "
                "uuid path. KiCad would place fresh copies at the origin, and with "
                "'delete extra footprints' ticked it deletes the existing ones first -- "
                "placement and routing go with them.",
                "evidence": {
                    "count": len(preview["add"]),
                    "sample": [x["ref"] for x in preview["add"][:10]],
                    "with_delete_extra": matrix.get("relink=off, delete-extra=on", {}),
                },
                "confidence": "measured",
                "fix": "run `kicad-cli sch relink` to restore the uuid links first",
            }
        )
    if preview["update_fields"]:
        names = sorted({n for r in preview["update_fields"] for n in r["missing"] + r["differs"]})
        out.append(
            {
                "id": "S2-field-writes",
                "severity": "warn",
                "title": "the update would rewrite fields on footprints",
                "detail": "'Update fields' is ticked by default, so "
                f"{len(preview['update_fields'])} "
                f"footprints would gain or change fields ({', '.join(names)}). Nothing is "
                "added or deleted, but the board file changes broadly -- this is why "
                "'zero added, zero deleted' is not the same as 'nothing happens'.",
                "evidence": {
                    "footprints": len(preview["update_fields"]),
                    "fields": names,
                    "sample": preview["update_fields"][:5],
                },
                "confidence": "measured",
                "fix": "decide whether these fields belong on the board; untick "
                "'Update fields' in the dialog to keep the board as it is",
            }
        )
    sch_names = {k for c in comps for k, v in c["fields"].items() if v != ""}
    board_only_fields: dict[str, int] = {}
    for f in fps:
        for k in f["fields"]:
            if k not in FIELD_EXEMPT and k not in sch_names:
                board_only_fields[k] = board_only_fields.get(k, 0) + 1
    if board_only_fields:
        out.append(
            {
                "id": "S3-board-only-fields",
                "severity": "info",
                "title": "some fields exist only on the board",
                "detail": "The field comparison is one-directional: KiCad checks that the "
                "footprint has what the symbol has, never the reverse. These survive a "
                "normal update and are deleted only if 'remove extra fields' is ticked.",
                "evidence": {"fields": board_only_fields},
                "confidence": "measured",
                "fix": "if they matter, add them to the schematic symbols so they are "
                "not at the mercy of one checkbox",
            }
        )
    if preview["change_reference"]:
        out.append(
            {
                "id": "S4-renumbered",
                "severity": "warn",
                "title": "references differ between the board and the schematic",
                "detail": f"{len(preview['change_reference'])} footprints are matched by uuid "
                "but carry a different reference. The update renumbers the board to follow "
                "the schematic; nothing is added or deleted.",
                "evidence": {"sample": preview["change_reference"][:10]},
                "confidence": "measured",
                "fix": "confirm the schematic is the side that is right before syncing",
            }
        )
    return out


def preview(args: dict[str, Any]) -> None:
    board_arg = args.get("board")
    if not board_arg:
        envelope.fail("E_USAGE", "--board is required", {"param": "board"})
    board = Path(str(board_arg)).expanduser()
    if not board.exists():
        envelope.fail("E_NOT_FOUND", "board file does not exist", {"path": str(board)})

    schematic = (
        Path(str(args["schematic"])) if args.get("schematic") else board.with_suffix(".kicad_sch")
    )
    node, stderr = netlist.export(schematic)
    comps = netlist.components(node)
    fps = _board_footprints(board)

    checks = _preflight(board, comps, fps, stderr)
    blocking = [
        c
        for c in checks
        if not c["pass"]
        and c["id"] in {"fully_annotated", "netlist_exported_cleanly", "no_duplicate_references"}
    ]
    if blocking:
        envelope.fail(
            "E_VALIDATION",
            "the inputs are not in a state where a count would mean anything",
            {
                "failed": blocking,
                "why": "a number produced from these inputs would look just like a "
                "number from a healthy project; refusing is the only honest answer",
            },
        )

    rows = _rows(comps, fps, DEFAULTS)

    # The dialog's answer depends on two checkboxes more than anything else, and
    # "did you tick the box" is exactly the sort of thing that gets lost between
    # a report and the person reading it. So give all four.
    matrix = {}
    for relink in (False, True):
        for delete in (False, True):
            sw = dict(DEFAULTS, lookup_by_timestamp=not relink, delete_unused=delete)
            r = _rows(comps, fps, sw)
            key = f"relink={'on' if relink else 'off'}, delete-extra={'on' if delete else 'off'}"
            matrix[key] = {"add": len(r["add"]), "remove": len(r["remove"])}

    envelope.ok(
        {
            "board": str(board),
            "schematic": str(schematic),
            "components": len(comps),
            "footprints": len(fps),
            "preflight": checks,
            "assumptions": {
                **DEFAULTS,
                "source": "the state DIALOG_UPDATE_PCB opens in; only 'update footprints' "
                "and 'update fields' are ticked, and no previous session is restored",
            },
            "counts": {
                "add": len(rows["add"]),
                "remove": len(rows["remove"]),
                "change_footprint": len(rows["change_footprint"]),
                "change_value": len(rows["change_value"]),
                "change_reference": len(rows["change_reference"]),
                "update_fields": len(rows["update_fields"]),
            },
            "matrix": matrix,
            "dialog_preview": rows,
            "findings": _findings(rows, matrix, fps, comps),
            "status": "CLEAN" if not (rows["add"] or rows["remove"]) else "DESTRUCTIVE",
            "not_checked": [
                "the dialog itself was never opened; every row here is computed from "
                "KiCad's matching rule applied to the same netlist KiCad would use",
                "whether the library entries for any added or changed footprint resolve "
                "on this machine -- a footprint KiCad cannot load produces an error row "
                "instead of an add row"
                + (
                    " (nothing is being added here, so it does not arise)"
                    if not rows["add"]
                    else ""
                ),
                "design variants: a project using them can map one component to several "
                "footprints, and that branch has never been exercised",
                "side effects beyond the dialog's list: removing orphaned tracks, "
                "dropping unused nets, moving group membership",
                "`pcb drc --schematic-parity` was deliberately not used as the source: "
                "it matches by reference designator, so it is blind to exactly the "
                "broken-link case this command exists to catch",
            ],
        }
    )
