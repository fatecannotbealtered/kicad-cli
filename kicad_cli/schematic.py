"""The circuit specification `sch create` takes, and what makes one valid.

The input is JSON, not Python. An agent that has to emit code to use a tool is
an agent running code it wrote, and the review surface for that is the whole
language. A parts-and-nets document is reviewable in the dry run, refusable on
a typo, and cannot do anything but describe a circuit.

This module judges what can be judged without KiCad's libraries: the shape,
the references, the form of every connection. Whether `Device:R` exists and
has a pin 2 is `kicad_cli/native/sch_create.py`'s question, answered against
the installed libraries before anything is written.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import envelope

PIN_REF = re.compile(r"\A(?P<ref>[A-Za-z_][A-Za-z0-9_]*)\.(?P<pin>.+)\Z")


def load_spec(path: str) -> dict[str, Any]:
    """Read and shape-check the specification. No libraries touched yet."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        envelope.fail("E_NOT_FOUND", "cannot read the circuit specification", {"path": path})
        raise AssertionError from exc  # pragma: no cover - fail() exits
    try:
        spec = json.loads(raw)
    except ValueError as exc:
        envelope.fail(
            "E_VALIDATION",
            "the circuit specification is not valid JSON",
            {"path": path, "reason": str(exc)[:200]},
        )
        raise AssertionError from exc  # pragma: no cover
    if not isinstance(spec, dict):
        envelope.fail("E_VALIDATION", "the specification must be a JSON object", {"path": path})
    problems: list[dict[str, Any]] = []
    parts = spec.get("parts")
    nets = spec.get("nets")
    if not isinstance(parts, list) or not parts:
        problems.append({"field": "parts", "problem": "a non-empty array is required"})
    if not isinstance(nets, list) or not nets:
        problems.append({"field": "nets", "problem": "a non-empty array is required"})
    if not problems:
        problems = _structural_problems(parts, nets)
    if problems:
        envelope.fail(
            "E_VALIDATION",
            "the specification is missing what a circuit needs",
            {"problems": problems[:40], "problem_count": len(problems), "shape": SHAPE},
        )
    return spec


def _structural_problems(parts: list, nets: list) -> list[dict[str, Any]]:
    """Everything wrong with a specification that the libraries cannot answer.

    Whether `Device:R` exists needs KiCad; whether a part was given a symbol at
    all does not. Keeping the two apart is what lets a broken specification be
    refused on a machine with no KiCad installed -- CI is one, and being told to
    install KiCad when the real problem is a missing field is a bad answer.
    """
    problems: list[dict[str, Any]] = []
    refs: set[str] = set()
    for index, item in enumerate(parts):
        where = {"index": index, "ref": item.get("ref") if isinstance(item, dict) else None}
        if not isinstance(item, dict):
            problems.append({**where, "problem": "each part must be an object"})
            continue
        if not item.get("ref"):
            problems.append({**where, "problem": "each part needs a ref"})
            continue
        if not item.get("symbol"):
            problems.append({**where, "problem": "each part needs a symbol"})
            continue
        if ":" not in str(item["symbol"]):
            problems.append(
                {**where, "problem": "symbol must be 'Library:Name'", "got": item["symbol"]}
            )
            continue
        if str(item["ref"]) in refs:
            problems.append({**where, "problem": "duplicate ref"})
            continue
        refs.add(str(item["ref"]))

    for index, item in enumerate(nets):
        where = {"index": index, "net": item.get("name") if isinstance(item, dict) else None}
        if not isinstance(item, dict) or not item.get("name"):
            problems.append({**where, "problem": "each net needs a name"})
            continue
        connect = item.get("connect")
        if not isinstance(connect, list) or len(connect) < 2:
            problems.append({**where, "problem": "a net needs at least two connections"})
            continue
        for entry in connect:
            match = PIN_REF.match(str(entry))
            if match is None:
                problems.append({**where, "problem": "connection must be 'REF.PIN'", "got": entry})
            elif refs and match.group("ref") not in refs:
                problems.append({**where, "problem": "no such part", "ref": match.group("ref")})
    return problems


SHAPE = {
    "title": "optional string",
    "parts": [
        {"ref": "U1", "symbol": "Library:Symbol", "value": "optional", "footprint": "optional"}
    ],
    "nets": [{"name": "+5V", "connect": ["U1.VI", "C1.1"]}],
}
