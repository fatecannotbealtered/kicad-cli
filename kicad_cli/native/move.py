"""``board move``, in this process: put parts where they are told to go.

A port of `payload/pcb_place.py`, which ran on pcbnew. The geometry remains
compatible; results now include a separate consumer's saved-file observation.

Some routing problems are placement problems. When the order of the parts
on the board does not match the order of the pins they connect to, the
tracks have to cross, and no router untangles that. This does the smallest
thing: move the named parts to the given coordinates, then report whose
courtyards now overlap. Where a part goes is the caller's decision.

A footprint's pads, drawings and text are stored relative to it, so a move
rewrites one thing in the file: the footprint's own `(at x y)`. Everything
else keeps its bytes.
"""

from __future__ import annotations

import hashlib
import math
import os
import uuid
from pathlib import Path

from .. import envelope, move_readback
from ..fileformat import geometry
from ..fileformat.board import Board, Footprint, shape_points, stroke_width
from ..fileformat.sexpr import number
from . import load_board, write

NM = 1_000_000
COURTYARDS = ("F.CrtYd", "B.CrtYd")
COURTYARD_SHRINK = 5_000  # nm: see courtyard_box


def mm(value: float) -> float:
    return value / NM


def from_mm(value: float) -> int:
    """Millimetres to nanometres as the payload converted them: truncated,
    not rounded -- 1.005 mm lands on 1.004999. Kept so the port is provable;
    listed in DEVELOPMENT_STATUS.md to be fixed on its own."""
    return int(float(value) * float(NM))


def parse_moves(text: str) -> dict[str, tuple[float, float]]:
    moves = {}
    for item in text.split(";"):
        item = item.strip()
        if not item:
            continue
        try:
            ref, xy = item.split(":")
            x, y = xy.split(",")
            ref = ref.strip()
            coordinates = (float(x), float(y))
            if (
                not ref
                or ref in moves
                or not all(math.isfinite(v) and math.isfinite(v * NM) for v in coordinates)
            ):
                raise ValueError("empty/duplicate reference or non-finite coordinate")
            moves[ref] = coordinates
        except ValueError:
            envelope.fail(
                "E_USAGE",
                "--moves is REF:x,y separated by ';'",
                {
                    "param": "moves",
                    "got": item,
                    "format": 'REF:x,y separated by ";" -- for example "U1:120.5,60.0; C3:118,62"',
                },
            )
    if not moves:
        envelope.fail(
            "E_USAGE", "--moves must contain at least one finite REF:x,y", {"param": "moves"}
        )
    return moves


def courtyard_box(fp: Footprint) -> tuple[float, float, float, float]:
    """The courtyard's box in mm, front layer first; failing that, the box
    around the footprint's copper and drawings without its text.

    What pcbnew reports for a courtyard is not the box of the lines as drawn:
    each side lies further out by the line's width less 5 um -- 45 um for the
    usual 0.05 mm line, 95 for 0.10, 115 for 0.12, measured on every footprint
    of five demo boards. Where a courtyard has arcs or circles pcbnew's own
    approximation of them adds a few micrometres more, which this does not
    reproduce.
    """
    found = courtyard(fp)
    if found is not None:
        return found
    box = fp.bbox() or (*fp.position, *fp.position)
    return (mm(box[0]), mm(box[1]), mm(box[2]), mm(box[3]))


def courtyard(fp: Footprint) -> tuple[float, float, float, float] | None:
    """The courtyard's box in mm, front layer first, as `courtyard_box`
    measures it; None for a footprint with no courtyard."""
    for layer in COURTYARDS:
        boxes = []
        for node in fp.node.lists():
            if not node.head.startswith("fp_"):
                continue
            found = node.find("layer")
            if found is None or found.value(1) != layer:
                continue
            points = shape_points(node)
            if not points:
                continue
            grow = stroke_width(node) - COURTYARD_SHRINK
            x1, y1, x2, y2 = geometry.bbox(geometry.place(points, *fp.position, fp.angle))
            boxes.append((x1 - grow, y1 - grow, x2 + grow, y2 + grow))
        if boxes:
            return (
                mm(min(b[0] for b in boxes)),
                mm(min(b[1] for b in boxes)),
                mm(max(b[2] for b in boxes)),
                mm(max(b[3] for b in boxes)),
            )
    return None


