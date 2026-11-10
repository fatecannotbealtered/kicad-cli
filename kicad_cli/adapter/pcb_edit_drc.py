"""KiCad's DRC before a board edit: what the change would add, before anything is written.

The board as saved and the board as it would be after the change are each staged in a
private temporary folder with the project's rule files beside them (same-stem ``.kicad_pro``
and ``.kicad_dru``, and the project's library tables), so KiCad checks both the same way.
KiCad's DRC runs on both at once; the folder is deleted afterwards.

A finding is the same before and after when its group, type, severity, exclusion and the
UUIDs of its items are the same; positions are not compared, so a violation a moved part
carries along is not new. What blocks the change, as in the previous implementation:

- a new error-severity violation that is not excluded;
- more unconnected items on a net than before.

New warnings are reported but do not block.
"""
from __future__ import annotations

import shutil
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import base, kicad_tools
from .fileformat.board import Board
from .fileformat.sexpr import Document

GROUPS = ("violations", "schematic_parity")
SIDECARS = (".kicad_pro", ".kicad_dru")
TABLES = ("fp-lib-table", "sym-lib-table")
MAX_LISTED = 10


@dataclass
class Precheck:
    new_errors: list[dict[str, Any]] = field(default_factory=list)
    new_warnings: list[dict[str, Any]] = field(default_factory=list)
    unconnected: dict[str, tuple[int, int]] = field(default_factory=dict)  # net -> (before, after)
    counts: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return bool(self.new_errors or self.unconnected)

    def blocked_by(self) -> list[str]:
        out = [f"KiCad's DRC would report a new error: {describe(f)}" for f in self.new_errors[:MAX_LISTED]]
        if len(self.new_errors) > MAX_LISTED:
            out.append(f"... and {len(self.new_errors) - MAX_LISTED} more new DRC errors")
        for net, (before, after) in sorted(self.unconnected.items())[:MAX_LISTED]:
            out.append(f"Net {net} would have {after} unconnected items instead of {before}")
        return out

    def summary(self) -> str:
        unconnected = sum(after - before for before, after in self.unconnected.values())
        left = (self.counts.get("after") or {}).get("unconnected_items")
        return (f"KiCad's DRC: {len(self.new_errors)} new errors, {len(self.new_warnings)} new warnings, "
                f"{unconnected} more unconnected items" + (f", {left} left in all" if left is not None else ""))

    def as_result(self) -> dict[str, Any]:
        return {
            "new_errors": [summarise(f) for f in self.new_errors[:MAX_LISTED]],
            "new_warnings": [summarise(f) for f in self.new_warnings[:MAX_LISTED]],
            "more_unconnected": [{"net": n, "before": b, "after": a} for n, (b, a) in sorted(self.unconnected.items())],
            "counts": self.counts,
        }


def item_ids(finding: dict[str, Any]) -> list[str]:
    return [i if isinstance(i, str) else str((i or {}).get("uuid", "")) for i in finding.get("items") or []]


def describe(finding: dict[str, Any]) -> str:
    items = [i.get("description", "") for i in finding.get("items") or [] if isinstance(i, dict)]
    text = finding.get("description") or finding.get("type", "")
    return f"{finding.get('type', '')}: {text}" + (f" ({'; '.join(x for x in items if x)})" if any(items) else "")


def summarise(finding: dict[str, Any]) -> dict[str, Any]:
    return {"type": finding.get("type"), "severity": finding.get("severity"), "description": describe(finding)}


def _key(group: str, finding: dict[str, Any]) -> tuple:
    return (group, finding.get("type"), finding.get("severity"), bool(finding.get("excluded", False)),
            tuple(sorted(item_ids(finding))))


def nets_by_uuid(*boards: Board) -> dict[str, str]:
    """The net of every pad, track, via and zone, by UUID."""
    out: dict[str, str] = {}
    for board in boards:
        for fp in board.footprints:
            for pad in fp.pads:
                if pad.node is not None and (u := pad.node.find("uuid")) is not None:
                    out[u.value(1) or ""] = pad.net
        for item in [*board.tracks, *board.vias, *board.zones]:
            if item.node is not None and (u := item.node.find("uuid")) is not None:
                out[u.value(1) or ""] = item.net
    return out


def _per_net(report: dict[str, Any], nets: dict[str, str]) -> Counter:
    counts: Counter = Counter()
    for finding in report.get("unconnected_items") or []:
        net = next((nets[i] for i in item_ids(finding) if i in nets), "?")
        counts[net or "?"] += 1
    return counts


def compare(before: dict[str, Any], after: dict[str, Any], nets: dict[str, str]) -> Precheck:
    """What the candidate's report adds to the baseline's."""
    available = Counter(_key(g, f) for g in GROUPS for f in before.get(g) or [])
    result = Precheck()
    for group in GROUPS:
        for finding in after.get(group) or []:
            key = _key(group, finding)
            if available[key]:
                available[key] -= 1
            elif finding.get("severity") == "error" and not finding.get("excluded"):
                result.new_errors.append(finding)
            elif not finding.get("excluded"):
                result.new_warnings.append(finding)
    b, a = _per_net(before, nets), _per_net(after, nets)
    result.unconnected = {net: (b.get(net, 0), n) for net, n in a.items() if n > b.get(net, 0)}
    result.counts = {
        name: {g: len(r.get(g) or []) for g in ("violations", "unconnected_items", "schematic_parity")}
        for name, r in (("before", before), ("after", after))
    }
    return result


def stage(board: Path, text: str, folder: Path) -> Path:
    """The board text in a folder of its own, with the project's rules and tables beside it."""
    folder.mkdir(parents=True, exist_ok=True)
    for suffix in SIDECARS:
        source = board.with_suffix(suffix)
        if source.is_file():
            shutil.copyfile(source, folder / source.name)
    for name in TABLES:
        source = board.parent / name
        if source.is_file():
            shutil.copyfile(source, folder / name)
    staged = folder / board.name
    staged.write_text(text, encoding="utf-8", newline="")
    return staged


def precheck(board: Path, before_text: str, after_text: str) -> Precheck:
    """KiCad's DRC of the board before and after the change, compared."""
    kicad = base.kicad()
    with tempfile.TemporaryDirectory(prefix="kicad-cli-precheck-") as tmp:
        root = Path(tmp)
        staged = [stage(board, before_text, root / "before"), stage(board, after_text, root / "after")]
        with ThreadPoolExecutor(max_workers=2) as pool:
            reports = list(pool.map(lambda p: kicad_tools.drc(kicad, p, parity=False), staged))
    nets = nets_by_uuid(Board(Document.parse(before_text)), Board(Document.parse(after_text)))
    return compare(reports[0], reports[1], nets)
