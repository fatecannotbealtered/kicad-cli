"""``kicad-cli sch audit`` -- what the electrical rules check is not telling you.

A clean ERC is easy to misread. It means "nothing the configured rules object
to", and four rules are switched off in a stock KiCad project before anyone
touches the settings. One of those, ``single_global_label``, fires when a
global label appears exactly once in the whole design -- which is what a typo
in a label looks like. On a design whose sheets talk to each other mainly
through global labels, shipping with that rule off is not a neutral default.

So this command does not stop at reading the configuration. It copies the
project somewhere disposable, turns the silenced rules back on, and runs ERC
again. The answer to "what is being hidden" is then a measurement rather than
an inference.
"""

from __future__ import annotations

import fnmatch
import json
import re
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .. import envelope, kicad_env, netlist, sexpr

# The rules a freshly created KiCad 10 project has switched off. Measured, not
# assumed: 9 of the 17 projects KiCad ships have exactly this set and no other
# combination appears more than twice, so it is the shipped default rather than
# anyone's choice. The distinction matters -- "someone turned this off" and
# "this arrives off" call for different conversations.
KICAD_DEFAULT_IGNORED = {
    "footprint_filter",
    "four_way_junction",
    "simulation_model_issue",
    "single_global_label",
}

# Why an operator should care about each rule that commonly arrives disabled.
WHY_IT_MATTERS = {
    "single_global_label": "a global label used exactly once connects to nothing; "
    "this is what a mistyped label looks like, and it is invisible on the canvas",
    "four_way_junction": "four wires meeting at a point is ambiguous to read and is a "
    "common way for two nets to be joined by accident",
    "footprint_filter": "the symbol's own filter says this footprint does not belong to this part",
    "simulation_model_issue": "only affects simulation, not the board",
    "unconnected_wire_endpoint": "a wire that ends in mid-air usually means an edit was "
    "left half-finished",
    "multiple_net_names": "one net carrying several names is how two nets silently become one",
    "pin_to_pin": "pin type conflicts, e.g. two outputs driving each other",
    "footprint_link_issues": "the symbol points at a footprint that cannot be resolved",
}


