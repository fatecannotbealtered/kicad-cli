"""KiCad's DRC report, as kicad-cli returns it: a summary by group and severity, and the findings.

KiCad's JSON report has three groups: ``violations``, ``unconnected_items`` and, when the
schematic parity check ran, ``schematic_parity``. Each finding has a type, a severity
(error, warning or exclusion), a description and the items it is about, each with a UUID,
a description and a position in mm.
"""
from __future__ import annotations

import math
from typing import Any

from .base import AdapterError

GROUPS = ("violations", "unconnected_items", "schematic_parity")
SEVERITIES = ("error", "warning", "exclusion")


def _position(item: dict[str, Any]) -> tuple[float | None, float | None]:
    pos = item.get("pos") if isinstance(item.get("pos"), dict) else {}
    x, y = pos.get("x"), pos.get("y")
    ok = all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (x, y))
    return (round(float(x), 6), round(float(y), 6)) if ok else (None, None)


def shape(report: Any, parity: bool) -> dict[str, Any]:
    """The result of ``board check``: summary counts and one item per finding."""
    if not isinstance(report, dict) or report.get("coordinate_units", "mm") != "mm":
        raise AdapterError("E_NATIVE_FAILURE", "KiCad's DRC report is not in the expected form.")
    groups = GROUPS if parity else GROUPS[:2]
    summary: dict[str, dict[str, int]] = {}
    items: list[dict[str, Any]] = []
    for group in groups:
        findings = report.get(group, [])
        if not isinstance(findings, list):
            raise AdapterError("E_NATIVE_FAILURE", f"KiCad's DRC report has no list of {group}.")
        counts = dict.fromkeys(SEVERITIES, 0)
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            severity = str(finding.get("severity", ""))
            if severity in counts:
                counts[severity] += 1
            parts = []
            for item in finding.get("items") or []:
                if isinstance(item, str):
                    item = {"uuid": item}
                if not isinstance(item, dict):
                    continue
                x, y = _position(item)
                parts.append({"uuid": item.get("uuid", ""), "description": item.get("description", ""),
                              "x_mm": x, "y_mm": y})
            items.append({"group": group, "type": finding.get("type", ""), "severity": severity,
                          "excluded": bool(finding.get("excluded", severity == "exclusion")),
                          "description": finding.get("description", ""), "items": parts})
        summary[group] = counts
    errors = sum(c["error"] for c in summary.values())
    warnings = sum(c["warning"] for c in summary.values())
    return {
        "passed": errors == 0,
        "errors": errors,
        "warnings": warnings,
        "summary": summary,
        "schematic_parity_checked": parity,
        "zones_refilled": True,
        "kicad_version": report.get("kicad_version", ""),
        "items": items,
    }
