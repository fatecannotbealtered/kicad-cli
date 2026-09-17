"""``kicad-cli sch ...`` -- schematic-side checks.

These parse the files directly. No KiCad install is needed and no SWIG binding
is involved, which is the point: the bindings are removed in KiCad 11, and a
check that reads the file format keeps working.
"""

from __future__ import annotations

import difflib
import time
from pathlib import Path
from typing import Any

from .. import envelope, kicad_env, netlist, sexpr


def _find_schematics(board: Path) -> tuple[list[Path], str | None]:
    """Sheets reachable from this project's root schematic, in hierarchy order.

    Globbing the directory looks equivalent and is not: a folder can hold more
    than one independent design. KiCad's own ecc83 demo ships ecc83-pp and
    ecc83-pp_v2 side by side, and merging them invents a duplicate reference
    for almost every part. We follow the hierarchy instead, starting from the
    schematic that shares the project's name.
    """
    root_sch = board.with_suffix(".kicad_sch")
    if not root_sch.exists():
        return [], None
    seen: list[Path] = []
    queue = [root_sch]
    while queue:
        cur = queue.pop(0)
        if cur in seen or not cur.exists():
            continue
        seen.append(cur)
        try:
            node = sexpr.parse(cur.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for sheet in sexpr.walk(node, "sheet"):
            name = sexpr.prop(sheet, "Sheetfile") or sexpr.prop(sheet, "Sheet file")
            if name:
                queue.append(cur.parent / name)
    return seen, root_sch.name


def _load(path: Path) -> sexpr.Node:
    try:
        return sexpr.parse(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        envelope.fail("E_IO", f"could not read {path.name}: {exc}", {"path": str(path)})
        raise AssertionError("unreachable") from exc  # pragma: no cover


def _footprint_path(sheet_path: str, symbol_uuid: str) -> str:
    """The value KiCad writes into a footprint's ``path`` field.

    A symbol instance records the *sheet* path, which begins with the root
    schematic's own uuid. The footprint path drops that root segment and
    appends the symbol's uuid instead. Guessing this wrong is not a cosmetic
    error: writing a plausible-but-wrong path would silently associate parts
    with the wrong symbols, which is worse than leaving them unlinked.

    Verified against five KiCad-generated demo projects (complex_hierarchy,
    ecc83, interf_u, kit-dev-coldfire-xilinx_5213, cm5_minima): 351 of 351
    footprint paths reproduce exactly, across both flat and hierarchical
    designs.
    """
    segments = [x for x in sheet_path.split("/") if x]
    return "/" + "/".join(segments[1:] + [symbol_uuid])


def _board_footprints(board: Path) -> list[dict[str, Any]]:
    """Reference, own uuid and schematic path for every footprint on the board."""
    root = _load(board)
    out = []
    for fp in sexpr.walk(root, "footprint"):
        out.append(
            {
                "ref": sexpr.prop(fp, "Reference"),
                "uuid": sexpr.value(sexpr.child(fp, "uuid")),
                # The link back to the schematic symbol. Empty means unlinked.
                "path": sexpr.value(sexpr.child(fp, "path"), default=""),
                "footprint": sexpr.value(fp, 1),
                # KiCad's own marker for a footprint that is meant to live only
                # on the board: fiducials, mounting holes, logos, silkscreen
                # art. Counting these as broken links buries the real ones --
                # on the cm5_minima demo it was 27 of 28 "failures".
                "board_only": "board_only" in (sexpr.child(fp, "attr") or []),
                # KiCad's placeholder for a footprint that was never annotated.
                # It has no symbol to link to, so it is neither a broken link
                # nor a healthy one -- it is a decision the designer owes.
                "unannotated": "**" in str(sexpr.prop(fp, "Reference") or ""),
            }
        )
    return out


def _schematic_symbols(board: Path) -> tuple[list[dict[str, Any]], list[str], str | None]:
    """Every placed symbol instance across all sheets, with its full instance path.

    A symbol carries its own uuid; the sheet it sits on contributes the prefix.
    KiCad stores that prefix per instance, so we read it rather than trying to
    reconstruct the sheet hierarchy ourselves.
    """
    symbols: list[dict[str, Any]] = []
    files: list[str] = []
    sheets, root_name = _find_schematics(board)
    for sch in sheets:
        files.append(sch.name)
        root = _load(sch)
        for sym in sexpr.walk(root, "symbol"):
            inst = sexpr.child(sym, "instances")
            if inst is None:
                continue  # library definition inside lib_symbols, not a placement
            uuid = sexpr.value(sexpr.child(sym, "uuid"))
            for project in sexpr.children(inst, "project"):
                for p in sexpr.children(project, "path"):
                    sheet_path = sexpr.value(p, 1, "")
                    ref = sexpr.value(sexpr.child(p, "reference"))
                    unit = sexpr.value(sexpr.child(p, "unit"), default="1")
                    symbols.append(
                        {
                            "ref": ref,
                            "unit": unit,
                            "uuid": uuid,
                            "sheet_path": sheet_path,
                            "full_path": _footprint_path(sheet_path, uuid),
                            "sheet_file": sch.name,
                            "lib_id": sexpr.value(sexpr.child(sym, "lib_id")),
                        }
                    )
    return symbols, files, root_name


def _board_path(args: dict[str, Any]) -> Path:
    board_arg = args.get("board")
    if not board_arg:
        envelope.fail("E_USAGE", "--board is required", {"param": "board"})
    board = Path(str(board_arg)).expanduser()
    if not board.exists():
        envelope.fail("E_NOT_FOUND", "board file does not exist", {"path": str(board)})
    return board


def _analyse(board: Path) -> dict[str, Any]:
    """The whole of ``sch link``, as data. ``relink`` reads the same report it
    prints, so the thing it acts on is exactly the thing a human was shown."""
    footprints = _board_footprints(board)
    symbols, sheet_files, root_name = _schematic_symbols(board)

    if not symbols:
        envelope.fail(
            "E_NOT_FOUND",
            "no schematic symbols found for this project",
            {
                "expected_root": str(board.with_suffix(".kicad_sch")),
                "hint": "sch link follows the sheet hierarchy from <project>.kicad_sch; "
                "point --board at the board inside its own project directory",
            },
        )

    by_path = {s["full_path"]: s for s in symbols if s["full_path"]}
    # A reference starting with '#' is KiCad's marker for a symbol that exists
    # only on the schematic: power ports, PWR_FLAG, and similar. They are meant
    # to have no footprint, so counting them as orphans would bury the real
    # ones. On this project that is 77 of 77 -- the whole finding was noise.
    virtual = [s for s in symbols if str(s["ref"]).startswith("#")]
    placed = [s for s in symbols if not str(s["ref"]).startswith("#")]

    # Multi-unit parts legitimately repeat a reference, once per unit, so a
    # duplicate only means something when the unit repeats too.
    by_ref_unit: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for s in placed:
        by_ref_unit.setdefault((s["ref"], str(s["unit"])), []).append(s)

    linked, unlinked, dangling, mismatched = [], [], [], []
    # Three different reasons a footprint can carry no path, with three
    # different answers. Reporting them as one number was this check's own
    # bug: on KiCad's cm5_minima demo it turned 1 real question into 28.
    board_only = [fp for fp in footprints if fp["board_only"] and not fp["path"]]
    unannotated = [
        {"ref": fp["ref"], "footprint": fp["footprint"]}
        for fp in footprints
        if fp["unannotated"] and not fp["board_only"] and not fp["path"]
    ]
    skip = {
        id(fp) for fp in footprints if not fp["path"] and (fp["board_only"] or fp["unannotated"])
    }
    for fp in footprints:
        if id(fp) in skip:
            continue
        path = fp["path"]
        if not path:
            unlinked.append({"ref": fp["ref"], "footprint": fp["footprint"]})
            continue
        target = by_path.get(path)
        if target is None:
            dangling.append({"ref": fp["ref"], "path": path})
        elif target["ref"] != fp["ref"]:
            mismatched.append({"ref": fp["ref"], "path": path, "schematic_ref": target["ref"]})
        else:
            linked.append({"ref": fp["ref"], "path": path})

    board_refs = {fp["ref"] for fp in footprints}
    orphan_symbols = [
        {"ref": s["ref"], "unit": s["unit"], "sheet_file": s["sheet_file"], "lib_id": s["lib_id"]}
        for s in placed
        if s["ref"] not in board_refs
    ]
    duplicate_refs = sorted(
        f"{ref} (unit {unit})" for (ref, unit), items in by_ref_unit.items() if len(items) > 1
    )
    multi_unit = sorted(
        {s["ref"] for s in placed if len([x for x in placed if x["ref"] == s["ref"]]) > 1}
    )

    total = len(footprints) - len(board_only) - len(unannotated)
    at_risk = len(unlinked) + len(dangling) + len(mismatched)

    findings = []
    if unlinked:
        findings.append(
            {
                "id": "L1-unlinked",
                "severity": "error" if len(unlinked) > total * 0.1 else "warn",
                "title": "footprints carry no link back to a schematic symbol",
                "detail": f"{len(unlinked)} of {total} footprints have an empty path field. "
                "KiCad then has nothing but the reference designator to match on, so "
                "'Update PCB from Schematic' treats them as unknown parts: it deletes "
                "them and adds fresh ones, taking the placement and routing with them.",
                "evidence": {
                    "unlinked_count": len(unlinked),
                    "total": total,
                    "sample": [x["ref"] for x in unlinked[:10]],
                },
                "confidence": "measured",
                "fix": "run `kicad-cli sch relink` to write the symbol uuids back, then "
                "open the board once in KiCad and confirm 'Update PCB from Schematic' "
                "reports zero additions and zero deletions",
            }
        )
    if dangling:
        findings.append(
            {
                "id": "L2-dangling",
                "severity": "error",
                "title": "footprint links point at symbols that no longer exist",
                "detail": f"{len(dangling)} footprints reference a schematic path that "
                "does not resolve. The symbol was deleted or the sheet was restructured.",
                "evidence": {"sample": dangling[:10]},
                "confidence": "measured",
                "fix": "decide per part whether it should still be on the board; "
                "`sch relink` will not silently repoint a dangling link",
            }
        )
    if mismatched:
        findings.append(
            {
                "id": "L3-mismatched",
                "severity": "error",
                "title": "footprint link resolves to a different reference designator",
                "detail": f"{len(mismatched)} footprints link to a symbol whose reference "
                "differs. Either the board or the schematic was renumbered alone.",
                "evidence": {"sample": mismatched[:10]},
                "confidence": "measured",
                "fix": "reconcile the renumbering before any further sync; this is the "
                "state in which an update can swap two parts without warning",
            }
        )
    if orphan_symbols:
        findings.append(
            {
                "id": "L4-orphan-symbol",
                "severity": "warn",
                "title": "schematic symbols with no footprint on the board",
                "detail": f"{len(orphan_symbols)} placed symbols have no matching "
                "reference on the board. Expected for power flags and other "
                "virtual parts; a real part here means the board is incomplete.",
                "evidence": {"sample": orphan_symbols[:10]},
                "confidence": "measured",
                "fix": "check each one; virtual symbols are fine, real parts are not",
            }
        )
    if unannotated:
        findings.append(
            {
                "id": "L6-unannotated",
                "severity": "info",
                "title": "footprints placed on the board without ever being annotated",
                "detail": f"{len(unannotated)} footprints still carry KiCad's 'REF**' "
                "placeholder. They have no schematic symbol to link to, so `sch relink` "
                "will not touch them. Usually decoration -- logos, markings -- but "
                "'Update PCB from Schematic' will delete them if 'delete footprints with "
                "no symbols' is on. Mark them board_only to make that intent explicit.",
                "evidence": {"sample": unannotated[:10]},
                "confidence": "measured",
                "fix": "set the board_only attribute on decoration, or annotate the "
                "footprint and give it a symbol",
            }
        )
    if duplicate_refs:
        findings.append(
            {
                "id": "L5-duplicate-ref",
                "severity": "error",
                "title": "the same reference designator appears on more than one symbol",
                "detail": "Reference-based matching cannot be trusted while duplicates "
                "exist, which makes both linking and any schematic sync ambiguous.",
                "evidence": {"refs": duplicate_refs[:20]},
                "confidence": "measured",
                "fix": "renumber so every placed symbol has a unique reference",
            }
        )

    return {
        "board": str(board),
        "schematic_files": sheet_files,
        "footprints": len(footprints),
        "root_schematic": root_name,
        "footprints_from_schematic": total,
        "board_only": len(board_only),
        "unannotated": len(unannotated),
        "symbols": len(symbols),
        "linked": len(linked),
        "unlinked": len(unlinked),
        "dangling": len(dangling),
        "mismatched": len(mismatched),
        "orphan_symbols": len(orphan_symbols),
        "virtual_symbols": len(virtual),
        "multi_unit_parts": multi_unit,
        "at_risk": at_risk,
        "status": "PASS" if at_risk == 0 and not duplicate_refs else "FAIL",
        "findings": findings,
        "details": {
            "unlinked": unlinked,
            "dangling": dangling,
            "mismatched": mismatched,
            "orphan_symbols": orphan_symbols,
            "unannotated": unannotated,
        },
        # Saying what was not looked at is part of the answer. An agent that
        # only sees "PASS" will otherwise read it as "everything is fine".
        "not_checked": [
            "whether the linked paths match what eeschema itself would generate "
            "(only KiCad can confirm that; open the board and run Update PCB from "
            "Schematic to verify zero additions and deletions)",
            "whether each unit of a multi-unit part is placed "
            f"(found {len(multi_unit)} multi-unit parts: {', '.join(multi_unit) or 'none'})",
            "net connectivity - see `board parity`",
        ],
    }


def link(args: dict[str, Any]) -> None:
    envelope.ok(_analyse(_board_path(args)))


def _netlist_paths(board: Path) -> dict[str, dict[str, Any]]:
    """Ask KiCad for the path it will look for, instead of reconstructing it.

    ``sch link`` derives a footprint path from the schematic files alone, and
    that derivation is verified. Writing is a different bar: the netlist
    exporter feeds the same updater that will read this field, so taking the
    value from there removes a whole class of ways to be subtly wrong.

    One fact worth stating, because it is not obvious and it decides what is
    safe to write: a multi-unit part exports *several* uuids, one per unit --
    ``(tstamps "uuid-a" "uuid-b" "uuid-c")`` -- and the updater tries each one.
    Across KiCad's own 16 demo boards, all 3835 linked footprints carry a path
    built from the sheet path plus one of that component's unit uuids, and
    which unit it happens to be is not predictable from the files. So there is
    nothing to choose here: any unit is a correct answer.
    """
    node, stderr = netlist.export(board.with_suffix(".kicad_sch"))
    if "annotat" in stderr.lower() or "批注" in stderr:
        envelope.fail(
            "E_VALIDATION",
            "the schematic is not fully annotated; KiCad would refuse the update too",
            {"stderr": stderr[-400:]},
        )

    return {
        c["ref"]: {"path": c["paths"][0], "units": len(c["paths"]), "accepted": c["paths"]}
        for c in netlist.components(node)
        if c["paths"]
    }


def _read_raw(path: Path) -> str:
    """Read without translating line endings, so a rewrite preserves them."""
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def _write_raw(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _insert_paths(text: str, wanted: dict[str, str]) -> tuple[str, list[str], list[str]]:
    """Add a ``(path ...)`` line to each named footprint, changing nothing else.

    The obvious implementation is to load the board with pcbnew and save it.
    That was the first implementation, and it was wrong: saving a board written
    by KiCad 9 with KiCad 10 migrates the file format -- the version stamp
    moves, ``tenting`` is restructured, ``covering`` appears. The user asked to
    restore a link, not to upgrade their file, and a write command has no
    business doing the larger thing quietly. So the edit is made in text.

    Placement follows KiCad's own ordering, which is
    ``property* path sheetname sheetfile attr``, so the line is put in front of
    whichever of those comes first. ``sheetname`` and ``sheetfile`` are left for
    KiCad to fill in; they are display fields, and every extra field written by
    hand is another chance to be wrong about a format we do not own.
    """
    lines = text.split("\n")
    eol = "\r" if lines and lines[0].endswith("\r") else ""
    out: list[str] = []
    inserted: list[str] = []

    depth = 0  # tab depth of the footprint currently open, -1 when outside one
    indent = -1
    ref: str | None = None
    anchor: int | None = None  # index into ``out`` where the line should go
    uuid_at: int | None = None
    pending: list[tuple[int, str]] = []

    def tabs(line: str) -> int:
        return len(line) - len(line.lstrip("\t"))

    for line in lines:
        stripped = line.strip()
        if indent < 0 and stripped.startswith("(footprint "):
            indent = tabs(line)
            ref, anchor, uuid_at = None, None, None
            out.append(line)
            continue
        if indent >= 0:
            depth = tabs(line)
            if depth == indent + 1:
                if stripped.startswith('(property "Reference" "'):
                    ref = stripped.split('"')[3]
                elif uuid_at is None and stripped.startswith('(uuid "'):
                    uuid_at = len(out)
                elif anchor is None and stripped.startswith(("(sheetname", "(sheetfile", "(attr ")):
                    anchor = len(out)
            elif depth == indent and stripped == ")":
                at = anchor if anchor is not None else uuid_at
                if ref in wanted and at is not None:
                    pending.append((at, "\t" * (indent + 1) + f'(path "{wanted[ref]}")' + eol))
                    inserted.append(str(ref))
                indent = -1
        out.append(line)

    # Splice from the end so the earlier offsets stay valid.
    for at, line in sorted(pending, key=lambda p: -p[0]):
        out.insert(at, line)

    missed = sorted(set(wanted) - set(inserted))
    return "\n".join(out), inserted, missed


def _only_added_paths(before: str, after: str) -> tuple[bool, list[str]]:
    """Confirm the file grew by nothing but ``(path ...)`` lines.

    A plain load-and-save round trip through pcbnew is byte-identical on this
    board, which is what makes this check meaningful: every line of difference
    after a relink has to be one we asked for, and anything else is a reason to
    put the backup back.
    """
    diff = [
        line
        for line in difflib.unified_diff(before.splitlines(), after.splitlines(), n=0, lineterm="")
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    ]
    unexpected = [d for d in diff if d.startswith("-") or "(path " not in d]
    return not unexpected, unexpected[:20]


def _blockers(report: dict[str, Any]) -> list[dict[str, Any]]:
    """States in which a reference designator is not a trustworthy handle.

    An unlinked footprint has nothing but its reference to be identified by, so
    if references are ambiguous there is no safe write -- only a plausible one.
    """
    out = []
    if report["dangling"]:
        out.append(
            {
                "what": "dangling links",
                "count": report["dangling"],
                "why": "some footprints already point at symbols that do not resolve; "
                "repairing those is a design decision, not a mechanical one",
            }
        )
    if report["mismatched"]:
        out.append(
            {
                "what": "mismatched links",
                "count": report["mismatched"],
                "why": "a link resolves to a different reference, so the board and the "
                "schematic disagree about which part is which",
            }
        )
    dup = [f for f in report["findings"] if f["id"] == "L5-duplicate-ref"]
    if dup:
        out.append(
            {
                "what": "duplicate reference designators",
                "count": len(dup[0]["evidence"]["refs"]),
                "why": "matching by reference is ambiguous while duplicates exist",
            }
        )
    return out


def relink(args: dict[str, Any]) -> None:
    board = _board_path(args)
    report = _analyse(board)

    blockers = _blockers(report)
    if blockers:
        envelope.fail(
            "E_CONFLICT",
            "the board is not in a state where relinking by reference is safe",
            {"blockers": blockers, "fix": "resolve these in KiCad first, then re-run"},
        )

    targets = report["details"]["unlinked"]
    if not targets:
        envelope.ok(
            {
                "board": str(board),
                "written": [],
                "counts": {"written": 0, "already_linked": report["linked"]},
                "status": "NOOP",
                "note": "every footprint that should carry a schematic link already does",
            }
        )

    available = _netlist_paths(board)
    plan, unresolved = [], []
    for fp in targets:
        entry = available.get(fp["ref"])
        if entry is None:
            unresolved.append(fp["ref"])
        else:
            plan.append({"ref": fp["ref"], "path": entry["path"], "units": entry["units"]})
    if unresolved:
        envelope.fail(
            "E_CONFLICT",
            "some footprints have no component of that reference in the schematic",
            {
                "unresolved": unresolved[:20],
                "count": len(unresolved),
                "why": "these are on the board but not in the netlist, so there is no "
                "symbol to link them to; relink writes nothing rather than fix part of "
                "the board and leave the rest inconsistent",
            },
        )

    multi = [p["ref"] for p in plan if p["units"] > 1]
    preview = {
        "board": str(board),
        "will_write": len(plan),
        "leaves_alone": report["linked"] + report["board_only"] + report["unannotated"],
        "sample": plan[:10],
        "multi_unit_parts": multi,
        "will": f"write a schematic uuid into the path field of {len(plan)} footprints; "
        "no geometry, net or placement data is touched",
    }
    envelope.check_confirm(args.get("confirm"), f"sch relink:{board.name}", preview)

    before = _read_raw(board)
    backup = board.with_name(f"{board.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    _write_raw(backup, before)

    wanted = {p["ref"]: p["path"] for p in plan}
    baseline = kicad_env.run_payload("verify_links", ["--board", str(board)])
    if not baseline.get("ok"):
        err = baseline.get("error") or {}
        envelope.fail(
            err.get("code", "E_UNKNOWN"),
            "could not read the board with KiCad before editing it",
            {**(err.get("details") or {}), "nothing_written": True},
        )

    after, inserted, missed = _insert_paths(before, wanted)
    if missed:
        envelope.fail(
            "E_INTEGRITY",
            "could not find where to write the link for every footprint; nothing written",
            {"missed": missed[:20], "count": len(missed)},
        )
    _write_raw(board, after)

    def put_back(code: str, message: str, details: dict[str, Any]) -> None:
        _write_raw(board, before)
        envelope.fail(code, message, {**details, "board_restored": True, "backup": str(backup)})

    clean, unexpected = _only_added_paths(before, after)
    if not clean:
        put_back(
            "E_INTEGRITY",
            "the edit touched more than the link fields; the board has been put back",
            {"unexpected_diff": unexpected},
        )

    # KiCad's own parser is the judge of what we wrote. Reading the file back
    # with pcbnew both proves it still parses and proves the links are the ones
    # we planned -- our own parser agreeing with itself would prove nothing.
    check = kicad_env.run_payload("verify_links", ["--board", str(board)])
    if not check.get("ok"):
        err = check.get("error") or {}
        put_back(
            "E_INTEGRITY",
            "KiCad could not read the board after the edit; the board has been put back",
            err.get("details") or {"payload_error": err.get("message")},
        )

    seen = check["data"]["links"]
    wrong = {
        r: {"wanted": p, "kicad_read": seen.get(r)} for r, p in wanted.items() if seen.get(r) != p
    }
    if wrong:
        put_back(
            "E_INTEGRITY",
            "KiCad read back a different link than we wrote; the board has been put back",
            {"disagreements": dict(list(wrong.items())[:10]), "count": len(wrong)},
        )
    census_same = check["data"]["census"] == baseline["data"]["census"]
    if not census_same:
        put_back(
            "E_INTEGRITY",
            "the object counts changed; the board has been put back",
            {"before": baseline["data"]["census"], "after": check["data"]["census"]},
        )

    recheck = _analyse(board)
    envelope.ok(
        {
            "board": str(board),
            "backup": str(backup),
            "written": [{"ref": r, "path": wanted[r]} for r in inserted][:50],
            "counts": {"written": len(inserted), "already_linked": report["linked"]},
            "multi_unit_parts": multi,
            "verified": {
                "diff_contains_only_link_fields": clean,
                "object_counts_unchanged": census_same,
                "kicad_reads_back_what_we_wrote": True,
                "census": check["data"]["census"],
                "link_status_after": recheck["status"],
                "unlinked_after": recheck["unlinked"],
            },
            "status": "PASS" if recheck["status"] == "PASS" else "PARTIAL",
            # This tool cannot run the updater, so it must not claim the outcome
            # the user actually cares about. Saying so is the honest end of the
            # job, not a disclaimer bolted on afterwards.
            "not_checked": [
                "whether KiCad's own 'Update PCB from Schematic' now reports zero "
                "additions and zero deletions -- that dialog is the only place this can "
                "be confirmed, and it should be opened before the result is trusted",
                "footprints marked board_only or left unannotated were not touched; "
                "they have no symbol to link to",
                "the sheetname and sheetfile display fields were not written; KiCad "
                "fills those in itself the next time it saves the board",
            ],
        }
    )