def overlaps(a, b, gap=0.0) -> bool:
    return not (
        a[2] + gap <= b[0] or b[2] + gap <= a[0] or a[3] + gap <= b[1] or b[3] + gap <= a[1]
    )


class _Attempt:
    """A bounded readback receipt, including independently observed restoration.

    The ID identifies this invocation. Durable operation lookup, scope records,
    and the new dry-run protocol are still a separate migration; never imply
    this ID can be queried with an operation command that does not exist.
    """

    def __init__(self, path, before, moves):
        self.path, self.before, self.moves = path, before, moves
        self.after = None
        self.armed = self.save_started = self.finalized = False
        self.stage = "apply"
        self.status = "not_run"
        expected, _ = move_readback.compare(before, before, moves)
        self.receipt = {
            "operation_id": str(uuid.uuid4()),
            "capability_id": "board.move",
            "plan_id": None,
            "target": {
                "mode": "file",
                "engine_id": "kicad_cli.native.move",
                "instance_id": None,
                "object_id": str(Path(path).resolve()),
                "locator": str(Path(path).resolve()),
            },
            "authorization_scope_id": None,
            "authorization_unavailable_reason": "Legacy confirmation loop; "
            "scope and durable operation protocol are not yet migrated.",
            "effects": ["change", "persist"],
            "snapshot_before": before.snapshot,
            "snapshot_after": None,
            "outcome": {
                "execution": "not_started",
                "change": "not_applied",
                "persistence": "unchanged",
                "delivery": "not_applicable",
                "verification": {},
            },
            "failed_stage": None,
            "changed_items": [],
            "artifacts": [],
            "deliveries": [],
            "recovery": {"action": "none", "reason": "No failure observed.", "supported": True},
            "extensions": {
                "expected": expected,
                "observed": [],
                "source_before": before.source,
                "source_after": None,
                "failures": [],
                "units": {
                    "position": "mm",
                    "storage": "nm",
                    "rotation": "deg",
                    "coordinate_frame": "KiCad board, x right, y down",
                    "position_tolerance_nm": 1,
                    "conversion": "Legacy writer truncates binary-float mm * 1e6; "
                    "comparison uses independent Decimal physical targets.",
                },
                "coverage": {
                    "references": sorted(moves),
                    "checked": [
                        "selected identity, saved position, rotation and layer",
                        "all other board bytes outside selected footprint at expressions",
                    ],
                    "not_checked": [
                        "DRC",
                        "connectivity",
                        "courtyard accuracy",
                        "visual layout",
                        "KiCad native loading",
                        "project sidecars and GUI memory",
                    ],
                },
                "protocol_limitations": "Readback pilot: invocation ID only, "
                "no durable operation lookup, scope protocol or new dry-run envelope. "
                "Not full driver conformance.",
            },
        }

    def _verification(self):
        item = {"id": "saved_pose_and_protected_content", "status": self.status, "required": True}
        if self.status in ("passed", "failed"):
            item["evidence_ref"] = "#/extensions"
        else:
            item["reason"] = "Saved-file observation did not complete."
        self.receipt["outcome"]["verification"] = {
            "status": self.status,
            "scope": "Saved footprint poses and non-pose board bytes only; "
            "no DRC or engineering approval.",
            "snapshot_id": self.after.snapshot["id"] if self.after else None,
            "coverage": "complete" if self.status in ("passed", "failed") else "none",
            "checks": [item],
            "not_checked": [],
            "evidence_refs": ["#/extensions"],
        }
        if self.status == "incomplete":
            self.receipt["outcome"]["verification"]["reason"] = (
                "Independent file readback was unavailable or incomplete."
            )

    def observed(self, after):
        self.after = after
        self.receipt["snapshot_after"] = after.snapshot
        ext = self.receipt["extensions"]
        ext["observed"], ext["source_after"] = after.objects, after.source
        ext["expected"], ext["failures"] = move_readback.compare(self.before, after, self.moves)
        self.status = "failed" if ext["failures"] else "passed"
        self._verification()

    def abort(self, code):
        if self.finalized:
            return code
        self.finalized = True
        self.receipt["failed_stage"] = self.stage
        outcome = self.receipt["outcome"]
        outcome["execution"] = "failed"
        if self.status == "not_run":
            self.status = "incomplete"
        self._verification()
        if self.armed:
            try:
                write.write_txn.rollback("board move failure")
                restored = move_readback.observe(self.path, sorted(self.moves))
                self.receipt["snapshot_after"] = restored.snapshot
                self.receipt["extensions"]["restoration"] = {
                    "snapshot": restored.snapshot,
                    "source": restored.source,
                    "matches_baseline": restored.snapshot["revision"]
                    == self.before.snapshot["revision"],
                }
                if (
                    self.save_started
                    and restored.snapshot["revision"] != self.before.snapshot["revision"]
                ):
                    raise move_readback.ReadbackError(
                        "restored bytes differ from the pre-write baseline"
                    )
                outcome["change"] = "rolled_back" if self.save_started else "not_applied"
                outcome["persistence"] = "unchanged"
                self.receipt["recovery"] = {
                    "action": "inspect",
                    "reason": "Restoration independently read back; "
                    "the failed attempt was not committed.",
                    "supported": True,
                }
            except (OSError, ValueError) as exc:
                outcome["change"] = outcome["persistence"] = "unknown"
                self.receipt["extensions"]["restoration_error"] = str(exc)
                self.receipt["recovery"] = {
                    "action": "human_review",
                    "reason": "Cannot independently establish final saved state; "
                    "inspect board and transaction backup/journal before another write.",
                    "supported": True,
                }
                return "E_OUTCOME_UNKNOWN"
        return code

    def failure_details(self):
        return {
            "dispatch_state": "dispatched" if self.save_started else "not_started",
            "operation_id": self.receipt["operation_id"],
            "receipt": self.receipt,
            "lookup": f"Inspect {self.path} and its .kicad-cli.backup/.journal; "
            "durable operation lookup is not yet implemented.",
        }

    def fail(self, code, message, reason):
        code = self.abort(code)
        envelope.fail(code, message, {**self.failure_details(), "reason": str(reason)})

    def finish(self):
        changed = self.before.snapshot["revision"] != self.after.snapshot["revision"]
        self.receipt["outcome"].update(
            execution="completed",
            change="applied" if changed else "not_applied",
            persistence="saved" if changed else "unchanged",
        )
        prior = {item["ref"]: item for item in self.before.objects}
        self.receipt["changed_items"] = [
            {
                "id": item["id"],
                "change": "footprint position",
                "evidence_ref": "#/extensions/observed",
            }
            for item in self.after.objects
            if item["position_nm"] != prior[item["ref"]]["position_nm"]
        ]