def _run_erc(schematic: Path, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Run ERC, retrying the launch the way ``netlist.export`` does.

    Same transient as the netlist export -- a process launch on Windows fails
    now and then with an empty stderr and no output file, for reasons that have
    nothing to do with the schematic. It went unhandled here, and `sch audit`
    runs ERC twice per call, so it had two chances per invocation to report a
    healthy schematic as E_IO. It flaked one full test run in three that way.

    If all three attempts fail the error carries each one's returncode and
    stderr, because a bare "could not run ERC" gives whoever hits it next
    nothing to go on.
    """
    exe = kicad_env.find_official_cli()
    attempts: list[dict[str, Any]] = []
    for _ in range(3):
        tmp = Path(tempfile.mkdtemp(prefix="kicadcli-erc-"))
        out = tmp / "erc.json"
        try:
            proc = subprocess.run(  # noqa: S603
                [
                    str(exe),
                    "sch",
                    "erc",
                    "--severity-all",
                    "--format",
                    "json",
                    "-o",
                    str(out),
                    str(schematic),
                ],
                capture_output=True,
                timeout=1800,
                env=env or netlist._isolated_env(),
            )
        except subprocess.TimeoutExpired:
            shutil.rmtree(tmp, ignore_errors=True)
            envelope.fail("E_TIMEOUT", "ERC timed out")
        if out.exists():
            try:
                return json.loads(out.read_text(encoding="utf-8"))
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        attempts.append(
            {
                "returncode": proc.returncode,
                "stderr": proc.stderr.decode("utf-8", "replace").strip()[-300:],
            }
        )
        shutil.rmtree(tmp, ignore_errors=True)
        time.sleep(0.4)

    envelope.fail(
        "E_IO",
        "KiCad could not run ERC on this schematic",
        {"schematic": str(schematic), "attempts": attempts},
    )
    raise AssertionError("unreachable")  # pragma: no cover


def _sample(rows: list[dict[str, Any]], per_type: int = 4) -> list[dict[str, Any]]:
    """A few of each kind, rather than the first N of whatever came back.

    Taking the head of the list looks harmless and is not: on this project 91
    benign footprint-filter rows arrived before 6 single-global-label rows, so
    a head-of-list sample hid the finding that mattered behind the one that did
    not. Rarity is a reason to show something, not to drop it.
    """
    seen: Counter[str] = Counter()
    out = []
    for r in sorted(rows, key=lambda x: str(x.get("type"))):
        key = str(r.get("type"))
        if seen[key] < per_type:
            seen[key] += 1
            out.append(r)
    return out


def _patterns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse repeated messages into the shape they actually have.

    Ninety-one violations reading "assigned footprint does not match the filter"
    are one decision with ninety-one consequences. Reporting them as ninety-one
    problems invites ninety-one investigations of the same thing.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        # The parenthesised specifics differ per part; the sentence around them
        # is what identifies the pattern.
        shape = re.sub(r"\([^)]*\)", "(...)", str(r.get("description", "")))
        groups.setdefault((str(r.get("type")), shape), []).append(r)
    return sorted(
        (
            {
                "type": t,
                "pattern": shape,
                "count": len(items),
                "sheets": sorted({i["sheet"] for i in items})[:6],
            }
            for (t, shape), items in groups.items()
        ),
        key=lambda g: -g["count"],
    )


def _violations(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for sheet in report.get("sheets", []):
        for v in sheet.get("violations", []):
            rows.append(
                {
                    "type": v.get("type"),
                    "severity": v.get("severity"),
                    "description": v.get("description", ""),
                    "sheet": sheet.get("path", ""),
                }
            )
    return rows


def _unmute(project: Path, rules: list[str]) -> None:
    """Raise the given rules to ``warning`` in a copy of the project file."""
    data = json.loads(project.read_text(encoding="utf-8"))
    sev = data.setdefault("erc", {}).setdefault("rule_severities", {})
    for r in rules:
        sev[r] = "warning"
    project.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _label_census(sheets: list[Path]) -> dict[str, Any]:
    """How the sheets actually talk to each other.

    Whether a silenced rule matters depends on the design. A rule about global
    labels is academic on a flat board and load-bearing on one where they are
    the only thing joining eight sheets.
    """
    global_labels: Counter[str] = Counter()
    hier_labels: Counter[str] = Counter()
    sheet_pins = 0
    for path in sheets:
        try:
            node = sexpr.parse(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for g in sexpr.walk(node, "global_label"):
            name = sexpr.value(g, 1)
            if isinstance(name, str):
                global_labels[name] += 1
        for h in sexpr.walk(node, "hierarchical_label"):
            name = sexpr.value(h, 1)
            if isinstance(name, str):
                hier_labels[name] += 1
        sheet_pins += sum(1 for _ in sexpr.walk(node, "pin"))
    singles = sorted(n for n, c in global_labels.items() if c == 1)
    return {
        "global_labels": sum(global_labels.values()),
        "distinct_global_labels": len(global_labels),
        "global_labels_used_once": singles,
        "hierarchical_labels": sum(hier_labels.values()),
        "distinct_hierarchical_labels": len(hier_labels),
    }


def _filters_match(fpid: str, patterns: list[str]) -> bool:
    """Apply a symbol's footprint filters the way KiCad does.

    A filter containing a colon is matched against the whole ``Library:Name``;
    one without is matched against the name alone. Missing that distinction
    made three connectors here look like genuine mismatches when they were the
    same naming artefact as the other eighty-eight.
    """
    name = fpid.split(":", 1)[-1]
    return any(fnmatch.fnmatch(fpid if ":" in p else name, p) for p in patterns)


def _unflatten(fpid: str) -> str:
    """Undo ``ProjectLib:OriginalLib__OriginalName`` back to the original id."""
    name = fpid.split(":", 1)[-1]
    if "__" not in name:
        return fpid
    original_lib, original_name = name.split("__", 1)
    return f"{original_lib}:{original_name}"


def _footprint_filter_analysis(sheets: list[Path]) -> dict[str, Any]:
    """Separate a naming convention from an actual mismatch.

    A symbol carries a footprint filter -- ``R_*`` on a resistor -- and KiCad
    checks the assigned footprint against it. Republishing every footprint into
    one project library, renaming ``Resistor_SMD:R_0603`` to
    ``ProjectLib:Resistor_SMD__R_0603``, breaks every one of those filters at once.
    The parts are right; the names moved.

    Answered from the files rather than from the violation text, because that
    text is translated and parsing it would break on a differently configured
    machine.
    """
    explained, genuine, checked = [], [], 0
    for path in sheets:
        try:
            root = sexpr.parse(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        lib_symbols = sexpr.child(root, "lib_symbols")
        filters = {
            sexpr.value(s, 1): sexpr.prop(s, "ki_fp_filters")
            for s in (sexpr.children(lib_symbols, "symbol") if lib_symbols else [])
        }
        for sym in sexpr.walk(root, "symbol"):
            if sexpr.child(sym, "instances") is None:
                continue
            spec = filters.get(sexpr.value(sexpr.child(sym, "lib_id")))
            assigned = sexpr.prop(sym, "Footprint") or ""
            if not spec or not assigned:
                continue
            checked += 1
            patterns = str(spec).split()
            if _filters_match(str(assigned), patterns):
                continue
            row = {
                "reference": sexpr.prop(sym, "Reference"),
                "footprint": assigned,
                "filters": patterns,
                "sheet": path.name,
            }
            if _filters_match(_unflatten(str(assigned)), patterns):
                explained.append(row)
            else:
                genuine.append(row)
    return {
        "symbols_with_filters": checked,
        "mismatched": len(explained) + len(genuine),
        "explained_by_library_prefix": len(explained),
        "genuinely_unmatched": genuine,
    }


def audit(args: dict[str, Any]) -> None:
    arg = args.get("schematic") or args.get("board")
    if not arg:
        envelope.fail("E_USAGE", "--schematic or --board is required", {"param": "schematic"})
    target = Path(str(arg)).expanduser()
    schematic = target.with_suffix(".kicad_sch") if target.suffix == ".kicad_pcb" else target
    if not schematic.exists():
        envelope.fail("E_NOT_FOUND", "schematic file does not exist", {"path": str(schematic)})
    project = schematic.with_suffix(".kicad_pro")

    configured: dict[str, str] = {}
    if project.exists():
        try:
            configured = (
                json.loads(project.read_text(encoding="utf-8"))
                .get("erc", {})
                .get("rule_severities", {})
            )
        except (OSError, ValueError):
            configured = {}

    silenced = sorted(k for k, v in configured.items() if v == "ignore")
    shipped_off = [r for r in silenced if r in KICAD_DEFAULT_IGNORED]
    turned_off_here = [r for r in silenced if r not in KICAD_DEFAULT_IGNORED]
    downgraded = sorted(k for k, v in configured.items() if v == "warning")

    baseline = _run_erc(schematic)
    as_configured = _violations(baseline)

    # Now the part that cannot be inferred: run it again with nothing muted.
    hidden: list[dict[str, Any]] = []
    unmuted_ok = False
    if silenced and project.exists():
        work = Path(tempfile.mkdtemp(prefix="kicadcli-unmute-"))
        try:
            shutil.copytree(schematic.parent, work / "p")
            copy_sch = work / "p" / schematic.name
            copy_pro = work / "p" / project.name
            if copy_pro.exists():
                _unmute(copy_pro, silenced)
                loud = _violations(_run_erc(copy_sch))
                seen = {(v["type"], v["description"]) for v in as_configured}
                hidden = [v for v in loud if (v["type"], v["description"]) not in seen]
                unmuted_ok = True
        except (OSError, shutil.Error):
            unmuted_ok = False
        finally:
            shutil.rmtree(work, ignore_errors=True)

    sheets, _root = _sheet_files(schematic)
    labels = _label_census(sheets)
    filters = _footprint_filter_analysis(sheets)

    by_sev = Counter(v["severity"] for v in as_configured)
    by_type_hidden = Counter(v["type"] for v in hidden)

    findings = []
    if hidden:
        findings.append(
            {
                "id": "A1-hidden-violations",
                "severity": "error" if any(v["type"] in configured for v in hidden) else "warn",
                "title": "ERC is clean only because some rules are switched off",
                "detail": f"Turning the {len(silenced)} silenced rules back on and running ERC "
                f"again surfaces {len(hidden)} violations that the configured run does not "
                "report. These are not predictions; KiCad produced them.",
                "evidence": {
                    "as_configured": len(as_configured),
                    "with_rules_enabled": len(as_configured) + len(hidden),
                    "by_type": dict(by_type_hidden),
                    "patterns": _patterns(hidden)[:8],
                },
                "confidence": "measured",
                "fix": "review each one, then either fix it or raise the rule's severity so "
                "the decision is recorded in the project instead of inherited from a default",
            }
        )
    if "single_global_label" in silenced and labels["global_labels"]:
        singles = labels["global_labels_used_once"]
        findings.append(
            {
                "id": "A2-global-label-exposure",
                "severity": "warn" if singles else "info",
                "title": "the design leans on global labels while the rule guarding them is off",
                "detail": f"{labels['global_labels']} global labels across "
                f"{len(sheets)} sheets ({labels['distinct_global_labels']} distinct), and "
                "'single_global_label' is disabled -- which is KiCad's own default, not "
                f"something this project chose. {len(singles)} label names appear exactly "
                "once, and a label that appears once connects to nothing.",
                "evidence": {
                    "global_labels": labels["global_labels"],
                    "distinct": labels["distinct_global_labels"],
                    "used_once": singles[:20],
                    "used_once_count": len(singles),
                    "sheets": len(sheets),
                },
                "confidence": "measured",
                "fix": "check each single-use label for a typo, then set "
                "single_global_label to at least 'warning' for this project",
            }
        )
    if filters["mismatched"]:
        real = filters["genuinely_unmatched"]
        findings.append(
            {
                "id": "A4-footprint-filter",
                "severity": "warn" if real else "info",
                "title": "footprint filters do not match, mostly for one reason",
                "detail": f"{filters['explained_by_library_prefix']} of "
                f"{filters['mismatched']} mismatches disappear if the library prefix is "
                "stripped from the footprint name -- the footprints were republished into "
                "one project library as 'OriginalLib__OriginalName', which every symbol "
                f"filter then misses. Those are a naming convention, not a problem. The "
                f"remaining {len(real)} are not explained that way and are worth a look.",
                "evidence": {
                    "symbols_with_filters": filters["symbols_with_filters"],
                    "explained_by_library_prefix": filters["explained_by_library_prefix"],
                    "genuinely_unmatched": real[:10],
                },
                "confidence": "measured",
                "fix": "check the unexplained ones for a wrong footprint; for the rest, "
                "either widen the symbol filters or keep the rule disabled deliberately",
            }
        )
    if turned_off_here:
        findings.append(
            {
                "id": "A3-locally-silenced",
                "severity": "warn",
                "title": "rules were switched off in this project specifically",
                "detail": "These are not KiCad defaults -- someone disabled them here. "
                "That may have been the right call, but it is undocumented.",
                "evidence": {"rules": {r: WHY_IT_MATTERS.get(r, "") for r in turned_off_here}},
                "confidence": "measured",
                "fix": "record why, or restore the rule",
            }
        )

    envelope.ok(
        {
            "schematic": str(schematic),
            "sheets": len(sheets),
            "as_configured": {
                "violations": len(as_configured),
                "by_severity": dict(by_sev),
                "patterns": _patterns(as_configured),
                "details": _sample(as_configured),
            },
            "silenced_rules": {
                "all": silenced,
                "kicad_default": shipped_off,
                "disabled_in_this_project": turned_off_here,
                "downgraded_to_warning": downgraded,
                "why_they_matter": {r: WHY_IT_MATTERS.get(r, "") for r in silenced},
            },
            "with_rules_enabled": {
                "ran": unmuted_ok,
                "additional_violations": len(hidden),
                "by_type": dict(by_type_hidden),
                # Grouped first, then a few of each kind. The counts are exact;
                # the rows are a sample and say so.
                "patterns": _patterns(hidden),
                "sample": _sample(hidden),
            },
            "cross_sheet_connectivity": labels,
            "footprint_filters": filters,
            "findings": findings,
            "status": "PASS" if not (as_configured or hidden) else "FAIL",
            "not_checked": [
                "the pin-type conflict matrix was read but not second-guessed; a project "
                "may legitimately relax it",
                "ERC excludes recorded in the project file are honoured as written, not "
                "re-examined",
                "silenced rules were re-run at 'warning' severity; a rule whose default is "
                "'error' will therefore appear here as a warning",
                "this reports what ERC can see; it says nothing about whether the circuit "
                "is correct",
            ],
        }
    )


def _sheet_files(schematic: Path) -> tuple[list[Path], str | None]:
    """Sheets reachable from the root, so sibling designs are not mixed in."""
    if not schematic.exists():
        return [], None
    seen: list[Path] = []
    queue = [schematic]
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
    return seen, schematic.name