def run(path: str, moves_text: str, confirm: str | None) -> None:
    attempt = None

    def on_failure(code, details):
        if attempt is None:
            details["write_state"] = "not_started"
        else:
            attempt.abort(code)
            details.update(attempt.failure_details())
            details["write_state"] = (
                "unknown"
                if attempt.receipt["outcome"]["change"] == "unknown"
                else write.write_txn.state()
                if attempt.armed
                else "not_started"
            )

    envelope.on_fail(on_failure)
    board = load_board(path)
    moves = parse_moves(moves_text)

    counts = {ref: sum(fp.reference == ref for fp in board.footprints) for ref in moves}
    ambiguous = sorted(ref for ref, count in counts.items() if count > 1)
    if ambiguous:
        envelope.fail("E_VALIDATION", "ambiguous footprint references", {"refs": ambiguous})
    fps = {fp.reference: fp for fp in board.footprints}
    missing = [ref for ref in moves if ref not in fps]
    if missing:
        envelope.fail("E_NOT_FOUND", "板上没有这些位号", {"refs": missing})

    try:
        before = move_readback.observe(path, sorted(moves))
    except (OSError, ValueError) as exc:
        envelope.fail(
            "E_VALIDATION",
            "cannot independently observe board before writing",
            {"reason": str(exc)},
        )
    if (
        hashlib.sha256(board.document.source.encode("utf-8")).hexdigest()
        != before.snapshot["revision"]
    ):
        envelope.fail("E_CONFLICT", "board changed while being loaded", {"board": path})

    plan = []
    for ref, (x, y) in sorted(moves.items()):
        fp = fps[ref]
        plan.append(
            {
                "ref": ref,
                "from": [round(mm(fp.position[0]), 2), round(mm(fp.position[1]), 2)],
                "to": [x, y],
                "rot_deg": None,
            }
        )
    preview = {
        "board": path,
        "moves": plan,
        "will": f"搬动 {len(plan)} 个器件；搬完检查庭院重叠",
        "readback": {
            "objects": before.objects,
            "source": before.source["name"],
            "version": before.source["version"],
            "position_tolerance_nm": 1,
            "scope": "saved poses and protected board content; not DRC",
        },
    }
    envelope.check_confirm(
        confirm,
        f"pcb_place:{','.join(sorted(moves))}:{os.path.basename(path)}",
        preview,
        path,
    )

    attempt = _Attempt(path, before, moves)
    write.begin(path)
    attempt.armed = True
    try:
        if Path(path).read_bytes() != board.document.source.encode("utf-8"):
            attempt.fail(
                "E_CONFLICT", "board changed before the write lock was acquired", "state_drift"
            )
    except OSError as exc:
        attempt.fail("E_IO", "cannot recheck board under write lock", exc)
    for ref, (x, y) in moves.items():
        move_footprint(board, fps[ref], from_mm(x), from_mm(y))

    # Courtyards after the move. An overlap is not always wrong -- parts can be
    # stacked on purpose -- but one this move made has to be seen.
    boxes = [(fp.reference, courtyard_box(fp)) for fp in board.footprints]
    clash = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if boxes[i][0] not in moves and boxes[j][0] not in moves:
                continue  # neither moved: not this command's doing
            if overlaps(boxes[i][1], boxes[j][1]):
                clash.append([boxes[i][0], boxes[j][0]])

    attempt.stage = "persist"
    attempt.save_started = True
    try:
        write.save(board)
    except OSError as exc:
        attempt.fail("E_IO", "could not save moved board", exc)
    attempt.stage = "verify"
    try:
        attempt.observed(move_readback.observe(path, sorted(moves)))
    except (OSError, ValueError) as exc:
        attempt.fail("E_VERIFY_INCOMPLETE", "could not independently read saved board", exc)
    if attempt.status != "passed":
        attempt.fail(
            "E_VERIFY_FAILED",
            "saved board differs from the requested move",
            attempt.receipt["extensions"]["failures"],
        )
    try:
        latest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        attempt.status = "incomplete"
        attempt.fail("E_VERIFY_INCOMPLETE", "cannot recheck verified snapshot before commit", exc)
    if latest != attempt.after.snapshot["revision"]:
        attempt.status = "incomplete"
        attempt.receipt["extensions"]["state_drift"] = {
            "verified_sha256": attempt.after.snapshot["revision"],
            "latest_sha256": latest,
        }
        attempt.fail("E_CONFLICT", "saved board changed after readback", "state_drift")
    try:
        write.write_txn.commit()
    except OSError as exc:
        attempt.stage = "persist"
        attempt.fail("E_IO", "could not finish moved board transaction", exc)
    attempt.finish()
    envelope.ok(
        {
            "moved": len(moves),
            "plan": plan,
            "courtyard_clash": clash,
            "receipt": attempt.receipt,
            "note": "器件搬动后，连到它们的走线已经失效，"
            "接下来必须重布相关网络（board route --mode repair），并另行检查 DRC/连通性",
        }
    )


def move_footprint(board: Board, fp: Footprint, x: int, y: int) -> None:
    """Put a footprint's origin at (x, y), in nm, and keep the model in step."""
    at = fp.node.find("at")
    at.set(1, number(x / NM))
    at.set(2, number(y / NM))
    dx, dy = x - fp.position[0], y - fp.position[1]
    fp.position = (x, y)
    for pad in fp.pads:
        pad.position = (pad.position[0] + dx, pad.position[1] + dy)
